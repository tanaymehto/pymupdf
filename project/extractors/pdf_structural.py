import re
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz

# Add project root to sys.path for sibling imports
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ---------------------------------------------------------------------------
# Bombay multiline state-machine extractor
# ---------------------------------------------------------------------------
_BOMBAY_CASE_PAT = re.compile(
    r"^\s*([A-Z][A-Z./()\- ]{0,24})\s*/?\s*(\d{1,7})\s*/\s*((?:19|20)\d{2})\s*$",
    re.IGNORECASE,
)
_BOMBAY_CASE_INLINE = re.compile(
    r"\b([A-Z][A-Z./()\- ]{0,24})\s*/?\s*(\d{1,7})\s*/\s*((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_BOMBAY_SERIAL_CASE_PAT = re.compile(
    r"^\s*(\d{1,4})[.)]?\s+([A-Z][A-Z./()\- ]{0,24})\s*/?\s*(\d{1,7})\s*/\s*((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_SERIAL_PAT = re.compile(r"^\d{1,4}[.)]?$")
_VS_PAT = re.compile(r"^\s*VS\s*$", re.IGNORECASE)
_REMARK_PAT = re.compile(r"^\s*REMARK\s*[:\-]", re.IGNORECASE)
_TIMESTAMP_PAT = re.compile(r"\d{1,2}/\d{1,2}/\d{4}|\d{2}:\d{2}:\d{2}|\d+/\d+$")
# Match a whole line that is only a date/time/page stamp, e.g. "01/03/2026 19:42:49 1/20"
_TIMESTAMP_LINE_PAT = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{2}:\d{2}:\d{2})?(?:\s+\d+/\d+)?\s*$"
    r"|^\d{2}:\d{2}:\d{2}\s*$"
    r"|^\d+/\d+\s*$"
)
# Strip trailing timestamp/page-number suffixes from a line (inline removal)
_TIMESTAMP_STRIP = re.compile(
    r"\s+\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{2}:\d{2}:\d{2})?(?:\s+\d+/\d+)?\s*$"
    r"|\s+\d{2}:\d{2}:\d{2}(?:\s+\d+/\d+)?\s*$"
    r"|\s+\d+/\d+\s*$"
)
# Lines that look like remark/order continuations (not party names)
_REMARK_CONT_PAT = re.compile(
    r"^\s*(?::-|\d+\)|\d+\.\s|per\s+|vide\s+|order\s+dated|dated\s+|\(as per|cross objection)",
    re.IGNORECASE,
)
_SKIP_LINE_PAT = re.compile(
    r"CAUSELIST|HIGHCOURT|BOMBAY HIGH|COURT NO\b|FOR WEDNESDAY|FOR MONDAY|"
    r"FOR TUESDAY|FOR THURSDAY|FOR FRIDAY|FOR SATURDAY|^\s*-\s*\d+\s*-\s*$",
    re.IGNORECASE,
)
_BRACKET_PAT = re.compile(r"^\[(?:Civil|Criminal|Labour|Company|Const)\]$", re.IGNORECASE)
_WITH_PAT = re.compile(r"^\s*(?:with|in)\s*$", re.IGNORECASE)
_FOR_PARTY_PAT = re.compile(r"^\s*FOR\s+(?:PETITIONER|RESPONDENT|APPELLANT|R\.?\s*NO)", re.IGNORECASE)
_ADVOCATE_SUFFIX = re.compile(r"\b(ADV|ADVOCATE|COUNSEL|VAKALATNAMA|CHAMBERS)\b", re.IGNORECASE)
_DULY_SERVED = re.compile(r"DULY SERVED|R\.NO\.\s*\d|RESP\.\s*NO\.", re.IGNORECASE)
_BOMBAY_NOISE_PAT = re.compile(
    r"dismissed|as per|order dated|court.?s order|connected|note\s*:|remark\s*:|for orders",
    re.IGNORECASE,
)
_BOMBAY_SUPPLEMENT_INLINE_TYPES = {
    "IA",
    "CAF",
    "B/WP",
    "BWP",
    "IAST",
    "IA(ST)",
    "XOB/ST",
    "XOBST",
    "XOB(ST)",
}


def _normalize_case_type(raw_type: str) -> str:
    text = (raw_type or "").upper().strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace(".", "")
    text = text.replace("-", "")
    text = re.sub(r"/{2,}", "/", text)
    text = text.strip("/")
    return text


def _normalize_serial(raw_serial: str) -> str:
    return re.sub(r"[.)]$", "", (raw_serial or "").strip())


def _is_valid_bombay_case_type(raw_type: str) -> bool:
    case_type = _normalize_case_type(raw_type)
    if len(case_type) > 18:
        return False
    if "//" in case_type:
        return False
    if case_type in {"NO", "NOTE", "REMARK", "DATED"}:
        return False
    if case_type.endswith("NO"):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z/()]{0,17}", case_type))


