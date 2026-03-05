import fitz, sys
from pathlib import Path
sys.path.insert(0,'project')
from extractors import pdf_structural as ps

pdf=Path('madras')/'cause_02032026.pdf'
doc=fitz.open(str(pdf))
Y_TOL=3.5
rej=0
rej_filing=0
rej_filing_no_serial=0
rej_other=0
for p in doc:
    words_raw=p.get_text('words') or []
    lines=[]
    for w in sorted(words_raw,key=lambda w:(float(w[1]), float(w[0]))):
        if not str(w[4]).strip():
            continue
        if lines and abs(float(w[1])-float(lines[-1][0][1]))<=Y_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        serial_words=[w[4] for w in line if float(w[0]) < ps._M_SERIAL_MAX]
        case_words=[w[4] for w in line if ps._M_CASE_MIN <= float(w[0]) < ps._M_CASE_MAX]
        serial_txt=' '.join(serial_words).strip()
        serial_on_line=bool(serial_txt and ps._M_SERIAL_PAT.match(serial_txt))
        case_txt=ps._M_FILING_PAT.sub('', ' '.join(case_words)).strip()
        m=ps._MADRAS_CASE_PAT.match(case_txt)
        if not m:
            continue
        accepted = ps._match_madras_case_text(case_txt, serial_on_line=serial_on_line)
        if accepted:
            continue
        rej += 1
        tail = case_txt[m.end():].strip().upper()
        if tail.startswith('(FILING') or tail.startswith('FILING'):
            rej_filing += 1
            if not serial_on_line:
                rej_filing_no_serial += 1
        else:
            rej_other += 1
print('rejected_total',rej)
print('rejected_filing',rej_filing)
print('rejected_filing_no_serial',rej_filing_no_serial)
print('rejected_other',rej_other)
