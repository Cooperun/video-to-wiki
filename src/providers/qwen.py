import os
import base64
import json
import re
import logging
from openai import OpenAI
from src.providers.base import BaseProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class QwenProvider(BaseProvider):
    def __init__(
        self,
        api_key,
        api_base,
        model="qwen3-vl-plus"
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        
        if not self.api_key:
            raise ValueError(
                "错误: 未配置百炼 API Key。\n"
                "💡 解决方案: 请在本地命令行运行: export DASHSCOPE_API_KEY='你的百炼KEY'\n"
                "或者直接在 config.yaml 中的 qwen -> api_key 中填写你的 Key。"
            )
            
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base
        )

    def encode_image_base64(self, image_path):
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def extract_json(self, text):
        """
        Robust JSON extractor to fetch selected keyframes from VLM output.
        """
        # 1. Search for ```json ... ``` block
        json_block_match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
        if json_block_match:
            try:
                return json.loads(json_block_match.group(1).strip())
            except Exception:
                pass
                
        # 2. Search for any standard {...} JSON block
        brace_match = re.search(r"(\{[\s\S]*\})", text)
        if brace_match:
            try:
                return json.loads(brace_match.group(1).strip())
            except Exception:
                pass
                
        return None

    def fallback_extract_from_markdown(self, markdown_text, candidate_frames):
        """
        Fallback parser: if VLM didn't return standard JSON, parse the Markdown text
        for any image tags like screenshot_HH_MM_SS.jpg or variations, and
        correlate them with candidate_frames to recover the selection.
        """
        logging.info("标准 JSON 时间戳解析失败，尝试通过正文 Markdown 图标进行降级恢复解析...")
        selected_frames = []
        
        # Regex matches screenshot_HH_MM_SS or screenshot_HH-MM-SS or simply time formats
        # We look for HH_MM_SS or similar matches
        pattern = re.compile(r"screenshot_(\d{2})_(\d{2})_(\d{2})")
        matches = pattern.findall(markdown_text)
        
        if not matches:
            # Try matching variations e.g. screenshot_HH_MM_SS
            # Or direct image names
            pattern_var = re.compile(r"frame_\d+_time_(\d{2})_(\d{2})_(\d{2})")
            matches = pattern_var.findall(markdown_text)
            
        unique_times = set()
        for h, m, s in matches:
            formatted_time = f"{h}:{m}:{s}" # 00:02:35
            unique_times.add(formatted_time)
            
        logging.info(f"在 Markdown 正文中搜索到 {len(unique_times)} 个视觉截图标签: {unique_times}")
        
        # Correlate with physical candidate_frames
        for frame_file, fmt_time, _ in candidate_frames:
            # Map "00_00_15" -> "00:00:15"
            time_normalized = fmt_time.replace("_", ":")
            if time_normalized in unique_times:
                selected_frames.append({
                    "filename": frame_file,
                    "timestamp": time_normalized
                })
                
        return selected_frames

    def normalize_selected_frames(self, selected_frames, candidate_frames, candidate_frames_dir):
        """
        Normalize VLM-selected frames back to physical candidate frame filenames.
        Models sometimes emit screenshot_* names or bracketed timestamps even when
        instructed to return the original frame_* filename.
        """
        by_time = {
            fmt_time.replace("_", ":"): filename
            for filename, fmt_time, _ in candidate_frames
        }
        normalized = []
        seen = set()

        for frame in selected_frames:
            filename = frame.get("filename", "")
            timestamp = str(frame.get("timestamp", "")).strip().strip("[]")

            if not timestamp:
                time_match = re.search(r"(\d{2})_(\d{2})_(\d{2})", filename)
                if time_match:
                    timestamp = ":".join(time_match.groups())

            if timestamp and timestamp in by_time:
                filename = by_time[timestamp]
            elif filename and not os.path.exists(os.path.join(candidate_frames_dir, filename)):
                logging.warning(f"无法归一化模型返回的截图文件名: {filename} ({timestamp})")

            key = (filename, timestamp)
            if filename and timestamp and key not in seen:
                normalized.append({
                    "filename": filename,
                    "timestamp": timestamp
                })
                seen.add(key)

        return normalized

    def fallback_representative_frames(self, candidate_frames, target_count=4, existing_frames=None):
        """
        Last-resort visual fallback. If the VLM refuses to select any screenshots,
        keep a few time-distributed frames so the video note still has visual anchors.
        """
        existing_frames = existing_frames or []
        if not candidate_frames:
            return existing_frames

        existing_times = {frame.get("timestamp") for frame in existing_frames}

        usable_frames = [frame for frame in candidate_frames if frame[2] >= 3] or candidate_frames
        if len(usable_frames) <= target_count:
            picked = usable_frames
        else:
            picked = []
            for i in range(target_count):
                idx = round((len(usable_frames) - 1) * (i + 1) / (target_count + 1))
                picked.append(usable_frames[idx])

        fallback_frames = [
            {
                "filename": filename,
                "timestamp": fmt_time.replace("_", ":")
            }
            for filename, fmt_time, _ in picked
            if fmt_time.replace("_", ":") not in existing_times
        ]

        return (existing_frames + fallback_frames)[:target_count]

    def generate_wiki(self, transcript_text, candidate_frames, video_title, candidate_frames_dir=None, visual_anchors=None, enable_images=False):
        visual_anchors = visual_anchors or []
        candidate_frames = candidate_frames or []
        if not enable_images:
            return self.generate_text_wiki(transcript_text, video_title), []

        # 1. Downsample candidate frames if they exceed safe token/payload limits (max 45 frames)
        max_safe_frames = 45
        total_candidates = len(candidate_frames)
        
        if total_candidates > max_safe_frames:
            logging.info(f"候选截图过多 ({total_candidates} 张)，为了防止 API 负载超限与节省 Token，将等间距降采样至 {max_safe_frames} 张。")
            step = total_candidates / max_safe_frames
            sampled_frames = []
            for i in range(max_safe_frames):
                sampled_frames.append(candidate_frames[int(i * step)])
            # Make sure we always include the last frame
            if candidate_frames[-1] not in sampled_frames:
                sampled_frames[-1] = candidate_frames[-1]
            candidate_frames = sampled_frames
        
        logging.info(f"开始上传并构建 Qwen 多模态 API 请求 (候选截图数: {len(candidate_frames)})...")
        
        # 2. Build multi-image message content
        content_payload = []
        
        # Introduction text
        intro_text = (
            "你是一个极其专业的技术研报撰写者和知识库架构师。\n"
            "我现在给你提供某技术视频的本地语音转写文本（带精确时间戳），以及由 VisualLocator 初筛出的视觉补充候选截图。\n"
            "图片不是为了证明文字，而是为了补充文字无法完整表达的信息。你的任务不是为了配图而配图，而是只保留那些画面中包含大量信息、表格、参数、对比、代码、图表、界面状态等需要用户自己查看的图片。\n\n"
            f"视频名称/标题为: 《{video_title}》\n"
            "以下是按顺序输入给你的候选帧图片，我们会给每张图一个编号，格式为 [Image X]，代表你在大模型输入里收到的第 X 张多模态图片。在你的推理中，请把这第 X 张图片和时间戳对应起来。\n"
        )
        
        # Add frame reference descriptions in prompt so VLM perfectly aligns them
        frame_descriptions = []
        for i, (filename, fmt_time, _) in enumerate(candidate_frames, 1):
            time_normalized = fmt_time.replace("_", ":")
            anchor = self.find_anchor(filename, visual_anchors)
            reason_text = ""
            if anchor:
                reasons = "；".join(anchor.get("reasons", []))
                nearby_text = anchor.get("nearby_text", "")
                reason_text = f"；定位原因: {reasons or '视觉补充候选'}；邻近转写: {nearby_text[:120]}"
            frame_descriptions.append(f"- [Image {i}]: 对应视频时刻 {time_normalized} (文件名: {filename}{reason_text})")
            
        intro_text += "\n".join(frame_descriptions) + "\n\n"
        intro_text += "以下是原视频的完整 ASR 转文本内容：\n"
        intro_text += "===================================\n"
        intro_text += transcript_text + "\n"
        intro_text += "===================================\n\n"
        
        # Add guidelines and final instruction
        instruction_text = (
            "### 写作与排版要求（重要，必须严格执行）：\n"
            "1. **目标不是泛泛视频摘要，而是可进入个人 RAG 知识库的 Markdown 条目。** 请仔细梳理语音转文本内容，写出结构化、事实密度高、方便后续问答检索的学习笔记。\n"
            "2. **固定结构**：正文必须包含以下二级标题：`## 一句话结论`、`## 关键知识点`、`## 详细笔记`、`## 可用于后续问答的事实`。如果视频包含术语、代码、命令或工具，也请增加 `## 术语与工具` 或 `## 代码与命令`。\n"
            "3. **检索友好**：`## 可用于后续问答的事实` 必须使用项目符号列出可被直接引用的事实，每条事实尽量带时间戳，例如 `[00:03:12] 作者说明 ...`。避免空泛评价。\n"
            "4. **富文本排版**：多使用加粗、列表、提示块。对于代码演示视频，必须尽力提取关键代码并用 ``` 语法包装。\n"
            "5. **关键帧配图机制**：\n"
            "   - 仔细甄别每一张输入的 [Image X] 图片。\n"
            "   - 这些图片是 VisualLocator 基于文字时间线预筛出的视觉补充候选。请非常克制地选图：如果文字已经能清楚表达，就不要插图。\n"
            "   - 只有当图片包含大量文字信息、机器/模型参数表、横向对比表、排行榜、图表曲线、技术架构图、流程图、代码块、复杂 UI 状态、测评结果页等文字难以完整转述的信息时，才保存图片。\n"
            "   - 如果某张图片只是人脸、产品普通外观、桌面氛围、简单字幕、普通演示瞬间，且正文已经能讲清楚，请忽略。\n"
            "   - 允许 0 张截图。如果没有真正必要的视觉补充，请不要强行插图。\n"
            "   - 如果决定保存某张图片，请在正文相应讲解段落的**正上方或正下方**，以标准语法插入该截图！\n"
            f"   - 插入截图语法必须严格为标准 Markdown：`![视频截图 HH:MM:SS](screenshot_HH_MM_SS.jpg)` (例如 [Image 2] 的时间戳是 00:00:42，插入语法为 `![视频截图 00:00:42](screenshot_00_00_42.jpg)`)。\n"
            "   - 禁止在正文里只输出图片表格；只要选择截图，就必须把截图以内嵌图片语法放进相关段落。\n"
            "   - **禁止插入垃圾帧**：对于演讲者的面部表情特写、无关过渡动画、或者与上一张几乎完全没有内容差异的重复画面，请直接忽略，不要插入文章中！\n"
            "6. 输出结果格式必须分为两部分：\n"
            "   - 第一部分：完整的 Markdown 格式文章正文（包含文字叙述和内嵌的标准 Markdown 图片语法 `![视频截图 HH:MM:SS](screenshot_HH_MM_SS.jpg)`）。\n"
            "   - 第二部分：在文章正文的最末尾，使用一个标准的 ```json ... ``` 块输出被你在文章中引用的所有截图文件名和时间戳。这个 JSON 块必须是合法 JSON：不能有注释，不能有尾随逗号，timestamp 不能带方括号。\n"
            "   - `## 可用于后续问答的事实` 只能写自然语言项目符号事实，禁止在这个章节里输出 JSON、截图列表或代码块。\n"
            "   - 标准 JSON 块格式如下：\n"
            "     ```json\n"
            "     {\n"
            "       \"selected_frames\": [\n"
            "         {\"filename\": \"frame_0002_time_HH_MM_SS.jpg\", \"timestamp\": \"HH:MM:SS\"},\n"
            "         ...\n"
            "       ]\n"
            "     }\n"
            "     ```\n\n"
            "让我们现在开始，请直接输出最终的高质量深度图文笔记！"
        )
        
        # First append the massive text instructions
        content_payload.append({
            "type": "text",
            "text": intro_text + instruction_text
        })
        
        # Next, append each image sequentially as base64 URLs
        for i, (filename, _, _) in enumerate(candidate_frames, 1):
            image_path = os.path.join(candidate_frames_dir, filename)
            b64_str = self.encode_image_base64(image_path)
            content_payload.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_str}"
                }
            })
            
        messages = [
            {
                "role": "user",
                "content": content_payload
            }
        ]

        logging.info("百炼多模态 API 请求构建完成，正在发送请求并等待响应 (由于分析任务庞大，响应可能需要 15-40 秒，请耐心等待)...")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2, # Lower temperature for stable formatting and precise JSON
                max_tokens=8192
            )
        except Exception as e:
            raise RuntimeError(f"百炼 API 请求执行失败: {e}\n请检查 DASHSCOPE_API_KEY 环境变量配置以及网络连接。")

        response_text = response.choices[0].message.content
        logging.info("百炼 API 响应成功！")
        
        # 3. Robust parsing of selected keyframes
        selected_frames = []
        json_data = self.extract_json(response_text)
        
        if json_data and "selected_frames" in json_data:
            selected_frames = self.normalize_selected_frames(
                json_data["selected_frames"],
                candidate_frames,
                candidate_frames_dir
            )
            logging.info(f"大模型解析成功，已自动挑选出 {len(selected_frames)} 张核心高价值截图。")
        else:
            # Fallback
            selected_frames = self.normalize_selected_frames(
                self.fallback_extract_from_markdown(response_text, candidate_frames),
                candidate_frames,
                candidate_frames_dir
            )
            logging.info(f"降级提取完成，共恢复出 {len(selected_frames)} 张关联截图。")

        # Clean the Markdown text by removing the JSON code block if it is present in the main article body
        clean_markdown = response_text
        # Remove trailing selected_frames JSON block, with or without the json language tag.
        clean_markdown = re.sub(
            r"```(?:json)?\s*\{[\s\S]*?\"selected_frames\"[\s\S]*?\}\s*```",
            "",
            clean_markdown,
            flags=re.IGNORECASE
        ).strip()
        
        # Remove leading ```markdown or ``` and trailing ```
        clean_markdown = re.sub(r"^```markdown\s*", "", clean_markdown, flags=re.IGNORECASE)
        clean_markdown = re.sub(r"^```\s*", "", clean_markdown)
        clean_markdown = re.sub(r"\s*```$", "", clean_markdown).strip()
        
        return clean_markdown, selected_frames

    def generate_text_wiki(self, transcript_text, video_title):
        prompt = (
            "你是一个严谨、辩证、面向长期知识库的学习笔记整理者。\n"
            "我会给你一段视频的 ASR 转写文本。请只基于文字内容生成 Markdown 笔记，不要插入图片。\n\n"
            f"视频标题: 《{video_title}》\n\n"
            "请遵守以下要求：\n"
            "1. 输出适合个人 RAG/知识库检索的 Markdown，不要写泛泛的视频观后感。\n"
            "2. 必须包含：`## 一句话结论`、`## 核心观点`、`## 详细笔记`、`## 背景补充与细节澄清`、`## 可能的问题或争议`、`## 可用于后续问答的事实`。\n"
            "3. 如果 ASR 有明显错字、漏词或语义模糊，请结合上下文进行合理修正；但凡是你基于常识或背景知识补全的内容，都要明确标注为“推断”或“背景补充”。\n"
            "4. 要辩证看待视频内容：如果讲法可能过度简化、存在事实错误、概念混用、因果关系不严谨、工具适用边界没说清楚，请在 `## 可能的问题或争议` 中指出。\n"
            "5. 对教程/工具类视频，要提取可执行步骤、关键配置、命令、注意事项和失败条件。\n"
            "6. 对观点类视频，要区分作者观点、事实依据、你的背景补充和潜在反例。\n"
            "7. `## 可用于后续问答的事实` 使用项目符号，每条尽量带时间戳。\n"
            "8. 不要编造视频没有提到的具体数字、链接、命令或专有名词；如果需要补全，只能写为背景提示或待验证项。\n\n"
            "以下是 ASR 转写文本：\n"
            "===================================\n"
            f"{transcript_text}\n"
            "===================================\n\n"
            "请直接输出最终 Markdown 笔记。"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=8192
            )
        except Exception as e:
            raise RuntimeError(f"百炼 API 请求执行失败: {e}\n请检查 DASHSCOPE_API_KEY 环境变量配置以及网络连接。")

        response_text = response.choices[0].message.content
        logging.info("百炼文本笔记 API 响应成功！")
        clean_markdown = re.sub(r"^```markdown\s*", "", response_text.strip(), flags=re.IGNORECASE)
        clean_markdown = re.sub(r"^```\s*", "", clean_markdown)
        clean_markdown = re.sub(r"\s*```$", "", clean_markdown).strip()
        return clean_markdown

    def find_anchor(self, filename, visual_anchors):
        for anchor in visual_anchors:
            if anchor.get("filename") == filename:
                return anchor
        return None
