with open('agents/tema_q/pipeline.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if "gate_resp = await call_llm_async" in line and "gate_prompt + f\"" in line:
        new_lines.append('        gate_resp = await call_llm_async("You are a strict relevance filter.", gate_prompt + f"\\n\\nText:\\n{bounded_doc}", 10)\n')
        # We need to skip the next lines that were broken:
        #
        # Text:
        # {bounded_doc}", 10)
        skip = 3
    elif skip > 0:
        skip -= 1
        continue
    else:
        # check if it's the other broken f-string
        if "unified_article\": f\"### {topic}" in line:
            new_lines.append('            "unified_article": f"### {topic}\\nNo relevant documents passed the filter.",\n')
            skip = 1 # skip the "No relevant documents passed the filter.",
        else:
            new_lines.append(line)

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.writelines(new_lines)
