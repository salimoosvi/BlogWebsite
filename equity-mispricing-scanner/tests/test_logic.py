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


def test_resolve_cap_tiers():
    from scanner.screen import resolve_cap_tiers
    # Mega + Large -> floor $10B, no ceiling.
    lo, hi = resolve_cap_tiers(["mega", "large"])
    assert lo == 10e9 and hi is None
    # Large only -> $10B-$200B band.
    lo, hi = resolve_cap_tiers(["large"])
    assert lo == 10e9 and hi == 200e9
    # Mid+small contiguous -> $300M-$10B.
    lo, hi = resolve_cap_tiers(["small", "mid"])
    assert lo == 300e6 and hi == 10e9
    try:
        resolve_cap_tiers(["enormous"])
        assert False, "should raise on unknown tier"
    except KeyError:
        pass


def test_max_cap_ceiling():
    from scanner.screen import ScreenConfig, resolve_cap_tiers, screen_reasons
    lo, hi = resolve_cap_tiers(["large"])
    cfg = ScreenConfig(min_market_cap_usd=lo, max_market_cap_usd=hi)
    mega = _stock("MEGA", "Tech", 25, mcap=500e9)   # above $200B ceiling
    assert any("ceiling" in r for r in screen_reasons(mega, cfg))
    large = _stock("LRG", "Tech", 25, mcap=50e9)
    assert screen_reasons(large, cfg) == []
    small = _stock("SML", "Tech", 25, mcap=3e9)     # below $10B floor
    assert any("floor" in r for r in screen_reasons(small, cfg))


def test_us_only_guard():
    from scanner.screen import ScreenConfig, screen_reasons
    cfg = ScreenConfig(us_only=True, min_market_cap_usd=10e9)
    tsx = _stock("ENB", "Energy", 15, mcap=80e9)
    tsx.exchange = "TSX"
    tsx.currency = "CAD"
    assert any("non-US" in r for r in screen_reasons(tsx, cfg))
    us = _stock("AAPL", "Tech", 30, mcap=3000e9)
    us.exchange = "NASDAQ"
    us.currency = "USD"
    # Mega-cap US name passes a US-only, $10B-floor (no ceiling) screen.
    assert screen_reasons(us, cfg) == []


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


def test_parse_constituents_basic():
    import pandas as pd
    from scanner.universe import parse_constituents
    df = pd.DataFrame({
        "Symbol": ["AAPL", "BRK.B", "MSFT"],
        "Security": ["Apple", "Berkshire", "Microsoft"],
        "GICS Sector": ["Information Technology", "Financials", "Information Technology"],
    })
    entries = parse_constituents(df, "US")
    assert len(entries) == 3
    assert entries[0].ticker == "AAPL"
    assert entries[1].sector == "Financials"
    assert all(e.exchange == "US" for e in entries)


def test_parse_constituents_alt_headers_and_garbage():
    import pandas as pd
    from scanner.universe import parse_constituents
    df = pd.DataFrame({
        "Ticker symbol": ["RY", "ENB", "some footnote text here"],
        "Sector": ["Financials", "Energy", None],
    })
    entries = parse_constituents(df, "TSX")
    tickers = [e.ticker for e in entries]
    assert "RY" in tickers and "ENB" in tickers
    assert "SOME FOOTNOTE TEXT HERE" not in tickers   # space-containing row dropped


def test_parse_constituents_no_symbol_col():
    import pandas as pd
    from scanner.universe import parse_constituents
    df = pd.DataFrame({"Foo": [1, 2], "Bar": [3, 4]})
    assert parse_constituents(df, "US") == []


def test_merge_dedup_and_sector_backfill():
    from scanner.universe import UniverseEntry, merge
    a = [UniverseEntry("AAPL", "US", None), UniverseEntry("MSFT", "US", "Tech")]
    b = [UniverseEntry("AAPL", "US", "Information Technology"),
         UniverseEntry("NVDA", "NASDAQ", "Tech")]
    out = merge(a, b)
    by_key = {(e.ticker, e.exchange): e for e in out}
    assert len(out) == 3
    assert by_key[("AAPL", "US")].sector == "Information Technology"   # backfilled


def test_yahoo_symbol_normalization():
    from scanner.providers.yahoo import YahooProvider
    f = YahooProvider.yahoo_symbol
    assert f("BRK.B", "US") == "BRK-B"
    assert f("AAPL", "NASDAQ") == "AAPL"
    assert f("RY", "TSX") == "RY.TO"
    assert f("CCL.B", "TSX") == "CCL-B.TO"
    assert f("ENB.TO", "TSX") == "ENB.TO"   # already suffixed, not doubled


def test_f_rejects_nan_and_bool():
    from scanner.providers.yahoo import _f
    assert _f({"a": float("nan")}, "a") is None
    assert _f({"a": float("inf")}, "a") is None
    assert _f({"a": True}, "a") is None        # bool is not a number here
    assert _f({"a": 12.5}, "a") == 12.5
    assert _f({"a": None, "b": 3}, "a", "b") == 3.0


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
