"""Run every preset defined in ``presets/presets.json`` and write a timestamped
snapshot JSON per preset under ``runs/<preset>/<UTC-timestamp>.json``.

A preset is a dict like:
    {
      "name": "us_mega_large_standard",
      "description": "...",
      "universe": "data/universe/us.csv",
      "cap_tier": ["mega", "large"],
      "us_only": true,
      "n_sd": 1.0,
      "max_per_sector": 3,
      "top_n": 10,
      "min_avg_volume": 500000
    }

The dashboard loads everything under the runs directory and lets you filter.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cli import scan_universe
from .config import Config
from .screen import resolve_cap_tiers
from .serialize import to_snapshot, write


def load_presets(path: str | Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def apply_preset(preset: dict, base: Config) -> Config:
    cfg = Config(**dataclasses.asdict(base))   # copy
    if "cap_tier" in preset:
        lo, hi = resolve_cap_tiers(preset["cap_tier"])
        cfg.min_market_cap_usd = lo
        cfg.max_market_cap_usd = hi
    overrides = ("min_market_cap_usd", "max_market_cap_usd", "us_only",
                 "n_sd", "max_per_sector", "top_n", "min_avg_volume")
    for k in overrides:
        if k in preset:
            setattr(cfg, k, preset[k])
    return cfg


def run_all(presets_path: str, out_dir: str, sleep: float = 0.4,
            verbose: bool = True) -> list[Path]:
    presets = load_presets(presets_path)
    base = Config.from_env()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    written: list[Path] = []
    for preset in presets:
        name = preset["name"]
        universe = preset["universe"]
        if verbose:
            print(f"[preset {name}] universe={universe}", file=sys.stderr)
        cfg = apply_preset(preset, base)
        try:
            cands = scan_universe(universe, cfg, sleep=sleep, verbose=verbose)
        except Exception as e:
            print(f"  [error] preset {name} failed: {e}", file=sys.stderr)
            continue
        snap = to_snapshot(name, {**preset,
                                  "_resolved": {"min_mcap": cfg.min_market_cap_usd,
                                                "max_mcap": cfg.max_market_cap_usd}},
                           cands)
        path = Path(out_dir) / name / f"{stamp}.json"
        write(snap, str(path))
        written.append(path)
        if verbose:
            qual = sum(1 for c in cands if c.scorecard.qualifies)
            print(f"  -> {path}  ({len(cands)} evaluated, {qual} qualified)",
                  file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run all presets and write snapshots")
    p.add_argument("--presets", default="presets/presets.json")
    p.add_argument("--out-dir", default="runs")
    p.add_argument("--sleep", type=float, default=0.4)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    paths = run_all(args.presets, args.out_dir, sleep=args.sleep, verbose=not args.quiet)
    if not paths:
        print("No snapshots written.", file=sys.stderr)
        return 1
    print(f"\nWrote {len(paths)} snapshot(s) under {args.out_dir}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
