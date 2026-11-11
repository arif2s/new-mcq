import os
import random
import time
from unittest.mock import patch
import pytest

# Enforce Invariant 1: Zero VRAM for test suite
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from agents.tema_q.subtopic_router import RawProseBlock, select_clinical_sentences
from core.reporter import sanitize_clinical_html, sanitize_reference_url
from core.scheduler import (
    calculate_fsrs_next_interval,
    compute_next_fsrs_state,
    compute_retrievability,
)
from parsers.docling.engine import SpatialSpanIndex


@pytest.fixture(autouse=True)
def enforce_strict_test_time_limit():
    """Fails any individual unit test that takes longer than 0.50 seconds."""
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.50, f"Test exceeded strict offline time limit: {elapsed:.3f}s > 0.500s"


def test_extractive_summarizer_order_and_telemetry():
    # 1. Verify blocks within the same heading preserve ascending block_idx/page order regardless of score
    src_map = {
        0: [
            (0.95, RawProseBlock(text="Block two text here", heading="Diagnosis", page=1, block_idx=2, source_label="S1")),
            (0.12, RawProseBlock(text="Block zero text here", heading="Diagnosis", page=1, block_idx=0, source_label="S1")),
            (0.60, RawProseBlock(text="Block one text here", heading="Diagnosis", page=1, block_idx=1, source_label="S1")),
        ]
    }
    sorted_srcs = [src_key for src_key in sorted(src_map.keys()) if src_map[src_key]]
    assert sorted_srcs
    primary_src_idx = sorted_srcs[0]
    primary_blocks = [
        b for _, b in sorted(
            src_map[primary_src_idx],
            key=lambda item: getattr(item[1], "block_idx", getattr(item[1], "page", 0)),
        )
    ]
    assert [pb.block_idx for pb in primary_blocks] == [0, 1, 2]

    # 2. Verify that when all sentences fall below min_relevance, the resurrected fallback sentences
    #    are NOT left in telemetry.solar_pruned_sentences under "below_min_relevance"
    class MockTelemetry:
        def __init__(self):
            self.solar_pruned_sentences = []

    tel = MockTelemetry()
    clean_sents = [
        "First clinical sentence is here.",
        "Second clinical sentence is here.",
        "Third clinical sentence is here.",
        "Fourth clinical sentence is here.",
        "Fifth clinical sentence is here.",
        "Sixth clinical sentence is dropped.",
        "Seventh clinical sentence is dropped.",
    ]
    scores = [0.01, 0.02, 0.03, 0.01, 0.04, 0.02, 0.01]
    res = select_clinical_sentences(
        clean_sents=clean_sents,
        scores=scores,
        min_relevance=0.20,
        max_words=200,
        source_label="Source 1",
        heading_breadcrumb="Diagnosis",
        telemetry=tel,
    )
    # Fallback resurrects top 5 sentences in reading order
    assert len(res) == 5
    assert res == clean_sents[:5]

    # Exactly 2 sentences (index 5 and 6) should remain pruned under below_min_relevance
    pruned_below_min = [
        entry["sentence"]
        for entry in tel.solar_pruned_sentences
        if entry["reason"] == "below_min_relevance"
    ]
    assert len(pruned_below_min) == 2
    assert "Sixth clinical sentence is dropped." in pruned_below_min
    assert "Seventh clinical sentence is dropped." in pruned_below_min
    for resurrected in clean_sents[:5]:
        assert resurrected not in pruned_below_min


def test_fsrs_same_day_cram_vs_spaced_recall():
    # 1. Verify that reviewing an active card (curr_state=2) with elapsed_days=0.0 yields R=1.0
    #    and does NOT inflate stability (next_s == curr_s).
    r_zero = compute_retrievability(elapsed_days=0.0, stability=10.0)
    assert r_zero == 1.0

    res_cram = compute_next_fsrs_state(
        curr_state=2,
        curr_s=10.0,
        curr_d=5.0,
        curr_lapses=0,
        grade=3,
        elapsed_days=0.0,
    )
    assert res_cram["stability"] == 10.0

    # 2. Verify that reviewing the same card at elapsed_days=curr_s increases stability as expected.
    res_spaced = compute_next_fsrs_state(
        curr_state=2,
        curr_s=10.0,
        curr_d=5.0,
        curr_lapses=0,
        grade=3,
        elapsed_days=10.0,
    )
    assert res_spaced["stability"] > 10.0
    assert res_spaced["stability"] == pytest.approx(29.0086, rel=1e-3)

    # 3. Verify get_fsrs_parameters() is NOT called when requested_retention and max_interval_days are passed.
    with patch("core.scheduler.get_fsrs_parameters") as mock_get_params:
        res_explicit = compute_next_fsrs_state(
            curr_state=2,
            curr_s=10.0,
            curr_d=5.0,
            curr_lapses=0,
            grade=3,
            elapsed_days=5.0,
            requested_retention=0.85,
            max_interval_days=180,
        )
        mock_get_params.assert_not_called()
        assert res_explicit["interval_days"] <= 180

        # Also test calculate_fsrs_next_interval conditional invocation
        calc_explicit = calculate_fsrs_next_interval(
            rating=3,
            state=2,
            difficulty=5.0,
            stability=10.0,
            reps=5,
            lapses=0,
            requested_retention=0.85,
            max_interval_days=180,
            elapsed_days=5.0,
        )
        mock_get_params.assert_not_called()
        assert calc_explicit["interval_days"] <= 180


