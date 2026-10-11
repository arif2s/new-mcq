import os
import re
import logging
import tantivy

from config import INDEX_PATH

logger = logging.getLogger(__name__)

_schema = None
_index = None

def get_schema():
    global _schema
    if _schema is None:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("doc_id", stored=True)
        builder.add_text_field("source_type", stored=True)
        builder.add_text_field("file_path", stored=True)
        builder.add_integer_field("page_number", stored=True)
        builder.add_text_field("title", stored=True)
        builder.add_text_field("document_body", stored=False, tokenizer_name="en_stem")
        builder.add_text_field("obsidian_body", stored=True, tokenizer_name="en_stem")
        _schema = builder.build()
    return _schema

def get_index():
    global _index
    if _index is None:
        os.makedirs(INDEX_PATH, exist_ok=True)
        _index = tantivy.Index(get_schema(), path=str(INDEX_PATH))
    return _index

import math
from parsers.zim_reader import expand_query_via_zim

def search_index(query_str: str, limit_per_source: int = 3) -> dict:
    # 1. Expand query using offline Wikipedia (ZIM)
    expanded_query_str = expand_query_via_zim(query_str)

    index = get_index()

    # REMOVED: index.reload()
    # Calling reload() on every search forces an NTFS directory stat check on Windows.
    # At >100k files, this stalls the event loop. Tantivy's searcher is immutable and
    # thread-safe; reloading should only be done via a dedicated background indexing task.
    searcher = index.searcher()

    # Strip punctuation and collapse whitespace for a safer base query
    safe_query = re.sub(r'[^\w\s]', ' ', expanded_query_str)
    safe_query = re.sub(r'\s+', ' ', safe_query).strip()

    if not safe_query:
        return {"obsidian": [], "pdf": [], "zim": []}

    word_count = len(safe_query.split())

    try:
        if word_count <= 2:
            # For 1-2 word topics, strongly boost title and exact phrase matches in body
            # to prioritize pages actually about the topic versus mere mentions
            boosted_query_str = f'title:"{safe_query}"^5 OR document_body:"{safe_query}"^2 OR obsidian_body:"{safe_query}"^2 OR {safe_query}'
            query = index.parse_query(boosted_query_str, ["title", "document_body", "obsidian_body"])
        else:
            query = index.parse_query(query_str, ["title", "document_body", "obsidian_body"])
    except ValueError:
        try:
            query = index.parse_query(safe_query, ["title", "document_body", "obsidian_body"])
        except ValueError as e:
            logger.error(f"Failed to parse fallback query '{safe_query}': {e}")
            return {"obsidian": [], "pdf": [], "zim": []}

    # Stage 1: Lexical Retrieval
    top_docs = searcher.search(query, limit=50)

    candidates = []

    # Calculate terms for term counting
    query_terms = set(query_str.lower().split())

    for bm25_score, doc_address in top_docs.hits:
        doc = searcher.doc(doc_address)

        source_list = doc.get("source_type", [])
        if not source_list:
            continue

        source = source_list[0]

        # Determine the chunk text to compute length and term matches
        if source == "obsidian" or source == "zim":
            body_field = doc.get("obsidian_body", [""])
            body_text = body_field[0] if body_field else ""
        else:
            # For PDFs, document_body is stored=False by schema rules.
            # We can't access it here, so chunk length relies on title or we default to a safe 0
            # to avoid reading from a hallucinated or non-existent stored field.
            body_text = ""

        chunk_length = len(body_text)

        # Count matching terms
        term_count = sum(1 for term in query_terms if term in body_text.lower() or term in (doc.get("title", [""])[0] or "").lower())

        # composite score: Score = (BM25 * 1.0) + log10(chunk_length + 1) * 0.5 + (term_count * 1.5)
        composite_score = bm25_score * 1.0 + math.log10(chunk_length + 1) * 0.5 + term_count * 1.5

        candidates.append({
            "doc": doc,
            "score": composite_score,
            "source": source
        })

    # Sort by composite score
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Stage 2: Zero-VRAM Semantic Reranking
    top_20 = candidates[:20]

    if not top_20:
        return {"obsidian": [], "pdf": [], "zim": []}

    try:
        from flashrank import Ranker, RerankRequest
        # Initialize Ranker (runs on ONNX CPU by default)
        # Using a fixed local cache directory for offline capabilities
        ranker = Ranker(model_name="ms-marco-TinyBERT-L-2-v2", cache_dir="./data/models/flashrank")

        passages = []
        for i, candidate in enumerate(top_20):
            doc = candidate["doc"]
            source = candidate["source"]

            # Use appropriate body text, defaulting to title
            if source == "obsidian" or source == "zim":
                text = doc.get("obsidian_body", [""])[0] or doc.get("title", [""])[0] or " "
            else:
                text = doc.get("title", [""])[0] or " "

            passages.append({
                "id": i,
                "text": text
            })

        request = RerankRequest(query=query_str, passages=passages)
        reranked_results = ranker.rerank(request)

        # Map back to candidates
        reranked_candidates = []
        for res in reranked_results:
            idx = res["id"]
            cand = top_20[idx]
            cand["score"] = float(res["score"]) # Update with semantic score
            reranked_candidates.append(cand)

        # Re-sort candidates based on the semantic score
        reranked_candidates.sort(key=lambda x: x["score"], reverse=True)
    except Exception as e:
        logger.error(f"FlashRank reranking failed: {e}. Falling back to composite scores.")
        reranked_candidates = top_20

    # Pre-allocate expected keys to guarantee strict downstream dictionary shapes
    results = {"obsidian": [], "pdf": [], "zim": []}

    for candidate in reranked_candidates:
        doc = candidate["doc"]
        source = candidate["source"]

        if source not in results:
            results[source] = []

        if len(results[source]) < limit_per_source:
            file_path_list = doc.get("file_path", [])
            doc_id_list = doc.get("doc_id", [])
            title_list = doc.get("title", [])
            page_list = doc.get("page_number", [])

            results[source].append({
                "doc_id": doc_id_list[0] if doc_id_list else "",
                "file_path": file_path_list[0] if file_path_list else "",
                "page": page_list[0] if page_list else 0,
                "title": title_list[0] if title_list else "Untitled",
                "score": candidate["score"]
            })

            if all(len(v) >= limit_per_source for k, v in results.items() if k in ("obsidian", "pdf", "zim")):
                break

    return results
