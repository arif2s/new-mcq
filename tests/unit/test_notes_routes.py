import pytest
import asyncio
from core.database import initialize_database, get_db_connection
from server.routes_notes import (
    upsert_lecture_note,
    get_lecture_notes,
    get_single_lecture_note,
    delete_lecture_note,
    upsert_lecture_note_item,
    delete_lecture_note_item,
    LectureNoteMetaPayload,
    LectureNoteItemPayload,
    _build_safe_fts5_query,
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

def test_safe_fts5_query_sanitizer():
    assert _build_safe_fts5_query("") == ""
    assert _build_safe_fts5_query("a") == ""
    query = _build_safe_fts5_query("Cholesteatoma (Middle Ear)")
    assert '"Cholesteatoma"*' in query
    assert '"Middle"*' in query
    assert '"Ear"*' in query

@pytest.mark.asyncio
async def test_lecture_note_crud():
    # 1. Create Note
    create_res = await upsert_lecture_note(LectureNoteMetaPayload(title="ENT Day 1", shade_index=2, is_pinned=1))
    assert create_res["status"] == "success"
    note_id = create_res["note"]["note_id"]
    assert create_res["note"]["title"] == "ENT Day 1"
    assert create_res["note"]["shade_index"] == 2
    assert create_res["note"]["is_pinned"] == 1

    # 2. Add Locked Topic Lines
    item1 = await upsert_lecture_note_item(note_id, LectureNoteItemPayload(line_order=0, topic_text="Cholesteatoma"))
    assert item1["status"] == "success"
    assert item1["topic_text"] == "Cholesteatoma"
    assert item1["topic_key"] == "cholesteatoma"
    assert "references" in item1

    item2 = await upsert_lecture_note_item(note_id, LectureNoteItemPayload(line_order=1, topic_text="Otosclerosis"))
    assert item2["status"] == "success"
    assert item2["line_order"] == 1

    # 3. Retrieve Single Note
    single = await get_single_lecture_note(note_id)
    assert single["status"] == "success"
    assert single["note"]["title"] == "ENT Day 1"
    assert len(single["items"]) == 2
    assert single["items"][0]["topic_text"] == "Cholesteatoma"

    # 4. List Notes with Pagination and Preview
    listing = await get_lecture_notes(page=1, page_size=12, q="")
    assert listing["status"] == "success"
    assert listing["total_items"] == 1
    assert len(listing["notes"]) == 1
    note_card = listing["notes"][0]
    assert note_card["note_id"] == note_id
    assert note_card["total_topics"] == 2
    assert len(note_card["top_items"]) == 2

    # 5. Search filtering
    search_hit = await get_lecture_notes(page=1, page_size=12, q="Cholesteatoma")
    assert search_hit["total_items"] == 1
    search_miss = await get_lecture_notes(page=1, page_size=12, q="NonExistentTermXYZ")
    assert search_miss["total_items"] == 0

    # 6. Delete item
    del_item = await delete_lecture_note_item(note_id, item1["item_id"])
    assert del_item["deleted"] is True
    single_after_item_del = await get_single_lecture_note(note_id)
    assert len(single_after_item_del["items"]) == 1

    # 7. Delete Note
    del_note = await delete_lecture_note(note_id)
    assert del_note["deleted"] is True
    listing_empty = await get_lecture_notes(page=1, page_size=12, q="")
    assert listing_empty["total_items"] == 0

from unittest.mock import patch
from server.routes_notes import extract_topics_from_note_markdown, extract_and_sync_note_topics

def test_markdown_topic_extraction():
    sample_md = (
        "# Renal Pathology Notes\n"
        "Patient presents with [[Nephrotic syndrome]] and biopsy confirms [[Cardiac amyloidosis|amyloidosis]].\n"
        "Treat Acute pancreatitis aggressively with intravenous fluids."
    )
    with patch(
        "server.routes_notes.extract_topics_local",
        return_value={"all_topics": ["Acute pancreatitis", "Nephrotic syndrome"]},
    ):
        topics = extract_topics_from_note_markdown(sample_md)

    assert "Nephrotic syndrome" in topics
    assert "Cardiac amyloidosis" in topics
    assert "Acute pancreatitis" in topics

@pytest.mark.asyncio
async def test_note_mode_and_markdown_extraction_sync():
    create_res = await upsert_lecture_note(LectureNoteMetaPayload(
        title="Pathology Note",
        note_mode="notes",
        card_color="rgba(100, 150, 200, 0.28)",
        content_markdown="# Nephrology\nTreat [[Nephrotic syndrome]] promptly."
    ))
    assert create_res["status"] == "success"
    note = create_res["note"]
    assert note["note_mode"] == "notes"
    assert note["card_color"] == "rgba(100, 150, 200, 0.28)"
    assert note["content_markdown"] == "# Nephrology\nTreat [[Nephrotic syndrome]] promptly."
    # Extracted topics should include Nephrotic syndrome
    items = create_res["items"]
    assert any(it["topic_text"] == "Nephrotic syndrome" for it in items)


@pytest.mark.asyncio
async def test_ignore_list_and_suppressed_topics_deduplication():
    # Mock get_ignored_topics_set to include 'ignoredterm'
    with patch("server.routes_notes.get_ignored_topics_set", return_value={"ignoredterm"}):
        note_res = await upsert_lecture_note(LectureNoteMetaPayload(
            title="Ignore Test Note",
            note_mode="notes",
            content_markdown="Patient with [[Asthma]] and [[IgnoredTerm]] and [[Asthma]]."
        ))
        note_id = note_res["note"]["note_id"]

        synced = await extract_and_sync_note_topics(note_id, {"content_markdown": "Patient with [[Asthma]], [[ignoredterm]], [[Asthma]]."})
        items = synced["items"]
        topic_texts = [it["topic_text"].lower() for it in items]
        # 'ignoredterm' should be excluded
        assert "ignoredterm" not in topic_texts
        # 'asthma' should not have duplicates
        assert topic_texts.count("asthma") == 1

        # Delete 'asthma' item
        asthma_item = next(it for it in items if it["topic_text"].lower() == "asthma")
        del_res = await delete_lecture_note_item(note_id, asthma_item["item_id"])
        assert del_res["deleted"] is True

        # Check suppressed_topics on note
        refreshed = await get_single_lecture_note(note_id)
        suppressed = refreshed["note"].get("suppressed_topics", [])
        assert "asthma" in suppressed

        # Re-syncing should not re-add asthma because it is now suppressed for this note
        resynced = await extract_and_sync_note_topics(note_id, {"content_markdown": "Patient with [[Asthma]] again."})
        resynced_texts = [it["topic_text"].lower() for it in resynced["items"]]
        assert "asthma" not in resynced_texts
