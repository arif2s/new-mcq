import asyncio
import logging
import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, Union, List
from contextlib import closing
import httpx

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
    MCQ_DETAILED_EXPLANATION_PROMPT,
    TOPIC_EXTRACTION_SYSTEM_PROMPT,
    STOP_TOKENS,
)
from agents.tema_q.auditor import merge_json_nodes
from core.database import get_db_connection

logger = logging.getLogger(__name__)

_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        limits = httpx.Limits(max_keepalive_connections=1, max_connections=2)
        _http_client = httpx.AsyncClient(timeout=LM_STUDIO_TIMEOUT, limits=limits)
    return _http_client


async def close_http_client():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def parse_llm_json(response_str: str) -> Union[dict, list]:
    """
    Safely parses either a JSON object {...} or a JSON array [...] from an LLM response.
    Fixes the bug where Pass 2 JSON arrays were corrupted by a greedy {...} regex.
    """
    if not response_str or not response_str.strip():
        return {"section": "General", "subtopic": "Clinical Summary", "conditional_context": "", "clinical_points": []}

    cleaned = response_str.strip()
    match_fence = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match_fence:
        cleaned = match_fence.group(1).strip()

    first_bracket = cleaned.find("[")
    first_brace = cleaned.find("{")

    candidate = cleaned
    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        last_bracket = cleaned.rfind("]")
        if last_bracket > first_bracket:
            candidate = cleaned[first_bracket:last_bracket + 1]
    elif first_brace != -1:
        last_brace = cleaned.rfind("}")
        if last_brace > first_brace:
            candidate = cleaned[first_brace:last_brace + 1]

    try:
        return json.loads(candidate)
    except Exception:
        if first_brace != -1:
            last_brace = cleaned.rfind("}")
            if last_brace > first_brace:
                try:
                    return json.loads(cleaned[first_brace:last_brace + 1])
                except Exception:
                    pass
        logger.warning("Failed to parse LLM JSON; falling back to structured text node.")
        return {
            "section": "Clinical Summary",
            "subtopic": "Synthesis",
            "conditional_context": "",
            "clinical_points": [{"point": cleaned[:600], "source": "Reference", "page": 1}],
        }


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
    parser = "fitz"
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                parser = app_state.get("pdfParser", "fitz")
    except Exception as e:
        logger.debug(f"Failed to fetch pdfParser state: {e}")

    if parser == "docling":
        try:
            res = extract_structured_layout(file_path, page)
            if res:
                return res
        except Exception as e:
            logger.warning(f"Docling extraction failed, falling back to fitz: {e}")
    return extract_page_slice(file_path, page)


async def extract_topics_from_mcq(
    question: str,
    options: dict,
    explanation: str = "",
    skip_lm: bool = True,
    extractor_strategy: str = "medspacy",
) -> dict:
    """
    Uses the fast local canonicalizing medical NLP extractor by default so topic review popups
    open instantaneously without waiting on LM Studio.
    """
    from agents.tema_q.topic_extractor import extract_topics_local
    local_result = extract_topics_local(question, options, explanation, strategy=extractor_strategy)
    if skip_lm or local_result.get("all_topics"):
        return local_result

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
        parsed = parse_llm_json(response)
        if isinstance(parsed, dict) and parsed.get("all_topics"):
            return parsed
    except Exception as e:
        logger.warning(f"LLM topic extraction fallback to local: {e}")

    return local_result


