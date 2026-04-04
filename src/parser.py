"""
Parser: собирает компании из 2ГИС.
- API для быстрого получения списка фирм (rating, reviews)
- HTML-страница фирмы для контактов (phone, email, website)
Фильтрует: с сайтом, сетевые бренды, без контактов, дубли с blacklist.
"""

import json
import time
import logging
import os
import re
import requests

from db import init_db, save_lead, lead_exists, is_blacklisted, export_leads_json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
SHARED_DIR = os.path.join(PROJECT_ROOT, "agent-runtime", "shared")
LOG_PATH = os.path.join(SHARED_DIR, "parser-log.md")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, "parser.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

API_URL = "https://catalog.api.2gis.com/3.0/items"
FIRM_URL = "https://2gis.ru/{city_slug}/firm/{firm_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

REGION_IDS = {
    "moscow": 32, "spb": 2, "novosibirsk": 1, "ekaterinburg": 7,
    "kazan": 12, "nizhniy_novgorod": 22, "samara": 24, "chelyabinsk": 30,
    "ufa": 28, "rostov-na-donu": 49, "krasnoyarsk": 14, "perm": 23,
    "volgograd": 5, "krasnodar": 13, "saratov": 25, "tolyatti": 82,
    "yaroslavl": 31, "irkutsk": 9, "habarovsk": 29, "vladivostok": 4,
    "orenburg": 43, "tomsk": 27, "kemerovo": 11, "astrakhan": 124,
    "naberezhnye_chelny": 83, "penza": 46, "lipetsk": 64, "kirov": 67,
}

IGNORE_DOMAINS = [
    "2gis", "google", "yandex.ru", "facebook.com", "instagram.com",
    "vk.com", "t.me", "wa.me", "whatsapp.com", "youtube.com", "tiktok.com",
    "zoon.ru", "yell.ru", "flamp.ru",
]

KNOWN_CHAINS = [
    "колёса даром", "колеса даром", "тойота центр", "toyota", "шинный отель",
    "рольф", "автомир", "major", "мажор", "cdek", "сдэк", "dns", "мвидео",
    "м.видео", "эльдорадо", "fix price", "фикс прайс", "пятёрочка",
    "пятерочка", "магнит", "лента", "ашан", "леруа мерлен", "leroy merlin",
    "obi", "оби", "castorama", "кастрама", "ikea", "икеа", "metro",
    "колесо.ру", "bianca", "youdo", "profi.ru", "профи.ру",
]

SEASON_PEAKS = {
    "Шиномонтаж":      [3, 4, 10, 11],
    "Клининг":          [1, 3, 4, 12],
    "Автосервис":       [3, 4, 9, 10],
    "Ремонт телефонов": [9, 10, 12, 1],
    "Массаж":           [10, 11, 12],
    "Ателье":           [3, 4, 9, 10],
    "Ремонт обуви":     [3, 4, 9, 10],
    "Фотограф":         [5, 6, 9, 12],
    "Грузоперевозки":   [5, 6, 7, 8, 9],
}

SEASON_BOOST = 1.5

FREE_EMAIL_DOMAINS = [
    "gmail.com", "mail.ru", "yandex.ru", "ya.ru", "bk.ru", "inbox.ru",
    "list.ru", "rambler.ru", "hotmail.com", "outlook.com", "yahoo.com",
]

MIN_RATING = 3.5
MIN_REVIEWS = 3
QUALIFIED_MIN_RATING = 4.5
QUALIFIED_MIN_REVIEWS = 20

SKIP_NAME_PATTERNS = [
    "частный", "частная", "мастер ", "ип ", "индивидуальный",
    "на дому", "выезд", "мобильный шиномонтаж", "мобильный ремонт",
]


def is_solo_business(name: str) -> bool:
    n = name.lower().strip()
    return any(p in n for p in SKIP_NAME_PATTERNS)


def passes_quality_filters(name: str, rating, reviews: int, has_phone: bool) -> tuple[bool, str]:
    if not has_phone:
        return False, "no_phone"
    if is_solo_business(name):
        return False, "solo"
    if rating is not None and rating < MIN_RATING:
        return False, "low_rating"
    if reviews < MIN_REVIEWS:
        return False, "few_reviews"
    return True, "ok"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_known_chain(name: str) -> bool:
    name_lower = name.lower()
    return any(chain in name_lower for chain in KNOWN_CHAINS)


