from fastapi.testclient import TestClient
from server.app import app

client = TestClient(app)

def test_pdf_extract_endpoint():
    res = client.post("/api/sources/test_pdf_extract", json={"file_path": "nonexistent.pdf", "page_number": 1, "engine": "fitz"})
    assert res.status_code == 200
    data = res.json()
    assert "status" in data

def test_embed_endpoint():
    res = client.post("/api/models/test_embed", json={"text": "Test clinical term: Amyloidosis"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["dimension"] == 384

def test_rerank_endpoint():
    res = client.post("/api/models/test_rerank", json={
        "query": "heart attack",
        "passages": ["Myocardial infarction causes sudden chest pain.", "Kidney failure reduces urine output."]
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert len(data["results"]) == 2
