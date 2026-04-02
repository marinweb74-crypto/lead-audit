"""
Parser: собирает компании из 2ГИС через Playwright.
Фильтрует: с сайтом, сетевые бренды, без контактов, дубли с blacklist.
"""

import json
import ipaddress
import socket
import time
import logging
import os
import re
import requests as http_requests
from urllib.parse import quote
from playwright.sync_api import sync_playwright

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

BASE_URL = "https://2gis.ru"

IGNORE_DOMAINS = [
    "2gis", "google", "yandex.ru/maps", "facebook.com", "instagram.com",
    "vk.com", "t.me", "wa.me", "whatsapp.com", "youtube.com", "tiktok.com",
    "otello.ru", "zoon.ru", "yell.ru", "flamp.ru", "tugis.ru",
]

KNOWN_CHAINS = [
    "колёса даром", "колеса даром", "тойота центр", "toyota", "шинный отель",
    "рольф", "автомир", "major", "мажор", "cdek", "сдэк", "dns", "мвидео",
    "м.видео", "эльдорадо", "fix price", "фикс прайс", "пятёрочка",
    "пятерочка", "магнит", "лента", "ашан", "леруа мерлен", "leroy merlin",
    "obi", "оби", "castorama", "кастрама", "ikea", "икеа", "metro",
    "колесо.ру", "bianca", "youdo", "profi.ru", "профи.ру",
]

FREE_EMAIL_DOMAINS = [
    "gmail.com", "mail.ru", "yandex.ru", "ya.ru", "bk.ru", "inbox.ru",
    "list.ru", "rambler.ru", "hotmail.com", "outlook.com", "yahoo.com",
]

MIN_RATING = 4.0
MIN_REVIEWS = 5

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


def _is_valid_email(email: str) -> bool:
    if not email or not isinstance(email, str):
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def has_own_domain_email(email: str) -> bool:
    if not _is_valid_email(email):
        return False
    domain = email.split("@")[1].lower()
    return domain not in FREE_EMAIL_DOMAINS


def _is_private_ip(hostname: str) -> bool:
    """Check if hostname resolves to a private/reserved IP."""
    try:
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
    except (socket.gaierror, ValueError):
        return True


def verify_website_by_email_domain(email: str) -> bool:
    if not _is_valid_email(email):
        return False
    domain = email.split("@")[1].lower()
    if domain in FREE_EMAIL_DOMAINS:
        return False
    if _is_private_ip(domain):
        return False
    try:
        resp = http_requests.head(f"https://{domain}", timeout=5, allow_redirects=True)
        return resp.status_code < 400
    except Exception:
        try:
            resp = http_requests.head(f"http://{domain}", timeout=5, allow_redirects=True)
            return resp.status_code < 400
        except Exception:
            return False


def extract_source_id(href: str) -> str:
    match = re.search(r"/firm/(\d+)", href)
    return match.group(1) if match else ""


def has_real_website(page) -> bool:
    for link in page.query_selector_all("a[href]"):
        href = link.get_attribute("href") or ""
        if not href.startswith("http"):
            continue
        if any(domain in href for domain in IGNORE_DOMAINS):
            continue
        return True
    return False


def read_contacts(page) -> dict:
    result = {"phone": "", "email": "", "has_website": False, "rating": None, "reviews": 0}

    phone_links = page.query_selector_all('a[href^="tel:"]')
    if phone_links:
        result["phone"] = (phone_links[0].get_attribute("href") or "").replace("tel:", "")

    email_links = page.query_selector_all('a[href^="mailto:"]')
    if email_links:
        result["email"] = (email_links[0].get_attribute("href") or "").replace("mailto:", "")

    result["has_website"] = has_real_website(page)

    try:
        rating_el = page.query_selector('[class*="rating"] [class*="value"]')
        if rating_el:
            text = rating_el.inner_text().strip().replace(",", ".")
            result["rating"] = float(text)
    except Exception as e:
        log.debug("Failed to parse rating: %s", e)

    try:
        review_el = page.query_selector('[class*="rating"] [class*="count"]')
        if review_el:
            text = review_el.inner_text().strip()
            nums = re.findall(r"\d+", text)
            if nums:
                result["reviews"] = int(nums[0])
    except Exception as e:
        log.debug("Failed to parse reviews: %s", e)

    return result


def get_firms_on_page(page) -> list[dict]:
    firm_links = page.query_selector_all('a[href*="/firm/"]')
    firms = []
    seen = set()
    for link in firm_links:
        href = link.get_attribute("href") or ""
        sid = extract_source_id(href)
        if sid and sid not in seen:
            seen.add(sid)
            name = ""
            try:
                name = link.inner_text().strip()
            except Exception:
                pass
            firms.append({"source_id": sid, "name": name})
    return firms


def go_to_next_page(page, current_page: int) -> bool:
    next_page = str(current_page + 1)
    try:
        btn = page.query_selector(f'button:text-is("{next_page}")')
        if not btn:
            btn = page.query_selector(f'a:text-is("{next_page}")')
        if btn:
            btn.click()
            page.wait_for_timeout(3000)
            return True
    except Exception as e:
        log.error("Failed to go to page %s: %s", next_page, e)
    return False


