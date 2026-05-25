import logging
import re
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class OpenAICompatibleProvider:
    """
    OpenAICompatibleProvider connects to any OpenAI API protocol-compliant middleware or gateway.
    It supports seamless switching of API endpoints, keys, models, and custom structuring prompts.
    """
    def __init__(
        self,
        api_key,
        api_base,
        model,
        structuring_prompt=None
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.structuring_prompt = structuring_prompt

        if not self.api_key:
            raise ValueError(
                "错误: 未配置 OpenAI 兼容 API Key。\n"
                "💡 解决方案: 请在本地 shell 中配置: export OPENAI_API_KEY='你的 Key'\n"
                "或者在 config.yaml 的 openai_compatible 块中配置 api_key。"
            )
        
        if not self.api_base:
            raise ValueError(
                "错误: 未配置 OpenAI 兼容 API Base URL。\n"
                "💡 解决方案: 请在 config.yaml 的 openai_compatible 块中配置 api_base。"
            )

        if not self.model:
            raise ValueError(
                "错误: 未配置 OpenAI 兼容 Model 名称。\n"
                "💡 解决方案: 请在 config.yaml 的 openai_compatible 块中配置 model。"
            )

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base
        )

    def generate_text_wiki(self, transcript_text, video_title, custom_prompt=None):
        # Determine active structuring prompt: CLI parameter override > Config File structuring_prompt > Built-in default
        active_prompt = custom_prompt if custom_prompt else self.structuring_prompt
        
        if active_prompt:
            prompt = active_prompt.replace("{video_title}", video_title)
            if "{transcript_text}" in prompt:
                prompt = prompt.replace("{transcript_text}", transcript_text)
            else:
                prompt += f"\n\n以下是 ASR 转写文本：\n===================================\n{transcript_text}\n===================================\n\n请直接输出最终 Markdown 笔记。"
        else:
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

        request_kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 8192,
            "stream": False
        }

        try:
            response = self.client.chat.completions.create(**request_kwargs)
        except Exception as e:
            raise RuntimeError(f"OpenAI 兼容 API 请求执行失败: {e}\n请检查您的 api_key, api_base, model 配置以及网络连接。")

        response_text = response.choices[0].message.content
        logging.info(f"OpenAI 兼容 API (模型: {self.model}) 响应成功！")
        clean_markdown = re.sub(r"^```markdown\s*", "", response_text.strip(), flags=re.IGNORECASE)
        clean_markdown = re.sub(r"^```\s*", "", clean_markdown)
        clean_markdown = re.sub(r"\s*```$", "", clean_markdown).strip()
        return clean_markdown
