import os
import shutil
import re
import logging
import json
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class WikiCompiler:
    def __init__(self, wiki_dir, temp_dir, image_link_style="standard"):
        self.wiki_dir = os.path.abspath(wiki_dir)
        self.temp_dir = os.path.abspath(temp_dir)
        self.image_link_style = image_link_style
        
        # Target paths in llm_wiki
        self.output_wiki_dir = os.path.join(self.wiki_dir, "视频知识库")
        self.assets_dir = os.path.join(self.output_wiki_dir, "assets")
        self.manifests_dir = os.path.join(self.output_wiki_dir, "manifests")

    def compile(
        self,
        video_title,
        markdown_content,
        selected_frames,
        candidate_frames_dir,
        source_metadata=None,
        transcript_text="",
        structured_segments=None,
        candidate_frames=None,
        visual_anchors=None,
        provider_name="",
        model_name="",
        keep_temp=False
    ):
        """
        Package Markdown and screenshots into llm_wiki structure,
        correct image references, and clean up candidate files.
        """
        # Ensure target directories exist
        os.makedirs(self.output_wiki_dir, exist_ok=True)
        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.manifests_dir, exist_ok=True)
        
        logging.info("开始编译打包 Markdown 文章和选定的视频截图...")
        source_metadata = source_metadata or {}
        structured_segments = structured_segments or []
        candidate_frames = candidate_frames or []
        visual_anchors = visual_anchors or []
        imported_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

        # 1. Copy and rename selected screenshots
        copied_count = 0
        for frame in selected_frames:
            filename = frame.get("filename")
            timestamp = frame.get("timestamp")
            
            if not filename or not timestamp:
                continue
                
            src_path = os.path.join(candidate_frames_dir, filename)
            
            if not os.path.exists(src_path):
                # Sometimes VLM might return the formatted name instead of original
                # Let's search by timestamp inside candidate_frames_dir
                timestamp_clean = timestamp.replace(":", "_")
                found = False
                for f in os.listdir(candidate_frames_dir):
                    if timestamp_clean in f and f.endswith(".jpg"):
                        src_path = os.path.join(candidate_frames_dir, f)
                        found = True
                        break
                if not found:
                    logging.warning(f"跳过不存在的截图文件: {filename} (时间戳: {timestamp})")
                    continue
            
            # Format destination filename: [video_title]_[timestamp_formatted].jpg
            # E.g. system_architecture_00_02_35.jpg
            timestamp_fmt = timestamp.replace(":", "_")
            dest_filename = f"{video_title}_{timestamp_fmt}.jpg"
            dest_path = os.path.join(self.assets_dir, dest_filename)
            
            # Physical copy
            shutil.copy2(src_path, dest_path)
            copied_count += 1
            logging.info(f"保存截图: {dest_filename} -> assets/")

        logging.info(f"成功导出 {copied_count} 张截图到 assets 目录。")

        # 2. Correct image references to the configured style.
        modified_markdown = markdown_content
        
        # WikiLink: ![[screenshot_HH_MM_SS.jpg]] -> configured assets link.
        def wikilink_replacer(match):
            h, m, s = match.group(1), match.group(2), match.group(3)
            return self.format_image_link(video_title, f"{h}_{m}_{s}", f"视频截图 {h}:{m}:{s}")
            
        modified_markdown = re.sub(
            r"!\[\[screenshot_(\d{2})_(\d{2})_(\d{2})\.jpg\]\]",
            wikilink_replacer,
            modified_markdown
        )
        
        # Standard Markdown format image link replacer:
        # e.g., ![description](screenshot_HH_MM_SS.jpg) -> configured assets link.
        def markdown_replacer(match):
            desc = match.group(1)
            h, m, s = match.group(2), match.group(3), match.group(4)
            return self.format_image_link(video_title, f"{h}_{m}_{s}", desc or f"视频截图 {h}:{m}:{s}")
            
        modified_markdown = re.sub(
            r"!\[(.*?)\]\(screenshot_(\d{2})_(\d{2})_(\d{2})\.jpg\)",
            markdown_replacer,
            modified_markdown
        )

        if selected_frames and "assets/" not in modified_markdown:
            modified_markdown += "\n\n## 关键截图\n\n"
            for frame in selected_frames:
                timestamp = frame.get("timestamp", "")
                if not timestamp:
                    continue
                timestamp_fmt = timestamp.replace(":", "_")
                modified_markdown += f"{self.format_image_link(video_title, timestamp_fmt, f'视频截图 {timestamp}')}\n"
                modified_markdown += f"> 自动保留的视频关键帧，时间戳 {timestamp}。\n\n"

        # 3. Save finalized Markdown file to llm_wiki
        markdown_filename = f"{video_title}.md"
        markdown_path = os.path.join(self.output_wiki_dir, markdown_filename)
        
        # Add metadata / header info to Markdown
        source_url = source_metadata.get("source_url", "")
        platform = source_metadata.get("platform", "")
        source_id = source_metadata.get("source_id", "")
        header = (
            "---\n"
            f"title: {json.dumps(video_title, ensure_ascii=False)}\n"
            "source_type: video\n"
            f"source_url: {json.dumps(source_url, ensure_ascii=False)}\n"
            f"platform: {json.dumps(platform, ensure_ascii=False)}\n"
            f"source_id: {json.dumps(source_id, ensure_ascii=False)}\n"
            f"imported_at: {json.dumps(imported_at, ensure_ascii=False)}\n"
            "tags:\n"
            "  - 视频知识库\n"
            "  - 自动导入\n"
            "---\n\n"
        )

        final_body = self.ensure_retrieval_sections(modified_markdown, transcript_text)
        
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(header + final_body)
            
        logging.info(f"最终 Markdown 笔记已成功写入: {markdown_path}")

        manifest_path = self.write_manifest(
            video_title=video_title,
            markdown_path=markdown_path,
            copied_count=copied_count,
            selected_frames=selected_frames,
            candidate_frames=candidate_frames,
            visual_anchors=visual_anchors,
            source_metadata=source_metadata,
            structured_segments=structured_segments,
            provider_name=provider_name,
            model_name=model_name,
            imported_at=imported_at
        )
        
        # 4. Garbage Collection (Clean Up temporary directory)
        if keep_temp:
            logging.info(f"已启用 keep-temp，保留临时目录: {self.temp_dir}")
        else:
            self.cleanup()
        
        return markdown_path, manifest_path

    def format_image_link(self, video_title, timestamp_fmt, alt_text):
        path = f"assets/{video_title}_{timestamp_fmt}.jpg"
        if self.image_link_style == "obsidian":
            return f"![[{path}]]"
        return f"![{alt_text}]({path})"

    def ensure_retrieval_sections(self, markdown_content, transcript_text):
        final_body = markdown_content.strip()

        if final_body.count("```") % 2 != 0:
            logging.warning("检测到 Markdown 代码围栏未闭合，已自动补齐。")
            final_body += "\n```\n"

        if "## 可用于后续问答的事实" not in final_body:
            final_body += (
                "\n\n## 可用于后续问答的事实\n\n"
                "- 本节由导入工具预留，用于后续将视频内容整理为更细粒度的事实卡片。\n"
            )

        if transcript_text and "## 原始转写时间线" not in final_body:
            final_body += "\n\n## 原始转写时间线\n\n"
            final_body += "```text\n"
            final_body += transcript_text.strip()
            final_body += "\n```\n"

        return final_body + "\n"

    def write_manifest(
        self,
        video_title,
        markdown_path,
        copied_count,
        selected_frames,
        candidate_frames,
        visual_anchors,
        source_metadata,
        structured_segments,
        provider_name,
        model_name,
        imported_at
    ):
        manifest = {
            "schema_version": 1,
            "imported_at": imported_at,
            "video_title": video_title,
            "source": source_metadata,
            "outputs": {
                "markdown_path": markdown_path,
                "assets_dir": self.assets_dir,
                "selected_frame_count": copied_count,
            },
            "processing": {
                "provider": provider_name,
                "model": model_name,
                "candidate_frame_count": len(candidate_frames),
                "visual_anchor_count": len(visual_anchors),
                "image_count": len(selected_frames),
                "transcript_segment_count": len(structured_segments),
            },
            "selected_frames": selected_frames,
            "visual_anchors": visual_anchors,
            "candidate_frames": [
                {
                    "filename": frame[0],
                    "timestamp": frame[1].replace("_", ":"),
                    "seconds": frame[2],
                }
                for frame in candidate_frames
            ],
        }

        manifest_path = os.path.join(self.manifests_dir, f"{video_title}.manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        logging.info(f"导入 manifest 已写入: {manifest_path}")
        return manifest_path

    def cleanup(self):
        """
        Remove temporary media files to free local disk space.
        """
        logging.info("执行垃圾清理，清空临时音频与未选中的候选帧图片...")
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                logging.info(f"临时目录 {self.temp_dir} 已成功清空。")
            except Exception as e:
                logging.warning(f"清空临时目录失败: {e}")
