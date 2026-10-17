import os
import sys
import shutil
import asyncio
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional
from contextlib import closing
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

try:
    import tantivy
except ImportError:
    tantivy = None

from config import UI_DIR
from core.database import get_db_connection
from parsers.tantivy_engine import get_index
from parsers.obsidian_reader import extract_obsidian_sections
from parsers.pdf_extractor import extract_page_with_headings
from parsers.zim_reader import get_archive
from parsers.embedding_engine import CPUEmbeddingEngine, is_embedding_model_downloaded
from parsers.lancedb_engine import LanceDBEngine
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
    pdfEngine: Optional[str] = "fitz"


class OpenPdfPayload(BaseModel):
    file_path: str
    page: int = 1


@router.get("/wallpapers")
def get_wallpapers():
    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    base_dir = UI_DIR / "img"
    dark_dir = base_dir / "dark"
    light_dir = base_dir / "light"

    dark_wallpapers = []
    light_wallpapers = []

    if dark_dir.exists() and dark_dir.is_dir():
        dark_wallpapers = sorted([
            f.name for f in dark_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_extensions
        ])

    if light_dir.exists() and light_dir.is_dir():
        light_wallpapers = sorted([
            f.name for f in light_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_extensions
        ])

    return {"dark": dark_wallpapers, "light": light_wallpapers}


@router.get("")
def get_sources():
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_sources")
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "path": r["path"],
                "enabled": bool(r["enabled"]),
                "itemCount": r["itemCount"],
                "lastIndexed": r["lastIndexed"],
                "pdfEngine": r["pdfEngine"] if "pdfEngine" in r.keys() and r["pdfEngine"] else "fitz",
            }
            for r in cursor.fetchall()
        ]


@router.post("")
def add_source(payload: SourcePayload):
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO knowledge_sources
            (id, name, type, path, enabled, itemCount, lastIndexed, pdfEngine)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.id,
                payload.name,
                payload.type,
                payload.path,
                int(payload.enabled),
                payload.itemCount,
                payload.lastIndexed,
                payload.pdfEngine or "fitz",
            ),
        )
        conn.commit()
    return {"status": "success"}


@router.post("/{source_id}/toggle")
def toggle_source(source_id: str):
    with closing(get_db_connection()) as conn:
        conn.execute("UPDATE knowledge_sources SET enabled = 1 - enabled WHERE id = ?", (source_id,))
        conn.commit()
    return {"status": "success"}


@router.delete("/{source_id}")
def delete_source(source_id: str):
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM knowledge_sources WHERE id = ?", (source_id,))
        conn.commit()
    return {"status": "success"}


