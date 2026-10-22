"""
Lightweight PyMuPDF fallback fast text slicer with page heading detection and adaptive geometric layout.
Enforces zero VRAM usage, ChunkDict compliance, ligature normalization, and margin bounds filtering.
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

from parsers.pdf_parser import (
    AdaptivePdfLayoutEngine,
    clean_pdf_text,
    extract_page_with_adaptive_layout,
)

logger = logging.getLogger(__name__)

# Re-entrant synchronization lock for native PyMuPDF context operations
_FITZ_LOCK = threading.RLock()


@functools.lru_cache(maxsize=256)
def _cached_extract_page_slice(normalized_path: str, page_number: int) -> str:
    """Internal LRU-cached worker for page text extraction using Adaptive Geometric Layout Engine."""
    if fitz is None:
        logger.warning("PyMuPDF (fitz) is not installed; cannot slice PDF page.")
        return ""

    path_obj = Path(normalized_path)
    if not path_obj.is_file():
        return ""

    try:
        res = extract_page_with_adaptive_layout(normalized_path, page_number)
        text = res.get("extracted_text", "")
        if text:
            return text

        with _FITZ_LOCK:
            with fitz.open(normalized_path) as doc:
                if doc.is_encrypted:
                    try:
                        doc.authenticate("")
                    except Exception:
                        pass

                target_idx = page_number - 1
                if target_idx < 0 or target_idx >= doc.page_count:
                    return ""

                page = doc.load_page(target_idx)
                raw_text = page.get_text("text", sort=True)
                return clean_pdf_text(raw_text)

    except Exception as exc:
        logger.error("Failed to extract page %d from %s: %s", page_number, normalized_path, exc)
        return ""


def extract_page_slice(file_path: Union[str, Path], page_number: int) -> str:
    """
    Extracts text from a specific PDF page using Stage 3.1-3.6 Adaptive Geometric Layout Engine.
    Results are cached in memory to optimize batch RAG synthesis and topic verification.
    """
    if not file_path:
        return ""
    try:
        p_num = int(page_number)
    except (ValueError, TypeError):
        return ""

    if p_num < 1:
        return ""

    try:
        norm_path = str(Path(file_path).resolve())
    except Exception:
        norm_path = str(file_path)
    return _cached_extract_page_slice(norm_path, p_num)


def extract_page_with_headings(page: Any, default_title: str = "") -> Dict[str, Any]:
    """
    Extracts page text and section headings via Stage 3.1-3.6 Adaptive Multi-Column Layout Engine.
    Filters running headers/footers and returns dictionaries conforming to ChunkDict.
    """
    clean_default = str(default_title or "").strip()
    page_num = 1
    if hasattr(page, "number"):
        try:
            page_num = max(1, int(page.number) + 1)
        except (ValueError, TypeError):
            pass

    file_path = ""
    if hasattr(page, "parent") and getattr(page.parent, "name", None):
        file_path = str(page.parent.name)

    try:
        engine = AdaptivePdfLayoutEngine(page, default_title=clean_default)
        res = engine.run(promote_headings=True)

        cleaned_text = res.get("extracted_text", "")
        heading_path = res.get("heading_path", clean_default or "Untitled")
        subheading = res.get("subheading", clean_default or "Untitled")
        title_str = clean_default or "Untitled"

        text_len = len(cleaned_text)
        snippet = cleaned_text[:400] + ("..." if text_len > 400 else "")
        doc_id = hashlib.md5(
            f"{file_path}_{page_num}_{cleaned_text[:50]}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

        return {
            "id": doc_id,
            "doc_id": doc_id,
            "text": cleaned_text,
            "extracted_text": cleaned_text,
            "snippet": snippet,
            "title": title_str,
            "heading": heading_path,
            "heading_path": heading_path,
            "subheading": subheading,
            "source": "pdf",
            "file_path": file_path,
            "page": page_num,
            "page_number": page_num,
            "length": text_len,
            "article_length": text_len,
            "doc_length": text_len,
            "score": 0.0,
            "rerank_score": 0.0,
            "parent_id": None,
        }

    except Exception as exc:
        logger.debug("extract_page_with_headings error on page %d: %s", page_num, exc)
        try:
            fallback = clean_pdf_text(page.get_text("text"))
        except Exception:
            fallback = ""

        title_str = clean_default or "Untitled"
        text_len = len(fallback)
        doc_id = hashlib.md5(
            f"{file_path}_{page_num}_{fallback[:50]}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return {
            "id": doc_id,
            "doc_id": doc_id,
            "text": fallback,
            "extracted_text": fallback,
            "snippet": fallback[:400] + ("..." if text_len > 400 else ""),
            "title": title_str,
            "heading": title_str,
            "heading_path": title_str,
            "subheading": title_str,
            "source": "pdf",
            "file_path": file_path,
            "page": page_num,
            "page_number": page_num,
            "length": text_len,
            "article_length": text_len,
            "doc_length": text_len,
            "score": 0.0,
            "rerank_score": 0.0,
            "parent_id": None,
        }


def extract_pdf_pages(file_path: Union[str, Path], default_title: str = "") -> List[Dict[str, Any]]:
    """
    Extracts all pages from a PDF document using Adaptive Multi-Column Layout Engine indexing.
    Returns ChunkDict-compliant dictionaries suitable for Tantivy and LanceDB ingestion.
    """
    norm_path = str(Path(file_path).resolve())
    path_obj = Path(norm_path)
    if not path_obj.is_file():
        logger.warning("Target PDF file does not exist: %s", norm_path)
        return []

    if fitz is None:
        logger.error("PyMuPDF (fitz) is not installed; cannot index PDF.")
        return []

    title = default_title or path_obj.stem
    sections: List[Dict[str, Any]] = []

    try:
        with fitz.open(norm_path) as doc:
            if doc.is_encrypted:
                try:
                    doc.authenticate("")
                except Exception:
                    pass

            page_count = int(doc.page_count)
            for idx in range(page_count):
                with _FITZ_LOCK:
                    page = doc.load_page(idx)
                    page_data = extract_page_with_headings(page, default_title=title)

                page_text = page_data["text"]
                if not page_text.strip():
                    continue

                page_num = idx + 1
                raw_key = f"{norm_path}_{page_num}_0_{page_text[:50]}"
                chunk_id = hashlib.md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()

                sections.append({
                    "id": chunk_id,
                    "doc_id": chunk_id,
                    "text": page_text,
                    "snippet": page_data["snippet"],
                    "title": title,
                    "source_article": title,
                    "main_topic": title,
                    "heading": page_data["heading"],
                    "heading_path": page_data["heading_path"],
                    "subheading": page_data["subheading"],
                    "source": "pdf",
                    "file_path": norm_path,
                    "page": page_num,
                    "page_number": page_num,
                    "chunk_index": 0,
                    "length": page_data["length"],
                    "doc_length": page_data["doc_length"],
                    "article_length": page_data["article_length"],
                    "parent_content": page_text,
                    "child_content": page_text,
                    "score": 0.0,
                    "rerank_score": 0.0,
                    "parent_id": None,
                })

        return sections

    except Exception as exc:
        logger.error("Failed extracting PDF pages from %s: %s", norm_path, exc)
        return []


def get_pdf_page_count(file_path: Union[str, Path]) -> int:
    """Fast, lightweight helper to get total page count without loading pages."""
    if fitz is None or not file_path:
        return 0

    try:
        norm_path = str(Path(file_path).resolve())
    except Exception:
        return 0

    if not Path(norm_path).is_file():
        return 0

    try:
        with _FITZ_LOCK:
            with fitz.open(norm_path) as doc:
                return int(doc.page_count)
    except Exception:
        return 0


# Interoperable alias matching docling_parser interface
extract_pdf_structure = extract_pdf_pages
