import gc
import logging
import math
import os
import re
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# Enforce Invariant 1: Zero VRAM for Backend
os.environ["CUDA_VISIBLE_DEVICES"] = ""

try:
    import tantivy
except ImportError:
    tantivy = None

from config import INDEX_PATH
from parsers.reranker import rerank_chunks
from parsers.zim_reader import expand_query_via_zim, search_zim_articles

logger = logging.getLogger("medisearch.tantivy")

_TANTIVY_LOCK = threading.RLock()
_schema: Optional[Any] = None
_index: Optional[Any] = None

_RESERVED_QUERY_TOKENS = {"and", "or", "not", "in", "to"}


def _get_fallback_embedder():
    """Returns the process-wide cached CPUEmbeddingEngine singleton (Invariant 8)."""
    try:
        from parsers.embedding_engine import get_embedding_engine

        return get_embedding_engine()
    except Exception as exc:
        logger.debug("Failed to retrieve CPUEmbeddingEngine singleton: %s", exc)
        return None


def _get_fallback_lancedb():
    """Returns the process-wide cached LanceDBEngine singleton (Invariant 8)."""
    try:
        from parsers.lancedb_engine import get_lancedb_engine

        return get_lancedb_engine()
    except Exception as exc:
        logger.debug("Failed to retrieve LanceDBEngine singleton: %s", exc)
        return None


def get_schema() -> Optional[Any]:
    """Builds the Tantivy schema with BM25 stemmers and stored metadata (Section 4)."""
    global _schema
    if tantivy is None:
        return None
    with _TANTIVY_LOCK:
        if _schema is None:
            builder = tantivy.SchemaBuilder()
            builder.add_text_field("doc_id", stored=True)
            builder.add_text_field("source_type", stored=True)
            builder.add_text_field("file_path", stored=True)
            builder.add_integer_field("page_number", stored=True)
            builder.add_text_field("title", stored=True, tokenizer_name="en_stem")
            builder.add_text_field("heading_path", stored=True, tokenizer_name="en_stem")
            builder.add_integer_field("doc_length", stored=True)
            builder.add_text_field("snippet", stored=True, tokenizer_name="en_stem")
            builder.add_text_field("document_body", stored=False, tokenizer_name="en_stem")
            builder.add_text_field("obsidian_body", stored=True, tokenizer_name="en_stem")
            _schema = builder.build()
        return _schema


def _remove_stale_lock_files() -> None:
    """Removes lingering Tantivy lock files if a previous process terminated abruptly (Invariant 17)."""
    if not os.path.exists(INDEX_PATH):
        return
    for lock_name in (".tantivy-writer.lock", ".tantivy-meta.lock"):
        lock_file = os.path.join(INDEX_PATH, lock_name)
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except OSError:
                pass


def get_index(reset: bool = False) -> Optional[Any]:
    """Retrieves or builds the Tantivy index, handling Windows file lock release gracefully."""
    global _index
    if tantivy is None:
        return None

    with _TANTIVY_LOCK:
        if reset:
            _index = None
            gc.collect()  # Release Windows mmap file handles before deletion
            time.sleep(0.05)
            if os.path.exists(INDEX_PATH):
                try:
                    shutil.rmtree(INDEX_PATH)
                except OSError as exc:
                    logger.warning(
                        "Windows file lock encountered while clearing Tantivy index: %s", exc
                    )
                    _remove_stale_lock_files()

        if _index is None:
            os.makedirs(INDEX_PATH, exist_ok=True)
            schema = get_schema()
            try:
                _index = tantivy.Index(schema, path=str(INDEX_PATH))
            except Exception as exc:
                logger.warning("Recreating Tantivy index due to schema/corruption (%s)...", exc)
                gc.collect()
                try:
                    shutil.rmtree(INDEX_PATH, ignore_errors=True)
                except Exception:
                    pass
                os.makedirs(INDEX_PATH, exist_ok=True)
                _index = tantivy.Index(schema, path=str(INDEX_PATH))
        return _index


def _sanitize_query_tokens(raw_text: str) -> str:
    """Strips punctuation and neutralizes Tantivy boolean keywords (AND/OR/NOT/IN/TO)."""
    cleaned = re.sub(r"[^\w\s]", " ", raw_text or "")
    tokens = [
        tok.lower()
        for tok in cleaned.split()
        if tok and tok.lower() not in _RESERVED_QUERY_TOKENS
    ]
    return " ".join(tokens).strip()


