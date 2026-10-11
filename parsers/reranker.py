import logging
import os

logger = logging.getLogger(__name__)

def rerank_chunks(query: str, chunks: list[dict], top_k: int = 3) -> list[dict]:
    if not chunks:
        return []

    try:
        from flashrank import Ranker, RerankRequest
        ranker = Ranker(model_name="ms-marco-TinyBERT-L-2-v2", cache_dir="./data/models/flashrank")

        passages = []
        for i, chunk in enumerate(chunks):
            # Try to get the child content for reranking
            text = chunk.get("child_content", "") or chunk.get("parent_content", "") or chunk.get("title", "")
            passages.append({
                "id": i,
                "text": text
            })

        request = RerankRequest(query=query, passages=passages)
        reranked_results = ranker.rerank(request)

        # Map back to candidates
        reranked_candidates = []
        for res in reranked_results:
            idx = res["id"]
            cand = chunks[idx]
            cand["rerank_score"] = float(res["score"])
            reranked_candidates.append(cand)

        reranked_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked_candidates[:top_k]
    except Exception as e:
        logger.error(f"FlashRank reranking failed: {e}. Returning default chunks.")
        return chunks[:top_k]
