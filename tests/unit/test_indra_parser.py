from __future__ import annotations

import os
from pathlib import Path
import pytest
import pymupdf

from parsers.indra import (
    IndraModularEngine,
    LayoutStrategyRegistry,
    PageElement,
    PageElements,
    TableGrid,
    enrich_lines,
    reconstruct_2d_table_grid,
    deskew_bbox_norm,
)
import parsers.indra_parser as ip


@pytest.fixture(scope="session")
def single_col_pdf(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("pdfs") / "single_col.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 60), "Clinical Practice Guidelines", fontsize=24)
    page.insert_text((50, 100), "Emergency Airway Management", fontsize=16)
    page.insert_text((50, 140), "Airway management is critical in emergency situations.", fontsize=11)
    page.insert_text((50, 180), "1. Immediate Assessment", fontsize=14)
    page.insert_text((50, 210), "Check responsiveness and airway patency.", fontsize=11)
    page.insert_text((50, 250), "2. Interventions", fontsize=14)
    page.insert_text((50, 280), "Perform endotracheal intubation if GCS is below 8.", fontsize=11)
    page.insert_text((50, 310), "- Prepare suction equipment", fontsize=11)
    page.insert_text((50, 330), "- Administer pre-oxygenation with 100% O2", fontsize=11)
    page.insert_text((50, 350), "- Check blood gas PaO2 and PaCO2", fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="session")
def multicol_pdf(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("pdfs") / "multi_col.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "Journal of Medical Research", fontsize=20)
    # Col 1
    page.insert_text((50, 90), "Introduction to Sepsis", fontsize=14)
    page.insert_text((50, 110), "Sepsis is a life-threatening organ dysfunction", fontsize=10)
    page.insert_text((50, 125), "caused by a dysregulated host response to infection.", fontsize=10)
    page.insert_text((50, 140), "Early identification is essential for survival.", fontsize=10)
    page.insert_text((50, 165), "a) Clinical Presentation", fontsize=12)
    page.insert_text((50, 185), "Fever, hypothermia, tachypnea, and tachycardia.", fontsize=10)
    # Col 2
    page.insert_text((330, 90), "Diagnostic Criteria", fontsize=14)
    page.insert_text((330, 110), "SOFA score increases by 2 points or more.", fontsize=10)
    page.insert_text((330, 125), "Serum lactate levels above 2 mmol/L indicate shock.", fontsize=10)
    page.insert_text((330, 165), "b) Initial Resuscitation", fontsize=12)
    page.insert_text((330, 185), "Administer 30 mL/kg crystalloid fluid within 3h.", fontsize=10)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="session")
def scanned_pdf(tmp_path_factory, single_col_pdf) -> str:
    path = str(tmp_path_factory.mktemp("pdfs") / "scanned_image.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    src_doc = pymupdf.open(single_col_pdf)
    pix = src_doc[0].get_pixmap(dpi=150)
    src_doc.close()
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    doc.save(path)
    doc.close()
    return path


def test_single_column_pdf_extraction(single_col_pdf):
    res = ip.extract_page_with_indra(single_col_pdf, page_number=1)
    assert res["status"] == "success"
    assert res["source"] == "text"
    assert "Clinical Practice Guidelines" in res["markdown"]
    assert "Emergency Airway Management" in res["markdown"]
    assert "1. Immediate Assessment" in res["markdown"]
    assert "2. Interventions" in res["markdown"]
    # Check that headings are detected without list item duplication
    assert len(res["headings"]) >= 2
    assert "PaO2" in res["markdown"] and "PaCO2" in res["markdown"]


def test_multi_column_pdf_extraction(multicol_pdf):
    res = ip.extract_page_with_indra(multicol_pdf, page_number=1)
    assert res["status"] == "success"
    assert res["source"] == "text"
    headings = res["headings"]
    assert "Journal of Medical Research" in headings
    assert "Introduction to Sepsis" in headings
    assert "Diagnostic Criteria" in headings
    # Verify reading order separates columns rather than interleaving line-by-line
    md = res["markdown"]
    pos_col1 = md.find("Early identification is essential for survival.")
    pos_col2 = md.find("Diagnostic Criteria")
    assert pos_col1 < pos_col2, "Column 1 text should precede Column 2 heading"


def test_scanned_pdf_graceful_fallback(scanned_pdf):
    res = ip.extract_page_with_indra(scanned_pdf, page_number=1)
    assert res["status"] == "success"
    # When no OCR backend is installed in the test environment, fallback gracefully succeeds without raising exceptions
    assert "markdown" in res
    assert "svg_overlay" in res
    assert res["page"] == 1


def test_no_global_cuda_disable_on_import():
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    lines = enrich_lines([{"text": "Sample text", "bbox_norm": (0.1, 0.1, 0.5, 0.12), "size_pt": 11.0}])
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "0"
    assert lines[0]["size"] == 11.0


def test_lowercase_short_word_not_corrupted_to_number_2():
    ir = PageElements(page_number=1, width_pt=612.0, height_pt=792.0, profile_id="native_digital")
    ir.elements = [
        PageElement(
            element_id=1,
            text="in",
            bbox_norm=(0.10, 0.20, 0.118, 0.215),
            size_pt=10.0,
            rel_size=1.0,
        ),
        PageElement(
            element_id=2,
            text="Patients with Acute Respiratory Distress",
            bbox_norm=(0.125, 0.20, 0.44, 0.215),
            size_pt=10.0,
            rel_size=1.0,
        ),
    ]
    strategy = LayoutStrategyRegistry.get("native_digital")
    strategy.assign_columns_and_merge_lines(ir)
    active = [e for e in ir.elements if not e.is_suppressed_furniture]
    assert len(active) == 1
    assert active[0].text == "in Patients with Acute Respiratory Distress"
    assert not active[0].text.startswith("2.")


def test_2d_table_sparse_columns_and_multiline_cells():
    cells = [
        {"text": "Parameter", "bbox_norm": (0.10, 0.30, 0.25, 0.32)},
        {"text": "Baseline", "bbox_norm": (0.40, 0.30, 0.52, 0.32)},
        {"text": "Follow-up", "bbox_norm": (0.70, 0.30, 0.85, 0.32)},
        {"text": "Systolic Blood", "bbox_norm": (0.10, 0.34, 0.26, 0.36)},
        {"text": "120 mmHg", "bbox_norm": (0.40, 0.34, 0.52, 0.36)},
        {"text": "115 mmHg", "bbox_norm": (0.70, 0.34, 0.85, 0.36)},
        {"text": "Pressure (mmHg)", "bbox_norm": (0.10, 0.365, 0.27, 0.382)},
        {"text": "p < 0.01", "bbox_norm": (0.40, 0.41, 0.50, 0.43)},
        {"text": "p < 0.001", "bbox_norm": (0.70, 0.41, 0.82, 0.43)},
    ]
    grid = reconstruct_2d_table_grid(cells, (0.08, 0.29, 0.88, 0.45))
    assert grid is not None
    assert len(grid.rows) == 3
    assert grid.rows[0] == ["Parameter", "Baseline", "Follow-up"]
    assert grid.rows[1] == ["Systolic Blood Pressure (mmHg)", "120 mmHg", "115 mmHg"]
    assert grid.rows[2] == ["", "p < 0.01", "p < 0.001"]


def test_sticky_academic_profile_across_pages():
    engine = IndraModularEngine()
    p1 = PageElements(
        page_number=1,
        width_pt=612.0,
        height_pt=792.0,
        profile_id="native_digital",
        elements=[PageElement(1, "DOI: 10.1001/jama.2025.1234 Corresponding Author", (0.1, 0.1, 0.8, 0.15), 10.0, 1.0)],
    )
    prof1 = engine.resolve_profile(p1, has_native_spans=True)
    assert prof1.profile_id == "academic_article"

    p2 = PageElements(
        page_number=2,
        width_pt=612.0,
        height_pt=792.0,
        profile_id="native_digital",
        elements=[PageElement(1, "Methods and Statistical Analysis", (0.1, 0.1, 0.45, 0.13), 11.5, 1.15, is_bold=True)],
    )
    prof2 = engine.resolve_profile(p2, has_native_spans=True)
    assert prof2.profile_id == "academic_article"
