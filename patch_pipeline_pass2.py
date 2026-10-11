import re

with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

new_pass2_code = r"""    # Pass 2+: Iterative Breadth-First Micro-editing
    doc_budget = 1200
    for doc_idx, doc_content in enumerate(documents[1:]):
        used_tokens = count_tokens(json.dumps(final_article)) + count_tokens(PASS2_AUDIT_SYSTEM_PROMPT) + 150
        dynamic_doc_budget = min(doc_budget, MAX_CONTEXT_TOKENS - used_tokens - 600)

        if dynamic_doc_budget < 200:
            logger.warning("Approaching MAX_CONTEXT_TOKENS limit, skipping further document integration.")
            break

        bounded_doc = truncate_to_token_limit(doc_content, max(dynamic_doc_budget, 400))

        # 1. Parse secondary reference into JSON
        sec_msg = f"Clinical Topic: {topic}\n\nSecondary Reference:\n{bounded_doc}"
        sec_json_str = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, sec_msg, 600)
        sec_json = parse_llm_json(sec_json_str)

        # 2. Match subtopic nodes programmatically
        base_subtopic = final_article.get("subtopic", "")
        sec_subtopic = sec_json.get("subtopic", "")

        # If they match or are somewhat similar
        if base_subtopic and sec_subtopic and (base_subtopic.lower() in sec_subtopic.lower() or sec_subtopic.lower() in base_subtopic.lower() or "error" not in base_subtopic.lower()):
            # 3. Send only isolated JSON arrays to LLM for micro-merging
            base_points = final_article.get("clinical_points", [])
            sec_points = sec_json.get("clinical_points", [])

            # Extract source and page directly from bounded_doc if possible, or search_results metadata
            meta_source = f"Secondary Ref {doc_idx+1}"
            meta_page = 1
            if "Source (PDF -" in doc_content:
                try:
                    import re
                    m = re.search(r'Source \(PDF - (.*?) - Page (\d+)\)', doc_content)
                    if m:
                        meta_source = m.group(1).strip()
                        meta_page = int(m.group(2))
                except:
                    pass
            elif "Source (Obsidian -" in doc_content:
                try:
                    import re
                    m = re.search(r'Source \(Obsidian - (.*?)\)', doc_content)
                    if m:
                        meta_source = m.group(1).strip()
                except:
                    pass
            elif "Source (ZIM -" in doc_content:
                try:
                    import re
                    m = re.search(r'Source \(ZIM - (.*?)\)', doc_content)
                    if m:
                        meta_source = m.group(1).strip()
                except:
                    pass

            merge_prompt = PASS2_AUDIT_SYSTEM_PROMPT.replace("{subtopic}", base_subtopic)
            merge_user_msg = f"Base Points:\n{json.dumps(base_points)}\n\nSecondary Points:\n{json.dumps(sec_points)}"

            merged_resp = await call_llm_async(merge_prompt, merge_user_msg, 600)

            # The LLM output might contain ### Discrepancies & Variances followed by the JSON, or vice versa
            discrepancy_flag = ""
            if "### Discrepancies & Variances" in merged_resp:
                discrepancy_flag = "### Discrepancies & Variances\n"
                # Extract text around it
                parts = merged_resp.split("### Discrepancies & Variances")
                if len(parts) > 1:
                    discrep_text = parts[1].split("```")[0].split("[")[0].strip()
                    if discrep_text:
                        discrepancy_flag += discrep_text + "\n"
                    elif len(parts) > 2:
                        discrep_text = parts[2].split("```")[0].split("[")[0].strip()
                        discrepancy_flag += discrep_text + "\n"

            merged_json = parse_llm_json(merged_resp)
            if isinstance(merged_json, list):
                # Ensure metadata is appended correctly
                for pt in merged_json:
                    if not pt.get("source"):
                        pt["source"] = meta_source
                        pt["page"] = meta_page
                final_article["clinical_points"] = merged_json
                if discrepancy_flag:
                    final_article["discrepancies"] = discrepancy_flag
            elif isinstance(merged_json, dict) and "clinical_points" in merged_json:
                pts = merged_json.get("clinical_points", [])
                for pt in pts:
                    if not pt.get("source"):
                        pt["source"] = meta_source
                        pt["page"] = meta_page
                final_article["clinical_points"] = pts
                if discrepancy_flag:
                    final_article["discrepancies"] = discrepancy_flag"""

# Manual replace using split and join to avoid re.sub issues
parts = content.split("    # Pass 2+: Iterative Breadth-First Micro-editing")
if len(parts) > 1:
    before = parts[0]
    after = parts[1]

    parts2 = after.split("    # Pass 3: Distractor Audit (Optional)")
    if len(parts2) > 1:
        after = "    # Pass 3: Distractor Audit (Optional)" + parts2[1]

    content = before + new_pass2_code + "\n\n" + after

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(content)
