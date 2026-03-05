from pathlib import Path
import sys,re
sys.path.insert(0, str(Path("project")))
from extractors import paddle_ocr
from extractors import pdf_structural as ps
import fitz

pdf = Path('bombay')/'entirecauselist.pdf'
doc = fitz.open(str(pdf))
page_index = 0
rgb = paddle_ocr._render_rgb(doc, page_index, dpi=120)
words = paddle_ocr._ppocr_word_dicts(rgb)
rows = ps.extract_page_from_words(words, 1, court='bombay', coord_scale=72/120)
print('page1_words', len(words))
print('page1_rows', len(rows))

# reproduce line building in _bombay_from_word_dicts
Y_TOL = 5.0
sorted_words = sorted(words, key=lambda w: (float(w['top']), float(w['x0'])))
raw_lines=[]
for w in sorted_words:
    if not str(w.get('text','')).strip():
        continue
    if raw_lines and abs(float(w['top']) - float(raw_lines[-1][0]['top'])) <= Y_TOL:
        raw_lines[-1].append(w)
    else:
        raw_lines.append([w])
all_lines=[]
for line_words in raw_lines:
    left_words=[w for w in line_words if float(w['x0'])<=280]
    right_words=[w for w in line_words if float(w['x0'])>280]
    if left_words:
        txt=' '.join(str(w['text']) for w in sorted(left_words,key=lambda w: float(w['x0']))).strip()
        all_lines.append(txt)
    if right_words:
        txt=' '.join(str(w['text']) for w in sorted(right_words,key=lambda w: float(w['x0']))).strip()
        all_lines.append(txt)

cand=[ln for ln in all_lines if re.search(r'\b\d{1,7}\s*/\s*(19|20)\d{2}\b', ln)]
print('case_like_lines', len(cand))
for ln in cand[:80]:
    print(ln)
