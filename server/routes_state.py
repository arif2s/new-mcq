from fastapi import APIRouter
from pydantic import BaseModel
import json
from core.database import get_db_connection

router = APIRouter(prefix="/api/state", tags=["state"])

class StateUpdate(BaseModel):
    key: str
    value: dict

@router.get("/{key}")
def get_state(key: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row["value"])
            except json.JSONDecodeError:
                return {}
        return {}

@router.post("/")
def save_state(state: StateUpdate):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_state (key, value, updated_at)
            VALUES (?, ?, unixepoch('now'))
        """, (state.key, json.dumps(state.value)))
        conn.commit()
    return {"status": "success"}

@router.get("/")
def get_all_state():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_state")
        rows = cursor.fetchall()
        result = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
        return result
