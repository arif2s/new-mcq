"""
agents/tema_q/pipeline.py - 2-pass iterative LLM synthesis coordinator
with token budgeting (4096 hard cap) and LM Studio integration.
Enforces Invariant 1 (Zero VRAM), Invariant 4 (Token Caps), Invariant 13 (Polymorphic References),
and Invariant 16 (Clean HTTP Client Lifespan Teardown).
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import httpx

try:
    import tiktoken

    _tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception:
    _tokenizer = None

from agents.tema_q.auditor import filter_novel_secondary_sentences, merge_json_nodes
from agents.tema_q.prompts import (
    DISTRACTOR_ANALYSIS_PROMPT,
    EXPLANATION_GENERATION_PROMPT,
    PASS1_CLINICAL_SYSTEM_PROMPT,
    PASS2_AUDIT_SYSTEM_PROMPT,
    STOP_TOKENS,
    TOPIC_EXTRACTION_SYSTEM_PROMPT,
    VLM_PAGE_SYNTHESIS_SYSTEM_PROMPT,
)
from parsers.pdf_topic_indexer import lookup_pdf_topic_pages, render_topic_pages_to_base64
from agents.tema_q.subtopic_router import route_and_prune_subtopics
from config import DEFAULT_MODEL, LM_STUDIO_TIMEOUT, LM_STUDIO_URL, MAX_CONTEXT_TOKENS
from core.database import get_db_connection
from parsers.docling_parser import extract_structured_layout
from parsers.obsidian_reader import read_markdown_body
from parsers.pdf_extractor import extract_page_slice
from parsers.zim_reader import get_zim_article

logger = logging.getLogger("medisearch.pipeline")

# Thread-local storage + global registry for clean lifespan teardown (Invariant 16)
_thread_local = threading.local()
_CLIENT_REGISTRY_LOCK = threading.Lock()
_ACTIVE_CLIENTS: Set[httpx.AsyncClient] = set()
_cached_pdf_parser: Optional[str] = None

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def _strip_reasoning_tags(text: str) -> str:
    """Strips <think>...</think> blocks emitted by reasoning GGUF models."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    if "<think>" in cleaned.lower():
        idx = cleaned.lower().find("<think>")
        cleaned = cleaned[:idx]
    return cleaned.strip()


def get_http_client() -> httpx.AsyncClient:
    """Returns an httpx.AsyncClient strictly bound to the current thread and event loop."""
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    client: Optional[httpx.AsyncClient] = getattr(_thread_local, "client", None)
    cached_loop = getattr(_thread_local, "loop", None)

    if client is None or client.is_closed or cached_loop is not current_loop:
        limits = httpx.Limits(max_keepalive_connections=2, max_connections=4)
        client = httpx.AsyncClient(timeout=LM_STUDIO_TIMEOUT, limits=limits)
        _thread_local.client = client
        _thread_local.loop = current_loop
        with _CLIENT_REGISTRY_LOCK:
            _ACTIVE_CLIENTS.add(client)

    return client


async def close_http_client() -> None:
    """Closes all active HTTP clients across threads cleanly during lifespan teardown (Invariant 16)."""
    with _CLIENT_REGISTRY_LOCK:
        clients = list(_ACTIVE_CLIENTS)
        _ACTIVE_CLIENTS.clear()

    for client in clients:
        try:
            if client and not client.is_closed:
                await client.aclose()
        except Exception:
            pass

    _thread_local.client = None
    _thread_local.loop = None


