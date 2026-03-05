import argparse
import json
import math
import os
import statistics
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class BenchmarkConfig:
    pdf_path: Path
    output_dir: Path
    max_pages: Optional[int]
    dpi: int


class PdfImageRenderer:
    def __init__(self, pdf_path: Path, dpi: int):
        self.pdf = pdfium.PdfDocument(str(pdf_path))
        self.scale = dpi / 72.0

    def render_bgr(self, page_index: int) -> np.ndarray:
        page = self.pdf[page_index]
        bitmap = page.render(scale=self.scale)
        image = bitmap.to_numpy()
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        return image


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_row_text(parts: List[str]) -> str:
    joined = " ".join(p for p in parts if p)
    return " ".join(joined.split())


def alnum_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(ch.isalnum() or ch.isspace() for ch in text)
    return good / len(text)


def page_text_density(text: str) -> float:
    return len(text.strip())


def quality_metrics(entries: List[Dict[str, Any]], total_pages: int, runtime_s: float, approach: str) -> Dict[str, Any]:
    if not entries:
        return {
            "approach": approach,
            "runtime_seconds": round(runtime_s, 2),
            "seconds_per_page": round(runtime_s / max(total_pages, 1), 3),
            "rows": 0,
            "garbage_ratio": 1.0,
            "avg_text_len": 0,
            "quality_score": 0.0,
        }

    texts = [safe_text(e.get("raw_text", "")) for e in entries]
    lengths = [len(t) for t in texts]
    good = [t for t in texts if len(t) >= 12 and alnum_ratio(t) >= 0.65]
    garbage_ratio = 1 - (len(good) / len(texts))
    avg_len = statistics.mean(lengths)

    score = (1 - garbage_ratio) * 0.6 + min(avg_len / 80.0, 1.0) * 0.25 + max(0.0, 1.0 - (runtime_s / (max(total_pages, 1) * 2.5))) * 0.15

    return {
        "approach": approach,
        "runtime_seconds": round(runtime_s, 2),
        "seconds_per_page": round(runtime_s / max(total_pages, 1), 3),
        "rows": len(entries),
        "garbage_ratio": round(garbage_ratio, 4),
        "avg_text_len": round(avg_len, 2),
        "quality_score": round(score, 4),
    }


def extract_rows_from_pdfplumber_page(words: List[Dict[str, Any]], page_num: int) -> List[Dict[str, Any]]:
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    row_tol = 4.0

    for word in words_sorted:
        if not current:
            current = [word]
            continue
        if abs(word["top"] - current[-1]["top"]) <= row_tol:
            current.append(word)
        else:
            rows.append(current)
            current = [word]
    if current:
        rows.append(current)

    out: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        row_words = sorted(row, key=lambda w: w["x0"])
        text = normalize_row_text([safe_text(w.get("text", "")) for w in row_words])
        if len(text) < 6:
            continue
        x1 = min(float(w["x0"]) for w in row_words)
        y1 = min(float(w["top"]) for w in row_words)
        x2 = max(float(w["x1"]) for w in row_words)
        y2 = max(float(w["bottom"]) for w in row_words)
        out.append(
            {
                "page": page_num,
                "row_id": f"{page_num}-{i}",
                "item_no": None,
                "case_no": None,
                "party": None,
                "stage": None,
                "raw_text": text,
                "geometry": {"x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2)},
                "confidence": 0.95,
            }
        )
    return out


def extract_rows_from_tesseract_data(data: Dict[str, List[Any]], page_num: int) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[int, int, int], List[int]] = {}
    n = len(data.get("text", []))
    for i in range(n):
        txt = safe_text(data["text"][i])
        conf = float(data["conf"][i]) if str(data["conf"][i]).strip() not in {"", "-1"} else -1.0
        if not txt or conf < 30:
            continue
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        groups.setdefault(key, []).append(i)

    rows = []
    for idx, (key, ids) in enumerate(sorted(groups.items()), start=1):
        ids_sorted = sorted(ids, key=lambda j: int(data["left"][j]))
        words = [safe_text(data["text"][j]) for j in ids_sorted]
        text = normalize_row_text(words)
        if len(text) < 6:
            continue
        conf_vals = [float(data["conf"][j]) for j in ids_sorted]
        lefts = [int(data["left"][j]) for j in ids_sorted]
        tops = [int(data["top"][j]) for j in ids_sorted]
        widths = [int(data["width"][j]) for j in ids_sorted]
        heights = [int(data["height"][j]) for j in ids_sorted]
        rows.append(
            {
                "page": page_num,
                "row_id": f"{page_num}-{idx}",
                "item_no": None,
                "case_no": None,
                "party": None,
                "stage": None,
                "raw_text": text,
                "geometry": {
                    "x1": int(min(lefts)),
                    "y1": int(min(tops)),
                    "x2": int(max(l + w for l, w in zip(lefts, widths))),
                    "y2": int(max(t + h for t, h in zip(tops, heights))),
                },
                "confidence": round(float(sum(conf_vals) / max(len(conf_vals), 1) / 100.0), 3),
            }
        )
    return rows


