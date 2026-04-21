from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

# Prefer curl_cffi (does real Chrome TLS/JA3 fingerprinting — gets through most
# Cloudflare-lite setups). Fall back to plain requests if not installed.
try:
    from curl_cffi import requests as curl_requests  # type: ignore
    _USE_CURL_CFFI = True
except ImportError:
    import requests as _requests
    curl_requests = None
    _USE_CURL_CFFI = False

# Import requests for type checking / fallback regardless
import requests as requests_lib


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


class HttpClient:
    """Thin wrapper so the rest of the code doesn't care which backend we use."""

    def __init__(self, impersonate: str = "chrome124"):
        self.impersonate = impersonate
        if _USE_CURL_CFFI:
            self._session = curl_requests.Session(impersonate=impersonate)
            self._session.headers.update(DEFAULT_HEADERS)
        else:
            self._session = requests_lib.Session()
            self._session.headers.update(DEFAULT_HEADERS)

    @property
    def headers(self) -> dict:
        return self._session.headers

    def get(self, url: str, *, timeout: int = 25, **kw):
        if _USE_CURL_CFFI:
            # curl_cffi's Session.get accepts impersonate per-call too
            return self._session.get(url, timeout=timeout, **kw)
        return self._session.get(url, timeout=timeout, **kw)


def make_session() -> HttpClient:
    return HttpClient()


def polite_get(session: HttpClient, url: str, *, timeout: int = 25,
               sleep: float = 1.2, **kw) -> Optional[Any]:
    """GET with a small delay + error swallowing so one failed site doesn't kill the run."""
    try:
        time.sleep(sleep)
        r = session.get(url, timeout=timeout, **kw)
        if r.status_code >= 400:
            log.warning("GET %s -> %s", url, r.status_code)
            return None
        return r
    except Exception as e:
        log.warning("GET %s failed: %s", url, e)
        return None


PRICE_RE = re.compile(r"\$?\s*([\d]{1,3}(?:[,\.]\d{3})+|\d{4,6})")
YEAR_RE = re.compile(r"\b(19[6-9]\d|20[0-2]\d)\b")
MILEAGE_RE = re.compile(r"([\d]{1,3}(?:,\d{3})+|\d{4,6})\s*(?:mi|miles|k\s*mi|k\s*miles)\b", re.I)


def parse_price(text: str) -> Optional[int]:
    if not text:
        return None
    m = PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(",", "").replace(".", "")
    try:
        val = int(raw)
    except ValueError:
        return None
    # Sanity filter — reject nonsense prices
    if val < 500 or val > 500_000:
        return None
    return val


def parse_year(text: str) -> Optional[int]:
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def parse_mileage(text: str) -> Optional[int]:
    if not text:
        return None
    m = MILEAGE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return int(raw)
    except ValueError:
        return None


def detect_transmission(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    manual_signals = [
        "manual transmission", "manual trans", " manual", "6-speed manual",
        "5-speed manual", "5 speed manual", "6 speed manual", "6mt", "5mt",
        "stick shift", "3-pedal", "three pedal",
    ]
    auto_signals = [
        "automatic transmission", "automatic trans", "tiptronic", "pdk",
        "dct", "dual-clutch", "dual clutch", "steptronic", " automatic",
        "cvt",
    ]
    has_manual = any(s in t for s in manual_signals)
    has_auto = any(s in t for s in auto_signals)
    if has_manual and not has_auto:
        return "manual"
    if has_auto and not has_manual:
        return "automatic"
    if has_manual and has_auto:
        return "manual" if t.count("manual") > t.count("automatic") else "automatic"
    return None


def build_keyword_query(models: list[str]) -> str:
    return " ".join(m.split()[-1] for m in models[:6])
