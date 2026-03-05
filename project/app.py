import inspect
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

# Suppress TensorFlow oneDNN verbose messages (printed by docling/surya deps)
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
# Fix HuggingFace Hub symlink WinError 1314 on Windows without Developer Mode:
# use file copies instead of symlinks when linking blobs into model snapshots.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from flask import Flask, Response, jsonify, render_template, request
from flask_compress import Compress

from extractors import pdf_structural
from structured_parser import parse_cause_list


app = Flask(__name__)
Compress(app)

METHOD_MAP = {
    "PyMuPDF Structural Parsing": pdf_structural.extract,
}


def _normalize_case_no(value: str) -> str:
    text = (value or "").upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_declared_case_total(pdf_path: Path) -> int | None:
    try:
        import fitz
    except Exception:
        return None

    patterns = [
        re.compile(r"TOTAL\s+(?:NO\.?\s+OF\s+)?CASES?\s*[:\-]?\s*(\d{2,6})", re.IGNORECASE),
        re.compile(r"TOTAL\s+MATTERS?\s*[:\-]?\s*(\d{2,6})", re.IGNORECASE),
        re.compile(r"NO\.?\s+OF\s+CASES?\s*[:\-]?\s*(\d{2,6})", re.IGNORECASE),
    ]

    declared = []
    try:
        doc = fitz.open(str(pdf_path))
        scan_pages = min(len(doc), 40)
        for i in range(scan_pages):
            text = doc[i].get_text("text") or ""
            for pat in patterns:
                for m in pat.finditer(text):
                    try:
                        n = int(m.group(1))
                    except Exception:
                        continue
                    if 10 <= n <= 200000:
                        declared.append(n)
        doc.close()
    except Exception:
        return None

    return max(declared) if declared else None


@app.errorhandler(Exception)
def handle_unexpected_error(exc: Exception):
    return jsonify({"error": f"Server error: {exc}"}), 500


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/extract")
def extract_route():
    if "file" not in request.files:
        return jsonify({"error": "No PDF uploaded"}), 400

    pdf_file = request.files["file"]
    method = request.form.get("method", "PyMuPDF Structural Parsing")
    court = request.form.get("court", "madras")
    max_pages_raw = request.form.get("max_pages", "").strip()

    if not pdf_file or not pdf_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a valid PDF file"}), 400

    if method not in METHOD_MAP:
        return jsonify({"error": "Invalid extraction method"}), 400

    max_pages = None
    if max_pages_raw:
        try:
            max_pages = int(max_pages_raw)
            if max_pages <= 0:
                return jsonify({"error": "max_pages must be a positive integer"}), 400
        except ValueError:
            return jsonify({"error": "max_pages must be an integer"}), 400

    effective_max_pages = max_pages

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        pdf_path = Path(tmp_dir) / pdf_file.filename
        pdf_file.save(pdf_path)
        declared_total = _extract_declared_case_total(pdf_path)

        extractor = METHOD_MAP[method]

        # PyMuPDF-only pipeline: read fitz text layer directly and apply
        # court-specific structural parsing rules.

        started = time.perf_counter()
        try:
            extractor_sig = inspect.signature(extractor)
            if "court" in extractor_sig.parameters:
                rows = extractor(pdf_path, max_pages=effective_max_pages, court=court)
            else:
                rows = extractor(pdf_path, max_pages=effective_max_pages)
        except Exception as exc:
            return jsonify({"error": f"Extraction failed for {method}: {exc}"}), 500
        elapsed = time.perf_counter() - started

    detected_case_numbers = [_normalize_case_no(str(row.get("case_no", ""))) for row in rows if row.get("case_no")]
    case_count = len(detected_case_numbers)
    unique_case_count = len(set(detected_case_numbers))
    structured_cases = parse_cause_list(rows, court=court, declared_total=declared_total)

    return jsonify(
        {
            "method": method,
            "court": court,
            "extraction_time": round(elapsed, 2),
            "number_of_rows": len(rows),
            "number_of_case_numbers_detected": case_count,
            "number_of_unique_case_numbers_detected": unique_case_count,
            "number_of_cases": len(structured_cases),
            "declared_total_cases": declared_total,
            "warning": None,
            "cases": structured_cases,
            "rows": rows,
        }
    )


# ---------------------------------------------------------------------------
# Live-streaming extraction endpoint  (SSE – text/event-stream)
# ---------------------------------------------------------------------------

class _QueueWriter:
    """Tees stdout/stderr to both the original stream and a progress queue.

    Handles tqdm-style \\r line overwriting so we emit clean progress lines
    even when the download progress bar refreshes in-place.
    """
    def __init__(self, q: queue.Queue, original):
        self._q = q
        self._original = original
        self._cur = ""

    def write(self, s: str) -> int:
        try:
            self._original.write(s)
            self._original.flush()
        except Exception:
            pass
        # Simulate a terminal: \r resets current line, \n flushes it
        for ch in s:
            if ch == "\r":
                self._cur = ""
            elif ch == "\n":
                line = self._cur.strip()
                if line:
                    self._q.put(("log", line))
                self._cur = ""
            else:
                self._cur += ch
        return len(s)

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        # Delegate fileno(), encoding, etc. to the wrapped stream
        return getattr(self._original, name)


