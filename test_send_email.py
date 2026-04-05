"""Test: try sending one email to yourself to verify SMTP works end-to-end."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")) as f:
    config = json.load(f)

smtp_cfg = config["smtp"]

# Check how many leads have email
from db import init_db, get_connection
init_db()
with get_connection() as conn:
    with_email = conn.execute(
        "SELECT id, name, email, has_telegram, tg_checked, audit_generated FROM leads WHERE email IS NOT NULL AND email != '' LIMIT 10"
    ).fetchall()

print(f"Leads with email (first 10):")
for r in with_email:
    print(f"  id={r['id']} name={r['name']} email={r['email']} has_tg={r['has_telegram']} tg_checked={r['tg_checked']} audited={r['audit_generated']}")

# Check outreach for these leads
with get_connection() as conn:
    for r in with_email[:3]:
        outreach = conn.execute(
            "SELECT channel, step, status, error FROM outreach WHERE lead_id=?", (r['id'],)
        ).fetchall()
        if outreach:
            print(f"\n  Outreach for {r['name']}:")
            for o in outreach:
                print(f"    ch={o['channel']} step={o['step']} status={o['status']} err={o['error']}")
        else:
            print(f"\n  No outreach for {r['name']}")

# Check audits.json for these leads
audits_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-runtime", "shared", "audits.json")
if os.path.exists(audits_path):
    with open(audits_path) as f:
        audits = json.load(f)
    audit_ids = {a.get("lead_id") for a in audits}
    for r in with_email[:3]:
        has_audit = r['id'] in audit_ids
        print(f"  {r['name']}: audit in json = {has_audit}")
else:
    print("audits.json not found")
