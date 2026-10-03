import re
import logging
import functools

logger = logging.getLogger(__name__)

# Pre-compile frontmatter separately because it anchors to the start of the string (\A)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", flags=re.DOTALL)

# Combine Comments and Embeds into a single regex for a 1-pass deletion
_DELETIONS_RE = re.compile(r"%%.*?%%|!\[\[.*?\]\]", flags=re.DOTALL)

# Wikilinks extractor
_WIKILINKS_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")

@functools.lru_cache(maxsize=256)
def read_markdown_body(file_path: str) -> str:
    """
    Reads and cleans an Obsidian markdown file.
    Cached in memory to prevent redundant disk I/O across sequential questions.
    """
    try:
        # Open the file directly instead of using path.exists() first.
        # This halves the number of NTFS kernel calls on Windows.
        with open(file_path, 'r', encoding="utf-8", errors="ignore") as f:
            content = f.read()

        if not content:
            return ""

        # Strictly match frontmatter ONLY at the start of the file
        body = _FRONTMATTER_RE.sub("", content)

        # Strip Obsidian comments and image/file embeds in a single C-level pass
        body = _DELETIONS_RE.sub("", body)

        # Simplify standard wikilinks: [[Target|Label]] -> Label, [[Target]] -> Target
        body = _WIKILINKS_RE.sub(r"\1", body)

        return body.strip()

    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning(f"OS Error reading Obsidian note {file_path}: {e}")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error parsing Obsidian note {file_path}: {e}")
        return ""
