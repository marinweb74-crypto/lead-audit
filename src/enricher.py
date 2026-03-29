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

# ---------- Niche statistics ----------

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
    "Казань": 1.0, "Самара": 0.9, "Уфа": 0.85, "Челябинск": 0.85,
    "Красноярск": 0.8, "Новосибирск": 1.1, "Екатеринбург": 1.0,
}

BASE_SEARCHES = {
    "Шиномонтаж": 3400, "Клининг": 2800, "Автосервис": 4500,
    "Ремонт телефонов": 3100, "Массаж": 2200, "Ателье": 1800,
    "Ремонт обуви": 1200, "Фотограф": 1900, "Грузоперевозки": 2600,
}

CITY_CODES = {
    "Казань": "kzn",
    "Самара": "smr",
    "Уфа": "ufa",
    "Челябинск": "chel",
    "Красноярск": "krsk",
    "Новосибирск": "nsk",
    "Екатеринбург": "ekb",
}


# ---------- Helpers ----------

def _transliterate_slug(text: str) -> str:
    """Transliterate Russian text to Latin and make a URL-safe slug."""
    latin = translit(text, 'ru', reversed=True)
    slug = latin.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


def _generate_domain_suggestions(name: str, city: str, category: str) -> list[str]:
    """Generate 3 domain suggestions for a lead.

    Returns:
        [
            "{name_transliterated}-{city}.ru",           # best option
            "{name_transliterated}{city_code}.ru",        # name + region code
            "{category_transliterated}-{name_transliterated}.ru",  # category + name
        ]
    """
    name_slug = _transliterate_slug(name)
    city_slug = _transliterate_slug(city) if city else "city"
    category_slug = _transliterate_slug(category) if category else "biz"
    city_code = CITY_CODES.get(city, city_slug[:3])

    return [
        f"{name_slug}-{city_slug}.ru",
        f"{name_slug}{city_code}.ru",
        f"{category_slug}-{name_slug}.ru",
    ]


def _check_domain_available(domain: str, api_key: str) -> bool:
    """Check if domain is available via Brave Search (site: query)."""
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": f"site:{domain}", "count": 1},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("web", {}).get("results", [])
            return len(results) == 0  # No results = likely available
    except Exception:
        pass
    return True  # Assume available on error


def check_domains(domains: list[str], api_key: str) -> list[dict]:
    """Check availability of multiple domains. Returns list of {domain, available}."""
    checked = []
    for d in domains:
        available = _check_domain_available(d, api_key)
        checked.append({"domain": d, "available": available})
        time.sleep(0.5)
    return checked


def _load_config() -> dict:
    """Load configuration from config.json."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_unenriched_leads() -> list[dict]:
    """Fetch all leads that have not been enriched yet."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM leads WHERE enriched = 0"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _estimate_monthly_searches(category: str, city: str) -> int:
    """Estimate monthly search volume for a category in a given city."""
    base = BASE_SEARCHES.get(category, 2000)
    multiplier = CITY_SEARCHES.get(city, 0.7)
    return int(base * multiplier)


def _calculate_losses(category: str, city: str) -> dict:
    """Calculate estimated daily and monthly losses from niche stats."""
    stats = NICHE_STATS.get(category)
    if stats is None:
        return {
            "daily_loss": 0,
            "monthly_loss": 0,
            "lost_clients_low": 0,
            "lost_clients_high": 0,
            "monthly_searches": 0,
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
    }


def _brave_search(query: str, api_key: str) -> dict | None:
    """Search Brave Search API. Returns parsed JSON response or None on error."""
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": 10}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("Brave Search request failed for '%s': %s", query, exc)
        return None


def _parse_search_results(name: str, results: dict) -> dict:
    """Parse Brave Search results to determine search visibility and competitors."""
    web_results = results.get("web", {}).get("results", [])

    search_visible = 0
    search_position = None
    competitors = []

    for i, item in enumerate(web_results, start=1):
        title = item.get("title", "")
        url = item.get("url", "")
        description = item.get("description", "")

        # Check if the lead's name appears in any result
        if name.lower() in title.lower() or name.lower() in description.lower():
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


# ---------- Main enrichment ----------

def enrich_lead(lead: dict, api_key: str | None = None) -> dict:
    """Enrich a single lead with search data and loss calculations.

    Returns an enrichment dict suitable for update_lead_enrichment().
    """
    name = lead["name"]
    city = lead.get("city", "")
    category = lead.get("category", "")

    # Calculate losses from niche stats (always available)
    losses = _calculate_losses(category, city)

    # Generate 3 domain suggestions
    domain_suggestions = _generate_domain_suggestions(name, city, category)

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
        "domain_available": 1,
        "competitors_total": 0,
        "competitors_with_site": 0,
        "daily_loss": losses["daily_loss"],
        "monthly_loss": losses["monthly_loss"],
        "lost_clients_low": losses["lost_clients_low"],
        "lost_clients_high": losses["lost_clients_high"],
    }

    # If Brave API key is available, search for the business
    if api_key:
        query = f"{name} {city}"
        results = _brave_search(query, api_key)

        if results:
            search_data = _parse_search_results(name, results)
            enrichment["search_visible"] = search_data["search_visible"]
            enrichment["search_position"] = search_data["search_position"]
            enrichment["competitors_in_search"] = search_data["competitors_in_search"]
            enrichment["competitors_total"] = search_data["competitors_total"]

            # Count competitors that have their own website (non-aggregator)
            aggregators = ["2gis", "yandex", "google", "yell", "zoon", "flamp", "otzovik"]
            web_results = results.get("web", {}).get("results", [])
            sites_count = sum(
                1 for r in web_results
                if not any(agg in r.get("url", "").lower() for agg in aggregators)
            )
            enrichment["competitors_with_site"] = sites_count

    return enrichment


def run(config: dict | None = None):
    """Run the enrichment pipeline for all unenriched leads."""
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

        # Respect rate limits when using Brave API
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