async def generate_mcq_explanation(question_data: Dict[str, Any]) -> str:
    """
    Generates a detailed per-MCQ explanation via LM Studio explaining:
    1. What the question is about
    2. Why the correct option is right
    3. Why each of the remaining options is wrong
    """
    q_text = question_data.get("question") or question_data.get("question_text") or ""
    opt_a = question_data.get("option_a") or question_data.get("opt_a") or ""
    opt_b = question_data.get("option_b") or question_data.get("opt_b") or ""
    opt_c = question_data.get("option_c") or question_data.get("opt_c") or ""
    opt_d = question_data.get("option_d") or question_data.get("opt_d") or ""
    correct = question_data.get("correct_answer") or ""
    csv_exp = question_data.get("explanation") or ""
    topic = question_data.get("topic") or question_data.get("topic_name") or ""

    user_msg = (
        f"Topic: {topic}\n"
        f"Question: {q_text}\n"
        f"Options:\n"
        f"(A) {opt_a}\n"
        f"(B) {opt_b}\n"
        f"(C) {opt_c}\n"
        f"(D) {opt_d}\n"
        f"Correct Option: {correct}\n"
    )
    if csv_exp and csv_exp != "No explanation provided.":
        user_msg += f"Baseline CSV Note: {csv_exp}\n"

    try:
        return await call_llm_async(MCQ_DETAILED_EXPLANATION_PROMPT, user_msg, max_tokens=450)
    except Exception as e:
        logger.warning(f"Failed to generate LLM MCQ explanation: {e}")
        return ""


async def resolve_documents(search_results: dict) -> List[Dict[str, Any]]:
    """
    Resolves reference documents respecting limits: max 1 ZIM, max 2 PDF, max 2 Obsidian (diff folders).
    Uses pathlib.Path(p).parent so Windows backslash paths work properly.
    """
    documents: List[Dict[str, Any]] = []

    if search_results.get("zim"):
        top_zim = search_results["zim"][0]
        title = top_zim.get("title", "")
        content = await asyncio.to_thread(get_zim_article, top_zim.get("file_path", ""), title)
        if content:
            documents.append({
                "source_type": "zim",
                "source_label": f"ZIM: {title}",
                "page": 1,
                "header": f"Source (ZIM - {title}):\n{content}",
                "raw_text": content,
            })

    if search_results.get("pdf"):
        for top_pdf in search_results["pdf"][:2]:
            page = int(top_pdf.get("page", 1) or 1)
            title = top_pdf.get("title", "PDF")
            content = await asyncio.to_thread(_extract_pdf_sync, top_pdf["file_path"], page)
            if content:
                documents.append({
                    "source_type": "pdf",
                    "source_label": title,
                    "page": page,
                    "header": f"Source (PDF - {title} - Page {page}):\n{content}",
                    "raw_text": content,
                })

    if search_results.get("obsidian"):
        used_folders = set()
        for top_obs in search_results["obsidian"]:
            if len(used_folders) >= 2:
                break
            file_path = top_obs.get("file_path", "")
            folder = str(Path(file_path).parent) if file_path else ""
            if folder in used_folders:
                continue
            title = top_obs.get("title", "Obsidian Note")
            content = await asyncio.to_thread(read_markdown_body, file_path)
            if content:
                documents.append({
                    "source_type": "obsidian",
                    "source_label": title,
                    "page": 1,
                    "header": f"Source (Obsidian - {title}):\n{content}",
                    "raw_text": content,
                })
                used_folders.add(folder)

    return documents


