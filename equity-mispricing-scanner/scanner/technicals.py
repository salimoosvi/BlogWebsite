"""Price-derived technical signals. Pure functions over a close-price series."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import Technicals


def sma(values: Sequence[float], window: int) -> Optional[float]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def rsi(values: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI. Returns None if not enough data."""
    if len(values) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    # Seed with the first `period` deltas.
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    # Wilder smoothing over the remainder.
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _slope_positive(values: Sequence[float], window: int) -> Optional[bool]:
    if len(values) < window:
        return None
    return values[-1] > values[-window]


def compute_technicals(closes: Sequence[float], highs_52w: Optional[float] = None,
                       lows_52w: Optional[float] = None) -> Technicals:
    """Build a Technicals object from a close-price series (oldest -> newest)."""
    t = Technicals()
    if not closes:
        return t
    price = closes[-1]
    t.price = price
    t.dma50 = sma(closes, 50)
    t.dma200 = sma(closes, 200)
    t.rsi14 = rsi(closes, 14)

    hi = highs_52w if highs_52w is not None else max(closes[-252:]) if len(closes) >= 1 else None
    lo = lows_52w if lows_52w is not None else min(closes[-252:]) if len(closes) >= 1 else None
    if hi:
        t.pct_from_52w_high = (price - hi) / hi
    if lo:
        t.pct_from_52w_low = (price - lo) / lo
    if len(closes) >= 22:
        prior = closes[-22]
        if prior:
            t.ret_1m = (price - prior) / prior

    if t.dma50 is not None:
        t.above_50dma = price > t.dma50
    if t.dma200 is not None:
        t.above_200dma = price > t.dma200

    # Confirmed downtrend: price < 50DMA < 200DMA, 50DMA sloping down,
    # and RSI not signalling a reversal (still < 50, i.e. no momentum bounce).
    dma50_slope_up = _slope_positive(closes, 10) if len(closes) >= 10 else None
    if (t.dma50 is not None and t.dma200 is not None and t.rsi14 is not None):
        t.confirmed_downtrend = (
            price < t.dma50 < t.dma200
            and dma50_slope_up is False
            and t.rsi14 < 50
        )
    return t
