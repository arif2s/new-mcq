import re

with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

json_parser_code = """
import re

def parse_llm_json(response_str: str) -> dict:
    # Strip markdown wrappers if present
    match = re.search(r'```(?:json)?\s*(.*?)\s*```', response_str, re.DOTALL)
    if match:
        response_str = match.group(1)

    # Try to find { ... } block
    match_braces = re.search(r'\{.*\}', response_str, re.DOTALL)
    if match_braces:
        response_str = match_braces.group(0)

    try:
        return json.loads(response_str)
    except Exception as e:
        logger.error(f"Failed to parse LLM JSON: {e} - Response: {response_str}")
        return {"section": "Error", "subtopic": "Error", "conditional_context": "", "clinical_points": []}
"""

if "def parse_llm_json" not in content:
    content = content.replace("def count_tokens(text: str) -> int:", json_parser_code + "\n\ndef count_tokens(text: str) -> int:")

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(content)
