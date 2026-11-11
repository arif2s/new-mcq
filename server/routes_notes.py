"""
External app bridges (SumatraPDF launch, Obsidian native URI, ZIM asset/article proxy).
Enforces zero VRAM usage, blueprint route aliases, polymorphic ZIM asset unpacking, and safe vault traversal.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import difflib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple
import urllib.parse

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from pydantic import BaseModel

try:
    from pydantic import ConfigDict

    _NOTE_CONFIG = ConfigDict(extra="allow")
except ImportError:
    _NOTE_CONFIG = None

from config import VAULTS_DIR
from core.database import get_db_connection, atomic_transaction
from core.topic_filter import canonicalize_topic_norm, deduplicate_note_topics
from parsers.obsidian_reader import read_obsidian_for_browser
from parsers.zim_reader import get_zim_article_html, get_zim_asset
from server.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["Notes & Readers"])

_IGNORED_VAULT_DIRS = {".obsidian", ".trash", ".git", "__pycache__", "node_modules"}


# ============================================================================
# 1. REQUEST DTOs
# ============================================================================

class QAMetricsRequest(BaseModel):
    original_text: str = ""
    parsed_text: str = ""


class OpenObsidianRequest(BaseModel):
    if _NOTE_CONFIG is not None:
        model_config = _NOTE_CONFIG
    else:

        class Config:
            extra = "allow"

    file_path: Optional[str] = None
    filePath: Optional[str] = None
    title: Optional[str] = None
    note_title: Optional[str] = None
    vault: Optional[str] = None
    anchor: Optional[str] = None

    def get_file_path(self) -> Optional[str]:
        return (self.file_path or self.filePath or "").strip() or None

    def get_title(self) -> Optional[str]:
        return (self.title or self.note_title or "").strip() or None


# ============================================================================
# 2. INTERNAL RESOLUTION HELPERS
# ============================================================================

def find_sumatra_executable() -> str:
    """Discovers SumatraPDF binary on Windows using PATH and standard installation paths (Invariant 6)."""
    for binary in ("SumatraPDF", "SumatraPDF.exe", "sumatrapdf", "sumatrapdf.exe"):
        found = shutil.which(binary)
        if found:
            return found

    appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

    candidates = [
        Path(appdata) / "SumatraPDF" / "SumatraPDF.exe" if appdata else None,
        Path(program_files) / "SumatraPDF" / "SumatraPDF.exe" if program_files else None,
        Path(program_files_x86) / "SumatraPDF" / "SumatraPDF.exe"
        if program_files_x86
        else None,
    ]

    for cand in candidates:
        if cand and cand.is_file():
            return str(cand)

    return "SumatraPDF.exe"


def _resolve_pdf_path(file_path: str) -> Optional[Path]:
    """Resolves a validated .pdf file path from direct path or enabled PDF knowledge sources (Invariant 6)."""
    if not file_path:
        return None

    clean_str = urllib.parse.unquote(file_path).strip().strip('"').strip("'")
    candidate = Path(clean_str)
    if candidate.is_file() and candidate.suffix.lower() == ".pdf":
        return candidate.resolve()

    try:
        from server.routes_sources import _resolve_pdf_path as sources_resolve

        resolved = sources_resolve(clean_str)
        if resolved and resolved.is_file() and resolved.suffix.lower() == ".pdf":
            return resolved
    except Exception:
        pass

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT path FROM knowledge_sources WHERE LOWER(type) = 'pdf' AND enabled = 1"
        )
        pdf_rows = cursor.fetchall()

    search_name = candidate.name.lower()
    search_stem = candidate.stem.lower()

    for row in pdf_rows:
        source_path = Path(row["path"])
        if not source_path.exists():
            continue
        if source_path.is_file() and source_path.suffix.lower() == ".pdf":
            if (
                source_path.name.lower() == search_name
                or source_path.stem.lower() == search_stem
            ):
                return source_path.resolve()
        elif source_path.is_dir():
            rel_cand = source_path / clean_str
            if rel_cand.is_file() and rel_cand.suffix.lower() == ".pdf":
                return rel_cand.resolve()
            direct_child = source_path / candidate.name
            if direct_child.is_file() and direct_child.suffix.lower() == ".pdf":
                return direct_child.resolve()
            for p_file in source_path.rglob("*.pdf"):
                if (
                    p_file.name.lower() == search_name
                    or p_file.stem.lower() == search_stem
                ):
                    return p_file.resolve()

    return None


def _resolve_obsidian_note_path(
    file_path: Optional[str] = None,
    title: Optional[str] = None,
) -> Tuple[Optional[Path], Optional[str], Optional[Path]]:
    """
    Resolves an Obsidian note path, vault folder name, and vault root Path from file_path or title.
    Strips in-page wikilink anchors (e.g. 'Heart Failure#Diagnosis' -> 'Heart Failure'),
    URL unquotes inputs, prunes hidden directories (.obsidian, .trash), and enforces path traversal safety (Invariant 6).
    """
    clean_file = (
        urllib.parse.unquote(file_path).strip().strip('"').strip("'")
        if file_path
        else ""
    )
    clean_title = (
        urllib.parse.unquote(title).strip().strip('"').strip("'") if title else ""
    )

    if "#" in clean_file:
        clean_file = clean_file.split("#", 1)[0].strip()
    if "#" in clean_title:
        clean_title = clean_title.split("#", 1)[0].strip()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, path FROM knowledge_sources WHERE LOWER(type) = 'obsidian' AND enabled = 1"
        )
        vault_rows = cursor.fetchall()

    allowed_vaults: list[Tuple[Path, str]] = []
    for r in vault_rows:
        vp = Path(r["path"]).resolve()
        if vp.is_dir():
            allowed_vaults.append((vp, vp.name))
        elif vp.is_file() and vp.suffix.lower() == ".md":
            allowed_vaults.append((vp.parent, vp.parent.name))

    if VAULTS_DIR.is_dir():
        allowed_vaults.append((VAULTS_DIR.resolve(), VAULTS_DIR.name))

    # 1. Direct existing path check (strictly validated against .md suffix and allowed vault roots)
    if clean_file:
        direct_cand = Path(clean_file)
        if direct_cand.is_file() and direct_cand.suffix.lower() == ".md":
            resolved_direct = direct_cand.resolve()
            for v_root, v_folder_name in allowed_vaults:
                try:
                    if resolved_direct.is_relative_to(v_root):
                        return resolved_direct, v_folder_name, v_root
                except Exception:
                    pass

    target_stem = Path(clean_title or clean_file or "").stem.strip()
    if not target_stem:
        return None, None, None

    search_name_lower = f"{target_stem.lower()}.md"
    search_stem_lower = target_stem.lower()

    for v_dir, vault_folder_name in allowed_vaults:
        if not v_dir.is_dir():
            continue

        # Fast direct resolution inside vault root
        for candidate_rel in (
            clean_file,
            f"{clean_file}.md" if clean_file else "",
            f"{clean_title}.md" if clean_title else "",
        ):
            if not candidate_rel:
                continue
            try:
                cand = (v_dir / candidate_rel).resolve()
                if (
                    cand.is_file()
                    and cand.suffix.lower() == ".md"
                    and cand.is_relative_to(v_dir)
                ):
                    return cand, vault_folder_name, v_dir
            except Exception:
                pass

        # Pruned walk: skip hidden folders (.obsidian, .trash, .git)
        for root, dirs, files in os.walk(v_dir):
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in _IGNORED_VAULT_DIRS and not d.startswith(".")
            ]
            for f in files:
                if not f.lower().endswith(".md"):
                    continue
                f_lower = f.lower()
                if (
                    f_lower == search_name_lower
                    or Path(f).stem.lower() == search_stem_lower
                ):
                    found = (Path(root) / f).resolve()
                    try:
                        if found.is_file() and found.is_relative_to(v_dir):
                            return found, vault_folder_name, v_dir
                    except Exception:
                        pass

    return None, None, None


def safe_levenshtein_approximation(a: str, b: str) -> int:
    """Calculates Levenshtein edit distance via SequenceMatcher opcodes."""
    if not a:
        return len(b)
    if not b:
        return len(a)

    matcher = difflib.SequenceMatcher(None, a, b)
    distance = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            distance += max(i2 - i1, j2 - j1)
        elif tag == "insert":
            distance += j2 - j1
        elif tag == "delete":
            distance += i2 - i1
    return distance


# ============================================================================
# 3. ROUTE ENDPOINTS
# ============================================================================

@router.post("/qa_metrics")
@router.post("/qa-metrics")
async def calculate_qa_metrics(payload: QAMetricsRequest) -> Dict[str, Any]:
    """Computes character ratio, token ratio, and Levenshtein distance offloaded to a worker thread."""
    original = payload.original_text or ""
    parsed = payload.parsed_text or ""

    orig_chars = len(original)
    parsed_chars = len(parsed)
    char_ratio = (parsed_chars / orig_chars) if orig_chars > 0 else 0.0

    orig_tokens = len(original.split())
    parsed_tokens = len(parsed.split())
    token_ratio = (parsed_tokens / orig_tokens) if orig_tokens > 0 else 0.0

    lev_dist = await asyncio.to_thread(
        safe_levenshtein_approximation, original, parsed
    )

    return {
        "status": "success",
        "char_ratio": round(char_ratio, 4),
        "token_ratio": round(token_ratio, 4),
        "levenshtein_distance": int(lev_dist),
    }


@router.get("/launch-sumatra")
@router.get("/launch_sumatra")
def launch_sumatra(
    file_path: str,
    page: Optional[int] = None,
    page_number: Optional[int] = None,
) -> Dict[str, str]:
    """
    Launches SumatraPDF directly at the cited page number adhering to Invariant 6.
    Accepts both page and page_number query parameters and validates .pdf extension.
    """
    if sys.platform != "win32":
        return {
            "status": "error",
            "message": "PDF deep-linking via SumatraPDF is only supported on Windows.",
        }

    target_page = max(1, int(page_number or page or 1))
    sumatra_exe = find_sumatra_executable()

    try:
        resolved_path = _resolve_pdf_path(file_path)
        if resolved_path is None and file_path:
            cand = Path(urllib.parse.unquote(file_path).strip())
            if cand.is_file():
                resolved_path = cand.resolve()

        if resolved_path is None or not resolved_path.is_file():
            return {
                "status": "error",
                "message": f"PDF file not found: {file_path}",
            }
        if resolved_path.suffix.lower() != ".pdf":
            return {
                "status": "error",
                "message": f"Invalid file extension (expected .pdf): {resolved_path.name}",
            }

        subprocess.Popen(
            [
                sumatra_exe,
                "-reuse-instance",
                "-page",
                str(target_page),
                str(resolved_path),
            ],
            shell=False,
        )
        return {"status": "success", "file_path": str(resolved_path), "page": str(target_page)}
    except Exception as exc:
        logger.error("Failed launching SumatraPDF: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/obsidian")
@router.get("/obsidian-note")
def get_obsidian_note(
    file_path: Optional[str] = None,
    title: Optional[str] = None,
    anchor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns an Obsidian note formatted for in-browser rendering.
    Supports both canonical /api/notes/obsidian and legacy /api/notes/obsidian-note paths.
    """
    if not file_path and not title:
        return {
            "status": "error",
            "message": "Missing file_path or title query parameter.",
        }

    resolved, _, _ = _resolve_obsidian_note_path(
        file_path=file_path, title=title
    )
    if not resolved or not resolved.is_file():
        return {
            "status": "error",
            "message": f"Obsidian note not found: {title or file_path}",
        }

    res = read_obsidian_for_browser(str(resolved))
    if anchor and isinstance(res, dict):
        res["anchor"] = anchor
    return res