def _extract_bombay_case_from_line(line: str, allow_inline: bool = True) -> tuple[str | None, str | None]:
    line = (line or "").strip()
    if not line:
        return None, None

    m_sc = _BOMBAY_SERIAL_CASE_PAT.match(line)
    if m_sc and _is_valid_bombay_case_type(m_sc.group(2)):
        serial = m_sc.group(1).strip()
        case_no = f"{_normalize_case_type(m_sc.group(2))}/{m_sc.group(3)}/{m_sc.group(4)}"
        return serial, case_no

    m = _BOMBAY_CASE_PAT.match(line)
    if not m and allow_inline and len(line) <= 90:
        m = _BOMBAY_CASE_INLINE.search(line)
    if m and _is_valid_bombay_case_type(m.group(1)):
        case_no = f"{_normalize_case_type(m.group(1))}/{m.group(2)}/{m.group(3)}"
        return None, case_no

    return None, None


def _extract_all_bombay_inline_cases(line: str) -> List[str]:
    out: List[str] = []
    text = (line or "").strip()
    if not text:
        return out
    for m in _BOMBAY_CASE_INLINE.finditer(text):
        case_type = _normalize_case_type(m.group(1))
        if not _is_valid_bombay_case_type(case_type):
            continue
        out.append(f"{case_type}/{m.group(2)}/{m.group(3)}")
    return out


def _supplement_bombay_cases_from_lines(
    all_lines: List[tuple],
    page_num: int,
    existing_case_nos: set[str],
) -> List[Dict[str, Any]]:
    extras: List[Dict[str, Any]] = []
    seen = set(existing_case_nos)

    for _x0, line in all_lines:
        text = (line or "").strip()
        if not text:
            continue
        if _BOMBAY_NOISE_PAT.search(text):
            continue

        serial_hit, case_hit = _extract_bombay_case_from_line(text, allow_inline=False)
        if not case_hit and len(text) <= 48:
            serial_hit, case_hit = _extract_bombay_case_from_line(text, allow_inline=True)

        candidates: List[str] = []
        if case_hit:
            candidates.append(case_hit)

        # Capture additional inline companion cases from longer lines for
        # known Bombay companion families (IA/CAF/XOB/B-WP forms).
        if len(text) > 48:
            for c in _extract_all_bombay_inline_cases(text):
                c_type = c.split("/")[0]
                if c_type in _BOMBAY_SUPPLEMENT_INLINE_TYPES:
                    candidates.append(c)

        for case_no in candidates:
            if case_no in seen:
                continue
            seen.add(case_no)
            extras.append(
                {
                    "page": page_num,
                    "row_id": f"{page_num}-x{len(extras)+1}",
                    "case_no": case_no,
                    "serial": serial_hit or "",
                    "petitioner": "",
                    "respondent": "",
                    "advocates": "",
                    "raw_text": case_no,
                    "party": "",
                    "stage": "",
                    "geometry": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
                    "confidence": 0.7,
                    "_pre_parsed": True,
                }
            )

    return extras


def _clean_party(lines: List[str]) -> str:
    cleaned = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if _REMARK_PAT.match(ln):
            break
        # Skip remark continuation lines (start with :-, 1), etc.)
        if _REMARK_CONT_PAT.match(ln):
            break
        # Strip generic service notes and timestamps
        if _DULY_SERVED.search(ln) and len(ln) < 40:
            continue
        # Skip lines that are just dates/times/page numbers
        if _TIMESTAMP_LINE_PAT.match(ln) or re.fullmatch(r"[\d/: ]+", ln):
            continue
        # Strip inline trailing timestamp / page-number suffixes
        ln = _TIMESTAMP_STRIP.sub("", ln).strip()
        if ln:
            cleaned.append(ln)
    return " ".join(cleaned).strip(" ,.-")


