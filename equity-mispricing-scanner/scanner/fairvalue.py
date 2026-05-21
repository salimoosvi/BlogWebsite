"""Multiples-based fair value.

Applies sector-median multiples to the stock's own per-share metrics to derive
an implied price under each method, then reports the low/median/high of the
methods that had data. No DCF here (free data lacks reliable forward FCF); the
report flags where a DCF or sum-of-parts would be more appropriate.
"""
from __future__ import annotations

import statistics as stats
from dataclasses import dataclass, field
from typing import Optional

from .models import StockData


@dataclass
class FairValue:
    low: Optional[float] = None
    mid: Optional[float] = None
    high: Optional[float] = None
    upside_pct: Optional[float] = None
    methods: dict[str, float] = field(default_factory=dict)   # method -> implied price
    notes: list[str] = field(default_factory=list)


def estimate(stock: StockData,
             sector_stat: dict[str, tuple[float, float]]) -> FairValue:
    fv = FairValue()
    f = stock.fundamentals
    price = stock.technicals.price
    implied: dict[str, float] = {}

    def med(metric: str) -> Optional[float]:
        s = sector_stat.get(metric)
        return s[0] if s else None

    # 1) Forward P/E x forward EPS
    fpe, feps = med("forward_pe"), f.forward_eps
    if fpe and feps and feps > 0:
        implied["fwd_PE x fwd_EPS"] = fpe * feps
    elif fpe and f.trailing_eps and f.trailing_eps > 0:
        implied["sector_PE x trailing_EPS"] = fpe * f.trailing_eps

    # 2) EV/EBITDA -> equity value per share
    ev_mult, ebitda = med("ev_ebitda"), f.ebitda
    if ev_mult and ebitda and ebitda > 0 and f.shares_out and f.shares_out > 0:
        ev = ev_mult * ebitda
        equity = ev - (f.total_debt or 0.0) + (f.cash or 0.0)
        if equity > 0:
            implied["EV/EBITDA"] = equity / f.shares_out

    # 3) P/S x sales per share
    ps_mult, sps = med("ps"), f.sales_per_share
    if ps_mult and sps and sps > 0:
        implied["P/S x sales_ps"] = ps_mult * sps

    # 4) P/B x book value per share
    pb_mult, bvps = med("pb"), f.book_value_per_share
    if pb_mult and bvps and bvps > 0:
        implied["P/B x BVPS"] = pb_mult * bvps

    if not implied:
        fv.notes.append("no fair-value method had sufficient data")
        return fv

    vals = sorted(implied.values())
    fv.methods = implied
    fv.low = vals[0]
    fv.high = vals[-1]
    fv.mid = stats.median(vals)
    if price and price > 0 and fv.mid:
        fv.upside_pct = (fv.mid - price) / price
    if len(implied) == 1:
        fv.notes.append("single-method estimate — treat the range as indicative only")
    return fv
