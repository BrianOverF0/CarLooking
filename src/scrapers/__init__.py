from __future__ import annotations

from typing import Callable

from ..models import Listing
from . import (
    craigslist,
    cars_com,
    autotrader,
    cars_and_bids,
    bring_a_trailer,
    ebay_motors,
    facebook,
)

# name -> callable that accepts (criteria: dict, target_models: list[str]) -> list[Listing]
REGISTRY: dict[str, Callable[[dict, list[str]], list[Listing]]] = {
    "craigslist": craigslist.scrape,
    "cars_com": cars_com.scrape,
    "autotrader": autotrader.scrape,
    "cars_and_bids": cars_and_bids.scrape,
    "bring_a_trailer": bring_a_trailer.scrape,
    "ebay_motors": ebay_motors.scrape,
    "facebook_marketplace": facebook.scrape,
}
