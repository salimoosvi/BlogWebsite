"""Build a universe CSV from index constituents.

    python -m scanner.build_universe --indices sp500,nasdaq100,russell1000,tsx \
        --out data/universe/full.csv

Requires network + pandas/lxml. Each index is fetched independently: one
failing (page moved, schema changed, network blocked) does not abort the build —
it's reported and skipped. Output is deduped across indices.
"""
from __future__ import annotations

import argparse
import sys

from .universe import FETCHERS, UniverseEntry, merge, write_csv


def build(indices: list[str]) -> tuple[list[UniverseEntry], dict[str, str]]:
    collected: list[list[UniverseEntry]] = []
    status: dict[str, str] = {}
    for name in indices:
        fetch = FETCHERS.get(name)
        if fetch is None:
            status[name] = "unknown index"
            continue
        try:
            entries = fetch()
            if entries:
                collected.append(entries)
                status[name] = f"ok ({len(entries)} names)"
            else:
                status[name] = "no constituents parsed (page layout changed?)"
        except Exception as e:
            status[name] = f"failed: {e}"
    return merge(*collected), status


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build a universe CSV from index constituents")
    p.add_argument("--indices", default="sp500,nasdaq100,russell1000,tsx",
                   help="comma-separated: " + ", ".join(FETCHERS))
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    indices = [s.strip() for s in args.indices.split(",") if s.strip()]
    entries, status = build(indices)

    for name, st in status.items():
        print(f"  {name}: {st}", file=sys.stderr)
    if not entries:
        print("No constituents collected — nothing written. "
              "(In a restricted network, Wikipedia is unreachable; run where "
              "outbound HTTP is allowed.)", file=sys.stderr)
        return 1
    write_csv(entries, args.out)
    print(f"Wrote {len(entries)} unique names to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
