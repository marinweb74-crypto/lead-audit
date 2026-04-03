import sys
import os
import json
import sqlite3
import requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "src")

print("=== 1. CONFIG ===")
try:
    with open("config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    print("OK: config.json loaded")
    print("  2gis_api_key:", cfg.get("2gis_api_key", "MISSING")[:8] + "...")
    print("  cities:", len(cfg.get("cities", [])))
    print("  categories:", cfg.get("categories", []))
except Exception as e:
    print("FAIL:", e)
    sys.exit(1)

print("\n=== 2. DATABASE ===")
try:
    from db import init_db, get_stats
    init_db()
    stats = get_stats()
    print("OK: DB initialized")
    for k, v in stats.items():
        print(f"  {k}: {v}")
except Exception as e:
    print("FAIL:", e)
    sys.exit(1)

print("\n=== 3. 2GIS API TEST ===")
try:
    key = cfg.get("2gis_api_key", "")
    r = requests.get("https://catalog.api.2gis.com/3.0/items", params={
        "key": key, "q": "Шиномонтаж", "region_id": 32,
        "type": "branch", "page_size": 5, "fields": "items.reviews"
    }, timeout=15)
    print("Status:", r.status_code)
    data = r.json()
    if "error" in data:
        print("API ERROR:", data["error"])
    else:
        items = data.get("result", {}).get("items", [])
        total = data.get("result", {}).get("total", 0)
        print(f"OK: {total} total results, got {len(items)} items")
        for item in items[:3]:
            name = item.get("name", "?")
            reviews = item.get("reviews", {})
            rating = reviews.get("general_rating", "?")
            count = reviews.get("general_review_count", 0)
            print(f"  - {name} (rating: {rating}, reviews: {count})")
except Exception as e:
    print("FAIL:", e)

print("\n=== 4. PARSER TEST (1 city, 1 category, 5 items) ===")
try:
    from parser import collect_from_api, init_db as p_init
    p_init()
    test_stats = collect_from_api(key, "moscow", "Москва", "Шиномонтаж", max_items=5, max_pages=1)
    print("Result:")
    for k, v in test_stats.items():
        print(f"  {k}: {v}")
except Exception as e:
    print("FAIL:", e)
    import traceback
    traceback.print_exc()

print("\n=== 5. DB AFTER PARSE ===")
try:
    stats2 = get_stats()
    for k, v in stats2.items():
        print(f"  {k}: {v}")
except Exception as e:
    print("FAIL:", e)

print("\n=== DONE ===")
