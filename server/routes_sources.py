"""
Knowledge source management (PDF, Obsidian, ZIM), wallpaper listing, and background indexer execution.
Enforces zero VRAM usage, cached singleton embedding, Tantivy lock safety, and batched CPU vectors.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import gc
import hashlib
import logging
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional
import urllib.parse

# Enforce Invariant 1: Zero VRAM for backend processes before any native ML/search imports
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

try:
    import tantivy
except ImportError:
    tantivy = None

from config import (
    PDFS_DIR,
    UI_DIR,
)
from core.database import get_db_connection
from parsers.embedding_engine import (
    get_configured_embedding_model,
    get_embedding_engine,
    is_embedding_model_downloaded,
)
from parsers.lancedb_engine import get_lancedb_engine
from parsers.obsidian_reader import extract_obsidian_sections
from parsers.pdf_extractor import (
    extract_page_slice,
    extract_page_with_headings,
    extract_pdf_pages,
    fitz,
    get_pdf_page_count,
)
from parsers.pdf_parser import extract_page_with_adaptive_layout
from parsers.tantivy_engine import get_index
from parsers.zim_reader import get_archive, get_enabled_zim_paths
from server.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])

# Invariant 17: Concurrency state guards for Tantivy writer isolation
_INDEXING_LOCK = asyncio.Lock()
_INDEXING_IN_PROGRESS = False

_IGNORED_VAULT_DIRS = {".obsidian", ".trash", ".git", "__pycache__", "node_modules"}


def _is_hidden_vault_path(path_obj: Path, root_obj: Optional[Path] = None) -> bool:
    """Returns True if path_obj resides inside .obsidian, .trash, or any dot-directory."""
    try:
        rel_parts = (
            path_obj.relative_to(root_obj).parts
            if root_obj and path_obj.is_relative_to(root_obj)
            else path_obj.parts
        )
    except Exception:
        rel_parts = path_obj.parts
    return any(
        part.lower() in _IGNORED_VAULT_DIRS or (part.startswith(".") and len(part) > 1)
        for part in rel_parts[:-1]
    )


class SourcePayload(BaseModel):
    id: str
    name: str
    type: str
    path: str
    enabled: bool = True
    itemCount: int = 0
    lastIndexed: str = "Never"
    pdfEngine: Optional[str] = "docling_v2"


class OpenPdfPayload(BaseModel):
    file_path: str
    page: int = 1
    page_number: Optional[int] = None


class TestPdfExtractPayload(BaseModel):
    file_path: Optional[str] = None
    page_number: int = 1
    engine: str = "fitz"


def find_sumatra_executable() -> Optional[str]:
    """Discovers SumatraPDF binary on Windows using PATH and standard installation paths (Invariant 6)."""
    for binary in ("SumatraPDF", "SumatraPDF.exe", "sumatrapdf", "sumatrapdf.exe"):
        found = shutil.which(binary)
        if found:
            return found

    appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

    candidates = [
        Path(appdata) / "SumatraPDF" / "SumatraPDF.exe" if appdata else None,
        Path(program_files) / "SumatraPDF" / "SumatraPDF.exe" if program_files else None,
        Path(program_files_x86) / "SumatraPDF" / "SumatraPDF.exe"
        if program_files_x86
        else None,
    ]

    for cand in candidates:
        if cand and cand.is_file():
            return str(cand)

    return None


@router.get("/wallpapers")
def get_wallpapers() -> Dict[str, List[str]]:
    """Returns dark and light wallpaper filenames for the dynamic UI glassmorphism theme."""
    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    base_dir = UI_DIR / "img"
    dark_dir = base_dir / "dark"
    light_dir = base_dir / "light"

    dark_wallpapers: List[str] = []
    light_wallpapers: List[str] = []

    if dark_dir.is_dir():
        dark_wallpapers = sorted([
            f.name
            for f in dark_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_extensions
        ])

    if light_dir.is_dir():
        light_wallpapers = sorted([
            f.name
            for f in light_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_extensions
        ])

    # Fallback to root wallpaper or image directories if subfolders are empty
    if not dark_wallpapers and not light_wallpapers:
        general_dir = (
            UI_DIR / "wallpapers" if (UI_DIR / "wallpapers").is_dir() else base_dir
        )
        if general_dir.is_dir():
            general_files = sorted([
                f.name
                for f in general_dir.iterdir()
                if f.is_file() and f.suffix.lower() in valid_extensions
            ])
            dark_wallpapers = list(general_files)
            light_wallpapers = list(general_files)

    return {"dark": dark_wallpapers, "light": light_wallpapers}


@router.get("")
def get_sources() -> List[Dict[str, Any]]:
    """Returns all configured offline knowledge sources from SQLite."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_sources ORDER BY name ASC")
        return [
            {
                "id": str(r["id"]),
                "name": str(r["name"]),
                "type": str(r["type"]),
                "path": str(r["path"]),
                "enabled": bool(r["enabled"]),
                "itemCount": int(r["itemCount"] or 0),
                "lastIndexed": str(r["lastIndexed"] or "Never"),
                "pdfEngine": str(r["pdfEngine"])
                if ("pdfEngine" in r.keys() and r["pdfEngine"])
                else "docling_v2",
            }
            for r in cursor.fetchall()
        ]


