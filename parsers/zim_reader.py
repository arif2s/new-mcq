"""
parsers/zim_reader.py

LibZim C-bindings reader for offline Wikipedia and medical .zim archives.
Optimized for zero VRAM, sub-millisecond B-tree lookups, memory-mapped I/O,
and strict adherence to blueprint.md ChunkDict and asset contracts.
"""

from __future__ import annotations

import contextlib
import functools
import html
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.parse

# Enforce Invariant 1: Zero VRAM allocation on backend helpers
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from core.database import get_db_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global Configuration & Path Discovery
# ---------------------------------------------------------------------------
try:
    import config

    DATA_DIR: Path = Path(getattr(config, "DATA_DIR", "data"))
    ZIM_DIR: Path = Path(getattr(config, "ZIM_DIR", DATA_DIR / "zim"))
except ImportError:
    DATA_DIR = Path("data")
    ZIM_DIR = DATA_DIR / "zim"

try:
    import libzim

    HAS_LIBZIM = True
except ImportError:
    libzim = None
    HAS_LIBZIM = False
    logger.warning(
        "libzim package not installed. ZIM archive lookups will operate in fallback mode."
    )

_ZIM_READ_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Pre-Compiled Regex Patterns
# ---------------------------------------------------------------------------
_SCRIPT_STYLE_PATTERN = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", flags=re.IGNORECASE | re.DOTALL
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', flags=re.IGNORECASE)
_SRC_RE = re.compile(r'(src|srcset)=["\']([^"\']+)["\']', flags=re.IGNORECASE)
_STYLE_COLOR_RE = re.compile(
    r'\s*(?:bgcolor|color|text)=["\'][^"\']*["\']', flags=re.IGNORECASE
)
_INLINE_STYLE_RE = re.compile(
    r'style=["\'][^"\']*(?:color|background)[^"\']*["\']', flags=re.IGNORECASE
)
_BODY_EXTRACT_RE = re.compile(r"<body[^>]*>(.*?)</body>", flags=re.IGNORECASE | re.DOTALL)
_WIKI_FOOTER_RE = re.compile(
    r'<(?:div|section)[^>]*(?:class|id)=["\'][^"\']*(?:reflist|navbox|references|metadata)[^"\']*["\'][^>]*>.*?</(?:div|section)>',
    flags=re.IGNORECASE | re.DOTALL,
)
_WIKI_HEADER_STOP_RE = re.compile(
    r"<h[2-4][^>]*>\s*(?:References|External links|See also|Further reading|Notes)\s*</h[2-4]>",
    flags=re.IGNORECASE,
)
_CITATION_BRACKETS_RE = re.compile(r"\[[\d,\s-]+\]")
_AUDIO_PRONUNCIATION_RE = re.compile(
    r"\([^)]*\b(?:listen\vert{}audio\vert{}pronunciation\vert{}IPA)\b[^)]*\)",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Asset Tuple/Dict Polyfill Contract (Invariant 11)
# ---------------------------------------------------------------------------
class ZimAsset(tuple):
    """
    Subclasses tuple to satisfy both `tuple[bytes, str]` blueprint contract
    (e.g., `content, mimetype = get_zim_asset(...)`) and dictionary access
    (e.g., `"content" in res`, `res["content"]`, `res.get("mimetype")`, `res.zim_path`).
    """

    _DICT_KEYS = ("content", "mimetype", "zim_path")

    def __new__(cls, content: bytes, mimetype: str, zim_path: str = ""):
        return super().__new__(cls, (bytes(content), str(mimetype)))

    def __init__(self, content: bytes, mimetype: str, zim_path: str = ""):
        self._zim_path = str(zim_path or "")

    @property
    def content(self) -> bytes:
        return self[0]

    @property
    def mimetype(self) -> str:
        return self[1]

    @property
    def zim_path(self) -> str:
        return self._zim_path

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and key in self._DICT_KEYS:
            return True
        return super().__contains__(key)

    def __getitem__(self, item: Union[int, str, slice]) -> Any:
        if isinstance(item, str):
            if item == "content":
                return self[0]
            if item == "mimetype":
                return self[1]
            if item == "zim_path":
                return self._zim_path
            raise KeyError(item)
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "content":
            return self[0]
        if key == "mimetype":
            return self[1]
        if key == "zim_path":
            return self._zim_path
        return default

    def keys(self):
        return self._DICT_KEYS

    def values(self):
        return (self[0], self[1], self._zim_path)

    def items(self):
        return (
            ("content", self[0]),
            ("mimetype", self[1]),
            ("zim_path", self._zim_path),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self[0],
            "mimetype": self[1],
            "zim_path": self._zim_path,
        }


# ---------------------------------------------------------------------------
# ZIM Archive & Source Discovery Management
# ---------------------------------------------------------------------------
_ENABLED_PATHS_CACHE: Optional[List[str]] = None
_ENABLED_PATHS_TIMESTAMP: float = 0.0
_ENABLED_PATHS_TTL: float = 5.0
_PATHS_LOCK = threading.RLock()


def get_enabled_zim_paths(force_refresh: bool = False) -> List[str]:
    """
    Returns valid file paths for all enabled ZIM sources.
    Uses a 5-second TTL cache to prevent SQLite connection thrashing on hot search paths.
    Falls back to auto-discovering .zim files in config.ZIM_DIR if DB has no records.
    """
    global _ENABLED_PATHS_CACHE, _ENABLED_PATHS_TIMESTAMP

    now = time.time()
    if (
        not force_refresh
        and _ENABLED_PATHS_CACHE is not None
        and (now - _ENABLED_PATHS_TIMESTAMP) < _ENABLED_PATHS_TTL
    ):
        return list(_ENABLED_PATHS_CACHE)

    with _PATHS_LOCK:
        if (
            not force_refresh
            and _ENABLED_PATHS_CACHE is not None
            and (time.time() - _ENABLED_PATHS_TIMESTAMP) < _ENABLED_PATHS_TTL
        ):
            return list(_ENABLED_PATHS_CACHE)

        if force_refresh:
            _cached_resolve_zim_topic.cache_clear()

        paths: List[str] = []
        has_any_zim_row = False
        try:
            with contextlib.closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT path, enabled FROM knowledge_sources WHERE LOWER(type) = 'zim'"
                )
                for row in cursor.fetchall():
                    has_any_zim_row = True
                    p = row[0] if isinstance(row, tuple) else row["path"]
                    en = row[1] if isinstance(row, tuple) else row["enabled"]
                    if p and int(en or 0) == 1:
                        norm = os.path.abspath(os.path.normpath(str(p)))
                        if os.path.isfile(norm) and norm not in paths:
                            paths.append(norm)
        except Exception as exc:
            logger.debug("Could not query knowledge_sources for ZIM paths: %s", exc)

        # Fallback: Auto-discover from ZIM_DIR only if no ZIM sources are registered in DB
        if not paths and not has_any_zim_row and ZIM_DIR.exists():
            for z_file in sorted(ZIM_DIR.glob("*.zim")):
                norm = os.path.abspath(os.path.normpath(str(z_file)))
                if os.path.isfile(norm) and norm not in paths:
                    paths.append(norm)

        _ENABLED_PATHS_CACHE = paths
        _ENABLED_PATHS_TIMESTAMP = time.time()
        return list(paths)


