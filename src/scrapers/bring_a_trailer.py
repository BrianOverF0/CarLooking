"""
Bring a Trailer scraper.

BaT ignores the anon `?search=` query param; the public /auctions/ page
always returns every active auction. We fetch it once, filter the bootstrap
JSON client-side by target model match, then enrich each match with a quick
detail-page fetch to get the seller's city/state.

Key fields in BaT's bootstrap items:
  - title, url, year, era
  - current_bid (int), current_bid_formatted, current_bid_label ("Bid:" | "Sold for:")
  - sold_text  ("" while active, e.g. "Sold for $X" when finished)
  - categories (list of numeric IDs) — category 379 = automobilia/parts/wheels/signs
  - lat, lon  (seller coordinates)
  - country, country_code
  - excerpt   (short description)

Non-car filter: any listing whose categories contains "379" is an accessory
(hardtops, wheels, neon signs, etc.) and gets skipped.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import (
    make_session, polite_get, parse_price, parse_year, parse_mileage,
    detect_transmission, title_matches_model,
)

log = logging.getLogger(__name__)

BASE = "https://bringatrailer.com/auctions/"

# Sachse, TX
SACHSE_LAT = 32.9762
SACHSE_LON = -96.5952

# BaT category IDs for non-car items (Automobilia/Parts)
NONCAR_CATEGORIES = {"379", "380"}

# Per-run detail fetch cap to keep requests bounded
DETAIL_FETCH_CAP = 60


def _build_url(query: str = "") -> str:
    return f"{BASE}?{urlencode({'search': query})}" if query else BASE


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _extract_balanced_json(text: str, start_idx: int) -> Optional[str]:
    if start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start_idx, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx:i + 1]
    return None


def _parse_bootstrap(html: str) -> list[dict]:
    items: list[dict] = []
    for marker in ["auctionsCurrentInitialData", "auctionsCompletedInitialData"]:
        idx = html.find(marker)
        if idx < 0:
            continue
        brace_idx = html.find("{", idx)
        if brace_idx < 0:
            continue
        blob = _extract_balanced_json(html, brace_idx)
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        its = data.get("items") if isinstance(data, dict) else None
        if isinstance(its, list):
            items.extend(its)
    return items


def _is_car(item: dict) -> bool:
    """Reject automobilia / parts / wheels / signs (BaT category 379/380)."""
    cats = item.get("categories") or []
    if not isinstance(cats, list):
        return True
    for c in cats:
        if str(c) in NONCAR_CATEGORIES:
            return False
    # Also reject items with no year at all — those are almost always accessories
    year = item.get("year")
    if not year or not str(year).strip():
        return False
    return True


def _item_to_listing(item: dict) -> Optional[Listing]:
    url = item.get("url")
    title = item.get("title") or ""
    if not url or not title:
        return None

    year_raw = item.get("year")
    try:
        year = int(year_raw) if year_raw else parse_year(title)
    except (TypeError, ValueError):
        year = parse_year(title)

    # Price + type
    sold_text = item.get("sold_text") or ""
    bid_label = str(item.get("current_bid_label") or "").lower()
    current_bid = item.get("current_bid")
    price: Optional[int] = None
    price_type = "bid"
    if sold_text:
        # Completed auction — extract the final price from sold_text
        price = parse_price(sold_text)
        price_type = "sold"
    elif current_bid:
        try:
            price = int(current_bid)
        except (TypeError, ValueError):
            price = parse_price(item.get("current_bid_formatted") or "")
        price_type = "sold" if "sold" in bid_label else "bid"

    # Coords + distance
    distance = None
    try:
        lat = float(item.get("lat")) if item.get("lat") else None
        lon = float(item.get("lon")) if item.get("lon") else None
        if lat is not None and lon is not None:
            distance = round(_haversine_miles(lat, lon, SACHSE_LAT, SACHSE_LON), 1)
    except (TypeError, ValueError):
        pass

    # Transmission hint from title (most BaT titles include "5-Speed" / "6-Speed Manual" / "PDK" etc.)
    trans_from_title = detect_transmission(title)

    thumbnail = item.get("thumbnail_url") or item.get("image_url") or item.get("photo_url") or ""
    images = [thumbnail] if thumbnail else []

    # Auction end date — BaT bootstrap may expose closing_at (Unix timestamp),
    # date_close (ISO string), or ends_at. Try all variants.
    auction_ends: Optional[str] = None
    for key in ("closing_at", "date_close", "ends_at", "close_date", "end_date"):
        raw_end = item.get(key)
        if not raw_end:
            continue
        try:
            if isinstance(raw_end, (int, float)):
                # Unix timestamp
                auction_ends = datetime.fromtimestamp(raw_end, tz=timezone.utc).isoformat()
            else:
                # Already a string — store as-is
                auction_ends = str(raw_end)
            break
        except (ValueError, OSError, OverflowError):
            continue

    return Listing(
        source="bring_a_trailer",
        url=url,
        title=title,
        price=price,
        price_type=price_type,
        year=year if isinstance(year, int) else None,
        mileage=parse_mileage(title),
        transmission=trans_from_title,     # may be upgraded/overridden by detail page
        location=item.get("country"),      # overridden by detail page when possible
        distance_miles=distance,
        description=item.get("excerpt") or "",
        images=images,
        auction_ends=auction_ends,
        raw_id=str(item.get("id") or ""),
    )


# BaT wraps the location text in an anchor pointing to Google Maps, e.g.
#   <strong>Location</strong>: <a href="...">Lancaster, Pennsylvania 17603</a>
# There's an earlier "Located in United States" widget on the page that looks
# similar but has no colon — the `\s*:\s*` requirement skips that one.
LOCATION_RE = re.compile(
    r"<strong[^>]*>Location</strong>\s*:\s*(?:<a[^>]*>)?([^<\n]+?)(?:</a|</|<br|\n)",
    re.I,
)


def _enrich_from_detail(session, listing: Listing) -> None:
    """Fetch the detail page and pull seller city + state into location.
    Also upgrades the transmission + mileage fields where possible."""
    resp = polite_get(session, listing.url, sleep=0.9, timeout=25)
    if resp is None:
        return
    body = resp.text
    m = LOCATION_RE.search(body)
    if m:
        loc = re.sub(r"\s+", " ", m.group(1)).strip()
        # BaT strings look like "Lancaster, Pennsylvania 17603" — strip trailing ZIP
        loc = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", loc)
        if loc:
            listing.location = loc

    # Pull transmission + mileage out of the essentials bullet list
    soup = BeautifulSoup(body, "lxml")
    ess = soup.select_one(".essentials, .listing-essentials, .item-specs")
    if ess:
        text = ess.get_text(" | ", strip=True)
        lower = text.lower()
        if "manual transmission" in lower or "-speed manual" in lower:
            listing.transmission = "manual"
        elif "automatic transmission" in lower:
            listing.transmission = "automatic"

        mi = parse_mileage(text)
        if mi and not listing.mileage:
            listing.mileage = mi

        if not listing.description:
            listing.description = text[:800]


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://bringatrailer.com/"})
    max_price = criteria.get("max_price", 23000)
    manual_only = criteria.get("transmission") == "manual"

    url = _build_url()
    log.info("bat: one fetch + local filter + detail enrich")
    resp = polite_get(session, url, sleep=1.5, timeout=25)
    if resp is None:
        return []

    raw_items = _parse_bootstrap(resp.text)
    log.info("bat raw items: %d", len(raw_items))

    # Filter: car + target model + price headroom
    matches: list[Listing] = []
    seen: set[str] = set()
    for item in raw_items:
        if not _is_car(item):
            continue
        title = item.get("title", "")
        matched = next((m for m in target_models if title_matches_model(title, m)), None)
        if not matched:
            continue
        listing = _item_to_listing(item)
        if listing is None or listing.url in seen:
            continue
        if listing.price and listing.price > max_price * 1.2:
            continue
        seen.add(listing.url)
        listing.model = matched
        matches.append(listing)

    log.info("bat matched cars: %d — fetching details for location/trans", len(matches))

    # Enrich each match with detail-page data (location, transmission). Cap
    # to keep runs bounded. BaT tolerates ~1 req/sec comfortably.
    for i, listing in enumerate(matches[:DETAIL_FETCH_CAP]):
        _enrich_from_detail(session, listing)

    # After enrichment, optionally drop anything clearly not manual
    if manual_only:
        matches = [l for l in matches if l.transmission != "automatic"]
        # Leave unknowns in — some listings don't surface trans in essentials

    log.info("bring-a-trailer total listings: %d", len(matches))
    return matches
