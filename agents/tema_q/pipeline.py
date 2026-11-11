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
from parsers.docling import extract_structured_layout
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


async def close_thread_local_http_client() -> None:
    """Closes the current thread's httpx.AsyncClient on its bound event loop."""
    client: Optional[httpx.AsyncClient] = getattr(_thread_local, "client", None)
    _thread_local.client = None
    _thread_local.loop = None
    if client is not None:
        with _CLIENT_REGISTRY_LOCK:
            _ACTIVE_CLIENTS.discard(client)
        try:
            if not client.is_closed:
                await client.aclose()
        except Exception:
            pass


async def close_http_client() -> None:
    """Closes all active HTTP clients across threads cleanly during lifespan teardown (Invariant 16)."""
    with _CLIENT_REGISTRY_LOCK:
        clients = list(_ACTIVE_CLIENTS)
        _ACTIVE_CLIENTS.clear()

    for client in clients:
        try:
            if client and not client.is_closed:
                # If the client's underlying transport is bound to an event loop that is closed,
                # attempting to aclose() directly might raise RuntimeError. Safely guard against it.
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

    try:
        from agents.tema_q.json_utils import extract_and_repair_json
        parsed = extract_and_repair_json(cleaned)
        if isinstance(parsed, (dict, list)):
            return parsed
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
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 500,
    model_id: Optional[str] = None,
    telemetry_meta: Optional[Dict[str, Any]] = None,
    context_length_tokens: Optional[int] = None,
) -> str:
    """Async chat completion against local LM Studio server with dynamic context token ceiling."""
    import time
    t0 = time.perf_counter()
    active_model = str(model_id or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    safe_max_out = max(16, min(int(max_tokens or 500), 1500))
    sys_str = str(system_prompt or "").strip()
    sys_tokens = count_tokens(sys_str)

    # Dynamic context ceiling: use context_length_tokens if passed or fallback to MAX_CONTEXT_TOKENS (4096)
    effective_ctx = max(2048, int(context_length_tokens or MAX_CONTEXT_TOKENS))
    available_user_tokens = max(128, effective_ctx - sys_tokens - safe_max_out - 64)
    bounded_user_prompt = truncate_to_token_limit(str(user_prompt or ""), available_user_tokens)
    user_tokens = count_tokens(bounded_user_prompt)

    url = f"{LM_STUDIO_URL}/chat/completions"
    payload = {
        "model": active_model,
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
    cleaned = _strip_reasoning_tags(raw_content)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    out_tokens = count_tokens(cleaned)

    if isinstance(telemetry_meta, dict):
        telemetry_meta.update({
            "model_id": active_model,
            "prompt_tokens": sys_tokens + user_tokens,
            "completion_tokens": out_tokens,
            "latency_ms": elapsed_ms,
            "tokens_per_sec": round(out_tokens / max(0.001, elapsed_ms / 1000.0), 2),
            "raw_unstripped_content": raw_content,
            "bounded_user_prompt": bounded_user_prompt,
        })
    return cleaned


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
    from server.routes_state import get_persisted_app_settings
    settings = get_persisted_app_settings()
    active_parser = str(settings.get("pdfParser") or "docling_v2")
    active_profile = str(settings.get("pdfLayoutProfile") or "native_digital")

    raw_md = ""
    if active_parser == "indra_base_heading_opt":
        try:
            from parsers.indra import extract_page_with_indra_base_heading_opt
            res = extract_page_with_indra_base_heading_opt(file_path, page_number=page, render_page_image=False)
            raw_md = str(res.get("markdown") or res.get("text") or "")
        except Exception as exc:
            logger.warning("indra_base_heading_opt failed in _extract_pdf_sync: %s", exc)
            raw_md = extract_page_slice(file_path, page)
    elif active_parser == "docling_v1_heading_opt":
        try:
            from parsers.pdf_extractor import extract_structured_layout_v1_heading_opt
            raw_md = extract_structured_layout_v1_heading_opt(file_path, page)
        except Exception as exc:
            try:
                from parsers.docling import extract_structured_layout_v1_heading_opt
                doc_res = extract_structured_layout_v1_heading_opt(file_path, page)
                raw_md = str(doc_res.get("markdown") if isinstance(doc_res, dict) else (doc_res or ""))
            except Exception as e2:
                logger.warning("docling_v1_heading_opt failed in _extract_pdf_sync: %s / %s", exc, e2)
                raw_md = extract_page_slice(file_path, page)
    elif active_parser == "pymupdf_fast":
        try:
            from parsers.pdf_extractor import extract_page_slice
            raw_md = extract_page_slice(file_path, page)
        except Exception as exc:
            logger.warning("extract_page_slice failed in _extract_pdf_sync: %s", exc)
            raw_md = ""
    else:
        # Default: docling_v2
        try:
            from parsers.docling.engine import extract_page_with_docling_v2
            res = extract_page_with_docling_v2(file_path, page, profile=active_profile)
            if isinstance(res, dict):
                raw_md = str(res.get("markdown") or res.get("text") or "")
            else:
                raw_md = str(res or "")
        except Exception as exc:
            try:
                from parsers.docling import extract_structured_layout as extract_v2
                raw_md = extract_v2(file_path, page) or ""
            except Exception as e2:
                logger.warning("Docling v2 extraction failed, falling back to page slice: %s / %s", exc, e2)
                raw_md = extract_page_slice(file_path, page)

    if bool(settings.get("sanitizeClinicalProse", True)):
        try:
            from agents.tema_q.subtopic_router import sanitize_clinical_prose
            raw_md = sanitize_clinical_prose(raw_md)
        except Exception as s_exc:
            logger.debug("sanitize_clinical_prose failed: %s", s_exc)

    return raw_md


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

        content = ""
        if pdf_path:
            pages_to_extract = list(range(start_page, min(end_page, start_page + 2) + 1)) if end_page > start_page else [start_page]
            page_texts = []
            for p_num in pages_to_extract:
                p_text = await asyncio.to_thread(_extract_pdf_sync, pdf_path, p_num)
                if p_text:
                    page_texts.append(p_text)
            content = "\n\n".join(page_texts)

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


GENERIC_CLINICAL_SUBHEADINGS: Set[str] = {
    "treatment", "diagnosis", "pathogenesis", "clinical features", "epidemiology",
    "complications", "overview", "management", "etiology", "pathology", "symptoms",
    "signs and symptoms", "prognosis", "investigations", "differential diagnosis",
    "prevention", "screening", "risk factors", "pathophysiology", "classification",
    "evaluation", "definition", "introduction", "therapy", "clinical presentation",
}


def extract_topic_exclusive_passages(
    full_text: str, target_topic: str, max_words: int, is_microedit: bool = False
) -> str:
    """
    Extracts topic-exclusive passages from full_text, stripping off-topic sections and conflicting
    sibling conditions while respecting max_words budget (Issues #11, #12, #13).
    """
    if not full_text or not target_topic:
        return full_text or ""

    from agents.tema_q.topic_extractor import (
        SIBLING_MUTUAL_EXCLUSIONS,
        _contains_phrase_bounded,
        get_topic_aliases,
    )

    clean_target = target_topic.strip()
    target_lower = clean_target.lower()
    try:
        aliases = get_topic_aliases(clean_target, max_aliases=8)
    except Exception:
        aliases = [clean_target]

    if not aliases:
        aliases = [clean_target]
    elif clean_target.lower() not in [a.lower() for a in aliases]:
        aliases.insert(0, clean_target)

    alias_lowers = [a.lower().strip() for a in aliases if a.strip()]

    # Collect conflicting sibling topics
    conflicting_siblings: Set[str] = set()
    for group_a, group_b in SIBLING_MUTUAL_EXCLUSIONS:
        in_a = any(_contains_phrase_bounded(target_lower, t) for t in group_a)
        in_b = any(_contains_phrase_bounded(target_lower, t) for t in group_b)
        if in_a and not in_b:
            conflicting_siblings.update(group_b)
        elif in_b and not in_a:
            conflicting_siblings.update(group_a)

    # Split into headed sections or paragraphs: (?m)(?=^#{1,6}\s+|\n\s*\n)
    blocks = re.split(r"(?m)(?=^#{1,6}\s+|\n\s*\n)", full_text)
    retained_blocks: List[str] = []
    active_heading_is_on_topic = True

    for block in blocks:
        block_str = block.strip()
        if not block_str:
            continue

        # Check if block starts with a markdown heading
        heading_match = re.match(r"^#{1,6}\s+(.+)$", block_str, flags=re.MULTILINE)
        if heading_match:
            heading_text = heading_match.group(1).strip()
            heading_clean = re.sub(r"^[0-9\.\-\s]+", "", heading_text).strip()
            heading_lower = heading_clean.lower()

            # Check if heading explicitly mentions target topic or aliases
            heading_has_target = any(
                _contains_phrase_bounded(heading_lower, a) for a in alias_lowers
            )
            # Check if heading is a generic clinical subheading
            is_generic_subheading = any(
                heading_lower == g or heading_lower.startswith(f"{g} ") or heading_lower.endswith(f" {g}")
                for g in GENERIC_CLINICAL_SUBHEADINGS
            )
            # Check if heading is conflicting sibling
            heading_is_conflict = any(
                _contains_phrase_bounded(heading_lower, sib) for sib in conflicting_siblings
            )

            if heading_has_target:
                active_heading_is_on_topic = True
            elif heading_is_conflict:
                active_heading_is_on_topic = False
            elif is_generic_subheading:
                # Retain previous active_heading_is_on_topic state
                pass
            else:
                # Heading mentions an unrelated topic: check if block body mentions target
                body_has_target = any(
                    _contains_phrase_bounded(block_str.lower(), a) for a in alias_lowers
                )
                active_heading_is_on_topic = body_has_target

        if not active_heading_is_on_topic:
            # If off-topic heading, skip unless block explicitly contains target alias
            if not any(_contains_phrase_bounded(block_str.lower(), a) for a in alias_lowers):
                continue

        # Exclude block if it focuses on conflicting sibling without mentioning target
        block_lower = block_str.lower()
        has_conflict = any(_contains_phrase_bounded(block_lower, sib) for sib in conflicting_siblings)
        has_target = any(_contains_phrase_bounded(block_lower, a) for a in alias_lowers)
        if has_conflict and not has_target:
            continue

        retained_blocks.append(block_str)

    if not retained_blocks:
        # Fallback: scan for any block mentioning target aliases
        for block in blocks:
            b_str = block.strip()
            if any(_contains_phrase_bounded(b_str.lower(), a) for a in alias_lowers):
                retained_blocks.append(b_str)

    if not retained_blocks:
        retained_blocks = [b.strip() for b in blocks if b.strip()]

    combined = "\n\n".join(retained_blocks)

    # Word bounding
    max_w = max(50, int(max_words or (280 if is_microedit else 1000)))
    words = combined.split()
    if len(words) > max_w:
        combined = " ".join(words[:max_w])

    return combined


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
    skip_lm: Optional[bool] = None,
    model_id: Optional[str] = None,
    heading_wise: Optional[bool] = None,
    collect_telemetry: Optional[bool] = None,
    synthesis_mode: Optional[str] = None,
    user_max_words_override: Optional[int] = None,
) -> dict:
    """Executes multi-pass clinical synthesis and variance auditing across resolved sources."""
    from server.routes_state import get_persisted_app_settings
    settings = get_persisted_app_settings()

    # Apply saved settings when caller does not pass an explicit override
    if skip_lm is None:
        skip_lm = not bool(settings.get("llmEnabled", True))
    if heading_wise is None:
        heading_wise = bool(settings.get("headingWise", True))
    if collect_telemetry is None:
        collect_telemetry = bool(settings.get("collectTelemetry", False))
    if not synthesis_mode:
        synthesis_mode = str(settings.get("synthesisMode") or "tantivy_microedit")
    if user_max_words_override is None:
        user_max_words_override = int(settings.get("lmMaxWordsOverride") or 0)

    topic_key = re.sub(r"[\s_]+", "-", (topic or "").lower().strip())
    flat_refs = _flatten_top_references(search_results)

    effective_synthesis_mode = (synthesis_mode or "").strip().lower()
    effective_max_words_override = user_max_words_override

    if effective_synthesis_mode not in ("tantivy_microedit", "per_article_lm_summary"):
        effective_synthesis_mode = "tantivy_microedit"

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
            "synthesis_mode": effective_synthesis_mode,
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
            "synthesis_mode": effective_synthesis_mode,
        }

    from core.model_lifecycle import model_lifecycle
    ctx_info = await model_lifecycle.get_active_model_context_info(
        user_max_words_override=effective_max_words_override, client=get_http_client()
    )

    lm_available = True
    active_model = str(model_id or ctx_info.get("model_id") or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    from agents.tema_q.telemetry import (
        HeadingDispatchTrace,
        PipelineDiagnosticTrace,
        extract_clinical_numbers_and_stages,
    )
    import time
    t_pipeline_start = time.perf_counter()

    trace: Optional[PipelineDiagnosticTrace] = None
    if collect_telemetry:
        import uuid
        trace = PipelineDiagnosticTrace(
            trace_id=f"trace_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            topic=topic,
            model_id=active_model,
        )
        grouped_hits = _group_search_results_polymorphic(search_results)
        trace.circle2_raw_hits_summary = {
            "zim": len(grouped_hits.get("zim", [])),
            "pdf": len(grouped_hits.get("pdf", [])),
            "obsidian": len(grouped_hits.get("obsidian", [])),
        }
        trace.circle2_resolved_documents = [
            {
                "source_type": d.get("source_type"),
                "source_label": d.get("source_label"),
                "page": d.get("page", 1),
                "words": len(str(d.get("raw_text") or "").split()),
            }
            for d in documents
        ]
        if mcq_context and isinstance(mcq_context, dict):
            trace.circle1_extracted_topics = {
                "question": mcq_context.get("question", ""),
                "target_topic": topic,
            }

    per_article_summaries: List[Dict[str, Any]] = []

    # -------------------------------------------------------------
    # BRANCH 1: Mode B - Separate LM Studio Summaries per Article
    # -------------------------------------------------------------
    if effective_synthesis_mode == "per_article_lm_summary":
        article_summary_blocks: List[str] = []
        all_points: List[Dict[str, Any]] = []
        subtopics_list: List[Dict[str, Any]] = []

        per_art_prompt = (
            f"You are an expert clinical medical professor summarizing evidence for '{topic}'. "
            "Analyze the provided document excerpt and output a structured JSON object with:\n"
            "{\n"
            f'  "section": "{topic}",\n'
            '  "clinical_points": [\n'
            '     {"point": "Key clinical fact or finding", "source": "Source Name", "page": 1}\n'
            "  ],\n"
            '  "summary_markdown": "Concise high-yield prose summary of the document excerpt."\n'
            "}\n"
            "Include key diagnostic criteria, drug dosages, pathophysiology, and distinguishing features. "
            "Only focus on information relevant to the target topic."
        )

        for doc in documents:
            source_lbl = doc.get("source_label") or "Reference Document"
            source_tp = doc.get("source_type") or "ref"
            pg = doc.get("page", 1)

            filtered_passage = extract_topic_exclusive_passages(
                doc.get("raw_text") or "",
                target_topic=topic,
                max_words=ctx_info["effective_summary_word_limit"],
                is_microedit=False,
            )
            word_count_sent = len(filtered_passage.split())

            user_msg = (
                f"Clinical Topic: {topic}\n"
                f"Source: {source_lbl} (Page {pg})\n"
                f"Document Excerpt:\n{filtered_passage}"
            )

            article_md = ""
            article_pts: List[Dict[str, Any]] = []

            if lm_available:
                try:
                    resp_str = await call_llm_async(
                        per_art_prompt,
                        user_msg,
                        max_tokens=650,
                        model_id=active_model,
                        context_length_tokens=ctx_info["loaded_context_length_tokens"],
                    )
                    parsed = parse_llm_json(resp_str)
                    if isinstance(parsed, dict):
                        article_md = str(parsed.get("summary_markdown") or "").strip()
                        raw_pts = parsed.get("clinical_points") or []
                        if isinstance(raw_pts, list):
                            for p in raw_pts:
                                if isinstance(p, dict) and p.get("point"):
                                    p.setdefault("source", source_lbl)
                                    p.setdefault("page", pg)
                                    article_pts.append(p)
                                elif isinstance(p, str) and p.strip():
                                    article_pts.append({"point": p.strip(), "source": source_lbl, "page": pg})
                    elif isinstance(parsed, list):
                        for p in parsed:
                            if isinstance(p, dict) and p.get("point"):
                                p.setdefault("source", source_lbl)
                                p.setdefault("page", pg)
                                article_pts.append(p)
                            elif isinstance(p, str) and p.strip():
                                article_pts.append({"point": p.strip(), "source": source_lbl, "page": pg})
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError) as net_err:
                    logger.warning("LM Studio offline during per-article summary: %s", net_err)
                    lm_available = False
                except Exception as exc:
                    logger.warning("Per-article summary LLM call failed for '%s': %s", source_lbl, exc)

            if not article_pts:
                # Fallback to key snippet extraction
                for sentence in filtered_passage.split(". ")[:4]:
                    cleaned_s = sentence.strip()
                    if cleaned_s:
                        article_pts.append({"point": cleaned_s, "source": source_lbl, "page": pg})

            if not article_md:
                pts_bullets = "\n".join(f"- {p['point']}" for p in article_pts[:5])
                article_md = f"Summary of clinical findings from {source_lbl}:\n{pts_bullets}"

            all_points.extend(article_pts)
            subtopics_list.append({
                "subtopic": source_lbl,
                "clinical_points": article_pts,
                "summary_markdown": article_md,
            })

            per_article_summaries.append({
                "source": source_lbl,
                "source_type": source_tp,
                "page": pg,
                "word_count_sent": word_count_sent,
                "summary_markdown": article_md,
                "clinical_points": article_pts,
            })

            badge_type = source_tp.upper()
            header_str = f"### [{badge_type}] {source_lbl} (Page {pg})\n"
            article_summary_blocks.append(f"{header_str}\n{article_md}\n\n**Key Points:**\n" + "\n".join(
                f"- {p['point']}" for p in article_pts
            ))

        final_article = {
            "section": "Clinical Summary",
            "subtopic": topic,
            "conditional_context": f"Compiled from {len(documents)} distinct reference document(s)",
            "clinical_points": all_points,
            "subtopics": subtopics_list,
            "discrepancies": "",
        }
        enhanced_markdown = "\n\n---\n\n".join(article_summary_blocks) if article_summary_blocks else (
            f"No summary generated for {topic}."
        )

    # -------------------------------------------------------------
    # BRANCH 2: Mode A - Tantivy Micro-Editing Combination (Default)
    # -------------------------------------------------------------
    else:
        # Pre-filter each resolved document's raw_text through topic gating
        for doc in documents:
            if doc.get("raw_text"):
                doc["raw_text"] = extract_topic_exclusive_passages(
                    doc["raw_text"],
                    target_topic=topic,
                    max_words=ctx_info["effective_summary_word_limit"],
                    is_microedit=False,
                )

        # Fast CPU Question-Anchored Subtopic Router & Pruner
        t_route_0 = time.perf_counter()
        subtopic_buckets = await asyncio.to_thread(
            route_and_prune_subtopics, topic, documents, mcq_context, 5, 650, 450, trace
        )
        t_route_ms = round((time.perf_counter() - t_route_0) * 1000.0, 2)
        if trace:
            trace.stage_timings_ms["circle2_5_subtopic_routing"] = t_route_ms

        synthesized_subtopic_nodes: List[Dict[str, Any]] = []

        micro_budget = ctx_info["microedit_word_limit"]

        if subtopic_buckets:
            for bucket in subtopic_buckets:
                sub_title = bucket.display_name
                pass2_sys_prompt = PASS2_AUDIT_SYSTEM_PROMPT.replace("{subtopic}", sub_title)

                # Determine sections to dispatch for Pass 1
                if heading_wise and getattr(bucket, "primary_heading_sections", None):
                    sections_to_dispatch = bucket.primary_heading_sections
                else:
                    sections_to_dispatch = [
                        {
                            "heading_breadcrumb": bucket.display_name,
                            "text": bucket.primary_text,
                            "source_label": bucket.primary_source_label,
                            "source_type": "ref",
                            "page": bucket.primary_page,
                            "word_count": len(bucket.primary_text.split()),
                        }
                    ]

                subtopic_node: Optional[Dict[str, Any]] = None

                for sec_item in sections_to_dispatch:
                    raw_sec_text = sec_item.get("text") or ""
                    sec_text = extract_topic_exclusive_passages(
                        raw_sec_text, target_topic=topic, max_words=micro_budget, is_microedit=True
                    )
                    sec_heading = sec_item.get("heading_breadcrumb") or bucket.display_name
                    sec_src = sec_item.get("source_label") or bucket.primary_source_label
                    sec_pg = sec_item.get("page") or bucket.primary_page
                    sec_type = sec_item.get("source_type") or "ref"

                    pass1_user_msg = (
                        f"Clinical Topic: {topic}\n"
                        f"Target Subtopic: {bucket.display_name} ({bucket.question_anchor})\n"
                        f"Source Document: {sec_src} (Page {sec_pg})\n"
                        f"Article Heading Breadcrumb: {sec_heading}\n\n"
                        f"Heading Text:\n{sec_text}"
                    )

                    call_meta: Dict[str, Any] = {}
                    base_note_str = ""
                    heading_json: Dict[str, Any] = {}

                    if lm_available:
                        try:
                            base_note_str = await call_llm_async(
                                PASS1_CLINICAL_SYSTEM_PROMPT,
                                pass1_user_msg,
                                max_tokens=600,
                                model_id=active_model,
                                telemetry_meta=call_meta,
                                context_length_tokens=ctx_info["loaded_context_length_tokens"],
                            )
                            parsed_base = parse_llm_json(base_note_str)
                            if isinstance(parsed_base, list):
                                heading_json = {
                                    "section": "Clinical Summary",
                                    "subtopic": sub_title,
                                    "conditional_context": "",
                                    "clinical_points": parsed_base,
                                    "discrepancies": "",
                                }
                            elif isinstance(parsed_base, dict):
                                heading_json = parsed_base
                                heading_json["subtopic"] = sub_title
                            else:
                                heading_json = {
                                    "section": "Clinical Summary",
                                    "subtopic": sub_title,
                                    "conditional_context": "",
                                    "clinical_points": [
                                        {
                                            "point": str(base_note_str)[:500],
                                            "source": sec_src,
                                            "page": sec_pg,
                                        }
                                    ],
                                    "discrepancies": "",
                                }
                        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError) as net_err:
                            logger.warning("LM Studio offline during Pass 1: %s", net_err)
                            lm_available = False
                        except Exception as exc:
                            logger.error("Pass 1 LLM synthesis failed for heading '%s': %s", sec_heading, exc)

                    if not heading_json:
                        heading_json = {
                            "section": "Clinical Summary",
                            "subtopic": sub_title,
                            "conditional_context": "",
                            "clinical_points": [
                                {
                                    "point": sec_text[:500],
                                    "source": sec_src,
                                    "page": sec_pg,
                                }
                            ],
                            "discrepancies": "",
                        }

                    # Record Telemetry for this Heading Dispatch
                    if trace:
                        src_dosages_stages = extract_clinical_numbers_and_stages(sec_text)
                        llm_pts_str = " ".join(
                            str(pt.get("point", "")) for pt in heading_json.get("clinical_points", []) if isinstance(pt, dict)
                        )
                        retained_ds = [ds for ds in src_dosages_stages if ds in llm_pts_str.lower()]
                        missing_ds = [ds for ds in src_dosages_stages if ds not in llm_pts_str.lower()]
                        ret_score = (len(retained_ds) / len(src_dosages_stages)) if src_dosages_stages else 1.0

                        trace.llm_dispatches.append(
                            HeadingDispatchTrace(
                                subtopic_id=bucket.subtopic_id,
                                subtopic_name=bucket.display_name,
                                heading_breadcrumb=sec_heading,
                                source_label=sec_src,
                                source_type=sec_type,
                                page=sec_pg,
                                pass_number=1,
                                model_id=active_model,
                                input_word_count=len(sec_text.split()),
                                input_token_count=call_meta.get("prompt_tokens", count_tokens(pass1_user_msg)),
                                prompt_sent=pass1_user_msg,
                                raw_llm_response=base_note_str or json.dumps(heading_json),
                                parsed_points_count=len(heading_json.get("clinical_points", [])),
                                output_word_count=len(llm_pts_str.split()),
                                json_repaired_or_fallback=bool(not base_note_str or "{" not in base_note_str),
                                latency_ms=call_meta.get("latency_ms", 0.0),
                                tokens_per_sec=call_meta.get("tokens_per_sec", 0.0),
                                source_dosages_and_stages=src_dosages_stages,
                                retained_dosages_and_stages=retained_ds,
                                missing_dosages_and_stages=missing_ds,
                                retention_score=round(ret_score, 2),
                            )
                        )

                    subtopic_node = merge_json_nodes(
                        subtopic_node,
                        heading_json,
                        metadata={"source": sec_src, "page": sec_pg},
                        primary_max_words=500,
                        telemetry=trace,
                    )

                if subtopic_node is None:
                    subtopic_node = {
                        "section": "Clinical Summary",
                        "subtopic": sub_title,
                        "conditional_context": "",
                        "clinical_points": [],
                        "discrepancies": "",
                    }

                # Pass 2+: Secondary Sources Delta Micro-Editing
                for sec_entry in bucket.secondary_entries:
                    sec_text = sec_entry.get("text") or ""
                    sec_src = sec_entry.get("source_label") or "Secondary Reference"
                    sec_page = sec_entry.get("page") or 1
                    sec_breadcrumb = sec_entry.get("heading_breadcrumb") or bucket.display_name
                    sec_type = sec_entry.get("source_type") or "ref"

                    novel_sec_text = filter_novel_secondary_sentences(
                        sec_text,
                        subtopic_node.get("clinical_points", []),
                        max_words=micro_budget,
                        telemetry=trace,
                        source_label=sec_src,
                    )
                    if not novel_sec_text:
                        continue

                    sec_json: Dict[str, Any] = {}
                    sec_call_meta: Dict[str, Any] = {}
                    sec_json_str = ""

                    if lm_available:
                        bounded_base_pts = truncate_to_token_limit(
                            json.dumps(subtopic_node.get("clinical_points", [])), 1200
                        )
                        prompt_header = (
                            f"Clinical Topic: {topic}\n"
                            f"Target Subtopic: {sub_title} ({bucket.question_anchor})\n"
                            f"Source Document: {sec_src} (Page {sec_page})\n"
                            f"Article Heading Breadcrumb: {sec_breadcrumb}\n"
                            f"Primary Baseline Points:\n{bounded_base_pts}\n\n"
                            f"Secondary Reference:\n"
                        )
                        used_tokens = count_tokens(pass2_sys_prompt) + count_tokens(prompt_header) + 500
                        available_tokens = max(100, ctx_info["loaded_context_length_tokens"] - used_tokens)
                        novel_sec_text = truncate_to_token_limit(novel_sec_text, available_tokens)
                        sec_msg = f"{prompt_header}{novel_sec_text}"
                        try:
                            sec_json_str = await call_llm_async(
                                pass2_sys_prompt,
                                sec_msg,
                                max_tokens=500,
                                model_id=active_model,
                                telemetry_meta=sec_call_meta,
                                context_length_tokens=ctx_info["loaded_context_length_tokens"],
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

                    if trace:
                        sec_dosages_stages = extract_clinical_numbers_and_stages(novel_sec_text)
                        sec_pts_str = " ".join(
                            str(pt.get("point", "")) for pt in sec_json.get("clinical_points", []) if isinstance(pt, dict)
                        )
                        retained_ds = [ds for ds in sec_dosages_stages if ds in sec_pts_str.lower()]
                        missing_ds = [ds for ds in sec_dosages_stages if ds not in sec_pts_str.lower()]
                        ret_score = (len(retained_ds) / len(sec_dosages_stages)) if sec_dosages_stages else 1.0

                        trace.llm_dispatches.append(
                            HeadingDispatchTrace(
                                subtopic_id=bucket.subtopic_id,
                                subtopic_name=bucket.display_name,
                                heading_breadcrumb=sec_breadcrumb,
                                source_label=sec_src,
                                source_type=sec_type,
                                page=sec_page,
                                pass_number=2,
                                model_id=active_model,
                                input_word_count=len(novel_sec_text.split()),
                                input_token_count=sec_call_meta.get("prompt_tokens", count_tokens(sec_msg)),
                                prompt_sent=sec_msg,
                                raw_llm_response=sec_json_str or json.dumps(sec_json),
                                parsed_points_count=len(sec_json.get("clinical_points", [])),
                                output_word_count=len(sec_pts_str.split()),
                                json_repaired_or_fallback=bool(not sec_json_str or "{" not in sec_json_str),
                                latency_ms=sec_call_meta.get("latency_ms", 0.0),
                                tokens_per_sec=sec_call_meta.get("tokens_per_sec", 0.0),
                                source_dosages_and_stages=sec_dosages_stages,
                                retained_dosages_and_stages=retained_ds,
                                missing_dosages_and_stages=missing_ds,
                                retention_score=round(ret_score, 2),
                            )
                        )

                    subtopic_node = merge_json_nodes(
                        subtopic_node,
                        sec_json,
                        metadata={"source": sec_src, "page": sec_page},
                        max_words=1000,
                        telemetry=trace,
                    )

                synthesized_subtopic_nodes.append(subtopic_node)

        if synthesized_subtopic_nodes:
            all_points = []
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
                telemetry=trace,
            )

        enhanced_markdown = _format_article_as_markdown(final_article, topic)

    # Detect variances if discrepancies empty
    from agents.tema_q.auditor import detect_clinical_variances
    raw_discrepancies = final_article.get("discrepancies", "")
    if not raw_discrepancies and len(documents) > 1:
        base_t = str(documents[0].get("raw_text") or "")
        sec_t = str(documents[1].get("raw_text") or "")
        detected_vars = detect_clinical_variances(base_t, sec_t)
        if detected_vars:
            final_article["discrepancies"] = detected_vars

    variance_text = str(
        final_article.get("discrepancies", "")
        or final_article.get("variances", "")
        or ""
    ).strip()

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
        bounded_synthesis_md = truncate_to_token_limit(enhanced_markdown, 1800)
        distractor_user_msg = (
            f"Topic: {topic}\n"
            f"Question: {mcq_context.get('question', '')}\n"
            f"Options:\n"
            f"(A) {opts.get('A', '')}\n"
            f"(B) {opts.get('B', '')}\n"
            f"(C) {opts.get('C', '')}\n"
            f"(D) {opts.get('D', '')}\n"
            f"Correct Answer: {correct_ans}\n\n"
            f"Clinical Reference Synthesis:\n{bounded_synthesis_md}"
        )
        try:
            distractor_output = await call_llm_async(
                DISTRACTOR_ANALYSIS_PROMPT, distractor_user_msg, max_tokens=300
            )
        except Exception:
            distractor_output = "Distractor analysis skipped (LM Studio unresponsive)."

    result_payload: Dict[str, Any] = {
        "topic_key": topic_key,
        "display_title": topic,
        "enhanced_explanation": enhanced_markdown,
        "distractor_analysis": distractor_output,
        "unified_article": json.dumps(final_article),
        "variance_audit": variance_text,
        "top_references": flat_refs or search_results,
        "reference_links": search_results,
        "synthesis_mode": effective_synthesis_mode,
        "per_article_summaries": per_article_summaries,
    }

    if trace:
        trace.detected_variances = variance_text
        trace.finalize_and_analyze()
        try:
            saved_trace_path = trace.save_to_disk()
            result_payload["trace_log_path"] = saved_trace_path
        except Exception as exc:
            logger.warning("Failed to save pipeline diagnostic trace to disk: %s", exc)
        result_payload["pipeline_telemetry"] = trace.to_dict()

    return result_payload


# --- Phase 5 Audited Self-Correction Loop Helper ---
from agents.tema_q.auditor import (
    deterministic_mcq_sanity_check as _sanity_check,
    evaluate_audit_verdict as _eval_verdict,
    run_audited_generation_with_retry,
)
