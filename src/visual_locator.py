import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class VisualLocator:
    """
    Locate frames that are likely to supplement transcript text.
    This first version is dependency-free: it uses transcript semantic triggers,
    time-window matching, and distance-based deduplication.
    """

    TRIGGERS = {
        "表格": 3.2,
        "表": 2.4,
        "参数": 3.0,
        "配置页": 2.8,
        "配置界面": 2.8,
        "参数偏好": 3.0,
        "规格": 2.8,
        "跑分": 3.0,
        "榜单": 2.8,
        "排行榜": 3.0,
        "对比表": 3.4,
        "对比图": 3.2,
        "对比": 2.2,
        "架构图": 3.4,
        "架构": 2.8,
        "流程图": 3.4,
        "流程": 2.6,
        "代码": 3.0,
        "公式": 3.0,
        "图表": 3.2,
        "曲线": 2.8,
        "柱状图": 3.0,
        "折线图": 3.0,
        "矩阵": 2.8,
        "拓扑": 3.0,
        "界面": 2.0,
        "控制台": 2.4,
        "设置页": 2.4,
        "演示": 2.0,
        "屏幕上": 2.4,
        "画面中": 2.4,
        "这张图": 3.2,
        "这个表": 3.2,
        "这张表": 3.4,
        "如图": 3.2,
        "PPI": 2.6,
    }

    def __init__(self, min_frames=0, max_frames=8, trigger_window_sec=6, min_gap_sec=12, min_score=2.5):
        self.min_frames = min_frames
        self.max_frames = max_frames
        self.trigger_window_sec = trigger_window_sec
        self.min_gap_sec = min_gap_sec
        self.min_score = min_score

    def locate(self, structured_segments, candidate_frames):
        if not candidate_frames:
            return []

        events = self._semantic_events(structured_segments)
        scored = self._score_frames(candidate_frames, events)
        selected = self._select_ranked(scored)
        selected = self._ensure_minimum_coverage(selected, scored)
        selected = sorted(selected, key=lambda item: item["seconds"])

        logging.info(
            "VisualLocator 完成：语义触发点 %d 个，定位视觉锚点 %d 个。",
            len(events),
            len(selected)
        )
        return selected

    def filter_candidate_frames(self, candidate_frames, visual_anchors):
        anchor_filenames = {anchor["filename"] for anchor in visual_anchors}
        return [frame for frame in candidate_frames if frame[0] in anchor_filenames]

    def _semantic_events(self, structured_segments):
        events = []
        for segment in structured_segments:
            text = segment.get("text", "")
            hits = []
            weight = 0
            for trigger, trigger_weight in self.TRIGGERS.items():
                if trigger.lower() in text.lower():
                    hits.append(trigger)
                    weight += trigger_weight

            if hits:
                events.append({
                    "time": float(segment.get("start", 0)),
                    "end": float(segment.get("end", segment.get("start", 0))),
                    "text": text,
                    "triggers": hits,
                    "weight": weight,
                })

        return events

    def _score_frames(self, candidate_frames, events):
        scored = []
        duration = max(frame[2] for frame in candidate_frames) if candidate_frames else 0

        for filename, fmt_time, seconds in candidate_frames:
            score = 0.0
            reasons = []
            nearby_texts = []

            if seconds < 3 and duration > 30:
                score -= 1.5

            for event in events:
                distance = abs(seconds - event["time"])
                if distance <= self.trigger_window_sec:
                    proximity = 1 - (distance / self.trigger_window_sec)
                    event_score = event["weight"] * (0.35 + 0.65 * proximity)
                    score += event_score
                    reasons.append("语义触发: " + ",".join(event["triggers"][:4]))
                    nearby_texts.append(event["text"])

            scored.append({
                "filename": filename,
                "timestamp": fmt_time.replace("_", ":"),
                "seconds": seconds,
                "score": round(score, 4),
                "reasons": sorted(set(reasons)),
                "nearby_text": " / ".join(nearby_texts[:3]),
            })

        return scored

    def _select_ranked(self, scored):
        ranked = sorted(scored, key=lambda item: (item["score"], item["seconds"]), reverse=True)
        selected = []

        for item in ranked:
            if item["score"] < self.min_score:
                break
            if self._too_close(item, selected):
                continue
            selected.append(item)
            if len(selected) >= self.max_frames:
                break

        return selected

    def _ensure_minimum_coverage(self, selected, scored):
        if len(selected) >= self.min_frames or not scored:
            return selected

        eligible = [item for item in scored if item["score"] >= self.min_score]
        if not eligible:
            return selected

        for item in sorted(eligible, key=lambda item: item["score"], reverse=True):
            if self._too_close(item, selected):
                continue
            selected.append(item)
            if len(selected) >= self.min_frames:
                break

        return selected

    def _too_close(self, item, selected):
        return any(abs(item["seconds"] - existing["seconds"]) < self.min_gap_sec for existing in selected)
