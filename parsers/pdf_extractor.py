"""
Lightweight PyMuPDF fallback fast text slicer with page heading detection.
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

logger = logging.getLogger(__name__)

# Re-entrant synchronization lock for native PyMuPDF context operations
_FITZ_LOCK = threading.RLock()

# Unicode ligature and formatting character translation table for medical search
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


def clean_pdf_text(text: str) -> str:
    """
    Sanitizes raw PDF text for Tantivy BM25 indexing, LanceDB FTS, and SQLite safety:
    - Strips null bytes (\\x00) and form feeds (\\x0c)
    - Replaces typographical ligatures (fi, fl, ffi)
    - Normalizes CRLF and excessive vertical whitespace
    - Reassembles words hyphenated across line breaks
    """
    if not text:
        return ""

    t = text.replace("\x00", "").replace("\x0c", "\n")
    for lig, rep in _LIGATURE_MAP.items():
        if lig in t:
            t = t.replace(lig, rep)

    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # Reassemble hyphenated words split across lines (e.g., 'hyper-\ntension' -> 'hypertension')
    t = re.sub(r"([A-Za-z]{3,})-\n([a-z]{3,})", r"\1\2", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


@functools.lru_cache(maxsize=256)
def _cached_extract_page_slice(normalized_path: str, page_number: int) -> str:
    """Internal LRU-cached worker for page text extraction."""
    if fitz is None:
        logger.warning("PyMuPDF (fitz) is not installed; cannot slice PDF page.")
        return ""

    path_obj = Path(normalized_path)
    if not path_obj.is_file():
        return ""

    try:
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
    Extracts text from a specific PDF page in natural visual reading order (sort=True).
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
    Extracts page text and section headings via font size heuristics and margin bounds.
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
        page_dict = page.get_text("dict", sort=True)
        blocks = page_dict.get("blocks", []) if isinstance(page_dict, dict) else []

        page_height = 800.0
        if hasattr(page, "rect") and hasattr(page.rect, "height"):
            page_height = float(page.rect.height) or 800.0

        weighted_font_sizes: List[float] = []
        candidates: List[Tuple[float, str]] = []
        body_text_parts: List[str] = []
        all_text_parts: List[str] = []

        for b in blocks:
            if b.get("type") != 0:
                continue

            b_bbox = b.get("bbox", (0.0, 0.0, 0.0, 0.0))
            y0, y1 = float(b_bbox[1]), float(b_bbox[3])
            # Exclude running headers (top 4%) and footers/page numbers (bottom 4%)
            is_marginal = (y0 < page_height * 0.04) or (y1 > page_height * 0.96)

            for line in b.get("lines", []):
                line_text_parts: List[str] = []
                max_size = 0.0

                for span in line.get("spans", []):
                    s_text = str(span.get("text", "")).strip()
                    if s_text:
                        sz = float(span.get("size", 10.0) or 10.0)
                        if not is_marginal:
                            # Weight font sizes by character count to avoid superscript skew
                            weight = max(1, min(len(s_text), 60) // 4)
                            weighted_font_sizes.extend([sz] * weight)
                        if sz > max_size:
                            max_size = sz
                        line_text_parts.append(s_text)

                joined_line = " ".join(line_text_parts).strip()
                if not joined_line:
                    continue

                all_text_parts.append(joined_line)
                if is_marginal:
                    continue

                body_text_parts.append(joined_line)

                if 3 <= len(joined_line) <= 120 and not joined_line.isdigit():
                    candidates.append((max_size, joined_line))

        raw_joined = "\n".join(body_text_parts if body_text_parts else all_text_parts)
        cleaned_text = clean_pdf_text(raw_joined)
        if not cleaned_text:
            cleaned_text = clean_pdf_text(page.get_text("text", sort=True))

        headings: List[str] = []
        if weighted_font_sizes and candidates:
            sorted_sizes = sorted(weighted_font_sizes)
            median_size = sorted_sizes[len(sorted_sizes) // 2]
            threshold = max(median_size * 1.15, median_size + 1.5)

            for sz, l_text in candidates:
                if sz >= threshold and l_text not in headings:
                    headings.append(l_text)
                    if len(headings) >= 2:
                        break

        title_str = clean_default or "Untitled"
        parts = [clean_default] + headings if clean_default else headings
        heading_path = " > ".join(parts) if parts else title_str
        subheading = headings[-1] if headings else title_str

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
    Extracts all pages from a PDF document using lightweight PyMuPDF indexing.
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
                # Fine-grained per-page lock so single-page slices never stall behind bulk indexing
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


# Interoperable alias matching docling_parser interface (Section 3)
extract_pdf_structure = extract_pdf_pages