def _extract_bombay_page(page_obj: fitz.Page, page_num: int) -> List[Dict[str, Any]]:
    """State-machine parser for Bombay High Court multi-line vertical layout.

    Each case block looks like:
        [SERIAL]
        TYPE/NUMBER/YEAR
        [Civil]
        PETITIONER LINE 1
        PETITIONER LINE 2
        VS
        RESPONDENT LINE 1
        ADVOCATE NAME
        FOR RESPONDENT NO. X
        REMARK: ...  (skip everything until next case)
    """
    # Use blocks to preserve left vs right column separation
    blocks = page_obj.get_text("blocks") or []
    # block = (x0, y0, x1, y1, text, block_no, block_type)
    text_blocks = sorted(
        [(float(b[0]), float(b[1]), b[4]) for b in blocks if b[6] == 0],
        key=lambda b: (b[1], b[0]),
    )

    # Flatten blocks into (x0, line_text) pairs
    all_lines: List[tuple] = []
    for x0, _y0, text in text_blocks:
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                all_lines.append((x0, stripped))

    cases: List[Dict[str, Any]] = []
    serial: Optional[str] = None
    case_no: Optional[str] = None
    petitioner_parts: List[str] = []
    respondent_parts: List[str] = []
    advocate_parts: List[str] = []
    state = "LOOKING"  # LOOKING | PETITIONER | RESPONDENT | SKIP

    def _flush():
        nonlocal case_no, serial
        if case_no:
            pet_text = _clean_party(petitioner_parts)
            resp_text = _clean_party(respondent_parts)
            adv_text = " | ".join(a.strip() for a in advocate_parts if a.strip())
            cases.append(
                {
                    "page": page_num,
                    "row_id": f"{page_num}-{len(cases)+1}",
                    "case_no": case_no,
                    "serial": serial or "",
                    "petitioner": pet_text,
                    "respondent": resp_text,
                    "advocates": adv_text,
                    "raw_text": f"{case_no} {pet_text} VS {resp_text}",
                    "party": pet_text,
                    "stage": adv_text,
                    "geometry": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
                    "confidence": 0.9,
                    "_pre_parsed": True,
                }
            )
        case_no = None
        petitioner_parts.clear()
        respondent_parts.clear()
        advocate_parts.clear()

    for x0, line in all_lines:
        # Header / footer — skip
        if _SKIP_LINE_PAT.search(line):
            continue

        # REMARK start → flush current case, enter SKIP
        if _REMARK_PAT.match(line):
            state = "SKIP"
            continue

        if state == "SKIP":
            serial_hit, case_hit = _extract_bombay_case_from_line(line, allow_inline=False)
            if serial_hit and case_hit:
                state = "PETITIONER"
                serial = serial_hit
                case_no = case_hit
                continue
            if case_hit:
                state = "PETITIONER"
                case_no = case_hit
                continue
            if _SERIAL_PAT.match(line):
                state = "LOOKING"
                serial = _normalize_serial(line)
            continue

        # Ignore [Civil], WITH, IN tags
        if _BRACKET_PAT.match(line) or _WITH_PAT.match(line):
            continue

        # Serial number
        if _SERIAL_PAT.match(line):
            serial = _normalize_serial(line)
            continue

        # Case number — new case begins (supports serial+case on same line)
        serial_hit, case_hit = _extract_bombay_case_from_line(line, allow_inline=(state == "LOOKING"))
        if case_hit:
            _flush()
            if serial_hit:
                serial = serial_hit
            case_no = case_hit
            state = "PETITIONER"
            continue

        if state == "LOOKING":
            continue

        if state == "PETITIONER":
            if _VS_PAT.match(line):
                state = "RESPONDENT"
            elif _REMARK_CONT_PAT.match(line):
                # Looks like remark/order continuation text — skip this pseudo-case
                state = "SKIP"
            elif _FOR_PARTY_PAT.match(line):
                advocate_parts.append(line)
            elif x0 > 280 and len(line) <= 60 and not _DULY_SERVED.search(line):
                # Right column — likely advocate name while petitioner is being built
                advocate_parts.append(line)
            else:
                petitioner_parts.append(line)

        elif state == "RESPONDENT":
            if _VS_PAT.match(line):
                # Double VS — ignore
                pass
            elif _FOR_PARTY_PAT.match(line):
                advocate_parts.append(line)
            elif x0 > 280 and len(line) <= 60 and not _DULY_SERVED.search(line):
                advocate_parts.append(line)
            elif _ADVOCATE_SUFFIX.search(line) and len(line) < 80:
                advocate_parts.append(line)
            else:
                respondent_parts.append(line)

    _flush()
    existing = {str(c.get("case_no") or "") for c in cases if c.get("case_no")}
    cases.extend(_supplement_bombay_cases_from_lines(all_lines, page_num, existing))
    return cases


# ---------------------------------------------------------------------------
# Madras column-aware state-machine extractor
# ---------------------------------------------------------------------------
#
# Madras PDF column layout (from actual word-position analysis):
#   x0 <  65  → serial number (e.g. "36", "37")
#   65 ≤ x0 < 200  → case number column (e.g. "CMP/5191/2026", "WMP 6722/2026")
#   200 ≤ x0 < 382 → party column  (petitioner lines, then "VS", then respondent)
#   x0 ≥ 382 → advocate column
#
_M_SERIAL_MAX = 65
_M_CASE_MIN = 65
_M_CASE_MAX = 200
_M_PARTY_MIN = 200
_M_PARTY_MAX = 382
_M_ADV_MIN = 382

# Case number pattern for the case-number column.
# Handles formats like:
#   CMP/5191/2026, WMP 6722/2026, WA/5/2024, CRL A/245/2026,
#   CONT P/109/2026, CROS.OBJ 34/2021, CRP(MD) 333/2022.
_MADRAS_CASE_PAT = re.compile(
    r"^([A-Z][A-Z.()\-]{0,12}(?:\s+[A-Z][A-Z.()\-]{0,12}){0,2})"
    r"(?:\s+NO\.?)?[\s/]+(\d{1,7})\s*/\s*(\d{4})\b",
    re.IGNORECASE,
)
_M_AND_PAT = re.compile(r"^AND$", re.IGNORECASE)
_M_VS_PAT = re.compile(r"^VS\.?$", re.IGNORECASE)
_M_SERIAL_PAT = re.compile(r"^\d{1,4}$")
_M_DASH_PAT = re.compile(r"^-{3,}$")
_M_FILING_PAT = re.compile(r"\(Filing\s+No\.?\)", re.IGNORECASE)
# Skip page headers / separators (whole-line match)
_M_SKIP_PAT = re.compile(
    r"CAUSE\s*LIST\b|MADRAS\s*HIGH\s*COURT|APPELLATE\s*SIDE\b|ORIGINAL\s*SIDE\b|"
    r"HON\.?BLE\b.*JUSTICE|BENCH\b|COURT\s*NO\.\s*[A-Z0-9]|"
    r"^\s*S\.?\s*NO\.?\s*$|^\s*CASE\s+NO\.?\s*$|"
    r"^\s*PETITIONER\s*$|^\s*RESPONDENT\s*$|^\s*ADVOCATE\s*$|"
    r"^\s*-+\s*\d+\s*-+\s*$",
    re.IGNORECASE,
)