@functools.lru_cache(maxsize=4)
def _open_archive_cached(normalized_path: str) -> Optional[Any]:
    """Internal memoized archive loader using normalized file paths."""
    if not HAS_LIBZIM or libzim is None:
        return None
    try:
        with _ZIM_READ_LOCK:
            return libzim.Archive(normalized_path)
    except Exception as exc:
        logger.error("Failed opening ZIM archive at '%s': %s", normalized_path, exc)
        return None


def get_archive(zim_path: str) -> Optional[Any]:
    """
    Loads and caches ZIM archives with normalized paths to avoid duplicate memory maps.
    LibZim memory-maps the >10GB file header/B-tree index with near-zero RAM overhead.
    """
    if not zim_path:
        return None
    norm_path = os.path.abspath(os.path.normpath(str(zim_path)))
    if not os.path.isfile(norm_path):
        return None
    return _open_archive_cached(norm_path)


def _follow_redirect(entry: Any) -> Any:
    """Follows ZIM redirect chains (e.g. 'Heart attack' -> 'Myocardial infarction')."""
    if entry is None:
        return None
    try:
        hops = 0
        while (
            entry is not None
            and getattr(entry, "is_redirect", lambda: False)()
            and hops < 5
        ):
            entry = entry.get_redirect_entry()
            hops += 1
    except Exception as exc:
        logger.debug("Error following ZIM redirect: %s", exc)
    return entry


