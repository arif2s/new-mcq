"""
PDF and Medical OCR Text Cleaning and Dehyphenation (`parsers/docling/cleaner.py`).

Provides ligature resolution, dehyphenation across linebreaks, drop-cap merging,
OCR character confusable repair, and Markdown sanitization.
"""

from __future__ import annotations

import re
from typing import Match

_LIGATURE_MAP = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
    "\u00a0": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2009": " ",
    "\u200b": "",
    "\u200e": "",
    "\u200f": "",
    "\ufeff": "",
    "\uf0ae": "→",
}

_HYPHENATED_MEDICAL_PREFIXES = (
    "anti", "non", "auto", "co", "beta", "alpha", "self", "cross", "two", "three", "four", "single", "multi"
)

_BOUND_MEDICAL_PREFIXES = (
    "pre", "post", "sub", "intra", "extra", "hyper", "hypo", "re", "micro", "macro"
)

_MEDICAL_PREFIXES = _HYPHENATED_MEDICAL_PREFIXES + _BOUND_MEDICAL_PREFIXES

_VALID_MEDICAL_ALPHANUM_RE = re.compile(
    r"\b(?:"
    r"\d+(?:\.\d+)?(?:mcg|mmol|meq|mmhg|kcal|mg|ml|ul|iu|hrs?|days?|wks?|mos?|yrs?)|"
    r"HbA1c|G6PD|C[1-9][a-z]|IL[0-9]+[A-Za-z]*|CYP[0-9][A-Z][0-9]+|"
    r"SGLT[12]i?|PDE5i?|5-?HT[0-9][A-Z]?|B2M|C[1-8]|T(?:[1-9]|1[0-2])|L[1-5]|S[1-5]"
    r")\b",
    re.IGNORECASE,
)

# Lexical OCR anomaly patterns that indicate corrupted glyph recognition on a span
_OCR_GARBLED_TOKEN_RE = re.compile(
    r"(?:[\ufffd\u25a1\u25a0]|[@#$%\^&\*~`|\\]{2,}|"
    r"\b[A-Za-z]+[0-9]+[A-Za-z0-9]*[A-Za-z]\b|"
    r"\b[0-9]+[A-Za-z]{3,}\b|"
    r"[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]{6,})"
)

# Regex for finite clause verbs and trailing function words that never appear in standalone noun-phrase headings
_PROSE_FINITE_VERBS_RE = re.compile(
    r"\b(is|are|was|were|can|cannot|will|may|might|should|would|could|has|have|had|do|does|did|must|shall)\b",
    re.IGNORECASE,
)

_TRAILING_FUNCTION_WORD_RE = re.compile(
    r"\b(the|a|an|of|in|to|for|with|on|at|from|by|as|into|and|or|but|nor|yet|so|"
    r"that|which|who|whom|whose|it|its|this|these|those|they|their|them|he|she|we|"
    r"is|are|was|were|be|been|being|can|will|may|should|would|could|has|have|had|not|no)$",
    re.IGNORECASE,
)

_MED_SECTION_HEADINGS = (
    "PATHOPHYSIOLOGY", "CLINICAL MANIFESTATIONS", "DIAGNOSIS AND TREATMENT",
    "ETIOLOGY AND RISK FACTORS", "ETIOLOGY", "MANAGEMENT", "OVERVIEW",
    "EPIDEMIOLOGY", "PREVENTION", "DIAGNOSTIC WORKUP", "COMPLICATIONS",
    "PATIENT EVALUATION", "TREATMENT", "CLINICAL FEATURES", "PATHOGENESIS",
    "PROGNOSIS", "DIFFERENTIAL DIAGNOSIS", "LABORATORY FINDINGS",
)


