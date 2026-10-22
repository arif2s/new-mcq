"""
Persistent user and application state GET/PUT/PATCH storage and offline vendor asset management.
Enforces zero VRAM usage, recursive deep-merge PATCH semantics, atomic SQLite upserts, and settings export/import.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

try:
    from pydantic import ConfigDict

    _STATE_CONFIG = ConfigDict(extra="allow")
except ImportError:
    _STATE_CONFIG = None

from config import VENDOR_DIR

try:
    from config import VENDOR_ASSETS
except ImportError:
    VENDOR_ASSETS = {
        "tailwind.min.js": "https://cdn.tailwindcss.com",
        "marked.min.js": "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
        "purify.min.js": "https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js",
        "katex.min.js": "https://cdn.jsdelivr.net/npm/katex/dist/katex.min.js",
        "katex.min.css": "https://cdn.jsdelivr.net/npm/katex/dist/katex.min.css",
    }

from core.database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/state", tags=["state"])

_KNOWN_APP_PERSISTENCE_FIELDS = {
    "theme",
    "wallpaper",
    "llmEnabled",
    "skipLMStudio",
    "pdfParser",
    "extractorStrategy",
    "extractor_strategy",
    "embeddingModel",
    "embedding_model",
    "flashrankModel",
    "flashrank_model",
    "scispacyModel",
    "scispacy_model",
    "processingQueueHardLimit",
    "sidebarCollapsed",
    "inspectorOpen",
}


class StateUpdate(BaseModel):
    """
    Flexible state payload supporting both key-value pairs and flat/nested dictionary mappings.
    Allows extra arbitrary fields without raising validation errors.
    """

    if _STATE_CONFIG is not None:
        model_config = _STATE_CONFIG
    else:

        class Config:
            extra = "allow"

    key: Optional[str] = None
    value: Optional[Any] = None


class StateImportPayload(BaseModel):
    state: Dict[str, Any]


def _serialize_value(v: Any) -> str:
    """Safely serializes state values, preventing double-encoding of pre-serialized JSON."""
    if isinstance(v, str):
        trimmed = v.strip()
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                json.loads(trimmed)
                return trimmed
            except Exception:
                pass
        return json.dumps(v)
    return json.dumps(v)


def _deserialize_value(raw: Any) -> Any:
    """Parses JSON state payloads or falls back cleanly to the raw string/value."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _deep_merge_dicts(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merges nested dictionaries without clobbering sibling keys."""
    merged = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dicts(merged[k], v)
        else:
            merged[k] = v
    return merged


def _extract_state_pairs(
    payload: Union[StateUpdate, Dict[str, Any]]
) -> List[Tuple[str, Any]]:
    """Extracts (key, value) pairs from StateUpdate models or raw dictionaries."""
    pairs: List[Tuple[str, Any]] = []

    if isinstance(payload, StateUpdate):
        if payload.key is not None:
            pairs.append((str(payload.key), payload.value))

        extra = getattr(payload, "model_extra", None)
        if extra is None:
            extra = {
                k: v
                for k, v in payload.__dict__.items()
                if k not in ("key", "value")
            }

        if extra:
            for k, v in extra.items():
                pairs.append((str(k), v))
    elif isinstance(payload, dict):
        if "key" in payload and "value" in payload and len(payload) == 2:
            pairs.append((str(payload["key"]), payload.get("value")))
        else:
            for k, v in payload.items():
                pairs.append((str(k), v))

    return pairs


