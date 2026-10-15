import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import closing

from config import FLASHRANK_CACHE_DIR
from core.database import get_db_connection

logger = logging.getLogger(__name__)

# Singleton cache for loaded FlashRank Ranker models
_RANKER_CACHE: Dict[str, Any] = {}


def get_configured_flashrank_model() -> str:
    """Reads the active FlashRank model from SQLite user_state."""
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                model_name = state.get("flashrankModel")
                if model_name:
                    return model_name
    except Exception:
        pass
    return "ms-marco-TinyBERT-L-2-v2"


def is_flashrank_model_downloaded(model_name: str) -> bool:
    """Checks if the FlashRank model folder exists locally inside FLASHRANK_CACHE_DIR."""
    model_dir = Path(FLASHRANK_CACHE_DIR) / model_name
    if model_dir.exists() and model_dir.is_dir():
        return any(model_dir.iterdir())
    legacy_dir = Path("./data/models/flashrank") / model_name
    return legacy_dir.exists() and legacy_dir.is_dir() and any(legacy_dir.iterdir())


def download_flashrank_model(model_name: str) -> Dict[str, Any]:
    """
    Explicitly downloads and initializes the selected FlashRank model into FLASHRANK_CACHE_DIR.
    Triggered by pressing 'Download for Offline Use' in Settings.
    """
    os.makedirs(FLASHRANK_CACHE_DIR, exist_ok=True)
    try:
        from flashrank import Ranker
        ranker = Ranker(model_name=model_name, cache_dir=str(FLASHRANK_CACHE_DIR))
        _RANKER_CACHE[model_name] = ranker
        return {"status": "success", "model_id": model_name, "downloaded": True}
    except Exception as e:
        logger.error(f"Failed to download FlashRank model {model_name}: {e}")
        return {"status": "error", "model_id": model_name, "message": str(e), "downloaded": False}


def get_ranker(model_name: Optional[str] = None):
    """
    Returns a cached FlashRank Ranker instance.
    If not downloaded locally, avoids blocking offline searches unless explicitly available.
    """
    target_model = model_name or get_configured_flashrank_model()
    if target_model in _RANKER_CACHE:
        return _RANKER_CACHE[target_model]

    if not is_flashrank_model_downloaded(target_model):
        if target_model != "ms-marco-TinyBERT-L-2-v2" and is_flashrank_model_downloaded("ms-marco-TinyBERT-L-2-v2"):
            target_model = "ms-marco-TinyBERT-L-2-v2"
            if target_model in _RANKER_CACHE:
                return _RANKER_CACHE[target_model]

    try:
        from flashrank import Ranker
        os.makedirs(FLASHRANK_CACHE_DIR, exist_ok=True)
        ranker = Ranker(model_name=target_model, cache_dir=str(FLASHRANK_CACHE_DIR))
        _RANKER_CACHE[target_model] = ranker
        return ranker
    except Exception as e:
        logger.debug(f"FlashRank model '{target_model}' unavailable ({e}); using depth-weighted lexical ranking.")
        return None


def rerank_chunks(query: str, chunks: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Reranks candidate chunks using FlashRank cross-encoding combined with
    depth/comprehensiveness score so detailed, longer articles stay prioritized.
    """
    if not chunks:
        return []

    ranker = get_ranker()
    if ranker is None:
        return chunks[:top_k]

    try:
        from flashrank import RerankRequest

        passages = []
        for i, chunk in enumerate(chunks):
            heading = chunk.get("heading_path", "") or chunk.get("subheading", "") or chunk.get("title", "")
            body = (
                chunk.get("child_content", "")
                or chunk.get("snippet", "")
                or chunk.get("parent_content", "")
                or chunk.get("title", "")
            )
            combined_text = f"{heading}: {body}"[:1200] if heading else body[:1200]
            passages.append({"id": i, "text": combined_text or " "})

        request = RerankRequest(query=query, passages=passages)
        reranked_results = ranker.rerank(request)

        reranked_candidates = []
        for res in reranked_results:
            idx = res["id"]
            cand = dict(chunks[idx])
            sem_score = float(res["score"])
            depth_bonus = float(cand.get("depth_bonus", 0.0))
            cand["rerank_score"] = sem_score + (depth_bonus * 0.08)
            reranked_candidates.append(cand)

        reranked_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked_candidates[:top_k]
    except Exception as e:
        logger.warning(f"FlashRank reranking fallback triggered: {e}")
        return chunks[:top_k]
