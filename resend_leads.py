"""Reset outreach status for specific leads so /send picks them up again."""
import json, os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTREACH_PATH = os.path.join(PROJECT_ROOT, "agent-runtime", "shared", "outreach.json")

TARGETS = ["G-Clean", "Perehvat"]

if not os.path.exists(OUTREACH_PATH):
    print("outreach.json not found")
    exit(1)

with open(OUTREACH_PATH, "r", encoding="utf-8") as f:
    records = json.load(f)

before = len(records)
records = [r for r in records if not any(t.lower() in r.get("name", "").lower() for t in TARGETS)]
removed = before - len(records)

with open(OUTREACH_PATH, "w", encoding="utf-8") as f:
    json.dump(records, f, ensure_ascii=False, indent=2)

print(f"Removed {removed} outreach records for: {', '.join(TARGETS)}")
print("Now run /send to resend these leads")
