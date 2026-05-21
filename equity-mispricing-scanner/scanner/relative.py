"""Relative-valuation flags: cheap vs sector median and vs own multi-year median.

A 'cheap' flag fires when a multiple sits more than ``n_sd`` standard deviations
below the comparison median, on the cheap side. For ratios where lower = cheaper
(P/E, fwd P/E, EV/EBITDA, P/B, P/S, PEG) that means value < median - n_sd*sd.
"""
from __future__ import annotations

import statistics as stats
from dataclasses import dataclass, field
from typing import Optional

from .models import StockData

# Multiples where a lower value means cheaper.
LOWER_IS_CHEAP = ["pe", "forward_pe", "peg", "ev_ebitda", "pb", "ps"]


@dataclass
class CheapFlag:
    metric: str
    value: float
    median: float
    sd: float
    z: float          # (value - median) / sd  (negative = below median)
    basis: str        # "sector" or "own_history"


@dataclass
class RelativeFlags:
    sector_cheap: list[CheapFlag] = field(default_factory=list)
    own_history_cheap: list[CheapFlag] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def cheap_vs_both(self) -> list[str]:
        """Metrics flagged cheap on BOTH sector and own-history bases."""
        s = {f.metric for f in self.sector_cheap}
        o = {f.metric for f in self.own_history_cheap}
        return sorted(s & o)


def _get(stock: StockData, metric: str) -> Optional[float]:
    return getattr(stock.fundamentals, metric, None)


def sector_medians(universe: list[StockData]) -> dict[str, dict[str, tuple[float, float]]]:
    """Return {sector: {metric: (median, sd)}} computed across the universe.

    SD uses population stdev; sectors with <3 valid points for a metric are
    skipped (insufficient sample) and reported by the caller.
    """
    by_sector: dict[str, list[StockData]] = {}
    for s in universe:
        by_sector.setdefault(s.sector or "Unknown", []).append(s)

    out: dict[str, dict[str, tuple[float, float]]] = {}
    for sector, members in by_sector.items():
        metric_stats: dict[str, tuple[float, float]] = {}
        for metric in LOWER_IS_CHEAP:
            vals = [v for v in (_get(m, metric) for m in members)
                    if v is not None and v > 0]   # drop nonsensical negatives
            if len(vals) >= 3:
                med = stats.median(vals)
                sd = stats.pstdev(vals) if len(vals) > 1 else 0.0
                metric_stats[metric] = (med, sd)
        out[sector] = metric_stats
    return out


def flag_stock(stock: StockData,
               sector_stat: dict[str, tuple[float, float]],
               n_sd: float = 1.0) -> RelativeFlags:
    flags = RelativeFlags()

    # --- vs sector ---
    for metric in LOWER_IS_CHEAP:
        val = _get(stock, metric)
        if val is None or val <= 0:
            continue
        stat = sector_stat.get(metric)
        if not stat:
            flags.notes.append(f"sector sample too small for {metric}")
            continue
        med, sd = stat
        if sd <= 0:
            continue
        z = (val - med) / sd
        if z <= -n_sd:
            flags.sector_cheap.append(CheapFlag(metric, val, med, sd, z, "sector"))

    # --- vs own multi-year history ---
    hist_map = {
        "pe": stock.history.pe,
        "ev_ebitda": stock.history.ev_ebitda,
        "ps": stock.history.ps,
        "pb": stock.history.pb,
    }
    for metric, series in hist_map.items():
        val = _get(stock, metric)
        clean = [x for x in series if x is not None and x > 0]
        if val is None or val <= 0:
            continue
        if len(clean) < 4:
            flags.notes.append(f"insufficient own history for {metric} "
                               f"({len(clean)} pts) — own-5yr test skipped")
            continue
        med = stats.median(clean)
        sd = stats.pstdev(clean)
        if sd <= 0:
            continue
        z = (val - med) / sd
        if z <= -n_sd:
            flags.own_history_cheap.append(CheapFlag(metric, val, med, sd, z, "own_history"))

    return flags
