"""JSON serialization for scan candidates.

A snapshot is the persisted contract between the scanner and the dashboard:
``{preset, run_at, config, candidates: [...]}``. Dates serialize as ISO strings;
nested dataclasses round-trip via ``dataclasses.asdict``.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone
from typing import Any

from .report import Candidate


def _default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def candidate_to_dict(c: Candidate) -> dict:
    sc = c.scorecard
    return {
        "stock": dataclasses.asdict(c.stock),
        "flags": {
            "sector_cheap": [dataclasses.asdict(f) for f in c.flags.sector_cheap],
            "own_history_cheap": [dataclasses.asdict(f) for f in c.flags.own_history_cheap],
            "cheap_vs_both": c.flags.cheap_vs_both(),
            "notes": list(c.flags.notes),
        },
        "fair_value": dataclasses.asdict(c.fair_value),
        "scorecard": {
            "c1_valuation": sc.c1_valuation,
            "c2_overpunished_news": sc.c2_overpunished_news,
            "c3_sentiment_contradicts": sc.c3_sentiment_contradicts,
            "c4_fundamental_trend": sc.c4_fundamental_trend,
            "criteria_met": sc.criteria_met,
            "qualifies": sc.qualifies,
            "conviction": sc.conviction,
            "evidence": dict(sc.evidence),
        },
    }


def to_snapshot(preset_name: str, config_summary: dict, candidates: list[Candidate],
                run_at: datetime | None = None) -> dict:
    return {
        "preset": preset_name,
        "run_at": (run_at or datetime.now(timezone.utc)).isoformat(),
        "config": config_summary,
        "candidates": [candidate_to_dict(c) for c in candidates],
    }


def dumps(snapshot: dict, indent: int = 2) -> str:
    return json.dumps(snapshot, default=_default, indent=indent)


def write(snapshot: dict, path: str) -> None:
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(dumps(snapshot))
