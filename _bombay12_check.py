from pathlib import Path
import sys
from collections import Counter
sys.path.insert(0, str(Path('project')))
from extractors import pdf_structural, paddle_ocr, surya_ocr

pdf = Path('bombay')/'entirecauselist.pdf'

rows_py = pdf_structural.extract(pdf, court='bombay', max_pages=12)
print('pymupdf_12', len(rows_py))
print('sample', [r.get('case_no') for r in rows_py[:25]])

serial_missing = sum(1 for r in rows_py if not str(r.get('serial') or '').strip())
print('serial_missing', serial_missing)

cnt = Counter(str(r.get('case_no') or '').upper() for r in rows_py)
print('dupe_rows', sum(v-1 for v in cnt.values() if v>1))
print('top_dupes', cnt.most_common(10))
