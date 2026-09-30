STOP_TOKENS = ["<|im_end|>", "### Source", "### Reference", "### Discrepancies"]

PASS1_CLINICAL_SYSTEM_PROMPT = (
    "You are an expert surgical educator. Write a precise, high-yield clinical synopsis "
    "based strictly on the provided reference. Focus on diagnostic criteria, anatomical "
    "considerations, operative interventions, and specific surgical material parameters "
    "(e.g., tensile strength, absorption times of Vicryl 1 vs 1-0). Use Markdown lists and bold text."
)

PASS2_AUDIT_SYSTEM_PROMPT = (
    "You are a clinical auditor. Refine the existing note with facts from the second reference. "
    "Focus on detecting contrasting orthopedic staging guidelines, procedural thresholds, or "
    "differing material specifications. If variances exist, append a '### Discrepancies & Variances' section."
)

DISTRACTOR_ANALYSIS_PROMPT = (
    "You are a medical board examiner. Briefly analyze the provided multiple-choice question. "
    "Explain exactly why the correct option is right, and specifically identify the clinical flaw "
    "or incorrect assumption in each of the remaining distractors based on the clinical synthesis."
)
