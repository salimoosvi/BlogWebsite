"""Command-line entry point.

    python -m scanner.cli --universe data/universe/sample_universe.csv --out report.md

Pipeline: load universe -> fetch (Yahoo) -> optional enrich (Finnhub/SEC) ->
screen filters -> sector medians -> relative flags -> fair value -> score ->
sector cap + rank -> markdown report.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import Config
from .fairvalue import estimate
from .relative import flag_stock, sector_medians
from .report import Candidate, build_report
from .scoring import score
from .screen import ScreenConfig, resolve_cap_tiers, screen_reasons
from .universe import load_csv


def _build_providers(cfg: Config):
    from .providers.yahoo import YahooProvider
    provider = YahooProvider(history_period=cfg.history_period)
    enrichers = []
    if cfg.finnhub_key:
        try:
            from .providers.finnhub import FinnhubEnricher
            enrichers.append(FinnhubEnricher(cfg.finnhub_key))
        except Exception as e:
            print(f"[warn] Finnhub disabled: {e}", file=sys.stderr)
    if cfg.edgar_user_agent:
        try:
            from .providers.sec_edgar import SecEdgarEnricher
            enrichers.append(SecEdgarEnricher(cfg.edgar_user_agent))
        except Exception as e:
            print(f"[warn] SEC EDGAR disabled: {e}", file=sys.stderr)
    return provider, enrichers


def run(universe_path: str, cfg: Config, out_path: str | None,
        sleep: float = 0.4, verbose: bool = True) -> str:
    entries = load_csv(universe_path)
    provider, enrichers = _build_providers(cfg)
    screen_cfg = ScreenConfig(min_market_cap_usd=cfg.min_market_cap_usd,
                              max_market_cap_usd=cfg.max_market_cap_usd,
                              us_only=cfg.us_only,
                              min_avg_volume=cfg.min_avg_volume,
                              cad_usd=cfg.cad_usd)

    fetched = []
    for i, e in enumerate(entries, 1):
        if verbose:
            print(f"[{i}/{len(entries)}] {e.ticker} ({e.exchange})", file=sys.stderr)
        try:
            sd = provider.fetch(e.ticker, e.exchange)
        except Exception as ex:
            print(f"  [error] fetch failed: {ex}", file=sys.stderr)
            continue
        if e.sector and not sd.sector:
            sd.sector = e.sector
        for en in enrichers:
            try:
                en.enrich(sd)
            except Exception as ex:
                sd.note(f"{en.name} enrich failed: {ex}")
        fetched.append(sd)
        time.sleep(sleep)   # be polite to the data source

    # Screen.
    passed = []
    for sd in fetched:
        reasons = screen_reasons(sd, screen_cfg)
        if reasons:
            if verbose:
                print(f"  [screened out] {sd.ticker}: {'; '.join(reasons)}", file=sys.stderr)
            continue
        passed.append(sd)

    # Sector medians computed across the screened set.
    medians = sector_medians(passed)

    candidates: list[Candidate] = []
    for sd in passed:
        flags = flag_stock(sd, medians.get(sd.sector or "Unknown", {}), n_sd=cfg.n_sd)
        fv = estimate(sd, medians.get(sd.sector or "Unknown", {}))
        sc = score(sd, flags)
        candidates.append(Candidate(stock=sd, flags=flags, fair_value=fv, scorecard=sc))

    report = build_report(candidates, top_n=cfg.top_n, max_per_sector=cfg.max_per_sector)
    if out_path:
        Path(out_path).write_text(report)
        if verbose:
            print(f"\nReport written to {out_path}", file=sys.stderr)
    return report


def debug_one(ticker: str, exchange: str, cfg: Config) -> str:
    """Fetch a single ticker and dump every field + data-quality note."""
    from .debug import dump_stock
    provider, enrichers = _build_providers(cfg)
    sd = provider.fetch(ticker, exchange)
    for en in enrichers:
        try:
            en.enrich(sd)
        except Exception as ex:
            sd.note(f"{en.name} enrich failed: {ex}")
    screen_cfg = ScreenConfig(min_market_cap_usd=cfg.min_market_cap_usd,
                              max_market_cap_usd=cfg.max_market_cap_usd,
                              us_only=cfg.us_only,
                              min_avg_volume=cfg.min_avg_volume, cad_usd=cfg.cad_usd)
    return dump_stock(sd, screen_cfg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Daily long-only mispricing scanner")
    p.add_argument("--universe", help="CSV with ticker,exchange[,sector]")
    p.add_argument("--ticker", help="debug a single ticker (dumps all fields, no report)")
    p.add_argument("--exchange", default="US", help="exchange for --ticker (US/NYSE/NASDAQ/TSX)")
    p.add_argument("--out", default=None, help="output markdown path (else stdout)")
    p.add_argument("--top", type=int, default=None, help="max names in report")
    p.add_argument("--max-per-sector", type=int, default=None)
    p.add_argument("--min-mcap-usd", type=float, default=None)
    p.add_argument("--max-mcap-usd", type=float, default=None)
    p.add_argument("--cap-tier", default=None,
                   help="comma list of {mega,large,mid,small}; sets the mcap band "
                        "(e.g. 'mega,large' -> $10B+). Explicit --min/--max-mcap-usd "
                        "take precedence over the preset.")
    p.add_argument("--us-only", action="store_true",
                   help="exclude TSX / non-USD listings")
    p.add_argument("--min-adv", type=float, default=None)
    p.add_argument("--sleep", type=float, default=0.4, help="seconds between fetches")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    cfg = Config.from_env()
    if args.cap_tier:
        try:
            lo, hi = resolve_cap_tiers(args.cap_tier.split(","))
        except KeyError as e:
            p.error(str(e))
        cfg.min_market_cap_usd = lo
        cfg.max_market_cap_usd = hi
    if args.us_only:
        cfg.us_only = True

    if args.ticker:
        print(debug_one(args.ticker.upper(), args.exchange.upper(), cfg))
        return 0
    if not args.universe:
        p.error("either --universe or --ticker is required")
    if args.top is not None:
        cfg.top_n = args.top
    if args.max_per_sector is not None:
        cfg.max_per_sector = args.max_per_sector
    if args.min_mcap_usd is not None:
        cfg.min_market_cap_usd = args.min_mcap_usd
    if args.max_mcap_usd is not None:
        cfg.max_market_cap_usd = args.max_mcap_usd
    if args.min_adv is not None:
        cfg.min_avg_volume = args.min_adv

    report = run(args.universe, cfg, args.out, sleep=args.sleep, verbose=not args.quiet)
    if not args.out:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
