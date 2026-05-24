import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class OralSanitizer:
    """
    Sanitize ASR or subtitle texts by filtering out redundant oral filler words,
    correcting minor errors, and merging highly fragmented short segments.
    """
    
    # Common high-frequency Chinese filler words and phrases
    FILLER_PATTERNS = [
        # Explicit filler phrases
        (r"\b就是说那个\b", ""),
        (r"\b那什么\b", ""),
        (r"\b然后呢\b", "然后"),
        (r"\b然后其实\b", "其实"),
        (r"\b这个那个\b", ""),
        
        # High-frequency spoken interjections (often surrounded by spaces or punctuation)
        (r"(?<=[\s，。！？、])(呃|啊|哦|哈|耶|吧|嘛)(?=[\s，。！？、])", ""),
        # Consecutive identical stuttering words (e.g., "就是就是", "然后然后")
        (r"\b(就是){2,}\b", "就是"),
        (r"\b(然后){2,}\b", "然后"),
        (r"\b(那个){2,}\b", "那个"),
    ]

    def __init__(self):
        pass

    def clean_fillers(self, text):
        """
        Regex-based oral filler words cleaner.
        """
        if not text:
            return ""
            
        cleaned = text
        for pattern, replacement in self.FILLER_PATTERNS:
            try:
                cleaned = re.sub(pattern, replacement, cleaned)
            except Exception as e:
                logging.warning(f"正则过滤失败: {pattern} -> {e}")
                
        # Compress redundant spaces
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def merge_segments(self, segments, max_gap_sec=2.2, max_len=150):
        """
        Merges extremely fragmented adjacent segments into readable paragraphs.
        
        Args:
            segments (list): List of dicts [{"start": float, "end": float, "text": str}]
            max_gap_sec (float): Max silent gap allowed between segments to combine them.
            max_len (int): Max char length of a single merged segment before forcing a split.
            
        Returns:
            list: Compact, highly readable segments.
        """
        if not segments:
            return []
            
        merged = []
        current = None
        
        for seg in segments:
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start))
            text = self.clean_fillers(seg.get("text", "")).strip()
            
            if not text:
                continue
                
            if current is None:
                current = {
                    "start": start,
                    "end": end,
                    "text": text
                }
            else:
                gap = start - current["end"]
                # Conditions to merge:
                # 1. Temporal proximity: gap between current end and next start is small
                # 2. Text length limit: merged text is not overly long
                if gap <= max_gap_sec and (len(current["text"]) + len(text) < max_len):
                    # Combine texts nicely
                    if current["text"].endswith((".", ",", "!", "?", "。", "，", "！", "？")):
                        current["text"] += " " + text
                    else:
                        # Auto-append a soft comma if no punctuation exists at boundary
                        current["text"] += "，" + text
                    current["end"] = end
                else:
                    merged.append(current)
                    current = {
                        "start": start,
                        "end": end,
                        "text": text
                    }
                    
        if current:
            merged.append(current)
            
        logging.info(f"OralSanitizer: 时间线合并完成。原片段数: {len(segments)} -> 合并后段数: {len(merged)}")
        return merged

    def format_timeline(self, segments, formatter_fn):
        """
        Converts parsed segments back to the timeline block string.
        """
        lines = []
        for seg in segments:
            start_str = formatter_fn(seg["start"])
            end_str = formatter_fn(seg["end"])
            lines.append(f"[{start_str} --> {end_str}] {seg['text']}")
        return "\n".join(lines)