# Accept only clean case-column tails after the matched case number.
# This prevents false positives when party text bleeds into the case column.
_M_CASE_TAIL_OK = re.compile(
    r"^(?:$|[\-:.,;()\[\] ]+$|\(FILING\s*NO\.?\)?|AND\b|WITH\b)",
    re.IGNORECASE,
)


def _match_madras_case_text(case_txt: str, serial_on_line: bool = False):
    m = _MADRAS_CASE_PAT.match(case_txt or "")
    if not m:
        return None
    tail = (case_txt[m.end():] if case_txt else "").strip()
    if not tail:
        return m
    if _M_CASE_TAIL_OK.match(tail):
        return m
    if serial_on_line:
        return m
    return None


def _extract_madras_page(page_obj: fitz.Page, page_num: int) -> List[Dict[str, Any]]:
    """Column-aware state-machine parser for Madras High Court cause list.

    Reads all word tokens, classifies them by x-coordinate into four columns,
    then uses a state machine to build structured case records.
    """
    words_raw = page_obj.get_text("words") or []
    if not words_raw:
        return []

    # Group word tokens into visual lines (y-coordinate within 3.5 px = same line)
    Y_TOL = 3.5
    lines: List[List] = []
    for w in sorted(words_raw, key=lambda w: (float(w[1]), float(w[0]))):
        if not str(w[4]).strip():
            continue
        if lines and abs(float(w[1]) - float(lines[-1][0][1])) <= Y_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])

    cases: List[Dict[str, Any]] = []
    cur_serial = ""
    cur_case_no = ""
    pet_parts: List[str] = []
    resp_parts: List[str] = []
    adv_parts: List[str] = []
    party_state = "PETITIONER"  # PETITIONER | RESPONDENT

    def _flush() -> None:
        nonlocal cur_case_no
        if not cur_case_no:
            return
        pet = " ".join(pet_parts).strip(" ,.-")
        resp = " ".join(resp_parts).strip(" ,.-")
        adv_clean = [a for a in adv_parts if a.strip() and not _M_DASH_PAT.match(a.strip())]
        adv = " | ".join(adv_clean)
        cases.append({
            "page": page_num,
            "row_id": f"{page_num}-{len(cases) + 1}",
            "case_no": cur_case_no,
            "serial": cur_serial,
            "petitioner": pet,
            "respondent": resp,
            "advocates": adv,
            "raw_text": f"{cur_case_no} {pet} VS {resp}",
            "party": pet,
            "stage": adv,
            "geometry": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
            "confidence": 0.9,
            "_pre_parsed": True,
        })
        cur_case_no = ""
        pet_parts.clear()
        resp_parts.clear()
        adv_parts.clear()

    for line in lines:
        # Bucket words by column
        serial_words = [w[4] for w in line if float(w[0]) < _M_SERIAL_MAX]
        case_words   = [w[4] for w in line if _M_CASE_MIN <= float(w[0]) < _M_CASE_MAX]
        party_words  = [w[4] for w in line if _M_PARTY_MIN <= float(w[0]) < _M_PARTY_MAX]
        adv_words    = [w[4] for w in line if float(w[0]) >= _M_ADV_MIN]

        serial_txt = " ".join(serial_words).strip()
        case_txt   = _M_FILING_PAT.sub("", " ".join(case_words)).strip()
        party_txt  = " ".join(party_words).strip()
        adv_txt    = " ".join(adv_words).strip()

        # Skip header/footer lines
        full_line = " ".join(w[4] for w in line).strip()
        if _M_SKIP_PAT.search(full_line):
            continue

        # Serial number (far-left column only)
        serial_on_line = bool(serial_txt and _M_SERIAL_PAT.match(serial_txt))
        if serial_on_line:
            cur_serial = serial_txt

        # Case-number column processing
        if case_txt:
            if _M_AND_PAT.match(case_txt):
                # "AND" between companion cases: flush current, next case shares serial
                _flush()
            else:
                m = _match_madras_case_text(case_txt, serial_on_line=serial_on_line)
                if m:
                    _flush()
                    raw_type = m.group(1).upper().replace(".", "").replace(" ", "")
                    cur_case_no = f"{raw_type}/{m.group(2)}/{m.group(3)}"
                    party_state = "PETITIONER"
                    # If party text appears on same line as case number, collect it
                    if party_txt:
                        if _M_VS_PAT.match(party_txt):
                            party_state = "RESPONDENT"
                        elif party_state == "PETITIONER":
                            pet_parts.append(party_txt)
                        else:
                            resp_parts.append(party_txt)
                    if adv_txt and not _M_DASH_PAT.match(adv_txt):
                        adv_parts.append(adv_txt)
                    continue  # already handled party/adv above

        # Party and advocate columns (only when a case is open)
        if not cur_case_no:
            continue

        if party_txt:
            if _M_VS_PAT.match(party_txt):
                party_state = "RESPONDENT"
            elif party_state == "PETITIONER":
                pet_parts.append(party_txt)
            else:
                resp_parts.append(party_txt)

        if adv_txt and not _M_DASH_PAT.match(adv_txt):
            adv_parts.append(adv_txt)

    _flush()
    return cases


