import re
import html
import logging

logger = logging.getLogger(__name__)

_archive_cache = {}

def get_archive(zim_path: str):
    if zim_path not in _archive_cache:
        import libzim
        _archive_cache[zim_path] = libzim.Archive(zim_path)
    return _archive_cache[zim_path]

def get_zim_article(zim_path: str, title: str) -> str:
    try:
        archive = get_archive(zim_path)
        formatted_title = title.replace(' ', '_')

        # Support both historical (A/) and modern (C/) ZIM namespaces
        entry = archive.get_entry_by_path(f"C/{formatted_title}") or archive.get_entry_by_path(f"A/{formatted_title}")

        if not entry:
            return ""

        raw_html = bytes(entry.get_item().content).decode("utf-8", errors="ignore")

        # Remove script and style blocks before stripping tags
        no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', raw_html, flags=re.IGNORECASE | re.DOTALL)
        clean_text = re.sub(r"<[^>]+>", " ", no_scripts)
        clean_text = html.unescape(clean_text)

        return " ".join(clean_text.split())[:3000]
    except Exception as e:
        logger.warning(f"Failed to extract from ZIM {zim_path}: {e}")
        return ""
