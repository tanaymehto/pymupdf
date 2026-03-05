"""Approach 4 – Pure Docling Extraction.

Docling uses the DocLayNet deep-learning model to detect and classify
every layout zone on every page (paragraph, table, section_header,
page_header, page_footer, figure, etc.), then reads the native PDF text
layer for each zone independently.

Every word coordinate comes from Docling's own bbox attribution — fitz
is NOT used for text at all.  fitz is only used to build batch sub-PDFs
(since Docling's convert() needs a file path) and to seed page heights
before Docling has run.

This gives two things pure-fitz parsing lacks:
  1. Label-aware filtering: page_header / page_footer / figure / caption
     zones are explicitly discarded before the state machine ever sees them.
  2. Logical reading order: Docling emits items in reading order, not the
     arbitrary draw order that raw fitz get_text("words") can produce.

Memory: Docling's C++ preprocessing allocates per-page tensors; large PDFs
trigger std::bad_alloc.  We process in batches of BATCH_SIZE pages and
skip remaining batches on OOM (already-processed pages are still returned).
"""
import gc
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import fitz  # used ONLY to split batch sub-PDFs — never for text extraction

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from extractors.pdf_structural import extract_page_from_words

_CONVERTER = None
BATCH_SIZE = 25  # pages per Docling call — keeps peak RAM ≤ ~2 GB

# Zone labels to discard entirely — not part of the case list body
_SKIP_LABELS = {
    "page_header", "page_footer", "picture", "figure", "chart",
    "formula", "caption", "footnote",
}


def _get_converter(progress_cb=None):
    global _CONVERTER
    if _CONVERTER is None:
        _cb = progress_cb or (lambda m: None)
        # Fix WinError 1314 (symlink privilege) on Windows without Developer Mode
        try:
            from huggingface_hub import constants as hf_constants
            from huggingface_hub.file_download import _are_symlinks_supported_in_dir
            from pathlib import Path as _Path
            cache_dir = str(_Path(hf_constants.HF_HUB_CACHE).expanduser().resolve())
            _are_symlinks_supported_in_dir[cache_dir] = False
        except Exception:
            pass
        _cb("Docling: loading DocLayNet layout model (downloads ~400 MB on first run)...")
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        opts = PdfPipelineOptions()
        opts.do_ocr = False           # native text PDF — no OCR needed
        opts.do_table_structure = False
        _CONVERTER = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=opts)}
        )
        _cb("Docling: model loaded.")
    return _CONVERTER


def _item_label(item) -> str:
    try:
        return str(getattr(item, "label", "") or "").lower().replace("-", "_")
    except Exception:
        return ""


