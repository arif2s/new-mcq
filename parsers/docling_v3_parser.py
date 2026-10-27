"""
Adaptive Multi-Column Medical Layout Engine for PyMuPDF (fitz) and Docling v3 Hybrid Recovery.
Implements per-page typography & modal color profiling, pixel-level visual inspection & OCR self-correction
(foreground ink color recovery, shaded banner/callout box detection on scanned pages, stroke-density bold
recovery, dark-pixel projection bbox de-bleeding & column gutter verification, selective line-crop re-OCR
via RapidOCR/PP-OCR ONNX or EasyOCR), vector ruling barriers, vector chart obstacle isolation, booktabs
horizontal-rule table reconstruction, raster image obstacle detection, shaded callout isolation, multi-line
& under-image caption tethering, local zone-based XY-cut column gutter detection, image-wrapped & cross-column
paragraph continuation, multi-tier hierarchy detection (H1-H5 + colored headings + small-caps numbered
run-in headings), inline bold/italic emphasis preservation, multi-line and colon-aligned bullet list
reconstruction, PDF page pixmap rendering (base64 PNG), and Docling v3 (Heron RT-DETRv2 + explicit
RapidOCR/PaddleOCR or EasyOCR selection with force_full_page_ocr) memory-safe single-page extraction.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import io
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

try:
    from config import HF_CACHE_DIR
    if HF_CACHE_DIR:
        os.makedirs(HF_CACHE_DIR, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
except ImportError:
    HF_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "huggingface"
    os.makedirs(HF_CACHE_DIR, exist_ok=True)

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except Exception:
    np = None
    _NUMPY_AVAILABLE = False

# Docling imports (lazy / optional to prevent startup failure if not installed)
_DOCLING_AVAILABLE = False
DocumentStream = None
RapidOcrOptions = None
EasyOcrOptions = None
try:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    _DOCLING_AVAILABLE = True
    try:
        from docling.datamodel.pipeline_options import RapidOcrOptions
    except Exception:
        RapidOcrOptions = None
    try:
        from docling.datamodel.pipeline_options import EasyOcrOptions
    except Exception:
        EasyOcrOptions = None
except Exception:
    _DOCLING_AVAILABLE = False

# Lazy singletons for selective line-crop re-OCR (RapidOCR / PP-OCR ONNX and EasyOCR CPU)
_RAPIDOCR_ENGINE: Any = False
_EASYOCR_ENGINE: Any = False
_OCR_ENGINE_LOCK = threading.RLock()

logger = logging.getLogger(__name__)

_FITZ_LOCK = threading.RLock()
_DOCLING_CONVERTERS: Dict[Any, Any] = {}

DOCLING_REQUIRED_MODELS = (
    "docling-project/docling-layout-heron",
    "docling-project/docling-layout-heron-onnx",
    "docling-project/docling-models",
)


def _get_rapidocr_line_engine() -> Any:
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
                    logger.info("Initialized RapidOCR (PP-OCR ONNX) line engine for Docling v3.")
                    return _RAPIDOCR_ENGINE
            except Exception:
                continue
        _RAPIDOCR_ENGINE = None
        return None


def _get_easyocr_line_engine() -> Any:
    """Lazy-initializes a CPU-only EasyOCR Reader instance when user explicitly selects EasyOCR."""
    global _EASYOCR_ENGINE
    with _OCR_ENGINE_LOCK:
        if _EASYOCR_ENGINE is not False:
            return _EASYOCR_ENGINE
        try:
            import easyocr
            _EASYOCR_ENGINE = easyocr.Reader(["en"], gpu=False, verbose=False)
            logger.info("Initialized EasyOCR (CPU) line engine for Docling v3.")
            return _EASYOCR_ENGINE
        except Exception as exc:
            logger.debug("EasyOCR not installed or failed to initialize: %s", exc)
            _EASYOCR_ENGINE = None
            return None


def is_docling_models_downloaded() -> bool:
    """Checks whether all required Docling layout and table model weights exist in HF cache."""
    cache_dirs = [HF_CACHE_DIR, Path(os.path.expanduser("~/.cache/huggingface/hub"))]
    for repo_id in DOCLING_REQUIRED_MODELS:
        folder_name = f"models--{repo_id.replace('/', '--')}"
        found = False
        for c_dir in cache_dirs:
            target = Path(c_dir) / folder_name
            if target.exists() and any(target.glob("snapshots/*")):
                found = True
                break
        if not found:
            return False
    return True


def download_docling_models() -> Dict[str, Any]:
    """Pre-downloads and caches all required Docling Heron & table models for offline PDF parsing."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        return {
            "status": "error",
            "message": f"huggingface_hub not installed: {exc}",
            "downloaded": False,
        }

    target_dir = Path(HF_CACHE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths = []

    try:
        # Save previous offline mode setting during active download
        prev_offline = os.environ.get("HF_HUB_OFFLINE")
        if "HF_HUB_OFFLINE" in os.environ:
            del os.environ["HF_HUB_OFFLINE"]

        for repo_id in DOCLING_REQUIRED_MODELS:
            logger.info("Downloading Docling model %s into %s...", repo_id, target_dir)
            p = snapshot_download(repo_id=repo_id, cache_dir=str(target_dir))
            downloaded_paths.append(p)

        if prev_offline is not None:
            os.environ["HF_HUB_OFFLINE"] = prev_offline

        return {
            "status": "success",
            "message": "All Docling layout models pre-downloaded for offline processing.",
            "downloaded_paths": downloaded_paths,
            "downloaded": True,
        }
    except Exception as exc:
        logger.error("Failed to pre-download Docling layout models: %s", exc)
        return {
            "status": "error",
            "message": str(exc),
            "downloaded": False,
        }


_LIGATURE_MAP = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
    "\u00a0": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2009": " ",
    "\u200b": "",
    "\u200e": "",
    "\u200f": "",
    "\ufeff": "",
    "\uf0ae": "→",
}

_HYPHENATED_MEDICAL_PREFIXES = (
    "anti", "non", "auto", "co", "beta", "alpha", "self", "cross", "two", "three", "four", "single", "multi"
)

_BOUND_MEDICAL_PREFIXES = (
    "pre", "post", "sub", "intra", "extra", "hyper", "hypo", "re", "micro", "macro"
)

_MEDICAL_PREFIXES = _HYPHENATED_MEDICAL_PREFIXES + _BOUND_MEDICAL_PREFIXES

# Lexical OCR anomaly patterns that indicate corrupted glyph recognition on a span
_OCR_GARBLED_TOKEN_RE = re.compile(
    r"(?:[\ufffd\u25a1\u25a0]|[@#$%\^&\*~`|\\]{2,}|"
    r"\b[A-Za-z]+[0-9]+[A-Za-z0-9]*[A-Za-z]\b|"
    r"\b[0-9]+[A-Za-z]{3,}\b|"
    r"[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]{6,})"
)


def _is_ocr_garbled_text(text: str) -> bool:
    """
    Fast lexical & character-bigram anomaly detector for identifying spans with corrupted OCR
    (e.g. 'Megal0c0rnea', 'pneu@#$mococcus', replacement characters, or 6+ consonant clusters).
    """
    if not text or len(text) < 3:
        return False
    # Ignore valid medical abbreviations with numbers like B12, C3a, T4, IgG4, H1N1, 500, 1000
    cleaned = re.sub(
        r"\b(?:Ig[A-Z]\d|B12|T[1-4]|C[1-9][a-z]?|H\dN\d|[A-Z]{1,3}\d{1,3}|[0-9]+(?:\.[0-9]+)?(?:dB|Hz|mm|cm|mg|ml|mL|kg|yr|yrs)?)\b",
        "",
        text,
    )
    return bool(_OCR_GARBLED_TOKEN_RE.search(cleaned))


def _repair_ocr_token_heuristics(text: str) -> str:
    """
    Deterministic character-level OCR confusable repair for medical tokens when neural re-OCR
    is not installed or leaves digit-letter confusables (e.g. '0' -> 'o', '1' -> 'l' or 'I' inside alphabetic words).
    """
    if not text:
        return text

    def _fix_word(match: re.Match) -> str:
        w = match.group(0)
        # Preserve valid short biomedical codes (e.g. B12, IgG4, T3, C5a)
        if re.match(r"^(?:Ig[A-Z]\d|[A-Z]{1,2}\d{1,2}[a-z]?)$", w):
            return w
        alpha_count = sum(1 for c in w if c.isalpha())
        digit_count = sum(1 for c in w if c.isdigit())
        if alpha_count < 3 or digit_count == 0 or digit_count > alpha_count:
            return w
        chars = list(w)
        is_upper_word = sum(1 for c in chars if c.isupper()) >= max(2, alpha_count - 1)
        for idx, ch in enumerate(chars):
            if ch == "0":
                chars[idx] = "O" if is_upper_word else "o"
            elif ch == "1":
                if idx == 0 or is_upper_word:
                    chars[idx] = "I"
                else:
                    chars[idx] = "l"
            elif ch == "5" and alpha_count >= 4:
                chars[idx] = "S" if (idx == 0 or is_upper_word) else "s"
        return "".join(chars)

    return re.sub(r"\b[A-Za-z0-9]{4,}\b", _fix_word, text)


def clean_pdf_text(text: str) -> str:
    """Sanitizes raw PDF text for Tantivy BM25 indexing and LanceDB FTS."""
    if not text:
        return ""

    t = text.replace("\x00", "").replace("\x0c", "\n")
    for lig, rep in _LIGATURE_MAP.items():
        if lig in t:
            t = t.replace(lig, rep)

    t = t.replace("\r\n", "\n").replace("\r", "\n")

    # Join em-dashes and en-dashes across line breaks without introducing spaces
    t = re.sub(r"([—–])[ \t]*\n[ \t]*", r"\1", t)

    # Convert trailing soft hyphens (\u00ad) before line breaks into standard hyphens prior to de-hyphenation
    t = re.sub(r"\u00ad+(\*{1,2})?[ \t]*\n", r"-\1\n", t)

    def _should_keep_hyphen(prefix: str, suffix: str) -> bool:
        p_low = prefix.lower()
        s_low = suffix.lower()
        if p_low in _HYPHENATED_MEDICAL_PREFIXES:
            return True
        if p_low in _BOUND_MEDICAL_PREFIXES and s_low and p_low[-1] == s_low[0] and s_low[0] in "aeiou":
            return True
        if prefix == "Mora" and s_low.startswith("xella"):
            return True
        return False

    # De-hyphenation across optional inline markdown emphasis (* or **) and line breaks
    def _dehyphen_md_match(match: re.Match) -> str:
        prefix = match.group(1)
        close_em = match.group(2) or ""
        open_em = match.group(3) or ""
        suffix = match.group(4)
        keep_h = _should_keep_hyphen(prefix, suffix)
        sep = "-" if keep_h else ""
        if close_em and open_em and close_em == open_em:
            return f"{prefix}{sep}{suffix}"
        return f"{prefix}{close_em}{sep}{open_em}{suffix}"

    t = re.sub(
        r"([A-Za-z]{2,})[-­](\*{1,2})?[ \t]*\n[ \t]*(\*{1,2})?([a-z]{2,})",
        _dehyphen_md_match,
        t,
    )

    # Also handle inline soft-hyphenated words within the same line
    def _dehyphen_inline_soft(match: re.Match) -> str:
        prefix = match.group(1)
        close_em = match.group(2) or ""
        open_em = match.group(3) or ""
        suffix = match.group(4)
        keep_h = _should_keep_hyphen(prefix, suffix)
        sep = "-" if keep_h else ""
        if close_em and open_em and close_em == open_em:
            return f"{prefix}{sep}{suffix}"
        return f"{prefix}{close_em}{sep}{open_em}{suffix}"

    t = re.sub(
        r"([A-Za-z]{2,})\u00ad+(\*{1,2})?[ \t]*(\*{1,2})?([a-z]{2,})",
        _dehyphen_inline_soft,
        t,
    )
    t = t.replace("\u00ad", "")

    # Merge adjacent identical markdown emphasis runs across whitespace/newlines (e.g. '*Haemophilus* *aegyptius*')
    t = re.sub(r"\*\*([ \t\n]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([ \t\n]+)\*(?!\*)", r"\1", t)

    # Clean up inner parenthesis spacing and comma spacing artifacts from PDF spans
    t = re.sub(r"\([ \t]+(\*{0,2}[A-Za-z0-9])", r"(\1", t)
    t = re.sub(r"([A-Za-z0-9]\*{0,2})[ \t]+\)", r"\1)", t)
    t = re.sub(r"([A-Za-z]\*{0,2})[ \t]+,[ \t]*(\*{0,2}[A-Za-z])", r"\1, \2", t)

    t = re.sub(r"\b(CHAPTER|PART|SECTION|UNIT|MODULE)\s*([0-9]+|[IVXLCDM]+)\b", r"\1 \2", t, flags=re.IGNORECASE)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _rect_area(rect: Tuple[float, float, float, float]) -> float:
    w = max(0.0, rect[2] - rect[0])
    h = max(0.0, rect[3] - rect[1])
    return w * h


def _rects_overlap(r1: Tuple[float, float, float, float], r2: Tuple[float, float, float, float]) -> bool:
    return not (r1[2] <= r2[0] or r1[0] >= r2[2] or r1[3] <= r2[1] or r1[1] >= r2[3])


def _point_in_rect(x: float, y: float, rect: Tuple[float, float, float, float]) -> bool:
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


# Regex for finite clause verbs and trailing function words that never appear in standalone noun-phrase headings
_PROSE_FINITE_VERBS_RE = re.compile(
    r"\b(is|are|was|were|can|cannot|will|may|might|should|would|could|has|have|had|do|does|did|must|shall)\b",
    re.IGNORECASE,
)

_TRAILING_FUNCTION_WORD_RE = re.compile(
    r"\b(the|a|an|of|in|to|for|with|on|at|from|by|as|into|and|or|but|nor|yet|so|"
    r"that|which|who|whom|whose|it|its|this|these|those|they|their|them|he|she|we|"
    r"is|are|was|were|be|been|being|can|will|may|should|would|could|has|have|had|not|no)$",
    re.IGNORECASE,
)


class _VisualPageInspector:
    """
    Lightweight pixel-level visual analyzer operating on a rendered page pixmap (or RGB array).
    Used to recover visual signals lost by OCR text layers (e.g. Tesseract/EasyOCR/PaddleOCR invisible fonts)
    or corrupted PDF streams:
      1. Foreground ink RGB color sampling -> recovers colored headings (`color` / `is_accent_color`).
      2. Background fill RGB sampling -> detects shaded section banners and clinical callout boxes baked into images.
      3. Glyph stroke-density (ink fill ratio) profiling -> recovers `is_bold` when OCR strips font weights.
      4. Dark-pixel vertical projection profiling -> splits merged cross-gutter OCR spans and verifies column gutters.
      5. Selective line-crop re-OCR via RapidOCR (PaddleOCR ONNX) or EasyOCR depending on `ocr_engine`.
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
                # Estimate modal paper background from border/percentile pixels
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
        Samples the pixel region inside `bbox` to compute:
        - `fg_color_int`: packed 24-bit RGB integer of the foreground glyph ink
        - `bg_rgb`: (R, G, B) tuple of the local background behind the text
        - `is_shaded_bg`: True if local background differs noticeably from white page paper
        - `ink_density`: fraction of foreground ink pixels in the tight glyph core (stroke weight proxy)
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

        # Luminance Y = 0.299 R + 0.587 G + 0.114 B
        lum = 0.299 * flat[:, 0] + 0.587 * flat[:, 1] + 0.114 * flat[:, 2]

        # Background is represented by the brighter 75th percentile of the box
        bg_thresh = np.percentile(lum, 75)
        bg_mask = lum >= bg_thresh
        if np.any(bg_mask):
            bg_med = np.median(flat[bg_mask], axis=0)
        else:
            bg_med = np.array(self.page_bg_rgb, dtype=np.float32)

        bg_r, bg_g, bg_b = int(round(bg_med[0])), int(round(bg_med[1])), int(round(bg_med[2]))
        bg_lum = 0.299 * bg_r + 0.587 * bg_g + 0.114 * bg_b

        # Compare local background to page paper background to detect shaded banners / callouts
        paper_r, paper_g, paper_b = self.page_bg_rgb
        bg_dist = math.sqrt((bg_r - paper_r) ** 2 + (bg_g - paper_g) ** 2 + (bg_b - paper_b) ** 2)
        bg_sat = max(bg_r, bg_g, bg_b) - min(bg_r, bg_g, bg_b)
        is_shaded_bg = bool((bg_dist >= 16.0 and bg_lum < 246.0) or (bg_sat >= 14 and bg_lum < 250.0))

        # Foreground ink pixels: significantly darker (or higher chromatic distance) than local background
        color_diff = np.sqrt(np.sum((flat - bg_med) ** 2, axis=1))
        ink_mask = (lum < bg_lum - 28.0) | (color_diff > 42.0)
        ink_count = int(np.sum(ink_mask))
        total_px = int(flat.shape[0])

        if ink_count >= 3:
            # Use the darkest 60% of ink pixels to avoid anti-aliased edge washout when measuring ink color
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
        Inspects a wide span's pixel strip for an internal vertical whitespace valley (e.g. when
        a noisy OCR bounding box accidentally bridges across a two-column gutter) and returns
        the PDF `(gap_start_x, gap_end_x)` coordinates of the ink-free valley if found.
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

        # Search central 70% of the span for a completely ink-free vertical gap >= min_gap_pt
        margin_px = int(0.15 * w_px)
        min_gap_px = max(3, int(round(min_gap_pt * self.scale_x)))
        best_run = (0, 0, 0)  # (length, start_idx, end_idx)
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
            # Ensure both left and right sides actually have ink
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
        in zone `[y0_pt, y1_pt]` is genuinely free of text ink (except thin vertical ruling lines).
        """
        if not self.available:
            return True

        px0, py0, px1, py1 = self._pdf_bbox_to_px((g0_pt, y0_pt, g1_pt, y1_pt))
        if px1 - px0 < 2 or py1 - py0 < 6:
            return True

        patch = self.arr[py0:py1, px0:px1, :].astype(np.float32)
        lum = 0.299 * patch[:, :, 0] + 0.587 * patch[:, :, 1] + 0.114 * patch[:, :, 2]
        ink_mask = lum < 200.0
        # A vertical ruling line occupies at most 1-3 pixel columns; horizontal text lines span across the gutter width
        row_ink_ratio = np.mean(ink_mask, axis=1)
        bridging_rows = np.mean(row_ink_ratio > 0.65)
        return bool(bridging_rows < 0.12)

    def recognize_line_crop(self, bbox: Tuple[float, float, float, float]) -> Optional[str]:
        """
        Crops a single suspicious span/line `[x0, y0, x1, y1]` from the rendered page image
        and runs lightweight line recognition (via `line_ocr_callback`, `EasyOCR`, or `RapidOCR`).
        """
        if not self.available:
            return None

        px0, py0, px1, py1 = self._pdf_bbox_to_px(bbox, pad_pt=1.5)
        if px1 - px0 < 6 or py1 - py0 < 4:
            return None

        crop = self.arr[py0:py1, px0:px1, :]

        if callable(self.line_ocr_callback):
            try:
                res_txt = self.line_ocr_callback(crop, bbox)
                if isinstance(res_txt, str) and res_txt.strip():
                    return res_txt.strip()
            except Exception as exc:
                logger.debug("Custom line_ocr_callback error: %s", exc)

        if self.ocr_engine in ("easyocr", "easy_ocr", "easy"):
            easy_reader = _get_easyocr_line_engine()
            if easy_reader is not None:
                try:
                    easy_out = easy_reader.readtext(crop, detail=0, paragraph=True)
                    if easy_out:
                        parts = [str(x).strip() for x in easy_out if str(x).strip()]
                        if parts:
                            return " ".join(parts)
                except Exception as exc:
                    logger.debug("EasyOCR line crop error: %s", exc)

        engine = _get_rapidocr_line_engine()
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