def parse_llm_json(response_str: str) -> Union[dict, list]:
    """Safely extracts and parses JSON objects or arrays from an LLM response."""
    cleaned = _strip_reasoning_tags(response_str or "")
    if not cleaned:
        return {
            "section": "General",
            "subtopic": "Clinical Summary",
            "conditional_context": "",
            "clinical_points": [],
            "discrepancies": "",
        }

    match_fence = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if match_fence:
        cleaned = match_fence.group(1).strip()

    first_bracket = cleaned.find("[")
    first_brace = cleaned.find("{")

    candidate = cleaned
    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        last_bracket = cleaned.rfind("]")
        if last_bracket > first_bracket:
            candidate = cleaned[first_bracket : last_bracket + 1]
    elif first_brace != -1:
        last_brace = cleaned.rfind("}")
        if last_brace > first_brace:
            candidate = cleaned[first_brace : last_brace + 1]

    try:
        return json.loads(candidate)
    except Exception:
        # Clean up trailing commas commonly emitted by local 4B/7B GGUF models
        candidate_fixed = re.sub(r",\s*([\]}])", r"\g<1>", candidate)
        try:
            return json.loads(candidate_fixed)
        except Exception:
            pass

        logger.warning("Failed to parse LLM JSON; constructing structured text node.")
        return {
            "section": "Clinical Summary",
            "subtopic": "Synthesis",
            "conditional_context": "",
            "clinical_points": [{"point": cleaned[:600], "source": "Reference", "page": 1}],
            "discrepancies": "",
        }


def count_tokens(text: str) -> int:
    """Calculates token length using tiktoken or safe 4-char character approximation."""
    if not text:
        return 0
    if _tokenizer:
        try:
            return len(_tokenizer.encode(str(text)))
        except Exception:
            pass
    return max(1, len(str(text)) // 4)


def truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """Bounds text to max_tokens to prevent LM Studio context overflow (Invariant 4)."""
    if not text:
        return ""
    max_tokens = max(16, int(max_tokens or 512))
    text_str = str(text)
    if _tokenizer:
        try:
            tokens = _tokenizer.encode(text_str)
            if len(tokens) > max_tokens:
                return _tokenizer.decode(tokens[:max_tokens]) + "\n[...TRUNCATED...]"
            return text_str
        except Exception:
            pass

    char_limit = max_tokens * 4
    if len(text_str) > char_limit:
        return text_str[:char_limit] + "\n[...TRUNCATED...]"
    return text_str


async def call_llm_async(
    system_prompt: str, user_prompt: str, max_tokens: int = 500
) -> str:
    """Async chat completion against local LM Studio server with hard 4096-token budget (Invariant 4)."""
    safe_max_out = max(16, min(int(max_tokens or 500), 1500))
    sys_str = str(system_prompt or "").strip()
    sys_tokens = count_tokens(sys_str)

    # Enforce Invariant 4: Total prompt + completion tokens must never exceed MAX_CONTEXT_TOKENS (4096)
    available_user_tokens = max(128, MAX_CONTEXT_TOKENS - sys_tokens - safe_max_out - 64)
    bounded_user_prompt = truncate_to_token_limit(str(user_prompt or ""), available_user_tokens)

    url = f"{LM_STUDIO_URL}/chat/completions"
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": sys_str},
            {"role": "user", "content": bounded_user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": safe_max_out,
        "stop": STOP_TOKENS,
        "stream": False,
    }
    client = get_http_client()
    response = await client.post(url, json=payload)
    response.raise_for_status()
    data = response.json()
    raw_content = data["choices"][0]["message"]["content"]
    return _strip_reasoning_tags(raw_content)


async def call_vlm_page_images_async(
    system_prompt: str,
    topic: str,
    subtopic: str,
    heading_path: str,
    page_images: List[Tuple[int, str]],
    source_label: str,
    fallback_text: str = "",
    max_tokens: int = 650,
) -> str:
    """
    Sends 1..3 cropped/rendered PDF page images directly to LM Studio's Vision model.
    If the loaded model in LM Studio is text-only (returns HTTP 400/422 on image_url),
    seamlessly falls back to standard text completion using `fallback_text`.
    """
    if not page_images:
        return await call_llm_async(system_prompt, fallback_text, max_tokens=max_tokens)

    pages_str = ", ".join(str(p) for p, _ in page_images)
    instruction_text = (
        f"Source Document: {source_label} (Pages: {pages_str})\n"
        f"Target Hierarchical Breadcrumb: {heading_path}\n"
        f"Target Clinical Topic: {topic}\n"
        f"Target Subtopic: {subtopic}\n\n"
        f"Extract all high-yield clinical points, tables, flowcharts, and dosing criteria "
        f"strictly for '{topic}' ({heading_path}). Do not mix with sibling topics."
    )
    user_content: List[Dict[str, Any]] = [{"type": "text", "text": instruction_text}]
    for _, b64_url in page_images:
        user_content.append({"type": "image_url", "image_url": {"url": b64_url}})

    url = f"{LM_STUDIO_URL}/chat/completions"
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": str(system_prompt or "").strip()},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": max(64, min(int(max_tokens or 650), 1500)),
        "stop": STOP_TOKENS,
        "stream": False,
    }
    try:
        client = get_http_client()
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        raw_content = data["choices"][0]["message"]["content"]
        return _strip_reasoning_tags(raw_content)
    except Exception as exc:
        logger.info("VLM image_url call unsupported by active model (%s); using text fallback.", exc)
        return await call_llm_async(
            system_prompt,
            f"{instruction_text}\n\nExtracted Reference Text:\n{fallback_text}",
            max_tokens=max_tokens,
        )


