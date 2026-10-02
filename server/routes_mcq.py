from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import time
from core.database import get_db_connection
from core.scheduler import calculate_fsrs_next_interval

router = APIRouter(prefix="/api/mcq", tags=["mcq"])

class ScorePayload(BaseModel):
    mcq_id: int
    # 1. Enforce strict rating bounds at the API boundary to prevent FSRS lookup crashes
    rating: int = Field(ge=1, le=4, description="1=Again, 2=Hard, 3=Good, 4=Easy")

@router.post("/score")
def score_question(payload: ScorePayload):
    """Calculates the next due date using FSRS and updates the MCQ bank."""

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT state, difficulty, stability, reps, lapses FROM mcq_bank WHERE id = ?",
            (payload.mcq_id,)
        )
        card = cursor.fetchone()

        if not card:
            raise HTTPException(status_code=404, detail="MCQ not found")

        new_stats = calculate_fsrs_next_interval(
            rating=payload.rating,
            state=card["state"],
            difficulty=card["difficulty"],
            stability=card["stability"],
            reps=card["reps"],
            lapses=card["lapses"]
        )

        # 2. Compute the timestamp exactly once as an integer
        current_time = int(time.time())
        due_timestamp = current_time + new_stats["interval_seconds"]

        cursor.execute("""
            UPDATE mcq_bank
            SET state=?, difficulty=?, stability=?, reps=?, lapses=?,
                last_review=?, due_timestamp=?
            WHERE id=?
        """, (
            new_stats["state"],
            new_stats["difficulty"],
            new_stats["stability"],
            new_stats["reps"],
            new_stats["lapses"],
            current_time,
            due_timestamp,
            payload.mcq_id
        ))
        conn.commit()

    # 3. Return the response outside the 'with' block to release the DB lock immediately
    return {"status": "success", "next_due": new_stats["interval_seconds"]}
