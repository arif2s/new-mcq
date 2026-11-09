"""
tests/test_heading_wise_pipeline.py - Unit and regression tests for heading-wise
LM Studio dispatch, multi-source breadcrumb preservation, pipeline telemetry,
and multi-model benchmarking.
"""

from __future__ import annotations

import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, patch

os.environ["CUDA_VISIBLE_DEVICES"] = ""

from agents.tema_q.subtopic_router import (
    sanitize_clinical_prose,
    _split_into_headed_prose_blocks,
    route_and_prune_subtopics,
)
from agents.tema_q.telemetry import (
    PipelineDiagnosticTrace,
    extract_clinical_numbers_and_stages,
)
from agents.tema_q.pipeline import run_tema_q_synthesis, call_llm_async


def test_sanitize_clinical_prose_converts_html_headings():
    """Verify HTML headings are converted to Markdown headings before tag stripping."""
    html = "<h1>Falciparum Malaria</h1><h2>Pathogenesis & Transmission</h2><p>Transmitted by female Anopheles mosquito with high parasitemia.</p>"
    clean = sanitize_clinical_prose(html)
    assert "# Falciparum Malaria" in clean
    assert "## Pathogenesis & Transmission" in clean
    assert "Transmitted by female Anopheles" in clean


def test_split_into_headed_prose_blocks_hierarchical_breadcrumbs():
    """Verify hierarchical breadcrumbs (H1 > H2 > H3) are produced."""
    doc = {
        "raw_text": (
            "<h1>Infectious Diseases</h1>\n"
            "<h2>Protozoa</h2>\n"
            "<h3>Malaria Pathogenesis</h3>\n"
            "Plasmodium falciparum invades erythrocytes via apical membrane antigen 1.\n"
            "It expresses PfEMP1 on infected red cells leading to cytoadherence and cerebral malaria."
        ),
        "source_label": "ZIM: Infectious Diseases",
        "source_type": "zim",
        "page": 1,
    }
    blocks = _split_into_headed_prose_blocks(doc, source_idx=0)
    assert len(blocks) >= 1
    assert blocks[0].heading == "Infectious Diseases > Protozoa > Malaria Pathogenesis"
    assert "Plasmodium falciparum" in blocks[0].text


def test_route_and_prune_subtopics_primary_heading_sections_and_telemetry():
    """Verify primary_heading_sections populated and telemetry recorded."""
    doc = {
        "raw_text": (
            "# Falciparum Malaria\n"
            "## Causes, Vector & Pathogenesis\n"
            "Transmitted by the female Anopheles mosquito vector.\n"
            "Sporozoites travel to the liver and infect hepatocytes within 30 minutes.\n"
            "## Clinical Features (Signs & Symptoms)\n"
            "Patients present with cyclical fever, severe anemia, and splenomegaly.\n"
        ),
        "source_label": "Harrison Internal Medicine",
        "source_type": "pdf",
        "page": 45,
    }
    trace = PipelineDiagnosticTrace(
        trace_id="test_trace_1",
        topic="Falciparum Malaria",
        model_id="test-model",
    )

    buckets = route_and_prune_subtopics(
        topic="Falciparum Malaria",
        documents=[doc],
        telemetry=trace,
    )
    assert len(buckets) >= 1
    first_bucket = buckets[0]
    assert hasattr(first_bucket, "primary_heading_sections")
    assert len(first_bucket.primary_heading_sections) >= 1
    assert "heading_breadcrumb" in first_bucket.primary_heading_sections[0]

    # Verify telemetry recorded extracted headings and buckets summary
    assert len(trace.extracted_headings_per_doc) == 1
    assert trace.extracted_headings_per_doc[0]["headings_count"] >= 2
    assert len(trace.routed_buckets_summary) >= 1


@pytest.mark.asyncio
async def test_run_tema_q_synthesis_heading_wise_with_mock_llm():
    """Verify run_tema_q_synthesis passes model_id and records telemetry dispatches."""
    sample_search_results = {
        "obsidian": [
            {
                "title": "Malaria Notes",
                "file_path": "data/vaults/Malaria.md",
                "text": (
                    "# Falciparum Malaria\n"
                    "## Causes, Vector & Pathogenesis\n"
                    "Transmitted by female Anopheles mosquito with dose 50 mg/kg artesunate.\n"
                    "Stage II disease requires ICU management."
                ),
            }
        ]
    }

    mock_llm_response = json.dumps({
        "section": "Clinical Summary",
        "subtopic": "Causes, Vector & Pathogenesis",
        "conditional_context": "Endemic zone",
        "clinical_points": [
            {
                "point": "Transmitted by Anopheles mosquito with dose 50 mg/kg artesunate in stage II.",
                "source": "Malaria Notes",
                "page": 1,
            }
        ],
        "discrepancies": "",
    })

    with patch("agents.tema_q.pipeline.call_llm_async", new_callable=AsyncMock) as mock_call:
        async def mock_call_impl(sys_p, user_p, max_tokens=500, model_id=None, telemetry_meta=None):
            if isinstance(telemetry_meta, dict):
                telemetry_meta.update({
                    "model_id": model_id,
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "latency_ms": 120.0,
                    "tokens_per_sec": 416.0,
                    "raw_unstripped_content": mock_llm_response,
                    "bounded_user_prompt": user_p,
                })
            return mock_llm_response

        mock_call.side_effect = mock_call_impl

        synth = await run_tema_q_synthesis(
            topic="Falciparum Malaria",
            search_results=sample_search_results,
            skip_lm=False,
            model_id="qwen2.5-7b-instruct",
            heading_wise=True,
            collect_telemetry=True,
        )

        assert "pipeline_telemetry" in synth
        telemetry = synth["pipeline_telemetry"]
        assert telemetry["model_id"] == "qwen2.5-7b-instruct"
        assert len(telemetry["llm_dispatches"]) >= 1

        first_disp = telemetry["llm_dispatches"][0]
        assert first_disp["model_id"] == "qwen2.5-7b-instruct"
        assert first_disp["pass_number"] == 1
        assert "50 mg/kg" in first_disp["source_dosages_and_stages"]
        assert first_disp["retention_score"] >= 0.5


def test_extract_clinical_numbers_and_stages():
    """Verify numeric dosages and staging classifications are extracted correctly."""
    text = "Treatment of Grade 3 nephrotoxicity requires 100 mg/kg/day methylprednisolone and 5 mcg/kg/min dopamine."
    tokens = extract_clinical_numbers_and_stages(text)
    assert "100 mg/kg/day" in tokens
    assert "5 mcg/kg/min" in tokens
    assert "grade 3" in tokens
