"""
Visual Page Inspection and OCR Self-Correction (`parsers/docling/inspector.py`).

Provides pixel-level visual analysis on rendered page pixmaps (RGB arrays) for:
1. Foreground ink color sampling (accent/colored headings).
2. Background fill color sampling (shaded section banners / callout boxes).
3. Glyph stroke-density (bold weight recovery when stripped by OCR).
4. Vertical dark-pixel projection (gutter verification & span de-bleeding).
5. Line-crop neural re-OCR via RapidOCR / EasyOCR.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except Exception:
    np = None
    _NUMPY_AVAILABLE = False

_RAPIDOCR_ENGINE: Any = False
_EASYOCR_ENGINE: Any = False
_OCR_ENGINE_LOCK = threading.RLock()


def get_rapidocr_line_engine() -> Any:
    """Lazy-initializes a CPU-only RapidOCR (PP-OCR ONNX) instance for selective line-crop re-OCR."""
    global _RAPIDOCR_ENGINE
    with _OCR_ENGINE_LOCK:
        if _RAPIDOCR_ENGINE is not False:
            return _RAPIDOCR_ENGINE
        for mod_name in ("rapidocr_onnxruntime", "rapidocr"):
            try:
                mod = __import__(mod_name, fromlist=["RapidOCR"])
                cls = getattr(mod, "RapidOCR", None)
                if cls is not None:
                    _RAPIDOCR_ENGINE = cls()
                    logger.info("Initialized RapidOCR (PP-OCR ONNX) line engine for Docling.")
                    return _RAPIDOCR_ENGINE
            except Exception:
                continue
        _RAPIDOCR_ENGINE = None
        return None


def get_easyocr_line_engine() -> Any:
    """Lazy-initializes a CPU-only EasyOCR Reader instance when user explicitly selects EasyOCR."""
    global _EASYOCR_ENGINE
    with _OCR_ENGINE_LOCK:
        if _EASYOCR_ENGINE is not False:
            return _EASYOCR_ENGINE
        try:
            import easyocr
            _EASYOCR_ENGINE = easyocr.Reader(["en"], gpu=False, verbose=False)
            logger.info("Initialized EasyOCR (CPU) line engine for Docling.")
            return _EASYOCR_ENGINE
        except Exception as exc:
            logger.debug("EasyOCR not installed or failed to initialize: %s", exc)
            _EASYOCR_ENGINE = None
            return None


class VisualPageInspector:
    """
    Lightweight pixel-level visual analyzer operating on a rendered page pixmap (or RGB array).
    Used to recover visual signals lost by OCR text layers or corrupted PDF streams.
    """

    def __init__(
        self,
        pixmap: Any,
        page_width: float,
        page_height: float,
        line_ocr_callback: Optional[Any] = None,
        ocr_engine: str = "auto",
    ):
        self.page_width = max(1.0, float(page_width))
        self.page_height = max(1.0, float(page_height))
        self.line_ocr_callback = line_ocr_callback
        self.ocr_engine = str(ocr_engine or "auto").strip().lower()
        self.arr: Optional[Any] = None
        self.img_w: int = 0
        self.img_h: int = 0
        self.scale_x: float = 1.0
        self.scale_y: float = 1.0
        self.page_bg_rgb: Tuple[int, int, int] = (255, 255, 255)

        if not _NUMPY_AVAILABLE or pixmap is None:
            return

        try:
            if hasattr(pixmap, "samples") and hasattr(pixmap, "width") and hasattr(pixmap, "height"):
                n_chan = int(getattr(pixmap, "n", 3) or 3)
                raw = np.frombuffer(pixmap.samples, dtype=np.uint8)
                h_px, w_px = int(pixmap.height), int(pixmap.width)
                if raw.size == h_px * w_px * n_chan and h_px > 10 and w_px > 10:
                    reshaped = raw.reshape((h_px, w_px, n_chan))
                    if n_chan >= 3:
                        self.arr = reshaped[:, :, :3]
                    elif n_chan == 1:
                        self.arr = np.repeat(reshaped, 3, axis=2)
            elif isinstance(pixmap, np.ndarray) and pixmap.ndim == 3 and pixmap.shape[2] >= 3:
                self.arr = pixmap[:, :, :3].astype(np.uint8, copy=False)

            if self.arr is not None:
                self.img_h, self.img_w = int(self.arr.shape[0]), int(self.arr.shape[1])
                self.scale_x = self.img_w / self.page_width
                self.scale_y = self.img_h / self.page_height
                sample_step = max(1, min(self.img_h, self.img_w) // 50)
                sub = self.arr[::sample_step, ::sample_step, :]
                p90 = np.percentile(sub.reshape(-1, 3), 90, axis=0)
                self.page_bg_rgb = (int(p90[0]), int(p90[1]), int(p90[2]))
        except Exception as exc:
            logger.debug("VisualPageInspector init error: %s", exc)
            self.arr = None

    @property
    def available(self) -> bool:
        return self.arr is not None and self.img_w > 10 and self.img_h > 10

    def _pdf_bbox_to_px(
        self, bbox: Tuple[float, float, float, float], pad_pt: float = 0.0
    ) -> Tuple[int, int, int, int]:
        x0 = max(0, int(math.floor((bbox[0] - pad_pt) * self.scale_x)))
        y0 = max(0, int(math.floor((bbox[1] - pad_pt) * self.scale_y)))
        x1 = min(self.img_w, int(math.ceil((bbox[2] + pad_pt) * self.scale_x)))
        y1 = min(self.img_h, int(math.ceil((bbox[3] + pad_pt) * self.scale_y)))
        return x0, y0, x1, y1

    def sample_span_visual_metrics(
        self, bbox: Tuple[float, float, float, float]
    ) -> Optional[Dict[str, Any]]:
        """
        Samples the pixel region inside `bbox` to compute foreground ink color,
        local background color, shaded background flag, and ink density.
        """
        if not self.available:
            return None

        px0, py0, px1, py1 = self._pdf_bbox_to_px(bbox, pad_pt=0.5)
        if px1 - px0 < 3 or py1 - py0 < 3:
            return None

        patch = self.arr[py0:py1, px0:px1, :]
        flat = patch.reshape(-1, 3).astype(np.float32)
        if flat.shape[0] < 9:
            return None

        lum = 0.299 * flat[:, 0] + 0.587 * flat[:, 1] + 0.114 * flat[:, 2]

        bg_thresh = np.percentile(lum, 75)
        bg_mask = lum >= bg_thresh
        if np.any(bg_mask):
            bg_med = np.median(flat[bg_mask], axis=0)
        else:
            bg_med = np.array(self.page_bg_rgb, dtype=np.float32)

        bg_r, bg_g, bg_b = int(round(bg_med[0])), int(round(bg_med[1])), int(round(bg_med[2]))
        bg_lum = 0.299 * bg_r + 0.587 * bg_g + 0.114 * bg_b

        paper_r, paper_g, paper_b = self.page_bg_rgb
        bg_dist = math.sqrt((bg_r - paper_r) ** 2 + (bg_g - paper_g) ** 2 + (bg_b - paper_b) ** 2)
        bg_sat = max(bg_r, bg_g, bg_b) - min(bg_r, bg_g, bg_b)
        is_shaded_bg = bool((bg_dist >= 16.0 and bg_lum < 246.0) or (bg_sat >= 14 and bg_lum < 250.0))

        color_diff = np.sqrt(np.sum((flat - bg_med) ** 2, axis=1))
        ink_mask = (lum < bg_lum - 28.0) | (color_diff > 42.0)
        ink_count = int(np.sum(ink_mask))
        total_px = int(flat.shape[0])

        if ink_count >= 3:
            ink_pixels = flat[ink_mask]
            ink_lums = lum[ink_mask]
            cutoff = np.percentile(ink_lums, 60)
            core_ink = ink_pixels[ink_lums <= cutoff]
            if core_ink.shape[0] == 0:
                core_ink = ink_pixels
            fg_med = np.median(core_ink, axis=0)
            fg_r, fg_g, fg_b = int(round(fg_med[0])), int(round(fg_med[1])), int(round(fg_med[2]))
        else:
            fg_r, fg_g, fg_b = 0, 0, 0

        fg_color_int = ((fg_r & 255) << 16) | ((fg_g & 255) << 8) | (fg_b & 255)
        ink_density = float(ink_count) / max(1, total_px)

        return {
            "fg_color_int": fg_color_int,
            "bg_rgb": (bg_r, bg_g, bg_b),
            "is_shaded_bg": is_shaded_bg,
            "ink_density": ink_density,
        }

    def find_whitespace_split_x(
        self, bbox: Tuple[float, float, float, float], min_gap_pt: float = 8.0
    ) -> Optional[Tuple[float, float]]:
        """
        Inspects a wide span's pixel strip for an internal vertical whitespace valley and returns
        the PDF `(gap_start_x, gap_end_x)` coordinates.
        """
        if not self.available:
            return None

        px0, py0, px1, py1 = self._pdf_bbox_to_px(bbox, pad_pt=0.0)
        w_px = px1 - px0
        h_px = py1 - py0
        if w_px < 40 or h_px < 4:
            return None

        patch = self.arr[py0:py1, px0:px1, :].astype(np.float32)
        lum = 0.299 * patch[:, :, 0] + 0.587 * patch[:, :, 1] + 0.114 * patch[:, :, 2]
        bg_val = np.percentile(lum, 80)
        ink_col_counts = np.sum(lum < (bg_val - 28.0), axis=0)

        margin_px = int(0.15 * w_px)
        min_gap_px = max(3, int(round(min_gap_pt * self.scale_x)))
        best_run = (0, 0, 0)
        cur_start = None

        for x_i in range(margin_px, w_px - margin_px):
            if ink_col_counts[x_i] == 0:
                if cur_start is None:
                    cur_start = x_i
            else:
                if cur_start is not None:
                    run_len = x_i - cur_start
                    if run_len > best_run[0]:
                        best_run = (run_len, cur_start, x_i)
                    cur_start = None
        if cur_start is not None:
            run_len = (w_px - margin_px) - cur_start
            if run_len > best_run[0]:
                best_run = (run_len, cur_start, w_px - margin_px)

        if best_run[0] >= min_gap_px:
            if np.sum(ink_col_counts[:best_run[1]]) >= 5 and np.sum(ink_col_counts[best_run[2]:]) >= 5:
                g0_pt = float((px0 + best_run[1]) / self.scale_x)
                g1_pt = float((px0 + best_run[2]) / self.scale_x)
                return (g0_pt, g1_pt)
        return None

    def verify_vertical_gutter(
        self, g0_pt: float, g1_pt: float, y0_pt: float, y1_pt: float
    ) -> bool:
        """
        Verifies via pixel projection that a candidate vertical column gutter `[g0_pt, g1_pt]`
        in zone `[y0_pt, y1_pt]` is genuinely free of text ink.
        """
        if not self.available:
            return True

        px0, py0, px1, py1 = self._pdf_bbox_to_px((g0_pt, y0_pt, g1_pt, y1_pt))
        if px1 - px0 < 2 or py1 - py0 < 6:
            return True

        patch = self.arr[py0:py1, px0:px1, :].astype(np.float32)
        lum = 0.299 * patch[:, :, 0] + 0.587 * patch[:, :, 1] + 0.114 * patch[:, :, 2]
        ink_mask = lum < 200.0
        row_ink_ratio = np.mean(ink_mask, axis=1)
        bridging_rows = np.mean(row_ink_ratio > 0.65)
        return bool(bridging_rows < 0.12)

    def recognize_line_crop(self, bbox: Tuple[float, float, float, float]) -> Optional[str]:
        """
        Crops a single span/line `[x0, y0, x1, y1]` from the rendered page image
        and runs lightweight line recognition.
        """
        if not self.available:
            return None

        px0, py0, px1, py1 = self._pdf_bbox_to_px(bbox, pad_pt=1.5)
        if px1 - px0 < 6 or py1 - py0 < 4:
            return None

        crop = self.arr[py0:py1, px0:px1, :]

        if self.ocr_engine in ("indra", "indra_math"):
            import parsers.indra as indra_p
            res = indra_p.ocr_image_region(crop, math=(self.ocr_engine == "indra_math"))
            txt = res.get("text") or res.get("markdown") or ""
            return txt.strip() if txt.strip() else None

        if callable(self.line_ocr_callback):
            try:
                res_txt = self.line_ocr_callback(crop, bbox)
                if isinstance(res_txt, str) and res_txt.strip():
                    return res_txt.strip()
            except Exception as exc:
                logger.debug("Custom line_ocr_callback error: %s", exc)

        if self.ocr_engine in ("easyocr", "easy_ocr", "easy"):
            easy_reader = get_easyocr_line_engine()
            if easy_reader is not None:
                try:
                    easy_out = easy_reader.readtext(crop, detail=0, paragraph=True)
                    if easy_out:
                        parts = [str(x).strip() for x in easy_out if str(x).strip()]
                        if parts:
                            return " ".join(parts)
                except Exception as exc:
                    logger.debug("EasyOCR line crop error: %s", exc)

        engine = get_rapidocr_line_engine()
        if engine is not None:
            try:
                ocr_out, _ = engine(crop)
                if ocr_out:
                    parts = [str(item[1]).strip() for item in ocr_out if len(item) >= 2 and str(item[1]).strip()]
                    if parts:
                        return " ".join(parts)
            except Exception as exc:
                logger.debug("RapidOCR line crop error: %s", exc)

        return None


# Backwards compatibility alias
_VisualPageInspector = VisualPageInspector
_get_rapidocr_line_engine = get_rapidocr_line_engine
_get_easyocr_line_engine = get_easyocr_line_engine
