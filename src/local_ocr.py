"""
local_ocr.py - 本地 OCR 后端，优先基于 RapidOCR 实现离线中文字幕识别

特性：
  - 零 API 成本，完全离线运行
  - RapidOCR ONNX 中文场景文字识别优先，EasyOCR 兜底
  - 专为视频硬字幕优化的裁剪与图像预处理流水线
  - 支持简体中文 + 英文双语混合字幕
  - 单例模型缓存，首次初始化后后续调用零开销
  - 置信度评分，支持 hybrid 模式精度兜底
  - 自动检测 Apple Silicon MPS / CUDA GPU 加速
"""

import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _detect_best_device() -> bool:
    """
    自动检测最优计算设备。
    返回 True 表示可以使用 GPU 加速（CUDA 或 Apple MPS），否则返回 False (CPU)。

    注意：EasyOCR 的 gpu=True 在 CUDA 可用时调用 CUDA；在 Apple Silicon 上
    是否使用 MPS 取决于 EasyOCR/PyTorch 的运行时支持。
    """
    try:
        import torch
        if torch.cuda.is_available():
            logger.info("🚀 [LocalOCR] 检测到 CUDA GPU，将启用 GPU 加速！")
            return True
        if torch.backends.mps.is_available():
            logger.info("🚀 [LocalOCR] 检测到 Apple Silicon MPS，将启用 Metal GPU 加速！")
            return True
    except Exception:
        pass
    return False


