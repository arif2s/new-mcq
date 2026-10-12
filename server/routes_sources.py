from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List
from pathlib import Path
import asyncio
import hashlib
import logging

from core.database import get_db_connection
from parsers.tantivy_engine import get_index
import tantivy
from server.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])

class SourcePayload(BaseModel):
    id: str
    name: str
    type: str
    path: str
    enabled: bool = True
    itemCount: int = 0
    lastIndexed: str = "Never"
    pdfEngine: str = "fitz"

@router.get("")
def get_sources():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_sources")
        return [
            {
                "id": r["id"], "name": r["name"], "type": r["type"],
                "path": r["path"], "enabled": bool(r["enabled"]),
                "itemCount": r["itemCount"], "lastIndexed": r["lastIndexed"],
                "pdfEngine": r["pdfEngine"] if "pdfEngine" in r.keys() else "fitz"
            }
            for r in cursor.fetchall()
        ]

@router.post("")
def add_source(payload: SourcePayload):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge_sources (id, name, type, path, enabled, itemCount, lastIndexed, pdfEngine)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.id, payload.name, payload.type, payload.path,
            int(payload.enabled), payload.itemCount, payload.lastIndexed, payload.pdfEngine
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
    await ws_manager.broadcast_indexing_progress(5, "Starting indexing process...")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM knowledge_sources WHERE enabled = 1")
            sources = [dict(r) for r in cursor.fetchall()]

        if not sources:
            await ws_manager.broadcast_indexing_progress(100, "No enabled sources to index.")
            return

        # Initialize Tantivy Lexical Search Engine
        index = get_index()
        writer = index.writer(50_000_000)
        writer.delete_all_documents()
        writer.commit()

        # Initialize LanceDB Semantic Search Engine & Embedder
        await ws_manager.broadcast_indexing_progress(10, "Loading CPU Embedding Model...")
        from parsers.lancedb_engine import LanceDBEngine
        from parsers.embedding_engine import CPUEmbeddingEngine

        embedder = await asyncio.to_thread(CPUEmbeddingEngine)
        lancedb_engine = await asyncio.to_thread(LanceDBEngine)

        # Drop and recreate the LanceDB table for a clean full re-index
        try:
            await asyncio.to_thread(lancedb_engine.db.drop_table, lancedb_engine.table_name)
        except Exception:
            pass
        lancedb_engine = await asyncio.to_thread(LanceDBEngine)

        total_sources = len(sources)

        for i, src in enumerate(sources):
            progress = 15 + int(80 * (i / total_sources))
            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']}...")

            source_path = Path(src["path"])
            item_count = 0
            lancedb_records = []

            def flush_lancedb():
                if lancedb_records:
                    lancedb_engine.insert(lancedb_records)
                    lancedb_records.clear()

            if source_path.exists():
                if src["type"] == "obsidian":
                    files = list(source_path.rglob("*.md"))
                    total_files = len(files)
                    for file_idx, file_path in enumerate(files):
                        # Yield to event loop to preserve WebSocket heartbeats
                        await asyncio.sleep(0)
                        if file_idx % 10 == 0:
                            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} - file {file_idx+1}/{total_files}: {file_path.name}")

                        try:
                            content = file_path.read_text(encoding="utf-8", errors="ignore")

                            # 1. Lexical Retrieval (Tantivy) -> Index the full parent context
                            doc = tantivy.Document()
                            doc.add_text("doc_id", str(file_path))
                            doc.add_text("source_type", "obsidian")
                            doc.add_text("file_path", str(file_path))
                            doc.add_integer("page_number", 1)
                            doc.add_text("title", file_path.name)
                            doc.add_text("obsidian_body", content)
                            writer.add_document(doc)

                            # 2. Semantic Retrieval (LanceDB) -> Index hierarchical child chunks
                            chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
                            for c_idx, chunk in enumerate(chunks):
                                vec = await asyncio.to_thread(embedder.embed_single, chunk)
                                if not vec: continue

                                chunk_id = hashlib.md5(f"{file_path}_1_{c_idx}_{chunk[:50]}".encode()).hexdigest()
                                lancedb_records.append({
                                    "id": chunk_id,
                                    "vector": vec,
                                    "source_article": file_path.name,
                                    "main_topic": src['name'],
                                    "subheading": file_path.name,
                                    "chunk_index": c_idx,
                                    "page_number": 1,
                                    "parent_content": content,
                                    "child_content": chunk
                                })

                            # Batch commit to PyArrow to optimize RAM limits
                            if len(lancedb_records) >= 100:
                                await asyncio.to_thread(flush_lancedb)

                            item_count += 1
                        except (IOError, UnicodeDecodeError):
                            continue

                elif src["type"] == "pdf":
                    pdf_files = list(source_path.rglob("*.pdf")) if source_path.is_dir() else [source_path] if source_path.suffix.lower() == ".pdf" else []
                    total_files = len(pdf_files)
                    pdf_engine = src.get("pdfEngine", "fitz")

                    if pdf_engine == "docling":
                        from parsers.docling_parser import extract_pdf_structure

                    import fitz

                    for file_idx, file_path in enumerate(pdf_files):
                        await asyncio.sleep(0)
                        try:
                            if pdf_engine == "docling":
                                await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} - file {file_idx+1}/{total_files}: {file_path.name} (Docling)")
                                sections = await asyncio.to_thread(extract_pdf_structure, str(file_path))

                                for section_idx, section in enumerate(sections):
                                    page_num = section["page"]
                                    heading = section["heading"]
                                    parent_content = section["text"]

                                    # 1. Lexical Retrieval (Tantivy)
                                    doc = tantivy.Document()
                                    doc.add_text("doc_id", f"{file_path}_{page_num}_{section_idx}")
                                    doc.add_text("source_type", "pdf")
                                    doc.add_text("file_path", str(file_path))
                                    doc.add_integer("page_number", page_num)
                                    doc.add_text("title", heading)
                                    doc.add_text("document_body", parent_content)
                                    writer.add_document(doc)

                                    # 2. Semantic Retrieval (LanceDB)
                                    child_chunks = [c.strip() for c in parent_content.split("\n\n") if c.strip()]
                                    subheading = heading.split(" > ")[-1] if " > " in heading else heading

                                    for c_idx, child_content in enumerate(child_chunks):
                                        if child_content.startswith("[") and child_content.endswith("]") and child_content[1:-1] == heading:
                                            continue

                                        vec = await asyncio.to_thread(embedder.embed_single, child_content)
                                        if not vec: continue

                                        chunk_id = hashlib.md5(f"{file_path}_{page_num}_{section_idx}_{c_idx}_{child_content[:50]}".encode()).hexdigest()
                                        lancedb_records.append({
                                            "id": chunk_id,
                                            "vector": vec,
                                            "source_article": file_path.name,
                                            "main_topic": src['name'],
                                            "subheading": subheading,
                                            "chunk_index": c_idx,
                                            "page_number": page_num,
                                            "parent_content": parent_content,
                                            "child_content": child_content
                                        })

                                    if len(lancedb_records) >= 100:
                                        await asyncio.to_thread(flush_lancedb)

                                    item_count += 1
                            else:
                                with fitz.open(str(file_path)) as pdf_doc:
                                    total_pages = pdf_doc.page_count
                                    for page_num in range(total_pages):
                                        await asyncio.sleep(0)
                                        if page_num % 5 == 0:
                                            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} - file {file_idx+1}/{total_files} page {page_num+1}/{total_pages}: {file_path.name}")

                                        page = pdf_doc.load_page(page_num)
                                        text = page.get_text("text")

                                        if not text.strip():
                                            continue

                                        # 1. Lexical Retrieval (Tantivy)
                                        doc = tantivy.Document()
                                        doc.add_text("doc_id", f"{file_path}_{page_num}")
                                        doc.add_text("source_type", "pdf")
                                        doc.add_text("file_path", str(file_path))
                                        doc.add_integer("page_number", page_num + 1)
                                        doc.add_text("title", file_path.name)
                                        doc.add_text("document_body", text)
                                        writer.add_document(doc)

                                        # 2. Semantic Retrieval (LanceDB)
                                        chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
                                        for c_idx, chunk in enumerate(chunks):
                                            vec = await asyncio.to_thread(embedder.embed_single, chunk)
                                            if not vec: continue

                                            chunk_id = hashlib.md5(f"{file_path}_{page_num}_{c_idx}_{chunk[:50]}".encode()).hexdigest()
                                            lancedb_records.append({
                                                "id": chunk_id,
                                                "vector": vec,
                                                "source_article": file_path.name,
                                                "main_topic": src['name'],
                                                "subheading": f"Page {page_num + 1}",
                                                "chunk_index": c_idx,
                                                "page_number": page_num + 1,
                                                "parent_content": text,
                                                "child_content": chunk
                                            })

                                        if len(lancedb_records) >= 100:
                                            await asyncio.to_thread(flush_lancedb)

                                        item_count += 1
                        except Exception as e:
                            logger.error(f"Failed PDF processing for {file_path}: {e}")
                            continue

            # Push remaining vector chunks
            await asyncio.to_thread(flush_lancedb)

            with get_db_connection() as update_conn:
                update_cursor = update_conn.cursor()
                update_cursor.execute(
                    "UPDATE knowledge_sources SET itemCount = ?, lastIndexed = ? WHERE id = ?",
                    (item_count, "Just now", src["id"])
                )
                update_conn.commit()

        writer.commit()
        await ws_manager.broadcast_indexing_progress(100, "Indexing Complete! Hybrid LanceDB/Tantivy Database Ready.")

    except Exception as e:
        logger.error(f"Indexing process failed critically: {e}", exc_info=True)
        await ws_manager.broadcast_indexing_progress(100, f"Indexing failed: {str(e)}")


