import os
import tempfile
import pytest
import pymupdf as fitz

from parsers.docling_parser import (
    get_converter,
    extract_structured_layout,
    extract_pdf_structure,
    ingest_to_lancedb,
)


@pytest.fixture
def sample_pdf_path():
    doc = fitz.open()

    # Page 1: Chapter title and section
    p1 = doc.new_page()
    p1.insert_text((50, 50), "Cardiology Overview", fontsize=22)
    p1.insert_text((50, 90), "1. Ischemic Heart Disease", fontsize=18)
    p1.insert_text(
        (50, 120),
        "Ischemic heart disease is a leading cause of mortality worldwide.",
        fontsize=12,
    )

    # Page 2: Subheading and content
    p2 = doc.new_page()
    p2.insert_text((50, 50), "1.1 Clinical Presentation", fontsize=16)
    p2.insert_text(
        (50, 80),
        "Patients present with substernal chest pressure radiating to the left arm.",
        fontsize=12,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()

    yield tmp.name

    if os.path.exists(tmp.name):
        os.remove(tmp.name)


def test_get_converter():
    converter = get_converter()
    assert converter is not None


def test_extract_structured_layout(sample_pdf_path):
    layout_p1 = extract_structured_layout(sample_pdf_path, 1)
    assert isinstance(layout_p1, str)
    assert len(layout_p1) > 0
    assert "Cardiology Overview" in layout_p1 or "Ischemic Heart Disease" in layout_p1

    layout_p2 = extract_structured_layout(sample_pdf_path, 2)
    assert isinstance(layout_p2, str)
    assert len(layout_p2) > 0

    invalid_page = extract_structured_layout(sample_pdf_path, 999)
    assert invalid_page == ""


def test_extract_pdf_structure(sample_pdf_path):
    sections = extract_pdf_structure(sample_pdf_path)
    assert isinstance(sections, list)
    assert len(sections) > 0

    for section in sections:
        assert "page" in section
        assert "heading" in section
        assert "subheading" in section
        assert "text" in section
        assert "article_length" in section
        assert section["article_length"] == len(section["text"])


def test_extract_pdf_structure_nonexistent_file():
    sections = extract_pdf_structure("nonexistent_path.pdf")
    assert sections == []