def _get_pdf_parser_setting() -> str:
    global _cached_pdf_parser
    if _cached_pdf_parser is not None:
        return _cached_pdf_parser
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                if isinstance(app_state, dict):
                    _cached_pdf_parser = str(app_state.get("pdfParser") or "docling_v2")
                    return _cached_pdf_parser
    except Exception as exc:
        logger.debug("Failed to fetch pdfParser state: %s", exc)
    _cached_pdf_parser = "docling_v2"
    return _cached_pdf_parser


def _extract_pdf_sync(file_path: str, page: int) -> str:
    parser = _get_pdf_parser_setting()
    if parser in ("docling_v1_heading_opt", "docling_v1"):
        try:
            from parsers.docling_v1_heading_opt import extract_structured_layout_v1_heading_opt
            res = extract_structured_layout_v1_heading_opt(file_path, page)
            if res:
                return res
        except Exception as exc:
            logger.warning("docling_v1_heading_opt failed, falling back: %s", exc)
    elif parser in ("indra_base_heading_opt", "indra"):
        try:
            from parsers.indra_base_heading_opt import extract_page_with_indra_base_heading_opt
            res_dict = extract_page_with_indra_base_heading_opt(
                file_path, page_number=page, render_page_image=False
            )
            if res_dict and res_dict.get("markdown"):
                return str(res_dict["markdown"])
        except Exception as exc:
            logger.warning("indra_base_heading_opt failed, falling back: %s", exc)
    elif parser == "docling_v2":
        try:
            from parsers.docling_v2_parser import extract_structured_layout as extract_v2
            res = extract_v2(file_path, page)
            if res:
                return res
        except Exception as exc:
            logger.warning("Docling v2 extraction failed, falling back to fitz: %s", exc)
    elif parser == "docling":
        try:
            res = extract_structured_layout(file_path, page)
            if res:
                return res
        except Exception as exc:
            logger.warning("Docling extraction failed, falling back to fitz: %s", exc)
    return extract_page_slice(file_path, page)


async def extract_topics_from_mcq(
    question: str,
    options: dict,
    explanation: str = "",
    skip_lm: bool = True,
    extractor_strategy: str = "medspacy",
) -> dict:
    """Extracts clinical topics using fast local canonicalization, falling back to LLM if needed."""
    from agents.tema_q.topic_extractor import extract_topics_local

    local_result = await asyncio.to_thread(
        extract_topics_local,
        question,
        options,
        explanation,
        strategy=extractor_strategy,
    )
    if skip_lm or local_result.get("all_topics"):
        return local_result

    opts = options if isinstance(options, dict) else {}
    user_msg = (
        f"Question: {question}\n"
        f"Options:\n"
        f"(A) {opts.get('A', '')}\n"
        f"(B) {opts.get('B', '')}\n"
        f"(C) {opts.get('C', '')}\n"
        f"(D) {opts.get('D', '')}\n"
        f"Explanation: {explanation}\n"
    )
    try:
        response = await call_llm_async(
            TOPIC_EXTRACTION_SYSTEM_PROMPT, user_msg, max_tokens=150
        )
        parsed = parse_llm_json(response)
        if isinstance(parsed, dict) and parsed.get("all_topics"):
            parsed.setdefault("main_topic", parsed["all_topics"][0])
            parsed.setdefault("topic_details", local_result.get("topic_details", []))
            return parsed
    except Exception as exc:
        logger.warning("LLM topic extraction fallback to local: %s", exc)

    return local_result


