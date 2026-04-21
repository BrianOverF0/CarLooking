"""
Rough A/C retrofit / repair cost estimator for Texas-driven cars.

Ranges are deliberately conservative midpoints based on common shop quotes
and Vintage Air / Classic Auto Air retrofit kits (2024–2025). These are
estimates for budgeting, not quotes.
"""
from __future__ import annotations

import re
from typing import Optional


def estimate_ac_cost(year: Optional[int], title: str, description: str) -> Optional[int]:
    """Return a USD estimate to get A/C blowing cold, or None if likely already working."""
    # Some scrapers return year as a string (e.g. BaT's "1980") — coerce.
    if isinstance(year, str):
        try:
            year = int(year)
        except ValueError:
            year = None
    elif year is not None and not isinstance(year, int):
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

    text = f"{title} {description}".lower()

    # Explicit "cold a/c" / "ac blows cold" → no cost
    cold_signals = ["ice cold a/c", "ice cold ac", "cold a/c", "cold ac",
                    "a/c blows cold", "ac blows cold", "a/c works", "ac works",
                    "a/c ice cold", "ac ice cold"]
    if any(s in text for s in cold_signals):
        return 0

    # Explicit no A/C or broken
    no_ac = ["no a/c", "no ac", "a/c does not work", "ac does not work",
             "a/c not working", "ac not working", "a/c needs", "ac needs",
             "needs a/c", "needs ac", "a/c needs charge", "ac broken",
             "a/c broken", "a/c blows warm", "ac blows warm", "a/c delete",
             "ac delete"]
    has_no_ac = any(s in text for s in no_ac)

    # Age-based baseline. Pre-'93 used R-12 (banned), retrofit required.
    base_cost: Optional[int] = None
    if year is None:
        # Unknown year — only flag if explicitly no A/C
        if has_no_ac:
            return 2500  # generic "assume you'll spend this" number
        return None

    if year < 1975:
        # Likely never had factory A/C or long-since defunct. Vintage Air kit.
        base_cost = 4000
    elif year < 1993:
        # R-12 era → retrofit to R-134a
        base_cost = 2500
    elif year < 2005:
        # R-134a, original components likely tired
        base_cost = 1400
    else:
        # Newer — usually just a recharge or compressor
        base_cost = 800

    if has_no_ac:
        return base_cost

    # Weak A/C hints
    weak = ["a/c weak", "ac weak", "a/c blows", "ac blows", "a/c recharge",
            "ac recharge", "needs charge", "a/c could use", "ac could use"]
    if any(s in text for s in weak):
        return int(base_cost * 0.5)

    # Not mentioned at all → for older cars in Texas, budget defensively
    if year < 1993:
        return int(base_cost * 0.6)   # likely needs attention
    return None   # assume functional on newer cars
