import re
from typing import Dict, Any, Optional, List

_VARIANCE_PATTERN = re.compile(
    r"\b(?:versus|vs\.?|contrast|differs?|controversy|disagree|variance|"
    r"alternative criteria|classification|grade\s+[IVX\d]+|stage\s+[IVX\d]+)\b",
    re.IGNORECASE
)

_THRESHOLD_PATTERN = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|l|mmol/l|meq/l|mm|cm|%|units?|iu|days?|hours?|weeks?)|"
    r"grade\s+[IVX\d]+|stage\s+[IVX\d]+)\b",
    re.IGNORECASE
)


def detect_clinical_variances(base_text: str, secondary_text: str) -> str:
    """
    Performs fast heuristic keyword and staging/threshold mismatch extraction across texts.
    Returns a variance callout string if differences are detected.
    """
    if not secondary_text:
        return ""

    variances: List[str] = []

    matches = {m.lower() for m in _VARIANCE_PATTERN.findall(secondary_text)}
    if matches:
        for m in sorted(matches):
            variances.append(f"Contrasting staging/criteria term observed: '{m}'")

    if base_text:
        base_thresholds = {t.lower() for t in _THRESHOLD_PATTERN.findall(base_text)}
        sec_thresholds = {t.lower() for t in _THRESHOLD_PATTERN.findall(secondary_text)}

        divergent = sec_thresholds - base_thresholds
        if divergent and base_thresholds:
            base_grades = {t for t in base_thresholds if t.startswith(("grade", "stage"))}
            sec_grades = {t for t in sec_thresholds if t.startswith(("grade", "stage"))}
            if base_grades and sec_grades and base_grades != sec_grades:
                variances.append(
                    f"Conflicting staging/grading criteria: Primary [{', '.join(sorted(base_grades))}] "
                    f"vs Secondary [{', '.join(sorted(sec_grades))}]"
                )

    if variances:
        unique_variances = list(dict.fromkeys(variances))
        return "- " + "\n- ".join(unique_variances)

    return ""


def _normalize_text(text: str) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def merge_json_nodes(
    base_json: Dict[str, Any],
    second_json: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Matches and merges clinical JSON subtopic nodes deterministically in Python.
    Prevents LLM mapping hallucination and combines discrete clinical points with source citations.
    """
    if not base_json and not second_json:
        return {
            "section": "General",
            "subtopic": "Clinical Summary",
            "conditional_context": "",
            "clinical_points": []
        }

    if not base_json:
        merged = dict(second_json)
        if metadata:
            for pt in merged.get("clinical_points", []):
                pt.setdefault("source", metadata.get("source", "Secondary Reference"))
                pt.setdefault("page", metadata.get("page", 1))
        return merged

    if not second_json:
        return dict(base_json)

    merged: Dict[str, Any] = {
        "section": base_json.get("section") or second_json.get("section") or "Clinical Summary",
        "subtopic": base_json.get("subtopic") or second_json.get("subtopic") or "General",
        "conditional_context": base_json.get("conditional_context", ""),
        "clinical_points": list(base_json.get("clinical_points", [])),
    }

    sec_context = second_json.get("conditional_context", "")
    if sec_context:
        if not merged["conditional_context"]:
            merged["conditional_context"] = sec_context
        elif _normalize_text(sec_context) not in _normalize_text(merged["conditional_context"]):
            merged["conditional_context"] += f"; {sec_context}"

    existing_point_norms = {
        _normalize_text(pt.get("point", ""))
        for pt in merged["clinical_points"]
        if pt.get("point")
    }

    meta_source = (metadata or {}).get("source", "Secondary Reference")
    meta_page = (metadata or {}).get("page", 1)

    for pt in second_json.get("clinical_points", []):
        if not isinstance(pt, dict):
            continue
        pt_text = pt.get("point", "").strip()
        if not pt_text:
            continue

        norm_text = _normalize_text(pt_text)
        if not norm_text:
            continue

        is_duplicate = norm_text in existing_point_norms or any(
            norm_text in existing_norm or existing_norm in norm_text
            for existing_norm in existing_point_norms
            if existing_norm
        )

        if not is_duplicate:
            merged["clinical_points"].append({
                "point": pt_text,
                "source": pt.get("source") or meta_source,
                "page": pt.get("page") or meta_page,
            })
            existing_point_norms.add(norm_text)

    base_full_text = " ".join(pt.get("point", "") for pt in base_json.get("clinical_points", []) if isinstance(pt, dict))
    sec_full_text = " ".join(pt.get("point", "") for pt in second_json.get("clinical_points", []) if isinstance(pt, dict))

    variance_callout = detect_clinical_variances(base_full_text, sec_full_text)
    existing_discrepancies = base_json.get("discrepancies", "") or second_json.get("discrepancies", "")

    if variance_callout:
        if existing_discrepancies and variance_callout not in existing_discrepancies:
            merged["discrepancies"] = f"{existing_discrepancies}\n{variance_callout}".strip()
        else:
            merged["discrepancies"] = variance_callout
    elif existing_discrepancies:
        merged["discrepancies"] = existing_discrepancies

    return merged
