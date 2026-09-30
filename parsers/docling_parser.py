import os, tempfile, logging, fitz
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat

logger = logging.getLogger(__name__)
_converter = None

def get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = True
        pipeline_options.generate_page_images = False
        _converter = DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})
    return _converter

def extract_structured_layout(file_path: str, page_number: int) -> str:
    if not os.path.exists(file_path): return ""
    temp_pdf_path = None
    try:
        doc = fitz.open(file_path)
        target_page_index = max(0, page_number - 1)
        single_page_doc = fitz.open()
        single_page_doc.insert_pdf(doc, from_page=target_page_index, to_page=target_page_index)
        fd, temp_pdf_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        single_page_doc.save(temp_pdf_path)
        single_page_doc.close()
        doc.close()

        converter = get_converter()
        conversion_result = converter.convert(temp_pdf_path)
        return conversion_result.document.export_to_markdown().strip()
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return ""
    finally:
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
