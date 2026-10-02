from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any
import json
import time
from core.database import get_db_connection

router = APIRouter(prefix="/api/state", tags=["state"])

# Enforce stricter typing for the payload
class StateUpdate(BaseModel):
    key: str
    value: Dict[str, Any]

@router.get("/{key}")
def get_state(key: str):
    row = None

    # 1. Scope the database lock strictly to the read operation
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = ?", (key,))
        row = cursor.fetchone()

    # 2. Execute JSON parsing outside of the database connection context
    if row:
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            pass

    return {}

@router.post("/")
def save_state(state: StateUpdate):
    # 3. Process serialization and time computation before acquiring the DB lock
    state_json = json.dumps(state.value)
    current_time = int(time.time())

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 4. Use the Python timestamp rather than forcing SQLite to compute unixepoch('now')
        cursor.execute("""
            INSERT OR REPLACE INTO user_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (state.key, state_json, current_time))
        conn.commit()

    return {"status": "success"}

@router.get("/")
def get_all_state():
    result = {}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_state")

        # 5. Stream rows directly from the SQLite cursor instead of using fetchall()
        for row in cursor:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue

    return result
