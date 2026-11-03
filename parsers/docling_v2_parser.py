"""
Adaptive Multi-Column Medical Layout Engine for PyMuPDF (fitz) and Docling v2 Hybrid Recovery.
Implements per-page typography profiling, vector ruling barriers, shaded callout isolation,
topological DAG reading order, base64 PNG rendering, layout boxes, and heading path detection.

Modularized via `parsers/docling/` subpackage while preserving 100% backwards compatibility.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import io
import logging
import os
import threading
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

# Re-export modular components for complete backwards compatibility
from parsers.docling import (
    AdaptivePdfLayoutEngine,
    DOCLING_REQUIRED_MODELS,
    clean_pdf_text,
    download_docling_models,
    ensure_offline_mode_if_downloaded,
    get_docling_converter,
    is_docling_models_downloaded,
    point_in_rect,
    rect_area,
    rects_overlap,
    serialize_docling_ast,
)
from parsers.docling.cleaner import (
    _BOUND_MEDICAL_PREFIXES,
    _HYPHENATED_MEDICAL_PREFIXES,
    _LIGATURE_MAP,
    _MEDICAL_PREFIXES,
    _PROSE_FINITE_VERBS_RE,
    _TRAILING_FUNCTION_WORD_RE,
)
from parsers.docling.converter import (
    _DOCLING_AVAILABLE,
    _DOCLING_CONVERTERS,
    _DOCLING_LOCK,
    DocumentStream,
    EasyOcrOptions,
    RapidOcrOptions,
    _get_docling_converter,
    _serialize_docling_ast,
)
from parsers.docling.geometry import (
    _point_in_rect,
    _rect_area,
    _rects_overlap,
)

logger = logging.getLogger(__name__)

_FITZ_LOCK = threading.RLock()


def extract_page_with_docling_v2(
    file_path: Union[str, Path],
    page_number: int,
    default_title: str = "",
    initial_heading_stack: Optional[Dict[int, str]] = None,
    page_toc: Optional[List[Tuple[int, str]]] = None,
    ocr_engine: str = "auto",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Docling v2 Memory-Safe Single-Page Extraction:
    - Slices requested page into a temporary 1-page PDF buffer wrapped in DocumentStream
    - Auto-detects whether OCR is necessary
    - Calibrates headings, booktabs tables, and image-wrapped text against AdaptivePdfLayoutEngine
    """
    norm_path = str(Path(file_path).resolve())
    title = default_title or Path(norm_path).stem
    raw_engine = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocr_engine") or ocr_engine or "auto")
    engine_pref = str(raw_engine).strip().lower()

    if engine_pref in ("indra", "indra_math", "indra_ocr"):
        import parsers.indra_parser as indra_p
        i_mode = "ocr" if engine_pref == "indra_ocr" else "auto"
        i_math = (engine_pref == "indra_math")
        return indra_p.extract_page_with_indra(
            file_path=norm_path,
            page_number=page_number,
            mode=i_mode,
            math=i_math,
            default_title=title,
            initial_heading_stack=initial_heading_stack,
        )

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

                # When native/OCR text layer exists on the PDF page, prefer AdaptivePdfLayoutEngine's
                # deterministic hierarchy, booktabs table, and paragraph reconstruction
                if not do_ocr and adaptive_res.get("extracted_text", "").strip():
                    return adaptive_res

                single_doc = fitz.open()
                single_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
                pdf_bytes = single_doc.write()
                single_doc.close()

        converter = _get_docling_converter(do_ocr=do_ocr, ocr_engine=engine_pref)
        if converter is None:
            return adaptive_res

        ensure_offline_mode_if_downloaded()

        doc_input = DocumentStream(name=f"page_{page_number}.pdf", stream=io.BytesIO(pdf_bytes))
        conv_res = converter.convert(doc_input)
        raw_docling_md = _serialize_docling_ast(
            conv_res.document,
            hierarchy_tree=adaptive_res.get("hierarchy_tree", []),
        )

        adaptive_md = adaptive_res.get("extracted_text", "")
        if not raw_docling_md or len(raw_docling_md.strip()) < 0.25 * len(adaptive_md):
            logger.info("Docling output sparse; augmenting with adaptive multi-column recovery.")
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
        logger.warning("Docling v2 conversion encountered exception: %s. Falling back to adaptive engine.", exc)
        return adaptive_res


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


@functools.lru_cache(maxsize=128)
def _cached_structured_layout(norm_path: str, page_number: int, ocr_engine: str) -> str:
    res = extract_page_with_docling_v2(norm_path, page_number, ocr_engine=ocr_engine)
    return res.get("extracted_text", "") or res.get("text", "")


def extract_structured_layout(
    file_path: Union[str, Path],
    page_number: int,
    ocr_engine: str = "auto",
    **kwargs: Any,
) -> str:
    """Convenience layout extractor returning Markdown text using Docling v2."""
    if not kwargs:
        return _cached_structured_layout(str(Path(file_path).resolve()), int(page_number), str(ocr_engine or "auto"))
    res = extract_page_with_docling_v2(file_path, page_number, ocr_engine=ocr_engine, **kwargs)
    return res.get("extracted_text", "") or res.get("text", "")


def extract_pdf_structure(
    file_path: Union[str, Path],
    ocr_engine: str = "auto",
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Extracts structured sections from a PDF for Tantivy BM25 and LanceDB indexing using Docling v2.
    Conforms strictly to the Section 4 ChunkDict contract.
    """
    if not file_path:
        return []

    raw_engine = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocr_engine") or ocr_engine or "auto")
    engine_pref = str(raw_engine).strip().lower()
    if engine_pref in ("indra", "indra_math", "indra_ocr"):
        import parsers.indra_parser as indra_p
        i_mode = "ocr" if engine_pref == "indra_ocr" else "auto"
        i_math = (engine_pref == "indra_math")
        return indra_p.extract_pdf_structure(
            file_path=file_path,
            mode=i_mode,
            math=i_math,
        )

    norm_path = str(Path(file_path).resolve())
    path_obj = Path(norm_path)
    if not path_obj.is_file():
        logger.warning("Target PDF file does not exist: %s", norm_path)
        return []

    default_title = path_obj.stem
    sections: List[Dict[str, Any]] = []

    if fitz is None:
        logger.error("PyMuPDF (fitz) is required for extract_pdf_structure")
        return sections

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

        logger.info("Successfully extracted %d structured PDF sections from %s via Docling v2", len(sections), norm_path)
        return sections
    except Exception as exc:
        logger.error("Docling v2 PDF structure extraction failed for %s: %s", norm_path, exc)
        return sections
