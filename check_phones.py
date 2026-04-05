"""Check phone number formats in DB."""
import sqlite3
c = sqlite3.connect("/root/lead-audit/leads.db")
rows = c.execute("SELECT phone FROM leads WHERE phone IS NOT NULL AND phone != '' ORDER BY id DESC LIMIT 20").fetchall()
print("Last 20 phones:")
for r in rows:
    phone = r[0]
    print(f"  [{phone}] len={len(phone)} starts_with_plus={'yes' if phone.startswith('+') else 'NO'}")

# Count formats
total = c.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''").fetchone()[0]
with_plus = c.execute("SELECT COUNT(*) FROM leads WHERE phone LIKE '+%'").fetchone()[0]
with_8 = c.execute("SELECT COUNT(*) FROM leads WHERE phone LIKE '8%' AND phone NOT LIKE '+%'").fetchone()[0]
with_7 = c.execute("SELECT COUNT(*) FROM leads WHERE phone LIKE '7%' AND phone NOT LIKE '+%'").fetchone()[0]
other = total - with_plus - with_8 - with_7
print(f"\nTotal: {total}")
print(f"  +XXX: {with_plus}")
print(f"  8XXX: {with_8}")
print(f"  7XXX: {with_7}")
print(f"  Other: {other}")
c.close()
