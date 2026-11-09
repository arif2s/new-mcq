"""
agents/tema_q/auditor.py - Primary clinical discrepancy, staging variance detection,
and deterministic JSON node merger engine.
Enforces zero VRAM usage, precise numeric dosage/staging divergence extraction,
and balanced token Jaccard deduplication (>= 0.75) for clinical JSON node merges.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import re
from typing import Any, Dict, Generator, List, Optional, Set, Tuple


@contextmanager
def enforce_cpu_context() -> Generator[None, None, None]:
    """Scoped execution context that masks GPU devices without permanent process pollution."""
    previous_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    try:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        yield
    finally:
        if previous_cuda is not None:
            os.environ["CUDA_VISIBLE_DEVICES"] = previous_cuda
        else:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)

_VARIANCE_PATTERN = re.compile(
    r"\b(?:versus|vs\.?|contrast|differs?|controversy|disagree|variance|"
    r"alternative criteria|discordance|conflicting|divergence)\b",
    re.IGNORECASE,
)

_STAGING_PATTERN = re.compile(
    r"\b(grade|stage|nyha\s+class|ckd\s+stage|child[- ]pugh|curb[- ]65)\s+([IVX\d]+(?:[a-d])?)\b",
    re.IGNORECASE,
)

_NUMERIC_DOSAGE_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(mg/kg(?:/day)?|mcg/kg(?:/min)?|mg|g|mcg|ml|l|mmol/l|meq/l|mmhg|mm|cm|%|iu|units?)\b",
    re.IGNORECASE,
)

_ROMAN_TO_ARABIC = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "ia": "1a",
    "ib": "1b",
    "iia": "2a",
    "iib": "2b",
    "iiia": "3a",
    "iiib": "3b",
    "iiic": "3c",
    "iva": "4a",
    "ivb": "4b",
}


def _normalize_text(text: str) -> str:
    """Normalizes text for linguistic comparison and deduplication."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _calculate_token_jaccard(s1: str, s2: str) -> float:
    """Calculates token Jaccard similarity between two normalized strings."""
    t1 = set(s1.split())
    t2 = set(s2.split())
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _count_words(text: str) -> int:
    return len((text or "").split())


def _token_containment(short_norm: str, long_norm: str) -> float:
    """Computes fraction of tokens in short_norm that are present in long_norm."""
    t_short = {w for w in short_norm.split() if len(w) >= 3}
    t_long = {w for w in long_norm.split() if len(w) >= 3}
    if not t_short or not t_long:
        return 0.0
    return len(t_short & t_long) / len(t_short)


def filter_novel_secondary_sentences(
    secondary_text: str,
    base_points: List[Dict[str, Any]],
    max_words: int = 400,
    telemetry: Optional[Any] = None,
    source_label: str = "",
) -> str:
    """
    CPU pre-filter for Tema_Q Pass 2: strips sentences from secondary_text that are
    already covered by base_points unless they contain novel numeric dosages or staging values.
    """
    if not secondary_text or not secondary_text.strip():
        return ""
    if not base_points:
        words = secondary_text.split()
        return " ".join(words[:max_words])

    base_norms = [_normalize_text(pt.get("point", "")) for pt in base_points if pt.get("point")]
    base_combined = " ".join(pt.get("point", "") for pt in base_points if pt.get("point"))
    base_nums = {val.lower() for val, _ in _NUMERIC_DOSAGE_PATTERN.findall(base_combined)}

    sents = re.split(r"(?<=[.!?])\s+", secondary_text.strip())
    novel_sents: List[str] = []
    used_words = 0

    for s in sents:
        s_clean = s.strip()
        if not s_clean:
            continue
        s_norm = _normalize_text(s_clean)
        if not s_norm:
            continue

        s_nums = {val.lower() for val, _ in _NUMERIC_DOSAGE_PATTERN.findall(s_clean)}
        has_new_numbers = bool(s_nums - base_nums) or bool(_VARIANCE_PATTERN.search(s_clean))

        max_jaccard = max((_calculate_token_jaccard(s_norm, bn) for bn in base_norms), default=0.0)
        max_containment = max((_token_containment(s_norm, bn) for bn in base_norms), default=0.0)
        is_covered = max_jaccard >= 0.72 or max_containment >= 0.80

        if is_covered and not has_new_numbers:
            if telemetry is not None and hasattr(telemetry, "pass2_pre_filtered_sentences"):
                telemetry.pass2_pre_filtered_sentences.append({
                    "sentence": s_clean,
                    "reason": "duplicate_or_subsumed",
                    "source_label": source_label,
                    "jaccard_score": round(max_jaccard, 3),
                    "containment_score": round(max_containment, 3),
                })
            continue

        w_cnt = len(s_clean.split())
        if used_words + w_cnt > max_words and novel_sents:
            if telemetry is not None and hasattr(telemetry, "pass2_pre_filtered_sentences"):
                telemetry.pass2_pre_filtered_sentences.append({
                    "sentence": s_clean,
                    "reason": "budget_exceeded",
                    "source_label": source_label,
                    "word_count": w_cnt,
                })
            break
        novel_sents.append(s_clean)
        used_words += w_cnt

    return " ".join(novel_sents).strip()


