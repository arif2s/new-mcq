"""
Obsidian Markdown reader, frontmatter stripper, wikilink cleaner, and section extractor.
Enforces zero VRAM usage, ChunkDict compliance, UTF-8 BOM safety, and code block protection.
"""

from __future__ import annotations

import functools
import hashlib
import html
import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

logger = logging.getLogger(__name__)

# Pre-compiled regular expressions
# Robust YAML frontmatter: handles --- or ... delimiters, empty frontmatter, CRLF, and trailing EOF
_FRONTMATTER_RE = re.compile(
    r"\A---\s*[\r\n]+(?:(.*?)[\r\n]+)?(?:---|\.\.\.)\s*(?:[\r\n]+|\Z)",
    flags=re.DOTALL,
)

# Comments and embeds for plain-text RAG body
_DELETIONS_RE = re.compile(r"%%.*?%%|!\[\[.*?\]\]", flags=re.DOTALL)
_COMMENTS_ONLY_RE = re.compile(r"%%.*?%%", flags=re.DOTALL)

# Obsidian Image/Asset Embeds: ![[image.png]] or ![[folder/image.png|400]]
_IMAGE_EMBED_RE = re.compile(r"!\[\[([^|\]]+)(?:\|([^\]]*))?\]\]")

# Obsidian Wikilinks: [[Target|Label]], [[Target#Heading|Label]], [[Target#Heading]], [[Target]], [[#Heading]]
_WIKILINK_EXTRACT_RE = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]*))?\]\]")

# Markdown Heading Detector (ATX style)
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _clean_wikilink_text(raw_text: str) -> str:
    """
    Substitutes wikilinks with their cleanest human-readable presentation:
    - [[Target|Label]] -> Label
    - [[Target#Section]] -> Target (or Section if Target is empty)
    - [[Target]] -> Target
    """
    if not raw_text:
        return ""

    def _repl(match: re.Match) -> str:
        target = match.group(1).strip()
        label = match.group(2)
        if label is not None and label.strip():
            return label.strip()
        if "#" in target:
            parts = target.split("#", 1)
            return parts[0].strip() if parts[0].strip() else parts[1].strip()
        return target

    return _WIKILINK_EXTRACT_RE.sub(_repl, raw_text)


@functools.lru_cache(maxsize=512)
def _cached_read_markdown_body(normalized_path: str) -> str:
    """Internal LRU-cached reader (maxsize=512) for normalized file paths."""
    try:
        path_obj = Path(normalized_path)
        if not path_obj.is_file():
            return ""

        # Invariant 19: Use utf-8-sig to automatically strip Windows UTF-8 BOM if present
        content = path_obj.read_text(encoding="utf-8-sig", errors="ignore")
        if not content:
            return ""

        body = _FRONTMATTER_RE.sub("", content)
        body = _DELETIONS_RE.sub("", body)
        body = _clean_wikilink_text(body)
        return body.strip()

    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("OS Error reading Obsidian note %s: %s", normalized_path, exc)
        return ""
    except Exception as exc:
        logger.error("Unexpected error parsing Obsidian note %s: %s", normalized_path, exc)
        return ""


def read_markdown_body(file_path: Union[str, Path]) -> str:
    """
    Reads and cleans an Obsidian markdown file for RAG synthesis and indexing.
    Caches results to eliminate disk I/O bottlenecks during search reranking.
    """
    if not file_path:
        return ""
    try:
        norm_path = str(Path(file_path).resolve())
    except Exception:
        norm_path = str(file_path)
    return _cached_read_markdown_body(norm_path)


