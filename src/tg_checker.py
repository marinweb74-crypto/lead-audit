"""
tg_checker.py — Check if leads have Telegram accounts by phone number.
Uses Telethon ImportContactsRequest, then deletes the contact.
"""

import asyncio
import logging
import os
import time

from telethon import TelegramClient
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.types import InputPhoneContact

from db import init_db, get_leads_for_tg_check, mark_tg_checked

logger = logging.getLogger("tg_checker")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DELAY = 1.5


async def check_leads(config: dict) -> dict:
    init_db()
    tg_cfg = config.get("telethon", {})
    if not tg_cfg.get("api_id") or not tg_cfg.get("api_hash"):
        logger.error("Telethon not configured")
        return {"checked": 0, "has_tg": 0, "no_tg": 0}

    session_path = os.path.join(PROJECT_ROOT, tg_cfg.get("session_name", "sender_session"))
    client = TelegramClient(session_path, tg_cfg["api_id"], tg_cfg["api_hash"])
    await client.start(phone=tg_cfg.get("phone"))

    # Auto-mark email-only leads (no phone) as checked
    from db import get_connection
    with get_connection() as conn:
        conn.execute("""UPDATE leads SET tg_checked=1, has_telegram=0
                        WHERE enriched=1 AND tg_checked=0
                        AND (phone IS NULL OR phone='')""")
        conn.commit()

    leads = get_leads_for_tg_check()
    if not leads:
        logger.info("No leads to check")
        await client.disconnect()
        return {"checked": 0, "has_tg": 0, "no_tg": 0}

    logger.info("Checking %d leads for Telegram", len(leads))
    stats = {"checked": 0, "has_tg": 0, "no_tg": 0}

    for lead in leads:
        phone = lead.get("phone", "").strip()
        if not phone:
            mark_tg_checked(lead["id"], False)
            stats["checked"] += 1
            stats["no_tg"] += 1
            continue

        try:
            contact = InputPhoneContact(
                client_id=0,
                phone=phone,
                first_name=lead.get("name", "Check"),
                last_name="",
            )
            result = await client(ImportContactsRequest([contact]))
            has_tg = bool(result.users)
            mark_tg_checked(lead["id"], has_tg)

            if has_tg:
                stats["has_tg"] += 1
                try:
                    await client(DeleteContactsRequest(id=[result.users[0]]))
                except Exception:
                    pass
            else:
                stats["no_tg"] += 1

            stats["checked"] += 1
            logger.info("[%d/%d] %s: %s", stats["checked"], len(leads),
                        lead.get("name", "?"), "TG" if has_tg else "no TG")

        except Exception as e:
            logger.error("Error checking %s: %s", lead.get("name"), e)
            # Don't mark as checked — leave for retry next run
            continue

        await asyncio.sleep(DELAY)

    await client.disconnect()
    logger.info("Done: %d checked, %d with TG, %d without", stats["checked"], stats["has_tg"], stats["no_tg"])
    return stats


def run(config: dict) -> dict:
    return asyncio.run(check_leads(config))