def _compute_depth_score(
    bm25_score: float,
    query_str: str,
    query_terms: Set[str],
    title: str,
    heading_path: str,
    body_text: str,
    doc_length: int,
) -> Tuple[float, float]:
    """Computes a composite depth score favoring detailed articles over brief mentions."""
    q_lower = query_str.lower().strip()
    title_lower = (title or "").lower()
    heading_lower = (heading_path or "").lower()
    body_lower = (body_text or "").lower()

    # 1. Heading & Title Relevance Boost
    heading_boost = 0.0
    if q_lower and q_lower in title_lower:
        heading_boost += 20.0
    if q_lower and q_lower in heading_lower:
        heading_boost += 15.0

    # 2. Topic Mention Count & Term Density
    exact_phrase_mentions = body_lower.count(q_lower) if len(q_lower) >= 3 else 0
    term_mentions = sum(body_lower.count(t) for t in query_terms if len(t) >= 3)
    distinct_terms_matched = sum(
        1 for t in query_terms if t in body_lower or t in title_lower or t in heading_lower
    )

    if exact_phrase_mentions >= 3 or term_mentions >= 5:
        coverage_boost = 12.0 + min(exact_phrase_mentions * 2.5 + term_mentions * 0.8, 25.0)
    elif exact_phrase_mentions >= 1 or term_mentions >= 2:
        coverage_boost = 5.0 + exact_phrase_mentions * 2.0 + term_mentions * 0.5
    else:
        coverage_boost = distinct_terms_matched * 1.5

    # 3. Comprehensive Article Length Bonus
    effective_length = max(int(doc_length or 0), len(body_text))
    length_boost = math.log10(effective_length + 10) * 3.0

    depth_bonus = heading_boost + coverage_boost + length_boost
    total_score = (float(bm25_score) * 1.2) + depth_bonus
    return total_score, depth_bonus


