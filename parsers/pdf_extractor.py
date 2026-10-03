import fitz
import functools
import logging

logger = logging.getLogger(__name__)

# LRU Cache prevents repeated disk I/O for frequently accessed reference pages
@functools.lru_cache(maxsize=128)
def extract_page_slice(file_path: str, page_number: int) -> str:
    """
    Extracts text from a specific PDF page.
    Results are cached in memory to optimize batch processing.
    """
    try:
        with fitz.open(file_path) as doc:
            target_idx = max(0, page_number - 1)

            if target_idx >= doc.page_count:
                return ""

            page = doc.load_page(target_idx)

            # sort=True forces PyMuPDF to read text in natural visual order,
            # which is critical for multi-column clinical PDFs and textbooks.
            return page.get_text("text", sort=True)

    except Exception as e:
        # Prevent silent failures in the RAG pipeline
        logger.error(f"Failed to extract page {page_number} from {file_path}: {e}")
        return ""
