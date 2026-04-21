"""
Craigslist scraper.

Note: Craigslist disabled public RSS feeds (they now return 403). This scraper
instead hits the normal search page and parses the JSON-LD `ItemList` blob
that every results page embeds as:

    <script type="application/ld+json" id="ld_searchpage_results">...</script>

That blob has clean structured data — title, price, geo-coordinates, address,
and image URLs — without needing to scrape individual post pages.

We hit the Dallas site plus nearby CL regions (within ~200mi of Sachse, TX).
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import (
    make_session, polite_get, parse_price, parse_year, parse_mileage,
    detect_transmission,
)

log = logging.getLogger(__name__)

# Craigslist subdomains within ~200mi of Sachse, TX
CL_SITES = [
    "dallas", "easttexas", "waco", "texoma", "shreveport", "lawton", "oklahomacity",
]

# Sachse, TX
SACHSE_LAT = 32.9762
SACHSE_LON = -96.5952


def _build_search_url(site: str, criteria: dict, query: str) -> str:
    params = {
        "query": query,
        "hasPic": 1,
        "min_price": criteria.get("min_price", 2000),
        "max_price": criteria.get("max_price", 23000),
        "postal": criteria.get("zip_code", "75048"),
        "search_distance": criteria.get("radius_miles", 200),
        "auto_transmission": 1,   # 1 = manual
    }
    return f"https://{site}.craigslist.org/search/cta?{urlencode(params)}"


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _extract_results_jsonld(html: str) -> Optional[list]:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="ld_searchpage_results")
    if not tag or not tag.string:
        return None
    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError:
        return None
    return data.get("itemListElement") or []


# Craigslist JSON-LD names look like:  "2001 Mazda Miata - $8500 (Dallas)"
# Try to pull a URL from a nearby HTML card since JSON-LD doesn't include it.
def _url_for_item(item_name: str, html_cards: list[dict]) -> Optional[str]:
    # Fuzzy match title against card titles
    norm = re.sub(r"\s+", " ", item_name).lower()
    for card in html_cards:
        if card["title_norm"] and card["title_norm"] in norm:
            return card["url"]
        if norm and card["title_norm"] and norm[:40] in card["title_norm"]:
            return card["url"]
    return None


def _parse_html_cards(html: str) -> list[dict]:
    """Return simple [{url, title_norm}] list from the static result cards."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for card in soup.select("li.cl-static-search-result"):
        a = card.find("a", href=True)
        title_el = card.find("div", class_="title") or card.find("a")
        if not a or not title_el:
            continue
        out.append({
            "url": a["href"],
            "title_norm": re.sub(r"\s+", " ", title_el.get_text(" ", strip=True)).lower(),
        })
    # Newer layouts use li.cl-search-result or article
    if not out:
        for card in soup.select("li.cl-search-result, article.result-row"):
            a = card.find("a", href=True)
            if not a:
                continue
            out.append({
                "url": a["href"],
                "title_norm": re.sub(r"\s+", " ", a.get_text(" ", strip=True)).lower(),
            })
    return out


def _parse_item(item: dict, html_cards: list[dict], source_site: str) -> Optional[Listing]:
    inner = item.get("item", item)   # ld+json wraps each in {"item": ...}
    name = inner.get("name") or ""
    if not name:
        return None

    offer = inner.get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}

    price = None
    if offer:
        p = offer.get("price")
        try:
            price = int(float(p)) if p is not None else None
        except (ValueError, TypeError):
            price = None

    # Location + coords
    avail = offer.get("availableAtOrFrom", {}) if isinstance(offer, dict) else {}
    geo = avail.get("geo", {}) if isinstance(avail, dict) else {}
    lat, lon = geo.get("latitude"), geo.get("longitude")
    addr = avail.get("address", {}) if isinstance(avail, dict) else {}
    city = addr.get("addressLocality") if isinstance(addr, dict) else None
    state = addr.get("addressRegion") if isinstance(addr, dict) else None
    location = ", ".join(x for x in [city, state] if x) or None

    distance = None
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        try:
            distance = round(_haversine_miles(float(lat), float(lon),
                                              SACHSE_LAT, SACHSE_LON), 1)
        except (TypeError, ValueError):
            distance = None

    # URL not in JSON-LD; match against HTML cards by title
    url = _url_for_item(name, html_cards)
    if not url:
        # As a fallback, synthesize a search URL reference
        return None

    images = inner.get("image") or []
    if isinstance(images, str):
        images = [images]

    year = parse_year(name)
    mileage = parse_mileage(name)

    return Listing(
        source=f"craigslist/{source_site}",
        url=url,
        title=name,
        price=price or parse_price(name),
        year=year,
        mileage=mileage,
        transmission="manual",     # we filtered server-side
        location=location,
        distance_miles=distance,
        images=[u for u in images if isinstance(u, str)][:4],
    )


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    out: list[Listing] = []
    seen_urls: set[str] = set()

    for site in CL_SITES:
        for model in target_models:
            url = _build_search_url(site, criteria, model)
            log.info("CL %s q=%r", site, model)
            resp = polite_get(session, url, sleep=0.8, timeout=20)
            if resp is None:
                continue
            cards = _parse_html_cards(resp.text)
            items = _extract_results_jsonld(resp.text) or []
            for item in items:
                listing = _parse_item(item, cards, site)
                if not listing or listing.url in seen_urls:
                    continue
                seen_urls.add(listing.url)
                listing.model = model
                out.append(listing)

    log.info("Craigslist total listings: %d", len(out))
    return out
