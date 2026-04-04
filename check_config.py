import json
import os
import requests

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

key = cfg.get("anthropic_api_key", "MISSING")
model = cfg.get("anthropic_model", "MISSING")

print("anthropic_api_key:", key[:20] + "..." if len(key) > 20 else key)
print("anthropic_model:", model)

if key == "MISSING" or key.startswith("YOUR_"):
    print("ERROR: no anthropic key in config!")
else:
    print("\nTesting Claude API...")
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": model, "max_tokens": 50, "messages": [{"role": "user", "content": "Say hello in Russian"}]},
            headers={"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=30,
        )
        print("Status:", resp.status_code)
        if resp.status_code == 200:
            print("Response:", resp.json()["content"][0]["text"])
            print("API WORKS!")
        else:
            print("Error:", resp.text[:300])
    except Exception as e:
        print("Failed:", e)
