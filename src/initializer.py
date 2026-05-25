import os
import sys
import shutil
import subprocess
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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

        # 4. Success summary
        print("\n" + "="*60)
        print(" 🎉  系统初始化流程圆满结束！")
        print("="*60)
        print(f" 📂 全局配置文件: {global_config_file}")
        print(" 💡 贴心提示: 您可以直接使用文本编辑器修改该配置，调整您的 API 密钥与大模型参数。")
        if not is_in_path:
            print(" 🔄 请在新窗口中运行，或运行 'source ~/.zshrc'（或重启您的终端）以激活全局直接调用。")
        print("="*60 + "\n")
