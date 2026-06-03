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
    def __init__(self, config_path=None, provider=None, model=None, search=False, ocr_mode=None, enable_subtitle_ocr=True):
        self.config_path = config_path
        self.config = AppConfig(config_path=self.config_path)
        self.enable_subtitle_ocr = enable_subtitle_ocr
        
        # CLI overrides
        self.provider_name = provider if provider else self.config.provider
        self.qwen_model = model if model and self.provider_name == "qwen" else self.config.qwen_model
        self.deepseek_model = model if model and self.provider_name == "deepseek" else self.config.deepseek_model
        self.openai_compat_model = model if model and self.provider_name == "openai_compatible" else self.config.openai_compat_model
        
        # Visual grounding correction enabled status
        self.search_enabled = search if search else self.config.search_grounding_enabled

        # OCR mode override (CLI wins over config.yaml)
        if ocr_mode:
            self.config.ocr_mode = ocr_mode.lower().strip()
        self._validate_ocr_runtime()

        if self.config.enable_images:
            self.active_provider_name = self.provider_name if self.provider_name == "qwen" else "qwen"
            self.active_model_name = self.qwen_model
        else:
            self.active_provider_name = self.provider_name if self.provider_name in ["deepseek", "openai_compatible"] else "deepseek"
            if self.active_provider_name == "deepseek":
                self.active_model_name = self.deepseek_model
            else:
                self.active_model_name = self.openai_compat_model
        
        self.corrections_log = []

    def _validate_ocr_runtime(self):
        if not self.enable_subtitle_ocr:
            return

        if self.config.ocr_mode == "cloud" and not self.config.qwen_api_key:
            raise RuntimeError(
                "cloud OCR 模式需要配置云端视觉 API Key。"
                "请设置 DASHSCOPE_API_KEY / BAILIAN_API_KEY 或 qwen.api_key；"
                "如果只想本地识别，请改用 --ocr-mode local。"
            )

        if self.config.ocr_mode == "hybrid" and not self.config.qwen_api_key:
            logging.warning(
                "hybrid OCR 未检测到云端视觉 API Key，将自动退化为本地 OCR；"
                "低置信度帧不会触发云端精修。"
            )

    def run(self, input_source, keep_temp=False):
        start_time = time.time()
        
        logging.info("配置初始化完成：")
        logging.info(f" - 知识库目录: {self.config.wiki_dir}")
        logging.info(f" - 临时文件目录: {self.config.temp_dir}")
        logging.info(f" - ASR 语音模型尺寸: {self.config.asr_model_size}")
        logging.info(f" - 字幕优先极速引擎: {'开启' if self.config.asr_subtitle_first else '关闭'}")
        logging.info(f" - 多模态视觉硬字幕纠偏: {'开启' if self.search_enabled else '关闭'}")
        logging.info(f" - 嵌入式硬字幕 OCR: {'开启' if self.enable_subtitle_ocr else '关闭'}")
        if self.enable_subtitle_ocr:
            logging.info(f" - 硬字幕 OCR 模式: [{self.config.ocr_mode.upper()}]")
        logging.info(f" - 激活的多模态/文本 Provider: {self.active_provider_name}")
        logging.info(f" - 激活的推理总结模型: {self.active_model_name}")
        
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

        # Embedded visual subtitle extraction is used only as an internal signal
        # for ASR/OCR alignment and timeline repair. Normal ingestion should
        # publish a single final Markdown article, not intermediate subtitle files.
        ocr_metrics = None
        
        if video_path and not subtitles_fetched and self.enable_subtitle_ocr:
            try:
                from src.pure_subtitle_extractor import VisualSubtitleExtractor
                ocr_mode = getattr(self.config, 'ocr_mode', 'cloud')
                local_conf = getattr(self.config, 'ocr_local_confidence_threshold', 0.5)
                logging.info(f"🔮 [Pipeline] 正在同步进行纯画面视觉硬字幕 OCR 提取时间线 (模式: {ocr_mode.upper()})...")
                
                extractor = VisualSubtitleExtractor(
                    api_key=self.config.qwen_api_key,
                    api_base=self.config.qwen_api_base,
                    model=getattr(self.config, "qwen_ocr_model", self.config.qwen_model),
                    temp_dir=self.config.temp_dir,
                    ocr_mode=ocr_mode,
                    local_engine=getattr(self.config, 'ocr_local_engine', 'rapidocr'),
                    local_confidence_threshold=local_conf,
                )
                
                # Execute extraction with Heuristic 3 active. The returned SRT/MD
                # strings are intentionally not written to the wiki in normal mode.
                extractor.run_extraction(video_path, interval_sec=2.0)
                
                # Compute telemetry metrics comparing ASR segments and OCR timeline (Requirement 2)
                total_ocr_attempts = extractor.ocr_called_count + getattr(extractor, "ocr_local_count", 0)
                ocr_metrics = self.compute_ocr_metrics(
                    asr_segments=structured_segments,
                    visual_timeline=extractor.subtitle_timeline,
                    ocr_called=total_ocr_attempts,
                    ocr_duplicates=extractor.ocr_duplicate_count
                )
                
                logging.info("\n" + "="*50)
                logging.info(" 📊  嵌入式硬字幕 OCR 提取完成！")
                logging.info("="*50)
                logging.info(f"  - 🎤 ASR语音片段总数: {ocr_metrics['total_asr']}")
                logging.info(f"  - ☁️ 云端 OCR 调用次数: {extractor.ocr_called_count}")
                logging.info(f"  - 💻 本地 OCR 调用次数: {extractor.ocr_local_count}")
                if extractor.ocr_mode == 'hybrid':
                    logging.info(f"  - ⬆️ Hybrid 升级精修次数: {extractor.ocr_hybrid_escalated_count}")
                logging.info(f"  - ⚠️ OCR重复识别次数: {ocr_metrics['ocr_duplicates']} (重复率: {ocr_metrics['duplicate_rate']:.2f}%)")
                logging.info(f"  - 🎯 OCR语音覆盖率: {ocr_metrics['ocr_coverage_rate']:.2f}% (命中数: {ocr_metrics['hits']})")
                logging.info("="*50 + "\n")
                logging.info(" 📦 纯视觉字幕时间线仅作为内部校验信号使用，不再写入最终 Wiki 目录。")
                
                # Combine both results to construct a dual-source combined timeline (Requirement 4)
                transcript_text = self.format_dual_source_timeline(structured_segments, extractor.subtitle_timeline)
                
            except Exception as e:
                logging.warning(f"⚠️ [Pipeline] 嵌入式硬字幕 OCR 提取失败: {e}。将优雅降级回退至纯 ASR 流程。")

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

        # 5.5 Optional Multimodal Visual Grounding (VLM-based hardcoded subtitle correction)
        grounding_context = ""
        dynamic_mapping = {}
        if self.search_enabled and not subtitles_fetched and video_path:
            try:
                text_key = self.config.deepseek_api_key if self.active_provider_name == "deepseek" else self.config.openai_compat_api_key
                text_base = self.config.deepseek_api_base if self.active_provider_name == "deepseek" else self.config.openai_compat_api_base
                text_model = self.active_model_name
                
                vlm_key = self.config.qwen_api_key
                vlm_base = self.config.qwen_api_base
                vlm_model = self.config.qwen_model
                
                from src.search_grounding import VisualGrounding
                grounder = VisualGrounding(
                    api_key=vlm_key,
                    api_base=vlm_base,
                    model=vlm_model,
                    temp_dir=self.config.temp_dir
                )
                
                logging.info("👁️ [Pipeline] 正在启动多模态视觉硬字幕纠偏层 (Visual Grounding)...")
                dynamic_mapping = grounder.perform_visual_grounding(
                    transcript_text=transcript_text,
                    structured_segments=structured_segments,
                    video_path=video_path,
                    text_api_key=text_key,
                    text_api_base=text_base,
                    text_model=text_model
                )
                if dynamic_mapping:
                    logging.info(f"👁️ [Pipeline] 多模态视觉校验得出以下更正词映射: {dynamic_mapping}")
            except Exception as e:
                logging.warning(f"多模态视觉硬字幕纠偏执行异常，将优雅跳过: {e}")

        # 5.6 ASR Term Normalization (前置实体纠偏层)
        from src.normalizer import TermNormalizer
        normalizer = TermNormalizer(custom_corrections_path=self.config.custom_corrections_path)
        
        canonical_transcript_text, corrections_log = normalizer.normalize(transcript_text, dynamic_mapping)
        
        # 5.7 ASR 纠偏二次校验与自愈闭环 (Self-Healing Verification Loop)
        if self.active_provider_name == "deepseek":
            active_key = self.config.deepseek_api_key
            active_base = self.config.deepseek_api_base
            active_model = self.deepseek_model
        elif self.active_provider_name == "openai_compatible":
            active_key = self.config.openai_compat_api_key
            active_base = self.config.openai_compat_api_base
            active_model = self.openai_compat_model
        elif self.config.deepseek_api_key:
            active_key = self.config.deepseek_api_key
            active_base = self.config.deepseek_api_base
            active_model = self.deepseek_model
        else:
            active_key = self.config.openai_compat_api_key
            active_base = self.config.openai_compat_api_base
            active_model = self.openai_compat_model
        
        canonical_transcript_text, corrections_log = normalizer.verify_and_heal(
            original_transcript=transcript_text,
            canonical_text=canonical_transcript_text,
            initial_corrections=corrections_log,
            api_key=active_key,
            api_base=active_base,
            model=active_model
        )
        self.corrections_log = corrections_log

        # Combine normalized ASR text with grounding context if present
        full_transcript_text = canonical_transcript_text
        if grounding_context:
            full_transcript_text = canonical_transcript_text + "\n\n" + grounding_context

        # 6. Model Alignment / Note Generation
        if not self.config.enable_images:
            try:
                if self.active_provider_name == "deepseek":
                    provider = DeepSeekProvider(
                        api_key=self.config.deepseek_api_key,
                        api_base=self.config.deepseek_api_base,
                        model=self.deepseek_model,
                        enable_thinking=self.config.deepseek_enable_thinking,
                        reasoning_effort=self.config.deepseek_reasoning_effort,
                        structuring_prompt=self.config.deepseek_structuring_prompt
                    )
                elif self.active_provider_name == "openai_compatible":
                    from src.providers.openai_compatible import OpenAICompatibleProvider
                    provider = OpenAICompatibleProvider(
                        api_key=self.config.openai_compat_api_key,
                        api_base=self.config.openai_compat_api_base,
                        model=self.openai_compat_model,
                        structuring_prompt=self.config.openai_compat_structuring_prompt
                    )
                else:
                    logging.error(f"不支持的文本 Provider: {self.active_provider_name}")
                    sys.exit(1)

                markdown_content = provider.generate_text_wiki(
                    transcript_text=full_transcript_text,
                    video_title=video_title
                )
                selected_frames = []
            except Exception as e:
                logging.error(f"调用文本大模型接口失败: {e}")
                sys.exit(1)
        elif self.provider_name == "qwen":
            try:
                provider = QwenProvider(
                    api_key=self.config.qwen_api_key,
                    api_base=self.config.qwen_api_base,
                    model=self.qwen_model
                )
                markdown_content, selected_frames = provider.generate_wiki(
                    transcript_text=full_transcript_text,
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

        # 6.5 Article-Level Verification and Self-Healing Loop
        healed_mappings = normalizer.verify_and_heal_article(
            original_transcript=transcript_text,
            canonical_text=canonical_transcript_text,
            markdown_content=markdown_content,
            initial_corrections=self.corrections_log,
            api_key=active_key,
            api_base=active_base,
            model=active_model
        )

        if healed_mappings:
            logging.warning("⚠️ [Pipeline] 检测到最终文章中存在术语逻辑冲突！启动二次深度自愈...")
            # A. Save corrections persistently
            normalizer.save_custom_corrections(healed_mappings)
            
            # B. Re-normalize original transcript with corrected mappings (dynamic override)
            canonical_transcript_text, self.corrections_log = normalizer.normalize(transcript_text, dynamic_mapping)
            
            full_transcript_text = canonical_transcript_text
            if grounding_context:
                full_transcript_text = canonical_transcript_text + "\n\n" + grounding_context
            
            # C. Regenerate Markdown Content
            logging.info("🔄 [Pipeline] 正在使用自愈后的纠偏词重新生成文章，消除概念冲突...")
            if not self.config.enable_images:
                try:
                    markdown_content = provider.generate_text_wiki(
                        transcript_text=full_transcript_text,
                        video_title=video_title
                    )
                except Exception as e:
                    logging.error(f"二次调用文本大模型接口失败: {e}")
                    sys.exit(1)
            elif self.provider_name == "qwen":
                try:
                    markdown_content, selected_frames = provider.generate_wiki(
                        transcript_text=full_transcript_text,
                        candidate_frames=located_candidate_frames,
                        video_title=video_title,
                        candidate_frames_dir=candidate_frames_dir,
                        visual_anchors=visual_anchors,
                        enable_images=self.config.enable_images
                    )
                except Exception as e:
                    logging.error(f"二次调用百炼大模型接口失败: {e}")
                    sys.exit(1)
            logging.info("💚 [Pipeline] 二次生成与自愈完成！")

        # 6.6 Timeline-Level Cleanup
        # The raw dual-source timeline is a synthesis aid. Before publishing it
        # into the wiki, repair it against the final article so obvious ASR/OCR
        # noise does not remain in the document.
        publish_transcript_text = normalizer.repair_timeline_with_article(
            transcript_text=full_transcript_text,
            markdown_content=markdown_content,
            api_key=active_key,
            api_base=active_base,
            model=active_model
        )

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
                transcript_text=publish_transcript_text,
                structured_segments=structured_segments,
                candidate_frames=candidate_frames,
                visual_anchors=visual_anchors,
                provider_name=self.active_provider_name,
                model_name=self.active_model_name,
                keep_temp=keep_temp,
                asr_corrections=self.corrections_log
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
        if 'ocr_metrics' in locals() and ocr_metrics:
            print(f" 📊  ASR-OCR 语音覆盖率: {ocr_metrics['ocr_coverage_rate']:.2f}% (命中数: {ocr_metrics['hits']}/{ocr_metrics['total_asr']})")
            if 'extractor' in locals():
                mode = getattr(extractor, 'ocr_mode', 'cloud')
                cloud_n = getattr(extractor, 'ocr_called_count', 0)
                local_n = getattr(extractor, 'ocr_local_count', 0)
                hybrid_n = getattr(extractor, 'ocr_hybrid_escalated_count', 0)
                dup_rate = ocr_metrics['duplicate_rate']
                print(f" 📊  OCR 模式: [{mode.upper()}]  ☁️ 云端调用: {cloud_n} 次  💻 本地调用: {local_n} 次  重复率: {dup_rate:.2f}%")
                if mode == 'hybrid' and hybrid_n:
                    print(f" 📊  Hybrid 升级精修次数: {hybrid_n} 次")
            else:
                print(f" 📊  OCR 实际调用/重复率: {ocr_metrics['ocr_called']} 次 / {ocr_metrics['duplicate_rate']:.2f}%")
        if keep_temp:
            print(f" 🧪  临时文件已保留: {self.config.temp_dir}")
        print(f" ⏱️  总耗时: {minutes} 分 {seconds} 秒")
        print("="*50 + "\n")
        
        return final_markdown_path

    def compute_ocr_metrics(self, asr_segments, visual_timeline, ocr_called, ocr_duplicates):
        """
        Computes deep telemetry metrics between ASR and OCR subtitle outputs.
        """
        if not asr_segments:
            return {
                "ocr_called": ocr_called,
                "ocr_duplicates": ocr_duplicates,
                "duplicate_rate": 0.0,
                "ocr_coverage_rate": 0.0,
                "hits": 0,
                "misses": 0,
                "total_asr": 0
            }
            
        hits = 0
        misses = 0
        total_asr = len(asr_segments)
        
        # We check alignment for each ASR segment
        for seg in asr_segments:
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            
            # Look for any overlapping visual subtitle frame that has text
            has_overlap = False
            for item in visual_timeline:
                sec = item["seconds"]
                text = item["text"]
                if text and text.lower() != "none":
                    # If visual subtitle falls within the ASR speaking range (with 1.5s tolerance padding)
                    if (start - 1.5) <= sec <= (end + 1.5):
                        has_overlap = True
                        break
            if has_overlap:
                hits += 1
            else:
                misses += 1
                
        coverage_rate = (hits / total_asr) * 100 if total_asr > 0 else 0.0
        duplicate_rate = (ocr_duplicates / ocr_called) * 100 if ocr_called > 0 else 0.0
        
        return {
            "ocr_called": ocr_called,
            "ocr_duplicates": ocr_duplicates,
            "duplicate_rate": duplicate_rate,
            "ocr_coverage_rate": coverage_rate,
            "hits": hits,
            "misses": misses,
            "total_asr": total_asr
        }

    def format_dual_source_timeline(self, asr_segments, visual_timeline):
        """
        Merge ASR speech segments and visual OCR segments into a chronological, 
        aligned dual-source timeline string for optimal LLM synthesis.
        """
        # Create list of chronological events
        events = []
        
        # Add ASR segments
        for seg in asr_segments:
            events.append({
                "time": seg.get("start", 0.0),
                "type": "asr",
                "text": seg.get("text", "").strip()
            })
            
        # Add Visual timeline segments
        for item in visual_timeline:
            text = item["text"].strip()
            if text and text.lower() != "none":
                events.append({
                    "time": item["seconds"],
                    "type": "ocr",
                    "text": text
                })
                
        # Sort chronologically
        events.sort(key=lambda x: x["time"])
        
        # Merge close events or list them chronologically
        lines = []
        lines.append("以下是该技术视频的【双源对照参考时间线】。它合并了 ASR 语音识别结果与视频画面纯视觉 OCR 硬字幕提取结果。")
        lines.append("ASR 语音识别包含完整的说话口语上下文，但可能包含同音字、英文缩写或代码专有名词错乱；")
        lines.append("而视觉 OCR 字幕直接从画面中提取，对技术代码词汇（如 Vibe Coding, DeepSeek, CLAUDE.md 等）的大小写与拼写具有 100% 的高精确度。")
        lines.append("请您在理解语义、归纳总结和编写最终技术维基笔记时，优先以视觉 OCR 字幕的精准拼写为准，并参考 ASR 提供的完整表达上下文！\n")
        lines.append("双源对照参考流水线：\n===================================")
        
        # Group events by 4-second blocks to make them beautifully side-by-side or chronological
        current_block_time = -10.0
        current_asr_texts = []
        current_ocr_texts = []
        
        def format_timestamp(sec):
            h = int(sec) // 3600
            m = (int(sec) % 3600) // 60
            s = int(sec) % 60
            return f"{h:02d}:{m:02d}:{s:02d}"
            
        for ev in events:
            t = ev["time"]
            # If event is within 3.5 seconds of the current block, group them together
            if t - current_block_time > 3.5:
                # Flush previous block
                if current_asr_texts or current_ocr_texts:
                    ts_str = format_timestamp(current_block_time)
                    lines.append(f"- **[{ts_str}]**")
                    if current_asr_texts:
                        lines.append(f"  - 🎤 ASR 语音: {' / '.join(current_asr_texts)}")
                    if current_ocr_texts:
                        lines.append(f"  - 👁️ 视觉字幕: {' / '.join(current_ocr_texts)}")
                # Start new block
                current_block_time = t
                current_asr_texts = []
                current_ocr_texts = []
                
            if ev["type"] == "asr":
                if ev["text"] not in current_asr_texts:
                    current_asr_texts.append(ev["text"])
            else:
                if ev["text"] not in current_ocr_texts:
                    current_ocr_texts.append(ev["text"])
                    
        # Flush last block
        if current_asr_texts or current_ocr_texts:
            ts_str = format_timestamp(current_block_time)
            lines.append(f"- **[{ts_str}]**")
            if current_asr_texts:
                lines.append(f"  - 🎤 ASR 语音: {' / '.join(current_asr_texts)}")
            if current_ocr_texts:
                lines.append(f"  - 👁️ 视觉字幕: {' / '.join(current_ocr_texts)}")
                
        lines.append("===================================")
        return "\n".join(lines)
