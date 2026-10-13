from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import time
import json
from core.database import get_db_connection
from core.scheduler import calculate_fsrs_next_interval
from agents.topic_extractor import extract_topics

router = APIRouter(prefix="/api/mcq", tags=["mcq"])

class TopicExtractionPayload(BaseModel):
    mcq_text: str
    use_zim_refinement: bool = False
    use_tantivy_refinement: bool = False

@router.post("/topics/extract")
def extract_mcq_topics(payload: TopicExtractionPayload):
    topics = extract_topics(
        text=payload.mcq_text,
        use_zim_refinement=payload.use_zim_refinement,
        use_tantivy_refinement=payload.use_tantivy_refinement
    )
    return {"topics": topics}

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

@router.get("/synthesis")
def get_synthesis(topic: str):
    """Fetches the cached Tema_Q RAG synthesis and reference links for the Inspector Panel."""
    if not topic:
        raise HTTPException(status_code=400, detail="Topic parameter is required")

    topic_key = topic.lower().replace(" ", "-")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT enhanced_explanation, distractor_analysis, unified_article, top_references
            FROM cached_syntheses
            WHERE topic_key = ?
        """, (topic_key,))

        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Synthesis not found for this topic.")

        references = {}
        if row["top_references"]:
            try:
                references = json.loads(row["top_references"])
            except json.JSONDecodeError:
                pass

        return {
            "enhanced_explanation": row["enhanced_explanation"] or "",
            "distractor_analysis": row["distractor_analysis"] or "",
            "unified_article": row["unified_article"] or "",
            "references": references
        }