@router.post("/obsidian-native")
@router.post("/open-obsidian")
def open_obsidian_native(req: OpenObsidianRequest) -> Dict[str, Any]:
    """
    Opens the target note inside the native Obsidian desktop application via obsidian:// URI
    adhering strictly to Invariant 6 (obsidian://open?vault=...&file=...).
    """
    req_file = req.get_file_path()
    req_title = req.get_title()

    anchor = (req.anchor or "").strip()
    if not anchor:
        if req_title and "#" in req_title:
            anchor = req_title.split("#", 1)[1].strip()
        elif req_file and "#" in req_file:
            anchor = req_file.split("#", 1)[1].strip()

    resolved, detected_vault, vault_root = _resolve_obsidian_note_path(
        file_path=req_file, title=req_title
    )
    vault_name = req.vault or detected_vault or "Primary"

    if resolved and resolved.is_file():
        if vault_root and resolved.is_relative_to(vault_root):
            rel_file = resolved.relative_to(vault_root).as_posix()
        else:
            rel_file = resolved.name
        uri = f"obsidian://open?vault={urllib.parse.quote(vault_name)}&file={urllib.parse.quote(rel_file)}"
    else:
        file_target = Path(req_title or req_file or "").stem.strip()
        uri = f"obsidian://open?vault={urllib.parse.quote(vault_name)}&file={urllib.parse.quote(file_target)}"

    if anchor:
        uri += f"&heading={urllib.parse.quote(anchor)}"

    launch_error = None
    try:
        if sys.platform == "win32" and hasattr(os, "startfile"):
            os.startfile(uri)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", uri], shell=False)
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", uri], shell=False)
    except Exception as exc:
        launch_error = str(exc)
        logger.warning("Could not open Obsidian URI %s: %s", uri, exc)

    return {
        "status": "success" if not launch_error else "warning",
        "uri": uri,
        "resolved_path": str(resolved) if resolved else "",
        "vault": vault_name,
        "error": launch_error,
    }


