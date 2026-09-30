import re
import logging
logger = logging.getLogger(__name__)

def get_zim_article(zim_path: str, title: str) -> str:
    """Reads article text directly from a local .zim archive via libzim."""
    try:
        import libzim
        archive = libzim.Archive(zim_path)
        entry = archive.get_entry_by_path(f"A/{title.replace(' ', '_')}")
        if not entry:
            return ""
        raw_html = bytes(entry.get_item().content).decode("utf-8", errors="ignore")
        # Lightweight regex strip of HTML tags
        clean_text = re.sub(r"<[^>]+>", " ", raw_html)
        return " ".join(clean_text.split())[:3000]
    except Exception as e:
        logger.warning(f"Failed to extract from ZIM {zim_path}: {e}")
        return ""
