import sqlite3
import json
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "leads.db")
BLACKLIST_PATH = os.path.join(PROJECT_ROOT, "blacklist.json")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            city TEXT,
            category TEXT,
            source_id TEXT UNIQUE,
            rating_2gis REAL,
            reviews_2gis INTEGER DEFAULT 0,
            has_photos INTEGER DEFAULT 0,
            working_hours TEXT,
            search_visible INTEGER DEFAULT 0,
            search_position INTEGER,
            competitors_in_search TEXT,
            monthly_searches INTEGER,
            cpc REAL,
            competition TEXT,
            google_maps_claimed INTEGER DEFAULT 0,
            google_rating REAL,
            google_reviews INTEGER DEFAULT 0,
            domain_suggestion TEXT,
            domain_available INTEGER,
            competitors_total INTEGER DEFAULT 0,
            competitors_with_site INTEGER DEFAULT 0,
            daily_loss REAL DEFAULT 0,
            monthly_loss REAL DEFAULT 0,
            lost_clients_low INTEGER DEFAULT 0,
            lost_clients_high INTEGER DEFAULT 0,
            collected_at TEXT NOT NULL,
            enriched INTEGER DEFAULT 0,
            audit_generated INTEGER DEFAULT 0,
            sent_step INTEGER DEFAULT 0,
            sent_channel TEXT,
            replied INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            step INTEGER NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            error TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
    """)
    conn.commit()
    conn.close()


def load_blacklist() -> set:
    if not os.path.exists(BLACKLIST_PATH):
        return set()
    with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item["source_id"] for item in data}


def is_blacklisted(source_id: str) -> bool:
    return source_id in _BLACKLIST


def lead_exists(source_id: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM leads WHERE source_id = ?", (source_id,)).fetchone()
    conn.close()
    return row is not None


def save_lead(data: dict) -> bool:
    if lead_exists(data["source_id"]):
        return False
    if is_blacklisted(data["source_id"]):
        return False
    conn = get_connection()
    conn.execute(
        """INSERT INTO leads (name, phone, email, city, category, source_id,
           rating_2gis, reviews_2gis, collected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["name"], data.get("phone", ""), data.get("email", ""),
            data["city"], data["category"], data["source_id"],
            data.get("rating_2gis"), data.get("reviews_2gis", 0),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return True


def update_lead_enrichment(source_id: str, enrichment: dict):
    conn = get_connection()
    conn.execute(
        """UPDATE leads SET
            search_visible=?, search_position=?, competitors_in_search=?,
            monthly_searches=?, cpc=?, competition=?,
            google_maps_claimed=?, google_rating=?, google_reviews=?,
            domain_suggestion=?, domain_available=?,
            competitors_total=?, competitors_with_site=?,
            daily_loss=?, monthly_loss=?,
            lost_clients_low=?, lost_clients_high=?,
            enriched=1
           WHERE source_id=?""",
        (
            enrichment.get("search_visible", 0),
            enrichment.get("search_position"),
            json.dumps(enrichment.get("competitors_in_search", []), ensure_ascii=False),
            enrichment.get("monthly_searches"),
            enrichment.get("cpc"),
            enrichment.get("competition"),
            enrichment.get("google_maps_claimed", 0),
            enrichment.get("google_rating"),
            enrichment.get("google_reviews", 0),
            json.dumps(enrichment.get("domain_suggestion", []), ensure_ascii=False),
            enrichment.get("domain_available"),
            enrichment.get("competitors_total", 0),
            enrichment.get("competitors_with_site", 0),
            enrichment.get("daily_loss", 0),
            enrichment.get("monthly_loss", 0),
            enrichment.get("lost_clients_low", 0),
            enrichment.get("lost_clients_high", 0),
            source_id,
        ),
    )
    conn.commit()
    conn.close()


def get_leads_for_audit(limit: int = 100) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM leads WHERE enriched = 1 AND audit_generated = 0 LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_audit_generated(lead_id: int):
    conn = get_connection()
    conn.execute("UPDATE leads SET audit_generated = 1 WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


def get_leads_for_sending(limit: int = 50) -> list[dict]:
    """Возвращает лидов с готовым аудитом, ещё не отправленных.
    Данные аудита (audit_text, audit_pdf_path, message_text) хранятся
    в audits.json и подтягиваются в sender.py отдельно."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM leads
           WHERE audit_generated = 1 AND sent_step = 0 AND replied = 0
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_outreach(lead_id: int, channel: str, step: int, status: str, error: str = None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO outreach (lead_id, channel, step, status, sent_at, error) VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, channel, step, status, datetime.now().isoformat(), error),
    )
    conn.execute(
        "UPDATE leads SET sent_step = ?, sent_channel = ? WHERE id = ?",
        (step, channel, lead_id),
    )
    conn.commit()
    conn.close()


def get_outreach_count(lead_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM outreach WHERE lead_id = ? AND status = 'delivered'",
        (lead_id,),
    ).fetchone()
    conn.close()
    return row[0]


def get_stats() -> dict:
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    enriched = conn.execute("SELECT COUNT(*) FROM leads WHERE enriched = 1").fetchone()[0]
    audited = conn.execute("SELECT COUNT(*) FROM leads WHERE audit_generated = 1").fetchone()[0]
    sent = conn.execute("SELECT COUNT(*) FROM leads WHERE sent_step > 0").fetchone()[0]
    replied = conn.execute("SELECT COUNT(*) FROM leads WHERE replied = 1").fetchone()[0]
    conn.close()
    return {
        "total": total, "enriched": enriched, "audited": audited,
        "sent": sent, "replied": replied,
    }


def export_leads_json(output_path: str):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM leads").fetchall()
    conn.close()
    leads = [dict(r) for r in rows]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    return len(leads)


# Initialize blacklist on import
_BLACKLIST = load_blacklist()


if __name__ == "__main__":
    init_db()
    print(f"DB at: {DB_PATH}")
    print(f"Blacklist: {len(_BLACKLIST)} companies")