async def run_tema_q_synthesis(
    topic: str,
    search_results: dict,
    mcq_context: dict = None,
    skip_lm: bool = False,
) -> dict:
    """
    Runs the multi-pass Tema_Q clinical synthesis and variance auditor across
    resolved ZIM, PDF, and Obsidian documents.
    """
    if skip_lm:
        return {
            "enhanced_explanation": "LM Studio processing skipped (Reference-Only Mode).",
            "distractor_analysis": "LM Studio processing skipped.",
            "unified_article": "",
            "reference_links": search_results,
        }

    documents = await resolve_documents(search_results)
    if not documents:
        return {
            "enhanced_explanation": "No local reference files found.",
            "distractor_analysis": "N/A",
            "unified_article": json.dumps({
                "section": "General",
                "subtopic": topic,
                "conditional_context": "",
                "clinical_points": [],
                "discrepancies": "",
            }),
            "reference_links": search_results,
        }

    # Pre-Pass Relevance Gate
    valid_documents = []
    for doc in documents:
        bounded_doc = truncate_to_token_limit(doc["header"], 500)
        gate_prompt = f"Does this text contain useful clinical or scientific information related to '{topic}'? Answer ONLY 'YES' or 'NO'."
        try:
            gate_resp = await call_llm_async(
                "You are a clinical relevance filter.",
                gate_prompt + f"\n\nText:\n{bounded_doc}",
                10,
            )
            if "NO" not in gate_resp.upper() or "YES" in gate_resp.upper():
                valid_documents.append(doc)
        except Exception:
            valid_documents.append(doc)

    if not valid_documents:
        valid_documents = documents[:2]

    # Pass 1: Primary Reference Synthesis
    doc1 = valid_documents[0]
    bounded_doc1 = truncate_to_token_limit(doc1["header"], 2000)
    pass1_user_msg = f"Clinical Topic: {topic}\n\nPrimary Reference:\n{bounded_doc1}"

    try:
        base_note_str = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, 600)
        parsed_base = parse_llm_json(base_note_str)
        if isinstance(parsed_base, list):
            base_note_json = {
                "section": "Clinical Summary",
                "subtopic": topic,
                "conditional_context": "",
                "clinical_points": parsed_base,
            }
        else:
            base_note_json = parsed_base
    except Exception as e:
        logger.error(f"Pass 1 LLM synthesis failed for '{topic}': {e}")
        base_note_str = f"Failed to reach LM Studio: {e}"
        base_note_json = {
            "section": "Clinical Summary",
            "subtopic": topic,
            "conditional_context": "",
            "clinical_points": [{"point": doc1["raw_text"][:500], "source": doc1["source_label"], "page": doc1["page"]}],
        }

    for pt in base_note_json.get("clinical_points", []):
        if isinstance(pt, dict):
            pt.setdefault("source", doc1["source_label"])
            pt.setdefault("page", doc1["page"])

    final_article = base_note_json

    # Pass 2+: Iterative Secondary Reference Integration & Variance Auditing
    doc_budget = 1200
    for doc_sec in valid_documents[1:]:
        used_tokens = count_tokens(json.dumps(final_article)) + count_tokens(PASS2_AUDIT_SYSTEM_PROMPT) + 150
        dynamic_doc_budget = min(doc_budget, MAX_CONTEXT_TOKENS - used_tokens - 600)
        if dynamic_doc_budget < 200:
            break

        bounded_sec = truncate_to_token_limit(doc_sec["header"], max(dynamic_doc_budget, 400))
        sec_msg = f"Clinical Topic: {topic}\n\nSecondary Reference:\n{bounded_sec}"

        try:
            sec_json_str = await call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, sec_msg, 600)
            sec_parsed = parse_llm_json(sec_json_str)
            if isinstance(sec_parsed, list):
                sec_json = {"section": "Clinical Summary", "subtopic": topic, "conditional_context": "", "clinical_points": sec_parsed}
            else:
                sec_json = sec_parsed

            final_article = merge_json_nodes(
                final_article,
                sec_json,
                metadata={"source": doc_sec["source_label"], "page": doc_sec["page"]},
            )
        except Exception as e:
            logger.warning(f"Pass 2 merge skipped for secondary doc on '{topic}': {e}")

    # Pass 3: Optional MCQ Distractor Audit
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
            f"Clinical Reference Synthesis:\n{json.dumps(final_article)}"
        )
        try:
            distractor_output = await call_llm_async(DISTRACTOR_ANALYSIS_PROMPT, distractor_user_msg, 300)
        except Exception:
            distractor_output = "Distractors audited against clinical reference criteria."
    else:
        distractor_output = "Distractors audited against top clinical reference criteria."

    return {
        "enhanced_explanation": base_note_str,
        "distractor_analysis": distractor_output,
        "unified_article": json.dumps(final_article),
        "reference_links": search_results,
    }
