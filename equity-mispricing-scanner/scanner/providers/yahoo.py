"""yfinance-backed provider. No API key required.

Populates prices, fundamentals, technicals, news, insider transactions, analyst
rating changes, short interest, and a coarse historical P/E series. Every access
is guarded; a missing field becomes ``None`` plus a data_quality note rather than
an exception. Network access to Yahoo's hosts is required at run time.
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from ..models import (AnalystChange, Fundamentals, InsiderTxn, NewsItem,
                      StockData)
from ..technicals import compute_technicals

try:
    import yfinance as yf
except Exception:  # pragma: no cover - import guarded for environments w/o the dep
    yf = None


def _f(d: dict, *keys: str) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                continue
            return fv
    return None


def _retry(fn: Callable[[], Any], tries: int = 3, base: float = 0.6) -> Any:
    """Run fn with small exponential backoff on transient failures.

    Re-raises the last exception so the caller's guard can record a note.
    """
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - transient network/parse errors
            last = e
            if i < tries - 1:
                time.sleep(base * (2 ** i))
    if last:
        raise last


def _ts_to_date(ts: Any) -> Optional[date]:
    try:
        if ts is None:
            return None
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return ts.date() if hasattr(ts, "date") else None
    except Exception:
        return None


class YahooProvider:
    name = "yahoo"

    def __init__(self, history_period: str = "2y"):
        if yf is None:
            raise RuntimeError("yfinance is not installed (pip install yfinance)")
        self.history_period = history_period

    @staticmethod
    def yahoo_symbol(ticker: str, exchange: str | None) -> str:
        """Convert a raw/display ticker to a Yahoo symbol.

        US: dotted share classes use dashes (BRK.B -> BRK-B).
        TSX: dashes + .TO suffix (CCL.B -> CCL-B.TO; RY -> RY.TO).
        """
        s = ticker.strip().upper()
        if exchange == "TSX":
            if s.endswith(".TO"):
                return s
            return s.replace(".", "-") + ".TO"
        return s.replace(".", "-")

    def fetch(self, ticker: str, exchange: str | None = None) -> StockData:
        symbol = self.yahoo_symbol(ticker, exchange)

        sd = StockData(ticker=ticker, exchange=exchange)
        t = yf.Ticker(symbol)

        info: dict = {}
        try:
            info = _retry(lambda: t.get_info()) or {}
        except Exception as e:
            sd.note(f"info unavailable: {e}")

        self._basics(sd, info, symbol)
        self._fundamentals(sd, info, t)
        self._technicals(sd, info, t)
        self._news(sd, t)
        self._insiders(sd, t)
        self._analyst(sd, info, t)
        self._earnings_date(sd, t)
        self._hist_pe(sd, t)
        return sd

    # --- sections ---
    def _basics(self, sd: StockData, info: dict, symbol: str) -> None:
        sd.currency = info.get("currency") or info.get("financialCurrency")
        if sd.exchange == "TSX" and not sd.currency:
            sd.currency = "CAD"
        sd.sector = info.get("sector")
        sd.industry = info.get("industry")
        sd.market_cap = _f(info, "marketCap")
        sd.avg_volume = _f(info, "averageVolume", "averageDailyVolume10Day",
                           "averageDailyVolume3Month")
        name = (info.get("longName") or info.get("shortName") or "")
        sd.is_adr = ("ADR" in name.upper()) or None
        if sd.market_cap is None:
            sd.note("market cap missing")
        if sd.sector is None:
            sd.note("sector missing — sector-relative test will be blind")

    def _fundamentals(self, sd: StockData, info: dict, t: Any) -> None:
        f = sd.fundamentals
        f.pe = _f(info, "trailingPE")
        f.forward_pe = _f(info, "forwardPE")
        f.peg = _f(info, "pegRatio", "trailingPegRatio")
        f.ev_ebitda = _f(info, "enterpriseToEbitda")
        f.pb = _f(info, "priceToBook")
        f.ps = _f(info, "priceToSalesTrailing12Months")
        f.gross_margin = _f(info, "grossMargins")
        f.operating_margin = _f(info, "operatingMargins")
        f.rev_growth_yoy = _f(info, "revenueGrowth")
        f.eps_growth_yoy = _f(info, "earningsGrowth", "earningsQuarterlyGrowth")
        f.forward_eps = _f(info, "forwardEps")
        f.trailing_eps = _f(info, "trailingEps")
        f.ebitda = _f(info, "ebitda")
        f.shares_out = _f(info, "sharesOutstanding")
        f.total_debt = _f(info, "totalDebt")
        f.cash = _f(info, "totalCash")
        price = _f(info, "currentPrice", "regularMarketPrice")
        rev_per_share = _f(info, "revenuePerShare")
        if rev_per_share:
            f.sales_per_share = rev_per_share
        bvps = _f(info, "bookValue")
        if bvps:
            f.book_value_per_share = bvps

        fcf = _f(info, "freeCashflow")
        if fcf and sd.market_cap:
            f.fcf_yield = fcf / sd.market_cap
        if f.ebitda and f.total_debt is not None and f.cash is not None and f.ebitda > 0:
            f.net_debt_ebitda = (f.total_debt - f.cash) / f.ebitda

        # PEG fallback from forward PE / earnings growth.
        if f.peg is None and f.forward_pe and f.eps_growth_yoy and f.eps_growth_yoy > 0:
            f.peg = f.forward_pe / (f.eps_growth_yoy * 100)

        # ROIC: not reliably available on free data; record as a gap.
        roe = _f(info, "returnOnEquity")
        sd.note(f"ROIC unavailable from free source (ROE={roe:.1%} as weak proxy only)"
                if roe is not None else "ROIC/ROE unavailable from free source")

        self._margin_trends(sd, t)
        self._growth_cagr(sd, t)

    def _margin_trends(self, sd: StockData, t: Any) -> None:
        try:
            q = t.quarterly_income_stmt
        except Exception:
            q = None
        if q is None or getattr(q, "empty", True):
            sd.note("quarterly income statement unavailable — margin trend blank")
            return
        try:
            cols = list(q.columns)[:4]
            cols = list(reversed(cols))   # oldest -> newest
            rev_row = self._row(q, "Total Revenue", "TotalRevenue", "Operating Revenue", "Revenue")
            gp_row = self._row(q, "Gross Profit", "GrossProfit")
            op_row = self._row(q, "Operating Income", "OperatingIncome",
                               "Operating Income or Loss", "EBIT")
            for c in cols:
                rev = self._cell(rev_row, c)
                if not rev:
                    continue
                gp = self._cell(gp_row, c)
                op = self._cell(op_row, c)
                if gp is not None:
                    sd.fundamentals.gross_margin_trend.append(gp / rev)
                if op is not None:
                    sd.fundamentals.operating_margin_trend.append(op / rev)
        except Exception as e:
            sd.note(f"margin trend parse failed: {e}")

    def _growth_cagr(self, sd: StockData, t: Any) -> None:
        try:
            a = t.income_stmt
        except Exception:
            a = None
        if a is None or getattr(a, "empty", True):
            sd.note("annual income statement unavailable — 3yr CAGR blank")
            return
        try:
            cols = list(a.columns)        # newest first
            rev_row = self._row(a, "Total Revenue", "TotalRevenue", "Operating Revenue", "Revenue")
            eps_row = self._row(a, "Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS")
            if rev_row is not None and len(cols) >= 4:
                new = self._cell(rev_row, cols[0])
                old = self._cell(rev_row, cols[3])
                if new and old and old > 0:
                    sd.fundamentals.rev_cagr_3y = (new / old) ** (1 / 3) - 1
            if eps_row is not None and len(cols) >= 4:
                new = self._cell(eps_row, cols[0])
                old = self._cell(eps_row, cols[3])
                if new and old and old > 0:
                    sd.fundamentals.eps_cagr_3y = (new / old) ** (1 / 3) - 1
        except Exception as e:
            sd.note(f"CAGR parse failed: {e}")

    def _technicals(self, sd: StockData, info: dict, t: Any) -> None:
        closes: list[float] = []
        try:
            h = _retry(lambda: t.history(period=self.history_period, auto_adjust=True))
            if h is not None and not h.empty:
                closes = [float(x) for x in h["Close"].dropna().tolist()
                          if x == x and not math.isinf(float(x))]
        except Exception as e:
            sd.note(f"price history unavailable: {e}")
        hi = _f(info, "fiftyTwoWeekHigh")
        lo = _f(info, "fiftyTwoWeekLow")
        if closes:
            sd.technicals = compute_technicals(closes, hi, lo)
        else:
            sd.note("no price history — technicals blank")
        # Prefer Yahoo's own DMA fields if our window was short.
        if sd.technicals.dma50 is None:
            sd.technicals.dma50 = _f(info, "fiftyDayAverage")
        if sd.technicals.dma200 is None:
            sd.technicals.dma200 = _f(info, "twoHundredDayAverage")
        if sd.technicals.price is None:
            sd.technicals.price = _f(info, "currentPrice", "regularMarketPrice")

    def _news(self, sd: StockData, t: Any) -> None:
        try:
            items = t.news or []
        except Exception as e:
            sd.note(f"news unavailable: {e}")
            return
        cutoff = date.today() - timedelta(days=7)
        for it in items:
            content = it.get("content", it)   # yfinance changed schema; handle both
            title = content.get("title") or it.get("title") or ""
            pub = it.get("providerPublishTime")
            on = _ts_to_date(pub)
            if on is None:
                pd = content.get("pubDate") or content.get("displayTime")
                try:
                    on = datetime.fromisoformat(pd.replace("Z", "+00:00")).date() if pd else None
                except Exception:
                    on = None
            url = (it.get("link") or (content.get("canonicalUrl") or {}).get("url") or "")
            src = it.get("publisher") or (content.get("provider") or {}).get("displayName") or "?"
            if on is None or on >= cutoff:
                # tone/material left None: not auto-classified to avoid fabrication.
                sd.news.append(NewsItem(on=on, title=title, source=src, url=url))
        if not sd.news:
            sd.note("no news in last 7d (or source empty)")
        else:
            sd.note("news tone/materiality NOT auto-classified — analyst/NLP enricher to tag")

    def _insiders(self, sd: StockData, t: Any) -> None:
        try:
            df = t.insider_transactions
        except Exception as e:
            sd.note(f"insider data unavailable: {e}")
            return
        if df is None or getattr(df, "empty", True):
            sd.note("no insider transactions reported")
            return
        cutoff = date.today() - timedelta(days=90)
        buy_val = sell_val = 0.0
        try:
            for _, r in df.iterrows():
                on = _ts_to_date(r.get("Start Date") or r.get("Date"))
                if on and on < cutoff:
                    continue
                txt = str(r.get("Transaction", ""))
                shares = r.get("Shares")
                value = r.get("Value")
                shares = float(shares) if shares == shares and shares is not None else None
                value = float(value) if value == value and value is not None else None
                sd.sentiment.insider_txns_90d.append(
                    InsiderTxn(on=on, insider=str(r.get("Insider", "")),
                               transaction=txt, shares=shares, value=value))
                if value:
                    if "buy" in txt.lower() or "purchase" in txt.lower():
                        buy_val += value
                    elif "sale" in txt.lower() or "sell" in txt.lower():
                        sell_val += value
            sd.sentiment.insider_buy_value_90d = buy_val or None
            sd.sentiment.insider_sell_value_90d = sell_val or None
        except Exception as e:
            sd.note(f"insider parse failed: {e}")

    def _analyst(self, sd: StockData, info: dict, t: Any) -> None:
        sd.sentiment.short_interest_pct = _f(info, "shortPercentOfFloat")
        ss, ssp = _f(info, "sharesShort"), _f(info, "sharesShortPriorMonth")
        if ss is not None and ssp:
            sd.sentiment.short_interest_change = (ss - ssp) / ssp
        try:
            ud = t.upgrades_downgrades
        except Exception:
            ud = None
        if ud is None or getattr(ud, "empty", True):
            sd.note("analyst rating changes unavailable")
            return
        cutoff = date.today() - timedelta(days=30)
        ups = downs = 0
        try:
            for idx, r in ud.iterrows():
                on = _ts_to_date(idx)
                if on is None or on < cutoff:
                    continue
                action = str(r.get("Action", "")).lower()
                ch = AnalystChange(on=on, firm=str(r.get("Firm", "")), action=action,
                                   from_grade=str(r.get("FromGrade", "")),
                                   to_grade=str(r.get("ToGrade", "")))
                sd.sentiment.analyst_changes_30d.append(ch)
                if action in ("up", "init") or "upgrade" in action:
                    ups += 1
                elif action == "down" or "downgrade" in action:
                    downs += 1
            sd.sentiment.net_upgrades_30d = ups - downs
        except Exception as e:
            sd.note(f"analyst parse failed: {e}")

    def _earnings_date(self, sd: StockData, t: Any) -> None:
        try:
            ed = t.get_earnings_dates(limit=12)
        except Exception:
            ed = None
        if ed is None or getattr(ed, "empty", True):
            sd.note("earnings dates unavailable — 48h blackout filter cannot apply")
            return
        try:
            today = date.today()
            past = [d.date() if hasattr(d, "date") else d
                    for d in ed.index if (d.date() if hasattr(d, "date") else d) <= today]
            if past:
                sd.last_earnings_date = max(past)
        except Exception as e:
            sd.note(f"earnings date parse failed: {e}")

    def _hist_pe(self, sd: StockData, t: Any) -> None:
        """Coarse own-history P/E: year-end close / annual diluted EPS."""
        try:
            a = t.income_stmt
            h = t.history(period="6y", auto_adjust=True)
        except Exception:
            sd.note("historical P/E unavailable — own-5yr test will be skipped")
            return
        if (a is None or getattr(a, "empty", True) or h is None or getattr(h, "empty", True)):
            sd.note("historical P/E unavailable — own-5yr test will be skipped")
            return
        try:
            eps_row = self._row(a, "Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS")
            if eps_row is None:
                sd.note("no historical EPS row — own-5yr P/E skipped")
                return
            closes_by_year: dict[int, float] = {}
            for ts, px in h["Close"].dropna().items():
                closes_by_year[ts.year] = float(px)   # last seen = year-end-ish
            for col in a.columns:
                yr = col.year if hasattr(col, "year") else None
                eps = self._cell(eps_row, col)
                px = closes_by_year.get(yr) if yr else None
                if eps and eps > 0 and px:
                    sd.history.pe.append(px / eps)
            if len(sd.history.pe) < 4:
                sd.note(f"own-history P/E has only {len(sd.history.pe)} pts "
                        "(coarse, annual-based) — own-5yr test may be skipped")
        except Exception as e:
            sd.note(f"historical P/E parse failed: {e}")

    # --- DataFrame helpers ---
    @staticmethod
    def _row(df: Any, *names: str):
        for n in names:
            try:
                if n in df.index:
                    return df.loc[n]
            except Exception:
                continue
        return None

    @staticmethod
    def _cell(row: Any, col: Any) -> Optional[float]:
        if row is None:
            return None
        try:
            v = row[col]
            return float(v) if v == v and v is not None else None
        except Exception:
            return None