def _docling_words_for_batch(
    batch_pdf_path: str,
    page_offset: int,
    page_heights: dict,
    progress_cb,
) -> dict:
    """Run Docling on a batch sub-PDF; return {abs_page_no: [word_dict, ...]}.

    Docling exposes text at the item (paragraph / cell / heading) level with
    one bounding box per item.  We split each item's text into tokens and
    distribute them proportionally across the item's x-span so the shared
    column-aware state machine can assign each token to the right column.

    Coordinate output: origin top-left, units PDF points (fitz convention).
    Docling's bottom-up PDF coords are flipped using page height.
    """
    converter = _get_converter(progress_cb=progress_cb)
    result = converter.convert(batch_pdf_path)
    dl_doc = result.document

    # Update page heights from Docling's own measurement (more accurate)
    try:
        for pg_no, pg in dl_doc.pages.items():
            try:
                abs_pno = int(pg_no) + page_offset
                page_heights[abs_pno] = float(pg.size.height)
            except Exception:
                pass
    except Exception:
        pass

    page_words: dict = {}

    for item, _ in dl_doc.iterate_items():
        label = _item_label(item)
        if any(skip in label for skip in _SKIP_LABELS):
            continue  # discard headers, footers, figures, etc.

        # Get text from the item
        text = ""
        try:
            text = str(item.text or "").strip()
        except AttributeError:
            try:
                text = item.export_to_text().strip()
            except Exception:
                pass
        if not text:
            continue

        for prov in (getattr(item, "prov", None) or []):
            try:
                local_pno = int(prov.page_no)
                abs_pno = local_pno + page_offset
                bbox = prov.bbox
                ph = page_heights.get(abs_pno, 0.0)

                x0 = float(bbox.l)
                x1 = float(bbox.r)

                # Docling uses PDF bottom-up coords when bbox.t > bbox.b
                if ph > 0 and float(bbox.t) > float(bbox.b):
                    top    = ph - float(bbox.t)
                    bottom = ph - float(bbox.b)
                else:
                    top    = float(bbox.t)
                    bottom = float(bbox.b)

                # Distribute words proportionally across the horizontal span
                tokens = text.split()
                if not tokens:
                    continue
                line_width = max(x1 - x0, 1.0)
                total_chars = sum(len(t) for t in tokens) + max(len(tokens) - 1, 0)
                total_chars = max(total_chars, 1)
                cursor = x0
                for token in tokens:
                    tw = (len(token) / total_chars) * line_width
                    page_words.setdefault(abs_pno, []).append({
                        "text": token,
                        "x0": cursor,
                        "x1": cursor + tw,
                        "top": top,
                        "bottom": bottom,
                    })
                    cursor += ((len(token) + 1) / total_chars) * line_width
            except Exception:
                continue

    return page_words


def extract(
    pdf_path: Path,
    max_pages: int | None = None,
    court: str = "madras",
    progress_cb=None,
) -> List[Dict[str, Any]]:
    _cb = progress_cb or (lambda m: None)

    fitz_doc = fitz.open(str(pdf_path))
    total_pages = len(fitz_doc) if max_pages is None else min(len(fitz_doc), max_pages)

    # Seed page heights from fitz as fallback (overwritten per batch by Docling)
    page_heights: dict = {i + 1: fitz_doc[i].rect.height for i in range(total_pages)}

    try:
        _get_converter(progress_cb=_cb)
    except Exception as e:
        _cb(f"Docling: model load failed ({e}); cannot proceed.")
        fitz_doc.close()
        return []

    # ------------------------------------------------------------------ #
    # Batch through the PDF — Docling provides layout + text + coords     #
    # ------------------------------------------------------------------ #
    all_page_words: dict = {}
    batch_starts = list(range(0, total_pages, BATCH_SIZE))
    _cb(f"Docling: processing {total_pages} pages in batches of {BATCH_SIZE}...")

    for b_idx, start in enumerate(batch_starts):
        end = min(start + BATCH_SIZE, total_pages)
        _cb(f"Docling: layout + text — pages {start+1}–{end} (batch {b_idx+1}/{len(batch_starts)})...")
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            batch_writer = fitz.open()
            batch_writer.insert_pdf(fitz_doc, from_page=start, to_page=end - 1)
            batch_writer.save(tmp_path)
            batch_writer.close()

            batch_words = _docling_words_for_batch(tmp_path, start, page_heights, _cb)
            all_page_words.update(batch_words)

            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
        except (MemoryError, Exception) as e:
            err_str = str(e)
            if "bad_alloc" in err_str or isinstance(e, MemoryError):
                _cb(f"Docling: OOM on batch {b_idx+1} — skipping remaining pages.")
                break
            _cb(f"Docling: batch {b_idx+1} error ({err_str[:80]}) — skipping.")
        finally:
            gc.collect()

    fitz_doc.close()

    # ------------------------------------------------------------------ #
    # Parse each page's word dicts through the shared state machine       #
    # ------------------------------------------------------------------ #
    rows: List[Dict[str, Any]] = []
    for page_index in range(total_pages):
        page_num = page_index + 1
        _cb(f"Docling: parsing page {page_num} / {total_pages}...")
        word_dicts = all_page_words.get(page_num, [])
        page_rows = extract_page_from_words(
            word_dicts, page_num, court=court, coord_scale=1.0,
        )
        rows.extend(page_rows)

    return rows
