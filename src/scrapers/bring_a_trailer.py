"""
Bring a Trailer scraper.

BaT's search is reachable at:
  https://bringatrailer.com/auctions/?search=<keyword>

Listings live at /listing/<slug>/. BaT is nationwide and auction-based.
Bids on desirable weekend cars often exceed our budget, but there are
no-reserve ones and less-hyped models that land under $23K.
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

BASE = "https://bringatrailer.com/auctions/"


def _build_url(query: str) -> str:
    return f"{BASE}?{urlencode({'search': query})}"


def _extract_balanced_json(text: str, start_idx: int) -> Optional[str]:
    """Starting from an opening '{' at start_idx, return the matching {...}
    substring. Crude but good enough for these bootstrap blobs (no embedded
    raw '{' inside strings that aren't properly escaped)."""
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


def _parse_cards(html: str) -> list[Listing]:
    out: list[Listing] = []

    # BaT bootstraps data as `var auctionsCurrentInitialData = {...};` — items are
    # inside. Match the starting point and balance braces.
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
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        out.extend(_from_bootstrap(items))

    if out:
        return out

    soup = BeautifulSoup(html, "lxml")

    # HTML fallback
    for card in soup.select("div.auctions-item, .listing-card, .block-auction"):
        link = card.find("a", href=re.compile(r"/listing/"))
        if not link:
            continue
        url = link.get("href")
        if not url:
            continue
        if not url.startswith("http"):
            url = f"https://bringatrailer.com{url}"

        title_el = card.select_one(".auctions-item-title, h3, .title")
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        price_el = card.select_one(".bid-value, .current-bid, .price")
        price = parse_price(price_el.get_text(strip=True)) if price_el else None

        out.append(Listing(
            source="bring_a_trailer",
            url=url,
            title=title,
            price=price,
            year=parse_year(title),
        ))
    return out


def _from_bootstrap(items: list[dict]) -> list[Listing]:
    out = []
    for item in items:
        url = item.get("url") or item.get("permalink")
        if not url:
            continue
        title = item.get("title") or ""
        bid = item.get("current_bid") or item.get("current_bid_formatted") or item.get("price")
        if isinstance(bid, dict):
            bid = bid.get("amount") or bid.get("value")
        price = None
        if bid:
            try:
                price = int(str(bid).replace(",", "").replace("$", ""))
            except ValueError:
                pass
        out.append(Listing(
            source="bring_a_trailer",
            url=url,
            title=title,
            price=price,
            year=parse_year(title) or item.get("year"),
            raw_id=str(item.get("id", "")) or None,
        ))
    return out


def _title_matches_model(title: str, model: str) -> bool:
    """BaT's `?search=` param doesn't actually filter — the page returns all
    active auctions with JS-side filtering. We filter server-returned items
    against the model name ourselves."""
    t = title.lower()
    m = model.lower()
    # Require last token of model (e.g. "miata") to appear
    toks = m.split()
    needle = toks[-1] if len(toks[-1]) > 2 else " ".join(toks[-2:])
    return needle in t


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    session.headers.update({"Referer": "https://bringatrailer.com/"})
    max_price = criteria.get("max_price", 23000)
    manual_only = criteria.get("transmission") == "manual"

    out: list[Listing] = []
    seen_urls: set[str] = set()

    # BaT ignores the search param for anon GET — one hit returns all active
    # auctions. We fetch once, then filter client-side against target_models.
    url = _build_url("weekend")  # query value doesn't matter
    log.info("bat: one fetch + local filter")
    resp = polite_get(session, url, sleep=1.5, timeout=25)
    if resp is None:
        return out

    all_items = _parse_cards(resp.text)
    for l in all_items:
        if l.url in seen_urls:
            continue
        if l.price and l.price > max_price * 1.2:
            continue
        # Keep only if it matches one of our target models
        matched = next((m for m in target_models if _title_matches_model(l.title, m)), None)
        if not matched:
            continue
        seen_urls.add(l.url)
        l.model = matched
        l.transmission = "manual" if manual_only else l.transmission
        out.append(l)

    log.info("bring-a-trailer total listings: %d", len(out))
    return out
