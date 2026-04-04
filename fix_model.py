import json
import os

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

cfg["anthropic_model"] = "claude-haiku-4-5-20251001"

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

print("Fixed model to claude-haiku-4-5-20251001")