@router.post("")
def add_source(payload: SourcePayload) -> Dict[str, str]:
    """Adds or updates an offline knowledge source."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO knowledge_sources
            (id, name, type, path, enabled, itemCount, lastIndexed, pdfEngine)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                path = excluded.path,
                enabled = excluded.enabled,
                itemCount = excluded.itemCount,
                lastIndexed = excluded.lastIndexed,
                pdfEngine = excluded.pdfEngine
            """,
            (
                payload.id,
                payload.name,
                payload.type.strip().lower(),
                payload.path.strip(),
                int(payload.enabled),
                payload.itemCount,
                payload.lastIndexed,
                payload.pdfEngine or "docling_v2",
            ),
        )
        conn.commit()

    if payload.type.strip().lower() == "zim":
        try:
            get_enabled_zim_paths(force_refresh=True)
        except Exception:
            pass
    return {"status": "success"}


@router.post("/{source_id}/toggle")
def toggle_source(source_id: str) -> Dict[str, Any]:
    """Toggles enabled state for a specific knowledge source."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE knowledge_sources SET enabled = 1 - enabled WHERE id = ?",
            (source_id,),
        )
        conn.commit()
        cursor.execute(
            "SELECT enabled, type FROM knowledge_sources WHERE id = ?", (source_id,)
        )
        row = cursor.fetchone()
        is_enabled = bool(row["enabled"]) if row else False
        src_type = str(row["type"] or "").lower() if row else ""

    if src_type == "zim":
        try:
            get_enabled_zim_paths(force_refresh=True)
        except Exception:
            pass
    return {"status": "success", "enabled": is_enabled}


@router.delete("/{source_id}")
def delete_source(source_id: str) -> Dict[str, str]:
    """Removes a knowledge source record from SQLite."""
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM knowledge_sources WHERE id = ?", (source_id,))
        conn.commit()
    try:
        get_enabled_zim_paths(force_refresh=True)
    except Exception:
        pass
    return {"status": "success"}


async def run_indexing() -> None:
    """
    Builds the hybrid Tantivy (lexical BM25) and LanceDB (dense vector + FTS) indices across
    enabled Obsidian, PDF, and ZIM sources.
    """
    global _INDEXING_IN_PROGRESS

    if _INDEXING_IN_PROGRESS:
        logger.warning("run_indexing skipped: already running.")
        return

    async with _INDEXING_LOCK:
        _INDEXING_IN_PROGRESS = True
        writer = None
        committed = False

        await ws_manager.broadcast_indexing_progress(
            source_id="system",
            percent=5,
            message="Starting hybrid vault indexing...",
            status="running",
        )

        try:
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM knowledge_sources WHERE enabled = 1")
                sources = [dict(r) for r in cursor.fetchall()]

            if not sources:
                await ws_manager.broadcast_indexing_progress(
                    source_id="system",
                    percent=100,
                    message="No enabled sources to index.",
                    status="done",
                )
                return

            await asyncio.sleep(0)
            index = await asyncio.to_thread(get_index, reset=True)
            writer = (
                await asyncio.to_thread(index.writer, 50_000_000)
                if index is not None
                else None
            )

            configured_model = get_configured_embedding_model(force_refresh=True)
            can_embed = is_embedding_model_downloaded(configured_model)
            embedder = get_embedding_engine(configured_model)
            v_dim = embedder.get_dimension()
            lancedb_engine = get_lancedb_engine(vector_dim=v_dim)
            await asyncio.to_thread(lancedb_engine.reset_table, v_dim)

            if can_embed:
                await ws_manager.broadcast_indexing_progress(
                    source_id="system",
                    percent=10,
                    message=f"Loading CPU Embedding Model: {configured_model} (dim={v_dim})...",
                    status="running",
                )
            else:
                await ws_manager.broadcast_indexing_progress(
                    source_id="system",
                    percent=10,
                    message="Embedding model not downloaded. Indexing lexical Tantivy, LanceDB FTS, & ZIM.",
                    status="running",
                )

            total_sources = len(sources)
            total_lancedb_chunks = 0

            for i, src in enumerate(sources):
                src_base_pct = 15 + int(80 * (i / max(total_sources, 1)))
                src_span_pct = max(1, int(80 / max(total_sources, 1)))
                src_type = str(src.get("type") or "").strip().lower()

                await ws_manager.broadcast_indexing_progress(
                    source_id=src["id"],
                    percent=src_base_pct,
                    message=f"Indexing {src['name']} ({src_type.upper()})...",
                    status="running",
                )

                source_path = Path(src["path"]).resolve()
                item_count = 0
                last_indexed_label = "Just now"
                lancedb_records: List[Dict[str, Any]] = []

                def flush_lancedb_sync() -> None:
                    nonlocal total_lancedb_chunks
                    if lancedb_records and lancedb_engine is not None:
                        lancedb_engine.insert(lancedb_records)
                        total_lancedb_chunks += len(lancedb_records)
                    lancedb_records.clear()

                if source_path.exists():
                    if src_type == "obsidian":
                        if source_path.is_dir():
                            files = [
                                f
                                for f in source_path.rglob("*.md")
                                if f.is_file()
                                and not _is_hidden_vault_path(f, source_path)
                            ]
                        elif source_path.is_file() and source_path.suffix.lower() == ".md":
                            files = [source_path]
                        else:
                            files = []

                        total_files = len(files)

                        for file_idx, file_path in enumerate(files):
                            await asyncio.sleep(0)
                            if file_idx % 15 == 0:
                                current_pct = src_base_pct + int(
                                    src_span_pct * (file_idx / max(total_files, 1))
                                )
                                await ws_manager.broadcast_indexing_progress(
                                    source_id=src["id"],
                                    percent=current_pct,
                                    message=f"Indexing {src['name']} ({file_idx + 1}/{total_files}): {file_path.name}",
                                    status="running",
                                )

                            try:
                                sections = await asyncio.to_thread(
                                    extract_obsidian_sections, str(file_path)
                                )
                                if not sections:
                                    continue

                                for sec_idx, sec in enumerate(sections):
                                    sec_text = str(sec.get("text", "")).strip()
                                    if not sec_text:
                                        continue

                                    h_path = str(
                                        sec.get("heading_path")
                                        or sec.get("heading")
                                        or file_path.stem
                                    )
                                    subheading = str(
                                        sec.get("subheading")
                                        or h_path.split(" > ")[-1]
                                    )
                                    art_len = int(
                                        sec.get(
                                            "article_length",
                                            sec.get("doc_length", len(sec_text)),
                                        )
                                    )
                                    snippet = sec_text[:800]

                                    if writer is not None and tantivy is not None:
                                        doc = tantivy.Document()
                                        doc.add_text(
                                            "doc_id", f"obs_{file_path.stem}_{sec_idx}"
                                        )
                                        doc.add_text("source_type", "obsidian")
                                        doc.add_text("file_path", str(file_path))
                                        doc.add_integer("page_number", 1)
                                        doc.add_text("title", file_path.stem)
                                        doc.add_text("heading_path", h_path)
                                        doc.add_integer("doc_length", art_len)
                                        doc.add_text("snippet", snippet)
                                        doc.add_text("document_body", sec_text)
                                        doc.add_text("obsidian_body", sec_text)
                                        writer.add_document(doc)

                                    raw_chunks = [
                                        c.strip()
                                        for c in sec_text.split("\n\n")
                                        if c.strip()
                                    ]
                                    chunk_candidates = []
                                    for c_idx, chunk in enumerate(raw_chunks):
                                        if chunk.startswith("[") and chunk.endswith("]"):
                                            continue
                                        if len(chunk) < 15:
                                            continue
                                        chunk_candidates.append((c_idx, chunk))

                                    if chunk_candidates:
                                        texts_to_embed = [c[1] for c in chunk_candidates]
                                        if can_embed and embedder is not None:
                                            vectors = await asyncio.to_thread(
                                                embedder.embed, texts_to_embed
                                            )
                                        else:
                                            vectors = []

                                        if not vectors or len(vectors) != len(
                                            chunk_candidates
                                        ):
                                            vectors = [
                                                [0.0] * v_dim for _ in chunk_candidates
                                            ]

                                        for (c_idx, chunk), vec in zip(
                                            chunk_candidates, vectors
                                        ):
                                            raw_key = f"{file_path}_1_{sec_idx}_{c_idx}_{chunk[:50]}"
                                            c_id = hashlib.md5(
                                                raw_key.encode("utf-8"),
                                                usedforsecurity=False,
                                            ).hexdigest()

                                            lancedb_records.append({
                                                "id": str(c_id),
                                                "vector": [float(x) for x in vec],
                                                "source_article": str(file_path.name),
                                                "file_path": str(file_path),
                                                "main_topic": str(file_path.stem),
                                                "subheading": str(subheading),
                                                "heading_path": str(h_path),
                                                "chunk_index": int(c_idx),
                                                "page_number": 1,
                                                "article_length": int(art_len),
                                                "parent_content": str(sec_text),
                                                "child_content": str(chunk),
                                            })

                                    if len(lancedb_records) >= 128:
                                        await asyncio.to_thread(flush_lancedb_sync)

                                item_count += 1
                            except Exception as exc:
                                logger.debug(
                                    "Skipped Obsidian note %s: %s", file_path, exc
                                )

                    elif src_type == "pdf":
                        pdf_files = (
                            [
                                f
                                for f in source_path.rglob("*")
                                if f.is_file() and f.suffix.lower() == ".pdf"
                            ]
                            if source_path.is_dir()
                            else (
                                [source_path]
                                if source_path.suffix.lower() == ".pdf"
                                else []
                            )
                        )
                        total_files = len(pdf_files)
                        pdf_engine = str(src.get("pdfEngine", "docling_v2") or "docling_v2").lower()

                        for file_idx, file_path in enumerate(pdf_files):
                            await asyncio.sleep(0)
                            file_pct = src_base_pct + int(
                                src_span_pct * (file_idx / max(total_files, 1))
                            )
                            await ws_manager.broadcast_indexing_progress(
                                source_id=src["id"],
                                percent=file_pct,
                                message=f"Indexing {src['name']} ({file_idx + 1}/{total_files}): {file_path.name} [{pdf_engine.upper()}]",
                                status="running",
                            )

                            try:
                                sections = []
                                if pdf_engine == "docling_v2":
                                    try:
                                        from parsers.docling_v2_parser import (
                                            extract_pdf_structure,
                                        )

                                        sections = await asyncio.to_thread(
                                            extract_pdf_structure, str(file_path)
                                        )
                                    except Exception as d_err:
                                        logger.warning(
                                            "Docling v2 extraction failed for %s: %s",
                                            file_path,
                                            d_err,
                                        )
                                elif pdf_engine == "docling":
                                    try:
                                        from parsers.docling_parser import (
                                            extract_pdf_structure,
                                        )

                                        sections = await asyncio.to_thread(
                                            extract_pdf_structure, str(file_path)
                                        )
                                    except Exception as d_err:
                                        logger.warning(
                                            "Docling extraction failed for %s: %s",
                                            file_path,
                                            d_err,
                                        )

                                if not sections:
                                    sections = await asyncio.to_thread(
                                        extract_pdf_pages,
                                        str(file_path),
                                        file_path.stem,
                                    )

                                if not sections:
                                    continue

                                for sec_idx, section in enumerate(sections):
                                    page_num = max(
                                        1,
                                        int(
                                            section.get(
                                                "page_number", section.get("page", 1)
                                            )
                                            or 1
                                        ),
                                    )
                                    parent_content = str(
                                        section.get("text", "")
                                    ).strip()
                                    if not parent_content:
                                        continue

                                    heading = str(
                                        section.get(
                                            "heading_path",
                                            section.get("heading", file_path.stem),
                                        )
                                    )
                                    subheading = str(
                                        section.get("subheading", heading)
                                    )
                                    art_len = int(
                                        section.get(
                                            "article_length",
                                            section.get("length", len(parent_content)),
                                        )
                                    )
                                    snippet = parent_content[:800]

                                    if writer is not None and tantivy is not None:
                                        doc = tantivy.Document()
                                        doc.add_text(
                                            "doc_id",
                                            f"pdf_{file_path.stem}_{page_num}_{sec_idx}",
                                        )
                                        doc.add_text("source_type", "pdf")
                                        doc.add_text("file_path", str(file_path))
                                        doc.add_integer("page_number", page_num)
                                        doc.add_text("title", file_path.stem)
                                        doc.add_text("heading_path", heading)
                                        doc.add_integer("doc_length", art_len)
                                        doc.add_text("snippet", snippet)
                                        doc.add_text("document_body", parent_content)
                                        writer.add_document(doc)

                                    raw_chunks = [
                                        c.strip()
                                        for c in parent_content.split("\n\n")
                                        if c.strip()
                                    ]
                                    chunk_candidates = []
                                    for c_idx, child_chunk in enumerate(raw_chunks):
                                        if child_chunk.startswith(
                                            "["
                                        ) and child_chunk.endswith("]"):
                                            continue
                                        if len(child_chunk) < 15:
                                            continue
                                        chunk_candidates.append((c_idx, child_chunk))

                                    if chunk_candidates:
                                        texts_to_embed = [c[1] for c in chunk_candidates]
                                        if can_embed and embedder is not None:
                                            vectors = await asyncio.to_thread(
                                                embedder.embed, texts_to_embed
                                            )
                                        else:
                                            vectors = []

                                        if not vectors or len(vectors) != len(
                                            chunk_candidates
                                        ):
                                            vectors = [
                                                [0.0] * v_dim for _ in chunk_candidates
                                            ]

                                        for (c_idx, child_chunk), vec in zip(
                                            chunk_candidates, vectors
                                        ):
                                            raw_key = f"{file_path}_{page_num}_{sec_idx}_{c_idx}_{child_chunk[:50]}"
                                            c_id = hashlib.md5(
                                                raw_key.encode("utf-8"),
                                                usedforsecurity=False,
                                            ).hexdigest()

                                            lancedb_records.append({
                                                "id": str(c_id),
                                                "vector": [float(x) for x in vec],
                                                "source_article": str(file_path.name),
                                                "file_path": str(file_path),
                                                "main_topic": str(file_path.stem),
                                                "subheading": str(subheading),
                                                "heading_path": str(heading),
                                                "chunk_index": int(c_idx),
                                                "page_number": int(page_num),
                                                "article_length": int(art_len),
                                                "parent_content": str(parent_content),
                                                "child_content": str(child_chunk),
                                            })

                                    if len(lancedb_records) >= 128:
                                        await asyncio.to_thread(flush_lancedb_sync)

                                item_count += 1
                            except Exception as exc:
                                logger.error(
                                    "Failed processing PDF file %s: %s", file_path, exc
                                )

                    elif src_type == "zim":
                        await ws_manager.broadcast_indexing_progress(
                            source_id=src["id"],
                            percent=src_base_pct,
                            message=f"Linking direct ZIM B-tree index for {src['name']}...",
                            status="running",
                        )
                        try:
                            archive = await asyncio.to_thread(
                                get_archive, str(source_path)
                            )
                            item_count = int(
                                getattr(archive, "article_count", 0)
                                or getattr(archive, "all_entry_count", 0)
                            )
                            last_indexed_label = "Linked (Direct ZIM B-Tree)"
                        except Exception as exc:
                            logger.warning(
                                "Could not open ZIM archive %s: %s", source_path, exc
                            )
                            last_indexed_label = "Error reading ZIM"

                await asyncio.to_thread(flush_lancedb_sync)

                with closing(get_db_connection()) as update_conn:
                    update_conn.execute(
                        "UPDATE knowledge_sources SET itemCount = ?, lastIndexed = ? WHERE id = ?",
                        (item_count, last_indexed_label, src["id"]),
                    )
                    update_conn.commit()

            if writer is not None:
                await ws_manager.broadcast_indexing_progress(
                    source_id="system",
                    percent=96,
                    message="Committing Tantivy BM25 inverted index segments...",
                    status="running",
                )
                await asyncio.to_thread(writer.commit)
                committed = True

            if lancedb_engine is not None and total_lancedb_chunks > 0:
                await ws_manager.broadcast_indexing_progress(
                    source_id="system",
                    percent=98,
                    message="Creating LanceDB hybrid full-text search index...",
                    status="running",
                )
                try:
                    await asyncio.to_thread(lancedb_engine.create_fts_index)
                except Exception as fts_err:
                    logger.debug("LanceDB FTS creation note: %s", fts_err)

            get_enabled_zim_paths(force_refresh=True)

            success_msg = "Indexing Complete! Hybrid Tantivy + ZIM Ready"
            if can_embed:
                success_msg += f" (with {total_lancedb_chunks:,} CPU vector embeddings)."
            else:
                success_msg += " (Lexical + ZIM active; download Embedding Model in Settings to enable Vector search)."

            await ws_manager.broadcast_indexing_progress(
                source_id="system",
                percent=100,
                message=success_msg,
                status="done",
            )

        except Exception as exc:
            logger.error("Vault indexing failed critically: %s", exc, exc_info=True)
            await ws_manager.broadcast_indexing_progress(
                source_id="system",
                percent=100,
                message=f"Indexing failed: {str(exc)}",
                status="error",
            )
        finally:
            if writer is not None:
                if not committed and hasattr(writer, "rollback"):
                    try:
                        await asyncio.to_thread(writer.rollback)
                    except Exception:
                        pass
                del writer
                writer = None
                gc.collect()
            _INDEXING_IN_PROGRESS = False


@router.post("/index")
def index_sources(background_tasks: BackgroundTasks) -> Dict[str, str]:
    """Enqueues hybrid vault indexing in background tasks if not already running (Invariant 17)."""
    if _INDEXING_IN_PROGRESS or _INDEXING_LOCK.locked():
        return {"status": "busy", "message": "Indexing is already running in background."}
    background_tasks.add_task(run_indexing)
    return {"status": "started"}


def _resolve_pdf_path(file_path: str) -> Optional[Path]:
    """Resolves a PDF file path from direct path or by querying enabled PDF knowledge sources."""
    if not file_path:
        return None

    clean_str = urllib.parse.unquote(file_path).strip().strip('"').strip("'")
    candidate = Path(clean_str)
    if candidate.is_file() and candidate.suffix.lower() == ".pdf":
        return candidate.resolve()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT path FROM knowledge_sources WHERE LOWER(type) = 'pdf' AND enabled = 1"
        )
        pdf_rows = cursor.fetchall()

    search_name = candidate.name.lower()
    search_stem = candidate.stem.lower()

    for row in pdf_rows:
        source_path = Path(row["path"])
        if not source_path.exists():
            continue
        if source_path.is_file() and source_path.suffix.lower() == ".pdf":
            if (
                source_path.name.lower() == search_name
                or source_path.stem.lower() == search_stem
            ):
                return source_path.resolve()
        elif source_path.is_dir():
            rel_cand = source_path / clean_str
            if rel_cand.is_file() and rel_cand.suffix.lower() == ".pdf":
                return rel_cand.resolve()
            direct_child = source_path / candidate.name
            if direct_child.is_file() and direct_child.suffix.lower() == ".pdf":
                return direct_child.resolve()
            for p_file in source_path.rglob("*.pdf"):
                if (
                    p_file.name.lower() == search_name
                    or p_file.stem.lower() == search_stem
                ):
                    return p_file.resolve()

    return None


@router.post("/open_pdf")
def open_pdf(payload: OpenPdfPayload) -> Dict[str, str]:
    """Launches SumatraPDF directly at the cited page number adhering to Invariant 6."""
    target_page = (
        payload.page_number if payload.page_number is not None else payload.page
    )
    target_page = max(1, int(target_page or 1))

    if sys.platform != "win32":
        return {
            "status": "error",
            "message": f"SumatraPDF deep-linking is designed for Windows (Target: {payload.file_path} p.{target_page}).",
        }

    try:
        resolved_path = _resolve_pdf_path(payload.file_path)
        if resolved_path is None and payload.file_path:
            cand = Path(urllib.parse.unquote(payload.file_path).strip())
            if cand.is_file():
                resolved_path = cand.resolve()

        if resolved_path is None or not resolved_path.is_file():
            return {
                "status": "error",
                "message": f"PDF file not found: {payload.file_path}",
            }
        if resolved_path.suffix.lower() != ".pdf":
            return {
                "status": "error",
                "message": f"Invalid file extension (expected .pdf): {resolved_path.name}",
            }

        sumatra_exe = find_sumatra_executable() or "SumatraPDF.exe"
        subprocess.Popen(
            [
                sumatra_exe,
                "-reuse-instance",
                "-page",
                str(target_page),
                str(resolved_path),
            ],
            shell=False,
        )
        return {"status": "success", "file_path": str(resolved_path), "page": str(target_page)}
    except Exception as exc:
        logger.error("Failed opening SumatraPDF: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/asset")
def get_asset(path: str) -> FileResponse:
    """
    Serves embedded images and attachments referenced inside Obsidian notes.
    Guarantees path traversal safety (is_relative_to) and resolves within attachments subfolders.
    """
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="Asset path is required")

    clean_path = urllib.parse.unquote(path).strip().replace("\\", "/").lstrip("/")
    target_name = Path(clean_path).name

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT path FROM knowledge_sources WHERE LOWER(type) = 'obsidian' AND enabled = 1"
        )
        sources = cursor.fetchall()

    for source in sources:
        vault_path = Path(source["path"]).resolve()
        if not vault_path.is_dir():
            continue

        try:
            direct_candidate = (vault_path / clean_path).resolve()
            if (
                direct_candidate.is_file()
                and direct_candidate.is_relative_to(vault_path)
                and not _is_hidden_vault_path(direct_candidate, vault_path)
            ):
                media_type = (
                    mimetypes.guess_type(str(direct_candidate))[0]
                    or "application/octet-stream"
                )
                return FileResponse(direct_candidate, media_type=media_type)
        except Exception:
            pass

        for sub_dir in ("attachments", "assets", "images", "files", "media", ""):
            try:
                cand = (
                    (vault_path / sub_dir / target_name).resolve()
                    if sub_dir
                    else (vault_path / target_name).resolve()
                )
                if cand.is_file() and cand.is_relative_to(vault_path):
                    media_type = (
                        mimetypes.guess_type(str(cand))[0]
                        or "application/octet-stream"
                    )
                    return FileResponse(cand, media_type=media_type)
            except Exception:
                continue

        for root, dirs, files in os.walk(vault_path):
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in _IGNORED_VAULT_DIRS and not d.startswith(".")
            ]
            if target_name in files:
                found_path = (Path(root) / target_name).resolve()
                try:
                    if found_path.is_file() and found_path.is_relative_to(vault_path):
                        media_type = (
                            mimetypes.guess_type(str(found_path))[0]
                            or "application/octet-stream"
                        )
                        return FileResponse(found_path, media_type=media_type)
                except Exception:
                    pass

    raise HTTPException(
        status_code=404, detail=f"Obsidian asset not found: {target_name}"
    )


@router.post("/build_zim_dict")
async def build_zim_dict() -> Dict[str, Any]:
    """Verifies enabled ZIM archives via zero-copy C++ B-tree without bloating SQLite/LanceDB."""
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, path FROM knowledge_sources WHERE LOWER(type) = 'zim' AND enabled = 1"
            )
            zim_rows = cursor.fetchall()

            if not zim_rows:
                return {
                    "status": "error",
                    "message": "No enabled ZIM files found in sources.",
                }

            total_entries = 0
            for row in zim_rows:
                try:
                    archive = await asyncio.to_thread(get_archive, str(row["path"]))
                    cnt = int(
                        getattr(archive, "article_count", 0)
                        or getattr(archive, "all_entry_count", 0)
                    )
                    total_entries += cnt
                    cursor.execute(
                        "UPDATE knowledge_sources SET itemCount = ?, lastIndexed = 'Linked (Direct ZIM B-Tree)' WHERE id = ?",
                        (cnt, row["id"]),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed verifying ZIM archive %s: %s", row["path"], exc
                    )
            conn.commit()

        get_enabled_zim_paths(force_refresh=True)
        return {"status": "success", "total_entries": total_entries}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/test_pdf_extract")
def test_pdf_extract(payload: TestPdfExtractPayload) -> Dict[str, Any]:
    """
    Tests PDF extraction engines (PyMuPDF Fitz, Docling ML, or PyMuPDF Heuristic Headings)
    page-by-page for side-by-side verification and manual optimization.
    Returns JSON strictly conforming to the frontend API contract.
    """
    import parsers.docling_parser as docling_ext
    import parsers.pdf_extractor as pdf_ext

    available_pdfs: List[Dict[str, str]] = []
    seen_pdf_paths = set()

    def _add_pdf_item(item_id: str, name: str, pdf_path: Path) -> None:
        resolved = str(pdf_path.resolve())
        if resolved.lower() not in seen_pdf_paths:
            seen_pdf_paths.add(resolved.lower())
            available_pdfs.append({"id": item_id, "name": name, "path": resolved})

    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, path FROM knowledge_sources WHERE LOWER(type) = 'pdf'"
            )
            for r in cursor.fetchall():
                p = Path(r["path"])
                if p.is_file() and p.suffix.lower() == ".pdf":
                    _add_pdf_item(str(r["id"]), str(r["name"] or p.name), p)
                elif p.is_dir():
                    for sub_pdf in sorted(p.rglob("*.pdf")):
                        if sub_pdf.is_file():
                            _add_pdf_item(str(sub_pdf), sub_pdf.name, sub_pdf)
    except Exception as exc:
        logger.warning("Error querying PDF sources: %s", exc)

    if not available_pdfs and PDFS_DIR.exists():
        for f in sorted(PDFS_DIR.rglob("*.pdf")):
            if f.is_file():
                _add_pdf_item(str(f), f.name, f)

    target_path: Optional[Path] = None
    if payload.file_path and payload.file_path.strip():
        req_p = _resolve_pdf_path(payload.file_path.strip())
        if req_p and req_p.is_file():
            target_path = req_p

    if not target_path and available_pdfs:
        target_path = Path(available_pdfs[0]["path"])

    if not target_path:
        return {
            "status": "error",
            "message": "No valid PDF file provided or found in sources. Please add a PDF source or specify a valid path.",
            "available_pdfs": available_pdfs,
        }

    try:
        page_count = pdf_ext.get_pdf_page_count(str(target_path))
        page_num = max(
            1, min(int(payload.page_number or 1), page_count if page_count > 0 else 1)
        )
        engine_used = (payload.engine or "fitz").lower().strip()
        extracted_text = ""
        heading_path = ""
        subheading = ""

        page_image_base64 = ""
        layout_boxes = []
        hierarchy_tree = []
        page_width = 612.0
        page_height = 792.0

        if engine_used == "docling_v2":
            import parsers.docling_v2_parser as docling_v2_ext

            res = docling_v2_ext.extract_page_with_docling_v2(
                str(target_path), page_num, default_title=target_path.stem
            )
            extracted_text = res.get("extracted_text", "") or res.get("text", "")
            heading_path = res.get("heading_path", target_path.stem)
            subheading = res.get("subheading", target_path.stem)
            page_image_base64 = res.get("page_image_base64", "")
            layout_boxes = res.get("layout_boxes", [])
            hierarchy_tree = res.get("hierarchy_tree", [])
            page_width = res.get("page_width", 612.0)
            page_height = res.get("page_height", 792.0)
        elif engine_used == "docling":
            extracted_text = docling_ext.extract_structured_layout(
                str(target_path), page_num
            )
            res = extract_page_with_adaptive_layout(
                str(target_path), page_num, default_title=target_path.stem
            )
            heading_path = res.get("heading_path", target_path.stem)
            subheading = res.get("subheading", target_path.stem)
            page_image_base64 = res.get("page_image_base64", "")
            layout_boxes = res.get("layout_boxes", [])
            hierarchy_tree = res.get("hierarchy_tree", [])
            page_width = res.get("page_width", 612.0)
            page_height = res.get("page_height", 792.0)
        elif engine_used == "headings":
            res = extract_page_with_adaptive_layout(
                str(target_path), page_num, default_title=target_path.stem, promote_headings=True
            )
            extracted_text = res.get("extracted_text", "")
            heading_path = res.get("heading_path", target_path.stem)
            subheading = res.get("subheading", target_path.stem)
            page_image_base64 = res.get("page_image_base64", "")
            layout_boxes = res.get("layout_boxes", [])
            hierarchy_tree = res.get("hierarchy_tree", [])
            page_width = res.get("page_width", 612.0)
            page_height = res.get("page_height", 792.0)
        else:
            engine_used = "fitz"
            res = extract_page_with_adaptive_layout(
                str(target_path), page_num, default_title=target_path.stem, promote_headings=False
            )
            extracted_text = res.get("extracted_text", "")
            heading_path = res.get("heading_path", target_path.stem)
            subheading = res.get("subheading", target_path.stem)
            page_image_base64 = res.get("page_image_base64", "")
            layout_boxes = res.get("layout_boxes", [])
            hierarchy_tree = res.get("hierarchy_tree", [])
            page_width = res.get("page_width", 612.0)
            page_height = res.get("page_height", 792.0)

        return {
            "status": "success",
            "file_name": target_path.name,
            "file_path": str(target_path),
            "page_number": page_num,
            "total_pages": page_count,
            "engine": engine_used,
            "heading_path": heading_path,
            "subheading": subheading,
            "extracted_text": extracted_text,
            "available_pdfs": available_pdfs,
            "page_image_base64": page_image_base64,
            "layout_boxes": layout_boxes,
            "hierarchy_tree": hierarchy_tree,
            "page_width": page_width,
            "page_height": page_height,
        }
    except Exception as exc:
        logger.error("test_pdf_extract error: %s", exc)
        return {
            "status": "error",
            "message": f"Extraction failed: {str(exc)}",
            "available_pdfs": available_pdfs,
        }