@router.get("/zim")
@router.get("/zim-article")
@router.get("/zim_article")
def zim_article(
    article_id: Optional[str] = None,
    title: Optional[str] = None,
    zim_path: Optional[str] = None,
    file_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extracts and cleans HTML from an enabled >10GB ZIM archive for in-browser viewing,
    automatically following ZIM redirects and rewriting internal links.
    """
    target = (article_id or title or "").strip()
    if not target:
        raise HTTPException(
            status_code=400, detail="Missing article_id or title query parameter."
        )

    effective_zim_path = zim_path or file_path
    try:
        return get_zim_article_html(target, zim_path=effective_zim_path)
    except Exception as exc:
        logger.error("Failed extracting ZIM article '%s': %s", target, exc)
        return {"status": "error", "message": str(exc), "html": ""}


@router.get("/zim-asset")
@router.get("/zim_asset")
def zim_asset(
    path: str,
    zim_path: Optional[str] = None,
    file_path: Optional[str] = None,
) -> Response:
    """
    Serves images, illustrations, and media assets extracted from a local ZIM archive.
    Polymorphically extracts content and mimetype across dicts, tuples, and ZimAsset polyfills (Invariant 11).
    """
    clean_path = urllib.parse.unquote(path).strip()
    effective_zim_path = zim_path or file_path or ""
    asset = get_zim_asset(clean_path, zim_path=effective_zim_path)

    if not asset:
        raise HTTPException(
            status_code=404, detail=f"ZIM asset '{clean_path}' not found"
        )

    content: bytes = b""
    media_type: str = "application/octet-stream"

    # Polymorphic unpacking satisfying Invariant 11
    if isinstance(asset, dict):
        content = asset.get("content", b"")
        media_type = asset.get("mimetype", "application/octet-stream")
    elif hasattr(asset, "content") and hasattr(asset, "mimetype"):
        content = asset.content
        media_type = asset.mimetype
    elif isinstance(asset, (tuple, list)) and len(asset) >= 2:
        content = asset[0]
        media_type = asset[1]
    else:
        try:
            content = asset["content"]
            media_type = asset["mimetype"]
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Invalid ZIM asset structure returned by reader.",
            )

    return Response(
        content=content,
        media_type=str(media_type),
        headers={"Cache-Control": "public, max-age=86400"},
    )

# --- Phase 6 Asynchronous Note Index Synchronization Hook ---
from typing import Any as _Any, Callable as _Callable


def build_note_index_document(note_id: str, title: str, markdown_body: str, tags: list[str] | None = None) -> dict[str, _Any]:
    """Construct a normalized index document whenever a synthesized study note is saved."""
    return {
        "id": f"note:{note_id}",
        "source_type": "note",
        "title": (title or "Untitled Note").strip(),
        "content": (markdown_body or "").strip(),
        "tags": list(tags or []),
    }


# ============================================================================
# 4. MY NOTES - LECTURE TOPIC PAD & CARD CRUD ENDPOINTS
# ============================================================================

import json
import re
import time
import uuid

def _safe_search_index(clean_topic: str, limit_per_source: int = 4):
    try:
        from parsers.tantivy_engine import search_index
        return search_index(clean_topic, limit_per_source=limit_per_source)
    except Exception as exc:
        logger.warning("search_index fallback on '%s': %s", clean_topic, exc)
        return {"obsidian": [], "pdf": [], "zim": []}

def _safe_lookup_pdf_topic_pages(clean_topic: str):
    try:
        from parsers.pdf_topic_indexer import lookup_pdf_topic_pages
        return lookup_pdf_topic_pages(clean_topic)
    except Exception:
        return []


from agents.tema_q.topic_extractor import extract_topics_local, get_ignored_topics_set, normalize_topic_key

_WIKILINK_TOPIC_RE = re.compile(r"(?<!!)\[\[(?:([^|\]]+)\|)?([^\]]+)\]\]")

_NOTE_MINING_STATE: dict[str, dict] = {}


def extract_topics_from_note_markdown(markdown_text: str, suppressed_keys: Optional[set[str]] = None) -> list[str]:
    """Extracts guaranteed topics from [[Wikilinks]] and automated medical entities from prose, adhering to the global ignore list and suppressed keys."""
    if not markdown_text or not markdown_text.strip():
        return []

    ignored_set = get_ignored_topics_set()
    suppressed = suppressed_keys or set()

    seen: set[str] = set()
    seen_texts: set[str] = set()
    ordered_topics: list[str] = []

    def _add(term: str) -> None:
        clean = (term or "").strip()
        if not clean or len(clean) < 2:
            return
        key = normalize_topic_key(clean)
        clean_lower = clean.lower()
        if not key or key in ignored_set or key in suppressed or clean_lower in suppressed:
            return
        if key not in seen and clean_lower not in seen_texts:
            seen.add(key)
            seen_texts.add(clean_lower)
            ordered_topics.append(clean)

    # 1. Explicit Obsidian [[Wikilink]] or [[Target|Alias]] notations
    for match in _WIKILINK_TOPIC_RE.finditer(markdown_text):
        target = (match.group(1) or match.group(2) or "").strip()
        if "#" in target:
            target = target.split("#", 1)[0].strip()
        if target:
            _add(target)

    # 2. Automated clinical NLP extraction via MedSpaCy / SciSpaCy local extractor
    try:
        extracted = extract_topics_local(question=markdown_text)
        for topic_name in extracted.get("all_topics", []):
            _add(topic_name)
    except Exception as exc:
        logger.warning("Clinical NLP extraction error from note markdown: %s", exc)

    # Final pass deduplication preserving first casing
    deduped: list[str] = []
    final_seen_keys: set[str] = set()
    for t in ordered_topics:
        k = normalize_topic_key(t)
        if k and k not in final_seen_keys:
            final_seen_keys.add(k)
            deduped.append(t)

    return deduped


class LectureNoteMetaPayload(BaseModel):
    if _NOTE_CONFIG is not None:
        model_config = _NOTE_CONFIG
    else:
        class Config:
            extra = "allow"

    note_id: Optional[str] = None
    title: str = "Untitled Note"
    shade_index: int = 0
    is_pinned: int = 0
    note_mode: str = "list"
    content_markdown: str = ""
    card_color: str = ""
    items: Optional[list] = None


class LectureNoteItemPayload(BaseModel):
    item_id: Optional[str] = None
    line_order: int = 0
    topic_text: str
    force_refresh: bool = False


def _build_safe_fts5_query(raw_topic: str) -> str:
    """Sanitizes clinical topic strings for SQLite FTS5 MATCH without syntax errors."""
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", (raw_topic or "").strip()) if len(t) >= 2]
    if not tokens:
        return ""
    # Phrase or AND match across sanitized tokens
    return " AND ".join(f'"{tok}"*' for tok in tokens[:5])


def _count_matching_mcqs_sync(conn, topic_text: str) -> int:
    """Fast sub-millisecond MCQ count across csv_questions_fts and topic_name."""
    clean = (topic_text or "").strip()
    if not clean:
        return 0
    cursor = conn.cursor()
    fts_query = _build_safe_fts5_query(clean)
    like_pat = f"%{clean}%"

    try:
        if fts_query:
            cursor.execute(
                """
                SELECT COUNT(DISTINCT cq.id) AS cnt
                FROM csv_questions cq
                LEFT JOIN csv_questions_fts fts ON fts.rowid = cq.rowid
                WHERE csv_questions_fts MATCH ?
                   OR LOWER(cq.topic_name) LIKE LOWER(?)
                """,
                (fts_query, like_pat),
            )
            row = cursor.fetchone()
            if row and row["cnt"] is not None:
                return int(row["cnt"])
    except Exception as exc:
        logger.debug("FTS5 count fallback for '%s': %s", clean, exc)

    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM csv_questions
        WHERE LOWER(topic_name) LIKE LOWER(?)
           OR LOWER(question) LIKE LOWER(?)
           OR LOWER(opt_a) LIKE LOWER(?)
           OR LOWER(opt_b) LIKE LOWER(?)
           OR LOWER(opt_c) LIKE LOWER(?)
           OR LOWER(opt_d) LIKE LOWER(?)
        """,
        (like_pat, like_pat, like_pat, like_pat, like_pat, like_pat),
    )
    row = cursor.fetchone()
    return int(row["cnt"] or 0) if row else 0


