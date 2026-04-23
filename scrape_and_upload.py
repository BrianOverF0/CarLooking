"""
Run on your PC (residential IP) — scrapes all sources, then uploads to Azure.

Usage:
    python scrape_and_upload.py

Reads CARLOOKING_UPLOAD_URL and CARLOOKING_UPLOAD_TOKEN from environment
or from a local .env file (never committed).

Set these once:
    Windows:  setx CARLOOKING_UPLOAD_URL "https://carlooking.azurewebsites.net"
              setx CARLOOKING_UPLOAD_TOKEN "your-token-here"
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
LISTINGS_FILE = ROOT / "output" / "listings.json"

# Load .env if present (never committed)
_env_file = ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

UPLOAD_URL = os.environ.get("CARLOOKING_UPLOAD_URL", "").rstrip("/")
UPLOAD_TOKEN = os.environ.get("CARLOOKING_UPLOAD_TOKEN", "")


def scrape() -> bool:
    log.info("Running scraper (residential IP)...")
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode != 0:
        log.error("Scraper exited with code %d", result.returncode)
        return False
    log.info("Scrape complete — %s", LISTINGS_FILE)
    return True


def upload() -> bool:
    if not UPLOAD_URL or not UPLOAD_TOKEN:
        log.error(
            "Set CARLOOKING_UPLOAD_URL and CARLOOKING_UPLOAD_TOKEN "
            "(env vars or .env file)"
        )
        return False
    if not LISTINGS_FILE.exists():
        log.error("No listings file found: %s", LISTINGS_FILE)
        return False

    with open(LISTINGS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    log.info("Uploading %d listings to %s ...", len(data), UPLOAD_URL)
    body = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{UPLOAD_URL}/api/upload-listings",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Upload-Token": UPLOAD_TOKEN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            log.info("Upload OK — Azure now has %d listings", result.get("count", "?"))
            return True
    except urllib.error.HTTPError as e:
        log.error("Upload failed: HTTP %d — %s", e.code, e.read().decode()[:200])
        return False
    except Exception as e:
        log.error("Upload failed: %s", e)
        return False


if __name__ == "__main__":
    ok = scrape()
    if ok:
        upload()
    sys.exit(0 if ok else 1)
