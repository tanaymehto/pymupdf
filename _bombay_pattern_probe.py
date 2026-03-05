import re, fitz
from pathlib import Path

pdf = Path('bombay')/'entirecauselist.pdf'
doc = fitz.open(str(pdf))

serial_like = re.compile(r'^\s*\d{1,4}[.)]?\s*$')
case_loose = re.compile(r'\b([A-Z][A-Z./()\-]{1,20})\s*/\s*(\d{1,7})\s*/\s*(\d{4})\b', re.I)

serial_hits=0
serial_examples=[]
case_hits=0
case_examples=[]
for pi in range(min(12, len(doc))):
    blocks = doc[pi].get_text('blocks') or []
    text_blocks = sorted([(float(b[0]), float(b[1]), b[4]) for b in blocks if b[6]==0], key=lambda x:(x[1],x[0]))
    for x0,y0,text in text_blocks:
        for line in text.split('\n'):
            s=line.strip()
            if not s:
                continue
            if serial_like.match(s):
                serial_hits += 1
                if len(serial_examples)<30: serial_examples.append((pi+1,s))
            m = case_loose.search(s)
            if m:
                case_hits += 1
                if len(case_examples)<80: case_examples.append((pi+1,s))

print('serial_hits', serial_hits)
print('serial_examples', serial_examples[:30])
print('case_line_hits_loose', case_hits)
print('case_examples')
for ex in case_examples[:80]:
    print(ex)