def is_ocr_garbled_text(text: str) -> bool:
    """
    Fast lexical & character-bigram anomaly detector for identifying spans with corrupted OCR.
    """
    if not text or len(text) < 3:
        return False
    # Strip valid unspaced clinical dosages and biomedical alphanumeric tokens
    cleaned = _VALID_MEDICAL_ALPHANUM_RE.sub("", text)
    # Ignore valid medical abbreviations with numbers like B12, C3a, T4, IgG4, H1N1, 500, 1000
    cleaned = re.sub(
        r"\b(?:Ig[A-Z]\d|B12|T[1-4]|C[1-9][a-z]?|H\dN\d|[A-Z]{1,3}\d{1,3}|[0-9]+(?:\.[0-9]+)?(?:dB|Hz|mm|cm|mg|ml|mL|kg|yr|yrs)?)\b",
        "",
        cleaned,
    )
    return bool(_OCR_GARBLED_TOKEN_RE.search(cleaned))


def repair_ocr_token_heuristics(text: str) -> str:
    """
    Deterministic character-level OCR confusable repair for medical tokens when neural re-OCR
    is not installed or leaves digit-letter confusables (e.g. '0' -> 'o', '1' -> 'l' or 'I' inside alphabetic words).
    """
    if not text:
        return text

    def _fix_word(match: Match) -> str:
        w = match.group(0)
        # Preserve valid short biomedical codes (e.g. B12, IgG4, T3, C5a)
        if re.match(r"^(?:Ig[A-Z]\d|[A-Z]{1,2}\d{1,2}[a-z]?)$", w):
            return w
        alpha_count = sum(1 for c in w if c.isalpha())
        digit_count = sum(1 for c in w if c.isdigit())
        if alpha_count < 3 or digit_count == 0 or digit_count > alpha_count:
            return w
        chars = list(w)
        is_upper_word = sum(1 for c in chars if c.isupper()) >= max(2, alpha_count - 1)
        for idx, ch in enumerate(chars):
            if ch == "0":
                chars[idx] = "O" if is_upper_word else "o"
            elif ch == "1":
                if idx == 0 or is_upper_word:
                    chars[idx] = "I"
                else:
                    chars[idx] = "l"
            elif ch == "5" and alpha_count >= 4:
                chars[idx] = "S" if (idx == 0 or is_upper_word) else "s"
        return "".join(chars)

    return re.sub(r"\b[A-Za-z0-9]{4,}\b", _fix_word, text)


