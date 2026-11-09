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


# --- Phase 3 Obsidian YAML Frontmatter & Wikilink Extractor ---
import re as _re
from typing import Any as _Any

_FRONTMATTER_RE = _re.compile(r"\A---\s*\n(.*?)\n---\s*\n", _re.DOTALL)
_WIKILINK_RE = _re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")


def extract_obsidian_metadata_and_clean_body(markdown_text: str) -> tuple[dict[_Any, _Any], str]:
    metadata: dict[str, list[str]] = {"aliases": [], "tags": [], "wikilinks": []}
    if not markdown_text:
        return metadata, ""
    body = markdown_text
    fm_match = _FRONTMATTER_RE.match(markdown_text)
    if fm_match:
        fm_raw = fm_match.group(1)
        body = markdown_text[fm_match.end():]
        for key in ("aliases", "tags"):
            inline = _re.search(rf"^{key}:\s*\[(.*?)\]", fm_raw, _re.MULTILINE)
            if inline:
                metadata[key].extend([x.strip().strip("'\"") for x in inline.group(1).split(",") if x.strip()])
            else:
                block = _re.search(rf"^{key}:\s*\n((?:\s*-\s*.*\n?)+)", fm_raw, _re.MULTILINE)
                if block:
                    metadata[key].extend([
                        _re.sub(r"^\s*-\s*", "", line).strip().strip("'\"")
                        for line in block.group(1).splitlines()
                        if line.strip()
                    ])

    def _replace_wl(m: _re.Match) -> str:
        target = m.group(1).strip()
        label = (m.group(len(m.groups())) or target).strip() if m.lastindex else target
        if target and target not in metadata["wikilinks"]:
            metadata["wikilinks"].append(target)
        return label

    return metadata, _WIKILINK_RE.sub(_replace_wl, body).strip()


from dataclasses import dataclass, field
import json

