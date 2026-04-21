"""
Hemmings.com scraper — best effort.

Hemmings wraps its results in a Vue/Inertia-style SPA behind a Cloudflare JS
challenge. Without a browser we can only see the static shell + whatever
listing URLs are rendered into the initial HTML before JS hydration.

Strategy: hit the classifieds search page, extract any /classifieds/cars-for-sale/<make>/<model>/<id>.html
URLs we can find, then fetch each detail page to read its ld+json Vehicle block.

Yields are typically lower than Craigslist/eBay/BaT but Hemmings is *the*
place for older 911s, Datsun Zs, British roadsters, etc. — so even a handful
of hits is worth it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import make_session, polite_get, parse_price, parse_year

log = logging.getLogger(__name__)

BASE = "https://www.hemmings.com/classifieds/cars-for-sale"
LISTING_URL_RE = re.compile(r"/classifieds/cars-for-sale/[a-z0-9\-]+/[a-z0-9\-]+/(\d+)\.html", re.I)


def _build_search_url(query: str, max_price: int) -> str:
    params = {
        "Keyword": query,
        "PriceMax": max_price,
        "Transmission": "Manual",
    }
    return f"{BASE}?{urlencode(params)}"


def _collect_listing_urls(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for m in LISTING_URL_RE.finditer(html):
        path = m.group(0)
        if path in seen:
            continue
        seen.add(path)
        urls.append(f"https://www.hemmings.com{path}")
    return urls


def _parse_detail(html: str, url: str) -> Optional[Listing]:
    soup = BeautifulSoup(html, "lxml")
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    year = parse_year(title or "")
    price = None
    location = None
    make = None
    model = None
    mileage = None

    # Try ld+json Vehicle / Car blocks
    for tag in soup.find_all("script", type="application/ld+json"):
        txt = tag.string or tag.get_text() or ""
        if not txt.strip():
            continue
        try:
            d = json.loads(txt)
        except json.JSONDecodeError:
            continue
        # d can be list, dict, or have @graph
        candidates = []
        if isinstance(d, list):
            candidates = d
        elif isinstance(d, dict):
            if "@graph" in d:
                candidates = d["@graph"]
            else:
                candidates = [d]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            typ = str(c.get("@type", "")).lower()
            if typ not in {"car", "vehicle", "product"}:
                continue
            title = title or c.get("name")
            md = c.get("modelDate") or c.get("vehicleModelDate")
            if md:
                year = year or parse_year(str(md)) or (int(md) if str(md).isdigit() else None)
            mfg = c.get("manufacturer") or c.get("brand") or {}
            if isinstance(mfg, dict):
                make = make or mfg.get("name")
            mdl = c.get("model")
            if isinstance(mdl, dict):
                model = model or mdl.get("name")
            elif isinstance(mdl, str):
                model = model or mdl
            offers = c.get("offers") or {}
            if isinstance(offers, dict):
                price = price or parse_price(str(offers.get("price", "")))
                avail = offers.get("availableAtOrFrom") or {}
                if isinstance(avail, dict):
                    addr = avail.get("address") or {}
                    if isinstance(addr, dict):
                        city = addr.get("addressLocality")
                        state = addr.get("addressRegion")
                        if city or state:
                            location = ", ".join(x for x in [city, state] if x)
            mi = c.get("mileageFromOdometer")
            if isinstance(mi, dict):
                try:
                    mileage = int(float(mi.get("value", 0))) or None
                except (TypeError, ValueError):
                    mileage = None

    # Fallback: look for $<digits> in page body for price
    if not price:
        body_text = soup.get_text(" ", strip=True)
        m = re.search(r"\$\s?([\d,]{4,10})", body_text)
        if m:
            try:
                v = int(m.group(1).replace(",", ""))
                if 500 <= v <= 500_000:
                    price = v
            except ValueError:
                pass

    return Listing(
        source="hemmings",
        url=url,
        title=title or "(no title)",
        price=price,
        year=year if isinstance(year, int) else None,
        make=make,
        model=model,
        mileage=mileage,
        transmission="manual",
        location=location,
    )


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://www.hemmings.com/"})

    out: list[Listing] = []
    seen_urls: set[str] = set()
    max_price = criteria.get("max_price", 23000)

    # Per-model search pages — collect detail URLs
    detail_urls: list[str] = []
    for model in target_models:
        url = _build_search_url(model, max_price)
        log.info("hemmings search: %r", model)
        resp = polite_get(session, url, sleep=1.2, timeout=25)
        if resp is None:
            continue
        urls = _collect_listing_urls(resp.text)
        for u in urls:
            if u not in seen_urls:
                seen_urls.add(u)
                detail_urls.append(u)

    # Cap per-run fetches to keep requests bounded
    detail_urls = detail_urls[:40]

    # Fetch each detail page
    for u in detail_urls:
        resp = polite_get(session, u, sleep=1.0, timeout=25)
        if resp is None:
            continue
        listing = _parse_detail(resp.text, u)
        if not listing:
            continue
        out.append(listing)

    log.info("hemmings total listings: %d", len(out))
    return out