def _resolve_topic_refs_and_mcqs(topic_text: str, force_refresh: bool = False, conn: Optional[Any] = None) -> Dict[str, Any]:
    """
    Resolves references (Obsidian, PDF, ZIM) and MCQ counts for a topic line.
    Checks cached_syntheses first (<1ms), falling back to search_index + pdf_topic_index.
    """
    clean_topic = (topic_text or "").strip()
    topic_key = re.sub(r"[\s_]+", "-", clean_topic.lower())
    cached_refs: Optional[Dict[str, list]] = None

    if not force_refresh and topic_key:
        if conn is not None:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT top_references FROM cached_syntheses WHERE topic_key = ?",
                (topic_key,),
            )
            row = cursor.fetchone()
            if row and row["top_references"]:
                try:
                    parsed = json.loads(row["top_references"])
                    if isinstance(parsed, dict) and any(parsed.get(k) for k in ("obsidian", "pdf", "zim")):
                        cached_refs = parsed
                except Exception:
                    pass
        else:
            with closing(get_db_connection()) as db_conn:
                cursor = db_conn.cursor()
                cursor.execute(
                    "SELECT top_references FROM cached_syntheses WHERE topic_key = ?",
                    (topic_key,),
                )
                row = cursor.fetchone()
                if row and row["top_references"]:
                    try:
                        parsed = json.loads(row["top_references"])
                        if isinstance(parsed, dict) and any(parsed.get(k) for k in ("obsidian", "pdf", "zim")):
                            cached_refs = parsed
                    except Exception:
                        pass

    if cached_refs is None:
        search_res = _safe_search_index(clean_topic, limit_per_source=4)

        # Enrich PDF hits with exact hierarchical pdf_topic_index pages if available
        try:
            idx_hits = _safe_lookup_pdf_topic_pages(clean_topic)
            existing_pdf_keys = {
                (str(p.get("file_path") or "").lower(), int(p.get("page") or 1))
                for p in (search_res.get("pdf") or [])
            }
            for h in (idx_hits or [])[:3]:
                key = (str(h.get("pdf_path") or "").lower(), int(h.get("start_page") or 1))
                if key not in existing_pdf_keys and h.get("pdf_path"):
                    search_res.setdefault("pdf", []).insert(0, {
                        "source": "pdf",
                        "source_type": "pdf",
                        "title": h.get("pdf_title") or h.get("topic_title") or clean_topic,
                        "heading": h.get("heading_path") or h.get("topic_title") or "",
                        "file_path": h.get("pdf_path"),
                        "page": int(h.get("start_page") or 1),
                        "snippet": f"Section: {h.get('heading_path') or clean_topic} (Pages {h.get('start_page')}–{h.get('end_page')})",
                    })
                    existing_pdf_keys.add(key)
        except Exception:
            pass

        cached_refs = {
            "obsidian": (search_res.get("obsidian") or [])[:4],
            "pdf": (search_res.get("pdf") or [])[:4],
            "zim": (search_res.get("zim") or [])[:4],
        }

    obs_list = cached_refs.get("obsidian") or []
    pdf_list = cached_refs.get("pdf") or []
    zim_list = cached_refs.get("zim") or []

    if conn is not None:
        mcq_cnt = _count_matching_mcqs_sync(conn, clean_topic)
        if topic_key:
            conn.execute(
                """
                INSERT INTO cached_syntheses (
                    topic_key, display_title, enhanced_explanation, distractor_analysis,
                    unified_article, variance_audit, top_references,
                    has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                ) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, unixepoch('now'))
                ON CONFLICT(topic_key) DO UPDATE SET
                    top_references = excluded.top_references,
                    has_obsidian_note = excluded.has_obsidian_note,
                    has_pdf_match = excluded.has_pdf_match,
                    has_zim_match = excluded.has_zim_match,
                    updated_at = unixepoch('now')
                """,
                (
                    topic_key,
                    clean_topic,
                    json.dumps(cached_refs),
                    1 if obs_list else 0,
                    1 if pdf_list else 0,
                    1 if zim_list else 0,
                ),
            )
    else:
        with closing(get_db_connection()) as db_conn:
            mcq_cnt = _count_matching_mcqs_sync(db_conn, clean_topic)
            if topic_key:
                db_conn.execute(
                    """
                    INSERT INTO cached_syntheses (
                        topic_key, display_title, enhanced_explanation, distractor_analysis,
                        unified_article, variance_audit, top_references,
                        has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                    ) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, unixepoch('now'))
                    ON CONFLICT(topic_key) DO UPDATE SET
                        top_references = excluded.top_references,
                        has_obsidian_note = excluded.has_obsidian_note,
                        has_pdf_match = excluded.has_pdf_match,
                        has_zim_match = excluded.has_zim_match,
                        updated_at = unixepoch('now')
                    """,
                    (
                        topic_key,
                        clean_topic,
                        json.dumps(cached_refs),
                        1 if obs_list else 0,
                        1 if pdf_list else 0,
                        1 if zim_list else 0,
                    ),
                )

    return {
        "topic_key": topic_key,
        "mcq_count": mcq_cnt,
        "obsidian_count": len(obs_list),
        "pdf_count": len(pdf_list),
        "zim_count": len(zim_list),
        "ref_count": len(obs_list) + len(pdf_list) + len(zim_list),
        "references": cached_refs,
    }


