import re

with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

# I want to add the Pre-Pass Relevance Gate logic.
gate_code = """
    # Pre-Pass Relevance Gate
    valid_documents = []
    for doc in documents:
        budget = 500
        bounded_doc = truncate_to_token_limit(doc, budget)
        gate_prompt = f"Does this text primarily focus on the clinical topic '{topic}'? Answer ONLY 'YES' or 'NO'."
        gate_resp = await call_llm_async("You are a strict relevance filter.", gate_prompt + f"\n\nText:\n{bounded_doc}", 10)
        if 'YES' in gate_resp.upper():
            valid_documents.append(doc)
        else:
            logger.info(f"Pre-pass gate rejected document for topic '{topic}'")

    documents = valid_documents
    if not documents:
        return {
            "enhanced_explanation": "Documents failed relevance gate.",
            "distractor_analysis": "N/A",
            "unified_article": f"### {topic}\nNo relevant documents passed the filter.",
            "reference_links": search_results
        }
"""

# Replace `# Strict token reservation: Pass 1` with the new gate
new_content = content.replace("    # Strict token reservation: Pass 1", gate_code + "\n    # Strict token reservation: Pass 1")

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(new_content)
