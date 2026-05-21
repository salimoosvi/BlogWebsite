"""Unit tests for the offline-computable logic (no network).

Covers technicals, relative SD flags, screen filters, fair value, and scoring.
Run: python -m pytest tests/ -q   (or python tests/test_logic.py)
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from scanner.fairvalue import estimate
from scanner.models import (Fundamentals, InsiderTxn, NewsItem, StockData,
                            Technicals)
from scanner.relative import flag_stock, sector_medians
from scanner.scoring import score
from scanner.screen import ScreenConfig, screen_reasons
from scanner.technicals import compute_technicals, rsi, sma


def test_sma_and_rsi():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2], 5) is None
    # Monotonic rise -> RSI = 100.
    assert rsi(list(range(1, 30))) == 100.0
    # Need >= period+1 points.
    assert rsi([1, 2, 3], 14) is None


def test_compute_technicals_downtrend():
    closes = [100 - i * 0.5 for i in range(260)]  # steady decline
    t = compute_technicals(closes)
    assert t.price == closes[-1]
    assert t.above_50dma is False
    assert t.confirmed_downtrend is True


def test_compute_technicals_uptrend_not_downtrend():
    closes = [50 + i * 0.5 for i in range(260)]
    t = compute_technicals(closes)
    assert t.above_50dma is True
    assert t.confirmed_downtrend is False


def _stock(ticker, sector, pe, **kw):
    f = Fundamentals(pe=pe, forward_pe=kw.get("fpe"), ev_ebitda=kw.get("ev"),
                     ps=kw.get("ps"), pb=kw.get("pb"))
    sd = StockData(ticker=ticker, sector=sector, currency="USD",
                   market_cap=kw.get("mcap", 10e9), avg_volume=kw.get("adv", 5e6),
                   fundamentals=f)
    sd.technicals = Technicals(price=kw.get("price", 100), ret_1m=kw.get("ret_1m"),
                               above_50dma=kw.get("above50", True),
                               dma200=kw.get("dma200", 90))
    return sd


def test_sector_median_and_cheap_flag():
    universe = [
        _stock("A", "Tech", 30), _stock("B", "Tech", 32), _stock("C", "Tech", 28),
        _stock("D", "Tech", 31), _stock("CHEAP", "Tech", 8),
    ]
    medians = sector_medians(universe)
    assert "Tech" in medians and "pe" in medians["Tech"]
    cheap = [s for s in universe if s.ticker == "CHEAP"][0]
    flags = flag_stock(cheap, medians["Tech"], n_sd=1.0)
    metrics = [f.metric for f in flags.sector_cheap]
    assert "pe" in metrics
    # Expensive name should not be flagged.
    exp = [s for s in universe if s.ticker == "B"][0]
    assert not flag_stock(exp, medians["Tech"]).sector_cheap


def test_own_history_flag():
    s = _stock("Z", "Tech", 10)
    s.history.pe = [25, 26, 24, 27, 25]   # current 10 is well below own median
    medians = {"pe": (24.0, 5.0)}
    flags = flag_stock(s, medians, n_sd=1.0)
    assert "pe" in flags.cheap_vs_both()


def test_screen_filters():
    cfg = ScreenConfig()
    small = _stock("SMALL", "Tech", 10, mcap=100e6)
    assert any("market cap" in r for r in screen_reasons(small, cfg))
    thin = _stock("THIN", "Tech", 10, adv=100_000)
    assert any("ADV" in r for r in screen_reasons(thin, cfg))
    fresh = _stock("FRESH", "Tech", 10)
    fresh.last_earnings_date = date.today()
    assert any("earnings" in r for r in screen_reasons(fresh, cfg))
    good = _stock("GOOD", "Tech", 10)
    assert screen_reasons(good, cfg) == []


def test_cad_conversion_in_screen():
    cfg = ScreenConfig(cad_usd=0.50, min_market_cap_usd=500e6)
    # 900M CAD * 0.50 = 450M USD -> below floor.
    cad = _stock("CADCO", "Energy", 10, mcap=900e6)
    cad.currency = "CAD"
    assert any("market cap" in r for r in screen_reasons(cad, cfg))


def test_fair_value_multiples():
    s = _stock("FV", "Tech", 10, price=100)
    s.fundamentals.forward_eps = 10
    s.fundamentals.ebitda = 1e9
    s.fundamentals.shares_out = 1e8
    s.fundamentals.total_debt = 1e8
    s.fundamentals.cash = 2e8
    sector_stat = {"forward_pe": (15.0, 2.0), "ev_ebitda": (12.0, 1.0)}
    fv = estimate(s, sector_stat)
    # fwd PE method: 15 * 10 = 150.
    assert math.isclose(fv.methods["fwd_PE x fwd_EPS"], 150.0)
    assert fv.upside_pct is not None and fv.upside_pct > 0


def test_scoring_requires_two_criteria():
    s = _stock("Q", "Tech", 8, price=80, ret_1m=-0.15, above50=False)
    s.history.pe = [25, 26, 24, 27, 25]
    s.fundamentals.operating_margin_trend = [0.10, 0.12, 0.14, 0.16]
    s.fundamentals.rev_growth_yoy = 0.10
    s.sentiment.insider_buy_value_90d = 1_000_000
    s.sentiment.insider_sell_value_90d = 0.0
    medians = {"pe": (24.0, 5.0)}
    flags = flag_stock(s, medians, n_sd=1.0)
    sc = score(s, flags)
    assert sc.qualifies
    assert sc.conviction >= 2


def test_scoring_rejects_thin_evidence():
    s = _stock("WEAK", "Tech", 30, price=100)   # in-line valuation, no signals
    medians = {"pe": (30.0, 5.0)}
    flags = flag_stock(s, medians)
    sc = score(s, flags)
    assert not sc.qualifies


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