def _enforce_points_word_budget(
    points: List[Dict[str, Any]], max_words: int
) -> List[Dict[str, Any]]:
    """Bounds clinical_points to max_words while prioritizing numeric/staging bullets."""
    bounded: List[Dict[str, Any]] = []
    running_words = 0
    for pt in points:
        txt = str(pt.get("point") or "").strip()
        if not txt:
            continue
        w_cnt = _count_words(txt)
        if running_words + w_cnt <= max_words:
            bounded.append(pt)
            running_words += w_cnt
        else:
            remaining = max_words - running_words
            if remaining >= 12:
                truncated_txt = " ".join(txt.split()[:remaining]).rstrip(".,;:") + "."
                pt_copy = dict(pt)
                pt_copy["point"] = truncated_txt
                bounded.append(pt_copy)
            break
    return bounded


def _extract_canonical_stages(text: str) -> Dict[str, Set[str]]:
    """Extracts staging/grading mentions normalized across Roman and Arabic numerals."""
    stages_by_prefix: Dict[str, Set[str]] = {}
    for raw_prefix, raw_val in _STAGING_PATTERN.findall(text or ""):
        prefix = re.sub(r"[\s-]+", " ", raw_prefix.lower().strip())
        val_low = raw_val.lower().strip()
        norm_val = _ROMAN_TO_ARABIC.get(val_low, val_low)
        stages_by_prefix.setdefault(prefix, set()).add(norm_val)
    return stages_by_prefix


def detect_clinical_variances(base_text: str, secondary_text: str) -> str:
    """
    Performs fast heuristic keyword, staging, and dosage mismatch extraction across clinical texts.
    Returns a formatted Markdown variance callout string if differences are detected.
    """
    if not secondary_text or not str(secondary_text).strip():
        return ""

    base_str = str(base_text or "").strip()
    sec_str = str(secondary_text).strip()
    variances: List[str] = []

    # 1. Detect explicit contrastive markers in secondary text
    matches = {m.lower() for m in _VARIANCE_PATTERN.findall(sec_str)}
    for m in sorted(matches):
        variances.append(f"Contrasting criteria or variance marker observed: '{m}'")

    # 2. Extract and compare staging classifications and numeric thresholds
    if base_str:
        base_stages = _extract_canonical_stages(base_str)
        sec_stages = _extract_canonical_stages(sec_str)

        shared_stage_types = set(base_stages.keys()) & set(sec_stages.keys())
        for st_type in sorted(shared_stage_types):
            b_set = base_stages[st_type]
            s_set = sec_stages[st_type]
            if b_set != s_set:
                b_stage_str = ", ".join(f"{st_type} {v}".upper() for v in sorted(b_set))
                s_stage_str = ", ".join(f"{st_type} {v}".upper() for v in sorted(s_set))
                variances.append(
                    f"Conflicting staging/grading criteria: Primary [{b_stage_str}] vs Secondary [{s_stage_str}]"
                )

        # Extract dosages mapped by clinical unit
        base_dosages: Dict[str, Set[str]] = {}
        for val, unit in _NUMERIC_DOSAGE_PATTERN.findall(base_str):
            u_norm = "iu" if unit.lower().startswith("unit") else unit.lower().strip()
            base_dosages.setdefault(u_norm, set()).add(val.strip())

        sec_dosages: Dict[str, Set[str]] = {}
        for val, unit in _NUMERIC_DOSAGE_PATTERN.findall(sec_str):
            u_norm = "iu" if unit.lower().startswith("unit") else unit.lower().strip()
            sec_dosages.setdefault(u_norm, set()).add(val.strip())

        # Check for diverging dosage values under shared clinical units
        shared_units = set(base_dosages.keys()) & set(sec_dosages.keys())
        for u in sorted(shared_units):
            b_vals = base_dosages[u]
            s_vals = sec_dosages[u]
            if b_vals != s_vals and (s_vals - b_vals):
                b_str = ", ".join(f"{v} {u}" for v in sorted(b_vals))
                s_str = ", ".join(f"{v} {u}" for v in sorted(s_vals))
                variances.append(
                    f"Divergent dosing/threshold for '{u}': Primary [{b_str}] vs Secondary [{s_str}]"
                )

    if variances:
        unique_variances = list(dict.fromkeys(variances))
        return "- " + "\n- ".join(unique_variances)

    return ""