@app.post("/extract-stream")
def extract_stream_route():
    """SSE endpoint: runs extraction in a thread, streams progress + result.

    The browser receives server-sent events while the extraction runs:
      {"type": "log",       "message": "..."}   – stdout/stderr/tqdm lines
      {"type": "progress",  "page": N, "total": M}  – per-page progress
      {"type": "heartbeat"}                         – keep-alive (every 30 s)
      {"type": "result",    "data":  {...}}          – final JSON on success
      {"type": "error",     "message": "..."}        – extraction failure
    """
    if "file" not in request.files:
        return jsonify({"error": "No PDF uploaded"}), 400
    pdf_file = request.files["file"]
    method    = request.form.get("method", "PyMuPDF Structural Parsing")
    court     = request.form.get("court", "madras")
    max_pages_raw = request.form.get("max_pages", "").strip()

    if not pdf_file or not pdf_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a valid PDF file"}), 400
    if method not in METHOD_MAP:
        return jsonify({"error": "Invalid extraction method"}), 400

    max_pages = None
    if max_pages_raw:
        try:
            max_pages = int(max_pages_raw)
            if max_pages <= 0:
                return jsonify({"error": "max_pages must be a positive integer"}), 400
        except ValueError:
            return jsonify({"error": "max_pages must be an integer"}), 400

    declared_total = None

    effective_max_pages = max_pages

    # Save PDF to a named temp dir (must survive after this function returns)
    tmp_dir  = Path(tempfile.gettempdir()) / f"causelist_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = tmp_dir / (pdf_file.filename or "upload.pdf")
    pdf_file.save(pdf_path)

    msg_queue: "queue.Queue[tuple]" = queue.Queue()
    result_holder: dict = {}

    def run():
        # Capture stdout + stderr (includes tqdm download bars) into the queue.
        # NOTE: sys.stdout/stderr are global; this is safe only because the dev
        # server runs one extraction at a time.  Both streams are also teed to
        # the original so Flask's own log output is preserved.
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _QueueWriter(msg_queue, old_out)
        sys.stderr = _QueueWriter(msg_queue, old_err)
        try:
            extractor = METHOD_MAP[method]
            sig = inspect.signature(extractor)
            kwargs: dict = {"max_pages": effective_max_pages}
            if "court"       in sig.parameters: kwargs["court"]       = court
            if "progress_cb" in sig.parameters:
                kwargs["progress_cb"] = lambda msg: msg_queue.put(("log", msg))

            local_declared_total = _extract_declared_case_total(pdf_path)

            started = time.perf_counter()
            rows    = extractor(pdf_path, **kwargs)
            elapsed = time.perf_counter() - started

            detected      = [_normalize_case_no(str(r.get("case_no", ""))) for r in rows if r.get("case_no")]
            case_count     = len(detected)
            unique_count   = len(set(detected))
            structured     = parse_cause_list(rows, court=court, declared_total=local_declared_total)

            result_holder["data"] = {
                "method":                               method,
                "court":                                court,
                "extraction_time":                      round(elapsed, 2),
                "number_of_rows":                       len(rows),
                "number_of_case_numbers_detected":      case_count,
                "number_of_unique_case_numbers_detected": unique_count,
                "number_of_cases":                      len(structured),
                "declared_total_cases":                local_declared_total,
                "warning":                              None,
                "cases":                                structured,
                # rows (raw extractor output) intentionally excluded from the
                # SSE payload — serialising 5000+ dicts as a single SSE event
                # causes multi-second hangs and browser choke on large PDFs.
                # The Download JSON button uses `cases` which is the clean output.
            }
        except Exception as exc:
            result_holder["error"] = str(exc)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            try:
                import shutil; shutil.rmtree(str(tmp_dir), ignore_errors=True)
            except Exception:
                pass
            msg_queue.put(("done", None))

    threading.Thread(target=run, daemon=True).start()

    def generate():
        while True:
            try:
                typ, val = msg_queue.get(timeout=30)
            except queue.Empty:
                yield 'data: {"type":"heartbeat"}\n\n'
                continue
            if typ == "log":
                yield f"data: {json.dumps({'type': 'log', 'message': val})}\n\n"
            elif typ == "done":
                if "error" in result_holder:
                    payload = json.dumps({"type": "error", "message": result_holder["error"]})
                else:
                    payload = json.dumps({"type": "result", "data": result_holder["data"]})
                yield f"data: {payload}\n\n"
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    app.run(debug=True)
