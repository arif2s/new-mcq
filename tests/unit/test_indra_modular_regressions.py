import os
import pytest
from parsers.indra import (
    IndraHybridLayoutRefiner,
    IndraModularEngine,
    LayoutStrategyRegistry,
    PageElement,
    PageElements,
    RegexCatalog,
    enrich_lines,
    reconstruct_2d_table_grid,
)


def test_no_global_cuda_disable_and_clean_enrich():
    """Bug 2.2 & 2.4: Importing/calling enrich_lines preserves CUDA and point sizes."""
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    lines = enrich_lines([{"text": "Sample text", "bbox_norm": (0.1, 0.1, 0.5, 0.12), "size_pt": 11.0}])
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "0"
    assert lines[0]["size"] == 11.0


def test_alpha_prefixes_b_and_c_not_mutated_to_2():
    """issues.md §3.3 & §3.5: 'b) Investigations :' and 'c) Treatment:' must never become '2.'."""
    ir = PageElements(page_number=4, width_pt=612.0, height_pt=792.0, profile_id="native_digital")
    ir.elements = [
        PageElement(1, "b)", (0.10, 0.30, 0.118, 0.315), 11.5, 1.0, is_bold=True),
        PageElement(2, "Investigations :", (0.125, 0.30, 0.28, 0.315), 11.5, 1.0, is_bold=True),
        PageElement(3, "c)", (0.10, 0.45, 0.118, 0.465), 11.04, 0.92, is_bold=True),
        PageElement(4, "Treatment:", (0.125, 0.45, 0.24, 0.465), 11.04, 0.92, is_bold=True),
    ]
    strategy = LayoutStrategyRegistry.get("native_digital")
    strategy.assign_columns_and_merge_lines(ir)
    strategy.score_and_classify_elements(ir)
    active = [e for e in ir.elements if not e.is_suppressed_furniture]
    assert len(active) == 2
    assert active[0].text.startswith("b) Investigations")
    assert active[1].text.startswith("c) Treatment")
    assert active[0].role == "heading"
    assert active[1].role == "heading"


def test_situation_1_multiline_purple_heading_recognized():
    """issues.md §3.4: Multi-line '*Situation 1' with purple accent color and 'are limited' merges & scores as heading."""
    ir = PageElements(page_number=4, width_pt=612.0, height_pt=792.0, profile_id="native_digital")
    ir.elements = [
        PageElement(
            1,
            "*Situation 1: At Secondary Hospital/ Non-Metro situation: Optimal Standards of",
            (0.10, 0.20, 0.865, 0.218),
            12.0,
            1.1,
            fg_rgb=(128, 0, 128),
            is_bold=True,
            is_accent_color=True,
        ),
        PageElement(
            2,
            "Treatment in Situations where technology and resources are limited",
            (0.10, 0.222, 0.667, 0.240),
            12.0,
            1.1,
            fg_rgb=(128, 0, 128),
            is_bold=True,
            is_accent_color=True,
        ),
    ]
    strategy = LayoutStrategyRegistry.get("native_digital")
    strategy.assign_columns_and_merge_lines(ir)
    strategy.score_and_classify_elements(ir)
    active = [e for e in ir.elements if not e.is_suppressed_furniture]
    assert len(active) == 1
    assert active[0].role == "heading"
    assert "technology and resources are limited" in active[0].text


def test_no_duplicate_headings_or_list_items_in_refiner():
    """issues.md §2.1 & §2.2: Headings and list items must appear exactly once in refine_page output."""
    ir = PageElements(
        page_number=1,
        width_pt=612.0,
        height_pt=792.0,
        profile_id="native_digital",
        elements=[
            PageElement(1, "Common health problems", (0.1, 0.1, 0.6, 0.14), 16.0, 1.5, is_bold=True, role="heading", heading_level=2),
            PageElement(2, "1. Keep the healthy diet and suitable lifestyle", (0.1, 0.2, 0.8, 0.22), 11.0, 1.0, role="list_item"),
        ],
    )
    refiner = IndraHybridLayoutRefiner(page_number=1, default_title="Doc")
    res = refiner.refine_page(ir)
    assert res["headings"] == ["Common health problems"]
    assert len(res["hierarchy_tree"]) == 1
    assert res["markdown"].count("Keep the healthy diet") == 1
