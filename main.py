"""
CarLooking — CLI entry point.

    python main.py                       # run all enabled scrapers, write reports
    python main.py --source craigslist   # only one source
    python main.py --llm                 # enrich with Claude (needs ANTHROPIC_API_KEY)
    python main.py --dry-run             # parse config, don't hit the network
    python main.py --top 50              # show more in the report
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from src.analyzer import analyze
from src.models import Listing
from src.report import print_summary, write_html_report, write_json
from src.scrapers import REGISTRY


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    p = argparse.ArgumentParser(description="CarLooking — scrape + score manual weekend cars")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--source", action="append",
                   help="run only these source(s); repeatable")
    p.add_argument("--llm", action="store_true",
                   help="enable Claude enrichment for top 20 (needs ANTHROPIC_API_KEY)")
    p.add_argument("--dry-run", action="store_true",
                   help="load config and print target sources; no network calls")
    p.add_argument("--top", type=int, default=None,
                   help="override top_n in report")
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.WARNING - (10 * args.verbose),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        return 2
    config = load_config(str(cfg_path))

    criteria = config.get("criteria", {})
    target_models = config.get("target_models", [])
    red_flags = config.get("red_flags", [])
    green_flags = config.get("green_flags", [])
    out_cfg = config.get("output", {})
    top_n = args.top or out_cfg.get("top_n", 25)

    # Split models: regular at standard budget, extended (GTR etc.) at higher budget
    ext_cfg = config.get("extended_budget", {})
    ext_models: list[str] = ext_cfg.get("models", [])
    ext_max: int = ext_cfg.get("max_price", criteria.get("max_price", 23000))
    ext_criteria = {**criteria, "max_price": ext_max} if ext_models else {}
    regular_models = [m for m in target_models if m not in ext_models]

    # Decide which scrapers to run
    if args.source:
        source_names = args.source
    else:
        source_names = [name for name, enabled in config.get("sources", {}).items() if enabled]

    print(f"[CarLooking] Sources: {', '.join(source_names)}")
    print(f"[CarLooking] Budget: ${criteria.get('min_price',0):,}-${criteria.get('max_price',0):,} "
          f"| Manual | {criteria.get('radius_miles')}mi of {criteria.get('zip_code')}")
    if ext_models:
        print(f"[CarLooking] Extended budget (${ext_max:,}): {', '.join(ext_models)}")
    print(f"[CarLooking] Target models: {len(target_models)} ({len(regular_models)} regular + {len(ext_models)} extended)")

    if args.dry_run:
        print("[CarLooking] --dry-run, exiting before network calls")
        return 0

    all_listings: list[Listing] = []
    for name in source_names:
        fn = REGISTRY.get(name)
        if fn is None:
            print(f"  ! Unknown source: {name}")
            continue
        print(f"  > Scraping {name} ...")
        count = 0
        # Pass 1: regular models at standard budget
        if regular_models:
            try:
                listings = fn(criteria, regular_models)
                count += len(listings)
                all_listings.extend(listings)
            except Exception as e:
                logging.exception("Scraper %s (regular) crashed: %s", name, e)
        # Pass 2: extended budget models (GTR etc.) at higher price cap
        if ext_models:
            try:
                listings = fn(ext_criteria, ext_models)
                count += len(listings)
                all_listings.extend(listings)
            except Exception as e:
                logging.exception("Scraper %s (extended) crashed: %s", name, e)
        print(f"    {count} raw listings")

    print(f"[CarLooking] Total raw listings: {len(all_listings)}")

    # Analyze
    print("[CarLooking] Scoring listings ...")
    all_listings = analyze(
        all_listings,
        criteria=criteria,
        target_models=target_models,
        red_flags=red_flags,
        green_flags=green_flags,
        use_llm=args.llm,
    )

    # Write outputs
    json_path = out_cfg.get("json_path", "output/listings.json")
    html_path = out_cfg.get("html_report", "output/report.html")
    write_json(all_listings, json_path)
    write_html_report(all_listings, html_path, criteria, top_n=top_n)

    print(f"[CarLooking] Wrote {json_path} and {html_path}")
    print_summary(all_listings, top_n=min(top_n, 20))
    return 0


if __name__ == "__main__":
    sys.exit(main())
