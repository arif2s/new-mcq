"""
Tests for server/routes_analysis.py.
Verifies all 5 analysis endpoints:
- GET /api/analysis/files
- POST /api/analysis/initialize
- GET /api/analysis/page-data
- POST /api/analysis/save-page
- POST /api/analysis/save-markdown
"""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import pytest
from fastapi.testclient import TestClient
import pymupdf as fitz

from core.database import get_db_connection, initialize_database
from server.app import app


@pytest.fixture(scope="module")
def client():
    initialize_database()
    return TestClient(app)


@pytest.fixture
def sample_pdf():
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    doc = fitz.open()
    # 2 pages PDF
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 72), "Cardiovascular Assessment Chapter 1", fontsize=20)
    p1.insert_text((72, 120), "Hypertension is a major risk factor for cardiovascular disease.", fontsize=12)

    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 72), "Clinical Management Guidelines", fontsize=18)
    p2.insert_text((72, 120), "First-line agents include ACE inhibitors, ARBs, and CCBs.", fontsize=12)

    doc.save(str(tmp_path))
    doc.close()

    yield tmp_path

    try:
        tmp_path.unlink()
    except Exception:
        pass


def test_get_analysis_files(client, sample_pdf):
    # Add source to knowledge_sources
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_sources (id, name, type, path, enabled, itemCount, lastIndexed, pdfEngine)
            VALUES (?, ?, ?, ?, 1, 0, 'Never', 'indra')
            """,
            ("test_ks_pdf", "Test PDF", "pdf", str(sample_pdf)),
        )

    response = client.get("/api/analysis/files")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "files" in data
    found = any(f["file_path"].lower() == str(sample_pdf.resolve()).lower() for f in data["files"])
    assert found, f"File {sample_pdf} should be found in analysis files list"


def test_initialize_analysis(client, sample_pdf):
    payload = {
        "file_path": str(sample_pdf),
        "profile_id": "scanned_two_column_textbook",
    }
    response = client.post("/api/analysis/initialize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total_pages"] == 2
    assert data["profile_id"] == "scanned_two_column_textbook"

    # Verify database state
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT total_pages, active_profile, status FROM layout_analysis_progress WHERE file_path = ?",
            (str(sample_pdf.resolve()),),
        ).fetchone()
        assert row is not None
        assert row["total_pages"] == 2
        assert row["active_profile"] == "scanned_two_column_textbook"


def test_get_page_data_dynamic_extraction(client, sample_pdf):
    # First call: No user overrides, so it runs Indra dynamic extraction
    response = client.get(
        "/api/analysis/page-data",
        params={"file_path": str(sample_pdf), "page_number": 1},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["page_number"] == 1
    assert "dimensions" in data
    assert data["dimensions"]["width"] > 0
    assert data["dimensions"]["height"] > 0
    assert "elements" in data
    assert len(data["elements"]) > 0

    # Ensure elements were cached in user_annotated_bboxes
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM user_annotated_bboxes WHERE file_path = ? AND page_number = 1",
            (str(sample_pdf.resolve()),),
        ).fetchall()
        assert len(rows) == len(data["elements"])


def test_save_page_and_get_overrides(client, sample_pdf):
    # Custom annotated bounding boxes
    custom_boxes = [
        {
            "id": "box_1",
            "x0": 50.0,
            "y0": 50.0,
            "x1": 400.0,
            "y1": 100.0,
            "element_type": "header",
            "heading_level": 1,
            "display_order": 0,
            "raw_text": "Cardiovascular Assessment Chapter 1",
            "is_custom_drawn": 0,
        },
        {
            "id": "box_2",
            "x0": 50.0,
            "y0": 110.0,
            "x1": 500.0,
            "y1": 250.0,
            "element_type": "text",
            "heading_level": 0,
            "display_order": 1,
            "raw_text": "Hypertension is a major risk factor for cardiovascular disease.",
            "is_custom_drawn": 1,
        },
    ]

    save_payload = {
        "file_path": str(sample_pdf),
        "page_number": 1,
        "bboxes": custom_boxes,
    }
    save_resp = client.post("/api/analysis/save-page", json=save_payload)
    assert save_resp.status_code == 200
    save_data = save_resp.json()
    assert save_data["status"] == "success"
    assert save_data["saved_boxes_count"] == 2
    assert 1 in save_data["reviewed_pages"]

    # Now get_page_data should return source="user_annotated_bboxes"
    page_resp = client.get(
        "/api/analysis/page-data",
        params={"file_path": str(sample_pdf), "page_number": 1},
    )
    assert page_resp.status_code == 200
    page_data = page_resp.json()
    assert page_data["source"] == "user_annotated_bboxes"
    assert len(page_data["elements"]) == 2
    assert page_data["elements"][0]["id"] == "box_1"
    assert page_data["elements"][0]["display_order"] == 0
    assert page_data["elements"][1]["id"] == "box_2"
    assert page_data["elements"][1]["display_order"] == 1


def test_save_markdown_override(client, sample_pdf):
    markdown_content = "# Cardiovascular Assessment\n\nHypertension management guidelines."
    payload = {
        "file_path": str(sample_pdf),
        "markdown_text": markdown_content,
    }
    resp = client.post("/api/analysis/save-markdown", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["markdown_length"] == len(markdown_content)

    # Verify in DB
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT final_markdown_override, status FROM layout_analysis_progress WHERE file_path = ?",
            (str(sample_pdf.resolve()),),
        ).fetchone()
        assert row is not None
        assert row["final_markdown_override"] == markdown_content
        assert row["status"] == "completed"


def test_toc_update_heading_and_finalize(client, tmp_path):
    # Setup test file in layout_analysis_progress and user_annotated_bboxes
    test_pdf = tmp_path / "doc_test.pdf"
    test_pdf.write_bytes(b"%PDF-1.4 mock content")
    norm_path = str(test_pdf.resolve())

    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO layout_analysis_progress
            (file_path, status, total_pages, reviewed_pages_json, active_profile)
            VALUES (?, 'in_progress', 2, '[1, 2]', 'native_digital')
            """,
            (norm_path,),
        )
        conn.execute("DELETE FROM user_annotated_bboxes WHERE file_path = ?", (norm_path,))
        conn.execute(
            """
            INSERT INTO user_annotated_bboxes
            (id, file_path, page_number, x0, y0, x1, y1, element_type, heading_level, display_order, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"h1_{norm_path}", norm_path, 1, 0.1, 0.1, 0.9, 0.2, "heading", 1, 0, "Chapter 1: Intro"),
        )
        conn.execute(
            """
            INSERT INTO user_annotated_bboxes
            (id, file_path, page_number, x0, y0, x1, y1, element_type, heading_level, display_order, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"h2_{norm_path}", norm_path, 1, 0.1, 0.25, 0.9, 0.35, "heading", 2, 1, "Section 1.1: Background"),
        )
        conn.execute(
            """
            INSERT INTO user_annotated_bboxes
            (id, file_path, page_number, x0, y0, x1, y1, element_type, heading_level, display_order, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"p_{norm_path}", norm_path, 1, 0.1, 0.4, 0.9, 0.8, "text", 0, 2, "This is the body content."),
        )
        conn.commit()

    # 1. Test GET /api/analysis/toc
    resp = client.get(f"/api/analysis/toc?file_path={norm_path}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert len(data["headings"]) == 2
    h1 = data["headings"][0]
    h2 = data["headings"][1]
    assert h1["title"] == "Chapter 1: Intro"
    assert h1["heading_level"] == 1
    assert h1["parent_index"] is None
    assert h2["title"] == "Section 1.1: Background"
    assert h2["heading_level"] == 2
    assert h2["parent_index"] == 0
    assert h2["breadcrumb"] == "Chapter 1: Intro > Section 1.1: Background"

    # 2. Test POST /api/analysis/update-heading (promote h2 to h1)
    resp2 = client.post("/api/analysis/update-heading", json={"id": f"h2_{norm_path}", "heading_level": 1})
    assert resp2.status_code == 200
    assert resp2.json()["heading_level"] == 1

    # Verify updated TOC
    resp3 = client.get(f"/api/analysis/toc?file_path={norm_path}")
    data3 = resp3.json()
    assert data3["headings"][1]["heading_level"] == 1
    assert data3["headings"][1]["parent_index"] is None

    # 3. Test POST /api/analysis/finalize
    resp4 = client.post("/api/analysis/finalize", json={
        "file_path": norm_path,
        "export_to_lancedb": False,
        "output_filename": "test_output.md"
    })
    assert resp4.status_code == 200
    res_fin = resp4.json()
    assert res_fin["status"] == "success"
    assert res_fin["analysis_status"] == "completed"
    assert Path(res_fin["markdown_file"]).exists()

    saved_md = Path(res_fin["markdown_file"]).read_text(encoding="utf-8")
    assert "# Chapter 1: Intro" in saved_md
    assert "# Section 1.1: Background" in saved_md
    assert "This is the body content." in saved_md

    # Check database status was updated to completed
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM layout_analysis_progress WHERE file_path = ?", (norm_path,))
        r = cursor.fetchone()
        assert r["status"] == "completed"
