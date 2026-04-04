import sqlite3
c = sqlite3.connect("/root/lead-audit/leads.db")
total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
with_phone = c.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''").fetchone()[0]
with_email = c.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''").fetchone()[0]
qualified = c.execute("SELECT COUNT(*) FROM leads WHERE qualified = 1").fetchone()[0]
enriched = c.execute("SELECT COUNT(*) FROM leads WHERE enriched = 1").fetchone()[0]
audited = c.execute("SELECT COUNT(*) FROM leads WHERE audit_generated = 1").fetchone()[0]

cols = [r[1] for r in c.execute("PRAGMA table_info(leads)").fetchall()]
if "tg_checked" in cols:
    tg_checked = c.execute("SELECT COUNT(*) FROM leads WHERE tg_checked = 1").fetchone()[0]
    has_tg = c.execute("SELECT COUNT(*) FROM leads WHERE has_telegram = 1").fetchone()[0]
    not_checked = c.execute("SELECT COUNT(*) FROM leads WHERE enriched = 1 AND tg_checked = 0").fetchone()[0]
    ready = c.execute("SELECT COUNT(*) FROM leads WHERE enriched=1 AND audit_generated=0 AND tg_checked=1 AND (has_telegram=1 OR (email IS NOT NULL AND email != ''))").fetchone()[0]
else:
    tg_checked = has_tg = not_checked = ready = "N/A (migration needed)"

print(f"Total: {total}")
print(f"Enriched: {enriched}")
print(f"Audited: {audited}")
print(f"With phone: {with_phone}")
print(f"With email: {with_email}")
print(f"Qualified: {qualified}")
print(f"TG checked: {tg_checked}")
print(f"Has Telegram: {has_tg}")
print(f"Not TG-checked: {not_checked}")
print(f"Ready for audit: {ready}")
print()
cats = c.execute("SELECT category, COUNT(*) FROM leads GROUP BY category").fetchall()
for cat, cnt in cats:
    print(f"  {cat}: {cnt}")
c.close()
