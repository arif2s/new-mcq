import re

VARIANCE_TRIGGERS = [
    r"\bversus\b", r"\bcontrast\b", r"\bdiffers?\b", r"\bcontroversy\b",
    r"\bdisagree\b", r"\bvariance\b", r"\balternative criteria\b",
    r"\bclassification\b", r"\bgrade [I|V|X\d]+\b", r"\bstage [I|V|X\d]+\b"
]

def detect_clinical_variances(base_text: str, secondary_text: str) -> str:
    """
    Performs fast heuristic keyword and staging mismatch extraction across texts.
    Returns a variance callout string if differences are detected.
    """
    variances = []
    sec_lower = secondary_text.lower()

    for pattern in VARIANCE_TRIGGERS:
        matches = re.findall(pattern, sec_lower)
        if matches:
            variances.append(f"Contrasting staging/criteria term observed: '{matches[0]}'")

    if variances:
        return "- " + "\n- ".join(set(variances))
    return ""
