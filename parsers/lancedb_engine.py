import logging
import os
import threading
from typing import Any, Dict, List, Optional

# Enforce Invariant 1: Zero VRAM for Backend
os.environ["CUDA_VISIBLE_DEVICES"] = ""

try:
    import lancedb
    import pyarrow as pa
except ImportError:
    lancedb = None
    pa = None

from config import LANCEDB_PATH
from parsers.embedding_engine import get_embedding_engine

logger = logging.getLogger("medisearch.lancedb")

DEFAULT_TABLE_NAME = "medical_knowledge"

_LANCEDB_LOCK = threading.RLock()
_ENGINE_SINGLETONS: Dict[str, "LanceDBEngine"] = {}


def _resolve_active_vector_dim(vector_dim: Optional[int] = None) -> int:
    """Resolves active embedding dimension from CPUEmbeddingEngine if vector_dim is omitted."""
    if isinstance(vector_dim, int) and vector_dim > 0:
        return vector_dim
    try:
        dim = get_embedding_engine().get_dimension()
        if isinstance(dim, int) and dim > 0:
            return dim
    except Exception as exc:
        logger.debug("Falling back to 384 vector_dim: %s", exc)
    return 384


def build_lancedb_schema(vector_dim: int = 384) -> Optional["pa.Schema"]:
    """Builds the PyArrow schema including parent-child hierarchical chunking and heading paths."""
    if pa is None:
        return None
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), int(vector_dim))),
        pa.field("source_article", pa.string()),
        pa.field("file_path", pa.string()),
        pa.field("main_topic", pa.string()),
        pa.field("subheading", pa.string()),
        pa.field("heading_path", pa.string()),
        pa.field("chunk_index", pa.int32()),
        pa.field("page_number", pa.int32()),
        pa.field("article_length", pa.int32()),
        pa.field("parent_content", pa.string()),
        pa.field("child_content", pa.string()),
    ])


