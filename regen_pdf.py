"""Regenerate PDF for specific leads by name (uses existing audit_text, no API call)."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from auditor import generate_pdf, _load_audits, _save_audits

TARGETS = ["G-Clean", "Perehvat"]

audits = _load_audits()
updated = 0
for a in audits:
    name = a.get("name", "")
    if any(t.lower() in name.lower() for t in TARGETS):
        txt = a.get("audit_text", "")
        if not txt:
            print(f"SKIP {name}: no audit_text")
            continue
        lead = {
            "id": a.get("lead_id"),
            "name": name,
            "city": a.get("city", ""),
            "category": a.get("category", ""),
            "monthly_searches": a.get("monthly_searches", 0),
            "competitors_total": a.get("competitors_total", 0),
            "competitors_with_site": a.get("competitors_with_site", 0),
            "competitors_in_search": a.get("competitors_in_search", "[]"),
        }
        pdf = generate_pdf(lead, txt)
        a["audit_pdf_path"] = pdf
        print(f"OK: {name} -> {pdf}")
        updated += 1

if updated:
    _save_audits(audits)
    print(f"\nRegenerated {updated} PDFs")
else:
    print("No matching audits found")
