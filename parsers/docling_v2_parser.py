"""
Adaptive Multi-Column Medical Layout Engine for PyMuPDF (fitz) and Docling v2 Hybrid Recovery.
Implements per-page typography & modal color profiling, vector ruling barriers, raster image obstacle
detection, shaded callout isolation, multi-line & under-image caption tethering, local zone-based
XY-cut column gutter detection, image-wrapped paragraph continuation, multi-tier hierarchy detection
(H1-H4 + colored headings + run-in headings), PDF page pixmap rendering (base64 PNG), and
Docling v2 (Heron RT-DETRv2) memory-safe single-page extraction.
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

# Docling imports (lazy / optional to prevent startup failure if not installed)
_DOCLING_AVAILABLE = False
DocumentStream = None
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
except Exception:
    _DOCLING_AVAILABLE = False

logger = logging.getLogger(__name__)

_FITZ_LOCK = threading.RLock()
_DOCLING_CONVERTERS: Dict[bool, Any] = {}

DOCLING_REQUIRED_MODELS = (
    "docling-project/docling-layout-heron",
    "docling-project/docling-layout-heron-onnx",
    "docling-project/docling-models",
)


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
    "\u200b": "",
    "\u200e": "",
    "\u200f": "",
    "\ufeff": "",
    "\u00ad": "",
}

_MEDICAL_PREFIXES = (
    "anti", "non", "pre", "post", "sub", "intra", "extra",
    "hyper", "hypo", "auto", "co", "re", "beta", "alpha", "micro", "macro"
)


def clean_pdf_text(text: str) -> str:
    """Sanitizes raw PDF text for Tantivy BM25 indexing and LanceDB FTS."""
    if not text:
        return ""

    t = text.replace("\x00", "").replace("\x0c", "\n")
    for lig, rep in _LIGATURE_MAP.items():
        if lig in t:
            t = t.replace(lig, rep)

    t = t.replace("\r\n", "\n").replace("\r", "\n")

    # De-hyphenation aware of medical compound prefixes
    def _dehyphen_match(match: re.Match) -> str:
        prefix, suffix = match.group(1), match.group(2)
        if prefix.lower() in _MEDICAL_PREFIXES:
            return f"{prefix}-{suffix}"
        return f"{prefix}{suffix}"

    t = re.sub(r"([A-Za-z]{2,})-\n([a-z]{2,})", _dehyphen_match, t)
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


class AdaptivePdfLayoutEngine:
    """
    Deterministic Adaptive Multi-Column Medical Layout Engine with Visual Context:
    1. Per-page typography baseline (mu_w, mu_h, mu_size, body_color) and heading size tiers
    2. Vector graphic rulings (vertical barriers, horizontal zone splitters, header/footer rules, banner frames)
    3. Raster & block image obstacle detection for wrapped-text continuity
    4. Structural element classification (tables, callout boxes, multi-line & under-image figure captions)
    5. Local zone-based XY-cut column gutter solver with image-wrap protection
    6. Multi-tier hierarchy depth recognition (H1-H4 + accent-colored headings + medical run-in headings)
    """

    def __init__(
        self,
        page: Any,
        default_title: str = "",
        initial_heading_stack: Optional[Dict[int, str]] = None,
        page_toc: Optional[List[Tuple[int, str]]] = None,
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

        # Typography & Color Profile Defaults
        self.mu_size: float = 10.0
        self.mu_h: float = 12.0
        self.mu_w: float = 5.0
        self.body_color: int = 0
        self.heading_size_tiers: List[float] = []

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

        # 2. Extract vector rulings & background frames (and refine header/footer marginal exclusions)
        self._extract_rulings_and_frames(drawings, spans)

        # 3. Isolate tables & shaded clinical callout boxes
        self._isolate_tables_and_callouts(spans, drawings)

        # 4. Filter out excluded spans (tables, callouts, marginal headers/footers)
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

    def _profile_page(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extracts text spans, raster image obstacles, and drawing instructions, profiling body typography."""
        try:
            page_dict = self.page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
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
                    s_text = str(span.get("text", "")).strip()
                    if not s_text:
                        continue
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
                        or any(k in font_lower for k in ("bold", "black", "heavy", "demi", "semibold", "-bd", "medi", "hebo"))
                        or font_lower.endswith(("bo", "bd", "blk"))
                    )

                    # Running header/footer exclusion (top/bottom margins + standalone page numbers)
                    in_top_band = y1 <= self.page_height * 0.08
                    in_bottom_band = y0 >= self.page_height * 0.92
                    is_page_num = bool(
                        re.match(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$|^[ivxlcdm]+$", s_text, re.IGNORECASE)
                    )
                    is_heading_like = (
                        is_bold
                        or (color != 0 and color != self.body_color)
                        or sz >= 1.05 * self.mu_size
                        or s_text.isupper()
                        or bool(re.match(r"^(CHAPTER|PART|SECTION|UNIT|MODULE)\b", s_text, re.IGNORECASE))
                    )
                    is_marginal = (
                        (y0 < self.page_height * 0.035 and not is_heading_like)
                        or (y1 > self.page_height * 0.965 and not is_heading_like)
                        or ((in_top_band or in_bottom_band) and is_page_num)
                        or ((in_top_band or in_bottom_band) and not is_heading_like and sz <= 1.02 * self.mu_size and len(s_text) <= 95)
                    )

                    span_dict = {
                        "text": s_text,
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

        # Un-flag genuine chapter/article titles and section headings near the top or bottom of the page
        for s in all_spans:
            if s["is_marginal"]:
                if s["size"] >= 1.05 * self.mu_size or s["is_bold"] or self._is_accent_color(s.get("color", 0)):
                    if not re.match(r"^\d+$", s["text"]):
                        s["is_marginal"] = False

        self._compute_heading_size_tiers(all_spans)

        return all_spans, drawings

    def _extract_rulings_and_frames(
        self, drawings: List[Dict[str, Any]], spans: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Extracts vertical ruling lines, horizontal zone/margin dividers, and section header banners."""
        top_header_rule_y: Optional[float] = None
        bottom_footer_rule_y: Optional[float] = None

        for dw in drawings:
            rect = dw.get("rect")
            fill = dw.get("fill")
            if not rect:
                continue
            rx0, ry0, rx1, ry1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
            rw = rx1 - rx0
            rh = ry1 - ry0

            # Vertical ruling line (barrier between columns)
            if rw <= 3.5 and rh >= 3.5 * self.mu_h:
                self.vertical_rules.append((rx0, ry0, rx1, ry1))

            # Horizontal ruling line (zone boundary or header/footer separator)
            elif rh <= 3.5 and rw >= 0.20 * self.page_width:
                self.horizontal_rules.append((rx0, ry0, rx1, ry1))
                if rw >= 0.45 * self.page_width:
                    if ry1 <= self.page_height * 0.095:
                        top_header_rule_y = max(top_header_rule_y or 0.0, ry1)
                    elif ry0 >= self.page_height * 0.905:
                        bottom_footer_rule_y = min(bottom_footer_rule_y or self.page_height, ry0)

            # Shallow section header banner background rectangles
            if fill is not None and 0.5 * self.mu_h <= rh <= 6.0 * self.mu_h and rw >= 0.15 * self.page_width:
                self.header_banners.append((rx0, ry0, rx1, ry1))

            # Also treat large vector diagram clusters as visual figure obstacles if they are non-filled complex paths
            items = dw.get("items", [])
            if len(items) >= 25 and rw >= 60.0 and rh >= 60.0 and _rect_area((rx0, ry0, rx1, ry1)) < 0.70 * self.page_width * self.page_height:
                if fill is None:
                    cand_rect = (rx0, ry0, rx1, ry1)
                    if not any(_rects_overlap(cand_rect, existing) for existing in self.image_bboxes):
                        self.image_bboxes.append(cand_rect)

        # Any text span sitting above a top running-header rule or below a bottom running-footer rule is marginal
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

    def _isolate_tables_and_callouts(
        self, spans: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> None:
        """Isolates native tables and shaded clinical callout boxes as atomic blocks."""
        # 1. Native PyMuPDF Tables
        try:
            tabs = self.page.find_tables()
            for tab in tabs:
                bbox = (float(tab.bbox[0]), float(tab.bbox[1]), float(tab.bbox[2]), float(tab.bbox[3]))
                extracted = tab.extract()
                if not extracted or len(extracted) < 2 or not extracted[0] or len(extracted[0]) < 2:
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

        # 2. Shaded Background Callout Boxes
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
        Identifies multi-line figure/table captions and unlabeled descriptions directly beneath images,
        tethering them to their parent figures so caption lines never mix into main body paragraphs.
        """
        if not lines:
            return []

        caption_pattern = re.compile(
            r"^(Fig\.|Figure|Table|Box|Plate|Diagram|Scheme|Image|Photo|Graph|Chart)\s*([A-Z0-9\.\-]+|\b)",
            re.IGNORECASE,
        )

        lines_sorted = sorted(lines, key=lambda l: (l["y0"], l["x0"]))
        used_indices = set()

        for i, line in enumerate(lines_sorted):
            if i in used_indices:
                continue

            l_text = line["text"].strip()
            lx0, ly0, lx1, ly1 = line["x0"], line["y0"], line["x1"], line["y1"]
            lw = lx1 - lx0

            is_explicit_caption = bool(caption_pattern.match(l_text)) and (
                bool(re.match(r"^(Fig\.|Figure|Table|Box|Plate|Diagram|Scheme|Image|Photo|Graph|Chart)\s*\d+", l_text, re.I))
                or line.get("bold_ratio", 0.0) > 0.15
                or line.get("is_accent_color", False)
                or line.get("is_italic", False)
                or line["size"] <= 0.96 * self.mu_size
            )

            # Check if this line sits directly underneath or inside the bottom edge of a picture/image obstacle
            is_under_image_desc = False
            parent_img: Optional[Tuple[float, float, float, float]] = None
            for img_r in self.image_bboxes:
                ix0, iy0, ix1, iy1 = img_r
                horiz_aligned = (lx0 >= ix0 - 3.5 * self.mu_w) and (lx1 <= ix1 + 3.5 * self.mu_w)
                vert_adjacent = (-0.8 * self.mu_h <= (ly0 - iy1) <= 2.0 * self.mu_h)
                if horiz_aligned and vert_adjacent:
                    parent_img = img_r
                    if (
                        is_explicit_caption
                        or line["size"] <= 0.95 * self.mu_size
                        or line["is_italic"]
                        or line.get("is_accent_color", False)
                        or ((ix1 - ix0) <= 0.65 * self.page_width and lw <= (ix1 - ix0) + 2.0 * self.mu_w)
                    ):
                        is_under_image_desc = True
                    break

            if not (is_explicit_caption or is_under_image_desc):
                continue

            # Group subsequent lines belonging to the same multi-line caption
            cap_lines = [line]
            temp_indices = [i]
            curr_x0, curr_y0, curr_x1, curr_y1 = lx0, ly0, lx1, ly1
            ref_size = line["size"]

            for j in range(i + 1, len(lines_sorted)):
                if j in used_indices:
                    continue
                nxt = lines_sorted[j]
                v_gap = nxt["y0"] - curr_y1
                if v_gap > 0.80 * self.mu_h:
                    break
                if v_gap < -0.35 * self.mu_h:
                    continue

                horiz_overlap = min(curr_x1, nxt["x1"]) - max(curr_x0, nxt["x0"])
                min_line_w = max(1.0, min(curr_x1 - curr_x0, nxt["x1"] - nxt["x0"]))
                same_col_bounds = abs(nxt["x0"] - curr_x0) <= 3.0 * self.mu_w and abs(nxt["x1"] - curr_x1) <= 8.0 * self.mu_w
                size_compatible = abs(nxt["size"] - ref_size) <= 0.85 or nxt["size"] <= 0.95 * self.mu_size

                if (horiz_overlap > 0.45 * min_line_w or same_col_bounds) and size_compatible:
                    if caption_pattern.match(nxt["text"].strip()) and re.search(r"\d", nxt["text"][:15]):
                        break
                    # Stop if next line looks like a normal full-size body paragraph after a 1-line caption
                    if (
                        not is_under_image_desc
                        and nxt["size"] >= 0.99 * self.mu_size
                        and not nxt["is_italic"]
                        and cap_lines[-1]["text"].rstrip().endswith(".")
                        and (nxt["x0"] - curr_x0) >= 1.2 * self.mu_w
                    ):
                        break

                    cap_lines.append(nxt)
                    temp_indices.append(j)
                    curr_x0 = min(curr_x0, nxt["x0"])
                    curr_y0 = min(curr_y0, nxt["y0"])
                    curr_x1 = max(curr_x1, nxt["x1"])
                    curr_y1 = max(curr_y1, nxt["y1"])
                else:
                    break

            # Guard against misclassifying a long normal-font body paragraph under an inline image as a caption
            if not is_explicit_caption and line["size"] >= 0.97 * self.mu_size and not line["is_italic"]:
                if len(cap_lines) > 3:
                    continue

            for idx_used in temp_indices:
                used_indices.add(idx_used)

            cap_text = clean_pdf_text(" ".join(cl["text"].strip() for cl in cap_lines))
            cap_bbox = (curr_x0, curr_y0, curr_x1, curr_y1)
            if parent_img is not None:
                # Expand parent image obstacle to include its attached caption so wrapped text bridges both
                idx_img = self.image_bboxes.index(parent_img)
                self.image_bboxes[idx_img] = (
                    min(parent_img[0], curr_x0),
                    min(parent_img[1], curr_y0),
                    max(parent_img[2], curr_x1),
                    max(parent_img[3], curr_y1),
                )

            self.caption_blocks.append({
                "bbox": cap_bbox,
                "text": cap_text,
                "markdown": f"> *{cap_text}*",
                "type": "caption",
                "y0": curr_y0,
                "x0": curr_x0,
                "x1": curr_x1,
                "y1": curr_y1,
                "has_parent_image": parent_img is not None,
            })
            self.exclusion_zones.append(cap_bbox)

        return [l for idx, l in enumerate(lines_sorted) if idx not in used_indices]

    def _is_excluded(self, x: float, y: float) -> bool:
        return any(_point_in_rect(x, y, ez) for ez in self.exclusion_zones)

    def _assemble_lines(self, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Assembles text spans into lines, splitting across column gutters and vertical rulings."""
        if not spans:
            return []

        spans_sorted = sorted(spans, key=lambda s: (s["y0"], s["x0"]))
        lines: List[Dict[str, Any]] = []
        curr_spans: List[Dict[str, Any]] = [spans_sorted[0]]

        for s in spans_sorted[1:]:
            last_s = curr_spans[-1]
            v_overlap = min(s["y1"], last_s["y1"]) - max(s["y0"], last_s["y0"])
            min_h = max(1.0, min(s["height"], last_s["height"]))
            if v_overlap > 0.45 * min_h or abs(s["y0"] - last_s["y0"]) < 0.40 * self.mu_h:
                curr_spans.append(s)
            else:
                lines.extend(self._split_line_segments(curr_spans))
                curr_spans = [s]

        if curr_spans:
            lines.extend(self._split_line_segments(curr_spans))

        return lines

    def _split_line_segments(self, line_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Splits spans on the same horizontal line when crossing vertical rules or column gaps,
        while preserving color, bold ratios, and run-in lead-in subheadings.
        """
        line_spans.sort(key=lambda s: s["x0"])
        segments: List[List[Dict[str, Any]]] = [[line_spans[0]]]

        for s in line_spans[1:]:
            prev_s = segments[-1][-1]
            gap_x = s["x0"] - prev_s["x1"]

            crosses_rule = any(
                vr[0] >= prev_s["x1"] - 2.0
                and vr[0] <= s["x0"] + 2.0
                and not (vr[3] < min(s["y0"], prev_s["y0"]) or vr[1] > max(s["y1"], prev_s["y1"]))
                for vr in self.vertical_rules
            )

            # Use 2.6 * mu_w so justified single-column text never fractures mid-line
            if crosses_rule or gap_x >= 2.6 * self.mu_w:
                segments.append([s])
            else:
                segments[-1].append(s)

        results = []
        for seg in segments:
            parts: List[str] = []
            for idx, s in enumerate(seg):
                if idx > 0 and not s.get("is_superscript"):
                    prev_s = seg[idx - 1]
                    gap_x = s["x0"] - prev_s["x1"]
                    prev_txt = prev_s["text"].strip()
                    is_single_letter_lead = (
                        len(prev_txt) == 1
                        and prev_txt.isalpha()
                        and bool(s["text"])
                        and s["text"][0].islower()
                        and gap_x < 1.4 * self.mu_w
                    )
                    needs_space = (
                        not is_single_letter_lead
                        and (gap_x >= 0.15 * self.mu_w or gap_x < 0.0 or abs(s["y0"] - prev_s["y0"]) >= 0.25 * self.mu_h)
                    )
                    if needs_space:
                        parts.append(" ")
                parts.append(s["text"])
            seg_text = "".join(parts).strip()
            if not seg_text:
                continue

            x0 = min(s["x0"] for s in seg)
            x1 = max(s["x1"] for s in seg)
            y0 = min(s["y0"] for s in seg)
            y1 = max(s["y1"] for s in seg)

            non_sup = [s for s in seg if not s.get("is_superscript")] or seg
            max_size = max(s["size"] for s in non_sup)

            total_chars = max(1, sum(len(s["text"]) for s in non_sup))
            bold_chars = sum(len(s["text"]) for s in non_sup if s["is_bold"])
            italic_chars = sum(len(s["text"]) for s in non_sup if s["is_italic"])
            accent_chars = sum(len(s["text"]) for s in non_sup if self._is_accent_color(s.get("color", 0)))

            bold_ratio = bold_chars / total_chars
            is_bold = bold_ratio >= 0.65
            is_italic = (italic_chars / total_chars) >= 0.65
            is_accent_color = (accent_chars / total_chars) >= 0.65
            dom_color = non_sup[0].get("color", self.body_color)

            # Extract run-in lead-in bold or colored title at the start of a paragraph line
            lead_bold_parts: List[str] = []
            rest_parts: List[str] = []
            in_lead = True
            for s in non_sup:
                if in_lead and (s["is_bold"] or self._is_accent_color(s.get("color", 0))):
                    lead_bold_parts.append(s["text"])
                else:
                    in_lead = False
                    rest_parts.append(s["text"])

            lead_in_bold = " ".join(lead_bold_parts).strip() if rest_parts else ""
            rest_text = " ".join(rest_parts).strip() if lead_bold_parts else ""

            results.append({
                "text": seg_text,
                "bbox": (x0, y0, x1, y1),
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "size": max_size,
                "color": dom_color,
                "is_accent_color": is_accent_color,
                "is_bold": is_bold,
                "is_italic": is_italic,
                "bold_ratio": bold_ratio,
                "lead_in_bold": lead_in_bold,
                "rest_text": rest_text,
                "type": "text",
            })

        return results

    def _is_wrapped_beside_image(self, block: Dict[str, Any]) -> bool:
        """Returns True if a text line sits horizontally adjacent to a figure/image obstacle."""
        by0, by1 = block.get("y0", 0.0), block.get("y1", 0.0)
        bx0, bx1 = block.get("x0", 0.0), block.get("x1", 0.0)
        bh = max(1.0, by1 - by0)
        for img_r in self.image_bboxes:
            v_overlap = min(by1, img_r[3]) - max(by0, img_r[1])
            if v_overlap > 0.25 * bh:
                if bx1 <= img_r[0] + 2.0 * self.mu_w or bx0 >= img_r[2] - 2.0 * self.mu_w:
                    return True
        return False

    def _detect_zone_gutters(self, zone_lines: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
        """
        Detects vertical column gutters locally within a horizontal zone.
        Adapts to both narrow (0.8 * mu_w) and wide column gaps while ignoring narrow lines
        caused by inline images so image-wrapped paragraphs never create phantom columns.
        """
        text_lines = [l for l in zone_lines if l.get("type", "text") == "text"]
        if len(text_lines) < 2:
            return []

        non_image_wrapped = [l for l in text_lines if not l.get("is_image_wrapped", False)]
        if len(non_image_wrapped) < 2:
            return []

        # Verify genuine side-by-side text columns exist in this zone
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
            if len(left_lines) >= 2 and len(right_lines) >= 2:
                valid_gutters.append((g0, g1))
            elif any(abs(g_mid - vr[0]) <= 4.0 for vr in self.vertical_rules) and left_lines and right_lines:
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
        Orders blocks within a single column so that figure captions/callouts sitting beside
        narrow image-wrapped text lines do not slice the wrapped text mid-paragraph.
        """
        sorted_items = sorted(col_blocks, key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0)))
        if len(sorted_items) <= 1:
            return sorted_items

        wrapped_y_ranges: List[Tuple[float, float]] = []
        for b in sorted_items:
            if b.get("type", "text") == "text" and b.get("is_image_wrapped", False):
                wrapped_y_ranges.append((b["y0"] - 0.5 * self.mu_h, b["y1"] + 0.5 * self.mu_h))

        if not wrapped_y_ranges:
            return sorted_items

        # Merge overlapping wrapped Y intervals
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

            # Flush any deferred captions whose wrapped text band has completed
            still_deferred: List[Tuple[float, Dict[str, Any]]] = []
            for release_y, flt in deferred_floats:
                if iy0 > release_y:
                    result.append(flt)
                else:
                    still_deferred.append((release_y, flt))
            deferred_floats = still_deferred

            if b_type in {"caption", "callout"}:
                # Check if this caption/callout overlaps vertically with narrow image-wrapped text lines
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
            is_heading_candidate = (
                line["size"] >= 1.12 * self.mu_size
                or ((line["is_bold"] or line.get("is_accent_color", False)) and line["size"] >= 1.0 * self.mu_size)
                or in_banner
            )
            if (line_w >= 0.62 * self.page_width or in_banner) and is_heading_candidate:
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
                    b for b in zone if b.get("type", "text") == "text" and not b.get("is_image_wrapped", False)
                ] or zone
                c_min_x = min(b.get("x0", 0.0) for b in norm_lines)
                c_max_x = max(b.get("x1", self.page_width) for b in norm_lines)
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
                        if b.get("type", "text") == "text" and not b.get("is_image_wrapped", False)
                    ] or col_items
                    c_min_x = min(b.get("x0", 0.0) for b in norm_col_lines)
                    c_max_x = max(b.get("x1", self.page_width) for b in norm_col_lines)
                    for b in col_items:
                        b["col_min_x"] = c_min_x
                        b["col_max_x"] = c_max_x
                    ordered_blocks.extend(self._order_column_with_image_wrap(col_items))

        return ordered_blocks

    def _merge_wrapped_headings(self, ordered_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merges multi-line wrapped headings of identical typography (and color) before level classification
        so 2-line main headings are not split into separate H1/H2 entries.
        """
        if not ordered_blocks:
            return []

        merged: List[Dict[str, Any]] = []
        i = 0
        n = len(ordered_blocks)

        list_start_re = re.compile(r"^([•▪▸◦‣⁃\-\*]|\d+[\.\)]|\([a-zA-Z0-9]+\))\s+")

        while i < n:
            curr = dict(ordered_blocks[i])
            if curr.get("type", "text") != "text":
                merged.append(curr)
                i += 1
                continue

            curr_text = curr.get("text", "").strip()
            curr_sz = curr.get("size", self.mu_size)
            curr_bold = curr.get("is_bold", False)
            curr_accent = curr.get("is_accent_color", False)
            curr_lead_bold = bool(curr.get("lead_in_bold"))

            curr_bbox = curr.get("bbox", (curr.get("x0", 0), curr.get("y0", 0), curr.get("x1", 0), curr.get("y1", 0)))
            curr_in_banner = self._is_in_header_banner(curr_bbox)

            is_candidate = (
                not curr_lead_bold
                and not list_start_re.match(curr_text)
                and (curr_sz >= 1.04 * self.mu_size or curr_bold or curr_accent or curr_text.isupper() or curr_in_banner)
                and len(curr_text) <= 120
                and not curr_text.endswith((".", ";", "?", "!"))
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
                    nxt_accent = nxt.get("is_accent_color", False)
                    nxt_bbox = nxt.get("bbox", (nxt.get("x0", 0), nxt.get("y0", 0), nxt.get("x1", 0), nxt.get("y1", 0)))
                    nxt_in_banner = self._is_in_header_banner(nxt_bbox)
                    v_gap = nxt.get("y0", 0.0) - curr.get("y1", 0.0)
                    v_pitch = nxt.get("y0", 0.0) - curr.get("y0", 0.0)

                    same_style = (
                        abs(curr_sz - nxt_sz) <= 0.8
                        and (curr_in_banner == nxt_in_banner)
                        and (
                            (curr_bold == nxt_bold and curr_accent == nxt_accent)
                            or (curr_in_banner and nxt_in_banner)
                        )
                        and not nxt.get("lead_in_bold")
                        and not list_start_re.match(nxt_text)
                        and (curr_text.isupper() == nxt_text.isupper())
                    )
                    tight_vertical = (
                        (-0.85 * max(curr_sz, self.mu_h) <= v_gap <= 0.85 * max(curr_sz, self.mu_h))
                        or (0.0 <= v_pitch <= 1.5 * max(curr_sz, self.mu_h))
                    )

                    if same_style and tight_vertical and len(curr_text) + len(nxt_text) <= 160:
                        curr_text = f"{curr_text} {nxt_text}"
                        curr["text"] = curr_text
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
    ) -> Optional[int]:
        """
        Determines exact heading hierarchy level (H1-H5) taking active heading stack depth into account:
        - Level 1 (H1): Chapter / Part / Main Article Title
        - Level 2 (H2): Section Review Banner / Main Section Heading
        - Level 3 (H3): Sub-section Heading (e.g. CONGENITAL ANOMALIES, KERATITIS)
        - Level 4 (H4): Sub-subheading (e.g. Megalocornea, Microcornea, CORNEAL ULCER)
        - Level 5 (H5): Minor Sub-heading (e.g. Bacterial corneal ulcer, Hypopyon corneal ulcer)
        Returns None if the line is standard body text or a list item.
        """
        l_text = item.get("text", "").strip()
        if not l_text or len(l_text) > 150 or len(l_text) < 2:
            return None

        # Never classify lines that are purely numbers or citations
        if re.match(r"^[\d\s\.,\-–—\(\)\[\]]+$", l_text):
            return None

        sz = item.get("size", self.mu_size)
        is_bold = item.get("is_bold", False)
        is_italic = item.get("is_italic", False)
        is_accent = item.get("is_accent_color", False)
        bbox = item.get("bbox", (0.0, 0.0, 0.0, 0.0))
        line_w = bbox[2] - bbox[0]
        is_spanning = line_w >= 0.60 * self.page_width
        in_banner = self._is_in_header_banner(bbox)

        alpha_chars = [c for c in l_text if c.isalpha()]
        if not alpha_chars:
            return None
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        is_all_caps = upper_ratio >= 0.85 and len(alpha_chars) >= 3

        deepest_lvl = max(active_stack.keys()) if active_stack else None

        # 1. Ground-Truth PDF Table of Contents (TOC) Match
        norm_line = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", l_text.lower())).strip()
        for toc_lvl, toc_title in self.page_toc:
            norm_toc = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", toc_title.lower())).strip()
            if norm_toc and (norm_line == norm_toc or norm_line.startswith(norm_toc) or norm_toc.startswith(norm_line)):
                return min(max(int(toc_lvl), 1), 5)

        # Explicit check for main review pill / section review header
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

        if l_text.endswith((".", ";", ",")) and sz < 1.25 * self.mu_size and not in_banner:
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

        # 4. H2 / H3: Main Section & Sub-section Banners
        if in_banner:
            if deepest_lvl == 2 or (active_stack and 2 in active_stack):
                return 3
            return 2 if (is_all_caps or sz >= 1.02 * self.mu_size or is_bold or is_spanning or len(l_text) <= 60) else 3
        if sz >= 1.18 * self.mu_size and (is_bold or is_accent or is_all_caps):
            if deepest_lvl is None or deepest_lvl < 2:
                return 2
            return min(deepest_lvl + 1, 5) if deepest_lvl >= 2 else 3
        if (is_bold or is_accent) and is_all_caps and sz >= 0.98 * self.mu_size and len(l_text) <= 95:
            if deepest_lvl is None or deepest_lvl < 2:
                return 2
            return min(deepest_lvl + 1, 5) if deepest_lvl >= 2 else 3
        if (is_bold or is_accent) and is_spanning and sz >= 1.05 * self.mu_size and len(l_text) <= 100:
            if deepest_lvl is None or deepest_lvl < 2:
                return 2
            return min(deepest_lvl + 1, 5) if deepest_lvl >= 2 else 3

        # 5. Relative H3/H4/H5 Heading Classification based on active stack depth
        if is_bold or is_accent or is_all_caps or sz >= 0.96 * self.mu_size:
            if len(l_text) <= 90 and not item.get("lead_in_bold"):
                if deepest_lvl == 4:
                    return 5
                elif deepest_lvl == 3:
                    return 4
                elif deepest_lvl == 2:
                    return 3

        # Fallback H3 / H4 / H5
        min_h3_scale = 1.0 if promote_headings else 0.98
        if is_accent and sz >= 0.95 * self.mu_size and len(l_text) <= 95 and not item.get("lead_in_bold"):
            return 3 if not is_italic else 4
        if is_bold and sz >= min_h3_scale * self.mu_size and not is_italic and len(l_text) <= 90 and not item.get("lead_in_bold"):
            return 3 if (deepest_lvl is None or deepest_lvl < 3) else min(deepest_lvl + 1, 5)
        if is_all_caps and (is_bold or is_accent or sz >= 0.98 * self.mu_size) and len(l_text) <= 85 and has_top_spacing:
            return 3 if (deepest_lvl is None or deepest_lvl < 3) else min(deepest_lvl + 1, 5)
        if is_bold and not is_italic and sz >= 0.96 * self.mu_size and len(l_text) <= 75 and has_top_spacing:
            return 3 if (deepest_lvl is None or deepest_lvl < 3) else min(deepest_lvl + 1, 5)

        # 6. H4/H5 Minor Subheadings
        if (is_bold or is_accent) and is_italic and sz >= 0.95 * self.mu_size and len(l_text) <= 75:
            return 5 if deepest_lvl == 4 else 4
        if promote_headings and (is_bold or is_accent) and sz >= 0.95 * self.mu_size and len(l_text) <= 70:
            return 5 if deepest_lvl == 4 else 4

        return None

    def _format_document(
        self, ordered_blocks: List[Dict[str, Any]], promote_headings: bool = False
    ) -> Dict[str, Any]:
        """
        Merges wrapped headings, classifies H1-H4 hierarchy levels and run-in headings,
        maintains a stateful parent-child heading stack, and formats clean Markdown with
        seamless paragraph continuation across inline figures.
        """
        blocks = self._merge_wrapped_headings(ordered_blocks)

        output_parts: List[str] = []
        headings_found: List[str] = []
        hierarchy_tree: List[Dict[str, Any]] = []
        layout_boxes: List[Dict[str, Any]] = []

        active_stack: Dict[int, str] = dict(self.initial_heading_stack)

        current_paragraph: List[str] = []
        para_bbox: Optional[List[float]] = None
        pending_floats: List[Dict[str, Any]] = []
        prev_line: Optional[Dict[str, Any]] = None
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
                    output_parts.append(md_content)
                layout_boxes.append({
                    "order": order_counter,
                    "type": f_type,
                    "bbox": to_norm_bbox(f_bbox),
                    "markdown": md_content,
                    "label": f"{f_type.title()} #{order_counter}",
                })
                order_counter += 1

        def flush_paragraph() -> None:
            nonlocal current_paragraph, para_bbox, order_counter
            if current_paragraph:
                raw_joined = "\n".join(current_paragraph)
                cleaned = clean_pdf_text(raw_joined).replace("\n", " ")
                if cleaned:
                    output_parts.append(cleaned)
                    if para_bbox:
                        layout_boxes.append({
                            "order": order_counter,
                            "type": "paragraph",
                            "bbox": to_norm_bbox(tuple(para_bbox)),
                            "label": f"Body #{order_counter}",
                        })
                        order_counter += 1
                current_paragraph = []
                para_bbox = None
            flush_pending_floats()

        list_pattern = re.compile(r"^([•▪▸◦‣⁃\-\*]|\d+[\.\)]|\([a-zA-Z0-9]+\))\s+(.+)$")
        run_in_regex = re.compile(
            r"^(\*\*([A-Za-z0-9\s\-/]{3,42}?)\*\*[\.:—\-]?|"
            r"([A-Z][A-Za-z0-9\s\-/]{2,38})[\.:])\s+(.+)$"
        )

        for item in blocks:
            b_type = item.get("type", "text")
            bbox = item.get("bbox", (0.0, 0.0, 0.0, 0.0))
            norm_bbox = to_norm_bbox(bbox)

            if b_type in {"table", "callout", "caption"}:
                # If a caption or callout interrupts an unfinished sentence mid-paragraph, defer it until paragraph flush
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
                    output_parts.append(md_content)
                layout_boxes.append({
                    "order": order_counter,
                    "type": b_type,
                    "bbox": norm_bbox,
                    "markdown": md_content,
                    "label": f"{b_type.title()} #{order_counter}",
                })
                order_counter += 1
                continue

            l_text = item.get("text", "").strip()
            if not l_text:
                continue

            prev_y1 = prev_line["y1"] if (prev_line and prev_line.get("col_idx") == item.get("col_idx")) else None

            # 1. Check Standalone Heading Level (H1, H2, H3, H4, H5)
            h_level = self._classify_heading_level(
                item, prev_y1=prev_y1, promote_headings=promote_headings, active_stack=active_stack
            )
            if h_level is not None:
                flush_paragraph()
                clean_h = update_heading_stack(h_level, l_text)
                is_bold_heading = (
                    item.get("is_bold", False)
                    or item.get("is_accent_color", False)
                    or l_text.startswith("**")
                    or h_level in (4, 5)
                )
                prefix = "#" * h_level
                if is_bold_heading and h_level in (4, 5):
                    output_parts.append(f"{prefix} **{clean_h}**")
                else:
                    output_parts.append(f"{prefix} {clean_h}")
                layout_boxes.append({
                    "order": order_counter,
                    "type": "heading",
                    "level": h_level,
                    "bbox": norm_bbox,
                    "label": f"H{h_level}: {clean_h[:32]}",
                })
                order_counter += 1
                prev_line = item
                continue

            # 2. Check Medical Run-In Subheading (H4) at start of paragraph
            lead_bold = item.get("lead_in_bold", "").strip() if isinstance(item.get("lead_in_bold"), str) else ""
            rest_txt = item.get("rest_text", "").strip() if isinstance(item.get("rest_text"), str) else ""
            run_in_title = ""
            run_in_body = ""

            if lead_bold and rest_txt and 2 <= len(lead_bold) <= 45 and lead_bold.endswith((".", ":", "—", "-")):
                run_in_title = lead_bold.rstrip(".:—- ").strip()
                run_in_body = rest_txt
            else:
                m_run = run_in_regex.match(l_text)
                if m_run and (item.get("bold_ratio", 0.0) > 0.15 or l_text.startswith("**")):
                    run_in_title = (m_run.group(2) or m_run.group(3) or "").strip(".:—- ")
                    run_in_body = m_run.group(4).strip()

            if run_in_title and run_in_body:
                flush_paragraph()
                clean_h = update_heading_stack(4, run_in_title)
                output_parts.append(f"#### {clean_h}")
                layout_boxes.append({
                    "order": order_counter,
                    "type": "heading",
                    "level": 4,
                    "bbox": norm_bbox,
                    "label": f"H4 (Run-In): {clean_h[:28]}",
                })
                order_counter += 1
                current_paragraph = [run_in_body]
                para_bbox = [bbox[0], bbox[1], bbox[2], bbox[3]]
                prev_line = item
                continue

            # 3. Check Multi-Level List Items
            list_match = list_pattern.match(l_text)
            if list_match:
                flush_paragraph()
                raw_marker, list_body = list_match.group(1), list_match.group(2)
                col_min_x = item.get("col_min_x", bbox[0])
                indent_steps = max(0, min(3, int((bbox[0] - col_min_x) / (2.2 * self.mu_w))))
                indent_str = "  " * indent_steps
                md_marker = raw_marker if re.match(r"^\d+[\.\)]$", raw_marker) else "-"
                output_parts.append(f"{indent_str}{md_marker} {list_body}")
                layout_boxes.append({
                    "order": order_counter,
                    "type": "list",
                    "bbox": norm_bbox,
                    "label": f"List Item #{order_counter}",
                })
                order_counter += 1
                prev_line = item
                continue

            # 4. Standard Body Paragraph Continuation vs New Paragraph Break
            if current_paragraph and prev_line is not None:
                same_col = item.get("col_idx", 0) == prev_line.get("col_idx", 0)
                v_gap = bbox[1] - prev_line["y1"]
                v_pitch = bbox[1] - prev_line["y0"]
                col_min_x = item.get("col_min_x", min(bbox[0], prev_line["x0"]))
                col_max_x = item.get("col_max_x", max(bbox[2], prev_line["x1"]))

                curr_wrapped = item.get("is_image_wrapped", False)
                prev_wrapped = prev_line.get("is_image_wrapped", False)

                # Detect if an inline image obstacle sits between prev_line and current line
                bridged_over_image = False
                if same_col and v_gap > 1.1 * self.mu_h:
                    for img_r in self.image_bboxes:
                        if img_r[1] >= prev_line["y0"] - 1.5 * self.mu_h and img_r[3] <= bbox[3] + 1.5 * self.mu_h:
                            bridged_over_image = True
                            break

                prev_txt = prev_line["text"].rstrip()
                ends_sentence = prev_txt.endswith((".", "!", "?", ":"))
                starts_lower = bool(l_text and l_text[0].islower())

                # Require a genuine vertical paragraph gap (> 0.95 * mu_h and baseline pitch > 1.55 * mu_h)
                large_v_gap = (v_gap > 0.95 * self.mu_h and v_pitch > 1.55 * self.mu_h) and not bridged_over_image

                eff_max_x = prev_line["x1"] if prev_wrapped else col_max_x
                prev_short_line = (
                    ends_sentence
                    and not starts_lower
                    and (prev_line["x1"] < eff_max_x - 4.0 * self.mu_w)
                )

                first_line_indent = (
                    not curr_wrapped
                    and not prev_wrapped
                    and ends_sentence
                    and (bbox[0] - col_min_x) >= 1.3 * self.mu_w
                    and (bbox[0] - prev_line["x0"]) >= 1.1 * self.mu_w
                )

                # Continuation override if previous line did not end with sentence punctuation
                force_join = (
                    not ends_sentence
                    and same_col
                    and (starts_lower or curr_wrapped or prev_wrapped or bridged_over_image or v_gap <= 1.25 * self.mu_h)
                )

                if not force_join and (not same_col or large_v_gap or prev_short_line or first_line_indent):
                    flush_paragraph()

            current_paragraph.append(l_text)
            if para_bbox is None:
                para_bbox = [bbox[0], bbox[1], bbox[2], bbox[3]]
            else:
                para_bbox[0] = min(para_bbox[0], bbox[0])
                para_bbox[1] = min(para_bbox[1], bbox[1])
                para_bbox[2] = max(para_bbox[2], bbox[2])
                para_bbox[3] = max(para_bbox[3], bbox[3])

            prev_line = item

        flush_paragraph()

        final_md = "\n\n".join(output_parts).strip()
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


def _get_docling_converter(do_ocr: bool) -> Any:
    """Initializes and caches a CPU-only Docling DocumentConverter instance (using Heron RT-DETRv2 layout model)."""
    if not _DOCLING_AVAILABLE:
        return None

    if do_ocr in _DOCLING_CONVERTERS:
        return _DOCLING_CONVERTERS[do_ocr]

    if HF_CACHE_DIR:
        os.environ["HF_HOME"] = str(HF_CACHE_DIR)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)

    if is_docling_models_downloaded():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    accelerator = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
    pipeline_options = PdfPipelineOptions()
    pipeline_options.accelerator_options = accelerator
    pipeline_options.do_ocr = do_ocr
    pipeline_options.images_scale = 2.0  # 144 DPI for dense multi-column recognition
    pipeline_options.do_table_structure = True

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

    _DOCLING_CONVERTERS[do_ocr] = converter
    return converter


def _serialize_docling_ast(doc: Any, hierarchy_tree: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Serializes a DoclingDocument AST into Markdown while calibrating H1-H4 levels
    (and promoting colored/typographic headings) using AdaptivePdfLayoutEngine's hierarchy tree.
    """
    level_lookup: Dict[str, int] = {}
    for h_node in (hierarchy_tree or []):
        norm_title = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(h_node.get("title", "")).lower())).strip()
        if norm_title:
            level_lookup[norm_title] = int(h_node.get("level", 2))

    blocks: List[str] = []
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
            blocks.append(f"# {text_val}")
        elif label == DocItemLabel.SECTION_HEADER and text_val:
            norm_t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text_val.lower())).strip()
            calibrated_lvl = level_lookup.get(norm_t)
            if calibrated_lvl is None:
                for k_norm, k_lvl in level_lookup.items():
                    if k_norm in norm_t or norm_t in k_norm:
                        calibrated_lvl = k_lvl
                        break
            if calibrated_lvl is None:
                if re.match(r"^(CHAPTER|PART|SECTION|UNIT)\s+", text_val, re.I):
                    calibrated_lvl = 1
                elif text_val.isupper() or re.match(r"^([IVXLCDM]+\.|\d{1,2}\.)\s+", text_val):
                    calibrated_lvl = 2
                elif re.match(r"^\d{1,2}\.\d{1,2}\.?\s+", text_val):
                    calibrated_lvl = 3
                else:
                    calibrated_lvl = min(max(int(level) + 1, 2), 4)

            blocks.append(f"{'#' * calibrated_lvl} {text_val}")
        elif label == DocItemLabel.FORMULA and text_val:
            blocks.append(f"$$\n{text_val}\n$$")
        elif label == DocItemLabel.LIST_ITEM and text_val:
            indent = "  " * max(0, int(level) - 1)
            marker = getattr(item, "marker", "-") or "-"
            blocks.append(f"{indent}{marker} {text_val}")
        elif label == DocItemLabel.TABLE:
            cap = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
            if cap:
                blocks.append(f"> *{cap}*")
            if hasattr(item, "export_to_markdown"):
                blocks.append(item.export_to_markdown(doc=doc).strip())
        elif label == DocItemLabel.PICTURE:
            cap = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
            if cap:
                blocks.append(f"> **[Figure]** *{cap}*")
        elif label == DocItemLabel.CAPTION and text_val:
            blocks.append(f"> *{text_val}*")
        elif label == DocItemLabel.FOOTNOTE and text_val:
            blocks.append(f"> ^[Footnote]: {text_val}")
        elif text_val:
            # Check if AdaptivePdfLayoutEngine identified this short line as a colored/typographic heading
            norm_t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text_val.lower())).strip()
            if len(text_val) <= 110 and norm_t in level_lookup:
                lvl = level_lookup[norm_t]
                blocks.append(f"{'#' * lvl} {text_val}")
            else:
                blocks.append(text_val)

    return "\n\n".join(b for b in blocks if b)


def extract_page_with_docling_v2(
    file_path: Union[str, Path],
    page_number: int,
    default_title: str = "",
    initial_heading_stack: Optional[Dict[int, str]] = None,
    page_toc: Optional[List[Tuple[int, str]]] = None,
) -> Dict[str, Any]:
    """
    Docling v2 Memory-Safe Single-Page Extraction:
    - Slices requested page into a temporary 1-page PDF buffer wrapped in DocumentStream
    - Auto-detects whether OCR is necessary
    - Calibrates headings and image-wrapped text against AdaptivePdfLayoutEngine
    """
    norm_path = str(Path(file_path).resolve())
    title = default_title or Path(norm_path).stem

    adaptive_res = extract_page_with_adaptive_layout(
        norm_path,
        page_number,
        default_title=title,
        initial_heading_stack=initial_heading_stack,
        page_toc=page_toc,
    )

    if not _DOCLING_AVAILABLE or DocumentStream is None:
        logger.info("Docling library not installed; cleanly falling back to Adaptive Multi-Column Layout.")
        return adaptive_res

    try:
        with _FITZ_LOCK:
            with fitz.open(norm_path) as doc:
                page_idx = page_number - 1
                if page_idx < 0 or page_idx >= doc.page_count:
                    return adaptive_res

                page = doc[page_idx]
                native_text = page.get_text("text").strip()
                do_ocr = len(native_text) < 40

                single_doc = fitz.open()
                single_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
                pdf_bytes = single_doc.write()
                single_doc.close()

        converter = _get_docling_converter(do_ocr=do_ocr)
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

        if not raw_docling_md or len(raw_docling_md.strip()) < 0.25 * len(adaptive_res.get("extracted_text", "")):
            logger.info("Docling output sparse; augmenting with adaptive multi-column recovery.")
            return adaptive_res

        formatted_md = clean_pdf_text(raw_docling_md)
        callout_texts = [
            b.get("markdown", "")
            for b in adaptive_res.get("layout_boxes", [])
            if b.get("type") == "callout" and b.get("markdown", "")[:30] not in formatted_md
        ]
        if callout_texts:
            formatted_md += "\n\n" + "\n\n".join(callout_texts)

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
        logger.warning("Docling v2 conversion encountered exception: %s. Falling back to adaptive engine.", exc)
        return adaptive_res


def extract_page_with_adaptive_layout(
    file_path: Union[str, Path],
    page_number: int,
    default_title: str = "",
    promote_headings: bool = False,
    initial_heading_stack: Optional[Dict[int, str]] = None,
    page_toc: Optional[List[Tuple[int, str]]] = None,
) -> Dict[str, Any]:
    """Helper executing the Adaptive Multi-Column Geometric Engine on a specific PDF page, including Base64 PNG pixmap."""
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
            )
            result = engine.run(promote_headings=promote_headings)
            result["page_image_base64"] = page_image_b64
            result["page_width"] = float(page.rect.width)
            result["page_height"] = float(page.rect.height)
            return result


def extract_structured_layout(file_path: Union[str, Path], page_number: int) -> str:
    """Convenience layout extractor returning Markdown text using Docling v2."""
    res = extract_page_with_docling_v2(file_path, page_number)
    return res.get("extracted_text", "") or res.get("text", "")


def extract_pdf_structure(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Extracts structured sections from a PDF for Tantivy BM25 and LanceDB indexing using Docling v2.
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
            p_res = extract_page_with_docling_v2(
                norm_path,
                p_num,
                default_title=default_title,
                initial_heading_stack=running_stack,
                page_toc=toc_by_page.get(p_num, []),
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
        logger.error("Docling v2 PDF structure extraction failed for %s: %s", norm_path, exc)
        return []
