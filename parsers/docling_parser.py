import os
import io
import logging
import fitz
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat, DocumentStream

logger = logging.getLogger(__name__)
_converter = None

def get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = True
        pipeline_options.generate_page_images = False
        _converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    return _converter

def extract_structured_layout(file_path: str, page_number: int) -> str:
    if not os.path.exists(file_path):
        return ""

    try:
        with fitz.open(file_path) as doc:
            target_page_index = max(0, page_number - 1)
            if target_page_index >= doc.page_count:
                return ""

            with fitz.open() as single_page_doc:
                single_page_doc.insert_pdf(doc, from_page=target_page_index, to_page=target_page_index)
                pdf_bytes = single_page_doc.tobytes()

        # Execute conversion entirely in memory
        converter = get_converter()
        stream = DocumentStream(name=f"page_{page_number}.pdf", stream=io.BytesIO(pdf_bytes))
        conversion_result = converter.convert(stream)

        return conversion_result.document.export_to_markdown().strip()

    except Exception as e:
        logger.error(f"Docling extraction failed: {e}")
        return ""