def clean_pdf_text(text: str) -> str:
    """Sanitizes raw PDF text for Tantivy BM25 indexing and LanceDB FTS."""
    if not text:
        return ""

    t = text.replace("\x00", "").replace("\x0c", "\n")
    for lig, rep in _LIGATURE_MAP.items():
        if lig in t:
            t = t.replace(lig, rep)

    t = t.replace("\r\n", "\n").replace("\r", "\n")

    # Join em-dashes and en-dashes across line breaks without introducing spaces
    t = re.sub(r"([—–])[ \t]*\n[ \t]*", r"\1", t)

    # Convert trailing soft hyphens (\u00ad) before line breaks into standard hyphens prior to de-hyphenation
    t = re.sub(r"\u00ad+(\*{1,2})?[ \t]*\n", r"-\1\n", t)

    def _should_keep_hyphen(prefix: str, suffix: str) -> bool:
        p_low = prefix.lower()
        s_low = suffix.lower()
        if p_low in _HYPHENATED_MEDICAL_PREFIXES:
            return True
        if p_low in _BOUND_MEDICAL_PREFIXES and s_low and p_low[-1] == s_low[0] and s_low[0] in "aeiou":
            return True
        if prefix == "Mora" and s_low.startswith("xella"):
            return True
        return False

    # De-hyphenation across optional inline markdown emphasis (* or **) and line breaks
    def _dehyphen_md_match(match: Match) -> str:
        prefix = match.group(1)
        close_em = match.group(2) or ""
        open_em = match.group(3) or ""
        suffix = match.group(4)
        keep_h = _should_keep_hyphen(prefix, suffix)
        sep = "-" if keep_h else ""
        if close_em and open_em and close_em == open_em:
            return f"{prefix}{sep}{suffix}"
        return f"{prefix}{close_em}{sep}{open_em}{suffix}"

    t = re.sub(
        r"([A-Za-z]{2,})[-­](\*{1,2})?[ \t]*\n[ \t]*(\*{1,2})?([a-z]{2,})",
        _dehyphen_md_match,
        t,
    )

    # Also handle inline soft-hyphenated words within the same line
    def _dehyphen_inline_soft(match: Match) -> str:
        prefix = match.group(1)
        close_em = match.group(2) or ""
        open_em = match.group(3) or ""
        suffix = match.group(4)
        keep_h = _should_keep_hyphen(prefix, suffix)
        sep = "-" if keep_h else ""
        if close_em and open_em and close_em == open_em:
            return f"{prefix}{sep}{suffix}"
        return f"{prefix}{close_em}{sep}{open_em}{suffix}"

    t = re.sub(
        r"([A-Za-z]{2,})\u00ad+(\*{1,2})?[ \t]*(\*{1,2})?([a-z]{2,})",
        _dehyphen_inline_soft,
        t,
    )
    t = t.replace("\u00ad", "")

    # Merge adjacent identical markdown emphasis runs across whitespace/newlines (e.g. '*Haemophilus* *aegyptius*')
    t = re.sub(r"\*\*([ \t\n]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([ \t\n]+)\*(?!\*)", r"\1", t)

    # Clean up inner parenthesis spacing and comma spacing artifacts from PDF spans
    t = re.sub(r"\([ \t]+(\*{0,2}[A-Za-z0-9])", r"(\1", t)
    t = re.sub(r"([A-Za-z0-9]\*{0,2})[ \t]+\)", r"\1)", t)
    t = re.sub(r"([A-Za-z]\*{0,2})[ \t]+,[ \t]*(\*{0,2}[A-Za-z])", r"\1, \2", t)

    t = re.sub(r"\b(CHAPTER|PART|SECTION|UNIT|MODULE)\s*([0-9]+|[IVXLCDM]+)\b", r"\1 \2", t, flags=re.IGNORECASE)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# Backwards compatibility re-exports
_is_ocr_garbled_text = is_ocr_garbled_text
_repair_ocr_token_heuristics = repair_ocr_token_heuristics


# --- Phase 3 Pre-Compiled Regex & Biomedical Hyphen Preservation ---
import re as _re

_CONTROL_CHARS_RE = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE_RE = _re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE_RE = _re.compile(r"\n{3,}")
_PROTECTED_PREFIXES = (
    r"(?:alpha|beta|gamma|delta|epsilon|anti|non|post|pre|pro|co|re|sub|super|"
    r"inter|intra|extra|ultra|micro|macro|multi|peri|para|trans|cis|levo|dextro|"
    r"hyper|hypo|auto|neo|pseudo|semi|uni|bi|tri|quadri|poly|mono|endo|exo|"
    r"[A-Za-z]*\d+|[A-Z]{1,4})"
)
_PROTECTED_SET = {
    "alpha", "beta", "gamma", "delta", "epsilon", "anti", "non", "post", "pre", "pro", "co", "re", "sub", "super",
    "inter", "intra", "extra", "ultra", "micro", "macro", "multi", "peri", "para", "trans", "cis", "levo", "dextro",
    "hyper", "hypo", "auto", "neo", "pseudo", "semi", "uni", "bi", "tri", "quadri", "poly", "mono", "endo", "exo"
}
_HYPHEN_BREAK_RE = _re.compile(r"\b([a-zA-Z0-9]+)-\s*\n\s*([a-zA-Z0-9]+)\b")


def clean_ocr_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00ad", "-")
    text = _CONTROL_CHARS_RE.sub("", text)

    def _replace_hyphen(m: _re.Match) -> str:
        prefix = m.group(1)
        suffix = m.group(2)
        if prefix.lower() in _PROTECTED_SET or len(prefix) <= 1 or prefix.isupper():
            return f"{prefix}-\n{suffix}"
        return f"{prefix}{suffix}"

    text = _HYPHEN_BREAK_RE.sub(_replace_hyphen, text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return _MULTI_NEWLINE_RE.sub("\n\n", text).strip()
