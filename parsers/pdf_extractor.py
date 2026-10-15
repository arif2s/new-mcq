import functools
import logging
from typing import Dict, Any, List

try:
    import fitz
except ImportError:
    fitz = None

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=128)
def extract_page_slice(file_path: str, page_number: int) -> str:
    """
    Extracts text from a specific PDF page in natural visual reading order (sort=True).
    Results are cached in memory to optimize batch RAG synthesis.
    """
    if fitz is None:
        return ""
    try:
        with fitz.open(file_path) as doc:
            target_idx = max(0, int(page_number) - 1)
            if target_idx >= doc.page_count:
                return ""
            page = doc.load_page(target_idx)
            return page.get_text("text", sort=True)
    except Exception as e:
        logger.error(f"Failed to extract page {page_number} from {file_path}: {e}")
        return ""


def extract_page_with_headings(page, default_title: str = "") -> Dict[str, Any]:
    """
    Extracts both full page text and prominent section headings (via font size heuristics)
    during PyMuPDF (fitz) indexing so section headings are preserved without ML overhead.
    """
    try:
        text = page.get_text("text", sort=True).strip()
        if not text:
            return {"text": "", "heading_path": default_title, "subheading": default_title, "length": 0}

        blocks = page.get_text("dict").get("blocks", [])
        font_sizes: List[float] = []
        spans_info = []

        for b in blocks:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                line_text_parts = []
                max_size = 0.0
                for span in line.get("spans", []):
                    s_text = span.get("text", "").strip()
                    if s_text:
                        sz = float(span.get("size", 10.0))
                        font_sizes.append(sz)
                        if sz > max_size:
                            max_size = sz
                        line_text_parts.append(s_text)
                joined_line = " ".join(line_text_parts).strip()
                if joined_line and 3 <= len(joined_line) <= 110:
                    spans_info.append((max_size, joined_line))

        headings: List[str] = []
        if font_sizes and spans_info:
            sorted_sizes = sorted(font_sizes)
            median_size = sorted_sizes[len(sorted_sizes) // 2]
            threshold = max(median_size * 1.15, median_size + 1.5)
            for sz, l_text in spans_info:
                if sz >= threshold and not l_text.isdigit():
                    if l_text not in headings:
                        headings.append(l_text)
                    if len(headings) >= 2:
                        break

        heading_path = f"{default_title} > {' > '.join(headings)}" if headings else default_title
        return {
            "text": text,
            "heading_path": heading_path,
            "subheading": headings[-1] if headings else default_title,
            "length": len(text),
        }
    except Exception:
        fallback = page.get_text("text")
        return {
            "text": fallback,
            "heading_path": default_title,
            "subheading": default_title,
            "length": len(fallback),
        }
