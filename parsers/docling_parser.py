import io
import logging
import functools
import fitz
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat, DocumentStream
from docling_core.types.doc.document import TextItem, SectionHeaderItem, TableItem

logger = logging.getLogger(__name__)
_converter = None

def get_converter() -> DocumentConverter:
    """Singleton loader for Docling ML models."""
    global _converter
    if _converter is None:
        pipeline_options = PdfPipelineOptions()
        # Keep heavy OCR/image generation disabled for text/table structure speed
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
    Extracts structured markdown from a specific PDF page using Docling.
    Cached in memory to prevent redundant ML conversions on the same page.
    """
    try:
        # Open directly (EAFP) instead of using os.path.exists()
        with fitz.open(file_path) as doc:
            target_page_index = max(0, page_number - 1)

            if target_page_index >= doc.page_count:
                return ""

            # Isolate the target page in memory to prevent Docling from scanning the entire PDF
            with fitz.open() as single_page_doc:
                single_page_doc.insert_pdf(doc, from_page=target_page_index, to_page=target_page_index)
                pdf_bytes = single_page_doc.tobytes()

        # Execute ML conversion entirely in memory
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
    Extracts structured data from a PDF, identifying headings, paragraphs, and tables,
    and returns a list of dictionaries with page, heading, and text.
    Handles multicolumn texts natively through Docling.
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
            # Return headings sorted by level
            return " > ".join([heading_stack[k] for k in sorted(heading_stack.keys())])

        def save_section(page_num):
            if current_text:
                text_content = "\n\n".join(current_text).strip()
                if text_content:
                    sections.append({
                        "page": page_num,
                        "heading": get_current_heading(),
                        "text": text_content
                    })

        for item, level in conversion_result.document.iterate_items():
            item_page = current_page
            if hasattr(item, 'prov') and item.prov:
                item_page = item.prov[0].page_no

            # Whenever we cross a page boundary, we must save the current chunk
            # and start a new one on the new page, carrying over the same heading.
            if item_page != current_page and current_text:
                save_section(current_page)
                current_text = []
                current_page = item_page

            if isinstance(item, TextItem):
                if isinstance(item, SectionHeaderItem) or getattr(item, 'label', None) in ['section_header', 'title', 'page_header']:
                    # New section starts, save the previous one
                    save_section(current_page)
                    # Update heading stack
                    # Determine level (use getattr to be safe, fallback to 'level' from iterate_items if needed, or 1)
                    item_level = getattr(item, 'level', 1)
                    if item_level is None:
                        item_level = 1

                    # Remove any heading at this level or deeper
                    keys_to_remove = [k for k in heading_stack.keys() if k >= item_level]
                    for k in keys_to_remove:
                        del heading_stack[k]

                    heading_stack[item_level] = item.text.strip()


                    # We inject the full hierarchy into the text output
                    current_text = [f"[{get_current_heading()}]"]
                    current_page = item_page
                elif item.text.strip():
                    current_text.append(item.text)
                    current_page = item_page
            elif isinstance(item, TableItem):
                current_text.append(item.export_to_markdown())
                current_page = item_page

        # Save the last section
        save_section(current_page)
        return sections

    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Full PDF Docling extraction failed for {file_path}: {e}")
        return []
