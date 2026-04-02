"""Enricher module — enriches lead data using Brave Search API and niche statistics."""

import json
import logging
import os
import re
import time

import requests
from transliterate import translit

from db import init_db, get_connection, update_lead_enrichment

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

NICHE_STATS = {
    "Шиномонтаж": {"avg_check": 2500, "conversion_low": 0.05, "conversion_high": 0.08, "mobile_search": 73},
    "Клининг": {"avg_check": 4000, "conversion_low": 0.08, "conversion_high": 0.12, "mobile_search": 81},
    "Ателье": {"avg_check": 3000, "conversion_low": 0.06, "conversion_high": 0.10, "mobile_search": 68},
    "Ремонт обуви": {"avg_check": 1500, "conversion_low": 0.07, "conversion_high": 0.10, "mobile_search": 71},
    "Ремонт телефонов": {"avg_check": 3500, "conversion_low": 0.10, "conversion_high": 0.15, "mobile_search": 85},
    "Массаж": {"avg_check": 3000, "conversion_low": 0.08, "conversion_high": 0.12, "mobile_search": 77},
    "Фотограф": {"avg_check": 5000, "conversion_low": 0.05, "conversion_high": 0.08, "mobile_search": 62},
    "Грузоперевозки": {"avg_check": 8000, "conversion_low": 0.04, "conversion_high": 0.07, "mobile_search": 58},
    "Автосервис": {"avg_check": 5000, "conversion_low": 0.06, "conversion_high": 0.09, "mobile_search": 76},
}

CITY_SEARCHES = {
    "Москва": 2.0, "Санкт-Петербург": 1.5,
    "Новосибирск": 1.1, "Екатеринбург": 1.0, "Казань": 1.0,
    "Нижний Новгород": 0.9, "Самара": 0.9,
    "Челябинск": 0.85, "Уфа": 0.85, "Красноярск": 0.8,
    "Ростов-на-Дону": 0.9, "Пермь": 0.8, "Волгоград": 0.8,
    "Краснодар": 0.9, "Саратов": 0.75, "Тольятти": 0.7,
    "Ярославль": 0.7, "Иркутск": 0.7, "Хабаровск": 0.7,
    "Владивосток": 0.7, "Оренбург": 0.7, "Томск": 0.65,
    "Кемерово": 0.65, "Астрахань": 0.65, "Набережные Челны": 0.6,
    "Пенза": 0.6, "Липецк": 0.6, "Киров": 0.6,
}

BASE_SEARCHES = {
    "Шиномонтаж": 3400, "Клининг": 2800, "Автосервис": 4500,
    "Ремонт телефонов": 3100, "Массаж": 2200, "Ателье": 1800,
    "Ремонт обуви": 1200, "Фотограф": 1900, "Грузоперевозки": 2600,
}

CITY_CODES = {
    "Москва": "msk", "Санкт-Петербург": "spb",
    "Казань": "kzn", "Самара": "smr", "Уфа": "ufa",
    "Челябинск": "chel", "Красноярск": "krsk",
    "Новосибирск": "nsk", "Екатеринбург": "ekb",
    "Нижний Новгород": "nn", "Ростов-на-Дону": "rnd",
    "Пермь": "prm", "Волгоград": "vlg", "Краснодар": "krd",
    "Саратов": "sar", "Тольятти": "tlt", "Ярославль": "yar",
    "Иркутск": "irk", "Хабаровск": "khv", "Владивосток": "vl",
    "Оренбург": "orb", "Томск": "tmsk", "Кемерово": "kmr",
    "Астрахань": "astr", "Набережные Челны": "nch",
    "Пенза": "pnz", "Липецк": "lpk", "Киров": "krv",
}

MAX_RETRIES = 3
RETRY_BACKOFF = 2


def _transliterate_slug(text: str) -> str:
    latin = translit(text, 'ru', reversed=True)
    slug = latin.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or "business"


def _generate_domain_suggestions(name: str, city: str, category: str) -> list[str]:
    name_slug = _transliterate_slug(name)
    city_slug = _transliterate_slug(city) if city else "city"
    category_slug = _transliterate_slug(category) if category else "biz"
    city_code = CITY_CODES.get(city, city_slug[:3])

    return [
        f"{name_slug}-{city_slug}.ru",
        f"{name_slug}{city_code}.ru",
        f"{category_slug}-{name_slug}.ru",
    ]


def _check_domain_available(domain: str, api_key: str) -> bool | None:
    """Check if domain is available via Brave Search. Returns None on error."""
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": f"site:{domain}", "count": 1},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("web", {}).get("results", [])
            return len(results) == 0
    except Exception:
        pass
    return None


def check_domains(domains: list[str], api_key: str) -> list[dict]:
    checked = []
    for d in domains:
        available = _check_domain_available(d, api_key)
        checked.append({"domain": d, "available": available})
        time.sleep(0.5)
    return checked


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_unenriched_leads() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE enriched = 0"
        ).fetchall()
    return [dict(r) for r in rows]


def _estimate_monthly_searches(category: str, city: str) -> int:
    base = BASE_SEARCHES.get(category, 2000)
    multiplier = CITY_SEARCHES.get(city, 0.7)
    return int(base * multiplier)


