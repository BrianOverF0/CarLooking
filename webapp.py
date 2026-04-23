"""
Local Flask app for browsing the scraped listings.

Run:
    python webapp.py
    # then open http://127.0.0.1:5173/

Features:
  - Grid of cards, sorted by score
  - Live filter/search: text, source, verdict, price range, year range, min score
  - Sort dropdown: score, all-in price, price, year, mileage
  - Click a card for full details modal with direct link to listing
  - "Refresh data" button kicks off a new scrape (runs main.py in a subprocess)
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

ROOT = Path(__file__).parent
LISTINGS_FILE = ROOT / "output" / "listings.json"
MAIN_SCRIPT = ROOT / "main.py"

app = Flask(__name__)

_scrape_lock = threading.Lock()
_scrape_state: dict[str, Any] = {
    "running": False, "started_at": None, "last_finished": None, "last_error": None,
}
# Rolling log of the last 200 lines from the most recent scrape run
_scrape_log: collections.deque[str] = collections.deque(maxlen=200)
_scrape_log_lock = threading.Lock()


def _load_listings() -> list[dict]:
    if not LISTINGS_FILE.exists():
        return []
    try:
        with open(LISTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _run_scrape():
    _scrape_state["running"] = True
    _scrape_state["started_at"] = time.time()
    _scrape_state["last_error"] = None
    with _scrape_log_lock:
        _scrape_log.clear()
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, "-u", str(MAIN_SCRIPT)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in proc.stdout:
            line = line.rstrip()
            with _scrape_log_lock:
                _scrape_log.append(line)
        proc.wait(timeout=1800)
        if proc.returncode != 0:
            with _scrape_log_lock:
                last = list(_scrape_log)[-5:]
            _scrape_state["last_error"] = "\n".join(last)
    except Exception as e:
        _scrape_state["last_error"] = str(e)[:400]
        with _scrape_log_lock:
            _scrape_log.append(f"ERROR: {e}")
    finally:
        _scrape_state["running"] = False
        _scrape_state["last_finished"] = time.time()


@app.get("/")
def index():
    return render_template_string(TEMPLATE)


@app.get("/api/listings")
def api_listings():
    return jsonify(_load_listings())


@app.get("/api/status")
def api_status():
    listings = _load_listings()
    mtime = LISTINGS_FILE.stat().st_mtime if LISTINGS_FILE.exists() else None
    return jsonify({
        "count": len(listings),
        "data_mtime": mtime,
        "scrape": _scrape_state,
    })


@app.get("/api/refresh/log")
def api_refresh_log():
    """Returns the current scrape log as SSE stream, then closes."""
    def generate():
        sent = 0
        while True:
            with _scrape_log_lock:
                lines = list(_scrape_log)
            for line in lines[sent:]:
                yield f"data: {json.dumps(line)}\n\n"
            sent = len(lines)
            if not _scrape_state["running"]:
                yield "data: {\"__done__\": true}\n\n"
                break
            time.sleep(0.4)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/refresh")
def api_refresh():
    with _scrape_lock:
        if _scrape_state["running"]:
            return jsonify({"ok": False, "error": "scrape already running"}), 409
        t = threading.Thread(target=_run_scrape, daemon=True)
        t.start()
    return jsonify({"ok": True})


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CarLooking</title>
<style>
  :root {
    --bg: #0f172a; --panel: #1e293b; --panel-2: #111827; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #60a5fa;
    --green: #22c55e; --blue: #3b82f6; --amber: #f59e0b; --orange: #f97316; --red: #ef4444;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
               font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  header {
    position: sticky; top: 0; z-index: 10; background: var(--panel-2);
    border-bottom: 1px solid var(--border); padding: 12px 20px;
    display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  }
  header h1 { margin: 0; font-size: 18px; letter-spacing: 0.02em; }
  header .stats { color: var(--muted); font-size: 13px; margin-right: auto; }
  header input[type=search], header select {
    background: var(--panel); color: var(--text); border: 1px solid var(--border);
    padding: 6px 10px; border-radius: 6px; font-size: 14px;
  }
  header input[type=search] { min-width: 260px; }
  header button {
    background: var(--blue); color: white; border: none; padding: 7px 14px;
    border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600;
  }
  header button:disabled { opacity: 0.5; cursor: not-allowed; }
  header button.secondary { background: var(--panel); border: 1px solid var(--border); color: var(--text); }

  .layout { display: grid; grid-template-columns: 260px 1fr; min-height: calc(100vh - 58px); }
  aside.filters {
    background: var(--panel-2); border-right: 1px solid var(--border);
    padding: 16px 18px; position: sticky; top: 58px; align-self: start;
    max-height: calc(100vh - 58px); overflow-y: auto;
  }
  aside h3 {
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
    color: var(--muted); margin: 14px 0 8px;
  }
  aside h3:first-child { margin-top: 0; }
  aside label { display: flex; align-items: center; gap: 6px; font-size: 13px; margin: 4px 0; cursor: pointer; }
  aside input[type=number], aside select {
    width: 100%; background: var(--panel); border: 1px solid var(--border);
    color: var(--text); padding: 5px 8px; border-radius: 4px; font-size: 13px;
  }
  .range-row { display: flex; gap: 6px; }
  .range-row input { width: 50%; }

  main { padding: 16px 20px; }
  .grid {
    display: grid; gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  }

  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 0; cursor: pointer; transition: transform 0.12s ease, border-color 0.12s;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .card-img {
    width: 100%; height: 180px; object-fit: cover; display: block; flex-shrink: 0;
    background: var(--panel-2);
  }
  .card-img-placeholder {
    width: 100%; height: 180px; display: flex; align-items: center; justify-content: center;
    background: var(--panel-2); color: var(--border); font-size: 32px; flex-shrink: 0;
  }
  .card-body { padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; flex: 1; }
  .card .row1 { display: flex; align-items: center; gap: 8px; }
  .score-badge {
    color: white; font-weight: 700; padding: 3px 8px; border-radius: 6px;
    font-size: 13px; min-width: 42px; text-align: center;
  }
  .verdict { font-weight: 700; font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; }
  .source { color: var(--muted); font-size: 11px; margin-left: auto; white-space: nowrap; }
  .title { font-size: 15px; font-weight: 600; color: var(--text); margin: 2px 0;
           line-height: 1.3; overflow: hidden; display: -webkit-box;
           -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .meta { color: var(--muted); font-size: 12px; display: flex; gap: 6px; flex-wrap: wrap; }
  .meta span::after { content: " · "; color: var(--border); }
  .meta span:last-child::after { content: ""; }
  .prices { display: flex; gap: 16px; padding-top: 8px; border-top: 1px solid var(--border); font-size: 13px; }
  .prices .box { flex: 1; }
  .prices .label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; display: block; }
  .prices .value { font-weight: 600; font-size: 15px; }

  .price-type {
    display: inline-block; font-size: 9px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; padding: 1px 5px; border-radius: 3px; margin-left: 4px;
    vertical-align: middle; position: relative; top: -1px;
  }
  .pt-bid { background: #7c3aed; color: white; }
  .pt-sold { background: #64748b; color: white; }
  .pt-asking { background: #334155; color: #cbd5e1; }
  .pt-auction { background: #0891b2; color: white; }
  .auction-ends {
    font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: 600;
    display: inline-block; margin-top: 4px;
  }
  .auction-ends.soon { background: #7c3aed22; color: #a78bfa; border: 1px solid #7c3aed55; }
  .auction-ends.live { background: #dc262622; color: #f87171; border: 1px solid #dc262655; }

  /* Modal */
  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.65);
    display: none; align-items: center; justify-content: center; z-index: 100;
    padding: 20px;
  }
  .modal-bg.active { display: flex; }
  .modal {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    max-width: 720px; width: 100%; max-height: 90vh; overflow: auto;
    padding: 22px 26px;
  }
  .modal h2 { margin-top: 0; margin-bottom: 4px; }
  .modal-gallery {
    display: flex; gap: 6px; margin-bottom: 14px; overflow-x: auto;
    scrollbar-width: thin; scrollbar-color: var(--border) transparent;
  }
  .modal-gallery img {
    height: 200px; width: auto; max-width: 340px; object-fit: cover;
    border-radius: 6px; flex-shrink: 0; border: 1px solid var(--border);
  }
  .modal .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 24px; margin: 14px 0; }
  .modal .item .k { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; display: block; }
  .modal .item .v { font-size: 14px; }
  .modal .ul-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .modal h4 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 6px; }
  .modal h4.bad { color: #fca5a5; }
  .modal h4.good { color: #86efac; }
  .modal ul { margin: 0; padding-left: 18px; font-size: 13px; }
  .modal ul li { margin-bottom: 3px; }
  .modal .actions { display: flex; gap: 10px; margin-top: 18px; }
  .modal .actions a, .modal .actions button {
    background: var(--blue); color: white; padding: 9px 16px; border-radius: 6px;
    border: none; font-size: 13px; font-weight: 600; cursor: pointer; text-decoration: none;
  }
  .modal .actions button.secondary { background: var(--panel-2); border: 1px solid var(--border); color: var(--text); }

  .empty { color: var(--muted); text-align: center; padding: 60px 20px; font-size: 14px; }

  /* Scrape progress panel */
  .progress-panel {
    display: none; position: fixed; bottom: 20px; right: 20px; z-index: 200;
    background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px;
    width: 420px; max-height: 320px; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    flex-direction: column; overflow: hidden;
  }
  .progress-panel.active { display: flex; }
  .progress-header {
    display: flex; align-items: center; gap: 8px; padding: 10px 14px;
    border-bottom: 1px solid var(--border); font-size: 13px; font-weight: 600;
  }
  .progress-header .spinner {
    width: 12px; height: 12px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin 0.7s linear infinite; flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .progress-header .close-btn {
    margin-left: auto; background: none; border: none; color: var(--muted);
    cursor: pointer; font-size: 16px; padding: 0 2px; line-height: 1;
  }
  .progress-log {
    flex: 1; overflow-y: auto; padding: 8px 12px; font-size: 11px;
    font-family: "Consolas", "Menlo", monospace; color: var(--muted);
    scroll-behavior: smooth;
  }
  .progress-log .log-line { padding: 1px 0; white-space: pre-wrap; word-break: break-all; }
  .progress-log .log-line.highlight { color: var(--text); }
  .progress-log .log-line.error { color: #f87171; }
  .progress-done {
    padding: 8px 14px; border-top: 1px solid var(--border); font-size: 12px;
    color: var(--green); display: none;
  }
</style>
</head>
<body>
<header>
  <h1>CarLooking</h1>
  <div class="stats" id="stats">Loading…</div>
  <input type="search" id="q" placeholder="Search title, model, location…">
  <select id="sort">
    <option value="score">Best match</option>
    <option value="distance_asc">Distance ↑ (closest)</option>
    <option value="allin_asc">All-in price ↑</option>
    <option value="price_asc">Price ↑</option>
    <option value="price_desc">Price ↓</option>
    <option value="year_desc">Year ↓ (newest)</option>
    <option value="year_asc">Year ↑ (oldest)</option>
    <option value="mileage_asc">Mileage ↑</option>
  </select>
  <button id="refresh">Refresh data</button>
  <button id="reload" class="secondary">Reload</button>
</header>

<div class="layout">
  <aside class="filters">
    <h3>Verdict</h3>
    <div id="verdicts"></div>

    <h3>Source</h3>
    <div id="sources"></div>

    <h3>Min score</h3>
    <input type="number" id="minScore" min="0" max="100" value="0" step="5">

    <h3>Price (all-in USD)</h3>
    <div class="range-row">
      <input type="number" id="priceMin" placeholder="Min" min="0">
      <input type="number" id="priceMax" placeholder="Max" min="0">
    </div>

    <h3>Year</h3>
    <div class="range-row">
      <input type="number" id="yearMin" placeholder="Min" min="1950" max="2030">
      <input type="number" id="yearMax" placeholder="Max" min="1950" max="2030">
    </div>

    <h3>Mileage ≤</h3>
    <input type="number" id="milesMax" placeholder="e.g. 150000" min="0">

    <h3 style="margin-top: 24px;">
      <button id="clearFilters" class="secondary" style="width:100%;background:var(--panel);border:1px solid var(--border);color:var(--text);padding:6px;border-radius:4px;cursor:pointer;font-size:12px;">Clear filters</button>
    </h3>
  </aside>

  <main>
    <div id="grid" class="grid"></div>
    <div id="empty" class="empty" style="display:none;">No listings match your filters.</div>
  </main>
</div>

<div class="progress-panel" id="progressPanel">
  <div class="progress-header">
    <div class="spinner" id="progressSpinner"></div>
    <span id="progressTitle">Scraping…</span>
    <button class="close-btn" onclick="document.getElementById('progressPanel').classList.remove('active')" title="Hide (scrape still running)">×</button>
  </div>
  <div class="progress-log" id="progressLog"></div>
  <div class="progress-done" id="progressDone">Done — reloading listings…</div>
</div>

<div class="modal-bg" id="modalBg">
  <div class="modal" id="modal"></div>
</div>

<script>
const VERDICT_COLORS = {
  "strong buy": "#22c55e",
  "worth a look": "#3b82f6",
  "mixed": "#f59e0b",
  "risky": "#f97316",
  "skip": "#ef4444",
};

let listings = [];
const state = {
  q: "", sort: "score", minScore: 0,
  priceMin: null, priceMax: null,
  yearMin: null, yearMax: null, milesMax: null,
  sources: new Set(), verdicts: new Set(),
};

function money(n) { return n == null ? "—" : "$" + n.toLocaleString(); }
function num(n) { return n == null ? "—" : n.toLocaleString(); }

function priceTypeLabel(pt) {
  return ({
    "bid": "Current bid", "sold": "Sold for", "auction": "Auction",
  })[pt] || "Asking";
}
function priceTypeBadge(pt) {
  if (!pt || pt === "asking") return "";
  const cls = "pt-" + pt;
  const txt = pt === "bid" ? "BID" : pt === "sold" ? "SOLD" : pt === "auction" ? "AUCTION" : pt.toUpperCase();
  return `<span class="price-type ${cls}">${txt}</span>`;
}

async function load() {
  const r = await fetch("/api/listings");
  listings = await r.json();
  buildFacets();
  render();
  updateStats();
}

async function updateStats() {
  const r = await fetch("/api/status");
  const s = await r.json();
  const mtime = s.data_mtime ? new Date(s.data_mtime * 1000).toLocaleString() : "never";
  const running = s.scrape?.running;
  const err = s.scrape?.last_error;
  let line = `${s.count} listings · updated ${mtime}`;
  if (running) line += " · scraping…";
  if (err && !running) line += ` · last error: ${err}`;
  document.getElementById("stats").textContent = line;
  document.getElementById("refresh").disabled = !!running;
  if (running) setTimeout(updateStats, 3000);
}

function buildFacets() {
  const sources = {}, verdicts = {};
  listings.forEach(l => {
    sources[l.source] = (sources[l.source] || 0) + 1;
    const v = l.verdict || "unknown";
    verdicts[v] = (verdicts[v] || 0) + 1;
  });

  const vEl = document.getElementById("verdicts");
  vEl.innerHTML = "";
  Object.entries(verdicts).sort((a,b)=>b[1]-a[1]).forEach(([v, n]) => {
    const id = `v-${v.replace(/\s+/g,'-')}`;
    vEl.insertAdjacentHTML("beforeend",
      `<label><input type="checkbox" class="vck" data-v="${v}" checked> <span style="color:${VERDICT_COLORS[v]||'var(--text)'}">${v}</span> <span style="color:var(--muted);margin-left:auto">${n}</span></label>`);
  });
  document.querySelectorAll(".vck").forEach(cb => cb.addEventListener("change", () => {
    state.verdicts = new Set(Array.from(document.querySelectorAll(".vck:checked")).map(e => e.dataset.v));
    render();
  }));
  state.verdicts = new Set(Object.keys(verdicts));

  const sEl = document.getElementById("sources");
  sEl.innerHTML = "";
  Object.entries(sources).sort((a,b)=>b[1]-a[1]).forEach(([s, n]) => {
    sEl.insertAdjacentHTML("beforeend",
      `<label><input type="checkbox" class="sck" data-s="${s}" checked> ${s} <span style="color:var(--muted);margin-left:auto">${n}</span></label>`);
  });
  document.querySelectorAll(".sck").forEach(cb => cb.addEventListener("change", () => {
    state.sources = new Set(Array.from(document.querySelectorAll(".sck:checked")).map(e => e.dataset.s));
    render();
  }));
  state.sources = new Set(Object.keys(sources));
}

function filterAndSort() {
  const q = state.q.toLowerCase();
  const now = Date.now();
  let out = listings.filter(l => {
    if (q && !(
      (l.title||"").toLowerCase().includes(q) ||
      (l.location||"").toLowerCase().includes(q) ||
      (l.model||"").toLowerCase().includes(q) ||
      (l.description||"").toLowerCase().includes(q)
    )) return false;
    if (!state.sources.has(l.source)) return false;
    if (!state.verdicts.has(l.verdict || "unknown")) return false;
    if ((l.score||0) < state.minScore) return false;
    const ap = l.all_in_price ?? l.price;
    if (state.priceMin != null && (ap ?? 0) < state.priceMin) return false;
    if (state.priceMax != null && (ap ?? 1e9) > state.priceMax) return false;
    if (state.yearMin != null && (l.year ?? 1) < state.yearMin) return false;
    if (state.yearMax != null && (l.year ?? 9999) > state.yearMax) return false;
    if (state.milesMax != null && (l.mileage ?? 0) > state.milesMax) return false;
    // Hide active bids with more than 24h left — current bid is not representative
    if (l.price_type === "bid" && l.auction_ends) {
      const hoursLeft = (new Date(l.auction_ends) - now) / 3600000;
      if (!isNaN(hoursLeft) && hoursLeft > 24) return false;
    }
    return true;
  });

  const cmpNum = (a, b, def) => (a ?? def) - (b ?? def);
  switch (state.sort) {
    case "distance_asc": out.sort((a,b)=>cmpNum(a.distance_miles, b.distance_miles, 1e9)); break;
    case "allin_asc": out.sort((a,b)=>cmpNum(a.all_in_price ?? a.price, b.all_in_price ?? b.price, 1e9)); break;
    case "price_asc": out.sort((a,b)=>cmpNum(a.price, b.price, 1e9)); break;
    case "price_desc": out.sort((a,b)=>cmpNum(b.price, a.price, -1)); break;
    case "year_desc": out.sort((a,b)=>cmpNum(b.year, a.year, -1)); break;
    case "year_asc": out.sort((a,b)=>cmpNum(a.year, b.year, 9999)); break;
    case "mileage_asc": out.sort((a,b)=>cmpNum(a.mileage, b.mileage, 1e9)); break;
    default: out.sort((a,b) => (b.score||0) - (a.score||0));
  }
  return out;
}

function render() {
  const out = filterAndSort();
  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  grid.innerHTML = "";
  if (out.length === 0) { empty.style.display = "block"; return; }
  empty.style.display = "none";
  out.forEach((l, i) => {
    const color = VERDICT_COLORS[l.verdict] || "#6b7280";
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.idx = listings.indexOf(l);
    const imgUrl = (l.images && l.images[0]) ? l.images[0] : "";
    const imgHtml = imgUrl
      ? `<img class="card-img" src="${escapeHTML(imgUrl)}" alt="${escapeHTML(l.title||"")}" loading="lazy" onerror="this.parentNode.replaceChild(Object.assign(document.createElement('div'),{className:'card-img-placeholder',textContent:'🚗'}),this)">`
      : `<div class="card-img-placeholder">🚗</div>`;
    card.innerHTML = `
      ${imgHtml}
      <div class="card-body">
        <div class="row1">
          <span class="score-badge" style="background:${color}">${(l.score||0).toFixed(0)}</span>
          <span class="verdict" style="color:${color}">${l.verdict||"?"}</span>
          <span class="source">${l.source}</span>
        </div>
        <div class="title">${escapeHTML(l.title||"")}</div>
        <div class="meta">
          ${l.year ? `<span>${l.year}</span>` : ""}
          ${l.mileage ? `<span>${num(l.mileage)} mi</span>` : ""}
          ${l.transmission ? `<span>${escapeHTML(l.transmission)}</span>` : ""}
          ${l.location ? `<span>${escapeHTML(l.location)}</span>` : ""}
          ${l.distance_miles ? `<span>${Math.round(l.distance_miles)} mi away</span>` : ""}
        </div>
        ${auctionEndsHtml(l)}
        <div class="prices">
          <div class="box"><span class="label">${priceTypeLabel(l.price_type)}</span><span class="value">${money(l.price)}${priceTypeBadge(l.price_type)}</span></div>
          <div class="box"><span class="label">A/C work</span><span class="value">${l.ac_estimate_usd == null ? "—" : (l.ac_estimate_usd === 0 ? "Works" : money(l.ac_estimate_usd))}</span></div>
          <div class="box"><span class="label">All-in</span><span class="value">${money(l.all_in_price)}</span></div>
        </div>
      </div>`;
    card.addEventListener("click", () => openModal(l));
    grid.appendChild(card);
  });
}

function openModal(l) {
  const color = VERDICT_COLORS[l.verdict] || "#6b7280";
  const modal = document.getElementById("modal");
  const galleryHtml = (l.images && l.images.length)
    ? `<div class="modal-gallery">${l.images.map(u=>`<img src="${escapeHTML(u)}" loading="lazy" onerror="this.style.display='none'">`).join("")}</div>`
    : "";
  modal.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
      <span class="score-badge" style="background:${color}">${(l.score||0).toFixed(0)}</span>
      <span class="verdict" style="color:${color}">${l.verdict||"?"}</span>
      <span class="source">${l.source}</span>
    </div>
    <h2>${escapeHTML(l.title||"")}</h2>
    ${auctionEndsHtml(l)}
    ${galleryHtml}
    <div class="grid2">
      <div class="item"><span class="k">${priceTypeLabel(l.price_type)}</span><span class="v">${money(l.price)}${priceTypeBadge(l.price_type)}</span></div>
      <div class="item"><span class="k">A/C retrofit</span><span class="v">${l.ac_estimate_usd == null ? "—" : (l.ac_estimate_usd === 0 ? "Works as-listed" : money(l.ac_estimate_usd))}</span></div>
      <div class="item"><span class="k">All-in</span><span class="v">${money(l.all_in_price)}</span></div>
      <div class="item"><span class="k">Year / Make / Model</span><span class="v">${[l.year, l.make, l.model].filter(Boolean).join(" ") || "—"}</span></div>
      <div class="item"><span class="k">Mileage</span><span class="v">${num(l.mileage)}</span></div>
      <div class="item"><span class="k">Transmission</span><span class="v">${escapeHTML(l.transmission || "—")}</span></div>
      <div class="item"><span class="k">Location</span><span class="v">${escapeHTML(l.location || "—")}</span></div>
      <div class="item"><span class="k">Distance</span><span class="v">${l.distance_miles ? Math.round(l.distance_miles) + " mi" : "—"}</span></div>
    </div>
    <div class="ul-cols">
      <div>
        <h4 class="bad">Concerns</h4>
        <ul>${(l.concerns||[]).map(c=>`<li>${escapeHTML(c)}</li>`).join("") || "<li style='color:var(--muted)'>none</li>"}</ul>
      </div>
      <div>
        <h4 class="good">Benefits</h4>
        <ul>${(l.benefits||[]).map(c=>`<li>${escapeHTML(c)}</li>`).join("") || "<li style='color:var(--muted)'>none</li>"}</ul>
      </div>
    </div>
    ${l.description ? `<div style="margin-top:18px"><h4>Description</h4><div style="font-size:13px;color:var(--muted);white-space:pre-wrap;max-height:240px;overflow:auto;border-top:1px solid var(--border);padding-top:8px">${escapeHTML(l.description)}</div></div>` : ""}
    <div class="actions">
      <a href="${l.url}" target="_blank" rel="noopener">Open listing ↗</a>
      <button class="secondary" onclick="closeModal()">Close</button>
    </div>
  `;
  document.getElementById("modalBg").classList.add("active");
}
function closeModal() { document.getElementById("modalBg").classList.remove("active"); }
document.getElementById("modalBg").addEventListener("click", e => {
  if (e.target.id === "modalBg") closeModal();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

// Wire up inputs
document.getElementById("q").addEventListener("input", e => { state.q = e.target.value; render(); });
document.getElementById("sort").addEventListener("change", e => { state.sort = e.target.value; render(); });
document.getElementById("minScore").addEventListener("input", e => { state.minScore = +e.target.value || 0; render(); });
document.getElementById("priceMin").addEventListener("input", e => { state.priceMin = e.target.value ? +e.target.value : null; render(); });
document.getElementById("priceMax").addEventListener("input", e => { state.priceMax = e.target.value ? +e.target.value : null; render(); });
document.getElementById("yearMin").addEventListener("input", e => { state.yearMin = e.target.value ? +e.target.value : null; render(); });
document.getElementById("yearMax").addEventListener("input", e => { state.yearMax = e.target.value ? +e.target.value : null; render(); });
document.getElementById("milesMax").addEventListener("input", e => { state.milesMax = e.target.value ? +e.target.value : null; render(); });

document.getElementById("clearFilters").addEventListener("click", () => {
  state.q = ""; state.minScore = 0;
  state.priceMin = state.priceMax = state.yearMin = state.yearMax = state.milesMax = null;
  ["q","minScore","priceMin","priceMax","yearMin","yearMax","milesMax"].forEach(id => {
    const el = document.getElementById(id);
    if (el.id === "minScore") el.value = 0; else el.value = "";
  });
  document.querySelectorAll(".sck, .vck").forEach(cb => cb.checked = true);
  state.sources = new Set(Array.from(document.querySelectorAll(".sck")).map(e => e.dataset.s));
  state.verdicts = new Set(Array.from(document.querySelectorAll(".vck")).map(e => e.dataset.v));
  render();
});

document.getElementById("reload").addEventListener("click", load);
document.getElementById("refresh").addEventListener("click", startRefresh);

async function startRefresh() {
  const btn = document.getElementById("refresh");
  btn.disabled = true;

  const r = await fetch("/api/refresh", { method: "POST" });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    alert("Refresh failed: " + body);
    btn.disabled = false;
    return;
  }

  // Show progress panel
  const panel = document.getElementById("progressPanel");
  const log = document.getElementById("progressLog");
  const done = document.getElementById("progressDone");
  const title = document.getElementById("progressTitle");
  const spinner = document.getElementById("progressSpinner");
  log.innerHTML = "";
  done.style.display = "none";
  spinner.style.display = "block";
  title.textContent = "Scraping…";
  panel.classList.add("active");

  // Connect SSE stream for live log lines
  const es = new EventSource("/api/refresh/log");
  es.onmessage = (e) => {
    let parsed;
    try { parsed = JSON.parse(e.data); } catch { return; }
    if (parsed && parsed.__done__) {
      es.close();
      spinner.style.display = "none";
      title.textContent = "Scrape complete";
      done.style.display = "block";
      btn.disabled = false;
      updateStats();
      setTimeout(() => { load(); done.style.display = "none"; }, 1200);
      return;
    }
    const line = typeof parsed === "string" ? parsed : JSON.stringify(parsed);
    const div = document.createElement("div");
    div.className = "log-line" +
      (line.startsWith("  >") || line.startsWith("[CarLooking]") ? " highlight" : "") +
      (line.startsWith("ERROR") || line.includes("crashed") ? " error" : "");
    div.textContent = line;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  };
  es.onerror = () => {
    es.close();
    btn.disabled = false;
    spinner.style.display = "none";
    title.textContent = "Scrape error — check log";
  };
}

function escapeHTML(s) {
  return String(s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

function auctionEndsHtml(l) {
  if (l.price_type !== "bid" || !l.auction_ends) return "";
  const end = new Date(l.auction_ends);
  if (isNaN(end)) return "";
  const hoursLeft = (end - Date.now()) / 3600000;
  if (hoursLeft <= 0) return `<div class="auction-ends soon">Auction ended</div>`;
  if (hoursLeft <= 24) {
    const h = Math.floor(hoursLeft), m = Math.round((hoursLeft - h) * 60);
    return `<div class="auction-ends live">Ends in ${h}h ${m}m — bid near final</div>`;
  }
  const days = Math.round(hoursLeft / 24);
  return `<div class="auction-ends soon">Ends in ~${days}d — bid will climb</div>`;
}

load();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5173"))
    print(f"\n  CarLooking web UI -> http://127.0.0.1:{port}/\n")
    app.run(host="127.0.0.1", port=port, debug=False)
