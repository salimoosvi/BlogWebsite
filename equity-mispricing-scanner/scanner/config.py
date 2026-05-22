"""Runtime configuration. Secrets come from environment variables only."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    finnhub_key: Optional[str] = None
    edgar_user_agent: Optional[str] = None
    cad_usd: float = 0.73
    min_market_cap_usd: float = 500_000_000
    max_market_cap_usd: Optional[float] = None
    us_only: bool = False
    min_avg_volume: float = 500_000
    max_per_sector: int = 3
    top_n: int = 10
    n_sd: float = 1.0
    history_period: str = "2y"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            finnhub_key=os.getenv("FINNHUB_API_KEY") or None,
            edgar_user_agent=os.getenv("EDGAR_USER_AGENT") or None,
            cad_usd=float(os.getenv("CAD_USD", "0.73")),
            min_market_cap_usd=float(os.getenv("MIN_MCAP_USD", "500000000")),
            min_avg_volume=float(os.getenv("MIN_ADV", "500000")),
            max_per_sector=int(os.getenv("MAX_PER_SECTOR", "3")),
            top_n=int(os.getenv("TOP_N", "10")),
        )