def _coerce_clinical_point(
    pt: Any, default_source: str, default_page: int
) -> Optional[Dict[str, Any]]:
    """Normalizes a clinical point item across dict and str representations."""
    if isinstance(pt, str):
        clean_pt = pt.strip()
        if not clean_pt:
            return None
        return {"point": clean_pt, "source": default_source, "page": default_page}

    if isinstance(pt, dict):
        pt_text = str(
            pt.get("point")
            or pt.get("text")
            or pt.get("fact")
            or pt.get("content")
            or ""
        ).strip()
        if not pt_text:
            return None
        raw_page = pt.get("page") or pt.get("page_number") or default_page
        try:
            page_int = max(1, int(raw_page))
        except (ValueError, TypeError):
            page_int = default_page
        return {
            "point": pt_text,
            "source": str(pt.get("source") or pt.get("source_article") or default_source),
            "page": page_int,
        }
    return None


def _extract_node_text(node: Dict[str, Any], points: List[Dict[str, Any]]) -> str:
    """Extracts combined text from a JSON node for variance comparison."""
    parts = [pt["point"] for pt in points if pt.get("point")]
    raw = str(node.get("raw_text") or node.get("text") or "").strip()
    if raw:
        parts.append(raw)
    return " ".join(parts).strip()


def merge_json_nodes(
    base_json: Optional[Dict[str, Any]],
    second_json: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    max_words: int = 1000,
    primary_max_words: int = 500,
) -> Dict[str, Any]:
    """
    Matches and merges clinical JSON subtopic nodes deterministically in Python (Section 3).
    Combines clinical points using token Jaccard similarity (>= 0.75) deduplication,
    attributes source/page metadata, merges conditional contexts, attaches variance audits,
    and enforces hard word budgets (primary_max_words=500, max_words=1000).
    """
    base = base_json if isinstance(base_json, dict) else {}
    sec = second_json if isinstance(second_json, dict) else {}

    if not base and not sec:
        return {
            "section": "General",
            "subtopic": "Clinical Summary",
            "conditional_context": "",
            "clinical_points": [],
            "discrepancies": "",
        }

    meta_dict = metadata if isinstance(metadata, dict) else {}
    meta_source = str(meta_dict.get("source") or meta_dict.get("title") or "Secondary Reference")
    try:
        meta_page = max(1, int(meta_dict.get("page") or meta_dict.get("page_number") or 1))
    except (ValueError, TypeError):
        meta_page = 1

    primary_source = str(base.get("source") or "Primary Reference")
    try:
        primary_page = max(1, int(base.get("page") or 1))
    except (ValueError, TypeError):
        primary_page = 1

    if not base:
        merged = dict(sec)
        merged["section"] = str(sec.get("section") or "Clinical Summary")
        merged["subtopic"] = str(sec.get("subtopic") or "General")
        merged["conditional_context"] = str(sec.get("conditional_context") or "").strip()
        merged["discrepancies"] = str(
            sec.get("discrepancies") or sec.get("variance_audit") or ""
        ).strip()
        pts: List[Dict[str, Any]] = []
        for raw_pt in sec.get("clinical_points", []) or []:
            coerced = _coerce_clinical_point(raw_pt, meta_source, meta_page)
            if coerced:
                pts.append(coerced)
        merged["clinical_points"] = _enforce_points_word_budget(pts, primary_max_words)
        return merged

    base_points: List[Dict[str, Any]] = []
    for raw_pt in base.get("clinical_points", []) or []:
        coerced = _coerce_clinical_point(raw_pt, primary_source, primary_page)
        if coerced:
            base_points.append(coerced)

    if not sec:
        merged = dict(base)
        merged["section"] = str(base.get("section") or "Clinical Summary")
        merged["subtopic"] = str(base.get("subtopic") or "General")
        merged["conditional_context"] = str(base.get("conditional_context") or "").strip()
        merged["clinical_points"] = _enforce_points_word_budget(base_points, max_words)
        merged["discrepancies"] = str(
            base.get("discrepancies") or base.get("variance_audit") or ""
        ).strip()
        return merged

    merged: Dict[str, Any] = {
        "section": str(base.get("section") or sec.get("section") or "Clinical Summary"),
        "subtopic": str(base.get("subtopic") or sec.get("subtopic") or "General"),
        "conditional_context": str(base.get("conditional_context") or "").strip(),
        "clinical_points": list(base_points),
        "discrepancies": str(
            base.get("discrepancies") or base.get("variance_audit") or ""
        ).strip(),
    }

    # Preserve raw_text if either node carries prose fallback content
    base_raw = str(base.get("raw_text") or "").strip()
    sec_raw = str(sec.get("raw_text") or "").strip()
    if base_raw or sec_raw:
        if base_raw and sec_raw and _normalize_text(sec_raw) not in _normalize_text(base_raw):
            merged["raw_text"] = f"{base_raw}\n\n{sec_raw}"
        else:
            merged["raw_text"] = base_raw or sec_raw

    sec_context = str(sec.get("conditional_context") or "").strip()
    if sec_context:
        if not merged["conditional_context"]:
            merged["conditional_context"] = sec_context
        elif _normalize_text(sec_context) not in _normalize_text(merged["conditional_context"]):
            merged["conditional_context"] += f"; {sec_context}"

    sec_points: List[Dict[str, Any]] = []
    for raw_pt in sec.get("clinical_points", []) or []:
        coerced = _coerce_clinical_point(raw_pt, meta_source, meta_page)
        if not coerced:
            continue
        sec_points.append(coerced)
        pt_text = coerced["point"]
        norm_text = _normalize_text(pt_text)
        if not norm_text:
            continue

        # In-place micro-upgrade check:
        # If containment >= 0.80 and secondary has numeric dosage/staging while existing does not,
        # upgrade existing point in-place with more specific details.
        upgraded = False
        for ex_pt in merged["clinical_points"]:
            ex_norm = _normalize_text(ex_pt.get("point", ""))
            if not ex_norm:
                continue
            if _token_containment(ex_norm, norm_text) >= 0.80 and bool(
                _NUMERIC_DOSAGE_PATTERN.search(pt_text)
            ) and not bool(_NUMERIC_DOSAGE_PATTERN.search(ex_pt.get("point", ""))):
                ex_pt["point"] = pt_text
                ex_pt["source"] = f"{ex_pt.get('source', '')} + {meta_source}".strip("+ ")
                upgraded = True
                break

        if upgraded:
            continue

        # Balanced deduplication: exact match or token Jaccard overlap >= 0.75
        is_duplicate = any(
            norm_text == _normalize_text(ex_pt.get("point", ""))
            or _calculate_token_jaccard(norm_text, _normalize_text(ex_pt.get("point", ""))) >= 0.75
            for ex_pt in merged["clinical_points"]
        )

        if not is_duplicate:
            merged["clinical_points"].append(coerced)

    # Cap total points at max_words
    merged["clinical_points"] = _enforce_points_word_budget(merged["clinical_points"], max_words)

    # Combine explicit discrepancies from both nodes + heuristic variance detection
    base_full_text = _extract_node_text(base, base_points)
    sec_full_text = _extract_node_text(sec, sec_points)
    variance_callout = detect_clinical_variances(base_full_text, sec_full_text)

    disc_parts: List[str] = []
    for candidate_disc in (
        merged["discrepancies"],
        str(sec.get("discrepancies") or sec.get("variance_audit") or "").strip(),
        variance_callout,
    ):
        if candidate_disc and candidate_disc.lower() not in ("none", "n/a"):
            if all(_normalize_text(candidate_disc) not in _normalize_text(existing) for existing in disc_parts):
                disc_parts.append(candidate_disc)

    merged["discrepancies"] = "\n".join(disc_parts).strip()
    return merged


