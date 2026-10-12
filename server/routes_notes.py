from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import difflib
import subprocess
import shutil
import os
from pathlib import Path

router = APIRouter(prefix="/api/notes", tags=["Notes QA"])

class QAMetricsRequest(BaseModel):
    original_text: str
    parsed_text: str

def safe_levenshtein_approximation(a: str, b: str) -> int:
    """
    Safely approximate Levenshtein distance using standard library without C-extensions.
    Useful for strict offline Windows environments where wheels fail.
    """
    if not a:
        return len(b)
    if not b:
        return len(a)

    # SequenceMatcher ratio isn't a direct inverse for edit distance.
    # We can do a quick opcodes count to approximate Levenshtein accurately:
    matcher = difflib.SequenceMatcher(None, a, b)
    distance = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'replace':
            distance += max(i2 - i1, j2 - j1)
        elif tag == 'insert':
            distance += j2 - j1
        elif tag == 'delete':
            distance += i2 - i1
    return distance

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
        "levenshtein_distance": lev_dist
    }


@router.get("/launch-sumatra")
def launch_sumatra(file_path: str, page: int = 1):
    sumatra_exe = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe") or "SumatraPDF"
    if sys.platform != 'win32':
        return {"status": "error", "message": "PDF deep-linking via SumatraPDF is only supported on Windows."}

    try:
        sanitized_path = Path(file_path).resolve()
        subprocess.Popen([sumatra_exe, "-reuse-instance", "-page", str(page), str(sanitized_path)], shell=False)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/zim-article")
def zim_article(article_id: str):
    import libzim
    from core.database import get_db_connection

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM knowledge_sources WHERE type = 'zim' AND enabled = 1 LIMIT 1")
            row = cursor.fetchone()

        if not row:
            return {"status": "error", "message": "No ZIM file enabled."}

        zim_path = row["path"] if isinstance(row, dict) else row[0]

        archive = libzim.Archive(zim_path)

        # In libzim, we typically get entry by path or title. We'll try by path/url.
        entry = archive.get_entry_by_path(article_id)

        content = entry.get_item().content
        return {"status": "success", "html": content.decode('utf-8')}
    except Exception as e:
        return {"status": "error", "message": str(e)}