async def generate_mcq_explanation(question_data: Dict[str, Any]) -> str:
    """Generates a detailed per-MCQ explanation via LM Studio with universal schema key handling."""
    if not isinstance(question_data, dict):
        return ""

    q_text = question_data.get("question") or question_data.get("question_text") or ""
    opts_dict = question_data.get("options") if isinstance(question_data.get("options"), dict) else {}
    opt_a = question_data.get("option_a") or question_data.get("opt_a") or opts_dict.get("A") or ""
    opt_b = question_data.get("option_b") or question_data.get("opt_b") or opts_dict.get("B") or ""
    opt_c = question_data.get("option_c") or question_data.get("opt_c") or opts_dict.get("C") or ""
    opt_d = question_data.get("option_d") or question_data.get("opt_d") or opts_dict.get("D") or ""
    correct = (
        question_data.get("correct_answer")
        or question_data.get("correct_opt")
        or question_data.get("answer")
        or ""
    )
    csv_exp = (
        question_data.get("explanation")
        or question_data.get("csv_explanation")
        or ""
    )
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
        return await call_llm_async(EXPLANATION_GENERATION_PROMPT, user_msg, max_tokens=450)
    except Exception as exc:
        logger.warning("Failed to generate LLM MCQ explanation: %s", exc)
        return ""


