"""Universe exclusion filters from the mandate.

Excludes: market cap below threshold (native currency, with CAD->USD fx for the
USD floor), avg daily volume below threshold, ADR-only listings, and names that
reported earnings within the lookback window (default 48h).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .models import StockData

# Conventional USD market-cap tier bands: (min_inclusive, max_exclusive).
# A None max means "no upper bound".
CAP_TIERS: dict[str, tuple[float, Optional[float]]] = {
    "mega": (200_000_000_000, None),
    "large": (10_000_000_000, 200_000_000_000),
    "mid": (2_000_000_000, 10_000_000_000),
    "small": (300_000_000, 2_000_000_000),
}


def resolve_cap_tiers(tiers: list[str]) -> tuple[float, Optional[float]]:
    """Combine one or more tier names into a single (min, max) USD band.

    min = lowest selected floor; max = highest selected ceiling (None wins, i.e.
    if any selected tier is open-ended the combined band has no ceiling).
    Raises KeyError on an unknown tier name.
    """
    lows: list[float] = []
    highs: list[Optional[float]] = []
    for t in tiers:
        key = t.strip().lower()
        if key not in CAP_TIERS:
            raise KeyError(f"unknown cap tier '{t}'; choose from {list(CAP_TIERS)}")
        lo, hi = CAP_TIERS[key]
        lows.append(lo)
        highs.append(hi)
    combined_max: Optional[float] = None if any(h is None for h in highs) else max(highs)
    return min(lows), combined_max


@dataclass
class ScreenConfig:
    min_market_cap_usd: float = 500_000_000
    max_market_cap_usd: Optional[float] = None
    min_avg_volume: float = 500_000
    earnings_blackout_hours: int = 48
    cad_usd: float = 0.73          # CAD->USD; override with a live rate
    exclude_adr: bool = True
    us_only: bool = False          # exclude TSX / non-USD listings


def _mcap_usd(stock: StockData, cfg: ScreenConfig) -> Optional[float]:
    if stock.market_cap is None:
        return None
    if (stock.currency or "USD").upper() == "CAD":
        return stock.market_cap * cfg.cad_usd
    return stock.market_cap


def screen_reasons(stock: StockData, cfg: ScreenConfig,
                   today: Optional[date] = None) -> list[str]:
    """Return a list of exclusion reasons. Empty list == passes the screen."""
    today = today or date.today()
    reasons: list[str] = []

    if cfg.us_only:
        cur = (stock.currency or "").upper()
        if stock.exchange == "TSX" or cur == "CAD":
            reasons.append(f"non-US listing (exchange={stock.exchange}, currency={cur or '?'})")

    mcap = _mcap_usd(stock, cfg)
    if mcap is None:
        reasons.append("market cap unavailable")
    else:
        if mcap < cfg.min_market_cap_usd:
            reasons.append(f"market cap ${mcap/1e9:.1f}B < ${cfg.min_market_cap_usd/1e9:.1f}B floor")
        if cfg.max_market_cap_usd is not None and mcap > cfg.max_market_cap_usd:
            reasons.append(f"market cap ${mcap/1e9:.1f}B > ${cfg.max_market_cap_usd/1e9:.1f}B ceiling")

    if stock.avg_volume is None:
        reasons.append("avg volume unavailable")
    elif stock.avg_volume < cfg.min_avg_volume:
        reasons.append(f"ADV {stock.avg_volume:,.0f} < {cfg.min_avg_volume:,.0f} floor")

    if cfg.exclude_adr and stock.is_adr:
        reasons.append("ADR-only listing")

    if stock.last_earnings_date is not None:
        delta = today - stock.last_earnings_date
        if timedelta(0) <= delta <= timedelta(hours=cfg.earnings_blackout_hours):
            reasons.append(f"earnings {delta} ago (within {cfg.earnings_blackout_hours}h blackout)")

    return reasons


def passes(stock: StockData, cfg: ScreenConfig, today: Optional[date] = None) -> bool:
    return len(screen_reasons(stock, cfg, today)) == 0
