"""
Adaptive Multi-Column Medical Layout Engine for PyMuPDF (fitz) and Docling Hybrid Recovery.
Implements per-page typography profiling, table/callout box isolation, crowded line splitting,
zone-segmented recursive XY-cut multi-column ordering, heading path detection, and cross-column stitching.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import threading
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

logger = logging.getLogger(__name__)

_FITZ_LOCK = threading.RLock()

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
    "anti",
    "non",
    "pre",
    "post",
    "sub",
    "intra",
    "extra",
    "hyper",
    "hypo",
    "auto",
    "co",
    "re",
    "beta",
    "alpha",
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
    Deterministic 6-Stage Adaptive Multi-Column Medical Layout Engine.
    Processes a PyMuPDF Page object to produce clean, column-ordered Markdown with headings and tables.
    """

    def __init__(self, page: Any, default_title: str = ""):
        self.page = page
        self.default_title = str(default_title or "").strip() or "Untitled"
        self.page_rect = (
            (0.0, 0.0, float(page.rect.width), float(page.rect.height))
            if hasattr(page, "rect") and page.rect
            else (0.0, 0.0, 612.0, 792.0)
        )
        self.page_width = self.page_rect[2] - self.page_rect[0]
        self.page_height = self.page_rect[3] - self.page_rect[1]

        # Stage 3.1 Profile
        self.mu_size: float = 10.0
        self.mu_h: float = 12.0
        self.mu_w: float = 5.0

        self.tables_markdown: List[Dict[str, Any]] = []
        self.exclusion_zones: List[Tuple[float, float, float, float]] = []
        self.callout_blocks: List[Dict[str, Any]] = []
        self.caption_blocks: List[Dict[str, Any]] = []

    def run(self, promote_headings: bool = False) -> Dict[str, Any]:
        """Executes all 6 stages of adaptive geometric layout reconstruction."""
        # Stage 3.1: Typography & Spatial Profiling
        spans, drawings = self._stage_3_1_profile()

        # Stage 3.2: Isolate Tables & Shaded Callout Boxes
        self._stage_3_2_isolate_tables_and_callouts(spans, drawings)

        # Stage 3.3: Isolate & Tether Crowded Picture Descriptions / Figure Captions
        self._stage_3_3_isolate_captions(spans)

        # Remove spans inside exclusion zones (tables, callouts, captions)
        remaining_spans = [
            s for s in spans if not self._is_excluded((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2)
        ]

        # Stage 3.4: Line Merging & Intra-Line Column Splitting
        lines = self._stage_3_4_assemble_lines(remaining_spans, drawings)

        # Stage 3.5: Zone-Segmented Recursive XY-Cut for Reading Order
        ordered_blocks = self._stage_3_5_zone_xy_cut(lines, drawings)

        # Stage 3.6: Heading Hierarchy, Lists & Cross-Column Sentence Stitching
        return self._stage_3_6_format_output(ordered_blocks, promote_headings=promote_headings)

    def _stage_3_1_profile(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Stage 3.1: Per-Page Typography & Spatial Profiling."""
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
            if b.get("type") != 0:  # text block
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

                    is_marginal = (y0 < self.page_height * 0.04) or (y1 > self.page_height * 0.96)

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
                        "is_bold": bool(flags & 2 or "bold" in font.lower()),
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
                            char_w = (x1 - x0) / len(s_text)
                            char_widths_weighted.extend([char_w] * w)

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

        return all_spans, drawings

    def _stage_3_2_isolate_tables_and_callouts(
        self, spans: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> None:
        """Stage 3.2: Isolate Tables & Shaded Callout Boxes First."""
        # 1. Native PyMuPDF Tables
        try:
            tabs = self.page.find_tables()
            for tab in tabs:
                bbox = tab.bbox
                extracted = tab.extract()
                if not extracted or len(extracted) < 2:
                    continue
                if not extracted[0] or len(extracted[0]) < 2:
                    continue

                rows_md = []
                for idx, row in enumerate(extracted):
                    cleaned_cells = [
                        str(cell or "").replace("\n", "<br>").strip().replace("|", "\\|")
                        for cell in row
                    ]
                    row_line = "| " + " | ".join(cleaned_cells) + " |"
                    rows_md.append(row_line)
                    if idx == 0:
                        separator = "| " + " | ".join(["---"] * len(cleaned_cells)) + " |"
                        rows_md.append(separator)

                table_text = "\n".join(rows_md)
                self.tables_markdown.append({
                    "bbox": bbox,
                    "markdown": table_text,
                    "type": "table",
                    "y0": float(bbox[1]),
                })
                self.exclusion_zones.append(bbox)
        except Exception as exc:
            logger.debug("find_tables error: %s", exc)

        # 2. Shaded Callout Boxes from Drawings
        for dw in drawings:
            rect = dw.get("rect")
            fill = dw.get("fill")
            if not rect or fill is None:
                continue

            r_bbox = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            r_w = r_bbox[2] - r_bbox[0]
            r_h = r_bbox[3] - r_bbox[1]

            if r_w >= 8.0 * self.mu_w and r_h >= 2.5 * self.mu_h:
                if _rect_area(r_bbox) < 0.8 * self.page_width * self.page_height:
                    if any(_rects_overlap(r_bbox, tb) for tb in self.exclusion_zones):
                        continue

                    callout_spans = [
                        s for s in spans
                        if _point_in_rect((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2, r_bbox)
                        and not self._is_excluded((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2)
                    ]

                    if callout_spans:
                        callout_spans.sort(key=lambda s: (round(s["y0"] / (self.mu_h * 0.5)), s["x0"]))
                        lines_str = []
                        curr_line = []
                        last_y = None
                        for s in callout_spans:
                            if last_y is not None and abs(s["y0"] - last_y) > self.mu_h * 0.5:
                                lines_str.append(" ".join(curr_line))
                                curr_line = []
                            curr_line.append(s["text"])
                            last_y = s["y0"]
                        if curr_line:
                            lines_str.append(" ".join(curr_line))

                        callout_text = clean_pdf_text("\n".join(lines_str))
                        if len(callout_text) >= 20:
                            bq_lines = [f"> {line}" for line in callout_text.split("\n") if line.strip()]
                            callout_md = "> **[Clinical Box]**\n" + "\n".join(bq_lines)
                            self.callout_blocks.append({
                                "bbox": r_bbox,
                                "markdown": callout_md,
                                "type": "callout",
                                "y0": r_bbox[1],
                            })
                            self.exclusion_zones.append(r_bbox)

    def _stage_3_3_isolate_captions(self, spans: List[Dict[str, Any]]) -> None:
        """Stage 3.3: Isolate & Tether Crowded Picture Descriptions / Figure Captions."""
        image_rects = []
        try:
            for img in self.page.get_images(full=True):
                xref = img[0]
                rects = self.page.get_image_rects(xref)
                for r in rects:
                    image_rects.append((float(r[0]), float(r[1]), float(r[2]), float(r[3])))
        except Exception:
            pass

        caption_pattern = re.compile(
            r"^(Fig\.|Figure|Table|Box|Plate|Diagram|Scheme|Image)\s*\d+", re.IGNORECASE
        )

        for s in spans:
            if self._is_excluded((s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2):
                continue

            s_text = s["text"].strip()
            if caption_pattern.match(s_text):
                is_near_image = any(
                    abs(s["y0"] - img_r[3]) <= 2.5 * self.mu_h or abs(s["y1"] - img_r[1]) <= 2.5 * self.mu_h
                    for img_r in image_rects
                )
                typo_shift = (s["size"] <= self.mu_size - 0.4) or s["is_italic"] or s["is_bold"]

                if is_near_image or typo_shift:
                    caption_bbox = s["bbox"]
                    self.caption_blocks.append({
                        "bbox": caption_bbox,
                        "text": s_text,
                        "type": "caption",
                        "y0": s["y0"],
                    })

    def _is_excluded(self, x: float, y: float) -> bool:
        return any(_point_in_rect(x, y, ez) for ez in self.exclusion_zones)

    def _stage_3_4_assemble_lines(
        self, spans: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Stage 3.4: Line Merging & Intra-Line Column Splitting (For Crowded Columns)."""
        if not spans:
            return []

        spans_sorted = sorted(spans, key=lambda s: (s["y0"], s["x0"]))

        lines: List[Dict[str, Any]] = []
        curr_spans: List[Dict[str, Any]] = [spans_sorted[0]]

        for s in spans_sorted[1:]:
            last_s = curr_spans[-1]
            v_overlap = min(s["y1"], last_s["y1"]) - max(s["y0"], last_s["y0"])
            min_h = min(s["height"], last_s["height"])
            if v_overlap > 0.5 * min_h or abs(s["y0"] - last_s["y0"]) < 0.4 * self.mu_h:
                curr_spans.append(s)
            else:
                lines.extend(self._split_and_build_line(curr_spans, drawings))
                curr_spans = [s]

        if curr_spans:
            lines.extend(self._split_and_build_line(curr_spans, drawings))

        return lines

    def _split_and_build_line(
        self, line_spans: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Splits a line if horizontal gaps exceed column thresholds (intra-line splitting)."""
        line_spans.sort(key=lambda s: s["x0"])
        segments: List[List[Dict[str, Any]]] = [[line_spans[0]]]

        for s in line_spans[1:]:
            prev_s = segments[-1][-1]
            gap_x = s["x0"] - prev_s["x1"]

            split_line = False
            if gap_x > 2.2 * self.mu_w:
                split_line = True
            elif gap_x > 1.1 * self.mu_w:
                mid_x = (prev_s["x1"] + s["x0"]) / 2.0
                for dw in drawings:
                    r = dw.get("rect")
                    if r and abs(r[2] - r[0]) < 2.0 and abs(mid_x - r[0]) < 3.0:
                        split_line = True
                        break

            if split_line:
                segments.append([s])
            else:
                segments[-1].append(s)

        result_lines = []
        for seg in segments:
            seg_text = " ".join(s["text"] for s in seg)
            x0 = min(s["x0"] for s in seg)
            x1 = max(s["x1"] for s in seg)
            y0 = min(s["y0"] for s in seg)
            y1 = max(s["y1"] for s in seg)
            max_size = max(s["size"] for s in seg)
            is_bold = any(s["is_bold"] for s in seg)
            is_italic = any(s["is_italic"] for s in seg)

            result_lines.append({
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

        return result_lines

    def _stage_3_5_zone_xy_cut(
        self, lines: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Stage 3.5: Zone-Segmented Recursive XY-Cut for Multi-Column Reading Order."""
        non_text = self.tables_markdown + self.callout_blocks + self.caption_blocks
        if not lines:
            return sorted(non_text, key=lambda b: b.get("y0", 0.0))

        # Detect Column Gutters via 1D X-histogram
        gutters = self._detect_gutters(lines, drawings)

        # Detect Spanning Dividers (headings or elements crossing gutters)
        spanning_dividers: List[float] = []
        for line in lines:
            line_w = line["x1"] - line["x0"]
            is_heading_candidate = line["size"] >= 1.15 * self.mu_size or (
                line["is_bold"] and line["size"] >= self.mu_size
            )
            crosses_gutter = any(
                line["x0"] <= g[0] - 1.5 * self.mu_w and line["x1"] >= g[1] + 1.5 * self.mu_w
                for g in gutters
            )

            if (line_w >= 0.7 * self.page_width or crosses_gutter) and is_heading_candidate:
                spanning_dividers.append(line["y0"])

        spanning_dividers.sort()
        divider_y_set = set(spanning_dividers)

        # Partition lines into horizontal zones at spanning divider lines
        zones: List[List[Dict[str, Any]]] = []
        current_zone: List[Dict[str, Any]] = []

        for line in sorted(lines, key=lambda l: l["y0"]):
            if line["y0"] in divider_y_set:
                if current_zone:
                    zones.append(current_zone)
                    current_zone = []
                zones.append([line])
            else:
                current_zone.append(line)

        if current_zone:
            zones.append(current_zone)

        # Order Column-First inside each Zone
        ordered_text_lines: List[Dict[str, Any]] = []
        for zone in zones:
            if not gutters or len(zone) <= 1:
                ordered_text_lines.extend(sorted(zone, key=lambda l: l["y0"]))
            else:
                cols: Dict[int, List[Dict[str, Any]]] = {}
                for line in zone:
                    mid_x = (line["x0"] + line["x1"]) / 2.0
                    col_idx = 0
                    for g_i, g in enumerate(gutters):
                        if mid_x > g[1]:
                            col_idx = g_i + 1
                    cols.setdefault(col_idx, []).append(line)

                for c_k in sorted(cols.keys()):
                    ordered_text_lines.extend(sorted(cols[c_k], key=lambda l: l["y0"]))

        # Interleave Tables, Callouts, and Captions into column-ordered text without disturbing column order
        all_ordered = list(ordered_text_lines)
        for nt in sorted(non_text, key=lambda b: b.get("y0", 0.0)):
            inserted = False
            for idx, tl in enumerate(all_ordered):
                if tl.get("y0", 0.0) > nt.get("y0", 0.0):
                    all_ordered.insert(idx, nt)
                    inserted = True
                    break
            if not inserted:
                all_ordered.append(nt)

        return all_ordered

    def _detect_gutters(
        self, lines: List[Dict[str, Any]], drawings: List[Dict[str, Any]]
    ) -> List[Tuple[float, float]]:
        """Detects candidate vertical column gutters on the page."""
        hist = [0] * int(self.page_width + 1)
        for line in lines:
            line_w = line["x1"] - line["x0"]
            if line_w < 0.6 * self.page_width:
                for x in range(int(max(0, line["x0"])), int(min(self.page_width, line["x1"]))):
                    hist[x] += 1

        gutters: List[Tuple[float, float]] = []
        in_gutter = False
        g_start = 0

        x_min = int(self.page_width * 0.1)
        x_max = int(self.page_width * 0.9)

        for x in range(x_min, x_max):
            if hist[x] <= 1:
                if not in_gutter:
                    in_gutter = True
                    g_start = x
            else:
                if in_gutter:
                    in_gutter = False
                    g_w = x - g_start
                    if g_w >= 2.0 * self.mu_w:
                        gutters.append((float(g_start), float(x)))
                    elif g_w >= 0.85 * self.mu_w:
                        mid_g = (g_start + x) / 2.0
                        for dw in drawings:
                            r = dw.get("rect")
                            if r and abs(r[2] - r[0]) < 2.0 and abs(mid_g - r[0]) < 4.0:
                                gutters.append((float(g_start), float(x)))
                                break

        return gutters

    def _stage_3_6_format_output(
        self, ordered_blocks: List[Dict[str, Any]], promote_headings: bool = False
    ) -> Dict[str, Any]:
        """Stage 3.6: Heading Hierarchy, Lists & Cross-Column Sentence Stitching."""
        output_parts: List[str] = []
        headings_found: List[str] = []
        current_paragraph: List[str] = []
        last_line_dict: Optional[Dict[str, Any]] = None

        def flush_paragraph():
            nonlocal current_paragraph
            if current_paragraph:
                p_text = clean_pdf_text(" ".join(current_paragraph))
                if p_text:
                    output_parts.append(p_text)
                current_paragraph = []

        for item in ordered_blocks:
            b_type = item.get("type", "text")

            if b_type in {"table", "callout"}:
                flush_paragraph()
                md_str = item.get("markdown", "").strip()
                if md_str:
                    output_parts.append(md_str)
                last_line_dict = None
                continue

            if b_type == "caption":
                flush_paragraph()
                cap_text = item.get("text", "").strip()
                if cap_text:
                    output_parts.append(f"> *Caption: {cap_text}*")
                last_line_dict = None
                continue

            l_text = item.get("text", "").strip()
            if not l_text:
                continue

            sz = item.get("size", self.mu_size)
            is_bold = item.get("is_bold", False)

            is_h2 = sz >= 1.30 * self.mu_size
            is_h3 = sz >= 1.12 * self.mu_size and (is_bold or len(l_text) < 90)
            is_h4 = (
                sz >= 0.98 * self.mu_size
                and is_bold
                and len(l_text) < 75
                and not l_text.endswith(".")
            )

            if is_h2 or is_h3 or is_h4 or promote_headings:
                flush_paragraph()
                prefix = "## " if is_h2 else ("### " if is_h3 else "#### ")
                output_parts.append(f"{prefix}{l_text}")
                headings_found.append(l_text)
                last_line_dict = None
                continue

            if last_line_dict is not None and current_paragraph:
                prev_text = current_paragraph[-1]
                no_terminal_punct = not prev_text.rstrip().endswith((".", "!", "?", ":", ";"))
                starts_lower = l_text[0].islower() if l_text else False

                if no_terminal_punct and starts_lower:
                    current_paragraph.append(l_text)
                else:
                    flush_paragraph()
                    current_paragraph.append(l_text)
            else:
                current_paragraph.append(l_text)

            last_line_dict = item

        flush_paragraph()

        final_md = "\n\n".join(output_parts).strip()
        h_path = " > ".join([self.default_title] + headings_found[:2]) if headings_found else self.default_title
        subheading = headings_found[-1] if headings_found else self.default_title

        return {
            "text": final_md,
            "extracted_text": final_md,
            "heading_path": h_path,
            "subheading": subheading,
            "headings": headings_found,
        }


def extract_page_with_adaptive_layout(
    file_path: Union[str, Path], page_number: int, default_title: str = "", promote_headings: bool = False
) -> Dict[str, Any]:
    """Helper executing the Adaptive Multi-Column Geometric Engine on a specific PDF page."""
    if fitz is None:
        logger.warning("PyMuPDF (fitz) is not installed; cannot execute adaptive layout engine.")
        return {
            "text": "",
            "extracted_text": "",
            "heading_path": default_title or "Untitled",
            "subheading": default_title or "Untitled",
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
                    "heading_path": title,
                    "subheading": title,
                }

            page = doc[page_idx]
            engine = AdaptivePdfLayoutEngine(page, default_title=title)
            return engine.run(promote_headings=promote_headings)
