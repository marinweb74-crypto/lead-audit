"""
LeadAudit Telegram Bot — управление парсингом и рассылкой.

/start — статус
/parse — парсинг + обогащение + аудиты (50 лидов)
/send — запуск рассылки
/stats — статистика
/stop — остановить текущий процесс
/help — список команд
"""

import asyncio
import json
import logging
import os
import sys

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from db import init_db, get_stats

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("bot")

router = Router()
CONFIG = {}
_current_task = None


def is_authorized(message: Message) -> bool:
    uid = CONFIG.get("telegram_user_id", 0)
    return uid == 0 or message.from_user.id == uid


@router.message(CommandStart())
async def cmd_start(message: Message):
    if not is_authorized(message):
        await message.answer("У вас нет доступа к этому боту.")
        return
    stats = get_stats()
    await message.answer(
        "<b>LeadAudit Bot</b>\n\n"
        f"Лидов: {stats['total']}\n"
        f"Обогащено: {stats['enriched']}\n"
        f"Аудитов: {stats['audited']}\n"
        f"Отправлено: {stats['sent']}\n"
        f"Ответили: {stats['replied']}\n\n"
        "Команды:\n"
        "/parse — парсинг + аудиты (50 лидов)\n"
        "/send — рассылка\n"
        "/stats — статистика\n"
        "/stop — остановить\n"
        "/help — справка"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not is_authorized(message):
        await message.answer("У вас нет доступа к этому боту.")
        return
    await message.answer(
        "<b>Команды:</b>\n\n"
        "/start — статус и обзор\n"
        "/parse — парсинг 2ГИС + обогащение + генерация аудитов\n"
        "/send — запуск рассылки (Telegram + Email)\n"
        "/stats — текущая статистика по базе\n"
        "/stop — остановить текущий процесс"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_authorized(message):
        await message.answer("У вас нет доступа к этому боту.")
        return
    stats = get_stats()
    await message.answer(
        f"<b>Статистика</b>\n\n"
        f"Лидов в базе: {stats['total']}\n"
        f"Обогащено: {stats['enriched']}\n"
        f"Аудитов готово: {stats['audited']}\n"
        f"Отправлено: {stats['sent']}\n"
        f"Ответили: {stats['replied']}"
    )


@router.message(Command("parse"))
async def cmd_parse(message: Message):
    if not is_authorized(message):
        await message.answer("У вас нет доступа к этому боту.")
        return
    global _current_task
    if _current_task and not _current_task.done():
        await message.answer("Уже работает. /stop чтобы остановить.")
        return

    await message.answer("Запускаю парсинг + обогащение + проверка TG + аудиты (до 50 новых лидов)...")
    _current_task = asyncio.create_task(_run_parse(message))


async def _run_parse(message: Message):
    try:
        await message.answer("1/3 Парсинг 2ГИС...")
        from parser import run as run_parser
        stats_before = get_stats()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, run_parser, CONFIG)

        stats_after = get_stats()
        new = stats_after["total"] - stats_before["total"]
        await message.answer(f"Парсинг завершён. Новых лидов: {new}")

        await message.answer("2/4 Обогащение данных...")
        from enricher import run as run_enricher
        await loop.run_in_executor(None, run_enricher, CONFIG)

        stats_after = get_stats()
        await message.answer(f"Обогащение завершено. Обогащено: {stats_after['enriched']}")

        await message.answer("3/4 Проверка Telegram...")
        try:
            from tg_checker import check_leads
            tg_stats = await check_leads(CONFIG)
            await message.answer(
                f"Проверка завершена.\n"
                f"Проверено: {tg_stats['checked']}\n"
                f"Есть TG: {tg_stats['has_tg']}\n"
                f"Нет TG: {tg_stats['no_tg']}"
            )
        except Exception as e:
            await message.answer(f"Ошибка проверки TG: {e}. Продолжаю с аудитами...")
            log.exception("TG check error")

        await message.answer("4/4 Генерация аудитов (только для лидов с TG или email)...")
        from auditor import run as run_auditor
        await loop.run_in_executor(None, run_auditor, CONFIG)

        stats_after = get_stats()
        await message.answer(
            f"Готово!\n\n"
            f"Лидов: {stats_after['total']}\n"
            f"Обогащено: {stats_after['enriched']}\n"
            f"Аудитов: {stats_after['audited']}\n"
            f"Можно отправлять: /send"
        )
    except asyncio.CancelledError:
        await message.answer("Процесс остановлен.")
    except Exception as e:
        await message.answer(f"Ошибка: {type(e).__name__}: {e}")
        log.exception("Parse error")


@router.message(Command("send"))
async def cmd_send(message: Message):
    if not is_authorized(message):
        await message.answer("У вас нет доступа к этому боту.")
        return
    global _current_task
    if _current_task and not _current_task.done():
        await message.answer("Уже работает. /stop чтобы остановить.")
        return

    await message.answer("Запускаю рассылку...")
    _current_task = asyncio.create_task(_run_send(message))


async def _run_send(message: Message):
    try:
        from sender import run as run_sender
        stats = await run_sender(CONFIG)
        if stats:
            await message.answer(
                f"Рассылка завершена!\n\n"
                f"Telegram: {stats.get('telegram', 0)}\n"
                f"Email: {stats.get('email', 0)}\n"
                f"Пропущено: {stats.get('skipped', 0)}\n"
                f"Ошибки: {stats.get('errors', 0)}"
            )
        else:
            await message.answer("Нет аудитов для отправки. Сначала /parse")
    except asyncio.CancelledError:
        await message.answer("Рассылка остановлена.")
    except Exception as e:
        await message.answer(f"Ошибка: {type(e).__name__}: {e}")
        log.exception("Send error")


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    if not is_authorized(message):
        await message.answer("У вас нет доступа к этому боту.")
        return
    global _current_task
    if _current_task and not _current_task.done():
        _current_task.cancel()
        await message.answer("Останавливаю...")
    else:
        await message.answer("Ничего не запущено.")


async def main():
    global CONFIG
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)

    token = CONFIG.get("telegram_bot_token", "")
    if not token or token.startswith("YOUR_"):
        log.error("Bot token not set in config.json")
        return

    init_db()
    dp = Dispatcher()
    dp.include_router(router)
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    log.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