# ---------------------------------------------------------------------------
# Delhi High Court column-aware extractor
# ---------------------------------------------------------------------------
#
# Delhi PDF column layout (page width 720 pt, from rl09032026.pdf analysis):
#   x0 <  65         → serial number  (e.g. "1.", "11.")
#   65 ≤ x0 < 260   → case column    (e.g. "FAO (COMM) 29/2022", "W.P.(C) 11059/2017")
#   260 ≤ x0 < 440  → party column   (petitioner, "Vs.", respondent)
#   x0 ≥ 440        → advocate column
#
_D_SERIAL_MAX = 65
_D_CASE_MIN   = 65
_D_CASE_MAX   = 260
_D_PARTY_MIN  = 260
_D_PARTY_MAX  = 440
_D_ADV_MIN    = 440

# Serial: one-to-three digits followed by a dot  ("1.", "10.", "100.")
_D_SERIAL_PAT = re.compile(r"^\d{1,3}\.$")

# Main case number: type token(s) then NUMBER/YEAR
# Handles: "FAO (COMM) 29/2022", "W.P.(C) 11059/2017", "RFA(COMM) 5/2022", "W.P.(CRL) 3/2024"
_DELHI_CASE_PAT = re.compile(
    r"^([A-Z][A-Z.()\s]*?)\s{1,4}(\d{1,7}/\d{4})\s*$",
    re.IGNORECASE,
)

# Sub-application lines ("CM APPL. 8304/2022") — belong to the current case, NOT a new case
_D_SUBAPP_PAT = re.compile(r"^CM\s+APPL\.?\s+\d{1,7}/\d{4}", re.IGNORECASE)

# "Vs." / "Vs. SOME TEXT" in party column — may have respondent text on same token
_D_VS_PAT     = re.compile(r"^Vs\.?\s*$", re.IGNORECASE)
_D_VS_START   = re.compile(r"^Vs\.?\s+", re.IGNORECASE)

# "WITH" connector line in case column — indicates a companion case, not a new serial
_D_WITH_PAT   = re.compile(r"^WITH\s+", re.IGNORECASE)

# Lines to skip entirely: bench headers, VC instructions, page stamps, navigation text
_D_SKIP_PAT = re.compile(
    r"HON.?BLE\b|COURT\s+NO\b|DIVISION\s+BENCH|CLICK\s+HERE|MEETING\s+NO\b|"
    r"\bVC\b$|https?://|REGULAR\s+MATTER|Courtmaster|harikishan|@dhc\.|"
    r"ADJOURNMENT|FOR\s+MOVING|NOTE:\s*FOR|^\s*R-\d+\s*$|"
    r"CAUSE\s*LIST\b|DELHI\s+HIGH\s+COURT",
    re.IGNORECASE,
)