import math as _math

_WORD_PATTERN = re.compile(r"\w+")


def _extract_tokens(text: str) -> Set[str]:
    return set(_WORD_PATTERN.findall(text.lower()))


def deduplicate_clinical_nodes(
    nodes: List[Dict[str, Any]],
    threshold: float = 0.75
) -> List[Dict[str, Any]]:
    """
    Deduplicates clinical assertions using an inverted token index.
    Reduces complexity from O(K^2 * L) to average-case O(K * L).
    """
    if not nodes:
        return []

    retained_nodes: List[Dict[str, Any]] = []
    retained_token_sets: List[Set[str]] = []
    inverted_index: Dict[str, List[int]] = {}

    for node in nodes:
        point_text = str(node.get("point", ""))
        tokens = _extract_tokens(point_text)
        if not tokens:
            continue

        token_count = len(tokens)
        min_overlap_required = int(_math.ceil(threshold * token_count))

        # Identify candidate node indices that share tokens
        candidate_matches: Dict[int, int] = {}
        for token in tokens:
            if token in inverted_index:
                for candidate_idx in inverted_index[token]:
                    candidate_matches[candidate_idx] = candidate_matches.get(candidate_idx, 0) + 1

        is_duplicate = False
        for cand_idx, shared_count in candidate_matches.items():
            if shared_count >= min_overlap_required:
                candidate_tokens = retained_token_sets[cand_idx]
                intersection_size = len(tokens.intersection(candidate_tokens))
                union_size = token_count + len(candidate_tokens) - intersection_size
                if union_size > 0 and (intersection_size / union_size) >= threshold:
                    is_duplicate = True
                    break

        if not is_duplicate:
            new_idx = len(retained_nodes)
            retained_nodes.append(node)
            retained_token_sets.append(tokens)
            for token in tokens:
                inverted_index.setdefault(token, []).append(new_idx)

    return retained_nodes


