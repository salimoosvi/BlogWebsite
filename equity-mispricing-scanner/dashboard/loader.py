"""Pure (no-streamlit) snapshot loading & row-flattening.

Kept separate so it can be unit-tested without a browser. The dashboard imports
``load_snapshots`` and ``to_rows`` and only adds the Streamlit UI on top.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CRITERIA_KEYS = {
    "C1": "c1_valuation",
    "C2": "c2_overpunished_news",
    "C3": "c3_sentiment_contradicts",
    "C4": "c4_fundamental_trend",
}


def load_snapshots(runs_dir: str | Path) -> list[dict]:
    """Read every *.json under runs_dir (recursively). Each snapshot dict gets
    a synthetic ``_path`` field for traceability."""
    snaps: list[dict] = []
    for p in sorted(Path(runs_dir).glob("**/*.json")):
        try:
            with open(p) as f:
                snap = json.load(f)
        except Exception:
            continue
        snap["_path"] = str(p)
        snaps.append(snap)
    return snaps


def to_rows(snaps: list[dict]) -> list[dict]:
    """Flatten snapshots into one row per (snapshot, candidate). Keeps the full
    candidate dict in ``_candidate`` for the detail pane."""
    rows: list[dict] = []
    for snap in snaps:
        preset = snap.get("preset", "?")
        run_at = snap.get("run_at", "")
        for c in snap.get("candidates", []):
            s = c.get("stock", {})
            sc = c.get("scorecard", {})
            fv = c.get("fair_value", {})
            tech = s.get("technicals", {}) or {}
            rows.append({
                "preset": preset,
                "run_at": run_at,
                "ticker": s.get("ticker"),
                "exchange": s.get("exchange"),
                "sector": s.get("sector"),
                "currency": s.get("currency"),
                "price": tech.get("price"),
                "fv_mid": fv.get("mid"),
                "upside_pct": fv.get("upside_pct"),
                "conviction": sc.get("conviction", 0),
                "criteria_met": sc.get("criteria_met", 0),
                "qualifies": bool(sc.get("qualifies", False)),
                "c1": bool(sc.get("c1_valuation", False)),
                "c2": bool(sc.get("c2_overpunished_news", False)),
                "c3": bool(sc.get("c3_sentiment_contradicts", False)),
                "c4": bool(sc.get("c4_fundamental_trend", False)),
                "_candidate": c,
                "_snap": snap,
            })
    return rows


def latest_run_per_preset(rows: list[dict]) -> list[dict]:
    """Keep only rows from the latest run_at per preset."""
    latest: dict[str, str] = {}
    for r in rows:
        if r["run_at"] > latest.get(r["preset"], ""):
            latest[r["preset"]] = r["run_at"]
    return [r for r in rows if r["run_at"] == latest.get(r["preset"])]


def filter_rows(rows: list[dict], *, presets: list[str] | None = None,
                sectors: list[str] | None = None, exchanges: list[str] | None = None,
                conviction_range: tuple[int, int] = (0, 5),
                criteria_any: list[str] | None = None,
                min_upside_pct: float = -1.0,
                qualified_only: bool = True) -> list[dict]:
    """Apply the dashboard filter chain. Pure function for testability."""
    out = rows
    if presets:
        out = [r for r in out if r["preset"] in presets]
    if sectors:
        out = [r for r in out if (r.get("sector") in sectors)]
    if exchanges:
        out = [r for r in out if (r.get("exchange") in exchanges)]
    lo, hi = conviction_range
    out = [r for r in out if lo <= (r["conviction"] or 0) <= hi]
    if qualified_only:
        out = [r for r in out if r["qualifies"]]
    if criteria_any:
        keys = [CRITERIA_KEYS[c] for c in criteria_any if c in CRITERIA_KEYS]
        out = [r for r in out
               if any(r["_candidate"]["scorecard"].get(k) for k in keys)]
    out = [r for r in out
           if (r.get("upside_pct") is None and min_upside_pct <= -1.0)
           or (r.get("upside_pct") is not None and r["upside_pct"] >= min_upside_pct)]
    return out
