with open('agents/tema_q/prompts.py', 'r') as f:
    content = f.read()

old_pass2 = """PASS2_AUDIT_SYSTEM_PROMPT = (
    "You are an academic medical auditor. Refine the existing Base Note with facts from the Second Reference. "
    "You must detect differing staging guidelines, diagnostic thresholds, or dosages between the two. "
    "You MUST append a dedicated '### Discrepancies & Variances' section at the bottom explicitly citing both sources."
)"""

new_pass2 = """PASS2_AUDIT_SYSTEM_PROMPT = (
    "Merge these clinical points for '{subtopic}'. Retain all conditions. "
    "If drug dosages or diagnostic cutoffs conflict, output a flag labeled '### Discrepancies & Variances'. "
    "Do not hallucinate. Output the merged points as a JSON array of objects matching: "
    '[{"point": "string", "source": "string", "page": 1}]'
)"""

content = content.replace(old_pass2, new_pass2)

with open('agents/tema_q/prompts.py', 'w') as f:
    f.write(content)
