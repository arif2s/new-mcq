from agents.tema_q.auditor import deterministic_mcq_sanity_check, evaluate_audit_verdict
from agents.tema_q.json_utils import extract_and_repair_json
from agents.tema_q.pipeline import run_audited_generation_with_retry
from agents.tema_q.session_processor import prepare_multi_article_context
from agents.tema_q.subtopic_router import deduplicate_subtopics
from agents.tema_q.topic_extractor import parse_and_filter_llm_topics
from core.scheduler import compute_fsrs_retrievability, update_fsrs_card_state


def test_resilient_json_repair_and_topic_extraction():
    noisy_llm = """<think>Let me extract the clinical topics...</think>
    ```json
    {
      "topics": ["does", "exits", "Acute Pericarditis", "restarts", "Cardiac Tamponade",]
    """
    data = extract_and_repair_json(noisy_llm)
    assert "topics" in data

    filtered = parse_and_filter_llm_topics(noisy_llm)
    assert filtered == ["Acute Pericarditis", "Cardiac Tamponade"]


def test_subtopic_clustering_and_deduplication():
    raw_subtopics = [
        "Coronary Artery",
        "coronary arteries",
        "does",
        "Renal Tubular Acidosis",
        "renal tubular acidoses!",
    ]
    deduped = deduplicate_subtopics(raw_subtopics)
    assert deduped == ["Coronary Artery", "Renal Tubular Acidosis"]


def test_three_article_budget_enforcement_and_deduplication():
    shared_para = "Beck triad consists of hypotension, muffled heart sounds, and jugular venous distension."
    art1 = {"source_type": "PDF", "title": "Harrison", "content": shared_para + "\n\n" + ("word " * 600)}
    art2 = {"source_type": "ZIM", "title": "WikiMed", "content": shared_para + "\n\n" + ("alpha " * 600)}
    art3 = {"source_type": "Obsidian", "title": "CardioNotes", "content": "beta " * 600}

    prepared = prepare_multi_article_context(
        [art1, art2, art3],
        max_words_per_article=500,
        max_total_words=1000,
    )
    assert len(prepared) == 3
    assert all(p["word_count"] <= 500 for p in prepared)
    assert sum(p["word_count"] for p in prepared) <= 1000
    # Shared paragraph should appear in art1 but be deduplicated from art2
    assert "Beck triad" in prepared[0]["content"]
    assert "Beck triad" not in prepared[1]["content"]


def test_auditor_rubric_and_self_correction_loop():
    calls = 0

    def _mock_gen(critique: str | None):
        nonlocal calls
        calls += 1
        if critique is None:
            # First attempt has duplicate options
            return {
                "question": "Which nerve is compressed in carpal tunnel syndrome?",
                "options": ["Median nerve", "Median nerve", "Ulnar nerve", "Radial nerve"],
                "correct_answer": "Median nerve",
            }
        return {
            "question": "Which nerve is compressed in carpal tunnel syndrome?",
            "options": ["Median nerve", "Ulnar nerve", "Radial nerve", "Musculocutaneous nerve"],
            "correct_answer": "Median nerve",
        }

    def _mock_audit(_mcq):
        return '{"stem_clarity": 0.95, "single_correct_answer": 1.0, "distractor_plausibility": 0.9, "source_grounding": 0.9, "overall_pass": true, "critique": ""}'

    final_mcq = run_audited_generation_with_retry(_mock_gen, _mock_audit, max_retries=1)
    assert calls == 2
    assert final_mcq["audit_passed"] is True
    assert len(set(final_mcq["options"])) == 4

    failed_verdict = evaluate_audit_verdict('{"stem_clarity": 0.4, "single_correct_answer": 0.9}')
    assert failed_verdict.overall_pass is False


def test_fsrs_scheduler_intervals_and_response_time_penalty():
    s0 = update_fsrs_card_state(None, rating=3)
    assert s0["stability"] > 0
    assert s0["interval_days"] >= 1

    # Fast Good answer vs. Slow Good answer (>45s lucky guess penalty)
    fast_good = update_fsrs_card_state(s0, rating=4, elapsed_days=3.0, response_time_ms=4000)
    slow_good = update_fsrs_card_state(s0, rating=4, elapsed_days=3.0, response_time_ms=60000)
    assert fast_good["effective_rating"] == 4
    assert slow_good["effective_rating"] == 3
    assert fast_good["stability"] > slow_good["stability"]

    # Forgetting curve decreases monotonically with elapsed days
    r_day1 = compute_fsrs_retrievability(1.0, 10.0)
    r_day30 = compute_fsrs_retrievability(30.0, 10.0)
    assert 0.0 < r_day30 < r_day1 <= 1.0
