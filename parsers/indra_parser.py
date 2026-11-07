"""
Indra OCR & Hybrid PDF Layout Parser (`parsers/indra_parser.py`).

Modular 3-Pillar Architecture (`LAYOUT_PROFILES`, `RegexCatalog`, `FallbackChain`).
Maintains full backward compatibility for all API consumers and server routes.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

import numpy as np

import config
from parsers.embedding_engine import get_embedding_engine
from parsers.indra_base_heading_opt import extract_page_with_indra_base_heading_opt
from parsers.lancedb_engine import LanceDBEngine
from parsers.pdf_extractor import clean_pdf_text

# Re-export all sub-components for modular architecture & backwards compatibility
from parsers.indra import (
    AcademicArticleStrategy,
    BBox,
    CoordinateZone,
    DEFAULT_WIN_THREADS,
    DocumentHeadingTracker,
    ElementRole,
    FallbackChain,
    FurnitureSuppressionPolicy,
    HeadingPrototype,
    HeadingWeightPolicy,
    INDRA_MODELS_DIR,
    INDRA_REPO_ID,
    IllustratedBookletStrategy,
    IndraHybridLayoutRefiner,
    IndraModularEngine,
    LAYOUT_PROFILES,
    LayoutProfile,
    LayoutStrategy,
    LayoutStrategyRegistry,
    NativeDigitalStrategy,
    PageElement,
    PageElements,
    PageGeometryPolicy,
    RGB,
    REQUIRED_CORE_FILES,
    REQUIRED_MATH_FILES,
    RegexCatalog,
    ScannedTwoColumnTextbookStrategy,
    TableGrid,
    ThreadSafeLRUCache,
    build_layout_svg_overlay,
    deskew_bbox_norm,
    download_indra_models,
    enrich_lines,
    get_indra_converter,
    get_indra_models_dir,
    get_or_create_document_tracker,
    is_indra_models_downloaded,
    overlap_ratio,
    reconstruct_2d_table_grid,
    rgb_distance,
    rgb_saturation,
    rgb_to_hsv,
    union_bbox,
)

logger = logging.getLogger(__name__)

# Cache for single page markdown layouts
_LAYOUT_MD_CACHE: ThreadSafeLRUCache[tuple, str] = ThreadSafeLRUCache(maxsize=128)


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
