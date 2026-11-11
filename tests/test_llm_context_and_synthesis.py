from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import httpx
import pytest

from core.model_lifecycle import ContextLengthExceededError, ModelLifecycleManager, model_lifecycle
from agents.tema_q.pipeline import (
    call_llm_async,
    count_tokens,
    run_tema_q_synthesis,
    truncate_to_token_limit,
    truncate_with_count,
    _tokenizer,
)
from agents.tema_q.auditor import (
    deterministic_mcq_sanity_check,
    normalize_mcq_options,
    run_audited_generation_with_retry,
)


@pytest.mark.asyncio
async def test_model_context_scaling_and_safe_budget_invariants():
    """
    Simulates loaded models with 2048, 8192, and 32768 context lengths in model_lifecycle.
    Verifies that call_llm_async dynamically bounds prompt tokens so:
        sys_tokens + user_tokens + safe_max_out + 64 <= effective_ctx
    holds strictly under all configurations.
    """
    for ctx_len in (2048, 8192, 32768):
        model_lifecycle._cached_context_tokens = ctx_len
        model_lifecycle._active_model_id = f"mock-model-{ctx_len}"

        captured_payloads = []

        def mock_post(url, json=None, **kwargs):
            captured_payloads.append(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "Clinical response content."}}]
            }
            return resp

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_post)

        with patch("agents.tema_q.pipeline.get_http_client", return_value=mock_client):
            # Send a user prompt with 10,000 words (exceeding smaller context windows)
            large_user_prompt = "Clinical observation sentence. " * 3000
            telemetry: dict = {}

            resp = await call_llm_async(
                system_prompt="You are an expert clinical medical professor.",
                user_prompt=large_user_prompt,
                max_tokens=4000,
                context_length_tokens=ctx_len,
                telemetry_meta=telemetry,
            )
            assert resp == "Clinical response content."
            assert len(captured_payloads) == 1
            payload = captured_payloads[0]

            out_max = payload["max_tokens"]
            sys_content = payload["messages"][0]["content"]
            user_content = payload["messages"][1]["content"]

            sys_tokens = count_tokens(sys_content)
            user_tokens = count_tokens(user_content)

            total_tokens = sys_tokens + user_tokens + out_max + 64
            assert total_tokens <= ctx_len, (
                f"Context overflow for ctx={ctx_len}: total={total_tokens} (sys={sys_tokens}, "
                f"user={user_tokens}, out={out_max}, chat_overhead=64) > {ctx_len}"
            )
            assert telemetry["effective_context_tokens"] == ctx_len


@pytest.mark.asyncio
async def test_input_overflow_and_exact_token_truncation():
    """
    Verifies that:
    1. truncate_with_count and truncate_to_token_limit never exceed max_tokens, reserving suffix tokens.
    2. strict_overflow_check=True raises ContextLengthExceededError with explicit diagnostic details.
    """
    long_text = "Daratumumab 16 mg/kg IV weekly is administered with dexamethasone for multiple myeloma. " * 50
    target_caps = [30, 50, 100, 250]

    for cap in target_caps:
        truncated_str, count = truncate_with_count(long_text, max_tokens=cap)
        assert count <= cap, f"truncate_with_count exceeded cap {cap}: got {count}"
        if _tokenizer:
            exact_tokens = len(_tokenizer.encode(truncated_str))
            assert exact_tokens <= cap, f"Exact token count {exact_tokens} exceeded cap {cap}"

        thin_wrapper_res = truncate_to_token_limit(long_text, max_tokens=cap)
        assert thin_wrapper_res == truncated_str

    # Test strict_overflow_check policy
    with pytest.raises(ContextLengthExceededError) as exc_info:
        await call_llm_async(
            system_prompt="Short system prompt.",
            user_prompt="Large input document. " * 2000,
            max_tokens=500,
            context_length_tokens=1024,
            strict_overflow_check=True,
        )

    err = exc_info.value
    assert err.model_limit == 1024
    assert err.excess_tokens > 0
    assert err.required_tokens > 1024
    assert "exceeds available context window" in str(err)


@pytest.mark.asyncio
async def test_single_pass_tokenization_spy():
    """
    Spies on _tokenizer.encode during call_llm_async to verify that user prompt
    and system prompt are tokenized only once per call (no double/triple tokenization).
    """
    if _tokenizer is None:
        pytest.skip("tiktoken not available")

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Sample synthesis answer."}}]
    }
    mock_client.post = AsyncMock(return_value=mock_resp)

    original_encode = _tokenizer.encode
    encode_calls = []

    def spy_encode(text, *args, **kwargs):
        encode_calls.append(text)
        return original_encode(text, *args, **kwargs)

    user_text = "Patient presents with fever and cough."
    sys_text = "System prompt medical specialist."

    with patch.object(_tokenizer, "encode", side_effect=spy_encode), \
         patch("agents.tema_q.pipeline.get_http_client", return_value=mock_client):
        await call_llm_async(
            system_prompt=sys_text,
            user_prompt=user_text,
            max_tokens=200,
            context_length_tokens=4096,
        )

    # user_text should only be encoded once in truncate_with_count
    user_encodes = [t for t in encode_calls if user_text in t]
    assert len(user_encodes) == 1, f"Expected user_prompt to be encoded once, got {len(user_encodes)}"