def extract_obsidian_sections(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Splits an Obsidian Markdown note by headings while tracking fenced code block state (Invariant 19),
    eliminating phantom empty sections, and returning standard ChunkDict dictionaries.
    """
    if not file_path:
        return []

    norm_path = str(Path(file_path).resolve())
    path_obj = Path(norm_path)
    if not path_obj.is_file():
        return []

    try:
        # Invariant 19: Decode with utf-8-sig to strip Windows UTF-8 BOM
        raw_content = path_obj.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception as exc:
        logger.warning("Failed to read Obsidian file %s: %s", norm_path, exc)
        return []

    if not raw_content.strip():
        return []

    note_title = path_obj.stem
    body = _FRONTMATTER_RE.sub("", raw_content)
    body = _COMMENTS_ONLY_RE.sub("", body)
    total_length = len(body)

    lines = body.splitlines()
    heading_stack: Dict[int, str] = {0: note_title}
    current_lines: List[str] = []
    sections: List[Dict[str, Any]] = []
    chunk_index = 0
    active_fence: Optional[str] = None

    def get_current_heading_path() -> str:
        valid_parts = [
            heading_stack[k].strip()
            for k in sorted(heading_stack.keys())
            if heading_stack[k].strip()
        ]
        return " > ".join(valid_parts) if valid_parts else note_title

    def flush_section() -> None:
        nonlocal chunk_index
        if not current_lines:
            return

        # Prevent phantom sections containing only heading echo tags
        substantive_lines = [
            line
            for line in current_lines
            if not (line.strip().startswith("[") and line.strip().endswith("]"))
        ]
        if not substantive_lines:
            return

        text = "\n".join(current_lines).strip()
        clean_text = _clean_wikilink_text(_DELETIONS_RE.sub("", text)).strip()
        if not clean_text:
            return

        h_path = get_current_heading_path()
        subheading = h_path.split(" > ")[-1] if " > " in h_path else h_path
        snippet = clean_text[:400] + ("..." if len(clean_text) > 400 else "")

        # Deterministic MD5 ID generation matching LanceDB and ChunkDict invariants
        raw_key = f"{norm_path}_1_{chunk_index}_{clean_text[:50]}"
        chunk_id = hashlib.md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()

        sections.append({
            "id": chunk_id,
            "doc_id": chunk_id,
            "text": clean_text,
            "snippet": snippet,
            "title": note_title,
            "source_article": note_title,
            "main_topic": note_title,
            "heading": h_path,
            "heading_path": h_path,
            "subheading": subheading,
            "source": "obsidian",
            "file_path": norm_path,
            "page": 1,
            "page_number": 1,
            "chunk_index": chunk_index,
            "length": total_length,
            "doc_length": total_length,
            "article_length": total_length,
            "parent_content": clean_text,
            "child_content": clean_text,
            "score": 0.0,
            "rerank_score": 0.0,
            "parent_id": None,
        })
        chunk_index += 1

    for line in lines:
        stripped_line = line.strip()

        # Invariant 19: Guard against code block comments being mistaken for Markdown headings
        if stripped_line.startswith("```") or stripped_line.startswith("~~~"):
            fence_marker = "```" if stripped_line.startswith("```") else "~~~"
            if active_fence is None:
                active_fence = fence_marker
            elif active_fence == fence_marker:
                active_fence = None
            current_lines.append(line)
            continue

        if active_fence is None:
            m = _HEADING_LINE_RE.match(stripped_line)
            if m:
                flush_section()
                level = len(m.group(1))
                h_text = _clean_wikilink_text(m.group(2).strip())

                # Pop equal or deeper headings from the stack
                for k in [k for k in list(heading_stack.keys()) if k >= level]:
                    del heading_stack[k]
                heading_stack[level] = h_text

                current_lines = [f"[{get_current_heading_path()}]"]
                continue

        current_lines.append(line)

    flush_section()

    # Fallback: if no markdown headings existed, treat entire cleaned body as a single chunk
    if not sections and body.strip():
        clean_full = read_markdown_body(norm_path)
        if clean_full:
            chunk_id = hashlib.md5(
                f"{norm_path}_1_0_{clean_full[:50]}".encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()
            doc_len = len(clean_full)
            sections.append({
                "id": chunk_id,
                "doc_id": chunk_id,
                "text": clean_full,
                "snippet": clean_full[:400] + ("..." if doc_len > 400 else ""),
                "title": note_title,
                "source_article": note_title,
                "main_topic": note_title,
                "heading": note_title,
                "heading_path": note_title,
                "subheading": note_title,
                "source": "obsidian",
                "file_path": norm_path,
                "page": 1,
                "page_number": 1,
                "chunk_index": 0,
                "length": doc_len,
                "doc_length": doc_len,
                "article_length": doc_len,
                "parent_content": clean_full,
                "child_content": clean_full,
                "score": 0.0,
                "rerank_score": 0.0,
                "parent_id": None,
            })

    return sections


def _extract_headings_outside_fences(markdown_body: str) -> List[str]:
    """Extracts ATX markdown headings while ignoring lines inside fenced code blocks (Invariant 19)."""
    headings: List[str] = []
    active_fence: Optional[str] = None
    for line in markdown_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence_marker = "```" if stripped.startswith("```") else "~~~"
            if active_fence is None:
                active_fence = fence_marker
            elif active_fence == fence_marker:
                active_fence = None
            continue
        if active_fence is None:
            m = _HEADING_LINE_RE.match(stripped)
            if m:
                h_clean = _clean_wikilink_text(m.group(2).strip())
                if h_clean:
                    headings.append(h_clean)
    return headings


def read_obsidian_for_browser(file_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Prepares an Obsidian note for rich in-browser rendering (Section 3):
    - Strips frontmatter while preserving tables, HTML, and LaTeX equations ($...$, $$...$$)
    - Rewrites image embeds ![[img.png|400]] to /api/sources/asset
    - Rewrites wikilinks to compliant .citation-link citation anchors with data-source-type="obsidian"
    """
    if not file_path:
        return {"status": "error", "message": "Empty file_path provided"}

    path_obj = Path(file_path).resolve()
    if not path_obj.is_file():
        return {"status": "error", "message": f"Obsidian note not found: {file_path}"}

    try:
        # Invariant 19: Decode with utf-8-sig
        raw_content = path_obj.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    frontmatter_raw = ""
    fm_match = _FRONTMATTER_RE.match(raw_content)
    if fm_match:
        frontmatter_raw = (fm_match.group(1) or "").strip()
        body = raw_content[fm_match.end() :]
    else:
        body = raw_content

    # Remove Obsidian %% comments %%
    body = _COMMENTS_ONLY_RE.sub("", body)

    # Extract outline headings before HTML link rewriting (respecting code fences per Invariant 19)
    headings = _extract_headings_outside_fences(body)

    # Convert ![[image.png|width]] to <img src="/api/sources/asset?path=..." /> or clickable note embed
    def _replace_embed(match: re.Match) -> str:
        target = match.group(1).strip()
        size_spec = (match.group(2) or "").strip()
        encoded_path = urllib.parse.quote(target)
        safe_target = html.escape(target, quote=True)
        ext = Path(target).suffix.lower()

        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}:
            width_attr = (
                f' style="max-width:{int(size_spec)}px;"'
                if size_spec.isdigit()
                else ' style="max-width:100%;"'
            )
            return (
                f'<img src="/api/sources/asset?path={encoded_path}" '
                f'alt="{safe_target}" class="rounded-lg my-3 shadow-md"{width_attr} />'
            )

        return (
            f'<a href="#" class="citation-link citation-obsidian underline text-accent" '
            f'data-source-type="obsidian" data-file-path="{safe_target}" '
            f'data-page="1">{safe_target}</a>'
        )

    body = _IMAGE_EMBED_RE.sub(_replace_embed, body)

    # Convert [[Target|Label]] to clickable internal Obsidian citation links (Section 3 & Invariant 5)
    def _replace_wikilink(match: re.Match) -> str:
        target_raw = match.group(1).strip()
        label_raw = match.group(2)

        target_file = target_raw
        anchor = ""
        if "#" in target_raw:
            parts = target_raw.split("#", 1)
            target_file = parts[0].strip()
            anchor = parts[1].strip()

        # If in-page anchor [[#Section]], default file to current note
        resolved_file = target_file if target_file else path_obj.stem
        display_label = (
            label_raw.strip()
            if (label_raw and label_raw.strip())
            else (anchor if not target_file else target_raw)
        )

        safe_file = html.escape(resolved_file, quote=True)
        safe_anchor = html.escape(anchor, quote=True)
        safe_label = html.escape(display_label, quote=True)

        return (
            f'<a href="#" class="citation-link citation-obsidian underline text-accent" '
            f'data-source-type="obsidian" data-file-path="{safe_file}" '
            f'data-anchor="{safe_anchor}" data-page="1">{safe_label}</a>'
        )

    body = _WIKILINK_EXTRACT_RE.sub(_replace_wikilink, body)

    return {
        "status": "success",
        "title": path_obj.stem,
        "file_path": str(path_obj),
        "frontmatter": frontmatter_raw,
        "markdown": body.strip(),
        "headings": headings,
        "length": len(body),
    }
