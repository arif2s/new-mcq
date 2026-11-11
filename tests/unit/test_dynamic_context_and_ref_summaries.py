from __future__ import annotations

import json
import pytest
import httpx

from core.model_lifecycle import ModelLifecycleManager
from agents.tema_q.subtopic_router import sanitize_clinical_prose, route_and_prune_subtopics
from core.reporter import generate_topic_report


@pytest.mark.asyncio
async def test_lm_studio_high_context_auto_scaling_30k_to_60k():
    """Verifies that a 60,000-token loaded model in LM Studio scales input/output budgets
    instead of clamping to 4,096 tokens or ~4,000 characters (650 words)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "unloaded-small-model",
                        "state": "not-loaded",
                        "max_context_length": 4096,
                    },
                    {
                        "id": "qwen2.5-14b-instruct",
                        "state": "loaded",
                        "type": "llm",
                        "loaded_context_length": 60000,
                        "max_context_length": 65536,
                    },
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        mgr = ModelLifecycleManager("http://localhost:1234/v1")
        ctx_info = await mgr.get_active_model_context_info(client=mock_client)

    assert ctx_info["model_id"] == "qwen2.5-14b-instruct"
    assert ctx_info["loaded_context_length_tokens"] == 60000
    # Must vastly exceed the legacy 650-word (~4000 char) and 280-word caps
    assert ctx_info["primary_source_input_words"] > 15000
    assert ctx_info["secondary_source_input_words"] > 10000
    assert ctx_info["microedit_word_limit"] > 3000
    assert ctx_info["subtopic_block_char_limit"] > 6000
    assert ctx_info["max_output_tokens"] >= 8000


def test_sanitize_clinical_prose_linearizes_tables_instead_of_dropping():
    """Verifies that clinical staging/dosage tables are linearized rather than stripped."""
    raw_md = (
        "### Staging & Management\n"
        "| Stage | Criteria | Drug Regimen |\n"
        "|---|---|---|\n"
        "| Stage I | BP 130-139 mmHg | Lisinopril 10 mg/day |\n"
    )
    cleaned = sanitize_clinical_prose(raw_md)
    assert "Stage I" in cleaned
    assert "Lisinopril 10 mg/day" in cleaned


def test_subtopic_router_respects_scaled_block_char_limit():
    """Verifies that prose blocks longer than 600 characters are not prematurely truncated when block_char_limit scales."""
    long_clinical_text = (
        "# Microscopy and Staining\n"
        + ("Congo red staining demonstrates apple-green birefringence under polarized light. " * 40)
        + "Fibril diameter is 7.5 to 10 nm on electron microscopy."
    )
    docs = [
        {
            "source_type": "obsidian",
            "source_label": "Obsidian: Amyloidosis",
            "page": 1,
            "raw_text": long_clinical_text,
        }
    ]
    buckets = route_and_prune_subtopics(
        topic="Amyloidosis",
        documents=docs,
        max_source1_input_words=5000,
        max_sec_input_words=3500,
        block_char_limit=8000,
    )
    assert len(buckets) >= 1
    combined_primary = " ".join(b.primary_text for b in buckets)
    assert "7.5 to 10 nm" in combined_primary


def test_reporter_renders_referenced_topic_summaries_and_links(tmp_path, monkeypatch):
    """Verifies that generated HTML dossiers include LM Studio summaries and summary links for referenced topics."""
    import core.reporter as reporter_mod
    monkeypatch.setattr(reporter_mod, "REPORTS_DIR", tmp_path)

    synthesis_output = {
        "topic_key": "cardiac-amyloidosis",
        "display_title": "Cardiac Amyloidosis",
        "enhanced_explanation": "### Overview\nRestrictive cardiomyopathy caused by extracellular protein deposition.",
        "unified_article": json.dumps({
            "section": "Cardiology",
            "subtopic": "Cardiac Amyloidosis",
            "clinical_points": [{"point": "Tafamidis 61 mg/day reduces mortality in ATTR cardiomyopathy.", "source": "Harrison", "page": 12}],
        }),
        "per_article_summaries": [
            {
                "ref_topic_key": "attr-cardiomyopathy-pdf-p12",
                "ref_display_title": "ATTR Cardiomyopathy",
                "source": "Harrison",
                "source_type": "pdf",
                "page": 12,
                "summary_markdown": "**LM Studio Summary:** ATTR cardiomyopathy involves transthyretin tetramer dissociation.",
                "summary_link": "/api/notes/topic/attr-cardiomyopathy-pdf-p12/summary",
                "report_url": "/reports/attr_cardiomyopathy_pdf_p12_report.html",
            }
        ],
    }

    report_path = generate_topic_report(
        topic="Cardiac Amyloidosis",
        search_results=synthesis_output["per_article_summaries"],
        synthesis_output=synthesis_output,
    )
    assert report_path is not None
    html_text = (tmp_path / "cardiac_amyloidosis_report.html").read_text(encoding="utf-8")
    assert "/api/notes/topic/attr-cardiomyopathy-pdf-p12/summary" in html_text
    assert "transthyretin tetramer dissociation" in html_text
