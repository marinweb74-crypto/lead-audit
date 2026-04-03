import sqlite3
c = sqlite3.connect("/root/lead-audit/leads.db")
total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
dupes = c.execute("SELECT COUNT(*) FROM leads WHERE source_id IS NOT NULL").fetchone()[0]
print("Total leads:", total)
print("With source_id:", dupes)
cats = c.execute("SELECT category, COUNT(*) FROM leads GROUP BY category").fetchall()
for cat, cnt in cats:
    print(f"  {cat}: {cnt}")
c.close()