async def run_indexing():
    """
    Builds the hybrid Tantivy (lexical + heading path + article length) and LanceDB (semantic)
    indices across enabled Obsidian, PDF, and >10GB ZIM sources.
    - Never crashes if offline and embedding model has not been downloaded yet.
    - Handles >10GB ZIM archives via zero-copy C++ B-tree metadata without bloating SQLite/LanceDB.
    """
    await ws_manager.broadcast_indexing_progress(5, "Starting hybrid vault indexing...")
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM knowledge_sources WHERE enabled = 1")
            sources = [dict(r) for r in cursor.fetchall()]

        if not sources:
            await ws_manager.broadcast_indexing_progress(100, "No enabled sources to index.")
            return

        index = await asyncio.to_thread(get_index, True)
        writer = index.writer(50_000_000) if index is not None else None

        await ws_manager.broadcast_indexing_progress(10, "Checking CPU Embedding Model cache...")
        embedder = await asyncio.to_thread(CPUEmbeddingEngine)
        can_embed = is_embedding_model_downloaded(embedder.model_id)
        lancedb_engine = await asyncio.to_thread(LanceDBEngine, vector_dim=embedder.get_dimension())
        if can_embed:
            await asyncio.to_thread(lancedb_engine.reset_table, embedder.get_dimension())

        total_sources = len(sources)

        for i, src in enumerate(sources):
            progress = 15 + int(80 * (i / max(total_sources, 1)))
            await ws_manager.broadcast_indexing_progress(progress, f"Indexing {src['name']} ({src['type'].upper()})...")

            source_path = Path(src["path"])
            item_count = 0
            last_indexed_label = "Just now"
            lancedb_records = []

            def flush_lancedb():
                if lancedb_records and can_embed:
                    lancedb_engine.insert(lancedb_records)
                lancedb_records.clear()

            if source_path.exists():
                # 1. OBSIDIAN VAULT INDEXING (Preserves Heading Hierarchy & Total Note Length)
                if src["type"] == "obsidian":
                    files = list(source_path.rglob("*.md")) if source_path.is_dir() else ([source_path] if source_path.suffix.lower() == ".md" else [])
                    total_files = len(files)
                    for file_idx, file_path in enumerate(files):
                        await asyncio.sleep(0)
                        if file_idx % 10 == 0:
                            await ws_manager.broadcast_indexing_progress(
                                progress,
                                f"Indexing {src['name']} ({file_idx + 1}/{total_files}): {file_path.name}",
                            )
                        try:
                            sections = await asyncio.to_thread(extract_obsidian_sections, str(file_path))
                            if not sections:
                                continue

                            for sec_idx, sec in enumerate(sections):
                                sec_text = sec["text"]
                                h_path = sec["heading_path"]
                                subheading = sec["subheading"]
                                art_len = sec["article_length"]

                                if writer is not None and tantivy is not None:
                                    doc = tantivy.Document()
                                    doc.add_text("doc_id", f"obs_{file_path}_{sec_idx}")
                                    doc.add_text("source_type", "obsidian")
                                    doc.add_text("file_path", str(file_path))
                                    doc.add_integer("page_number", 1)
                                    doc.add_text("title", file_path.stem)
                                    doc.add_text("heading_path", h_path)
                                    doc.add_integer("doc_length", art_len)
                                    doc.add_text("snippet", sec_text[:800])
                                    doc.add_text("obsidian_body", sec_text)
                                    writer.add_document(doc)

                                if can_embed:
                                    chunks = [c.strip() for c in sec_text.split("\n\n") if c.strip()]
                                    for c_idx, chunk in enumerate(chunks):
                                        vec = await asyncio.to_thread(embedder.embed_single, chunk)
                                        if not vec:
                                            continue
                                        chunk_id = hashlib.md5(f"{file_path}_{sec_idx}_{c_idx}_{chunk[:50]}".encode()).hexdigest()
                                        lancedb_records.append({
                                            "id": chunk_id,
                                            "vector": vec,
                                            "source_article": file_path.name,
                                            "file_path": str(file_path),
                                            "main_topic": file_path.stem,
                                            "subheading": subheading,
                                            "heading_path": h_path,
                                            "chunk_index": c_idx,
                                            "page_number": 1,
                                            "article_length": art_len,
                                            "parent_content": sec_text,
                                            "child_content": chunk,
                                        })

                                if len(lancedb_records) >= 100:
                                    await asyncio.to_thread(flush_lancedb)

                            item_count += 1
                        except Exception as e:
                            logger.debug(f"Skipped Obsidian file {file_path}: {e}")

                # 2. PDF TEXTBOOK INDEXING (Supports PyMuPDF Fitz with Headings OR Docling ML Layout)
                elif src["type"] == "pdf":
                    if source_path.is_dir():
                        pdf_files = [f for f in source_path.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf"]
                    else:
                        pdf_files = [source_path] if source_path.suffix.lower() == ".pdf" else []
                    total_files = len(pdf_files)
                    pdf_engine = src.get("pdfEngine", "fitz") or "fitz"

                    for file_idx, file_path in enumerate(pdf_files):
                        await asyncio.sleep(0)
                        try:
                            sections = []
                            if pdf_engine == "docling":
                                try:
                                    from parsers.docling_parser import extract_pdf_structure
                                    await ws_manager.broadcast_indexing_progress(
                                        progress,
                                        f"Indexing {src['name']} ({file_idx + 1}/{total_files}): {file_path.name} [Docling]",
                                    )
                                    sections = await asyncio.to_thread(extract_pdf_structure, str(file_path))
                                except Exception as d_err:
                                    logger.warning(f"Docling extraction failed for {file_path}: {d_err}")

                            if pdf_engine == "docling" and sections:
                                for section_idx, section in enumerate(sections):
                                    page_num = int(section["page"])
                                    heading = section["heading"]
                                    subheading = section.get("subheading", heading)
                                    parent_content = section["text"]
                                    art_len = int(section.get("article_length", len(parent_content)))

                                    if writer is not None and tantivy is not None:
                                        doc = tantivy.Document()
                                        doc.add_text("doc_id", f"pdf_{file_path}_{page_num}_{section_idx}")
                                        doc.add_text("source_type", "pdf")
                                        doc.add_text("file_path", str(file_path))
                                        doc.add_integer("page_number", page_num)
                                        doc.add_text("title", file_path.stem)
                                        doc.add_text("heading_path", heading)
                                        doc.add_integer("doc_length", art_len)
                                        doc.add_text("snippet", parent_content[:800])
                                        doc.add_text("document_body", parent_content)
                                        writer.add_document(doc)

                                    if can_embed:
                                        child_chunks = [c.strip() for c in parent_content.split("\n\n") if c.strip()]
                                        for c_idx, child_content in enumerate(child_chunks):
                                            if child_content.startswith("[") and child_content.endswith("]"):
                                                continue
                                            vec = await asyncio.to_thread(embedder.embed_single, child_content)
                                            if not vec:
                                                continue
                                            chunk_id = hashlib.md5(f"{file_path}_{page_num}_{section_idx}_{c_idx}".encode()).hexdigest()
                                            lancedb_records.append({
                                                "id": chunk_id,
                                                "vector": vec,
                                                "source_article": file_path.name,
                                                "file_path": str(file_path),
                                                "main_topic": file_path.stem,
                                                "subheading": subheading,
                                                "heading_path": heading,
                                                "chunk_index": c_idx,
                                                "page_number": page_num,
                                                "article_length": art_len,
                                                "parent_content": parent_content,
                                                "child_content": child_content,
                                            })

                                    if len(lancedb_records) >= 100:
                                        await asyncio.to_thread(flush_lancedb)
                                    item_count += 1
                            else:
                                if pdf_engine == "docling":
                                    logger.warning(f"Docling extraction produced no sections for {file_path}. Falling back to Fitz.")
                                import fitz
                                with fitz.open(str(file_path)) as pdf_doc:
                                    total_pages = pdf_doc.page_count
                                    for page_num in range(total_pages):
                                        await asyncio.sleep(0)
                                        if page_num % 10 == 0:
                                            await ws_manager.broadcast_indexing_progress(
                                                progress,
                                                f"Indexing {src['name']} ({file_idx + 1}/{total_files}) p.{page_num + 1}/{total_pages}: {file_path.name}",
                                            )
                                        page = pdf_doc.load_page(page_num)
                                        page_info = extract_page_with_headings(page, default_title=file_path.stem)
                                        text = page_info["text"]
                                        if not text:
                                            continue

                                        h_path = page_info["heading_path"]
                                        subheading = page_info["subheading"]
                                        art_len = page_info["length"]

                                        if writer is not None and tantivy is not None:
                                            doc = tantivy.Document()
                                            doc.add_text("doc_id", f"pdf_{file_path}_{page_num + 1}")
                                            doc.add_text("source_type", "pdf")
                                            doc.add_text("file_path", str(file_path))
                                            doc.add_integer("page_number", page_num + 1)
                                            doc.add_text("title", file_path.stem)
                                            doc.add_text("heading_path", h_path)
                                            doc.add_integer("doc_length", art_len)
                                            doc.add_text("snippet", text[:800])
                                            doc.add_text("document_body", text)
                                            writer.add_document(doc)

                                        if can_embed:
                                            chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
                                            for c_idx, chunk in enumerate(chunks):
                                                vec = await asyncio.to_thread(embedder.embed_single, chunk)
                                                if not vec:
                                                    continue
                                                chunk_id = hashlib.md5(f"{file_path}_{page_num + 1}_{c_idx}".encode()).hexdigest()
                                                lancedb_records.append({
                                                    "id": chunk_id,
                                                    "vector": vec,
                                                    "source_article": file_path.name,
                                                    "file_path": str(file_path),
                                                    "main_topic": file_path.stem,
                                                    "subheading": subheading,
                                                    "heading_path": h_path,
                                                    "chunk_index": c_idx,
                                                    "page_number": page_num + 1,
                                                    "article_length": art_len,
                                                    "parent_content": text,
                                                    "child_content": chunk,
                                                })

                                        if len(lancedb_records) >= 100:
                                            await asyncio.to_thread(flush_lancedb)
                                        item_count += 1
                        except Exception as e:
                            logger.error(f"Failed PDF processing for {file_path}: {e}")

                # 3. >10GB ZIM ARCHIVE VERIFICATION (Zero-Copy B-Tree Header Inspection)
                elif src["type"] == "zim":
                    await ws_manager.broadcast_indexing_progress(
                        progress,
                        f"Linking ZIM B-tree index for {src['name']}...",
                    )
                    try:
                        archive = await asyncio.to_thread(get_archive, str(source_path))
                        item_count = int(getattr(archive, "article_count", 0) or getattr(archive, "all_entry_count", 0))
                        last_indexed_label = "Linked (Direct ZIM B-Tree)"
                    except Exception as e:
                        logger.warning(f"Could not open ZIM archive {source_path}: {e}")
                        last_indexed_label = "Error reading ZIM"

            await asyncio.to_thread(flush_lancedb)

            with closing(get_db_connection()) as update_conn:
                update_conn.execute(
                    "UPDATE knowledge_sources SET itemCount = ?, lastIndexed = ? WHERE id = ?",
                    (item_count, last_indexed_label, src["id"]),
                )
                update_conn.commit()

        if writer is not None:
            writer.commit()

        msg = "Indexing Complete! Hybrid Tantivy + ZIM Ready"
        if can_embed:
            msg += " (with CPU Vector Embeddings)."
        else:
            msg += " (Lexical + ZIM active; download Embedding Model in Settings to enable Vector search)."
        await ws_manager.broadcast_indexing_progress(100, msg)

    except Exception as e:
        logger.error(f"Indexing process failed critically: {e}", exc_info=True)
        await ws_manager.broadcast_indexing_progress(100, f"Indexing failed: {str(e)}")


@router.post("/index")
def index_sources(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_indexing)
    return {"status": "started"}


@router.post("/open_pdf")
def open_pdf(payload: OpenPdfPayload):
    """Launches SumatraPDF directly at the cited page number."""
    if sys.platform != "win32":
        return {
            "status": "error",
            "message": f"SumatraPDF deep-linking is designed for Windows (Target: {payload.file_path} p.{payload.page}).",
        }

    sumatra_exe = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe") or "SumatraPDF"
    try:
        sanitized_path = Path(payload.file_path).resolve()
        subprocess.Popen(
            [sumatra_exe, "-reuse-instance", "-page", str(max(1, int(payload.page))), str(sanitized_path)],
            shell=False,
        )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/asset")
def get_asset(path: str):
    """Serves embedded images and attachments referenced inside Obsidian notes."""
    with closing(get_db_connection()) as conn:
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

            for root, _, files in os.walk(vault_path):
                if target_name in files:
                    found_path = Path(root) / target_name
                    if found_path.is_file():
                        return FileResponse(found_path)

    raise HTTPException(status_code=404, detail="Obsidian asset not found")


@router.post("/build_zim_dict")
async def build_zim_dict():
    """
    Verifies enabled ZIM archives without copying >10GB of entries into SQLite.
    """
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, path FROM knowledge_sources WHERE type = 'zim' AND enabled = 1")
            zim_rows = cursor.fetchall()

            if not zim_rows:
                return {"status": "error", "message": "No enabled ZIM files found in sources."}

            total_entries = 0
            for row in zim_rows:
                try:
                    archive = await asyncio.to_thread(get_archive, row["path"])
                    cnt = int(getattr(archive, "article_count", 0) or getattr(archive, "all_entry_count", 0))
                    total_entries += cnt
                    cursor.execute(
                        "UPDATE knowledge_sources SET itemCount = ?, lastIndexed = 'Linked (Direct ZIM B-Tree)' WHERE id = ?",
                        (cnt, row["id"]),
                    )
                except Exception as e:
                    logger.warning(f"Failed verifying ZIM archive {row['path']}: {e}")
            conn.commit()

        return {"status": "success", "total_entries": total_entries}
    except Exception as e:
        return {"status": "error", "message": str(e)}