class LanceDBEngine:
    """Hybrid CPU vector + FTS storage engine enforcing Invariant 8 dimension sync & ChunkDict contracts."""

    def __init__(
        self,
        db_path: str = str(LANCEDB_PATH),
        table_name: str = DEFAULT_TABLE_NAME,
        vector_dim: Optional[int] = None,
    ):
        self.db_path: str = str(db_path)
        self.table_name: str = table_name
        self.vector_dim: int = _resolve_active_vector_dim(vector_dim)
        self.db: Optional[Any] = None
        self.table: Optional[Any] = None

        os.makedirs(self.db_path, exist_ok=True)
        self._init_table()

    def _get_table_names(self) -> List[str]:
        if self.db is None:
            return []
        try:
            return list(self.db.table_names())
        except Exception:
            return []

    def _init_table(self) -> None:
        if lancedb is None or pa is None:
            logger.warning("LanceDB or PyArrow not installed; vector storage disabled.")
            return

        with _LANCEDB_LOCK:
            try:
                self.db = lancedb.connect(self.db_path)
                existing_tables = self._get_table_names()

                if self.table_name in existing_tables:
                    self.table = self.db.open_table(self.table_name)
                    try:
                        vec_field = self.table.schema.field("vector")
                        existing_dim = getattr(vec_field.type, "list_size", self.vector_dim)
                        if existing_dim and int(existing_dim) != int(self.vector_dim):
                            logger.warning(
                                "Vector dim mismatch on '%s' (table=%d, active=%d). "
                                "Auto-resetting table per Invariant 8.",
                                self.table_name,
                                int(existing_dim),
                                int(self.vector_dim),
                            )
                            self.reset_table(vector_dim=self.vector_dim)
                    except Exception as schema_exc:
                        logger.debug("Could not inspect existing LanceDB vector field: %s", schema_exc)
                else:
                    schema = build_lancedb_schema(self.vector_dim)
                    self.table = self.db.create_table(self.table_name, schema=schema)
                    logger.info(
                        "Initialized new LanceDB table '%s' (dim=%d)",
                        self.table_name,
                        self.vector_dim,
                    )
            except Exception as exc:
                logger.error("Failed to initialize LanceDB at '%s': %s", self.db_path, exc)

    def reset_table(self, vector_dim: Optional[int] = None) -> None:
        """Drops and recreates the table cleanly during vault re-indexing or model dimension changes."""
        if vector_dim is not None:
            self.vector_dim = _resolve_active_vector_dim(vector_dim)
        else:
            self.vector_dim = _resolve_active_vector_dim(self.vector_dim)

        if lancedb is None or pa is None:
            return

        with _LANCEDB_LOCK:
            try:
                if self.db is None:
                    self.db = lancedb.connect(self.db_path)

                if self.table_name in self._get_table_names():
                    self.db.drop_table(self.table_name)

                schema = build_lancedb_schema(self.vector_dim)
                self.table = self.db.create_table(self.table_name, schema=schema)
                logger.info(
                    "Reset LanceDB table '%s' with dimension %d",
                    self.table_name,
                    self.vector_dim,
                )
            except Exception as exc:
                logger.error("Failed to reset LanceDB table '%s': %s", self.table_name, exc)

    def create_fts_index(self) -> None:
        """Builds or refreshes full-text search index on child_content."""
        if self.table is None:
            return
        with _LANCEDB_LOCK:
            try:
                self.table.create_fts_index("child_content", replace=True)
                logger.info("LanceDB FTS index created on 'child_content'")
            except Exception as exc:
                logger.debug("LanceDB FTS indexing deferred or unsupported: %s", exc)

    def insert(self, records: List[Dict[str, Any]]) -> None:
        """Inserts document chunks into LanceDB, auto-syncing vector dimensions if changed."""
        if not records or self.table is None or pa is None:
            return

        with _LANCEDB_LOCK:
            first_vec = records[0].get("vector", [])
            if first_vec and len(first_vec) != self.vector_dim:
                logger.warning(
                    "Incoming record vector dimension (%d) differs from table dimension (%d). "
                    "Resetting table to %d per Invariant 8.",
                    len(first_vec),
                    self.vector_dim,
                    len(first_vec),
                )
                self.reset_table(vector_dim=len(first_vec))

            normalized = []
            for r in records:
                vec = r.get("vector", [])
                if not vec or len(vec) != self.vector_dim:
                    continue

                parent = str(r.get("parent_content", "") or "")
                child = str(r.get("child_content", "") or r.get("text", "") or "")
                sub = str(
                    r.get("subheading", "")
                    or r.get("heading", "")
                    or r.get("section_title", "")
                    or ""
                )
                page_num = max(1, int(r.get("page_number", r.get("page", 1)) or 1))
                art_len = int(
                    r.get("article_length", r.get("doc_length", len(parent) or len(child))) or 0
                )

                normalized.append({
                    "id": str(r.get("id", r.get("doc_id", ""))),
                    "vector": [float(x) for x in vec],
                    "source_article": str(r.get("source_article", r.get("title", ""))),
                    "file_path": str(r.get("file_path", r.get("source_article", ""))),
                    "main_topic": str(r.get("main_topic", "")),
                    "subheading": sub,
                    "heading_path": str(r.get("heading_path", sub)),
                    "chunk_index": int(r.get("chunk_index", 0) or 0),
                    "page_number": page_num,
                    "article_length": art_len,
                    "parent_content": parent,
                    "child_content": child,
                })

            if not normalized:
                return

            try:
                schema = build_lancedb_schema(self.vector_dim)
                pyarrow_table = pa.Table.from_pylist(normalized, schema=schema)
                self.table.add(pyarrow_table)
            except Exception as exc:
                logger.error("Failed to insert %d records into LanceDB: %s", len(normalized), exc)

    def _normalize_search_output(self, raw_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalizes raw LanceDB rows strictly to the Section 4 ChunkDict contract."""
        normalized: List[Dict[str, Any]] = []
        for row in raw_results:
            item = dict(row)
            # Remove raw float vector to keep memory & JSON payloads lightweight
            item.pop("vector", None)

            rec_id = str(item.get("id") or item.get("doc_id") or "")
            child_text = str(
                item.get("child_content")
                or item.get("text")
                or item.get("parent_content")
                or ""
            )
            subheading = str(
                item.get("subheading")
                or item.get("heading")
                or item.get("section_title")
                or ""
            )
            heading_path = str(item.get("heading_path") or subheading)
            title = str(
                item.get("source_article")
                or item.get("title")
                or item.get("main_topic")
                or ""
            )
            file_path = str(item.get("file_path") or item.get("source_article") or "")
            page_num = max(1, int(item.get("page_number") or item.get("page") or 1))
            art_len = int(
                item.get("article_length")
                or item.get("doc_length")
                or len(item.get("parent_content") or child_text)
            )

            if "score" in item and item["score"] is not None:
                score = float(item["score"])
            elif "_distance" in item and item["_distance"] is not None:
                score = 1.0 / (1.0 + max(0.0, float(item["_distance"])))
            elif "_score" in item and item["_score"] is not None:
                score = float(item["_score"])
            else:
                score = 0.0

            source_type = str(item.get("source") or "pdf")
            if file_path.lower().endswith(".md"):
                source_type = "obsidian"

            item.update({
                "id": rec_id,
                "doc_id": rec_id,
                "text": child_text,
                "snippet": child_text[:600] if len(child_text) > 600 else child_text,
                "title": title,
                "heading": subheading,
                "section_title": subheading,
                "heading_path": heading_path,
                "source": source_type,
                "file_path": file_path,
                "page": page_num,
                "page_number": page_num,
                "doc_length": art_len,
                "article_length": art_len,
                "score": score,
                "rerank_score": float(item.get("rerank_score", score)),
                "parent_id": item.get("parent_id"),
            })
            normalized.append(item)
        return normalized

    def vector_search(self, query_vector: List[float], limit: int = 25) -> List[Dict[str, Any]]:
        """Dense CPU vector nearest-neighbor search."""
        if not query_vector or self.table is None:
            return []
        if len(query_vector) != self.vector_dim:
            logger.debug(
                "Query vector dim (%d) != LanceDB table dim (%d); skipping vector search.",
                len(query_vector),
                self.vector_dim,
            )
            return []
        try:
            raw = self.table.search(query_vector).limit(int(limit)).to_list()
            return self._normalize_search_output(raw)
        except Exception as exc:
            logger.debug("LanceDB vector search skipped: %s", exc)
            return []

    def fts_search(self, query_string: str, limit: int = 25) -> List[Dict[str, Any]]:
        """Full-text lexical keyword search over child_content."""
        if not query_string or not query_string.strip() or self.table is None:
            return []
        try:
            try:
                raw = self.table.search(query_string, query_type="fts").limit(int(limit)).to_list()
            except TypeError:
                raw = self.table.search(query_string).limit(int(limit)).to_list()
            return self._normalize_search_output(raw)
        except Exception as exc:
            logger.debug("LanceDB FTS search skipped: %s", exc)
            return []

    def count_rows(self) -> int:
        """Returns row count without loading vector arrays into RAM."""
        if self.table is None:
            return 0
        try:
            if hasattr(self.table, "count_rows"):
                return int(self.table.count_rows())
            return len(self.table.search().select(["id"]).to_arrow())
        except Exception:
            return 0


def get_lancedb_engine(
    db_path: str = str(LANCEDB_PATH),
    table_name: str = DEFAULT_TABLE_NAME,
    vector_dim: Optional[int] = None,
) -> LanceDBEngine:
    """Returns a cached LanceDBEngine singleton (Invariant 8) synchronized with the active embedding dimension."""
    with _LANCEDB_LOCK:
        target_dim = _resolve_active_vector_dim(vector_dim)
        cache_key = f"{db_path}::{table_name}"
        engine = _ENGINE_SINGLETONS.get(cache_key)
        if engine is None:
            engine = LanceDBEngine(db_path=db_path, table_name=table_name, vector_dim=target_dim)
            _ENGINE_SINGLETONS[cache_key] = engine
        elif engine.vector_dim != target_dim:
            engine.reset_table(vector_dim=target_dim)
        return engine
