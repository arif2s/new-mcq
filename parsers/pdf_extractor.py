import fitz

def extract_page_slice(file_path: str, page_number: int) -> str:
    try:
        with fitz.open(file_path) as doc:
            target_idx = max(0, page_number - 1)
            if target_idx >= doc.page_count:
                return ""
            page = doc.load_page(target_idx)
            return page.get_text("text")
    except Exception:
        return ""