def _sync_app_persistence_dict(
    conn: Any, incoming_dict: Dict[str, Any], deep_merge: bool = True
) -> None:
    """Ensures updates to app_persistence or known persistence fields stay synced in user_state."""
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
    row = cursor.fetchone()
    existing_app: Dict[str, Any] = {}
    if row:
        raw_val = row[0] if isinstance(row, tuple) else row["value"]
        parsed = _deserialize_value(raw_val)
        if isinstance(parsed, dict):
            existing_app = parsed

    updated_app = (
        _deep_merge_dicts(existing_app, incoming_dict)
        if deep_merge
        else dict(incoming_dict)
    )
    conn.execute(
        """
        INSERT INTO user_state (key, value, updated_at)
        VALUES ('app_persistence', ?, unixepoch('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (_serialize_value(updated_app),),
    )


# ============================================================================
# 1. STATIC VENDOR & SETTINGS ROUTES (Must Precede Parameterized /{key} Routes)
# ============================================================================

@router.get("/verify_vendor")
@router.get("/vendor_status")
def get_vendor_status() -> Dict[str, Any]:
    """
    Reports whether UI vendor dependencies (Tailwind, Marked, DOMPurify, KaTeX)
    are cached locally in ui/vendor/ for 100% offline operation.
    Supports both canonical /api/state/verify_vendor and /api/state/vendor_status.
    """
    assets: Dict[str, bool] = {}
    all_cached = True

    asset_keys = (
        VENDOR_ASSETS.keys() if hasattr(VENDOR_ASSETS, "keys") else VENDOR_ASSETS
    )

    for filename in asset_keys:
        p = VENDOR_DIR / filename
        is_ok = p.exists() and p.is_file() and p.stat().st_size > 128
        assets[filename] = is_ok
        if not is_ok:
            all_cached = False

    return {
        "status": "success",
        "offline_ready": all_cached,
        "all_present": all_cached,
        "assets": assets,
        "vendor_dir": str(VENDOR_DIR),
    }


@router.post("/verify_vendor")
@router.post("/vendor_download")
async def trigger_vendor_download() -> Dict[str, Any]:
    """Triggers atomic downloading of vendor dependencies via config.ensure_offline_vendor_assets."""
    try:
        from config import ensure_offline_vendor_assets

        await asyncio.to_thread(ensure_offline_vendor_assets)
        return get_vendor_status()
    except Exception as exc:
        logger.error("Failed to download offline vendor assets: %s", exc)
        return {
            "status": "error",
            "message": str(exc),
            "offline_ready": False,
        }


@router.get("/export")
def export_state() -> Dict[str, Any]:
    """Exports all persistent application state from SQLite for backup/migration."""
    state_dump: Dict[str, Any] = {}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_state ORDER BY key ASC")
        for row in cursor.fetchall():
            k = str(row[0] if isinstance(row, tuple) else row["key"])
            v = row[1] if isinstance(row, tuple) else row["value"]
            state_dump[k] = _deserialize_value(v)

    return {
        "status": "success",
        "state": state_dump,
    }


@router.post("/import")
def import_state(
    payload: Union[StateImportPayload, Dict[str, Any]]
) -> Dict[str, Any]:
    """Restores application state from a JSON backup dump."""
    data = payload.state if isinstance(payload, StateImportPayload) else payload
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid state import payload; expected JSON object.",
        )

    imported_count = 0
    with closing(get_db_connection()) as conn:
        for k, v in data.items():
            if k in ("status", "message"):
                continue
            val_str = _serialize_value(v)
            conn.execute(
                """
                INSERT INTO user_state (key, value, updated_at)
                VALUES (?, ?, unixepoch('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(k), val_str),
            )
            imported_count += 1
        conn.commit()

    return {"status": "success", "imported_keys": imported_count}


# ============================================================================
# 2. BULK STATE ENDPOINTS
# ============================================================================

@router.get("")
@router.get("/")
def get_all_state() -> Dict[str, Any]:
    """Retrieves all stored user state key-value pairs."""
    result: Dict[str, Any] = {}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_state")
        for row in cursor.fetchall():
            k = str(row[0] if isinstance(row, tuple) else row["key"])
            v = row[1] if isinstance(row, tuple) else row["value"]
            result[k] = _deserialize_value(v)

    return result


@router.put("")
@router.put("/")
@router.post("")
@router.post("/")
def save_state(state: Union[StateUpdate, Dict[str, Any]]) -> Dict[str, str]:
    """
    Upserts persistent state records adhering to Invariant 15.
    Supports single StateUpdate objects and multi-key dictionaries.
    """
    pairs = _extract_state_pairs(state)
    if not pairs:
        return {"status": "noop", "message": "No valid state keys provided."}

    persistence_patch: Dict[str, Any] = {}
    with closing(get_db_connection()) as conn:
        for k, v in pairs:
            val_str = _serialize_value(v)
            conn.execute(
                """
                INSERT INTO user_state (key, value, updated_at)
                VALUES (?, ?, unixepoch('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(k), val_str),
            )
            if k in _KNOWN_APP_PERSISTENCE_FIELDS:
                persistence_patch[k] = v

        if persistence_patch:
            _sync_app_persistence_dict(conn, persistence_patch, deep_merge=True)
        conn.commit()

    return {"status": "success"}


@router.patch("")
@router.patch("/")
def patch_all_state(
    state: Union[StateUpdate, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Deep-merges incoming state keys into user_state (and user_state.app_persistence)
    without clobbering existing sibling keys.
    """
    pairs = _extract_state_pairs(state)
    if not pairs:
        return {"status": "noop", "message": "No valid state keys provided."}

    persistence_patch: Dict[str, Any] = {}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        for k, v in pairs:
            parsed_v = _deserialize_value(v) if isinstance(v, str) else v
            if isinstance(parsed_v, dict):
                cursor.execute(
                    "SELECT value FROM user_state WHERE key = ?", (str(k),)
                )
                row = cursor.fetchone()
                existing_val = (
                    _deserialize_value(
                        row[0] if isinstance(row, tuple) else row["value"]
                    )
                    if row
                    else None
                )
                if isinstance(existing_val, dict):
                    parsed_v = _deep_merge_dicts(existing_val, parsed_v)

            conn.execute(
                """
                INSERT INTO user_state (key, value, updated_at)
                VALUES (?, ?, unixepoch('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(k), _serialize_value(parsed_v)),
            )
            if k in _KNOWN_APP_PERSISTENCE_FIELDS:
                persistence_patch[k] = parsed_v

        if persistence_patch:
            _sync_app_persistence_dict(conn, persistence_patch, deep_merge=True)
        conn.commit()

    return {"status": "success"}


# ============================================================================
# 3. KEY-SPECIFIC STATE ENDPOINTS
# ============================================================================

@router.get("/{key}")
def get_state(key: str) -> Any:
    """
    Retrieves a specific state record by key.
    Returns the parsed JSON data, scalar value, or empty dict if not found.
    """
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = ?", (key,))
        row = cursor.fetchone()

    if row:
        raw_val = row[0] if isinstance(row, tuple) else row["value"]
        return _deserialize_value(raw_val)

    return {}


@router.put("/{key}")
@router.post("/{key}")
def put_state_key(key: str, payload: Any = Body(...)) -> Dict[str, str]:
    """Directly sets the state payload for a specific key."""
    actual_value = (
        payload.get("value")
        if isinstance(payload, dict) and "value" in payload and len(payload) == 1
        else payload
    )
    val_str = _serialize_value(actual_value)

    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT INTO user_state (key, value, updated_at)
            VALUES (?, ?, unixepoch('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, val_str),
        )
        conn.commit()

    return {"status": "success"}


@router.patch("/{key}")
def patch_state_key(key: str, payload: Any = Body(...)) -> Dict[str, Any]:
    """
    Partially updates an existing state dictionary (e.g., app_persistence updates).
    Recursively deep-merges patch dictionary with existing dictionary without overwriting sibling keys.
    """
    patch_data = (
        payload.get("value")
        if isinstance(payload, dict) and "value" in payload and len(payload) == 1
        else payload
    )
    if isinstance(patch_data, str):
        patch_data = _deserialize_value(patch_data)

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = ?", (key,))
        row = cursor.fetchone()

        existing_val = None
        if row:
            raw_val = row[0] if isinstance(row, tuple) else row["value"]
            existing_val = _deserialize_value(raw_val)

        if isinstance(existing_val, dict) and isinstance(patch_data, dict):
            merged_val = _deep_merge_dicts(existing_val, patch_data)
        else:
            merged_val = patch_data

        val_str = _serialize_value(merged_val)
        conn.execute(
            """
            INSERT INTO user_state (key, value, updated_at)
            VALUES (?, ?, unixepoch('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, val_str),
        )
        conn.commit()

    return {"status": "success", "value": merged_val}


@router.delete("/{key}")
def delete_state_key(key: str) -> Dict[str, str]:
    """Deletes a specific state key from SQLite."""
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM user_state WHERE key = ?", (key,))
        conn.commit()
    return {"status": "success"}