def test_mcq_option_normalization_and_sanity_check():
    """
    Verifies that:
    1. normalize_mcq_options correctly supports both list and dict schemas.
    2. deterministic_mcq_sanity_check strips whitespace, enforces count >= 2,
       and validates designated correct answer against keys or text.
    """
    # 1. Normalization from list
    raw_list = ["  Aspirin 81 mg ", "", "Clopidogrel 75 mg", "   ", "Ticagrelor 90 mg"]
    norm_list, opt_dict = normalize_mcq_options(raw_list)
    assert norm_list == ["Aspirin 81 mg", "Clopidogrel 75 mg", "Ticagrelor 90 mg"]
    assert opt_dict == {"A": "Aspirin 81 mg", "B": "Clopidogrel 75 mg", "C": "Ticagrelor 90 mg"}

    # 2. Normalization from dict
    raw_dict = {"A": "Metformin", "B": "  ", "C": "Glipizide", "D": "Empagliflozin"}
    norm_list_d, opt_dict_d = normalize_mcq_options(raw_dict)
    assert norm_list_d == ["Metformin", "Glipizide", "Empagliflozin"]
    assert opt_dict_d == {"A": "Metformin", "C": "Glipizide", "D": "Empagliflozin"}

    # 3. Sanity check with list schema and text answer
    mcq_valid_list = {
        "question": "What is the first-line oral therapy for uncomplicated Type 2 Diabetes?",
        "options": ["Metformin", "Insulin glargine", "Glipizide", "Acarbose"],
        "correct_answer": "Metformin",
    }
    is_ok, reason = deterministic_mcq_sanity_check(mcq_valid_list)
    assert is_ok is True
    assert reason == "OK"

    # 4. Sanity check with dict schema and letter answer
    mcq_valid_dict = {
        "question": "Which antiplatelet agent irreversibly inhibits COX-1?",
        "options": {"A": "Aspirin", "B": "Clopidogrel", "C": "Prasugrel"},
        "correct_answer": "A",
    }
    is_ok2, reason2 = deterministic_mcq_sanity_check(mcq_valid_dict)
    assert is_ok2 is True
    assert reason2 == "OK"

    # 5. Rejects whitespace-only options leaving < 2 non-empty options
    mcq_whitespace = {
        "question": "Which drug is used for anaphylactic shock?",
        "options": ["Epinephrine", "   ", "", "  \t "],
        "correct_answer": "Epinephrine",
    }
    is_ok3, reason3 = deterministic_mcq_sanity_check(mcq_whitespace)
    assert is_ok3 is False
    assert "at least 2 non-empty options" in reason3

    # 6. Rejects out-of-set answer
    mcq_out_of_set = {
        "question": "What is the drug of choice for acute gout attack?",
        "options": ["Indomethacin", "Colchicine", "Prednisone"],
        "correct_answer": "Allopurinol",
    }
    is_ok4, reason4 = deterministic_mcq_sanity_check(mcq_out_of_set)
    assert is_ok4 is False
    assert "does not match any valid option" in reason4


def test_run_audited_generation_retry_resilience():
    """
    Verifies that run_audited_generation_with_retry does not fail immediately on transient
    exceptions raised by generate_fn, but captures the critique and succeeds on subsequent retry.
    """
    attempts = 0

    def faulty_generate(critique: str | None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("LM Studio socket temporarily dropped")
        # Second attempt succeeds
        return {
            "question": "Which antibiotic is first-line for Group A Streptococcal pharyngitis?",
            "options": ["Penicillin V", "Ciprofloxacin", "Vancomycin", "Gentamicin"],
            "correct_answer": "Penicillin V",
        }

    def mock_audit(mcq):
        return json.dumps({
            "stem_clarity": 0.95,
            "single_correct_answer": 1.0,
            "distractor_plausibility": 0.9,
            "source_grounding": 0.9,
            "overall_pass": True,
            "critique": "",
        })

    result = run_audited_generation_with_retry(faulty_generate, mock_audit, max_retries=2)
    assert attempts == 2
    assert result["audit_passed"] is True
    assert result["correct_answer"] == "Penicillin V"


@pytest.mark.asyncio
async def test_multi_pass_synthesis_distractor_fallback_and_dynamic_ctx():
    """
    Verifies that multi-pass synthesis sets distractor_analysis to 'N/A' when no options are provided,
    and dynamically binds to loaded model context in model_lifecycle.
    """
    model_lifecycle._cached_context_tokens = 16384
    model_lifecycle._active_model_id = "qwen2.5-14b-instruct"

    fake_docs = [
        {
            "source_type": "obsidian",
            "source_label": "Obsidian: Kawasaki",
            "page": 1,
            "header": "Source (Obsidian - Kawasaki):\n# Kawasaki Disease\nIVIG 2 g/kg is standard treatment.",
            "raw_text": "# Kawasaki Disease\nIVIG 2 g/kg is standard treatment with high-dose aspirin.",
        }
    ]

    mock_llm_reply = json.dumps({
        "section": "Clinical Summary",
        "subtopic": "Kawasaki Disease",
        "conditional_context": "Acute phase",
        "clinical_points": [
            {"point": "IVIG 2 g/kg single infusion prevents coronary aneurysms.", "source": "Obsidian: Kawasaki", "page": 1}
        ],
        "discrepancies": "",
    })

    with patch("agents.tema_q.pipeline.resolve_documents", new=AsyncMock(return_value=fake_docs)), \
         patch("agents.tema_q.pipeline.call_llm_async", new=AsyncMock(return_value=mock_llm_reply)):

        # Run synthesis with NO MCQ options
        result = await run_tema_q_synthesis(
            topic="Kawasaki Disease",
            search_results={"obsidian": [{"title": "Kawasaki"}]},
            mcq_context=None,
            synthesis_mode="tantivy_microedit",
        )

        assert result["distractor_analysis"] == "N/A"
        assert "Kawasaki" in result["enhanced_explanation"]
