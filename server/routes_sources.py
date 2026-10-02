from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List
from pathlib import Path
import asyncio
from core.database import get_db_connection
from parsers.tantivy_engine import get_index
import tantivy
from server.ws_manager import ws_manager

router = APIRouter(prefix="/api/sources", tags=["sources"])

class SourcePayload(BaseModel):
    id: str
    name: str
    type: str
    path: str
    enabled: bool = True
    itemCount: int = 0
    lastIndexed: str = "Never"

@router.get("")
def get_sources():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_sources")
        return [
            {
                "id": r["id"], "name": r["name"], "type": r["type"],
                "path": r["path"], "enabled": bool(r["enabled"]),
                "itemCount": r["itemCount"], "lastIndexed": r["lastIndexed"]
            }
            for r in cursor.fetchall()
        ]

@router.post("")
def add_source(payload: SourcePayload):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge_sources (id, name, type, path, enabled, itemCount, lastIndexed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.id, payload.name, payload.type, payload.path,
            int(payload.enabled), payload.itemCount, payload.lastIndexed
        ))
        conn.commit()
    return {"status": "success"}

@router.post("/{source_id}/toggle")
def toggle_source(source_id: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE knowledge_sources SET enabled = 1 - enabled WHERE id = ?", (source_id,))
        conn.commit()
    return {"status": "success"}

@router.delete("/{source_id}")
def delete_source(source_id: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM knowledge_sources WHERE id = ?", (source_id,))
        conn.commit()
    return {"status": "success"}

async def run_indexing():
    await ws_manager.broadcast_indexing_progress(10, "Starting indexing...")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM knowledge_sources WHERE enabled = 1")
            sources = [dict(r) for r in cursor.fetchall()]

        if not sources:
            await ws_manager.broadcast_indexing_progress(100, "No enabled sources to index.")
            return

        index = get_index()
        writer = index.writer()
        writer.delete_all_documents()
        writer.commit()

        total_sources = len(sources)

        for i, src in enumerate(sources):
            progress = 10 + int(80 * (i / total_sources))
            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']}...")

            source_path = Path(src["path"])
            item_count = 0

            if source_path.exists():
                if src["type"] == "obsidian":
                    # rglob iteratively yields paths, bypassing the memory overhead of os.walk
                    for file_path in source_path.rglob("*.md"):
                        try:
                            # Yield control back to the event loop. This prevents large Obsidian vaults
                            # from locking up the thread and freezing WebSocket broadcasts.
                            await asyncio.sleep(0)

                            content = file_path.read_text(encoding="utf-8", errors="ignore")

                            doc = tantivy.Document()
                            doc.add_text("doc_id", str(file_path))
                            doc.add_text("source_type", "obsidian")
                            doc.add_text("file_path", str(file_path))
                            doc.add_integer("page_number", 1)
                            doc.add_text("title", file_path.name)
                            doc.add_text("body", content)

                            writer.add_document(doc)
                            item_count += 1
                        except (IOError, UnicodeDecodeError):
                            continue

                elif src["type"] == "pdf":
                    # Consolidate branching logic: treat a single file as a list of 1
                    pdf_files = source_path.rglob("*.pdf") if source_path.is_dir() else [source_path] if source_path.suffix.lower() == ".pdf" else []

                    for file_path in pdf_files:
                        await asyncio.sleep(0)

                        doc = tantivy.Document()
                        doc.add_text("doc_id", str(file_path))
                        doc.add_text("source_type", "pdf")
                        doc.add_text("file_path", str(file_path))
                        doc.add_integer("page_number", 1)
                        doc.add_text("title", file_path.name)

                        # Body left intact as requested; structure is ready for PyMuPDF/Docling extraction logic
                        doc.add_text("body", f"PDF content for {file_path.name}")

                        writer.add_document(doc)
                        item_count += 1

            with get_db_connection() as update_conn:
                update_cursor = update_conn.cursor()
                update_cursor.execute(
                    "UPDATE knowledge_sources SET itemCount = ?, lastIndexed = ? WHERE id = ?",
                    (item_count, "Just now", src["id"])
                )
                update_conn.commit()

        writer.commit()
        await ws_manager.broadcast_indexing_progress(100, "Indexing Complete! All local knowledge material ready for AI RAG.")

    except Exception as e:
        await ws_manager.broadcast_indexing_progress(100, f"Indexing failed: {str(e)}")

@router.post("/index")
def index_sources(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_indexing)
    return {"status": "started"}
