import re
from pathlib import Path

# Pre-compile regex patterns for performance
_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", flags=re.DOTALL)
_COMMENTS_RE = re.compile(r"%%.*?%%", flags=re.DOTALL)
_EMBEDS_RE = re.compile(r"!\[\[.*?\]\]")
_WIKILINKS_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")

def read_markdown_body(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return ""

    content = path.read_text(encoding="utf-8", errors="ignore")

    # Strictly match frontmatter ONLY at the start of the file (\A)
    body = _FRONTMATTER_RE.sub("", content)

    # Strip Obsidian comments
    body = _COMMENTS_RE.sub("", body)

    # Strip image/file embeds entirely
    body = _EMBEDS_RE.sub("", body)

    # Simplify standard wikilinks: [[Target|Label]] -> Label, [[Target]] -> Target
    body = _WIKILINKS_RE.sub(r"\1", body)

    return body.strip()
