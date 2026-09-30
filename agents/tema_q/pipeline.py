import asyncio
import logging
from typing import Optional, Tuple
import httpx
import tiktoken

from config import LM_STUDIO_URL, LM_STUDIO_TIMEOUT, DEFAULT_MODEL, MAX_CONTEXT_TOKENS
from parsers.docling_parser import extract_structured_layout
from parsers.pdf_extractor import extract_page_slice
from parsers.obsidian_reader import read_markdown_body
from parsers.zim_reader import get_zim_article
from agents.tema_q.prompts import (
    PASS1_CLINICAL_SYSTEM_PROMPT,
    PASS2_AUDIT_SYSTEM_PROMPT,
    DISTRACTOR_ANALYSIS_PROMPT,
    STOP_TOKENS,
)
from agents.tema_q.auditor import detect_clinical_variances

logger = logging.getLogger(__name__)

# Cached persistent tokenizer
try:
    _tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception:
    _tokenizer = None

# Global HTTP client session for connection keep-alive
_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
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
    try:
        return extract_structured_layout(file_path, page)
    except Exception:
        return extract_page_slice(file_path, page)

async def resolve_primary_and_secondary_docs(search_results: dict) -> Tuple[str, str]:
    """Resolves primary and secondary reference documents asynchronously without blocking the loop."""
    documents = []

    # 1. PDF Parser (CPU-bound layout parser offloaded to worker thread)
    if search_results.get("pdf"):
        top_pdf = search_results["pdf"][0]
        content = await asyncio.to_thread(_extract_pdf_sync, top_pdf["file_path"], top_pdf["page"])
        if content:
            documents.append(f"Source (PDF - {top_pdf.get('title')}):\n{content}")

    # 2. Obsidian Markdown Parser (Disk I/O offloaded)
    if search_results.get("obsidian"):
        top_obs = search_results["obsidian"][0]
        content = await asyncio.to_thread(read_markdown_body, top_obs["file_path"])
        if content:
            documents.append(f"Source (Obsidian - {top_obs.get('title')}):\n{content}")

    # 3. LibZim C-binding reader (Disk & C-decompression offloaded)
    if search_results.get("zim"):
        top_zim = search_results["zim"][0]
        content = await asyncio.to_thread(get_zim_article, top_zim["file_path"], top_zim.get("title", ""))
        if content:
            documents.append(f"Source (ZIM - {top_zim.get('title')}):\n{content}")

    return (
        documents[0] if len(documents) > 0 else "",
        documents[1] if len(documents) > 1 else "",
    )

async def run_tema_q_synthesis(topic: str, search_results: dict, mcq_context: dict = None) -> dict:
    doc1_content, doc2_content = await resolve_primary_and_secondary_docs(search_results)

    if not doc1_content and not doc2_content:
        return {
            "enhanced_explanation": "No local reference files found.",
            "distractor_analysis": "N/A",
            "unified_article": f"### {topic}\nPlease add reference material.",
        }

    # Strict token reservation: Pass 1
    doc1_budget = 2000
    doc2_budget = 1200
    bounded_doc1 = truncate_to_token_limit(doc1_content, doc1_budget)

    pass1_user_msg = f"Clinical Topic: {topic}\n\nPrimary Reference:\n{bounded_doc1}"
    base_note = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, 350)

    # Pass 2: Auditing with dynamic context limits
    if doc2_content:
        used_tokens = count_tokens(base_note) + count_tokens(PASS2_AUDIT_SYSTEM_PROMPT) + 150
        dynamic_doc2_budget = min(doc2_budget, MAX_CONTEXT_TOKENS - used_tokens - 600)
        bounded_doc2 = truncate_to_token_limit(doc2_content, max(dynamic_doc2_budget, 400))

        pass2_user_msg = f"Topic: {topic}\n\nBaseline Note:\n{base_note}\n\nSecondary Reference:\n{bounded_doc2}"
        final_article = await call_llm_async(PASS2_AUDIT_SYSTEM_PROMPT, pass2_user_msg, 500)

        discrepancy_report = detect_clinical_variances(base_note, bounded_doc2)
        if discrepancy_report and "### Discrepancies & Variances" not in final_article:
            final_article += f"\n\n### Discrepancies & Variances\n{discrepancy_report}"
    else:
        final_article = base_note

    # Pass 3: Distractor Audit (Optional)
    if mcq_context and mcq_context.get("options"):
        opts = mcq_context["options"]
        distractor_user_msg = (
            f"Topic: {topic}\n"
            f"Question: {mcq_context.get('question', '')}\n"
            f"Options:\n"
            f"(A) {opts.get('A')}\n"
            f"(B) {opts.get('B')}\n"
            f"(C) {opts.get('C')}\n"
            f"(D) {opts.get('D')}\n"
            f"Correct Answer: {mcq_context.get('correct_opt')}\n\n"
            f"Clinical Reference Synthesis:\n{final_article}"
        )
        distractor_output = await call_llm_async(DISTRACTOR_ANALYSIS_PROMPT, distractor_user_msg, 250)
    else:
        distractor_output = "Distractors audited against top clinical reference criteria."

    return {
        "enhanced_explanation": base_note,
        "distractor_analysis": distractor_output,
        "unified_article": final_article,
    }
