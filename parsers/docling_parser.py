import io
import logging
import functools
import hashlib

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.base_models import InputFormat, DocumentStream
    from docling_core.types.doc.document import TextItem, SectionHeaderItem, TableItem, TitleItem
except ImportError:
    DocumentConverter = None
    PdfFormatOption = None
    PdfPipelineOptions = None
    InputFormat = None
    DocumentStream = None
    TextItem = None
    SectionHeaderItem = None
    TableItem = None
    TitleItem = None

logger = logging.getLogger(__name__)
_converter = None


def get_converter():
    """Singleton loader for Docling ML models."""
    global _converter
    if DocumentConverter is None:
        raise RuntimeError("Docling is not installed in the current environment.")
    if _converter is None:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = True
        pipeline_options.generate_page_images = False

        _converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    return _converter


@functools.lru_cache(maxsize=64)
def extract_structured_layout(file_path: str, page_number: int) -> str:
    """
    Extracts structured markdown (tables, columns, headings) from a specific PDF page using Docling.
    Cached in memory to prevent redundant ML conversions on the same page.
    """
    if fitz is None:
        return ""
    try:
        with fitz.open(file_path) as doc:
            target_page_index = max(0, int(page_number) - 1)
            if target_page_index >= doc.page_count:
                return ""

            with fitz.open() as single_page_doc:
                single_page_doc.insert_pdf(doc, from_page=target_page_index, to_page=target_page_index)
                pdf_bytes = single_page_doc.tobytes()

        converter = get_converter()
        stream = DocumentStream(name=f"page_{page_number}.pdf", stream=io.BytesIO(pdf_bytes))
        conversion_result = converter.convert(stream)

        return conversion_result.document.export_to_markdown().strip()

    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.error(f"Docling extraction failed for {file_path} page {page_number}: {e}")
        return ""


def extract_pdf_structure(file_path: str) -> list:
    """
    Extracts structured sections from a PDF, preserving hierarchical headings,
    paragraphs, markdown tables, and section character lengths.
    """
    try:
        converter = get_converter()
        conversion_result = converter.convert(file_path)

        sections = []
        heading_stack = {}
        current_text = []
        current_page = 1

        def get_current_heading():
            if not heading_stack:
                return "Untitled"
            return " > ".join([heading_stack[k] for k in sorted(heading_stack.keys())])

        def save_section(page_num):
            if current_text:
                text_content = "\n\n".join(current_text).strip()
                if text_content:
                    h_path = get_current_heading()
                    sections.append({
                        "page": page_num,
                        "heading": h_path,
                        "subheading": h_path.split(" > ")[-1] if " > " in h_path else h_path,
                        "text": text_content,
                        "article_length": len(text_content),
                    })

        for item, level in conversion_result.document.iterate_items():
            item_page = current_page
            if hasattr(item, "prov") and item.prov:
                try:
                    p = item.prov[0]
                    if hasattr(p, "page_no"):
                        item_page = int(p.page_no)
                    elif isinstance(p, dict) and "page_no" in p:
                        item_page = int(p["page_no"])
                except Exception:
                    pass

            if item_page != current_page and current_text:
                save_section(current_page)
                current_text = []
                current_page = item_page

            item_label = getattr(item, "label", None)
            label_str = str(item_label.value if hasattr(item_label, "value") else item_label).lower() if item_label else ""

            is_header = False
            if (SectionHeaderItem and isinstance(item, SectionHeaderItem)) or (TitleItem and isinstance(item, TitleItem)):
                is_header = True
            elif label_str in ["section_header", "title", "page_header", "header", "doc_title"]:
                is_header = True

            if is_header:
                save_section(current_page)
                item_level = getattr(item, "level", 1) or 1
                for k in [k for k in list(heading_stack.keys()) if k >= item_level]:
                    del heading_stack[k]
                item_text = getattr(item, "text", "").strip()
                if item_text:
                    heading_stack[item_level] = item_text
                    current_text = [f"[{get_current_heading()}]"]
                    current_page = item_page
            elif TextItem and isinstance(item, TextItem):
                if item.text.strip():
                    current_text.append(item.text)
                    current_page = item_page
            elif TableItem and isinstance(item, TableItem):
                current_text.append(item.export_to_markdown())
                current_page = item_page

        save_section(current_page)
        return sections

    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Full PDF Docling extraction failed for {file_path}: {e}")
        return []


def ingest_to_lancedb(file_path: str, source_article: str, main_topic: str = "") -> int:
    """
    Extracts PDF structure and ingests it into LanceDB with heading paths and article lengths.
    """
    from parsers.embedding_engine import CPUEmbeddingEngine
    from parsers.lancedb_engine import LanceDBEngine

    sections = extract_pdf_structure(file_path)
    if not sections:
        return 0

    embedder = CPUEmbeddingEngine()
    engine = LanceDBEngine(vector_dim=embedder.get_dimension())

    records = []
    chunk_index = 0

    for section in sections:
        page_num = section["page"]
        heading = section["heading"]
        subheading = section.get("subheading", heading)
        parent_content = section["text"]
        art_len = section.get("article_length", len(parent_content))

        child_chunks = [chunk.strip() for chunk in parent_content.split("\n\n") if chunk.strip()]

        for child_content in child_chunks:
            if child_content.startswith("[") and child_content.endswith("]") and child_content[1:-1] == heading:
                continue

            vector = embedder.embed_single(child_content)
            if not vector:
                continue

            chunk_id = hashlib.md5(f"{file_path}_{page_num}_{chunk_index}_{child_content[:50]}".encode()).hexdigest()

            records.append({
                "id": chunk_id,
                "vector": vector,
                "source_article": source_article,
                "file_path": str(file_path),
                "main_topic": main_topic or heading,
                "subheading": subheading,
                "heading_path": heading,
                "chunk_index": chunk_index,
                "page_number": page_num,
                "article_length": art_len,
                "parent_content": parent_content,
                "child_content": child_content,
            })
            chunk_index += 1

    if records:
        engine.insert(records)

    return len(records)