def _find_entry_in_archive(archive: Any, query_title: str) -> Any:
    """
    Performs O(log N) B-tree lookups inside a ZIM archive across title variants,
    acronyms, comma-inverted titles, and modern/legacy namespaces.
    """
    if archive is None or not query_title or not query_title.strip():
        return None

    clean = query_title.strip()

    # Generate variant candidates safely without circular dependencies
    try:
        from agents.topic_extractor import generate_candidate_variants

        raw_candidates = generate_candidate_variants(clean)
    except Exception:
        raw_candidates = [clean]
        if "," in clean:
            parts = [p.strip() for p in clean.split(",") if p.strip()]
            if len(parts) == 2:
                raw_candidates.append(f"{parts[1]} {parts[0]}")

    search_terms: List[str] = []
    seen_terms = set()
    for term in raw_candidates:
        if not term:
            continue
        c_low = term.lower()
        if c_low not in seen_terms:
            seen_terms.add(c_low)
            search_terms.append(term)

    all_title_cands: List[str] = []
    seen_cands = set()
    for st in search_terms:
        cands = [st, st.capitalize(), st.title()]
        if len(st) <= 5:
            cands.append(st.upper())
        for c in cands:
            if c not in seen_cands:
                seen_cands.add(c)
                all_title_cands.append(c)

    with _ZIM_READ_LOCK:
        # 1. Direct title index lookup if supported
        if hasattr(archive, "get_entry_by_title"):
            for cand in all_title_cands:
                try:
                    entry = archive.get_entry_by_title(cand)
                    if entry:
                        resolved = _follow_redirect(entry)
                        if resolved:
                            return resolved
                except Exception:
                    pass

        # 2. Modern (C/), legacy (A/), and root path lookups
        for cand in all_title_cands:
            slug = cand.replace(" ", "_")
            for prefix in ("C/", "A/", ""):
                try:
                    entry = archive.get_entry_by_path(f"{prefix}{slug}")
                    if entry:
                        resolved = _follow_redirect(entry)
                        if resolved:
                            return resolved
                except Exception:
                    pass

    return None


# ---------------------------------------------------------------------------
# Topic Resolution & SQLite Cache Sync
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=2048)
def _cached_resolve_zim_topic(
    term_lower: str, enabled_paths_tuple: Tuple[str, ...]
) -> Tuple[bool, str, str, str]:
    """
    Internal immutable tuple resolver keyed on (term_lower, enabled_paths_tuple)
    so enabling/disabling ZIM files in Vault never serves stale cache entries.
    Returns: (matched, canonical_title, zim_path, entry_path)
    """
    clean_term = term_lower.strip()
    if not clean_term or len(clean_term) < 2:
        return False, clean_term, "", ""

    # 1. B-Tree lookup across enabled ZIM archives
    for zim_path in enabled_paths_tuple:
        try:
            archive = get_archive(zim_path)
            if archive is None:
                continue

            entry = _find_entry_in_archive(archive, clean_term)
            if entry:
                canonical = getattr(entry, "title", "") or clean_term
                entry_path = (
                    getattr(entry, "path", "") or f"C/{canonical.replace(' ', '_')}"
                )
                return True, str(canonical), zim_path, str(entry_path)

            # Fallback suggestion lookup
            if hasattr(libzim, "SuggestionSearcher"):
                try:
                    with _ZIM_READ_LOCK:
                        s_searcher = libzim.SuggestionSearcher(archive)
                        suggestion = s_searcher.suggest(clean_term)
                        get_res_fn = getattr(
                            suggestion,
                            "get_results",
                            getattr(suggestion, "getResults", None),
                        )
                        if get_res_fn:
                            results = get_res_fn(0, 3)
                            for r_item in results:
                                if hasattr(r_item, "path"):
                                    e = _follow_redirect(r_item)
                                elif isinstance(r_item, int) and hasattr(
                                    archive, "get_entry_by_id"
                                ):
                                    e = _follow_redirect(archive.get_entry_by_id(r_item))
                                else:
                                    e = _follow_redirect(
                                        archive.get_entry_by_path(str(r_item))
                                    )

                                if e and getattr(e, "title", "").lower() == clean_term:
                                    return True, str(e.title), zim_path, str(e.path)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("ZIM lookup error on %s: %s", zim_path, exc)

    # 2. SQLite zim_titles cache fallback
    try:
        with contextlib.closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title FROM zim_titles WHERE title = ? COLLATE NOCASE LIMIT 1",
                (clean_term,),
            )
            row = cursor.fetchone()
            if row:
                matched_title = str(row[0] if isinstance(row, tuple) else row["title"])
                z_path = enabled_paths_tuple[0] if enabled_paths_tuple else ""
                return True, matched_title, z_path, f"C/{matched_title.replace(' ', '_')}"
    except Exception:
        pass

    return False, clean_term, "", ""


