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
                try:
                    download_indra_models(include_math=math)
                except Exception as dl_err:
                    logger.debug("Automatic Indra model download failed: %s", dl_err)
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

    # TOC Leader-Dot Filter: reject table if >= 30% of cell items match TOC_DOT_LEADER
    dot_leader_hits = sum(1 for c in cell_items if RegexCatalog.TOC_DOT_LEADER.search(str(c.get("text", ""))))
    if dot_leader_hits >= max(2, int(0.30 * len(cell_items))):
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



def _detect_and_ocr_chromatic_pill_banners(
    rgb_arr: np.ndarray,
    existing_lines: list[dict[str, Any]],
    dpi: int = 200,
) -> tuple[list[tuple[BBox, RGB]], list[dict[str, Any]]]:
    """Detects saturated colored pill banners (e.g. white text on orange #E65100) and OCRs inverted crops."""
    h_px, w_px, _ = rgb_arr.shape
    # Downsample by 2x for fast vectorized HSV mask
    small = rgb_arr[::2, ::2].astype(np.float32)
    r, g, b = small[:, :, 0], small[:, :, 1], small[:, :, 2]
    v = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    v_safe = np.where(v > 1.0, v, 1.0)
    s = np.where(v > 1.0, ((v - mn) / v_safe) * 255.0, 0.0)

    # Chromatic background mask (saturated orange/blue/teal/purple)
    chrom_mask = (s >= 90.0) & (v >= 75.0)
    row_Chrom_ratio = chrom_mask.mean(axis=1)
    sh, sw = chrom_mask.shape

    banners: list[tuple[BBox, RGB]] = []
    recovered_lines: list[dict[str, Any]] = []

    # Group contiguous chromatic rows into horizontal bands
    in_band = False
    y0_s = 0
    for y_i in range(sh):
        if row_Chrom_ratio[y_i] >= 0.12 and not in_band:
            in_band, y0_s = True, y_i
        elif (row_Chrom_ratio[y_i] < 0.12 or y_i == sh - 1) and in_band:
            in_band = False
            y1_s = y_i
            bh_norm = (y1_s - y0_s) / float(sh)
            if not (0.012 <= bh_norm <= 0.085):
                continue
            band_mask = chrom_mask[y0_s:y1_s, :]
            col_hits = np.where(band_mask.mean(axis=0) >= 0.35)[0]
            if col_hits.size < int(0.12 * sw):
                continue
            # Split into contiguous X segments if multiple pills share a row
            splits = np.where(np.diff(col_hits) > max(4, int(0.03 * sw)))[0]
            seg_starts = [col_hits[0]] + [col_hits[idx + 1] for idx in splits]
            seg_ends = [col_hits[idx] for idx in splits] + [col_hits[-1]]
            for x0_s, x1_s in zip(seg_starts, seg_ends):
                bw_norm = (x1_s - x0_s) / float(sw)
                if bw_norm < 0.12:
                    continue
                bb_norm: BBox = (
                    max(0.0, x0_s / float(sw)),
                    max(0.0, y0_s / float(sh)),
                    min(1.0, x1_s / float(sw)),
                    min(1.0, y1_s / float(sh)),
                )
                sub_rgb = small[y0_s:y1_s, x0_s:x1_s]
                sub_s = s[y0_s:y1_s, x0_s:x1_s]
                sub_v = v[y0_s:y1_s, x0_s:x1_s]
                chrom_pixels = sub_rgb[sub_s >= 90.0]
                if chrom_pixels.size == 0:
                    continue
                mean_rgb: RGB = (
                    int(np.median(chrom_pixels[:, 0])),
                    int(np.median(chrom_pixels[:, 1])),
                    int(np.median(chrom_pixels[:, 2])),
                )
                banners.append((bb_norm, mean_rgb))

                # Check if white/bright glyphs exist inside the pill and no OCR line overlaps yet
                white_ratio = float(((sub_v >= 195.0) & (sub_s <= 55.0)).mean())
                already_has_line = any(overlap_ratio(ln["bbox_norm"], bb_norm) >= 0.35 for ln in existing_lines)
                if white_ratio >= 0.04 and not already_has_line:
                    px0, py0 = max(0, int(bb_norm[0] * w_px) - 4), max(0, int(bb_norm[1] * h_px) - 4)
                    px1, py1 = min(w_px, int(bb_norm[2] * w_px) + 4), min(h_px, int(bb_norm[3] * h_px) + 4)
                    crop_rgb = rgb_arr[py0:py1, px0:px1].astype(np.float32)
                    cv = np.maximum(np.maximum(crop_rgb[:, :, 0], crop_rgb[:, :, 1]), crop_rgb[:, :, 2])
                    cmn = np.minimum(np.minimum(crop_rgb[:, :, 0], crop_rgb[:, :, 1]), crop_rgb[:, :, 2])
                    cs = np.where(cv > 1.0, ((cv - cmn) / cv) * 255.0, 0.0)
                    # Invert so white text (high V, low S) becomes black ink (0) on white background (255)
                    inv_gray = np.clip((255.0 - cv) + 0.85 * cs, 0.0, 255.0).astype(np.uint8)
                    txt_pill = ""
                    try:
                        import pytesseract
                        from PIL import Image
                        txt_pill = pytesseract.image_to_string(
                            Image.fromarray(inv_gray), config="--oem 1 --psm 7"
                        ).strip()
                    except Exception:
                        pass
                    txt_pill = RegexCatalog.clean_text(re.sub(r"^[\|!\[\]\(\)—–\-\s]+|[\|!\[\]\(\)—–\-\s]+$", "", txt_pill))
                    if len(txt_pill) >= 3 and any(c.isalpha() for c in txt_pill):
                        bh_pt = (bb_norm[3] - bb_norm[1]) * (h_px * 72.0 / max(72.0, float(dpi)))
                        recovered_lines.append({
                            "text": txt_pill,
                            "bbox_norm": bb_norm,
                            "size_pt": max(11.5, round(bh_pt * 0.62, 2)),
                            "fg_rgb": (255, 255, 255),
                            "bg_rgb": mean_rgb,
                            "in_banner": True,
                            "font_family": "sans",
                            "is_bold": True,
                            "first_bold": True,
                            "is_italic": False,
                            "thickness": 1.45,
                        })
    return banners, recovered_lines


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

    def _align_and_interleave_toc_headings(
        self, ir: PageElements, preassigned_page_toc: list[dict[str, Any]]
    ) -> None:
        """Aligns preassigned document TOC entries to page elements without erasing valid body content."""
        def _nk(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

        for toc_e in preassigned_page_toc:
            toc_title = toc_e.get("title", "").strip()
            if not toc_title:
                continue
            toc_level = int(toc_e.get("level", 2))
            toc_nk = _nk(toc_title)
            tk_tokens = set(toc_nk.split())

            # Find matching elements on the page
            best_el: Optional[PageElement] = None
            best_score = 0.0

            for el in ir.elements:
                if el.in_table or el.is_suppressed_furniture:
                    continue
                el_nk = _nk(el.text)
                if not el_nk:
                    continue
                if el_nk == toc_nk:
                    best_el = el
                    best_score = 1.0
                    break
                # Partial token overlap
                el_toks = set(el_nk.split())
                if tk_tokens and el_toks.issubset(tk_tokens):
                    sc = len(el_toks) / len(tk_tokens)
                    if sc > best_score:
                        best_score = sc
                        best_el = el

            if best_el is not None and best_score >= 0.50:
                best_el.role = "heading"
                best_el.heading_level = toc_level
                best_el.heading_confidence = max(best_el.heading_confidence, 0.95)
                if toc_e.get("heading_path"):
                    best_el.heading_path = toc_e["heading_path"]

                # Check secondary fragments that overlap with TOC text: protect body elements
                for frag_e in ir.elements:
                    if frag_e is best_el or frag_e.in_table or frag_e.is_suppressed_furniture:
                        continue
                    frag_nk = _nk(frag_e.text)
                    frag_tokens = set(frag_nk.split())
                    if (
                        len(frag_e.text.split()) <= 10
                        and not frag_e.in_table
                        and frag_tokens
                        and frag_tokens.issubset(tk_tokens)
                        and abs(frag_e.center_y - best_el.center_y) <= 0.04
                    ):
                        frag_e.is_suppressed_furniture = True
                        frag_e.role = "furniture"

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
                    and sum(1 for r in rows for cell in r if RegexCatalog.TOC_DOT_LEADER.search(cell)) < max(1, int(0.30 * len(rows)))
                ]
                words = page.extract_words(
                    x_tolerance=2.5,
                    y_tolerance=3.0,
                    keep_blank_chars=False,
                    extra_attrs=["fontname", "size", "non_stroking_color", "upright"],
                ) or []
                if not words:
                    return None

                # Subscript / Superscript handling with vertical center overlap
                words.sort(key=lambda x: (float(x["top"]), float(x["x0"])))
                row_buckets: list[list[dict[str, Any]]] = []
                for wd in words:
                    cy = 0.5 * (float(wd["top"]) + float(wd["bottom"]))
                    h_wd = max(4.0, float(wd["bottom"]) - float(wd["top"]))
                    placed = False
                    for rb in reversed(row_buckets[-4:] if len(row_buckets) >= 4 else row_buckets):
                        rb_cy = 0.5 * (float(rb[0]["top"]) + float(rb[0]["bottom"]))
                        rb_h = max(4.0, float(rb[0]["bottom"]) - float(rb[0]["top"]))
                        if abs(cy - rb_cy) <= 0.52 * max(h_wd, rb_h):
                            rb.append(wd)
                            placed = True
                            break
                    if not placed:
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
                    out_tokens: list[str] = []
                    for idx_w, wd in enumerate(seg):
                        w_str = str(wd.get("text", "")).strip()
                        if not w_str:
                            continue
                        if idx_w > 0 and out_tokens:
                            prev_w = seg[idx_w - 1]
                            gap_pt = float(wd["x0"]) - float(prev_w["x1"])
                            sz_max = max(float(wd.get("size") or 10.0), float(prev_w.get("size") or 10.0))
                            sz_min = min(float(wd.get("size") or 10.0), float(prev_w.get("size") or 10.0))
                            is_script_pair = (sz_min <= 0.84 * sz_max or abs(float(wd["bottom"]) - float(prev_w["bottom"])) >= 0.8) and gap_pt <= 0.38 * sz_max
                            is_blood_gas = gap_pt <= 2.2 and bool(re.match(r"^(?:P|Pa|Fi|Sp|Sa|a|v|O|CO|H|N|2|3|4)+$", out_tokens[-1] + w_str))
                            if is_script_pair or is_blood_gas:
                                out_tokens[-1] = f"{out_tokens[-1]}{w_str}"
                                continue
                        out_tokens.append(w_str)
                    txt = RegexCatalog.clean_text(" ".join(out_tokens))
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
            n_rects = tp.count_rects()
            lines: list[dict[str, Any]] = []
            banners: list[tuple[BBox, RGB]] = []
            source_type = "text"

            if n_rects >= 3 and self.mode != "ocr":
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
                        for idx in range(n_rects)
                        for x0, y0, x1, y1 in [tp.get_rect(idx)]
                        if (txt := RegexCatalog.clean_text(tp.get_text_bounded(x0, y0, x1, y1)))
                    ]
                finally:
                    tp.close()
            else:
                tp.close()
                source_type = "ocr"
                # Raster render at self.dpi
                scale = float(self.dpi) / 72.0
                bitmap = page.render(scale=scale)
                pil_img = bitmap.to_pil()
                rgb_arr = np.array(pil_img.convert("RGB"), dtype=np.uint8)
                h_px, w_px, _ = rgb_arr.shape
                bitmap.close()
                pil_img.close()

                # 1. Standard pytesseract image_to_data if available
                try:
                    import pytesseract
                    from PIL import Image
                    gray_img = Image.fromarray(rgb_arr).convert("L")
                    ocr_data = pytesseract.image_to_data(gray_img, output_type=pytesseract.Output.DICT)
                    n_boxes = len(ocr_data.get("text", []))
                    line_groups: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
                    for bi in range(n_boxes):
                        t_b = str(ocr_data["text"][bi]).strip()
                        if not t_b or int(ocr_data["conf"][bi]) < 20:
                            continue
                        block_num = ocr_data["block_num"][bi]
                        par_num = ocr_data["par_num"][bi]
                        line_num = ocr_data["line_num"][bi]
                        key_ln = (block_num, par_num, line_num)
                        if key_ln not in line_groups:
                            line_groups[key_ln] = []
                        line_groups[key_ln].append({
                            "text": t_b,
                            "x0": ocr_data["left"][bi],
                            "y0": ocr_data["top"][bi],
                            "x1": ocr_data["left"][bi] + ocr_data["width"][bi],
                            "y1": ocr_data["top"][bi] + ocr_data["height"][bi],
                        })
                    for grp in line_groups.values():
                        grp_txt = RegexCatalog.clean_text(" ".join(b["text"] for b in grp))
                        if not grp_txt:
                            continue
                        gx0 = min(b["x0"] for b in grp) / float(w_px)
                        gy0 = min(b["y0"] for b in grp) / float(h_px)
                        gx1 = max(b["x1"] for b in grp) / float(w_px)
                        gy1 = max(b["y1"] for b in grp) / float(h_px)
                        bh_pt = (gy1 - gy0) * h
                        lines.append({
                            "text": grp_txt,
                            "bbox_norm": (gx0, gy0, gx1, gy1),
                            "size_pt": max(8.0, round(bh_pt * 0.82, 2)),
                            "fg_rgb": (0, 0, 0),
                            "font_family": "sans",
                            "is_bold": False,
                            "is_italic": False,
                        })
                except Exception as ocr_err:
                    logger.debug("Raster pytesseract OCR unavailable: %s", ocr_err)

                # 2. White-on-Color Pill Banner detection + Inverted OCR
                detected_banners, pill_lines = _detect_and_ocr_chromatic_pill_banners(
                    rgb_arr, lines, dpi=self.dpi
                )
                banners.extend(detected_banners)
                lines.extend(pill_lines)

            page.close()
            if lines or banners:
                return {
                    "width": w,
                    "height": h,
                    "has_spans": source_type == "text",
                    "lines": lines,
                    "tables": [],
                    "figures": [],
                    "banners": banners,
                    "underlines": [],
                    "callouts": [],
                    "source": source_type,
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

        # Contributor / Borderless Numbered Table Detector (issues.md §5.1)
        contributor_indices = [
            i for i, ln in enumerate(raw_lines)
            if RegexCatalog.CONTRIBUTOR_ROW.match(str(ln.get("text", "")))
        ]
        has_contrib_header = any(
            re.search(r"(?i)(?:NAME|LIST)\s+OF\s+CONTRIBUTORS", str(ln.get("text", "")))
            for ln in raw_lines
        )
        if len(contributor_indices) >= 4 or (has_contrib_header and len(contributor_indices) >= 2):
            c_rows: list[list[str]] = []
            for ci in contributor_indices:
                raw_lines[ci]["in_table"] = True
                c_text = str(raw_lines[ci].get("text", "")).strip()
                m_sl = re.match(r"^(\d{1,2})[\.\)\s]+(.*)$", c_text)
                if m_sl:
                    c_rows.append([m_sl.group(1), m_sl.group(2).strip()])
                else:
                    c_rows.append([str(len(c_rows) + 1), c_text])
            if c_rows:
                cb_x0 = min(ln.get("bbox_norm", (0, 0, 1, 1))[0] for i, ln in enumerate(raw_lines) if i in contributor_indices)
                cb_y0 = min(ln.get("bbox_norm", (0, 0, 1, 1))[1] for i, ln in enumerate(raw_lines) if i in contributor_indices)
                cb_x1 = max(ln.get("bbox_norm", (0, 0, 1, 1))[2] for i, ln in enumerate(raw_lines) if i in contributor_indices)
                cb_y1 = max(ln.get("bbox_norm", (0, 0, 1, 1))[3] for i, ln in enumerate(raw_lines) if i in contributor_indices)
                detected_tables.append(
                    TableGrid(
                        bbox_norm=(cb_x0, cb_y0, cb_x1, cb_y1),
                        rows=c_rows,
                        headers=["Sl. No.", "Name & Designation of Contributor"],
                    )
                )

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
            raw_blk_label = ln.get("raw_block_label", "Text")
            parent_blk_id = ln.get("block_id")
            parent_blk_bbox = ln.get("block_bbox")

            if "blocks" in raw:
                # Inherit parent layout block label and bbox from native OCR/order model
                for b_idx, blk in enumerate(raw.get("blocks", [])):
                    b_lbl = blk.get("label", "Text")
                    b_box = blk.get("bbox", [])
                    if len(b_box) == 4:
                        b_w = float(raw.get("width_px") or w_ref)
                        b_h = float(raw.get("height_px") or h_ref)
                        blk_norm = (b_box[0] / b_w, b_box[1] / b_h, b_box[2] / b_w, b_box[3] / b_h)
                        if overlap_ratio(bb, blk_norm) >= 0.35:
                            raw_blk_label = b_lbl
                            parent_blk_id = f"blk_{page_number}_{b_idx}"
                            parent_blk_bbox = blk_norm
                            if not raw_role:
                                if b_lbl in ("Section-Header", "Title", "Header"):
                                    raw_role = "heading"
                                elif b_lbl == "List-Group":
                                    raw_role = "list_item"
                                elif b_lbl in ("Page-Header", "Page-Footer"):
                                    raw_role = "furniture"
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
                raw_block_label=raw_blk_label,
                block_id=parent_blk_id,
                block_bbox=parent_blk_bbox,
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


# --- Auto-migrated from indra_parser.py ---
import base64
import hashlib
import html
import io
import json
import shutil
import time
from functools import lru_cache
from parsers.embedding_engine import get_embedding_engine
from parsers.lancedb_engine import LanceDBEngine
from parsers.pdf_extractor import clean_pdf_text
from parsers.indra.document import DocumentHeadingTracker, get_or_create_document_tracker
from parsers.indra.profiles import (
    CoordinateZone,
    FurnitureSuppressionPolicy,
    HeadingWeightPolicy,
    LAYOUT_PROFILES,
    LayoutProfile,
    PageGeometryPolicy,
)
from parsers.indra.refiner import IndraHybridLayoutRefiner, build_layout_svg_overlay
from parsers.indra.rules import (
    RegexCatalog,
    deskew_bbox_norm,
    enrich_lines,
    overlap_ratio,
    rgb_distance,
    rgb_saturation,
    rgb_to_hsv,
    union_bbox,
)
from parsers.indra.strategies import (
    AcademicArticleStrategy,
    IllustratedBookletStrategy,
    LayoutStrategy,
    LayoutStrategyRegistry,
    NativeDigitalStrategy,
    ScannedTwoColumnTextbookStrategy,
)
from parsers.indra.types import (
    BBox,
    ElementRole,
    HeadingPrototype,
    PageElement,
    PageElements,
    RGB,
    TableGrid,
    ThreadSafeLRUCache,
)

def _render_single_pdf_page_to_png_and_gray(
    file_path: Path, page_number: int, dpi: int = 200
) -> tuple[Path, np.ndarray, int, int]:
    """Renders a single 1-based PDF page to a temporary 1-page PDF and grayscale uint8 array."""
    import pypdfium2 as pdfium

    if page_number < 1:
        raise ValueError(f"page_number must be >= 1 (got {page_number})")

    src_doc = pdfium.PdfDocument(str(file_path))
    try:
        total_pages = len(src_doc)
        if page_number > total_pages:
            raise ValueError(
                f"Page {page_number} out of range for '{file_path.name}' ({total_pages} pages)."
            )

        page_obj = src_doc[page_number - 1]
        scale = float(dpi) / 72.0
        bitmap = page_obj.render(scale=scale)
        pil_img = bitmap.to_pil()
        width, height = pil_img.size
        gray_arr = np.asarray(pil_img.convert("L"), dtype=np.uint8)

        single_pdf = pdfium.PdfDocument.new()
        single_pdf.import_pages(src_doc, pages=[page_number - 1])
        tmp_fd, tmp_pdf_str = tempfile.mkstemp(suffix=".pdf", prefix="indra_p_")
        os.close(tmp_fd)
        tmp_pdf_path = Path(tmp_pdf_str)
        single_pdf.save(str(tmp_pdf_path))
        single_pdf.close()
        page_obj.close()
        bitmap.close()
        pil_img.close()
        return tmp_pdf_path, gray_arr, width, height
    finally:
        src_doc.close()

def _map_indra_label_to_box_type(label: str) -> str:
    m = {
        "Section-Header": "header",
        "Title": "header",
        "Page-Header": "header",
        "Page-Footer": "footer",
        "Table": "table",
        "List-Group": "list",
        "Caption": "caption",
        "Footnote": "footnote",
        "Picture": "figure",
        "Figure": "figure",
        "Image": "figure",
        "Text": "paragraph",
    }
    return m.get(label, "paragraph")

def _build_layout_svg_overlay(
    blocks: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    width: int,
    height: int,
) -> str:
    return build_layout_svg_overlay(blocks, lines, width, height)

def ocr_image_region(
    gray_or_bgr_image: np.ndarray,
    math: bool = False,
    threads: int = DEFAULT_WIN_THREADS,
    **kwargs: Any,
) -> str:
    """Performs localized line crop OCR via Indra OCR with fallback."""
    if gray_or_bgr_image is None or gray_or_bgr_image.size == 0:
        return ""
    try:
        conv = get_indra_converter(mode="ocr", math=math, threads=threads, image_ocr=True)
        img_arr = np.asarray(gray_or_bgr_image)
        if img_arr.ndim == 3 and img_arr.shape[2] == 3:
            img_arr = np.ascontiguousarray(
                (
                    (77 * img_arr[:, :, 0].astype(np.uint16)
                    + 150 * img_arr[:, :, 1].astype(np.uint16)
                    + 29 * img_arr[:, :, 2].astype(np.uint16))
                    >> 8
                ).astype(np.uint8)
            )
        ocr_res = conv.ocr(img_arr)
        if hasattr(ocr_res, "text") and ocr_res.text:
            return str(ocr_res.text).strip()
        if hasattr(ocr_res, "lines"):
            return " ".join(str(getattr(ln, "text", "")).strip() for ln in ocr_res.lines if getattr(ln, "text", "")).strip()
        return ""
    except Exception as e:
        logger.debug("Localized OCR fallback triggered: %s", e)
        return ""

def extract_page_with_indra(
    file_path: Union[str, Path],
    page_number: int = 1,
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 200,
    threads: int = DEFAULT_WIN_THREADS,
    default_title: str = "",
    initial_heading_stack: Optional[dict[int, str]] = None,
    heading_tracker: Optional[DocumentHeadingTracker] = None,
    profile_id: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Extracts a single PDF page with layout-aware heading hierarchy and visual SVG overlays."""
    if mode == "base_heading_opt" or profile_id == "base_heading_opt":
        return extract_page_with_indra_base_heading_opt(
            file_path, page_number=page_number, dpi=dpi, threads=threads, **kwargs
        )
    path = Path(file_path).resolve()
    root_title = default_title or path.stem
    tracker = heading_tracker or get_or_create_document_tracker(path, default_title=root_title)

    engine = IndraModularEngine(
        profile_id=profile_id,
        mode=mode,
        math=math,
        furniture=furniture,
        dpi=dpi,
        threads=threads,
    )
    if initial_heading_stack:
        engine.active_titles.update({int(k): str(v) for k, v in initial_heading_stack.items()})
        tracker.active_stack.update({int(k): str(v) for k, v in initial_heading_stack.items()})

    ir = engine.extract_page_ir(path, page_number=page_number)
    refiner = IndraHybridLayoutRefiner(
        page_number=page_number,
        dpi=dpi,
        default_title=root_title,
        heading_tracker=tracker,
        engine=engine,
        pdf_path=path,
    )
    return refiner.refine_page(ir)

def extract_structured_layout(
    file_path: Union[str, Path],
    page_number: int,
    mode: str = "auto",
    math: bool = False,
    **kwargs: Any,
) -> str:
    """Mtime-aware cached wrapper returning pure Markdown for a target page."""
    path = Path(file_path).resolve()
    mtime_ns = path.stat().st_mtime_ns if path.is_file() else 0
    cache_key = (str(path), mtime_ns, int(page_number), str(mode), bool(math))
    cached_md = _LAYOUT_MD_CACHE.get(cache_key)
    if cached_md is not None:
        return cached_md

    res = extract_page_with_indra(
        file_path=path,
        page_number=page_number,
        mode=mode,
        math=math,
        **kwargs,
    )
    md = str(res.get("markdown") or res.get("text") or "")
    _LAYOUT_MD_CACHE.put(cache_key, md)
    return md

def extract_pdf_structure(
    file_path: Union[str, Path],
    default_title: str = "",
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 150,
    threads: int = DEFAULT_WIN_THREADS,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Extracts all pages of a PDF document sequentially, propagating heading stacks across pages."""
    import pypdfium2 as pdfium

    path = Path(file_path).resolve()
    root_title = default_title or path.stem
    doc = pdfium.PdfDocument(str(path))
    total_pages = len(doc)
    doc.close()

    tracker = get_or_create_document_tracker(path, default_title=root_title, reset=True)
    engine = IndraModularEngine(
        mode=mode,
        math=math,
        furniture=furniture,
        dpi=dpi,
        threads=threads,
    )

    all_sections: list[dict[str, Any]] = []
    active_stack: dict[int, str] = {}

    for page_num in range(1, total_pages + 1):
        res = extract_page_with_indra(
            file_path=path,
            page_number=page_num,
            mode=mode,
            math=math,
            furniture=furniture,
            dpi=dpi,
            threads=threads,
            default_title=root_title,
            initial_heading_stack=active_stack,
            heading_tracker=tracker,
            **kwargs,
        )
        for h_path, subhead, blocks in res.get("sections_for_chunks", []):
            content = "\n\n".join(b for b in blocks if b.strip()).strip()
            if content:
                all_sections.append({
                    "heading_path": h_path,
                    "subheading": subhead,
                    "text": content,
                    "content": content,
                    "page_number": page_num,
                })
        if res.get("active_heading_stack"):
            active_stack = dict(res["active_heading_stack"])

    return all_sections

def ingest_to_lancedb(
    file_path: Union[str, Path],
    source_article: str = "",
    main_topic: str = "",
    mode: str = "auto",
    math: bool = False,
    batch_size: int = 64,
    **kwargs: Any,
) -> int:
    """Extracts structured sections from a PDF and indexes them into LanceDB."""
    path = Path(file_path).resolve()
    sections = extract_pdf_structure(
        file_path=path,
        default_title=source_article or path.stem,
        mode=mode,
        math=math,
        **kwargs,
    )
    if not sections:
        return 0

    embedder = get_embedding_engine()
    lance = LanceDBEngine()
    records: list[dict[str, Any]] = []

    for idx, sec in enumerate(sections):
        content = sec["text"]
        digest = hashlib.sha256(f"{path.stem}_{sec['page_number']}_{idx}".encode()).hexdigest()[:16]
        records.append({
            "id": f"pdf_indra_{digest}",
            "file_path": str(path),
            "source_article": source_article or path.stem,
            "main_topic": main_topic or sec.get("subheading", path.stem),
            "heading_path": sec.get("heading_path", path.stem),
            "subheading": sec.get("subheading", path.stem),
            "page_number": sec["page_number"],
            "text": content,
            "vector": embedder.embed(content),
            "chunk_type": "section",
            "metadata": json.dumps({"source_engine": "indra", "page": sec["page_number"]}),
        })

    if records:
        lance.upsert_documents(records)
    return len(records)

def parse_document_text_to_ast(
    raw_text: str, default_title: str = "Document", escape_html_angles: bool = True
) -> dict[str, Any]:
    """Lightweight AST parser for text converted to structured sections."""
    lines = raw_text.splitlines()
    sections = []
    current_h = default_title
    cur_buf = []

    for ln in lines:
        if ln.startswith("#"):
            if cur_buf:
                sections.append({"heading": current_h, "content": "\n".join(cur_buf).strip()})
                cur_buf.clear()
            current_h = ln.lstrip("#").strip()
        else:
            cur_buf.append(ln)

    if cur_buf:
        sections.append({"heading": current_h, "content": "\n".join(cur_buf).strip()})

    return {"title": default_title, "sections": sections}


# --- Auto-migrated from indra_base_heading_opt.py ---
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from parsers.indra.refiner import build_layout_svg_overlay
from parsers.indra.rules import RegexCatalog, overlap_ratio
from parsers.indra.types import BBox, PageElement, RGB

def _normalize_key(text: str) -> str:
    cleaned = re.sub(r"^[•*\-–—\s#]+", "", (text or "").strip()).replace("**", "")
    return re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()

def extract_page_with_indra_base_heading_opt(
    file_path: Union[str, Path],
    page_number: int = 1,
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 200,
    threads: int = DEFAULT_WIN_THREADS,
    default_title: str = "",
    full_toc_scan: bool = False,
    render_page_image: bool = True,
) -> Dict[str, Any]:
    from parsers.heading_toc_calibrator import (
        build_document_toc_and_hierarchy,
        classify_outline_prefix,
        score_and_resolve_heading_candidate,
    )
    """
    Extracts a PDF page using Pure Base Indra OCR reading order and blocks,
    then non-destructively overlays calibrated H1-H6 heading levels and breadcrumbs
    from the Multi-Pass TOC Calibrator.
    """
    import pypdfium2 as pdfium

    t0 = time.perf_counter()
    path = Path(file_path).resolve()
    root_title = default_title.strip() or path.stem

    # 1. Run Multi-Pass TOC Calibrator (Lazy Window for single page, or Full Scan if cached/requested)
    toc_payload = build_document_toc_and_hierarchy(
        path, default_title=root_title, target_page=page_number, full_scan=full_toc_scan
    )
    preassigned_page_toc: List[Dict[str, Any]] = list(
        (toc_payload.get("page_toc_map") or {}).get(str(page_number), [])
    )
    init_stack: Dict[int, str] = {
        int(k): str(v)
        for k, v in ((toc_payload.get("page_initial_stacks") or {}).get(str(page_number), {})).items()
    }

    # 2. Render page via pypdfium2 & invoke Base Indra OCR natively
    doc = pdfium.PdfDocument(str(path))
    try:
        page_idx = max(0, min(len(doc) - 1, page_number - 1))
        page_obj = doc[page_idx]
        w_pt, h_pt = float(page_obj.get_width()), float(page_obj.get_height())
        scale = float(dpi) / 72.0
        bitmap = page_obj.render(scale=scale)
        pil_img = bitmap.to_pil()
        w_px, h_px = pil_img.size

        page_image_b64 = ""
        if render_page_image:
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            page_image_b64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
        pil_img.close()
        bitmap.close()

        indra_page = None
        try:
            if not is_indra_models_downloaded(include_math=math):
                download_indra_models(include_math=math)
            conv = get_indra_converter(
                mode=mode, math=math, furniture=furniture, dpi=dpi, threads=threads
            )
            indra_page = conv.pdf_page(str(path), doc, page_idx)
        except Exception as exc:
            logger.warning("Base Indra OCR fallback to native pypdfium2 textpage: %s", exc)
    finally:
        doc.close()

    # 3. Extract Base Indra Blocks in their EXACT native order.onnx sequence
    native_blocks: List[Dict[str, Any]] = []
    native_lines: List[Dict[str, Any]] = []

    if indra_page is not None:
        for blk in getattr(indra_page, "blocks", []):
            lbl = blk.get("label", "Text") if isinstance(blk, dict) else getattr(blk, "label", "Text")
            bx = list(blk.get("bbox", []) if isinstance(blk, dict) else getattr(blk, "bbox", []))
            txt = str(blk.get("text", "") if isinstance(blk, dict) else getattr(blk, "text", "")).strip()
            if txt:
                native_blocks.append({"label": lbl, "bbox": bx, "text": txt})

        for ln in getattr(indra_page, "lines", []):
            bx = list(getattr(ln, "bbox", getattr(ln, "box", [0, 0, 0, 0])))
            txt = str(getattr(ln, "text", "")).strip()
            if txt and len(bx) == 4:
                native_lines.append({"text": txt, "box": bx})

    # If native_blocks is empty (e.g. only lines returned or fallback), build sequential blocks from lines
    if not native_blocks and native_lines:
        for ln in native_lines:
            native_blocks.append({"label": "Text", "bbox": ln["box"], "text": ln["text"]})

    # 4. Multi-Step Typography & Calibrator Pass on native_blocks
    from parsers.indra.heading_calibrator import calibrate_page_blocks
    cal_inputs = []
    for blk in native_blocks:
        bx = blk.get("bbox") or [0, 0, w_px, h_px]
        cal_inputs.append({
            "raw_label": blk.get("label", "Text"),
            "text": blk.get("text", ""),
            "bbox": bx,
            "font_size": blk.get("font_size", 10.0),
            "is_bold": blk.get("is_bold", False),
            "text_color": blk.get("text_color", (0, 0, 0)),
            "bg_color": blk.get("bg_color"),
            "has_bg_fill": blk.get("has_bg_fill", False),
        })

    calibrated_blocks = calibrate_page_blocks(cal_inputs, page_height=float(h_px))

    # Synchronize calibrated results back to native_blocks
    for blk, cal in zip(native_blocks, calibrated_blocks):
        blk["label"] = cal.get("label", "Text")
        blk["heading_level"] = cal.get("heading_level")
        blk["is_heading"] = cal.get("is_heading", False)
        blk["heading_score"] = cal.get("heading_score", 0.0)

    # 5. Non-Destructive Heading Level Alignment & Output Assembly
    md_blocks: List[str] = []
    hierarchy_tree: List[Dict[str, Any]] = []
    headings: List[str] = []
    sections_for_chunks: List[Tuple[str, str, List[str]]] = []
    active_titles: Dict[int, str] = dict(init_stack)
    cur_sec_path = " > ".join([root_title] + [active_titles[k] for k in sorted(active_titles)]) if active_titles else root_title
    cur_sec_sub = active_titles[max(active_titles)] if active_titles else root_title
    cur_sec_blocks: List[str] = []

    used_toc_indices: Set[int] = set()

    for blk in native_blocks:
        lbl = str(blk.get("label", "Text"))
        if not furniture and lbl in ("Page-Header", "Page-Footer"):
            continue
        raw_txt = RegexCatalog.clean_text(blk.get("text", ""))
        if not raw_txt:
            continue

        bx = blk.get("bbox") or [0, 0, w_px, h_px]
        bb_norm: BBox = (
            float(bx[0]) / max(1.0, w_px),
            float(bx[1]) / max(1.0, h_px),
            float(bx[2]) / max(1.0, w_px),
            float(bx[3]) / max(1.0, h_px),
        ) if max(bx, default=0.0) > 1.05 else tuple(float(v) for v in bx)  # type: ignore

        bk_norm = _normalize_key(raw_txt)
        matched_toc: Optional[Dict[str, Any]] = None

        for t_idx, te in enumerate(preassigned_page_toc):
            if t_idx in used_toc_indices:
                continue
            tk = _normalize_key(te.get("title", ""))
            te_y0 = float(te.get("y0", -1.0))
            if tk and (
                bk_norm == tk
                or (len(tk) >= 6 and (bk_norm.startswith(tk) or tk.startswith(bk_norm)) and abs(bb_norm[1] - te_y0) <= 0.08)
            ):
                matched_toc = te
                used_toc_indices.add(t_idx)
                break

        is_heading = bool(blk.get("is_heading")) or (matched_toc is not None and lbl not in ("Page-Header", "Page-Footer", "Table", "Caption", "Footnote"))
        if is_heading:
            lvl = int(matched_toc["level"]) if matched_toc else int(blk.get("heading_level") or (1 if lbl == "Title" else 2))
            clean_title = str(matched_toc["title"]) if matched_toc else raw_txt.lstrip("#").strip()
            blk["label"] = "Section-Header"
            blk["heading_level"] = lvl

            active_titles[lvl] = clean_title
            for deeper in [k for k in list(active_titles) if k > lvl]:
                active_titles.pop(deeper, None)

            h_path = (
                str(matched_toc.get("heading_path"))
                if (matched_toc and matched_toc.get("heading_path"))
                else " > ".join([root_title] + [active_titles[k] for k in sorted(active_titles)])
            )
            headings.append(clean_title)
            node = {
                "page": page_number,
                "level": lvl,
                "tag": f"H{lvl}",
                "title": clean_title,
                "heading_path": h_path,
                "order": len(hierarchy_tree) + 1,
                "x0": round(bb_norm[0], 4),
                "y0": round(bb_norm[1], 4),
                "x1": round(bb_norm[2], 4),
                "y1": round(bb_norm[3], 4),
            }
            hierarchy_tree.append(node)

            if cur_sec_blocks:
                sections_for_chunks.append((cur_sec_path, cur_sec_sub, list(cur_sec_blocks)))
                cur_sec_blocks.clear()
            cur_sec_path = h_path
            cur_sec_sub = clean_title
            md_h = f"{'#' * lvl} {clean_title}"
            md_blocks.append(md_h)
            cur_sec_blocks.append(md_h)

            # If raw_txt was a run-in heading containing trailing body text after clean_title, preserve the body text!
            if len(bk_norm) > len(_normalize_key(clean_title)) + 6:
                rem_body = raw_txt[len(clean_title) :].lstrip(" :.—–-")
                if len(rem_body.split()) >= 3:
                    md_blocks.append(rem_body)
                    cur_sec_blocks.append(rem_body)
        elif lbl == "List-Group":
            md_li = raw_txt if raw_txt.startswith(("-", "*", "•")) else f"- {raw_txt}"
            md_blocks.append(md_li)
            cur_sec_blocks.append(md_li)
        elif lbl == "Caption":
            md_cap = f"> *{raw_txt}*"
            md_blocks.append(md_cap)
            cur_sec_blocks.append(md_cap)
        else:
            md_blocks.append(raw_txt)
            cur_sec_blocks.append(raw_txt)

    if cur_sec_blocks:
        sections_for_chunks.append((cur_sec_path, cur_sec_sub, list(cur_sec_blocks)))

    md_out = "\n\n".join(md_blocks)
    svg_overlay = build_layout_svg_overlay(native_blocks, native_lines, w_px, h_px)
    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)

    layout_boxes = []
    for idx_b, blk in enumerate(native_blocks):
        bx = blk.get("bbox") or [0, 0, w_px, h_px]
        bb_norm = [
            float(bx[0]) / max(1.0, w_px),
            float(bx[1]) / max(1.0, h_px),
            float(bx[2]) / max(1.0, w_px),
            float(bx[3]) / max(1.0, h_px),
        ] if max(bx, default=0.0) > 1.05 else [float(v) for v in bx]
        lbl = str(blk.get("label", "Text"))
        b_type = "heading" if lbl == "Section-Header" else ("list" if lbl == "List-Group" else "paragraph")
        layout_boxes.append({
            "order": idx_b + 1,
            "type": b_type,
            "label": f"{lbl} #{idx_b + 1}",
            "heading_level": blk.get("heading_level"),
            "bbox": bb_norm,
            "bbox_norm": bb_norm,
            "bbox_px": bx,
            "bbox_pt": [
                bb_norm[0] * float(w_pt),
                bb_norm[1] * float(h_pt),
                bb_norm[2] * float(w_pt),
                bb_norm[3] * float(h_pt),
            ],
            "text": blk.get("text", ""),
        })

    return {
        "status": "success",
        "engine": "indra_base_heading_opt",
        "page": page_number,
        "page_number": page_number,
        "markdown": md_out,
        "text": md_out,
        "headings": headings,
        "hierarchy_tree": hierarchy_tree,
        "active_heading_stack": active_titles,
        "document_toc": toc_payload.get("document_toc", []),
        "page_toc": hierarchy_tree,
        "blocks": native_blocks,
        "layout_boxes": layout_boxes,
        "lines": native_lines,
        "svg_overlay": svg_overlay,
        "page_image_b64": page_image_b64,
        "page_width": w_pt,
        "page_height": h_pt,
        "sections_for_chunks": sections_for_chunks,
        "elapsed_ms": elapsed,
    }
