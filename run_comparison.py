"""
Quick benchmark: PyMuPDF approach on both courts.
Compares case counts and timing.
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "project"))

import os
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
os.environ.setdefault("FLAGS_json_format_model", "False")

import torch  # shm.dll fix
from extractors import pdf_structural
from structured_parser import parse_cause_list

TESTS = [
    {
        "label": "Madras HC (PyMuPDF)",
        "pdf": Path(__file__).parent / "madras" / "cause_02032026.pdf",
        "court": "madras",
        "extractor": pdf_structural.extract,
    },
    {
        "label": "Bombay HC (PyMuPDF)",
        "pdf": Path(__file__).parent / "bombay" / "entirecauselist.pdf",
        "court": "bombay",
        "extractor": pdf_structural.extract,
    },
]

SEP = "-" * 60

print(SEP)
print(f"{'Label':<35} {'Pages':>6} {'Cases':>8} {'Time':>8}")
print(SEP)

for t in TESTS:
    if not t["pdf"].exists():
        print(f"{t['label']:<35}  PDF not found, skipping")
        continue
    t0 = time.perf_counter()
    rows = t["extractor"](t["pdf"], court=t["court"])
    elapsed = time.perf_counter() - t0
    cases = parse_cause_list(rows, court=t["court"])

    import fitz
    doc = fitz.open(str(t["pdf"]))
    pages = len(doc)
    doc.close()

    print(f"{t['label']:<35} {pages:>6} {len(cases):>8} {elapsed:>7.2f}s")

print(SEP)
print("Done.")