def _extract_rating(item: dict) -> tuple:
    reviews_data = item.get("reviews", {})
    rating = reviews_data.get("general_rating")
    review_count = reviews_data.get("general_review_count", 0)
    return rating, review_count


def _has_website_from_api(item: dict) -> bool:
    """Check if 2GIS API item contains a real website link."""
    # Check links field
    links = item.get("links", [])
    if isinstance(links, list):
        for link in links:
            url = link.get("value", "") or link.get("url", "") or ""
            if url and not any(d in url.lower() for d in IGNORE_DOMAINS):
                return True
    # Check external_content
    ext = item.get("external_content", [])
    if isinstance(ext, list):
        for e in ext:
            url = e.get("url", "") or e.get("value", "") or ""
            if url and not any(d in url.lower() for d in IGNORE_DOMAINS):
                return True
    return False


def _fetch_contacts_from_html(city_slug: str, firm_id: str, session: requests.Session) -> dict:
    """Fetch phone, email, website from 2GIS firm page HTML."""
    result = {"phone": "", "email": "", "has_website": False}

    url = FIRM_URL.format(city_slug=city_slug, firm_id=firm_id)
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            return result
        html = resp.text

        phones = re.findall(r'tel:([+\d\-() ]+)', html)
        if phones:
            cleaned = re.sub(r"[^\d+]", "", phones[0])
            if len(cleaned) >= 10:
                result["phone"] = cleaned

        emails = re.findall(r'mailto:([^"&]+)', html)
        if emails:
            result["email"] = emails[0].lower().strip()

        # Multiple patterns to catch website links
        all_urls = set()

        # Pattern 1: explicit website class/data-testid
        site_pattern = r'href="(https?://[^"]+)"[^>]*(?:class="[^"]*website|data-testid="[^"]*website)'
        all_urls.update(re.findall(site_pattern, html))

        # Pattern 2: JSON "website" field
        all_urls.update(re.findall(r'"website":\s*"(https?://[^"]+)"', html))

        # Pattern 3: "url" field in JSON context near website
        all_urls.update(re.findall(r'"type":\s*"website"[^}]*"value":\s*"(https?://[^"]+)"', html))
        all_urls.update(re.findall(r'"value":\s*"(https?://[^"]+)"[^}]*"type":\s*"website"', html))

        # Pattern 4: links with "site" or "website" nearby
        all_urls.update(re.findall(r'(?:site|website|сайт)[^"]*"(https?://[^"]+)"', html, re.IGNORECASE))
        all_urls.update(re.findall(r'"(https?://[^"]+)"[^"]*(?:site|website|сайт)', html, re.IGNORECASE))

        # Pattern 5: any href that looks like a business website (has its own domain)
        hrefs = re.findall(r'href="(https?://[^"]+)"', html)
        for h in hrefs:
            if not any(d in h.lower() for d in IGNORE_DOMAINS):
                # Skip if it's a 2GIS internal link or common non-business URL
                if '/firm/' not in h and '/geo/' not in h and 'catalog' not in h:
                    all_urls.add(h)

        for site_url in all_urls:
            if not any(d in site_url.lower() for d in IGNORE_DOMAINS):
                result["has_website"] = True
                log.debug("Found website for %s: %s", firm_id, site_url)
                break

    except Exception as e:
        log.debug("Failed to fetch contacts for %s: %s", firm_id, e)

    return result


