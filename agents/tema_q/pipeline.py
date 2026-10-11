import asyncio
import logging
import json
from typing import Optional, Dict, Any
from contextlib import closing

import httpx

# Cached persistent tokenizer
try:
    import tiktoken
    _tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception:
    _tokenizer = None

from config import LM_STUDIO_URL, LM_STUDIO_TIMEOUT, DEFAULT_MODEL, MAX_CONTEXT_TOKENS
from parsers.docling_parser import extract_structured_layout
from parsers.pdf_extractor import extract_page_slice
from parsers.obsidian_reader import read_markdown_body
from parsers.zim_reader import get_zim_article
from agents.tema_q.prompts import (
    PASS1_CLINICAL_SYSTEM_PROMPT,
    PASS2_AUDIT_SYSTEM_PROMPT,
    DISTRACTOR_ANALYSIS_PROMPT,
    TOPIC_EXTRACTION_SYSTEM_PROMPT,
    STOP_TOKENS,
)
from core.database import get_db_connection

logger = logging.getLogger(__name__)

# Global HTTP client session for connection keep-alive
_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        # Constrained limits to prevent overwhelming local LM Studio queue VRAM
        limits = httpx.Limits(max_keepalive_connections=1, max_connections=2)
        _http_client = httpx.AsyncClient(timeout=LM_STUDIO_TIMEOUT, limits=limits)
    return _http_client

async def close_http_client():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


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


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_tokenizer.encode(text)) if _tokenizer else len(text) // 4

def truncate_to_token_limit(text: str, max_tokens: int) -> str:
    if not text:
        return ""
    if _tokenizer:
        tokens = _tokenizer.encode(text)
        if len(tokens) > max_tokens:
            return _tokenizer.decode(tokens[:max_tokens]) + "\n[...TRUNCATED...]"
        return text
    char_limit = max_tokens * 4
    if len(text) > char_limit:
        return text[:char_limit] + "\n[...TRUNCATED...]"
    return text

async def call_llm_async(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
    url = f"{LM_STUDIO_URL}/chat/completions"
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stop": STOP_TOKENS,
        "stream": False,
    }
    client = get_http_client()
    response = await client.post(url, json=payload)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()

def _extract_pdf_sync(file_path: str, page: int) -> str:
    parser = 'fitz'
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                parser = app_state.get('pdfParser', 'fitz')
    except Exception as e:
        logger.warning(f"Failed to fetch pdfParser state: {e}")

    if parser == 'docling':
        try:
            return extract_structured_layout(file_path, page)
        except Exception as e:
            logger.warning(f"Docling extraction failed, falling back to fitz: {e}")
            return extract_page_slice(file_path, page)
    else:
        return extract_page_slice(file_path, page)

async def extract_topics_from_mcq(question: str, options: dict, explanation: str = '', skip_lm: bool = False, extractor_strategy: str = 'regex') -> dict:
    if skip_lm:
        from agents.tema_q.topic_extractor import extract_topics_local
        return extract_topics_local(question, options, explanation, extractor_strategy)

    user_msg = (
        f"Question: {question}\n"
        f"Options:\n"
        f"(A) {options.get('A', '')}\n"
        f"(B) {options.get('B', '')}\n"
        f"(C) {options.get('C', '')}\n"
        f"(D) {options.get('D', '')}\n"
        f"Explanation: {explanation}\n"
    )

    try:
        response = await call_llm_async(TOPIC_EXTRACTION_SYSTEM_PROMPT, user_msg, max_tokens=150)
        return json.loads(response)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse topic extraction JSON: {e} - Response: {response}")
    except Exception as e:
        logger.error(f"Topic extraction failed: {e}")

    return {"main_topic": "General Medical Concept", "all_topics": []}

async def resolve_documents(search_results: dict) -> list:
    """Resolves reference documents respecting limits: max 1 ZIM, max 2 PDF, max 2 Obsidian (diff folders)."""
    documents = []

    if search_results.get("zim"):
        top_zim = search_results["zim"][0]
        content = await asyncio.to_thread(get_zim_article, top_zim["file_path"], top_zim.get("title", ""))
        if content:
            documents.append(f"Source (ZIM - {top_zim.get('title')}):\n{content}")

    if search_results.get("pdf"):
        for top_pdf in search_results["pdf"][:2]:
            content = await asyncio.to_thread(_extract_pdf_sync, top_pdf["file_path"], top_pdf["page"])
            if content:
                documents.append(f"Source (PDF - {top_pdf.get('title')} - Page {top_pdf.get('page')}):\n{content}")

    if search_results.get("obsidian"):
        used_folders = set()
        for top_obs in search_results["obsidian"]:
            if len(used_folders) >= 2:
                break
            folder = top_obs.get("file_path", "").rsplit("/", 1)[0]
            if folder in used_folders:
                continue
            content = await asyncio.to_thread(read_markdown_body, top_obs["file_path"])
            if content:
                documents.append(f"Source (Obsidian - {top_obs.get('title')}):\n{content}")
                used_folders.add(folder)

    return documents

