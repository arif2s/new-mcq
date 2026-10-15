import os
import logging
from typing import List, Dict, Any

try:
    import lancedb
    import pyarrow as pa
except ImportError:
    lancedb = None
    pa = None

from config import LANCEDB_PATH

logger = logging.getLogger(__name__)


def build_lancedb_schema(vector_dim: int = 384):
    """Builds the PyArrow schema including heading hierarchy and article length."""
    if pa is None:
        return None
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), vector_dim)),
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
    def __init__(self, db_path: str = str(LANCEDB_PATH), table_name: str = "medical_knowledge", vector_dim: int = 384):
        self.db_path = str(db_path)
        self.table_name = table_name
        self.vector_dim = vector_dim
        self.db = None
        self.table = None

        os.makedirs(self.db_path, exist_ok=True)
        self._init_table()

    def _init_table(self):
        if lancedb is None:
            return
        try:
            self.db = lancedb.connect(self.db_path)
            existing_tables = self.db.table_names()
            if self.table_name in existing_tables:
                self.table = self.db.open_table(self.table_name)
                try:
                    vec_field = self.table.schema.field("vector")
                    existing_dim = getattr(vec_field.type, "list_size", self.vector_dim)
                    if existing_dim and existing_dim != self.vector_dim:
                        logger.info(f"Recreating LanceDB table for new vector dimension {self.vector_dim} (was {existing_dim}).")
                        self.db.drop_table(self.table_name)
                        self.table = self.db.create_table(self.table_name, schema=build_lancedb_schema(self.vector_dim))
                except Exception:
                    pass
            else:
                self.table = self.db.create_table(self.table_name, schema=build_lancedb_schema(self.vector_dim))
                try:
                    self.table.create_fts_index("child_content", replace=True)
                except Exception as e:
                    logger.debug(f"FTS index creation deferred: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize LanceDB at {self.db_path}: {e}")

    def reset_table(self, vector_dim: int = 384):
        """Drops and recreates the table cleanly during full vault re-indexing."""
        self.vector_dim = vector_dim
        if lancedb is None:
            return
        try:
            if self.db is None:
                self.db = lancedb.connect(self.db_path)
            if self.table_name in self.db.table_names():
                self.db.drop_table(self.table_name)
            self.table = self.db.create_table(self.table_name, schema=build_lancedb_schema(vector_dim))
        except Exception as e:
            logger.error(f"Failed to reset LanceDB table: {e}")

    def insert(self, records: List[Dict[str, Any]]):
        if not records or self.table is None:
            return

        normalized = []
        first_vec = records[0].get("vector", [])
        if first_vec and len(first_vec) != self.vector_dim:
            self.reset_table(vector_dim=len(first_vec))

        for r in records:
            parent = r.get("parent_content", "") or ""
            sub = r.get("subheading", "") or ""
            normalized.append({
                "id": str(r.get("id", "")),
                "vector": r.get("vector", []),
                "source_article": str(r.get("source_article", "")),
                "file_path": str(r.get("file_path", r.get("source_article", ""))),
                "main_topic": str(r.get("main_topic", "")),
                "subheading": sub,
                "heading_path": str(r.get("heading_path", sub)),
                "chunk_index": int(r.get("chunk_index", 0)),
                "page_number": int(r.get("page_number", 1)),
                "article_length": int(r.get("article_length", len(parent))),
                "parent_content": parent,
                "child_content": str(r.get("child_content", "")),
            })

        try:
            self.table.add(normalized)
        except Exception as e:
            logger.error(f"Failed to insert records into LanceDB: {e}")

    def vector_search(self, query_vector: List[float], limit: int = 25) -> List[Dict[str, Any]]:
        if not query_vector or self.table is None:
            return []
        try:
            return self.table.search(query_vector).limit(limit).to_list()
        except Exception as e:
            logger.debug(f"LanceDB vector search skipped: {e}")
            return []

    def fts_search(self, query_string: str, limit: int = 25) -> List[Dict[str, Any]]:
        if not query_string or self.table is None:
            return []
        try:
            return self.table.search(query_string, vector_column_name="child_content").limit(limit).to_list()
        except Exception as e:
            logger.debug(f"LanceDB FTS search skipped: {e}")
            return []

    def count_rows(self) -> int:
        if self.table is None:
            return 0
        try:
            if hasattr(self.table, "count_rows"):
                return int(self.table.count_rows())
            return len(self.table.to_arrow())
        except Exception:
            return 0
