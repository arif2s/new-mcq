import re
import html
import logging
import functools

logger = logging.getLogger(__name__)

# Pre-compile regex patterns at module load to avoid recompiling on every extraction
_SCRIPT_STYLE_PATTERN = re.compile(r'<(script|style)[^>]*>.*?</\1>', flags=re.IGNORECASE | re.DOTALL)
_HTML_TAG_PATTERN = re.compile(r'<[^>]+>')
_WHITESPACE_PATTERN = re.compile(r'\s+')

@functools.lru_cache(maxsize=4)
def get_archive(zim_path: str):
    """
    Loads and caches ZIM archives.
    LRU cache prevents unbounded open file handles from locking up the Windows filesystem.
    """
    import libzim
    return libzim.Archive(zim_path)

def get_zim_article(zim_path: str, title: str) -> str:
    try:
        archive = get_archive(zim_path)
        formatted_title = title.replace(' ', '_')

        # Support both historical (A/) and modern (C/) ZIM namespaces
        entry = archive.get_entry_by_path(f"C/{formatted_title}") or archive.get_entry_by_path(f"A/{formatted_title}")

        if not entry:
            return ""

        raw_html = bytes(entry.get_item().content).decode("utf-8", errors="ignore")

        # Fast C-level regex substitution
        no_scripts = _SCRIPT_STYLE_PATTERN.sub(' ', raw_html)
        clean_text = _HTML_TAG_PATTERN.sub(' ', no_scripts)

        # Unescape entities (e.g., &amp; -> &)
        clean_text = html.unescape(clean_text)

        # Collapse whitespace quickly using pre-compiled regex
        clean_text = _WHITESPACE_PATTERN.sub(' ', clean_text).strip()

        return clean_text[:3000]

    except Exception as e:
        logger.warning(f"Failed to extract from ZIM {zim_path}: {e}")
        return ""
