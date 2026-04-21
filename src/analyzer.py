"""
Worth / risk analyzer.

Heuristic-only scoring (no external API needed). If ANTHROPIC_API_KEY is
set AND the `anthropic` package is installed, we additionally run an LLM
pass to get richer concerns/benefits narrative.

Scoring (0–100):
  base                       = 50
  + matches target model     + 15
  + price within budget      + 10
  + price well under budget  + 10 more
  + manual confirmed         + 5
  + near Sachse              + 5
  + green flags              + 2 each (cap +10)
  - red flags                - 8 each
  - no price                 - 10
  - over budget after A/C    - 20
  - distance > radius        - 10
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from .ac_estimator import estimate_ac_cost
from .models import Listing

log = logging.getLogger(__name__)


def _contains_any(text: str, phrases: list[str]) -> list[str]:
    t = text.lower()
    return [p for p in phrases if p.lower() in t]


def _matches_target(listing: Listing, targets: list[str]) -> Optional[str]:
    hay = f"{listing.title} {listing.description}".lower()
    for model in targets:
        # match against the "distinctive" part (last 1-2 tokens)
        distinctive = model.lower()
        parts = distinctive.split()
        if len(parts) > 1:
            needle = " ".join(parts[-2:]) if len(parts[-1]) <= 3 else parts[-1]
        else:
            needle = parts[0]
        if needle in hay:
            return model
    return None


def _coerce_int(v) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def score_listing(
    listing: Listing,
    criteria: dict,
    target_models: list[str],
    red_flags: list[str],
    green_flags: list[str],
) -> None:
    """Mutates listing in-place with score, verdict, concerns, benefits, ac_estimate, all_in_price."""
    # Normalize types — scrapers may return strings from JSON blobs
    listing.year = _coerce_int(listing.year)
    listing.price = _coerce_int(listing.price)
    listing.mileage = _coerce_int(listing.mileage)

    concerns: list[str] = []
    benefits: list[str] = []
    score = 50.0

    # --- Target model match
    matched = _matches_target(listing, target_models)
    if matched:
        score += 15
        benefits.append(f"Matches target: {matched}")
        if not listing.model:
            listing.model = matched
    else:
        concerns.append("Doesn't clearly match any target model in config")
        score -= 5

    # --- Price vs budget
    max_price = criteria.get("max_price", 23000)
    min_price = criteria.get("min_price", 2000)
    price = listing.price

    if price is None:
        concerns.append("No price listed")
        score -= 10
    else:
        if price > max_price:
            score -= 10
            concerns.append(f"Over budget: ${price:,} > ${max_price:,}")
        elif price > max_price * 0.95:
            score += 4
            concerns.append(f"At top of budget: ${price:,}")
        elif price < max_price * 0.6:
            score += 20
            benefits.append(f"Well under budget at ${price:,}")
        else:
            score += 10
            benefits.append(f"Within budget: ${price:,}")

        if price < min_price:
            concerns.append(f"Suspiciously cheap (${price:,} < ${min_price:,}) — possible scam or major issue")
            score -= 15

    # --- Transmission
    if listing.transmission == "manual":
        score += 5
        benefits.append("Manual transmission confirmed")
    elif listing.transmission and listing.transmission != "manual":
        concerns.append(f"Transmission: {listing.transmission} (wanted manual)")
        score -= 25
    else:
        concerns.append("Transmission not confirmed — verify before buying")

    # --- Distance
    if listing.distance_miles is not None:
        radius = criteria.get("radius_miles", 200)
        if listing.distance_miles <= radius * 0.25:
            score += 5
            benefits.append(f"Close to Sachse: ~{int(listing.distance_miles)} mi")
        elif listing.distance_miles > radius:
            score -= 10
            concerns.append(f"Outside radius: ~{int(listing.distance_miles)} mi")

    # --- Red/green flags from description+title
    text = f"{listing.title} {listing.description}"
    red_hits = _contains_any(text, red_flags)
    green_hits = _contains_any(text, green_flags)

    if red_hits:
        score -= min(len(red_hits) * 8, 40)
        concerns.extend([f"Red flag: '{r}'" for r in red_hits])
    if green_hits:
        score += min(len(green_hits) * 2, 10)
        benefits.extend([f"Green flag: '{g}'" for g in green_hits])

    # --- Must-run hint
    if criteria.get("must_run"):
        must_run_red = ["not running", "doesn't run", "won't start",
                        "needs engine", "blown motor", "blown engine",
                        "needs transmission"]
        if any(r in text.lower() for r in must_run_red):
            score -= 20
            concerns.append("Listing suggests car doesn't run — excluded by must_run criterion")

    # --- Year sanity
    if listing.year:
        if listing.year < criteria.get("min_year", 1960):
            concerns.append(f"Older than min_year: {listing.year}")
            score -= 3
        if listing.year > criteria.get("max_year", 2025):
            concerns.append(f"Newer than max_year: {listing.year}")
            score -= 3

    # --- A/C estimate
    ac_cost = estimate_ac_cost(listing.year, listing.title, listing.description)
    listing.ac_estimate_usd = ac_cost
    if ac_cost:
        if ac_cost >= 2500:
            concerns.append(f"A/C likely needs ~${ac_cost:,} of work (Texas climate)")
            score -= 8
        elif ac_cost >= 1000:
            concerns.append(f"Budget ~${ac_cost:,} for A/C refresh")
            score -= 4
        else:
            concerns.append(f"Minor A/C service likely (~${ac_cost})")
    elif ac_cost == 0:
        benefits.append("A/C reported cold/working")

    # --- All-in price
    if price is not None:
        listing.all_in_price = price + (ac_cost or 0)
        if listing.all_in_price > max_price * 1.1:
            concerns.append(
                f"All-in (price + A/C) ${listing.all_in_price:,} exceeds budget headroom"
            )
            score -= 10

    # --- Missing data penalty
    missing = []
    if not listing.year:
        missing.append("year")
    if not listing.mileage and "cars_and_bids" not in listing.source and "bring_a_trailer" not in listing.source:
        missing.append("mileage")
    if missing:
        concerns.append(f"Listing missing: {', '.join(missing)}")
        score -= 3 * len(missing)

    # Final normalization
    score = max(0.0, min(100.0, score))
    listing.score = round(score, 1)
    listing.concerns = concerns
    listing.benefits = benefits

    # Verdict buckets
    if score >= 80 and price and price <= max_price:
        listing.verdict = "strong buy"
    elif score >= 65:
        listing.verdict = "worth a look"
    elif score >= 50:
        listing.verdict = "mixed"
    elif score >= 35:
        listing.verdict = "risky"
    else:
        listing.verdict = "skip"


def analyze(
    listings: list[Listing],
    criteria: dict,
    target_models: list[str],
    red_flags: list[str],
    green_flags: list[str],
    use_llm: bool = False,
) -> list[Listing]:
    for l in listings:
        score_listing(l, criteria, target_models, red_flags, green_flags)

    if use_llm:
        try:
            _enrich_with_llm(listings, criteria)
        except Exception as e:
            log.warning("LLM enrichment failed, falling back to heuristic only: %s", e)

    # Sort by score desc, breaking ties with price asc
    listings.sort(
        key=lambda l: (-(l.score or 0), l.all_in_price or l.price or 10**9)
    )
    return listings


def _enrich_with_llm(listings: list[Listing], criteria: dict) -> None:
    """Optional: use Claude to enrich concerns/benefits with narrative detail.
    Only runs on the top 20 by heuristic score to keep cost bounded."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        return

    client = Anthropic()
    top = listings[:20]
    if not top:
        return

    # Batch into one call to minimize cost
    items_desc = "\n\n---\n\n".join(
        f"[{i}] {l.source} | {l.title}\nPrice: {l.price} | Year: {l.year} | Miles: {l.mileage}\n"
        f"Desc: {l.description[:400]}\nCurrent concerns: {l.concerns}\nCurrent benefits: {l.benefits}"
        for i, l in enumerate(top)
    )

    prompt = (
        "You are helping evaluate used weekend sports/classic cars for a buyer in "
        f"Sachse, TX. Budget ${criteria.get('max_price', 23000):,}, manual only, "
        "must run well, minor repairs OK. For each listing below, produce ONE LINE "
        "of candid reality-check — known problem areas for that model/year, likely "
        "ownership costs, and whether the price seems reasonable vs typical market. "
        "Format: `[INDEX] <your one-liner>`. Be specific, direct, no hedging.\n\n"
        f"{items_desc}"
    )

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text if resp.content else ""

    for line in text.splitlines():
        m = re.match(r"\[(\d+)\]\s+(.*)", line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        if 0 <= idx < len(top):
            top[idx].concerns.append(f"AI notes: {m.group(2)}")
