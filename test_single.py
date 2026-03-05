"""Test a single approach and print any error."""
import requests, json

PDF = r"c:\Users\tanay\OneDrive\Desktop\internship\madras\cause_02032026.pdf"
URL = "http://127.0.0.1:5000/extract"

method = "LayoutParser Layout Extraction"   # change to test others

with open(PDF, "rb") as f:
    r = requests.post(URL, data={"court": "madras", "method": method},
                      files={"file": ("cause.pdf", f, "application/pdf")})

print(f"Status: {r.status_code}")
try:
    data = r.json()
    if "error" in data:
        print(f"Error: {data['error']}")
    else:
        cases = data.get("cases", [])
        print(f"Cases: {len(cases)}")
        if cases:
            print(f"Sample: {cases[0]}")
except Exception as e:
    print(f"Parse error: {e}")
    print(r.text[:500])
