"""Universe loading.

Primary path: a CSV you control (ticker,exchange[,sector]). This keeps the run
deterministic and offline-friendly. Optional helpers fetch index constituents
from Wikipedia when network allows, but the CSV is the source of truth.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UniverseEntry:
    ticker: str
    exchange: str        # NYSE / NASDAQ / TSX
    sector: str | None = None


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
                exchange=(r.get("exchange") or "").strip().upper(),
                sector=(r.get("sector") or "").strip() or None,
            ))
    return rows


def fetch_sp500() -> list[UniverseEntry]:
    """Best-effort S&P 500 constituents from Wikipedia. Requires network +
    pandas/lxml. Raises on failure so the caller can fall back to a CSV."""
    import pandas as pd  # local import; only needed for this optional path
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)
    df = tables[0]
    out: list[UniverseEntry] = []
    for _, row in df.iterrows():
        sym = str(row["Symbol"]).replace(".", "-").strip().upper()
        sector = str(row.get("GICS Sector", "")).strip() or None
        out.append(UniverseEntry(ticker=sym, exchange="NYSE", sector=sector))
    return out
