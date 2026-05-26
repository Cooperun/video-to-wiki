import os
import sys
import shutil
import subprocess
import logging
import yaml
from textwrap import dedent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


OPENAI_COMPATIBLE_PRESETS = [
    {
        "id": "openai",
        "name": "OpenAI 官方 API",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
        "key_hint": "sk-...",
    },
    {
        "id": "dashscope",
        "name": "阿里云百炼 OpenAI 兼容模式",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
        "key_hint": "sk-...",
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow 硅基流动",
        "api_base": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "key_env": "SILICONFLOW_API_KEY",
        "key_hint": "sk-...",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "api_base": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "key_env": "OPENROUTER_API_KEY",
        "key_hint": "sk-or-...",
    },
    {
        "id": "oneapi",
        "name": "OneAPI / NewAPI 自建网关",
        "api_base": "https://your-oneapi-domain.example.com/v1",
        "model": "deepseek-chat",
        "key_env": "OPENAI_API_KEY",
        "key_hint": "网关分配的 Key",
    },
    {
        "id": "litellm",
        "name": "LiteLLM Proxy",
        "api_base": "http://localhost:4000/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
        "key_hint": "LiteLLM master key",
    },
    {
        "id": "ollama",
        "name": "Ollama 本地 OpenAI 兼容接口",
        "api_base": "http://localhost:11434/v1",
        "model": "qwen2.5:7b-instruct",
        "key_env": "OPENAI_API_KEY",
        "key_hint": "Ollama 可填写 ollama",
        "default_api_key": "ollama",
    },
    {
        "id": "custom",
        "name": "手动填写 OpenAI 兼容端点",
        "api_base": "",
        "model": "",
        "key_env": "OPENAI_API_KEY",
        "key_hint": "服务商或网关分配的 Key",
    },
]


DEFAULT_CONFIG_TEMPLATE = dedent("""\
    provider: "openai_compatible"

    qwen:
      api_key: ""
      model: "qwen3-vl-plus"
      ocr_model: "qwen3-vl-plus"
      visual_locator_model: "qwen3-vl-plus"
      composer_model: "qwen3-vl-plus"
      api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"

    deepseek:
      api_key: ""
      api_base: "https://api.deepseek.com"
      model: "deepseek-v4-pro"
      enable_thinking: true
      reasoning_effort: "high"
      structuring_prompt: ""

    openai_compatible:
      api_key: ""
      api_base: ""
      model: ""
      structuring_prompt: ""

    search_grounding:
      enabled: false
      max_keywords: 3
      max_search_results: 3

    wiki_dir: "~/Documents/llm_wiki"
    temp_dir: "./temp"

    asr:
      model_size: "base"
      language: "zh"
      subtitle_first: true

    sampling:
      scene_threshold: 0.02
      max_interval_sec: 15
      max_width: 1280

    visual_locator:
      enabled: false
      min_frames: 0
      max_frames: 8
      trigger_window_sec: 6
      min_gap_sec: 12
      min_score: 2.5

    output:
      image_link_style: "standard"

    subtitle_ocr:
      mode: "hybrid"
      local_engine: "rapidocr"
      local_confidence_threshold: 0.5
""")


def _read_yaml(filepath):
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _env_has_any(*names):
    return any(bool(os.environ.get(name)) for name in names)


def _configured_value(config_data, section, key, *env_names):
    value = (config_data.get(section, {}) or {}).get(key, "")
    return bool(value) or _env_has_any(*env_names)


def _openai_compatible_env_names(config_data):
    api_base = ((config_data.get("openai_compatible", {}) or {}).get("api_base") or "").lower()
    if "dashscope" in api_base or "aliyuncs" in api_base:
        return ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "OPENAI_API_KEY")
    if "siliconflow" in api_base:
        return ("SILICONFLOW_API_KEY", "OPENAI_API_KEY")
    if "openrouter" in api_base:
        return ("OPENROUTER_API_KEY", "OPENAI_API_KEY")
    return ("OPENAI_API_KEY",)


def _prompt(default, message):
    suffix = f" 默认 {default}" if default else ""
    value = input(f"{message}({suffix}): ").strip()
    return value if value else default


