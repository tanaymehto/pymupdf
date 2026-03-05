import re


def _normalize_case_no(value: str) -> str:
    text = (value or "").upper().strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"/{2,}", "/", text)
    return text.strip("/")


def _merge_case_rows(existing, incoming):
    # Prefer richer metadata while keeping deterministic output.
    for key in ("petitioner", "respondent", "advocates", "serial", "case_type"):
        if (not existing.get(key) or existing.get(key) == "Not available in source") and incoming.get(key):
            existing[key] = incoming[key]
    if existing.get("page", 0) == 0 and incoming.get("page", 0):
        existing["page"] = incoming["page"]
    return existing


def _row_quality(case: dict) -> int:
    score = 0
    if str(case.get("serial") or "").strip():
        score += 2
    if str(case.get("petitioner") or "").strip() and case.get("petitioner") != "Not available in source":
        score += 1
    if str(case.get("respondent") or "").strip() and case.get("respondent") != "Not available in source":
        score += 1
    if str(case.get("advocates") or "").strip() and case.get("advocates") != "Not available in source":
        score += 1
    return score



def _build_output(case):
    """Normalise one extractor row into the 5 clean fields the UI expects."""
    return {
        "serial": str(case.get("serial") or ""),
        "page": int(case.get("page") or 0),
        "case_no": str(case.get("case_no") or ""),
        "case_type": str(case.get("case_type") or ""),
        "petitioner": str(case.get("petitioner") or "") or "Not available in source",
        "respondent": str(case.get("respondent") or "") or "Not available in source",
        "advocates": str(case.get("advocates") or "") or "Not available in source",
    }


def parse_cause_list(rows, court="madras", declared_total=None):
    """Convert raw extractor rows into clean structured dicts for the API."""
    structured = []
    by_case_no = {}
    quality_by_case = {}
    for case in rows:
        if not isinstance(case, dict):
            continue
        raw_cn = _normalize_case_no(str(case.get("case_no") or ""))
        if not raw_cn:
            continue
        case["case_type"] = raw_cn.split("/")[0].strip()
        case["case_no"] = raw_cn
        out = _build_output(case)
        quality_by_case[raw_cn] = max(quality_by_case.get(raw_cn, 0), _row_quality(out))
        if raw_cn in by_case_no:
            by_case_no[raw_cn] = _merge_case_rows(by_case_no[raw_cn], out)
        else:
            by_case_no[raw_cn] = out
            structured.append(by_case_no[raw_cn])

    if isinstance(declared_total, int) and declared_total > 0 and len(structured) > declared_total:
        overflow = len(structured) - declared_total
        ranked = sorted(
            enumerate(structured),
            key=lambda item: (
                quality_by_case.get(str(item[1].get("case_no") or ""), 0),
                int(item[1].get("page") or 0),
                item[0],
            ),
        )
        drop_indices = {idx for idx, _ in ranked[:overflow]}
        structured = [c for i, c in enumerate(structured) if i not in drop_indices]

    return structured

