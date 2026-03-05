"""Approach 1 – Surya OCR Layout Extraction.

Surya uses a SegFormer-based text detection model (same family as
document layout models) combined with a transformer-based recognition
model trained on 90+ languages.  It is entirely deep-learning, runs on
CPU via PyTorch, and is significantly more accurate than Tesseract on
degraded or complex documents.

fitz is used solely for page rendering (get_pixmap).
Text layer is NEVER consulted – Surya is the only text source.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

import gc

import fitz
import numpy as np
import torch
from PIL import Image
from surya.detection import DetectionPredictor
from surya.foundation import FoundationPredictor
from surya.recognition import RecognitionPredictor

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from extractors.pdf_structural import extract_page_from_words

_DPI = 150
_SCALE = 72.0 / _DPI  # pixel → PDF-point scale factor

# Lazy-loaded predictors (heavy; ~1 GB of model weights on first use)
_FOUNDATION_PREDICTOR: FoundationPredictor | None = None
_DET_PREDICTOR: DetectionPredictor | None = None
_REC_PREDICTOR: RecognitionPredictor | None = None


def _is_oom_runtime_error(exc: RuntimeError) -> bool:
    text = str(exc).lower()
    return (
        "out of memory" in text
        or "cuda out of memory" in text
        or "std::bad_alloc" in text
    )


def _get_predictors(progress_cb=None):
    global _FOUNDATION_PREDICTOR, _DET_PREDICTOR, _REC_PREDICTOR
    if _FOUNDATION_PREDICTOR is None:
        _cb = progress_cb or (lambda m: None)
        _cb("Surya: loading foundation model (downloads ~1 GB on first run)...")
        _FOUNDATION_PREDICTOR = FoundationPredictor()
        _cb("Surya: foundation model ready. Loading detection model...")
        _DET_PREDICTOR = DetectionPredictor()
        _cb("Surya: detection model ready. Loading recognition model...")
        _REC_PREDICTOR = RecognitionPredictor(foundation_predictor=_FOUNDATION_PREDICTOR)
        _cb("Surya: all models loaded.")
    return _DET_PREDICTOR, _REC_PREDICTOR


def _render_pil(fitz_doc: fitz.Document, page_index: int, dpi: int = _DPI) -> Image.Image:
    """Render a PDF page to a PIL RGB image."""
    page = fitz_doc[page_index]
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _split_line_into_words(
    bbox: list, text: str, score: float
) -> List[Dict[str, Any]]:
    """Distribute a text-line result into per-token word dicts.

    Surya (like EasyOCR and PP-OCR) returns one entry per line.
    We split proportionally by character count so the column parser
    can assign tokens to the correct column by x0.
    """
    tokens = text.split()
    if not tokens:
        return []

    # bbox is [x1, y1, x2, y2] for surya >= 0.6, or 4-point list for older
    if isinstance(bbox[0], (list, tuple)):
        # 4-point polygon [[x,y], ...]
        xs = [float(pt[0]) for pt in bbox]
        ys = [float(pt[1]) for pt in bbox]
    else:
        # [x1, y1, x2, y2]
        xs = [float(bbox[0]), float(bbox[2])]
        ys = [float(bbox[1]), float(bbox[3])]

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


def _surya_word_dicts(pil_image: Image.Image, progress_cb=None) -> List[Dict[str, Any]]:
    """Run Surya OCR on a PIL image and return per-word dicts."""
    det_predictor, rec_predictor = _get_predictors(progress_cb=progress_cb)
    # Disable gradient tracking — we're doing inference, not training
    with torch.no_grad():
        results = rec_predictor([pil_image], det_predictor=det_predictor)
    result = results[0]  # OCRResult for this page

    words: List[Dict[str, Any]] = []
    for line in result.text_lines:
        text = str(line.text or "").strip()
        conf = float(line.confidence or 0.0)
        if not text or conf < 0.3:
            continue
        # line.polygon: 4-point list [[x,y], [x,y], [x,y], [x,y]]
        words.extend(_split_line_into_words(line.polygon, text, conf))
    return words


def extract(
    pdf_path: Path,
    max_pages: int | None = None,
    court: str = "madras",
    progress_cb=None,
) -> List[Dict[str, Any]]:
    """Extract cases using Surya OCR on rendered page images.
    ALWAYS uses Surya – no fitz text-layer fallback.
    """
    _cb = progress_cb or (lambda m: None)
    doc = fitz.open(str(pdf_path))
    rows: List[Dict[str, Any]] = []
    total_pages = len(doc) if max_pages is None else min(len(doc), max_pages)

    try:
        for page_index in range(total_pages):
            page_num = page_index + 1
            _cb(f"Surya: processing page {page_num} / {total_pages}...")
            pil_img = None
            try:
                pil_img = _render_pil(doc, page_index, dpi=_DPI)
                words = _surya_word_dicts(pil_img, progress_cb=_cb)
                page_rows = extract_page_from_words(
                    words, page_num, court=court, coord_scale=_SCALE
                )
                rows.extend(page_rows)
            except MemoryError:
                _cb(
                    f"Surya: memory exhausted on page {page_num}. "
                    f"Returning partial results ({len(rows)} rows)."
                )
                break
            except RuntimeError as exc:
                if _is_oom_runtime_error(exc):
                    _cb(
                        f"Surya: runtime OOM on page {page_num}. "
                        f"Returning partial results ({len(rows)} rows)."
                    )
                    break
                raise
            finally:
                del pil_img
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
    finally:
        doc.close()

    return rows