class LocalOCRBackend:
    """
    本地字幕识别后端。

    使用方法：
        backend = LocalOCRBackend()
        text, confidence = backend.recognize(image_path)

    默认优先使用 RapidOCR，缺失时自动回退 EasyOCR。
    """

    _rapidocr_engine = None
    _easyocr_reader = None
    _easyocr_langs = None
    _easyocr_gpu = None

    def __init__(
        self,
        languages: list = None,
        gpu: bool = None,
        model_storage_directory: str = None,
        engine: str = "rapidocr",
    ):
        """
        初始化本地 OCR 后端。

        Args:
            languages: OCR 语言列表，默认 ['ch_sim', 'en']（简体中文 + 英文）
            gpu: 是否使用 GPU 加速。None (默认) 时自动检测 CUDA/MPS 并启用；
                 显式设为 False 可强制 CPU 模式。
            model_storage_directory: 模型缓存路径。None 时使用 EasyOCR 默认路径 (~/.EasyOCR/)
            engine: 本地 OCR 引擎，rapidocr / easyocr / auto。默认 rapidocr。
        """
        self.languages = languages or ["ch_sim", "en"]
        # None → 自动检测最优设备
        self.gpu = _detect_best_device() if gpu is None else gpu
        self.model_storage_directory = model_storage_directory
        self.engine = (engine or "rapidocr").lower().strip()
        self.active_engine = None
        self._ensure_engine()

    def _ensure_engine(self):
        if self.engine in ("rapidocr", "auto"):
            try:
                self._ensure_rapidocr()
                self.active_engine = "rapidocr"
                return
            except ImportError as e:
                if self.engine == "rapidocr":
                    logger.warning("[LocalOCR] RapidOCR 不可用，将回退 EasyOCR: %s", e)
                else:
                    logger.info("[LocalOCR] RapidOCR 不可用，尝试 EasyOCR: %s", e)

        self._ensure_easyocr()
        self.active_engine = "easyocr"

    def _ensure_rapidocr(self):
        if LocalOCRBackend._rapidocr_engine is not None:
            return
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise ImportError(
                "RapidOCR 未安装。请运行：pip3 install rapidocr-onnxruntime"
            ) from e

        logger.info("🔤 [LocalOCR] 正在初始化 RapidOCR ONNX Reader（中文场景文字，本地离线）。")
        LocalOCRBackend._rapidocr_engine = RapidOCR()
        logger.info("✅ [LocalOCR] RapidOCR 初始化完成，已就绪。")

    def _ensure_easyocr(self):
        """确保 EasyOCR Reader 已初始化（单例懒加载）。"""
        langs_key = tuple(sorted(self.languages))
        # 如果已经初始化且语言列表和 gpu 设置相同，直接复用
        if (LocalOCRBackend._easyocr_reader is not None
                and LocalOCRBackend._easyocr_langs == langs_key
                and LocalOCRBackend._easyocr_gpu == self.gpu):
            return

        try:
            import easyocr
        except ImportError:
            raise ImportError(
                "EasyOCR 未安装。请运行：pip3 install easyocr\n"
                "首次运行会自动下载中文模型（约 500MB）。"
            )

        device_str = "GPU/MPS" if self.gpu else "CPU"
        logger.info(
            f"🔤 [LocalOCR] 正在初始化 EasyOCR Reader（语言: {self.languages}，"
            f"计算设备: {device_str}）。首次运行会下载模型，请稍候..."
        )

        kwargs = {
            "lang_list": self.languages,
            "gpu": self.gpu,
            "verbose": False,
        }
        if self.model_storage_directory:
            kwargs["model_storage_directory"] = self.model_storage_directory

        LocalOCRBackend._easyocr_reader = easyocr.Reader(**kwargs)
        LocalOCRBackend._easyocr_langs = langs_key
        LocalOCRBackend._easyocr_gpu = self.gpu
        logger.info(f"✅ [LocalOCR] EasyOCR Reader 初始化完成，已就绪（{device_str}）。")

    def crop_subtitle_region(self, image_path: str, box: tuple = None) -> Optional["np.ndarray"]:
        try:
            from PIL import Image
            import numpy as np
        except ImportError as e:
            logger.warning(f"[LocalOCR] 裁剪依赖缺失: {e}")
            return None

        try:
            im = Image.open(image_path)
            width, height = im.size
            if not box:
                box = (0, int(height * 0.72), width, int(height * 0.96))
            return np.array(im.crop(box).convert("RGB"))
        except Exception as e:
            logger.warning(f"[LocalOCR] 字幕区域裁剪异常: {e}")
            return None

    def preprocess_subtitle_crop(self, image_path: str, box: tuple = None) -> Optional["np.ndarray"]:
        """
        对字幕区域进行专项图像预处理，显著提升细小文字的 OCR 精度：
          1. 裁剪字幕区（动态 ROI 或默认下方区域）
          2. 超分放大 ×2（Lanczos 插值）
          3. 灰度化
          4. OTSU 自适应阈值二值化（将字幕从复杂背景中剥离）
          5. 形态学膨胀（连接断裂笔画）

        Returns:
            处理后的 numpy 数组（OpenCV 格式），None 表示预处理失败。
        """
        try:
            from PIL import Image
            import numpy as np
            import cv2
        except ImportError as e:
            logger.warning(f"[LocalOCR] 预处理依赖缺失: {e}，将跳过预处理直接传原图。")
            return None

        try:
            im = Image.open(image_path)
            width, height = im.size

            # Step 1: 裁剪字幕区域（底部约 72% ~ 96%）。
            # B 站知识类视频常把大号硬字幕放在偏上的下三分之一位置，
            # 过窄的底部裁剪会切掉主体文字。
            if not box:
                top = int(height * 0.72)
                bottom = int(height * 0.96)
                box = (0, top, width, bottom)
            crop = im.crop(box)

            # Step 2: 超分辨率放大 ×2（使细小文字更大，EasyOCR 更易识别）
            new_w = crop.width * 2
            new_h = crop.height * 2
            crop_upscaled = crop.resize((new_w, new_h), Image.LANCZOS)

            # Step 3: 转为灰度图
            gray = np.array(crop_upscaled.convert("L"), dtype=np.uint8)

            # Step 4: OTSU 自适应二值化（消除背景渐变干扰，凸显字幕文字）
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # Step 5: 形态学膨胀（修复断字、笔画粘连）
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            processed = cv2.dilate(binary, kernel, iterations=1)

            return processed

        except Exception as e:
            logger.warning(f"[LocalOCR] 图像预处理异常: {e}，将使用原图进行识别。")
            return None

    def recognize(
        self,
        image_path: str,
        box: tuple = None,
        preprocess: bool = True,
    ) -> Tuple[str, float]:
        """
        对给定图片路径执行本地 OCR 识别。

        Args:
            image_path: 帧图片完整路径
            box: 字幕区域裁剪框 (left, top, right, bottom)，None 时自动使用默认下方区域
            preprocess: 是否执行图像预处理（推荐 True，精度更高）

        Returns:
            (识别出的字幕文本, 最高置信度分数)
            若无字幕，返回 ("None", 0.0)
        """
        if not os.path.exists(image_path):
            return "None", 0.0

        self._ensure_engine()

        if self.active_engine == "rapidocr":
            return self._recognize_rapidocr(image_path, box=box)

        return self._recognize_easyocr(image_path, box=box, preprocess=preprocess)

    def _recognize_rapidocr(self, image_path: str, box: tuple = None) -> Tuple[str, float]:
        img_arr = self.crop_subtitle_region(image_path, box=box)
        if img_arr is None:
            return "None", 0.0

        try:
            result, _ = LocalOCRBackend._rapidocr_engine(img_arr)
            if not result:
                return "None", 0.0

            valid_results = []
            for item in result:
                if len(item) < 3:
                    continue
                bbox, text, conf = item[0], item[1], float(item[2] or 0.0)
                if text and conf >= 0.35:
                    valid_results.append((bbox, text.strip(), conf))

            if not valid_results:
                return "None", 0.0

            valid_results = self._select_subtitle_line(valid_results)
            valid_results.sort(key=lambda r: (self._bbox_top(r[0]), self._bbox_left(r[0])))
            combined_text = " ".join(r[1] for r in valid_results if r[1])
            combined_text = self._postprocess(combined_text)
            if not combined_text:
                return "None", 0.0
            return combined_text, max(r[2] for r in valid_results)
        except Exception as e:
            logger.warning(f"[LocalOCR] RapidOCR 识别异常: {e}")
            return "None", 0.0

    def _recognize_easyocr(self, image_path: str, box: tuple = None, preprocess: bool = True) -> Tuple[str, float]:
        reader = LocalOCRBackend._easyocr_reader

        # 尝试预处理后的高精度识别路径
        processed_img = None
        if preprocess:
            processed_img = self.preprocess_subtitle_crop(image_path, box)

        try:
            if processed_img is not None:
                # 对预处理后的 numpy 数组直接进行 OCR
                results = reader.readtext(processed_img, detail=1, paragraph=False)
            else:
                # 降级：对原图字幕区域直接 OCR
                try:
                    from PIL import Image
                    import numpy as np
                    im = Image.open(image_path)
                    width, height = im.size
                    if not box:
                        box = (0, int(height * 0.72), width, int(height * 0.96))
                    crop = im.crop(box)
                    img_arr = np.array(crop)
                    results = reader.readtext(img_arr, detail=1, paragraph=False)
                except Exception:
                    results = reader.readtext(image_path, detail=1, paragraph=False)

            if not results:
                return "None", 0.0

            # 按置信度排序，过滤低置信度噪声
            valid_results = [(bbox, text, conf) for bbox, text, conf in results if conf > 0.3]
            if not valid_results:
                return "None", 0.0

            # 按水平位置排序（从左到右），合并同行文字
            valid_results.sort(key=lambda r: r[0][0][0])  # 按 bbox 左上角 x 坐标排序
            combined_text = " ".join(r[1].strip() for r in valid_results if r[1].strip())
            max_confidence = max(r[2] for r in valid_results)

            # 后处理：清理 OCR 噪声
            combined_text = self._postprocess(combined_text)

            if not combined_text:
                return "None", 0.0

            return combined_text, max_confidence

        except Exception as e:
            logger.warning(f"[LocalOCR] OCR 识别异常: {e}")
            return "None", 0.0

    def _bbox_left(self, bbox) -> float:
        try:
            return min(float(point[0]) for point in bbox)
        except Exception:
            return 0.0

    def _bbox_top(self, bbox) -> float:
        try:
            return min(float(point[1]) for point in bbox)
        except Exception:
            return 0.0

    def _bbox_bottom(self, bbox) -> float:
        try:
            return max(float(point[1]) for point in bbox)
        except Exception:
            return 0.0

    def _bbox_height(self, bbox) -> float:
        return max(0.0, self._bbox_bottom(bbox) - self._bbox_top(bbox))

    def _select_subtitle_line(self, results):
        """
        Keep the OCR row that looks like the actual subtitle.

        Dynamic ROI may still contain tiny page text above the caption. Subtitle
        glyphs are usually the tallest row in the ROI and sit near the bottom, so
        we choose the tallest lower text row and discard small surrounding UI text.
        """
        if len(results) <= 1:
            return results

        max_height = max(self._bbox_height(item[0]) for item in results)
        if max_height <= 0:
            return results

        candidates = [
            item for item in results
            if self._bbox_height(item[0]) >= max_height * 0.55
        ]
        if not candidates:
            return results

        anchor = max(
            candidates,
            key=lambda item: (
                self._bbox_height(item[0]),
                self._bbox_bottom(item[0]),
                item[2],
            )
        )
        anchor_center = (self._bbox_top(anchor[0]) + self._bbox_bottom(anchor[0])) / 2
        tolerance = max(12.0, self._bbox_height(anchor[0]) * 0.75)

        selected = []
        for item in candidates:
            center = (self._bbox_top(item[0]) + self._bbox_bottom(item[0])) / 2
            if abs(center - anchor_center) <= tolerance:
                selected.append(item)

        return selected or [anchor]

    def _postprocess(self, text: str) -> str:
        """
        后处理：清理 OCR 常见噪声。
          - 删除单个孤立符号（非字幕内容）
          - 合并异常空格
          - 删除纯标点行
        """
        if not text:
            return ""

        # 删除纯符号噪声（例如 '|', '-', '.', '。' 单独成字）
        text = re.sub(r"^\s*[^\w\u4e00-\u9fff]+\s*$", "", text)
        # 合并连续空格
        text = re.sub(r"\s{2,}", " ", text).strip()
        # 中文字幕通常不需要词间空格；保留英文/数字混合词内部空格由上游模型处理。
        if re.search(r"[\u4e00-\u9fff]", text):
            text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)

        return text

    def is_available(self) -> bool:
        """检查本地 OCR 是否可用。"""
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            try:
                import easyocr  # noqa: F401
                return True
            except ImportError:
                return False
