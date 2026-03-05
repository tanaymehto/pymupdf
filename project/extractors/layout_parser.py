"""Approach 1 – EasyOCR Layout Extraction.

Uses EasyOCR (CRAFT text detector + CRNN recogniser – deep-learning based)
to obtain word bounding-boxes from rendered page images.  Much faster than
Tesseract on CPU; does not require a system Tesseract install.
fitz is used solely for page rendering (get_pixmap).
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

import fitz
import numpy as np
import easyocr

# Ensure project root is on path so sibling imports work
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from extractors.pdf_structural import extract_page_from_words

_DPI = 150
_SCALE = 72.0 / _DPI  # pixel → PDF-point scale factor

# Single shared EasyOCR reader (initialised once at first use)
_READER: easyocr.Reader | None = None


def _get_reader() -> easyocr.Reader:
    global _READER
    if _READER is None:
        _READER = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _READER


def _render_rgb(fitz_doc: fitz.Document, page_index: int, dpi: int = _DPI) -> np.ndarray:
    """Render a PDF page to an RGB numpy array."""
    page = fitz_doc[page_index]
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def _split_line_into_words(
    bbox: list, text: str, score: float
) -> List[Dict[str, Any]]:
    """Split an EasyOCR text-line result into individual word dicts.

    EasyOCR returns one entry per text line with a 4-point quad.  We
    distribute word boxes proportionally by character count within the
    line's x-extent so that the column-based parser can assign each token
    to the correct column via its x0 coordinate.
    """
    tokens = text.split()
    if not tokens:
        return []

    xs = [float(pt[0]) for pt in bbox]
    ys = [float(pt[1]) for pt in bbox]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    line_width = max(x_max - x_min, 1.0)

    total_chars = sum(len(t) for t in tokens) + max(len(tokens) - 1, 0)
    total_chars = max(total_chars, 1)

    words: List[Dict[str, Any]] = []
    cursor = x_min
    for token in tokens:
        token_width = (len(token) / total_chars) * line_width
        words.append({
            "text": token,
            "x0": cursor,
            "x1": cursor + token_width,
            "top": y_min,
            "bottom": y_max,
            "confidence": score,
        })
        cursor += (len(token) + 1) / total_chars * line_width
    return words


def _easyocr_word_dicts(rgb_image: np.ndarray) -> List[Dict[str, Any]]:
    """Run EasyOCR on an RGB image and return per-word dicts."""
    reader = _get_reader()
    results = reader.readtext(rgb_image, detail=1)
    words: List[Dict[str, Any]] = []
    for bbox, text, conf in results:
        text = str(text).strip()
        if not text or conf < 0.3:
            continue
        words.extend(_split_line_into_words(bbox, text, conf))
    return words


def extract(
    pdf_path: Path,
    max_pages: int | None = None,
    court: str = "madras",
) -> List[Dict[str, Any]]:
    """Extract cases using EasyOCR on rendered page images.
    ALWAYS uses EasyOCR – no fitz text-layer fallback.
    """
    doc = fitz.open(str(pdf_path))
    rows: List[Dict[str, Any]] = []
    total_pages = len(doc) if max_pages is None else min(len(doc), max_pages)

    for page_index in range(total_pages):
        page_num = page_index + 1
        rgb = _render_rgb(doc, page_index, dpi=_DPI)
        words = _easyocr_word_dicts(rgb)
        page_rows = extract_page_from_words(
            words, page_num, court=court, coord_scale=_SCALE
        )
        rows.extend(page_rows)

    doc.close()
    return rows
