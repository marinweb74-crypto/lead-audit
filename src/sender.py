"""
sender.py — Отправка аудитов через Telegram (Telethon) и Email (SMTP).

Поток:
1. Читает audits.json из agent-runtime/shared/
2. Для каждого лида проверяет возможность отправки (дедупликация, макс. 3 касания, интервал 3 дня)
3. Приоритет: Telegram > Email
4. Telegram: поиск пользователя по телефону через Telethon, отправка сообщения + PDF
5. Email: отправка через SMTP (smtplib, ssl) сообщения + PDF вложение
6. Записывает каждую отправку в outreach.json и БД
"""

import asyncio
import json
import os
import random
import re
import smtplib
import ssl
import logging
from datetime import datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.types import InputPhoneContact

from db import init_db, record_outreach, get_connection

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_DIR = os.path.join(PROJECT_ROOT, "agent-runtime", "shared")
AUDITS_PATH = os.path.join(SHARED_DIR, "audits.json")
OUTREACH_PATH = os.path.join(SHARED_DIR, "outreach.json")
LOG_PATH = os.path.join(SHARED_DIR, "sender-log.md")

MAX_TOUCHES = 3
TOUCH_INTERVAL_DAYS = 3
MAX_FIRST_TOUCH_PER_DAY = 50
DELAY_MIN = 30
DELAY_MAX = 60
SMTP_TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("sender")


