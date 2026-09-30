import fitz

def extract_page_slice(file_path: str, page_number: int) -> str:
    try:
        doc = fitz.open(file_path)
        page = doc.load_page(max(0, page_number - 1))
        text = page.get_text("text")
        doc.close()
        return text
    except Exception:
        return ""
