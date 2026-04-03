import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")

if not os.path.exists(db_path):
    print("No leads.db found, skipping migration")
else:
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()]
    if "qualified" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN qualified INTEGER DEFAULT 0")
        conn.commit()
        print("Added qualified column")
    else:
        print("Column already exists")
    conn.close()
    print("Migration done")
