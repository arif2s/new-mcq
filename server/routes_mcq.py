from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import time
from core.database import get_db_connection
from core.scheduler import calculate_fsrs_next_interval

router = APIRouter(prefix="/api/mcq", tags=["mcq"])

class ScorePayload(BaseModel):
    mcq_id: int
    rating: int  # 1=Again, 2=Hard, 3=Good, 4=Easy

@router.post("/score")
def score_question(payload: ScorePayload):
    """Calculates the next due date using FSRS and updates the MCQ bank."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT state, difficulty, stability, reps, lapses FROM mcq_bank WHERE id = ?", (payload.mcq_id,))
        card = cursor.fetchone()

        if not card:
            raise HTTPException(status_code=404, detail="MCQ not found")

        # FSRS integration (assuming calculate_fsrs_next_interval returns a dictionary of updated stats)
        new_stats = calculate_fsrs_next_interval(
            rating=payload.rating, state=card["state"], difficulty=card["difficulty"],
            stability=card["stability"], reps=card["reps"], lapses=card["lapses"]
        )

        cursor.execute("""
            UPDATE mcq_bank
            SET state=?, difficulty=?, stability=?, reps=?, lapses=?,
                last_review=?, due_timestamp=?
            WHERE id=?
        """, (
            new_stats["state"], new_stats["difficulty"], new_stats["stability"],
            new_stats["reps"], new_stats["lapses"], time.time(),
            time.time() + new_stats["interval_seconds"], payload.mcq_id
        ))
        conn.commit()
        return {"status": "success", "next_due": new_stats["interval_seconds"]}
