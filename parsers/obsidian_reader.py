import re
from pathlib import Path

def read_markdown_body(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return ""

    content = path.read_text(encoding="utf-8", errors="ignore")

    # Strictly match frontmatter ONLY at the start of the file (\A)
    body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)

    # Strip Obsidian comments
    body = re.sub(r"%%.*?%%", "", body, flags=re.DOTALL)

    # Strip image/file embeds entirely
    body = re.sub(r"!\[\[.*?\]\]", "", body)

    # Simplify standard wikilinks: [[Target|Label]] -> Label, [[Target]] -> Target
    body = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", body)

    return body.strip()
