"""Test all 3 extraction approaches against the Madras PDF."""
import requests, json, os

PDF = r"c:\Users\tanay\OneDrive\Desktop\internship\madras\cause_02032026.pdf"
URL = "http://127.0.0.1:5000/extract"

methods = [
    "LayoutParser Layout Extraction",
    "PaddleOCR Document Structure Extraction",
    "PyMuPDF Structural Parsing",
]

for m in methods:
    with open(PDF, "rb") as f:
        r = requests.post(URL, data={"court": "madras", "method": m},
                          files={"file": ("cause.pdf", f, "application/pdf")})
    if r.status_code != 200:
        print(f"[{m}] ERROR {r.status_code}: {r.text[:200]}")
        continue
    data = r.json()
    cases = data.get("cases", [])
    n = len(cases)
    pet_filled = sum(1 for c in cases if c.get("petitioner","").strip())
    res_filled = sum(1 for c in cases if c.get("respondent","").strip())
    adv_filled = sum(1 for c in cases if c.get("advocates","").strip())
    sample = cases[0] if cases else {}
    print(f"\n--- {m} ---")
    print(f"  Total cases : {n}")
    print(f"  Petitioner  : {pet_filled}/{n} ({100*pet_filled//n if n else 0}%)")
    print(f"  Respondent  : {res_filled}/{n} ({100*res_filled//n if n else 0}%)")
    print(f"  Advocates   : {adv_filled}/{n} ({100*adv_filled//n if n else 0}%)")
    print(f"  Sample[0]   : case_no={sample.get('case_no')} | pet={sample.get('petitioner','')[:50]} | resp={sample.get('respondent','')[:50]}")

print("\nDone.")
