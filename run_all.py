#!/usr/bin/env python3
"""
run_all.py — Main coordinator script for the LeadAudit pipeline.

Runs the full pipeline sequentially:
  1. Load config.json
  2. Initialize DB
  3. Parse leads from 2GIS
  4. Enrich leads with search/domain data
  5. Generate AI audits (requires Gemini API key)
  6. Send outreach via Telegram/Email (requires telethon credentials)
  7. Generate final report to agent-runtime/outputs/report.md
"""

import asyncio
import sys
import os
import json
import logging
import traceback
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

LOG_PATH = os.path.join(PROJECT_ROOT, "run_all.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("run_all")

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "agent-runtime", "outputs")
REPORT_PATH = os.path.join(OUTPUTS_DIR, "report.md")


def _placeholder(value) -> bool:
    if value is None:
        return True
    v = str(value).strip()
    return v == "" or v.startswith("YOUR_")


def _banner(title: str):
    sep = "=" * 60
    log.info(sep)
    log.info("  %s", title)
    log.info(sep)


def main():
    started_at = datetime.now()
    errors: list[str] = []

    # Step 1 — Load config
    _banner("Step 1/7: Loading config.json")
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        log.info("Config loaded successfully (%d top-level keys)", len(config))
    except Exception as exc:
        log.error("FATAL: Cannot load config.json — %s", exc)
        print("\nPipeline aborted: config.json is required.")
        return

    # Detect available credentials
    gemini_keys = config.get("gemini_api_keys", [])
    has_gemini_key = bool(gemini_keys and not _placeholder(gemini_keys[0])) or not _placeholder(config.get("gemini_api_key"))
    telethon_cfg = config.get("telethon", {})
    has_telethon = (
        not _placeholder(telethon_cfg.get("api_id"))
        and not _placeholder(telethon_cfg.get("api_hash"))
        and not _placeholder(telethon_cfg.get("phone"))
    )
    smtp_cfg = config.get("smtp", {})
    has_smtp = (
        not _placeholder(smtp_cfg.get("email"))
        and not _placeholder(smtp_cfg.get("password"))
    )

    log.info("Credentials: gemini=%s  telethon=%s  smtp=%s",
             "OK" if has_gemini_key else "MISSING",
             "OK" if has_telethon else "MISSING",
             "OK" if has_smtp else "MISSING")

    # Step 2 — Initialize DB
    _banner("Step 2/7: Initializing database")
    try:
        from db import init_db, get_stats
        init_db()
        stats_before = get_stats()
        log.info("DB initialized. Current stats: %s", stats_before)
    except Exception as exc:
        log.error("FATAL: DB init failed — %s", exc)
        traceback.print_exc()
        print("\nPipeline aborted: database is required.")
        return

    # Step 3 — Parser
    _banner("Step 3/7: Running parser (2GIS)")
    parse_ok = False
    try:
        from parser import run as run_parser
        run_parser(config)
        stats_after_parse = get_stats()
        new_parsed = stats_after_parse["total"] - stats_before["total"]
        log.info("Parser done. Total leads: %d (new: %d)", stats_after_parse["total"], new_parsed)
        parse_ok = True
    except Exception as exc:
        log.error("Parser failed: %s", exc)
        errors.append(f"Parser error: {exc}")
        traceback.print_exc()

    # Step 4 — Enricher
    _banner("Step 4/7: Running enricher")
    try:
        from enricher import run as run_enricher
        run_enricher(config)
        stats_after_enrich = get_stats()
        new_enriched = stats_after_enrich["enriched"] - stats_before["enriched"]
        log.info("Enricher done. Total enriched: %d (new: %d)", stats_after_enrich["enriched"], new_enriched)
    except Exception as exc:
        log.error("Enricher failed: %s", exc)
        errors.append(f"Enricher error: {exc}")
        traceback.print_exc()

    # Step 5 — Auditor (requires Gemini API key)
    _banner("Step 5/7: Running auditor")
    if not has_gemini_key:
        log.warning("SKIPPED: Gemini API key is missing or placeholder.")
    else:
        try:
            from auditor import run as run_auditor
            run_auditor(config)
            stats_after_audit = get_stats()
            new_audited = stats_after_audit["audited"] - stats_before["audited"]
            log.info("Auditor done. Total audited: %d (new: %d)", stats_after_audit["audited"], new_audited)
        except Exception as exc:
            log.error("Auditor failed: %s", exc)
            errors.append(f"Auditor error: {exc}")
            traceback.print_exc()

    # Step 6 — Sender (requires Telethon or SMTP credentials)
    _banner("Step 6/7: Running sender")
    if not has_telethon and not has_smtp:
        log.warning("SKIPPED: No Telethon or SMTP credentials configured.")
    else:
        try:
            from sender import run as run_sender
            asyncio.run(run_sender(config))
            stats_after_send = get_stats()
            new_sent = stats_after_send["sent"] - stats_before["sent"]
            log.info("Sender done. Total sent: %d (new: %d)", stats_after_send["sent"], new_sent)
        except Exception as exc:
            log.error("Sender failed: %s", exc)
            errors.append(f"Sender error: {exc}")
            traceback.print_exc()

    # Step 7 — Generate final report
    _banner("Step 7/7: Generating report")
    try:
        final_stats = get_stats()
        _generate_report(config, stats_before, final_stats, started_at, errors)
        log.info("Report saved to %s", REPORT_PATH)
    except Exception as exc:
        log.error("Report generation failed: %s", exc)
        errors.append(f"Report error: {exc}")
        traceback.print_exc()

    elapsed = datetime.now() - started_at
    _banner("Pipeline complete")
    log.info("Total time: %s", str(elapsed).split(".")[0])
    if errors:
        log.warning("Completed with %d error(s):", len(errors))
        for i, err in enumerate(errors, 1):
            log.warning("  %d. %s", i, err)
    else:
        log.info("All steps completed successfully.")


