"""Normalized data models for the mispricing scanner.

Every numeric field is Optional. A missing value is recorded as ``None`` and a
human-readable note is appended to ``StockData.data_quality`` so the report can
say "data missing/stale" instead of fabricating a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Fundamentals:
    pe: Optional[float] = None
    forward_pe: Optional[float] = None
    peg: Optional[float] = None
    ev_ebitda: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None
    fcf_yield: Optional[float] = None          # FCF / market cap
    roic: Optional[float] = None               # proxy if true ROIC unavailable
    net_debt_ebitda: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    # Oldest -> newest, up to last 4 quarters.
    gross_margin_trend: list[float] = field(default_factory=list)
    operating_margin_trend: list[float] = field(default_factory=list)
    rev_growth_yoy: Optional[float] = None
    eps_growth_yoy: Optional[float] = None
    rev_cagr_3y: Optional[float] = None
    eps_cagr_3y: Optional[float] = None
    # Per-share inputs used by the fair-value engine.
    forward_eps: Optional[float] = None
    trailing_eps: Optional[float] = None
    ebitda: Optional[float] = None
    sales_per_share: Optional[float] = None
    book_value_per_share: Optional[float] = None
    shares_out: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None


@dataclass
class HistoricalRatios:
    """Stock's own multi-year ratio history, for the 'own 5yr median' test.

    Free data makes this coarse (annual EPS x year-end price). Empty lists mean
    'insufficient history' rather than zero.
    """
    pe: list[float] = field(default_factory=list)
    ev_ebitda: list[float] = field(default_factory=list)
    ps: list[float] = field(default_factory=list)
    pb: list[float] = field(default_factory=list)


@dataclass
class Technicals:
    price: Optional[float] = None
    dma50: Optional[float] = None
    dma200: Optional[float] = None
    rsi14: Optional[float] = None
    pct_from_52w_high: Optional[float] = None   # negative = below high
    pct_from_52w_low: Optional[float] = None
    ret_1m: Optional[float] = None              # ~21 trading-day return
    above_50dma: Optional[bool] = None
    above_200dma: Optional[bool] = None
    confirmed_downtrend: Optional[bool] = None


@dataclass
class AnalystChange:
    on: Optional[date]
    firm: str
    action: str            # up / down / init / reiterated / maintained
    from_grade: str
    to_grade: str


@dataclass
class InsiderTxn:
    on: Optional[date]
    insider: str
    transaction: str       # Buy / Sale / etc.
    shares: Optional[float]
    value: Optional[float]


@dataclass
class Sentiment:
    analyst_changes_30d: list[AnalystChange] = field(default_factory=list)
    net_upgrades_30d: Optional[int] = None       # upgrades - downgrades
    short_interest_pct: Optional[float] = None   # % of float
    short_interest_change: Optional[float] = None
    insider_txns_90d: list[InsiderTxn] = field(default_factory=list)
    insider_buy_value_90d: Optional[float] = None
    insider_sell_value_90d: Optional[float] = None
    options_skew: Optional[float] = None         # OTM put IV - OTM call IV
    social_sentiment_delta: Optional[float] = None


@dataclass
class NewsItem:
    on: Optional[date]
    title: str
    source: str
    url: str
    material: Optional[bool] = None
    tone: Optional[str] = None     # bullish / bearish / neutral


@dataclass
class StockData:
    ticker: str
    exchange: Optional[str] = None
    currency: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[float] = None          # native currency
    avg_volume: Optional[float] = None
    last_earnings_date: Optional[date] = None
    is_adr: Optional[bool] = None
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    history: HistoricalRatios = field(default_factory=HistoricalRatios)
    technicals: Technicals = field(default_factory=Technicals)
    sentiment: Sentiment = field(default_factory=Sentiment)
    news: list[NewsItem] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        if msg not in self.data_quality:
            self.data_quality.append(msg)
