"""
eBay Motors scraper.

eBay's public search has stable, ToS-less URL params and renders
server-side HTML with data we can parse. Pure requests+bs4, no API key.

Search URL (category 6001 = Cars & Trucks):
  https://www.ebay.com/sch/6001/i.html
    ?_from=R40
    &_nkw=<keyword>
    &_sop=12                 (sort: best match)
    &LH_ItemCondition=3000|4000|5000   (used / excellent / very-good)
    &LH_PrefLoc=1            (domestic)
    &_udhi=<max-price>
    &Transmission=Manual
    &_stpos=<zip>
    &_sadis=<radius>
    &_fcid=1
    &LH_ItemCondition=3000
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

BASE = "https://www.ebay.com/sch/6001/i.html"


def _build_url(criteria: dict, query: str, page: int = 1) -> str:
    params = [
        ("_from", "R40"),
        ("_nkw", query),
        ("_sop", "12"),
        ("_udhi", criteria.get("max_price", 23000)),
        ("_udlo", criteria.get("min_price", 2000)),
        ("Transmission", "Manual"),
        ("_stpos", criteria.get("zip_code", "75048")),
        ("_sadis", criteria.get("radius_miles", 200)),
        ("LH_PrefLoc", "1"),
        ("_pgn", page),
        ("_ipg", "60"),
    ]
    return f"{BASE}?{urlencode(params)}"


def _parse_listings(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Listing] = []

    # eBay rotates between several card classes: s-item (legacy),
    # s-card (newer), and su-card (experimental).
    selectors = ["li.s-item", "li.s-card", ".s-card", "li[data-view]", "div.s-item"]
    cards = []
    for sel in selectors:
        cards = soup.select(sel)
        if cards:
            break

    for item in cards:
        # Title
        title_el = (
            item.select_one(".s-item__title") or
            item.select_one(".s-card__title") or
            item.select_one(".su-styled-text.primary") or
            item.select_one("span[role='heading']") or
            item.select_one("h3")
        )
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title or title.lower().startswith("shop on ebay"):
            continue

        # URL — any link pointing to /itm/<digits>
        link_el = item.find("a", href=re.compile(r"/itm/\d+"))
        url = link_el.get("href") if link_el else None
        if not url:
            continue
        url = url.split("?")[0]

        # Price
        price_el = (
            item.select_one(".s-item__price") or
            item.select_one(".s-card__price") or
            item.select_one(".su-styled-text.positive")
        )
        price = parse_price(price_el.get_text(" ", strip=True)) if price_el else None

        # Subtitle / mileage (eBay puts mileage in subtitle or attribute row)
        sub_text = ""
        for sel in [".s-item__subtitle", ".s-card__subtitle", ".s-card__attribute-row"]:
            el = item.select_one(sel)
            if el:
                sub_text += " " + el.get_text(" ", strip=True)
        mileage = parse_mileage(f"{title} {sub_text}")

        # Location
        loc_el = (
            item.select_one(".s-item__location") or
            item.select_one(".s-item__itemLocation") or
            item.select_one(".s-card__location")
        )
        location = loc_el.get_text(strip=True) if loc_el else None
        if location:
            location = location.replace("from ", "").replace("From ", "").strip()

        m = re.search(r"/itm/(\d+)", url)
        raw_id = m.group(1) if m else None

        img_el = item.select_one("img[src]") or item.select_one("img[data-src]")
        img_src = ""
        if img_el:
            img_src = img_el.get("src") or img_el.get("data-src") or ""
            # Skip 1x1 tracking pixels and placeholder data URIs
            if img_src.startswith("data:") or "1x1" in img_src or img_src.endswith(".gif"):
                img_src = ""
        images = [img_src] if img_src else []

        out.append(Listing(
            source="ebay_motors",
            url=url,
            title=title,
            price=price,
            year=parse_year(title),
            mileage=mileage,
            transmission="manual",
            location=location,
            images=images,
            raw_id=raw_id,
        ))

    return out


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://www.ebay.com/b/Cars-Trucks/6001/bn_1865117"})

    out: list[Listing] = []
    seen_ids: set[str] = set()

    for model in target_models:
        for page in range(1, 3):
            url = _build_url(criteria, model, page=page)
            log.info("ebay motors: %r page=%d", model, page)
            resp = polite_get(session, url, sleep=1.3, timeout=20)
            if resp is None:
                break
            listings = _parse_listings(resp.text)
            if not listings:
                break
            added = 0
            for l in listings:
                key = l.raw_id or l.url
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                l.model = model
                out.append(l)
                added += 1
            if added == 0:
                break

    log.info("ebay motors total listings: %d", len(out))
    return out
