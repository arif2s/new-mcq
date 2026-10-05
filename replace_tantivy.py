import re

with open('parsers/tantivy_engine.py', 'r') as f:
    content = f.read()

# Replace the search_index logic to use boosted phrase queries
old_search = """
    try:
        query = index.parse_query(query_str, ["title", "body"])
    except ValueError:
        # Strip punctuation and collapse whitespace for a safer fallback query
        safe_query = re.sub(r'[^\\w\\s]', ' ', query_str)
        safe_query = re.sub(r'\\s+', ' ', safe_query).strip()

        if not safe_query:
            return {"obsidian": [], "pdf": [], "zim": []}

        try:
            query = index.parse_query(safe_query, ["title", "body"])
        except ValueError as e:
            logger.error(f"Failed to parse fallback query '{safe_query}': {e}")
            return {"obsidian": [], "pdf": [], "zim": []}
"""

new_search = """
    # Strip punctuation and collapse whitespace for a safer base query
    safe_query = re.sub(r'[^\\w\\s]', ' ', query_str)
    safe_query = re.sub(r'\\s+', ' ', safe_query).strip()

    if not safe_query:
        return {"obsidian": [], "pdf": [], "zim": []}

    word_count = len(safe_query.split())

    try:
        if word_count <= 2:
            # For 1-2 word topics, strongly boost title and exact phrase matches in body
            # to prioritize pages actually about the topic versus mere mentions
            boosted_query_str = f'title:"{safe_query}"^5 OR body:"{safe_query}"^2 OR {safe_query}'
            query = index.parse_query(boosted_query_str, ["title", "body"])
        else:
            query = index.parse_query(query_str, ["title", "body"])
    except ValueError:
        try:
            query = index.parse_query(safe_query, ["title", "body"])
        except ValueError as e:
            logger.error(f"Failed to parse fallback query '{safe_query}': {e}")
            return {"obsidian": [], "pdf": [], "zim": []}
"""
content = content.replace(old_search, new_search)

with open('parsers/tantivy_engine.py', 'w') as f:
    f.write(content)
