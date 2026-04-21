"""
Cars & Bids scraper.

C&B is an auction site (Doug DeMuro's). Listings are:
  https://carsandbids.com/auctions/<slug>

They have a JSON endpoint their front-end uses for live auctions:
  https://carsandbids.com/_data/auctions?q=<keyword>&page=<n>

If that endpoint changes (it does, periodically), we fall back to parsing
the HTML auctions listing page.

Note: C&B is a nationwide auction — our radius filter doesn't really apply,
but we still include hits because you may want to tow/ship one.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import make_session, polite_get, parse_price, parse_year, parse_mileage

log = logging.getLogger(__name__)

BASE_HTML = "https://carsandbids.com/search"


def _build_search_url(query: str) -> str:
    # C&B's public search uses `?q=`
    return f"{BASE_HTML}?{urlencode({'q': query})}"


def _parse_search_page(html: str) -> list[Listing]:
    """Parse auction cards out of the C&B search results HTML."""
    soup = BeautifulSoup(html, "lxml")
    out: list[Listing] = []

    # Try embedded __NEXT_DATA__ first (C&B uses Nuxt/Next-ish frameworks)
    script = soup.find("script", id="__NEXT_DATA__") or soup.find(
        "script", id="__NUXT_DATA__")
    if script and script.string:
        try:
            data = json.loads(script.string)
            out.extend(_listings_from_next_data(data))
            if out:
                return out
        except json.JSONDecodeError:
            pass

    # HTML fallback
    cards = soup.select("a[href*='/auctions/'], article.auction-item, .auction-card")
    for card in cards:
        href = card.get("href") if card.name == "a" else None
        if not href:
            link = card.find("a", href=re.compile(r"/auctions/"))
            href = link.get("href") if link else None
        if not href:
            continue
        url = href if href.startswith("http") else f"https://carsandbids.com{href}"

        title_el = card.select_one(".auction-title, h3, .title")
        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:120]

        # Current bid
        bid_el = card.select_one(".current-bid, .bid-price, .price")
        price = parse_price(bid_el.get_text(strip=True)) if bid_el else None

        year = parse_year(title)

        out.append(Listing(
            source="cars_and_bids",
            url=url,
            title=title,
            price=price,
            year=year,
        ))

    return out


def _listings_from_next_data(data: dict) -> list[Listing]:
    """Walk Next.js payload looking for auction objects."""
    out: list[Listing] = []

    def walk(obj):
        if isinstance(obj, dict):
            # auction items typically have slug + title + currentBid
            if "slug" in obj and ("title" in obj or "vehicle" in obj):
                slug = obj.get("slug")
                if not slug:
                    return
                url = f"https://carsandbids.com/auctions/{slug}"
                title = obj.get("title") or obj.get("vehicle", {}).get("title", "")
                price = None
                bid = obj.get("currentBid") or obj.get("current_bid")
                if isinstance(bid, dict):
                    bid = bid.get("amount") or bid.get("value")
                if bid:
                    try:
                        price = int(str(bid).replace(",", "").replace("$", ""))
                    except ValueError:
                        pass
                out.append(Listing(
                    source="cars_and_bids",
                    url=url,
                    title=str(title),
                    price=price,
                    year=parse_year(str(title)),
                    raw_id=str(slug),
                ))
                return  # don't keep recursing into this one
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return out


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://carsandbids.com/"})
    max_price = criteria.get("max_price", 23000)
    manual_only = criteria.get("transmission") == "manual"

    out: list[Listing] = []
    seen_urls: set[str] = set()

    for model in target_models:
        # append "manual" to hint the auction search to prefer manuals
        query = f"{model} manual" if manual_only else model
        url = _build_search_url(query)
        log.info("cars&bids: %r", query)
        resp = polite_get(session, url, sleep=1.2, timeout=20)
        if resp is None:
            continue
        listings = _parse_search_page(resp.text)
        for l in listings:
            if l.url in seen_urls:
                continue
            # filter by budget (auction bids will climb, so compare against max)
            if l.price and l.price > max_price * 1.2:
                # 20% headroom — a $25K bid on a 23K budget is borderline, flag in analyzer
                continue
            seen_urls.add(l.url)
            l.model = model
            l.transmission = "manual" if manual_only else l.transmission
            out.append(l)

    log.info("cars&bids total listings: %d", len(out))
    return out
