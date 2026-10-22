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

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

try:
    from pydantic import ConfigDict

    _NOTE_CONFIG = ConfigDict(extra="allow")
except ImportError:
    _NOTE_CONFIG = None

from config import VAULTS_DIR
from core.database import get_db_connection
from parsers.obsidian_reader import read_obsidian_for_browser
from parsers.zim_reader import get_zim_article_html, get_zim_asset

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