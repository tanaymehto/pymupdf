import fitz, sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,'project')
from extractors import pdf_structural as ps

pdf=Path('madras')/'cause_02032026.pdf'
Y_TOL=3.5
tails=Counter()
examples=[]
for p in fitz.open(str(pdf)):
    words_raw=p.get_text('words') or []
    lines=[]
    for w in sorted(words_raw,key=lambda w:(float(w[1]), float(w[0]))):
        if not str(w[4]).strip(): continue
        if lines and abs(float(w[1])-float(lines[-1][0][1]))<=Y_TOL: lines[-1].append(w)
        else: lines.append([w])
    for line in lines:
        serial_txt=' '.join([w[4] for w in line if float(w[0]) < ps._M_SERIAL_MAX]).strip()
        serial_on=bool(serial_txt and ps._M_SERIAL_PAT.match(serial_txt))
        case_txt=ps._M_FILING_PAT.sub('', ' '.join([w[4] for w in line if ps._M_CASE_MIN <= float(w[0]) < ps._M_CASE_MAX])).strip()
        m=ps._MADRAS_CASE_PAT.match(case_txt)
        if not m: continue
        if ps._match_madras_case_text(case_txt, serial_on_line=serial_on): continue
        tail=case_txt[m.end():].strip()
        key=tail[:40].upper()
        tails[key]+=1
        if len(examples)<80:
            examples.append((p.number+1, serial_on, case_txt, tail))
print('rejected',sum(tails.values()))
print('top_tails',tails.most_common(30))
print('examples')
for e in examples[:60]: print(e)
