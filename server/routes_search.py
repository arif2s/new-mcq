from fastapi import APIRouter
from pydantic import BaseModel
from parsers.tantivy_engine import search_index
from typing import Dict, Any

router = APIRouter(prefix="/api/search", tags=["search"])

class SearchRequest(BaseModel):
    query: str

@router.post("")
def perform_search(req: SearchRequest) -> Dict[str, Any]:
    # Route the top hit
    results = search_index(req.query, limit_per_source=1)

    # Return the top hit across all sources
    # Since search_index limits to 1 per source, we just find the absolute best among the returned
    top_hit = None
    max_score = -1.0

    for source, docs in results.items():
        if docs:
            doc = docs[0]
            if doc["score"] > max_score:
                max_score = doc["score"]
                top_hit = {
                    "source": source,
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "score": doc["score"]
                }

    return {"status": "success", "top_hit": top_hit, "all_results": results}
