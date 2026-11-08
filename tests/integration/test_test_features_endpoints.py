import os
import tempfile
import pymupdf as fitz
from fastapi.testclient import TestClient
from core.database import initialize_database
from server.app import app

# Ensure database tables exist before client requests
initialize_database()

client = TestClient(app)

def test_pdf_extract_endpoint():
    res = client.post("/api/sources/test_pdf_extract", json={"file_path": "nonexistent.pdf", "page_number": 1, "engine": "fitz"})
    assert res.status_code == 200
    data = res.json()
    assert "status" in data

def test_pdf_extract_endpoint_multipage():
    # Create temporary 2-page PDF
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "Chapter 1: Cardiology", fontsize=20)
    p1.insert_text((50, 90), "1. Ischemic Heart Disease Overview", fontsize=14)
    p1.insert_text((50, 120), "Ischemic heart disease is a leading cause of mortality.", fontsize=10)

    p2 = doc.new_page()
    p2.insert_text((50, 50), "2. Myocardial Infarction Management", fontsize=14)
    p2.insert_text((50, 80), "Early reperfusion therapy reduces infarct size.", fontsize=10)

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()

    try:
        # Test engine="fitz"
        res_fitz = client.post("/api/sources/test_pdf_extract", json={"file_path": tmp.name, "page_number": 2, "engine": "fitz"})
        assert res_fitz.status_code == 200
        d_fitz = res_fitz.json()
        assert d_fitz["status"] == "success"
        assert d_fitz["page_number"] == 2
        assert d_fitz["total_pages"] == 2
        assert "Myocardial Infarction" in d_fitz["extracted_text"]

        # Test engine="headings"
        res_h = client.post("/api/sources/test_pdf_extract", json={"file_path": tmp.name, "page_number": 2, "engine": "headings"})
        assert res_h.status_code == 200
        d_h = res_h.json()
        assert d_h["status"] == "success"
        assert d_h["heading_path"] != ""

        # Test engine="docling" on page 2 (page_number > 1)
        res_docling = client.post("/api/sources/test_pdf_extract", json={"file_path": tmp.name, "page_number": 2, "engine": "docling"})
        assert res_docling.status_code == 200
        d_docling = res_docling.json()
        assert d_docling["status"] == "success"
        assert d_docling["page_number"] == 2
        assert d_docling["total_pages"] == 2
        assert len(d_docling["extracted_text"]) > 0

        # Test engine="docling_v2" on page 2
        res_v2 = client.post("/api/sources/test_pdf_extract", json={"file_path": tmp.name, "page_number": 2, "engine": "docling_v2"})
        assert res_v2.status_code == 200
        d_v2 = res_v2.json()
        assert d_v2["status"] == "success"
        assert d_v2["engine"] == "docling_v2"
        assert d_v2["page_number"] == 2
        assert d_v2["total_pages"] == 2
        assert len(d_v2["extracted_text"]) > 0
        assert "page_image_base64" in d_v2
        assert "layout_boxes" in d_v2
        assert isinstance(d_v2["layout_boxes"], list)
        assert d_v2["page_width"] > 0
        assert d_v2["page_height"] > 0
        # Test engine="docling_v3" on page 2 with rapidocr
        res_v3 = client.post("/api/sources/test_pdf_extract", json={"file_path": tmp.name, "page_number": 2, "engine": "docling_v3", "ocr_engine": "rapidocr"})
        assert res_v3.status_code == 200
        d_v3 = res_v3.json()
        assert d_v3["status"] == "success"
        assert d_v3["engine"] == "docling_v3"
        assert d_v3["page_number"] == 2
        assert d_v3["total_pages"] == 2
        assert len(d_v3["extracted_text"]) > 0
        assert "page_image_base64" in d_v3
        assert "layout_boxes" in d_v3

    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)

def test_embed_endpoint():
    res = client.post("/api/models/test_embed", json={"text": "Test clinical term: Amyloidosis"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("success", "error")

def test_rerank_endpoint():
    res = client.post("/api/models/test_rerank", json={
        "query": "heart attack",
        "passages": ["Myocardial infarction causes sudden chest pain.", "Kidney failure reduces urine output."]
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("success", "error")
