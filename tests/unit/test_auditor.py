import asyncio
from unittest.mock import AsyncMock, patch
from agents.tema_q.auditor import (
    detect_clinical_variances,
    filter_novel_secondary_sentences,
    merge_json_nodes,
)
from agents.tema_q.json_utils import extract_and_repair_json
from agents.tema_q.pipeline import count_tokens, parse_llm_json, run_tema_q_synthesis
from agents.tema_q.telemetry import PipelineDiagnosticTrace
from config import MAX_CONTEXT_TOKENS


def test_task5_json_auditor_and_pipeline_offline():
    # 1. Verify LIFO JSON repair on outer-array and outer-object truncations
    truncated_arr = '<think>plan</think>[{"topic": "Malaria", "details": {"dose": "100mg"'
    repaired_arr = extract_and_repair_json(truncated_arr)
    assert isinstance(repaired_arr, list)
    assert repaired_arr[0]["details"]["dose"] == "100mg"

    truncated_obj = '{"clinical_points": [{"point": "P1", "source": "ZIM", "page": 1}, {"point": "P2"'
    repaired_obj = parse_llm_json(truncated_obj)
    assert isinstance(repaired_obj, dict)
    assert len(repaired_obj["clinical_points"]) == 2
    assert repaired_obj["clinical_points"][1]["point"] == "P2"

    # 2. Verify Pass 2 secondary sentence pre-filter preserves abbreviations
    sec_prose = (
        "Administer Ceftriaxone 2 g i.v. b.i.d. (e.g. in Lyme meningitis) for 14 days. "
        "Monitor renal function daily."
    )
    filtered = filter_novel_secondary_sentences(
        sec_prose,
        base_points=[{"point": "Monitor renal function daily."}],
    )
    assert "Ceftriaxone 2 g i.v. b.i.d." in filtered
    assert "for 14 days." in filtered
    assert "Monitor renal function" not in filtered

    # 3. Verify comparative clinical variance detection & merge telemetry
    base_txt = "Stage II disease requires 10 mg daily."
    sec_txt = "Stage IV disease requires 25 mg daily; controversy persists regarding adjuvant therapy."
    var_out = detect_clinical_variances(base_txt, sec_txt)
    assert "STAGE 2" in var_out and "STAGE 4" in var_out
    assert "10 mg" in var_out and "25 mg" in var_out
    assert "adjuvant therapy" in var_out

    trace = PipelineDiagnosticTrace(trace_id="test_tr", topic="Lymphoma", model_id="mock")
    merged = merge_json_nodes(
        {"subtopic": "Staging", "clinical_points": [{"point": base_txt, "source": "S1", "page": 1}]},
        {"subtopic": "Staging", "clinical_points": [{"point": sec_txt, "source": "S2", "page": 2}]},
        metadata={"source": "S2", "page": 2},
        telemetry=trace,
    )
    assert len(trace.auditor_merge_events) == 1
    assert merged["discrepancies"] != ""

    # 4. Verify Pass 2 and Pass 3 prompt token budgets stay strictly <= MAX_CONTEXT_TOKENS
    recorded_calls = []

    async def mock_llm(sys_p, user_p, max_tokens=500, **kwargs):
        recorded_calls.append((sys_p, user_p, max_tokens))
        return '{"section":"S","subtopic":"T","conditional_context":"","clinical_points":[{"point":"High-yield clinical point with 50 mg dosing.","source":"ZIM","page":1}],"discrepancies":""}'

    mock_docs = [
        {
            "source_type": "zim",
            "source_label": "ZIM: Amyloidosis",
            "page": 1,
            "header": "Source:\n# Diagnosis\n" + ("Congo red stain shows apple-green birefringence. " * 60),
            "raw_text": "# Diagnosis\n" + ("Congo red stain shows apple-green birefringence. " * 60),
        },
        {
            "source_type": "pdf",
            "source_label": "Harrison.pdf",
            "page": 12,
            "header": "Source:\n# Treatment\n" + ("Bortezomib 1.3 mg/m2 s.c. on days 1, 8, 15, 22. " * 60),
            "raw_text": "# Treatment\n" + ("Bortezomib 1.3 mg/m2 s.c. on days 1, 8, 15, 22. " * 60),
        },
    ]
    mcq_context = {
        "question": "What is the biopsy stain of choice?",
        "options": {"A": "Congo red", "B": "PAS", "C": "GMS", "D": "Ziehl-Neelsen"},
        "correct_answer": "A",
    }

    with patch("agents.tema_q.pipeline.resolve_documents", new=AsyncMock(return_value=mock_docs)), \
         patch("agents.tema_q.pipeline.call_llm_async", side_effect=mock_llm):
        res = asyncio.run(run_tema_q_synthesis("Amyloidosis", {}, mcq_context=mcq_context, collect_telemetry=True))

    assert len(recorded_calls) >= 2
    for sys_p, user_p, max_out in recorded_calls:
        assert count_tokens(sys_p) + count_tokens(user_p) + max_out <= MAX_CONTEXT_TOKENS
    assert "pipeline_telemetry" in res
