# File: tests/unit/test_rag_microedit_and_context_window.py
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest

from core.model_lifecycle import ModelLifecycleManager, model_lifecycle
from agents.tema_q.pipeline import (
    extract_topic_exclusive_passages,
    parse_llm_json,
    run_tema_q_synthesis,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def with_timeout(seconds: float = 5.0):
    """Hard per-test execution timeout decorator without external plugins."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fn, *args, **kwargs)
                return future.result(timeout=seconds)
        return wrapper
    return decorator


@with_timeout(5.0)
def test_context_window_auto_detection_and_asymmetric_word_budgets():
    """Issues #12 & #13: Verify LM Studio loaded_context_length auto-scaling and manual override."""
    mgr = ModelLifecycleManager("http://localhost:1234/v1")
    assert mgr.base_url == "http://localhost:1234"

    # Case 1: 8192 loaded context tokens in LM Studio, no manual override
    info_8k = mgr.calculate_budgets_from_tokens(loaded_ctx=8192, max_ctx=32768, model_id="qwen2.5-7b")
    assert info_8k["loaded_context_length_tokens"] == 8192
    # Summarization gets a large passage budget (>3500 words), while micro-editing stays compact (150..280 words)
    assert info_8k["effective_summary_word_limit"] >= 3500
    assert 150 <= info_8k["microedit_word_limit"] <= 280
    assert info_8k["effective_summary_word_limit"] > info_8k["microedit_word_limit"] * 10

    # Case 2: User sets a manual word limit override of 900 words
    info_override = mgr.calculate_budgets_from_tokens(
        loaded_ctx=8192, max_ctx=32768, model_id="qwen2.5-7b", user_max_words_override=900
    )
    assert info_override["effective_summary_word_limit"] == 900
    assert info_override["microedit_word_limit"] == 150


@with_timeout(5.0)
def test_extract_topic_exclusive_passages_filters_off_topic_sections():
    """Issue #11: Verify only sections pertaining to the target topic are retained for LM Studio."""
    mixed_article = (
        "# Amyloidosis\n"
        "Amyloidosis is characterized by extracellular deposition of misfolded fibrillar proteins "
        "showing apple-green birefringence on Congo red stain under polarized light.\n\n"
        "## Treatment of Amyloidosis\n"
        "First-line therapy for AL amyloidosis includes daratumumab, cyclophosphamide, bortezomib, "
        "and dexamethasone (Dara-CyBorD) at 1.3 mg/m2.\n\n"
        "# Sarcoidosis\n"
        "Sarcoidosis is a multisystem disorder characterized by non-caseating granulomas in the hilar "
        "lymph nodes and elevated serum ACE levels.\n\n"
        "# Ulcerative Colitis\n"
        "Ulcerative colitis causes continuous mucosal inflammation starting in the rectum."
    )

    gated_summary = extract_topic_exclusive_passages(
        mixed_article, target_topic="Amyloidosis", max_words=600, is_microedit=False
    )
    assert "Congo red" in gated_summary
    assert "Dara-CyBorD" in gated_summary
    assert "non-caseating granulomas" not in gated_summary
    assert "Ulcerative colitis" not in gated_summary

    # Verify microedit word cap is strictly enforced
    long_amyloid_text = " ".join(["Amyloidosis fibrils bind Congo red stain."] * 60)
    gated_micro = extract_topic_exclusive_passages(
        long_amyloid_text, target_topic="Amyloidosis", max_words=180, is_microedit=True
    )
    assert len(gated_micro.split()) <= 180


@with_timeout(5.0)
def test_dual_synthesis_modes_tantivy_microedit_vs_per_article_summary():
    """Issues #1 & #11: Verify both tantivy_microedit and per_article_lm_summary execute cleanly."""
    fake_docs = [
        {
            "source_type": "zim",
            "source_label": "ZIM: Amyloidosis",
            "page": 1,
            "header": "Source (ZIM - Amyloidosis):\n# Amyloidosis\nCongo red stain shows apple-green birefringence.",
            "raw_text": "# Amyloidosis\nCongo red stain shows apple-green birefringence under polarized light.",
        },
        {
            "source_type": "pdf",
            "source_label": "Harrison.pdf",
            "page": 42,
            "header": "Source (PDF - Harrison.pdf - Page 42):\n# Treatment\nBortezomib 1.3 mg/m2 is used in AL amyloidosis.",
            "raw_text": "# Treatment\nBortezomib 1.3 mg/m2 is used in AL amyloidosis.\n\n# Sarcoidosis\nNon-caseating granulomas.",
        },
    ]

    mock_llm_response = json.dumps({
        "section": "Clinical Summary",
        "subtopic": "Amyloidosis",
        "conditional_context": "Systemic AL",
        "clinical_points": [
            {"point": "Congo red stain demonstrates apple-green birefringence.", "source": "ZIM: Amyloidosis", "page": 1},
            {"point": "Bortezomib 1.3 mg/m2 is first-line for AL amyloidosis.", "source": "Harrison.pdf", "page": 42},
        ],
        "discrepancies": "",
    })

    async def _run():
        with patch("agents.tema_q.pipeline.resolve_documents", new=AsyncMock(return_value=fake_docs)), \
             patch("agents.tema_q.pipeline.call_llm_async", new=AsyncMock(return_value=mock_llm_response)):
            res_micro = await run_tema_q_synthesis(
                topic="Amyloidosis",
                search_results={"zim": [{"title": "Amyloidosis"}], "pdf": [{"title": "Harrison.pdf"}]},
                synthesis_mode="tantivy_microedit",
                user_max_words_override=800,
            )
            assert res_micro["synthesis_mode"] == "tantivy_microedit"
            assert "apple-green birefringence" in res_micro["enhanced_explanation"]

            res_per_art = await run_tema_q_synthesis(
                topic="Amyloidosis",
                search_results={"zim": [{"title": "Amyloidosis"}], "pdf": [{"title": "Harrison.pdf"}]},
                synthesis_mode="per_article_lm_summary",
                user_max_words_override=1200,
            )
            assert res_per_art["synthesis_mode"] == "per_article_lm_summary"
            assert len(res_per_art.get("per_article_summaries", [])) == 2
            assert "ZIM: Amyloidosis" in res_per_art["enhanced_explanation"]
            assert "Harrison.pdf" in res_per_art["enhanced_explanation"]

    asyncio.run(_run())


@with_timeout(5.0)
def test_ui_contains_synthesis_mode_and_word_limit_controls():
    """Verify frontend files expose the tantivy_microedit vs per_article_lm_summary toggle and word limit input."""
    index_html = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")
    search_js = (ROOT_DIR / "ui" / "js" / "search.js").read_text(encoding="utf-8")
    hub_js = (ROOT_DIR / "ui" / "js" / "hub.js").read_text(encoding="utf-8")
    state_js = (ROOT_DIR / "ui" / "js" / "state.js").read_text(encoding="utf-8")
    combined = "\n".join([index_html, search_js, hub_js, state_js])

    assert "tantivy_microedit" in combined
    assert "per_article_lm_summary" in combined
    assert "lm-word-limit-override" in combined or "lmMaxWordsOverride" in combined
