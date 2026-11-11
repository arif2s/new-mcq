# File: tests/unit/test_note_topic_dedup_and_highlights.py
from __future__ import annotations

import concurrent.futures
import functools
import json
import os
import re
import tempfile
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest
from core.topic_filter import (
    canonicalize_topic_norm,
    deduplicate_note_topics,
    filter_topics,
    is_valid_topic,
)
from core.database import initialize_database, get_db_connection


def with_timeout(seconds: float = 5.0):
    """Enforces a hard per-test execution timeout without external plugins."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fn, *args, **kwargs)
                return future.result(timeout=seconds)
        return wrapper
    return decorator


@with_timeout(5.0)
def test_canonicalize_and_deduplicate_surface_variants():
    raw_candidates = [
        {"name": "Amyloidosis", "cui": "C0002726"},
        {"name": "amyloidosis", "cui": "C0002726"},
        {"name": "Amyloidoses", "cui": ""},
        {"name": "The Amyloidosis", "cui": ""},
        {"name": "Cardiac amyloidosis", "cui": "C0268381"},
        {"name": "cardiac amyloidoses", "cui": ""},
    ]
    deduped = deduplicate_note_topics(raw_candidates)
    norms = [d["topic_norm"] for d in deduped]
    assert norms == ["amyloidosis", "cardiac amyloidosis"]
    assert "Amyloidoses" in deduped[0]["surface_variants"]
    assert "amyloidosis" in deduped[0]["surface_variants"]


@with_timeout(5.0)
def test_ignore_list_and_suppressed_topics_filtering():
    raw_candidates = [
        "Amyloidosis",
        "Multiple Myeloma",
        "patient",        # clinical stopword / Ignore_list.txt
        "treatment",      # clinical stopword
        "Sarcoidosis",
    ]
    ignored = {"multiple myeloma"}
    suppressed = {"sarcoidoses"}  # plural form in suppressed list must still block "Sarcoidosis"

    deduped = deduplicate_note_topics(
        raw_candidates,
        ignored_norms=ignored,
        suppressed_norms=suppressed,
    )
    norms = [d["topic_norm"] for d in deduped]
    assert norms == ["amyloidosis"]
    assert not is_valid_topic("Multiple Myeloma", extra_ignored=ignored)


@with_timeout(5.0)
def test_cui_synonym_collapse():
    raw_candidates = [
        {"name": "Renal failure", "cui": "C0035078"},
        {"name": "Kidney insufficiency", "cui": "C0035078"},
    ]
    deduped = deduplicate_note_topics(raw_candidates)
    assert len(deduped) == 1
    assert deduped[0]["cui"] == "C0035078"
    variants_lower = {v.lower() for v in deduped[0]["surface_variants"]}
    assert "renal failure" in variants_lower
    assert "kidney insufficiency" in variants_lower


@with_timeout(5.0)
def test_notes_js_contains_multi_mention_unwrapper():
    """Static contract verification that ui/js/notes.js implements multi-mention highlight unwrapping."""
    notes_js_path = Path(__file__).resolve().parent.parent.parent / "ui" / "js" / "notes.js"
    content = notes_js_path.read_text(encoding="utf-8")
    assert "note-topic-highlight" in content
    assert "data-topic-norm" in content or "dataset.topicNorm" in content
    assert "replaceWith" in content
    assert "canonicalizeTopicNormJS" in content
    assert "highlightTopicsInReadingContainer" in content
    assert "unwrapTopicHighlightsInDOM" in content


@with_timeout(5.0)
def test_routes_notes_topic_dedup_and_suppression_workflow():
    """Exercises note creation, topic extraction/dedup, deletion, suppression and re-extraction."""
    import asyncio
    from server.routes_notes import (
        upsert_lecture_note,
        get_single_lecture_note,
        delete_lecture_note_item,
        delete_lecture_note_topic,
        LectureNoteMetaPayload,
        _mine_and_populate_note_items_sync,
    )

    initialize_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM user_lecture_note_items")
        conn.execute("DELETE FROM user_lecture_notes")

    # 1. Create a note with markdown containing duplicate surface variants
    note_payload = LectureNoteMetaPayload(
        title="Amyloidosis Study",
        note_mode="notes",
        content_markdown="Amyloidosis is systemic. Primary amyloidosis and amyloidoses affect the heart.",
    )

    async def _run():
        res = await upsert_lecture_note(note_payload)
        note_id = res["note"]["note_id"]

        # Run extraction synchronously to verify database state
        _mine_and_populate_note_items_sync(note_id)

        # 2. Assert amyloidosis appears only once in user_lecture_note_items (distinct from primary amyloidosis)
        note_data = await get_single_lecture_note(note_id)
        items = note_data["items"]
        exact_amy_items = [it for it in items if it["topic_norm"] == "amyloidosis"]
        assert len(exact_amy_items) == 1
        amy_item = exact_amy_items[0]
        assert amy_item["topic_norm"] == "amyloidosis"

        # 3. Call the topic delete/remove endpoint for Amyloidosis
        del_res = await delete_lecture_note_item(note_id, amy_item["item_id"])
        assert del_res["status"] == "ok"
        assert del_res["deleted"] is True
        assert del_res["removed_topic_norm"] == "amyloidosis"

        # Verify amyloidosis is removed from items and persisted in suppressed_topics_json
        note_after_del = await get_single_lecture_note(note_id)
        assert len([it for it in note_after_del["items"] if it["topic_norm"] == "amyloidosis"]) == 0
        assert "amyloidosis" in note_after_del["note"]["suppressed_topics"]

        # Re-populating topics must not re-insert amyloidosis
        _mine_and_populate_note_items_sync(note_id)
        note_repopulated = await get_single_lecture_note(note_id)
        assert len([it for it in note_repopulated["items"] if it["topic_norm"] == "amyloidosis"]) == 0

    asyncio.run(_run())
