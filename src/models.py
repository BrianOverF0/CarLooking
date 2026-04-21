from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Listing:
    source: str
    url: str
    title: str
    price: Optional[int] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    mileage: Optional[int] = None
    transmission: Optional[str] = None   # "manual" | "automatic" | None
    location: Optional[str] = None
    distance_miles: Optional[float] = None
    description: str = ""
    images: list[str] = field(default_factory=list)
    posted_at: Optional[str] = None
    raw_id: Optional[str] = None

    # Populated by analyzer
    score: Optional[float] = None
    verdict: Optional[str] = None          # e.g. "steal", "fair", "overpriced"
    concerns: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    ac_estimate_usd: Optional[int] = None
    all_in_price: Optional[int] = None     # price + ac estimate

    def to_dict(self) -> dict:
        return asdict(self)
