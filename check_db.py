import sqlite3
c = sqlite3.connect("/root/lead-audit/leads.db")
total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
with_phone = c.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''").fetchone()[0]
with_email = c.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''").fetchone()[0]
qualified = c.execute("SELECT COUNT(*) FROM leads WHERE qualified = 1").fetchone()[0]
print(f"Total leads: {total}")
print(f"With phone: {with_phone}")
print(f"With email: {with_email}")
print(f"Qualified: {qualified}")
cats = c.execute("SELECT category, COUNT(*) FROM leads GROUP BY category").fetchall()
for cat, cnt in cats:
    print(f"  {cat}: {cnt}")
if with_email > 0:
    rows = c.execute("SELECT name, email FROM leads WHERE email IS NOT NULL AND email != '' LIMIT 5").fetchall()
    print("Email examples:")
    for name, email in rows:
        print(f"  {name}: {email}")
c.close()
