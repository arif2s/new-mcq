import gc
import json
import logging
import os
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

# Enforce Invariant 1: Zero VRAM for Backend (CPU-only FlashRank cross-encoding)
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from config import BASE_DIR, DEFAULT_FLASHRANK_MODEL, FLASHRANK_CACHE_DIR
from core.database import get_db_connection

logger = logging.getLogger("medisearch.reranker")

_RANKER_LOCK = threading.RLock()
_RANKER_CACHE: Dict[str, Any] = {}
_CONFIGURED_MODEL_CACHE: Optional[str] = None


def get_configured_flashrank_model(force_refresh: bool = False) -> str:
    """Reads the active FlashRank model from SQLite user_state with in-memory caching."""
    global _CONFIGURED_MODEL_CACHE
    with _RANKER_LOCK:
        if not force_refresh and _CONFIGURED_MODEL_CACHE is not None:
            return _CONFIGURED_MODEL_CACHE

        resolved_model: Optional[str] = None
        try:
            with closing(get_db_connection()) as conn:
                rows = conn.execute(
                    "SELECT key, value FROM user_state "
                    "WHERE key IN ('flashrankModel', 'app_persistence')"
                ).fetchall()
                state_map = {row["key"]: row["value"] for row in rows}

                # 1. Check direct user_state key (Section 4 AppPersistence contract)
                raw_direct = state_map.get("flashrankModel")
                if raw_direct:
                    try:
                        parsed = json.loads(raw_direct)
                        if isinstance(parsed, str) and parsed.strip():
                            resolved_model = parsed.strip()
                    except Exception:
                        if isinstance(raw_direct, str) and raw_direct.strip():
                            resolved_model = raw_direct.strip()

                # 2. Check nested key inside 'app_persistence' JSON object
                if not resolved_model and "app_persistence" in state_map:
                    raw_app = state_map["app_persistence"]
                    state_obj = json.loads(raw_app) if isinstance(raw_app, str) else raw_app
                    if isinstance(state_obj, dict):
                        candidate = state_obj.get("flashrankModel")
                        if isinstance(candidate, str) and candidate.strip():
                            resolved_model = candidate.strip()
        except Exception as exc:
            logger.debug("Failed to read configured FlashRank model from DB: %s", exc)

        _CONFIGURED_MODEL_CACHE = resolved_model or DEFAULT_FLASHRANK_MODEL
        return _CONFIGURED_MODEL_CACHE


def set_configured_flashrank_model(model_id: str) -> str:
    """Explicitly updates the active FlashRank model in SQLite user_state and invalidates caches."""
    global _CONFIGURED_MODEL_CACHE
    if not model_id or not str(model_id).strip():
        return get_configured_flashrank_model(force_refresh=True)

    clean_id = str(model_id).strip()
    with _RANKER_LOCK:
        _CONFIGURED_MODEL_CACHE = clean_id
        _RANKER_CACHE.clear()
        gc.collect()

        try:
            with closing(get_db_connection()) as conn:
                conn.execute(
                    """
                    INSERT INTO user_state (key, value, updated_at)
                    VALUES ('flashrankModel', ?, unixepoch('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (json.dumps(clean_id),),
                )

                cursor = conn.cursor()
                cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
                row = cursor.fetchone()
                app_obj = {}
                if row and row[0]:
                    try:
                        raw_app = row[0]
                        app_obj = json.loads(raw_app) if isinstance(raw_app, str) else raw_app
                    except Exception:
                        pass
                if isinstance(app_obj, dict):
                    app_obj["flashrankModel"] = clean_id
                    conn.execute(
                        """
                        INSERT INTO user_state (key, value, updated_at)
                        VALUES ('app_persistence', ?, unixepoch('now'))
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (json.dumps(app_obj),),
                    )
                conn.commit()
        except Exception as exc:
            logger.error("Failed to persist set_configured_flashrank_model to DB: %s", exc)

    return clean_id


def _has_valid_model_files(directory: Path) -> bool:
    """Verifies that both a non-empty .onnx model file and tokenizer.json exist in directory."""
    if not directory.exists() or not directory.is_dir():
        return False
    try:
        has_tok = any(
            f.is_file() and f.name == "tokenizer.json" and f.stat().st_size > 0
            for f in directory.rglob("*")
        )
        has_onnx = any(
            f.is_file() and f.suffix.lower() == ".onnx" and f.stat().st_size > 0
            for f in directory.rglob("*")
        )
        return has_tok and has_onnx
    except OSError:
        return False


def is_flashrank_model_downloaded(model_name: str) -> bool:
    """Checks if the FlashRank model folder and ONNX weights exist locally inside FLASHRANK_CACHE_DIR."""
    if not model_name:
        return False

    model_dir = Path(FLASHRANK_CACHE_DIR) / model_name
    if _has_valid_model_files(model_dir):
        return True

    # Root-anchored fallback directory check
    legacy_dir = Path(BASE_DIR) / "data" / "models" / "flashrank" / model_name
    if _has_valid_model_files(legacy_dir):
        return True

    return False


