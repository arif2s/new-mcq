import re

with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

# I need to fix the returned output to gracefully handle the fact that final_article is a JSON, not a string.
# "enhanced_explanation" used to be base_note (string), now it's base_note_str (string).

new_return = r"""    return {
        "enhanced_explanation": base_note_str,
        "distractor_analysis": distractor_output,
        "unified_article": json.dumps(final_article),
        "reference_links": search_results
    }"""

content = re.sub(r'    return {\n        "enhanced_explanation": base_note,\n        "distractor_analysis": distractor_output,\n        "unified_article": final_article,\n        "reference_links": search_results\n    }', new_return, content)

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(content)
