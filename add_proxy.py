import json
import os

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

cfg["proxy"] = {
    "host": "168.0.212.20",
    "port": 9539,
    "user": "47dxkm",
    "password": "coL1y8"
}

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

print("Added proxy to config.json")
