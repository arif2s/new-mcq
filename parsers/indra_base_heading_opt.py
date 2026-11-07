"""
parsers/indra_base_heading_opt.py

`indra_base_heading_opt`: Pure Base Indra OCR (`det.onnx` + `rec.onnx` + `order.onnx`)
preserving 100% of upstream Marker/Surya layout & reading order, augmented strictly
with HSV Pill Banner Recovery and Multi-Pass H1-H6 Heading Level Calibration.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np

from parsers.heading_toc_calibrator import (
    build_document_toc_and_hierarchy,
    classify_outline_prefix,
    score_and_resolve_heading_candidate,
)
from parsers.indra.engine import (
    DEFAULT_WIN_THREADS,
    download_indra_models,
    get_indra_converter,
    is_indra_models_downloaded,
)
from parsers.indra.profiles import LAYOUT_PROFILES
from parsers.indra.refiner import build_layout_svg_overlay
from parsers.indra.rules import RegexCatalog, overlap_ratio
from parsers.indra.types import BBox, PageElement, RGB

logger = logging.getLogger("medisearch.indra_base_opt")


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

    # 4. Non-Destructive Heading Level Alignment (Matches Base Blocks to Preassigned Multi-Pass TOC)
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

        is_heading = matched_toc is not None or lbl in ("Section-Header", "Title", "Header")
        if is_heading:
            lvl = int(matched_toc["level"]) if matched_toc else (1 if lbl == "Title" else 2)
            clean_title = str(matched_toc["title"]) if matched_toc else raw_txt.lstrip("#").strip()
            blk["label"] = "Section-Header"

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
        "lines": native_lines,
        "svg_overlay": svg_overlay,
        "page_image_b64": page_image_b64,
        "page_width": w_pt,
        "page_height": h_pt,
        "sections_for_chunks": sections_for_chunks,
        "elapsed_ms": elapsed,
    }