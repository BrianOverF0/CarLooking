"""
AutoTrader.com scraper.

Search URL:
  https://www.autotrader.com/cars-for-sale/all-cars
    ?searchRadius=200&zip=75048&maxPrice=23000
    &transmissionCodes=MAN&keyword=<free text>

The results page embeds its entire Redux state under:
  __NEXT_DATA__ → props → pageProps → __eggsState → inventory

That `inventory` is a dict keyed by listing ID, each value containing the
full vehicle record (title, make, model, year, mileage, price, owner/dealer,
distance). We walk that dict directly.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import make_session, polite_get, parse_year, parse_mileage, parse_price

log = logging.getLogger(__name__)

BASE = "https://www.autotrader.com/cars-for-sale/all-cars"


def _build_url(criteria: dict, query: str, first_record: int = 0) -> str:
    params = [
        ("searchRadius", criteria.get("radius_miles", 200)),
        ("zip", criteria.get("zip_code", "75048")),
        ("maxPrice", criteria.get("max_price", 23000)),
        ("minPrice", criteria.get("min_price", 2000)),
        ("transmissionCodes", "MAN"),
        ("keyword", query),
        ("numRecords", 50),
        ("firstRecord", first_record),
        ("sortBy", "relevance"),
    ]
    return f"{BASE}?{urlencode(params)}"


def _extract_next_data(html: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def _coerce_price(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        val = int(v)
    else:
        s = str(v).replace("$", "").replace(",", "").strip()
        if not s:
            return None
        try:
            val = int(float(s))
        except ValueError:
            return None
    return val if 500 <= val <= 500_000 else None


def _coerce_mileage(v: Any) -> Optional[int]:
    if v is None:
        return None
    s = str(v).replace(",", "").replace(" mi", "").replace("mi", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return None


def _normalize(listing_id: str, item: dict) -> Optional[Listing]:
    if not isinstance(item, dict):
        return None

    title = item.get("title") or item.get("listingTitle") or ""
    if isinstance(title, list):
        title = " ".join(str(t) for t in title)

    # Year / make / model may be top-level or nested under specifications
    year = item.get("year") or parse_year(str(title))
    make = item.get("make")
    model = item.get("model")
    trim = item.get("trim")

    # Price: try several known locations
    price = None
    for path in [
        ("pricingDetail", "salePrice"),
        ("pricingDetail", "primary"),
        ("price",),
        ("pricing", "salePrice"),
        ("pricing", "primary"),
    ]:
        cur: Any = item
        for k in path:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                cur = None
                break
        if cur:
            if isinstance(cur, dict):
                cur = cur.get("unformattedValue") or cur.get("value") or cur.get("price")
            price = _coerce_price(cur)
            if price:
                break

    # Fallback: scan pricingHistory for latest price
    if not price:
        hist = item.get("pricingHistory")
        if isinstance(hist, list) and hist:
            price = _coerce_price(hist[-1].get("price"))

    mileage = _coerce_mileage(item.get("mileage") or item.get("mileageValue"))

    # Distance
    distance = item.get("distance") or item.get("distanceFromZip")
    if isinstance(distance, dict):
        distance = distance.get("value") or distance.get("unformattedValue")
    try:
        distance = float(distance) if distance is not None else None
    except (ValueError, TypeError):
        distance = None

    # Location from owner
    owner = item.get("owner") or {}
    loc = None
    if isinstance(owner, dict):
        addr = owner.get("location", {}).get("address", {}) if isinstance(owner.get("location"), dict) else {}
        if isinstance(addr, dict):
            city = addr.get("city")
            state = addr.get("state")
            loc = ", ".join(x for x in [city, state] if x) or None

    # URL — autotrader exposes internal path; construct the canonical detail URL
    path = item.get("detailsPageUrl") or item.get("vdpUrl")
    if isinstance(path, dict):
        path = path.get("href") or path.get("url")
    if path and not str(path).startswith("http"):
        url = f"https://www.autotrader.com{path}"
    elif path:
        url = str(path)
    else:
        url = f"https://www.autotrader.com/cars-for-sale/vehicledetails.xhtml?listingId={listing_id}"

    # Infer a short title if none
    if not title:
        pieces = [str(year), make, model, trim]
        title = " ".join(p for p in pieces if p)

    # Skip ad tiles masquerading as inventory
    tile_type = item.get("tileType")
    if tile_type and "EXPERIAN" in str(tile_type).upper():
        return None

    return Listing(
        source="autotrader",
        url=url,
        title=str(title),
        price=price,
        year=year if isinstance(year, int) else parse_year(str(title)),
        make=str(make) if isinstance(make, str) else None,
        model=str(model) if isinstance(model, str) else None,
        mileage=mileage,
        transmission="manual",
        location=loc,
        distance_miles=distance,
        raw_id=str(listing_id),
    )


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://www.autotrader.com/"})

    out: list[Listing] = []
    seen_ids: set[str] = set()

    for model in target_models:
        for page in range(2):
            first = page * 50
            url = _build_url(criteria, model, first_record=first)
            log.info("autotrader: %r first=%d", model, first)
            resp = polite_get(session, url, sleep=1.5, timeout=25)
            if resp is None:
                break

            data = _extract_next_data(resp.text)
            if not data:
                log.debug("autotrader: no __NEXT_DATA__ on %s", url)
                break

            inventory = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("__eggsState", {})
                    .get("inventory", {})
            )
            if not isinstance(inventory, dict):
                break

            added = 0
            for listing_id, item in inventory.items():
                if listing_id in seen_ids:
                    continue
                listing = _normalize(str(listing_id), item)
                if not listing:
                    continue
                seen_ids.add(listing_id)
                if not listing.model:
                    listing.model = model
                out.append(listing)
                added += 1

            if added == 0:
                break

    log.info("autotrader total listings: %d", len(out))
    return out
