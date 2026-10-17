import re
import os
import html
import logging
import functools
import urllib.parse
from typing import Optional, Dict, Any, List
from contextlib import closing

from core.database import get_db_connection

logger = logging.getLogger(__name__)

_SCRIPT_STYLE_PATTERN = re.compile(r"<(script|style)[^>]*>.*?</\1>", flags=re.IGNORECASE | re.DOTALL)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', flags=re.IGNORECASE)


@functools.lru_cache(maxsize=4)
def get_archive(zim_path: str):
    """
    Loads and caches ZIM archives in memory.
    LibZim memory-maps the >10GB file header/B-tree index with near-zero RAM overhead.
    """
    import libzim
    return libzim.Archive(zim_path)


def get_enabled_zim_paths() -> List[str]:
    """Returns a list of file paths for all enabled ZIM sources in knowledge_sources."""
    paths = []
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM knowledge_sources WHERE type = 'zim' AND enabled = 1")
            for row in cursor.fetchall():
                p = row[0] if isinstance(row, tuple) else row["path"]
                if p and os.path.exists(p):
                    paths.append(p)
    except Exception as e:
        logger.debug(f"Could not fetch enabled ZIM paths: {e}")
    return paths


def _follow_redirect(entry):
    """Follows ZIM redirect chains (e.g. 'Heart attack' -> 'Myocardial infarction')."""
    try:
        hops = 0
        while entry is not None and entry.is_redirect() and hops < 5:
            entry = entry.get_redirect_entry()
            hops += 1
    except Exception:
        pass
    return entry


def _find_entry_in_archive(archive, query_title: str):
    """
    Attempts O(log N) B-tree lookups inside a ZIM archive across title variants
    and candidates (including inverted forms like 'renal transplantation' for 'transplantation, renal').
    """
    if not query_title or not query_title.strip():
        return None

    clean = query_title.strip()
    try:
        from agents.topic_extractor import generate_candidate_variants
        raw_candidates = generate_candidate_variants(clean)
    except Exception:
        raw_candidates = [clean]
        if "," in clean:
            parts = [p.strip() for p in clean.split(",") if p.strip()]
            if len(parts) == 2:
                raw_candidates.append(f"{parts[1]} {parts[0]}")

    search_terms = []
    seen_terms = set()
    for term in raw_candidates:
        if not term:
            continue
        c_low = term.lower()
        if c_low not in seen_terms:
            seen_terms.add(c_low)
            search_terms.append(term)

    all_title_cands = []
    seen_cands = set()
    for st in search_terms:
        cands = [st, st.capitalize(), st.title(), st.upper() if len(st) <= 5 else st]
        for c in cands:
            if c not in seen_cands:
                seen_cands.add(c)
                all_title_cands.append(c)

    # 1. Try get_entry_by_title if supported by libzim version
    if hasattr(archive, "get_entry_by_title"):
        for cand in all_title_cands:
            try:
                entry = archive.get_entry_by_title(cand)
                if entry:
                    return _follow_redirect(entry)
            except Exception:
                pass

    # 2. Try direct path lookup in modern (C/) and legacy (A/) ZIM namespaces
    for cand in all_title_cands:
        slug = cand.replace(" ", "_")
        for prefix in ("C/", "A/", ""):
            try:
                entry = archive.get_entry_by_path(f"{prefix}{slug}")
                if entry:
                    return _follow_redirect(entry)
            except Exception:
                pass

    return None


