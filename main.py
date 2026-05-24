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
    
    parser.add_argument("--config", type=str, default=None,
                        help="手动指定要加载的 config.yaml 文件路径。默认自动检索 CWD 与用户主目录。")
    parser.add_argument("--provider", type=str, choices=["qwen"], default=None, 
                        help="多模态大模型引擎，默认读取 config.yaml 中的配置")
    parser.add_argument("--model", type=str, default=None,
                        help="大模型名称，如 deepseek-v4-pro、qwen3-vl-plus 等，覆盖配置文件")
    parser.add_argument("--keep-temp", action="store_true",
                        help="处理成功后保留临时文件，便于调试或复用中间产物")
    return parser.parse_args()

def main():
    show_banner()
    args = parse_args()
    
    input_source = args.url if args.url else args.file
    
    # Initialize the modular pipeline
    try:
        pipeline = IngestionPipeline(
            config_path=args.config,
            provider=args.provider,
            model=args.model
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
