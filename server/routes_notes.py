from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import editdistance

router = APIRouter(prefix="/api/notes", tags=["Notes QA"])

class QAMetricsRequest(BaseModel):
    original_text: str
    parsed_text: str

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

    lev_dist = editdistance.eval(original, parsed)

    return {
        "char_ratio": round(char_ratio, 4),
        "token_ratio": round(token_ratio, 4),
        "levenshtein_distance": lev_dist
    }