def _group_search_results_polymorphic(search_results: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Normalizes search_results across SearchResultsDict, list[ChunkDict], or JSON string (Invariant 13)."""
    grouped: Dict[str, List[Dict[str, Any]]] = {"zim": [], "pdf": [], "obsidian": []}
    if not search_results:
        return grouped

    if isinstance(search_results, str):
        try:
            search_results = json.loads(search_results)
        except Exception:
            return grouped

    def _route_item(item: Any, fallback_src: str = "") -> None:
        if not isinstance(item, dict):
            return
        src = str(item.get("source") or item.get("source_type") or fallback_src).lower()
        f_path = str(item.get("file_path") or item.get("path") or "").lower()
        if src == "zim" or f_path.endswith(".zim"):
            grouped["zim"].append(item)
        elif src == "obsidian" or f_path.endswith(".md"):
            grouped["obsidian"].append(item)
        elif src in ("pdf", "lancedb") or f_path.endswith(".pdf"):
            grouped["pdf"].append(item)

    if isinstance(search_results, list):
        for item in search_results:
            _route_item(item)
    elif isinstance(search_results, dict):
        for key in ("zim", "pdf", "obsidian", "lancedb"):
            items = search_results.get(key)
            if isinstance(items, list):
                for item in items:
                    _route_item(item, fallback_src=key)

    return grouped


async def resolve_documents(
    search_results: dict,
    topic: Optional[str] = None,
    subtopic: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Resolves reference documents (max 1 ZIM, max 2 PDF, max 2 Obsidian in distinct folders)."""
    documents: List[Dict[str, Any]] = []
    grouped = _group_search_results_polymorphic(search_results)

    # 1. ZIM Document Resolution (max 1)
    if grouped["zim"]:
        top_zim = grouped["zim"][0]
        title = str(top_zim.get("title") or "")
        zim_file = str(top_zim.get("file_path") or top_zim.get("zim_path") or "")
        content = (
            await asyncio.to_thread(get_zim_article, zim_file, title)
            if zim_file and title
            else ""
        )
        if not content:
            content = str(top_zim.get("text") or top_zim.get("snippet") or "")

        if content:
            documents.append({
                "source_type": "zim",
                "source_label": f"ZIM: {title}",
                "page": 1,
                "header": f"Source (ZIM - {title}):\n{content}",
                "raw_text": content,
            })

    # 2. PDF Document Resolution (max 2)
    # Check SQLite pdf_topic_index first if topic is provided
    indexed_pdf_hits: List[Dict[str, Any]] = []
    if topic:
        try:
            indexed_pdf_hits = await asyncio.to_thread(lookup_pdf_topic_pages, topic, subtopic=subtopic)
        except Exception as exc:
            logger.debug("Failed querying pdf_topic_index: %s", exc)

    seen_pdf_pages: Set[Tuple[str, int]] = set()

    for idx_hit in indexed_pdf_hits:
        if sum(1 for d in documents if d["source_type"] == "pdf") >= 2:
            break
        pdf_path = str(idx_hit.get("pdf_path") or "")
        start_page = int(idx_hit.get("start_page") or 1)
        end_page = int(idx_hit.get("end_page") or start_page)
        start_y0 = float(idx_hit.get("start_y0") or 0.0)
        end_y1 = float(idx_hit.get("end_y1") or 1.0)
        heading_path = str(idx_hit.get("heading_path") or "")
        title = str(idx_hit.get("pdf_title") or Path(pdf_path).name)

        dedup_key = (pdf_path.lower(), start_page)
        if dedup_key in seen_pdf_pages:
            continue
        seen_pdf_pages.add(dedup_key)

        content = (
            await asyncio.to_thread(_extract_pdf_sync, pdf_path, start_page)
            if pdf_path
            else ""
        )

        page_images: List[Tuple[int, str]] = []
        if pdf_path:
            try:
                page_images = await asyncio.to_thread(
                    render_topic_pages_to_base64,
                    pdf_path,
                    start_page,
                    end_page,
                    start_y0=start_y0,
                    end_y1=end_y1,
                    crop_to_subtopic_bounds=True,
                )
            except Exception as exc:
                logger.debug("render_topic_pages_to_base64 error: %s", exc)

        if content or page_images:
            doc_entry: Dict[str, Any] = {
                "source_type": "pdf",
                "source_label": title,
                "page": start_page,
                "heading_path": heading_path,
                "header": f"Source (PDF - {title} - Page {start_page}):\n{content}",
                "raw_text": content,
            }
            if page_images:
                doc_entry["page_images"] = page_images
            documents.append(doc_entry)

    if grouped["pdf"]:
        for top_pdf in grouped["pdf"]:
            if sum(1 for d in documents if d["source_type"] == "pdf") >= 2:
                break
            page = max(1, int(top_pdf.get("page") or top_pdf.get("page_number") or 1))
            title = str(top_pdf.get("title") or top_pdf.get("source_article") or "PDF")
            pdf_path = str(top_pdf.get("file_path") or top_pdf.get("path") or "")
            dedup_key = (pdf_path.lower(), page)
            if dedup_key in seen_pdf_pages:
                continue
            seen_pdf_pages.add(dedup_key)

            content = (
                await asyncio.to_thread(_extract_pdf_sync, pdf_path, page)
                if pdf_path
                else ""
            )
            if not content:
                content = str(top_pdf.get("text") or top_pdf.get("snippet") or "")

            if content:
                documents.append({
                    "source_type": "pdf",
                    "source_label": title,
                    "page": page,
                    "header": f"Source (PDF - {title} - Page {page}):\n{content}",
                    "raw_text": content,
                })

    # 3. Obsidian Document Resolution (max 2 in distinct folders)
    if grouped["obsidian"]:
        used_folders: Set[str] = set()
        for top_obs in grouped["obsidian"]:
            if len(used_folders) >= 2:
                break
            file_path = str(top_obs.get("file_path") or top_obs.get("path") or "")
            folder = str(Path(file_path).parent) if file_path else f"obs_{len(used_folders)}"
            if folder in used_folders:
                continue

            title = str(top_obs.get("title") or "Obsidian Note")
            content = (
                await asyncio.to_thread(read_markdown_body, file_path)
                if file_path
                else ""
            )
            if not content:
                content = str(top_obs.get("text") or top_obs.get("snippet") or "")

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


async def resolve_primary_and_secondary_docs(search_results: dict) -> Tuple[str, str]:
    """Compatibility alias extracting primary and secondary header texts from search results."""
    docs = await resolve_documents(search_results)
    primary = docs[0]["header"] if len(docs) > 0 else ""
    secondary = docs[1]["header"] if len(docs) > 1 else ""
    return primary, secondary


def _flatten_top_references(search_results: Any) -> List[Dict[str, Any]]:
    """Normalizes search_results into a list[ChunkDict] satisfying SynthesisOutputDict['top_references']."""
    if isinstance(search_results, list):
        return [r for r in search_results if isinstance(r, dict)]
    grouped = _group_search_results_polymorphic(search_results)
    flat: List[Dict[str, Any]] = []
    for src_type in ("obsidian", "pdf", "zim"):
        for item in grouped.get(src_type, []):
            entry = dict(item)
            entry.setdefault("source", src_type)
            entry.setdefault("source_type", src_type)
            entry.setdefault("page", int(entry.get("page_number") or 1))
            flat.append(entry)
    return flat


def _format_article_as_markdown(article: Dict[str, Any], topic: str) -> str:
    """Formats a structured clinical JSON node into clean Markdown for enhanced_explanation."""
    if not isinstance(article, dict):
        return str(article or "")

    lines: List[str] = []
    context = str(article.get("conditional_context") or "").strip()
    if context:
        lines.append(f"**Clinical Context:** {context}\n")

    subtopics = article.get("subtopics")
    if isinstance(subtopics, list) and subtopics:
        for sub in subtopics:
            if not isinstance(sub, dict):
                continue
            sub_title = str(sub.get("subtopic") or sub.get("display_name") or "").strip()
            if sub_title:
                lines.append(f"### {sub_title}")
            sub_pts = sub.get("clinical_points") or []
            for pt in sub_pts:
                if isinstance(pt, dict) and pt.get("point"):
                    src = pt.get("source")
                    pg = pt.get("page")
                    cite = f" *({src}{f', p.{pg}' if pg else ''})*" if src else ""
                    lines.append(f"- {pt['point']}{cite}")
                elif isinstance(pt, str) and pt.strip():
                    lines.append(f"- {pt.strip()}")
            lines.append("")
    else:
        points = article.get("clinical_points") or []
        for pt in points:
            if isinstance(pt, dict) and pt.get("point"):
                src = pt.get("source")
                pg = pt.get("page")
                cite = f" *({src}{f', p.{pg}' if pg else ''})*" if src else ""
                lines.append(f"- {pt['point']}{cite}")
            elif isinstance(pt, str) and pt.strip():
                lines.append(f"- {pt.strip()}")

    if not lines and article.get("raw_text"):
        return str(article["raw_text"]).strip()

    return "\n".join(lines).strip() or f"Clinical synthesis compiled for {topic}."


async def run_tema_q_synthesis(
    topic: str,
    search_results: dict,
    mcq_context: Optional[dict] = None,
    skip_lm: bool = False,
) -> dict:
    """Executes multi-pass clinical synthesis and variance auditing across resolved sources."""
    topic_key = re.sub(r"[\s_]+", "-", (topic or "").lower().strip())
    flat_refs = _flatten_top_references(search_results)

    if skip_lm:
        return {
            "topic_key": topic_key,
            "display_title": topic,
            "enhanced_explanation": "LM Studio processing skipped (Reference-Only Mode).",
            "distractor_analysis": "LM Studio processing skipped.",
            "unified_article": "",
            "variance_audit": "",
            "top_references": flat_refs or search_results,
            "reference_links": search_results,
        }

    documents = await resolve_documents(search_results, topic=topic)
    if not documents:
        empty_article = {
            "section": "General",
            "subtopic": topic,
            "conditional_context": "",
            "clinical_points": [],
            "discrepancies": "",
        }
        return {
            "topic_key": topic_key,
            "display_title": topic,
            "enhanced_explanation": "No local reference files found.",
            "distractor_analysis": "N/A",
            "unified_article": json.dumps(empty_article),
            "variance_audit": "",
            "top_references": flat_refs or search_results,
            "reference_links": search_results,
        }

    lm_available = True

    # Fast CPU Question-Anchored Subtopic Router & Pruner
    subtopic_buckets = await asyncio.to_thread(
        route_and_prune_subtopics, topic, documents, mcq_context
    )

    synthesized_subtopic_nodes: List[Dict[str, Any]] = []

    if subtopic_buckets:
        for bucket in subtopic_buckets:
            sub_title = bucket.display_name
            pass2_sys_prompt = PASS2_AUDIT_SYSTEM_PROMPT.replace("{subtopic}", sub_title)

            # Pass 1: Primary Source Bulletization (<= 500 words)
            pass1_user_msg = (
                f"Clinical Topic: {topic}\n"
                f"Target Subtopic: {bucket.display_name} ({bucket.question_anchor})\n\n"
                f"Primary Reference:\n{bucket.primary_text}"
            )

            base_note_json: Dict[str, Any] = {}
            if lm_available:
                try:
                    matched_doc = next(
                        (
                            d for d in documents
                            if d.get("source_type") == "pdf"
                            and d.get("page_images")
                            and (d.get("source_label") == bucket.primary_source_label or d.get("page") == bucket.primary_page)
                        ),
                        None,
                    )
                    if matched_doc and matched_doc.get("page_images"):
                        base_note_str = await call_vlm_page_images_async(
                            system_prompt=VLM_PAGE_SYNTHESIS_SYSTEM_PROMPT,
                            topic=topic,
                            subtopic=sub_title,
                            heading_path=str(matched_doc.get("heading_path") or f"{topic} > {sub_title}"),
                            page_images=matched_doc["page_images"],
                            source_label=bucket.primary_source_label,
                            fallback_text=bucket.primary_text,
                            max_tokens=600,
                        )
                    else:
                        base_note_str = await call_llm_async(
                            PASS1_CLINICAL_SYSTEM_PROMPT, pass1_user_msg, max_tokens=600
                        )
                    parsed_base = parse_llm_json(base_note_str)
                    if isinstance(parsed_base, list):
                        base_note_json = {
                            "section": "Clinical Summary",
                            "subtopic": sub_title,
                            "conditional_context": "",
                            "clinical_points": parsed_base,
                            "discrepancies": "",
                        }
                    elif isinstance(parsed_base, dict):
                        base_note_json = parsed_base
                        base_note_json["subtopic"] = sub_title
                    else:
                        base_note_json = {
                            "section": "Clinical Summary",
                            "subtopic": sub_title,
                            "conditional_context": "",
                            "clinical_points": [
                                {
                                    "point": str(base_note_str)[:500],
                                    "source": bucket.primary_source_label,
                                    "page": bucket.primary_page,
                                }
                            ],
                            "discrepancies": "",
                        }
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError) as net_err:
                    logger.warning("LM Studio offline during Pass 1: %s", net_err)
                    lm_available = False
                except Exception as exc:
                    logger.error("Pass 1 LLM synthesis failed for subtopic '%s': %s", sub_title, exc)

            if not base_note_json:
                base_note_json = {
                    "section": "Clinical Summary",
                    "subtopic": sub_title,
                    "conditional_context": "",
                    "clinical_points": [
                        {
                            "point": bucket.primary_text[:500],
                            "source": bucket.primary_source_label,
                            "page": bucket.primary_page,
                        }
                    ],
                    "discrepancies": "",
                }

            subtopic_node = merge_json_nodes(
                None,
                base_note_json,
                metadata={"source": bucket.primary_source_label, "page": bucket.primary_page},
                primary_max_words=500,
            )

            # Pass 2+: Secondary Sources Delta Micro-Editing (<= 1000 words combined)
            for sec_entry in bucket.secondary_entries:
                sec_text = sec_entry.get("text") or ""
                sec_src = sec_entry.get("source_label") or "Secondary Reference"
                sec_page = sec_entry.get("page") or 1

                novel_sec_text = filter_novel_secondary_sentences(
                    sec_text, subtopic_node.get("clinical_points", []), max_words=400
                )
                if not novel_sec_text:
                    continue

                sec_json: Dict[str, Any] = {}
                if lm_available:
                    sec_msg = (
                        f"Clinical Topic: {topic}\n"
                        f"Target Subtopic: {sub_title} ({bucket.question_anchor})\n"
                        f"Primary Baseline Points:\n{json.dumps(subtopic_node.get('clinical_points', []))}\n\n"
                        f"Secondary Reference:\n{novel_sec_text}"
                    )
                    try:
                        sec_json_str = await call_llm_async(
                            pass2_sys_prompt, sec_msg, max_tokens=500
                        )
                        sec_parsed = parse_llm_json(sec_json_str)
                        if isinstance(sec_parsed, list):
                            sec_json = {
                                "section": "Clinical Summary",
                                "subtopic": sub_title,
                                "conditional_context": "",
                                "clinical_points": sec_parsed,
                                "discrepancies": "",
                            }
                        elif isinstance(sec_parsed, dict):
                            sec_json = sec_parsed
                            sec_json["subtopic"] = sub_title
                    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError):
                        lm_available = False
                    except Exception as exc:
                        logger.warning("Pass 2 LLM audit fallback for '%s': %s", sub_title, exc)

                if not sec_json:
                    sec_json = {
                        "section": "Clinical Summary",
                        "subtopic": sub_title,
                        "conditional_context": "",
                        "clinical_points": [
                            {
                                "point": novel_sec_text[:500],
                                "source": sec_src,
                                "page": sec_page,
                            }
                        ],
                        "discrepancies": "",
                    }

                subtopic_node = merge_json_nodes(
                    subtopic_node,
                    sec_json,
                    metadata={"source": sec_src, "page": sec_page},
                    max_words=1000,
                )

            synthesized_subtopic_nodes.append(subtopic_node)

    if synthesized_subtopic_nodes:
        all_points: List[Dict[str, Any]] = []
        all_disc: List[str] = []
        all_ctx: List[str] = []
        for node in synthesized_subtopic_nodes:
            all_points.extend(node.get("clinical_points", []))
            if node.get("conditional_context"):
                all_ctx.append(node["conditional_context"])
            if node.get("discrepancies"):
                all_disc.append(node["discrepancies"])

        final_article = {
            "section": "Clinical Summary",
            "subtopic": topic,
            "conditional_context": "; ".join(all_ctx),
            "clinical_points": all_points,
            "subtopics": synthesized_subtopic_nodes,
            "discrepancies": "\n".join(all_disc).strip(),
        }
    else:
        # Fallback if no subtopic buckets were formed
        doc1 = documents[0]
        base_note_json = {
            "section": "Clinical Summary",
            "subtopic": topic,
            "conditional_context": "",
            "clinical_points": [
                {
                    "point": doc1["raw_text"][:500],
                    "source": doc1["source_label"],
                    "page": doc1["page"],
                }
            ],
            "discrepancies": "",
        }
        final_article = merge_json_nodes(
            None,
            base_note_json,
            metadata={"source": doc1["source_label"], "page": doc1["page"]},
            primary_max_words=500,
        )

    # Pass 3: Optional MCQ Distractor Audit
    distractor_output = "Distractors audited against clinical reference criteria."
    if mcq_context and mcq_context.get("options") and lm_available:
        opts = mcq_context["options"] if isinstance(mcq_context["options"], dict) else {}
        correct_ans = (
            mcq_context.get("correct_answer")
            or mcq_context.get("correct_opt")
            or mcq_context.get("answer")
            or ""
        )
        distractor_user_msg = (
            f"Topic: {topic}\n"
            f"Question: {mcq_context.get('question', '')}\n"
            f"Options:\n"
            f"(A) {opts.get('A', '')}\n"
            f"(B) {opts.get('B', '')}\n"
            f"(C) {opts.get('C', '')}\n"
            f"(D) {opts.get('D', '')}\n"
            f"Correct Answer: {correct_ans}\n\n"
            f"Clinical Reference Synthesis:\n{json.dumps(final_article)}"
        )
        try:
            distractor_output = await call_llm_async(
                DISTRACTOR_ANALYSIS_PROMPT, distractor_user_msg, max_tokens=300
            )
        except Exception:
            distractor_output = "Distractor analysis skipped (LM Studio unresponsive)."

    variance_text = str(
        final_article.get("discrepancies", "")
        or final_article.get("variances", "")
        or ""
    ).strip()

    enhanced_markdown = _format_article_as_markdown(final_article, topic)

    return {
        "topic_key": topic_key,
        "display_title": topic,
        "enhanced_explanation": enhanced_markdown,
        "distractor_analysis": distractor_output,
        "unified_article": json.dumps(final_article),
        "variance_audit": variance_text,
        "top_references": flat_refs or search_results,
        "reference_links": search_results,
    }
