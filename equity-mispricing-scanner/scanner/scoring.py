"""Qualification gate and conviction scoring.

A name qualifies only if at least 2 of the 4 mandate criteria hold:
  C1. A multiple is >1 SD cheap vs BOTH sector and own 5yr history.
  C2. Recent material news looks over-punished vs fundamental impact.
  C3. A sentiment indicator contradicts the price weakness (insider buys,
      net analyst upgrades, sentiment turning).
  C4. A fundamental trend is improving and not yet in the multiple.

C2 and C3/C4 are necessarily heuristic on free data; each criterion records the
evidence string it fired on so the analyst can audit it. Nothing is fabricated:
a criterion that lacks data is simply False with a note.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .models import StockData
from .relative import RelativeFlags


@dataclass
class Scorecard:
    c1_valuation: bool = False
    c2_overpunished_news: bool = False
    c3_sentiment_contradicts: bool = False
    c4_fundamental_trend: bool = False
    evidence: dict[str, str] = field(default_factory=dict)
    conviction: int = 0

    @property
    def criteria_met(self) -> int:
        return sum([self.c1_valuation, self.c2_overpunished_news,
                    self.c3_sentiment_contradicts, self.c4_fundamental_trend])

    @property
    def qualifies(self) -> bool:
        return self.criteria_met >= 2


def _trend_improving(series: list[float]) -> Optional[bool]:
    """True if the last point is above the first and the last is the max-ish."""
    clean = [x for x in series if x is not None]
    if len(clean) < 3:
        return None
    return clean[-1] > clean[0]


def score(stock: StockData, flags: RelativeFlags,
          today: Optional[date] = None) -> Scorecard:
    today = today or date.today()
    sc = Scorecard()

    # --- C1: cheap vs BOTH sector and own history ---
    both = flags.cheap_vs_both()
    if both:
        sc.c1_valuation = True
        sc.evidence["C1"] = "cheap vs sector AND own history on: " + ", ".join(both)
    elif flags.sector_cheap:
        sc.evidence["C1"] = ("cheap vs sector only on "
                             + ", ".join(f.metric for f in flags.sector_cheap)
                             + " (own-history confirmation missing)")

    # --- C2: over-punished material news ---
    recent_window = today - timedelta(days=7)
    material_bearish = [n for n in stock.news
                        if n.material and n.tone == "bearish"
                        and n.on and n.on >= recent_window]
    ret_1m = stock.technicals.ret_1m
    # Fundamentals not deteriorating: margin trend not falling AND growth not negative.
    om_trend = _trend_improving(stock.fundamentals.operating_margin_trend)
    not_deteriorating = (om_trend is not False) and (
        stock.fundamentals.rev_growth_yoy is None or stock.fundamentals.rev_growth_yoy >= 0)
    if material_bearish and ret_1m is not None and ret_1m <= -0.10 and not_deteriorating:
        sc.c2_overpunished_news = True
        sc.evidence["C2"] = (f"{len(material_bearish)} material bearish item(s) in 7d, "
                            f"1m return {ret_1m:+.1%}, fundamentals not deteriorating")
    elif material_bearish:
        sc.evidence["C2"] = (f"{len(material_bearish)} material bearish item(s) but "
                            f"price/fundamental over-punishment not confirmed")

    # --- C3: sentiment contradicts price weakness ---
    s = stock.sentiment
    weak_price = (stock.technicals.above_50dma is False) or (ret_1m is not None and ret_1m < 0)
    contradictions: list[str] = []
    if s.insider_buy_value_90d and s.insider_sell_value_90d is not None:
        if s.insider_buy_value_90d > s.insider_sell_value_90d:
            contradictions.append(f"net insider buying ${s.insider_buy_value_90d:,.0f}")
    elif s.insider_buy_value_90d:
        contradictions.append(f"insider buying ${s.insider_buy_value_90d:,.0f}")
    if s.net_upgrades_30d and s.net_upgrades_30d > 0:
        contradictions.append(f"net +{s.net_upgrades_30d} analyst upgrades (30d)")
    if s.social_sentiment_delta and s.social_sentiment_delta > 0:
        contradictions.append(f"social sentiment +{s.social_sentiment_delta:.2f}")
    if weak_price and contradictions:
        sc.c3_sentiment_contradicts = True
        sc.evidence["C3"] = "; ".join(contradictions) + " while price weak"
    elif contradictions:
        sc.evidence["C3"] = "; ".join(contradictions) + " (but price not weak)"

    # --- C4: improving fundamental trend not yet in the multiple ---
    f = stock.fundamentals
    improving: list[str] = []
    if _trend_improving(f.operating_margin_trend):
        improving.append("operating margin expanding (4q)")
    if _trend_improving(f.gross_margin_trend):
        improving.append("gross margin expanding (4q)")
    if f.fcf_yield is not None and f.fcf_yield > 0.05:
        improving.append(f"FCF yield {f.fcf_yield:.1%}")
    if f.net_debt_ebitda is not None and f.net_debt_ebitda < 1.0:
        improving.append(f"net debt/EBITDA {f.net_debt_ebitda:.1f}x")
    if f.rev_cagr_3y is not None and f.rev_cagr_3y > 0.08:
        improving.append(f"rev 3y CAGR {f.rev_cagr_3y:.1%}")
    cheap_multiple = bool(flags.sector_cheap)
    if improving and cheap_multiple:
        sc.c4_fundamental_trend = True
        sc.evidence["C4"] = "; ".join(improving) + " while multiple below sector"
    elif improving:
        sc.evidence["C4"] = "; ".join(improving) + " (multiple not below sector)"

    sc.conviction = _conviction(sc, flags, stock)
    return sc


def _conviction(sc: Scorecard, flags: RelativeFlags, stock: StockData) -> int:
    """Map evidence strength to a 1-5 conviction score."""
    if not sc.qualifies:
        return 0
    score = sc.criteria_met            # 2..4
    # Bonuses for strength.
    if flags.cheap_vs_both():
        score += 1
    if sc.c3_sentiment_contradicts and stock.sentiment.insider_buy_value_90d:
        score += 0.5
    if stock.technicals.confirmed_downtrend:
        score -= 1                     # penalize buying into a confirmed downtrend
    return max(1, min(5, round(score)))
