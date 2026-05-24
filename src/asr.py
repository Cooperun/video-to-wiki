import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class SpeechToText:
    def __init__(self, model_size="small", language="zh"):
        self.model_size = model_size
        self.language = language
        self._model = None

    def _get_model(self):
        if self._model is not None:
            return self._model
        
        logging.info(f"正在本地加载 Faster-Whisper '{self.model_size}' 语言模型 (首次加载会自动下载模型文件，请耐心等待)...")
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "未能在本地环境中导入 faster-whisper。\n"
                "💡 解决方案: 请运行以下命令安装所需依赖：\n"
                "   pip install faster-whisper\n"
                "如果仍然失败，请确保你的 Python 版本为 3.9 - 3.11。"
            )

        # On Mac Apple Silicon, running on "cpu" with "int8" or "float32" is extremely fast and efficient
        # because of faster-whisper's CTranslate2 backend.
        try:
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            logging.info("Faster-Whisper 模型加载成功。")
            return self._model
        except Exception as e:
            logging.warning(f"使用 int8 加载模型失败 ({e})，尝试使用 float32 重新加载...")
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="float32")
            logging.info("Faster-Whisper (float32) 模型加载成功。")
            return self._model

    def format_timestamp(self, seconds):
        total_seconds = int(seconds)
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def transcribe(self, audio_path):
        """
        Transcribes audio locally and returns:
        1. A structured list: [{"start": 12.3, "end": 15.6, "text": "..."}]
        2. A formatted prompt string: "[00:00:12 - 00:00:15] 大家好..."
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在，无法转写: {audio_path}")

        model = self._get_model()
        
        logging.info("开始本地音频语音转写 (ASR)...")
        
        # Transcribe with Chinese preference if set
        segments, info = model.transcribe(
            audio_path, 
            beam_size=5, 
            language=self.language if self.language else None
        )
        
        structured_segments = []
        prompt_lines = []
        
        for segment in segments:
            # Avoid empty or silent segments
            text = segment.text.strip()
            if not text:
                continue
                
            start_str = self.format_timestamp(segment.start)
            end_str = self.format_timestamp(segment.end)
            
            structured_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": text
            })
            
            prompt_lines.append(f"[{start_str} --> {end_str}] {text}")

        logging.info(f"音频转写完成，共生成 {len(structured_segments)} 个文本片段。")
        
        full_prompt_text = "\n".join(prompt_lines)
        return structured_segments, full_prompt_text
