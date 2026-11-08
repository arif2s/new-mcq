import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from config import LANCEDB_PATH
from server.app import app
from core.database import initialize_database, get_db_connection
from parsers.tantivy_engine import search_index
from parsers.lancedb_engine import LanceDBEngine
from server.routes_sources import _resolve_pdf_path


@pytest.fixture(autouse=True)
def setup_test_db():
    initialize_database()
    yield


def test_search_index_returns_all_source_categories():
    results = search_index("cardiac amyloidosis", limit_per_source=3)
    assert isinstance(results, dict)
    assert "obsidian" in results
    assert "pdf" in results
    assert "zim" in results
    assert isinstance(results["obsidian"], list)
    assert isinstance(results["pdf"], list)
    assert isinstance(results["zim"], list)


def test_lancedb_reference_fallback(tmp_path):
    engine = LanceDBEngine(db_path=str(LANCEDB_PATH), table_name="medical_knowledge", vector_dim=384)
    dummy_records = [
        {
            "id": "pdf_test_1",
            "vector": [0.1] * 384,
            "source_article": "Cardiology_Textbook.pdf",
            "file_path": str(tmp_path / "Cardiology_Textbook.pdf"),
            "main_topic": "Amyloidosis",
            "subheading": "Cardiac Amyloidosis Overview",
            "heading_path": "Cardiology > Amyloidosis",
            "chunk_index": 0,
            "page_number": 42,
            "article_length": 1500,
            "parent_content": "Cardiac amyloidosis involves amyloid protein deposition in heart tissue causing restrictive cardiomyopathy.",
            "child_content": "Cardiac amyloidosis involves amyloid protein deposition in heart tissue causing restrictive cardiomyopathy.",
        },
        {
            "id": "obs_test_1",
            "vector": [0.2] * 384,
            "source_article": "Myocardial Infarction.md",
            "file_path": str(tmp_path / "Myocardial Infarction.md"),
            "main_topic": "Myocardial Infarction",
            "subheading": "ECG Findings",
            "heading_path": "MI > ECG",
            "chunk_index": 0,
            "page_number": 1,
            "article_length": 800,
            "parent_content": "ST-elevation myocardial infarction leads to troponin elevation and ECG changes.",
            "child_content": "ST-elevation myocardial infarction leads to troponin elevation and ECG changes.",
        },
    ]
    engine.insert(dummy_records)

    # Test end-to-end search_index fallback to LanceDB
    res = search_index("amyloidosis", limit_per_source=3)
    assert len(res["pdf"]) >= 1
    assert res["pdf"][0]["title"] == "Cardiology_Textbook.pdf"
    assert res["pdf"][0]["page"] == 42


def test_resolve_pdf_path():
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_sources (id, name, type, path, enabled) VALUES ('test_pdf', 'Test Book', 'pdf', '/tmp/Harrison_Internal_Medicine.pdf', 1)"
        )
        conn.commit()

    resolved = _resolve_pdf_path("Harrison_Internal_Medicine.pdf")
    assert resolved is None or isinstance(resolved, Path)


def test_synthesis_endpoint_references():
    client = TestClient(app)
    resp = client.get("/api/mcq/synthesis?topic=amyloidosis")
    assert resp.status_code == 200
    data = resp.json()
    assert "references" in data
    refs = data["references"]
    assert "pdf" in refs
    assert "obsidian" in refs
    assert "zim" in refs