def resolve_zim_topic(term: str) -> Dict[str, Any]:
    """
    Zero-bloat medical topic verifier and canonical synonym resolver.
    Returns a fresh mutable dictionary copy on each call to prevent cache poisoning.
    """
    clean_term = (term or "").strip()
    enabled_tuple = tuple(get_enabled_zim_paths())
    matched, canonical, zim_path, entry_path = _cached_resolve_zim_topic(
        clean_term.lower(), enabled_tuple
    )
    return {
        "matched": matched,
        "canonical_title": canonical if matched else clean_term,
        "zim_path": zim_path,
        "entry_path": entry_path,
    }


# ---------------------------------------------------------------------------
# Plain Text Article Extractor (RAG Context Optimized)
# ---------------------------------------------------------------------------
def get_zim_article(zim_path: str, title: str, max_chars: int = 4500) -> str:
    """
    Extracts clean clinical plain-text article content from a local .zim archive.
    Strips Wikipedia reference footers, citation brackets, phonetic guides, and navigation blocks
    to ensure LLM context tokens contain medical content rather than bibliography entries.
    """
    if not title or not title.strip():
        return ""

    candidate_paths: List[str] = []
    if zim_path and os.path.isfile(zim_path):
        candidate_paths.append(os.path.abspath(os.path.normpath(zim_path)))
    for p in get_enabled_zim_paths():
        if p not in candidate_paths:
            candidate_paths.append(p)

    if not candidate_paths:
        return ""

    max_chars = max(100, int(max_chars or 4500))

    for zp in candidate_paths:
        try:
            archive = get_archive(zp)
            if archive is None:
                continue

            entry = _find_entry_in_archive(archive, title)
            if not entry:
                continue

            with _ZIM_READ_LOCK:
                item = entry.get_item()
                raw_html = bytes(item.content).decode("utf-8", errors="ignore")

            # 1. Truncate at References / Notes section header
            header_match = _WIKI_HEADER_STOP_RE.search(raw_html)
            if header_match:
                raw_html = raw_html[: header_match.start()]

            # 2. Strip reflist, navboxes, and scripts/styles
            clean_html = _WIKI_FOOTER_RE.sub(" ", raw_html)
            no_scripts = _SCRIPT_STYLE_PATTERN.sub(" ", clean_html)

            # 3. Strip tags and unescape entities
            clean_text = _HTML_TAG_PATTERN.sub(" ", no_scripts)
            clean_text = html.unescape(clean_text)

            # 4. Strip audio pronunciation markers & citation brackets e.g. [1], [2-4]
            clean_text = _AUDIO_PRONUNCIATION_RE.sub("", clean_text)
            clean_text = _CITATION_BRACKETS_RE.sub("", clean_text)
            clean_text = _WHITESPACE_PATTERN.sub(" ", clean_text).strip()

            if clean_text:
                return clean_text[:max_chars]

        except Exception as exc:
            logger.warning("Failed extracting text from ZIM %s for '%s': %s", zp, title, exc)

    return ""