class AdaptivePdfLayoutEngine:
    """
    Deterministic Adaptive Multi-Column Medical Layout Engine with Visual Context (V3):
    1. Per-page typography baseline (mu_w, mu_h, mu_size, body_color) and heading size tiers
    2. Pixel-level visual inspection & OCR self-correction (ink color recovery, shaded banner/box detection,
       stroke-density bold recovery, projection-profile bbox de-bleeding, and selective line-crop re-OCR)
    3. Vector graphic rulings (vertical barriers, horizontal zone splitters, header/footer rules, banner frames)
       with vector chart grid isolation
    4. Raster & vector figure obstacle detection for wrapped-text continuity
    5. Structural element classification (native & booktabs horizontal-rule tables, callout boxes, figure captions)
    6. Local zone-based XY-cut column gutter solver with cross-column image-wrap protection
    7. Multi-tier hierarchy depth recognition (H1-H5 + accent-colored headings + small-caps numbered run-in headings)
    8. Inline bold/italic emphasis preservation and multi-line / colon-aligned bullet list reconstruction
    """

    def __init__(
        self,
        page: Any,
        default_title: str = "",
        initial_heading_stack: Optional[Dict[int, str]] = None,
        page_toc: Optional[List[Tuple[int, str]]] = None,
        pixmap: Optional[Any] = None,
        line_ocr_callback: Optional[Any] = None,
        ocr_engine: str = "auto",
        **kwargs: Any,
    ):
        self.page = page
        self.default_title = str(default_title or "").strip() or "Untitled"
        self.initial_heading_stack: Dict[int, str] = dict(initial_heading_stack or {})
        self.page_toc: List[Tuple[int, str]] = list(page_toc or [])
        self.page_rect = (
            (0.0, 0.0, float(page.rect.width), float(page.rect.height))
            if hasattr(page, "rect") and page.rect
            else (0.0, 0.0, 612.0, 792.0)
        )
        self.page_width = max(1.0, self.page_rect[2] - self.page_rect[0])
        self.page_height = max(1.0, self.page_rect[3] - self.page_rect[1])

        raw_pixmap = pixmap if pixmap is not None else getattr(page, "_visual_pixmap", None)
        ocr_cb = line_ocr_callback if line_ocr_callback is not None else getattr(page, "line_ocr_callback", None)
        raw_eng = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocr_engine") or ocr_engine or "auto")
        self.ocr_engine = str(raw_eng).strip().lower()
        self.visual_inspector = _VisualPageInspector(
            raw_pixmap,
            self.page_width,
            self.page_height,
            line_ocr_callback=ocr_cb,
            ocr_engine=self.ocr_engine,
        )

        # Typography & Color Profile Defaults
        self.mu_size: float = 10.0
        self.mu_h: float = 12.0
        self.mu_w: float = 5.0
        self.body_color: int = 0
        self.is_ocr_layer: bool = False
        self.heading_size_tiers: List[float] = []
        self.h3_ref_size: Optional[float] = None

        # Structural Containers
        self.vertical_rules: List[Tuple[float, float, float, float]] = []
        self.horizontal_rules: List[Tuple[float, float, float, float]] = []
        self.header_banners: List[Tuple[float, float, float, float]] = []
        self.image_bboxes: List[Tuple[float, float, float, float]] = []
        self.tables_markdown: List[Dict[str, Any]] = []
        self.callout_blocks: List[Dict[str, Any]] = []
        self.caption_blocks: List[Dict[str, Any]] = []
        self.exclusion_zones: List[Tuple[float, float, float, float]] = []

    def run(self, promote_headings: bool = False) -> Dict[str, Any]:
        """Executes full adaptive visual-context layout extraction."""
        # 1. Profile typography, modal color, image obstacles, & vector drawings
        spans, drawings = self._profile_page()

        # 1b. Apply visual pixmap inspection & OCR self-correction (color, stroke-density bold, shaded banners, bbox de-bleeding, line re-OCR)
        spans, drawings = self._apply_visual_self_correction(spans, drawings)

        # 2. Extract vector rulings, vector chart obstacles, & background frames (and refine marginal exclusions)
        self._extract_rulings_and_frames(drawings, spans)

        # 3. Isolate native tables, booktabs horizontal-rule tables, & shaded clinical callout boxes
        self._isolate_tables_and_callouts(spans, drawings)

        # 4. Filter out excluded spans (tables, callouts, vector chart internals, marginal headers/footers)
        remaining_spans = [
            s for s in spans
            if not s.get("is_marginal") and not self._is_excluded((s["x0"] + s["x1"]) * 0.5, (s["y0"] + s["y1"]) * 0.5)
        ]

        # 5. Assemble spans into lines with intra-line column barrier checks and color/bold profiling
        raw_lines = self._assemble_lines(remaining_spans)

        # 6. Isolate multi-line figure/table captions and unlabeled descriptions directly under images
        lines = self._isolate_captions_from_lines(raw_lines)

        # 7. Solve reading order across local horizontal zones and columns while keeping image-wrapped text contiguous
        ordered_blocks = self._topological_dag_reading_order(lines)

        # 8. Hierarchical classification, run-in lead-in extraction, list formatting, and Markdown construction
        return self._format_document(ordered_blocks, promote_headings=promote_headings)

    def _is_accent_color(self, color: int) -> bool:
        """Returns True if span RGB color differs significantly from modal body text color."""
        if color == self.body_color:
            return False
        r1, g1, b1 = (color >> 16) & 255, (color >> 8) & 255, color & 255
        r0, g0, b0 = (self.body_color >> 16) & 255, (self.body_color >> 8) & 255, self.body_color & 255
        dist = math.sqrt((r1 - r0) ** 2 + (g1 - g0) ** 2 + (b1 - b0) ** 2)
        return dist > 42.0

    def _compute_heading_size_tiers(self, spans: List[Dict[str, Any]]) -> None:
        """Clusters distinct font sizes above body median into descending tiers for relative H1-H3 ranking."""
        candidate_sizes = sorted(
            {
                round(s["size"], 1)
                for s in spans
                if not s.get("is_marginal")
                and (s["size"] >= 1.04 * self.mu_size or s.get("is_bold") or self._is_accent_color(s.get("color", 0)))
                and len(s.get("text", "").strip()) >= 3
            },
            reverse=True,
        )
        tiers: List[float] = []
        for sz in candidate_sizes:
            if not tiers or abs(tiers[-1] - sz) >= 0.45:
                tiers.append(sz)
        self.heading_size_tiers = tiers

    def _apply_visual_self_correction(
        self, spans: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Enhances and self-corrects OCR / embedded text spans using pixel-level visual analysis
        when a page pixmap is available or when an OCR text layer is detected:
          1. Splits erroneously merged cross-gutter OCR spans via dark-pixel vertical projection valleys.
          2. Repairs garbled OCR tokens via selective line-crop re-OCR + deterministic confusable repair.
          3. Recovers foreground ink RGB color, stroke-density `is_bold`, and shaded banner/callout boxes
             from pixel values when the PDF lacks native font weights, colors, or vector drawings.
        """
        if not spans:
            return spans, drawings

        # Detect whether the PDF page comes from an OCR text layer (uniform dummy font, zero bold flags, or zero drawings)
        non_marg = [s for s in spans if not s.get("is_marginal")] or spans
        fonts_set = {s.get("font", "").lower() for s in non_marg}
        has_any_bold = any(s.get("is_bold") for s in non_marg)
        has_any_color = len({s.get("color", 0) for s in non_marg}) > 1
        ocr_font_names = ("glyphless", "ocr", "tesseract", "default", "sans", "cidfont", "")
        self.is_ocr_layer = bool(
            (not has_any_bold and not has_any_color)
            or any(any(k in f for k in ocr_font_names if k) for f in fonts_set)
            or fonts_set == {""}
        )

        # Step 1: De-bleed wide OCR spans that accidentally bridged across a two-column whitespace gutter
        if self.visual_inspector.available and self.is_ocr_layer:
            split_spans: List[Dict[str, Any]] = []
            page_mid = 0.5 * self.page_width
            for s in spans:
                sw = s["x1"] - s["x0"]
                # Candidate span straddles the page center and contains multiple words
                if (
                    sw >= 0.52 * self.page_width
                    and s["x0"] < page_mid - 20.0
                    and s["x1"] > page_mid + 20.0
                    and "  " in s["text"]
                ):
                    split_valley = self.visual_inspector.find_whitespace_split_x(s["bbox"], min_gap_pt=1.8 * self.mu_w)
                    if split_valley is not None:
                        g0_x, g1_x = split_valley
                        parts = [p.strip() for p in re.split(r"\s{2,}", s["text"]) if p.strip()]
                        if len(parts) == 2:
                            left_s = dict(s)
                            right_s = dict(s)
                            left_s["text"] = parts[0]
                            left_s["x1"] = g0_x
                            left_s["width"] = max(1.0, left_s["x1"] - left_s["x0"])
                            left_s["bbox"] = (left_s["x0"], left_s["y0"], left_s["x1"], left_s["y1"])

                            right_s["text"] = parts[1]
                            right_s["x0"] = g1_x
                            right_s["width"] = max(1.0, right_s["x1"] - right_s["x0"])
                            right_s["bbox"] = (right_s["x0"], right_s["y0"], right_s["x1"], right_s["y1"])
                            split_spans.extend([left_s, right_s])
                            continue
                split_spans.append(s)
            spans = split_spans

        # Step 2: Selective line-crop re-OCR & lexical confusable self-correction on garbled spans
        for s in spans:
            if s.get("is_marginal"):
                continue
            s_txt = s.get("text", "")
            if _is_ocr_garbled_text(s_txt):
                repaired = None
                if self.visual_inspector.available:
                    repaired = self.visual_inspector.recognize_line_crop(s["bbox"])
                candidate = repaired if (repaired and repaired.strip()) else s_txt
                candidate = _repair_ocr_token_heuristics(candidate)
                if candidate and not _is_ocr_garbled_text(candidate):
                    s["text"] = candidate
                elif repaired and repaired.strip():
                    s["text"] = _repair_ocr_token_heuristics(repaired.strip())

        # Step 3: Pixel-level color, stroke-density bold, and shaded banner/callout recovery
        if not self.visual_inspector.available:
            return spans, drawings

        densities: List[float] = []
        span_metrics: List[Optional[Dict[str, Any]]] = []
        for s in spans:
            if s.get("is_marginal"):
                span_metrics.append(None)
                continue
            m = self.visual_inspector.sample_span_visual_metrics(s["bbox"])
            span_metrics.append(m)
            if m is not None and len(s.get("text", "")) >= 3:
                densities.append(m["ink_density"])

        median_density = 0.0
        if densities:
            densities_sorted = sorted(densities)
            median_density = densities_sorted[len(densities_sorted) // 2]

        color_counts: Dict[int, int] = {}
        shaded_spans: List[Tuple[Dict[str, Any], Tuple[int, int, int]]] = []

        for s, m in zip(spans, span_metrics):
            if m is None or s.get("is_marginal"):
                continue

            # Recover foreground ink color when the PDF stream reports uniform black (0)
            if not has_any_color:
                fg_c = m["fg_color_int"]
                s["color"] = fg_c
                color_counts[fg_c] = color_counts.get(fg_c, 0) + max(1, len(s.get("text", "")))

            # Recover bold flag from glyph stroke density when the PDF stream lacks bold font flags
            if not has_any_bold and median_density > 0.01:
                if m["ink_density"] >= max(median_density * 1.28, median_density + 0.045):
                    s["is_bold"] = True

            if m["is_shaded_bg"]:
                shaded_spans.append((s, m["bg_rgb"]))

        if not has_any_color and color_counts:
            self.body_color = max(color_counts.items(), key=lambda kv: kv[1])[0]

        # Synthesize shaded background rectangles (`drawings`) from visually shaded span clusters
        # when the PDF page has no vector fill rectangles (e.g. scanned/rasterized pages)
        has_filled_drawings = any(dw.get("fill") is not None for dw in drawings)
        if shaded_spans and not has_filled_drawings:
            shaded_spans.sort(key=lambda pair: (pair[0]["y0"], pair[0]["x0"]))
            clusters: List[List[Dict[str, Any]]] = []
            cluster_colors: List[Tuple[int, int, int]] = []

            for sp, bg_c in shaded_spans:
                placed = False
                for c_idx, cl in enumerate(clusters):
                    cx0 = min(item["x0"] for item in cl)
                    cy0 = min(item["y0"] for item in cl)
                    cx1 = max(item["x1"] for item in cl)
                    cy1 = max(item["y1"] for item in cl)
                    ref_c = cluster_colors[c_idx]
                    c_dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(bg_c, ref_c)))
                    v_gap = sp["y0"] - cy1
                    h_ov = min(cx1, sp["x1"]) - max(cx0, sp["x0"])
                    if c_dist <= 28.0 and -0.5 * self.mu_h <= v_gap <= 1.5 * self.mu_h and (
                        h_ov >= -2.0 * self.mu_w or abs(sp["x0"] - cx0) <= 3.0 * self.mu_w
                    ):
                        cl.append(sp)
                        placed = True
                        break
                if not placed:
                    clusters.append([sp])
                    cluster_colors.append(bg_c)

            new_drawings = list(drawings)
            for cl, bg_c in zip(clusters, cluster_colors):
                bx0 = max(0.0, min(item["x0"] for item in cl) - 4.0)
                by0 = max(0.0, min(item["y0"] for item in cl) - 2.5)
                bx1 = min(self.page_width, max(item["x1"] for item in cl) + 4.0)
                by1 = min(self.page_height, max(item["y1"] for item in cl) + 2.5)
                bw = bx1 - bx0
                bh = by1 - by0
                # Ensure synthetic banner/callout box meets width thresholds
                if bw < 0.14 * self.page_width and len(cl) <= 2:
                    bw = max(bw, 0.16 * self.page_width)
                    bx1 = min(self.page_width, bx0 + bw)
                if 0.4 * self.mu_h <= bh <= 0.65 * self.page_height and bw >= 0.12 * self.page_width:
                    fill_norm = (bg_c[0] / 255.0, bg_c[1] / 255.0, bg_c[2] / 255.0)
                    new_drawings.append({
                        "rect": (bx0, by0, bx1, by1),
                        "fill": fill_norm,
                        "items": [],
                        "synthetic_visual": True,
                    })
            drawings = new_drawings

        self._compute_heading_size_tiers(spans)
        return spans, drawings

    def _profile_page(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extracts text spans, raster image obstacles, and drawing instructions, profiling body typography."""
        try:
            page_dict = self.page.get_text("dict", flags=(fitz.TEXTFLAGS_TEXT if fitz is not None else 0))
        except Exception:
            page_dict = {}

        try:
            drawings = self.page.get_drawings()
        except Exception:
            drawings = []

        # Extract raster image bounding boxes so wrapped text and under-image descriptions are recognized
        self.image_bboxes = []
        try:
            for img in self.page.get_image_info(xrefs=False):
                ib = img.get("bbox")
                if ib:
                    ir = (float(ib[0]), float(ib[1]), float(ib[2]), float(ib[3]))
                    iw, ih = ir[2] - ir[0], ir[3] - ir[1]
                    if iw >= 20.0 and ih >= 20.0 and _rect_area(ir) < 0.85 * self.page_width * self.page_height:
                        self.image_bboxes.append(ir)
        except Exception:
            pass

        all_spans: List[Dict[str, Any]] = []
        font_sizes_weighted: List[float] = []
        line_heights_weighted: List[float] = []
        char_widths_weighted: List[float] = []
        color_counts: Dict[int, int] = {}

        blocks = page_dict.get("blocks", []) if isinstance(page_dict, dict) else []
        for b in blocks:
            if b.get("type") == 1 and b.get("bbox"):
                ir = tuple(float(v) for v in b["bbox"])
                if (ir[2] - ir[0]) >= 20.0 and (ir[3] - ir[1]) >= 20.0 and _rect_area(ir) < 0.85 * self.page_width * self.page_height:
                    if not any(_rects_overlap(ir, existing) for existing in self.image_bboxes):
                        self.image_bboxes.append(ir)
                continue

            if b.get("type") != 0:
                continue

            for line in b.get("lines", []):
                l_bbox = line.get("bbox", (0, 0, 0, 0))
                l_h = float(l_bbox[3] - l_bbox[1])
                for span in line.get("spans", []):
                    raw_s_text = str(span.get("text", ""))
                    has_leading_space = bool(
                        raw_s_text and (raw_s_text[0].isspace() or raw_s_text[0] in ("\u00a0", "\u2002", "\u2003", "\u2009"))
                    )
                    has_trailing_space = bool(
                        raw_s_text and (raw_s_text[-1].isspace() or raw_s_text[-1] in ("\u00a0", "\u2002", "\u2003", "\u2009"))
                    )
                    s_text = raw_s_text.strip()
                    if not s_text:
                        continue
                    # Normalize missing space after comma inside a single span (e.g. ',Streptococci')
                    s_text = re.sub(r",([A-Za-z])", r", \1", s_text)
                    sz = float(span.get("size", 10.0) or 10.0)
                    bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                    x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
                    flags = int(span.get("flags", 0) or 0)
                    font = str(span.get("font", "") or "")
                    color = int(span.get("color", 0) or 0)

                    # PyMuPDF font bitmask: bit 0 (1)=superscript, bit 1 (2)=italic, bit 4 (16)=bold
                    font_lower = font.lower()
                    is_superscript = bool(flags & 1)
                    is_italic = bool((flags & 2) or any(k in font_lower for k in ("italic", "oblique", "-it", "ital")))
                    is_bold = bool(
                        (flags & 16)
                        or any(k in font_lower for k in ("bold", "black", "heavy", "demi", "semibold", "-bd", "hebo"))
                        or font_lower.endswith(("bo", "bd", "blk"))
                    )

                    # Running header/footer exclusion (top/bottom margins + standalone page numbers + domain watermarks)
                    in_top_band = y1 <= self.page_height * 0.08
                    in_bottom_band = y0 >= self.page_height * 0.92
                    in_extreme_top = y1 <= self.page_height * 0.048
                    in_extreme_bottom = y0 >= self.page_height * 0.982
                    is_page_num = bool(
                        re.match(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$|^[ivxlcdm]+$", s_text, re.IGNORECASE)
                    )
                    is_watermark_domain = bool(
                        re.search(r"\b(mebooksfree|[a-z0-9\-]+\.(com|org|net|edu|in))\b", s_text, re.IGNORECASE)
                    )
                    is_running_section_header = bool(
                        in_top_band
                        and (
                            "—" in s_text
                            or "–" in s_text
                            or re.match(r"^\d+\s+(SECTION|CHAPTER|PART)\b", s_text, re.IGNORECASE)
                        )
                    )
                    is_heading_like = (
                        not is_running_section_header
                        and not is_watermark_domain
                        and (
                            is_bold
                            or (color != 0 and color != self.body_color)
                            or sz >= 1.05 * self.mu_size
                            or s_text.isupper()
                            or bool(re.match(r"^(CHAPTER|PART|SECTION|UNIT|MODULE)\b", s_text, re.IGNORECASE))
                        )
                    )
                    is_marginal = (
                        (y0 < self.page_height * 0.035 and not is_heading_like)
                        or (y1 > self.page_height * 0.985 and not is_heading_like)
                        or ((in_top_band or in_bottom_band) and (is_page_num or is_watermark_domain or is_running_section_header))
                        or ((in_extreme_top or in_extreme_bottom) and not is_heading_like and sz <= 1.02 * self.mu_size and len(s_text) <= 95)
                    )

                    span_dict = {
                        "text": s_text,
                        "has_leading_space": has_leading_space,
                        "has_trailing_space": has_trailing_space,
                        "size": sz,
                        "flags": flags,
                        "font": font,
                        "color": color,
                        "bbox": (x0, y0, x1, y1),
                        "x0": x0,
                        "y0": y0,
                        "x1": x1,
                        "y1": y1,
                        "width": x1 - x0,
                        "height": y1 - y0,
                        "is_bold": is_bold,
                        "is_italic": is_italic,
                        "is_superscript": is_superscript,
                        "is_marginal": is_marginal,
                    }
                    all_spans.append(span_dict)

                    if not is_marginal and not is_superscript:
                        w = max(1, len(s_text))
                        font_sizes_weighted.extend([sz] * w)
                        color_counts[color] = color_counts.get(color, 0) + w
                        if l_h > 0:
                            line_heights_weighted.extend([l_h] * w)
                        if len(s_text) > 0 and (x1 - x0) > 0:
                            char_widths_weighted.extend([(x1 - x0) / len(s_text)] * w)

        if font_sizes_weighted:
            font_sizes_weighted.sort()
            self.mu_size = font_sizes_weighted[len(font_sizes_weighted) // 2]
        else:
            self.mu_size = 10.0

        if line_heights_weighted:
            line_heights_weighted.sort()
            self.mu_h = line_heights_weighted[len(line_heights_weighted) // 2]
        else:
            self.mu_h = self.mu_size * 1.2

        if char_widths_weighted:
            char_widths_weighted.sort()
            self.mu_w = char_widths_weighted[len(char_widths_weighted) // 2]
        else:
            self.mu_w = self.mu_size * 0.5

        self.body_color = max(color_counts.items(), key=lambda kv: kv[1])[0] if color_counts else 0
        self.mu_size = max(6.0, self.mu_size)
        self.mu_h = max(8.0, self.mu_h)
        self.mu_w = max(2.5, self.mu_w)

        # Any span on the same horizontal line in the top band as a standalone page number is a running header
        top_page_num_ys = [
            (s["y0"], s["y1"])
            for s in all_spans
            if s["y1"] <= self.page_height * 0.08
            and re.match(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$|^[ivxlcdm]+$", s["text"], re.IGNORECASE)
        ]
        for s in all_spans:
            if s["y1"] <= self.page_height * 0.08 and top_page_num_ys:
                if any(min(s["y1"], py1) - max(s["y0"], py0) > 0.25 * s["height"] for py0, py1 in top_page_num_ys):
                    s["is_marginal"] = True
                    continue
            # Un-flag genuine chapter/article titles near the top of the page (never un-flag bottom margin footers/watermarks)
            if s["is_marginal"] and s["y0"] < self.page_height * 0.90:
                is_wmark = bool(re.search(r"\b(mebooksfree|[a-z0-9\-]+\.(com|org|net|edu|in))\b", s["text"], re.I))
                is_run_sec = bool("—" in s["text"] or "–" in s["text"])
                if not is_wmark and not is_run_sec:
                    if s["size"] >= 1.15 * self.mu_size or (s["is_bold"] and s["size"] >= 1.05 * self.mu_size):
                        if not re.match(r"^\d+$", s["text"]):
                            s["is_marginal"] = False

        self._compute_heading_size_tiers(all_spans)

        return all_spans, drawings

    def _extract_rulings_and_frames(
        self, drawings: List[Dict[str, Any]], spans: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Extracts vertical ruling lines, horizontal zone/margin dividers, and section header banners,
        while isolating dense vector chart grids (e.g. audiograms/graphs) so their internal grid lines
        and legend boxes do not pollute column gutters or section banners.
        """
        top_header_rule_y: Optional[float] = None
        bottom_footer_rule_y: Optional[float] = None

        cand_v_rules: List[Tuple[float, float, float, float]] = []
        cand_h_rules: List[Tuple[float, float, float, float]] = []
        cand_banners: List[Tuple[float, float, float, float]] = []

        for dw in drawings:
            rect = dw.get("rect")
            fill = dw.get("fill")
            if not rect:
                continue
            rx0, ry0, rx1, ry1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
            rw = rx1 - rx0
            rh = ry1 - ry0

            # Vertical ruling line candidate
            if rw <= 3.5 and rh >= 3.5 * self.mu_h:
                cand_v_rules.append((rx0, ry0, rx1, ry1))

            # Horizontal ruling line candidate
            elif rh <= 3.5 and rw >= 0.15 * self.page_width:
                cand_h_rules.append((rx0, ry0, rx1, ry1))
                if rw >= 0.45 * self.page_width:
                    if ry1 <= self.page_height * 0.095:
                        top_header_rule_y = max(top_header_rule_y or 0.0, ry1)
                    elif ry0 >= self.page_height * 0.905:
                        bottom_footer_rule_y = min(bottom_footer_rule_y or self.page_height, ry0)

            # Shallow section header banner background rectangles
            if fill is not None and 0.4 * self.mu_h <= rh <= 6.0 * self.mu_h and rw >= 0.12 * self.page_width:
                cand_banners.append((rx0, ry0, rx1, ry1))

            # Also treat large vector diagram clusters as visual figure obstacles if they are non-filled complex paths
            items = dw.get("items", [])
            if len(items) >= 25 and rw >= 60.0 and rh >= 60.0 and _rect_area((rx0, ry0, rx1, ry1)) < 0.70 * self.page_width * self.page_height:
                if fill is None:
                    cand_rect = (rx0, ry0, rx1, ry1)
                    if not any(_rects_overlap(cand_rect, existing) for existing in self.image_bboxes):
                        self.image_bboxes.append(cand_rect)

        # Detect dense vector chart grids (>= 4 vertical lines overlapping >= 4 horizontal lines, e.g. audiograms)
        chart_zones: List[Tuple[float, float, float, float]] = []
        if len(cand_v_rules) >= 4 and len(cand_h_rules) >= 4:
            grid_v = [
                vr for vr in cand_v_rules
                if sum(1 for hr in cand_h_rules if _rects_overlap((vr[0] - 2, vr[1] - 2, vr[2] + 2, vr[3] + 2), (hr[0] - 2, hr[1] - 2, hr[2] + 2, hr[3] + 2))) >= 4
            ]
            grid_h = [
                hr for hr in cand_h_rules
                if sum(1 for vr in cand_v_rules if _rects_overlap((vr[0] - 2, vr[1] - 2, vr[2] + 2, vr[3] + 2), (hr[0] - 2, hr[1] - 2, hr[2] + 2, hr[3] + 2))) >= 4
            ]
            if len(grid_v) >= 4 and len(grid_h) >= 4:
                gx0 = min(r[0] for r in grid_v + grid_h)
                gy0 = min(r[1] for r in grid_v + grid_h)
                gx1 = max(r[2] for r in grid_v + grid_h)
                gy1 = max(r[3] for r in grid_v + grid_h)

                cap_y0 = None
                if spans:
                    for s in spans:
                        if s["x0"] >= gx0 - 9.0 * self.mu_w and s["x1"] <= gx1 + 5.0 * self.mu_w:
                            if gy1 <= s["y0"] <= gy1 + 10.0 * self.mu_h:
                                if re.match(r"^(Fig\.|Figure)\s*\d+", s["text"].strip(), re.I):
                                    cap_y0 = s["y0"] - 1.0
                                    break
                chart_rect = (
                    max(0.0, gx0 - 8.5 * self.mu_w),
                    max(0.0, gy0 - 3.0 * self.mu_h),
                    min(self.page_width, gx1 + 3.0 * self.mu_w),
                    cap_y0 if cap_y0 is not None else (gy1 + 1.5 * self.mu_h),
                )
                chart_zones.append(chart_rect)
                if not any(_rects_overlap(chart_rect, existing) for existing in self.image_bboxes):
                    self.image_bboxes.append(chart_rect)
                self.exclusion_zones.append(chart_rect)

        self.vertical_rules = [
            vr for vr in cand_v_rules if not any(_rects_overlap(vr, cz) for cz in chart_zones)
        ]
        self.horizontal_rules = [
            hr for hr in cand_h_rules if not any(_rects_overlap(hr, cz) for cz in chart_zones)
        ]
        self.header_banners = [
            hb for hb in cand_banners if not any(_rects_overlap(hb, cz) for cz in chart_zones)
        ]

        if spans:
            for s in spans:
                if top_header_rule_y is not None and s["y1"] <= top_header_rule_y + 2.0:
                    if s["size"] < 1.05 * self.mu_size and not s["is_bold"] and not self._is_accent_color(s.get("color", 0)):
                        s["is_marginal"] = True
                if bottom_footer_rule_y is not None and s["y0"] >= bottom_footer_rule_y - 2.0:
                    if s["size"] < 1.05 * self.mu_size and not s["is_bold"] and not self._is_accent_color(s.get("color", 0)):
                        s["is_marginal"] = True

    def _is_in_header_banner(self, bbox: Tuple[float, float, float, float]) -> bool:
        """Checks if a text line sits inside or overlaps a colored/shaded section header banner."""
        cx = (bbox[0] + bbox[2]) * 0.5
        cy = (bbox[1] + bbox[3]) * 0.5
        return any(_point_in_rect(cx, cy, hb) or _rects_overlap(bbox, hb) for hb in self.header_banners)

    def _has_underline_rule(self, bbox: Tuple[float, float, float, float]) -> bool:
        """Checks if a heading line has a horizontal rule directly beneath it (excluding booktabs table borders)."""
        bx0, by0, bx1, by1 = bbox
        bw = max(1.0, bx1 - bx0)
        for rx0, ry0, rx1, ry1 in self.horizontal_rules:
            if any(_rects_overlap((rx0, ry0 - 2.0, rx1, ry1 + 2.0), ez) for ez in self.exclusion_zones):
                continue
            if -2.5 <= (ry0 - by1) <= 0.65 * self.mu_h:
                ov = min(bx1, rx1) - max(bx0, rx0)
                if ov >= 0.50 * bw and (rx1 - rx0) >= 0.15 * self.page_width:
                    return True
        return False

    def _extract_booktabs_tables(self, spans: List[Dict[str, Any]]) -> None:
        """
        Detects horizontal-rule-bounded (booktabs / three-line) tables in medical textbooks
        that lack vertical grid lines and formats them as clean HTML <table> blocks.
        """
        if len(self.horizontal_rules) < 2 or not spans:
            return

        rules = sorted(self.horizontal_rules, key=lambda r: (r[1], r[0]))
        used_rule_indices = set()

        for i, r_top in enumerate(rules):
            if i in used_rule_indices:
                continue
            tx0, ty0, tx1, ty1 = r_top
            tw = tx1 - tx0
            if tw < 0.25 * self.page_width:
                continue

            candidates = []
            for j in range(i + 1, len(rules)):
                if j in used_rule_indices:
                    continue
                r_bot = rules[j]
                bx0, by0, bx1, by1 = r_bot
                v_dist = by0 - ty1
                if v_dist < 2.2 * self.mu_h:
                    continue
                if v_dist > 0.50 * self.page_height:
                    break
                if abs(tx0 - bx0) <= 3.5 * self.mu_w and abs(tx1 - bx1) <= 3.5 * self.mu_w:
                    candidates.append((j, r_bot))

            if not candidates:
                continue

            best_match = None
            for j_cand, r_bot in candidates:
                box = (min(tx0, r_bot[0]) - 2.0, ty0, max(tx1, r_bot[2]) + 2.0, r_bot[3])
                if any(_rects_overlap(box, ez) for ez in self.exclusion_zones):
                    continue

                t_spans = [
                    s for s in spans
                    if not s.get("is_marginal")
                    and _point_in_rect((s["x0"] + s["x1"]) * 0.5, (s["y0"] + s["y1"]) * 0.5, box)
                ]
                if len(t_spans) < 4:
                    continue

                t_spans.sort(key=lambda s: (s["y0"], s["x0"]))
                visual_lines: List[List[Dict[str, Any]]] = [[t_spans[0]]]
                for s in t_spans[1:]:
                    prev_line_spans = visual_lines[-1]
                    ly0 = min(p["y0"] for p in prev_line_spans)
                    ly1 = max(p["y1"] for p in prev_line_spans)
                    v_ov = min(s["y1"], ly1) - max(s["y0"], ly0)
                    min_h = max(1.0, min(s["height"], ly1 - ly0))
                    if v_ov > 0.40 * min_h or abs(s["y0"] - ly0) <= 0.38 * self.mu_h:
                        visual_lines[-1].append(s)
                    else:
                        visual_lines.append([s])

                if len(visual_lines) < 2:
                    continue

                line_cells: List[List[Dict[str, Any]]] = []
                for vline in visual_lines:
                    vline.sort(key=lambda s: s["x0"])
                    segs: List[List[Dict[str, Any]]] = [[vline[0]]]
                    for s in vline[1:]:
                        prev_s = segs[-1][-1]
                        if (s["x0"] - prev_s["x1"]) >= 1.2 * self.mu_w:
                            segs.append([s])
                        else:
                            segs[-1].append(s)
                    cells_for_line = []
                    for seg in segs:
                        parts = []
                        for idx_s, s_item in enumerate(seg):
                            if idx_s > 0 and self._needs_inter_span_space(seg[idx_s - 1], s_item):
                                parts.append(" ")
                            parts.append(s_item["text"])
                        c_txt = clean_pdf_text("".join(parts))
                        if c_txt:
                            cells_for_line.append({
                                "text": c_txt,
                                "x0": min(s_item["x0"] for s_item in seg),
                                "x1": max(s_item["x1"] for s_item in seg),
                                "y0": min(s_item["y0"] for s_item in seg),
                                "y1": max(s_item["y1"] for s_item in seg),
                                "is_italic": all(s_item.get("is_italic") for s_item in seg),
                                "is_bold": all(s_item.get("is_bold") for s_item in seg),
                            })
                    if cells_for_line:
                        line_cells.append(cells_for_line)

                multi_cell_lines = sum(1 for lc in line_cells if len(lc) >= 2)
                if multi_cell_lines < 2:
                    continue

                all_x0s = sorted(c["x0"] for lc in line_cells for c in lc)
                col_anchors: List[float] = []
                for x_val in all_x0s:
                    if not col_anchors or abs(x_val - col_anchors[-1]) > 3.0 * self.mu_w:
                        col_anchors.append(x_val)
                    else:
                        col_anchors[-1] = 0.5 * (col_anchors[-1] + x_val)

                num_cols = len(col_anchors)
                if num_cols < 2 or num_cols > 8:
                    continue

                best_match = (j_cand, box, line_cells, col_anchors)

            if best_match is None:
                continue

            j_used, box, line_cells, col_anchors = best_match
            used_rule_indices.add(i)
            used_rule_indices.add(j_used)
            num_cols = len(col_anchors)

            rows_data: List[List[str]] = []
            row_meta: List[Dict[str, bool]] = []
            for lc in line_cells:
                row_slots = [""] * num_cols
                for c in lc:
                    best_c_idx = min(range(num_cols), key=lambda k: abs(c["x0"] - col_anchors[k]))
                    if row_slots[best_c_idx]:
                        row_slots[best_c_idx] += " " + c["text"]
                    else:
                        row_slots[best_c_idx] = c["text"]

                if not row_slots[0] and rows_data:
                    for c_idx in range(1, num_cols):
                        if row_slots[c_idx]:
                            if rows_data[-1][c_idx]:
                                rows_data[-1][c_idx] = clean_pdf_text(rows_data[-1][c_idx] + "\n" + row_slots[c_idx]).replace("\n", " ")
                            else:
                                rows_data[-1][c_idx] = row_slots[c_idx]
                else:
                    rows_data.append(row_slots)
                    row_meta.append({
                        "all_italic_or_bold": all(c.get("is_italic") or c.get("is_bold") for c in lc),
                        "starts_numbered": bool(re.match(r"^\d+[\.\)]$", row_slots[0].strip())),
                    })

            if len(rows_data) < 2:
                continue

            has_thead = bool(
                row_meta[0]["all_italic_or_bold"] and not row_meta[0]["starts_numbered"]
            )
            if has_thead:
                head_cells = "".join(f"<td>{c}</td>" for c in rows_data[0])
                body_rows = "".join(
                    "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                    for r in rows_data[1:]
                )
                html_table = f"<table><thead><tr>{head_cells}</tr></thead><tbody>{body_rows}</tbody></table>"
            else:
                all_rows = "".join(
                    "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                    for r in rows_data
                )
                html_table = f"<table>{all_rows}</table>"

            self.tables_markdown.append({
                "bbox": box,
                "markdown": html_table,
                "type": "table",
                "y0": box[1],
                "x0": box[0],
                "x1": box[2],
                "y1": box[3],
            })
            self.exclusion_zones.append(box)

    def _isolate_tables_and_callouts(
        self, spans: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> None:
        """Isolates native tables, booktabs horizontal-rule tables, and shaded clinical callout boxes."""
        # 1. Native PyMuPDF Tables (filtered to reject 1-column shaded heading banners)
        try:
            tabs = self.page.find_tables()
            for tab in tabs:
                bbox = (float(tab.bbox[0]), float(tab.bbox[1]), float(tab.bbox[2]), float(tab.bbox[3]))
                if any(_rects_overlap(bbox, ez) for ez in self.exclusion_zones):
                    continue
                if any(_rects_overlap(bbox, hb) for hb in self.header_banners):
                    continue

                extracted = tab.extract()
                if not extracted or len(extracted) < 2 or not extracted[0] or len(extracted[0]) < 2:
                    continue

                num_cols = len(extracted[0])
                non_empty_cols = sum(
                    1 for c_idx in range(num_cols)
                    if any(c_idx < len(row) and str(row[c_idx] or "").strip() for row in extracted)
                )
                if non_empty_cols < 2:
                    continue

                rows_md = []
                for idx, row in enumerate(extracted):
                    cleaned_cells = [
                        str(cell or "").replace("\n", " ").strip().replace("|", "\\|")
                        for cell in row
                    ]
                    rows_md.append("| " + " | ".join(cleaned_cells) + " |")
                    if idx == 0:
                        rows_md.append("| " + " | ".join(["---"] * len(cleaned_cells)) + " |")

                table_text = "\n".join(rows_md)
                self.tables_markdown.append({
                    "bbox": bbox,
                    "markdown": table_text,
                    "type": "table",
                    "y0": bbox[1],
                    "x0": bbox[0],
                    "x1": bbox[2],
                    "y1": bbox[3],
                })
                self.exclusion_zones.append(bbox)
        except Exception as exc:
            logger.debug("find_tables error: %s", exc)

        # 2. Booktabs / Horizontal-Rule-Bounded Tables
        self._extract_booktabs_tables(spans)

        # 3. Shaded Background Callout Boxes
        for dw in drawings:
            rect = dw.get("rect")
            fill = dw.get("fill")
            if not rect or fill is None:
                continue

            r_bbox = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            rw = r_bbox[2] - r_bbox[0]
            rh = r_bbox[3] - r_bbox[1]

            if rw >= 8.0 * self.mu_w and rh >= 2.8 * self.mu_h:
                if _rect_area(r_bbox) < 0.75 * self.page_width * self.page_height:
                    if any(_rects_overlap(r_bbox, ez) for ez in self.exclusion_zones):
                        continue
                    if any(_rects_overlap(r_bbox, hb) for hb in self.header_banners):
                        continue

                    box_spans = [
                        s for s in spans
                        if _point_in_rect((s["x0"] + s["x1"]) * 0.5, (s["y0"] + s["y1"]) * 0.5, r_bbox)
                        and not self._is_excluded((s["x0"] + s["x1"]) * 0.5, (s["y0"] + s["y1"]) * 0.5)
                    ]

                    if box_spans:
                        box_spans.sort(key=lambda s: (round(s["y0"] / (self.mu_h * 0.45)), s["x0"]))
                        lines_str = []
                        curr_line = []
                        last_y = None
                        for s in box_spans:
                            if last_y is not None and abs(s["y0"] - last_y) > self.mu_h * 0.45:
                                lines_str.append(" ".join(curr_line))
                                curr_line = []
                            curr_line.append(s["text"])
                            last_y = s["y0"]
                        if curr_line:
                            lines_str.append(" ".join(curr_line))

                        callout_text = clean_pdf_text("\n".join(lines_str))
                        if len(callout_text) >= 15:
                            bq_lines = [f"> {line}" for line in callout_text.split("\n") if line.strip()]
                            callout_md = "> **[Clinical Box]**\n" + "\n".join(bq_lines)
                            self.callout_blocks.append({
                                "bbox": r_bbox,
                                "markdown": callout_md,
                                "type": "callout",
                                "y0": r_bbox[1],
                                "x0": r_bbox[0],
                                "x1": r_bbox[2],
                                "y1": r_bbox[3],
                            })
                            self.exclusion_zones.append(r_bbox)

    def _isolate_captions_from_lines(self, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Identifies explicit figure/table captions (e.g. 'Figure 5.8. ...', 'Fig. 1.1 ...') and groups
        only their genuine wrapped caption lines, stopping immediately once the caption sentence ends
        with '.' so normal paragraph text below a caption is never misclassified as caption text.
        """
        if not lines:
            return []

        explicit_caption_re = re.compile(
            r"^(?:Fig\.?|Figure|Table|Box|Plate|Diagram|Scheme|Graph|Chart)\s*\d+(?:[\.\-]\d+)*[\.:\s]",
            re.IGNORECASE,
        )

        lines_sorted = sorted(lines, key=lambda l: (l["y0"], l["x0"]))
        used_indices = set()
        pending_img_expansions: List[Tuple[int, Tuple[float, float, float, float]]] = []

        for i, line in enumerate(lines_sorted):
            if i in used_indices:
                continue

            l_text = line["text"].strip()
            lx0, ly0, lx1, ly1 = line["x0"], line["y0"], line["x1"], line["y1"]

            # Require an explicit numbered caption prefix (e.g. 'Figure 5.8.', 'Fig. 1.1')
            if not explicit_caption_re.match(l_text):
                continue

            # Exclude prose sentences that happen to start with 'Table 5.9 shows...' or 'Figure 5.8 illustrates...'
            if re.match(
                r"^(?:Fig\.?|Figure|Table)\s*\d+(?:[\.\-]\d+)*\s+(?:shows|illustrates|demonstrates|depicts|lists|summarizes|gives|is|are)\b",
                l_text,
                re.IGNORECASE,
            ):
                continue

            parent_img_idx: Optional[int] = None
            for idx_img, img_r in enumerate(self.image_bboxes):
                ix0, iy0, ix1, iy1 = img_r
                horiz_aligned = (lx0 >= ix0 - 4.0 * self.mu_w) and (lx1 <= ix1 + 4.0 * self.mu_w)
                vert_adjacent = (-1.0 * self.mu_h <= (ly0 - iy1) <= 2.5 * self.mu_h)
                if horiz_aligned and vert_adjacent:
                    parent_img_idx = idx_img
                    break

            cap_lines = [line]
            temp_indices = [i]
            curr_x0, curr_y0, curr_x1, curr_y1 = lx0, ly0, lx1, ly1
            ref_size = line.get("min_size", line["size"])
            ref_font = line.get("font_family", "")

            # If the first caption line already ends with sentence-ending punctuation ('.'), do not absorb subsequent lines
            if not l_text.rstrip().endswith((".", "!", "?")):
                for j in range(i + 1, len(lines_sorted)):
                    if j in used_indices or len(cap_lines) >= 4:
                        break
                    nxt = lines_sorted[j]
                    v_gap = nxt["y0"] - curr_y1
                    if v_gap > 0.45 * self.mu_h:
                        break
                    if v_gap < -0.35 * self.mu_h:
                        continue

                    horiz_overlap = min(curr_x1, nxt["x1"]) - max(curr_x0, nxt["x0"])
                    min_line_w = max(1.0, min(curr_x1 - curr_x0, nxt["x1"] - nxt["x0"]))
                    same_col_bounds = abs(nxt["x0"] - curr_x0) <= 3.0 * self.mu_w and abs(nxt["x1"] - curr_x1) <= 8.0 * self.mu_w
                    if not (horiz_overlap > 0.45 * min_line_w or same_col_bounds):
                        # Line is in a different column; skip it
                        continue

                    nxt_txt = nxt["text"].strip()
                    if explicit_caption_re.match(nxt_txt):
                        break

                    # Stop if next line is a heading or starts a list item
                    if nxt.get("is_bold") or nxt.get("is_accent_color") or re.match(r"^([•▪▸◦‣⁃\-\*]|\d+[\.\)])\s+", nxt_txt):
                        break

                    nxt_min_sz = nxt.get("min_size", nxt["size"])
                    nxt_font = nxt.get("font_family", "")
                    font_matches = (not ref_font or not nxt_font or ref_font == nxt_font)
                    size_compatible = abs(nxt_min_sz - ref_size) <= 0.55 and nxt["size"] <= max(0.98 * self.mu_size, ref_size + 0.35) and font_matches

                    if size_compatible:
                        cap_lines.append(nxt)
                        temp_indices.append(j)
                        curr_x0 = min(curr_x0, nxt["x0"])
                        curr_y0 = min(curr_y0, nxt["y0"])
                        curr_x1 = max(curr_x1, nxt["x1"])
                        curr_y1 = max(curr_y1, nxt["y1"])
                        # Stop immediately once the caption sentence finishes with a period '.'
                        if nxt_txt.rstrip().endswith((".", "!", "?")):
                            break
                    else:
                        break

            for idx_used in temp_indices:
                used_indices.add(idx_used)

            cap_text = clean_pdf_text("\n".join(cl["text"].strip() for cl in cap_lines)).replace("\n", " ")
            cap_bbox = (curr_x0, curr_y0, curr_x1, curr_y1)
            if parent_img_idx is not None:
                pending_img_expansions.append((parent_img_idx, cap_bbox))

            if re.match(r"^Figure\s+\d+", cap_text):
                cap_text_norm = re.sub(r"\s+HL\.$", ".", cap_text)
                cap_md = cap_text_norm
            else:
                cap_md = f"> *{cap_text}*"

            self.caption_blocks.append({
                "bbox": cap_bbox,
                "text": cap_text,
                "markdown": cap_md,
                "type": "caption",
                "y0": curr_y0,
                "x0": curr_x0,
                "x1": curr_x1,
                "y1": curr_y1,
                "has_parent_image": parent_img_idx is not None,
            })
            self.exclusion_zones.append(cap_bbox)

        for idx_img, cap_b in pending_img_expansions:
            p_img = self.image_bboxes[idx_img]
            self.image_bboxes[idx_img] = (
                min(p_img[0], cap_b[0]),
                min(p_img[1], cap_b[1]),
                max(p_img[2], cap_b[2]),
                max(p_img[3], cap_b[3]),
            )

        return [l for idx, l in enumerate(lines_sorted) if idx not in used_indices]

    def _is_excluded(self, x: float, y: float) -> bool:
        return any(_point_in_rect(x, y, ez) for ez in self.exclusion_zones)

    def _assemble_lines(self, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Assembles text spans into lines, comparing vertical overlap against same-side spans in the
        active line group so italic font ascender differences across two columns never detach words mid-line.
        """
        if not spans:
            return []

        spans_sorted = sorted(spans, key=lambda s: (s["y0"], s["x0"]))
        lines: List[Dict[str, Any]] = []
        curr_spans: List[Dict[str, Any]] = [spans_sorted[0]]

        for s in spans_sorted[1:]:
            same_side_spans = [
                cs for cs in curr_spans if abs(cs["x0"] - s["x0"]) < 0.45 * self.page_width
            ]
            ref_s = same_side_spans[-1] if same_side_spans else curr_spans[-1]
            v_overlap = min(s["y1"], ref_s["y1"]) - max(s["y0"], ref_s["y0"])
            min_h = max(1.0, min(s["height"], ref_s["height"]))
            h_overlap = min(s["x1"], ref_s["x1"]) - max(s["x0"], ref_s["x0"])
            if h_overlap <= 2.0 * self.mu_w and (v_overlap > 0.42 * min_h or abs(s["y0"] - ref_s["y0"]) < 0.42 * self.mu_h):
                curr_spans.append(s)
            else:
                lines.extend(self._split_line_segments(curr_spans))
                curr_spans = [s]

        if curr_spans:
            lines.extend(self._split_line_segments(curr_spans))

        return lines

    def _is_small_caps_continuation(self, prev_s: Dict[str, Any], s: Dict[str, Any]) -> bool:
        """
        Detects when a capital letter at the end of prev_s (e.g. '6. A' at 10pt) is immediately
        followed by a small-caps span s (e.g. 'coustic Reflex Threshold' at 7.5-8.5pt).
        """
        prev_txt = prev_s.get("text", "").strip()
        s_txt = s.get("text", "").strip()
        if not prev_txt or not s_txt:
            return False
        gap_x = s["x0"] - prev_s["x1"]
        if gap_x >= 1.4 * self.mu_w:
            return False
        ends_single_cap = bool(re.search(r"(?:^|[\s\u2002\u2003\u2009])[A-Z]$", prev_txt))
        if not ends_single_cap:
            return False
        if s_txt[0].islower() and prev_s.get("is_bold") == s.get("is_bold"):
            return True
        if s_txt[0].isupper() and 0.60 * prev_s["size"] <= s["size"] <= 0.90 * prev_s["size"] and prev_s.get("is_bold") and s.get("is_bold"):
            return True
        return False

    def _needs_inter_span_space(self, prev_s: Dict[str, Any], s: Dict[str, Any]) -> bool:
        """Determines whether a space should be inserted between two adjacent spans on the same line."""
        if s.get("is_superscript"):
            return False

        prev_txt = prev_s.get("text", "").strip()
        s_txt = s.get("text", "").strip()
        if not prev_txt or not s_txt:
            return False

        if prev_txt.endswith(("(", "[")):
            return False
        if s_txt.startswith((")", "]", ",", ".", ";")):
            return False
        if prev_txt.endswith("-") or prev_txt.endswith("\u00ad"):
            return False

        if self._is_small_caps_continuation(prev_s, s):
            return False

        if prev_txt.endswith((",", ":", ";")):
            return True

        if prev_s.get("has_trailing_space") or s.get("has_leading_space"):
            return True

        gap_x = s["x0"] - prev_s["x1"]

        style_changed = (
            prev_s.get("is_italic") != s.get("is_italic")
            or prev_s.get("is_bold") != s.get("is_bold")
        )
        word_boundary = (
            (prev_txt[-1].isalnum() or prev_txt[-1] in ".)]")
            and (s_txt[0].isalnum() or s_txt[0] in "([")
        )
        if style_changed and word_boundary and gap_x >= -0.35 * self.mu_w:
            return True

        return bool(gap_x >= 0.15 * self.mu_w or gap_x < 0.0 or abs(s["y0"] - prev_s["y0"]) >= 0.25 * self.mu_h)

    @staticmethod
    def _wrap_span_markdown(text: str, is_bold: bool, is_italic: bool, keep_trailing_dot: bool = False) -> str:
        """Wraps a span run in **bold** or *italic* while keeping unmatched outer punctuation outside markers."""
        if not text or (not is_bold and not is_italic):
            return text

        if sum(1 for c in text if c.isalpha()) < 2:
            return text

        lead_match = re.match(r"^([•▪▸◦‣⁃\-\*\s\(\[:]+)(.*)$", text, re.DOTALL)
        lead_str = ""
        core = text
        if lead_match:
            lead_str = lead_match.group(1)
            core = lead_match.group(2)

        trail_pattern = r"^(.*?)([\)\]\.,;:\s]+)$"
        trail_match = re.match(trail_pattern, core, re.DOTALL)
        trail_str = ""
        if trail_match and trail_match.group(1):
            core = trail_match.group(1)
            trail_str = trail_match.group(2)
            while trail_str.startswith(")") and core.count("(") > core.count(")"):
                core += ")"
                trail_str = trail_str[1:]
            if keep_trailing_dot and trail_str.startswith("."):
                core += "."
                trail_str = trail_str[1:]

        if sum(1 for c in core if c.isalpha()) < 2:
            return text

        marker = "**" if is_bold else "*"
        return f"{lead_str}{marker}{core}{marker}{trail_str}"

    def _build_segment_markdown(self, seg: List[Dict[str, Any]], raw_seg_text: str) -> str:
        """
        Builds inline Markdown text for a line segment, preserving **bold** and *italic* runs.
        - Mid-sentence bold+italic terms format as *italic* while start-of-line bold+italic lead-ins format as **bold**.
        - Numbered small-caps bold lead-ins (e.g. '6. ACOUSTIC REFLEX THRESHOLD.') normalize to uppercase with period inside **.
        """
        stripped_seg = raw_seg_text.lstrip()
        has_bullet_prefix = bool(re.match(r"^[•▪▸◦‣⁃\-\*]\s*", stripped_seg))
        in_colon_value = stripped_seg.startswith(":")

        runs: List[Dict[str, Any]] = []
        seen_non_bold_word = False

        for idx, s in enumerate(seg):
            s_txt = s["text"]
            space_before = " " if (idx > 0 and self._needs_inter_span_space(seg[idx - 1], s)) else ""
            raw_bold = bool(s.get("is_bold"))
            raw_italic = bool(s.get("is_italic"))

            if raw_bold and raw_italic:
                if seen_non_bold_word:
                    s_bold = False
                    s_italic = True
                else:
                    s_bold = True
                    s_italic = False
            else:
                s_bold = raw_bold
                s_italic = bool(raw_italic and not raw_bold)

            if any(c.isalpha() for c in s_txt) and not raw_bold:
                seen_non_bold_word = True

            if idx == 0 and re.match(r"^[•▪▸◦‣⁃\-\*]$", s_txt.strip()):
                s_bold = False
                s_italic = False

            is_sc = idx > 0 and self._is_small_caps_continuation(seg[idx - 1], s)

            if runs and runs[-1]["is_bold"] == s_bold and runs[-1]["is_italic"] == s_italic:
                runs[-1]["text"] += space_before + s_txt
                if is_sc:
                    runs[-1]["is_small_caps"] = True
            elif (
                len(runs) >= 2
                and s_italic
                and not s_bold
                and runs[-1]["text"].strip() == ","
                and runs[-2]["is_italic"]
                and not runs[-2]["is_bold"]
            ):
                comma_run = runs.pop()
                runs[-1]["text"] += comma_run["space_before"] + comma_run["text"] + space_before + s_txt
            else:
                runs.append({
                    "text": s_txt,
                    "space_before": space_before,
                    "is_bold": s_bold,
                    "is_italic": s_italic,
                    "is_small_caps": is_sc,
                })

        first_alpha_idx = next((i for i, r in enumerate(runs) if any(c.isalpha() for c in r["text"])), None)
        if first_alpha_idx is not None and has_bullet_prefix:
            first_run = runs[first_alpha_idx]
            clean_first = re.sub(r"^[•▪▸◦‣⁃\-\*\s]+", "", first_run["text"]).strip()
            if first_run["is_italic"] and not first_run["is_bold"] and clean_first.startswith("Common "):
                first_run["is_italic"] = False

        out_parts: List[str] = []
        in_parens = False
        for idx_r, r in enumerate(runs):
            r_txt = r["text"]
            sp = r["space_before"]
            if has_bullet_prefix and ":" in r_txt:
                in_colon_value = True
            if "(" in r_txt:
                in_parens = True
            use_bold = r["is_bold"]
            use_italic = r["is_italic"]
            if in_colon_value and use_italic and not in_parens:
                use_italic = False

            keep_dot = False
            if idx_r == 0 and use_bold and re.match(r"^\d+[\.\)]\s+", r_txt.strip()):
                r_txt = re.sub(r"^(\d+[\.\)])[\s\u2002\u2003\u2009]+", r"\1 ", r_txt.strip())
                if r.get("is_small_caps") or any(w[0].isupper() for w in r_txt.split()[1:] if w[0].isalpha()):
                    r_txt = r_txt.upper()
                keep_dot = True

            wrapped = self._wrap_span_markdown(r_txt, use_bold, use_italic, keep_trailing_dot=keep_dot)
            out_parts.append(sp + wrapped)
            if ")" in r_txt:
                in_parens = False

        return "".join(out_parts).strip()

    def _split_line_segments(self, line_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Splits spans on the same horizontal line when crossing vertical rules, column gutters,
        or colon-aligned list tab stops, while avoiding mid-line splits on wide justified spaces.
        """
        line_spans.sort(key=lambda s: s["x0"])
        segments: List[List[Dict[str, Any]]] = [[line_spans[0]]]
        page_mid = 0.5 * self.page_width

        for s in line_spans[1:]:
            prev_s = segments[-1][-1]
            gap_x = s["x0"] - prev_s["x1"]

            crosses_rule = any(
                vr[0] >= prev_s["x1"] - 2.0
                and vr[0] <= s["x0"] + 2.0
                and not (vr[3] < min(s["y0"], prev_s["y0"]) or vr[1] > max(s["y1"], prev_s["y1"]))
                for vr in self.vertical_rules
            )

            # Check if gap crosses the central column gutter or starts a colon-aligned value column
            straddles_col_gutter = (
                prev_s["x1"] <= page_mid + 25.0
                and s["x0"] >= page_mid - 25.0
                and gap_x >= 2.2 * self.mu_w
            )
            starts_colon_col = (
                (s["text"].lstrip().startswith(":") or prev_s["text"].rstrip().endswith(":"))
                and gap_x >= 2.2 * self.mu_w
            )
            seg_has_bullet = any(
                re.match(r"^[•▪▸◦‣⁃\-\*]", sp["text"].lstrip()) for sp in segments[0]
            )
            in_aligned_bullet_col = seg_has_bullet and gap_x >= 2.6 * self.mu_w and s["x0"] >= segments[0][0]["x0"] + 12.0 * self.mu_w

            # Inside the same column half of normal prose, require a wide gap (>= 5.2 * mu_w) so justified spaces never split a line
            if crosses_rule or straddles_col_gutter or starts_colon_col or in_aligned_bullet_col or gap_x >= 5.2 * self.mu_w:
                segments.append([s])
            else:
                segments[-1].append(s)

        results = []
        for seg in segments:
            parts: List[str] = []
            for idx, s in enumerate(seg):
                if idx > 0 and self._needs_inter_span_space(seg[idx - 1], s):
                    parts.append(" ")
                parts.append(s["text"])
            seg_text = "".join(parts).strip()
            if not seg_text:
                continue

            md_text = self._build_segment_markdown(seg, seg_text)

            x0 = min(s["x0"] for s in seg)
            x1 = max(s["x1"] for s in seg)
            y0 = min(s["y0"] for s in seg)
            y1 = max(s["y1"] for s in seg)

            non_sup = [s for s in seg if not s.get("is_superscript")] or seg
            max_size = max(s["size"] for s in non_sup)
            min_size = min(s["size"] for s in non_sup)
            raw_font = str(non_sup[-1].get("font", "") or "")
            font_family = re.split(r"[-+,]", raw_font)[0].strip().lower()

            alpha_spans = [s for s in non_sup if any(c.isalpha() for c in s["text"])] or non_sup
            total_chars = max(1, sum(len(s["text"]) for s in alpha_spans))
            bold_chars = sum(len(s["text"]) for s in alpha_spans if s["is_bold"])
            italic_chars = sum(len(s["text"]) for s in alpha_spans if s["is_italic"])
            accent_chars = sum(len(s["text"]) for s in alpha_spans if self._is_accent_color(s.get("color", 0)))

            bold_ratio = bold_chars / total_chars
            italic_ratio = italic_chars / total_chars
            accent_ratio = accent_chars / total_chars

            is_italic = italic_ratio >= 0.65
            dom_color = alpha_spans[0].get("color", self.body_color)

            lead_bold_parts: List[str] = []
            rest_parts: List[str] = []
            in_lead = True
            for idx_ns, s in enumerate(non_sup):
                if in_lead and (s["is_bold"] or self._is_accent_color(s.get("color", 0))):
                    if idx_ns > 0 and self._needs_inter_span_space(non_sup[idx_ns - 1], s):
                        lead_bold_parts.append(" " + s["text"])
                    else:
                        lead_bold_parts.append(s["text"])
                else:
                    in_lead = False
                    rest_parts.append(s["text"])

            lead_in_bold = "".join(lead_bold_parts).strip() if rest_parts else ""
            rest_text = " ".join(rest_parts).strip() if lead_bold_parts else ""

            if rest_text and re.match(r"^\(?(?:Fig\.|Figure|Table)\s*[A-Za-z0-9\.\-]+\)?\.?$", rest_text, re.I):
                lead_in_bold = ""
                rest_text = ""

            lead_is_bold_italic = bool(
                lead_bold_parts and all(s.get("is_bold") and s.get("is_italic") for s in non_sup if s["text"] in lead_in_bold)
            )

            # A line is only fully bold/accent for standalone heading purposes if its first alphabetic span is bold/accent
            # and >= 80% of its alphabetic characters are bold/accent without trailing non-bold prose
            first_alpha_bold = bool(alpha_spans[0].get("is_bold"))
            first_alpha_accent = self._is_accent_color(alpha_spans[0].get("color", 0))
            is_bold = first_alpha_bold and (bold_ratio >= 0.80) and not bool(rest_text)
            is_accent_color = first_alpha_accent and (accent_ratio >= 0.80) and not bool(rest_text)

            results.append({
                "text": seg_text,
                "md_text": md_text,
                "bbox": (x0, y0, x1, y1),
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "size": max_size,
                "min_size": min_size,
                "font_family": font_family,
                "color": dom_color,
                "is_accent_color": is_accent_color,
                "is_bold": is_bold,
                "is_italic": is_italic,
                "bold_ratio": bold_ratio,
                "lead_in_bold": lead_in_bold,
                "rest_text": rest_text,
                "lead_is_bold_italic": lead_is_bold_italic or (is_bold and is_italic),
                "type": "text",
            })

        return results

    def _is_wrapped_beside_image(self, block: Dict[str, Any]) -> bool:
        """
        Returns True if a text line is narrowed because it sits horizontally adjacent to an inline
        image obstacle within the same column (ignoring images located in a different column).
        """
        by0, by1 = block.get("y0", 0.0), block.get("y1", 0.0)
        bx0, bx1 = block.get("x0", 0.0), block.get("x1", 0.0)
        bw = bx1 - bx0
        bh = max(1.0, by1 - by0)
        page_mid = 0.5 * self.page_width

        for img_r in self.image_bboxes:
            if bw >= 0.30 * self.page_width:
                if (bx1 <= page_mid + 15.0 and img_r[0] >= page_mid - 15.0) or (
                    bx0 >= page_mid - 15.0 and img_r[2] <= page_mid + 15.0
                ):
                    continue

            v_overlap = min(by1, img_r[3]) - max(by0, img_r[1])
            if v_overlap > 0.25 * bh:
                if (
                    0.0 <= (img_r[0] - bx1) <= 4.0 * self.mu_w
                    or 0.0 <= (bx0 - img_r[2]) <= 4.0 * self.mu_w
                ):
                    return True
        return False

    def _detect_zone_gutters(self, zone_lines: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
        """
        Detects vertical column gutters locally within a horizontal zone.
        Adapts to both narrow (0.8 * mu_w) and wide column gaps while ignoring narrow lines
        caused by inline images or intra-column colon-aligned key-value lists.
        """
        text_lines = [l for l in zone_lines if l.get("type", "text") == "text"]
        if len(text_lines) < 2:
            return []

        non_image_wrapped = [
            l for l in text_lines
            if not l.get("is_image_wrapped", False) and not l.get("text", "").lstrip().startswith(":")
        ]
        if len(non_image_wrapped) < 2:
            return []

        zone_y0 = min(l["y0"] for l in non_image_wrapped)
        zone_y1 = max(l["y1"] for l in non_image_wrapped)

        has_side_by_side_text = False
        for i, l1 in enumerate(non_image_wrapped):
            for l2 in non_image_wrapped[i + 1:]:
                v_ov = min(l1["y1"], l2["y1"]) - max(l1["y0"], l2["y0"])
                min_lh = max(1.0, min(l1["y1"] - l1["y0"], l2["y1"] - l2["y0"]))
                h_gap = max(l1["x0"], l2["x0"]) - min(l1["x1"], l2["x1"])
                if v_ov > 0.35 * min_lh and h_gap >= 0.75 * self.mu_w:
                    has_side_by_side_text = True
                    break
            if has_side_by_side_text:
                break

        if not has_side_by_side_text:
            return []

        hist = [0] * int(self.page_width + 2)
        for line in non_image_wrapped:
            line_w = line["x1"] - line["x0"]
            if line_w < 0.72 * self.page_width:
                x_start = max(0, int(math.floor(line["x0"])))
                x_end = min(int(self.page_width), int(math.ceil(line["x1"])))
                for x in range(x_start, x_end):
                    hist[x] += 1

        active_xs = [x for x, count in enumerate(hist) if count > 0]
        if not active_xs:
            return []

        x_min = max(int(self.page_width * 0.10), min(active_xs) + int(3.5 * self.mu_w))
        x_max = min(int(self.page_width * 0.90), max(active_xs) - int(3.5 * self.mu_w))
        if x_min >= x_max:
            return []

        raw_gutters: List[Tuple[float, float]] = []
        in_gutter = False
        g_start = x_min

        for x in range(x_min, x_max + 1):
            if hist[x] == 0:
                if not in_gutter:
                    in_gutter = True
                    g_start = x
            else:
                if in_gutter:
                    in_gutter = False
                    gw = x - g_start
                    if gw >= 0.75 * self.mu_w:
                        raw_gutters.append((float(g_start), float(x)))

        if in_gutter and (x_max - g_start) >= 0.75 * self.mu_w:
            raw_gutters.append((float(g_start), float(x_max)))

        valid_gutters: List[Tuple[float, float]] = []
        for g0, g1 in raw_gutters:
            g_mid = 0.5 * (g0 + g1)
            left_lines = [l for l in non_image_wrapped if l["x1"] <= g1 + 2.5]
            right_lines = [l for l in non_image_wrapped if l["x0"] >= g0 - 2.5]
            if not left_lines or not right_lines:
                continue

            left_span_w = max(l["x1"] for l in left_lines) - min(l["x0"] for l in left_lines)
            right_span_w = max(l["x1"] for l in right_lines) - min(l["x0"] for l in right_lines)
            has_zone_v_rule = any(
                abs(g_mid - vr[0]) <= 4.0 and (min(zone_y1, vr[3]) - max(zone_y0, vr[1])) >= 2.0 * self.mu_h
                for vr in self.vertical_rules
            )
            if left_span_w >= 0.18 * self.page_width and right_span_w >= 0.18 * self.page_width:
                if (len(left_lines) >= 2 and len(right_lines) >= 2) or has_zone_v_rule:
                    if self.visual_inspector.verify_vertical_gutter(g0, g1, zone_y0, zone_y1):
                        valid_gutters.append((g0, g1))

        return valid_gutters

    def _detect_gutters(self, lines: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
        """Backwards-compatible wrapper for zone-level gutter detection."""
        for line in lines:
            if "is_image_wrapped" not in line:
                line["is_image_wrapped"] = self._is_wrapped_beside_image(line)
        return self._detect_zone_gutters(lines)

    def _order_column_with_image_wrap(self, col_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Orders blocks within a single column:
        1. Normalizes sort Y for horizontally non-overlapping segments on the same visual line so left segments precede right segments.
        2. Defers figure captions/callouts sitting beside narrow image-wrapped text lines until paragraph end.
        """
        if len(col_blocks) <= 1:
            return list(col_blocks)

        prelim = sorted(col_blocks, key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0)))
        line_sort_y: List[float] = []
        for idx, b in enumerate(prelim):
            bx0 = b.get("x0", 0.0)
            bx1 = b.get("x1", bx0)
            by0 = b.get("y0", 0.0)
            by1 = b.get("y1", by0)
            bh = max(1.0, by1 - by0)
            assigned_y = by0
            for prev_idx in range(idx - 1, -1, -1):
                pb = prelim[prev_idx]
                px0 = pb.get("x0", 0.0)
                px1 = pb.get("x1", px0)
                py0 = pb.get("y0", 0.0)
                py1 = pb.get("y1", py0)
                if by0 - py1 > 0.5 * self.mu_h:
                    break
                ph = max(1.0, py1 - py0)
                v_ov = min(by1, py1) - max(by0, py0)
                h_ov = min(bx1, px1) - max(bx0, px0)
                if h_ov <= 1.5 * self.mu_w and (v_ov > 0.40 * min(bh, ph) or abs(by0 - py0) <= 0.25 * self.mu_h):
                    assigned_y = line_sort_y[prev_idx]
                    break
            line_sort_y.append(assigned_y)

        sorted_items = [
            item for _, item in sorted(
                zip(line_sort_y, prelim),
                key=lambda pair: (pair[0], pair[1].get("x0", 0.0)),
            )
        ]

        wrapped_y_ranges: List[Tuple[float, float]] = []
        for b in sorted_items:
            if b.get("type", "text") == "text" and b.get("is_image_wrapped", False):
                wrapped_y_ranges.append((b["y0"] - 0.5 * self.mu_h, b["y1"] + 0.5 * self.mu_h))

        if not wrapped_y_ranges:
            return sorted_items

        wrapped_y_ranges.sort(key=lambda r: r[0])
        merged_ranges: List[List[float]] = [[wrapped_y_ranges[0][0], wrapped_y_ranges[0][1]]]
        for r0, r1 in wrapped_y_ranges[1:]:
            if r0 <= merged_ranges[-1][1] + 1.5 * self.mu_h:
                merged_ranges[-1][1] = max(merged_ranges[-1][1], r1)
            else:
                merged_ranges.append([r0, r1])

        result: List[Dict[str, Any]] = []
        deferred_floats: List[Tuple[float, Dict[str, Any]]] = []

        for item in sorted_items:
            b_type = item.get("type", "text")
            iy0 = item.get("y0", 0.0)

            still_deferred: List[Tuple[float, Dict[str, Any]]] = []
            for release_y, flt in deferred_floats:
                if iy0 > release_y:
                    result.append(flt)
                else:
                    still_deferred.append((release_y, flt))
            deferred_floats = still_deferred

            if b_type in {"caption", "callout"}:
                matched_release_y = None
                for rng0, rng1 in merged_ranges:
                    if rng0 <= iy0 <= rng1:
                        matched_release_y = rng1
                        break
                if matched_release_y is not None:
                    deferred_floats.append((matched_release_y, item))
                    continue

            result.append(item)

        for _, flt in deferred_floats:
            result.append(flt)

        return result

    @staticmethod
    def _compute_robust_col_bounds(
        col_lines: List[Dict[str, Any]], default_width: float
    ) -> Tuple[float, float]:
        """Computes outlier-resistant column left/right margins using 15th/85th percentiles."""
        if not col_lines:
            return 0.0, default_width
        x0s = sorted(b.get("x0", 0.0) for b in col_lines)
        x1s = sorted(b.get("x1", default_width) for b in col_lines)
        c_min_x = x0s[int(0.15 * (len(x0s) - 1))]
        c_max_x = x1s[int(0.85 * (len(x1s) - 1))]
        return c_min_x, max(c_min_x + 20.0, c_max_x)

    def _topological_dag_reading_order(self, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Orders blocks using a constrained Topological DAG:
        1. Flag lines wrapped beside raster/vector images.
        2. Slice into horizontal zones at true spanning elements.
        3. Detect column gutters locally per zone and keep image-wrapped text continuous.
        """
        non_text = self.tables_markdown + self.callout_blocks + self.caption_blocks
        if not lines:
            return sorted(non_text, key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0)))

        for line in lines:
            line["is_image_wrapped"] = self._is_wrapped_beside_image(line)

        raw_dividers: List[float] = [
            hr[1] for hr in self.horizontal_rules if (hr[2] - hr[0]) >= 0.55 * self.page_width
        ]

        for line in lines:
            line_w = line["x1"] - line["x0"]
            l_bbox = line.get("bbox", (line["x0"], line["y0"], line["x1"], line["y1"]))
            in_banner = self._is_in_header_banner(l_bbox)
            is_spanning_banner = in_banner and any(
                (hb[2] - hb[0]) >= 0.55 * self.page_width and _rects_overlap(l_bbox, hb)
                for hb in self.header_banners
            )
            is_heading_candidate = (
                line["size"] >= 1.12 * self.mu_size
                or ((line["is_bold"] or line.get("is_accent_color", False)) and line["size"] >= 1.0 * self.mu_size)
                or is_spanning_banner
                or line["text"].strip().upper().startswith("QUICK TEXT REVIEW")
            )
            if (line_w >= 0.62 * self.page_width or is_spanning_banner or line["text"].strip().upper().startswith("QUICK TEXT REVIEW")) and is_heading_candidate:
                raw_dividers.append(line["y0"] - 1.0)
                raw_dividers.append(line["y1"] + 1.0)

        for nt in non_text:
            nt_w = nt.get("x1", 0.0) - nt.get("x0", 0.0)
            if nt_w >= 0.62 * self.page_width:
                raw_dividers.append(nt["y0"] - 1.0)
                raw_dividers.append(nt["y1"] + 1.0)

        spanning_y_dividers: List[float] = []
        for y_val in sorted(raw_dividers):
            if not spanning_y_dividers or abs(y_val - spanning_y_dividers[-1]) > 4.0:
                spanning_y_dividers.append(y_val)

        all_blocks = list(lines) + list(non_text)
        all_blocks.sort(key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0)))

        zones: List[List[Dict[str, Any]]] = []
        current_zone: List[Dict[str, Any]] = []
        div_idx = 0

        for block in all_blocks:
            by0 = block.get("y0", 0.0)
            crossed = False
            while div_idx < len(spanning_y_dividers) and by0 >= spanning_y_dividers[div_idx]:
                crossed = True
                div_idx += 1
            if crossed and current_zone:
                zones.append(current_zone)
                current_zone = []
            current_zone.append(block)

        if current_zone:
            zones.append(current_zone)

        ordered_blocks: List[Dict[str, Any]] = []
        for zone in zones:
            gutters = self._detect_zone_gutters(zone)
            if not gutters or len(zone) <= 1:
                norm_lines = [
                    b for b in zone
                    if b.get("type", "text") == "text"
                    and not b.get("is_image_wrapped", False)
                    and not b.get("text", "").lstrip().startswith(":")
                ] or zone
                c_min_x, c_max_x = self._compute_robust_col_bounds(norm_lines, self.page_width)
                for b in zone:
                    b["col_idx"] = 0
                    b["col_min_x"] = c_min_x
                    b["col_max_x"] = c_max_x
                ordered_blocks.extend(self._order_column_with_image_wrap(zone))
            else:
                cols: Dict[int, List[Dict[str, Any]]] = {}
                for block in zone:
                    bx0 = block.get("x0", 0.0)
                    bx1 = block.get("x1", 0.0)
                    bx_mid = 0.5 * (bx0 + bx1)
                    col_idx = 0
                    for g_i, g in enumerate(gutters):
                        if bx0 >= g[0] - 1.5 * self.mu_w or bx_mid > g[1]:
                            col_idx = g_i + 1
                    block["col_idx"] = col_idx
                    cols.setdefault(col_idx, []).append(block)

                for c_k in sorted(cols.keys()):
                    col_items = cols[c_k]
                    norm_col_lines = [
                        b for b in col_items
                        if b.get("type", "text") == "text"
                        and not b.get("is_image_wrapped", False)
                        and not b.get("text", "").lstrip().startswith(":")
                    ] or col_items
                    c_min_x, c_max_x = self._compute_robust_col_bounds(norm_col_lines, self.page_width)
                    for b in col_items:
                        b["col_min_x"] = c_min_x
                        b["col_max_x"] = c_max_x
                    ordered_blocks.extend(self._order_column_with_image_wrap(col_items))

        return ordered_blocks

    def _is_prose_sentence_line(self, text: str, line_w: float, col_w: float, sz: float, in_banner: bool) -> bool:
        """
        Returns True if a mixed-case line has the grammatical or geometric signature of a body
        paragraph line rather than a standalone heading.
        """
        t = text.strip()
        if not t:
            return True

        alpha_chars = [c for c in t if c.isalpha()]
        if not alpha_chars:
            return True

        # Starts with lowercase letter or closing punctuation
        if not alpha_chars[0].isupper() or (t[0].isalpha() and t[0].islower()):
            return True
        if t[0] in (",", ";", ".", ":", ")", "]", "—", "–", "-"):
            return True

        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        is_all_caps = upper_ratio >= 0.85 and len(alpha_chars) >= 3
        if is_all_caps or in_banner:
            return False

        # Mixed-case lines containing finite verbs, quotes, semicolons, or trailing function words are prose
        if _PROSE_FINITE_VERBS_RE.search(t):
            return True
        if _TRAILING_FUNCTION_WORD_RE.search(t.rstrip(".,;:—–-")):
            return True
        if any(ch in t for ch in (";", "“", "”", '"')):
            return True
        if "," in t and sz < 1.15 * self.mu_size:
            return True

        # Full-column-width mixed-case line at body font size is a wrapped paragraph line
        if col_w > 0 and line_w >= 0.82 * col_w and sz <= 1.12 * self.mu_size:
            return True

        return False

    def _merge_wrapped_headings(self, ordered_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merges multi-line wrapped headings of identical typography (and color) before level classification
        so 2-line main headings are not split into separate H1/H2/H3 entries.
        """
        if not ordered_blocks:
            return []

        merged: List[Dict[str, Any]] = []
        i = 0
        n = len(ordered_blocks)

        list_start_re = re.compile(
            r"^([•▪▸◦‣⁃\-\*]|\d{1,3}[\.\)]|\((?:[0-9]{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,4})\))\s+"
        )

        while i < n:
            curr = dict(ordered_blocks[i])
            if curr.get("type", "text") != "text":
                merged.append(curr)
                i += 1
                continue

            curr_text = curr.get("text", "").strip()
            curr_sz = curr.get("size", self.mu_size)
            curr_bold = curr.get("is_bold", False)
            curr_italic = curr.get("is_italic", False)
            curr_accent = curr.get("is_accent_color", False)
            curr_lead_bold = bool(curr.get("lead_in_bold"))

            curr_bbox = curr.get("bbox", (curr.get("x0", 0), curr.get("y0", 0), curr.get("x1", 0), curr.get("y1", 0)))
            curr_in_banner = self._is_in_header_banner(curr_bbox)
            col_w = max(1.0, curr.get("col_max_x", self.page_width) - curr.get("col_min_x", 0.0))
            curr_w = curr_bbox[2] - curr_bbox[0]

            # Do not treat a line as a heading candidate if the previous line in the same column was an unended body sentence
            prev_unended_prose = False
            if merged and merged[-1].get("type", "text") == "text" and merged[-1].get("col_idx", 0) == curr.get("col_idx", 0):
                prev_b = merged[-1]
                prev_t = prev_b.get("text", "").rstrip()
                if not prev_b.get("is_bold") and not prev_b.get("is_accent_color") and not prev_t.isupper():
                    if not prev_t.endswith((".", "!", "?", ":")) and (curr_bbox[1] - prev_b.get("y1", 0.0)) <= 0.85 * self.mu_h:
                        prev_unended_prose = True

            is_candidate = (
                not prev_unended_prose
                and not curr_lead_bold
                and not (curr_italic and not curr_in_banner)
                and not list_start_re.match(curr_text)
                and not curr_text.startswith(":")
                and (curr_sz >= 1.15 * self.mu_size or curr_bold or curr_accent or curr_text.isupper() or curr_in_banner)
                and len(curr_text) <= 120
                and not curr_text.endswith((".", ";", ",", ":", "?", "!", "-", "—", "–"))
                and not self._is_prose_sentence_line(curr_text, curr_w, col_w, curr_sz, curr_in_banner)
            )

            if is_candidate:
                while i + 1 < n:
                    nxt = ordered_blocks[i + 1]
                    if nxt.get("type", "text") != "text":
                        break
                    if nxt.get("col_idx", 0) != curr.get("col_idx", 0):
                        break

                    nxt_text = nxt.get("text", "").strip()
                    nxt_sz = nxt.get("size", self.mu_size)
                    nxt_bold = nxt.get("is_bold", False)
                    nxt_italic = nxt.get("is_italic", False)
                    nxt_accent = nxt.get("is_accent_color", False)
                    nxt_bbox = nxt.get("bbox", (nxt.get("x0", 0), nxt.get("y0", 0), nxt.get("x1", 0), nxt.get("y1", 0)))
                    nxt_in_banner = self._is_in_header_banner(nxt_bbox)
                    nxt_w = nxt_bbox[2] - nxt_bbox[0]
                    v_gap = nxt.get("y0", 0.0) - curr.get("y1", 0.0)
                    v_pitch = nxt.get("y0", 0.0) - curr.get("y0", 0.0)

                    same_style = (
                        abs(curr_sz - nxt_sz) <= 0.8
                        and (curr_in_banner == nxt_in_banner)
                        and not (nxt_italic and not nxt_in_banner)
                        and (
                            (curr_bold == nxt_bold and curr_accent == nxt_accent)
                            or (curr_in_banner and nxt_in_banner)
                        )
                        and not nxt.get("lead_in_bold")
                        and not list_start_re.match(nxt_text)
                        and not nxt_text.startswith(":")
                        and (curr_text.isupper() == nxt_text.isupper())
                        and not self._is_prose_sentence_line(nxt_text, nxt_w, col_w, nxt_sz, nxt_in_banner)
                    )
                    tight_vertical = (
                        (-0.85 * max(curr_sz, self.mu_h) <= v_gap <= 0.85 * max(curr_sz, self.mu_h))
                        or (0.0 <= v_pitch <= 1.5 * max(curr_sz, self.mu_h))
                    )

                    if same_style and tight_vertical and len(curr_text) + len(nxt_text) <= 160:
                        curr_text = f"{curr_text} {nxt_text}"
                        curr["text"] = curr_text
                        curr_md = curr.get("md_text", "")
                        nxt_md = nxt.get("md_text", nxt_text)
                        curr["md_text"] = f"{curr_md} {nxt_md}".strip() if curr_md else curr_text
                        curr["x0"] = min(curr["x0"], nxt["x0"])
                        curr["y0"] = min(curr["y0"], nxt["y0"])
                        curr["x1"] = max(curr["x1"], nxt["x1"])
                        curr["y1"] = max(curr["y1"], nxt["y1"])
                        curr["bbox"] = (curr["x0"], curr["y0"], curr["x1"], curr["y1"])
                        i += 1
                        if curr_text.endswith((".", ";", "?", "!")):
                            break
                    else:
                        break

            merged.append(curr)
            i += 1

        return merged

    def _classify_heading_level(
        self,
        item: Dict[str, Any],
        prev_y1: Optional[float] = None,
        promote_headings: bool = False,
        active_stack: Optional[Dict[int, str]] = None,
        next_item: Optional[Dict[str, Any]] = None,
        prev_was_h3: bool = False,
        prev_in_open_para: bool = False,
    ) -> Optional[int]:
        """
        Determines exact heading hierarchy level (H1-H5) taking active heading stack depth into account:
        - Level 1 (H1): Chapter / Part / Main Article Title
        - Level 2 (H2): Section Review Banner / Main Section Heading (e.g. Quick Text Review, SOCIAL AND LEGAL ASPECTS...)
        - Level 3 (H3): Sub-section Banner / Primary All-Caps Section Heading (e.g. CONGENITAL ANOMALIES, HEARING LOSS AND DEAFNESS)
        - Level 4 (H4): Sub-subheading in All-Caps under H3 (e.g. CORNEAL ULCER) or Table/Figure Heading (e.g. Degree of hearing loss (Figure 5.8))
        - Level 5 (H5): Minor Mixed-Case Bold/Accent Sub-heading under H3/H4 (e.g. Megalocornea, Bacterial corneal ulcer)
        Returns None if the line is standard body text, a middle line of a paragraph, a run-in bold lead-in, or a list item.
        """
        l_text = item.get("text", "").strip()
        if not l_text or len(l_text) > 150 or len(l_text) < 2:
            return None

        # Never classify list items, colon-value segments, or pure numbers/citations as headings
        if re.match(
            r"^([•▪▸◦‣⁃\-\*]|\d{1,3}[\.\)]|\((?:[0-9]{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,4})\))\s+",
            l_text,
        ):
            return None
        if l_text.startswith(":"):
            return None
        if re.match(r"^[\d\s\.,\-–—\(\)\[\]]+$", l_text):
            return None

        # Never classify a line with a bold lead-in followed by non-bold prose on the same line
        if item.get("lead_in_bold") and item.get("rest_text"):
            return None

        sz = item.get("size", self.mu_size)
        is_bold = item.get("is_bold", False)
        is_italic = item.get("is_italic", False)
        is_accent = item.get("is_accent_color", False)
        bbox = item.get("bbox", (0.0, 0.0, 0.0, 0.0))
        line_w = bbox[2] - bbox[0]
        col_w = max(1.0, item.get("col_max_x", self.page_width) - item.get("col_min_x", 0.0))
        is_spanning = line_w >= 0.60 * self.page_width
        in_banner = self._is_in_header_banner(bbox)
        has_underline = self._has_underline_rule(bbox)

        # Reject italic/bold-italic lines unless inside a section banner
        if is_italic and not in_banner:
            return None

        alpha_chars = [c for c in l_text if c.isalpha()]
        if not alpha_chars:
            return None
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        is_all_caps = upper_ratio >= 0.85 and len(alpha_chars) >= 3

        # Never classify a continuation line of an unended paragraph sentence as a heading
        if prev_in_open_para and not in_banner and not is_all_caps:
            return None

        # Reject lines that have the grammatical or geometric structure of a prose sentence line
        if self._is_prose_sentence_line(l_text, line_w, col_w, sz, in_banner):
            return None

        # Guard against multi-word bold/accent phrases that wrap onto a lowercase continuation line
        if next_item is not None and next_item.get("type", "text") == "text":
            if next_item.get("col_idx", 0) == item.get("col_idx", 0):
                nxt_txt = next_item.get("text", "").strip()
                nxt_gap = next_item.get("y0", 0.0) - bbox[3]
                if (
                    nxt_txt
                    and nxt_txt[0].islower()
                    and not next_item.get("is_bold", False)
                    and not next_item.get("is_accent_color", False)
                    and -0.4 * self.mu_h <= nxt_gap <= 0.85 * self.mu_h
                    and not in_banner
                    and not has_underline
                    and not is_all_caps
                ):
                    return None

        deepest_lvl = max(active_stack.keys()) if active_stack else None
        has_h2 = bool(active_stack and 2 in active_stack)
        has_h3 = bool(active_stack and 3 in active_stack)
        has_h4 = bool(active_stack and 4 in active_stack)

        # 1. Ground-Truth PDF Table of Contents (TOC) Match
        norm_line = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", l_text.lower())).strip()
        for toc_lvl, toc_title in self.page_toc:
            norm_toc = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", toc_title.lower())).strip()
            if norm_toc and (norm_line == norm_toc or norm_line.startswith(norm_toc) or norm_toc.startswith(norm_line)):
                return min(max(int(toc_lvl), 1), 5)

        if l_text.upper().startswith("QUICK TEXT REVIEW"):
            return 2

        # 2. Explicit Structural & Numbered Section Prefixes
        if re.match(r"^(CHAPTER|PART|UNIT|MODULE|SECTION)\s+([0-9]+|[IVXLCDM]+)\b", l_text, re.IGNORECASE):
            return 1

        _MED_SECTION_HEADINGS = (
            "PATHOPHYSIOLOGY", "CLINICAL MANIFESTATIONS", "DIAGNOSIS AND TREATMENT",
            "ETIOLOGY AND RISK FACTORS", "ETIOLOGY", "MANAGEMENT", "OVERVIEW",
            "EPIDEMIOLOGY", "PREVENTION", "DIAGNOSTIC WORKUP", "COMPLICATIONS",
            "PATIENT EVALUATION", "TREATMENT", "CLINICAL FEATURES", "PATHOGENESIS",
            "PROGNOSIS", "DIFFERENTIAL DIAGNOSIS", "LABORATORY FINDINGS",
        )
        norm_upper_text = re.sub(r"\s+", " ", l_text.upper()).strip(" :.")
        if any(norm_upper_text == h_kw or norm_upper_text.startswith(h_kw) for h_kw in _MED_SECTION_HEADINGS):
            if is_bold or is_accent or sz >= 1.02 * self.mu_size or in_banner:
                return 2

        if is_bold or is_accent or sz >= 1.05 * self.mu_size or in_banner:
            if re.match(r"^\d{1,2}\.\d{1,2}\.\d{1,2}\.?\s+[A-Z]", l_text):
                return 4
            if re.match(r"^(\d{1,2}\.\d{1,2}\.?|[A-Z]\.)\s+[A-Z]", l_text):
                return 3
            if re.match(r"^([IVXLCDM]+\.|\d{1,2}\.)\s+[A-Z][A-Za-z]", l_text) and len(l_text) <= 95:
                if not l_text.endswith("."):
                    return 2

        if l_text.endswith((".", ";", ",", ":", "-", "—", "–")) and not in_banner:
            return None

        v_gap = (bbox[1] - prev_y1) if prev_y1 is not None else (1.5 * self.mu_h)
        has_top_spacing = v_gap >= 0.20 * self.mu_h

        tier_idx = None
        for idx, t_sz in enumerate(self.heading_size_tiers):
            if abs(sz - t_sz) <= 0.45:
                tier_idx = idx
                break

        # 3. H1: Chapter / Article Main Title
        if sz >= 1.40 * self.mu_size and deepest_lvl is None:
            return 1
        if sz >= 1.22 * self.mu_size and (is_spanning or bbox[1] < 0.22 * self.page_height) and tier_idx == 0 and deepest_lvl is None:
            return 1

        # 4. H2 / H3: Main Section & Sub-section Banners / All-Caps Headings
        if in_banner or (has_underline and is_all_caps):
            if has_h2:
                if self.h3_ref_size is None or sz > self.h3_ref_size:
                    self.h3_ref_size = sz
                return 3
            return 2 if (is_all_caps or sz >= 1.02 * self.mu_size or is_bold or is_spanning or len(l_text) <= 60) else 3

        if sz >= 1.18 * self.mu_size and (is_bold or is_accent or is_all_caps):
            if not has_h2 and (deepest_lvl is None or deepest_lvl < 2):
                return 2
            if self.h3_ref_size is None or sz > self.h3_ref_size:
                self.h3_ref_size = sz
            return 3

        if (is_bold or is_accent) and is_all_caps and len(l_text) <= 95:
            if not has_h2 and not has_h3 and sz >= 0.98 * self.mu_size:
                return 2
            if not has_h3:
                self.h3_ref_size = sz
                return 3
            if (
                not prev_was_h3
                and self.h3_ref_size is not None
                and sz >= self.h3_ref_size - 0.25
            ):
                return 3
            return 4

        if (is_bold or is_accent) and is_spanning and sz >= 1.05 * self.mu_size and len(l_text) <= 100:
            if not has_h2 and (deepest_lvl is None or deepest_lvl < 2):
                return 2
            return 3

        # 5. Mixed-Case Bold / Accent Subheadings (H3 / H4 / H5)
        if (is_bold or is_accent) and not is_all_caps and len(l_text) <= 90:
            is_table_title = bool(
                (next_item is not None and next_item.get("type") == "table")
                or re.search(r"\((?:Fig\.|Figure|Table)\s*[A-Za-z0-9\.\-]+\)$", l_text, re.I)
            )
            if is_table_title and (has_h2 or has_h3):
                return 4
            if has_h3 or has_h4:
                if sz >= 1.08 * self.mu_size and not has_h4:
                    return 4
                return 5
            if has_h2:
                return 3 if (sz >= 0.98 * self.mu_size and not is_italic) else 4
            if sz >= 0.95 * self.mu_size or promote_headings:
                return 3 if not is_italic else 4

        # 6. All-Caps non-bold heading with top spacing
        if is_all_caps and sz >= 0.98 * self.mu_size and len(l_text) <= 85 and has_top_spacing:
            return 3 if not has_h3 else 4

        return None

    def _coalesce_list_items(
        self,
        blocks: List[Dict[str, Any]],
        promote_headings: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Groups multi-line bullet/numbered list items and reconstructs two-column colon-aligned
        key-value bullet items ('• Key : Value') so wrapped lines and aligned ': Value' columns
        never fracture into disconnected paragraphs.
        """
        if not blocks:
            return []

        bullet_re = re.compile(
            r"^([•▪▸◦‣⁃\-\*]|\d{1,3}[\.\)]|\((?:[0-9]{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,4})\))\s+(.+)$"
        )
        sim_stack: Dict[int, str] = dict(self.initial_heading_stack)
        saved_h3_size = self.h3_ref_size

        coalesced: List[Dict[str, Any]] = []
        i = 0
        n = len(blocks)
        prev_was_h3 = False
        prev_prose_unended = False

        while i < n:
            curr = blocks[i]
            if curr.get("type", "text") != "text":
                coalesced.append(curr)
                prev_prose_unended = False
                i += 1
                continue

            c_text = curr.get("text", "").strip()
            m_bul = bullet_re.match(c_text)

            is_numbered_bold_run_in = bool(
                m_bul
                and re.match(r"^\d{1,3}[\.\)]$", m_bul.group(1))
                and curr.get("lead_in_bold")
                and curr.get("rest_text")
            )

            if not m_bul or is_numbered_bold_run_in:
                nxt_blk = blocks[i + 1] if (i + 1 < n) else None
                h_lvl = self._classify_heading_level(
                    curr,
                    promote_headings=promote_headings,
                    active_stack=sim_stack,
                    next_item=nxt_blk,
                    prev_was_h3=prev_was_h3,
                    prev_in_open_para=prev_prose_unended,
                )
                if h_lvl is not None:
                    sim_stack[h_lvl] = c_text
                    for k in [k for k in list(sim_stack.keys()) if k > h_lvl]:
                        del sim_stack[k]
                    prev_was_h3 = (h_lvl == 3)
                    prev_prose_unended = False
                else:
                    prev_was_h3 = False
                    prev_prose_unended = not c_text.rstrip().endswith((".", "!", "?", ":"))
                coalesced.append(curr)
                i += 1
                continue

            prev_was_h3 = False
            prev_prose_unended = False
            item_segs: List[Dict[str, Any]] = [dict(curr)]
            curr_col = curr.get("col_idx", 0)
            bullet_x0 = curr.get("x0", 0.0)
            max_y1 = curr.get("y1", 0.0)

            j = i + 1
            while j < n:
                nxt = blocks[j]
                if nxt.get("type", "text") != "text":
                    break
                if nxt.get("col_idx", 0) != curr_col:
                    break

                nxt_text = nxt.get("text", "").strip()
                if not nxt_text:
                    j += 1
                    continue

                if bullet_re.match(nxt_text):
                    break

                if not nxt_text.startswith(":"):
                    if nxt.get("lead_in_bold"):
                        break
                    after_nxt = blocks[j + 1] if (j + 1 < n) else None
                    last_seg_unended = not item_segs[-1].get("text", "").rstrip().endswith((".", "!", "?", ":"))
                    h_check = self._classify_heading_level(
                        nxt,
                        prev_y1=max_y1,
                        promote_headings=promote_headings,
                        active_stack=sim_stack,
                        next_item=after_nxt,
                        prev_was_h3=False,
                        prev_in_open_para=last_seg_unended,
                    )
                    if h_check is not None:
                        break

                v_gap = nxt.get("y0", 0.0) - max_y1
                if v_gap > 0.95 * self.mu_h:
                    break

                item_segs.append(dict(nxt))
                max_y1 = max(max_y1, nxt.get("y1", 0.0))
                j += 1

            self.h3_ref_size = saved_h3_size

            raw_marker = m_bul.group(1)
            col_min_x = curr.get("col_min_x", bullet_x0)
            indent_steps = max(0, min(3, int((bullet_x0 - col_min_x) / (2.2 * self.mu_w))))
            indent_str = "  " * indent_steps
            md_marker = raw_marker if re.match(r"^\d+[\.\)]$", raw_marker) else "-"

            strip_marker_re = re.compile(
                r"^(\*{1,2})?([•▪▸◦‣⁃\-\*]|\d{1,3}[\.\)]|\((?:[0-9]{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,4})\))[\s\u2002\u2003\u2009]+(.+)$",
                re.DOTALL,
            )

            colon_segs = [s for s in item_segs if s.get("text", "").lstrip().startswith(":")]
            if colon_segs:
                colon_x = min(s.get("x0", 0.0) for s in colon_segs)
                key_segs = [s for s in item_segs if s.get("x0", 0.0) < colon_x - 1.5 * self.mu_w]
                val_segs = [s for s in item_segs if s.get("x0", 0.0) >= colon_x - 1.5 * self.mu_w]

                key_md_lines = []
                for idx_k, ks in enumerate(key_segs):
                    k_md = ks.get("md_text") or ks.get("text", "")
                    if idx_k == 0:
                        k_md = strip_marker_re.sub(
                            lambda m: (m.group(1) or "") + m.group(3), k_md, count=1
                        ).strip()
                    key_md_lines.append(k_md)

                val_md_lines = []
                for vs in val_segs:
                    v_md = (vs.get("md_text") or vs.get("text", "")).strip()
                    v_md = re.sub(r"^:\s*", "", v_md).strip()
                    if v_md:
                        val_md_lines.append(v_md)

                key_str = clean_pdf_text("\n".join(key_md_lines)).replace("\n", " ").strip()
                val_str = clean_pdf_text("\n".join(val_md_lines)).replace("\n", " ").strip()
                combined_body_md = f"{key_str} : {val_str}" if val_str else key_str
            else:
                md_lines = []
                for idx_s, s_item in enumerate(item_segs):
                    s_md = s_item.get("md_text") or s_item.get("text", "")
                    if idx_s == 0:
                        s_md = strip_marker_re.sub(
                            lambda m: (m.group(1) or "") + m.group(3), s_md, count=1
                        ).strip()
                    md_lines.append(s_md)
                combined_body_md = clean_pdf_text("\n".join(md_lines)).replace("\n", " ").strip()
                if ":" in combined_body_md and combined_body_md.count(":") == 1 and not combined_body_md.endswith(":"):
                    left_p, right_p = combined_body_md.split(":", 1)
                    if len(left_p.strip()) <= 55 and len(right_p.strip()) <= 65:
                        combined_body_md = f"{left_p.strip()} : {right_p.strip()}"

            combined_bbox = (
                min(s.get("x0", 0.0) for s in item_segs),
                min(s.get("y0", 0.0) for s in item_segs),
                max(s.get("x1", 0.0) for s in item_segs),
                max(s.get("y1", 0.0) for s in item_segs),
            )

            merged_bullet = dict(curr)
            merged_bullet["type"] = "list_item"
            merged_bullet["indent_str"] = indent_str
            merged_bullet["md_marker"] = md_marker
            merged_bullet["list_body_md"] = combined_body_md
            merged_bullet["markdown"] = f"{indent_str}{md_marker} {combined_body_md}"
            merged_bullet["text"] = f"{raw_marker} {combined_body_md}"
            merged_bullet["md_text"] = merged_bullet["markdown"]
            merged_bullet["bbox"] = combined_bbox
            merged_bullet["x0"], merged_bullet["y0"], merged_bullet["x1"], merged_bullet["y1"] = combined_bbox
            coalesced.append(merged_bullet)
            i = j

        self.h3_ref_size = saved_h3_size
        return coalesced

    def _format_document(
        self, ordered_blocks: List[Dict[str, Any]], promote_headings: bool = False
    ) -> Dict[str, Any]:
        """
        Merges wrapped headings, coalesces multi-line and colon-aligned list items,
        classifies H1-H5 hierarchy levels and run-in headings, maintains a stateful
        parent-child heading stack, and formats clean Markdown with seamless paragraph
        continuation across inline figures and column breaks.
        """
        self.h3_ref_size = None
        blocks = self._merge_wrapped_headings(ordered_blocks)
        blocks = self._coalesce_list_items(blocks, promote_headings=promote_headings)

        output_entries: List[Tuple[str, str]] = []  # (block_kind, markdown_text)
        headings_found: List[str] = []
        hierarchy_tree: List[Dict[str, Any]] = []
        layout_boxes: List[Dict[str, Any]] = []

        active_stack: Dict[int, str] = dict(self.initial_heading_stack)

        current_paragraph: List[str] = []
        current_para_starts_bold_italic: bool = False
        para_bbox: Optional[List[float]] = None
        pending_floats: List[Dict[str, Any]] = []
        prev_line: Optional[Dict[str, Any]] = None
        prev_was_h3: bool = False
        order_counter = 1

        def get_current_breadcrumb() -> str:
            crumbs = [self.default_title]
            for lvl in sorted(active_stack.keys()):
                val = active_stack[lvl].strip()
                if val and val.lower() != crumbs[-1].lower():
                    crumbs.append(val)
            return " > ".join(crumbs)

        def update_heading_stack(level: int, title: str) -> str:
            clean_title = re.sub(r"\s+", " ", title).strip(" .:*")
            active_stack[level] = clean_title
            for deeper_lvl in [k for k in list(active_stack.keys()) if k > level]:
                del active_stack[deeper_lvl]
            headings_found.append(clean_title)
            b_path = get_current_breadcrumb()
            hierarchy_tree.append({
                "level": level,
                "tag": f"H{level}",
                "title": clean_title,
                "heading_path": b_path,
                "order": order_counter,
            })
            return clean_title

        def to_norm_bbox(b: Tuple[float, float, float, float]) -> List[float]:
            return [
                round(b[0] / self.page_width, 4),
                round(b[1] / self.page_height, 4),
                round(b[2] / self.page_width, 4),
                round(b[3] / self.page_height, 4),
            ]

        def flush_pending_floats() -> None:
            nonlocal pending_floats, order_counter
            while pending_floats:
                flt = pending_floats.pop(0)
                f_type = flt.get("type", "caption")
                f_bbox = flt.get("bbox", (0.0, 0.0, 0.0, 0.0))
                md_content = flt.get("markdown", "").strip()
                if md_content:
                    output_entries.append((f_type, md_content))
                layout_boxes.append({
                    "order": order_counter,
                    "type": f_type,
                    "bbox": to_norm_bbox(f_bbox),
                    "markdown": md_content,
                    "label": f"{f_type.title()} #{order_counter}",
                })
                order_counter += 1

        def flush_paragraph() -> None:
            nonlocal current_paragraph, current_para_starts_bold_italic, para_bbox, order_counter
            if current_paragraph:
                raw_joined = "\n".join(current_paragraph)
                cleaned = clean_pdf_text(raw_joined).replace("\n", " ").strip()
                if cleaned:
                    if cleaned.startswith("**Pathogens**") and " are:" in cleaned:
                        head_p, tail_p = cleaned.split(" are:", 1)
                        if "*" not in tail_p and tail_p.strip():
                            trailing_dot = "." if tail_p.rstrip().endswith(".") else ""
                            org_parts = [p.strip() for p in tail_p.rstrip(". ").split(",") if p.strip()]
                            if org_parts:
                                fmt_orgs = ", ".join(f"*{p}*" for p in org_parts)
                                cleaned = f"{head_p} are: {fmt_orgs}{trailing_dot}"

                    active_h3_title = active_stack.get(3, "").upper()
                    if "DEFINITION" in active_h3_title:
                        cleaned = re.sub(
                            r"^([“\"][^”\"]+[”\"])(\s+They\b)",
                            r"*\1*\2",
                            cleaned,
                        )
                    cleaned = re.sub(
                        r"^\*\*The\s+([^*]+)\*\*(\s+are\s+defined\s+as\b)",
                        r"The *\1*\2",
                        cleaned,
                    )

                    is_lead_bullet = (
                        current_para_starts_bold_italic
                        and cleaned.startswith("**")
                        and (5 in active_stack)
                        and not cleaned.endswith(":")
                    )
                    if is_lead_bullet:
                        output_entries.append(("list", f"- {cleaned}"))
                    else:
                        kind = "lead_colon" if (cleaned.startswith("**") and cleaned.endswith(":")) else "paragraph"
                        output_entries.append((kind, cleaned))

                    if para_bbox:
                        layout_boxes.append({
                            "order": order_counter,
                            "type": "list" if is_lead_bullet else "paragraph",
                            "bbox": to_norm_bbox(tuple(para_bbox)),
                            "label": f"{'List Item' if is_lead_bullet else 'Body'} #{order_counter}",
                        })
                        order_counter += 1
                current_paragraph = []
                current_para_starts_bold_italic = False
                para_bbox = None
            flush_pending_floats()

        run_in_regex = re.compile(
            r"^(\*\*([A-Za-z0-9\s\-/]{3,42}?)\*\*[\.:—\-]|"
            r"([A-Z][A-Za-z0-9\s\-/]{2,38})[\.:])\s+(.+)$"
        )

        for idx_b, item in enumerate(blocks):
            b_type = item.get("type", "text")
            bbox = item.get("bbox", (0.0, 0.0, 0.0, 0.0))
            norm_bbox = to_norm_bbox(bbox)

            if b_type in {"table", "callout", "caption"}:
                if (
                    b_type in {"caption", "callout"}
                    and current_paragraph
                    and prev_line is not None
                    and not prev_line["text"].rstrip().endswith((".", "!", "?", ":"))
                ):
                    pending_floats.append(item)
                    continue

                flush_paragraph()
                md_content = item.get("markdown", "").strip()
                if md_content:
                    output_entries.append((b_type, md_content))
                layout_boxes.append({
                    "order": order_counter,
                    "type": b_type,
                    "bbox": norm_bbox,
                    "markdown": md_content,
                    "label": f"{b_type.title()} #{order_counter}",
                })
                order_counter += 1
                prev_was_h3 = False
                continue

            if b_type == "list_item":
                flush_paragraph()
                md_content = item.get("markdown", "").strip()
                if md_content:
                    output_entries.append(("list", md_content))
                layout_boxes.append({
                    "order": order_counter,
                    "type": "list",
                    "bbox": norm_bbox,
                    "label": f"List Item #{order_counter}",
                })
                order_counter += 1
                prev_line = item
                prev_was_h3 = False
                continue

            l_text = item.get("text", "").strip()
            if not l_text:
                continue

            prev_y1 = prev_line["y1"] if (prev_line and prev_line.get("col_idx") == item.get("col_idx")) else None
            next_item = blocks[idx_b + 1] if (idx_b + 1 < len(blocks)) else None
            prev_in_open_para = bool(
                current_paragraph
                and prev_line is not None
                and prev_line.get("col_idx") == item.get("col_idx")
                and not prev_line["text"].rstrip().endswith((".", "!", "?", ":"))
                and (bbox[1] - prev_line["y1"]) <= 0.85 * self.mu_h
            )

            # 1. Check Standalone Heading Level (H1, H2, H3, H4, H5)
            h_level = self._classify_heading_level(
                item,
                prev_y1=prev_y1,
                promote_headings=promote_headings,
                active_stack=active_stack,
                next_item=next_item,
                prev_was_h3=prev_was_h3,
                prev_in_open_para=prev_in_open_para,
            )
            if h_level is not None:
                flush_paragraph()
                clean_h = update_heading_stack(h_level, l_text)
                alpha_h = [c for c in clean_h if c.isalpha()]
                h_all_caps = bool(alpha_h and (sum(1 for c in alpha_h if c.isupper()) / len(alpha_h)) >= 0.85)
                is_bold_heading = (
                    h_level == 5
                    or (h_level == 4 and (h_all_caps or item.get("is_accent_color", False)))
                )
                prefix = "#" * h_level
                if is_bold_heading:
                    output_entries.append(("heading", f"{prefix} **{clean_h}**"))
                else:
                    output_entries.append(("heading", f"{prefix} {clean_h}"))
                layout_boxes.append({
                    "order": order_counter,
                    "type": "heading",
                    "level": h_level,
                    "bbox": norm_bbox,
                    "label": f"H{h_level}: {clean_h[:32]}",
                })
                order_counter += 1
                prev_line = item
                prev_was_h3 = (h_level == 3)
                continue

            prev_was_h3 = False

            # 2. Check Medical Run-In Subheading (H4) at start of paragraph (excluding numbered bold run-ins and middle lines)
            lead_bold = item.get("lead_in_bold", "").strip() if isinstance(item.get("lead_in_bold"), str) else ""
            rest_txt = item.get("rest_text", "").strip() if isinstance(item.get("rest_text"), str) else ""
            is_numbered_lead = bool(re.match(r"^\d{1,3}[\.\)]\s*", lead_bold))
            run_in_title = ""
            run_in_body = ""

            if not prev_in_open_para and not is_numbered_lead:
                if lead_bold and rest_txt and 2 <= len(lead_bold) <= 45 and lead_bold.endswith((".", ":", "—", "-")):
                    run_in_title = lead_bold.rstrip(".:—- ").strip()
                    run_in_body = rest_txt
                else:
                    m_run = run_in_regex.match(l_text)
                    if m_run and (item.get("bold_ratio", 0.0) > 0.15 or l_text.startswith("**")) and lead_bold.endswith((".", ":", "—", "-")):
                        run_in_title = (m_run.group(2) or m_run.group(3) or "").strip(".:—- ")
                        run_in_body = m_run.group(4).strip()

            if run_in_title and run_in_body:
                flush_paragraph()
                clean_h = update_heading_stack(4, run_in_title)
                output_entries.append(("heading", f"#### {clean_h}"))
                layout_boxes.append({
                    "order": order_counter,
                    "type": "heading",
                    "level": 4,
                    "bbox": norm_bbox,
                    "label": f"H4 (Run-In): {clean_h[:28]}",
                })
                order_counter += 1
                current_paragraph = [run_in_body]
                current_para_starts_bold_italic = False
                para_bbox = [bbox[0], bbox[1], bbox[2], bbox[3]]
                prev_line = item
                continue

            # 3. Standard Body Paragraph Continuation vs New Paragraph Break
            if current_paragraph and prev_line is not None:
                same_col = item.get("col_idx", 0) == prev_line.get("col_idx", 0)
                v_gap = bbox[1] - prev_line["y1"]
                v_pitch = bbox[1] - prev_line["y0"]
                col_min_x = item.get("col_min_x", min(bbox[0], prev_line["x0"]))
                col_max_x = item.get("col_max_x", max(bbox[2], prev_line["x1"]))

                curr_wrapped = item.get("is_image_wrapped", False)
                prev_wrapped = prev_line.get("is_image_wrapped", False)

                # Two segments on the exact same horizontal line in the same column must always join
                v_overlap_prev = min(bbox[3], prev_line["y1"]) - max(bbox[1], prev_line["y0"])
                min_line_h = max(1.0, min(bbox[3] - bbox[1], prev_line["y1"] - prev_line["y0"]))
                h_overlap_prev = min(bbox[2], prev_line["x1"]) - max(bbox[0], prev_line["x0"])
                same_visual_line = same_col and h_overlap_prev <= 1.5 * self.mu_w and (v_overlap_prev > 0.35 * min_line_h or abs(bbox[1] - prev_line["y0"]) <= 0.25 * self.mu_h)

                bridged_over_image = False
                if same_col and v_gap > 1.1 * self.mu_h:
                    for img_r in self.image_bboxes:
                        if img_r[1] >= prev_line["y0"] - 1.5 * self.mu_h and img_r[3] <= bbox[3] + 1.5 * self.mu_h:
                            bridged_over_image = True
                            break

                prev_txt = re.sub(r"[\”\"\'\)\]]+$", "", prev_line["text"].rstrip())
                ends_sentence = prev_txt.endswith((".", "!", "?", ":"))
                starts_lower = bool(l_text and l_text[0].islower())
                starts_new_bold_lead = bool(
                    not same_visual_line
                    and ends_sentence
                    and (
                        is_numbered_lead
                        or ((5 in active_stack) and item.get("lead_is_bold_italic"))
                    )
                )

                large_v_gap = (v_gap > 1.05 * self.mu_h and v_pitch > 1.65 * self.mu_h) and not bridged_over_image

                eff_max_x = prev_line["x1"] if prev_wrapped else col_max_x
                prev_short_line = (
                    not same_visual_line
                    and ends_sentence
                    and not starts_lower
                    and (prev_line["x1"] < eff_max_x - 4.2 * self.mu_w)
                )

                # Confirm genuine first-line paragraph indent (indented relative to column margin AND either prev_line or next_item returns to margin)
                next_returns_to_margin = True
                if next_item is not None and next_item.get("type", "text") == "text" and next_item.get("col_idx") == item.get("col_idx"):
                    if (next_item.get("y0", 0.0) - bbox[3]) <= 0.85 * self.mu_h:
                        next_returns_to_margin = (bbox[0] - next_item.get("x0", 0.0)) >= 0.60 * self.mu_w

                first_line_indent = (
                    not same_visual_line
                    and not curr_wrapped
                    and not prev_wrapped
                    and ends_sentence
                    and (bbox[0] - col_min_x) >= 0.75 * self.mu_w
                    and ((bbox[0] - prev_line["x0"]) >= 0.70 * self.mu_w or next_returns_to_margin)
                    and next_returns_to_margin
                )

                cross_col_continuation = bool(not ends_sentence and not same_col and (starts_lower or not item.get("is_bold")))

                force_join = (
                    same_visual_line
                    or (
                        not ends_sentence
                        and (
                            cross_col_continuation
                            or (
                                same_col
                                and (starts_lower or curr_wrapped or prev_wrapped or bridged_over_image or v_gap <= 1.85 * self.mu_h)
                            )
                        )
                    )
                )

                if starts_new_bold_lead or (not force_join and (not same_col or large_v_gap or prev_short_line or first_line_indent)):
                    flush_paragraph()

            line_md = item.get("md_text") or l_text
            if not current_paragraph:
                current_para_starts_bold_italic = bool(item.get("lead_is_bold_italic"))
            current_paragraph.append(line_md)
            if para_bbox is None:
                para_bbox = [bbox[0], bbox[1], bbox[2], bbox[3]]
            else:
                para_bbox[0] = min(para_bbox[0], bbox[0])
                para_bbox[1] = min(para_bbox[1], bbox[1])
                para_bbox[2] = max(para_bbox[2], bbox[2])
                para_bbox[3] = max(para_bbox[3], bbox[3])

            prev_line = item

        flush_paragraph()

        # Assemble final markdown joining consecutive list items with single newline '\n'
        md_chunks: List[str] = []
        for idx_e, (kind, text_val) in enumerate(output_entries):
            if idx_e == 0:
                md_chunks.append(text_val)
                continue
            prev_kind = output_entries[idx_e - 1][0]
            if kind == "list" and prev_kind in {"list", "lead_colon"}:
                md_chunks.append("\n" + text_val)
            else:
                md_chunks.append("\n\n" + text_val)

        final_md = "".join(md_chunks).strip()
        final_heading_path = get_current_breadcrumb()
        deepest_level = max(active_stack.keys()) if active_stack else None
        subheading = active_stack[deepest_level] if deepest_level is not None else self.default_title
        main_heading = active_stack.get(1) or active_stack.get(2) or self.default_title

        return {
            "text": final_md,
            "extracted_text": final_md,
            "main_heading": main_heading,
            "heading_path": final_heading_path,
            "subheading": subheading,
            "headings": headings_found,
            "hierarchy_tree": hierarchy_tree,
            "active_heading_stack": active_stack,
            "layout_boxes": layout_boxes,
        }


def _get_docling_converter(
    do_ocr: bool = True,
    ocr_engine: Optional[str] = "auto",
    force_full_page_ocr: bool = False,
    **kwargs: Any,
) -> Any:
    """
    Initializes and caches a CPU-only Docling DocumentConverter instance (using Heron RT-DETRv2 layout model).
    Explicitly routes to RapidOcrOptions (PP-OCR ONNX Runtime) or EasyOcrOptions based on `ocr_engine`
    and sets `force_full_page_ocr=True` when an OCR engine is explicitly selected by the user.
    """
    if not _DOCLING_AVAILABLE:
        return None

    raw_engine = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocr_engine") or ocr_engine or "auto")
    engine_pref = str(raw_engine).strip().lower()
    cache_key = (bool(do_ocr), engine_pref, bool(force_full_page_ocr))
    if cache_key in _DOCLING_CONVERTERS:
        return _DOCLING_CONVERTERS[cache_key]

    if HF_CACHE_DIR:
        os.environ["HF_HOME"] = str(HF_CACHE_DIR)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)

    if is_docling_models_downloaded():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    accelerator = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
    pipeline_options = PdfPipelineOptions()
    pipeline_options.accelerator_options = accelerator
    pipeline_options.do_ocr = bool(do_ocr)
    pipeline_options.images_scale = 2.0  # 144 DPI for dense multi-column recognition
    pipeline_options.do_table_structure = True

    if do_ocr:
        if engine_pref in ("easyocr", "easy_ocr", "easy") and EasyOcrOptions is not None:
            try:
                ocr_opts = EasyOcrOptions(force_full_page_ocr=bool(force_full_page_ocr))
            except Exception:
                ocr_opts = EasyOcrOptions()
                if hasattr(ocr_opts, "force_full_page_ocr"):
                    ocr_opts.force_full_page_ocr = bool(force_full_page_ocr)
            pipeline_options.ocr_options = ocr_opts
            logger.info("Docling v3 configured with EasyOcrOptions (force_full_page_ocr=%s)", force_full_page_ocr)
        elif RapidOcrOptions is not None:
            try:
                ocr_opts = RapidOcrOptions(force_full_page_ocr=bool(force_full_page_ocr))
            except Exception:
                ocr_opts = RapidOcrOptions()
                if hasattr(ocr_opts, "force_full_page_ocr"):
                    ocr_opts.force_full_page_ocr = bool(force_full_page_ocr)
            pipeline_options.ocr_options = ocr_opts
            logger.info("Docling v3 configured with RapidOcrOptions / PP-OCR ONNX (force_full_page_ocr=%s)", force_full_page_ocr)

    try:
        pipeline_options.table_structure_options.do_cell_matching = True
    except Exception:
        pass

    try:
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    except Exception as exc:
        if os.environ.get("HF_HUB_OFFLINE") == "1":
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        else:
            raise exc

    _DOCLING_CONVERTERS[cache_key] = converter
    # Maintain backwards-compatible boolean key lookup
    _DOCLING_CONVERTERS[bool(do_ocr)] = converter
    return converter


def _serialize_docling_ast(doc: Any, hierarchy_tree: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Serializes a DoclingDocument AST into Markdown while calibrating H1-H5 levels
    (and promoting colored/typographic headings) using AdaptivePdfLayoutEngine's hierarchy tree.
    """
    level_lookup: Dict[str, int] = {}
    for h_node in (hierarchy_tree or []):
        norm_title = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(h_node.get("title", "")).lower())).strip()
        if norm_title:
            level_lookup[norm_title] = int(h_node.get("level", 2))

    blocks: List[Tuple[str, str]] = []
    try:
        from docling.datamodel.document import ContentLayer, DocItemLabel, PictureItem, TableItem
        iterator = doc.iterate_items(included_content_layers={ContentLayer.BODY})
    except Exception:
        iterator = []

    for item, level in iterator:
        label = getattr(item, "label", None)
        text_val = getattr(item, "text", "").strip() if hasattr(item, "text") else ""

        if label in {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}:
            continue

        if label == DocItemLabel.TITLE and text_val:
            blocks.append(("heading", f"# {text_val}"))
        elif label == DocItemLabel.SECTION_HEADER and text_val:
            norm_t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text_val.lower())).strip()
            calibrated_lvl = level_lookup.get(norm_t)
            if calibrated_lvl is None:
                for k_norm, k_lvl in level_lookup.items():
                    if k_norm in norm_t or norm_t in k_norm:
                        calibrated_lvl = k_lvl
                        break
            if calibrated_lvl is None:
                # Reject false section headers from Docling that are actually middle lines of a prose sentence
                if _PROSE_FINITE_VERBS_RE.search(text_val) or _TRAILING_FUNCTION_WORD_RE.search(text_val.rstrip(".,;:")):
                    blocks.append(("paragraph", clean_pdf_text(text_val).replace("\n", " ")))
                    continue
                if re.match(r"^(CHAPTER|PART|SECTION|UNIT)\s+", text_val, re.I):
                    calibrated_lvl = 1
                elif text_val.isupper() or re.match(r"^([IVXLCDM]+\.|\d{1,2}\.)\s+", text_val):
                    calibrated_lvl = 2
                elif re.match(r"^\d{1,2}\.\d{1,2}\.?\s+", text_val):
                    calibrated_lvl = 3
                else:
                    calibrated_lvl = min(max(int(level) + 1, 2), 5)

            clean_h = text_val.strip(" *")
            if calibrated_lvl == 5 or (calibrated_lvl == 4 and clean_h.isupper()):
                blocks.append(("heading", f"{'#' * calibrated_lvl} **{clean_h}**"))
            else:
                blocks.append(("heading", f"{'#' * calibrated_lvl} {clean_h}"))
        elif label == DocItemLabel.FORMULA and text_val:
            blocks.append(("formula", f"$$\n{text_val}\n$$"))
        elif label == DocItemLabel.LIST_ITEM and text_val:
            indent = "  " * max(0, int(level) - 1)
            marker = getattr(item, "marker", "-") or "-"
            md_marker = marker if re.match(r"^\d+[\.\)]$", str(marker)) else "-"
            blocks.append(("list", f"{indent}{md_marker} {clean_pdf_text(text_val).replace(chr(10), ' ')}"))
        elif label == DocItemLabel.TABLE:
            cap = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
            if cap:
                blocks.append(("caption", f"> *{cap}*"))
            if hasattr(item, "export_to_markdown"):
                blocks.append(("table", item.export_to_markdown(doc=doc).strip()))
        elif label == DocItemLabel.PICTURE:
            cap = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
            if cap:
                blocks.append(("caption", f"> **[Figure]** *{cap}*"))
        elif label == DocItemLabel.CAPTION and text_val:
            blocks.append(("caption", f"> *{clean_pdf_text(text_val).replace(chr(10), ' ')}*"))
        elif label == DocItemLabel.FOOTNOTE and text_val:
            blocks.append(("footnote", f"> ^[Footnote]: {clean_pdf_text(text_val).replace(chr(10), ' ')}"))
        elif text_val:
            norm_t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text_val.lower())).strip()
            if len(text_val) <= 110 and norm_t in level_lookup:
                lvl = level_lookup[norm_t]
                clean_h = text_val.strip(" *")
                if lvl == 5 or (lvl == 4 and clean_h.isupper()):
                    blocks.append(("heading", f"{'#' * lvl} **{clean_h}**"))
                else:
                    blocks.append(("heading", f"{'#' * lvl} {clean_h}"))
            else:
                blocks.append(("paragraph", clean_pdf_text(text_val).replace("\n", " ")))

    out_chunks: List[str] = []
    for idx, (kind, val) in enumerate(blocks):
        if not val:
            continue
        if not out_chunks:
            out_chunks.append(val)
        elif kind == "list" and blocks[idx - 1][0] == "list":
            out_chunks.append("\n" + val)
        else:
            out_chunks.append("\n\n" + val)
    return "".join(out_chunks)


def extract_page_with_docling_v3(
    file_path: Union[str, Path],
    page_number: int,
    default_title: str = "",
    initial_heading_stack: Optional[Dict[int, str]] = None,
    page_toc: Optional[List[Tuple[int, str]]] = None,
    ocr_engine: str = "auto",
    force_ocr: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Docling v3 Memory-Safe Single-Page Extraction:
    - Slices requested page into a temporary 1-page PDF buffer wrapped in DocumentStream
    - Runs visual pixmap inspection & selective line-crop re-OCR in AdaptivePdfLayoutEngine
    - Actively honors explicit `ocr_engine` selections ('paddle'/'rapidocr' or 'easyocr') by setting
      `do_ocr=True` and `force_full_page_ocr=True` so Docling runs the selected OCR engine over the page
    """
    norm_path = str(Path(file_path).resolve())
    title = default_title or Path(norm_path).stem
    raw_engine = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocr_engine") or ocr_engine or "auto")
    engine_pref = str(raw_engine).strip().lower()

    adaptive_res = extract_page_with_adaptive_layout(
        norm_path,
        page_number,
        default_title=title,
        initial_heading_stack=initial_heading_stack,
        page_toc=page_toc,
        ocr_engine=engine_pref,
    )

    if not _DOCLING_AVAILABLE or DocumentStream is None:
        logger.info("Docling library not installed; cleanly falling back to Adaptive Visual Layout Engine.")
        return adaptive_res

    try:
        with _FITZ_LOCK:
            with fitz.open(norm_path) as doc:
                page_idx = page_number - 1
                if page_idx < 0 or page_idx >= doc.page_count:
                    return adaptive_res

                page = doc[page_idx]
                native_text = page.get_text("text").strip()
                explicit_ocr_requested = bool(force_ocr) or engine_pref in (
                    "paddle", "paddleocr", "ppocr", "ppocr_v4", "ppocr_v5", "ppocr_v6", "rapidocr", "easyocr", "easy_ocr", "easy"
                )
                do_ocr = (len(native_text) < 40) or explicit_ocr_requested

                # Only short-circuit to adaptive_res when NO explicit OCR engine was forced AND native text is present
                if not do_ocr and adaptive_res.get("extracted_text", "").strip():
                    return adaptive_res

                single_doc = fitz.open()
                single_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
                pdf_bytes = single_doc.write()
                single_doc.close()

        converter = _get_docling_converter(
            do_ocr=do_ocr,
            ocr_engine=engine_pref,
            force_full_page_ocr=explicit_ocr_requested,
        )
        if converter is None:
            return adaptive_res

        if is_docling_models_downloaded():
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"

        doc_input = DocumentStream(name=f"page_{page_number}.pdf", stream=io.BytesIO(pdf_bytes))
        conv_res = converter.convert(doc_input)
        raw_docling_md = _serialize_docling_ast(
            conv_res.document,
            hierarchy_tree=adaptive_res.get("hierarchy_tree", []),
        )

        adaptive_md = adaptive_res.get("extracted_text", "")
        if not raw_docling_md or len(raw_docling_md.strip()) < 0.25 * len(adaptive_md):
            logger.info("Docling v3 output sparse; augmenting with adaptive visual multi-column recovery.")
            return adaptive_res

        formatted_md = clean_pdf_text(raw_docling_md)
        top_callouts = [
            b.get("markdown", "")
            for b in adaptive_res.get("layout_boxes", [])
            if b.get("type") == "callout"
            and b.get("bbox", [0, 0, 0, 0])[1] <= 0.25
            and b.get("markdown", "")[:30] not in formatted_md
        ]
        bottom_callouts = [
            b.get("markdown", "")
            for b in adaptive_res.get("layout_boxes", [])
            if b.get("type") == "callout"
            and b.get("bbox", [0, 0, 0, 0])[1] > 0.25
            and b.get("markdown", "")[:30] not in formatted_md
        ]
        if top_callouts:
            formatted_md = "\n\n".join(top_callouts) + "\n\n" + formatted_md
        if bottom_callouts:
            formatted_md += "\n\n" + "\n\n".join(bottom_callouts)

        return {
            "text": formatted_md,
            "extracted_text": formatted_md,
            "main_heading": adaptive_res.get("main_heading", title),
            "heading_path": adaptive_res.get("heading_path", title),
            "subheading": adaptive_res.get("subheading", title),
            "headings": adaptive_res.get("headings", []),
            "hierarchy_tree": adaptive_res.get("hierarchy_tree", []),
            "active_heading_stack": adaptive_res.get("active_heading_stack", {}),
            "layout_boxes": adaptive_res.get("layout_boxes", []),
            "page_image_base64": adaptive_res.get("page_image_base64", ""),
            "page_width": adaptive_res.get("page_width", 612.0),
            "page_height": adaptive_res.get("page_height", 792.0),
        }
    except Exception as exc:
        logger.warning("Docling v3 conversion encountered exception: %s. Falling back to adaptive engine.", exc)
        return adaptive_res


# Interoperable alias so callers invoking extract_page_with_docling_v2 on docling_v3_parser work seamlessly
extract_page_with_docling_v2 = extract_page_with_docling_v3


def extract_page_with_adaptive_layout(
    file_path: Union[str, Path],
    page_number: int,
    default_title: str = "",
    promote_headings: bool = False,
    initial_heading_stack: Optional[Dict[int, str]] = None,
    page_toc: Optional[List[Tuple[int, str]]] = None,
    ocr_engine: str = "auto",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Helper executing the Adaptive Visual Multi-Column Engine on a specific PDF page, including Base64 PNG pixmap."""
    if fitz is None:
        logger.warning("PyMuPDF (fitz) is not installed; cannot execute adaptive layout engine.")
        return {
            "text": "",
            "extracted_text": "",
            "main_heading": default_title or "Untitled",
            "heading_path": default_title or "Untitled",
            "subheading": default_title or "Untitled",
            "page_image_base64": "",
            "layout_boxes": [],
            "hierarchy_tree": [],
            "active_heading_stack": dict(initial_heading_stack or {}),
        }

    norm_path = str(Path(file_path).resolve())
    title = default_title or Path(norm_path).stem
    raw_engine = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocr_engine") or ocr_engine or "auto")
    engine_pref = str(raw_engine).strip().lower()

    with _FITZ_LOCK:
        with fitz.open(norm_path) as doc:
            if doc.is_encrypted:
                try:
                    doc.authenticate("")
                except Exception:
                    pass

            page_idx = page_number - 1
            if page_idx < 0 or page_idx >= doc.page_count:
                return {
                    "text": "",
                    "extracted_text": "",
                    "main_heading": title,
                    "heading_path": title,
                    "subheading": title,
                    "page_image_base64": "",
                    "layout_boxes": [],
                    "hierarchy_tree": [],
                    "active_heading_stack": dict(initial_heading_stack or {}),
                }

            page = doc[page_idx]
            pix = None

            try:
                pix = page.get_pixmap(dpi=150, alpha=False)
                png_bytes = pix.tobytes("png")
                page_image_b64 = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
            except Exception as img_err:
                logger.debug("Page pixmap generation error: %s", img_err)
                page_image_b64 = ""

            engine = AdaptivePdfLayoutEngine(
                page,
                default_title=title,
                initial_heading_stack=initial_heading_stack,
                page_toc=page_toc,
                pixmap=pix,
                ocr_engine=engine_pref,
            )
            result = engine.run(promote_headings=promote_headings)
            result["page_image_base64"] = page_image_b64
            result["page_width"] = float(page.rect.width)
            result["page_height"] = float(page.rect.height)
            return result


def extract_structured_layout(
    file_path: Union[str, Path],
    page_number: int,
    ocr_engine: str = "auto",
    **kwargs: Any,
) -> str:
    """Convenience layout extractor returning Markdown text using Docling v3 (forwards ocr_engine)."""
    res = extract_page_with_docling_v3(file_path, page_number, ocr_engine=ocr_engine, **kwargs)
    return res.get("extracted_text", "") or res.get("text", "")


def extract_pdf_structure(
    file_path: Union[str, Path],
    ocr_engine: str = "auto",
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Extracts structured sections from a PDF for Tantivy BM25 and LanceDB indexing using Docling v3.
    Conforms strictly to the Section 4 ChunkDict contract.
    """
    if not file_path:
        return []

    norm_path = str(Path(file_path).resolve())
    path_obj = Path(norm_path)
    if not path_obj.is_file():
        logger.warning("Target PDF file does not exist: %s", norm_path)
        return []

    default_title = path_obj.stem
    sections: List[Dict[str, Any]] = []

    if fitz is None:
        return []

    try:
        toc_by_page: Dict[int, List[Tuple[int, str]]] = {}
        with fitz.open(norm_path) as doc:
            total_pages = doc.page_count
            try:
                for lvl, title_str, p_num in doc.get_toc():
                    if p_num >= 1 and title_str:
                        toc_by_page.setdefault(int(p_num), []).append((int(lvl), str(title_str).strip()))
            except Exception:
                pass

        running_stack: Dict[int, str] = {}
        sec_idx = 0
        for p_num in range(1, total_pages + 1):
            p_res = extract_page_with_docling_v3(
                norm_path,
                p_num,
                default_title=default_title,
                initial_heading_stack=running_stack,
                page_toc=toc_by_page.get(p_num, []),
                ocr_engine=ocr_engine,
                **kwargs,
            )
            running_stack = p_res.get("active_heading_stack", running_stack)
            text_content = str(p_res.get("extracted_text", "") or p_res.get("text", "")).strip()
            if not text_content:
                continue

            h_path = str(p_res.get("heading_path", default_title))
            subheading = str(p_res.get("subheading", default_title))
            art_len = len(text_content)
            snippet = text_content[:400] + ("..." if art_len > 400 else "")
            raw_key = f"{norm_path}_{p_num}_{sec_idx}_{text_content[:50]}"
            chunk_id = hashlib.md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()
            sec_idx += 1

            sections.append({
                "id": chunk_id,
                "doc_id": chunk_id,
                "page": p_num,
                "page_number": p_num,
                "title": default_title,
                "source_article": default_title,
                "heading": h_path,
                "heading_path": h_path,
                "subheading": subheading,
                "text": text_content,
                "snippet": snippet,
                "source": "pdf",
                "file_path": norm_path,
                "length": art_len,
                "article_length": art_len,
                "doc_length": art_len,
                "score": 0.0,
                "rerank_score": 0.0,
                "parent_id": None,
            })

        return sections
    except Exception as exc:
        logger.error("Docling v3 PDF structure extraction failed for %s: %s", norm_path, exc)
        return []