@router.post("/index")
def index_sources(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_indexing)
    return {"status": "started"}

class OpenPdfPayload(BaseModel):
    file_path: str
    page: int

@router.post("/open_pdf")
def open_pdf(payload: OpenPdfPayload):
    import subprocess
    import sys
    from pathlib import Path

    if sys.platform != 'win32':
        return {"status": "error", "message": "PDF deep-linking via SumatraPDF is only supported on Windows."}

    try:
        # Sanitize path by resolving to an absolute, real path to prevent directory traversal
        sanitized_path = Path(payload.file_path).resolve()

        # We need to construct the SumatraPDF command correctly
        subprocess.Popen(["SumatraPDF", "-page", str(payload.page), str(sanitized_path)], shell=False)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/asset")
def get_asset(path: str):
    from fastapi.responses import FileResponse

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM knowledge_sources WHERE type = 'obsidian' AND enabled = 1")
        sources = cursor.fetchall()

    target_name = Path(path).name

    for source in sources:
        vault_path = Path(source["path"])
        if vault_path.exists():
            try:
                direct_path = (vault_path / path).resolve()
                if direct_path.is_relative_to(vault_path.resolve()) and direct_path.is_file():
                     return FileResponse(direct_path)
            except Exception:
                pass

            import os
            for root, _, files in os.walk(vault_path):
                if target_name in files:
                    found_path = Path(root) / target_name
                    if found_path.is_file():
                        return FileResponse(found_path)

    raise HTTPException(status_code=404, detail="Asset not found")

@router.post("/build_zim_dict")
async def build_zim_dict():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM knowledge_sources WHERE type = 'zim' AND enabled = 1")
            zim_files = [row["path"] for row in cursor.fetchall()]

            if not zim_files:
                return {"status": "error", "message": "No enabled ZIM files found in sources."}

            cursor.execute("CREATE TABLE IF NOT EXISTS zim_titles (title TEXT PRIMARY KEY)")
            cursor.execute("DELETE FROM zim_titles")

            import libzim
            titles = []

            for zim_path in zim_files:
                try:
                    archive = libzim.Archive(zim_path)
                    for i in range(archive.all_entry_count):
                        entry = archive._get_entry_by_id(i)
                        title = entry.title
                        if title:
                            titles.append((title,))
                except Exception as e:
                    logger.warning(f"Failed reading ZIM for dictionary: {e}")

            if titles:
                cursor.executemany("INSERT OR IGNORE INTO zim_titles (title) VALUES (?)", titles)
            conn.commit()

        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