@functools.lru_cache(maxsize=1024)
def resolve_zim_topic(term: str) -> Dict[str, Any]:
    """
    Zero-bloat medical topic verifier and canonical synonym resolver using ZIM internal B-trees
    and/or the SQLite zim_titles table.
    Maps variants like 'Heart attack' -> 'Myocardial infarction' via ZIM redirects.
    """
    clean_term = (term or "").strip()
    if not clean_term or len(clean_term) < 2:
        return {"matched": False, "canonical_title": clean_term, "zim_path": "", "entry_path": ""}

    # 1. Direct C++ B-tree lookup across enabled >10GB ZIM files
    for zim_path in get_enabled_zim_paths():
        try:
            archive = get_archive(zim_path)
            entry = _find_entry_in_archive(archive, clean_term)
            if entry:
                canonical = getattr(entry, "title", "") or clean_term
                entry_path = getattr(entry, "path", "") or f"C/{canonical.replace(' ', '_')}"
                return {
                    "matched": True,
                    "canonical_title": canonical,
                    "zim_path": zim_path,
                    "entry_path": entry_path,
                }

            # Optional prefix/suggestion fallback if exact path missed due to casing
            try:
                import libzim
                if hasattr(libzim, "SuggestionSearcher"):
                    s_searcher = libzim.SuggestionSearcher(archive)
                    suggestion = s_searcher.suggest(clean_term)
                    results = suggestion.getResults(0, 3)
                    for r_path in results:
                        e = archive.get_entry_by_path(r_path)
                        e = _follow_redirect(e)
                        if e and getattr(e, "title", "").lower() == clean_term.lower():
                            return {
                                "matched": True,
                                "canonical_title": e.title,
                                "zim_path": zim_path,
                                "entry_path": e.path,
                            }
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"ZIM lookup error on {zim_path}: {e}")

    # 2. Fallback check against SQLite zim_titles table if populated
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title FROM zim_titles WHERE title = ? COLLATE NOCASE LIMIT 1",
                (clean_term,)
            )
            row = cursor.fetchone()
            if row:
                matched_title = row[0] if isinstance(row, tuple) else row["title"]
                zim_paths = get_enabled_zim_paths()
                return {
                    "matched": True,
                    "canonical_title": matched_title,
                    "zim_path": zim_paths[0] if zim_paths else "",
                    "entry_path": f"C/{matched_title.replace(' ', '_')}",
                }
    except Exception:
        pass

    return {"matched": False, "canonical_title": clean_term, "zim_path": "", "entry_path": ""}


def get_zim_article(zim_path: str, title: str, max_chars: int = 4500) -> str:
    """
    Extracts plain-text article content from a local .zim archive for RAG synthesis.
    Follows redirects automatically and prioritizes comprehensive body text.
    """
    try:
        if not zim_path or not os.path.exists(zim_path):
            paths = get_enabled_zim_paths()
            if not paths:
                return ""
            zim_path = paths[0]

        archive = get_archive(zim_path)
        entry = _find_entry_in_archive(archive, title)
        if not entry:
            return ""

        raw_html = bytes(entry.get_item().content).decode("utf-8", errors="ignore")
        no_scripts = _SCRIPT_STYLE_PATTERN.sub(" ", raw_html)
        clean_text = _HTML_TAG_PATTERN.sub(" ", no_scripts)
        clean_text = html.unescape(clean_text)
        clean_text = _WHITESPACE_PATTERN.sub(" ", clean_text).strip()

        return clean_text[:max_chars]

    except Exception as e:
        logger.warning(f"Failed to extract text from ZIM {zim_path} for '{title}': {e}")
        return ""


