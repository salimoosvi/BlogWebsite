"""Single-ticker diagnostic dump.

The first thing to run after the offline tests when you finally have network:
fetch one name and see exactly which fields populated, which came back empty,
and every data-quality note — so live Yahoo schema mismatches surface
immediately instead of silently producing an empty scan.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from .models import StockData
from .screen import ScreenConfig, screen_reasons


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, list):
        return f"[{len(v)} item(s)]"
    return str(v)


def _dump_dataclass(obj: Any, indent: str = "  ") -> list[str]:
    out = []
    for f in fields(obj):
        val = getattr(obj, f.name)
        if is_dataclass(val):
            out.append(f"{indent}{f.name}:")
            out.extend(_dump_dataclass(val, indent + "  "))
        else:
            out.append(f"{indent}{f.name:24} {_fmt(val)}")
    return out


def dump_stock(sd: StockData, screen_cfg: ScreenConfig | None = None) -> str:
    lines = [f"=== {sd.ticker} ({sd.exchange}) ==="]
    populated = 0
    total = 0

    def count(obj):
        nonlocal populated, total
        for f in fields(obj):
            val = getattr(obj, f.name)
            if is_dataclass(val):
                count(val)
            elif f.name in ("data_quality",):
                continue
            else:
                total += 1
                if val is not None and val != [] and val != "":
                    populated += 1

    count(sd)
    lines += _dump_dataclass(sd)
    lines.append("")
    lines.append(f"field coverage: {populated}/{total} populated")

    if sd.news:
        lines.append(f"news: {len(sd.news)} item(s)")
        for n in sd.news[:5]:
            lines.append(f"  - {n.on} {n.title[:70]} ({n.source})")
    if sd.sentiment.insider_txns_90d:
        lines.append(f"insider txns (90d): {len(sd.sentiment.insider_txns_90d)}")
    if sd.sentiment.analyst_changes_30d:
        lines.append(f"analyst changes (30d): {len(sd.sentiment.analyst_changes_30d)}")
    if sd.history.pe:
        lines.append(f"own-history P/E points: {len(sd.history.pe)} -> {[round(x,1) for x in sd.history.pe]}")

    if screen_cfg is not None:
        reasons = screen_reasons(sd, screen_cfg)
        lines.append("")
        lines.append("screen: " + ("PASS" if not reasons else "EXCLUDED — " + "; ".join(reasons)))

    if sd.data_quality:
        lines.append("")
        lines.append("data-quality notes:")
        for n in sd.data_quality:
            lines.append(f"  - {n}")
    return "\n".join(lines)
