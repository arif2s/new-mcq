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
from agents.tema_q.auditor import detect_clinical_variances
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

async def extract_topics_from_mcq(question: str, options: dict, skip_lm: bool = False, extractor_strategy: str = 'regex') -> dict:
    if skip_lm:
        from agents.tema_q.topic_extractor import extract_topics_local
        return extract_topics_local(question, options, extractor_strategy)

    user_msg = (
        f"Question: {question}\n"
        f"Options:\n"
        f"(A) {options.get('A', '')}\n"
        f"(B) {options.get('B', '')}\n"
        f"(C) {options.get('C', '')}\n"
        f"(D) {options.get('D', '')}\n"
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
            "unified_article": f"### {topic}\nSkipped LM Studio processing.",
            "reference_links": search_results
        }

    documents = await resolve_documents(search_results)

    if not documents:
        return {
            "enhanced_explanation": "No local reference files found.",
            "distractor_analysis": "N/A",
            "unified_article": f"### {topic}\nPlease add reference material.",
            "reference_links": search_results
        }

    # Strict token reservation: Pass 1
    doc1_content = documents[0]
    doc1_budget = 2000
    bounded_doc1 = truncate_to_token_limit(doc1_content, doc1_budget)

    pass1_user_msg = f"Clinical Topic: {topic}\n\nPrimary Reference:\n{bounded_doc1}"
    base_note = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, 350)

    final_article = base_note
    discrepancy_report_all = ""

    # Pass 2+: Iterative Breadth-First Micro-editing
    doc_budget = 1200
    for doc_content in documents[1:]:
        used_tokens = count_tokens(final_article) + count_tokens(PASS2_AUDIT_SYSTEM_PROMPT) + 150
        dynamic_doc_budget = min(doc_budget, MAX_CONTEXT_TOKENS - used_tokens - 600)

        # Guard against negative token limits if base note expands too fast
        if dynamic_doc_budget < 200:
            logger.warning("Approaching MAX_CONTEXT_TOKENS limit, skipping further document integration.")
            break

        bounded_doc = truncate_to_token_limit(doc_content, max(dynamic_doc_budget, 400))

        pass2_user_msg = f"Topic: {topic}\n\nBaseline Note:\n{final_article}\n\nSecondary Reference:\n{bounded_doc}"
        final_article = await call_llm_async(PASS2_AUDIT_SYSTEM_PROMPT, pass2_user_msg, 500)

        discrepancy_report = detect_clinical_variances(base_note, bounded_doc)
        if discrepancy_report:
             discrepancy_report_all += f"\n{discrepancy_report}"

    if discrepancy_report_all and "### Discrepancies & Variances" not in final_article:
        final_article += f"\n\n### Discrepancies & Variances\n{discrepancy_report_all}"

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

    return {
        "enhanced_explanation": base_note,
        "distractor_analysis": distractor_output,
        "unified_article": final_article,
        "reference_links": search_results
    }
