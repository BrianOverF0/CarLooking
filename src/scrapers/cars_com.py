"""
Cars.com scraper.

Uses their public search results page. Each result card contains JSON-ish
attributes in the HTML. We extract with BeautifulSoup rather than the
unofficial JSON API (which rotates often).

Filter scheme:
  https://www.cars.com/shopping/results/
    ?stock_type=used
    &maximum_distance=<miles>
    &zip=<zip>
    &transmission[]=Manual
    &list_price_max=<max>
    &makes[]=<make>
    &models[]=<make>-<model_slug>   (optional, we iterate all query-style)
    &keyword=<free text>
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import (
    make_session, polite_get, parse_price, parse_year, parse_mileage,
)

log = logging.getLogger(__name__)

BASE = "https://www.cars.com/shopping/results/"


def _build_url(criteria: dict, query: str, page: int = 1) -> str:
    params = [
        ("stock_type", "used"),
        ("maximum_distance", criteria.get("radius_miles", 200)),
        ("zip", criteria.get("zip_code", "75048")),
        ("transmission_slugs[]", "MANUAL"),   # newer param name
        ("transmission[]", "Manual"),
        ("list_price_max", criteria.get("max_price", 23000)),
        ("list_price_min", criteria.get("min_price", 2000)),
        ("keyword", query),
        ("page", page),
        ("page_size", 50),
        ("sort", "best_match_desc"),
    ]
    return f"{BASE}?{urlencode(params)}"


def _parse_results_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Listing] = []

    # Cars.com wraps each result in <div class="vehicle-card">
    cards = soup.select("div.vehicle-card")
    if not cards:
        # Fallback: data attributes on <a> tags
        cards = soup.select("a.vehicle-card-link")

    for card in cards:
        # Title / year-make-model
        title_el = card.select_one("h2.title, .vehicle-card-title, .title")
        title = title_el.get_text(strip=True) if title_el else ""

        # URL
        link_el = card.select_one("a.vehicle-card-link, a[href*='/vehicledetail/']")
        href = link_el.get("href") if link_el else None
        if not href:
            # some cards have no explicit link; skip
            continue
        url = href if href.startswith("http") else f"https://www.cars.com{href}"

        # Price
        price_el = card.select_one(".primary-price")
        price = parse_price(price_el.get_text(strip=True)) if price_el else None

        # Mileage
        miles_el = card.select_one(".mileage")
        mileage = parse_mileage(miles_el.get_text(strip=True)) if miles_el else None

        # Location
        loc_el = card.select_one(".dealer-name, .miles-from")
        location = loc_el.get_text(strip=True) if loc_el else None

        # Distance
        distance = None
        dist_el = card.select_one(".miles-from")
        if dist_el:
            m = re.search(r"([\d\.]+)\s*mi", dist_el.get_text())
            if m:
                try:
                    distance = float(m.group(1))
                except ValueError:
                    pass

        year = parse_year(title)

        out.append(Listing(
            source="cars.com",
            url=url,
            title=title,
            price=price,
            year=year,
            mileage=mileage,
            transmission="manual",   # we filtered for manual
            location=location,
            distance_miles=distance,
        ))

    return out


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    # Cars.com blocks generic user agents sometimes; add referrer
    session.headers.update({"Referer": "https://www.cars.com/"})

    out: list[Listing] = []
    seen_urls: set[str] = set()

    for model in target_models:
        for page in range(1, 3):  # first 2 pages is plenty per model
            url = _build_url(criteria, model, page=page)
            log.info("cars.com: %r page=%d", model, page)
            resp = polite_get(session, url, sleep=1.2, timeout=20)
            if resp is None:
                break
            listings = _parse_results_page(resp.text)
            if not listings:
                break
            added = 0
            for l in listings:
                if l.url in seen_urls:
                    continue
                seen_urls.add(l.url)
                l.model = model
                out.append(l)
                added += 1
            if added == 0:
                break

    log.info("cars.com total listings: %d", len(out))
    return out