# Date stamp at top of page: "09.03.2026"
_D_DATE_PAT = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def _delhi_from_word_dicts(
    word_dicts: List[Dict[str, Any]], page_num: int
) -> List[Dict[str, Any]]:
    """Delhi column-aware state-machine from word dicts (PDF point coordinates).

    Handles:
    - Multiple court sections per page (new COURT NO. header mid-page)
    - Sub-applications (CM APPL.) that belong to the parent case
    - Petitioner/respondent split by 'Vs.' in party column
    """
    Y_TOL = 3.5
    sorted_words = sorted(word_dicts, key=lambda w: (float(w["top"]), float(w["x0"])))
    fitz_like = [
        (float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]), str(w["text"]))
        for w in sorted_words if str(w.get("text", "")).strip()
    ]

    lines: List[List] = []
    for w in fitz_like:
        if lines and abs(w[1] - float(lines[-1][0][1])) <= Y_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])

    cases: List[Dict[str, Any]] = []
    cur_serial = ""      # most recently seen serial token on the current line
    case_serial = ""     # serial that belongs to the currently open case (set at case-open time)
    cur_case_no = ""
    pet_parts: List[str] = []
    resp_parts: List[str] = []
    adv_parts: List[str] = []
    sub_app_parts: List[str] = []   # CM APPL. / interlocutory application numbers
    party_state = "PETITIONER"

    def _flush_d() -> None:
        nonlocal cur_case_no, case_serial
        if not cur_case_no:
            return
        pet = " ".join(pet_parts).strip(" ,.-")
        resp = " ".join(resp_parts).strip(" ,.-")
        adv = " | ".join(a for a in adv_parts if a.strip())
        # Only emit rows that have at least a petitioner or respondent
        # (pure WITH-connector stubs have neither and should be dropped)
        if not pet and not resp:
            cur_case_no = ""
            case_serial = ""
            pet_parts.clear(); resp_parts.clear(); adv_parts.clear(); sub_app_parts.clear()
            return
        cases.append({
            "page": page_num,
            "row_id": f"{page_num}-{len(cases)+1}",
            "case_no": cur_case_no,
            "serial": case_serial,
            "petitioner": pet,
            "respondent": resp,
            "advocates": adv,
            "sub_applications": list(sub_app_parts),   # e.g. ["CM APPL. 8304/2022"]
            "raw_text": f"{cur_case_no} {pet} VS {resp}",
            "party": pet,
            "stage": adv,
            "geometry": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
            "confidence": 0.9,
            "_pre_parsed": True,
        })
        cur_case_no = ""
        case_serial = ""
        pet_parts.clear()
        resp_parts.clear()
        adv_parts.clear()
        sub_app_parts.clear()

    def _handle_party(txt: str) -> None:
        """Route party column text to petitioner / respondent, handling 'Vs.' variants."""
        nonlocal party_state
        if _D_VS_PAT.match(txt):
            party_state = "RESPONDENT"
        elif _D_VS_START.match(txt):
            # "Vs. SOME RESPONDENT TEXT" on the same token
            party_state = "RESPONDENT"
            remainder = _D_VS_START.sub("", txt).strip()
            if remainder:
                resp_parts.append(remainder)
        elif party_state == "PETITIONER":
            pet_parts.append(txt)
        else:
            resp_parts.append(txt)

    for line in lines:
        serial_words = [w[4] for w in line if w[0] < _D_SERIAL_MAX]
        case_words   = [w[4] for w in line if _D_CASE_MIN <= w[0] < _D_CASE_MAX]
        party_words  = [w[4] for w in line if _D_PARTY_MIN <= w[0] < _D_PARTY_MAX]
        adv_words    = [w[4] for w in line if w[0] >= _D_ADV_MIN]

        serial_txt = " ".join(serial_words).strip()
        case_txt   = " ".join(case_words).strip()
        party_txt  = " ".join(party_words).strip()
        adv_txt    = " ".join(adv_words).strip()
        full_line  = " ".join(w[4] for w in line).strip()

        # Skip headers, VC info, date stamps
        if _D_SKIP_PAT.search(full_line) or _D_DATE_PAT.match(full_line.split()[0] if full_line else ""):
            continue

        # Serial number (far-left column) — save for use when next case opens
        if serial_txt and _D_SERIAL_PAT.match(serial_txt):
            cur_serial = serial_txt.rstrip(".")

        # Case column
        if case_txt:
            if _D_WITH_PAT.match(case_txt):
                # "WITH FAO(COMM) 55/2022" — cross-reference to a companion case.
                # Skip entirely: it is not a new independently listed case.
                continue
            elif _D_SUBAPP_PAT.match(case_txt):
                # "CM APPL. 8304/2022" — interlocutory application under current case.
                # Record it as a sub-application, NOT a new case row.
                sub_app_parts.append(case_txt)
                # party_txt / adv_txt on this same line may carry "Vs." or respondent
                # text (Delhi PDF places Vs. inline with the first CM APPL. line),
                # so fall through to the party/adv handling below.
            else:
                m = _DELHI_CASE_PAT.match(case_txt)
                if m:
                    # KEY RULE: a new top-level case is only created when a
                    # serial number appears on the SAME line (same y-bucket).
                    # Serial is the court's own ground-truth counter — if it's
                    # absent, this case-number token is a sub-section header,
                    # companion reference, or overflow continuation, NOT a new row.
                    if serial_txt and _D_SERIAL_PAT.match(serial_txt):
                        _flush_d()
                        type_clean = re.sub(r"\s+", "", m.group(1)).upper()
                        cur_case_no = f"{type_clean}/{m.group(2)}"
                        case_serial = serial_txt.rstrip(".")
                        party_state = "PETITIONER"
                        if party_txt:
                            _handle_party(party_txt)
                        if adv_txt:
                            adv_parts.append(adv_txt)
                        continue
                    else:
                        # No serial on this line — treat as companion/sub entry
                        if cur_case_no:
                            sub_app_parts.append(case_txt)
                        # fall through to party/adv handling below

        if not cur_case_no:
            continue

        if party_txt:
            _handle_party(party_txt)

        if adv_txt:
            adv_parts.append(adv_txt)

    _flush_d()
    return cases


def _extract_delhi_page(page_obj: fitz.Page, page_num: int) -> List[Dict[str, Any]]:
    """Delegates to _delhi_from_word_dicts using fitz word positions."""
    words_raw = page_obj.get_text("words") or []
    return _delhi_from_word_dicts(
        [{"text": w[4], "x0": w[0], "x1": w[2], "top": w[1], "bottom": w[3]} for w in words_raw],
        page_num,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(
    pdf_path: Path,
    max_pages: int | None = None,
    court: str = "madras",
    progress_cb=None,
) -> List[Dict[str, Any]]:
    _cb = progress_cb or (lambda m: None)
    rows: List[Dict[str, Any]] = []
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc) if max_pages is None else min(len(doc), max_pages)
    court_lower = (court or "").strip().lower()

    for page_index in range(total_pages):
        page_num = page_index + 1
        _cb(f"PyMuPDF: processing page {page_num} / {total_pages}...")
        page = doc[page_index]
        if court_lower == "bombay":
            page_rows = _extract_bombay_page(page, page_index + 1)
        elif court_lower == "delhi":
            page_rows = _extract_delhi_page(page, page_index + 1)
        else:
            page_rows = _extract_madras_page(page, page_index + 1)

        rows.extend(page_rows)

    doc.close()
    return rows