def run_approach_1_layout_aware(cfg: BenchmarkConfig, pages_to_process: int) -> Dict[str, Any]:
    import pytesseract

    t0 = time.perf_counter()
    renderer = PdfImageRenderer(cfg.pdf_path, cfg.dpi)
    entries: List[Dict[str, Any]] = []

    with pdfplumber.open(str(cfg.pdf_path)) as pdf:
        for i in range(pages_to_process):
            page_num = i + 1
            page = pdf.pages[i]
            words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            page_text = page.extract_text() or ""

            if len(words) >= 20 and page_text_density(page_text) > 200:
                rows = extract_rows_from_pdfplumber_page(words, page_num)
                entries.extend(rows)
            else:
                image = renderer.render_bgr(i)
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
                rows = extract_rows_from_tesseract_data(data, page_num)
                entries.extend(rows)

    runtime = time.perf_counter() - t0
    report = quality_metrics(entries, pages_to_process, runtime, "approach_1_layout_aware")
    return {"report": report, "entries": entries}


def normalize_paddle_structure_result(result_item: Any, page_num: int) -> List[Dict[str, Any]]:
    rows_out: List[Dict[str, Any]] = []
    if isinstance(result_item, dict):
        cell_boxes = result_item.get("bbox", []) or []
    else:
        try:
            item_dict = dict(result_item)
            cell_boxes = item_dict.get("bbox", []) or []
        except Exception:
            cell_boxes = []

    idx = 1
    for polygon in cell_boxes:
        if not isinstance(polygon, list) or len(polygon) < 8:
            continue
        xs = [float(polygon[j]) for j in range(0, 8, 2)]
        ys = [float(polygon[j]) for j in range(1, 8, 2)]
        rows_out.append(
            {
                "page": page_num,
                "row_id": f"{page_num}-{idx}",
                "item_no": None,
                "case_no": None,
                "party": None,
                "stage": None,
                "raw_text": "",
                "geometry": {"x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)},
                "confidence": 0.9,
            }
        )
        idx += 1
    return rows_out


def attach_text_to_cells_with_tesseract(page_image: np.ndarray, cell_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    import pytesseract

    if not cell_entries:
        return []

    gray = cv2.cvtColor(page_image, cv2.COLOR_BGR2GRAY)
    data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
    n = len(data.get("text", []))

    tokens = []
    for i in range(n):
        txt = safe_text(data["text"][i])
        conf = float(data["conf"][i]) if str(data["conf"][i]).strip() not in {"", "-1"} else -1.0
        if not txt or conf < 25:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        cx = left + width / 2
        cy = top + height / 2
        tokens.append((txt, conf, cx, cy, left))

    out = []
    for entry in cell_entries:
        g = entry["geometry"]
        inside = [t for t in tokens if g["x1"] <= t[2] <= g["x2"] and g["y1"] <= t[3] <= g["y2"]]
        if not inside:
            continue
        inside_sorted = sorted(inside, key=lambda t: t[4])
        text = normalize_row_text([t[0] for t in inside_sorted])
        if len(text) < 4:
            continue
        conf = sum(t[1] for t in inside_sorted) / (100.0 * len(inside_sorted))
        new_entry = dict(entry)
        new_entry["raw_text"] = text
        new_entry["confidence"] = round(conf, 3)
        out.append(new_entry)

    return out


def run_approach_2_paddle(cfg: BenchmarkConfig, pages_to_process: int) -> Dict[str, Any]:
    from paddleocr import TableStructureRecognition

    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    t0 = time.perf_counter()
    renderer = PdfImageRenderer(cfg.pdf_path, cfg.dpi)
    entries: List[Dict[str, Any]] = []

    pipeline = TableStructureRecognition()

    for i in range(pages_to_process):
        page_num = i + 1
        if page_num % 25 == 0:
            print(f"[approach_2] processed {page_num}/{pages_to_process} pages")
        image = renderer.render_bgr(i)
        result = list(pipeline.predict(image))
        page_entries: List[Dict[str, Any]] = []
        for item in result:
            page_entries.extend(normalize_paddle_structure_result(item, page_num))

        page_entries = attach_text_to_cells_with_tesseract(image, page_entries)

        if not page_entries:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            text = ""
            try:
                import pytesseract

                text = safe_text(pytesseract.image_to_string(gray))
            except Exception:
                text = ""
            if len(text) >= 6:
                page_entries = [
                    {
                        "page": page_num,
                        "row_id": f"{page_num}-1",
                        "item_no": None,
                        "case_no": None,
                        "party": None,
                        "stage": None,
                        "raw_text": " ".join(text.split()),
                        "geometry": {"x1": 0, "y1": 0, "x2": int(image.shape[1]), "y2": int(image.shape[0])},
                        "confidence": 0.6,
                    }
                ]

        entries.extend(page_entries)

    runtime = time.perf_counter() - t0
    report = quality_metrics(entries, pages_to_process, runtime, "approach_2_paddle_structured")
    return {"report": report, "entries": entries}


def run_approach_3_pymupdf(cfg: BenchmarkConfig, pages_to_process: int) -> Dict[str, Any]:
    t0 = time.perf_counter()
    entries: List[Dict[str, Any]] = []

    doc = fitz.open(str(cfg.pdf_path))
    for i in range(pages_to_process):
        page_num = i + 1
        if page_num % 25 == 0:
            print(f"[approach_3] processed {page_num}/{pages_to_process} pages")

        page = doc[i]
        words = page.get_text("words") or []
        page_words = [
            {
                "text": str(w[4]),
                "x0": float(w[0]),
                "x1": float(w[2]),
                "top": float(w[1]),
                "bottom": float(w[3]),
            }
            for w in words
            if len(w) >= 5 and str(w[4]).strip()
        ]
        rows = extract_rows_from_pdfplumber_page(page_words, page_num)
        entries.extend(rows)

    doc.close()
    runtime = time.perf_counter() - t0
    report = quality_metrics(entries, pages_to_process, runtime, "approach_3_pymupdf_structural")
    return {"report": report, "entries": entries}


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_text_export(path: Path, entries: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            page = e.get("page")
            row_id = e.get("row_id")
            text = safe_text(e.get("raw_text", ""))
            if not text:
                continue
            f.write(f"{page}\t{row_id}\t{text}\n")


def run_benchmarks(
    cfg: BenchmarkConfig,
    selected_approaches: Optional[List[str]] = None,
    run_id_override: Optional[str] = None,
) -> Dict[str, Any]:
    reader = PdfReader(str(cfg.pdf_path))
    total_pages = len(reader.pages)
    pages_to_process = min(total_pages, cfg.max_pages) if cfg.max_pages else total_pages

    run_id = run_id_override or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_dir = cfg.output_dir / f"benchmark_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "run_id": run_id,
        "started_at": now_iso(),
        "pdf": str(cfg.pdf_path),
        "total_pages": total_pages,
        "pages_processed": pages_to_process,
        "dpi": cfg.dpi,
    }
    meta_path = run_dir / "meta.json"
    if meta_path.exists() and run_id_override:
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                existing_meta = json.load(f)
            meta = dict(existing_meta)
            meta["last_retry_at"] = now_iso()
            meta["retry_pages_processed"] = pages_to_process
            meta["dpi"] = cfg.dpi
        except Exception:
            pass
    write_json(meta_path, meta)

    results: Dict[str, Dict[str, Any]] = {}

    approach_map = {
        "approach_1_layout_aware": run_approach_1_layout_aware,
        "approach_2_paddle_structured": run_approach_2_paddle,
        "approach_3_pymupdf_structural": run_approach_3_pymupdf,
    }
    all_approaches = list(approach_map.keys())
    approaches_to_run = selected_approaches or all_approaches

    for name in approaches_to_run:
        fn = approach_map[name]
        started = time.perf_counter()
        try:
            out = fn(cfg, pages_to_process)
            status = "ok"
            error = None
        except Exception as exc:
            out = {"report": quality_metrics([], pages_to_process, 0.0, name), "entries": []}
            status = "failed"
            error = repr(exc)
        elapsed = time.perf_counter() - started

        report = out["report"]
        report["status"] = status
        report["error"] = error
        report["total_elapsed_seconds"] = round(elapsed, 2)

        payload = {
            "metadata": {
                "approach": name,
                "status": status,
                "error": error,
                "pages_processed": pages_to_process,
                "generated_at": now_iso(),
            },
            "report": report,
            "entries": out["entries"],
        }
        write_json(run_dir / f"{name}.json", payload)
        write_text_export(run_dir / f"{name}.txt", out["entries"])
        results[name] = report

    # Merge any existing approach reports when rerunning selected approaches
    for approach_name in all_approaches:
        if approach_name in results:
            continue
        existing_file = run_dir / f"{approach_name}.json"
        if existing_file.exists():
            try:
                with existing_file.open("r", encoding="utf-8") as f:
                    existing_payload = json.load(f)
                report = existing_payload.get("report")
                if isinstance(report, dict):
                    results[approach_name] = report
            except Exception:
                continue

    ranking = sorted(
        results.values(),
        key=lambda r: (r.get("status") != "ok", -r.get("quality_score", 0.0), r.get("seconds_per_page", 9999)),
    )
    summary = {
        "meta": meta,
        "results": results,
        "recommended": ranking[0] if ranking else None,
        "finished_at": now_iso(),
    }
    write_json(run_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark 3 cause-list extraction approaches")
    parser.add_argument("--pdf", required=True, help="Path to input PDF")
    parser.add_argument("--output", default="benchmark_outputs", help="Output directory")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional page cap for smoke test")
    parser.add_argument("--dpi", type=int, default=140, help="Rendering DPI for OCR approaches")
    parser.add_argument(
        "--approaches",
        default="all",
        help="Comma-separated approaches to run: all,approach_1_layout_aware,approach_2_paddle_structured,approach_3_pymupdf_structural",
    )
    parser.add_argument("--run-id", default=None, help="Reuse existing run id folder for retry")
    args = parser.parse_args()

    cfg = BenchmarkConfig(
        pdf_path=Path(args.pdf).resolve(),
        output_dir=Path(args.output).resolve(),
        max_pages=args.max_pages,
        dpi=args.dpi,
    )
    valid = {"approach_1_layout_aware", "approach_2_paddle_structured", "approach_3_pymupdf_structural"}
    selected_approaches = None
    if args.approaches and args.approaches.lower() != "all":
        selected_approaches = [x.strip() for x in args.approaches.split(",") if x.strip()]
        invalid = [x for x in selected_approaches if x not in valid]
        if invalid:
            raise ValueError(f"Invalid approach names: {invalid}")

    summary = run_benchmarks(cfg, selected_approaches=selected_approaches, run_id_override=args.run_id)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
