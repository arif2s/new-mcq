import re

with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

# Replace the Pass 1 base note call with parsing logic
old_pass1_code = """
    pass1_user_msg = f"Clinical Topic: {topic}\\n\\nPrimary Reference:\\n{bounded_doc1}"
    base_note = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, 350)

    final_article = base_note
"""

new_pass1_code = """
    # Pass 1: Base Note Generation as JSON
    pass1_user_msg = f"Clinical Topic: {topic}\\n\\nPrimary Reference:\\n{bounded_doc1}"
    base_note_str = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, 600)
    base_note_json = parse_llm_json(base_note_str)

    # Append source info for pass 1 if missing
    for pt in base_note_json.get("clinical_points", []):
        if not pt.get("source"):
            pt["source"] = "Primary Reference"
            pt["page"] = 1

    final_article = base_note_json
"""

if "base_note = await call_llm_async" in content:
    content = content.replace(old_pass1_code.strip(), new_pass1_code.strip())

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(content)