def update_yaml_many(filepath, updates):
    ok = True
    for section, key, value in updates:
        if section:
            ok = update_yaml_config(filepath, section, key, value) and ok
        else:
            ok = update_yaml_top_level(filepath, key, value) and ok
    return ok


def update_yaml_config(filepath, section, key, value):
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        in_section = False
        updated = False

        for line in lines:
            stripped = line.strip()
            # Detect section header, e.g. "deepseek:" or "openai_compatible:"
            if stripped.startswith(f"{section}:"):
                in_section = True
                new_lines.append(line)
                continue
            
            # Detect next section header or end of section
            if in_section and stripped.endswith(":") and not stripped.startswith("#"):
                # If we hit another section without matching the key
                if not stripped.startswith(f"{key}:"):
                    in_section = False

            if in_section and stripped.startswith(f"{key}:"):
                # Preserve leading whitespace indentation
                indent = line[:line.find(key)]
                # Replace the key line
                new_lines.append(f'{indent}{key}: "{value}"\n')
                updated = True
                in_section = False  # Reset section flag after update
                continue
            
            new_lines.append(line)

        if updated:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            return True
        return False
    except Exception as e:
        print(f" ❌ 更新子配置失败: {e}")
        return False

def update_yaml_top_level(filepath, key, value):
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        updated = False

        for line in lines:
            stripped = line.strip()
            # Ensure it's not in a sub-section
            if stripped.startswith(f"{key}:") and not line.startswith(" ") and not line.startswith("\t"):
                new_lines.append(f'{key}: "{value}"\n')
                updated = True
                continue
            new_lines.append(line)

        if updated:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            return True
        return False
    except Exception as e:
        print(f" ❌ 更新顶层配置失败: {e}")
        return False


