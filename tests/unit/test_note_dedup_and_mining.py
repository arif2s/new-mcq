import pytest
import asyncio
from unittest.mock import patch
from core.topic_filter import canonicalize_topic_norm, deduplicate_note_topics, normalize_and_deduplicate_note_topics
from core.database import initialize_database, get_db_connection
from server.routes_notes import (
    upsert_lecture_note,
    get_single_lecture_note,
    delete_lecture_note_item,
    delete_lecture_note_topic,
    LectureNoteMetaPayload,
    LectureNoteItemPayload,
    _NOTE_MINING_STATE,
)

@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM user_lecture_note_items")
        conn.execute("DELETE FROM user_lecture_notes")
    yield
    with get_db_connection() as conn:
        conn.execute("DELETE FROM user_lecture_note_items")
        conn.execute("DELETE FROM user_lecture_notes")

def test_canonicalize_topic_norm_singularization():
    assert canonicalize_topic_norm("Amyloidosis") == "amyloidosis"
    assert canonicalize_topic_norm("amyloidosis") == "amyloidosis"
    assert canonicalize_topic_norm("Amyloidoses") == "amyloidosis"
    assert canonicalize_topic_norm("Biopsies") == "biopsy"
    assert canonicalize_topic_norm("Kidneys") == "kidney"
    assert canonicalize_topic_norm("Ulcers") == "ulcer"
    assert canonicalize_topic_norm("The Amyloidosis") == "amyloidosis"

def test_deduplicate_note_topics_variants_and_stopwords():
    candidates = [
        "Amyloidosis",
        "amyloidosis",
        "Amyloidoses",
        "patient",       # Clinical stopword
        "heart",         # Isolated generic word
        "Heart failure", # Valid clinical concept
        "Kidney failure" # Valid clinical concept
    ]
    ignored = {"kidney failure"}
    suppressed = set()
    deduped = normalize_and_deduplicate_note_topics(candidates, ignored, suppressed)

    names = [d["topic_norm"] for d in deduped]
    assert "amyloidosis" in names
    assert "heart failure" in names
    assert "kidney failure" not in names
    assert "patient" not in names
    assert "heart" not in names

    # Check surface variants collection
    amy_item = next(d for d in deduped if d["topic_norm"] == "amyloidosis")
    assert set(amy_item["surface_variants"]) == {"Amyloidosis", "amyloidosis", "Amyloidoses"}

@pytest.mark.asyncio
async def test_non_blocking_note_save_and_mining_status():
    payload = LectureNoteMetaPayload(
        title="Clinical Nephrology",
        note_mode="notes",
        content_markdown="# Renal\nPatient with [[Amyloidosis]] and [[amyloidoses]]."
    )
    # Save should immediately return HTTP 200 with mining_status == 'populating'
    save_res = await upsert_lecture_note(payload)
    assert save_res["status"] == "success"
    assert save_res["mining_status"] == "populating"
    note_id = save_res["note"]["note_id"]

    # Wait for background task to complete
    await asyncio.sleep(0.4)
    refreshed = await get_single_lecture_note(note_id)
    assert refreshed["mining_status"] == "ready"
    # Deduplication check: Amyloidosis should appear at most once
    items = refreshed["items"]
    amy_items = [it for it in items if it["topic_key"] == "amyloidosis" or "amyloid" in it["topic_text"].lower()]
    assert len(amy_items) == 1

@pytest.mark.asyncio
async def test_topic_deletion_and_suppression():
    payload = LectureNoteMetaPayload(
        title="Delete Test Note",
        note_mode="notes",
        content_markdown="Patient diagnosed with [[Amyloidosis]]."
    )
    save_res = await upsert_lecture_note(payload)
    note_id = save_res["note"]["note_id"]
    await asyncio.sleep(0.4)

    refreshed = await get_single_lecture_note(note_id)
    assert len(refreshed["items"]) == 1
    item_id = refreshed["items"][0]["item_id"]

    # Delete via delete_lecture_note_item
    del_res = await delete_lecture_note_item(note_id, item_id)
    assert del_res["deleted"] is True

    # Check that topic is now in suppressed_topics
    refreshed2 = await get_single_lecture_note(note_id)
    assert len(refreshed2["items"]) == 0
    assert "amyloidosis" in refreshed2["note"]["suppressed_topics"]

    # Delete via delete_lecture_note_topic (by key/text)
    del_topic_res = await delete_lecture_note_topic(note_id, topic_key="heart-failure", topic_text="Heart Failure")
    refreshed3 = await get_single_lecture_note(note_id)
    assert "heart-failure" in refreshed3["note"]["suppressed_topics"]
    assert "heart failure" in refreshed3["note"]["suppressed_topics"]
