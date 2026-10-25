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


@pytest.fixture
def ophthalmology_pdf():
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)

    # Shaded top callout box with CHAPTER and 5 on separate lines
    rect_box = fitz.Rect(40, 30, 570, 90)
    p.draw_rect(rect_box, color=(0.2, 0.2, 0.5), fill=(0.92, 0.94, 0.98))
    p.insert_text((50, 48), "CHAPTER", fontsize=11, fontname="hebo")
    p.insert_text((50, 62), "5", fontsize=18, fontname="hebo")
    p.insert_text((150, 60), "Diseases of Cornea", fontsize=16, fontname="hebo")

    # Quick Text Review pill / header (H2)
    p.insert_text((50, 110), "Quick Text Review", fontsize=16, fontname="hebo")

    # CONGENITAL ANOMALIES sub-banner (H3)
    rect_banner = fitz.Rect(40, 130, 300, 150)
    p.draw_rect(rect_banner, color=(0.1, 0.3, 0.6), fill=(0.85, 0.9, 0.98))
    p.insert_text((45, 143), "CONGENITAL ANOMALIES", fontsize=11, fontname="hebo")

    # H4 Megalocornea
    p.insert_text((45, 165), "Megalocornea", fontsize=11, fontname="hebo")
    p.insert_text((45, 180), "The horizontal diameter of cornea at birth is 10 mm.", fontsize=10, fontname="helv")

    # Drop-cap initial letter test ("S" + "clerocornea")
    p.insert_text((45, 205), "S", fontsize=10, fontname="hebo")
    p.insert_text((52, 205), "clerocornea", fontsize=10, fontname="helv")

    # KERATITIS sub-banner (H3)
    rect_banner2 = fitz.Rect(40, 230, 300, 250)
    p.draw_rect(rect_banner2, color=(0.1, 0.3, 0.6), fill=(0.85, 0.9, 0.98))
    p.insert_text((45, 243), "KERATITIS", fontsize=11, fontname="hebo")

    # H4 CORNEAL ULCER
    p.insert_text((45, 265), "CORNEAL ULCER (ULCERATIVE KERATITIS)", fontsize=11, fontname="hebo")

    # H5 Bacterial corneal ulcer
    p.insert_text((45, 285), "Bacterial corneal ulcer", fontsize=10, fontname="hebo")
    p.insert_text((45, 300), "Commonest causative organisms are pneumococcus.", fontsize=10, fontname="helv")

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()

    yield tmp.name

    if os.path.exists(tmp.name):
        os.remove(tmp.name)


def test_ophthalmology_heading_levels_and_formatting(ophthalmology_pdf):
    res = extract_page_with_adaptive_layout(ophthalmology_pdf, 1, default_title="Ophthalmology")
    extracted = res.get("extracted_text", "")

    # 1. CHAPTER 5 merging in callout box
    assert "CHAPTER 5" in extracted

    # 2. Drop-cap concatenation
    assert "Sclerocornea" in extracted

    # 3. Heading levels and **text** bold heading formatting
    assert "## Quick Text Review" in extracted
    assert "### CONGENITAL ANOMALIES" in extracted
    assert "#### **Megalocornea**" in extracted or "#### Megalocornea" in extracted
    assert "### KERATITIS" in extracted
    assert "#### **CORNEAL ULCER (ULCERATIVE KERATITIS)**" in extracted or "#### CORNEAL ULCER" in extracted
    assert "##### **Bacterial corneal ulcer**" in extracted or "##### Bacterial corneal ulcer" in extracted

    # 4. Details saved in active stack and hierarchy tree for retrieval
    hierarchy = res.get("hierarchy_tree", [])
    titles = [h["title"] for h in hierarchy]
    assert "Megalocornea" in titles
    assert "Bacterial corneal ulcer" in titles

    active_stack = res.get("active_heading_stack", {})
    assert any(val == "Bacterial corneal ulcer" for val in active_stack.values())
