"""Render the scan into the mandated markdown format.

The tool fills in everything quantitative (table, fair-value math, the evidence
that fired each criterion). The narrative judgment pieces of each thesis — the
exact bear case, the variant view, the kill switch — are emitted as evidence +
prompts for the analyst, NOT auto-generated prose, so the report never invents a
catalyst or a news interpretation that the data didn't support.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .fairvalue import FairValue
from .models import StockData
from .relative import RelativeFlags
from .scoring import Scorecard


@dataclass
class Candidate:
    stock: StockData
    flags: RelativeFlags
    fair_value: FairValue
    scorecard: Scorecard


def _fmt(x: Optional[float], pct: bool = False, money: bool = False) -> str:
    if x is None:
        return "n/a"
    if pct:
        return f"{x:+.1%}"
    if money:
        return f"{x:,.2f}"
    return f"{x:.2f}"


def apply_sector_cap(cands: list[Candidate], max_per_sector: int = 3) -> list[Candidate]:
    """Keep highest-conviction names; drop those exceeding the per-sector cap."""
    ranked = sorted(cands, key=lambda c: c.scorecard.conviction, reverse=True)
    kept: list[Candidate] = []
    counts: dict[str, int] = {}
    for c in ranked:
        sec = c.stock.sector or "Unknown"
        if counts.get(sec, 0) < max_per_sector:
            kept.append(c)
            counts[sec] = counts.get(sec, 0) + 1
    return kept


def build_report(cands: list[Candidate], top_n: int = 10,
                 max_per_sector: int = 3, as_of: Optional[date] = None) -> str:
    as_of = as_of or date.today()
    qualified = [c for c in cands if c.scorecard.qualifies]
    qualified = apply_sector_cap(qualified, max_per_sector)
    qualified = sorted(qualified, key=lambda c: c.scorecard.conviction, reverse=True)[:top_n]

    lines: list[str] = []
    lines.append(f"# Daily Long-Only Mispricing Scan — {as_of.isoformat()}")
    lines.append("")
    lines.append("> Research, not advice. Numbers are sourced live at run time; "
                 "fields shown as `n/a` were missing/stale at the source and are "
                 "**not** estimated. Verify before acting.")
    lines.append("")

    if not qualified:
        lines.append("**No names cleared the >=2-of-4 qualification gate this run.** "
                     "This is a valid outcome — see the diagnostics section below.")
    else:
        lines.append("| Ticker | Exchange | Currency | Price | Est. Fair Value (mid) | "
                     "Upside % | Conviction | Primary Catalyst | Time Horizon |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for c in qualified:
            s = c.stock
            cat = _primary_catalyst(c)
            lines.append(
                f"| {s.ticker} | {s.exchange or 'n/a'} | {s.currency or 'n/a'} "
                f"| {_fmt(s.technicals.price, money=True)} "
                f"| {_fmt(c.fair_value.mid, money=True)} "
                f"| {_fmt(c.fair_value.upside_pct, pct=True)} "
                f"| {c.scorecard.conviction}/5 | {cat} | [analyst to set] |")
        lines.append("")
        for c in qualified:
            lines.extend(_thesis_block(c))

    lines.extend(_watching_section(qualified))
    lines.extend(_diagnostics(cands))
    return "\n".join(lines)


def _primary_catalyst(c: Candidate) -> str:
    s = c.stock
    if s.last_earnings_date:
        return f"next earnings (last: {s.last_earnings_date})"
    if c.scorecard.c3_sentiment_contradicts:
        return "sentiment inflection"
    if c.scorecard.c2_overpunished_news:
        return "news overhang clearing"
    return "[analyst to set]"


def _thesis_block(c: Candidate) -> list[str]:
    s, sc, fv = c.stock, c.scorecard, c.fair_value
    out: list[str] = []
    out.append("")
    out.append(f"## {s.ticker} — {s.sector or 'n/a'} | conviction {sc.conviction}/5 "
               f"| {sc.criteria_met}/4 criteria | currency {s.currency or 'n/a'}")
    out.append("")
    out.append("**Fair-value math (multiples comps):**")
    if fv.methods:
        for method, price in fv.methods.items():
            out.append(f"- {method}: {price:,.2f}")
        out.append(f"- Range {fv.low:,.2f} – {fv.high:,.2f}, mid {fv.mid:,.2f}; "
                   f"upside {_fmt(fv.upside_pct, pct=True)} vs price "
                   f"{_fmt(s.technicals.price, money=True)}")
    else:
        out.append("- n/a — insufficient data for a multiples estimate "
                   "(consider DCF / sum-of-parts manually)")
    for n in fv.notes:
        out.append(f"  - note: {n}")
    out.append("")
    out.append("**Why it qualified (criteria that fired):**")
    for key in ("C1", "C2", "C3", "C4"):
        if key in sc.evidence:
            fired = getattr(sc, {"C1": "c1_valuation", "C2": "c2_overpunished_news",
                                 "C3": "c3_sentiment_contradicts",
                                 "C4": "c4_fundamental_trend"}[key])
            mark = "[x]" if fired else "[ ]"
            out.append(f"- {mark} {key}: {sc.evidence[key]}")
    out.append("")
    out.append("**Thesis (analyst completes the narrative from the evidence above):**")
    out.append("- *Market is pricing in:* "
               f"price sits {_fmt(s.technicals.pct_from_52w_high, pct=True)} from 52w high, "
               f"RSI {_fmt(s.technicals.rsi14)} — [describe the bear case the price implies].")
    out.append("- *Variant view:* [what you see differently — anchor to the fired criteria].")
    out.append("- *Catalyst / timeline:* [event that closes the gap].")
    out.append("- *Kill switch:* [what would prove this wrong — e.g. break below "
               f"{_fmt(s.technicals.dma200, money=True)} 200DMA, margin reversal].")
    if s.data_quality:
        out.append("")
        out.append("**Data quality / gaps:** " + "; ".join(s.data_quality))
    out.append("")
    out.append("**Recent news (last 7d):**")
    if s.news:
        for n in s.news[:6]:
            tag = f"[{n.tone or '?'}/{'material' if n.material else 'immaterial' if n.material is False else '?'}]"
            out.append(f"- {n.on or 'n/a'} {tag} {n.title} — {n.source} ({n.url})")
    else:
        out.append("- none retrieved (or news source unavailable)")
    out.append("")
    out.append("---")
    return out


def _watching_section(cands: list[Candidate]) -> list[str]:
    out = ["", "## What I'm watching this week", ""]
    out.append("_Macro/sector items can't be auto-pulled offline; these are "
               "scaffolds plus any dated catalysts the scan found. Replace bracketed "
               "items with live calendar entries before relying on this._")
    out.append("1. [Fed/BoC] rate decision or speak that re-rates duration-sensitive multiples.")
    out.append("2. [Commodity/FX] print relevant to the cyclical/energy/materials names above "
               "(note: CAD names carry USD/CAD risk).")
    out.append("3. Sector earnings from the largest peers of the names above — read-through risk:")
    earnings = sorted({(c.stock.sector or "?", c.stock.last_earnings_date)
                       for c in cands if c.stock.last_earnings_date})
    for sec, d in earnings[:5]:
        out.append(f"   - {sec}: watch peer prints around {d}")
    out.append("")
    return out


def _diagnostics(cands: list[Candidate]) -> list[str]:
    out = ["## Run diagnostics", ""]
    out.append(f"- Names evaluated (post-screen): {len(cands)}")
    out.append(f"- Qualified (>=2/4): {sum(1 for c in cands if c.scorecard.qualifies)}")
    missing_hist = sum(1 for c in cands
                       if any('own history' in n for n in c.flags.notes))
    out.append(f"- Names where own-5yr history was insufficient (C1 partially blind): {missing_hist}")
    no_fv = sum(1 for c in cands if not c.fair_value.methods)
    out.append(f"- Names with no multiples-based fair value: {no_fv}")
    out.append("")
    return out
