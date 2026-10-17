import os
import sys
import shutil
import difflib
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional
from contextlib import closing
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from core.database import get_db_connection
from parsers.obsidian_reader import read_obsidian_for_browser
from parsers.zim_reader import get_zim_article_html, get_zim_asset

router = APIRouter(prefix="/api/notes", tags=["Notes & Readers"])


class QAMetricsRequest(BaseModel):
    original_text: str
    parsed_text: str


class OpenObsidianRequest(BaseModel):
    file_path: Optional[str] = None
    title: Optional[str] = None
    vault: Optional[str] = None


def safe_levenshtein_approximation(a: str, b: str) -> int:
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


def _resolve_obsidian_note_path(file_path: Optional[str] = None, title: Optional[str] = None) -> Optional[Path]:
    """
    Resolves an Obsidian note from either an absolute/relative path or a [[Wikilink]] title
    by searching enabled Obsidian vaults in knowledge_sources.
    """
    if file_path:
        candidate = Path(file_path)
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    search_stem = Path(title or file_path or "").stem.strip()
    if not search_stem:
        return None

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM knowledge_sources WHERE type = 'obsidian' AND enabled = 1")
        vault_rows = cursor.fetchall()

    for row in vault_rows:
        vault_dir = Path(row["path"])
        if not vault_dir.exists():
            continue
        if file_path:
            rel_cand = vault_dir / file_path
            if rel_cand.exists() and rel_cand.is_file():
                return rel_cand.resolve()
        for md_file in vault_dir.rglob("*.md"):
            if md_file.stem.lower() == search_stem.lower() or md_file.name.lower() == search_stem.lower():
                return md_file.resolve()

    return None


@router.post("/qa_metrics")
async def calculate_qa_metrics(payload: QAMetricsRequest):
    original = payload.original_text or ""
    parsed = payload.parsed_text or ""

    orig_chars = len(original)
    parsed_chars = len(parsed)
    char_ratio = parsed_chars / orig_chars if orig_chars > 0 else 0

    orig_tokens = len(original.split())
    parsed_tokens = len(parsed.split())
    token_ratio = parsed_tokens / orig_tokens if orig_tokens > 0 else 0

    lev_dist = safe_levenshtein_approximation(original, parsed)

    return {
        "char_ratio": round(char_ratio, 4),
        "token_ratio": round(token_ratio, 4),
        "levenshtein_distance": lev_dist,
    }


@router.get("/launch-sumatra")
def launch_sumatra(file_path: str, page: int = 1):
    sumatra_exe = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe") or "SumatraPDF"
    if sys.platform != "win32":
        return {"status": "error", "message": "PDF deep-linking via SumatraPDF is only supported on Windows."}

    try:
        from server.routes_sources import _resolve_pdf_path
        resolved_path = _resolve_pdf_path(file_path) or Path(file_path).resolve()
        subprocess.Popen([sumatra_exe, "-reuse-instance", "-page", str(page), str(resolved_path)], shell=False)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/obsidian-note")
def get_obsidian_note(file_path: Optional[str] = None, title: Optional[str] = None):
    """
    Returns an Obsidian note formatted for in-browser rendering (preserving LaTeX equations,
    Markdown tables | |, HTML tags, and rewriting ![[image.png]] to /api/sources/asset).
    """
    resolved = _resolve_obsidian_note_path(file_path=file_path, title=title)
    if not resolved:
        return {"status": "error", "message": f"Obsidian note not found: {title or file_path}"}
    return read_obsidian_for_browser(str(resolved))


@router.post("/open-obsidian")
def open_obsidian_native(req: OpenObsidianRequest):
    """
    Opens the target note inside the native Obsidian desktop application via obsidian:// URI
    when the user's Settings preference is set to 'Open in Obsidian App'.
    """
    resolved = _resolve_obsidian_note_path(file_path=req.file_path, title=req.title)
    if resolved:
        uri = f"obsidian://open?path={urllib.parse.quote(str(resolved))}"
    else:
        vault = urllib.parse.quote(req.vault or "Primary")
        file_target = urllib.parse.quote(req.title or req.file_path or "")
        uri = f"obsidian://open?vault={vault}&file={file_target}"

    if sys.platform == "win32":
        try:
            os.startfile(uri)
        except Exception:
            pass

    return {"status": "success", "uri": uri, "resolved_path": str(resolved) if resolved else ""}


@router.get("/zim-article")
def zim_article(article_id: str, zim_path: Optional[str] = None):
    """
    Extracts and cleans HTML from an enabled >10GB ZIM archive for in-browser viewing,
    automatically following ZIM redirects and rewriting internal links.
    """
    return get_zim_article_html(article_id, zim_path=zim_path)


@router.get("/zim-asset")
def zim_asset(path: str, zim_path: Optional[str] = None):
    """
    Serves images, illustrations, and media assets extracted from a local ZIM archive.
    """
    asset = get_zim_asset(path, zim_path=zim_path)
    if not asset:
        raise HTTPException(status_code=404, detail=f"ZIM asset '{path}' not found")
    return Response(content=asset["content"], media_type=asset["mimetype"])
