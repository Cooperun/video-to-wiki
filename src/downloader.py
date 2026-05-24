import os
import re
import subprocess
import logging
import hashlib
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class VideoDownloader:
    def __init__(self, temp_dir):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)

    def sanitize_title(self, title):
        """
        Sanitize video titles to be extremely markdown-friendly and safe for filenames.
        Replaces whitespace, Chinese punctuation, and special symbols with underscores.
        """
        # Remove common illegal characters in files
        title = re.sub(r'[\/\\:\*\?"<>\|\s]', '_', title)
        # Remove Chinese punctuation
        title = re.sub(r'[，。！？、：；“”‘’（）《》【】]', '_', title)
        # Condense multiple underscores into one
        title = re.sub(r'_+', '_', title)
        return title.strip('_')

    def is_url(self, path_or_url):
        return path_or_url.startswith("http://") or path_or_url.startswith("https://")

    def detect_platform(self, path_or_url):
        if not self.is_url(path_or_url):
            return "local"

        host = urlparse(path_or_url).netloc.lower()
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "bilibili.com" in host or "b23.tv" in host:
            return "bilibili"
        if "xiaohongshu.com" in host or "xhslink.com" in host:
            return "xiaohongshu"
        if "weixin.qq.com" in host or "video.qq.com" in host:
            return "wechat"
        return "unknown"

    def build_source_id(self, path_or_url):
        source_key = path_or_url
        if not self.is_url(path_or_url):
            source_key = os.path.abspath(path_or_url)
        return hashlib.sha256(source_key.encode("utf-8")).hexdigest()

    def _find_yt_dlp(self):
        """
        Dynamically locate the best yt-dlp executable.
        Prioritizes system-installed binaries (which match local user configurations)
        over the python module package fallback.
        """
        # 1. Try default system PATH yt-dlp
        try:
            res = subprocess.run(["yt-dlp", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return ["yt-dlp"]
        except Exception:
            pass

        # 2. Try common Mac/Linux local installation paths
        common_paths = [
            os.path.expanduser("~/.local/bin/yt-dlp"),
            "/opt/homebrew/bin/yt-dlp",
            "/usr/local/bin/yt-dlp"
        ]
        for p in common_paths:
            if os.path.exists(p) and os.access(p, os.X_OK):
                return [p]

        # 3. Fallback to python3 -m yt_dlp module
        return ["python3", "-m", "yt_dlp"]

    def process_input(self, path_or_url):
        """
        Returns (local_video_path, sanitized_video_title, source_metadata)
        """
        platform = self.detect_platform(path_or_url)
        source_metadata = {
            "source_id": self.build_source_id(path_or_url),
            "source_url": path_or_url if self.is_url(path_or_url) else "",
            "source_path": os.path.abspath(path_or_url) if not self.is_url(path_or_url) else "",
            "platform": platform,
            "download_status": "local" if platform == "local" else "pending",
            "raw_title": "",
        }

        if not self.is_url(path_or_url):
            # Local File Mode
            if not os.path.exists(path_or_url):
                raise FileNotFoundError(f"本地视频文件不存在: {path_or_url}")
            
            base_name = os.path.basename(path_or_url)
            title_only, ext = os.path.splitext(base_name)
            sanitized_title = self.sanitize_title(title_only)
            source_metadata["raw_title"] = title_only
            logging.info(f"使用本地视频文件: {path_or_url} (标题: {sanitized_title})")
            return os.path.abspath(path_or_url), sanitized_title, source_metadata
        
        # URL Mode
        url = path_or_url
        
        # WeChat Channels detection fallback
        if "channels.weixin.qq.com" in url or "finder.video.qq.com" in url:
            raise ValueError(
                "检测到微信视频号链接。\n"
                "警告: 由于微信闭源安全机制，命令行下载工具(yt-dlp)通常不支持直接解析下载视频号链接。\n"
                "建议方案: 请在本机/手机上播放视频，使用录屏、手机备份或浏览器导出方式将视频保存为 .mp4 文件，"
                "然后使用本地文件模式运行: \n"
                "   python main.py --file /path/to/video.mp4"
            )

        logging.info(f"检测到视频 URL: {url}，正在获取视频信息...")
        
        yt_dlp_cmd = self._find_yt_dlp()
        logging.info(f"使用 yt-dlp 执行命令: {' '.join(yt_dlp_cmd)}")
        
        # Extract title first using yt-dlp --get-title
        try:
            cmd_info = yt_dlp_cmd + ["--get-title", url]
            res = subprocess.run(cmd_info, capture_output=True, text=True, check=True)
            raw_title = res.stdout.strip()
            if not raw_title:
                raw_title = "downloaded_video"
        except Exception as e:
            logging.warning(f"获取视频标题失败 ({e})，将使用默认标题。")
            raw_title = "downloaded_video"

        sanitized_title = self.sanitize_title(raw_title)
        source_metadata["raw_title"] = raw_title
        output_template = os.path.join(self.temp_dir, f"{sanitized_title}.%(ext)s")
        
        # Build yt-dlp download command.
        # We download maximum 1080p to keep processing fast and light.
        logging.info(f"开始使用 yt-dlp 下载视频: {raw_title} ...")
        cmd_download = yt_dlp_cmd + [
            "-f", "bestvideo[height<=1080]+bestaudio/best",
            "--merge-output-format", "mp4",
            "-o", output_template,
            url
        ]
        
        try:
            subprocess.run(cmd_download, check=True)
        except subprocess.CalledProcessError as e:
            # Fallback to general download without merge or restrict if it fails
            logging.warning("按分辨率筛选下载失败，尝试默认最佳格式下载...")
            cmd_fallback = yt_dlp_cmd + [
                "-o", output_template,
                url
            ]
            subprocess.run(cmd_fallback, check=True)

        # Find the downloaded file (it might have .mp4, .mkv, etc. depending on merge)
        # Search the temp_dir for a file matching sanitized_title
        for file in os.listdir(self.temp_dir):
            name, ext = os.path.splitext(file)
            if name == sanitized_title and ext.lower() in [".mp4", ".mkv", ".webm", ".avi", ".mov"]:
                target_path = os.path.abspath(os.path.join(self.temp_dir, file))
                source_metadata["download_status"] = "ok"
                logging.info(f"视频下载成功，本地路径: {target_path}")
                return target_path, sanitized_title, source_metadata

        raise FileNotFoundError(f"未能在临时文件夹中找到下载成功的视频文件。")

    def download_subtitles(self, url, sanitized_title):
        """
        Fetch available subtitles using yt-dlp.
        Returns: list of structured segments or None if no subtitle tracks found.
        """
        yt_dlp_cmd = self._find_yt_dlp()
        
        # We save subtitle as [sanitized_title].[lang].[ext]
        output_template = os.path.join(self.temp_dir, sanitized_title)
        
        cmd = yt_dlp_cmd + [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "zh-Hans,zh-CN,zh-HK,zh-TW,zh,en",
            "--sub-format", "vtt/srt",
            "--skip-download",
            "-o", output_template,
            url
        ]
        
        logging.info(f"Subtitle-First: 尝试使用 yt-dlp 获取在线字幕...")
        try:
            # Short timeout to ensure we fail fast if network blocks
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        except Exception as e:
            logging.warning(f"获取在线字幕下载失败或超时: {e}")
            return None
            
        # Scan temp_dir for downloaded subtitle files
        sub_file = None
        for file in os.listdir(self.temp_dir):
            if file.startswith(sanitized_title) and file.endswith((".vtt", ".srt")):
                # Check for Chinese preference
                if "zh" in file.lower() or "hans" in file.lower() or "cn" in file.lower():
                    sub_file = os.path.join(self.temp_dir, file)
                    break
                else:
                    sub_file = os.path.join(self.temp_dir, file)
                    
        if not sub_file:
            logging.info("未能在该视频上匹配到任何在线中/英文字幕。")
            return None
            
        try:
            from src.subtitle_parser import SubtitleParser
            parser = SubtitleParser()
            segments = parser.parse(sub_file)
            
            # Clean up the downloaded subtitle file
            if os.path.exists(sub_file):
                os.remove(sub_file)
                
            return segments
        except Exception as e:
            logging.warning(f"字幕解析异常: {e}")
            if sub_file and os.path.exists(sub_file):
                os.remove(sub_file)
            return None
