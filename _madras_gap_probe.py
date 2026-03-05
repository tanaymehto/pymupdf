import re, fitz, sys
from pathlib import Path
sys.path.insert(0,'project')
from extractors import pdf_structural
from structured_parser import parse_cause_list

pdf = Path('madras')/'cause_02032026.pdf'
parsed = {str(c.get('case_no') or '').upper().strip() for c in parse_cause_list(pdf_structural.extract(pdf,court='madras'),court='madras') if c.get('case_no')}
pat = re.compile(r"\b([A-Z][A-Z./()\-]{0,24}(?:\s+[A-Z][A-Z./()\-]{0,24}){0,2})\s*(?:NO\.?\s*)?[\s/]+(\d{1,7})\s*/\s*((?:19|20)\d{2})\b", re.I)
loose = set()
for p in fitz.open(str(pdf)):
    t = p.get_text('text')
    for m in pat.finditer(t):
        typ = re.sub(r"\s+", "", m.group(1).upper()).replace('.', '').replace('-', '').strip('/')
        loose.add(f"{typ}/{m.group(2)}/{m.group(3)}")
missing = sorted(loose - parsed)
print('parsed', len(parsed), 'loose', len(loose), 'missing', len(missing))
print('missing_head', missing[:40])
