import sys
import os
import json
import requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

key = cfg.get("2gis_api_key", "")

print("=== Test 1: without sort param (like test_api.py) ===")
r1 = requests.get("https://catalog.api.2gis.com/3.0/items", params={
    "key": key, "q": "Шиномонтаж", "region_id": 32,
    "type": "branch", "page_size": 5, "fields": "items.reviews"
}, timeout=15)
d1 = r1.json()
items1 = d1.get("result", {}).get("items", [])
print(f"Status: {r1.status_code}, items: {len(items1)}, total: {d1.get('result', {}).get('total', 0)}")

print("\n=== Test 2: with sort=relevance (like parser) ===")
r2 = requests.get("https://catalog.api.2gis.com/3.0/items", params={
    "key": key, "q": "Шиномонтаж", "region_id": 32,
    "type": "branch", "page": 1, "page_size": 50,
    "fields": "items.reviews", "sort": "relevance"
}, timeout=15)
d2 = r2.json()
items2 = d2.get("result", {}).get("items", [])
print(f"Status: {r2.status_code}, items: {len(items2)}, total: {d2.get('result', {}).get('total', 0)}")
if r2.status_code != 200 or "error" in d2:
    print("ERROR:", d2.get("error"))

print("\n=== Test 3: without sort, page_size=50 (like parser but no sort) ===")
r3 = requests.get("https://catalog.api.2gis.com/3.0/items", params={
    "key": key, "q": "Шиномонтаж", "region_id": 32,
    "type": "branch", "page": 1, "page_size": 50,
    "fields": "items.reviews"
}, timeout=15)
d3 = r3.json()
items3 = d3.get("result", {}).get("items", [])
print(f"Status: {r3.status_code}, items: {len(items3)}, total: {d3.get('result', {}).get('total', 0)}")

print("\n=== Test 4: raw response from parser's exact params ===")
r4 = requests.get("https://catalog.api.2gis.com/3.0/items", params={
    "key": key, "q": "Шиномонтаж", "region_id": 32,
    "type": "branch", "page": 1, "page_size": 50,
    "fields": "items.reviews", "sort": "relevance"
}, timeout=15)
print(f"Status: {r4.status_code}")
print("Response keys:", list(r4.json().keys()))
result = r4.json().get("result")
if result:
    print("Result keys:", list(result.keys()))
    print("Items count:", len(result.get("items", [])))
else:
    print("No 'result' key in response")
    print("Full response:", r4.text[:500])
