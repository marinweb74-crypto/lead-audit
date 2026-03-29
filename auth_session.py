"""Скрипт авторизации Telethon. Запустите и введите код из Telegram."""
import asyncio
from telethon import TelegramClient

API_ID = 33368809
API_HASH = "3dc0cb001adb711629bf8fef2c591cb7"
PHONE = "+77068181615"
SESSION = "lead_audit_session2"


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(PHONE, force_sms=True)
        code = input("Введите код (SMS или Telegram): ")
        try:
            await client.sign_in(PHONE, code)
        except Exception as e:
            print(f"Ошибка: {e}")
            password = input("Если нужен пароль 2FA, введите: ")
            await client.sign_in(password=password)

    me = await client.get_me()
    print(f"Авторизован: {me.first_name} ({me.phone})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