def _get_lecture_notes_sync(page: int = 1, page_size: int = 12, q: Any = "") -> Dict[str, Any]:
    try:
        page = int(page)
    except Exception:
        page = 1
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 12
    offset = max(0, (page - 1) * page_size)
    clean_q = (str(q) if isinstance(q, str) else "").strip()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        if clean_q:
            like_pat = f"%{clean_q}%"
            count_query = """
                SELECT COUNT(DISTINCT n.note_id) AS cnt
                FROM user_lecture_notes n
                LEFT JOIN user_lecture_note_items i ON i.note_id = n.note_id
                WHERE n.title LIKE ? OR i.topic_text LIKE ?
            """
            cursor.execute(count_query, (like_pat, like_pat))
            total_items = int(cursor.fetchone()["cnt"] or 0)

            query = """
                SELECT DISTINCT n.note_id, n.title, n.shade_index, n.is_pinned,
                                n.note_mode, n.content_markdown, n.card_color,
                                n.created_at, n.updated_at
                FROM user_lecture_notes n
                LEFT JOIN user_lecture_note_items i ON i.note_id = n.note_id
                WHERE n.title LIKE ? OR i.topic_text LIKE ? OR n.content_markdown LIKE ?
                ORDER BY n.is_pinned DESC, n.updated_at DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(query, (like_pat, like_pat, like_pat, page_size, offset))
        else:
            cursor.execute("SELECT COUNT(*) AS cnt FROM user_lecture_notes")
            total_items = int(cursor.fetchone()["cnt"] or 0)

            query = """
                SELECT note_id, title, shade_index, is_pinned,
                       note_mode, content_markdown, card_color,
                       created_at, updated_at
                FROM user_lecture_notes
                ORDER BY is_pinned DESC, updated_at DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(query, (page_size, offset))

        note_rows = cursor.fetchall()
        notes = []

        for nr in note_rows:
            nid = nr["note_id"]
            cursor.execute(
                """
                SELECT item_id, line_order, topic_text, topic_key, mcq_count, ref_count,
                       obsidian_count, pdf_count, zim_count
                FROM user_lecture_note_items
                WHERE note_id = ?
                ORDER BY line_order ASC
                LIMIT 6
                """,
                (nid,),
            )
            top_items = [dict(r) for r in cursor.fetchall()]

            cursor.execute(
                """
                SELECT COUNT(*) as total_topics,
                       COALESCE(SUM(mcq_count), 0) as total_mcqs,
                       COALESCE(SUM(ref_count), 0) as total_refs
                FROM user_lecture_note_items
                WHERE note_id = ?
                """,
                (nid,),
            )
            stats = cursor.fetchone()

            notes.append({
                "note_id": nid,
                "title": nr["title"],
                "shade_index": nr["shade_index"] or 0,
                "is_pinned": bool(nr["is_pinned"]),
                "note_mode": nr["note_mode"] or "list",
                "content_markdown": nr["content_markdown"] or "",
                "card_color": nr["card_color"] or "",
                "created_at": nr["created_at"],
                "updated_at": nr["updated_at"],
                "top_items": top_items,
                "total_topics": int(stats["total_topics"] or 0),
                "total_mcqs": int(stats["total_mcqs"] or 0),
                "total_refs": int(stats["total_refs"] or 0),
            })

    total_pages = max(1, (total_items + page_size - 1) // page_size) if total_items > 0 else 1
    return {
        "status": "success",
        "notes": notes,
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
    }


def _mine_and_populate_note_items_sync(note_id: str) -> None:
    """Runs in a worker thread via asyncio.to_thread. Extracts, deduplicates, and populates items."""
    _NOTE_MINING_STATE[note_id] = {"status": "populating", "updated_at": time.time()}
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content_markdown, suppressed_topics_json FROM user_lecture_notes WHERE note_id = ?",
                (note_id,),
            )
            row = cursor.fetchone()
            if not row:
                _NOTE_MINING_STATE[note_id] = {"status": "ready", "updated_at": time.time()}
                return

            content_md = str(row["content_markdown"] or "")
            try:
                suppressed_list = json.loads(row["suppressed_topics_json"] or "[]")
            except Exception:
                suppressed_list = []

        if not content_md.strip():
            with closing(get_db_connection()) as conn:
                conn.execute("DELETE FROM user_lecture_note_items WHERE note_id = ?", (note_id,))
            _NOTE_MINING_STATE[note_id] = {"status": "ready", "updated_at": time.time()}
            try:
                ws_manager.broadcast_sync({"type": "note_topics_ready", "note_id": note_id})
            except Exception:
                pass
            return

        ignored_set = get_ignored_topics_set()
        suppressed_set = {canonicalize_topic_norm(s) for s in suppressed_list if s}

        wikilink_candidates: list[str] = []
        for match in _WIKILINK_TOPIC_RE.finditer(content_md):
            target = (match.group(1) or match.group(2) or "").strip()
            if "#" in target:
                target = target.split("#", 1)[0].strip()
            if target:
                wikilink_candidates.append(target)

        try:
            extracted = extract_topics_local(question=content_md)
            raw_details = extracted.get("topic_details") or extracted.get("all_topics") or []
        except Exception as exc:
            logger.warning("Error running extract_topics_local in background: %s", exc)
            raw_details = []

        combined_candidates = list(wikilink_candidates) + list(raw_details)
        deduped_topics = deduplicate_note_topics(combined_candidates, ignored_set, suppressed_set)

        with closing(get_db_connection()) as conn:
            with atomic_transaction(conn) as cursor:
                cursor.execute(
                    """
                    SELECT topic_key, item_id, mcq_count, ref_count, obsidian_count, pdf_count, zim_count, references_json
                    FROM user_lecture_note_items WHERE note_id = ?
                    """,
                    (note_id,),
                )
                existing_map = {
                    canonicalize_topic_norm(r["topic_key"]): dict(r) for r in cursor.fetchall()
                }

                cursor.execute("DELETE FROM user_lecture_note_items WHERE note_id = ?", (note_id,))
                for idx, t_item in enumerate(deduped_topics):
                    t_norm = t_item["topic_norm"]
                    t_key = t_item["topic_key"]
                    t_text = t_item["topic_text"]
                    prev = existing_map.get(t_norm)
                    if prev:
                        cursor.execute(
                            """
                            INSERT INTO user_lecture_note_items (
                                item_id, note_id, line_order, topic_text, topic_key,
                                mcq_count, ref_count, obsidian_count, pdf_count, zim_count,
                                references_json, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                            """,
                            (
                                prev["item_id"], note_id, idx, t_text, t_key,
                                prev["mcq_count"], prev["ref_count"], prev["obsidian_count"],
                                prev["pdf_count"], prev["zim_count"], prev["references_json"],
                            ),
                        )
                    else:
                        resolved = _resolve_topic_refs_and_mcqs(t_text, force_refresh=False, conn=conn)
                        cursor.execute(
                            """
                            INSERT INTO user_lecture_note_items (
                                item_id, note_id, line_order, topic_text, topic_key,
                                mcq_count, ref_count, obsidian_count, pdf_count, zim_count,
                                references_json, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                            """,
                            (
                                f"item_{uuid.uuid4().hex[:12]}", note_id, idx, t_text, t_key,
                                resolved["mcq_count"], resolved["ref_count"], resolved["obsidian_count"],
                                resolved["pdf_count"], resolved["zim_count"], json.dumps(resolved["references"]),
                            ),
                        )

        _NOTE_MINING_STATE[note_id] = {"status": "ready", "updated_at": time.time()}
        try:
            ws_manager.broadcast_sync({"type": "note_topics_ready", "note_id": note_id})
        except Exception as exc:
            logger.debug("Failed broadcasting note_topics_ready: %s", exc)
    except Exception as exc:
        logger.error("Background note mining failed for %s: %s", note_id, exc)
        _NOTE_MINING_STATE[note_id] = {"status": "error", "error": str(exc), "updated_at": time.time()}


