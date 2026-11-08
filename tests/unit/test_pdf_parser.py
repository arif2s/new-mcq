import os
import tempfile
import pytest
import pymupdf as fitz

from parsers.pdf_parser import AdaptivePdfLayoutEngine, extract_page_with_adaptive_layout


@pytest.fixture
def multicolumn_pdf_path():
    doc = fitz.open()

    # Create a 2-column medical page
    p = doc.new_page(width=612, height=792)

    # Title spanning across columns
    p.insert_text((50, 50), "SURGICAL MANAGEMENT OF BILIARY DISEASE", fontsize=18)

    # Column 1 (Left)
    p.insert_text((50, 90), "1. Introduction to Cholecystitis", fontsize=14)
    p.insert_text(
        (50, 110),
        "Acute cholecystitis is an inflammation of the gallbladder.",
        fontsize=10,
    )
    p.insert_text(
        (50, 125),
        "It most commonly results from obstruction of the cystic duct.",
        fontsize=10,
    )

    # Column 2 (Right)
    p.insert_text((320, 90), "2. Diagnostic Ultrasound Findings", fontsize=14)
    p.insert_text(
        (320, 110),
        "Ultrasound is the primary imaging modality of choice.",
        fontsize=10,
    )
    p.insert_text(
        (320, 125),
        "Findings include gallbladder wall thickening > 3mm and Murphy sign.",
        fontsize=10,
    )

    # Shaded Callout Box drawing on page
    rect = fitz.Rect(50, 200, 560, 260)
    p.draw_rect(rect, color=(0.2, 0.4, 0.8), fill=(0.9, 0.95, 1.0))
    p.insert_text((60, 220), "CLINICAL PEARL: Sonographic Murphy Sign", fontsize=11)
    p.insert_text(
        (60, 240),
        "Maximal tenderness over the sonographically localized gallbladder has high specificity.",
        fontsize=10,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()

    yield tmp.name

    if os.path.exists(tmp.name):
        os.remove(tmp.name)


def test_adaptive_pdf_layout_engine(multicolumn_pdf_path):
    res = extract_page_with_adaptive_layout(multicolumn_pdf_path, 1, default_title="Sabiston")
    assert isinstance(res, dict)
    extracted_text = res.get("extracted_text", "")
    assert len(extracted_text) > 0

    # Verify column 1 text appears before column 2 text or callout
    col1_idx = extracted_text.find("Acute cholecystitis")
    col2_idx = extracted_text.find("Diagnostic Ultrasound Findings")
    assert col1_idx != -1
    assert col2_idx != -1
    assert col1_idx < col2_idx

    # Verify clinical callout box is recovered and formatted
    assert "CLINICAL PEARL" in extracted_text or "Clinical Box" in extracted_text


def test_heading_path_detection(multicolumn_pdf_path):
    res = extract_page_with_adaptive_layout(multicolumn_pdf_path, 1, default_title="Sabiston", promote_headings=True)
    heading_path = res.get("heading_path", "")
    assert "Sabiston" in heading_path
