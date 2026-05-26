import os
import re
import json
import time
import logging
import base64
import subprocess
from openai import OpenAI
from src.subtitle_region import SubtitleRegionDetector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class VisualSubtitleExtractor:
    """
    VisualSubtitleExtractor 是一个独立模块，用于从视频帧中提取硬编码字幕。

    支持三种 OCR 后端模式：
      - 'cloud' (默认)：调用云端 Qwen-VL API，精度最高，需要网络和 API Key
      - 'local'       ：调用本地 OCR（默认 RapidOCR），完全离线，零 API 成本
      - 'hybrid'      ：本地 OCR 初筛，置信度低且有云端 Key 时回退到 Qwen-VL 精修；
                        没有云端 Key 时自动退化为 local

    使用像素差分过滤最小化 OCR 调用次数，并聚合结果为 SRT / Markdown 格式。
    """

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen3-vl-plus",
        temp_dir: str = "./temp",
        ocr_mode: str = "cloud",
        local_engine: str = "rapidocr",
        local_confidence_threshold: float = 0.5,
    ):
        """
        初始化字幕提取器。

        Args:
            api_key: Qwen-VL API Key（cloud 模式必需；hybrid 模式可选，有则兜底）
            api_base: Qwen-VL API Base URL
            model: Qwen-VL 模型名称
            temp_dir: 临时文件目录
            ocr_mode: OCR 后端模式，'cloud' / 'local' / 'hybrid'
            local_engine: 本地 OCR 引擎，'rapidocr' / 'easyocr' / 'auto'
            local_confidence_threshold: hybrid 模式中，本地置信度低于此值时触发云端精修（0.0~1.0）
        """
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.temp_dir = temp_dir
        self.ocr_mode = ocr_mode.lower().strip()
        self.local_engine = (local_engine or "rapidocr").lower().strip()
        self.local_confidence_threshold = local_confidence_threshold
        self.cloud_available = bool(self.api_key)

        if self.ocr_mode == "hybrid" and not self.cloud_available:
            logging.warning(
                "hybrid OCR 未配置云端视觉 API Key，将自动退化为本地 OCR；"
                "低置信度帧不会触发云端精修。"
            )

        # Temp dir specifically for subtitle frames
        self.frames_dir = os.path.join(self.temp_dir, "subtitle_frames")
        os.makedirs(self.frames_dir, exist_ok=True)

        # Cloud client (lazily initialized only when needed)
        self._cloud_client = None

        # Local OCR backend (lazily initialized only when needed)
        self._local_backend = None

        # Telemetry metrics tracking
        self.ocr_called_count = 0           # Total OCR API calls (cloud)
        self.ocr_local_count = 0            # Total local OCR calls
        self.ocr_duplicate_count = 0        # Pixel diff triggered but text was same
        self.ocr_hybrid_escalated_count = 0 # Hybrid: local low-confidence → cloud escalation
        self.subtitle_timeline = []
        self.subtitle_region_box = None
        self.subtitle_region_confidence = 0.0
        self.region_detector = SubtitleRegionDetector()

        logging.info(
            f"🔮 [VisualSubtitleExtractor] 初始化完成。OCR 模式: [{self.ocr_mode.upper()}]"
            + (f" | 本地置信度回退阈值: {self.local_confidence_threshold}" if self.ocr_mode == "hybrid" else "")
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Backend Initializers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_cloud_client(self):
        """懒加载云端 Qwen-VL 客户端（只在 cloud/hybrid 模式下初始化）。"""
        if self._cloud_client is None:
            if not self.api_key:
                raise ValueError(
                    "cloud/hybrid 模式需要 Qwen-VL API Key。"
                    "请在 config.yaml 中配置 qwen.api_key 或设置环境变量 DASHSCOPE_API_KEY。"
                )
            self._cloud_client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._cloud_client

    def _get_local_backend(self):
        """懒加载本地 OCR 后端（只在 local/hybrid 模式下初始化）。"""
        if self._local_backend is None:
            from src.local_ocr import LocalOCRBackend
            self._local_backend = LocalOCRBackend(
                languages=["ch_sim", "en"],
                gpu=None,
                engine=self.local_engine,
            )
        return self._local_backend

    # ─────────────────────────────────────────────────────────────────────────
    # Frame Extraction
    # ─────────────────────────────────────────────────────────────────────────

    def extract_frames_interval(self, video_path, interval_sec=2.0, max_width=1280):
        """
        使用单次优化的 ffmpeg 命令，按固定间距对视频进行硬字幕抽帧。
        通过解析 stderr 的 showinfo 滤镜输出，获取每帧的精确视频时间戳。
        """
        # Clean existing subtitle frames
        if os.path.exists(self.frames_dir):
            import shutil
            try:
                shutil.rmtree(self.frames_dir)
            except Exception:
                pass
        os.makedirs(self.frames_dir, exist_ok=True)

        logging.info(f"🎬 [Extractor] 开始按 {interval_sec} 秒等间距对视频进行硬字幕抽帧...")

        # Use fps filter to sample at interval (e.g. fps=1/2 means 1 frame every 2 seconds)
        select_filter = f"fps=1/{interval_sec},showinfo,scale={max_width}:-1"
        output_pattern = os.path.join(self.frames_dir, "sub_frame_%04d.jpg")

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", select_filter,
            "-fps_mode", "vfr",
            "-q:v", "4",
            output_pattern
        ]

        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        stderr_output = []

        while True:
            line = process.stderr.readline()
            if not line:
                break
            stderr_output.append(line)

        process.wait()

        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg 抽帧执行出错，错误码: {process.returncode}")

        # Parse timestamps of selected frames
        timestamps = {}
        pattern = re.compile(r"n:\s*(\d+)\s+pts:\s*\d+\s+pts_time:([0-9.]+)")

        for line in stderr_output:
            match = pattern.search(line)
            if match:
                frame_idx = int(match.group(1))
                pts_time = float(match.group(2))
                timestamps[frame_idx + 1] = pts_time

        # Rename physical files to self-documenting timestamps
        renamed_files = []
        actual_files = sorted([f for f in os.listdir(self.frames_dir) if f.startswith("sub_frame_") and f.endswith(".jpg")])

        for file in actual_files:
            idx_match = re.search(r"sub_frame_(\d+)\.jpg", file)
            if idx_match:
                idx = int(idx_match.group(1))
                if idx in timestamps:
                    seconds = timestamps[idx]
                    formatted_time = self.format_seconds(seconds)
                    new_filename = f"frame_{idx:04d}_time_{formatted_time}.jpg"

                    old_path = os.path.join(self.frames_dir, file)
                    new_path = os.path.join(self.frames_dir, new_filename)

                    os.rename(old_path, new_path)
                    renamed_files.append((new_filename, formatted_time, seconds))
                else:
                    logging.warning(f"无法确定 sub_frame_{idx:04d}.jpg 的时间轴点，跳过。")

        logging.info(f"🎬 [Extractor] 抽帧完成，共生成 {len(renamed_files)} 张等间距字幕候选帧。")
        return sorted(renamed_files, key=lambda x: x[2])

    # ─────────────────────────────────────────────────────────────────────────
    # Frame Analysis Utilities
    # ─────────────────────────────────────────────────────────────────────────

    def format_seconds(self, seconds_float):
        total_seconds = int(float(seconds_float))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}_{m:02d}_{s:02d}"

    def is_subtitle_changed(self, prev_path, curr_path, box=None, threshold=6.0):
        """
        使用均方误差 (MSE) 比较相邻帧的字幕区域（底部 15%）。
        超过阈值 → 字幕发生变化；否则复用上一帧 OCR 结果。
        """
        if not prev_path or not os.path.exists(prev_path):
            return True

        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return True

        try:
            im1 = Image.open(prev_path)
            im2 = Image.open(curr_path)

            width, height = im1.size

            # Default box: lower subtitle band. Keep it wide enough for videos
            # that place large captions above the absolute bottom edge.
            if not box:
                box = (0, int(height * 0.72), width, int(height * 0.96))

            crop1 = im1.crop(box).convert("L")
            crop2 = im2.crop(box).convert("L")

            arr1 = np.array(crop1, dtype=np.float32)
            arr2 = np.array(crop2, dtype=np.float32)

            mse = np.mean((arr1 - arr2) ** 2)
            return mse > threshold
        except Exception as e:
            logging.warning(f"帧比对异常 (默认强制进行 OCR): {e}")
            return True

    def is_subtitle_empty(self, image_path, box=None, std_dev_threshold=12.0):
        """
        检测字幕区域是否为纯色背景（无文字）。
        使用灰度像素的标准差作为轻量级启发式——文字区域标准差通常 > 20，
        纯背景 < 12。
        """
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return False

        try:
            im = Image.open(image_path)
            width, height = im.size
            if not box:
                box = (0, int(height * 0.72), width, int(height * 0.96))
            crop = im.crop(box).convert("L")
            arr = np.array(crop, dtype=np.float32)
            std_dev = np.std(arr)
            return std_dev < std_dev_threshold
        except Exception as e:
            logging.warning(f"本地空白字幕区校验异常: {e}")
            return False

    def encode_image_base64(self, image_path, box=None):
        if box:
            try:
                from io import BytesIO
                from PIL import Image

                im = Image.open(image_path)
                crop = im.crop(box)
                buffer = BytesIO()
                crop.save(buffer, format="JPEG", quality=92)
                return base64.b64encode(buffer.getvalue()).decode("utf-8")
            except Exception as e:
                logging.warning(f"字幕区域裁剪编码失败，将回退整帧上传: {e}")

        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # ─────────────────────────────────────────────────────────────────────────
    # OCR Backends
    # ─────────────────────────────────────────────────────────────────────────

    def ocr_subtitle_cloud(self, image_path, box=None):
        """
        【云端后端】调用 Qwen-VL 进行高精度 OCR 识别，返回字幕文本。
        """
        if not os.path.exists(image_path):
            return "None"

        b64_str = self.encode_image_base64(image_path, box=box)
        client = self._get_cloud_client()

        if box:
            system_prompt = (
                "你是一个高精度的硬字幕文本OCR提取专家。\n"
                "我会给你一张已经裁剪到视频硬字幕区域的图片。请识别并提取其中最主要的一行中文字幕。\n"
                "请严格遵守以下规则：\n"
                "1. 只输出识别到的字幕文本内容，禁止加任何标点，禁止加任何 Markdown 代码围栏。\n"
                "2. 如果图片中完全没有硬字幕，或者字幕非常模糊无法看清，请仅输出单个单词 'None'。\n"
                "3. 排除进度条、鼠标、页面文字、代码块、标题或非字幕字符，只输出说话字幕。"
            )
            user_text = "请识别并输出这张字幕区域裁剪图中的中文硬字幕文字。若无字幕，只输出 'None'。"
        else:
            system_prompt = (
            "你是一个高精度的硬字幕文本OCR提取专家。\n"
            "我会给你一张视频帧截图。请仔细观察截图的最底部位置，识别并提取视频中嵌入的这一行【中文硬字幕文本】。\n"
            "请严格遵守以下规则：\n"
            "1. 只输出识别到的字幕文本内容，禁止加任何标点，禁止加任何 Markdown 代码围栏。\n"
            "2. 如果底部完全没有硬字幕，或者字幕非常模糊无法看清，请仅输出单个单词 'None'。\n"
            "3. 排除画面中上部的网页文字、代码块、顶部标题或非字幕字符，只看最底部正中央的那一排硬字幕文字！"
            )
            user_text = "请识别并输出该视频帧底部的中文硬字幕文字。若无字幕，只输出 'None'。"

        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}}
        ]

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=128
            )
            response_text = response.choices[0].message.content.strip()

            # Clean think blocks and code fences
            response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE).strip()
            response_text = response_text.replace("`", "").strip()

            if response_text.lower() in ["none", "[none]", "none.", "无", "无字幕"]:
                return "None"

            return response_text
        except Exception as e:
            logging.warning(f"Qwen-VL 硬字幕 OCR 调用异常: {e}")
            return "None"

    def ocr_subtitle_local(self, image_path, box=None):
        """
        【本地后端】调用本地 OCR 引擎进行离线识别，返回 (text, confidence) 元组。
        """
        try:
            backend = self._get_local_backend()
            text, confidence = backend.recognize(image_path, box=box)
            return text, confidence
        except Exception as e:
            logging.warning(f"[LocalOCR] 本地 OCR 识别异常: {e}")
            return "None", 0.0

    def ocr_subtitle(self, image_path, box=None):
        """
        根据 ocr_mode 路由到对应后端，返回最终字幕文本字符串。

        路由策略：
          - 'cloud'  → 直接调用 Qwen-VL
          - 'local'  → 直接调用本地 OCR（默认 RapidOCR）
          - 'hybrid' → 先调用本地 OCR；若置信度 < local_confidence_threshold 且存在
                       云端 Key，则自动升级到 Qwen-VL 精修；否则保留本地结果
        """
        if self.ocr_mode == "local":
            text, confidence = self.ocr_subtitle_local(image_path, box=box)
            self.ocr_local_count += 1
            logging.debug(f"  [LocalOCR] 识别结果: '{text}' (置信度: {confidence:.3f})")
            return text

        elif self.ocr_mode == "hybrid":
            # Step 1: 本地 OCR 初筛
            local_text, local_confidence = self.ocr_subtitle_local(image_path, box=box)
            self.ocr_local_count += 1

            # Step 2: 置信度够高 → 直接采用本地结果
            if local_confidence >= self.local_confidence_threshold and local_text != "None":
                logging.debug(
                    f"  [HybridOCR] ✅ 本地结果采纳 (置信度 {local_confidence:.3f} >= {self.local_confidence_threshold}): '{local_text}'"
                )
                return local_text

            if not self.cloud_available:
                logging.debug(
                    f"  [HybridOCR] 云端 Key 缺失，保留本地结果: '{local_text}' "
                    f"(置信度: {local_confidence:.3f})"
                )
                return local_text

            # Step 3: 置信度不足 OR 本地无结果 → 升级到云端精修
            self.ocr_called_count += 1
            self.ocr_hybrid_escalated_count += 1
            logging.info(
                f"  [HybridOCR] ⬆️ 本地置信度 {local_confidence:.3f} < {self.local_confidence_threshold}，"
                f"升级到云端 Qwen-VL 精修..."
            )
            cloud_text = self.ocr_subtitle_cloud(image_path, box=box)
            return cloud_text

        else:
            # 默认 'cloud' 模式
            self.ocr_called_count += 1
            return self.ocr_subtitle_cloud(image_path, box=box)

    # ─────────────────────────────────────────────────────────────────────────
    # Output Compilation
    # ─────────────────────────────────────────────────────────────────────────

    def compile_srt(self, subtitle_timeline, interval_sec=2.0):
        """
        将时间线片段编译为标准 SRT 字幕文件格式，
        合并连续相同文字的帧为单个字幕块。
        """
        if not subtitle_timeline:
            return ""

        merged_timeline = []
        current_chunk = None

        for item in subtitle_timeline:
            sec = item["seconds"]
            text = item["text"].strip()

            if not text or text.lower() == "none":
                if current_chunk:
                    current_chunk["end"] = sec
                    merged_timeline.append(current_chunk)
                    current_chunk = None
                continue

            if current_chunk is None:
                current_chunk = {
                    "start": sec,
                    "end": sec + interval_sec,
                    "text": text
                }
            else:
                if current_chunk["text"] == text:
                    current_chunk["end"] = sec + interval_sec
                else:
                    current_chunk["end"] = sec
                    merged_timeline.append(current_chunk)
                    current_chunk = {
                        "start": sec,
                        "end": sec + interval_sec,
                        "text": text
                    }

        if current_chunk:
            merged_timeline.append(current_chunk)

        srt_lines = []
        for idx, chunk in enumerate(merged_timeline, 1):
            start_str = self.format_srt_timestamp(chunk["start"])
            end_str = self.format_srt_timestamp(chunk["end"])
            srt_lines.append(f"{idx}")
            srt_lines.append(f"{start_str} --> {end_str}")
            srt_lines.append(chunk["text"])
            srt_lines.append("")

        return "\n".join(srt_lines)

    def format_srt_timestamp(self, seconds):
        total_milliseconds = int(float(seconds) * 1000)
        h = total_milliseconds // 3600000
        m = (total_milliseconds % 3600000) // 60000
        s = (total_milliseconds % 60000) // 1000
        ms = total_milliseconds % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def compile_markdown_timeline(self, subtitle_timeline, interval_sec=2.0):
        """
        将时间线片段编译为人类可读的 Markdown 时间轴列表。
        """
        if not subtitle_timeline:
            return ""

        merged_timeline = []
        current_chunk = None

        for item in subtitle_timeline:
            sec = item["seconds"]
            text = item["text"].strip()

            if not text or text.lower() == "none":
                if current_chunk:
                    current_chunk["end"] = sec
                    merged_timeline.append(current_chunk)
                    current_chunk = None
                continue

            if current_chunk is None:
                current_chunk = {
                    "start": sec,
                    "end": sec + interval_sec,
                    "text": text
                }
            else:
                if current_chunk["text"] == text:
                    current_chunk["end"] = sec + interval_sec
                else:
                    current_chunk["end"] = sec
                    merged_timeline.append(current_chunk)
                    current_chunk = {
                        "start": sec,
                        "end": sec + interval_sec,
                        "text": text
                    }

        if current_chunk:
            merged_timeline.append(current_chunk)

        mode_label = {
            "cloud": "☁️ 云端 Qwen-VL OCR",
            "local": "💻 本地 OCR（离线）",
            "hybrid": "🔀 混合模式（本地优先 + 云端精修）",
        }.get(self.ocr_mode, self.ocr_mode)

        md_lines = [
            "# 📹 视频画面纯视觉硬字幕提取时间线\n",
            f"- **提取时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **提取策略**: {mode_label}",
            f"- **采样精度**: 每 {interval_sec} 秒等间距检测一次\n",
            "## 📝 视觉字幕流水线列表\n"
        ]

        for chunk in merged_timeline:
            h = int(chunk["start"]) // 3600
            m = (int(chunk["start"]) % 3600) // 60
            s = int(chunk["start"]) % 60
            timestamp = f"{h:02d}:{m:02d}:{s:02d}"
            md_lines.append(f"- **[{timestamp}]** {chunk['text']}")

        return "\n".join(md_lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Main Execution Entry Point
    # ─────────────────────────────────────────────────────────────────────────

    def run_extraction(self, video_path, interval_sec=2.0, std_dev_threshold=12.0):
        """
        主执行流程编排器：
          1. 按固定间距抽帧
          2. 本地方差检测，快速跳过空白帧（无字幕区域）
          3. 像素差分过滤，跳过未变化帧
          4. 按设定 OCR 模式路由到对应后端识别字幕
          5. 编译并返回 SRT 和 Markdown 时间线内容
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"未找到本地视频文件: {video_path}")

        # 1. 抽帧
        renamed_files = self.extract_frames_interval(video_path, interval_sec)
        if not renamed_files:
            logging.warning("没有成功提取到任何视频帧。")
            return "", ""

        frame_paths = [os.path.join(self.frames_dir, filename) for filename, _, _ in renamed_files]
        detected_region = self.region_detector.detect_from_paths(frame_paths)
        if detected_region:
            self.subtitle_region_box = detected_region.box
            self.subtitle_region_confidence = detected_region.confidence
            logging.info(
                "📐 [Extractor] 自动定位字幕区域: box=%s, confidence=%.2f, source=%s",
                self.subtitle_region_box,
                self.subtitle_region_confidence,
                detected_region.source,
            )
        else:
            self.subtitle_region_box = None
            self.subtitle_region_confidence = 0.0
            logging.info("📐 [Extractor] 未能自动定位字幕区域，将使用默认下方字幕裁剪框。")

        # 2. 遍历帧，差分过滤 + OCR
        subtitle_timeline = []
        prev_path = None
        last_text = "None"

        # 重置所有遥测指标
        self.ocr_called_count = 0
        self.ocr_local_count = 0
        self.ocr_duplicate_count = 0
        self.ocr_hybrid_escalated_count = 0

        logging.info(
            f"🔮 [Extractor] 开始执行像素差分过滤与 [{self.ocr_mode.upper()}] 字幕 OCR 提取流程..."
        )
        total_frames = len(renamed_files)
        skipped_count = 0
        blank_skipped_count = 0

        for idx, (filename, _, seconds) in enumerate(renamed_files, 1):
            curr_path = os.path.join(self.frames_dir, filename)

            # Heuristic Shield-1：本地快速空白字幕检测（像素标准差）
            if self.is_subtitle_empty(curr_path, box=self.subtitle_region_box, std_dev_threshold=std_dev_threshold):
                current_text = "None"
                last_text = current_text
                blank_skipped_count += 1
                logging.info(
                    f"  [{idx}/{total_frames}] @{self.format_seconds(seconds)}: "
                    f"[空白字幕] 跳过 OCR ↩️"
                )
                subtitle_timeline.append({"seconds": seconds, "text": current_text})
                prev_path = curr_path
                continue

            # Heuristic Shield-2：像素差分检测（字幕区域未变化则复用）
            is_changed = self.is_subtitle_changed(prev_path, curr_path, box=self.subtitle_region_box)

            if not is_changed:
                current_text = last_text
                skipped_count += 1
                logging.info(
                    f"  [{idx}/{total_frames}] @{self.format_seconds(seconds)}: "
                    f"[无变化] 复用 '{current_text}' ↩️"
                )
            else:
                # 字幕区域发生变化 → 触发 OCR
                logging.info(
                    f"  [{idx}/{total_frames}] @{self.format_seconds(seconds)}: "
                    f"[变化] → [{self.ocr_mode.upper()}] OCR..."
                )
                current_text = self.ocr_subtitle(image_path=curr_path, box=self.subtitle_region_box)

                # 检测重复（差分误判：像素跳变但文字实际相同）
                if current_text != "None" and last_text != "None" and current_text == last_text:
                    self.ocr_duplicate_count += 1
                    logging.info(f"    ⚠️  [重复]: 差分误判，文本实为 '{current_text}'")

                last_text = current_text
                if current_text != "None":
                    logging.info(f"    ✅ [识别]: '{current_text}'")

            subtitle_timeline.append({"seconds": seconds, "text": current_text})
            prev_path = curr_path

        # 保存时间线供父类 pipeline 对齐计算
        self.subtitle_timeline = subtitle_timeline

        # 清理临时帧目录
        try:
            import shutil
            shutil.rmtree(self.frames_dir)
        except Exception:
            pass

        # 输出遥测摘要
        total_ocr_calls = self.ocr_called_count + self.ocr_local_count
        logging.info(
            f"🎉 [Extractor] 纯视觉字幕提取完成！\n"
            f"   📊 共处理帧数: {total_frames}\n"
            f"   ⏭️  本地空白跳过: {blank_skipped_count} 帧\n"
            f"   ⏭️  差分无变化跳过: {skipped_count} 帧\n"
            f"   ☁️  云端 OCR 调用: {self.ocr_called_count} 次\n"
            f"   💻 本地 OCR 调用: {self.ocr_local_count} 次\n"
            f"   📐 字幕区域: {self.subtitle_region_box or 'default'} (confidence={self.subtitle_region_confidence:.2f})\n"
            + (f"   ⬆️  Hybrid 升级精修: {self.ocr_hybrid_escalated_count} 次\n" if self.ocr_mode == "hybrid" else "")
            + f"   ⚠️  差分误判重复: {self.ocr_duplicate_count} 次"
        )

        # 3. 编译最终输出
        srt_content = self.compile_srt(subtitle_timeline, interval_sec)
        md_content = self.compile_markdown_timeline(subtitle_timeline, interval_sec)

        return srt_content, md_content
