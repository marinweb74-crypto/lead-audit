import asyncio, json
from telethon import TelegramClient

with open("config.json") as f:
    cfg = json.load(f)

tg = cfg["telethon"]

async def main():
    client = TelegramClient(tg["session_name"], tg["api_id"], tg["api_hash"])
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"OK! Authorized as {me.first_name} ({me.phone})")
    else:
        print("FAIL: not authorized")
    await client.disconnect()

asyncio.run(main())
