from pathlib import Path
import sys
sys.path.insert(0,'project')
from extractors import pdf_structural
from structured_parser import parse_cause_list

rows = pdf_structural.extract(Path('madras')/'cause_02032026.pdf', court='madras')
cases = parse_cause_list(rows, court='madras')
weak = 0
for c in cases:
    s = str(c.get('serial') or '').strip()
    if (not s) and c.get('petitioner') == 'Not available in source' and c.get('respondent') == 'Not available in source' and c.get('advocates') == 'Not available in source':
        weak += 1
print('madras_total', len(cases))
print('weak', weak)