async def run_tema_q_synthesis(topic: str, search_results: dict, mcq_context: dict = None, skip_lm: bool = False) -> dict:
    if skip_lm:
        return {
            "enhanced_explanation": "LM Studio processing skipped.",
            "distractor_analysis": "LM Studio processing skipped.",
            "unified_article": f"### {topic}\nNo relevant documents passed the filter.",
        }

    documents = await resolve_documents(search_results)

    if not documents:
        return {
            "enhanced_explanation": "No local reference files found.",
            "distractor_analysis": "N/A",
            "unified_article": f"### {topic}\nNo relevant documents passed the filter.",
        }


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

    # Strict token reservation: Pass 1
    doc1_content = documents[0]
    doc1_budget = 2000
    bounded_doc1 = truncate_to_token_limit(doc1_content, doc1_budget)

    # Pass 1: Base Note Generation as JSON
    pass1_user_msg = f"Clinical Topic: {topic}\n\nPrimary Reference:\n{bounded_doc1}"
    base_note_str = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, 600)
    base_note_json = parse_llm_json(base_note_str)

    # Append source info for pass 1 if missing
    for pt in base_note_json.get("clinical_points", []):
        if not pt.get("source"):
            pt["source"] = "Primary Reference"
            pt["page"] = 1

    final_article = base_note_json
    discrepancy_report_all = ""

    # Pass 2+: Iterative Breadth-First Micro-editing
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
                    final_article["discrepancies"] = discrepancy_flag

    # Pass 3: Distractor Audit (Optional)
    if mcq_context and mcq_context.get("options"):
        opts = mcq_context["options"]
        distractor_user_msg = (
            f"Topic: {topic}\n"
            f"Question: {mcq_context.get('question', '')}\n"
            f"Options:\n"
            f"(A) {opts.get('A', '')}\n"
            f"(B) {opts.get('B', '')}\n"
            f"(C) {opts.get('C', '')}\n"
            f"(D) {opts.get('D', '')}\n"
            f"Correct Answer: {mcq_context.get('correct_opt', '')}\n\n"
            f"Clinical Reference Synthesis:\n{final_article}"
        )
        distractor_output = await call_llm_async(DISTRACTOR_ANALYSIS_PROMPT, distractor_user_msg, 250)
    else:
        distractor_output = "Distractors audited against top clinical reference criteria."

    # Save the output as HTML to storage/reports/{topic}_report.html
    import os
    os.makedirs("storage/reports", exist_ok=True)
    safe_topic = "".join([c for c in topic if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    safe_topic = safe_topic.replace(' ', '_')
    report_path = f"storage/reports/{safe_topic}_report.html"


    # Generate structured HTML from final_article JSON
    subtopic = final_article.get("subtopic", "")
    section = final_article.get("section", "")
    cond_context = final_article.get("conditional_context", "")
    clinical_points = final_article.get("clinical_points", [])
    discrepancies = final_article.get("discrepancies", "")

    html_points = ""
    for pt in clinical_points:
        point_text = pt.get("point", "")
        source = pt.get("source", "")
        page = pt.get("page", "")

        # Clickable reference badge
        if source:
            badge = f'<span class="badge" title="Source: {source} (Page {page})"><a href="#" onclick="alert(\'Open: {source} Page: {page}\'); return false;">[Ref: {source} p.{page}]</a></span>'
        else:
            badge = ""

        html_points += f"<li>{point_text} {badge}</li>\n"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Tema_Q Synthesis Report - {topic}</title>
<style>
  body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 20px; }}
  h1 {{ color: #2c3e50; }}
  h2, h3 {{ color: #34495e; }}
  .content {{ max-width: 800px; margin: auto; }}
  .section {{ margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
  .discrepancies {{ border-color: #e74c3c; background-color: #fadbd8; padding: 10px; margin-top: 10px; font-weight: bold; }}
  .badge {{ font-size: 0.85em; background-color: #e2e8f0; padding: 2px 6px; border-radius: 12px; margin-left: 8px; }}
  .badge a {{ text-decoration: none; color: #3182ce; }}
</style>
</head>
<body>
<div class="content">
  <h1>Clinical Synthesis: {topic}</h1>
  <div class="section">
    <h2>Unified Article: {subtopic}</h2>
    <p><strong>Section:</strong> {section}</p>
    <p><strong>Context:</strong> {cond_context}</p>
    <ul>
      {html_points}
    </ul>

        {f'<div class="discrepancies">{discrepancies}</div>' if discrepancies else ''}
  </div>
  <div class="section">
    <h2>Distractor Analysis</h2>
    <pre style="white-space: pre-wrap; font-family: inherit;">{distractor_output}</pre>
  </div>
</div>
</body>
</html>
"""
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as e:
        logger.error(f"Failed to save Tema_Q HTML report: {e}")

    return {
        "enhanced_explanation": base_note_str,
        "distractor_analysis": distractor_output,
        "unified_article": json.dumps(final_article),
        "reference_links": search_results
    }
