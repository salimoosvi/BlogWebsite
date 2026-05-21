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


@dataclass
class ScreenConfig:
    min_market_cap_usd: float = 500_000_000
    min_avg_volume: float = 500_000
    earnings_blackout_hours: int = 48
    cad_usd: float = 0.73          # CAD->USD; override with a live rate
    exclude_adr: bool = True


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

    mcap = _mcap_usd(stock, cfg)
    if mcap is None:
        reasons.append("market cap unavailable")
    elif mcap < cfg.min_market_cap_usd:
        reasons.append(f"market cap ${mcap/1e6:.0f}M < ${cfg.min_market_cap_usd/1e6:.0f}M floor")

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