def _generate_report(config: dict, stats_before: dict, stats_after: dict,
                     started_at: datetime, errors: list[str]):
    os.makedirs(OUTPUTS_DIR, exist_ok=True)

    finished_at = datetime.now()
    elapsed = finished_at - started_at

    new_parsed = stats_after["total"] - stats_before["total"]
    new_enriched = stats_after["enriched"] - stats_before["enriched"]
    new_audited = stats_after["audited"] - stats_before["audited"]
    new_sent = stats_after["sent"] - stats_before["sent"]

    cities = [c["name"] for c in config.get("cities", [])]
    categories = config.get("categories", [])

    tg_sent = 0
    email_sent = 0
    try:
        from db import get_connection
        with get_connection() as conn:
            tg_sent = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE channel = 'telegram' AND status = 'delivered'"
            ).fetchone()[0]
            email_sent = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE channel = 'email' AND status = 'delivered'"
            ).fetchone()[0]
    except Exception:
        pass

    error_section = ""
    if errors:
        lines = [f"- {err}" for err in errors]
        error_section = "\n".join(lines)
    else:
        error_section = "No errors."

    report = f"""# LeadAudit Pipeline Report

**Date:** {finished_at.strftime('%Y-%m-%d %H:%M:%S')}
**Duration:** {str(elapsed).split('.')[0]}

## Configuration

- **Cities:** {', '.join(cities) if cities else 'N/A'}
- **Categories:** {', '.join(categories) if categories else 'N/A'}
- **Leads per category:** {config.get('leads_per_category', 'N/A')}

## Results

| Metric | Before | After | New |
|--------|--------|-------|-----|
| Companies parsed | {stats_before['total']} | {stats_after['total']} | {new_parsed} |
| Enriched | {stats_before['enriched']} | {stats_after['enriched']} | {new_enriched} |
| Audits generated | {stats_before['audited']} | {stats_after['audited']} | {new_audited} |
| Sent (total) | {stats_before['sent']} | {stats_after['sent']} | {new_sent} |

### Sending breakdown

- **Telegram:** {tg_sent} delivered
- **Email:** {email_sent} delivered

## Cumulative stats

- **Total leads in DB:** {stats_after['total']}
- **Enriched:** {stats_after['enriched']}
- **Audited:** {stats_after['audited']}
- **Sent:** {stats_after['sent']}
- **Replied:** {stats_after['replied']}

## Errors and issues

{error_section}
"""

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
