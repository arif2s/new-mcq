import os
import lancedb
import pyarrow as pa
import logging

logger = logging.getLogger(__name__)

DB_PATH = "data/lancedb"

# Define the PyArrow schema
schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), 384)),
    pa.field("source_article", pa.string()),
    pa.field("main_topic", pa.string()),
    pa.field("subheading", pa.string()),
    pa.field("chunk_index", pa.int32()),
    pa.field("page_number", pa.int32()),
    pa.field("parent_content", pa.string()),
    pa.field("child_content", pa.string()),
])

class LanceDBEngine:
    def __init__(self, db_path: str = DB_PATH, table_name: str = "medical_knowledge"):
        self.db_path = db_path
        self.table_name = table_name

        # Ensure database directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        try:
            self.db = lancedb.connect(self.db_path)
            # Create or open the table
            if self.table_name in self.db.table_names():
                self.table = self.db.open_table(self.table_name)
            else:
                self.table = self.db.create_table(self.table_name, schema=schema)
                # Create FTS index on child_content natively using tantivy backed by lancedb
                # Note: FTS index requires string column
                try:
                    self.table.create_fts_index("child_content", replace=True)
                    logger.info("FTS index created successfully.")
                except Exception as e:
                    logger.error(f"Failed to create FTS index: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize LanceDB: {e}")
            raise e

    def insert(self, records: list[dict]):
        """
        Records must match the schema:
        - id: str
        - vector: list[float]
        - source_article: str
        - main_topic: str
        - subheading: str
        - chunk_index: int
        - page_number: int
        - parent_content: str
        - child_content: str
        """
        if not records:
            return

        try:
            self.table.add(records)
            # FTS index might need to be refreshed depending on LanceDB version
            # No explicit refresh is needed in some recent versions, but calling create_fts_index again can update it if needed.
            # We don't call it here to avoid overhead for every insertion.
            # In lancedb 0.40.0 FTS indexing might be synchronous or background.
        except Exception as e:
            logger.error(f"Failed to insert records into LanceDB: {e}")
            raise e

    def vector_search(self, query_vector: list[float], limit: int = 5):
        try:
            return self.table.search(query_vector).limit(limit).to_list()
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

    def fts_search(self, query_string: str, limit: int = 5):
        try:
            return self.table.search(query_string, vector_column_name="child_content").limit(limit).to_list()
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []

    def get_table(self):
        return self.table
