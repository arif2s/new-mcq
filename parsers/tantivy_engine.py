import os
import re
import math
import shutil
import logging
from typing import Dict, Any, List

try:
    import tantivy
except ImportError:
    tantivy = None

from config import INDEX_PATH
from parsers.zim_reader import expand_query_via_zim, search_zim_articles
from parsers.reranker import rerank_chunks

logger = logging.getLogger(__name__)

_schema = None
_index = None


def get_schema():
    global _schema
    if tantivy is None:
        return None
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


def get_index(reset: bool = False):
    global _index
    if tantivy is None:
        return None
    if reset:
        _index = None
        if os.path.exists(INDEX_PATH):
            shutil.rmtree(INDEX_PATH, ignore_errors=True)
    if _index is None:
        os.makedirs(INDEX_PATH, exist_ok=True)
        try:
            _index = tantivy.Index(get_schema(), path=str(INDEX_PATH))
        except Exception as e:
            logger.warning(f"Recreating Tantivy index due to schema upgrade ({e})...")
            shutil.rmtree(INDEX_PATH, ignore_errors=True)
            os.makedirs(INDEX_PATH, exist_ok=True)
            _index = tantivy.Index(get_schema(), path=str(INDEX_PATH))
    return _index


def _compute_depth_score(
    bm25_score: float,
    query_str: str,
    query_terms: set,
    title: str,
    heading_path: str,
    body_text: str,
    doc_length: int,
) -> tuple[float, float]:
    """
    Computes a composite score that ensures retrieved articles discuss the topic in detail
    (not just a passing mention) and favors longer, comprehensive articles:
    1. Exact/phrase match in Title or Section Heading Path (+15 to +25)
    2. Topic Mention Frequency & Density (multiple occurrences of topic terms in the text)
    3. Logarithmic Article Length bonus (log10(doc_length + 1) * 3.0)
    """
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

    # 2. Topic Mention Count (Depth of Coverage vs. Single Passing Mention)
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
    effective_length = max(doc_length, len(body_text))
    length_boost = math.log10(effective_length + 10) * 3.0

    depth_bonus = heading_boost + coverage_boost + length_boost
    total_score = (bm25_score * 1.2) + depth_bonus
    return total_score, depth_bonus


def search_index(query_str: str, limit_per_source: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """
    Executes a depth-weighted hybrid lexical + semantic reranked search across
    Obsidian notes, PDF textbooks, and >10GB ZIM archives using topic alias expansion.
    """
    results: Dict[str, List[Dict[str, Any]]] = {"obsidian": [], "pdf": [], "zim": []}
    if not query_str or not query_str.strip():
        return results

    try:
        from agents.topic_extractor import get_topic_aliases
        topic_aliases = get_topic_aliases(query_str)
    except Exception:
        topic_aliases = [query_str]
        if "," in query_str:
            parts = [p.strip() for p in query_str.split(",") if p.strip()]
            if len(parts) == 2:
                topic_aliases.append(f"{parts[1]} {parts[0]}")

    primary_query = topic_aliases[0] if topic_aliases else query_str
    expanded_query_str = expand_query_via_zim(primary_query)

    safe_query = re.sub(r"[^\w\s]", " ", expanded_query_str)
    safe_query = re.sub(r"\s+", " ", safe_query).strip()

    if not safe_query:
        return results

    try:
        index = get_index()
        if index is not None:
            index.reload()
            searcher = index.searcher()
            search_fields = ["title", "heading_path", "snippet", "document_body", "obsidian_body"]

            # Build boosted multi-alias Tantivy query
            alias_clauses = []
            for idx, alias_item in enumerate(topic_aliases[:5]):
                c_alias = re.sub(r"[^\w\s]", " ", alias_item).strip()
                if not c_alias:
                    continue
                w = max(6 - idx * 2, 2)
                alias_clauses.append(
                    f'title:"{c_alias}"^{w} OR heading_path:"{c_alias}"^{max(w-1, 1)} '
                    f'OR document_body:"{c_alias}"^2 OR obsidian_body:"{c_alias}"^2'
                )

            if alias_clauses:
                boosted = f"({' OR '.join(alias_clauses)}) OR {safe_query}"
            else:
                boosted = safe_query

            try:
                query = index.parse_query(boosted, search_fields)
            except Exception:
                query = index.parse_query(safe_query, ["title", "document_body", "obsidian_body"])

            top_docs = searcher.search(query, limit=60)
            candidates = []

            # Gather all terms across aliases for depth scoring
            all_query_terms = set()
            for a_item in topic_aliases[:5]:
                for t in re.sub(r"[^\w\s]", " ", a_item).lower().split():
                    if len(t) >= 2:
                        all_query_terms.add(t)

            for bm25_score, doc_address in top_docs.hits:
                doc = searcher.doc(doc_address)
                source_list = doc.get("source_type", [])
                if not source_list:
                    continue

                source = source_list[0]
                if source not in results:
                    continue

                title = (doc.get("title", ["Untitled"]) or ["Untitled"])[0]
                heading_path = (doc.get("heading_path", [title]) or [title])[0]
                file_path = (doc.get("file_path", [""]) or [""])[0]
                doc_id = (doc.get("doc_id", [""]) or [""])[0]
                page_num = (doc.get("page_number", [1]) or [1])[0]

                body_text = (
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

                candidates.append({
                    "doc_id": doc_id,
                    "source": source,
                    "file_path": file_path,
                    "page": page_num,
                    "title": title,
                    "heading_path": heading_path,
                    "snippet": body_text[:800],
                    "doc_length": doc_length,
                    "score": comp_score,
                    "depth_bonus": depth_bonus,
                })

            candidates.sort(key=lambda x: x["score"], reverse=True)

            # Stage 2: Cross-Encoder Reranking
            top_candidates = candidates[:24]
            reranked = rerank_chunks(query_str, top_candidates, top_k=24) if top_candidates else []

            for cand in reranked:
                src = cand["source"]
                if len(results[src]) < limit_per_source:
                    results[src].append({
                        "doc_id": cand["doc_id"],
                        "file_path": cand["file_path"],
                        "page": cand["page"],
                        "title": cand["title"],
                        "heading": cand.get("heading_path", cand["title"]),
                        "snippet": cand.get("snippet", "")[:400],
                        "doc_length": cand.get("doc_length", 0),
                        "score": round(float(cand.get("rerank_score", cand["score"])), 4),
                    })
    except Exception as e:
        logger.warning(f"Tantivy search error for '{query_str}': {e}")

    # Stage 3: Zero-Copy >10GB ZIM Lookup & Synonym Redirect Bypass
    if len(results["zim"]) < limit_per_source:
        try:
            zim_hits = search_zim_articles(query_str, limit=limit_per_source - len(results["zim"]))
            existing_titles = {r["title"].lower() for r in results["zim"]}
            for zh in zim_hits:
                if zh["title"].lower() not in existing_titles:
                    results["zim"].append(zh)
                    existing_titles.add(zh["title"].lower())
        except Exception as e:
            logger.debug(f"ZIM direct search bypass skipped: {e}")

    return results
