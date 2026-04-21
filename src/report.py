"""HTML + console reporting."""
from __future__ import annotations

import html
import json
import os
from datetime import datetime
from typing import Iterable

from .models import Listing


VERDICT_COLORS = {
    "strong buy": "#16a34a",
    "worth a look": "#2563eb",
    "mixed": "#ca8a04",
    "risky": "#ea580c",
    "skip": "#dc2626",
}


def _card_html(l: Listing) -> str:
    verdict = l.verdict or "mixed"
    color = VERDICT_COLORS.get(verdict, "#6b7280")
    price_str = f"${l.price:,}" if l.price else "—"
    all_in_str = f"${l.all_in_price:,}" if l.all_in_price else "—"
    ac = f"${l.ac_estimate_usd:,}" if l.ac_estimate_usd else ("Works" if l.ac_estimate_usd == 0 else "—")
    miles = f"{l.mileage:,} mi" if l.mileage else "—"
    year = str(l.year) if l.year else "—"

    concerns = "".join(f"<li>{html.escape(c)}</li>" for c in l.concerns[:8])
    benefits = "".join(f"<li>{html.escape(b)}</li>" for b in l.benefits[:8])

    return f"""
    <article class="listing">
      <div class="header">
        <span class="score" style="background:{color}">{l.score or 0:.0f}</span>
        <span class="verdict" style="color:{color}">{html.escape(verdict.upper())}</span>
        <span class="source">{html.escape(l.source)}</span>
      </div>
      <h3><a href="{html.escape(l.url)}" target="_blank" rel="noopener">{html.escape(l.title)}</a></h3>
      <div class="meta">
        <span>{year}</span> · <span>{miles}</span> · <span>{html.escape(l.transmission or "?")}</span>
        {f' · <span>{html.escape(l.location)}</span>' if l.location else ''}
        {f' · <span>{int(l.distance_miles)} mi from you</span>' if l.distance_miles else ''}
      </div>
      <div class="prices">
        <div><span class="label">Asking</span><span class="value">{price_str}</span></div>
        <div><span class="label">A/C work</span><span class="value">{ac}</span></div>
        <div><span class="label">All-in</span><span class="value">{all_in_str}</span></div>
      </div>
      <div class="flags">
        <div class="col">
          <h4>Concerns</h4>
          <ul>{concerns or '<li class="none">None noted</li>'}</ul>
        </div>
        <div class="col good">
          <h4>Benefits</h4>
          <ul>{benefits or '<li class="none">None noted</li>'}</ul>
        </div>
      </div>
    </article>
    """


def write_html_report(listings: list[Listing], path: str, criteria: dict, top_n: int = 25) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    top = listings[:top_n]
    cards = "\n".join(_card_html(l) for l in top)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_total = len(listings)

    page = f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>CarLooking Report — {ts}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; background: #0f172a; color: #e2e8f0; max-width: 1100px; margin: auto; }}
  h1 {{ margin-top: 0; }}
  .summary {{ background: #1e293b; padding: 16px 20px; border-radius: 10px; margin-bottom: 24px; }}
  .summary dt {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }}
  .summary dd {{ margin: 0 0 10px; font-size: 18px; }}
  article.listing {{ background: #1e293b; border-radius: 12px; padding: 18px 22px;
                     margin-bottom: 18px; border: 1px solid #334155; }}
  .header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
  .score {{ color: white; font-weight: 700; padding: 4px 10px; border-radius: 8px; font-size: 14px; }}
  .verdict {{ font-weight: 700; font-size: 12px; letter-spacing: 0.1em; }}
  .source {{ color: #94a3b8; font-size: 12px; margin-left: auto; }}
  h3 {{ margin: 4px 0 6px; font-size: 18px; }}
  h3 a {{ color: #f1f5f9; text-decoration: none; }}
  h3 a:hover {{ color: #60a5fa; text-decoration: underline; }}
  .meta {{ color: #94a3b8; font-size: 13px; margin-bottom: 12px; }}
  .prices {{ display: flex; gap: 24px; margin-bottom: 12px; padding: 10px 0;
             border-top: 1px solid #334155; border-bottom: 1px solid #334155; }}
  .prices .label {{ display: block; color: #94a3b8; font-size: 11px;
                    text-transform: uppercase; letter-spacing: 0.05em; }}
  .prices .value {{ font-size: 17px; font-weight: 600; }}
  .flags {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; font-size: 13px; }}
  .flags h4 {{ margin: 0 0 6px; font-size: 12px; color: #fca5a5; text-transform: uppercase;
               letter-spacing: 0.05em; }}
  .flags .good h4 {{ color: #86efac; }}
  .flags ul {{ margin: 0; padding-left: 18px; color: #cbd5e1; }}
  .flags li {{ margin-bottom: 3px; }}
  .flags li.none {{ color: #64748b; font-style: italic; }}
  footer {{ color: #64748b; font-size: 12px; margin-top: 32px; text-align: center; }}
</style>
</head>
<body>
<h1>CarLooking Report</h1>
<p style="color:#94a3b8">Generated {ts} · {n_total} total listings found · Showing top {len(top)}</p>

<div class="summary">
  <dl>
    <dt>Search</dt><dd>Manual · ${criteria.get('min_price', 0):,}–${criteria.get('max_price', 0):,} · Within {criteria.get('radius_miles')} mi of {criteria.get('zip_code')}</dd>
  </dl>
</div>

{cards}

<footer>
  Data scraped from public listings. Verify independently before buying.<br>
  Seller PII, phone numbers, and exact addresses should never be committed to git.
</footer>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def write_json(listings: list[Listing], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in listings], f, indent=2, default=str)


def print_summary(listings: Iterable[Listing], top_n: int = 15) -> None:
    """Console-friendly summary using rich, falling back to plain text."""
    listings = list(listings)
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        for l in listings[:top_n]:
            print(f"[{l.score or 0:5.1f}] {l.verdict:<12} ${l.price or 0:>7,} "
                  f"· {l.source:<20} · {l.title[:80]}")
        return

    console = Console()
    t = Table(title=f"Top {min(top_n, len(listings))} matches")
    t.add_column("Score", justify="right")
    t.add_column("Verdict")
    t.add_column("Price", justify="right")
    t.add_column("All-in", justify="right")
    t.add_column("Year")
    t.add_column("Miles", justify="right")
    t.add_column("Source")
    t.add_column("Title", overflow="fold", max_width=60)

    for l in listings[:top_n]:
        verdict_color = {
            "strong buy": "green",
            "worth a look": "cyan",
            "mixed": "yellow",
            "risky": "orange3",
            "skip": "red",
        }.get(l.verdict or "", "white")
        t.add_row(
            f"{l.score or 0:.1f}",
            f"[{verdict_color}]{l.verdict or '?'}[/]",
            f"${l.price:,}" if l.price else "—",
            f"${l.all_in_price:,}" if l.all_in_price else "—",
            str(l.year or "—"),
            f"{l.mileage:,}" if l.mileage else "—",
            l.source,
            l.title,
        )
    console.print(t)
    console.print(f"\n[dim]{len(listings)} total listings scraped. HTML report written.[/dim]")
