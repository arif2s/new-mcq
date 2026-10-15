import time
import json
from typing import Union, Optional, Dict
from contextlib import closing
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.database import get_db_connection
from core.scheduler import calculate_fsrs_next_interval
from core.queue_manager import is_llm_processing_enabled
from agents.tema_q.topic_extractor import extract_topics_local
from parsers.tantivy_engine import search_index

router = APIRouter(prefix="/api/mcq", tags=["mcq"])


class TopicExtractionPayload(BaseModel):
    mcq_text: str
    options: Optional[Dict[str, str]] = None
    explanation: str = ""
    use_zim_refinement: bool = False
    use_tantivy_refinement: bool = False


@router.post("/topics/extract")
def extract_mcq_topics(payload: TopicExtractionPayload):
    """
    Extracts canonical medical entities from an MCQ stem, options, and explanation.
    Classifies topics into verified (black text, pre-selected) and doubtful (grey text).
    """
    strategy = "medspacy"
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        if row:
            try:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                strategy = app_state.get("extractorStrategy", "medspacy")
            except Exception:
                pass

    res = extract_topics_local(
        question=payload.mcq_text,
        options=payload.options or {},
        explanation=payload.explanation,
        strategy=strategy,
        use_zim_refinement=payload.use_zim_refinement,
        use_tantivy_refinement=payload.use_tantivy_refinement,
    )
    topics = [t for t in res.get("all_topics", []) if t != "General Medical Concept"]
    return {
        "status": "success",
        "main_topic": res.get("main_topic", "General Medical Concept"),
        "topics": topics,
        "topic_details": res.get("topic_details", []),
    }


class ScorePayload(BaseModel):
    mcq_id: Union[int, str]
    rating: int = Field(ge=1, le=4, description="1=Again, 2=Hard, 3=Good, 4=Easy")


@router.post("/score")
def score_question(payload: ScorePayload):
    """
    Calculates the next FSRS v4.5 review interval and updates either csv_questions (string ID)
    or mcq_bank (integer ID).
    """
    mcq_id_str = str(payload.mcq_id).strip()
    if not mcq_id_str:
        raise HTTPException(status_code=400, detail="mcq_id is required")

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        # 1. Check csv_questions first (primary question table for CSV sets)
        cursor.execute(
            "SELECT state, difficulty, stability, reps, lapses FROM csv_questions WHERE id = ?",
            (mcq_id_str,),
        )
        card = cursor.fetchone()
        target_table = "csv_questions"
        lookup_id: Union[str, int] = mcq_id_str

        # 2. Fallback to mcq_bank if numeric ID
        if not card and mcq_id_str.isdigit():
            lookup_id = int(mcq_id_str)
            cursor.execute(
                "SELECT state, difficulty, stability, reps, lapses FROM mcq_bank WHERE id = ?",
                (lookup_id,),
            )
            card = cursor.fetchone()
            target_table = "mcq_bank"

        if not card:
            raise HTTPException(status_code=404, detail=f"MCQ '{mcq_id_str}' not found")

        new_stats = calculate_fsrs_next_interval(
            rating=payload.rating,
            state=card["state"] or 0,
            difficulty=card["difficulty"] or 0.0,
            stability=card["stability"] or 0.0,
            reps=card["reps"] or 0,
            lapses=card["lapses"] or 0,
        )

        current_time = int(time.time())
        due_timestamp = current_time + new_stats["interval_seconds"]

        cursor.execute(
            f"""
            UPDATE {target_table}
            SET state = ?, difficulty = ?, stability = ?, reps = ?, lapses = ?,
                last_review = ?, due_timestamp = ?
            WHERE id = ?
            """,
            (
                new_stats["state"],
                new_stats["difficulty"],
                new_stats["stability"],
                new_stats["reps"],
                new_stats["lapses"],
                current_time,
                due_timestamp,
                lookup_id,
            ),
        )
        conn.commit()

    return {
        "status": "success",
        "mcq_id": mcq_id_str,
        "next_due": new_stats["interval_seconds"],
        "due_timestamp": due_timestamp,
        "fsrs": new_stats,
    }


@router.get("/synthesis")
def get_synthesis(topic: str, resolve_if_missing: bool = True):
    """
    Fetches the cached Tema_Q synthesis and reference links for the Right Sidebar Inspector.
    If LLM processing is disabled or the topic has not been indexed yet, resolves reference
    links on the fly so citations are always available for active quiz questions.
    """
    if not topic or not topic.strip():
        raise HTTPException(status_code=400, detail="Topic parameter is required")

    clean_topic = topic.strip()
    topic_key = clean_topic.lower().replace(" ", "-")
    llm_enabled = is_llm_processing_enabled()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT display_title, enhanced_explanation, distractor_analysis, unified_article, top_references
            FROM cached_syntheses
            WHERE topic_key = ?
            """,
            (topic_key,),
        )
        row = cursor.fetchone()

        references = {}
        if row and row["top_references"]:
            try:
                references = json.loads(row["top_references"])
            except json.JSONDecodeError:
                references = {}

        # On-the-fly reference resolution if missing
        if (not row or not any(references.values())) and resolve_if_missing:
            try:
                references = search_index(clean_topic, limit_per_source=3)
                has_obs = 1 if references.get("obsidian") else 0
                has_pdf = 1 if references.get("pdf") else 0
                has_zim = 1 if references.get("zim") else 0
                cursor.execute(
                    """
                    INSERT INTO cached_syntheses (
                        topic_key, display_title, enhanced_explanation, distractor_analysis,
                        unified_article, top_references, has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                    ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, unixepoch('now'))
                    ON CONFLICT(topic_key) DO UPDATE SET
                        top_references = excluded.top_references,
                        has_obsidian_note = excluded.has_obsidian_note,
                        has_pdf_match = excluded.has_pdf_match,
                        has_zim_match = excluded.has_zim_match,
                        updated_at = unixepoch('now')
                    """,
                    (topic_key, clean_topic, json.dumps(references), has_obs, has_pdf, has_zim),
                )
                conn.commit()
            except Exception:
                pass

        if not row and not references:
            raise HTTPException(status_code=404, detail="Synthesis and references not found for this topic.")

        return {
            "topic": row["display_title"] if row else clean_topic,
            "llm_enabled": llm_enabled,
            "enhanced_explanation": (row["enhanced_explanation"] or "") if (row and llm_enabled) else "",
            "distractor_analysis": (row["distractor_analysis"] or "") if (row and llm_enabled) else "",
            "unified_article": (row["unified_article"] or "") if (row and llm_enabled) else "",
            "references": references,
        }
