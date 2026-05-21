"""Optional SEC EDGAR enricher for authoritative Form-4 insider transactions.

US issuers only. Requires a declared User-Agent per SEC fair-access policy
(set EDGAR_USER_AGENT to "Name email"). No API key. Degrades silently on
failure. This supplements / cross-checks Yahoo's insider feed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from ..models import InsiderTxn, StockData

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


class SecEdgarEnricher:
    name = "sec_edgar"

    def __init__(self, user_agent: str, timeout: int = 20):
        if requests is None:
            raise RuntimeError("requests not installed (pip install requests)")
        if not user_agent or "@" not in user_agent:
            raise ValueError('SEC requires a User-Agent like "Name email@x.com"')
        self.ua = user_agent
        self.timeout = timeout
        self._cik_cache: dict[str, int] = {}

    def _headers(self) -> dict:
        return {"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"}

    def _cik_for(self, ticker: str) -> Optional[int]:
        if not self._cik_cache:
            try:
                r = requests.get(TICKER_MAP, headers=self._headers(), timeout=self.timeout)
                if r.status_code == 200:
                    for row in r.json().values():
                        self._cik_cache[row["ticker"].upper()] = int(row["cik_str"])
            except Exception:
                return None
        return self._cik_cache.get(ticker.upper())

    def enrich(self, stock: StockData) -> None:
        # Skip non-US listings (EDGAR is US-only).
        if stock.exchange == "TSX":
            return
        cik = self._cik_for(stock.ticker)
        if cik is None:
            stock.note("SEC EDGAR: CIK not found (non-US or unmapped)")
            return
        try:
            r = requests.get(SUBMISSIONS.format(cik=cik), headers=self._headers(),
                             timeout=self.timeout)
            if r.status_code != 200:
                stock.note(f"SEC EDGAR: submissions HTTP {r.status_code}")
                return
            recent = r.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            cutoff = date.today() - timedelta(days=90)
            count = 0
            for form, fdate in zip(forms, dates):
                if form != "4":
                    continue
                try:
                    on = datetime.fromisoformat(fdate).date()
                except Exception:
                    continue
                if on < cutoff:
                    continue
                count += 1
            if count:
                stock.note(f"SEC EDGAR: {count} Form-4 filings in 90d "
                           "(cross-check vs Yahoo insider feed; parse XML for buy/sell split)")
        except Exception as e:
            stock.note(f"SEC EDGAR error: {e}")
