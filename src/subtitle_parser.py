import os
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class SubtitleParser:
    """
    Robust Subtitle Parser for WebVTT (.vtt) and SubRip (.srt) formats.
    Translates captions into standard structured segments:
    [{"start": float, "end": float, "text": str}]
    """

    def __init__(self):
        pass

    def parse(self, filepath):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"字幕文件未找到: {filepath}")
            
        ext = os.path.splitext(filepath)[1].lower()
        
        logging.info(f"开始解析字幕文件: {os.path.basename(filepath)} (格式: {ext})")
        
        if ext == ".vtt":
            return self._parse_vtt(filepath)
        elif ext == ".srt":
            return self._parse_srt(filepath)
        else:
            raise ValueError(f"不支持的字幕格式: {ext}。目前仅支持 .vtt 与 .srt。")

    def time_to_seconds(self, time_str):
        """
        Convert time string (HH:MM:SS.mmm or MM:SS.mmm) to float seconds.
        Handles commas or periods as millisecond separators.
        """
        time_str = time_str.replace(",", ".").strip()
        parts = time_str.split(":")
        
        try:
            if len(parts) == 3:
                h = float(parts[0])
                m = float(parts[1])
                s = float(parts[2])
                return h * 3600 + m * 60 + s
            elif len(parts) == 2:
                m = float(parts[0])
                s = float(parts[1])
                return m * 60 + s
            else:
                # Direct float
                return float(time_str)
        except ValueError:
            logging.warning(f"时间戳解析异常，将使用默认值 0.0: '{time_str}'")
            return 0.0

    def _parse_vtt(self, filepath):
        segments = []
        
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        current_seg = None
        # VTT timestamp line regex: matches 00:00:12.300 --> 00:00:15.600
        time_pattern = re.compile(r"(\d{2}:)?\d{2}:\d{2}[\.,]\d{3}\s*-->\s*(\d{2}:)?\d{2}:\d{2}[\.,]\d{3}")

        for line in lines:
            line = line.strip()
            
            # Skip VTT header
            if line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE"):
                continue
                
            if time_pattern.match(line):
                # Save previous segment if exists
                if current_seg and current_seg["text"]:
                    segments.append(current_seg)
                
                parts = line.split("-->")
                start_sec = self.time_to_seconds(parts[0])
                end_sec = self.time_to_seconds(parts[1])
                
                current_seg = {
                    "start": start_sec,
                    "end": end_sec,
                    "text": ""
                }
            elif current_seg and line:
                # Append subtitle text line
                # Ignore inline HTML tags like <b>, <i>, <c.color> often found in VTT
                clean_line = re.sub(r"<[^>]+>", "", line).strip()
                if clean_line:
                    if current_seg["text"]:
                        current_seg["text"] += " " + clean_line
                    else:
                        current_seg["text"] = clean_line
            elif not line and current_seg:
                # Empty line marks segment boundary
                if current_seg["text"]:
                    segments.append(current_seg)
                    current_seg = None

        if current_seg and current_seg["text"]:
            segments.append(current_seg)

        # Post-clean to remove Bilibili style automatic line alignments or empty segments
        final_segs = [s for s in segments if s["text"].strip()]
        logging.info(f"VTT 解析完成。提取出 {len(final_segs)} 个字幕帧。")
        return final_segs

    def _parse_srt(self, filepath):
        segments = []
        
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # SRT block matches: index\nstart --> end\ntext\n
        blocks = re.split(r"\n\s*\n", content.strip())
        time_pattern = re.compile(r"(\d{2}:)?\d{2}:\d{2}[\.,]\d{3}\s*-->\s*(\d{2}:)?\d{2}:\d{2}[\.,]\d{3}")

        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 2:
                continue
                
            # Line 0 is usually the index, Line 1 is the timestamp
            # Sometimes index line is missing or malformed, so search for timestamp line
            time_line = ""
            text_start_idx = 1
            
            for idx, line in enumerate(lines[:3]):
                if time_pattern.match(line):
                    time_line = line
                    text_start_idx = idx + 1
                    break
                    
            if not time_line:
                continue
                
            parts = time_line.split("-->")
            start_sec = self.time_to_seconds(parts[0])
            end_sec = self.time_to_seconds(parts[1])
            
            text = " ".join([l.strip() for l in lines[text_start_idx:]]).strip()
            # Clean HTML tags
            text = re.sub(r"<[^>]+>", "", text).strip()
            
            if text:
                segments.append({
                    "start": start_sec,
                    "end": end_sec,
                    "text": text
                })

        logging.info(f"SRT 解析完成。提取出 {len(segments)} 个字幕帧。")
        return segments
