import fitz
from pathlib import Path
pdf=Path('madras')/'cause_02032026.pdf'
d=fitz.open(str(pdf))
for i,p in enumerate(d[:40], start=1):
    t=p.get_text('text') or ''
    if '4535' in t or 'TOTAL' in t.upper() and 'CASE' in t.upper():
        print('page',i)
        for ln in t.splitlines():
            u=ln.upper()
            if '4535' in ln or ('TOTAL' in u and 'CASE' in u):
                print(ln)
