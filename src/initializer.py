import os
import sys
import shutil
import subprocess
import logging
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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
    def run_init():
        print("\n" + "="*60)
        print(" 🚀 开始一键智能初始化 video-to-wiki 系统环境")
        print("="*60 + "\n")

        # 1. Initialize Global Config Directory and Copy Template
        global_config_dir = os.path.expanduser("~/.config/video-to-wiki")
        global_config_file = os.path.join(global_config_dir, "config.yaml")
        
        # Locate project config template
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_config = os.path.join(project_root, "config.yaml")

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
            if os.path.exists(template_config):
                try:
                    shutil.copy(template_config, global_config_file)
                    print(f" 📄 挂载全局默认配置文件成功: {global_config_file}")
                except Exception as e:
                    print(f" ❌ 挂载配置文件失败: {e}")
            else:
                print(" ⚠️ 警告: 未在安装包中找到 config.yaml 模板，请手动创建配置文件。")
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
        print("\n 🔍 开始检测大模型 API 密钥状态...")
        has_deepseek_key = False
        has_openai_key = False
        
        # Read current config to see if key is configured
        if os.path.exists(global_config_file):
            try:
                with open(global_config_file, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                has_deepseek_key = bool(config_data.get("deepseek", {}).get("api_key"))
                has_openai_key = bool(config_data.get("openai_compatible", {}).get("api_key"))
            except Exception:
                pass

        env_deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
        env_openai_key = os.environ.get("OPENAI_API_KEY")

        if (has_deepseek_key or env_deepseek_key) or (has_openai_key or env_openai_key):
            print(" ✅ 已检测到您本机的 API 密钥配置，跳过交互式密钥设置。")
        else:
            print(" ⚠️ 检测到您尚未配置大模型 API 密钥。")
            try:
                choice = input("  👉 是否现在交互式配置大模型 API 密钥？(y/n, 默认 y): ").strip().lower()
                if choice in ["", "y", "yes"]:
                    print("\n  请选择您要配置的大模型通道:")
                    print("   [1] DeepSeek 官方 API (推荐思考总结驱动)")
                    print("   [2] OpenAI 兼容协议网关/中间件 (适用于 OneAPI, NewAPI, Ollama, 智谱等)")
                    
                    sub_choice = input("  请输入序号 (1 或 2, 默认 1): ").strip()
                    if sub_choice in ["", "1"]:
                        api_key = input("  🔑 请输入您的 DeepSeek API 密钥 (DEEPSEEK_API_KEY): ").strip()
                        if api_key:
                            update_yaml_config(global_config_file, "deepseek", "api_key", api_key)
                            update_yaml_top_level(global_config_file, "provider", "deepseek")
                            print("  🎉 DeepSeek 密钥配置成功！默认大模型驱动已自动激活为 'deepseek'。")
                        else:
                            print("  ⚠️ 输入为空，跳过配置。")
                    elif sub_choice == "2":
                        api_base = input("  🌐 请输入您的网关 Base URL (如 https://api.newapi.com/v1): ").strip()
                        api_key = input("  🔑 请输入您的网关 API 密钥 (OPENAI_API_KEY): ").strip()
                        model = input("  🤖 请输入调用的模型名称 (如 deepseek-chat 或 gpt-4o): ").strip()
                        
                        if api_base and api_key and model:
                            update_yaml_config(global_config_file, "openai_compatible", "api_base", api_base)
                            update_yaml_config(global_config_file, "openai_compatible", "api_key", api_key)
                            update_yaml_config(global_config_file, "openai_compatible", "model", model)
                            update_yaml_top_level(global_config_file, "provider", "openai_compatible")
                            print("  🎉 OpenAI 兼容网关配置成功！默认大模型驱动已自动激活为 'openai_compatible'。")
                        else:
                            print("  ⚠️ 输入不完整，跳过配置。")
            except (KeyboardInterrupt, EOFError):
                print("\n  ⚠️ 交互输入被中断，已跳过密钥配置。")

        # 5. Success summary
        print("\n" + "="*60)
        print(" 🎉  系统初始化流程圆满结束！")
        print("="*60)
        print(f" 📂 全局配置文件: {global_config_file}")
        print(" 💡 贴心提示: 您可以直接使用文本编辑器修改该配置，调整您的 API 密钥与大模型参数。")
        if not is_in_path:
            print(" 🔄 请在新窗口中运行，或运行 'source ~/.zshrc'（或重启您的终端）以激活全局直接调用。")
        print("="*60 + "\n")
