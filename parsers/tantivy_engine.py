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
        builder.add_text_field("body", stored=False, tokenizer_name="en_stem")
        _schema = builder.build()
    return _schema

def get_index():
    global _index
    if _index is None:
        os.makedirs(INDEX_PATH, exist_ok=True)
        _index = tantivy.Index(get_schema(), path=str(INDEX_PATH))
    return _index

def search_index(query_str: str, limit_per_source: int = 3) -> dict:
    index = get_index()

    # REMOVED: index.reload()
    # Calling reload() on every search forces an NTFS directory stat check on Windows.
    # At >100k files, this stalls the event loop. Tantivy's searcher is immutable and
    # thread-safe; reloading should only be done via a dedicated background indexing task.
    searcher = index.searcher()

    try:
        query = index.parse_query(query_str, ["title", "body"])
    except ValueError:
        # Strip punctuation and collapse whitespace for a safer fallback query
        safe_query = re.sub(r'[^\w\s]', ' ', query_str)
        safe_query = re.sub(r'\s+', ' ', safe_query).strip()

        if not safe_query:
            return {"obsidian": [], "pdf": [], "zim": []}

        try:
            query = index.parse_query(safe_query, ["title", "body"])
        except ValueError as e:
            logger.error(f"Failed to parse fallback query '{safe_query}': {e}")
            return {"obsidian": [], "pdf": [], "zim": []}

    top_docs = searcher.search(query, limit=50)

    # Pre-allocate expected keys to guarantee strict downstream dictionary shapes
    results = {"obsidian": [], "pdf": [], "zim": []}

    for score, doc_address in top_docs.hits:
        doc = searcher.doc(doc_address)

        # Safe extraction: Tantivy returns stored fields as lists.
        # Using .get() prevents IndexError if a field is missing (e.g. Obsidian notes lack page_numbers)
        source_list = doc.get("source_type")
        if not source_list:
            continue

        source = source_list[0]

        if source not in results:
            results[source] = []

        if len(results[source]) < limit_per_source:
            file_path_list = doc.get("file_path")
            doc_id_list = doc.get("doc_id")
            title_list = doc.get("title")
            page_list = doc.get("page_number")

            results[source].append({
                "doc_id": doc_id_list[0] if doc_id_list else "",
                "file_path": file_path_list[0] if file_path_list else "",
                "page": page_list[0] if page_list else 0,
                "title": title_list[0] if title_list else "Untitled",
                "score": score
            })

            # Early exit optimization: Stop iterating Hits once all our requested buckets are full
            if all(len(v) >= limit_per_source for k, v in results.items() if k in ("obsidian", "pdf", "zim")):
                break

    return results
