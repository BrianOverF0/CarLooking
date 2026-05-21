# CarLooking

Scrapes public used-car listings for **manual weekend cars** near Sachse, TX and ranks each one by a worth/risk heuristic. Budget ~$23K standard ($40K for GTR/Skyline), manual only, 200mi of zip 75048.

Targets: classic Zs, 911s, Miatas, Boxsters, S2000s, E30/E36/E46, RX-7/8, Supra, MR2, GTR, Fiat Abarth, and more — full list in [config.yaml](config.yaml).

---

## Quick start (local)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
pip install -r requirements.txt

python main.py -v             # scrape + score (~3 min)
python webapp.py              # UI at http://127.0.0.1:5173/
```

Double-click `start.bat` (or `start_silent.vbs` for no console) to run the webapp without a terminal.

---

## Architecture

```
[PC — residential IP]                   [Azure App Service]
  main.py (scrape)                          webapp.py (Flask UI)
  scrape_and_upload.py ──POST /api/──►      SQLite /home/carlooking.db
  Windows Task Scheduler (7am / 7pm)        https://carlooking.azurewebsites.net
```

**Why PC scrapes, not Azure:** Craigslist and eBay permanently block Azure data-center IPs. Scraper must run from a residential IP. Results are pushed to Azure via an authenticated upload endpoint.

---

## Current deployment status

**Azure resources: DELETED** (as of 2026-05-21 to eliminate costs).

To redeploy, follow the [Cloud Redeploy](#cloud-redeploy) section below. Takes ~15 minutes.

---

## Cloud Redeploy

> Run all `az` commands in **PowerShell** (not Git Bash — Git Bash mangles `/home` paths to `C:/Program Files/Git/home`).

### 1. Create Azure resources

```powershell
az login

# Resource group (southcentralus keeps latency low from TX)
az group create --name carlooking-rg --location southcentralus

# App Service plan — start on F1 (free); scale to B1 only if needed
az appservice plan create --name carlooking-plan --resource-group carlooking-rg --sku F1 --is-linux

# Web app (Python 3.12)
az webapp create --name carlooking --resource-group carlooking-rg --plan carlooking-plan --runtime "PYTHON:3.12"

# Startup command
az webapp config set --name carlooking --resource-group carlooking-rg --startup-file "startup.sh"
```

### 2. Set secrets (PowerShell only — never paste into config files)

```powershell
# Generate a random upload token
$token = [System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$secretKey = [System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))

az webapp config appsettings set --name carlooking --resource-group carlooking-rg --settings `
  CARLOOKING_PASSWORD="<your-login-password>" `
  SECRET_KEY="$secretKey" `
  UPLOAD_TOKEN="$token" `
  DATA_DIR="/home"

# Print the token — you'll need it in .env on the PC
Write-Host "CARLOOKING_UPLOAD_TOKEN=$token"
```

### 3. Enable persistent storage (required for SQLite)

```powershell
az webapp config appsettings set --name carlooking --resource-group carlooking-rg --settings WEBSITES_ENABLE_APP_SERVICE_STORAGE=true
```

### 4. Set up GitHub Actions

1. Azure portal → App Service `carlooking` → **Deployment Center** → **Get publish profile** → download XML
2. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → New secret:
   - `AZURE_WEBAPP_NAME` = `carlooking`
   - `AZURE_WEBAPP_PUBLISH_PROFILE` = paste entire XML content
3. Push to `main` — GitHub Actions deploys automatically

### 5. Update PC upload config

Edit `.env` in the repo root (gitignored):
```
CARLOOKING_UPLOAD_URL=https://carlooking.azurewebsites.net
CARLOOKING_UPLOAD_TOKEN=<token from step 2>
```

Never commit `.env`. Secrets stay local.

### 6. Verify

```powershell
# Health check
Invoke-RestMethod https://carlooking.azurewebsites.net/api/debug

# Manual upload test
python scrape_and_upload.py --upload-only
```

### Scaling

```powershell
# Scale UP to B1 (~$13/month) if cold-starts are too slow
az appservice plan update --name carlooking-plan --resource-group carlooking-rg --sku B1

# Scale back to free
az appservice plan update --name carlooking-plan --resource-group carlooking-rg --sku F1

# Delete everything
az group delete --name carlooking-rg --yes --no-wait
```

---

## PC scrape schedule (Windows Task Scheduler)

`scrape_and_upload.py` runs main.py then POSTs `output/listings.json` to Azure.

To set up scheduled runs:

```powershell
# 7:00 AM daily
$action = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\Code\CarLooking\scrape_and_upload.py" -WorkingDirectory "C:\Code\CarLooking"
$trigger = New-ScheduledTaskTrigger -Daily -At "7:00AM"
Register-ScheduledTask -TaskName "CarLooking-AM" -Action $action -Trigger $trigger -RunLevel Highest

# 7:00 PM daily
$trigger2 = New-ScheduledTaskTrigger -Daily -At "7:00PM"
Register-ScheduledTask -TaskName "CarLooking-PM" -Action $action -Trigger $trigger2 -RunLevel Highest
```

Manual run anytime:
```powershell
cd C:\Code\CarLooking
python scrape_and_upload.py          # scrape + upload
python scrape_and_upload.py --upload-only   # upload last output without re-scraping
```

---

## Android PWA

Once deployed, install as a full-screen app on Android (no Play Store):

1. Open Chrome on Android → `https://carlooking.azurewebsites.net`
2. Log in with your password
3. Tap 3-dot menu → **Add to Home screen** → **Install**

