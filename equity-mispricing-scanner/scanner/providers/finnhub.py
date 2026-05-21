"""Optional Finnhub enricher. Activates only when a free API key is supplied.

Adds: better last-7d company news, analyst recommendation-trend deltas, and
social sentiment delta. Degrades silently (records a note) on any failure so the
scan never depends on it. Free key: https://finnhub.io/register
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from ..models import NewsItem, StockData

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

BASE = "https://finnhub.io/api/v1"


class FinnhubEnricher:
    name = "finnhub"

    def __init__(self, api_key: str, timeout: int = 15):
        if requests is None:
            raise RuntimeError("requests not installed (pip install requests)")
        if not api_key:
            raise ValueError("Finnhub API key required")
        self.key = api_key
        self.timeout = timeout

    def _get(self, path: str, **params) -> Optional[object]:
        params["token"] = self.key
        try:
            r = requests.get(f"{BASE}/{path}", params=params, timeout=self.timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def enrich(self, stock: StockData) -> None:
        sym = stock.ticker
        self._news(stock, sym)
        self._reco(stock, sym)
        self._social(stock, sym)

    def _news(self, stock: StockData, sym: str) -> None:
        frm = (date.today() - timedelta(days=7)).isoformat()
        to = date.today().isoformat()
        data = self._get("company-news", symbol=sym, **{"from": frm, "to": to})
        if not isinstance(data, list):
            stock.note("finnhub: news unavailable")
            return
        existing = {n.title for n in stock.news}
        for it in data:
            title = it.get("headline", "")
            if title in existing:
                continue
            on = None
            if it.get("datetime"):
                on = datetime.utcfromtimestamp(it["datetime"]).date()
            stock.news.append(NewsItem(on=on, title=title,
                                       source=it.get("source", "finnhub"),
                                       url=it.get("url", "")))

    def _reco(self, stock: StockData, sym: str) -> None:
        data = self._get("stock/recommendation", symbol=sym)
        if not isinstance(data, list) or len(data) < 2:
            return
        latest, prior = data[0], data[1]
        def bull(d):
            return d.get("strongBuy", 0) + d.get("buy", 0)
        def bear(d):
            return d.get("sell", 0) + d.get("strongSell", 0)
        delta = (bull(latest) - bear(latest)) - (bull(prior) - bear(prior))
        if stock.sentiment.net_upgrades_30d is None:
            stock.sentiment.net_upgrades_30d = delta
        else:
            stock.sentiment.net_upgrades_30d += delta

    def _social(self, stock: StockData, sym: str) -> None:
        # social-sentiment is premium on some plans; tolerate absence.
        data = self._get("stock/social-sentiment", symbol=sym)
        if not isinstance(data, dict):
            return
        reddit = data.get("reddit") or []
        if reddit:
            scores = [x.get("score", 0) for x in reddit if isinstance(x, dict)]
            if scores:
                stock.sentiment.social_sentiment_delta = (
                    scores[0] - scores[-1]) / (abs(scores[-1]) + 1e-9)
