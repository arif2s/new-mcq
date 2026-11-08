from agents.tema_q.pipeline import parse_llm_json

text = """
Here is the JSON:
```json
{
  "section": "Diagnostics",
  "subtopic": "Staging",
  "conditional_context": "None",
  "clinical_points": [
    {"point": "Test point", "source": "Ref 1", "page": 1}
  ]
}
```
"""

print(parse_llm_json(text))

text_error = """
I am an AI and I made a mistake.
{
  "section": "Diagnostics",
  "subtopic": "Staging",
  "conditional_context": "None",
  "clinical_points": [
    {"point": "Test point", "source": "Ref 1", "page": 1}
  ]
}
"""
print(parse_llm_json(text_error))

text_list = """
[
  {"point": "Merged point", "source": "Ref 2", "page": 2}
]
"""
print(parse_llm_json(text_list))