def download_flashrank_model(model_name: str) -> Dict[str, Any]:
    """Downloads and initializes the selected FlashRank model into FLASHRANK_CACHE_DIR."""
    os.makedirs(FLASHRANK_CACHE_DIR, exist_ok=True)
    with _RANKER_LOCK:
        try:
            from flashrank import Ranker

            ranker = Ranker(model_name=model_name, cache_dir=str(FLASHRANK_CACHE_DIR))
            _RANKER_CACHE.clear()
            gc.collect()
            _RANKER_CACHE[model_name] = ranker

            global _CONFIGURED_MODEL_CACHE
            get_configured_flashrank_model(force_refresh=True)

            logger.info("Successfully loaded and cached FlashRank model '%s'", model_name)
            return {"status": "success", "model_id": model_name, "downloaded": True}
        except Exception as exc:
            logger.error("Failed to download FlashRank model '%s': %s", model_name, exc)
            return {
                "status": "error",
                "model_id": model_name,
                "message": str(exc),
                "downloaded": False,
            }


def get_ranker(model_name: Optional[str] = None) -> Optional[Any]:
    """Returns a cached FlashRank Ranker singleton (Invariant 8).

    Guarantees zero network calls in offline mode if files are not cached locally.
    """
    with _RANKER_LOCK:
        target_model = model_name or get_configured_flashrank_model()
        if target_model in _RANKER_CACHE:
            return _RANKER_CACHE[target_model]

        # Offline safety guard: do not invoke Ranker() if the ONNX files are not on disk
        if not is_flashrank_model_downloaded(target_model):
            if (
                target_model != DEFAULT_FLASHRANK_MODEL
                and is_flashrank_model_downloaded(DEFAULT_FLASHRANK_MODEL)
            ):
                target_model = DEFAULT_FLASHRANK_MODEL
                if target_model in _RANKER_CACHE:
                    return _RANKER_CACHE[target_model]
            else:
                logger.debug(
                    "FlashRank model '%s' not cached locally; bypassing cross-encoder in offline mode.",
                    target_model,
                )
                return None

        try:
            from flashrank import Ranker

            os.makedirs(FLASHRANK_CACHE_DIR, exist_ok=True)
            ranker = Ranker(model_name=target_model, cache_dir=str(FLASHRANK_CACHE_DIR))
            if _RANKER_CACHE:
                _RANKER_CACHE.clear()
                gc.collect()
            _RANKER_CACHE[target_model] = ranker
            return ranker
        except Exception as exc:
            logger.debug("FlashRank model '%s' initialization skipped: %s", target_model, exc)
            return None


def _normalize_fallback_chunks(
    chunks: List[Dict[str, Any]], top_k: int
) -> List[Dict[str, Any]]:
    """Ensures Invariant 9 ('text' and 'rerank_score' keys) even when cross-encoding is bypassed."""
    output: List[Dict[str, Any]] = []
    for chunk in chunks[: max(1, int(top_k or 5))]:
        cand = dict(chunk)
        body = str(
            cand.get("text")
            or cand.get("child_content")
            or cand.get("snippet")
            or cand.get("parent_content")
            or cand.get("title")
            or ""
        )
        cand["text"] = body
        base_score = float(cand.get("rerank_score", cand.get("score", 0.0)) or 0.0)
        cand["score"] = round(base_score, 4)
        cand["rerank_score"] = round(base_score, 4)
        output.append(cand)
    return output


def rerank_chunks(
    query: str, chunks: List[Dict[str, Any]], top_k: int = 5
) -> List[Dict[str, Any]]:
    """Reranks candidate chunks using FlashRank cross-encoding combined with depth scoring."""
    if not chunks:
        return []

    top_k = max(1, int(top_k or 5))
    if not query or not str(query).strip():
        return _normalize_fallback_chunks(chunks, top_k)

    ranker = get_ranker()
    if ranker is None:
        return _normalize_fallback_chunks(chunks, top_k)

    try:
        from flashrank import RerankRequest

        passages: List[Dict[str, Any]] = []
        extracted_bodies: List[str] = []

        for i, chunk in enumerate(chunks):
            heading = str(
                chunk.get("heading_path", "")
                or chunk.get("heading", "")
                or chunk.get("subheading", "")
                or chunk.get("title", "")
            ).strip()

            body = str(
                chunk.get("text", "")
                or chunk.get("child_content", "")
                or chunk.get("snippet", "")
                or chunk.get("parent_content", "")
                or chunk.get("title", "")
            ).strip()
            extracted_bodies.append(body)

            if heading and not body.startswith(heading):
                combined_text = f"{heading}: {body}"[:1200]
            else:
                combined_text = body[:1200]

            passages.append({"id": i, "text": combined_text.strip() or " "})

        request = RerankRequest(query=str(query).strip(), passages=passages)
        with _RANKER_LOCK:
            reranked_results = ranker.rerank(request)

        reranked_candidates: List[Dict[str, Any]] = []
        for res in reranked_results:
            idx = int(res["id"])
            cand = dict(chunks[idx])
            if not cand.get("text"):
                cand["text"] = extracted_bodies[idx]

            sem_score = float(res.get("score", 0.0))
            depth_bonus = float(cand.get("depth_bonus", 0.0))

            final_score = round(sem_score + (depth_bonus * 0.08), 4)
            cand["rerank_score"] = final_score
            cand["score"] = final_score
            reranked_candidates.append(cand)

        reranked_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked_candidates[:top_k]
    except Exception as exc:
        logger.warning("FlashRank reranking fallback triggered: %s", exc)
        return _normalize_fallback_chunks(chunks, top_k)