__all__ = [
    "detect_clinical_variances",
    "merge_json_nodes",
    "filter_novel_secondary_sentences",
    "deduplicate_clinical_nodes",
]


# --- Phase 5 Rubric-Based MCQ Auditor Validation ---
from typing import Any as _Any
from agents.tema_q.json_utils import AuditVerdictPayload as _AuditVerdictPayload, parse_llm_pydantic as _parse_llm_pydantic


def evaluate_audit_verdict(raw_llm_output: str, min_score_threshold: float = 0.75) -> _AuditVerdictPayload:
    """Parse LLM audit response into a structured rubric verdict and enforce threshold pass logic."""
    verdict = _parse_llm_pydantic(
        raw_llm_output,
        _AuditVerdictPayload,
        default_fallback=_AuditVerdictPayload(overall_pass=False, critique="Malformed audit JSON output"),
    )
    min_metric = min(
        verdict.stem_clarity,
        verdict.single_correct_answer,
        verdict.distractor_plausibility,
        verdict.source_grounding,
    )
    if min_metric < min_score_threshold:
        verdict.overall_pass = False
        if not verdict.critique:
            verdict.critique = f"Audit score {min_metric:.2f} below minimum threshold {min_score_threshold:.2f}."
    return verdict


def deterministic_mcq_sanity_check(mcq: dict[str, _Any]) -> tuple[bool, str]:
    """Fast zero-LLM pre-check ensuring stem, options, and single correct answer are well-formed."""
    question = str(mcq.get("question") or mcq.get("stem") or "").strip()
    options = mcq.get("options") or []
    answer = str(mcq.get("correct_answer") or mcq.get("answer") or "").strip()
    if len(question) < 10:
        return False, "Question stem is too short (<10 chars)."
    if not isinstance(options, list) or len(options) < 2:
        return False, "MCQ must provide at least 2 options."
    norm_opts = [str(o).strip() for o in options if str(o).strip()]
    if len(set(o.lower() for o in norm_opts)) != len(norm_opts):
        return False, "MCQ contains duplicate distractor options."
    if not answer:
        return False, "MCQ is missing a designated correct_answer."
    return True, "OK"


from typing import Callable as _Callable


def run_audited_generation_with_retry(
    generate_fn: _Callable[[Optional[str]], Dict[str, Any]],
    audit_fn: _Callable[[Dict[str, Any]], str],
    max_retries: int = 1,
) -> Dict[str, Any]:
    """
    Executes iterative generation with deterministic sanity checks and rubric audits.
    Guarantees explicit audit verification flags and structured error boundaries.
    """
    critique: Optional[str] = None
    last_candidate: Dict[str, Any] = {}

    for attempt in range(max_retries + 1):
        try:
            candidate = generate_fn(critique)
        except Exception as exc:
            return {
                "audit_passed": False,
                "audit_critique": f"Generation exception raised: {str(exc)}",
                "error": True,
            }

        last_candidate = candidate
        is_sane, sanity_reason = deterministic_mcq_sanity_check(candidate)
        if not is_sane:
            critique = sanity_reason
            candidate["audit_passed"] = False
            candidate["audit_critique"] = critique
            continue

        try:
            raw_audit = audit_fn(candidate)
            verdict = evaluate_audit_verdict(raw_audit)
            candidate["audit_passed"] = bool(verdict.overall_pass)
            candidate["audit_critique"] = verdict.critique
            candidate["audit_scores"] = verdict.model_dump()

            if verdict.overall_pass:
                return candidate
            critique = verdict.critique
        except Exception as exc:
            candidate["audit_passed"] = False
            candidate["audit_critique"] = f"Auditor exception raised: {str(exc)}"
            return candidate

    last_candidate["audit_passed"] = False
    if "audit_critique" not in last_candidate:
        last_candidate["audit_critique"] = "Maximum retries exceeded without satisfying audit criteria."
    return last_candidate
