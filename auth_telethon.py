import json
import os
from telethon import TelegramClient

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

tg = cfg["telethon"]
session_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), tg.get("session_name", "lead_audit_session"))

client = TelegramClient(session_path, tg["api_id"], tg["api_hash"])
client.start(phone=tg["phone"])
print("Telethon authorized! Session saved.")
client.disconnect()
