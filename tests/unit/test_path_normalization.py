import sqlite3
import pytest
from pathlib import Path
from core.database import (
    normalize_db_file_path,
    upsert_layout_analysis_progress,
    upsert_user_annotated_bbox,
)
from parsers.pdf_topic_indexer import lookup_pdf_topic_pages


def test_normalize_db_file_path_edge_cases():
    assert normalize_db_file_path("") == ""
    assert normalize_db_file_path(None) == ""
    assert normalize_db_file_path("   ") == ""

    # Path invariant checks
    p1 = "subfolder/../Book.pdf"
    p2 = "Book.pdf"
    assert normalize_db_file_path(p1) == normalize_db_file_path(p2)

    # Backslash / forward slash invariance
    p_slash = "folder/file.pdf"
    p_backslash = "folder\\file.pdf"
    assert normalize_db_file_path(p_slash) == normalize_db_file_path(p_backslash)
    assert "\\" not in normalize_db_file_path(p_backslash)


def test_path_fk_normalization_offline(tmp_path):
    db_path = tmp_path / "fk_test.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript("""
        CREATE TABLE layout_analysis_progress (
            file_path TEXT PRIMARY KEY, status TEXT, total_pages INTEGER,
            reviewed_pages_json TEXT, active_profile TEXT,
            final_markdown_override TEXT, updated_at REAL
        );
        CREATE TABLE user_annotated_bboxes (
            id TEXT PRIMARY KEY, file_path TEXT NOT NULL, page_number INTEGER NOT NULL,
            x0 REAL, y0 REAL, x1 REAL, y1 REAL, element_type TEXT NOT NULL,
            heading_level INTEGER DEFAULT 0, display_order INTEGER NOT NULL,
            raw_text TEXT DEFAULT '', is_custom_drawn INTEGER DEFAULT 0, updated_at REAL,
            FOREIGN KEY(file_path) REFERENCES layout_analysis_progress(file_path) ON DELETE CASCADE
        );
    """)

    p1 = str(tmp_path / "subfolder" / ".." / "Book.pdf")
    p2 = str(tmp_path / "Book.pdf").replace("/", "\\")
    assert normalize_db_file_path(p1) == normalize_db_file_path(p2)

    # Upsert parent with p1
    upsert_layout_analysis_progress(p1, status="in_progress", conn=conn)

    # Upsert child with p2 (windows-style / alternate representation)
    upsert_user_annotated_bbox("b1", p2, 1, 0.1, 0.1, 0.5, 0.5, "heading", conn=conn)
    row = conn.execute("SELECT file_path FROM user_annotated_bboxes WHERE id = 'b1'").fetchone()
    assert row["file_path"] == normalize_db_file_path(p1)

    # Check child bbox inserted without prior parent call automatically creates parent row
    p_orphan = str(tmp_path / "auto_parent" / ".." / "Doc.pdf").replace("/", "\\")
    upsert_user_annotated_bbox("b2", p_orphan, 1, 0.2, 0.2, 0.8, 0.8, "text", conn=conn)
    parent_row = conn.execute(
        "SELECT file_path, status FROM layout_analysis_progress WHERE file_path = ?",
        (normalize_db_file_path(p_orphan),),
    ).fetchone()
    assert parent_row is not None
    assert parent_row["file_path"] == normalize_db_file_path(p_orphan)
    assert parent_row["status"] == "pending"


def test_pdf_topic_indexer_lookup_path_normalization(tmp_path):
    db_path = tmp_path / "pti_test.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pdf_topic_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pdf_path TEXT NOT NULL,
            pdf_title TEXT NOT NULL,
            engine_source TEXT NOT NULL DEFAULT 'hybrid_toc',
            heading_level INTEGER NOT NULL,
            topic_title TEXT NOT NULL,
            topic_norm TEXT NOT NULL,
            parent_topic TEXT NOT NULL,
            parent_norm TEXT NOT NULL,
            heading_path TEXT NOT NULL,
            heading_path_norm TEXT NOT NULL,
            start_page INTEGER NOT NULL,
            end_page INTEGER NOT NULL,
            start_y0 REAL NOT NULL DEFAULT 0.0,
            end_y1 REAL NOT NULL DEFAULT 1.0,
            updated_at REAL DEFAULT (unixepoch('now'))
        );
    """)

    sample_pdf = str(tmp_path / "manuals" / ".." / "Harrison.pdf")
    norm_sample_pdf = normalize_db_file_path(sample_pdf)

    conn.execute(
        """
        INSERT INTO pdf_topic_index (
            pdf_path, pdf_title, engine_source, heading_level,
            topic_title, topic_norm, parent_topic, parent_norm,
            heading_path, heading_path_norm, start_page, end_page, start_y0, end_y1
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            norm_sample_pdf, "Harrison Internal Medicine", "hybrid_toc", 1,
            "Cardiology", "cardiology", "Medicine", "medicine",
            "Medicine > Cardiology", "medicine cardiology", 20, 25, 0.0, 1.0,
        ),
    )

    # Verify lookup with mock connection or against db
    from unittest.mock import patch
    def get_conn():
        c = sqlite3.connect(str(db_path), isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    with patch("parsers.pdf_topic_indexer.get_db_connection", side_effect=get_conn):
        # Query with unnormalized windows backslashes
        query_win_path = str(tmp_path / "Harrison.pdf").replace("/", "\\")
        results = lookup_pdf_topic_pages("Cardiology", pdf_path=query_win_path)
        assert len(results) == 1
        assert results[0]["pdf_path"] == norm_sample_pdf
        assert results[0]["topic_title"] == "Cardiology"

        # Query with non-matching path
        query_other_path = str(tmp_path / "OtherBook.pdf")
        results_other = lookup_pdf_topic_pages("Cardiology", pdf_path=query_other_path)
        assert len(results_other) == 0
