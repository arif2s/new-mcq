import re
from typing import Dict, Any, Optional, List

# Pre-compiled regex for clinical discrepancies, staging, and classification terms
_VARIANCE_PATTERN = re.compile(
    r"\b(?:versus|vs\.?|contrast|differs?|controversy|disagree|variance|"
    r"alternative criteria|classification|grade\s+[IVX\d]+|stage\s+[IVX\d]+)\b",
    re.IGNORECASE
)

# Extract numerical thresholds, cutoffs, or dosages (e.g., "50 mg", "3.5 mmol/L", "> 10 mm", "grade 2")
_THRESHOLD_PATTERN = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|l|mmol/l|meq/l|mm|cm|%|units?|iu|days?|hours?|weeks?)|"
    r"grade\s+[IVX\d]+|stage\s+[IVX\d]+)\b",
    re.IGNORECASE
)


def detect_clinical_variances(base_text: str, secondary_text: str) -> str:
    """
    Performs fast heuristic keyword and staging mismatch extraction across texts.
    Returns a variance callout string if differences are detected.
    """
    if not secondary_text:
        return ""

    variances: List[str] = []

    # 1. Keyword variance trigger scan
    matches = {m.lower() for m in _VARIANCE_PATTERN.findall(secondary_text)}
    if matches:
        for m in sorted(matches):
            variances.append(f"Contrasting staging/criteria term observed: '{m}'")

    # 2. Heuristic check for conflicting thresholds or stages between texts
    if base_text:
        base_thresholds = {t.lower() for t in _THRESHOLD_PATTERN.findall(base_text)}
        sec_thresholds = {t.lower() for t in _THRESHOLD_PATTERN.findall(secondary_text)}

        # Detect differing cutoffs/stages between references
        divergent = sec_thresholds - base_thresholds
        if divergent and base_thresholds:
            # Check for conflicting staging or grading specifically
            base_grades = {t for t in base_thresholds if t.startswith(("grade", "stage"))}
            sec_grades = {t for t in sec_thresholds if t.startswith(("grade", "stage"))}
            if base_grades and sec_grades and base_grades != sec_grades:
                variances.append(
                    f"Conflicting staging/grading criteria: Primary [{', '.join(base_grades)}] "
                    f"vs Secondary [{', '.join(sec_grades)}]"
                )

    if variances:
        # Deduplicate while preserving order
        unique_variances = list(dict.fromkeys(variances))
        return "- " + "\n- ".join(unique_variances)

    return ""


def _normalize_text(text: str) -> str:
    """Helper to strip punctuation and normalize whitespace for deduplication."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def merge_json_nodes(base_json: Dict[str, Any], second_json: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Matches and merges clinical JSON subtopic nodes programmatically in Python.
    Prevents LLM mapping hallucination and combines discrete clinical points.

    Schema:
    {
        "section": str,
        "subtopic": str,
        "conditional_context": str,
        "clinical_points": [{"point": str, "source": str, "page": int}],
        "discrepancies": str (optional)
    }
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
        # Apply metadata to incoming points if missing
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

    # 1. Merge Conditional Context
    sec_context = second_json.get("conditional_context", "")
    if sec_context:
        if not merged["conditional_context"]:
            merged["conditional_context"] = sec_context
        elif _normalize_text(sec_context) not in _normalize_text(merged["conditional_context"]):
            merged["conditional_context"] += f"; {sec_context}"

    # 2. Index existing points for O(1) text overlap check
    existing_point_norms = set()
    for pt in merged["clinical_points"]:
        pt_text = pt.get("point", "")
        if pt_text:
            existing_point_norms.add(_normalize_text(pt_text))

    # Default metadata fallbacks
    meta_source = (metadata or {}).get("source", "Secondary Reference")
    meta_page = (metadata or {}).get("page", 1)

    # 3. Programmatically integrate secondary clinical points
    for pt in second_json.get("clinical_points", []):
        pt_text = pt.get("point", "").strip()
        if not pt_text:
            continue

        norm_text = _normalize_text(pt_text)

        # Deduplication: check exact match or substantial sub-phrase overlap
        is_duplicate = False
        if norm_text in existing_point_norms:
            is_duplicate = True
        else:
            for existing_norm in existing_point_norms:
                # If one point is a direct subset of another longer point
                if norm_text in existing_norm or existing_norm in norm_text:
                    is_duplicate = True
                    break

        if not is_duplicate:
            new_point = {
                "point": pt_text,
                "source": pt.get("source") or meta_source,
                "page": pt.get("page") or meta_page
            }
            merged["clinical_points"].append(new_point)
            existing_point_norms.add(norm_text)

    # 4. Detect Clinical Variances across point texts
    base_full_text = " ".join(pt.get("point", "") for pt in base_json.get("clinical_points", []))
    sec_full_text = " ".join(pt.get("point", "") for pt in second_json.get("clinical_points", []))

    variance_callout = detect_clinical_variances(base_full_text, sec_full_text)

    # Merge existing discrepancies if present
    existing_discrepancies = base_json.get("discrepancies", "") or second_json.get("discrepancies", "")
    if variance_callout:
        if existing_discrepancies and variance_callout not in existing_discrepancies:
            merged["discrepancies"] = f"{existing_discrepancies}\n{variance_callout}".strip()
        else:
            merged["discrepancies"] = variance_callout
    elif existing_discrepancies:
        merged["discrepancies"] = existing_discrepancies

    return merged
