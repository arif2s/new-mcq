import json
import os
import re
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest
from fastapi.testclient import TestClient

from server.app import app
from core.database import initialize_database, get_db_connection

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM csv_questions WHERE id LIKE 'test_%'")
        conn.execute("DELETE FROM session_questions WHERE question_id LIKE 'test_%'")
        conn.execute("DELETE FROM quiz_sessions WHERE id LIKE 'test_%' OR id LIKE 'sess_%'")
        conn.commit()
    yield
    with get_db_connection() as conn:
        conn.execute("DELETE FROM csv_questions WHERE id LIKE 'test_%'")
        conn.execute("DELETE FROM session_questions WHERE question_id LIKE 'test_%'")
        conn.execute("DELETE FROM quiz_sessions WHERE id LIKE 'test_%' OR id LIKE 'sess_%'")
        conn.commit()


@pytest.fixture
def client():
    return TestClient(app)


def test_knowledge_hub_html_dom_structure():
    """Verify ui/index.html contains unified #hub-grid and #hub-empty without wasted space."""
    html_text = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")

    assert 'id="hub-main-container"' in html_text, "Missing #hub-main-container in ui/index.html"
    assert 'id="hub-loading"' in html_text, "Missing #hub-loading in ui/index.html"
    assert 'id="hub-empty"' in html_text, "Missing #hub-empty in ui/index.html"
    assert 'id="hub-grid"' in html_text, "Missing #hub-grid in ui/index.html"


def test_knowledge_hub_js_unified_grid_rendering():
    """Verify ui/js/hub.js renders due review set in the same group as other cards in #hub-grid."""
    hub_js = (ROOT_DIR / "ui" / "js" / "hub.js").read_text(encoding="utf-8")

    # Verify unified rendering in #hub-grid
    assert 'CONSOLIDATED_REVIEWS_SESSION_ID = "sess_spaced_repetition_due"' in hub_js
    assert 'createSessionCardNode' in hub_js
    assert 'hasDueCard' in hub_js
    assert 'totalActiveCards === 0' in hub_js
    assert 'gridEl.appendChild(dueCard)' in hub_js

    # Verify empty box is shown only when all active cards are 0
    assert 'if (totalActiveCards === 0)' in hub_js
    assert 'emptyEl.style.display = \'block\'' in hub_js

    # Verify Due session is not selectable for bulk delete
    assert 'sessionId === CONSOLIDATED_REVIEWS_SESSION_ID' in hub_js


def test_auto_removal_when_questions_revisited(client):
    """
    Verify that when questions are revisited and completed, the due & wrong review set
    is properly removed from Knowledge Hub before the no questions in hub message appears.
    """
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_state (key, value) VALUES ('app_persistence', ?)",
            (json.dumps({"auto_remove_completed_sets": True}),),
        )
        test_qid = "test_q_kh_retention_1"
        conn.execute("DELETE FROM csv_questions WHERE id = ?", (test_qid,))
        conn.execute("DELETE FROM session_questions WHERE question_id = ?", (test_qid,))
        conn.execute("DELETE FROM quiz_sessions WHERE id IN (?, ?)", ("sess_normal_study_1", "sess_spaced_repetition_due"))
        conn.execute(
            """
            INSERT INTO csv_questions (id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, state, due_timestamp)
            VALUES (?, 'Test question', 'A', 'B', 'C', 'D', 'A', 0, 0)
            """,
            (test_qid,),
        )
        conn.commit()

    # User attempts question in study set and answers incorrectly
    save_res = client.post(
        "/api/sessions/save",
        json={
            "id": "sess_normal_study_1",
            "subjectName": "Neurology",
            "totalQuestions": 1,
            "questions": [{"id": test_qid, "question": "Test question"}],
            "answers": [{"questionId": test_qid, "selected": "B", "isCorrect": False}],
            "isCompleted": True,
        },
    )
    assert save_res.status_code == 200

    # Normal study set is completed, so it is auto-removed from history,
    # and sess_spaced_repetition_due appears with the due question
    hist_res = client.get("/api/sessions/history")
    assert hist_res.status_code == 200
    active_sids = [s["id"] for s in hist_res.json()]
    assert "sess_normal_study_1" not in active_sids
    assert "sess_spaced_repetition_due" in active_sids

    # Now user reviews/revisits the due set and answers correctly
    rev_res = client.post(
        "/api/sessions/save",
        json={
            "id": "sess_spaced_repetition_due",
            "subjectName": "Spaced Repetition & Wrong Qs",
            "totalQuestions": 1,
            "questions": [{"id": test_qid, "question": "Test question"}],
            "answers": [{"questionId": test_qid, "selected": "A", "isCorrect": True}],
            "isCompleted": True,
        },
    )
    assert rev_res.status_code == 200

    # With questions revisited and completed, sess_spaced_repetition_due is cleanly removed
    hist_res2 = client.get("/api/sessions/history")
    active_sids2 = [s["id"] for s in hist_res2.json()]
    assert "sess_spaced_repetition_due" not in active_sids2
    # All sets are now empty, so total sessions returned is 0
    assert len(active_sids2) == 0

    # Clean up
    with get_db_connection() as conn:
        conn.execute("DELETE FROM csv_questions WHERE id = ?", (test_qid,))
        conn.execute("DELETE FROM session_questions WHERE question_id = ?", (test_qid,))
        conn.commit()


def test_delete_sessions_protects_due_session(client):
    """Verify POST /api/sessions/delete does not delete sess_spaced_repetition_due."""
    del_res = client.post(
        "/api/sessions/delete",
        json={"session_ids": ["sess_spaced_repetition_due"]},
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 0
