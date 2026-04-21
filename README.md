# CarLooking

Scrapes public used-car listings for **manual weekend cars** near Sachse, TX and ranks each one by a worth/risk heuristic. Built for a search under ~$23K with a bias toward aesthetically-interesting manuals (classic Zs, 911s, Miatas, Boxsters, S2000s, E30/E36/E46, RX-7/8, etc.).

Comes with a **local web UI** for filtering, sorting, and browsing the results — see [Web UI](#web-ui) below.

## Sources

Tested against live sites, DFW radius, manual filter:

| Source | Status | Notes |
|---|---|---|
| Craigslist (DFW + 200mi radius) | ✅ reliable | Parses JSON-LD on the search page (RSS feeds were deprecated by CL). Yields ~30–50 real listings per run. |
| eBay Motors | ✅ reliable | Public search HTML. Yields ~15–40 per run. Limits to manual + within radius server-side. |
| Bring a Trailer | ✅ reliable | Scrapes the bootstrap JSON on `/auctions/`. Nationwide, filtered client-side against your target model list. |
| AutoTrader | ⚠️ best-effort | Works on the first request; aggressive anti-bot then rate-limits us. For better yields use Playwright (not wired in by default). |
| ClassicCars.com | ⚠️ opt-in | Off by default. Their search/price filters don't actually filter — most listings are $40K+ muscle cars. Occasionally surfaces a sub-$23K Datsun/VW. Enable if you stretch budget. |
| Hemmings | ⚠️ opt-in | Off by default. Cloudflare JS challenge blocks the search-page URL discovery; detail pages work but we can't reach them without JS. |
| Cars.com | ⚠️ best-effort | Page is client-side-rendered — the SSR response has the shell but not listings. Useful only with Playwright. |
| Cars & Bids | ⚠️ best-effort | Heavy SPA, anon response is minimal. Playwright required for real data. |
| Facebook Marketplace | ❌ off by default | Against Meta ToS; needs persistent logged-in Playwright session; UI changes break it often. |
| CarGurus | ❌ off by default | Cloudflare-gated. Needs Playwright. |

Each scraper is isolated — if one 403s or breaks, the rest still work. Enable only what you want via `sources:` in `config.yaml`.

Primary reliable yield comes from **Craigslist + eBay Motors + Bring a Trailer** — that combo already surfaces 50–100+ real manual-transmission listings per run across the DFW area and nationwide auctions.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# or: source .venv/bin/activate on macOS/Linux

pip install -r requirements.txt
```

### Optional: Facebook Marketplace via Playwright

FB blocks anon scraping hard. If you want to try anyway:

```bash
pip install playwright
playwright install chromium
# then set sources.facebook_marketplace: true in config.yaml
```

On first run, a browser window opens — log in manually. The session persists to `.playwright_fb_profile/` (gitignored) so subsequent runs are automated. Expect this to break periodically when Meta changes their UI.

### Optional: Claude-enriched analysis

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python main.py --llm
```

Uses Claude Haiku for a cheap extra pass over the top 20 listings, adding candid model-specific commentary (known problem areas, typical ownership costs, price sanity).

## Run

```bash
# Dry run — verify config, no network
python main.py --dry-run

# Full scrape with all enabled sources
python main.py -v

# Just Craigslist (fastest, most reliable)
python main.py --source craigslist -v

# Multiple specific sources
python main.py --source craigslist --source cars_com --source ebay_motors
```

Outputs:
- `output/report.html` — static scored HTML report (open directly in a browser)
- `output/listings.json` — raw structured data, consumed by the web UI

Both are gitignored — they contain seller location/contact info.

## Web UI

For browsing, filtering, and searching the results interactively:

```bash
python webapp.py
# opens at http://127.0.0.1:5173/
```

Features:
- **Grid view** of scored listings, sortable by best-match, all-in price, price, year, or mileage
- **Live search** across title, model, location, and description
- **Sidebar filters**: verdict, source, min score, price range, year range, max mileage
- Click any card → **details modal** with full concerns/benefits + direct link to the listing
- **Refresh data** button kicks off a fresh scrape in the background and auto-reloads the grid when done

All filtering happens in the browser — the page loads the JSON file once on startup and never hits the network except for refresh.

## How scoring works

Each listing starts at 50 and is adjusted:

| Factor | ± |
|---|---|
| Matches a target model | +15 |
| Price within budget | +10 |
| Well under budget (<60%) | +20 |
| Manual transmission confirmed | +5 |
| Close to Sachse | +5 |
| Green flags in description ("clean title", "records", "cold a/c") | +2 each, cap +10 |
| Red flags ("rebuilt", "needs engine", "salvage") | -8 each |
| No price | -10 |
| Transmission mismatch | -25 |
| Outside radius | -10 |
| Missing year/mileage | -3 each |
| A/C needs major work (Texas) | -8 |

Verdict buckets: `strong buy` / `worth a look` / `mixed` / `risky` / `skip`.

## A/C estimator

Since you're in Texas, the scorer estimates A/C retrofit cost based on the car's age and any description keywords:

- Pre-1975: ~$4,000 (Vintage Air aftermarket kit)
- 1975–1992 (R-12 era): ~$2,500 (retrofit to R-134a)
- 1993–2004: ~$1,400 (original components tired)
- 2005+: ~$800 (recharge / compressor)
- Listing says "cold a/c" / "ice cold": $0
- Listing says "no a/c" / "needs a/c": full baseline estimate

The "All-in price" column on the report adds this to the asking price.

## Tuning

Edit `config.yaml` to change:
- Budget (`max_price`, `min_price`)
- Location (`zip_code`, `radius_miles`)
- Target model list
- Red/green flag phrases
- Which sources are enabled
- How many listings show up in the report

## Legal / ToS notes

- Craigslist RSS is explicitly permitted. Everything else is personal-use-scale scraping of public pages. Don't redistribute or commercialize the scraped data.
- Facebook Marketplace scraping is against Meta's ToS. The FB scraper is off by default for a reason; enabling it is at your own risk.
- The output files contain seller locations and sometimes names/phone numbers. `.gitignore` blocks `output/` from being committed. Keep it that way — this is a public repo.
- Never commit `.env`, `cookies.json`, `fb_session.json`, or the `.playwright_fb_profile/` directory. They contain session tokens.

## Disclaimer

Worth/risk scores are heuristic and not a substitute for a pre-purchase inspection. A 95-score '95 Miata can still have bad rockers. Budget a PPI for anything you're serious about.

## License

MIT. Do what you want.
