"""Provider interface. A provider turns a ticker into a populated StockData."""
from __future__ import annotations

from typing import Protocol

from ..models import StockData


class DataProvider(Protocol):
    name: str

    def fetch(self, ticker: str, exchange: str | None = None) -> StockData:
        """Return a StockData for the ticker. Must never raise on missing fields;
        record gaps via StockData.note() instead."""
        ...


class Enricher(Protocol):
    """Optional second-pass provider that adds fields to an existing StockData."""
    name: str

    def enrich(self, stock: StockData) -> None:
        ...
