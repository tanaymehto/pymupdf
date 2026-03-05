from pathlib import Path
import sys
sys.path.insert(0, str(Path("project")))
from extractors import surya_ocr, pdf_structural as ps

pdf = Path('bombay')/'entirecauselist.pdf'
rows = surya_ocr.extract(pdf, max_pages=5, court='bombay')
print('surya_5_pages', len(rows))
print('sample', [r.get('case_no') for r in rows[:20]])