def _get_single_lecture_note_sync(note_id: str) -> Optional[Dict[str, Any]]:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT note_id, title, shade_index, is_pinned,
                   note_mode, content_markdown, card_color,
                   suppressed_topics_json,
                   created_at, updated_at
            FROM user_lecture_notes
            WHERE note_id = ?
            """,
            (note_id,),
        )
        note_row = cursor.fetchone()
        if not note_row:
            return None

        note_dict = dict(note_row)
        try:
            note_dict["suppressed_topics"] = json.loads(note_dict.get("suppressed_topics_json") or "[]")
        except Exception:
            note_dict["suppressed_topics"] = []

        cursor.execute(
            """
            SELECT item_id, note_id, line_order, topic_text, topic_key,
                   mcq_count, ref_count, obsidian_count, pdf_count, zim_count,
                   references_json, updated_at
            FROM user_lecture_note_items
            WHERE note_id = ?
            ORDER BY line_order ASC
            """,
            (note_id,),
        )
        items = []
        for r in cursor.fetchall():
            item_dict = dict(r)
            try:
                item_dict["references"] = json.loads(item_dict.get("references_json") or "{}")
            except Exception:
                item_dict["references"] = {"obsidian": [], "pdf": [], "zim": []}
            items.append(item_dict)

        mining_info = _NOTE_MINING_STATE.get(note_id, {})
        mining_status = mining_info.get("status", "ready")

        resp: Dict[str, Any] = {
            "status": "success",
            "note": note_dict,
            "items": items,
            "mining_status": mining_status,
        }
        if mining_status == "populating":
            resp["message"] = "Populating... Please wait"
        return resp


def _extract_and_sync_note_topics_sync(note_id: str, markdown_override: Optional[str] = None) -> Dict[str, Any]:
    """Extracts topics from note markdown and merges them into user_lecture_note_items, adhering to the ignore list and note-suppressed topics."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT note_id, title, shade_index, is_pinned, note_mode, content_markdown, card_color, suppressed_topics_json, created_at, updated_at FROM user_lecture_notes WHERE note_id = ?",
            (note_id,),
        )
        note_row = cursor.fetchone()
        if not note_row:
            raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found.")

        md_text = markdown_override if markdown_override is not None else (note_row["content_markdown"] or "")
        suppressed_raw = note_row["suppressed_topics_json"] if "suppressed_topics_json" in note_row.keys() else "[]"
        try:
            suppressed_list = json.loads(suppressed_raw or "[]")
        except Exception:
            suppressed_list = []
        suppressed_keys = {str(k).strip().lower() for k in suppressed_list if str(k).strip()}

        cursor.execute(
            "SELECT item_id, line_order, topic_text, topic_key FROM user_lecture_note_items WHERE note_id = ? ORDER BY line_order ASC",
            (note_id,),
        )
        existing_items = [dict(r) for r in cursor.fetchall()]

    from agents.tema_q.topic_extractor import get_ignored_topics_set, normalize_topic_key
    ignored_set = get_ignored_topics_set()

    extracted_names = extract_topics_from_note_markdown(md_text, suppressed_keys=suppressed_keys)

    existing_keys = {r["topic_key"]: r for r in existing_items}
    existing_clean_text = {r["topic_text"].strip().lower() for r in existing_items}

    # Add newly extracted topics
    next_order = len(existing_items)
    for term in extracted_names:
        clean_t = term.strip()
        t_key = normalize_topic_key(clean_t)
        if (
            t_key
            and t_key not in existing_keys
            and clean_t.lower() not in existing_clean_text
            and t_key not in ignored_set
            and t_key not in suppressed_keys
            and clean_t.lower() not in suppressed_keys
        ):
            existing_keys[t_key] = True
            existing_clean_text.add(clean_t.lower())
            _upsert_lecture_note_item_sync(note_id, LectureNoteItemPayload(
                line_order=next_order,
                topic_text=clean_t,
                force_refresh=False,
            ))
            next_order += 1

    single = _get_single_lecture_note_sync(note_id)
    return single or {"status": "success", "note": dict(note_row), "items": []}


def _upsert_lecture_note_sync(payload: LectureNoteMetaPayload, background_mining: bool = False) -> Dict[str, Any]:
    note_id = (payload.note_id or "").strip() or f"note_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    title = (payload.title or "").strip() or "Untitled Note"
    mode = (payload.note_mode or "list").strip().lower()
    if mode not in ("notes", "list"):
        mode = "list"
    content_md = payload.content_markdown if payload.content_markdown is not None else ""
    color = (payload.card_color or "").strip()

    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT INTO user_lecture_notes (
                note_id, title, shade_index, is_pinned, note_mode,
                content_markdown, card_color, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, unixepoch('now'), unixepoch('now'))
            ON CONFLICT(note_id) DO UPDATE SET
                title = excluded.title,
                shade_index = excluded.shade_index,
                is_pinned = excluded.is_pinned,
                note_mode = excluded.note_mode,
                content_markdown = excluded.content_markdown,
                card_color = excluded.card_color,
                updated_at = unixepoch('now')
            """,
            (note_id, title, payload.shade_index, payload.is_pinned, mode, content_md, color),
        )

        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT note_id, title, shade_index, is_pinned, note_mode,
                   content_markdown, card_color, created_at, updated_at
            FROM user_lecture_notes WHERE note_id = ?
            """,
            (note_id,),
        )
        saved = cursor.fetchone()

    # If items are provided in payload, sync them if needed
    if payload.items is not None and isinstance(payload.items, list):
        for idx, it in enumerate(payload.items):
            if isinstance(it, dict) and it.get("topic_text"):
                _upsert_lecture_note_item_sync(note_id, LectureNoteItemPayload(
                    item_id=it.get("item_id"),
                    line_order=idx,
                    topic_text=it.get("topic_text"),
                ))
    elif content_md.strip() and not background_mining:
        # Synchronous fallback if background mining is not requested
        _extract_and_sync_note_topics_sync(note_id, markdown_override=content_md)

    single = _get_single_lecture_note_sync(note_id)
    resp = single or {"status": "success", "note": dict(saved), "items": []}
    if background_mining:
        resp["mining_status"] = "populating"
        resp["message"] = "Populating... Please wait"
    return resp


