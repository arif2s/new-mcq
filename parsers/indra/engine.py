from __future__ import annotations

import copy
import logging
import math
import os
import re
import tempfile
import threading
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

import numpy as np

import config
from parsers.indra.profiles import LAYOUT_PROFILES, LayoutProfile
from parsers.indra.rules import (
    RegexCatalog,
    enrich_lines,
    is_description_list_rows,
    overlap_ratio,
    rgb_distance,
    rgb_saturation,
)
from parsers.indra.strategies import LayoutStrategyRegistry
from parsers.indra.types import (
    BBox,
    HeadingPrototype,
    PageElement,
    PageElements,
    RGB,
    TableGrid,
    ThreadSafeLRUCache,
)

logger = logging.getLogger(__name__)

DEFAULT_WIN_THREADS: int = max(1, min(4, (os.cpu_count() or 4) // 2))

DATA_DIR: Path = Path(__file__).resolve().parent.parent.parent / "data"
MODELS_DIR: Path = DATA_DIR / "models"
INDRA_REPO_ID: str = "datalab-to/indra-ocr"
INDRA_MODELS_DIR: Path = getattr(
    config, "INDRA_MODELS_DIR", MODELS_DIR / "indra_ocr"
)

REQUIRED_CORE_FILES: tuple[str, ...] = (
    "det.onnx",
    "rec.onnx",
    "order.onnx",
    "meta.json",
)

REQUIRED_MATH_FILES: tuple[str, ...] = (
    "math_meta.json",
    "mathcls.onnx",
    "math_enc.onnx",
    "math_dec_init.onnx",
    "math_dec_step.onnx",
)


def get_indra_models_dir() -> Path:
    INDRA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return INDRA_MODELS_DIR


def is_indra_models_downloaded(include_math: bool = False) -> bool:
    model_dir = get_indra_models_dir()
    for fname in REQUIRED_CORE_FILES:
        fp = model_dir / fname
        if not fp.is_file() or fp.stat().st_size <= 128:
            return False
    if include_math:
        for fname in REQUIRED_MATH_FILES:
            fp = model_dir / fname
            if not fp.is_file() or fp.stat().st_size <= 128:
                return False
    return True


def download_indra_models(include_math: bool = False) -> dict[str, Any]:
    from huggingface_hub import hf_hub_download

    model_dir = get_indra_models_dir()
    files_to_fetch = list(REQUIRED_CORE_FILES)
    if include_math:
        files_to_fetch.extend(REQUIRED_MATH_FILES)

    downloaded = []
    for fname in files_to_fetch:
        target_path = model_dir / fname
        if not target_path.is_file() or target_path.stat().st_size <= 128:
            logger.info("Downloading Indra OCR component '%s'...", fname)
            hf_hub_download(
                repo_id=INDRA_REPO_ID,
                filename=fname,
                local_dir=str(model_dir),
                local_dir_use_symlinks=False,
            )
            downloaded.append(fname)

    return {
        "status": "success",
        "model_dir": str(model_dir),
        "downloaded_files": downloaded,
        "complete": is_indra_models_downloaded(include_math=include_math),
    }


@contextmanager
def _scoped_cpu_execution_env():
    """Scopes CPU execution and offline flags strictly during model init without corrupting process CUDA."""
    prev_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    prev_hf = os.environ.get("HF_HUB_OFFLINE")
    prev_tf = os.environ.get("TRANSFORMERS_OFFLINE")
    try:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        if is_indra_models_downloaded(include_math=False):
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        yield
    finally:
        if prev_cuda is not None:
            os.environ["CUDA_VISIBLE_DEVICES"] = prev_cuda
        else:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        if prev_hf is not None:
            os.environ["HF_HUB_OFFLINE"] = prev_hf
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)
        if prev_tf is not None:
            os.environ["TRANSFORMERS_OFFLINE"] = prev_tf
        else:
            os.environ.pop("TRANSFORMERS_OFFLINE", None)


_CONVERTER_CACHE: dict[tuple, Any] = {}
_CONVERTER_LOCK = threading.Lock()


def get_indra_converter(
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 200,
    threads: int = DEFAULT_WIN_THREADS,
    image_ocr: bool = False,
    table_heads: bool = True,
) -> Any:
    key = (mode, math, furniture, dpi, threads, image_ocr, table_heads)
    with _CONVERTER_LOCK:
        if key in _CONVERTER_CACHE:
            return _CONVERTER_CACHE[key]

        with _scoped_cpu_execution_env():
            import indra_ocr

            model_dir = get_indra_models_dir()
            if not is_indra_models_downloaded(include_math=math):
                raise RuntimeError(
                    f"Indra OCR ONNX models are not downloaded in '{model_dir}'."
                )

            conv = indra_ocr.load(
                path=str(model_dir),
                mode=mode,
                math=math,
                furniture=furniture,
                dpi=float(dpi),
                threads=threads,
                image_ocr=image_ocr,
            )
            _CONVERTER_CACHE[key] = conv
            return conv


class FallbackChain:
    """Executes a chain of provider functions with validation, returning the first valid result."""

    @classmethod
    def execute(
        cls,
        providers: Sequence[tuple[str, Callable[[], Optional[dict[str, Any]]]]],
        validator: Callable[[Optional[dict[str, Any]]], bool],
        default: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        for name, provider in providers:
            try:
                res = provider()
                if validator(res):
                    return res  # type: ignore[return-value]
            except Exception as e:
                logger.debug("Provider %s in FallbackChain skipped/failed: %s", name, e)
                continue
        return default()


def reconstruct_2d_table_grid(
    cell_items: Sequence[dict[str, Any]],
    table_bbox: BBox,
) -> Optional[TableGrid]:
    """Reconstructs a 2D TableGrid using horizontal interval clustering and line wrap consolidation."""
    if len(cell_items) < 2:
        return None
    tb_w = max(1e-4, table_bbox[2] - table_bbox[0])

    # 1. Group into Y bands
    sorted_cells = sorted(
        cell_items,
        key=lambda c: (0.5 * (float(c["bbox_norm"][1]) + float(c["bbox_norm"][3])), float(c["bbox_norm"][0])),
    )
    r_bands: list[list[dict[str, Any]]] = []
    for item in sorted_cells:
        bb = item["bbox_norm"]
        cy = 0.5 * (float(bb[1]) + float(bb[3]))
        h_item = max(0.008, float(bb[3]) - float(bb[1]))
        if r_bands:
            prev_bb = r_bands[-1][0]["bbox_norm"]
            prev_cy = 0.5 * (float(prev_bb[1]) + float(prev_bb[3]))
            prev_h = max(0.008, float(prev_bb[3]) - float(prev_bb[1]))
            if abs(cy - prev_cy) <= 0.48 * min(h_item, prev_h):
                r_bands[-1].append(item)
                continue
        r_bands.append([item])

    if len(r_bands) < 2:
        return None

    # 2. Discover column intervals
    col_intervals: list[tuple[float, float]] = []
    for rb in r_bands:
        rb.sort(key=lambda c: float(c["bbox_norm"][0]))
        if len(rb) == 1 and (float(rb[0]["bbox_norm"][2]) - float(rb[0]["bbox_norm"][0])) >= 0.60 * tb_w:
            continue
        for c in rb:
            cx0, cx1 = float(c["bbox_norm"][0]), float(c["bbox_norm"][2])
            if (cx1 - cx0) >= 0.65 * tb_w:
                continue
            merged = False
            for idx_iv, (ix0, ix1) in enumerate(col_intervals):
                ov = min(cx1, ix1) - max(cx0, ix0)
                min_w = max(1e-4, min(cx1 - cx0, ix1 - ix0))
                if ov >= 0.25 * min_w or abs(0.5 * (cx0 + cx1) - 0.5 * (ix0 + ix1)) <= 0.018:
                    col_intervals[idx_iv] = (min(cx0, ix0), max(cx1, ix1))
                    merged = True
                    break
            if not merged:
                col_intervals.append((cx0, cx1))

    col_intervals.sort(key=lambda iv: 0.5 * (iv[0] + iv[1]))
    consolidated_cols: list[tuple[float, float]] = []
    for ix0, ix1 in col_intervals:
        if consolidated_cols and min(ix1, consolidated_cols[-1][1]) - max(ix0, consolidated_cols[-1][0]) >= 0.20 * min(
            ix1 - ix0, consolidated_cols[-1][1] - consolidated_cols[-1][0]
        ):
            consolidated_cols[-1] = (min(consolidated_cols[-1][0], ix0), max(consolidated_cols[-1][1], ix1))
        else:
            consolidated_cols.append((ix0, ix1))

    num_cols = len(consolidated_cols)
    if num_cols < 2:
        rows_fallback = [[str(c["text"]).strip() for c in rb] for rb in r_bands if rb]
        return TableGrid(table_bbox, rows_fallback) if max(len(r) for r in rows_fallback) >= 2 else None

    def _assign_col(c_bb: BBox) -> int:
        cx0, cx1 = float(c_bb[0]), float(c_bb[2])
        c_mid = 0.5 * (cx0 + cx1)
        best_j = 0
        best_score = -1e9
        for j, (ax0, ax1) in enumerate(consolidated_cols):
            ov = max(0.0, min(cx1, ax1) - max(cx0, ax0))
            dist = abs(c_mid - 0.5 * (ax0 + ax1))
            sc = ov - 0.45 * dist
            if sc > best_score:
                best_score = sc
                best_j = j
        return best_j

    grid_rows: list[list[str]] = []
    row_y_bounds: list[tuple[float, float]] = []
    for rb in r_bands:
        y0_r = min(float(c["bbox_norm"][1]) for c in rb)
        y1_r = max(float(c["bbox_norm"][3]) for c in rb)
        row_cells = [""] * num_cols
        if len(rb) == 1 and (float(rb[0]["bbox_norm"][2]) - float(rb[0]["bbox_norm"][0])) >= 0.65 * tb_w:
            row_cells[0] = str(rb[0]["text"]).strip()
        else:
            for c in rb:
                j = _assign_col(c["bbox_norm"])
                t_val = str(c["text"]).strip()
                row_cells[j] = f"{row_cells[j]} {t_val}".strip() if row_cells[j] else t_val

        non_empty_js = [j for j, val in enumerate(row_cells) if val]
        prev_h = max(0.008, row_y_bounds[-1][1] - row_y_bounds[-1][0]) if row_y_bounds else 0.02
        if (
            len(grid_rows) >= 2
            and len(non_empty_js) == 1
            and (y0_r - row_y_bounds[-1][1]) <= 0.45 * prev_h
            and all(bool(grid_rows[-1][j]) for j in non_empty_js)
            and not any(RegexCatalog.BULLET_MARKER.match(row_cells[j]) for j in non_empty_js)
        ):
            for j in non_empty_js:
                grid_rows[-1][j] = RegexCatalog.clean_text(f"{grid_rows[-1][j]} {row_cells[j]}")
            row_y_bounds[-1] = (row_y_bounds[-1][0], max(row_y_bounds[-1][1], y1_r))
        else:
            grid_rows.append(row_cells)
            row_y_bounds.append((y0_r, y1_r))

    return TableGrid(table_bbox, grid_rows) if len(grid_rows) >= 2 else None


def _find_strip_gutter_bins(boxes_norm: Sequence[tuple[float, float, float, float]]) -> list[set[int]]:
    strip_hist = [[0] * 100 for _ in range(4)]
    strip_counts = [0, 0, 0, 0]
    for x0, y0, x1, y1 in boxes_norm:
        if (x1 - x0) >= 0.52 or (x1 - x0) <= 0.005:
            continue
        s_idx = min(3, max(0, int(0.5 * (y0 + y1) * 4.0)))
        strip_counts[s_idx] += 1
        b0, b1 = max(0, int((x0 + 0.004) * 100)), min(99, int((x1 - 0.004) * 100))
        for b in range(b0, b1 + 1):
            strip_hist[s_idx][b] += 1
    return [{b for b in range(16, 85) if strip_hist[s][b] == 0 and strip_counts[s] >= 4} for s in range(4)]


def _crosses_dynamic_channel(
    x1_prev_norm: float, x0_curr_norm: float, y_mid_norm: float, strip_channels: list[set[int]]
) -> bool:
    if x0_curr_norm <= x1_prev_norm:
        return False
    ch_bins = strip_channels[min(3, max(0, int(y_mid_norm * 4.0)))]
    if not ch_bins:
        return False
    b0, b1 = max(0, int(x1_prev_norm * 100) - 1), min(99, int(x0_curr_norm * 100) + 1)
    return any(b in ch_bins for b in range(b0, b1 + 1))


_SCANNED_CACHE = ThreadSafeLRUCache[tuple, dict[str, Any]](maxsize=32)


class IndraModularEngine:
    """Core modular engine coordinating extraction backends with fallback resilience."""

    def __init__(
        self,
        profile_id: Optional[str] = None,
        mode: str = "auto",
        math: bool = False,
        furniture: bool = False,
        dpi: int = 150,
        threads: int = DEFAULT_WIN_THREADS,
    ) -> None:
        self.explicit_profile_id = profile_id
        self.mode = mode
        self.math = math
        self.furniture = furniture
        self.dpi = dpi
        self.threads = threads
        self.active_stack: dict[int, HeadingPrototype] = {}
        self.active_titles: dict[int, str] = {}
        self.chapter_prototypes: dict[str, list[HeadingPrototype]] = defaultdict(list)
        self.document_toc: list[dict[str, Any]] = []
        self._sticky_profile_id: Optional[str] = None

    def resolve_profile(self, ir: PageElements, has_native_spans: bool) -> LayoutProfile:
        if self.explicit_profile_id and self.explicit_profile_id in LAYOUT_PROFILES:
            return LAYOUT_PROFILES[self.explicit_profile_id]

        full_page_txt = " ".join(e.text for e in ir.elements)
        if has_native_spans and (
            RegexCatalog.CITATION_OR_DOI.search(full_page_txt)
            or RegexCatalog.JOURNAL_HEADER.search(full_page_txt)
        ):
            self._sticky_profile_id = "academic_article"
            return LAYOUT_PROFILES["academic_article"]

        if getattr(self, "_sticky_profile_id", None) in LAYOUT_PROFILES and has_native_spans:
            return LAYOUT_PROFILES[self._sticky_profile_id]

        if has_native_spans:
            return LAYOUT_PROFILES["native_digital"]

        narrow_ratio = sum(1 for e in ir.elements if 0.15 <= e.width_norm <= 0.48) / max(1, len(ir.elements))
        return LAYOUT_PROFILES["scanned_two_column_textbook" if narrow_ratio >= 0.35 else "illustrated_booklet"]

    def extract_page_ir(self, pdf_path: Union[str, Path], page_number: int = 1) -> PageElements:
        path = Path(pdf_path)
        raw_data = FallbackChain.execute(
            providers=[
                ("indra_ocr", lambda: self._ingest_via_indra_ocr(path, page_number)),
                ("pdfplumber", lambda: self._ingest_via_pdfplumber(path, page_number)),
                ("pypdfium2", lambda: self._ingest_via_pdfium_ocr(path, page_number)),
            ],
            validator=lambda d: bool(d and (d.get("lines") or d.get("tables"))),
            default=lambda: {
                "width": 612.0,
                "height": 792.0,
                "has_spans": False,
                "lines": [],
                "tables": [],
                "figures": [],
                "banners": [],
                "underlines": [],
                "callouts": [],
                "source": "fallback",
            },
        )
        return self._build_ir_from_raw(raw_data, page_number)

    def _ingest_via_indra_ocr(self, path: Path, page_number: int) -> Optional[dict[str, Any]]:
        conv = get_indra_converter(
            mode=self.mode,
            math=self.math,
            furniture=self.furniture,
            dpi=self.dpi,
            threads=self.threads,
        )
        import pypdfium2 as pdfium

        src_doc = pdfium.PdfDocument(str(path))
        try:
            page_idx = page_number - 1
            if page_idx < 0 or page_idx >= len(src_doc):
                return None

            page_obj = src_doc[page_idx]
            scale = float(self.dpi) / 72.0
            bitmap = page_obj.render(scale=scale)
            pil_img = bitmap.to_pil()
            bitmap.close()
            w_px, h_px = float(pil_img.size[0]), float(pil_img.size[1])
            pil_img.close()

            # Execute native Indra OCR through its pdf_page pipeline
            indra_page = conv.pdf_page(str(path), src_doc, page_idx)
        finally:
            src_doc.close()

        # Serialize indra_page
        ser_lines = []
        ser_blocks = []
        ser_tables = []
        for blk in getattr(indra_page, "blocks", []):
            if isinstance(blk, dict):
                ser_blocks.append({
                    "label": blk.get("label", "Text"),
                    "bbox": list(blk.get("bbox", [])),
                    "text": str(blk.get("text", "")).strip(),
                })
            else:
                ser_blocks.append({
                    "label": getattr(blk, "label", "Text"),
                    "bbox": list(getattr(blk, "bbox", [])),
                    "text": str(getattr(blk, "text", "")).strip(),
                })
        for ln in getattr(indra_page, "lines", []):
            bx = getattr(ln, "bbox", getattr(ln, "box", [0, 0, 0, 0]))
            t = str(getattr(ln, "text", "")).strip()
            if t and len(bx) == 4:
                ser_lines.append({
                    "text": t,
                    "box": list(bx),
                    "conf": float(getattr(ln, "confidence", 1.0)),
                })

        w_pt = round(w_px * (72.0 / max(72.0, float(self.dpi))), 2)
        h_pt = round(h_px * (72.0 / max(72.0, float(self.dpi))), 2)

        lines = []
        for item in ser_lines:
            bx = item["box"]
            bb_n = (
                max(0.0, float(bx[0]) / w_px),
                max(0.0, float(bx[1]) / h_px),
                min(1.0, float(bx[2]) / w_px),
                min(1.0, float(bx[3]) / h_px),
            )
            lines.append({
                "text": item["text"],
                "bbox_norm": bb_n,
                "box": bx,
                "size_pt": max(7.5, (float(bx[3]) - float(bx[1])) * (72.0 / max(72.0, float(self.dpi))) * 0.80),
                "fg_rgb": (0, 0, 0),
                "bg_rgb": (255, 255, 255),
                "font_family": "serif",
                "is_bold": False,
                "is_italic": False,
                "conf": item.get("conf", 1.0),
            })

        actual_source = getattr(indra_page, "source", "indra_ocr")
        if actual_source == "ocr":
            actual_source = "indra_ocr"
        return {
            "width": w_pt,
            "height": h_pt,
            "width_px": w_px,
            "height_px": h_px,
            "has_spans": getattr(indra_page, "source", "") == "text",
            "lines": lines,
            "blocks": ser_blocks,
            "tables": ser_tables,
            "figures": [],
            "banners": [],
            "underlines": [],
            "callouts": [],
            "source": actual_source,
        }

    def _ingest_via_pdfplumber(self, path: Path, page_number: int) -> Optional[dict[str, Any]]:
        if self.mode == "ocr":
            return None
        import pypdfium2 as _pdfium

        p_doc = _pdfium.PdfDocument(str(path))
        try:
            p_obj = p_doc[page_number - 1]
            tp = p_obj.get_textpage()
            n_rects = tp.count_rects()
            tp.close()
            p_obj.close()
            if n_rects == 0:
                return None
        finally:
            p_doc.close()

        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            page = pdf.pages[page_number - 1]
            w, h = float(page.width), float(page.height)
            try:
                if len(page.chars or []) < 3:
                    return None
                tables = [
                    TableGrid((t.bbox[0] / w, t.bbox[1] / h, t.bbox[2] / w, t.bbox[3] / h), rows)
                    for t in page.find_tables()
                    if len(rows := [[str(c or "").strip() for c in r] for r in (t.extract() or []) if any(c for c in r)]) >= 2
                    and max(len(r) for r in rows) >= 2
                    and not is_description_list_rows(rows)
                ]
                words = page.extract_words(
                    x_tolerance=2.5,
                    y_tolerance=3.0,
                    keep_blank_chars=False,
                    extra_attrs=["fontname", "size", "non_stroking_color", "upright"],
                ) or []
                if not words:
                    return None

                # Subscript / Superscript handling with relative sizing
                words.sort(key=lambda x: (float(x["bottom"]), float(x["x0"])))
                row_buckets: list[list[dict[str, Any]]] = []
                for wd in words:
                    if (
                        row_buckets
                        and abs(float(wd["bottom"]) - float(row_buckets[-1][0]["bottom"])) <= 2.4
                        and abs(float(wd.get("size") or 10.0) - float(row_buckets[-1][-1].get("size") or 10.0)) <= 1.4
                    ):
                        row_buckets[-1].append(wd)
                    else:
                        row_buckets.append([wd])

                # Intra-row column splitting using dynamic strip channels
                word_boxes_norm = [
                    (float(wd["x0"]) / w, float(wd["top"]) / h, float(wd["x1"]) / w, float(wd["bottom"]) / h)
                    for wd in words
                ]
                strip_channels = _find_strip_gutter_bins(word_boxes_norm)
                segments: list[list[dict[str, Any]]] = []
                for row in row_buckets:
                    row.sort(key=lambda x: float(x["x0"]))
                    cur = [row[0]]
                    for wd in row[1:]:
                        prev = cur[-1]
                        sz = max(8.0, float(wd.get("size") or prev.get("size") or 10.0))
                        gap = float(wd["x0"]) - float(prev["x1"])
                        y_mid = 0.5 * (float(wd["top"]) + float(wd["bottom"])) / h
                        crosses_ch = gap >= max(5.5, 0.72 * sz) and _crosses_dynamic_channel(
                            float(prev["x1"]) / w, float(wd["x0"]) / w, y_mid, strip_channels
                        )
                        if gap > max(11.5, 1.65 * sz) or crosses_ch:
                            segments.append(cur)
                            cur = [wd]
                        else:
                            cur.append(wd)
                    segments.append(cur)

                lines = []
                for seg in segments:
                    seg_words = [str(x.get("text", "")) for x in seg]
                    txt = RegexCatalog.clean_text(" ".join(seg_words))
                    if not txt:
                        continue
                    x0 = min(float(x["x0"]) for x in seg)
                    y0 = min(float(x["top"]) for x in seg)
                    x1 = max(float(x["x1"]) for x in seg)
                    y1 = max(float(x["bottom"]) for x in seg)
                    sz = max((float(x.get("size") or 10.0) for x in seg), default=max(8.0, y1 - y0))
                    fn = str(seg[0].get("fontname") or "serif")
                    is_b = any("bold" in str(x.get("fontname", "")).lower() or "black" in str(x.get("fontname", "")).lower() for x in seg)
                    is_i = any("ital" in str(x.get("fontname", "")).lower() or "oblique" in str(x.get("fontname", "")).lower() for x in seg)
                    lines.append({
                        "text": txt,
                        "bbox_norm": (x0 / w, y0 / h, x1 / w, y1 / h),
                        "size_pt": sz,
                        "fg_rgb": (0, 0, 0),
                        "font_family": fn,
                        "is_bold": is_b,
                        "is_italic": is_i,
                    })

                banners = [
                    (
                        (float(r["x0"]) / w, float(r["top"]) / h, float(r["x1"]) / w, float(r["bottom"]) / h),
                        (int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)),
                    )
                    for r in (page.rects or [])
                    if isinstance(c := r.get("non_stroking_color"), (list, tuple))
                    and len(c) == 3
                    and all(isinstance(v, (int, float)) for v in c)
                    and (float(r["x1"]) - float(r["x0"])) >= 0.22 * w
                    and 6.0 <= (float(r["bottom"]) - float(r["top"])) <= 65.0
                ]
                return {
                    "width": w,
                    "height": h,
                    "has_spans": bool(lines),
                    "lines": lines,
                    "tables": tables,
                    "figures": [],
                    "banners": banners,
                    "underlines": [],
                    "callouts": [],
                    "source": "text",
                }
            finally:
                page.flush_cache()

    def _ingest_via_pdfium_ocr(self, path: Path, page_number: int) -> Optional[dict[str, Any]]:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[page_number - 1]
            w, h, tp = float(page.get_width()), float(page.get_height()), page.get_textpage()
            try:
                lines = [
                    {
                        "text": txt,
                        "bbox_norm": (x0 / w, max(0.0, 1.0 - (y1 / h)), x1 / w, min(1.0, 1.0 - (y0 / h))),
                        "size_pt": max(8.0, abs(y1 - y0)),
                        "fg_rgb": (0, 0, 0),
                        "font_family": "serif",
                        "is_bold": False,
                        "is_italic": False,
                    }
                    for idx in range(tp.count_rects())
                    for x0, y0, x1, y1 in [tp.get_rect(idx)]
                    if (txt := RegexCatalog.clean_text(tp.get_text_bounded(x0, y0, x1, y1)))
                ]
            finally:
                tp.close()
            page.close()
            if lines:
                return {
                    "width": w,
                    "height": h,
                    "has_spans": True,
                    "lines": lines,
                    "tables": [],
                    "figures": [],
                    "banners": [],
                    "underlines": [],
                    "callouts": [],
                    "source": "text",
                }
        finally:
            doc.close()
        return None

    def _build_ir_from_raw(self, raw: dict[str, Any], page_number: int) -> PageElements:
        w_ref = float(raw.get("width", 612.0))
        h_ref = float(raw.get("height", 792.0))
        raw_lines = enrich_lines(
            list(raw.get("lines", [])),
            px_per_pt=float(self.dpi) / 72.0,
            w_ref=w_ref,
            h_ref=h_ref,
        )
        detected_tables = list(raw.get("tables", []))

        # Base elements
        elements: list[PageElement] = []
        body_sizes = [float(ln.get("size_pt", 10.0)) for ln in raw_lines]
        median_body_size = float(np.median(body_sizes)) if body_sizes else 10.0

        for idx, ln in enumerate(raw_lines):
            t = str(ln.get("text", "")).strip()
            if not t:
                continue
            bb = ln.get("bbox_norm", (0.0, 0.0, 1.0, 1.0))
            sz = float(ln.get("size_pt", 10.0))
            rel_sz = round(sz / max(6.0, median_body_size), 2)
            fg = tuple(ln.get("fg_rgb", (0, 0, 0)))
            is_b = bool(ln.get("is_bold", False))
            is_i = bool(ln.get("is_italic", False))
            in_ban = any(
                overlap_ratio(bb, ban_bb) >= 0.45
                for ban_bb, _ in raw.get("banners", [])
            )

            raw_role = ln.get("role")
            if not raw_role and "blocks" in raw:
                # Check if this line is encompassed by a classified block from native OCR/order model
                for blk in raw.get("blocks", []):
                    b_lbl = blk.get("label", "")
                    b_box = blk.get("bbox", [])
                    if len(b_box) == 4 and b_lbl in ("Section-Header", "Title", "Header"):
                        # Check overlap with block
                        b_w = float(raw.get("width_px") or w_ref)
                        b_h = float(raw.get("height_px") or h_ref)
                        blk_norm = (b_box[0] / b_w, b_box[1] / b_h, b_box[2] / b_w, b_box[3] / b_h)
                        if overlap_ratio(bb, blk_norm) >= 0.50:
                            raw_role = "heading"
                            break

            el = PageElement(
                element_id=idx + 1,
                text=t,
                bbox_norm=bb,
                size_pt=sz,
                rel_size=rel_sz,
                fg_rgb=fg,
                in_banner=in_ban,
                is_bold=is_b,
                is_italic=is_i,
                font_family=ln.get("font_family", "serif"),
                role=raw_role or "paragraph",
            )
            elements.append(el)

        ir = PageElements(
            page_number=page_number,
            width_pt=w_ref,
            height_pt=h_ref,
            profile_id=self.explicit_profile_id or "native_digital",
            elements=elements,
            tables=detected_tables,
            banners=list(raw.get("banners", [])),
            source=raw.get("source", "text"),
        )

        profile = self.resolve_profile(ir, has_native_spans=bool(raw.get("has_spans", True)))
        ir.profile_id = profile.profile_id

        strat = LayoutStrategyRegistry.get(profile)
        strat.filter_furniture(ir)
        strat.assign_columns_and_merge_lines(ir)
        strat.score_and_classify_elements(ir)

        return ir
