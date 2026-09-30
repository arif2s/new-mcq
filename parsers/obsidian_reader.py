import re
from pathlib import Path

def read_markdown_body(file_path: str) -> str:
    """Strips YAML frontmatter and extracts body markdown."""
    path = Path(file_path)
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="ignore")
    # Strip YAML frontmatter: --- ... ---
    body = re.sub(r"^---[\s\S]*?---\s*", "", content, flags=re.MULTILINE)
    # Simplify Obsidian wikilinks: [[Target|Label]] -> Label, [[Target]] -> Target
    body = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", body)
    return body.strip()
