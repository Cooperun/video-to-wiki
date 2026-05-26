import sys
import argparse
import logging
from src.pipeline import IngestionPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def show_banner():
    banner = """
===========================================================
  🎥  Video to Markdown Wiki Ingestion System (video_to_wiki)
===========================================================
    """
    print(banner)

def parse_args():
    parser = argparse.ArgumentParser(description="将技术视频转换为带关键帧插图的 Markdown 知识库文章")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", type=str, help="视频的在线链接 (优先支持 Bilibili、YouTube；强登录平台失败后请用 --file)")
    group.add_argument("--file", type=str, help="本地视频文件的路径 (如微信视频号本地备份, .mp4, .mkv)")
    group.add_argument("--init", action="store_true", help="一键交互式自动初始化系统全局配置、环境路径与工作区")
    
    parser.add_argument("--config", type=str, default=None,
                        help="手动指定要加载的 config.yaml 文件路径。默认自动检索 CWD 与用户主目录。")
    parser.add_argument("--provider", type=str, choices=["qwen", "deepseek", "openai_compatible"], default=None, 
                        help="多模态大模型引擎，默认读取 config.yaml 中的配置")
    parser.add_argument("--model", type=str, default=None,
                        help="大模型名称，如 deepseek-v4-pro、qwen3-vl-plus 等，覆盖配置文件")
    parser.add_argument("--keep-temp", action="store_true",
                        help="处理成功后保留临时文件，便于调试或复用中间产物")
    parser.add_argument("--search", action="store_true",
                        help="一键激活多模态视觉硬字幕纠偏，校正 ASR 语音中发音模糊的生词名词")
    parser.add_argument("--extract-subtitle", action="store_true",
                        help="纯画面视觉硬字幕提取功能 (跳过语音 ASR，采用云端 VLM 差分提取)")
    parser.add_argument("--ocr-mode", type=str, choices=["cloud", "local", "hybrid"], default=None,
                        help="硬字幕 OCR 后端模式：cloud=云端 Qwen-VL（默认）/ local=本地离线 EasyOCR / hybrid=本地优先+云端精修")
    return parser.parse_args()

def main():
    import os
    show_banner()
    args = parse_args()
    
    # 1. Handle One-Click System Initialization
    if args.init:
        from src.initializer import SystemInitializer
        try:
            SystemInitializer.run_init()
            sys.exit(0)
        except Exception as e:
            logging.error(f"系统智能初始化失败: {e}")
            sys.exit(1)
            
    input_source = args.url if args.url else args.file
    
    # 2. Handle Standalone Visual Subtitle Extraction (Phase 9)
    if args.extract_subtitle:
        from src.config import AppConfig
        from src.downloader import VideoDownloader
        from src.pure_subtitle_extractor import VisualSubtitleExtractor
        
        logging.info("🚀 启动纯画面视觉硬字幕提取模式 (跳过语音 ASR)...")
        config = AppConfig(config_path=args.config)
        downloader = VideoDownloader(temp_dir=config.temp_dir)
        
        try:
            # Step A: Resolve and download video format if online url
            if downloader.is_url(input_source):
                logging.info(f"正在抓取解析在线链接并下载视频流: {input_source}")
                video_path, video_title, _ = downloader.process_input(input_source)
            else:
                video_path = os.path.abspath(input_source)
                video_title = os.path.splitext(os.path.basename(video_path))[0]
                
            logging.info(f"本地视频文件就绪: {video_path}")
            
            # Step B: Initialize standalone subtitle extractor
            ocr_mode = args.ocr_mode if args.ocr_mode else getattr(config, 'ocr_mode', 'cloud')
            extractor = VisualSubtitleExtractor(
                api_key=config.qwen_api_key,
                api_base=config.qwen_api_base,
                model=config.qwen_model,
                temp_dir=config.temp_dir,
                ocr_mode=ocr_mode,
                local_engine=getattr(config, 'ocr_local_engine', 'rapidocr'),
                local_confidence_threshold=getattr(config, 'ocr_local_confidence_threshold', 0.5),
            )
            
            # Step C: Execute pure visual subtitle extraction (sampling every 2s)
            srt_content, md_content = extractor.run_extraction(video_path, interval_sec=2.0)
            
            # Step D: Save output files to wiki directory
            wiki_video_dir = os.path.join(config.wiki_dir, "视频知识库")
            os.makedirs(wiki_video_dir, exist_ok=True)
            
            srt_path = os.path.join(wiki_video_dir, f"{video_title}_纯视觉字幕.srt")
            md_path = os.path.join(wiki_video_dir, f"{video_title}_纯视觉字幕.md")
            
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)
                
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
                
            logging.info("\n" + "="*50)
            logging.info(" 🎉  恭喜！纯画面视觉硬字幕提取成功！")
            logging.info("="*50)
            logging.info(f" 📂  SRT字幕路径: {srt_path}")
            logging.info(f" 📂  时间线笔记路径: {md_path}")
            logging.info("="*50 + "\n")
            
            # Garbage collection
            if not args.keep_temp:
                downloader.cleanup()
                
            sys.exit(0)
        except KeyboardInterrupt:
            logging.warning("\n⚠️ 任务被用户中断退出。")
            sys.exit(130)
        except Exception as e:
            logging.error(f"纯画面字幕提取执行异常失败: {e}")
            sys.exit(1)
        
    # 3. Default Ingestion Pipeline (Normal Wiki compilation)
    try:
        pipeline = IngestionPipeline(
            config_path=args.config,
            provider=args.provider,
            model=args.model,
            search=args.search,
            ocr_mode=args.ocr_mode,
        )
    except Exception as e:
        logging.error(f"构建 IngestionPipeline 管道失败: {e}")
        sys.exit(1)
        
    # Drive execution
    try:
        pipeline.run(input_source, keep_temp=args.keep_temp)
    except KeyboardInterrupt:
        logging.warning("\n⚠️ 任务被用户中断退出。")
        sys.exit(130)
    except Exception as e:
        logging.error(f"IngestionPipeline 管道运行期异常失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
