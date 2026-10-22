"""
agents/tema_q/auditor.py - Primary clinical discrepancy, staging variance detection,
and deterministic JSON node merger engine.
Enforces zero VRAM usage, precise numeric dosage/staging divergence extraction,
and balanced token Jaccard deduplication (>= 0.75) for clinical JSON node merges.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

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
) -> Dict[str, Any]:
    """
    Matches and merges clinical JSON subtopic nodes deterministically in Python (Section 3).
    Combines clinical points using token Jaccard similarity (>= 0.75) deduplication,
    attributes source/page metadata, merges conditional contexts, and attaches variance audits.
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
        merged["clinical_points"] = pts
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
        merged["clinical_points"] = base_points
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

    existing_point_norms: Dict[str, str] = {
        _normalize_text(pt["point"]): pt["point"]
        for pt in merged["clinical_points"]
        if pt.get("point") and _normalize_text(pt["point"])
    }

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

        # Balanced deduplication: exact match or token Jaccard overlap >= 0.75 (Section 3)
        is_duplicate = norm_text in existing_point_norms or any(
            _calculate_token_jaccard(norm_text, ex_norm) >= 0.75
            for ex_norm in existing_point_norms
        )

        if not is_duplicate:
            merged["clinical_points"].append(coerced)
            existing_point_norms[norm_text] = pt_text

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


__all__ = ["detect_clinical_variances", "merge_json_nodes"]
