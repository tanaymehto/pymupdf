from pathlib import Path
import sys
sys.path.insert(0, str(Path("project")))
from extractors import paddle_ocr, pdf_structural

pdf = Path('bombay')/'entirecauselist.pdf'
rows_ocr_12 = paddle_ocr.extract(pdf, max_pages=12, court='bombay')
print('paddle_12_cases', len(rows_ocr_12))
rows_ocr_30 = paddle_ocr.extract(pdf, max_pages=30, court='bombay')
print('paddle_30_cases', len(rows_ocr_30))
rows_pymu_30 = pdf_structural.extract(pdf, max_pages=30, court='bombay')
print('pymupdf_30_cases', len(rows_pymu_30))
print('sample_ocr', [r.get('case_no') for r in rows_ocr_30[:15]])
