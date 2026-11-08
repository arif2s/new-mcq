import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from parsers.heading_toc_calibrator import (
    classify_outline_prefix,
    compare_heading_visual_styles,
    rgb_hue,
    score_and_resolve_heading_candidate,
)
from parsers.indra.profiles import LAYOUT_PROFILES
from parsers.indra.types import PageElement
from parsers.pdf_topic_indexer import (
    _has_sibling_conflict,
    compute_toc_page_ranges,
    ensure_pdf_topic_index_table,
    lookup_pdf_topic_pages,
)
from core.database import get_db_connection


def test_multi_pass_calibrator_preserves_situation_and_alpha_subheadings():
    """Verifies *Situation 1/2 purple headings and a)-d) subheadings are accurately scored."""
    prof = LAYOUT_PROFILES["native_digital"]
    active_stack = {}
    protos = []

    sit_el = PageElement(
        element_id=1,
        text="*Situation 1: At Secondary Hospital/ Non-Metro situation: Optimal Standards",
        bbox_norm=(0.10, 0.18, 0.85, 0.22),
        size_pt=12.0,
        rel_size=1.1,
        fg_rgb=(128, 0, 128),
        is_bold=True,
        is_accent_color=True,
        fg_hue=rgb_hue((128, 0, 128)),
    )
    score_and_resolve_heading_candidate(sit_el, prof, active_stack, protos)
    assert sit_el.role == "heading"
    assert sit_el.heading_level in (2, 3)

    treat_el = PageElement(
        element_id=2,
        text="c) Treatment:",
        bbox_norm=(0.10, 0.35, 0.28, 0.37),
        size_pt=11.0,
        rel_size=0.92,
        is_bold=True,
    )
    score_and_resolve_heading_candidate(treat_el, prof, active_stack, protos)
    assert treat_el.role == "heading"
    assert treat_el.prefix_family == "ALPHA_LOWER"


def test_toc_page_ranges_and_broncho_vs_lobar_pneumonia_disambiguation():
    """
    Verifies that `compute_toc_page_ranges` and `lookup_pdf_topic_pages`
    accurately separate 'Bronchopneumonia > Treatment' (p.412) from 'Lobar Pneumonia > Treatment' (p.408).
    """
    ensure_pdf_topic_index_table()
    sample_toc = [
        {"page": 405, "level": 1, "title": "Chapter 12: Pneumonia", "heading_path": "Textbook > Chapter 12: Pneumonia", "y0": 0.10},
        {"page": 407, "level": 2, "title": "Lobar Pneumonia", "heading_path": "Textbook > Chapter 12: Pneumonia > Lobar Pneumonia", "y0": 0.15},
        {"page": 408, "level": 3, "title": "Pathology", "heading_path": "Textbook > Chapter 12: Pneumonia > Lobar Pneumonia > Pathology", "y0": 0.20},
        {"page": 408, "level": 3, "title": "Treatment", "heading_path": "Textbook > Chapter 12: Pneumonia > Lobar Pneumonia > Treatment", "y0": 0.55},
        {"page": 411, "level": 2, "title": "Bronchopneumonia", "heading_path": "Textbook > Chapter 12: Pneumonia > Bronchopneumonia", "y0": 0.18},
        {"page": 412, "level": 3, "title": "Clinical Features", "heading_path": "Textbook > Chapter 12: Pneumonia > Bronchopneumonia > Clinical Features", "y0": 0.12},
        {"page": 412, "level": 3, "title": "Treatment", "heading_path": "Textbook > Chapter 12: Pneumonia > Bronchopneumonia > Treatment", "y0": 0.48},
        {"page": 414, "level": 2, "title": "Lung Abscess", "heading_path": "Textbook > Chapter 12: Pneumonia > Lung Abscess", "y0": 0.22},
    ]
    ranges = compute_toc_page_ranges(sample_toc, total_pages=420)
    assert len(ranges) == 8

    # Check Bronchopneumonia > Treatment span (starts on p.412 y0=0.48, ends on p.414 y1=0.22)
    bp_treat = [r for r in ranges if r["parent_topic"] == "Bronchopneumonia" and r["topic_title"] == "Treatment"][0]
    assert bp_treat["start_page"] == 412
    assert bp_treat["start_y0"] == 0.48
    assert bp_treat["end_page"] == 414
    assert bp_treat["end_y1"] == 0.22

    # Insert into SQLite and test sibling-safe lookup
    with get_db_connection() as conn:
        conn.execute("DELETE FROM pdf_topic_index WHERE pdf_path = 'mock_harrison.pdf'")
        for r in ranges:
            conn.execute(
                """
                INSERT INTO pdf_topic_index (
                    pdf_path, pdf_title, engine_source, heading_level,
                    topic_title, topic_norm, parent_topic, parent_norm,
                    heading_path, heading_path_norm, start_page, end_page, start_y0, end_y1
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "mock_harrison.pdf", "Harrison", "indra_base_heading_opt", r["heading_level"],
                    r["topic_title"], r["topic_norm"], r["parent_topic"], r["parent_norm"],
                    r["heading_path"], r["heading_path_norm"], r["start_page"], r["end_page"],
                    r["start_y0"], r["end_y1"],
                ),
            )
        conn.commit()

    hits_broncho = lookup_pdf_topic_pages("Bronchopneumonia", subtopic="Treatment")
    assert len(hits_broncho) >= 1
    assert hits_broncho[0]["start_page"] == 412
    assert "Bronchopneumonia" in hits_broncho[0]["heading_path"]
    assert all("Lobar Pneumonia" not in h["heading_path"] for h in hits_broncho)

    hits_lobar = lookup_pdf_topic_pages("Lobar Pneumonia", subtopic="Treatment")
    assert len(hits_lobar) >= 1
    assert hits_lobar[0]["start_page"] == 408
    assert "Lobar Pneumonia" in hits_lobar[0]["heading_path"]
    assert all("Bronchopneumonia" not in h["heading_path"] for h in hits_lobar)