import os
import tempfile
import pytest
import pymupdf as fitz

from parsers.docling_v2_parser import (
    AdaptivePdfLayoutEngine,
    extract_page_with_adaptive_layout,
    extract_page_with_docling_v2,
    extract_pdf_structure as extract_pdf_structure_v2,
)
from parsers.pdf_extractor import (
    extract_page_with_headings,
    extract_pdf_pages,
)


@pytest.fixture
def multi_tier_pdf():
    doc = fitz.open()

    # Page 1: Title (H1), Wrapped H2 heading, H3 subheading, H4 run-in
    p1 = doc.new_page(width=612, height=792)

    # H1 Title
    p1.insert_text((50, 40), "HARRISON'S INTERNAL MEDICINE", fontsize=22, fontname="hebo")

    # Shaded section banner for H2
    rect_banner = fitz.Rect(45, 80, 565, 105)
    p1.draw_rect(rect_banner, color=(0.1, 0.3, 0.6), fill=(0.85, 0.9, 0.98))

    # Wrapped 2-line H2 heading inside or near banner
    p1.insert_text((50, 93), "CHAPTER 45: PATHOPHYSIOLOGY OF", fontsize=14, fontname="hebo")
    p1.insert_text((50, 105), "CONGESTIVE HEART FAILURE", fontsize=14, fontname="hebo")

    # Body text
    p1.insert_text((50, 130), "Heart failure is a complex clinical syndrome.", fontsize=10, fontname="helv")

    # H3 Subheading
    p1.insert_text((50, 160), "1.1 Etiology and Risk Factors", fontsize=12, fontname="hebo")
    p1.insert_text((50, 180), "Ischemic heart disease remains the predominant cause.", fontsize=10, fontname="helv")

    # H4 Sub-subheading / Run-in heading
    p1.insert_text((50, 210), "Pathogenesis.", fontsize=10, fontname="hebo")
    p1.insert_text((120, 210), "Myocardial injury triggers compensatory mechanisms.", fontsize=10, fontname="helv")

    # Page 2: New H2 Main Heading (should clear H3 and H4 from stack!)
    p2 = doc.new_page(width=612, height=792)

    # New H2 Heading
    p2.insert_text((50, 50), "2. CLINICAL MANIFESTATIONS", fontsize=15, fontname="hebo")
    p2.insert_text((50, 80), "Dyspnea is the cardinal symptom of left ventricular failure.", fontsize=10, fontname="helv")

    # H3 Subheading on page 2
    p2.insert_text((50, 110), "2.1 Physical Examination", fontsize=12, fontname="hebo")
    p2.insert_text((50, 130), "Cardiomegaly and S3 gallop may be present.", fontsize=10, fontname="helv")

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()

    yield tmp.name

    if os.path.exists(tmp.name):
        os.remove(tmp.name)


def test_wrapped_heading_merging(multi_tier_pdf):
    res1 = extract_page_with_adaptive_layout(multi_tier_pdf, 1, default_title="Harrison")
    text = res1.get("extracted_text", "")
    assert "CHAPTER 45: PATHOPHYSIOLOGY OF CONGESTIVE HEART FAILURE" in text or "CHAPTER 45" in text
    hierarchy = res1.get("hierarchy_tree", [])
    h_titles = [h["title"] for h in hierarchy]
    assert not ("CHAPTER 45: PATHOPHYSIOLOGY OF" in h_titles and "CONGESTIVE HEART FAILURE" in h_titles)


def test_stateful_stack_and_breadrumbs(multi_tier_pdf):
    sections = extract_pdf_pages(multi_tier_pdf, default_title="Harrison")
    assert len(sections) == 2

    p1_sec = sections[0]
    p2_sec = sections[1]

    assert "Harrison" in p1_sec["heading_path"]
    assert "CHAPTER 45" in p1_sec["heading_path"]

    assert "1.1 Etiology and Risk Factors" not in p2_sec["heading_path"]
    assert "Pathogenesis" not in p2_sec["heading_path"]
    assert "CLINICAL MANIFESTATIONS" in p2_sec["heading_path"]


def test_docling_v2_pdf_structure_stack_propagation(multi_tier_pdf):
    sections = extract_pdf_structure_v2(multi_tier_pdf)
    assert len(sections) == 2

    p1_sec = sections[0]
    p2_sec = sections[1]

    assert "CHAPTER 45" in p1_sec["heading_path"]
    assert "1.1 Etiology and Risk Factors" not in p2_sec["heading_path"]
    assert "CLINICAL MANIFESTATIONS" in p2_sec["heading_path"]
