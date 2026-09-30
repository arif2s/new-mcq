import os
import tantivy
from config import INDEX_PATH

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
    """Executes a sub-millisecond BM25 search partitioned by source type."""
    index = get_index()
    searcher = index.reader().searcher()
    query = index.parse_query(query_str, ["title", "body"])
    top_docs = searcher.search(query, limit=50)

    results = {"obsidian": [], "pdf": [], "zim": []}
    for score, doc_address in top_docs.hits:
        doc = searcher.doc(doc_address)
        source = doc["source_type"][0]
        if len(results[source]) < limit_per_source:
            results[source].append({
                "doc_id": doc["doc_id"][0], "file_path": doc["file_path"][0],
                "page": doc["page_number"][0], "title": doc["title"][0], "score": score
            })
    return results
