"""
Adaptive Multi-Column Medical Layout Engine for PyMuPDF (fitz) and Docling v2 Hybrid Recovery.
Implements per-page typography profiling, vector ruling barriers, shaded callout isolation,
constrained topological DAG reading order, multi-tier hierarchy detection (H1-H4 + run-in headings),
PDF page pixmap rendering (base64 PNG), and Docling v2 memory-safe single-page extraction.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import io
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

# Docling imports (lazy / optional to prevent startup failure if not installed)
_DOCLING_AVAILABLE = False
try:
    from docling.datamodel.base_models import InputFormat
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
    1. Per-page typography baseline (mu_w, mu_h, mu_size) and heading size tiers
    2. Vector graphic rulings (vertical barriers, horizontal zone splitters, banner frames)
    3. Structural element classification (tables, callout boxes, figure captions)
    4. Indentation profiling (first-line paragraph indents, multi-level list nesting)
    5. Constrained Topological DAG reading order solver
    6. Multi-tier hierarchy depth recognition (H1-H4 + medical run-in headings)
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

        # Typography Profile Defaults
        self.mu_size: float = 10.0
        self.mu_h: float = 12.0
        self.mu_w: float = 5.0
        self.heading_size_tiers: List[float] = []

        # Structural Containers
        self.vertical_rules: List[Tuple[float, float, float, float]] = []
        self.horizontal_rules: List[Tuple[float, float, float, float]] = []
        self.header_banners: List[Tuple[float, float, float, float]] = []
        self.tables_markdown: List[Dict[str, Any]] = []
        self.callout_blocks: List[Dict[str, Any]] = []
        self.caption_blocks: List[Dict[str, Any]] = []
        self.exclusion_zones: List[Tuple[float, float, float, float]] = []

    def run(self, promote_headings: bool = False) -> Dict[str, Any]:
        """Executes full adaptive visual-context layout extraction."""
        # 1. Profile typography & extract vector drawings
        spans, drawings = self._profile_page()

        # 2. Extract vector rulings & background frames
        self._extract_rulings_and_frames(drawings)

        # 3. Isolate tables & shaded clinical callout boxes
        self._isolate_tables_and_callouts(spans, drawings)

        # 4. Isolate figure & table captions
        self._isolate_captions(spans)

        # 5. Filter out excluded spans (tables, callouts, captions, marginal headers/footers)
        remaining_spans = [
            s for s in spans
            if not s.get("is_marginal") and not self._is_excluded((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2)
        ]

        # 6. Assemble spans into lines with intra-line column barrier checks
        lines = self._assemble_lines(remaining_spans)

        # 7. Solve Topological DAG reading order across columns and horizontal zones
        ordered_blocks = self._topological_dag_reading_order(lines)

        # 8. Hierarchical classification, run-in lead-in extraction, list formatting, and Markdown construction
        return self._format_document(ordered_blocks, promote_headings=promote_headings)

    def _compute_heading_size_tiers(self, spans: List[Dict[str, Any]]) -> None:
        """Clusters distinct font sizes above body median into descending tiers for relative H1-H3 ranking."""
        candidate_sizes = sorted(
            {
                round(s["size"], 1)
                for s in spans
                if not s.get("is_marginal")
                and (s["size"] >= 1.04 * self.mu_size or s.get("is_bold"))
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
        """Extracts text spans and drawing instructions, profiling body typography."""
        try:
            page_dict = self.page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
        except Exception:
            page_dict = {}

        try:
            drawings = self.page.get_drawings()
        except Exception:
            drawings = []

        all_spans: List[Dict[str, Any]] = []
        font_sizes_weighted: List[float] = []
        line_heights_weighted: List[float] = []
        char_widths_weighted: List[float] = []

        blocks = page_dict.get("blocks", []) if isinstance(page_dict, dict) else []
        for b in blocks:
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
                    color = span.get("color", 0)

                    # Running header/footer exclusion (< 3.5% from top or bottom)
                    is_marginal = (y0 < self.page_height * 0.035) or (y1 > self.page_height * 0.965)

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
                        "is_bold": bool(flags & 2 or "bold" in font.lower() or font.lower().endswith("bo") or "hebo" in font.lower()),
                        "is_italic": bool(flags & 1 or "italic" in font.lower() or "oblique" in font.lower()),
                        "is_marginal": is_marginal,
                    }
                    all_spans.append(span_dict)

                    if not is_marginal:
                        w = max(1, len(s_text))
                        font_sizes_weighted.extend([sz] * w)
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

        self.mu_size = max(6.0, self.mu_size)
        self.mu_h = max(8.0, self.mu_h)
        self.mu_w = max(2.5, self.mu_w)

        self._compute_heading_size_tiers(all_spans)

        return all_spans, drawings

    def _extract_rulings_and_frames(self, drawings: List[Dict[str, Any]]) -> None:
        """Extracts vertical ruling lines (column dividers), horizontal rulings, and section banners."""
        for dw in drawings:
            rect = dw.get("rect")
            fill = dw.get("fill")
            if not rect:
                continue
            rx0, ry0, rx1, ry1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
            rw = rx1 - rx0
            rh = ry1 - ry0

            # Vertical ruling line (barrier between columns)
            if rw <= 3.0 and rh >= 4.0 * self.mu_h:
                self.vertical_rules.append((rx0, ry0, rx1, ry1))

            # Horizontal ruling line (zone boundary or header/footer separator)
            elif rh <= 3.0 and rw >= 0.20 * self.page_width:
                self.horizontal_rules.append((rx0, ry0, rx1, ry1))

            # Shallow section header banner background rectangles
            if fill is not None and 1.0 * self.mu_h <= rh <= 3.2 * self.mu_h and rw >= 0.25 * self.page_width:
                self.header_banners.append((rx0, ry0, rx1, ry1))

    def _is_in_header_banner(self, bbox: Tuple[float, float, float, float]) -> bool:
        """Checks if a text line sits inside a colored/shaded section header banner."""
        cx = (bbox[0] + bbox[2]) * 0.5
        cy = (bbox[1] + bbox[3]) * 0.5
        return any(_point_in_rect(cx, cy, hb) for hb in self.header_banners)

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

            if rw >= 8.0 * self.mu_w and rh >= 2.2 * self.mu_h:
                if _rect_area(r_bbox) < 0.75 * self.page_width * self.page_height:
                    if any(_rects_overlap(r_bbox, ez) for ez in self.exclusion_zones):
                        continue

                    box_spans = [
                        s for s in spans
                        if _point_in_rect((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2, r_bbox)
                        and not self._is_excluded((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2)
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

    def _isolate_captions(self, spans: List[Dict[str, Any]]) -> None:
        """Identifies figure and table captions and tethers them."""
        caption_pattern = re.compile(
            r"^(Fig\.|Figure|Table|Box|Plate|Diagram|Scheme|Image)\s*\d+", re.IGNORECASE
        )
        for s in spans:
            if self._is_excluded((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2):
                continue

            s_text = s["text"].strip()
            if caption_pattern.match(s_text):
                caption_bbox = s["bbox"]
                self.caption_blocks.append({
                    "bbox": caption_bbox,
                    "text": s_text,
                    "markdown": f"> *{s_text}*",
                    "type": "caption",
                    "y0": s["y0"],
                    "x0": s["x0"],
                    "x1": s["x1"],
                    "y1": s["y1"],
                })
                self.exclusion_zones.append(caption_bbox)

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
            min_h = min(s["height"], last_s["height"])
            if v_overlap > 0.45 * min_h or abs(s["y0"] - last_s["y0"]) < 0.4 * self.mu_h:
                curr_spans.append(s)
            else:
                lines.extend(self._split_line_segments(curr_spans))
                curr_spans = [s]

        if curr_spans:
            lines.extend(self._split_line_segments(curr_spans))

        return lines

    def _split_line_segments(self, line_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Splits spans on the same vertical line if crossing vertical rules or wide column gaps."""
        line_spans.sort(key=lambda s: s["x0"])
        segments: List[List[Dict[str, Any]]] = [[line_spans[0]]]

        for s in line_spans[1:]:
            prev_s = segments[-1][-1]
            gap_x = s["x0"] - prev_s["x1"]

            crosses_rule = any(
                vr[0] >= prev_s["x1"] - 1.5 and vr[0] <= s["x0"] + 1.5
                for vr in self.vertical_rules
            )

            if crosses_rule or gap_x >= 1.75 * self.mu_w:
                segments.append([s])
            else:
                segments[-1].append(s)

        results = []
        for seg in segments:
            seg_text = " ".join(s["text"] for s in seg)
            x0 = min(s["x0"] for s in seg)
            x1 = max(s["x1"] for s in seg)
            y0 = min(s["y0"] for s in seg)
            y1 = max(s["y1"] for s in seg)
            max_size = max(s["size"] for s in seg)
            is_bold = any(s["is_bold"] for s in seg)
            is_italic = any(s["is_italic"] for s in seg)

            results.append({
                "text": seg_text,
                "bbox": (x0, y0, x1, y1),
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "size": max_size,
                "is_bold": is_bold,
                "is_italic": is_italic,
                "type": "text",
            })

        return results

    def _topological_dag_reading_order(self, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Orders blocks using a constrained Topological DAG:
        1. Detect vertical column gutters & horizontal rule dividers.
        2. Slice into horizontal zones at spanning elements.
        3. Sort column-first within each zone, integrating tables and callouts seamlessly.
        """
        non_text = self.tables_markdown + self.callout_blocks + self.caption_blocks
        if not lines:
            return sorted(non_text, key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0)))

        gutters = self._detect_gutters(lines)

        spanning_y_dividers: List[float] = []
        for hr in self.horizontal_rules:
            spanning_y_dividers.append(hr[1])

        for line in lines:
            line_w = line["x1"] - line["x0"]
            l_bbox = line.get("bbox", (line["x0"], line["y0"], line["x1"], line["y1"]))
            in_banner = self._is_in_header_banner(l_bbox)
            is_heading_candidate = line["size"] >= 1.08 * self.mu_size or (line["is_bold"] and line["size"] >= self.mu_size) or in_banner
            crosses_gutter = any(line["x0"] < (g[0] + g[1]) / 2.0 and line["x1"] > (g[0] + g[1]) / 2.0 for g in gutters)
            if (line_w >= 0.55 * self.page_width or crosses_gutter or in_banner) and is_heading_candidate:
                spanning_y_dividers.append(line["y0"])

        for nt in non_text:
            nt_w = nt.get("x1", 0) - nt.get("x0", 0)
            if nt_w >= 0.55 * self.page_width:
                spanning_y_dividers.append(nt["y0"])

        spanning_y_dividers.sort()

        all_blocks = list(lines) + list(non_text)
        all_blocks.sort(key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0)))

        zones: List[List[Dict[str, Any]]] = []
        current_zone: List[Dict[str, Any]] = []
        div_idx = 0

        for block in all_blocks:
            by0 = block.get("y0", 0.0)
            if div_idx < len(spanning_y_dividers) and by0 >= spanning_y_dividers[div_idx]:
                if current_zone:
                    zones.append(current_zone)
                    current_zone = []
                div_idx += 1
            current_zone.append(block)

        if current_zone:
            zones.append(current_zone)

        ordered_blocks: List[Dict[str, Any]] = []
        for zone in zones:
            if not gutters or len(zone) <= 1:
                for b in zone:
                    b["col_idx"] = 0
                ordered_blocks.extend(sorted(zone, key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0))))
            else:
                cols: Dict[int, List[Dict[str, Any]]] = {}
                for block in zone:
                    bx_mid = (block.get("x0", 0.0) + block.get("x1", 0.0)) / 2.0
                    col_idx = 0
                    for g_i, g in enumerate(gutters):
                        if bx_mid > g[1]:
                            col_idx = g_i + 1
                    block["col_idx"] = col_idx
                    cols.setdefault(col_idx, []).append(block)

                for c_k in sorted(cols.keys()):
                    ordered_blocks.extend(sorted(cols[c_k], key=lambda b: (b.get("y0", 0.0), b.get("x0", 0.0))))

        return ordered_blocks

    def _detect_gutters(self, lines: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
        """Builds a 1D X-histogram to locate column gutters, reinforced by vertical rules."""
        hist = [0] * int(self.page_width + 1)
        for line in lines:
            line_w = line["x1"] - line["x0"]
            if line_w < 0.65 * self.page_width:
                x_start = max(0, int(line["x0"]))
                x_end = min(int(self.page_width), int(line["x1"]))
                for x in range(x_start, x_end):
                    hist[x] += 1

        gutters: List[Tuple[float, float]] = []
        in_gutter = False
        g_start = 0

        x_min = int(self.page_width * 0.08)
        x_max = int(self.page_width * 0.92)

        for x in range(x_min, x_max):
            if hist[x] <= 1:
                if not in_gutter:
                    in_gutter = True
                    g_start = x
            else:
                if in_gutter:
                    in_gutter = False
                    gw = x - g_start
                    if gw >= 2.0 * self.mu_w:
                        gutters.append((float(g_start), float(x)))
                    elif gw >= 0.85 * self.mu_w:
                        mid_g = (g_start + x) / 2.0
                        if any(abs(mid_g - vr[0]) <= 3.5 for vr in self.vertical_rules):
                            gutters.append((float(g_start), float(x)))

        return gutters

    def _merge_wrapped_headings(self, ordered_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merges multi-line wrapped headings of identical typography before level classification
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
            curr_lead_bold = bool(curr.get("lead_in_bold"))

            curr_bbox = curr.get("bbox", (curr.get("x0", 0), curr.get("y0", 0), curr.get("x1", 0), curr.get("y1", 0)))
            curr_in_banner = self._is_in_header_banner(curr_bbox)

            is_candidate = (
                not curr_lead_bold
                and not list_start_re.match(curr_text)
                and (curr_sz >= 1.04 * self.mu_size or curr_bold or curr_text.isupper() or curr_in_banner)
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
                    nxt_bbox = nxt.get("bbox", (nxt.get("x0", 0), nxt.get("y0", 0), nxt.get("x1", 0), nxt.get("y1", 0)))
                    nxt_in_banner = self._is_in_header_banner(nxt_bbox)
                    v_gap = nxt.get("y0", 0.0) - curr.get("y1", 0.0)
                    v_pitch = nxt.get("y0", 0.0) - curr.get("y0", 0.0)

                    same_style = (
                        abs(curr_sz - nxt_sz) <= 0.8
                        and (curr_bold == nxt_bold or curr_in_banner or nxt_in_banner)
                        and not nxt.get("lead_in_bold")
                        and not list_start_re.match(nxt_text)
                    )
                    if not (curr_in_banner or nxt_in_banner):
                        same_style = same_style and (curr_text.isupper() == nxt_text.isupper() or curr_bold)
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
    ) -> Optional[int]:
        """
        Determines exact heading hierarchy level:
        - Level 1 (H1): Chapter / Part / Main Article Title
        - Level 2 (H2): Major Section Heading
        - Level 3 (H3): Subheading
        - Level 4 (H4): Minor Sub-subheading
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
        bbox = item.get("bbox", (0.0, 0.0, 0.0, 0.0))
        line_w = bbox[2] - bbox[0]
        is_spanning = line_w >= 0.60 * self.page_width
        in_banner = self._is_in_header_banner(bbox)

        alpha_chars = [c for c in l_text if c.isalpha()]
        if not alpha_chars:
            return None
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        is_all_caps = upper_ratio >= 0.85 and len(alpha_chars) >= 3

        # 1. Ground-Truth PDF Table of Contents (TOC) Match
        norm_line = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", l_text.lower())).strip()
        for toc_lvl, toc_title in self.page_toc:
            norm_toc = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", toc_title.lower())).strip()
            if norm_toc and (norm_line == norm_toc or norm_line.startswith(norm_toc) or norm_toc.startswith(norm_line)):
                return min(max(int(toc_lvl), 1), 4)

        # 2. Explicit Structural & Numbered Section Prefixes
        if re.match(r"^(CHAPTER|PART|UNIT|MODULE|SECTION)\s+([0-9]+|[IVXLCDM]+)\b", l_text, re.IGNORECASE):
            return 1

        _MED_SECTION_HEADINGS = (
            "PATHOPHYSIOLOGY", "CLINICAL MANIFESTATIONS", "DIAGNOSIS AND TREATMENT",
            "ETIOLOGY AND RISK FACTORS", "ETIOLOGY", "MANAGEMENT", "OVERVIEW",
            "EPIDEMIOLOGY", "PREVENTION", "DIAGNOSTIC WORKUP", "COMPLICATIONS",
            "PATIENT EVALUATION", "TREATMENT", "CLINICAL FEATURES", "PATHOGENESIS",
        )
        norm_upper_text = re.sub(r"\s+", " ", l_text.upper()).strip(" :.")
        if any(norm_upper_text == h_kw or norm_upper_text.startswith(h_kw) for h_kw in _MED_SECTION_HEADINGS):
            if is_bold or sz >= 1.02 * self.mu_size or in_banner:
                return 2

        if is_bold or sz >= 1.05 * self.mu_size or in_banner:
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
        has_top_spacing = v_gap >= 0.25 * self.mu_h

        tier_idx = None
        for idx, t_sz in enumerate(self.heading_size_tiers):
            if abs(sz - t_sz) <= 0.45:
                tier_idx = idx
                break

        # 3. H1: Chapter / Article Main Title
        if sz >= 1.40 * self.mu_size:
            return 1
        if sz >= 1.22 * self.mu_size and (is_spanning or bbox[1] < 0.22 * self.page_height) and tier_idx == 0:
            return 1

        # 4. H2: Main Section Heading
        if sz >= 1.18 * self.mu_size and (is_bold or is_all_caps or in_banner):
            return 2
        if in_banner and (is_spanning or is_all_caps or sz >= 1.05 * self.mu_size):
            return 2
        if is_bold and is_all_caps and sz >= 1.02 * self.mu_size and len(l_text) <= 90:
            return 2
        if is_bold and is_spanning and sz >= 1.08 * self.mu_size and len(l_text) <= 100:
            return 2

        # 5. H3: Subheading
        min_h3_scale = 1.02 if promote_headings else 1.04
        if in_banner:
            return 3
        if is_bold and sz >= min_h3_scale * self.mu_size and not is_italic and len(l_text) <= 90:
            return 3
        if is_all_caps and (is_bold or sz >= 1.0 * self.mu_size) and len(l_text) <= 75 and has_top_spacing:
            return 3
        if is_bold and not is_italic and sz >= 0.98 * self.mu_size and len(l_text) <= 70 and has_top_spacing:
            return 3

        # 6. H4: Minor Sub-subheading
        if is_bold and is_italic and sz >= 0.96 * self.mu_size and len(l_text) <= 70:
            return 4
        if promote_headings and is_bold and sz >= 0.96 * self.mu_size and len(l_text) <= 65:
            return 4

        return None

    def _format_document(
        self, ordered_blocks: List[Dict[str, Any]], promote_headings: bool = False
    ) -> Dict[str, Any]:
        """
        Merges wrapped headings, classifies H1-H4 hierarchy levels and run-in headings,
        maintains a stateful parent-child heading stack, and formats clean Markdown.
        """
        blocks = self._merge_wrapped_headings(ordered_blocks)

        output_parts: List[str] = []
        headings_found: List[str] = []
        hierarchy_tree: List[Dict[str, Any]] = []
        layout_boxes: List[Dict[str, Any]] = []

        active_stack: Dict[int, str] = dict(self.initial_heading_stack)

        current_paragraph: List[str] = []
        para_bbox: Optional[List[float]] = None
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
            clean_title = re.sub(r"\s+", " ", title).strip(" .:")
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
                prev_line = None
                continue

            l_text = item.get("text", "").strip()
            if not l_text:
                continue

            prev_y1 = prev_line["y1"] if (prev_line and prev_line.get("col_idx") == item.get("col_idx")) else None

            # 1. Check Standalone Heading Level (H1, H2, H3, H4)
            h_level = self._classify_heading_level(item, prev_y1=prev_y1, promote_headings=promote_headings)
            if h_level is not None:
                flush_paragraph()
                clean_h = update_heading_stack(h_level, l_text)
                prefix = "#" * h_level
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
                col_min_x = item.get("col_min_x", min(bbox[0], prev_line["x0"]))
                col_max_x = item.get("col_max_x", max(bbox[2], prev_line["x1"]))

                large_v_gap = v_gap > 0.65 * self.mu_h
                prev_short_line = (
                    prev_line["x1"] < col_max_x - 3.5 * self.mu_w
                    and prev_line["text"].rstrip().endswith((".", "!", "?", ":"))
                )
                first_line_indent = (
                    (bbox[0] - col_min_x) >= 1.2 * self.mu_w
                    and (bbox[0] - prev_line["x0"]) >= 1.0 * self.mu_w
                )

                if not same_col or large_v_gap or prev_short_line or first_line_indent:
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
    """Initializes and caches a CPU-only Docling DocumentConverter instance."""
    if not _DOCLING_AVAILABLE:
        return None

    if do_ocr in _DOCLING_CONVERTERS:
        return _DOCLING_CONVERTERS[do_ocr]

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

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    _DOCLING_CONVERTERS[do_ocr] = converter
    return converter


def _serialize_docling_ast(doc: Any, hierarchy_tree: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Serializes a DoclingDocument AST into Markdown while calibrating H1-H4 levels
    using the typography/TOC hierarchy tree from AdaptivePdfLayoutEngine.
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
    - Slices requested page into a temporary 1-page PDF buffer
    - Auto-detects whether OCR is necessary
    - Formats Markdown and recovers shaded callout boxes from PyMuPDF
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

    if not _DOCLING_AVAILABLE:
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

        doc_input = io.BytesIO(pdf_bytes)
        conv_res = converter.convert(doc_input)
        raw_docling_md = _serialize_docling_ast(conv_res.document, hierarchy_tree=adaptive_res.get("hierarchy_tree", []))

        if not raw_docling_md or len(raw_docling_md.strip()) < 0.25 * len(adaptive_res.get("extracted_text", "")):
            logger.info("Docling output sparse; augmenting with adaptive multi-column recovery.")
            return adaptive_res

        formatted_md = clean_pdf_text(raw_docling_md)
        callout_texts = [
            b.get("markdown", "") for b in adaptive_res.get("layout_boxes", [])
            if b.get("type") == "callout"
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