_DISAMBIG_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Matches ![[image.png]], ![[assets/pic.jpg|400]], ![[note#heading]]
_EMBED_LINK_RE = re.compile(r"!\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
# Matches [[target]], [[treatment(1)|Treatment]], [[target#heading|label]]
_WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
# Matches ==highlighted text==
_HIGHLIGHT_RE = re.compile(r"==([^=\n]+)==")
# Matches Obsidian callouts: > [!note] Optional Title
_CALLOUT_START_RE = re.compile(r"^>\s*\[!([a-zA-Z0-9_-]+)\]([+-])?(?:\s+(.*))?$", re.MULTILINE)

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"})
HUB_INDEX_FILENAMES: frozenset[str] = frozenset({"topics.md", "chapters.md", "headings.md", "index.md"})
CATEGORY_FOLDERS: frozenset[str] = frozenset({"topics", "chapters", "headings"})

# Clinical subheading synonym clusters so "treatment" matches "## Management" or "## Therapy"
CLINICAL_SUBHEADING_SYNONYMS: dict[str, tuple[str, ...]] = {
    "treatment": ("treatment", "management", "therapy", "therapeutics", "rx", "intervention", "prophylaxis"),
    "management": ("management", "treatment", "therapy", "therapeutics", "rx", "care"),
    "therapy": ("therapy", "treatment", "management", "rx"),
    "diagnosis": ("diagnosis", "diagnostic", "investigations", "workup", "evaluation", "labs", "imaging", "dx"),
    "investigations": ("investigations", "diagnosis", "workup", "evaluation", "labs", "imaging"),
    "symptoms": ("symptoms", "signs", "clinical", "features", "presentation", "manifestations", "history"),
    "presentation": ("presentation", "symptoms", "signs", "features", "manifestations"),
    "causes": ("causes", "etiology", "aetiology", "risk", "factors", "pathogenesis"),
    "etiology": ("etiology", "aetiology", "causes", "pathogenesis"),
    "pathophysiology": ("pathophysiology", "pathology", "mechanism", "pathogenesis"),
    "complications": ("complications", "sequelae", "prognosis", "outcomes"),
}


def heading_slug(text: str) -> str:
    """Create a deterministic HTML id slug for an Obsidian heading (e.g. '## c) Treatment:' -> 'heading-c-treatment')."""
    cleaned = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return f"heading-{cleaned or 'section'}"


def expand_query_tokens_with_synonyms(tokens: list[str]) -> dict[str, set[str]]:
    """Map each query token to itself plus any clinical subheading synonyms."""
    expanded: dict[str, set[str]] = {}
    for tok in tokens:
        group = set(CLINICAL_SUBHEADING_SYNONYMS.get(tok, (tok,)))
        group.add(tok)
        expanded[tok] = group
    return expanded


def clean_disambiguated_title(raw_title: str) -> str:
    """Strip trailing numeric disambiguation suffixes like 'treatment(1)' -> 'treatment'."""
    if not raw_title:
        return ""
    return _DISAMBIG_SUFFIX_RE.sub("", raw_title.strip()).strip()


def normalize_note_lookup_key(name: str) -> str:
    """Normalize a note filename or wikilink target for case-insensitive graph resolution."""
    s = (name or "").strip().replace("\\", "/")
    if s.lower().endswith(".md"):
        s = s[:-3]
    return s.strip().lower()


def extract_heading_sections(markdown_body: str) -> list[dict[str, Any]]:
    """
    Parse all H1-H6 headings inside a Markdown note and slice the exact hierarchical
    section text under each heading (including nested child headings until the next heading of <= level).
    """
    matches = list(_HEADING_LINE_RE.finditer(markdown_body))
    if not matches:
        return []

    sections: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        lvl = len(m.group(1))
        title = m.group(2).strip()
        start_pos = m.start()
        end_pos = len(markdown_body)
        for j in range(i + 1, len(matches)):
            next_lvl = len(matches[j].group(1))
            if next_lvl <= lvl:
                end_pos = matches[j].start()
                break
        section_md = markdown_body[start_pos:end_pos].strip()
        sections.append(
            {
                "level": lvl,
                "title": title,
                "slug": heading_slug(title),
                "content": section_md,
            }
        )
    return sections


@dataclass
class ObsidianNoteRecord:
    note_id: str
    book_name: str
    category: str
    file_name: str
    raw_title: str  # e.g. 'treatment(1)' or 'heart attack'
    clean_title: str  # e.g. 'treatment' or 'heart attack'
    rel_path: str
    abs_path: str
    is_hub_index: bool = False
    headings: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    child_links: list[str] = field(default_factory=list)
    embedded_assets: list[str] = field(default_factory=list)
    parent_note_ids: list[str] = field(default_factory=list)
    parent_titles: list[str] = field(default_factory=list)
    breadcrumb_path: str = ""
    contextual_search_text: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    raw_markdown: str = ""

    def get_section_by_heading(self, heading_query: str) -> dict[str, Any] | None:
        """
        Locate the best matching internal H1-H6 section inside this note by exact, substring,
        or clinical synonym match (e.g., heading_query='treatment' matches '## Management').
        """
        if not heading_query or not self.sections:
            return None
        q_low = heading_query.strip().lower()
        q_tokens = [t for t in re.split(r"\W+", q_low) if len(t) >= 2]
        expanded = expand_query_tokens_with_synonyms(q_tokens)

        # 1. Exact or direct substring match on heading title or slug
        for sec in self.sections:
            t_low = sec["title"].lower()
            if q_low == t_low or q_low == sec["slug"] or q_low in t_low:
                return sec

        # 2. Synonym token match on heading title
        best_sec = None
        best_hits = 0
        for sec in self.sections:
            t_low = sec["title"].lower()
            hits = sum(1 for _, syns in expanded.items() if any(s in t_low for s in syns))
            if hits > best_hits:
                best_hits = hits
                best_sec = sec
        return best_sec

    def to_db_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "book_name": self.book_name,
            "category": self.category,
            "file_name": self.file_name,
            "raw_title": self.raw_title,
            "clean_title": self.clean_title,
            "rel_path": self.rel_path,
            "abs_path": self.abs_path,
            "is_hub_index": int(self.is_hub_index),
            "headings_json": json.dumps(self.headings, ensure_ascii=False),
            "child_links_json": json.dumps(self.child_links, ensure_ascii=False),
            "assets_json": json.dumps(self.embedded_assets, ensure_ascii=False),
            "parent_note_ids_json": json.dumps(self.parent_note_ids, ensure_ascii=False),
            "parent_titles_json": json.dumps(self.parent_titles, ensure_ascii=False),
            "breadcrumb_path": self.breadcrumb_path,
            "contextual_search_text": self.contextual_search_text,
            "content_markdown": self.raw_markdown,
        }


def parse_single_obsidian_note(
    md_path: Path,
    vault_root: Path,
    book_name: str,
) -> ObsidianNoteRecord:
    """Parse a single Obsidian .md file, extracting H1-H6 sections, child [[wikilinks]], and ![[assets]]."""
    raw_text = md_path.read_text(encoding="utf-8", errors="replace")
    rel_path = md_path.relative_to(vault_root).as_posix()
    raw_title = md_path.stem
    clean_title = clean_disambiguated_title(raw_title)
    lower_fname = md_path.name.lower()

    is_hub = lower_fname in HUB_INDEX_FILENAMES
    parent_folder_name = md_path.parent.name
    if is_hub:
        category = f"Hub:{raw_title}"
    elif parent_folder_name.lower() in CATEGORY_FOLDERS:
        category = parent_folder_name.capitalize()
    else:
        category = "General"

    frontmatter: dict[str, Any] = {}
    body = raw_text
    fm_match = _FRONTMATTER_RE.match(raw_text)
    if fm_match:
        body = raw_text[fm_match.end():]

    sections = extract_heading_sections(body)
    headings = [{"level": s["level"], "title": s["title"], "slug": s["slug"]} for s in sections]

    embedded_assets: list[str] = []
    child_links: list[str] = []

    for em in _EMBED_LINK_RE.finditer(body):
        target = em.group(1).strip()
        ext = Path(target).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            if target not in embedded_assets:
                embedded_assets.append(target)
        elif target and target not in child_links:
            child_links.append(target)

    for wm in _WIKILINK_RE.finditer(body):
        target = wm.group(1).strip()
        ext = Path(target).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            if target not in embedded_assets:
                embedded_assets.append(target)
        elif target and target not in child_links:
            child_links.append(target)

    note_id = f"{book_name}::{rel_path}"
    return ObsidianNoteRecord(
        note_id=note_id,
        book_name=book_name,
        category=category,
        file_name=md_path.name,
        raw_title=raw_title,
        clean_title=clean_title,
        rel_path=rel_path,
        abs_path=str(md_path.resolve()),
        is_hub_index=is_hub,
        headings=headings,
        sections=sections,
        child_links=child_links,
        embedded_assets=embedded_assets,
        frontmatter=frontmatter,
        raw_markdown=raw_text,
    )


class ObsidianVaultGraph:
    """
    Indexes an entire Obsidian Vault supporting both:
    - Multi-note Parent -> Child trees (e.g., 'heart attack.md' -> 'treatment(1).md')
    - Compact single-file notes with internal subheadings (e.g., 'heart attack.md' containing '## Management')
    """

    def __init__(self, vault_root: str | Path) -> None:
        self.vault_root = Path(vault_root).resolve()
        self.notes_by_id: dict[str, ObsidianNoteRecord] = {}
        self._book_lookup: dict[tuple[str, str], str] = {}
        self._global_lookup: dict[str, list[str]] = {}
        self._asset_lookup: dict[tuple[str, str], Path] = {}
        self._global_asset_lookup: dict[str, Path] = {}

    def build(self) -> dict[str, ObsidianNoteRecord]:
        if not self.vault_root.exists():
            return {}

        for md_path in sorted(self.vault_root.rglob("*.md")):
            if any(part.startswith(".") for part in md_path.relative_to(self.vault_root).parts):
                continue
            rel_parts = md_path.relative_to(self.vault_root).parts
            book_name = rel_parts[0] if len(rel_parts) > 1 else self.vault_root.name

            rec = parse_single_obsidian_note(md_path, self.vault_root, book_name)
            self.notes_by_id[rec.note_id] = rec

            stem_key = normalize_note_lookup_key(rec.raw_title)
            book_rel_key = normalize_note_lookup_key(
                "/".join(rel_parts[1:]) if len(rel_parts) > 1 else rec.rel_path
            )
            self._book_lookup[(book_name.lower(), stem_key)] = rec.note_id
            self._book_lookup[(book_name.lower(), book_rel_key)] = rec.note_id
            self._global_lookup.setdefault(stem_key, []).append(rec.note_id)

        for file_path in sorted(self.vault_root.rglob("*")):
            if not file_path.is_file() or file_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            rel_parts = file_path.relative_to(self.vault_root).parts
            book_name = rel_parts[0] if len(rel_parts) > 1 else self.vault_root.name
            fname_key = file_path.name.lower()
            rel_asset_key = "/".join(rel_parts[1:]).lower() if len(rel_parts) > 1 else fname_key
            self._asset_lookup[(book_name.lower(), fname_key)] = file_path.resolve()
            self._asset_lookup[(book_name.lower(), rel_asset_key)] = file_path.resolve()
            self._global_asset_lookup[fname_key] = file_path.resolve()

        for parent_id, parent_rec in self.notes_by_id.items():
            for raw_target in parent_rec.child_links:
                target_id = self.resolve_wikilink(raw_target, current_book=parent_rec.book_name)
                if target_id and target_id in self.notes_by_id and target_id != parent_id:
                    child_rec = self.notes_by_id[target_id]
                    if parent_id not in child_rec.parent_note_ids:
                        child_rec.parent_note_ids.append(parent_id)
                    parent_label = parent_rec.clean_title or parent_rec.raw_title
                    if parent_label not in child_rec.parent_titles:
                        child_rec.parent_titles.append(parent_label)

        for rec in self.notes_by_id.values():
            non_hub_parents = [
                p for p in rec.parent_titles
                if p.lower() not in {"topics", "chapters", "headings", "index"}
            ]
            hub_parents = [
                p for p in rec.parent_titles
                if p.lower() in {"topics", "chapters", "headings", "index"}
            ]
            ordered_parents = hub_parents + non_hub_parents
            crumbs = [rec.book_name]
            if rec.category and not rec.is_hub_index and rec.category not in crumbs:
                crumbs.append(rec.category)
            for pt in ordered_parents:
                if pt not in crumbs:
                    crumbs.append(pt)
            crumbs.append(rec.clean_title or rec.raw_title)
            rec.breadcrumb_path = " > ".join(crumbs)

            heading_str = " ".join(h["title"] for h in rec.headings)
            parent_context_str = " ".join(non_hub_parents)
            rec.contextual_search_text = (
                f"{rec.clean_title} {rec.raw_title} {parent_context_str} "
                f"{rec.breadcrumb_path} {heading_str}"
            ).strip()

        return self.notes_by_id

    def resolve_wikilink(self, target: str, current_book: str | None = None) -> str | None:
        key = normalize_note_lookup_key(target)
        if not key:
            return None
        if current_book:
            hit = self._book_lookup.get((current_book.lower(), key))
            if hit:
                return hit
            leaf_key = key.rsplit("/", 1)[-1]
            hit = self._book_lookup.get((current_book.lower(), leaf_key))
            if hit:
                return hit
        candidates = self._global_lookup.get(key) or self._global_lookup.get(key.rsplit("/", 1)[-1])
        return candidates[0] if candidates else None

    def resolve_asset_path(self, asset_ref: str, current_book: str | None = None) -> Path | None:
        clean_ref = (asset_ref or "").strip().replace("\\", "/").lstrip("/")
        if not clean_ref:
            return None
        low_ref = clean_ref.lower()
        leaf_name = Path(clean_ref).name.lower()
        if current_book:
            b_low = current_book.lower()
            for candidate_key in (low_ref, leaf_name, f"assets/{leaf_name}"):
                if (b_low, candidate_key) in self._asset_lookup:
                    return self._asset_lookup[(b_low, candidate_key)]
        return self._global_asset_lookup.get(leaf_name)

    def search_notes_and_sections(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """
        Unified search that handles BOTH:
        1. Split child notes: 'treatment(1).md' under parent 'heart attack.md'
        2. Compact single notes: 'heart attack.md' with internal subheading '## Treatment' or '## Management'
        Returns dicts with:
          - 'record': ObsidianNoteRecord
          - 'score': float
          - 'matched_section': dict | None (if a specific internal H1-H6 subheading matched)
          - 'matched_content': str (sliced section markdown if subheading matched, else full note markdown)
          - 'anchor_slug': str | None (for scrolling directly to #heading-slug in the UI)
        """
        q_tokens = [
            t for t in re.split(r"\W+", (query or "").lower())
            if len(t) >= 2 and t not in {"of", "the", "in", "for", "and", "to", "a", "an", "with", "on"}
        ]
        if not q_tokens:
            return []

        expanded_map = expand_query_tokens_with_synonyms(q_tokens)
        results: list[dict[str, Any]] = []

        for rec in self.notes_by_id.values():
            if rec.is_hub_index:
                continue
            title_low = rec.clean_title.lower()
            parents_low = " ".join(rec.parent_titles).lower()
            body_low = rec.raw_markdown.lower()

            score = 0.0
            matched_in_title: set[str] = set()
            matched_in_parents: set[str] = set()
            matched_in_headings: set[str] = set()
            best_section: dict[str, Any] | None = None
            best_section_score = 0.0

            for tok, syn_set in expanded_map.items():
                if any(s in title_low for s in syn_set):
                    score += 4.0
                    matched_in_title.add(tok)
                if any(s in parents_low for s in syn_set):
                    score += 3.5
                    matched_in_parents.add(tok)
                if tok in body_low:
                    score += 0.5

            # Check internal H1-H6 sections for direct or synonym heading matches
            for sec in rec.sections:
                sec_title_low = sec["title"].lower()
                # Skip top-level H1 if it merely repeats the note filename
                is_redundant_h1 = (sec["level"] == 1 and sec_title_low == title_low)
                sec_hits = 0.0
                for tok, syn_set in expanded_map.items():
                    if tok in sec_title_low:
                        sec_hits += 3.5
                        if not is_redundant_h1 or tok not in matched_in_title:
                            matched_in_headings.add(tok)
                    elif any(s in sec_title_low for s in syn_set):
                        sec_hits += 3.0  # Clinical synonym hit (e.g. 'treatment' -> '## Management')
                        if not is_redundant_h1 or tok not in matched_in_title:
                            matched_in_headings.add(tok)
                if sec_hits > best_section_score and not is_redundant_h1:
                    best_section_score = sec_hits
                    best_section = sec

            score += best_section_score

            # Synergy Case A (Split Child Note): Query matches Child Title ('treatment') + Parent Title ('heart attack')
            if matched_in_title and matched_in_parents and (matched_in_title != matched_in_parents):
                score += 6.0

            # Synergy Case B (Small Single Note): Query matches Note Title ('heart attack') + Internal Subheading ('## Management')
            unmatched_by_title_matched_by_heading = matched_in_headings - matched_in_title
            if matched_in_title and unmatched_by_title_matched_by_heading:
                score += 6.0

            if score > 0:
                results.append(
                    {
                        "record": rec,
                        "score": round(score, 3),
                        "matched_section": best_section,
                        "matched_content": best_section["content"] if best_section else rec.raw_markdown,
                        "anchor_slug": best_section["slug"] if best_section else None,
                    }
                )

        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    def search_notes_by_topic(self, query: str, top_k: int = 10) -> list[ObsidianNoteRecord]:
        """Backward-compatible wrapper returning ranked ObsidianNoteRecord objects."""
        return [item["record"] for item in self.search_notes_and_sections(query, top_k=top_k)]


def convert_obsidian_markdown_to_html_ready(
    markdown_text: str,
    book_name: str = "",
    parent_titles: list[str] | None = None,
    asset_url_prefix: str = "/api/obsidian/asset",
) -> str:
    """
    Transform Obsidian-flavored Markdown into standard HTML/Markdown ready for marked.js + DOMPurify + KaTeX:
    - Injects deterministic id="heading-..." anchors on all H1-H6 headings so #heading deep-links scroll accurately.
    - Converts ![[image.png|400]] into <img src="/api/obsidian/asset?book=...&asset=..." width="400">.
    - Converts [[target#heading|Label]] into interactive <a class="obsidian-wikilink" data-note-target="target" data-anchor="heading-...">.
    - Converts ==highlights== and > [!note] Callouts.
    """
    if not markdown_text:
        return ""

    body = _FRONTMATTER_RE.sub("", markdown_text)

    # 1. Convert embedded images ![[pic.jpg|350]] and embedded notes ![[child_note#heading]]
    def _replace_embed(match: re.Match) -> str:
        target = (match.group(1) or "").strip()
        anchor_raw = (match.group(2) or "").strip()
        modifier = (match.group(3) or "").strip()
        ext = Path(target).suffix.lower()

        if ext in IMAGE_EXTENSIONS:
            q_book = urllib.parse.quote(book_name or "")
            q_asset = urllib.parse.quote(target)
            src = f"{asset_url_prefix}?book={q_book}&asset={q_asset}"
            alt = html.escape(modifier if modifier and not modifier.isdigit() else Path(target).stem)
            width_attr = f' width="{int(modifier)}"' if modifier.isdigit() else ""
            return f'<img src="{src}" alt="{alt}"{width_attr} class="obsidian-embed-img" loading="lazy" />'

        display = html.escape(modifier or clean_disambiguated_title(target) or target)
        anchor_id = heading_slug(anchor_raw) if anchor_raw else ""
        return (
            f'<a href="#{html.escape(anchor_id)}" class="obsidian-wikilink obsidian-note-embed" '
            f'data-note-target="{html.escape(target)}" data-book="{html.escape(book_name or "")}" '
            f'data-anchor="{html.escape(anchor_id)}">📄 {display}</a>'
        )

    body = _EMBED_LINK_RE.sub(_replace_embed, body)

    # 2. Convert internal wikilinks [[treatment(1)|Treatment]] or [[heart attack#Management]]
    def _replace_wikilink(match: re.Match) -> str:
        target = (match.group(1) or "").strip()
        anchor_raw = (match.group(2) or "").strip()
        label = (match.group(3) or "").strip()
        ext = Path(target).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            q_book = urllib.parse.quote(book_name or "")
            q_asset = urllib.parse.quote(target)
            src = f"{asset_url_prefix}?book={q_book}&asset={q_asset}"
            return f'<img src="{src}" alt="{html.escape(label or Path(target).stem)}" class="obsidian-embed-img" loading="lazy" />'

        display = html.escape(label or (f"{clean_disambiguated_title(target)} > {anchor_raw}" if anchor_raw else clean_disambiguated_title(target)) or target)
        anchor_id = heading_slug(anchor_raw) if anchor_raw else ""
        return (
            f'<a href="#{html.escape(anchor_id)}" class="obsidian-wikilink" '
            f'data-note-target="{html.escape(target)}" data-book="{html.escape(book_name or "")}" '
            f'data-anchor="{html.escape(anchor_id)}">{display}</a>'
        )

    body = _WIKILINK_RE.sub(_replace_wikilink, body)

    # 3. Add deterministic id="heading-..." attributes to H1-H6 headings for subheading scrolling
    def _replace_heading(match: re.Match) -> str:
        hashes = match.group(1)
        lvl = len(hashes)
        title_raw = match.group(2).strip()
        slug = heading_slug(title_raw)
        return f'<h{lvl} id="{slug}" class="obsidian-heading obsidian-h{lvl}">{title_raw}</h{lvl}>'

    body = _HEADING_LINE_RE.sub(_replace_heading, body)

    # 4. Convert ==highlights==
    body = _HIGHLIGHT_RE.sub(r'<mark class="obsidian-highlight">\1</mark>', body)

    # 5. Convert > [!info] Title callout headers
    def _replace_callout(match: re.Match) -> str:
        ctype = html.escape(match.group(1).lower())
        ctitle = html.escape((match.group(3) or ctype.capitalize()).strip())
        return f'> <div class="obsidian-callout-header" data-callout="{ctype}"><strong>{ctitle}</strong></div>'

    body = _CALLOUT_START_RE.sub(_replace_callout, body)

    # 6. Prepend Parent Breadcrumb Context Bar if parent_titles exist
    if parent_titles:
        links_html = " &rsaquo; ".join(
            f'<a href="#" class="obsidian-wikilink obsidian-parent-crumb" '
            f'data-note-target="{html.escape(p)}" data-book="{html.escape(book_name)}">{html.escape(p)}</a>'
            for p in parent_titles
        )
        crumb_bar = f'<div class="obsidian-breadcrumb-bar">📚 <strong>{html.escape(book_name)}</strong> &rsaquo; {links_html}</div>\n\n'
        body = crumb_bar + body

    return body.strip()
