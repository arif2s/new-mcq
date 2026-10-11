import re

with open('agents/tema_q/auditor.py', 'r') as f:
    content = f.read()

new_content = """
import re

# Consolidate into a single pre-compiled pattern for a 1-pass search
_VARIANCE_PATTERN = re.compile(
    r"\\b(?:versus|contrast|differs?|controversy|disagree|variance|"
    r"alternative criteria|classification|grade [IVX\\d]+|stage [IVX\\d]+)\\b",
    re.IGNORECASE
)

def detect_clinical_variances(base_text: str, secondary_text: str) -> str:
    \"\"\"
    Performs fast heuristic keyword and staging mismatch extraction across texts.
    Returns a variance callout string if differences are detected.
    \"\"\"
    if not secondary_text:
        return ""

    matches = {match.lower() for match in _VARIANCE_PATTERN.findall(secondary_text)}

    if matches:
        variances = [f"Contrasting staging/criteria term observed: '{m}'" for m in matches]
        return "- " + "\\n- ".join(variances)

    return ""

def merge_json_nodes(base_json: dict, second_json: dict, metadata: dict) -> dict:
    # We will let LLM handle the merging of points for matching subtopics.
    # But here we can define the helper if needed. Actually, the prompt says:
    # "Match subtopic nodes programmatically in Python. Do not use the LLM for this mapping."
    pass
"""

with open('agents/tema_q/auditor.py', 'w') as f:
    f.write(new_content)