def _is_valid_email(email: str) -> bool:
    if not email or not isinstance(email, str):
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def _safe_int(value, default=0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def get_touch_message(step: int, lead: dict, audit: dict) -> str:
    name = lead.get("name", "компания")
    monthly_loss = _safe_float(audit.get("monthly_loss") or lead.get("monthly_loss"))
    lost_clients_low = _safe_int(audit.get("lost_clients_low") or lead.get("lost_clients_low"))
    lost_clients_high = _safe_int(audit.get("lost_clients_high") or lead.get("lost_clients_high"))
    monthly_searches = _safe_int(audit.get("monthly_searches") or lead.get("monthly_searches"))
    competitors_with_site = _safe_int(audit.get("competitors_with_site") or lead.get("competitors_with_site"))

    if step == 1:
        custom_msg = audit.get("message_text", "")
        if custom_msg:
            return custom_msg
        return (
            f"Мы провели экспресс-аудит цифрового присутствия "
            f"компании «{name}».\n\n"
            f"\U0001F4CE Аудит во вложении.\n\n"
            f"Если не хотите открывать файл — напишите, "
            f"отправим всё в текстовом формате."
        )

    if step == 2:
        return (
            f"Добрый день! Напоминаю об аудите для «{name}».\n\n"
            f"Ключевые цифры из отчёта:\n"
            f"• Ежемесячные потери: ~{monthly_loss:,.0f} ₽\n"
            f"• Упущенные клиенты: {lost_clients_low}–{lost_clients_high} в месяц\n"
            f"• Запросов в месяц по вашей нише: {monthly_searches}\n"
            f"• Конкуренты с сайтом: {competitors_with_site}\n\n"
            f"Готов обсудить, как исправить ситуацию. Можем созвониться на 15 минут?"
        )

    if step == 3:
        return (
            f"Добрый день! Это последнее сообщение по аудиту «{name}».\n\n"
            f"Если сейчас не актуально — никаких проблем. Аудит останется у вас, "
            f"данные в нём актуальны ещё ~3 месяца.\n\n"
            f"Если появятся вопросы — пишите в любое время. Удачи в бизнесе!"
        )

    return ""


def get_email_subject(lead: dict, step: int) -> str:
    name = lead.get("name", "компания")
    city = lead.get("city", "")
    if step == 1:
        return f"Аудит цифрового присутствия — {name}, {city}"
    elif step == 2:
        return f"Напоминание: аудит для {name}"
    else:
        return f"Последнее сообщение — аудит {name}"


def load_outreach() -> list[dict]:
    if not os.path.exists(OUTREACH_PATH):
        return []
    with open(OUTREACH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_outreach(records: list[dict]):
    os.makedirs(os.path.dirname(OUTREACH_PATH), exist_ok=True)
    with open(OUTREACH_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def load_audits() -> list[dict]:
    if not os.path.exists(AUDITS_PATH):
        logger.error("audits.json не найден: %s", AUDITS_PATH)
        return []
    with open(AUDITS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def append_log(line: str):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def can_send(lead: dict, outreach_records: list[dict]) -> tuple[bool, int]:
    lead_id = lead.get("id")

    if lead.get("replied"):
        return False, 0

    lead_sends = [
        r for r in outreach_records
        if r.get("lead_id") == lead_id and r.get("status") == "delivered"
    ]

    current_step = len(lead_sends)

    if current_step >= MAX_TOUCHES:
        return False, 0

    if lead_sends:
        last_sent_at = max(r["sent_at"] for r in lead_sends)
        last_dt = datetime.fromisoformat(last_sent_at)
        if datetime.now() - last_dt < timedelta(days=TOUCH_INTERVAL_DAYS):
            return False, 0

    next_step = current_step + 1
    return True, next_step


def count_first_touches_today(outreach_records: list[dict]) -> int:
    today = datetime.now().date().isoformat()
    return sum(
        1 for r in outreach_records
        if r.get("step") == 1
        and r.get("status") == "delivered"
        and r.get("sent_at", "").startswith(today)
    )


async def send_telegram(client: TelegramClient, lead: dict, audit: dict, step: int) -> bool:
    phone = lead.get("phone", "").strip()
    if not phone:
        logger.warning("Лид %s (%s): нет телефона для Telegram", lead["id"], lead.get("name"))
        return False

    # Normalize phone format
    if phone.startswith("8") and len(phone) == 11:
        phone = "+7" + phone[1:]
    elif phone.startswith("7") and len(phone) == 11:
        phone = "+" + phone
    elif not phone.startswith("+"):
        phone = "+" + phone

    imported_user = None
    try:
        contact = InputPhoneContact(
            client_id=0,
            phone=phone,
            first_name=lead.get("name", "Контакт"),
            last_name="",
        )
        result = await client(ImportContactsRequest([contact]))

        if not result.users:
            logger.info("Лид %s (%s): пользователь не найден в Telegram по номеру %s",
                        lead["id"], lead.get("name"), phone)
            return False

        user = result.users[0]
        imported_user = user
        message_text = get_touch_message(step, lead, audit)

        await client.send_message(user, message_text)

        pdf_path = audit.get("audit_pdf_path", "")
        if pdf_path and os.path.exists(pdf_path) and step <= 2:
            await client.send_file(
                user,
                pdf_path,
                caption=f"Аудит — {lead.get('name', 'компания')}.pdf",
            )

        logger.info("Telegram OK: лид %s (%s), шаг %d", lead["id"], lead.get("name"), step)
        return True

    except errors.FloodWaitError as e:
        logger.error("FloodWaitError: нужно ждать %d сек. Останавливаем рассылку.", e.seconds)
        raise
    except Exception as e:
        logger.error("Telegram ошибка для лида %s: %s", lead.get("id"), e)
        return False
    finally:
        if imported_user:
            try:
                await client(DeleteContactsRequest(id=[imported_user]))
            except Exception:
                pass


def send_email(smtp_cfg: dict, lead: dict, audit: dict, step: int) -> bool:
    email_to = lead.get("email", "").strip()
    if not email_to or not _is_valid_email(email_to):
        logger.warning("Лид %s (%s): невалидный email '%s'", lead["id"], lead.get("name"), email_to)
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = f"{smtp_cfg['from_name']} <{smtp_cfg['email']}>"
        msg["To"] = email_to
        msg["Subject"] = get_email_subject(lead, step)

        body = get_touch_message(step, lead, audit)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        pdf_path = audit.get("audit_pdf_path", "")
        if pdf_path and os.path.exists(pdf_path) and step <= 2:
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            pdf_attachment = MIMEApplication(pdf_data, _subtype="pdf")
            pdf_filename = f"Аудит — {lead.get('name', 'компания')}.pdf"
            pdf_attachment.add_header(
                "Content-Disposition", "attachment", filename=pdf_filename
            )
            msg.attach(pdf_attachment)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_cfg["server"], smtp_cfg["port"],
                               context=context, timeout=SMTP_TIMEOUT) as server:
            server.login(smtp_cfg["email"], smtp_cfg["password"])
            server.sendmail(smtp_cfg["email"], email_to, msg.as_string())

        logger.info("Email OK: лид %s (%s), шаг %d", lead["id"], lead.get("name"), step)
        return True

    except Exception as e:
        logger.error("Email ошибка для лида %s: %s", lead.get("id"), e)
        return False


async def run(config: dict):
    init_db()
    append_log("Запуск sender")

    audits = load_audits()
    if not audits:
        logger.info("Нет аудитов для отправки.")
        append_log("Нет аудитов для отправки. Завершение.")
        return

    outreach_records = load_outreach()

    tg_cfg = config.get("telethon", {})
    tg_client = None

    try:
        if tg_cfg.get("api_id") and tg_cfg.get("api_hash"):
            session_path = os.path.join(PROJECT_ROOT, tg_cfg.get("session_name", "sender_session"))
            tg_client = TelegramClient(session_path, tg_cfg["api_id"], tg_cfg["api_hash"])
            await tg_client.start(phone=tg_cfg.get("phone"))
            logger.info("Telegram клиент подключён.")
        else:
            logger.warning("Telegram не настроен — будет использоваться только Email.")

        smtp_cfg = config.get("smtp", {})
        smtp_available = all(
            smtp_cfg.get(k) for k in ("server", "port", "email", "password", "from_name")
        )
        if not smtp_available:
            logger.warning("SMTP не настроен — Email отправка недоступна.")

        with get_connection() as conn:
            leads_rows = conn.execute("SELECT * FROM leads WHERE audit_generated = 1").fetchall()
        leads_by_id = {row["id"]: dict(row) for row in leads_rows}

        stats = {"telegram": 0, "email": 0, "skipped": 0, "errors": 0}
        flood_stopped = False

        for audit in audits:
            if flood_stopped:
                break

            lead_id = audit.get("lead_id")
            if not lead_id or lead_id not in leads_by_id:
                logger.warning("Аудит без валидного lead_id: %s", lead_id)
                stats["skipped"] += 1
                continue

            lead = leads_by_id[lead_id]

            first_touches_today = count_first_touches_today(outreach_records)
            if first_touches_today >= MAX_FIRST_TOUCH_PER_DAY:
                logger.info("Достигнут лимит %d первых касаний за сегодня. Останавливаем.",
                            MAX_FIRST_TOUCH_PER_DAY)
                append_log(f"Лимит {MAX_FIRST_TOUCH_PER_DAY} первых касаний за день достигнут.")
                break

            ok, step = can_send(lead, outreach_records)
            if not ok:
                stats["skipped"] += 1
                continue

            sent = False
            channel = None

            if tg_client and lead.get("phone") and lead.get("has_telegram", 1):
                try:
                    sent = await send_telegram(tg_client, lead, audit, step)
                    if sent:
                        channel = "telegram"
                except errors.FloodWaitError:
                    flood_stopped = True
                    record_outreach(lead_id, "telegram", step, "failed", error="flood_wait")
                    outreach_records.append({
                        "lead_id": lead_id, "name": lead.get("name"),
                        "channel": "telegram", "step": step, "status": "failed",
                        "sent_at": datetime.now().isoformat(), "error": "flood_wait",
                    })
                    append_log("FloodWaitError — рассылка остановлена.")
                    stats["errors"] += 1
                    break

            if not sent and smtp_available and lead.get("email"):
                sent = send_email(smtp_cfg, lead, audit, step)
                if sent:
                    channel = "email"

            if sent and channel:
                record_outreach(lead_id, channel, step, "delivered")
                outreach_records.append({
                    "lead_id": lead_id, "name": lead.get("name"),
                    "channel": channel, "step": step, "status": "delivered",
                    "sent_at": datetime.now().isoformat(),
                })
                stats[channel] += 1
                append_log(f"Отправлено: {lead.get('name')} | {channel} | шаг {step}")
            elif not sent:
                record_outreach(lead_id, "none", step, "failed", error="no_channel_available")
                outreach_records.append({
                    "lead_id": lead_id, "name": lead.get("name"),
                    "channel": "none", "step": step, "status": "failed",
                    "sent_at": datetime.now().isoformat(), "error": "no_channel_available",
                })
                stats["errors"] += 1

            if sent:
                delay = random.randint(DELAY_MIN, DELAY_MAX)
                logger.info("Пауза %d сек перед следующей отправкой...", delay)
                await asyncio.sleep(delay)

        save_outreach(outreach_records)

    finally:
        if tg_client:
            await tg_client.disconnect()

    summary = (
        f"Итого: Telegram={stats['telegram']}, Email={stats['email']}, "
        f"Пропущено={stats['skipped']}, Ошибки={stats['errors']}"
    )
    logger.info(summary)
    append_log(summary)
    append_log("Sender завершён.\n")

    return stats


if __name__ == "__main__":
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    if not os.path.exists(config_path):
        print(f"config.json не найден: {config_path}")
        print("Создайте config.json с секциями 'telethon' и 'smtp'.")
        exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    asyncio.run(run(config))