def collect_from_search(page, city_slug: str, city_name: str, category: str,
                        max_items: int, max_pages: int = 5) -> dict:
    encoded_cat = quote(category)
    url = f"{BASE_URL}/{city_slug}/search/{encoded_cat}"
    log.info("Opening: %s", url)

    page.goto(url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    # Check for captcha/block
    page_text = page.inner_text("body")[:500].lower() if page.query_selector("body") else ""
    if "captcha" in page_text or "заблокирован" in page_text:
        log.warning("Captcha or block detected on %s/%s", city_name, category)
        return {"found": 0, "saved": 0, "skipped_site": 0, "skipped_chain": 0,
                "skipped_no_contacts": 0, "skipped_blacklist": 0, "skipped_dupe": 0,
                "skipped_no_phone": 0, "skipped_solo": 0, "skipped_low_rating": 0,
                "skipped_few_reviews": 0, "errors": 1}

    stats = {"found": 0, "saved": 0, "skipped_site": 0, "skipped_chain": 0,
             "skipped_no_contacts": 0, "skipped_blacklist": 0, "skipped_dupe": 0,
             "skipped_no_phone": 0, "skipped_solo": 0, "skipped_low_rating": 0,
             "skipped_few_reviews": 0, "errors": 0}

    current_page = 1

    while current_page <= max_pages and stats["saved"] < max_items:
        firms = get_firms_on_page(page)
        log.info("Page %d: found %d firms", current_page, len(firms))

        if not firms:
            break

        for firm in firms:
            if stats["saved"] >= max_items:
                break

            stats["found"] += 1

            if is_blacklisted(firm["source_id"]):
                stats["skipped_blacklist"] += 1
                continue

            if lead_exists(firm["source_id"]):
                stats["skipped_dupe"] += 1
                continue

            try:
                firm_url = f"{BASE_URL}/{city_slug}/firm/{firm['source_id']}"
                page.goto(firm_url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
            except Exception as e:
                log.error("Failed to open %s: %s", firm["name"], e)
                stats["errors"] += 1
                continue

            contacts = read_contacts(page)

            if contacts["has_website"]:
                stats["skipped_site"] += 1
                continue

            if not contacts["phone"] and not contacts["email"]:
                stats["skipped_no_contacts"] += 1
                continue

            if is_known_chain(firm["name"]):
                stats["skipped_chain"] += 1
                continue

            if contacts["email"] and has_own_domain_email(contacts["email"]):
                if verify_website_by_email_domain(contacts["email"]):
                    stats["skipped_site"] += 1
                    continue

            passed, reason = passes_quality_filters(
                firm["name"], contacts["rating"], contacts["reviews"], bool(contacts["phone"])
            )
            if not passed:
                key = f"skipped_{reason}"
                if key in stats:
                    stats[key] += 1
                log.info("  Skipped (%s): %s | rating=%s reviews=%s",
                         reason, firm["name"], contacts["rating"], contacts["reviews"])
                continue

            saved = save_lead({
                "name": firm["name"] or "Unknown",
                "phone": contacts["phone"],
                "email": contacts["email"],
                "city": city_name,
                "category": category,
                "source_id": firm["source_id"],
                "rating_2gis": contacts["rating"],
                "reviews_2gis": contacts["reviews"],
            })

            if saved:
                stats["saved"] += 1
                log.info("NEW: %s | %s | %s | %s", firm["name"], contacts["phone"], city_name, category)

            time.sleep(1.5)

        # Navigate to next page directly instead of re-navigating from page 1
        if current_page < max_pages and stats["saved"] < max_items:
            if not go_to_next_page(page, current_page):
                break

        current_page += 1

    return stats


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


def run(config: dict):
    cities = config["cities"]
    categories = config["categories"]
    max_items = config.get("leads_per_category", 30)
    max_pages = config.get("max_pages", 5)
    all_stats = []

    init_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        for city in cities:
            for cat in categories:
                cat_name = cat if isinstance(cat, str) else cat["name"]
                log.info("=== %s / %s ===", city["name"], cat_name)
                try:
                    stats = collect_from_search(
                        page, city["slug"], city["name"], cat_name,
                        max_items, max_pages,
                    )
                    stats["city"] = city["name"]
                    stats["category"] = cat_name
                    all_stats.append(stats)
                    log.info("Result: %d new from %s / %s", stats["saved"], city["name"], cat_name)
                except Exception as e:
                    log.error("Error %s/%s: %s", city["name"], cat_name, e)
                    all_stats.append({
                        "city": city["name"], "category": cat_name,
                        "found": 0, "saved": 0, "skipped_site": 0, "skipped_chain": 0,
                        "skipped_no_contacts": 0, "skipped_blacklist": 0, "skipped_dupe": 0,
                        "skipped_no_phone": 0, "skipped_solo": 0, "skipped_low_rating": 0,
                        "skipped_few_reviews": 0, "errors": 1,
                    })
                time.sleep(2)

        browser.close()

    write_parser_log(all_stats)

    leads_path = os.path.join(SHARED_DIR, "leads.json")
    count = export_leads_json(leads_path)
    log.info("Exported %d leads to %s", count, leads_path)

    return all_stats


if __name__ == "__main__":
    cfg = load_config()
    run(cfg)
