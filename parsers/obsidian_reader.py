import re
import urllib.parse
import logging
import functools
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Pre-compile frontmatter anchored to start of string
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", flags=re.DOTALL)

# Comments and embeds for plain-text RAG body
_DELETIONS_RE = re.compile(r"%%.*?%%|!\[\[.*?\]\]", flags=re.DOTALL)
_COMMENTS_ONLY_RE = re.compile(r"%%.*?%%", flags=re.DOTALL)

# Obsidian Image/Asset Embeds: ![[image.png]] or ![[folder/image.png|400]]
_IMAGE_EMBED_RE = re.compile(r"!\[\[([^|\]]+)(?:\|([^\]]*))?\]\]")

# Standard Wikilinks: [[Target|Label]] or [[Target]]
_WIKILINKS_RE = re.compile(r"\[\[(?:([^|\]]*)\|)?([^\]]+)\]\]")

# Markdown Heading Detector
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$", flags=re.MULTILINE)


@functools.lru_cache(maxsize=256)
def read_markdown_body(file_path: str) -> str:
    """
    Reads and cleans an Obsidian markdown file for RAG synthesis and indexing.
    Cached in memory to prevent redundant disk I/O across sequential questions.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        if not content:
            return ""

        body = _FRONTMATTER_RE.sub("", content)
        body = _DELETIONS_RE.sub("", body)
        body = _WIKILINKS_RE.sub(r"\2", body)
        return body.strip()

    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning(f"OS Error reading Obsidian note {file_path}: {e}")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error parsing Obsidian note {file_path}: {e}")
        return ""


def extract_obsidian_sections(file_path: str) -> List[Dict[str, Any]]:
    """
    Splits an Obsidian Markdown note by headings while maintaining the full hierarchical
    heading path (e.g., 'Amyloidosis > Cardiac Manifestations > Diagnosis') and total note length.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_content = f.read()
    except Exception:
        return []

    if not raw_content.strip():
        return []

    note_title = Path(file_path).stem
    body = _FRONTMATTER_RE.sub("", raw_content)
    body = _COMMENTS_ONLY_RE.sub("", body)
    total_length = len(body)

    lines = body.splitlines()
    heading_stack: Dict[int, str] = {0: note_title}
    current_lines: List[str] = []
    sections: List[Dict[str, Any]] = []

    def current_heading_path() -> str:
        return " > ".join(heading_stack[k] for k in sorted(heading_stack.keys()))

    def flush_section():
        text = "\n".join(current_lines).strip()
        if text:
            clean_text = _WIKILINKS_RE.sub(r"\2", _DELETIONS_RE.sub("", text)).strip()
            if clean_text:
                h_path = current_heading_path()
                sections.append({
                    "heading_path": h_path,
                    "subheading": h_path.split(" > ")[-1],
                    "text": clean_text,
                    "article_length": total_length,
                })

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if m:
            flush_section()
            level = len(m.group(1))
            h_text = _WIKILINKS_RE.sub(r"\2", m.group(2).strip())
            for k in [k for k in heading_stack.keys() if k >= level]:
                del heading_stack[k]
            heading_stack[level] = h_text
            current_lines = [f"[{current_heading_path()}]"]
        else:
            current_lines.append(line)

    flush_section()

    if not sections and body.strip():
        clean_full = read_markdown_body(file_path)
        sections.append({
            "heading_path": note_title,
            "subheading": note_title,
            "text": clean_full,
            "article_length": len(clean_full),
        })

    return sections


def read_obsidian_for_browser(file_path: str) -> Dict[str, Any]:
    """
    Prepares an Obsidian note for rich in-browser rendering:
    - Preserves Markdown tables (| |), HTML tags, and LaTeX equations ($...$, $$...$$)
    - Rewrites Obsidian image embeds ![[img.png|400]] to /api/sources/asset?path=...
    - Rewrites wikilinks [[Target|Label]] to clickable .citation-obsidian links
    """
    path_obj = Path(file_path)
    if not path_obj.exists() or not path_obj.is_file():
        return {"status": "error", "message": f"Obsidian note not found: {file_path}"}

    try:
        raw_content = path_obj.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return {"status": "error", "message": str(e)}

    frontmatter_raw = ""
    fm_match = _FRONTMATTER_RE.match(raw_content)
    if fm_match:
        frontmatter_raw = fm_match.group(1).strip()
        body = raw_content[fm_match.end():]
    else:
        body = raw_content

    # Remove Obsidian %% comments %%
    body = _COMMENTS_ONLY_RE.sub("", body)

    # Convert ![[image.png|width]] to <img src="/api/sources/asset?path=..." /> or note link
    def _replace_embed(match):
        target = match.group(1).strip()
        size_spec = (match.group(2) or "").strip()
        encoded_path = urllib.parse.quote(target)
        ext = Path(target).suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}:
            width_attr = f' style="max-width:{int(size_spec)}px;"' if size_spec.isdigit() else ' style="max-width:100%;"'
            return f'<img src="/api/sources/asset?path={encoded_path}" alt="{target}" class="rounded-lg my-3 shadow-md"{width_attr} />'
        return f'<a href="#" class="citation-obsidian" data-file="{target}">{target}</a>'

    body = _IMAGE_EMBED_RE.sub(_replace_embed, body)

    # Convert [[Target|Label]] to clickable internal Obsidian note links
    def _replace_wikilink(match):
        target = (match.group(1) or match.group(2) or "").strip()
        label = (match.group(2) or target).strip()
        target_clean = target.split("#")[0].strip()
        return f'<a href="#" class="citation-obsidian" data-file="{target_clean}">{label}</a>'

    body = _WIKILINKS_RE.sub(_replace_wikilink, body)

    headings = [m.group(2).strip() for m in _HEADING_LINE_RE.finditer(body)]

    return {
        "status": "success",
        "title": path_obj.stem,
        "file_path": str(path_obj.resolve()),
        "frontmatter": frontmatter_raw,
        "markdown": body.strip(),
        "headings": headings,
        "length": len(body),
    }