def _delete_lecture_note_sync(note_id: str) -> bool:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_lecture_note_items WHERE note_id = ?", (note_id,))
        cursor.execute("DELETE FROM user_lecture_notes WHERE note_id = ?", (note_id,))
        return cursor.rowcount > 0


def _upsert_lecture_note_item_sync(note_id: str, payload: LectureNoteItemPayload) -> Dict[str, Any]:
    item_id = (payload.item_id or "").strip() or f"item_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    topic_text = (payload.topic_text or "").strip()

    # Resolve references and matching MCQs
    resolved = _resolve_topic_refs_and_mcqs(topic_text, force_refresh=payload.force_refresh)
    topic_key = resolved["topic_key"]
    refs_json = json.dumps(resolved["references"])

    with closing(get_db_connection()) as conn:
        # Verify or ensure parent note exists
        cursor = conn.cursor()
        cursor.execute("SELECT note_id FROM user_lecture_notes WHERE note_id = ?", (note_id,))
        if not cursor.fetchone():
            conn.execute(
                """
                INSERT INTO user_lecture_notes (note_id, title, shade_index, is_pinned, created_at, updated_at)
                VALUES (?, 'Untitled Note', 0, 0, unixepoch('now'), unixepoch('now'))
                """,
                (note_id,),
            )

        conn.execute(
            """
            INSERT INTO user_lecture_note_items (
                item_id, note_id, line_order, topic_text, topic_key,
                mcq_count, ref_count, obsidian_count, pdf_count, zim_count,
                references_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
            ON CONFLICT(item_id) DO UPDATE SET
                line_order = excluded.line_order,
                topic_text = excluded.topic_text,
                topic_key = excluded.topic_key,
                mcq_count = excluded.mcq_count,
                ref_count = excluded.ref_count,
                obsidian_count = excluded.obsidian_count,
                pdf_count = excluded.pdf_count,
                zim_count = excluded.zim_count,
                references_json = excluded.references_json,
                updated_at = unixepoch('now')
            """,
            (
                item_id,
                note_id,
                payload.line_order,
                topic_text,
                topic_key,
                resolved["mcq_count"],
                resolved["ref_count"],
                resolved["obsidian_count"],
                resolved["pdf_count"],
                resolved["zim_count"],
                refs_json,
            ),
        )
        conn.execute(
            "UPDATE user_lecture_notes SET updated_at = unixepoch('now') WHERE note_id = ?",
            (note_id,),
        )

    return {
        "status": "success",
        "item_id": item_id,
        "note_id": note_id,
        "line_order": payload.line_order,
        "topic_text": topic_text,
        "topic_key": topic_key,
        "mcq_count": resolved["mcq_count"],
        "ref_count": resolved["ref_count"],
        "obsidian_count": resolved["obsidian_count"],
        "pdf_count": resolved["pdf_count"],
        "zim_count": resolved["zim_count"],
        "references": resolved["references"],
    }


def _delete_lecture_note_item_sync(
    note_id: str,
    item_id: Optional[str] = None,
    topic_key: Optional[str] = None,
    topic_text: Optional[str] = None,
    global_ignore: bool = False,
) -> bool:
    clean_item_id = str(item_id) if item_id and not hasattr(item_id, "default") else None
    clean_topic_key = str(topic_key) if topic_key and not hasattr(topic_key, "default") else None
    clean_topic_text = str(topic_text).strip() if topic_text and not hasattr(topic_text, "default") else None
    clean_global_ignore = bool(global_ignore) if not hasattr(global_ignore, "default") else False

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        conditions = ["note_id = ?"]
        params: list[Any] = [note_id]
        sub_conds = []
        if clean_item_id:
            sub_conds.append("item_id = ?")
            params.append(clean_item_id)
        if clean_topic_key:
            sub_conds.append("topic_key = ?")
            params.append(clean_topic_key)
        if clean_topic_text:
            sub_conds.append("LOWER(topic_text) = LOWER(?)")
            params.append(clean_topic_text)

        if not sub_conds:
            return False

        conditions.append(f"({' OR '.join(sub_conds)})")
        where_clause = " AND ".join(conditions)

        cursor.execute(
            f"SELECT item_id, topic_text, topic_key FROM user_lecture_note_items WHERE {where_clause}",
            tuple(params),
        )
        matching_rows = [dict(r) for r in cursor.fetchall()]

        cursor.execute(
            f"DELETE FROM user_lecture_note_items WHERE {where_clause}",
            tuple(params),
        )
        deleted = cursor.rowcount > 0

        cursor.execute(
            "SELECT suppressed_topics_json FROM user_lecture_notes WHERE note_id = ?",
            (note_id,),
        )
        note_row = cursor.fetchone()
        existing_suppressed: list = []
        if note_row and note_row["suppressed_topics_json"]:
            try:
                existing_suppressed = json.loads(note_row["suppressed_topics_json"])
            except Exception:
                existing_suppressed = []

        suppressed_changed = False
        terms_to_globally_ignore: list[str] = []

        for it in matching_rows:
            t_key = it["topic_key"]
            t_text = (it["topic_text"] or "").strip()
            t_norm = canonicalize_topic_norm(t_text)
            for k in (t_key, t_text.lower(), t_norm):
                if k and k not in existing_suppressed:
                    existing_suppressed.append(k)
                    suppressed_changed = True
            if t_text:
                terms_to_globally_ignore.append(t_text)

        if clean_topic_text or clean_topic_key:
            for cand in (clean_topic_key, clean_topic_text.lower() if clean_topic_text else None, canonicalize_topic_norm(clean_topic_text) if clean_topic_text else None):
                if cand and cand not in existing_suppressed:
                    existing_suppressed.append(cand)
                    suppressed_changed = True
            if clean_topic_text:
                terms_to_globally_ignore.append(clean_topic_text)

        if suppressed_changed:
            conn.execute(
                "UPDATE user_lecture_notes SET suppressed_topics_json = ?, updated_at = unixepoch('now') WHERE note_id = ?",
                (json.dumps(existing_suppressed), note_id),
            )
        elif deleted:
            conn.execute(
                "UPDATE user_lecture_notes SET updated_at = unixepoch('now') WHERE note_id = ?",
                (note_id,),
            )

        if clean_global_ignore and terms_to_globally_ignore:
            from agents.tema_q.topic_extractor import add_ignored_topic
            for term in terms_to_globally_ignore:
                add_ignored_topic(term)

        return deleted