# ---------------------------------------------------------------------------
# Shared word-dict based parser (for LayoutParser / PaddleOCR extractors)
#
# `word_dicts`: list of {text, x0, x1, top, bottom} already in PDF point space.
# `coord_scale`: set to 72.0/dpi when supplying raw OCR pixel coordinates so
#                the function normalises them to PDF points automatically.
# ---------------------------------------------------------------------------

def _bombay_from_word_dicts(
    word_dicts: List[Dict[str, Any]], page_num: int
) -> List[Dict[str, Any]]:
    """Bombay state-machine from pre-grouped word dicts (PDF point coordinates)."""
    Y_TOL = 5.0
    # Group words into visual lines
    sorted_words = sorted(word_dicts, key=lambda w: (float(w["top"]), float(w["x0"])))
    raw_lines: List[List[Dict]] = []
    for w in sorted_words:
        if not str(w.get("text", "")).strip():
            continue
        if raw_lines and abs(float(w["top"]) - float(raw_lines[-1][0]["top"])) <= Y_TOL:
            raw_lines[-1].append(w)
        else:
            raw_lines.append([w])

    # Flatten to (x0, line_text) pairs matching what _extract_bombay_page produces
    all_lines: List[tuple] = []
    for line_words in raw_lines:
        left_words = [w for w in line_words if float(w["x0"]) <= 280]
        right_words = [w for w in line_words if float(w["x0"]) > 280]
        if left_words:
            x0 = float(left_words[0]["x0"])
            txt = " ".join(str(w["text"]) for w in sorted(left_words, key=lambda w: float(w["x0"])))
            all_lines.append((x0, txt.strip()))
        if right_words:
            x0_r = float(right_words[0]["x0"])
            txt_r = " ".join(str(w["text"]) for w in sorted(right_words, key=lambda w: float(w["x0"])))
            all_lines.append((x0_r, txt_r.strip()))

    # Reuse exact same state machine as _extract_bombay_page
    cases: List[Dict[str, Any]] = []
    serial: Optional[str] = None
    case_no: Optional[str] = None
    petitioner_parts: List[str] = []
    respondent_parts: List[str] = []
    advocate_parts: List[str] = []
    state = "LOOKING"

    def _flush_b() -> None:
        nonlocal case_no, serial
        if case_no:
            pet_text = _clean_party(petitioner_parts)
            resp_text = _clean_party(respondent_parts)
            adv_text = " | ".join(a.strip() for a in advocate_parts if a.strip())
            cases.append({
                "page": page_num,
                "row_id": f"{page_num}-{len(cases)+1}",
                "case_no": case_no,
                "serial": serial or "",
                "petitioner": pet_text,
                "respondent": resp_text,
                "advocates": adv_text,
                "raw_text": f"{case_no} {pet_text} VS {resp_text}",
                "party": pet_text,
                "stage": adv_text,
                "geometry": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
                "confidence": 0.85,
                "_pre_parsed": True,
            })
        case_no = None
        petitioner_parts.clear()
        respondent_parts.clear()
        advocate_parts.clear()

    for x0, line in all_lines:
        if _SKIP_LINE_PAT.search(line):
            continue
        if _REMARK_PAT.match(line):
            state = "SKIP"
            continue
        if state == "SKIP":
            serial_hit, case_hit = _extract_bombay_case_from_line(line, allow_inline=False)
            if serial_hit and case_hit:
                state = "PETITIONER"
                serial = serial_hit
                case_no = case_hit
                continue
            if case_hit:
                state = "PETITIONER"
                case_no = case_hit
                continue
            if _SERIAL_PAT.match(line):
                state = "LOOKING"
                serial = _normalize_serial(line)
            continue
        if _BRACKET_PAT.match(line) or _WITH_PAT.match(line):
            continue
        if _SERIAL_PAT.match(line):
            serial = _normalize_serial(line)
            continue
        serial_hit, case_hit = _extract_bombay_case_from_line(line, allow_inline=(state == "LOOKING"))
        if case_hit:
            _flush_b()
            if serial_hit:
                serial = serial_hit
            case_no = case_hit
            state = "PETITIONER"
            continue
        if state == "LOOKING":
            continue
        if state == "PETITIONER":
            if _VS_PAT.match(line):
                state = "RESPONDENT"
            elif _REMARK_CONT_PAT.match(line):
                state = "SKIP"
            elif _FOR_PARTY_PAT.match(line):
                advocate_parts.append(line)
            elif x0 > 280 and len(line) <= 60 and not _DULY_SERVED.search(line):
                advocate_parts.append(line)
            else:
                petitioner_parts.append(line)
        elif state == "RESPONDENT":
            if _VS_PAT.match(line):
                pass
            elif _FOR_PARTY_PAT.match(line):
                advocate_parts.append(line)
            elif x0 > 280 and len(line) <= 60 and not _DULY_SERVED.search(line):
                advocate_parts.append(line)
            elif _ADVOCATE_SUFFIX.search(line) and len(line) < 80:
                advocate_parts.append(line)
            else:
                respondent_parts.append(line)

    _flush_b()
    existing = {str(c.get("case_no") or "") for c in cases if c.get("case_no")}
    cases.extend(_supplement_bombay_cases_from_lines(all_lines, page_num, existing))
    return cases