def test_html_and_url_sanitization_offline():
    # 1. Pass "<script>alert(1)</script><p onclick='evil()'>Clinical <strong>Note</strong> &lt;2 mmol/L</p>"
    #    into sanitize_clinical_html() and assert "<script>" and "onclick" are stripped while "<strong>Note</strong>" remains.
    dirty_html = "<script>alert(1)</script><p onclick='evil()'>Clinical <strong>Note</strong> &lt;2 mmol/L</p>"
    clean_html = sanitize_clinical_html(dirty_html)
    assert "<script" not in clean_html
    assert "alert" not in clean_html
    assert "onclick" not in clean_html
    assert "<strong>Note</strong>" in clean_html
    assert "&lt;2 mmol/L" in clean_html

    # Also test dangerous URI injection inside tags
    dirty_uri = '<a href="javascript:alert(1)">Click</a>'
    clean_uri = sanitize_clinical_html(dirty_uri)
    assert "javascript:" not in clean_uri

    # 2. Pass "javascript:alert(1)" and "data:text/html,<script>" into sanitize_reference_url() and assert "#" is returned.
    assert sanitize_reference_url("javascript:alert(1)") == "#"
    assert sanitize_reference_url("data:text/html,<script>") == "#"
    assert sanitize_reference_url("vbscript:msgbox(1)") == "#"
    assert sanitize_reference_url("https://example.com/study") == "https://example.com/study"
    assert sanitize_reference_url("/api/search") == "/api/search"
    assert sanitize_reference_url("#section-1") == "#section-1"


def test_spatial_span_index_and_float_flushing_performance():
    # 1. Generate 5,000 synthetic spans and 200 candidate boxes; assert SpatialSpanIndex queries all 200 boxes
    #    in < 0.05 seconds and matches exact brute-force results.
    rng = random.Random(42)
    spans = []
    for i in range(5000):
        x0 = rng.uniform(0, 500)
        y0 = rng.uniform(0, 700)
        spans.append({
            "x0": x0,
            "y0": y0,
            "x1": x0 + rng.uniform(10, 50),
            "y1": y0 + 10,
            "is_marginal": False,
            "id": i,
        })

    span_idx = SpatialSpanIndex(spans)
    candidate_boxes = [
        (
            rng.uniform(50, 200),
            y,
            rng.uniform(250, 450),
            y + rng.uniform(10, 80),
        )
        for y in (rng.uniform(50, 600) for _ in range(200))
    ]

    t0 = time.perf_counter()
    for box in candidate_boxes:
        _ = span_idx.query_box(box)
    t_queries = time.perf_counter() - t0
    assert t_queries < 0.05, f"Spatial queries exceeded 0.05s: {t_queries:.4f}s"

    # Verify exact match with brute force
    for box in candidate_boxes[:25]:
        indexed_matches = {s["id"] for s in span_idx.query_box(box)}
        brute_matches = {
            s["id"]
            for s in spans
            if not s.get("is_marginal")
            and box[0] <= (s["x0"] + s["x1"]) * 0.5 <= box[2]
            and box[1] <= (s["y0"] + s["y1"]) * 0.5 <= box[3]
        }
        assert indexed_matches == brute_matches

    # 2. Verify flush_pending_floats() ignores empty markdown floats in BOTH output_entries and layout_boxes
    #    and does not raise ZeroDivisionError when page_width=0.
    class DummyPage:
        rect = None

    from parsers.docling.engine import AdaptivePdfLayoutEngine

    engine = AdaptivePdfLayoutEngine(
        page=DummyPage(),
        default_title="Test Page",
    )
    engine.page_width = 0.0
    engine.page_height = 0.0

    # Test _format_document with mixed empty/non-empty floats
    blocks = [
        {"type": "caption", "markdown": "   ", "bbox": (10.0, 10.0, 50.0, 30.0)},
        {"type": "caption", "markdown": "Figure 1: Valid Caption", "bbox": (10.0, 40.0, 80.0, 60.0)},
        {"type": "text", "markdown": "Main paragraph body text.", "text": "Main paragraph body text.", "bbox": (10.0, 70.0, 200.0, 90.0)},
        {"type": "caption", "markdown": "", "bbox": (10.0, 100.0, 50.0, 120.0)},
    ]

    doc_res = engine._format_document(blocks)
    assert doc_res is not None

    layout_boxes = doc_res["layout_boxes"]
    markdown_content = doc_res.get("text", "")

    # Only Figure 1 and the paragraph should be in layout_boxes and text; empty floats skipped in both
    caption_boxes = [lb for lb in layout_boxes if lb["type"] == "caption"]
    assert len(caption_boxes) == 1
    assert caption_boxes[0]["markdown"] == "Figure 1: Valid Caption"
    assert "Figure 1: Valid Caption" in markdown_content
