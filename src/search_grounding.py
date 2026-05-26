import os
import re
import json
import logging
import base64
import subprocess
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class VisualGrounding:
    """
    VisualGrounding captures video screenshots at precisely located timestamps where
    ambiguous ASR terms occur, uses a multimodal VLM (like Qwen-VL) to read
    hardcoded subtitles or visual elements, and returns accurate corrected mappings.
    """
    def __init__(self, api_key, api_base, model="qwen3-vl-plus", temp_dir="./temp"):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        
        if not self.api_key:
            raise ValueError(
                "错误: 未配置百炼 API Key。\n"
                "💡 解决方案: 视觉硬字幕校验需要配置 DASHSCOPE_API_KEY / BAILIAN_API_KEY。"
            )
            
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        self.typos = []
        self.hypotheses = []

    def extract_ambiguous_terms(self, transcript_text, text_api_key, text_api_base, text_model):
        """
        Calls the text LLM to identify ambiguous or suspected ASR terms in the transcript.
        Returns a list of dicts: [{"typo": "dpc v4 pro", "context": "..."}]
        """
        system_prompt = (
            "你是一个极其智能的语音转写(ASR)名词纠偏分析器。\n"
            "我会给你一段技术视频的语音转录文本。其中包含一些因发音、口音或听力误判导致的 ASR 错字（例如：把 'Cline' 转成 'clalicle'，'DeepSeek' 转成 'DPC' 或 'Dipsig'，'VS Code' 转成 'viscode'，'CLAUDE.md' 转成 'clothcode md' 等）。\n\n"
            "请你执行以下分析：\n"
            "1. 识别视频中最核心的 2-3 个发音或拼写极度存疑的生词、缩写或最新 AI 工具/模型代号（ASR 原始转写）。\n"
            "2. 提取出每一个存疑词所在的邻近上下文句子（用于在转录中精确定位）。\n\n"
            "请严格按照以下 JSON 格式输出，不要说任何废话或 Markdown 标记：\n"
            "[\n"
            '  {"typo": "clalicle", "context": "clalicle在沟通时候，这里运行了九之后"},\n'
            '  {"typo": "DPC V4 Pro", "context": "AI我使用的是最新的DPC V4 Pro，这个AI模型"}\n'
            "]"
        )

        try:
            text_client = OpenAI(api_key=text_api_key, base_url=text_api_base)
            request_kwargs = {
                "model": text_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"ASR转录文本部分段落：\n{transcript_text[:8000]}"}
                ],
                "temperature": 0.0,
                "max_tokens": 512,
                "stream": False
            }
            
            is_deepseek = "deepseek" in text_api_base.lower() or "deepseek" in text_model.lower()
            if is_deepseek:
                request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                
            response = text_client.chat.completions.create(**request_kwargs)
            response_text = response.choices[0].message.content.strip()
            
            response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE).strip()
            
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            elif response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            data = json.loads(response_text)
            if isinstance(data, list):
                logging.info(f"💡 [VisualGrounding] 成功分析出 {len(data)} 个 ASR 存疑名词: {data}")
                return data
        except Exception as e:
            logging.warning(f"提取 ASR 存疑名词分析失败: {e}")
            
        return []

    def locate_timestamps(self, typos_list, structured_segments):
        """
        Finds the matching segment and exact timestamp (seconds) for each typo in the structured ASR segments.
        Returns a list of dicts: [{"typo": "dpc v4 pro", "context": "...", "timestamp_sec": float}]
        """
        located = []
        for item in typos_list:
            typo = item.get("typo", "").strip()
            context = item.get("context", "").strip()
            if not typo:
                continue
                
            found = False
            # Step 1: Try to search for exact typo matching segment
            for segment in structured_segments:
                seg_text = segment.get("text", "")
                if typo.lower() in seg_text.lower():
                    start_sec = float(segment.get("start", 0))
                    end_sec = float(segment.get("end", start_sec))
                    timestamp_sec = round((start_sec + end_sec) / 2, 2)
                    located.append({
                        "typo": typo,
                        "context": context,
                        "timestamp_sec": timestamp_sec
                    })
                    found = True
                    break
                    
            if not found and context:
                # Step 2: Fallback to searching context matching segment
                context_sub = context[:10].strip()
                for segment in structured_segments:
                    seg_text = segment.get("text", "")
                    if context_sub.lower() in seg_text.lower():
                        start_sec = float(segment.get("start", 0))
                        end_sec = float(segment.get("end", start_sec))
                        timestamp_sec = round((start_sec + end_sec) / 2, 2)
                        located.append({
                            "typo": typo,
                            "context": context,
                            "timestamp_sec": timestamp_sec
                        })
                        found = True
                        break
                        
            if not found:
                logging.warning(f"无法定位 ASR 存疑词 '{typo}' 的时间戳段，将跳过视觉截图校验。")
                
        logging.info(f"📍 [VisualGrounding] 成功精确定位出 {len(located)} 处存疑词的时间戳位置。")
        return located

    def format_seconds(self, seconds_float):
        total_seconds = int(float(seconds_float))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}_{m:02d}_{s:02d}"

    def extract_frame_at_timestamp(self, video_path, timestamp_sec):
        """
        Extract a single high-quality frame at a specific timestamp (seconds).
        Returns the absolute path of the extracted image file.
        """
        output_filename = f"ambiguous_term_time_{self.format_seconds(timestamp_sec)}.jpg"
        output_path = os.path.join(self.temp_dir, output_filename)
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
            
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp_sec),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_path
        ]
        
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(output_path):
            return output_path
        else:
            logging.warning(f"ffmpeg 提取 {timestamp_sec} 秒截图失败。")
            return None

    def encode_image_base64(self, image_path):
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def verify_term_visually(self, typo, context, image_path):
        """
        Calls Qwen-VL to analyze the video screenshot, read hardcoded subtitles or code,
        and return the correct term mapping.
        """
        if not os.path.exists(image_path):
            return typo

        b64_str = self.encode_image_base64(image_path)
        
        system_prompt = (
            "你是一个高精度的技术视频画面与字幕分析专家。\n"
            "我会给你一张视频截图，对应视频中提到某个可能被 ASR（语音识别）误听的技术词汇的时间点。画面中可能包含视频内嵌的硬字幕，也可能包含屏幕上的代码、图表、软件界面或文字。\n"
            "另外，我还会给你这个时间点对应的 ASR 原始拼写错误词（以及它的上下文）。\n\n"
            "请根据视频截图中的硬字幕或画面视觉元素，找出该 ASR 拼写错误词所指代的【实际正确的专有名词拼写】。\n"
            "输出格式要求：请仅以 JSON 字典格式输出纠正映射关系。如果画面中无法看清或无法得出结论，请纠正为你的最佳常识推测名词。格式如下（只输出 JSON，不要任何 Markdown 围栏或额外废话）：\n"
            "{\n"
            '  "ASR原始错误词": "正确名词拼写"\n'
            "}"
        )

        user_content = [
            {
                "type": "text",
                "text": f"ASR 原始错误词：'{typo}'\n周围上下文：'{context}'\n\n请识别图片底部的硬字幕或画面中的相关文字，提取正确的名词。"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_str}"
                }
            }
        ]

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=256
            )
            response_text = response.choices[0].message.content.strip()
            
            # Clean JSON formatting
            response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL | re.IGNORECASE).strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            elif response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            # Parse the result
            data = json.loads(response_text)
            if isinstance(data, dict):
                # Search for correct term
                for k, v in data.items():
                    if k.lower().strip() == typo.lower().strip():
                        logging.info(f"👁️ [VisualGrounding] 成功通过视频画面硬字幕纠正术语: '{typo}' -> '{v}'")
                        return v.strip()
                # If key didn't match exactly, return first value
                if data:
                    v = list(data.values())[0]
                    logging.info(f"👁️ [VisualGrounding] 成功通过视频画面硬字幕纠正术语: '{typo}' -> '{v}'")
                    return v.strip()
        except Exception as e:
            logging.warning(f"通过 Qwen-VL 视觉纠错失败: {e}")
            
        return typo

    def perform_visual_grounding(self, transcript_text, structured_segments, video_path, text_api_key, text_api_base, text_model):
        """
        Main entry point for Visual Grounding.
        Excludes search steps, extracts screenshots near ambiguous terms,
        uses Qwen-VL to check video hardcoded subtitles, and returns a dictionary
        of corrected mappings.
        """
        if not video_path or not os.path.exists(video_path):
            logging.warning("⚠️ 没有找到本地视频文件，跳过视觉硬字幕纠错。")
            return {}
            
        # 1. Extract ambiguous terms
        typos_list = self.extract_ambiguous_terms(transcript_text, text_api_key, text_api_base, text_model)
        if not typos_list:
            return {}
            
        # 2. Locate timestamps in transcript segments
        located_items = self.locate_timestamps(typos_list, structured_segments)
        if not located_items:
            return {}
            
        # 3. Process each item: capture frame and run Qwen-VL OCR correction
        corrected_mappings = {}
        for item in located_items:
            typo = item["typo"]
            context = item["context"]
            timestamp_sec = item["timestamp_sec"]
            
            logging.info(f"📸 正在提取存疑词 '{typo}' 的视频时刻 ({timestamp_sec} 秒) 画面以进行多模态分析...")
            image_path = self.extract_frame_at_timestamp(video_path, timestamp_sec)
            
            if image_path:
                correct_term = self.verify_term_visually(typo, context, image_path)
                if correct_term and correct_term.lower().strip() != typo.lower().strip():
                    corrected_mappings[typo] = correct_term
                    
        return corrected_mappings