def _format_chunk_dict(
    *,
    doc_id: str,
    source: str,
    file_path: str,
    page: int,
    title: str,
    heading: str,
    heading_path: str,
    text: str,
    snippet: str,
    doc_length: int,
    score: float,
    rerank_score: Optional[float] = None,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Formats a search hit strictly to the Section 4 ChunkDict contract."""
    safe_page = max(1, int(page or 1))
    safe_len = max(0, int(doc_length or len(text)))
    safe_score = round(float(score), 4)
    safe_rerank = round(float(rerank_score if rerank_score is not None else score), 4)
    clean_text = text[:1200] if len(text) > 1200 else text
    clean_snippet = snippet[:400] if len(snippet) > 400 else snippet

    return {
        "id": str(doc_id),
        "doc_id": str(doc_id),
        "text": clean_text,
        "snippet": clean_snippet,
        "title": str(title or "Untitled"),
        "heading": str(heading or heading_path or title or "Untitled"),
        "heading_path": str(heading_path or heading or title or "Untitled"),
        "source": str(source),
        "file_path": str(file_path or ""),
        "page": safe_page,
        "page_number": safe_page,
        "doc_length": safe_len,
        "article_length": safe_len,
        "score": safe_rerank,
        "rerank_score": safe_rerank,
        "parent_id": parent_id,
    }


def search_index(query_str: str, limit_per_source: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """Executes a depth-weighted hybrid search across Obsidian, PDFs, and ZIM archives."""
    results: Dict[str, List[Dict[str, Any]]] = {"obsidian": [], "pdf": [], "zim": []}
    if not query_str or not query_str.strip():
        return results

    limit_per_source = max(1, int(limit_per_source or 3))

    try:
        from agents.topic_extractor import get_topic_aliases

        topic_aliases = get_topic_aliases(query_str)
    except Exception:
        topic_aliases = [query_str]
        if "," in query_str:
            parts = [p.strip() for p in query_str.split(",") if p.strip()]
            if len(parts) == 2:
                topic_aliases.append(f"{parts[1]} {parts[0]}")

    if not topic_aliases:
        topic_aliases = [query_str]

    primary_query = topic_aliases[0] if topic_aliases else query_str
    expanded_query_str = expand_query_via_zim(primary_query)
    safe_query = _sanitize_query_tokens(expanded_query_str) or _sanitize_query_tokens(query_str)

    if not safe_query:
        return results

    # Stage 1: Tantivy Lexical Search
    try:
        index = get_index()
        if index is not None:
            index.reload()
            searcher = index.searcher()
            search_fields = ["title", "heading_path", "snippet", "document_body", "obsidian_body"]

            alias_clauses = []
            for idx, alias_item in enumerate(topic_aliases[:5]):
                c_alias = _sanitize_query_tokens(alias_item)
                if not c_alias:
                    continue
                w = max(6 - idx * 2, 2)
                alias_clauses.append(
                    f'title:"{c_alias}"^{w} OR heading_path:"{c_alias}"^{max(w - 1, 1)} '
                    f'OR document_body:"{c_alias}"^2 OR obsidian_body:"{c_alias}"^2'
                )

            boosted = (
                f"({' OR '.join(alias_clauses)}) OR ({safe_query})"
                if alias_clauses
                else safe_query
            )

            try:
                query = index.parse_query(boosted, search_fields)
            except Exception:
                query = index.parse_query(safe_query, ["title", "document_body", "obsidian_body"])

            top_docs = searcher.search(query, limit=60)

            all_query_terms: Set[str] = set()
            for a_item in topic_aliases[:5]:
                for t in _sanitize_query_tokens(a_item).split():
                    if len(t) >= 2:
                        all_query_terms.add(t)

            # Deduplicate per (source, file_path) keeping the highest depth-scored passage (Invariant 12)
            dedup_by_file: Dict[Tuple[str, str], Dict[str, Any]] = {}

            for bm25_score, doc_address in top_docs.hits:
                doc = searcher.doc(doc_address)
                source_list = doc.get("source_type", [])
                if not source_list:
                    continue

                source = str(source_list[0])
                if source not in results:
                    continue

                title = str((doc.get("title", ["Untitled"]) or ["Untitled"])[0])
                heading_path = str((doc.get("heading_path", [title]) or [title])[0])
                file_path = str((doc.get("file_path", [""]) or [""])[0])
                doc_id = str((doc.get("doc_id", [""]) or [""])[0])
                page_num = max(1, int((doc.get("page_number", [1]) or [1])[0] or 1))

                body_text = str(
                    (doc.get("obsidian_body", [""]) or [""])[0]
                    or (doc.get("snippet", [""]) or [""])[0]
                )
                stored_len_list = doc.get("doc_length", [])
                doc_length = int(stored_len_list[0]) if stored_len_list else len(body_text)

                comp_score, depth_bonus = _compute_depth_score(
                    bm25_score=bm25_score,
                    query_str=primary_query,
                    query_terms=all_query_terms,
                    title=title,
                    heading_path=heading_path,
                    body_text=body_text,
                    doc_length=doc_length,
                )

                cand_item = {
                    "id": doc_id,
                    "doc_id": doc_id,
                    "source": source,
                    "file_path": file_path,
                    "page": page_num,
                    "page_number": page_num,
                    "title": title,
                    "heading": heading_path,
                    "heading_path": heading_path,
                    "text": body_text[:800],  # Invariant 9: Mandatory 'text' key for FlashRank
                    "snippet": body_text[:400],
                    "doc_length": doc_length,
                    "article_length": doc_length,
                    "score": comp_score,
                    "rerank_score": comp_score,
                    "depth_bonus": depth_bonus,
                    "parent_id": None,
                }

                file_key = (source, (file_path or doc_id).lower())
                existing = dedup_by_file.get(file_key)
                if existing is None or comp_score > existing["score"]:
                    # Invariant 12: Atomically sync page, heading, text, and snippet to highest hit
                    dedup_by_file[file_key] = cand_item

            candidates = sorted(dedup_by_file.values(), key=lambda x: x["score"], reverse=True)

            # Optional sibling topic filter before cross-encoder reranking
            try:
                from agents.topic_extractor import filter_sibling_topics

                filtered = filter_sibling_topics(primary_query, candidates)
                if filtered:
                    candidates = filtered
            except Exception:
                pass

            # Stage 2: Cross-Encoder Reranking (FlashRank CPU)
            top_candidates = candidates[:24]
            reranked = rerank_chunks(query_str, top_candidates, top_k=24) if top_candidates else []

            for cand in reranked:
                src = cand["source"]
                if len(results[src]) < limit_per_source:
                    results[src].append(
                        _format_chunk_dict(
                            doc_id=cand["doc_id"],
                            source=src,
                            file_path=cand["file_path"],
                            page=cand["page"],
                            title=cand["title"],
                            heading=cand.get("heading", cand["title"]),
                            heading_path=cand.get("heading_path", cand.get("heading", cand["title"])),
                            text=cand.get("text", cand.get("snippet", "")),
                            snippet=cand.get("snippet", cand.get("text", "")),
                            doc_length=cand.get("doc_length", 0),
                            score=cand["score"],
                            rerank_score=cand.get("rerank_score", cand["score"]),
                        )
                    )
    except Exception as exc:
        logger.warning("Tantivy search error for '%s': %s", query_str, exc)

    # Stage 3: Zero-Copy >10GB ZIM Lookup (Invariant 11)
    if len(results["zim"]) < limit_per_source:
        try:
            zim_hits = search_zim_articles(query_str, limit=limit_per_source - len(results["zim"]))
            existing_titles = {r["title"].lower() for r in results["zim"]}
            for zh in zim_hits:
                zh_title = str(zh.get("title", "Untitled"))
                if zh_title.lower() not in existing_titles:
                    zh_text = str(zh.get("text") or zh.get("snippet") or "")
                    zh_doc_id = str(zh.get("id") or zh.get("doc_id") or f"zim:{zh_title}")
                    results["zim"].append(
                        _format_chunk_dict(
                            doc_id=zh_doc_id,
                            source="zim",
                            file_path=str(zh.get("file_path") or ""),
                            page=int(zh.get("page") or zh.get("page_number") or 1),
                            title=zh_title,
                            heading=str(zh.get("heading") or zh_title),
                            heading_path=str(zh.get("heading_path") or zh.get("heading") or zh_title),
                            text=zh_text,
                            snippet=str(zh.get("snippet") or zh_text),
                            doc_length=int(zh.get("doc_length") or zh.get("article_length") or len(zh_text)),
                            score=float(zh.get("score", 1.0)),
                            rerank_score=float(zh.get("rerank_score", zh.get("score", 1.0))),
                        )
                    )
                    existing_titles.add(zh_title.lower())
        except Exception as exc:
            logger.debug("ZIM direct search bypass skipped: %s", exc)

    # Stage 4: LanceDB Hybrid Vector + FTS Fallback (Using shared process singletons)
    if len(results["pdf"]) < limit_per_source or len(results["obsidian"]) < limit_per_source:
        try:
            embedder = _get_fallback_embedder()
            lancedb_engine = _get_fallback_lancedb()

            if lancedb_engine is not None and lancedb_engine.count_rows() > 0:
                lance_hits: List[Dict[str, Any]] = []
                if embedder is not None:
                    from parsers.embedding_engine import is_embedding_model_downloaded

                    if is_embedding_model_downloaded(embedder.model_id):
                        q_vec = embedder.embed_single(primary_query)
                        if q_vec:
                            lance_hits.extend(lancedb_engine.vector_search(q_vec, limit=20))

                fts_hits = lancedb_engine.fts_search(query_str, limit=20)
                seen_lance_ids = {h.get("id") for h in lance_hits if h.get("id")}
                for fh in fts_hits:
                    if fh.get("id") not in seen_lance_ids:
                        lance_hits.append(fh)
                        seen_lance_ids.add(fh.get("id"))

                for lh in lance_hits:
                    f_path = str(lh.get("file_path") or lh.get("source_article") or "")
                    src = "obsidian" if f_path.lower().endswith(".md") else "pdf"

                    if len(results[src]) >= limit_per_source:
                        continue

                    title = str(lh.get("title") or lh.get("source_article") or lh.get("main_topic") or "Untitled")
                    heading = str(lh.get("heading") or lh.get("subheading") or lh.get("heading_path") or title)
                    heading_path = str(lh.get("heading_path") or heading)
                    page_num = max(1, int(lh.get("page_number") or lh.get("page") or 1))
                    body_text = str(
                        lh.get("text")
                        or lh.get("child_content")
                        or lh.get("parent_content")
                        or lh.get("snippet")
                        or ""
                    )
                    art_len = int(
                        lh.get("article_length") or lh.get("doc_length") or len(body_text)
                    )

                    existing_keys = {
                        (r.get("file_path", "").lower(), r.get("page", 1))
                        for r in results[src]
                    }
                    if (f_path.lower(), page_num) in existing_keys:
                        continue

                    raw_score = lh.get("score")
                    if raw_score is None and "_distance" in lh:
                        raw_score = 1.0 / (1.0 + max(0.0, float(lh["_distance"])))
                    final_score = float(raw_score if raw_score is not None else 0.8)

                    results[src].append(
                        _format_chunk_dict(
                            doc_id=str(lh.get("id") or lh.get("doc_id") or f"lance_{src}_{page_num}"),
                            source=src,
                            file_path=f_path,
                            page=page_num,
                            title=title,
                            heading=heading,
                            heading_path=heading_path,
                            text=body_text,
                            snippet=body_text[:400],
                            doc_length=art_len,
                            score=final_score,
                            rerank_score=float(lh.get("rerank_score", final_score)),
                            parent_id=lh.get("parent_id"),
                        )
                    )
        except Exception as exc:
            logger.debug("LanceDB reference search fallback skipped: %s", exc)

    return results
