import requests
r = requests.get("https://catalog.api.2gis.com/3.0/items", params={"key": "08ef163b-7c17-4bdc-81d1-227e4c2f066b", "q": "Шиномонтаж", "region_id": 32, "type": "branch", "page_size": 3})
print("Status:", r.status_code)
data = r.json()
items = data.get("result", {}).get("items", [])
print("Total:", data.get("result", {}).get("total", 0))
for item in items:
    print("-", item.get("name", "?"))
