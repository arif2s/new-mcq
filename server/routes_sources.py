from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List
import time
import os
import json
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
        rows = cursor.fetchall()
        return [{"id": r["id"], "name": r["name"], "type": r["type"], "path": r["path"], "enabled": bool(r["enabled"]), "itemCount": r["itemCount"], "lastIndexed": r["lastIndexed"]} for r in rows]

@router.post("")
def add_source(payload: SourcePayload):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge_sources (id, name, type, path, enabled, itemCount, lastIndexed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (payload.id, payload.name, payload.type, payload.path, int(payload.enabled), payload.itemCount, payload.lastIndexed))
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
        sources = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM knowledge_sources WHERE enabled = 1")
            sources = cursor.fetchall()
        if not sources:
            await ws_manager.broadcast_indexing_progress(100, "No enabled sources to index.")
            return
        index, reader = get_index()
        writer = index.writer()
        writer.delete_all_documents()
        writer.commit()
        total_sources = len(sources)
        for i, src in enumerate(sources):
            progress = 10 + int(80 * (i / total_sources))
            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']}...")
            path = src["path"]
            item_count = 0
            if os.path.exists(path):
                if src["type"] == "obsidian":
                    for root, _, files in os.walk(path):
                        for file in files:
                            if file.endswith(".md"):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, "r", encoding="utf-8") as file_handle:
                                        content = file_handle.read()
                                    doc = tantivy.Document()
                                    doc.add_text("doc_id", file_path)
                                    doc.add_text("source_type", "obsidian")
                                    doc.add_text("file_path", file_path)
                                    doc.add_integer("page_number", 1)
                                    doc.add_text("title", file)
                                    doc.add_text("body", content)
                                    writer.add_document(doc)
                                    item_count += 1
                                except Exception:
                                    pass
                elif src["type"] == "pdf":
                    if os.path.isdir(path):
                        for root, _, files in os.walk(path):
                            for file in files:
                                if file.endswith(".pdf"):
                                    file_path = os.path.join(root, file)
                                    doc = tantivy.Document()
                                    doc.add_text("doc_id", file_path)
                                    doc.add_text("source_type", "pdf")
                                    doc.add_text("file_path", file_path)
                                    doc.add_integer("page_number", 1)
                                    doc.add_text("title", file)
                                    doc.add_text("body", f"PDF content for {file}")
                                    writer.add_document(doc)
                                    item_count += 1
                    elif os.path.isfile(path) and path.endswith(".pdf"):
                        doc = tantivy.Document()
                        doc.add_text("doc_id", path)
                        doc.add_text("source_type", "pdf")
                        doc.add_text("file_path", path)
                        doc.add_integer("page_number", 1)
                        doc.add_text("title", os.path.basename(path))
                        doc.add_text("body", f"PDF content for {os.path.basename(path)}")
                        writer.add_document(doc)
                        item_count += 1
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE knowledge_sources SET itemCount = ?, lastIndexed = ? WHERE id = ?", (item_count, "Just now", src["id"]))
                conn.commit()
        writer.commit()
        await ws_manager.broadcast_indexing_progress(100, "Indexing Complete! All local knowledge material ready for AI RAG.")
    except Exception as e:
        await ws_manager.broadcast_indexing_progress(100, f"Indexing failed: {str(e)}")

@router.post("/index")
def index_sources(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_indexing)
    return {"status": "started"}