def _search_2gis(api_key: str, query: str, region_id: int,
                 page: int = 1, page_size: int = 10) -> dict | None:
    params = {
        "key": api_key,
        "q": query,
        "region_id": region_id,
        "type": "branch",
        "page": page,
        "page_size": page_size,
        "fields": "items.reviews,items.links,items.external_content",
    }

    try:
        resp = requests.get(API_URL, params=params, timeout=15)
        if resp.status_code == 403:
            log.error("2GIS API: access denied (invalid or expired key)")
            return None
        if resp.status_code == 429:
            log.warning("2GIS API: rate limited, waiting 5s...")
            time.sleep(5)
            resp = requests.get(API_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error("2GIS API request failed: %s", e)
        return None


def collect_from_api(api_key: str, city_slug: str, city_name: str,
                     category: str, max_items: int, max_pages: int = 10) -> dict:
    region_id = REGION_IDS.get(city_slug)
    if not region_id:
        log.warning("Unknown city slug: %s — skipping", city_slug)
        return _empty_stats()

    stats = _empty_stats()
    session = requests.Session()
    session.headers.update(HEADERS)

    for page_num in range(1, max_pages + 1):
        if stats["saved"] >= max_items:
            break

        log.info("  API page %d for %s / %s...", page_num, city_name, category)
        data = _search_2gis(api_key, category, region_id, page=page_num, page_size=10)

        if not data:
            stats["errors"] += 1
            break

        result = data.get("result", {})
        items = result.get("items", [])

        if not items:
            log.info("  No more results on page %d", page_num)
            break

        total_available = result.get("total", 0)
        log.info("  Page %d: %d items (total available: %d)", page_num, len(items), total_available)

        for item in items:
            if stats["saved"] >= max_items:
                break

            stats["found"] += 1
            source_id = str(item.get("id", ""))
            name = item.get("name", "").strip()

            if not source_id or not name:
                stats["errors"] += 1
                continue

            if is_blacklisted(source_id):
                stats["skipped_blacklist"] += 1
                continue

            if lead_exists(source_id):
                stats["skipped_dupe"] += 1
                continue

            if is_known_chain(name):
                stats["skipped_chain"] += 1
                continue

            rating, review_count = _extract_rating(item)

            # Pre-filter by rating/reviews before fetching contacts
            if rating is not None and rating < MIN_RATING:
                stats["skipped_low_rating"] += 1
                continue
            if review_count < MIN_REVIEWS:
                stats["skipped_few_reviews"] += 1
                continue
            if is_solo_business(name):
                stats["skipped_solo"] += 1
                continue

            # Check website from API first (fast)
            if _has_website_from_api(item):
                stats["skipped_site"] += 1
                log.debug("Skipped %s: has website (API)", name)
                continue

            # Fetch contacts from HTML page
            contacts = _fetch_contacts_from_html(city_slug, source_id, session)
            time.sleep(0.3)

            if contacts["has_website"]:
                stats["skipped_site"] += 1
                log.debug("Skipped %s: has website (HTML)", name)
                continue

            if not contacts["phone"] and not contacts["email"]:
                stats["skipped_no_contacts"] += 1
                continue

            is_qualified = (
                (rating is not None and rating >= QUALIFIED_MIN_RATING)
                and review_count >= QUALIFIED_MIN_REVIEWS
            )

            saved = save_lead({
                "name": name,
                "phone": contacts["phone"],
                "email": contacts["email"],
                "city": city_name,
                "category": category,
                "source_id": source_id,
                "rating_2gis": rating,
                "reviews_2gis": review_count,
                "qualified": 1 if is_qualified else 0,
            })

            if saved:
                stats["saved"] += 1
                log.info("  NEW: %s | %s | %s | %s", name, contacts["phone"], city_name, category)

        time.sleep(0.5)

    session.close()
    return stats


def _empty_stats() -> dict:
    return {"found": 0, "saved": 0, "skipped_site": 0, "skipped_chain": 0,
            "skipped_no_contacts": 0, "skipped_blacklist": 0, "skipped_dupe": 0,
            "skipped_no_phone": 0, "skipped_solo": 0, "skipped_low_rating": 0,
            "skipped_few_reviews": 0, "errors": 0}


def write_parser_log(all_stats: list[dict]):
    os.makedirs(SHARED_DIR, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("# Parser Log\n\n")
        total_found = sum(s["found"] for s in all_stats)
        total_saved = sum(s["saved"] for s in all_stats)
        f.write(f"**Total found:** {total_found}\n")
        f.write(f"**Total saved:** {total_saved}\n\n")
        for s in all_stats:
            f.write(f"## {s['city']} / {s['category']}\n")
            f.write(f"- Found: {s['found']}\n")
            f.write(f"- Saved: {s['saved']}\n")
            f.write(f"- Skipped (has site): {s['skipped_site']}\n")
            f.write(f"- Skipped (chain): {s['skipped_chain']}\n")
            f.write(f"- Skipped (no contacts): {s['skipped_no_contacts']}\n")
            f.write(f"- Skipped (blacklist): {s['skipped_blacklist']}\n")
            f.write(f"- Skipped (duplicate): {s['skipped_dupe']}\n")
            f.write(f"- Skipped (no phone): {s.get('skipped_no_phone', 0)}\n")
            f.write(f"- Skipped (solo/private): {s.get('skipped_solo', 0)}\n")
            f.write(f"- Skipped (low rating): {s.get('skipped_low_rating', 0)}\n")
            f.write(f"- Skipped (few reviews): {s.get('skipped_few_reviews', 0)}\n")
            f.write(f"- Errors: {s['errors']}\n\n")


def _is_peak_season(category: str) -> bool:
    from datetime import datetime
    month = datetime.now().month
    return month in SEASON_PEAKS.get(category, [])


PROGRESS_PATH = os.path.join(PROJECT_ROOT, "parser_progress.json")
BATCH_LIMIT = 50


def _load_progress() -> dict:
    if os.path.exists(PROGRESS_PATH):
        try:
            with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"city_idx": 0, "cat_idx": 0}


def _save_progress(city_idx: int, cat_idx: int):
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump({"city_idx": city_idx, "cat_idx": cat_idx}, f)


def run(config: dict):
    cities = config["cities"]
    categories = config["categories"]
    max_pages = config.get("max_pages", 10)
    batch_limit = config.get("parse_batch_limit", BATCH_LIMIT)

    api_key = config.get("2gis_api_key", "")
    if not api_key or api_key.startswith("YOUR_"):
        log.error("2GIS API key not set in config.json (field: 2gis_api_key)")
        return []

    cat_names = [c if isinstance(c, str) else c["name"] for c in categories]
    cat_names.sort(key=lambda c: (0 if _is_peak_season(c) else 1))

    progress = _load_progress()
    start_city = progress.get("city_idx", 0)
    start_cat = progress.get("cat_idx", 0)

    all_stats = []
    init_db()
    total_saved = 0

    for ci in range(len(cities)):
        city_idx = (start_city + ci) % len(cities)
        city = cities[city_idx]

        for cj in range(len(cat_names)):
            if ci == 0:
                cat_idx = (start_cat + cj) % len(cat_names)
            else:
                cat_idx = cj
            cat_name = cat_names[cat_idx]

            if total_saved >= batch_limit:
                _save_progress(city_idx, cat_idx)
                log.info("Batch limit %d reached, stopping. Resume next /parse", batch_limit)
                break

            remaining = batch_limit - total_saved
            is_peak = _is_peak_season(cat_name)
            tag = " [PEAK SEASON]" if is_peak else ""
            log.info("=== %s / %s%s (need %d more) ===", city["name"], cat_name, tag, remaining)

            try:
                stats = collect_from_api(
                    api_key, city["slug"], city["name"], cat_name,
                    remaining, max_pages,
                )
                stats["city"] = city["name"]
                stats["category"] = cat_name
                stats["peak_season"] = is_peak
                all_stats.append(stats)
                total_saved += stats["saved"]
                log.info("Result: saved=%d found=%d from %s / %s (total: %d/%d)",
                         stats["saved"], stats["found"], city["name"], cat_name,
                         total_saved, batch_limit)
            except Exception as e:
                log.error("Error %s/%s: %s", city["name"], cat_name, e)
                empty = _empty_stats()
                empty["city"] = city["name"]
                empty["category"] = cat_name
                empty["errors"] = 1
                all_stats.append(empty)
            time.sleep(1)
        else:
            continue
        break

    if total_saved >= batch_limit:
        pass
    else:
        _save_progress(0, 0)
        log.info("All cities/categories processed, resetting progress")

    write_parser_log(all_stats)

    leads_path = os.path.join(SHARED_DIR, "leads.json")
    count = export_leads_json(leads_path)
    log.info("Exported %d leads to %s", count, leads_path)
    log.info("Batch done: %d new leads saved", total_saved)

    return all_stats


if __name__ == "__main__":
    cfg = load_config()
    run(cfg)