class SystemInitializer:
    """
    Handles automatic one-click setup of global paths, environment configuration, 
    Obsidian directories, and API credentials.
    """
    @staticmethod
    def _run_user_config_wizard(global_config_file):
        print("\n 🔍 开始检测用户前置配置...")
        config_data = _read_yaml(global_config_file)
        provider = config_data.get("provider", "deepseek")
        wiki_dir = config_data.get("wiki_dir", "")

        text_ready = SystemInitializer._is_text_provider_ready(provider, config_data)
        qwen_ready = _configured_value(config_data, "qwen", "api_key", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY")

        print(f"  - 当前文本大模型 provider: {provider}")
        print(f"  - 文本总结模型: {'✅ 已具备基础配置' if text_ready else '⚠️ 未完整配置'}")
        print(f"  - Qwen-VL 云端 OCR 兜底: {'✅ 已配置' if qwen_ready else '⚠️ 未配置，可先用 local OCR 或稍后补充'}")
        if wiki_dir:
            print(f"  - Wiki 输出目录: {wiki_dir}")

        try:
            choice = input("  👉 是否进入配置向导？(y/n, 默认 y): ").strip().lower()
            if choice not in ["", "y", "yes"]:
                print("  ✅ 保留现有配置。")
                return

            SystemInitializer._configure_wiki_dir(global_config_file, config_data)
            SystemInitializer._configure_text_provider(global_config_file)
            config_data = _read_yaml(global_config_file)
            SystemInitializer._configure_qwen_ocr(global_config_file, config_data)
        except (KeyboardInterrupt, EOFError):
            print("\n  ⚠️ 交互输入被中断，已保留当前配置。")

    @staticmethod
    def _is_text_provider_ready(provider, config_data):
        if provider == "deepseek":
            return _configured_value(config_data, "deepseek", "api_key", "DEEPSEEK_API_KEY")
        if provider == "qwen":
            return _configured_value(config_data, "qwen", "api_key", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY")
        if provider == "openai_compatible":
            compat = config_data.get("openai_compatible", {}) or {}
            return (
                _configured_value(
                    config_data,
                    "openai_compatible",
                    "api_key",
                    *_openai_compatible_env_names(config_data)
                )
                and bool(compat.get("api_base"))
                and bool(compat.get("model"))
            )
        return False

    @staticmethod
    def _configure_wiki_dir(global_config_file, config_data):
        current = config_data.get("wiki_dir", "")
        suggested = os.path.expanduser("~/Documents/llm_wiki")
        if current and "/Users/byron/" not in current:
            suggested = current

        choice = input("  👉 是否配置 Wiki 输出目录？(y/n, 默认 y): ").strip().lower()
        if choice not in ["", "y", "yes"]:
            return

        wiki_dir = _prompt(suggested, "  📁 请输入 Wiki 输出目录 ")
        if wiki_dir:
            update_yaml_top_level(global_config_file, "wiki_dir", wiki_dir)
            os.makedirs(os.path.expanduser(wiki_dir), exist_ok=True)
            print(f"  ✅ Wiki 输出目录已配置为: {wiki_dir}")

    @staticmethod
    def _configure_text_provider(global_config_file):
        print("\n  请选择主要文本大模型通道。这个模型负责生成最终 Markdown 笔记：")
        print("   [1] OpenAI 兼容协议/中间件（推荐通用：OpenAI、OneAPI、NewAPI、LiteLLM、OpenRouter、Ollama 等）")
        print("   [2] DeepSeek 官方 API")
        print("   [3] Qwen 百炼原生通道（同一 Key 可兼顾视觉链路）")
        print("   [4] 暂时跳过")

        choice = input("  请输入序号 (默认 1): ").strip()
        if choice in ["", "1"]:
            SystemInitializer._configure_openai_compatible(global_config_file)
        elif choice == "2":
            SystemInitializer._configure_deepseek(global_config_file)
        elif choice == "3":
            SystemInitializer._configure_qwen_native(global_config_file)
        else:
            print("  ✅ 跳过文本大模型配置。")

    @staticmethod
    def _configure_deepseek(global_config_file):
        api_key = input("  🔑 请输入 DeepSeek API Key (留空则稍后用 DEEPSEEK_API_KEY 环境变量): ").strip()
        model = _prompt("deepseek-v4-pro", "  🤖 请输入 DeepSeek 模型名 ")
        updates = [
            (None, "provider", "deepseek"),
            ("deepseek", "model", model),
            ("deepseek", "api_base", "https://api.deepseek.com"),
        ]
        if api_key:
            updates.append(("deepseek", "api_key", api_key))
        update_yaml_many(global_config_file, updates)
        print("  🎉 已启用 DeepSeek 官方 API 作为文本总结通道。")

    @staticmethod
    def _configure_qwen_native(global_config_file):
        api_key = input("  🔑 请输入 DashScope/百炼 API Key (留空则稍后用 DASHSCOPE_API_KEY): ").strip()
        model = _prompt("qwen3-vl-plus", "  🤖 请输入 Qwen 模型名 ")
        updates = [
            (None, "provider", "qwen"),
            ("qwen", "model", model),
            ("qwen", "api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ]
        if api_key:
            updates.append(("qwen", "api_key", api_key))
        update_yaml_many(global_config_file, updates)
        print("  🎉 已启用 Qwen 百炼原生通道。")

    @staticmethod
    def _configure_openai_compatible(global_config_file):
        print("\n  请选择 OpenAI 兼容端点预设：")
        for index, preset in enumerate(OPENAI_COMPATIBLE_PRESETS, start=1):
            print(f"   [{index}] {preset['name']} ({preset['api_base'] or '手动填写'})")

        raw_choice = input("  请输入序号 (默认 1): ").strip()
        try:
            preset_index = int(raw_choice) - 1 if raw_choice else 0
            preset = OPENAI_COMPATIBLE_PRESETS[preset_index]
        except (ValueError, IndexError):
            preset = OPENAI_COMPATIBLE_PRESETS[0]

        print(f"  ✅ 已选择: {preset['name']}")
        api_base = _prompt(preset["api_base"], "  🌐 请输入 API Base URL ")
        model = _prompt(preset["model"], "  🤖 请输入模型名称 ")
        default_key = preset.get("default_api_key", "")
        api_key = input(
            f"  🔑 请输入 API Key（留空则稍后用 {preset['key_env']}；提示: {preset['key_hint']}）: "
        ).strip()
        if not api_key:
            api_key = default_key

        if not api_base or not model:
            print("  ⚠️ api_base 或 model 为空，已跳过 OpenAI 兼容配置。")
            return

        updates = [
            (None, "provider", "openai_compatible"),
            ("openai_compatible", "api_base", api_base),
            ("openai_compatible", "model", model),
        ]
        if api_key:
            updates.append(("openai_compatible", "api_key", api_key))
        update_yaml_many(global_config_file, updates)
        print("  🎉 已启用 OpenAI 兼容通道。后续可随时在 config.yaml 中更换网关或模型。")

    @staticmethod
    def _configure_qwen_ocr(global_config_file, config_data):
        qwen_ready = _configured_value(config_data, "qwen", "api_key", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY")
        ocr_mode = ((config_data.get("subtitle_ocr", {}) or {}).get("mode") or "hybrid").lower()
        if qwen_ready:
            print("\n  ✅ 已检测到 Qwen/DashScope Key，hybrid/cloud OCR 可直接使用。")
            return

        print("\n  当前默认硬字幕 OCR 是 hybrid：本地 RapidOCR 优先，低置信度帧用 Qwen-VL 云端兜底。")
        if ocr_mode in {"hybrid", "cloud"}:
            print("  ⚠️ 你尚未配置 DashScope Key；hybrid 会自动退化为本地 OCR，cloud 模式会报错。")

        choice = input("  👉 是否现在配置 DashScope Key 用于 Qwen-VL OCR 兜底？(y/n, 默认 n): ").strip().lower()
        if choice in ["y", "yes"]:
            api_key = input("  🔑 请输入 DASHSCOPE_API_KEY / BAILIAN_API_KEY: ").strip()
            model = _prompt("qwen3-vl-plus", "  👁️ 请输入视觉 OCR 模型名 ")
            if api_key:
                update_yaml_many(global_config_file, [
                    ("qwen", "api_key", api_key),
                    ("qwen", "ocr_model", model),
                    ("qwen", "api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                ])
                print("  🎉 Qwen-VL OCR 兜底配置完成。")
            else:
                print("  ⚠️ 输入为空，跳过 Qwen-VL 配置。")
        else:
            fallback = input("  👉 是否将默认 OCR 模式改为 local，避免无 Key 时触发云端兜底？(y/n, 默认 n): ").strip().lower()
            if fallback in ["y", "yes"]:
                update_yaml_config(global_config_file, "subtitle_ocr", "mode", "local")
                print("  ✅ 默认 OCR 模式已改为 local。")

    @staticmethod
    def run_init():
        print("\n" + "="*60)
        print(" 🚀 开始一键智能初始化 video-to-wiki 系统环境")
        print("="*60 + "\n")

        # 1. Initialize Global Config Directory and Copy Template
        global_config_dir = os.path.expanduser("~/.config/video-to-wiki")
        global_config_file = os.path.join(global_config_dir, "config.yaml")
        
        # Locate project config template. Prefer the example template so new
        # users do not inherit a contributor's local machine paths.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_candidates = [
            os.path.join(project_root, "config.yaml.example"),
            os.path.join(project_root, "config.yaml"),
        ]
        template_config = next((p for p in template_candidates if os.path.exists(p)), None)

        # Create global config directory
        if not os.path.exists(global_config_dir):
            try:
                os.makedirs(global_config_dir)
                print(f" 📂 创建全局配置文件夹成功: {global_config_dir}")
            except Exception as e:
                print(f" ❌ 创建全局配置文件夹失败: {e}")
        else:
            print(f" ✅ 全局配置文件夹已存在: {global_config_dir}")

        # Copy template config
        if not os.path.exists(global_config_file):
            if template_config:
                try:
                    shutil.copy(template_config, global_config_file)
                    print(f" 📄 挂载全局默认配置文件成功: {global_config_file}")
                except Exception as e:
                    print(f" ❌ 挂载配置文件失败: {e}")
            else:
                try:
                    with open(global_config_file, "w", encoding="utf-8") as f:
                        f.write(DEFAULT_CONFIG_TEMPLATE)
                    print(f" 📄 未找到外部模板，已写入内置最小配置文件: {global_config_file}")
                except Exception as e:
                    print(f" ❌ 写入内置配置文件失败: {e}")
        else:
            print(f" ✅ 全局配置文件已存在，保护您的现有设置跳过覆盖: {global_config_file}")

        # 2. Check System Dependencies (ffmpeg, yt-dlp)
        print("\n 🔍 开始检测系统底层音视频依赖...")

        # Check ffmpeg
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            print("  - ffmpeg: ✅ 已安装且可用")
        except Exception:
            print("  - ffmpeg: ❌ 未检测到。请运行 'brew install ffmpeg' 进行安装。")

        # Check yt-dlp
        try:
            from src.downloader import VideoDownloader
            downloader = VideoDownloader(temp_dir="/tmp")
            yt_dlp_path = downloader._find_yt_dlp()
            print(f"  - yt-dlp: ✅ 已安装且可用 ({yt_dlp_path[0]})")
        except Exception:
            print("  - yt-dlp: ❌ 未检测到，运行时可能会报错。")

        # 3. Path PATH Configuration Verification
        print("\n 🔍 开始检测系统 Shell 环境变量 (PATH)...")
        # Find where python installs user binaries
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        user_bin_dir = os.path.expanduser(f"~/Library/Python/{python_version}/bin")
        
        # Check if user_bin_dir is in active PATH
        active_paths = os.environ.get("PATH", "").split(os.pathsep)
        is_in_path = any(os.path.abspath(p) == os.path.abspath(user_bin_dir) for p in active_paths if p and os.path.exists(p))

        if is_in_path:
            print(" ✅ 全局命令运行路径已正确配置在您的系统 PATH 中！")
        else:
            print(" ⚠️ 检测到 Python 用户二进制路径未注入您系统的 PATH 中。")
            shell = os.environ.get("SHELL", "")
            shell_rc = None
            if "zsh" in shell:
                shell_rc = os.path.expanduser("~/.zshrc")
            elif "bash" in shell:
                shell_rc = os.path.expanduser("~/.bash_profile")
            
            if shell_rc:
                print(f"    👉 正在为您将该路径追加到 shell 配置文件 {shell_rc} 中...")
                try:
                    export_line = f'\n# video-to-wiki CLI binary path\nexport PATH="$PATH:{user_bin_dir}"\n'
                    # Check if already written
                    already_written = False
                    if os.path.exists(shell_rc):
                        with open(shell_rc, "r", encoding="utf-8", errors="ignore") as rc_file:
                            content = rc_file.read()
                            if user_bin_dir in content:
                                already_written = True
                    
                    if not already_written:
                        with open(shell_rc, "a", encoding="utf-8") as rc_file:
                            rc_file.write(export_line)
                        print(f"    🎉 自动修改 PATH 成功！请在新打开的终端中直接键入 'video-to-wiki' 运行。")
                    else:
                        print(f"    ✅ 配置文件中已存在该路径设置，跳过写入。")
                except Exception as e:
                    print(f"    ❌ 自动修改配置文件失败: {e}。请手动在 {shell_rc} 末尾加入: export PATH=\"$PATH:{user_bin_dir}\"")
            else:
                print(f"    ❌ 无法检测到您的默认 Shell。请手动将 '{user_bin_dir}' 加入到您的 shell 路径中。")

        # 4. Interactive Configuration setup
        SystemInitializer._run_user_config_wizard(global_config_file)

        # 5. Success summary
        print("\n" + "="*60)
        print(" 🎉  系统初始化流程圆满结束！")
        print("="*60)
        print(f" 📂 全局配置文件: {global_config_file}")
        print(" 💡 贴心提示: 您可以直接使用文本编辑器修改该配置，调整您的 API 密钥与大模型参数。")
        if not is_in_path:
            print(" 🔄 请在新窗口中运行，或运行 'source ~/.zshrc'（或重启您的终端）以激活全局直接调用。")
        print("="*60 + "\n")
