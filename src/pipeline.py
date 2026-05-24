import os
import sys
import time
import logging
import subprocess

from src.config import AppConfig
from src.downloader import VideoDownloader
from src.media_processor import MediaProcessor
from src.asr import SpeechToText
from src.visual_locator import VisualLocator
from src.providers.qwen import QwenProvider
from src.providers.deepseek import DeepSeekProvider
from src.compiler import WikiCompiler
from src.sanitizer import OralSanitizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class IngestionPipeline:
    """
    IngestionPipeline orchestrates the entire flow:
    URL/File -> (Subtitle/ASR) -> Sanitizer -> DeepSeek -> Compiler -> llm_wiki
    """
    def __init__(self, config_path=None, provider=None, model=None):
        self.config_path = config_path
        self.config = AppConfig(config_path=self.config_path)
        
        # CLI overrides
        self.provider_name = provider if provider else self.config.provider
        self.qwen_model = model if model and self.provider_name == "qwen" else self.config.qwen_model
        self.deepseek_model = model if model and self.provider_name == "deepseek" else self.config.deepseek_model
        
        self.active_provider_name = self.provider_name if self.config.enable_images else "deepseek"
        self.active_model_name = self.qwen_model if self.config.enable_images else self.deepseek_model

    def run(self, input_source, keep_temp=False):
        start_time = time.time()
        
        logging.info("配置初始化完成：")
        logging.info(f" - 知识库目录: {self.config.wiki_dir}")
        logging.info(f" - 临时文件目录: {self.config.temp_dir}")
        logging.info(f" - ASR 语音模型尺寸: {self.config.asr_model_size}")
        logging.info(f" - 字幕优先极速引擎: {'开启' if self.config.asr_subtitle_first else '关闭'}")
        logging.info(f" - 多模态引擎 Provider: {self.provider_name}")
        if self.provider_name == "qwen":
            logging.info(f" - 百炼多模态模型: {self.qwen_model}")
        logging.info(f" - 文字总结模型: DeepSeek 官方 API / {self.deepseek_model}")
        
        # 1. Download / Sourcing (Subtitle-First dual mode)
        downloader = VideoDownloader(temp_dir=self.config.temp_dir)
        is_online_url = downloader.is_url(input_source)
        
        subtitles_fetched = False
        structured_segments = []
        video_title = ""
        source_metadata = {}
        video_path = None
        
        # Try fetching online subtitles to bypass heavy video downloads & local ASR
        if is_online_url and self.config.asr_subtitle_first:
            try:
                yt_dlp_cmd = downloader._find_yt_dlp()
                # Quickly fetch Bilibili / YouTube title
                cmd_info = yt_dlp_cmd + ["--get-title", input_source]
                res = subprocess.run(cmd_info, capture_output=True, text=True, check=True, timeout=12)
                raw_title = res.stdout.strip() or "downloaded_video"
                video_title = downloader.sanitize_title(raw_title)
                
                sub_segments = downloader.download_subtitles(input_source, video_title)
                if sub_segments:
                    logging.info("🎉 Subtitle-First: 成功匹配提取到在线精细字幕！将 100% 绕过媒体文件下载与本地 ASR 转录！")
                    structured_segments = sub_segments
                    subtitles_fetched = True
                    source_metadata = {
                        "source_id": downloader.build_source_id(input_source),
                        "source_url": input_source,
                        "source_path": "",
                        "platform": downloader.detect_platform(input_source),
                        "download_status": "subtitles",
                        "raw_title": raw_title,
                    }
            except Exception as e:
                logging.warning(f"Subtitle-First 自动探测与字幕下载失败: {e}。将优雅降级到原有媒体转录流程。")

        # Regular Media Ingest Fallback
        if not subtitles_fetched:
            try:
                video_path, video_title, source_metadata = downloader.process_input(input_source)
            except Exception as e:
                logging.error(f"视频解析输入失败: {e}")
                sys.exit(1)

        # 2. Media Pre-processing (Bypassed if subtitles_fetched)
        candidate_frames_dir = None
        candidate_frames = []
        audio_path = None
        
        if not subtitles_fetched:
            processor = MediaProcessor(temp_dir=self.config.temp_dir)
            try:
                # Step A: Extract lightweight audio
                audio_path = processor.extract_audio(video_path)
                
                if self.config.enable_images:
                    # Step B: Adaptive keyframe sampling
                    candidate_frames_dir, candidate_frames = processor.extract_candidate_frames(
                        video_path=video_path,
                        scene_threshold=self.config.scene_threshold,
                        max_interval_sec=self.config.max_interval_sec,
                        max_width=self.config.max_width
                    )
            except Exception as e:
                logging.error(f"ffmpeg 媒体预处理失败: {e}")
                sys.exit(1)

            if self.config.enable_images and not candidate_frames:
                logging.error("没有提取到任何候选帧，请检查视频是否有效。")
                sys.exit(1)

        # 3. ASR (Speech-to-Text) / Subtitles Sourcing
        asr = SpeechToText(model_size=self.config.asr_model_size, language=self.config.asr_language)
        if not subtitles_fetched:
            try:
                structured_segments, _ = asr.transcribe(audio_path)
            except Exception as e:
                logging.error(f"本地语音转写 (ASR) 失败: {e}")
                sys.exit(1)
                
        # 4. Local Oral Text Sanitizing & Compact Merging
        sanitizer = OralSanitizer()
        # Merge Fragmented chunks and strip fillers
        structured_segments = sanitizer.merge_segments(structured_segments)
        transcript_text = sanitizer.format_timeline(structured_segments, asr.format_timestamp)

        if not transcript_text:
            logging.warning("警告: 转录时间线内容为空。")
            transcript_text = "[无声音或未检测到有效语音转写]"

        # 5. Optional Visual Location (Only if VLM image-link is active)
        visual_anchors = []
        located_candidate_frames = []
        if self.config.enable_images and not subtitles_fetched:
            locator = VisualLocator(
                min_frames=self.config.visual_min_frames,
                max_frames=self.config.visual_max_frames,
                trigger_window_sec=self.config.visual_trigger_window_sec,
                min_gap_sec=self.config.visual_min_gap_sec,
                min_score=self.config.visual_min_score
            )
            visual_anchors = locator.locate(structured_segments, candidate_frames)
            located_candidate_frames = locator.filter_candidate_frames(candidate_frames, visual_anchors)
            if located_candidate_frames:
                logging.info(
                    f"视觉补充定位完成：从 {len(candidate_frames)} 张候选帧中筛出 {len(located_candidate_frames)} 张优先送入视觉模型。"
                )
            else:
                logging.info("视觉补充定位未命中候选帧，本次不保存图片。")
        else:
            if subtitles_fetched:
                logging.info("字幕优先模式：已跳过图片提取与视觉补充定位。")
            else:
                logging.info("图片链路已关闭：跳过抽帧、视觉定位和图片上传。")

        # 6. Model Alignment / Note Generation
        if not self.config.enable_images:
            try:
                provider = DeepSeekProvider(
                    api_key=self.config.deepseek_api_key,
                    api_base=self.config.deepseek_api_base,
                    model=self.deepseek_model,
                    enable_thinking=self.config.deepseek_enable_thinking,
                    reasoning_effort=self.config.deepseek_reasoning_effort
                )
                markdown_content = provider.generate_text_wiki(
                    transcript_text=transcript_text,
                    video_title=video_title
                )
                selected_frames = []
            except Exception as e:
                logging.error(f"调用 DeepSeek 官方大模型接口失败: {e}")
                sys.exit(1)
        elif self.provider_name == "qwen":
            try:
                provider = QwenProvider(
                    api_key=self.config.qwen_api_key,
                    api_base=self.config.qwen_api_base,
                    model=self.qwen_model
                )
                markdown_content, selected_frames = provider.generate_wiki(
                    transcript_text=transcript_text,
                    candidate_frames=located_candidate_frames,
                    video_title=video_title,
                    candidate_frames_dir=candidate_frames_dir,
                    visual_anchors=visual_anchors,
                    enable_images=self.config.enable_images
                )
            except Exception as e:
                logging.error(f"调用百炼大模型接口失败: {e}")
                sys.exit(1)
        else:
            logging.error(f"不支持的 Provider: {self.provider_name}")
            sys.exit(1)

        # 7. Compiling, Asset Linking & Synchronizing
        try:
            compiler = WikiCompiler(
                wiki_dir=self.config.wiki_dir,
                temp_dir=self.config.temp_dir,
                image_link_style=self.config.image_link_style
            )
            final_markdown_path, manifest_path = compiler.compile(
                video_title=video_title,
                markdown_content=markdown_content,
                selected_frames=selected_frames,
                candidate_frames_dir=candidate_frames_dir,
                source_metadata=source_metadata,
                transcript_text=transcript_text,
                structured_segments=structured_segments,
                candidate_frames=candidate_frames,
                visual_anchors=visual_anchors,
                provider_name=self.active_provider_name,
                model_name=self.active_model_name,
                keep_temp=keep_temp
            )
        except Exception as e:
            logging.error(f"编译同步至 Wiki 知识库失败: {e}")
            sys.exit(1)

        # 8. Print Success Overview
        elapsed_time = time.time() - start_time
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        
        print("\n" + "="*50)
        print(" 🎉  恭喜！视频图文知识库文章导入成功！")
        print("="*50)
        print(f" 📂  最终文章路径: {final_markdown_path}")
        print(f" 🧾  导入记录路径: {manifest_path}")
        print(f" 🎯  视觉锚点数: {len(visual_anchors)}")
        print(f" 🖼️  已保存截图数: {len(selected_frames)} 张 (保存在 assets/ 目录下)")
        if keep_temp:
            print(f" 🧪  临时文件已保留: {self.config.temp_dir}")
        print(f" ⏱️  总耗时: {minutes} 分 {seconds} 秒")
        print("="*50 + "\n")
        
        return final_markdown_path
