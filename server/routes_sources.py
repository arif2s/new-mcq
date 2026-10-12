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
    pdfEngine: str = "pymupdf"

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
                "pdfEngine": r["pdfEngine"] if "pdfEngine" in r.keys() else "pymupdf"
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
        writer = index.writer(50_000_000)
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
                    files = list(source_path.rglob("*.md"))
                    total_files = len(files)
                    for file_idx, file_path in enumerate(files):
                        try:
                            # Yield control back to the event loop. This prevents large Obsidian vaults
                            # from locking up the thread and freezing WebSocket broadcasts.
                            await asyncio.sleep(0)
                            if file_idx % 10 == 0:
                                await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} - file {file_idx+1}/{total_files}: {file_path.name}")

                            content = file_path.read_text(encoding="utf-8", errors="ignore")

                            doc = tantivy.Document()
                            doc.add_text("doc_id", str(file_path))
                            doc.add_text("source_type", "obsidian")
                            doc.add_text("file_path", str(file_path))
                            doc.add_integer("page_number", 1)
                            doc.add_text("title", file_path.name)
                            doc.add_text("obsidian_body", content)

                            writer.add_document(doc)
                            item_count += 1
                        except (IOError, UnicodeDecodeError):
                            continue

                elif src["type"] == "pdf":
                    # Consolidate branching logic: treat a single file as a list of 1
                    pdf_files = list(source_path.rglob("*.pdf")) if source_path.is_dir() else [source_path] if source_path.suffix.lower() == ".pdf" else []
                    total_files = len(pdf_files)

                    pdf_engine = src.get("pdfEngine", "pymupdf")

                    import pymupdf
                    if pdf_engine == "docling":
                        from parsers.docling_parser import extract_pdf_structure

                    for file_idx, file_path in enumerate(pdf_files):
                        await asyncio.sleep(0)
                        try:
                            if pdf_engine == "docling":
                                await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} - file {file_idx+1}/{total_files}: {file_path.name} (Docling)")
                                sections = await asyncio.to_thread(extract_pdf_structure, str(file_path))
                                for section_idx, section in enumerate(sections):
                                    doc = tantivy.Document()
                                    doc.add_text("doc_id", f"{file_path}_{section['page']}_{section_idx}")
                                    doc.add_text("source_type", "pdf")
                                    doc.add_text("file_path", str(file_path))
                                    doc.add_integer("page_number", section["page"])
                                    doc.add_text("title", section["heading"])
                                    doc.add_text("document_body", section["text"])

                                    writer.add_document(doc)
                                    item_count += 1
                            else:
                                with pymupdf.open(str(file_path)) as pdf_doc:
                                    total_pages = pdf_doc.page_count
                                    for page_num in range(total_pages):
                                        await asyncio.sleep(0)
                                        if page_num % 5 == 0:
                                            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} - file {file_idx+1}/{total_files} page {page_num+1}/{total_pages}: {file_path.name}")

                                        page = pdf_doc.load_page(page_num)
                                        text = page.get_text("text")

                                        if not text.strip():
                                            continue

                                        doc = tantivy.Document()
                                        doc.add_text("doc_id", f"{file_path}_{page_num}")
                                        doc.add_text("source_type", "pdf")
                                        doc.add_text("file_path", str(file_path))
                                        doc.add_integer("page_number", page_num + 1)
                                        doc.add_text("title", file_path.name)
                                        doc.add_text("document_body", text)

                                        writer.add_document(doc)
                                        item_count += 1
                        except Exception:
                            continue

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

class OpenPdfPayload(BaseModel):
    file_path: str
    page: int

@router.post("/open_pdf")
def open_pdf(payload: OpenPdfPayload):
    import subprocess
    import sys
    import os
    from pathlib import Path

    if sys.platform != 'win32':
        return {"status": "error", "message": "PDF deep-linking via SumatraPDF is only supported on Windows."}

    try:
        # Sanitize path by resolving to an absolute, real path to prevent directory traversal
        sanitized_path = Path(payload.file_path).resolve()

        # We need to construct the SumatraPDF command correctly
        # Passing flags and values properly separated
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

    # Sanitize path to prevent directory traversal attacks
    target_name = Path(path).name

    for source in sources:
        vault_path = Path(source["path"])
        if vault_path.exists():
            # Attempt to resolve directly if it is a relative path within the vault
            try:
                # Resolve removes symlinks and ..
                # This ensures the resulting path is strictly under the vault_path
                direct_path = (vault_path / path).resolve()
                if direct_path.is_relative_to(vault_path.resolve()) and direct_path.is_file():
                     return FileResponse(direct_path)
            except Exception:
                pass

            # Fallback for when obsidian links are just the filename
            # Use os.walk which is generally faster than rglob for simple filename matching
            import os
            for root, _, files in os.walk(vault_path):
                if target_name in files:
                    found_path = Path(root) / target_name
                    if found_path.is_file():
                        return FileResponse(found_path)

    raise HTTPException(status_code=404, detail="Asset not found")

@router.post("/build_zim_dict")
async def build_zim_dict():
    # Attempt to build the local zim dict by finding zim files and indexing their titles
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

                    # Read entries and get titles.
                    for i in range(archive.all_entry_count):
                        entry = archive._get_entry_by_id(i)
                        title = entry.title
                        if title:
                            titles.append((title,))
                except Exception as e:
                    print(f"Failed reading ZIM for dictionary: {e}")

            if titles:
                cursor.executemany("INSERT OR IGNORE INTO zim_titles (title) VALUES (?)", titles)
            conn.commit()

        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