def get_zim_article_html(title_or_path: str, zim_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Extracts rich HTML from a ZIM entry for in-browser rendering.
    Follows redirects and rewrites internal relative links so clicking a link inside
    the ZIM viewer loads the target ZIM article inside the browser modal.
    """
    paths = [zim_path] if (zim_path and os.path.exists(zim_path)) else get_enabled_zim_paths()
    if not paths:
        return {"status": "error", "message": "No enabled ZIM archive found in RAG Vault."}

    for zp in paths:
        try:
            archive = get_archive(zp)
            entry = _find_entry_in_archive(archive, title_or_path)
            if not entry:
                continue

            canonical_title = getattr(entry, "title", title_or_path)
            raw_html = bytes(entry.get_item().content).decode("utf-8", errors="ignore")

            # Strip active <script> tags while keeping layout/tables/headings intact
            safe_html = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.IGNORECASE | re.DOTALL)

            # Extract <body> content if present
            body_match = re.search(r"<body[^>]*>(.*?)</body>", safe_html, flags=re.IGNORECASE | re.DOTALL)
            if body_match:
                safe_html = body_match.group(1)

            # Remove hardcoded inline text colors and background attributes that break table readability
            safe_html = re.sub(r'\s*(bgcolor|color|text)=["\'][^"\']*["\']', '', safe_html, flags=re.IGNORECASE)
            safe_html = re.sub(r'style=["\'][^"\']*(?:color|background)[^"\']*["\']', '', safe_html, flags=re.IGNORECASE)

            # Rewrite relative internal ZIM links into clickable data-article-id attributes
            def _rewrite_href(match):
                href = match.group(1)
                if href.startswith(("http://", "https://", "mailto:", "#")):
                    return match.group(0)
                cleaned_target = href.lstrip("./").lstrip("/")
                for prefix in ("C/", "A/"):
                    if cleaned_target.startswith(prefix):
                        cleaned_target = cleaned_target[len(prefix):]
                cleaned_target = cleaned_target.split("#")[0].replace("_", " ")
                return f'href="#" class="citation-zim" data-article-id="{html.escape(cleaned_target)}"'

            safe_html = _HREF_RE.sub(_rewrite_href, safe_html)

            # Rewrite img/source src attributes to point to local /api/notes/zim-asset endpoint
            _SRC_RE = re.compile(r'(src|srcset)=["\']([^"\']+)["\']', flags=re.IGNORECASE)
            def _rewrite_src(match):
                attr = match.group(1)
                src = match.group(2)
                if src.startswith(("http://", "https://", "data:", "#")):
                    return match.group(0)
                cleaned_src = src.lstrip("./").lstrip("/")
                encoded_src = urllib.parse.quote(cleaned_src)
                encoded_zp = urllib.parse.quote(zp)
                url = f"/api/notes/zim-asset?path={encoded_src}&zim_path={encoded_zp}"
                return f'{attr}="{url}"'

            safe_html = _SRC_RE.sub(_rewrite_src, safe_html)

            return {
                "status": "success",
                "title": canonical_title,
                "html": safe_html,
                "zim_path": zp,
                "length": len(safe_html),
            }
        except Exception as e:
            logger.warning(f"Error reading HTML from ZIM {zp} for '{title_or_path}': {e}")

    return {"status": "error", "message": f"Article '{title_or_path}' not found in ZIM archive."}


def get_zim_asset(asset_path: str, zim_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Extracts binary asset content (images, graphics) from a local .zim archive.
    """
    paths = [zim_path] if (zim_path and os.path.exists(zim_path)) else get_enabled_zim_paths()
    if not paths:
        return None

    clean_path = asset_path.lstrip("./").lstrip("/")
    candidates = [clean_path]
    if not clean_path.startswith(("I/", "A/", "C/", "-/", "m/")):
        candidates.extend([f"I/{clean_path}", f"I/m/{clean_path}", f"A/{clean_path}", f"-/{clean_path}"])

    for zp in paths:
        try:
            archive = get_archive(zp)
            for cand in candidates:
                try:
                    entry = archive.get_entry_by_path(cand)
                    if not entry:
                        continue
                    entry = _follow_redirect(entry)
                    if not entry:
                        continue
                    item = entry.get_item()
                    content_bytes = bytes(item.content)
                    mimetype = getattr(item, "mimetype", "") or "image/jpeg"
                    if mimetype == "application/octet-stream":
                        ext = os.path.splitext(cand)[1].lower()
                        if ext in (".png", ".apng"):
                            mimetype = "image/png"
                        elif ext in (".jpg", ".jpeg"):
                            mimetype = "image/jpeg"
                        elif ext == ".svg":
                            mimetype = "image/svg+xml"
                        elif ext == ".webp":
                            mimetype = "image/webp"
                    return {
                        "content": content_bytes,
                        "mimetype": mimetype,
                        "zim_path": zp,
                    }
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Error reading ZIM asset {asset_path} from {zp}: {e}")

    return None


def search_zim_articles(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """
    Fast direct search against >10GB ZIM archives using exact/redirect resolution
    plus LibZim fulltext/suggestion search across all topic aliases and synonyms.
    Returns depth-weighted results including article character length.
    """
    hits: List[Dict[str, Any]] = []
    seen_titles = set()

    try:
        from agents.topic_extractor import get_topic_aliases
        query_aliases = get_topic_aliases(query)
    except Exception:
        query_aliases = [query]
        if "," in query:
            parts = [p.strip() for p in query.split(",") if p.strip()]
            if len(parts) == 2:
                query_aliases.append(f"{parts[1]} {parts[0]}")

    # 1. Exact or Redirect Match across topic aliases
    for alias_q in query_aliases:
        if len(hits) >= limit:
            break
        resolved = resolve_zim_topic(alias_q)
        if resolved["matched"]:
            c_title = resolved["canonical_title"]
            if c_title.lower() not in seen_titles:
                body_text = get_zim_article(resolved["zim_path"], c_title, max_chars=4500)
                doc_len = len(body_text)
                seen_titles.add(c_title.lower())
                hits.append({
                    "doc_id": f"zim_{c_title}",
                    "file_path": resolved["zim_path"],
                    "article_id": c_title,
                    "page": 1,
                    "title": c_title,
                    "heading": c_title,
                    "snippet": body_text[:600],
                    "doc_length": doc_len,
                    "score": 120.0 + min(doc_len / 500.0, 20.0),
                })

    # 2. Suggestion / Prefix search for related comprehensive articles across aliases
    if len(hits) < limit:
        for alias_q in query_aliases:
            if len(hits) >= limit:
                break
            for zp in get_enabled_zim_paths():
                if len(hits) >= limit:
                    break
                try:
                    import libzim
                    archive = get_archive(zp)
                    if hasattr(libzim, "SuggestionSearcher"):
                        s_searcher = libzim.SuggestionSearcher(archive)
                        suggestion = s_searcher.suggest(alias_q)
                        for r_path in suggestion.getResults(0, limit * 2):
                            if len(hits) >= limit:
                                break
                            entry = _follow_redirect(archive.get_entry_by_path(r_path))
                            if not entry:
                                continue
                            t = getattr(entry, "title", "")
                            if not t or t.lower() in seen_titles:
                                continue
                            seen_titles.add(t.lower())
                            body_text = get_zim_article(zp, t, max_chars=4500)
                            doc_len = len(body_text)
                            if doc_len < 80:
                                continue
                            hits.append({
                                "doc_id": f"zim_{t}",
                                "file_path": zp,
                                "article_id": t,
                                "page": 1,
                                "title": t,
                                "heading": t,
                                "snippet": body_text[:600],
                                "doc_length": doc_len,
                                "score": 85.0 + min(doc_len / 500.0, 15.0),
                            })
                except Exception:
                    pass

    return hits[:limit]


def expand_query_via_zim(query: str) -> str:
    """
    Expands 1-2 word queries using the lead definition from a local ZIM archive,
    and resolves synonyms via ZIM redirect target titles.
    """
    words = (query or "").strip().split()
    if len(words) > 3 or not words:
        return query

    try:
        resolved = resolve_zim_topic(query)
        canonical = resolved.get("canonical_title") or query
        zim_path = resolved.get("zim_path")
        if not zim_path:
            paths = get_enabled_zim_paths()
            zim_path = paths[0] if paths else ""

        if not zim_path:
            return query

        article_text = get_zim_article(zim_path, canonical, max_chars=1000)
        if not article_text:
            return canonical

        match = re.search(r"^(.*?\.)(?:\s|$)", article_text)
        lead = match.group(1).strip() if match else article_text[:250].strip()

        if lead:
            if canonical.lower() != query.lower():
                return f"{canonical} {lead} {query}"
            return f"{lead} {query}"
    except Exception as e:
        logger.debug(f"Failed to expand query via ZIM: {e}")

    return query
