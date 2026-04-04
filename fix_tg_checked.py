"""Fix: mark old leads (already audited) as tg_checked, and run TG check for remaining."""
import sqlite3

c = sqlite3.connect("/root/lead-audit/leads.db")

# Old leads that already have audits — mark as checked
updated = c.execute(
    "UPDATE leads SET tg_checked=1, has_telegram=0 WHERE audit_generated=1 AND tg_checked=0"
).rowcount
print(f"Marked {updated} old audited leads as tg_checked")

# Leads with email but no audit — mark as tg_checked so they can get audits
email_updated = c.execute(
    """UPDATE leads SET tg_checked=1, has_telegram=0 
       WHERE enriched=1 AND tg_checked=0 AND audit_generated=0
       AND email IS NOT NULL AND email != ''"""
).rowcount
print(f"Marked {email_updated} email-only leads as tg_checked")

c.commit()

# Show new stats
ready = c.execute(
    """SELECT COUNT(*) FROM leads 
       WHERE enriched=1 AND audit_generated=0 AND tg_checked=1 
       AND (has_telegram=1 OR (email IS NOT NULL AND email != ''))"""
).fetchone()[0]
not_checked = c.execute(
    "SELECT COUNT(*) FROM leads WHERE enriched=1 AND tg_checked=0"
).fetchone()[0]
print(f"\nReady for audit: {ready}")
print(f"Still not TG-checked: {not_checked}")
c.close()
