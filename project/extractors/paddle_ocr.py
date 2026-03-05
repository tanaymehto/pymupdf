"""Approach 2 – PP-OCR (PaddleOCR) Document Structure Extraction.

Uses the same PP-OCR detection and recognition models as PaddleOCR
(en_PP-OCRv3_det + en_PP-OCRv4_rec) but runs them via the
rapidocr-onnxruntime package, which bundles pre-converted ONNX model
files and uses onnxruntime as the inference backend instead of
PaddlePaddle.  This avoids the PaddlePaddle OneDNN/MKLDNN crash that
occurs on Windows when PaddlePaddle 3.x is compiled with oneDNN support.

STRICT: ALWAYS renders every page to a 120-DPI image and runs PP-OCR
to obtain word bounding-boxes.  The embedded text layer is NEVER
consulted – PP-OCR is the only text source.  fitz is used solely for
rendering (get_pixmap).
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

import cv2
import fitz
import numpy as np

from rapidocr_onnxruntime import RapidOCR

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from extractors.pdf_structural import extract_page_from_words

_DPI = 120
_SCALE = 72.0 / _DPI  # pixel → PDF-point scale factor

# Single shared RapidOCR engine (heavy to initialise)
_ENGINE: RapidOCR | None = None


def _is_oom_runtime_error(exc: RuntimeError) -> bool:
    text = str(exc).lower()
    return "out of memory" in text or "std::bad_alloc" in text


def _get_engine() -> RapidOCR:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    return _ENGINE


def _render_rgb(fitz_doc: fitz.Document, page_index: int, dpi: int = _DPI) -> np.ndarray:
    """Render a PDF page to an RGB numpy array."""
    page = fitz_doc[page_index]
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def _split_line_into_words(
    poly: list, text: str, score: float
) -> List[Dict[str, Any]]:
    """Split a detected text-line into individual word dicts.

    PP-OCR (and RapidOCR) returns one result per text line, not per word.
    Each line has a single bounding quad but may contain several space-
    separated tokens.  We distribute word boxes proportionally by character
    count so that column-based parsers can assign each token to the correct
    column using its x0 coordinate.
    """
    tokens = text.split()
    if not tokens:
        return []

    xs = [float(pt[0]) for pt in poly]
    ys = [float(pt[1]) for pt in poly]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    line_width = max(x_max - x_min, 1.0)

    # Total character count including one space per inter-word gap
    total_chars = sum(len(t) for t in tokens) + max(len(tokens) - 1, 0)
    total_chars = max(total_chars, 1)

    words: List[Dict[str, Any]] = []
    cursor = x_min
    for i, token in enumerate(tokens):
        token_width = (len(token) / total_chars) * line_width
        words.append({
            "text": token,
            "x0": cursor,
            "x1": cursor + token_width,
            "top": y_min,
            "bottom": y_max,
            "confidence": score,
        })
        # Advance past token + one space character
        cursor += (len(token) + 1) / total_chars * line_width

    return words


def _ppocr_word_dicts(rgb_image: np.ndarray) -> List[Dict[str, Any]]:
    """Run PP-OCR (via RapidOCR/onnxruntime) on an RGB image.

    Splits detected text lines into individual word dicts with
    proportionally-spaced bounding boxes so the column-aware parser
    can assign each token to the correct column by x0.
    """
    engine = _get_engine()
    ret = engine(rgb_image)
    items = ret[0] if (ret is not None and ret[0] is not None) else []

    words: List[Dict[str, Any]] = []
    for item in items:
        if not item or len(item) < 3:
            continue
        poly, text, score = item[0], str(item[1]).strip(), float(item[2])
        if not text or score < 0.3:
            continue
        words.extend(_split_line_into_words(poly, text, score))
    return words


def extract(
    pdf_path: Path,
    max_pages: int | None = None,
    court: str = "madras",
    progress_cb=None,
) -> List[Dict[str, Any]]:
    """Extract cases using PP-OCR (PaddleOCR models) on rendered page images.
    ALWAYS uses PP-OCR via RapidOCR/onnxruntime – no fitz text-layer fallback.
    """
    _cb = progress_cb or (lambda m: None)
    doc = fitz.open(str(pdf_path))
    rows: List[Dict[str, Any]] = []
    total_pages = len(doc) if max_pages is None else min(len(doc), max_pages)

    try:
        for page_index in range(total_pages):
            page_num = page_index + 1
            _cb(f"PaddleOCR: processing page {page_num} / {total_pages}...")
            rgb = None
            try:
                rgb = _render_rgb(doc, page_index, dpi=_DPI)
                words = _ppocr_word_dicts(rgb)
                page_rows = extract_page_from_words(
                    words, page_num, court=court, coord_scale=_SCALE
                )
                rows.extend(page_rows)
            except MemoryError:
                _cb(
                    f"PaddleOCR: memory exhausted on page {page_num}. "
                    f"Returning partial results ({len(rows)} rows)."
                )
                break
            except RuntimeError as exc:
                if _is_oom_runtime_error(exc):
                    _cb(
                        f"PaddleOCR: runtime OOM on page {page_num}. "
                        f"Returning partial results ({len(rows)} rows)."
                    )
                    break
                raise
            finally:
                del rgb
    finally:
        doc.close()

    return rows