# ---------------------------------------------------------------------------
# Rich HTML Article Reader (In-App Viewport Delivery)
# ---------------------------------------------------------------------------
def get_zim_article_html(
    title_or_path: str, zim_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extracts sanitized HTML from a ZIM entry for in-app reader modal rendering.
    Decodes URL-encoded entities, rewrites internal links with blueprint-compliant
    citation attributes, and routes image requests through /api/notes/zim-asset.
    """
    if not title_or_path or not str(title_or_path).strip():
        return {"status": "error", "message": "Empty article title or path provided."}

    paths = (
        [os.path.abspath(os.path.normpath(zim_path))]
        if (zim_path and os.path.isfile(zim_path))
        else get_enabled_zim_paths()
    )
    if not paths:
        return {
            "status": "error",
            "message": "No enabled ZIM archive found in knowledge sources.",
        }

    # Clean leading path prefixes if provided
    clean_title = urllib.parse.unquote(str(title_or_path).strip().lstrip("./").lstrip("/"))
    for prefix in ("C/", "A/"):
        if clean_title.startswith(prefix):
            clean_title = clean_title[len(prefix) :]
    clean_title = clean_title.split("#")[0].replace("_", " ").strip()

    for zp in paths:
        try:
            archive = get_archive(zp)
            if archive is None:
                continue

            entry = _find_entry_in_archive(archive, clean_title)
            if not entry:
                continue

            with _ZIM_READ_LOCK:
                canonical_title = str(getattr(entry, "title", clean_title) or clean_title)
                raw_html = bytes(entry.get_item().content).decode("utf-8", errors="ignore")

            # Strip active <script> tags
            safe_html = re.sub(
                r"<script[^>]*>.*?</script>",
                "",
                raw_html,
                flags=re.IGNORECASE | re.DOTALL,
            )

            # Extract <body> if full HTML document
            body_match = _BODY_EXTRACT_RE.search(safe_html)
            if body_match:
                safe_html = body_match.group(1)

            # Normalize hardcoded styles that conflict with glassmorphic themes
            safe_html = _STYLE_COLOR_RE.sub("", safe_html)
            safe_html = _INLINE_STYLE_RE.sub("", safe_html)

            # Rewrite internal relative ZIM links with citation delegation attributes (Invariant 5 & Section 4)
            def _rewrite_href(match: re.Match) -> str:
                href = match.group(1)
                if href.startswith(
                    ("http://", "https://", "mailto:", "#", "javascript:")
                ):
                    return match.group(0)
                raw_target = urllib.parse.unquote(href.lstrip("./").lstrip("/"))
                for pfx in ("C/", "A/"):
                    if raw_target.startswith(pfx):
                        raw_target = raw_target[len(pfx) :]
                clean_target = raw_target.split("#")[0].replace("_", " ").strip()
                escaped_target = html.escape(clean_target, quote=True)
                escaped_zp = html.escape(zp, quote=True)
                return (
                    f'href="#" class="citation-link citation-zim" '
                    f'data-source-type="zim" data-article-id="{escaped_target}" '
                    f'data-title="{escaped_target}" data-file-path="{escaped_zp}" data-page="1"'
                )

            safe_html = _HREF_RE.sub(_rewrite_href, safe_html)

            # Rewrite <img> src to /api/notes/zim-asset and strip multi-source srcset
            def _rewrite_src(match: re.Match) -> str:
                attr = match.group(1).lower()
                src_val = match.group(2)
                if attr == "srcset":
                    return ""
                if src_val.startswith(("http://", "https://", "data:", "#")):
                    return match.group(0)
                cleaned_src = urllib.parse.unquote(src_val.lstrip("./").lstrip("/"))
                encoded_src = urllib.parse.quote(cleaned_src)
                encoded_zp = urllib.parse.quote(zp)
                return f'src="/api/notes/zim-asset?path={encoded_src}&zim_path={encoded_zp}"'

            safe_html = _SRC_RE.sub(_rewrite_src, safe_html)

            return {
                "status": "success",
                "title": canonical_title,
                "html": safe_html,
                "zim_path": zp,
                "file_path": zp,
                "length": len(safe_html),
            }
        except Exception as exc:
            logger.warning(
                "Error reading HTML from ZIM %s for '%s': %s", zp, clean_title, exc
            )

    return {
        "status": "error",
        "message": f"Article '{clean_title}' not found in ZIM archives.",
    }


# ---------------------------------------------------------------------------
# Binary Asset Extractor (Invariant 11 Contract Compliant)
# ---------------------------------------------------------------------------
def get_zim_asset(asset_path: str, zim_path: str = "") -> Optional[ZimAsset]:
    """
    Extracts binary asset content from a local .zim archive.
    Returns a ZimAsset polyfill supporting both `tuple[bytes, str]` unpacking
    and dictionary/attribute access (`.content`, `.mimetype`, `.zim_path`).
    """
    if not asset_path:
        return None

    paths: List[str] = []
    if zim_path and os.path.isfile(zim_path):
        paths.append(os.path.abspath(os.path.normpath(zim_path)))
    for p in get_enabled_zim_paths():
        if p not in paths:
            paths.append(p)
    if not paths:
        return None

    clean_path = urllib.parse.unquote(str(asset_path).lstrip("./").lstrip("/"))
    candidates = [clean_path]
    if not clean_path.startswith(("I/", "A/", "C/", "-/", "m/")):
        candidates.extend(
            [
                f"I/{clean_path}",
                f"I/m/{clean_path}",
                f"A/{clean_path}",
                f"-/{clean_path}",
            ]
        )

    for zp in paths:
        try:
            archive = get_archive(zp)
            if archive is None:
                continue

            for cand in candidates:
                try:
                    with _ZIM_READ_LOCK:
                        entry = archive.get_entry_by_path(cand)
                        if not entry:
                            continue
                        resolved = _follow_redirect(entry)
                        if not resolved:
                            continue
                        item = resolved.get_item()
                        content_bytes = bytes(item.content)
                        mimetype = str(getattr(item, "mimetype", "") or "image/jpeg")

                    # Fallback mime detection for generic octet streams
                    if mimetype == "application/octet-stream" or not mimetype:
                        ext = os.path.splitext(cand)[1].lower()
                        if ext in (".png", ".apng"):
                            mimetype = "image/png"
                        elif ext in (".jpg", ".jpeg"):
                            mimetype = "image/jpeg"
                        elif ext == ".svg":
                            mimetype = "image/svg+xml"
                        elif ext == ".webp":
                            mimetype = "image/webp"
                        elif ext == ".gif":
                            mimetype = "image/gif"
                        elif ext == ".css":
                            mimetype = "text/css"

                    return ZimAsset(content_bytes, mimetype, zp)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Error reading ZIM asset '%s' from %s: %s", asset_path, zp, exc)

    return None


# ---------------------------------------------------------------------------
# ChunkDict Contract-Compliant Multi-Source Search (Invariant 11)
# ---------------------------------------------------------------------------
def search_zim_articles(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """
    Searches ZIM archives and returns candidates matching the ChunkDict schema verbatim.
    Includes the mandatory 'text' and 'article_length' keys required by blueprint.md.
    """
    if not query or not query.strip():
        return []

    limit = max(1, int(limit or 3))
    hits: List[Dict[str, Any]] = []
    seen_titles = set()

    try:
        from agents.topic_extractor import get_topic_aliases

        query_aliases = get_topic_aliases(query, max_aliases=6)
    except Exception:
        query_aliases = [query]
        if "," in query:
            parts = [p.strip() for p in query.split(",") if p.strip()]
            if len(parts) == 2:
                query_aliases.append(f"{parts[1]} {parts[0]}")

    if not query_aliases:
        query_aliases = [query]

    # 1. Exact or Redirect B-Tree Match across aliases
    for alias_q in query_aliases:
        if len(hits) >= limit:
            break
        resolved = resolve_zim_topic(alias_q)
        if resolved["matched"]:
            c_title = str(resolved["canonical_title"])
            c_title_low = c_title.lower()
            if c_title_low not in seen_titles:
                body_text = get_zim_article(resolved["zim_path"], c_title, max_chars=4500)
                doc_len = len(body_text)
                if doc_len < 60:
                    continue
                seen_titles.add(c_title_low)
                base_score = round(120.0 + min(doc_len / 500.0, 20.0), 3)
                doc_id = f"zim_{c_title.replace(' ', '_')}"
                hits.append({
                    "id": doc_id,
                    "doc_id": doc_id,
                    "text": body_text,
                    "snippet": body_text[:600],
                    "title": c_title,
                    "heading": c_title,
                    "heading_path": c_title,
                    "source": "zim",
                    "file_path": str(resolved["zim_path"] or ""),
                    "page": 1,
                    "page_number": 1,
                    "doc_length": doc_len,
                    "article_length": doc_len,
                    "score": base_score,
                    "rerank_score": base_score,
                    "parent_id": None,
                })

    # 2. Suggestion search for related articles
    if len(hits) < limit and HAS_LIBZIM and libzim is not None:
        enabled_paths = get_enabled_zim_paths()
        for alias_q in query_aliases:
            if len(hits) >= limit:
                break
            for zp in enabled_paths:
                if len(hits) >= limit:
                    break
                archive = get_archive(zp)
                if archive is None or not hasattr(libzim, "SuggestionSearcher"):
                    continue
                try:
                    with _ZIM_READ_LOCK:
                        s_searcher = libzim.SuggestionSearcher(archive)
                        suggestion = s_searcher.suggest(alias_q)
                        get_res_fn = getattr(
                            suggestion,
                            "get_results",
                            getattr(suggestion, "getResults", None),
                        )
                        raw_items = list(get_res_fn(0, limit * 2)) if get_res_fn else []

                    for r_item in raw_items:
                        if len(hits) >= limit:
                            break
                        with _ZIM_READ_LOCK:
                            if hasattr(r_item, "path"):
                                entry = _follow_redirect(r_item)
                            elif isinstance(r_item, int) and hasattr(
                                archive, "get_entry_by_id"
                            ):
                                entry = _follow_redirect(archive.get_entry_by_id(r_item))
                            else:
                                entry = _follow_redirect(
                                    archive.get_entry_by_path(str(r_item))
                                )
                            t = str(getattr(entry, "title", "") or "") if entry else ""

                        if not t or t.lower() in seen_titles:
                            continue

                        body_text = get_zim_article(zp, t, max_chars=4500)
                        doc_len = len(body_text)
                        if doc_len < 100:
                            continue

                        seen_titles.add(t.lower())
                        base_score = round(85.0 + min(doc_len / 500.0, 15.0), 3)
                        doc_id = f"zim_{t.replace(' ', '_')}"
                        hits.append({
                            "id": doc_id,
                            "doc_id": doc_id,
                            "text": body_text,
                            "snippet": body_text[:600],
                            "title": t,
                            "heading": t,
                            "heading_path": t,
                            "source": "zim",
                            "file_path": zp,
                            "page": 1,
                            "page_number": 1,
                            "doc_length": doc_len,
                            "article_length": doc_len,
                            "score": base_score,
                            "rerank_score": base_score,
                            "parent_id": None,
                        })
                except Exception:
                    pass

    return hits[:limit]


# ---------------------------------------------------------------------------
# Lexical & Semantic Query Expansion
# ---------------------------------------------------------------------------
def expand_query_via_zim(query: str) -> str:
    """
    Expands 1-3 word queries with the lead definition from a local ZIM archive.
    Strips phonetic brackets and citation noise to protect downstream BM25 scores.
    """
    clean_query = (query or "").strip()
    words = clean_query.split()
    if len(words) > 3 or not words:
        return clean_query

    try:
        resolved = resolve_zim_topic(clean_query)
        canonical = resolved.get("canonical_title") or clean_query
        zim_path = resolved.get("zim_path")
        if not zim_path:
            paths = get_enabled_zim_paths()
            zim_path = paths[0] if paths else ""

        if not zim_path:
            return clean_query

        article_text = get_zim_article(zim_path, canonical, max_chars=1200)
        if not article_text:
            return canonical

        # Extract first non-trivial sentence
        sentence_match = re.search(r"^(.*?\.)(?:\s|$)", article_text)
        lead = (
            sentence_match.group(1).strip()
            if sentence_match
            else article_text[:200].strip()
        )

        # Clean audio markers, IPA pronunciations, and citation numbers
        lead = _AUDIO_PRONUNCIATION_RE.sub("", lead)
        lead = _CITATION_BRACKETS_RE.sub("", lead)
        lead = _WHITESPACE_PATTERN.sub(" ", lead).strip()

        if lead:
            if canonical.lower() != clean_query.lower():
                return f"{canonical} {lead} {clean_query}"
            return f"{lead} {clean_query}"
    except Exception as exc:
        logger.debug("Failed expanding query via ZIM: %s", exc)

    return clean_query


# ---------------------------------------------------------------------------
# Module Exports
# ---------------------------------------------------------------------------
__all__ = [
    "ZimAsset",
    "get_archive",
    "get_enabled_zim_paths",
    "resolve_zim_topic",
    "get_zim_article",
    "get_zim_article_html",
    "get_zim_asset",
    "search_zim_articles",
    "expand_query_via_zim",
]
