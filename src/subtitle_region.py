import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple


logger = logging.getLogger(__name__)


Box = Tuple[int, int, int, int]


@dataclass
class SubtitleRegion:
    box: Box
    confidence: float
    source: str


class SubtitleRegionDetector:
    """
    Estimate the on-screen subtitle band before OCR.

    The detector uses lightweight local image processing instead of OCR. It looks
    for dense high-contrast horizontal text strokes in the lower half of sampled
    frames, then expands the detected band so downstream OCR receives the full
    caption area instead of a fixed bottom strip.
    """

    def __init__(
        self,
        fallback_top_ratio: float = 0.72,
        fallback_bottom_ratio: float = 0.96,
        scan_top_ratio: float = 0.45,
        scan_bottom_ratio: float = 0.98,
        min_band_height_ratio: float = 0.10,
    ):
        self.fallback_top_ratio = fallback_top_ratio
        self.fallback_bottom_ratio = fallback_bottom_ratio
        self.scan_top_ratio = scan_top_ratio
        self.scan_bottom_ratio = scan_bottom_ratio
        self.min_band_height_ratio = min_band_height_ratio

    def fallback_box(self, width: int, height: int) -> Box:
        return (
            0,
            int(height * self.fallback_top_ratio),
            width,
            int(height * self.fallback_bottom_ratio),
        )

    def detect_from_paths(self, image_paths: Iterable[str], max_samples: int = 12) -> Optional[SubtitleRegion]:
        paths = [p for p in image_paths if p and os.path.exists(p)]
        if not paths:
            return None

        if len(paths) > max_samples:
            step = max(1, len(paths) // max_samples)
            paths = paths[::step][:max_samples]

        boxes = []
        weights = []
        last_size = None
        for path in paths:
            result = self.detect_frame(path)
            if result:
                boxes.append(result.box)
                weights.append(max(result.confidence, 0.01))
                last_size = (result.box[2], result.box[3])

        if not boxes:
            return None

        total_weight = sum(weights)
        top = int(sum(box[1] * weight for box, weight in zip(boxes, weights)) / total_weight)
        bottom = int(sum(box[3] * weight for box, weight in zip(boxes, weights)) / total_weight)
        width = boxes[0][2]
        height = last_size[1] if last_size else boxes[0][3]

        min_height = int(height * self.min_band_height_ratio)
        if bottom - top < min_height:
            center = (top + bottom) // 2
            top = max(0, center - min_height // 2)
            bottom = min(height, center + min_height // 2)

        top = max(0, top)
        bottom = min(height, bottom)
        confidence = min(1.0, sum(weights) / max(len(paths), 1))
        return SubtitleRegion(box=(0, top, width, bottom), confidence=confidence, source="local_cv")

    def detect_frame(self, image_path: str) -> Optional[SubtitleRegion]:
        try:
            from PIL import Image
            import numpy as np
            import cv2
        except ImportError as e:
            logger.debug("SubtitleRegionDetector dependencies unavailable: %s", e)
            return None

        try:
            image = Image.open(image_path).convert("RGB")
            width, height = image.size
            scan_top = int(height * self.scan_top_ratio)
            scan_bottom = int(height * self.scan_bottom_ratio)
            crop = image.crop((0, scan_top, width, scan_bottom))
            arr = np.array(crop)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

            # White or bright subtitle glyphs with dark outlines produce strong
            # edges and high brightness. Combining both suppresses background UI.
            edges = cv2.Canny(gray, 70, 170)
            bright = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)[1]
            mask = cv2.bitwise_and(edges, bright)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
            mask = cv2.dilate(mask, kernel, iterations=2)

            row_density = mask.mean(axis=1) / 255.0
            if row_density.max() < 0.015:
                return None

            threshold = max(0.018, row_density.max() * 0.34)
            active_rows = np.where(row_density >= threshold)[0]
            if active_rows.size == 0:
                return None

            # Prefer the lowest dense text component because subtitles usually
            # sit below charts/slides but above the absolute bottom edge.
            groups = self._group_consecutive(active_rows.tolist(), max_gap=5)
            groups = [g for g in groups if len(g) >= 4]
            if not groups:
                return None
            group = max(groups, key=lambda g: (g[-1], len(g)))

            pad = max(12, int(height * 0.035))
            top = max(scan_top, scan_top + group[0] - pad)
            bottom = min(height, scan_top + group[-1] + pad)

            min_height = int(height * self.min_band_height_ratio)
            if bottom - top < min_height:
                center = (top + bottom) // 2
                top = max(scan_top, center - min_height // 2)
                bottom = min(height, center + min_height // 2)

            confidence = float(min(1.0, row_density[group].mean() * 8.0))
            return SubtitleRegion(box=(0, int(top), width, int(bottom)), confidence=confidence, source="local_cv")
        except Exception as e:
            logger.debug("Subtitle region detection failed for %s: %s", image_path, e)
            return None

    def _group_consecutive(self, values, max_gap=1):
        if not values:
            return []
        groups = [[values[0]]]
        for value in values[1:]:
            if value - groups[-1][-1] <= max_gap:
                groups[-1].append(value)
            else:
                groups.append([value])
        return groups
