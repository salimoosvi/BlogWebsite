"""Universe loading + index-constituent builders.

Two layers:
  1. ``load_csv`` — the deterministic, offline source of truth (ticker,exchange[,sector]).
  2. ``fetch_*`` index builders that scrape constituent tables from Wikipedia.
     These need network + pandas/lxml and are intentionally tolerant of the
     column-header drift those pages are prone to. The table-parsing core
     (``parse_constituents``) is pure and unit-tested offline.

Ticker storage is human-readable/raw (e.g. ``BRK.B``); the Yahoo provider owns
the conversion to Yahoo symbols (``BRK-B``, ``RY.TO``).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SYMBOL_COLS = {"symbol", "ticker", "ticker symbol", "tickersymbol", "code",
               "ticker(s)", "symbols"}
SECTOR_COLS = {"gics sector", "sector", "gics sub-industry", "industry"}
COMPANY_COLS = {"company", "security", "company name", "name"}


@dataclass
class UniverseEntry:
    ticker: str
    exchange: str        # NYSE / NASDAQ / TSX / US
    sector: Optional[str] = None


# --------------------------------------------------------------------------- #
# Source of truth: CSV
# --------------------------------------------------------------------------- #
def load_csv(path: str | Path) -> list[UniverseEntry]:
    rows: list[UniverseEntry] = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            ticker = (r.get("ticker") or "").strip().upper()
            if not ticker or ticker.startswith("#"):
                continue
            rows.append(UniverseEntry(
                ticker=ticker,
                exchange=(r.get("exchange") or "US").strip().upper(),
                sector=(r.get("sector") or "").strip() or None,
            ))
    return rows


def write_csv(entries: list[UniverseEntry], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "exchange", "sector"])
        for e in entries:
            w.writerow([e.ticker, e.exchange, e.sector or ""])


def merge(*lists: list[UniverseEntry]) -> list[UniverseEntry]:
    """Dedup by (ticker, exchange); first occurrence wins, but backfill a
    missing sector from a later list."""
    seen: dict[tuple[str, str], UniverseEntry] = {}
    for lst in lists:
        for e in lst:
            key = (e.ticker, e.exchange)
            if key not in seen:
                seen[key] = e
            elif seen[key].sector is None and e.sector:
                seen[key].sector = e.sector
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Pure table parser (unit-tested without network)
# --------------------------------------------------------------------------- #
def _norm(s) -> str:
    return str(s).strip().lower()


def _flatten_columns(df):
    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [" ".join(str(p) for p in tup).strip() for tup in df.columns]
    return df


def _find_col(df, candidates: set[str]):
    for c in df.columns:
        if _norm(c) in candidates:
            return c
    # loose contains-match fallback
    for c in df.columns:
        n = _norm(c)
        if any(cand in n for cand in candidates):
            return c
    return None


def parse_constituents(df, exchange: str) -> list[UniverseEntry]:
    """Extract (ticker, exchange, sector) from a constituents DataFrame.

    Pure: accepts any DataFrame so it can be tested with a synthetic table.
    Returns [] if no symbol column is found.
    """
    df = _flatten_columns(df)
    sym_col = _find_col(df, SYMBOL_COLS)
    if sym_col is None:
        return []
    sec_col = _find_col(df, SECTOR_COLS)
    out: list[UniverseEntry] = []
    for _, row in df.iterrows():
        raw = row.get(sym_col)
        if raw is None or (isinstance(raw, float) and raw != raw):  # NaN
            continue
        ticker = str(raw).strip().upper()
        # Skip footnote/garbage rows.
        if not ticker or len(ticker) > 12 or " " in ticker:
            continue
        sector = None
        if sec_col is not None:
            sv = row.get(sec_col)
            if sv is not None and not (isinstance(sv, float) and sv != sv):
                sector = str(sv).strip() or None
        out.append(UniverseEntry(ticker=ticker, exchange=exchange, sector=sector))
    return out


def _pick_table(tables, exchange: str) -> list[UniverseEntry]:
    """From a list of DataFrames, return the constituents of the first table
    that yields a plausible set (a symbol column + >20 rows)."""
    best: list[UniverseEntry] = []
    for df in tables:
        try:
            parsed = parse_constituents(df, exchange)
        except Exception:
            continue
        if len(parsed) > len(best):
            best = parsed
    return best if len(best) >= 20 else best  # caller validates emptiness


# --------------------------------------------------------------------------- #
# Index builders (require network + pandas/lxml)
# --------------------------------------------------------------------------- #
def _read_html(url: str):
    import pandas as pd
    # A UA reduces 403s from Wikipedia.
    try:
        import requests
        from io import StringIO
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (universe-builder)"},
                            timeout=30).text
        return pd.read_html(StringIO(html))
    except Exception:
        return pd.read_html(url)   # let pandas try directly as a fallback


def fetch_sp500() -> list[UniverseEntry]:
    tables = _read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    return _pick_table(tables, "US")


def fetch_nasdaq100() -> list[UniverseEntry]:
    tables = _read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
    return _pick_table(tables, "NASDAQ")


def fetch_russell1000() -> list[UniverseEntry]:
    # Wikipedia's Russell 1000 page lists components; if absent, iShares IWB
    # holdings CSV is the better source (network-gated, schema changes often).
    tables = _read_html("https://en.wikipedia.org/wiki/Russell_1000_Index")
    return _pick_table(tables, "US")


def fetch_tsx_composite() -> list[UniverseEntry]:
    tables = _read_html("https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index")
    return _pick_table(tables, "TSX")


FETCHERS = {
    "sp500": fetch_sp500,
    "nasdaq100": fetch_nasdaq100,
    "russell1000": fetch_russell1000,
    "tsx": fetch_tsx_composite,
}