# Standard REST endpoints and alias /lecture-notes routes

@router.get("")
@router.get("/")
@router.get("/lecture-notes")
async def get_lecture_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    q: str = Query(""),
) -> Dict[str, Any]:
    """Returns paginated note cards with item previews, topic counts, matched MCQs, and references."""
    return await asyncio.to_thread(_get_lecture_notes_sync, page=page, page_size=page_size, q=q)


@router.get("/{note_id}")
@router.get("/lecture-notes/{note_id}")
async def get_single_lecture_note(note_id: str) -> Dict[str, Any]:
    """Returns the full note metadata and all ordered locked topic lines."""
    result = await asyncio.to_thread(_get_single_lecture_note_sync, note_id=note_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Lecture note '{note_id}' not found.")
    return result


@router.post("")
@router.post("/")
@router.post("/lecture-notes")
async def upsert_lecture_note(payload: LectureNoteMetaPayload, background_tasks: BackgroundTasks = None) -> Dict[str, Any]:
    """Creates or updates a note's metadata and items immediately, offloading mining to background."""
    should_mine = bool(payload.content_markdown and payload.content_markdown.strip() and not payload.items)
    result = await asyncio.to_thread(_upsert_lecture_note_sync, payload=payload, background_mining=should_mine)
    note_id = result.get("note", {}).get("note_id")
    if should_mine and note_id:
        _NOTE_MINING_STATE[note_id] = {"status": "populating", "updated_at": time.time()}
        if background_tasks is not None:
            background_tasks.add_task(asyncio.to_thread, _mine_and_populate_note_items_sync, note_id)
        else:
            asyncio.create_task(asyncio.to_thread(_mine_and_populate_note_items_sync, note_id))
    return result


@router.put("/{note_id}")
@router.put("/lecture-notes/{note_id}")
async def update_lecture_note(note_id: str, payload: LectureNoteMetaPayload, background_tasks: BackgroundTasks = None) -> Dict[str, Any]:
    """Updates / auto-saves a note's metadata and markdown content immediately, offloading mining to background."""
    payload.note_id = note_id
    should_mine = bool(payload.content_markdown and payload.content_markdown.strip() and not payload.items)
    result = await asyncio.to_thread(_upsert_lecture_note_sync, payload=payload, background_mining=should_mine)
    if should_mine and note_id:
        _NOTE_MINING_STATE[note_id] = {"status": "populating", "updated_at": time.time()}
        if background_tasks is not None:
            background_tasks.add_task(asyncio.to_thread, _mine_and_populate_note_items_sync, note_id)
        else:
            asyncio.create_task(asyncio.to_thread(_mine_and_populate_note_items_sync, note_id))
    return result


@router.delete("/{note_id}")
@router.delete("/lecture-notes/{note_id}")
async def delete_lecture_note(note_id: str) -> Dict[str, Any]:
    """Deletes a note and cascades its items."""
    deleted = await asyncio.to_thread(_delete_lecture_note_sync, note_id=note_id)
    return {"status": "success", "deleted": deleted}


@router.post("/{note_id}/extract-topics")
@router.post("/lecture-notes/{note_id}/extract-topics")
async def extract_and_sync_note_topics(note_id: str, payload: Optional[Dict[str, Any]] = None, background_tasks: BackgroundTasks = None) -> Dict[str, Any]:
    """Extracts explicit wikilinks and NLP topics from note markdown in background, returning immediate populating status."""
    md_override = payload.get("content_markdown") if payload and isinstance(payload, dict) else None
    if md_override is not None:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE user_lecture_notes SET content_markdown = ?, updated_at = unixepoch('now') WHERE note_id = ?",
                (md_override, note_id),
            )
    _NOTE_MINING_STATE[note_id] = {"status": "populating", "updated_at": time.time()}
    if background_tasks is not None:
        background_tasks.add_task(asyncio.to_thread, _mine_and_populate_note_items_sync, note_id)
    else:
        asyncio.create_task(asyncio.to_thread(_mine_and_populate_note_items_sync, note_id))

    note_data = await asyncio.to_thread(_get_single_lecture_note_sync, note_id=note_id)
    return note_data or {"status": "success", "mining_status": "populating", "message": "Populating... Please wait", "items": []}


@router.post("/{note_id}/items")
@router.post("/lecture-notes/{note_id}/items")
async def upsert_lecture_note_item(note_id: str, payload: LectureNoteItemPayload) -> Dict[str, Any]:
    """Upserts a locked topic line, resolves references and MCQ count, and returns enriched item."""
    return await asyncio.to_thread(_upsert_lecture_note_item_sync, note_id=note_id, payload=payload)


@router.put("/{note_id}/items/{item_id}")
@router.put("/lecture-notes/{note_id}/items/{item_id}")
async def edit_lecture_note_item(note_id: str, item_id: str, payload: LectureNoteItemPayload) -> Dict[str, Any]:
    """Edits or renames an individual topic in the note's topic list."""
    payload.item_id = item_id
    return await asyncio.to_thread(_upsert_lecture_note_item_sync, note_id=note_id, payload=payload)


@router.delete("/{note_id}/items/{item_id}")
@router.delete("/lecture-notes/{note_id}/items/{item_id}")
async def delete_lecture_note_item(
    note_id: str,
    item_id: str,
    topic_key: Optional[str] = None,
    topic_text: Optional[str] = None,
    global_ignore: bool = False,
) -> Dict[str, Any]:
    """Removes a topic line from a lecture note and appends to suppressed_topics_json."""
    deleted = await asyncio.to_thread(
        _delete_lecture_note_item_sync,
        note_id=note_id,
        item_id=item_id,
        topic_key=str(topic_key) if topic_key else None,
        topic_text=str(topic_text) if topic_text else None,
        global_ignore=bool(global_ignore),
    )
    return {"status": "success", "deleted": deleted}


@router.delete("/{note_id}/topics")
@router.delete("/lecture-notes/{note_id}/topics")
async def delete_lecture_note_topic(
    note_id: str,
    topic_key: Optional[str] = None,
    topic_text: Optional[str] = None,
    global_ignore: bool = False,
) -> Dict[str, Any]:
    """Removes a topic from a lecture note by topic_key or topic_text and suppresses it."""
    deleted = await asyncio.to_thread(
        _delete_lecture_note_item_sync,
        note_id=note_id,
        item_id=None,
        topic_key=str(topic_key) if topic_key else None,
        topic_text=str(topic_text) if topic_text else None,
        global_ignore=bool(global_ignore),
    )
    return {"status": "success", "deleted": deleted}
