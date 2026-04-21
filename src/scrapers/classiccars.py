"""
ClassicCars.com scraper.

Results page: https://classiccars.com/listings/find
Query params we use:
  searchText=<query>
  Transmission=Manual
  MaxPrice=<usd>
  ZipCode=<zip>
  SearchDistance=<mi>
  PageSize=48

Each page embeds an ItemList JSON-LD with per-car entries (`@type: "Car"`).
The HTML also has `div.search-result-item` cards that we use for URLs +
fallback text. The JSON-LD doesn't include listing URLs, so we pair by
sequence order.
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

BASE = "https://classiccars.com/listings/find"


def _build_url(criteria: dict, query: str, page: int = 1) -> str:
    params = [
        ("searchText", query),
        ("Transmission", "Manual"),
        ("MaxPrice", criteria.get("max_price", 23000)),
        ("MinPrice", criteria.get("min_price", 2000)),
        ("ZipCode", criteria.get("zip_code", "75048")),
        ("SearchDistance", criteria.get("radius_miles", 200)),
        ("PageSize", 48),
        ("PageNumber", page),
    ]
    return f"{BASE}?{urlencode(params)}"


def _parse_ld_cars(html: str) -> list[dict]:
    """Return list of Car-typed objects from any ld+json blob in the HTML."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        txt = tag.string or tag.get_text() or ""
        if not txt.strip():
            continue
        try:
            d = json.loads(txt)
        except json.JSONDecodeError:
            continue
        # Could be dict or list
        if isinstance(d, list):
            for item in d:
                if isinstance(item, dict) and str(item.get("@type", "")).lower() == "car":
                    out.append(item)
        elif isinstance(d, dict):
            if str(d.get("@type", "")).lower() == "car":
                out.append(d)
            elif d.get("@type") == "ItemList":
                for el in d.get("itemListElement", []):
                    item = el.get("item") if isinstance(el, dict) else None
                    if isinstance(item, dict) and str(item.get("@type", "")).lower() == "car":
                        out.append(item)
    return out


def _parse_html_cards(html: str) -> list[dict]:
    """Fallback HTML card parser — returns {url, title, price, location}."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for card in soup.select(".search-result-item"):
        a = card.find("a", href=re.compile(r"/listings/view/\d+/"))
        if not a:
            continue
        url = a.get("href") or ""
        if not url.startswith("http"):
            url = f"https://classiccars.com{url}"

        label = a.get("aria-label") or a.get_text(" ", strip=True)
        price_el = card.find(class_=re.compile(r"price", re.I))
        price_text = price_el.get_text(" ", strip=True) if price_el else ""

        loc_m = re.search(r"in ([A-Z][A-Za-z\. ]+,\s*[A-Za-z]+)\s*\d{0,5}", label)
        location = loc_m.group(1) if loc_m else None

        out.append({
            "url": url,
            "title": re.sub(r"\s+", " ", label)[:200],
            "price": parse_price(price_text),
            "location": location,
        })
    return out


def _pair_and_normalize(cards: list[dict], ld_cars: list[dict]) -> list[Listing]:
    """Pair cards (have URLs) with ld_cars (have rich data) by index order."""
    out: list[Listing] = []
    for i, card in enumerate(cards):
        ld = ld_cars[i] if i < len(ld_cars) else {}
        title = card.get("title") or ld.get("name") or ""
        year = None
        model_date = ld.get("modelDate")
        if model_date:
            year = parse_year(str(model_date)) or int(model_date) if str(model_date).isdigit() else None
        year = year or parse_year(title)

        make = None
        mfg = ld.get("manufacturer") if isinstance(ld.get("manufacturer"), dict) else None
        if mfg:
            make = mfg.get("name")
        elif isinstance(ld.get("brand"), dict):
            make = ld["brand"].get("name")

        model_name = ld.get("model")
        if isinstance(model_name, dict):
            model_name = model_name.get("name")

        price = card.get("price")
        if not price:
            offer = ld.get("offers")
            if isinstance(offer, dict):
                price = parse_price(str(offer.get("price") or ""))

        out.append(Listing(
            source="classiccars",
            url=card["url"],
            title=title,
            price=price,
            year=year if isinstance(year, int) else None,
            make=make,
            model=str(model_name) if model_name else None,
            transmission="manual",
            location=card.get("location"),
        ))
    return out


def _title_matches_model(title: str, model: str) -> bool:
    t = title.lower()
    toks = model.lower().split()
    needle = toks[-1] if len(toks[-1]) > 2 else " ".join(toks[-2:])
    return needle in t


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://classiccars.com/"})

    max_price = criteria.get("max_price", 23000)
    out: list[Listing] = []
    seen_urls: set[str] = set()

    # The site's `searchText` / `Keyword` / price / radius params don't
    # actually filter the public results — pagination also loops. Fetch 1
    # page of "recent" listings and match titles client-side. Low yield for
    # sub-$25K budgets but the occasional Datsun/British/older Porsche pops.
    for page in range(1, 2):
        url = _build_url(criteria, query="", page=page)
        log.info("classiccars page=%d (client-side model filter)", page)
        resp = polite_get(session, url, sleep=1.3, timeout=25)
        if resp is None:
            break
        cards = _parse_html_cards(resp.text)
        ld_cars = _parse_ld_cars(resp.text)
        if not cards:
            break

        listings = _pair_and_normalize(cards, ld_cars)
        added = 0
        for l in listings:
            if l.url in seen_urls:
                continue
            # price filter
            if l.price and l.price > max_price * 1.1:
                continue
            # model filter
            matched = next((m for m in target_models if _title_matches_model(l.title, m)), None)
            if not matched:
                continue
            seen_urls.add(l.url)
            if not l.model:
                l.model = matched
            out.append(l)
            added += 1
        # If a page yielded no new matches and no new cards either, stop early
        if added == 0 and page >= 2:
            break

    log.info("classiccars total matching listings: %d", len(out))
    return out
