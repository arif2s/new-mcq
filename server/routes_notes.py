from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import difflib

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
