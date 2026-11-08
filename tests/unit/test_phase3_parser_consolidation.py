from pathlib import Path
from parsers.schema import BoundingBox, ParsedBlock, ParsedDocument, clamp_heading_levels
from parsers.docling.cleaner import clean_ocr_text
from parsers.docling.geometry import bbox_iou, compute_iou_matrix
from parsers.heading_toc_calibrator import fast_heading_similarity
from parsers.obsidian_reader import extract_obsidian_metadata_and_clean_body
from parsers.zim_reader import fast_strip_zim_html


def test_legacy_parser_files_removed():
    root = Path(__file__).resolve().parents[2]
    legacy = [
        "parsers/docling_parser.py",
        "parsers/docling_v1_heading_opt.py",
        "parsers/docling_v2_parser.py",
        "parsers/docling_v3_parser.py",
        "parsers/indra_parser.py",
        "parsers/indra_base_heading_opt.py",
        "parsers/layout_profiles.py",
        "parsers/pdf_parser.py",
    ]
    for rel in legacy:
        assert not (root / rel).exists(), f"Legacy file should be removed: {rel}"


def test_heading_hierarchy_clamping():
    doc = ParsedDocument(
        source_path="sample.pdf",
        blocks=[
            ParsedBlock(block_id="1", text="Chapter 1", heading_level=1),
            ParsedBlock(block_id="2", text="Deep Subheading", heading_level=4),
            ParsedBlock(block_id="3", text="Body paragraph"),
        ],
    )
    md = doc.to_markdown()
    assert "# Chapter 1" in md
    assert "## Deep Subheading" in md

    items = [{"title": "A", "level": 2}, {"title": "B", "level": 5}]
    clamped = clamp_heading_levels(items)
    assert [x["level"] for x in clamped] == [1, 2]


def test_biomedical_hyphen_preservation_and_soft_break_joining():
    raw = "The patient received beta-\nblockers and anti-\ninflammatory therapy for fac-\ntitious fever."
    cleaned = clean_ocr_text(raw)
    assert "factitious" in cleaned
    assert "beta-" in cleaned
    assert "anti-" in cleaned


def test_vectorized_iou_matches_scalar():
    boxes_a = [[0, 0, 10, 10], [20, 20, 30, 30]]
    boxes_b = [[5, 5, 15, 15], [20, 20, 30, 30]]
    mat = compute_iou_matrix(boxes_a, boxes_b)
    assert abs(float(mat[0, 0]) - bbox_iou(boxes_a[0], boxes_b[0])) < 1e-5
    assert abs(float(mat[1, 1]) - 1.0) < 1e-5


def test_fast_toc_similarity_and_obsidian_zim_helpers():
    assert fast_heading_similarity("1.2 Pathophysiology of Heart Failure", "Pathophysiology of Heart Failure") > 0.8
    meta, body = extract_obsidian_metadata_and_clean_body(
        "---\naliases: [CHF, Heart Failure]\ntags:\n  - cardiology\n---\nSee [[Myocardial Infarction|MI]] notes."
    )
    assert meta["aliases"] == ["CHF", "Heart Failure"]
    assert meta["tags"] == ["cardiology"]
    assert meta["wikilinks"] == ["Myocardial Infarction"]
    assert body == "See MI notes."
    assert fast_strip_zim_html("<html><script>bad()</script><h1>Title</h1><p>Body &amp; text</p></html>") == "Title\nBody & text"
