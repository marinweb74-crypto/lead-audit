"""Скрипт авторизации Telethon. Запустите и введите код из Telegram."""
import asyncio
import json
import os
from telethon import TelegramClient

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _load_telethon_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("telethon", {})


async def main():
    cfg = _load_telethon_config()
    api_id = cfg.get("api_id")
    api_hash = cfg.get("api_hash")
    phone = cfg.get("phone")
    session = cfg.get("session_name", "lead_audit_session")

    if not all([api_id, api_hash, phone]):
        print("Telethon credentials not set in config.json")
        return

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(phone, force_sms=True)
        code = input("Введите код (SMS или Telegram): ")
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            print(f"Ошибка: {e}")
            password = input("Если нужен пароль 2FA, введите: ")
            await client.sign_in(password=password)

    me = await client.get_me()
    print(f"Авторизован: {me.first_name} ({me.phone})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