def _madras_from_word_dicts(
    word_dicts: List[Dict[str, Any]], page_num: int
) -> List[Dict[str, Any]]:
    """Madras column-aware state-machine from word dicts (PDF point coordinates)."""
    Y_TOL = 3.5
    sorted_words = sorted(word_dicts, key=lambda w: (float(w["top"]), float(w["x0"])))
    # Build (x0, y0, x1, y1, text) tuples matching fitz "words" format
    fitz_like = [(float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]), str(w["text"])) for w in sorted_words if str(w.get("text","")).strip()]

    lines: List[List] = []
    for w in fitz_like:
        if lines and abs(w[1] - float(lines[-1][0][1])) <= Y_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])

    cases: List[Dict[str, Any]] = []
    cur_serial = ""
    cur_case_no = ""
    pet_parts: List[str] = []
    resp_parts: List[str] = []
    adv_parts: List[str] = []
    party_state = "PETITIONER"

    def _flush_m() -> None:
        nonlocal cur_case_no
        if not cur_case_no:
            return
        pet = " ".join(pet_parts).strip(" ,.-")
        resp = " ".join(resp_parts).strip(" ,.-")
        adv_clean = [a for a in adv_parts if a.strip() and not _M_DASH_PAT.match(a.strip())]
        adv = " | ".join(adv_clean)
        cases.append({
            "page": page_num,
            "row_id": f"{page_num}-{len(cases)+1}",
            "case_no": cur_case_no,
            "serial": cur_serial,
            "petitioner": pet,
            "respondent": resp,
            "advocates": adv,
            "raw_text": f"{cur_case_no} {pet} VS {resp}",
            "party": pet,
            "stage": adv,
            "geometry": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
            "confidence": 0.85,
            "_pre_parsed": True,
        })
        cur_case_no = ""
        pet_parts.clear()
        resp_parts.clear()
        adv_parts.clear()

    for line in lines:
        serial_words = [w[4] for w in line if w[0] < _M_SERIAL_MAX]
        case_words   = [w[4] for w in line if _M_CASE_MIN <= w[0] < _M_CASE_MAX]
        party_words  = [w[4] for w in line if _M_PARTY_MIN <= w[0] < _M_PARTY_MAX]
        adv_words    = [w[4] for w in line if w[0] >= _M_ADV_MIN]

        serial_txt = " ".join(serial_words).strip()
        case_txt   = _M_FILING_PAT.sub("", " ".join(case_words)).strip()
        party_txt  = " ".join(party_words).strip()
        adv_txt    = " ".join(adv_words).strip()

        full_line = " ".join(w[4] for w in line).strip()
        if _M_SKIP_PAT.search(full_line):
            continue
        serial_on_line = bool(serial_txt and _M_SERIAL_PAT.match(serial_txt))
        if serial_on_line:
            cur_serial = serial_txt
        if case_txt:
            if _M_AND_PAT.match(case_txt):
                _flush_m()
            else:
                m = _match_madras_case_text(case_txt, serial_on_line=serial_on_line)
                if m:
                    _flush_m()
                    raw_type = m.group(1).upper().replace(".", "").replace(" ", "")
                    cur_case_no = f"{raw_type}/{m.group(2)}/{m.group(3)}"
                    party_state = "PETITIONER"
                    if party_txt:
                        if _M_VS_PAT.match(party_txt):
                            party_state = "RESPONDENT"
                        elif party_state == "PETITIONER":
                            pet_parts.append(party_txt)
                        else:
                            resp_parts.append(party_txt)
                    if adv_txt and not _M_DASH_PAT.match(adv_txt):
                        adv_parts.append(adv_txt)
                    continue
        if not cur_case_no:
            continue
        if party_txt:
            if _M_VS_PAT.match(party_txt):
                party_state = "RESPONDENT"
            elif party_state == "PETITIONER":
                pet_parts.append(party_txt)
            else:
                resp_parts.append(party_txt)
        if adv_txt and not _M_DASH_PAT.match(adv_txt):
            adv_parts.append(adv_txt)

    _flush_m()
    return cases


def extract_page_from_words(
    word_dicts: List[Dict[str, Any]],
    page_num: int,
    court: str = "madras",
    coord_scale: float = 1.0,
) -> List[Dict[str, Any]]:
    """Parse a page's worth of word dicts into structured case records.

    word_dicts: list of {text, x0, x1, top, bottom} – any coordinate space.
    coord_scale: multiply x0/x1/top/bottom by this value to convert to PDF points.
                 Pass 72.0/dpi for raw OCR pixel coordinates (e.g. 72/110 for Tesseract
                 rendered at 110 DPI, 72/120 for PaddleOCR at 120 DPI).
    """
    if coord_scale != 1.0:
        word_dicts = [
            {
                "text": w["text"],
                "x0": float(w["x0"]) * coord_scale,
                "x1": float(w["x1"]) * coord_scale,
                "top": float(w["top"]) * coord_scale,
                "bottom": float(w["bottom"]) * coord_scale,
                "confidence": w.get("confidence", 0.8),
            }
            for w in word_dicts
        ]
    court_lower = (court or "").strip().lower()
    if court_lower == "bombay":
        return _bombay_from_word_dicts(word_dicts, page_num)
    if court_lower == "delhi":
        return _delhi_from_word_dicts(word_dicts, page_num)
    return _madras_from_word_dicts(word_dicts, page_num)