def _calculate_losses(category: str, city: str) -> dict:
    stats = NICHE_STATS.get(category)
    if stats is None:
        return {
            "daily_loss": None,
            "monthly_loss": None,
            "lost_clients_low": None,
            "lost_clients_high": None,
            "monthly_searches": _estimate_monthly_searches(category, city),
            "loss_estimated": False,
        }

    monthly_searches = _estimate_monthly_searches(category, city)
    avg_check = stats["avg_check"]
    conv_low = stats["conversion_low"]
    conv_high = stats["conversion_high"]

    lost_clients_low = int(monthly_searches * conv_low)
    lost_clients_high = int(monthly_searches * conv_high)

    monthly_loss_low = lost_clients_low * avg_check
    monthly_loss_high = lost_clients_high * avg_check
    monthly_loss = (monthly_loss_low + monthly_loss_high) / 2
    daily_loss = round(monthly_loss / 30, 2)

    return {
        "daily_loss": daily_loss,
        "monthly_loss": round(monthly_loss, 2),
        "lost_clients_low": lost_clients_low,
        "lost_clients_high": lost_clients_high,
        "monthly_searches": monthly_searches,
        "loss_estimated": True,
    }


def _brave_search(query: str, api_key: str) -> dict | None:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": 10}

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = RETRY_BACKOFF ** attempt
                logger.warning("Brave Search %d for '%s', retry in %ds", resp.status_code, query, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            wait = RETRY_BACKOFF ** attempt
            logger.warning("Brave Search connection error for '%s', retry in %ds", query, wait)
            time.sleep(wait)
        except requests.RequestException as exc:
            logger.warning("Brave Search request failed for '%s': %s", query, exc)
            return None

    logger.error("Brave Search failed after %d retries for '%s'", MAX_RETRIES, query)
    return None


def _parse_search_results(name: str, results: dict) -> dict:
    web_results = results.get("web", {}).get("results", [])

    search_visible = 0
    search_position = None
    competitors = []

    name_lower = name.lower()
    name_words = set(name_lower.split())

    for i, item in enumerate(web_results, start=1):
        title = item.get("title", "")
        description = item.get("description", "")
        title_lower = title.lower()
        desc_lower = description.lower()

        # Match if majority of name words appear in title/description
        title_words = set(title_lower.split())
        match_count = len(name_words & title_words)
        if match_count >= max(len(name_words) // 2, 1) or name_lower in title_lower or name_lower in desc_lower:
            if search_position is None:
                search_visible = 1
                search_position = i
        else:
            competitors.append(title)

    return {
        "search_visible": search_visible,
        "search_position": search_position,
        "competitors_in_search": competitors[:5],
        "competitors_total": len(web_results),
    }


def enrich_lead(lead: dict, api_key: str | None = None) -> dict:
    name = lead["name"]
    city = lead.get("city", "")
    category = lead.get("category", "")

    losses = _calculate_losses(category, city)
    domain_suggestions = _generate_domain_suggestions(name, city, category)

    # Check domain availability if API key is available
    domain_available = None
    if api_key:
        domain_check = check_domains(domain_suggestions[:1], api_key)
        if domain_check and domain_check[0]["available"] is not None:
            domain_available = 1 if domain_check[0]["available"] else 0

    enrichment = {
        "search_visible": 0,
        "search_position": None,
        "competitors_in_search": [],
        "monthly_searches": losses["monthly_searches"],
        "cpc": None,
        "competition": None,
        "google_maps_claimed": 0,
        "google_rating": None,
        "google_reviews": 0,
        "domain_suggestion": domain_suggestions,
        "domain_available": domain_available,
        "competitors_total": 0,
        "competitors_with_site": 0,
        "daily_loss": losses["daily_loss"] or 0,
        "monthly_loss": losses["monthly_loss"] or 0,
        "lost_clients_low": losses["lost_clients_low"] or 0,
        "lost_clients_high": losses["lost_clients_high"] or 0,
    }

    if api_key:
        query = f"{name} {city}"
        results = _brave_search(query, api_key)

        if results:
            search_data = _parse_search_results(name, results)
            enrichment["search_visible"] = search_data["search_visible"]
            enrichment["search_position"] = search_data["search_position"]
            enrichment["competitors_in_search"] = search_data["competitors_in_search"]
            enrichment["competitors_total"] = search_data["competitors_total"]

            aggregators = ["2gis", "yandex", "google", "yell", "zoon", "flamp", "otzovik"]
            web_results = results.get("web", {}).get("results", [])
            sites_count = sum(
                1 for r in web_results
                if not any(agg in r.get("url", "").lower() for agg in aggregators)
            )
            enrichment["competitors_with_site"] = sites_count

    return enrichment


def run(config: dict | None = None):
    if config is None:
        config = _load_config()

    init_db()

    api_key = config.get("brave_search_api_key") or config.get("brave_api_key") or os.environ.get("BRAVE_API_KEY")
    delay = config.get("enricher_delay", 1.0)

    if not api_key:
        logger.warning(
            "Brave API key not set — enrichment will use niche stats only."
        )

    leads = _get_unenriched_leads()
    if not leads:
        logger.info("No unenriched leads found.")
        return 0

    logger.info("Enriching %d leads...", len(leads))
    enriched_count = 0

    for lead in leads:
        try:
            enrichment = enrich_lead(lead, api_key=api_key)
            update_lead_enrichment(lead["source_id"], enrichment)
            enriched_count += 1
            logger.info(
                "Enriched [%d/%d]: %s — daily_loss=%.0f, monthly_loss=%.0f",
                enriched_count, len(leads),
                lead["name"], enrichment["daily_loss"], enrichment["monthly_loss"],
            )
        except Exception:
            logger.exception("Failed to enrich lead %s", lead.get("source_id"))

        if api_key:
            time.sleep(delay)

    logger.info("Enrichment complete: %d/%d leads enriched.", enriched_count, len(leads))
    return enriched_count


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = _load_config()
    total = run(config)
    print(f"Enriched {total} leads.")
