import re

# Consolidate into a single pre-compiled pattern for a 1-pass search
_VARIANCE_PATTERN = re.compile(
    r"\b(?:versus|contrast|differs?|controversy|disagree|variance|"
    r"alternative criteria|classification|grade [IVX\d]+|stage [IVX\d]+)\b",
    re.IGNORECASE
)

def detect_clinical_variances(base_text: str, secondary_text: str) -> str:
    """
    Performs fast heuristic keyword and staging mismatch extraction across texts.
    Returns a variance callout string if differences are detected.
    """
    if not secondary_text:
        return ""

    # Single-pass findall is significantly faster than looping multiple patterns.
    # Set comprehension deduplicates identical matches instantly while normalizing case.
    matches = {match.lower() for match in _VARIANCE_PATTERN.findall(secondary_text)}

    if matches:
        variances = [f"Contrasting staging/criteria term observed: '{m}'" for m in matches]
        return "- " + "\n- ".join(variances)

    return ""
