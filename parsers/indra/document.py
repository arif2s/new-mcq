from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from parsers.indra.profiles import LAYOUT_PROFILES
from parsers.indra.rules import RegexCatalog, enrich_lines, overlap_ratio
from parsers.indra.strategies import LayoutStrategyRegistry
from parsers.indra.types import BBox, HeadingPrototype, PageElement, PageElements, RGB

logger = logging.getLogger(__name__)

CACHE_VERSION = "v37"


@dataclass
class DocumentHeadingTracker:
    """Tracks document-wide headings, hierarchical breadcrumbs, and cross-page state."""

    default_title: str = "Document"
    document_toc: list[dict[str, Any]] = field(default_factory=list)
    heading_mismatches: list[dict[str, Any]] = field(default_factory=list)
    active_stack: dict[int, str] = field(default_factory=dict)
    chapter_prototypes: dict[str, list[HeadingPrototype]] = field(
        default_factory=lambda: defaultdict(list)
    )
    is_full_scan: bool = False
    scanned_pages: set[int] = field(default_factory=set)
    page_toc_map: dict[int, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    page_initial_stacks: dict[int, dict[int, str]] = field(default_factory=dict)
    page_final_stacks: dict[int, dict[int, str]] = field(default_factory=dict)
    page_mismatches_map: dict[int, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def get_initial_stack_for_page(self, page_number: int) -> dict[int, str]:
        if page_number in self.page_initial_stacks:
            return dict(self.page_initial_stacks[page_number])

        # Scan document_toc for headings occurring before page_number
        stack: dict[int, str] = {}
        for entry in self.document_toc:
            p = entry.get("page", 1)
            if p >= page_number:
                break
            lvl = int(entry.get("level", 1))
            stack[lvl] = entry.get("title", "")
            for d in [k for k in list(stack) if k > lvl]:
                stack.pop(d, None)
        return stack

    def get_page_toc_entries(self, page_number: int) -> list[dict[str, Any]]:
        if page_number in self.page_toc_map:
            return self.page_toc_map[page_number]
        return [e for e in self.document_toc if e.get("page") == page_number]

    def get_mismatches_for_page(self, page_number: int) -> list[dict[str, Any]]:
        if page_number in self.page_mismatches_map:
            return self.page_mismatches_map[page_number]
        return [m for m in self.heading_mismatches if m.get("page") == page_number]


def _get_toc_disk_cache_path(file_path: Path) -> Path:
    cache_dir = Path(tempfile.gettempdir()) / "indra_toc_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    mtime = file_path.stat().st_mtime_ns if file_path.is_file() else 0
    h = hashlib.sha256(f"{file_path.resolve()}_{mtime}_{CACHE_VERSION}".encode()).hexdigest()[:24]
    return cache_dir / f"toc_{h}.json"


def _extract_c_api_vector_banners(page_raw: Any, width_pt: float, height_pt: float) -> list[tuple[BBox, RGB]]:
    """Extracts shaded background vector banners via direct PDFium C-API FPDFPage_CountObjects."""
    banners: list[tuple[BBox, RGB]] = []
    try:
        obj_count = pdfium_c.FPDFPage_CountObjects(page_raw)
        left = ctypes.c_float()
        bottom = ctypes.c_float()
        right = ctypes.c_float()
        top = ctypes.c_float()
        r = ctypes.c_uint()
        g = ctypes.c_uint()
        b = ctypes.c_uint()
        a = ctypes.c_uint()

        for idx in range(obj_count):
            obj = pdfium_c.FPDFPage_GetObject(page_raw, idx)
            obj_type = pdfium_c.FPDFPageObj_GetType(obj)
            # FPDF_PAGEOBJ_PATH is 2
            if obj_type == 2:
                if pdfium_c.FPDFPageObj_GetBounds(obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top)):
                    x0, y0, x1, y1 = left.value, bottom.value, right.value, top.value
                    w_obj = x1 - x0
                    h_obj = top.value - bottom.value
                    if w_obj >= 0.18 * width_pt and 6.0 <= h_obj <= 80.0:
                        has_fill = pdfium_c.FPDFPath_GetFillColor(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
                        if has_fill and a.value > 0:
                            rgb = (int(r.value), int(g.value), int(b.value))
                            bbox_norm: BBox = (
                                max(0.0, x0 / width_pt),
                                max(0.0, 1.0 - (y1 / height_pt)),
                                min(1.0, x1 / width_pt),
                                min(1.0, 1.0 - (y0 / height_pt)),
                            )
                            banners.append((bbox_norm, rgb))
    except Exception as e:
        logger.debug("FPDFPage_CountObjects banner extraction error: %s", e)
    return banners


def _iter_prescan_page_primitives(
    doc: pdfium.PdfDocument,
    page_number: int,
) -> tuple[float, float, list[PageElement], list[tuple[BBox, RGB]]]:
    """Prescans text rects, multi-point interior sampling, subscripts, and banners for a single page."""
    page = doc[page_number - 1]
    w = float(page.get_width())
    h = float(page.get_height())
    tp = page.get_textpage()

    raw_rects: list[dict[str, Any]] = []
    n_rects = tp.count_rects()

    for idx in range(n_rects):
        x0, y0, x1, y1 = tp.get_rect(idx)
        raw_txt = tp.get_text_bounded(x0, y0, x1, y1)
        txt = RegexCatalog.clean_text(raw_txt)
        if not txt:
            continue

        # Coordinate conversion: PDFium bottom-up y0, y1 -> top-down norm
        bx0 = max(0.0, x0 / w)
        by0 = max(0.0, 1.0 - (y1 / h))
        bx1 = min(1.0, x1 / w)
        by1 = min(1.0, 1.0 - (y0 / h))
        h_pt = max(6.0, abs(y1 - y0))

        # Multi-point interior sampling (0.25, 0.50, 0.75, 0.10)
        first_bold = False
        is_bold = False

        sample_chars = []
        raw_tp = tp.raw
        for frac in (0.25, 0.50, 0.75, 0.10):
            sx = x0 + frac * (x1 - x0)
            sy = y0 + 0.5 * (y1 - y0)
            c_idx = tp.get_index(sx, sy, 6.0, 6.0)
            if c_idx is not None and c_idx >= 0:
                sample_chars.append(c_idx)

        # Check font weight and font family
        for c_idx in sample_chars:
            font_size = pdfium_c.FPDFText_GetFontSize(raw_tp, c_idx)
            font_weight = pdfium_c.FPDFText_GetFontWeight(raw_tp, c_idx)
            if font_size > 0:
                h_pt = max(h_pt, font_size)
            if font_weight >= 600:
                is_bold = True

        raw_rects.append({
            "text": txt,
            "bbox_norm": (bx0, by0, bx1, by1),
            "size_pt": h_pt,
            "first_bold": first_bold,
            "is_bold": is_bold,
            "top": by0 * h,
            "bottom": by1 * h,
            "x0": bx0 * w,
            "x1": bx1 * w,
        })

    tp.close()

    # Extract C-API vector banners
    banners = _extract_c_api_vector_banners(page.raw, w, h)
    page.close()

    if not raw_rects:
        return w, h, [], banners

    # Subscript / Superscript horizontal row bucketing & inline script fusion (PaO2, PaCO2)
    raw_rects.sort(key=lambda x: (x["top"], x["x0"]))
    row_buckets: list[list[dict[str, Any]]] = []
    for rect in raw_rects:
        cy = 0.5 * (rect["top"] + rect["bottom"])
        h_rect = max(4.0, rect["bottom"] - rect["top"])
        placed = False
        for rb in reversed(row_buckets[-4:] if len(row_buckets) >= 4 else row_buckets):
            rb_cy = 0.5 * (rb[0]["top"] + rb[0]["bottom"])
            rb_h = max(4.0, rb[0]["bottom"] - rb[0]["top"])
            if abs(cy - rb_cy) <= 0.52 * max(h_rect, rb_h):
                rb.append(rect)
                placed = True
                break
        if not placed:
            row_buckets.append([rect])

    elements: list[PageElement] = []
    elem_id = 1

    for row in row_buckets:
        row.sort(key=lambda x: x["x0"])
        # Fuse adjacent physiological script tokens
        out_tokens: list[dict[str, Any]] = []
        for r_item in row:
            if not out_tokens:
                out_tokens.append(dict(r_item))
                continue
            prev = out_tokens[-1]
            gap_pt = r_item["x0"] - prev["x1"]
            sz_max = max(prev["size_pt"], r_item["size_pt"])
            sz_min = min(prev["size_pt"], r_item["size_pt"])
            is_script_pair = (sz_min <= 0.84 * sz_max or abs(r_item["bottom"] - prev["bottom"]) >= 0.8) and gap_pt <= 0.38 * sz_max
            is_blood_gas = gap_pt <= 2.2 and bool(re.match(r"^(?:P|Pa|Fi|Sp|Sa|a|v|O|CO|H|N|2|3|4)+$", prev["text"] + r_item["text"]))
            if is_script_pair or is_blood_gas:
                prev["text"] = f"{prev['text']}{r_item['text']}"
                prev["x1"] = r_item["x1"]
                prev["bbox_norm"] = (
                    min(prev["bbox_norm"][0], r_item["bbox_norm"][0]),
                    min(prev["bbox_norm"][1], r_item["bbox_norm"][1]),
                    max(prev["bbox_norm"][2], r_item["bbox_norm"][2]),
                    max(prev["bbox_norm"][3], r_item["bbox_norm"][3]),
                )
                prev["size_pt"] = max(prev["size_pt"], r_item["size_pt"])
            else:
                out_tokens.append(dict(r_item))

        for tok in out_tokens:
            bb = tok["bbox_norm"]
            in_ban = any(overlap_ratio(bb, ban_bb) >= 0.40 for ban_bb, _ in banners)
            el = PageElement(
                element_id=elem_id,
                text=tok["text"],
                bbox_norm=bb,
                size_pt=tok["size_pt"],
                rel_size=1.0,
                in_banner=in_ban,
                first_bold=tok["first_bold"],
                is_bold=tok["is_bold"],
            )
            elements.append(el)
            elem_id += 1

    return w, h, elements, banners


def build_document_toc_and_hierarchy(
    file_path: Path,
    default_title: str = "",
    target_page: int = 1,
    full_scan: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> DocumentHeadingTracker:
    """Builds TOC and hierarchy using Mode A (Lazy Bidirectional H1) or Mode B (Streaming Horizon)."""
    file_path = file_path.resolve()
    root_title = default_title or file_path.stem
    cache_path = _get_toc_disk_cache_path(file_path)

    # Check disk cache
    if cache_path.is_file():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("cache_version") == CACHE_VERSION and (data.get("is_full_scan") or not full_scan):
                tracker = DocumentHeadingTracker(
                    default_title=root_title,
                    document_toc=data.get("document_toc", []),
                    is_full_scan=bool(data.get("is_full_scan", False)),
                )
                for entry in tracker.document_toc:
                    p = entry.get("page", 1)
                    tracker.page_toc_map[p].append(entry)
                    tracker.scanned_pages.add(p)
                for p_str, stack in data.get("page_initial_stacks", {}).items():
                    tracker.page_initial_stacks[int(p_str)] = {int(k): str(v) for k, v in stack.items()}
                for p_str, stack in data.get("page_final_stacks", {}).items():
                    tracker.page_final_stacks[int(p_str)] = {int(k): str(v) for k, v in stack.items()}
                return tracker
        except Exception as e:
            logger.debug("Failed reading TOC cache %s: %s", cache_path, e)

    doc = pdfium.PdfDocument(str(file_path))
    total_pages = len(doc)

    tracker = DocumentHeadingTracker(default_title=root_title, is_full_scan=full_scan)
    strategy = LayoutStrategyRegistry.get("native_digital")

    # Determine page scan plan
    if full_scan:
        pages_to_scan = list(range(1, total_pages + 1))
    else:
        # Mode A: Lazy Bidirectional H1 Lookup
        # Scan backward from target_page until H1 found, plus page 1, plus forward until next H1
        scanned_set = {1, target_page}
        # Backward scan
        for p in range(target_page, 0, -1):
            scanned_set.add(p)
            if p == 1:
                break
            w, h, elems, bans = _iter_prescan_page_primitives(doc, p)
            if any(e.rel_size >= 1.40 or e.in_banner or RegexCatalog.CHAPTER_HEADER.match(e.text) for e in elems):
                break
        # Forward scan
        for p in range(target_page + 1, min(total_pages + 1, target_page + 25)):
            scanned_set.add(p)
            w, h, elems, bans = _iter_prescan_page_primitives(doc, p)
            if any(e.rel_size >= 1.40 or e.in_banner or RegexCatalog.CHAPTER_HEADER.match(e.text) for e in elems):
                break
        pages_to_scan = sorted(scanned_set)

    active_stack: dict[int, str] = {}
    current_chapter_batch: list[PageElement] = []

    for idx, p_num in enumerate(pages_to_scan):
        if progress_callback:
            progress_callback(idx + 1, len(pages_to_scan))

        tracker.page_initial_stacks[p_num] = dict(active_stack)
        w, h, elems, bans = _iter_prescan_page_primitives(doc, p_num)
        tracker.scanned_pages.add(p_num)

        if not elems:
            tracker.page_final_stacks[p_num] = dict(active_stack)
            continue

        ir = PageElements(
            page_number=p_num,
            width_pt=w,
            height_pt=h,
            profile_id="native_digital",
            elements=elems,
            banners=bans,
        )
        strategy.filter_furniture(ir)
        strategy.assign_columns_and_merge_lines(ir)
        strategy.score_and_classify_elements(ir, active_stack=active_stack)

        has_h1 = False
        for el in ir.elements:
            if el.is_suppressed_furniture or el.in_table:
                continue
            if el.role == "heading":
                lvl = int(el.heading_level or 2)
                if lvl == 1:
                    has_h1 = True
                h_title = el.text.strip()
                active_stack[lvl] = h_title
                for deeper in [k for k in list(active_stack) if k > lvl]:
                    active_stack.pop(deeper, None)

                h_path = " > ".join([root_title] + [active_stack[k] for k in sorted(active_stack)])
                el.heading_path = h_path

                entry = {
                    "id": f"toc_p{p_num}_{el.element_id}",
                    "page": p_num,
                    "level": lvl,
                    "title": h_title,
                    "heading_path": h_path,
                    "confidence": el.heading_confidence,
                    "box": el.bbox_norm,
                }
                tracker.document_toc.append(entry)
                tracker.page_toc_map[p_num].append(entry)

        tracker.page_final_stacks[p_num] = dict(active_stack)

        # Mode B Memory Management: flush chapter batch at H1 boundary or 80-page horizon
        if full_scan and (has_h1 or len(current_chapter_batch) >= 80):
            current_chapter_batch.clear()
        elif full_scan:
            current_chapter_batch.extend(ir.elements)

    doc.close()

    # Save to disk cache
    try:
        cache_data = {
            "cache_version": CACHE_VERSION,
            "is_full_scan": full_scan,
            "document_toc": tracker.document_toc,
            "page_initial_stacks": {str(k): v for k, v in tracker.page_initial_stacks.items()},
            "page_final_stacks": {str(k): v for k, v in tracker.page_final_stacks.items()},
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
    except Exception as e:
        logger.debug("Failed saving TOC disk cache: %s", e)

    return tracker


_DOCUMENT_TRACKERS: dict[str, DocumentHeadingTracker] = {}
_DOCUMENT_TRACKERS_LOCK = threading.Lock()


def get_or_create_document_tracker(
    file_path: Path,
    default_title: str = "",
    reset: bool = False,
    target_page: int = 1,
    full_scan: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> DocumentHeadingTracker:
    key = str(file_path.resolve())
    with _DOCUMENT_TRACKERS_LOCK:
        if reset or key not in _DOCUMENT_TRACKERS:
            tracker = build_document_toc_and_hierarchy(
                file_path=file_path,
                default_title=default_title,
                target_page=target_page,
                full_scan=full_scan,
                progress_callback=progress_callback,
            )
            _DOCUMENT_TRACKERS[key] = tracker
        return _DOCUMENT_TRACKERS[key]
