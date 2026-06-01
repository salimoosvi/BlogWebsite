"""Streamlit dashboard.

Run with:
    streamlit run dashboard/app.py -- --runs runs/

Filters across preset snapshots produced by ``python -m scanner.run_presets``.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
import streamlit as st

from .loader import (CRITERIA_KEYS, filter_rows, latest_run_per_preset,
                     load_snapshots, to_rows)


# ---- arg parsing (Streamlit passes args after `--`) -----------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--runs", default="runs")
    # Streamlit injects its own argv; everything after `--` is ours.
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    args, _ = p.parse_known_args(argv)
    return args


ARGS = _parse_args()


# ---- page ----------------------------------------------------------------
st.set_page_config(page_title="Mispricing Scan", layout="wide")
st.title("Mispricing Scan Dashboard")

runs_dir = st.sidebar.text_input("Runs directory", ARGS.runs)
snaps = load_snapshots(runs_dir)

if not snaps:
    st.warning(
        f"No snapshots found under `{runs_dir}/`.\n\n"
        "Generate some first:\n```\npython -m scanner.run_presets\n```")
    st.stop()

all_rows = to_rows(snaps)

# --- sidebar filters ---
st.sidebar.markdown("---")
presets = sorted({r["preset"] for r in all_rows})
sel_presets = st.sidebar.multiselect("Presets", presets, default=presets)

latest_only = st.sidebar.checkbox("Latest snapshot per preset", value=True)
qualified_only = st.sidebar.checkbox("Qualified only (≥2/4)", value=True)

sectors = sorted({r["sector"] for r in all_rows if r.get("sector")})
sel_sectors = st.sidebar.multiselect("Sector", sectors, default=sectors)

exchanges = sorted({r["exchange"] for r in all_rows if r.get("exchange")})
sel_exch = st.sidebar.multiselect("Exchange", exchanges, default=exchanges)

conv_lo, conv_hi = st.sidebar.slider("Conviction", 0, 5, (1, 5))

sel_criteria = st.sidebar.multiselect(
    "Criteria fired (ANY of)", list(CRITERIA_KEYS.keys()))

min_upside = st.sidebar.number_input(
    "Min upside (fraction; e.g. 0.1 = +10%)", value=-1.0, step=0.05)

# --- apply filters ---
rows = all_rows
if latest_only:
    rows = latest_run_per_preset(rows)
rows = filter_rows(
    rows,
    presets=sel_presets,
    sectors=sel_sectors,
    exchanges=sel_exch,
    conviction_range=(conv_lo, conv_hi),
    criteria_any=sel_criteria,
    min_upside_pct=min_upside,
    qualified_only=qualified_only,
)

# --- top metrics ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("Snapshots loaded", len(snaps))
c2.metric("Presets selected", len(set(r["preset"] for r in rows)))
c3.metric("Names after filters", len(rows))
c4.metric("Avg conviction",
          f"{(sum(r['conviction'] for r in rows)/len(rows)):.2f}" if rows else "—")

if not rows:
    st.info("No candidates match the current filters.")
    st.stop()

# --- main table ---
df = pd.DataFrame(rows)
display_cols = ["ticker", "preset", "sector", "exchange", "currency",
                "price", "fv_mid", "upside_pct",
                "conviction", "criteria_met",
                "c1", "c2", "c3", "c4", "run_at"]
shown = df[display_cols].copy()
shown["upside_pct"] = shown["upside_pct"].apply(
    lambda x: f"{x*100:+.1f}%" if pd.notna(x) else "—")
shown = shown.sort_values(["conviction", "criteria_met"], ascending=[False, False])
st.dataframe(shown, use_container_width=True, hide_index=True)

# --- detail pane ---
st.markdown("---")
st.subheader("Detail")
labels = [f"{r['ticker']} / {r['preset']} ({r['run_at'][:10]})" for r in rows]
pick = st.selectbox("Inspect", labels)
if pick:
    idx = labels.index(pick)
    r = rows[idx]
    c = r["_candidate"]
    s, sc, fv = c["stock"], c["scorecard"], c["fair_value"]

    a, b = st.columns([2, 1])
    with a:
        st.markdown(f"### {s['ticker']} — {s.get('sector') or '—'} | "
                    f"conviction {sc['conviction']}/5 | {sc['criteria_met']}/4 criteria")
        st.markdown("**Evidence**")
        for key, attr in CRITERIA_KEYS.items():
            ev = sc.get("evidence", {}).get(key)
            if ev is None:
                continue
            mark = "✅" if sc.get(attr) else "▫️"
            st.markdown(f"- {mark} **{key}** — {ev}")

        st.markdown("**Fair-value math**")
        methods = (fv or {}).get("methods") or {}
        if methods:
            fv_df = pd.DataFrame(
                [{"method": k, "implied price": v} for k, v in methods.items()])
            st.dataframe(fv_df, use_container_width=True, hide_index=True)
            if fv.get("mid") is not None:
                st.caption(
                    f"Range {fv['low']:.2f} – {fv['high']:.2f}, mid {fv['mid']:.2f}; "
                    f"upside "
                    f"{(fv['upside_pct']*100):+.1f}%" if fv.get("upside_pct") is not None
                    else "upside —")
        else:
            st.caption("No multiples-based fair value (insufficient data).")
        for n in (fv or {}).get("notes", []):
            st.caption(f"note: {n}")

        if s.get("news"):
            st.markdown("**Recent news**")
            news_df = pd.DataFrame(s["news"])[["on", "title", "source", "url"]]
            st.dataframe(news_df, use_container_width=True, hide_index=True)

    with b:
        st.markdown("**Snapshot**")
        st.write({"preset": r["preset"], "run_at": r["run_at"],
                  "config": r["_snap"].get("config", {})})
        if s.get("data_quality"):
            st.markdown("**Data-quality notes**")
            for n in s["data_quality"]:
                st.markdown(f"- {n}")

    st.markdown("---")
    st.markdown("**Same ticker across presets / runs**")
    cross = pd.DataFrame([{
        "preset": x["preset"], "run_at": x["run_at"],
        "qualifies": x["qualifies"], "conviction": x["conviction"],
        "criteria_met": x["criteria_met"], "upside_pct": x["upside_pct"]}
        for x in all_rows if x["ticker"] == s["ticker"]])
    st.dataframe(cross.sort_values("run_at", ascending=False),
                 use_container_width=True, hide_index=True)
