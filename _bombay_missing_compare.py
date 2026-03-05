import re, fitz
from pathlib import Path
import sys
sys.path.insert(0, str(Path('project')))
from extractors import pdf_structural
from structured_parser import parse_cause_list

pdf = Path('bombay')/'entirecauselist.pdf'
rows = pdf_structural.extract(pdf, court='bombay')
cases = parse_cause_list(rows, court='bombay')
parsed = {str(c.get('case_no') or '').upper().strip() for c in cases if c.get('case_no')}

loose_pat = re.compile(r"\b([A-Z][A-Z./()\-]{1,24})\s*/\s*(\d{1,7})\s*/\s*((?:19|20)\d{2})\b", re.I)
loose = set()
doc = fitz.open(str(pdf))
for p in doc:
    txt = p.get_text('text')
    for m in loose_pat.finditer(txt):
        t = re.sub(r'\s+','',m.group(1).upper()).replace('.','').replace('-','').strip('/')
        n = f"{t}/{m.group(2)}/{m.group(3)}"
        loose.add(n)

missing = sorted(loose - parsed)
extra = sorted(parsed - loose)
print('parsed_unique', len(parsed))
print('loose_unique', len(loose))
print('missing_from_parser', len(missing))
print('extra_not_loose', len(extra))
print('missing_sample', missing[:60])
