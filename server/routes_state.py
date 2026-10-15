import json
import time
from typing import Dict, Any
from contextlib import closing
from fastapi import APIRouter
from pydantic import BaseModel

from config import VENDOR_DIR, VENDOR_ASSETS
from core.database import get_db_connection

router = APIRouter(prefix="/api/state", tags=["state"])


class StateUpdate(BaseModel):
    key: str
    value: Dict[str, Any]


@router.get("/vendor_status")
def get_vendor_status() -> Dict[str, Any]:
    """
    Reports whether UI vendor dependencies (Tailwind, Marked, DOMPurify, KaTeX)
    are cached locally in ui/vendor/ for 100% offline operation.
    """
    assets = {}
    all_cached = True
    for filename in VENDOR_ASSETS.keys():
        p = VENDOR_DIR / filename
        is_ok = p.exists() and p.is_file() and p.stat().st_size > 128
        assets[filename] = is_ok
        if not is_ok:
            all_cached = False
    return {
        "status": "success",
        "offline_ready": all_cached,
        "assets": assets,
    }


@router.get("/{key}")
def get_state(key: str) -> Dict[str, Any]:
    row = None
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = ?", (key,))
        row = cursor.fetchone()

    if row:
        try:
            raw_val = row[0] if isinstance(row, tuple) else row["value"]
            return json.loads(raw_val)
        except json.JSONDecodeError:
            pass

    return {}


@router.post("")
@router.post("/")
def save_state(state: StateUpdate) -> Dict[str, str]:
    state_json = json.dumps(state.value)
    current_time = int(time.time())

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO user_state (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (state.key, state_json, current_time),
        )
        conn.commit()

    return {"status": "success"}


@router.get("")
@router.get("/")
def get_all_state() -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_state")
        for row in cursor:
            try:
                k = row[0] if isinstance(row, tuple) else row["key"]
                v = row[1] if isinstance(row, tuple) else row["value"]
                result[k] = json.loads(v)
            except json.JSONDecodeError:
                continue

    return result
