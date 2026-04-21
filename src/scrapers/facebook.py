"""
Facebook Marketplace scraper — best effort.

Meta's TOS forbids automated scraping and aggressively blocks bots.
Any reliable approach requires a logged-in browser session; without one
you get rate-limited, bot-checked, and eventually session-banned.

This module attempts two strategies, both disabled by default (set
`sources.facebook_marketplace: true` in config.yaml):

  1. Unauthenticated GET of the public Marketplace search URL. This returns
     extremely sparse data — Meta strips most server-rendered content for
     anon visitors. Usually yields 0–5 listings.

  2. Playwright with a persisted user-data-dir. You log in ONCE manually, then
     the scraper reuses your session. This works but:
       - Meta may flag and temporarily lock your account
       - Breaks on any UI refresh
       - Requires `pip install playwright && playwright install chromium`

If Playwright isn't installed, strategy 2 is skipped silently.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Listing
from .base import make_session, polite_get, parse_price, parse_year, parse_mileage

log = logging.getLogger(__name__)

# Dallas is the closest big FB Marketplace hub for Sachse, TX
LOCATION_ID = "108005632547964"  # Dallas, TX — Meta's internal location PK

# Sachse, TX lat/lon (backup method that sometimes works in anon mode)
SACHSE_LAT = 32.9762
SACHSE_LON = -96.5952


def _build_anon_url(query: str, criteria: dict) -> str:
    params = [
        ("query", query),
        ("minPrice", criteria.get("min_price", 2000)),
        ("maxPrice", criteria.get("max_price", 23000)),
        ("radius", criteria.get("radius_miles", 200)),
        ("sortBy", "creation_time_descend"),
        ("transmissionType", "manual"),
        ("exact", "false"),
    ]
    return f"https://www.facebook.com/marketplace/{LOCATION_ID}/search?{urlencode(params)}"


def _parse_anon_html(html: str) -> list[Listing]:
    """Meta anon HTML has minimal listing data. Extract what we can from
    embedded <script> JSON blobs."""
    out: list[Listing] = []
    soup = BeautifulSoup(html, "lxml")

    # FB embeds preload data in various <script> blocks; look for marketplace_search
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "marketplace_search" not in txt and "listing_id" not in txt:
            continue
        # Grab any {"listing_id":"..."} objects we can find
        for m in re.finditer(
            r'"listing_id"\s*:\s*"(\d+)"[^{}]*?"marketplace_listing_title"\s*:\s*"([^"]+)"[^{}]*?"formatted_amount"\s*:\s*"([^"]+)"',
            txt,
        ):
            lid, title, price_str = m.groups()
            out.append(Listing(
                source="facebook_marketplace",
                url=f"https://www.facebook.com/marketplace/item/{lid}/",
                title=title,
                price=parse_price(price_str),
                year=parse_year(title),
                raw_id=lid,
            ))

    return out


def _scrape_anon(criteria: dict, target_models: list[str]) -> list[Listing]:
    session = make_session()
    # Minimal masquerade as a mobile browser; anon FB responds better sometimes
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
        ),
        "Referer": "https://www.facebook.com/marketplace/",
    })

    out: list[Listing] = []
    seen = set()
    for model in target_models[:10]:  # cap — anon is very noisy
        url = _build_anon_url(f"{model} manual", criteria)
        log.info("FB Marketplace (anon): %r", model)
        resp = polite_get(session, url, sleep=2.0, timeout=20)
        if resp is None:
            continue
        for l in _parse_anon_html(resp.text):
            if l.raw_id in seen:
                continue
            seen.add(l.raw_id)
            l.model = model
            out.append(l)
    return out


def _scrape_playwright(criteria: dict, target_models: list[str]) -> list[Listing]:
    """Playwright path with persisted session. Returns [] if playwright missing."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log.info("playwright not installed — skipping FB Marketplace authed scrape")
        return []

    user_dir = os.environ.get("FB_USER_DATA_DIR", ".playwright_fb_profile")
    out: list[Listing] = []

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=user_dir,
            headless=False,  # visible so user can log in first time
            viewport={"width": 1280, "height": 900},
        )
        page = browser.new_page()

        # First launch: pause so user can log in
        if not os.path.exists(os.path.join(user_dir, "Default")):
            page.goto("https://www.facebook.com/login")
            log.warning("Log in to Facebook in the open window. After you see News Feed, "
                        "re-run this script. Session will be saved to %s", user_dir)
            page.wait_for_timeout(45_000)
            browser.close()
            return []

        for model in target_models[:15]:
            url = _build_anon_url(f"{model} manual", criteria)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                page.wait_for_timeout(2500)  # let results render
                # Scroll to load more
                for _ in range(3):
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(1200)

                html = page.content()
                parsed = _parse_anon_html(html)
                # Playwright also exposes DOM — grab marketplace item links
                anchors = page.locator("a[href*='/marketplace/item/']").all()
                seen_ids = {l.raw_id for l in parsed}
                for a in anchors[:40]:
                    href = a.get_attribute("href") or ""
                    m = re.search(r"/marketplace/item/(\d+)", href)
                    if not m:
                        continue
                    lid = m.group(1)
                    if lid in seen_ids:
                        continue
                    seen_ids.add(lid)
                    txt = a.inner_text()[:200]
                    parsed.append(Listing(
                        source="facebook_marketplace",
                        url=f"https://www.facebook.com/marketplace/item/{lid}/",
                        title=txt.splitlines()[0] if txt else "",
                        price=parse_price(txt),
                        year=parse_year(txt),
                        raw_id=lid,
                    ))
                for l in parsed:
                    l.model = model
                    out.append(l)
            except Exception as e:
                log.warning("FB playwright error on %r: %s", model, e)

        browser.close()

    return out


def scrape(criteria: dict, target_models: list[str]) -> list[Listing]:
    log.warning(
        "Facebook Marketplace scraping is fragile and against Meta TOS. "
        "Use for personal browsing only; do not commit PII from results."
    )
    anon = _scrape_anon(criteria, target_models)
    authed = _scrape_playwright(criteria, target_models)

    # Dedupe
    by_id: dict[str, Listing] = {}
    for l in anon + authed:
        key = l.raw_id or l.url
        if key not in by_id:
            by_id[key] = l
    out = list(by_id.values())
    log.info("FB marketplace total: %d (anon=%d, authed=%d)", len(out), len(anon), len(authed))
    return out
