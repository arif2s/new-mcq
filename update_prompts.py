with open('agents/tema_q/prompts.py', 'r') as f:
    content = f.read()

old_pass1 = """PASS1_CLINICAL_SYSTEM_PROMPT = (
    "You are an expert clinical surgical educator. Write a concise, high-yield clinical synopsis "
    "based strictly on the provided reference. Focus on diagnostic criteria, anatomical "
    "considerations, operative thresholds, and material specifications. "
    "Use compact Markdown bullet points and bold key findings. Keep the summary under 250 words "
    "to ensure complete, un-truncated output. Do not include introductory conversational filler."
)"""

new_pass1 = """PASS1_CLINICAL_SYSTEM_PROMPT = (
    "You are an expert clinical surgical educator. Write a concise, high-yield clinical synopsis "
    "based strictly on the provided reference. Focus on diagnostic criteria, anatomical "
    "considerations, operative thresholds, and material specifications. "
    "Respond ONLY with a valid JSON object matching this schema:\\n"
    '{"section": "string", "subtopic": "string", "conditional_context": "string", "clinical_points": [{"point": "string", "source": "string", "page": 1}]}'
    "\\nDo not wrap the JSON in markdown blocks. Do not include introductory conversational filler."
)"""

content = content.replace(old_pass1, new_pass1)

with open('agents/tema_q/prompts.py', 'w') as f:
    f.write(content)