Long-press the home screen icon → shortcut to **Refresh listings** triggers an immediate scrape upload.

---

## Sources

| Source | Status | Notes |
|---|---|---|
| Craigslist | ✅ reliable | JSON-LD parse. ~30–50 real listings/run. |
| eBay Motors | ✅ reliable | Nationwide with TX shipping estimate. ~15–40/run. |
| Bring a Trailer | ✅ reliable | Nationwide auction. Shows only auctions ending within 24h. |
| Cars & Bids | ✅ best-effort | Anon HTML + `__NEXT_DATA__`. Nationwide auction. |
| AutoTrader | ⚠️ best-effort | Anti-bot rate-limits after ~10 requests. |
| ClassicCars.com | ✅ enabled | Pagination limited; useful with $40K GTR budget. |
| Facebook Marketplace | ⚠️ low-yield | Anon mode only; against Meta ToS — personal use only. |
| Hemmings | ❌ off | Cloudflare JS challenge blocks search-page URL discovery. |
| Cars.com | ❌ off | SPA — needs Playwright. |
| CarGurus | ❌ off | Cloudflare-gated — needs Playwright. |

Toggle sources in `config.yaml` under `sources:`.

---

## Budget split

| Group | Budget | Models |
|---|---|---|
| Standard | $23K | Everything except GTR/Skyline |
| Extended | $40K | Nissan GT-R, Nissan Skyline, R32/R33/R34 GTR |

Each scraper runs twice per source (two-pass). Analyzer detects GTR keywords and scores against the correct cap automatically.

---

## Scoring

Each listing starts at 50:

| Factor | Δ |
|---|---|
| Matches target model | +15 |
| Well under budget (<60%) | +20 |
| Within budget | +10 |
| Manual transmission | +5 |
| Close to Sachse | +5 |
| Green flags ("clean title", "cold a/c", etc.) | +2 each, cap +10 |
| Red flags ("salvage", "needs engine", etc.) | -8 each |
| Transmission mismatch (auto confirmed) | -25 |
| Active auction >24h away | -15 |
| No price | -10 |
| Outside radius | -10 |
| Missing year/mileage | -3 each |
| A/C needs major work | -8 |

Verdicts: `strong buy` / `worth a look` / `mixed` — `risky` and `skip` are hidden from the UI.

---

## Project layout

| File | Purpose |
|---|---|
| [main.py](main.py) | CLI — scrape all enabled sources, score, write `output/` |
| [webapp.py](webapp.py) | Flask UI (HTML/CSS/JS inlined), SQLite storage, upload endpoint, SSE progress |
| [scrape_and_upload.py](scrape_and_upload.py) | PC-side: runs main.py then POSTs results to Azure |
| [config.yaml](config.yaml) | All tuning: budget, zip, radius, models, flags, source toggles |
| [startup.sh](startup.sh) | gunicorn launch for Azure |
| [src/models.py](src/models.py) | `Listing` dataclass |
| [src/analyzer.py](src/analyzer.py) | Heuristic scorer + optional Claude Haiku enrichment |
| [src/ac_estimator.py](src/ac_estimator.py) | Texas A/C retrofit cost heuristic |
| [src/scrapers/](src/scrapers/) | One file per site; each exports `scrape(criteria, models) -> list[Listing]` |

**Key design decisions:**
- `curl_cffi` with `impersonate="chrome124"` — real Chrome TLS fingerprint, bypasses Cloudflare on most sites
- Scrapers are independent — one crash doesn't kill the run
- Client-side model filtering (`title_matches_model()`) — sites that ignore their own search filters
- SQLite at `/home/carlooking.db` with `journal_mode=DELETE` (WAL breaks on Azure Files SMB)
- Service worker cache: bump `CACHE = 'carlooking-vN'` in webapp.py whenever UI HTML changes — forces phone to drop cached version

---

## Adding a scraper

1. Create `src/scrapers/<name>.py` with `scrape(criteria: dict, target_models: list[str]) -> list[Listing]`
2. Register in `src/scrapers/__init__.py` (`REGISTRY`)
3. Add to `config.yaml` under `sources:`
4. Use `make_session()` + `polite_get()` from `base.py`
5. Set `price_type="bid"/"asking"/"sold"` on each listing

---

## Optional: Claude-enriched analysis

```bash
# Set ANTHROPIC_API_KEY in your environment, then:
python main.py --llm
```

Runs Claude Haiku over the top 20 listings — adds one-liner commentary on known problem areas, typical costs, and price sanity. Cost: ~$0.01/run.

---

## Legal

- All scraping is personal-use-scale on public pages. Do not redistribute scraped data.
- Facebook Marketplace scraping is against Meta ToS. Off by default.
- `output/` is gitignored — it contains seller PII (names, locations, sometimes phone numbers). Keep it that way.
- Never commit `.env`, `cookies.json`, or `.playwright_fb_profile/`.

## Disclaimer

Scores are heuristic. A 95/100 listing can still have rusty rockers. Budget a PPI for anything you're serious about.
