# Equity Mispricing Scanner

A daily, **long-only** mispricing screen for US & Canadian equities. It pulls
fundamentals, technicals, sentiment and news per name, computes relative-value
flags vs sector and the stock's own history, derives a multiples-based fair
value, and applies a `>=2-of-4` qualification gate before ranking by conviction.

**This is research tooling, not investment advice.** It never fabricates a
number: any field missing or stale at the source is shown as `n/a` and noted in
the per-name "Data quality" section. The narrative parts of each thesis (bear
case, variant view, catalyst, kill switch) are emitted as evidence + prompts for
a human analyst — the tool does not auto-write a thesis it can't support.

## Data sources (free / freemium — no Bloomberg)

| Signal | Primary source | Optional enricher | Key? |
|---|---|---|---|
| Price, fundamentals, ratios, margins, growth | Yahoo (`yfinance`) | — | No |
| Technicals (RSI, 50/200 DMA, 52w range) | computed from Yahoo price history | — | No |
| Insider transactions (90d) | Yahoo | SEC EDGAR Form 4 (US only) | No / UA |
| Analyst rating changes (30d) | Yahoo upgrades/downgrades | Finnhub recommendation trend | No / key |
| News (7d) | Yahoo | Finnhub company-news | No / key |
| Short interest + change | Yahoo | — | No |
| Social sentiment delta | — | Finnhub social-sentiment (plan-gated) | key |
| Options skew | Yahoo option chain (approx; optional) | — | No |

Enrichers activate only if their credential is present and **degrade silently**
otherwise. Get a free Finnhub key at <https://finnhub.io/register>.

### Known free-data limitations (stated, not hidden)
- **Own 5yr ratio history** is coarse: annual EPS × year-end price for P/E only.
  EV/EBITDA, P/S, P/B history aren't reliably free, so the "own-history" leg of
  criterion C1 is skipped for those (noted per name).
- **ROIC** isn't reliably available free; reported as a gap (ROE shown as a weak
  proxy only, never substituted).
- **News tone/materiality** is **not** auto-classified — items are listed with
  date/source for the analyst (or a future NLP enricher) to tag.
- For higher-quality point-in-time data, swap in a paid provider behind the
  `DataProvider` protocol (`scanner/providers/base.py`).

## Network requirement

The scanner must run somewhere it can reach Yahoo's hosts
(`query1/query2.finance.yahoo.com`) and, if used, `finnhub.io` / `data.sec.gov`.
It will **not** work inside a restricted/allow-listed sandbox (e.g. Claude Code
on the web), where those hosts return `403 Host not in allowlist`. Run it
locally or in an environment whose egress policy permits market-data hosts.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# optional enrichers
export FINNHUB_API_KEY="..."                 # better news + analyst trend + social
export EDGAR_USER_AGENT="Your Name you@email.com"  # SEC Form-4 cross-check (US)
export CAD_USD="0.73"                          # CAD->USD for the market-cap floor
```

## Run

```bash
python -m scanner.cli --universe data/universe/sample_universe.csv --out report.md
```

Useful flags: `--top N`, `--max-per-sector N`, `--min-mcap-usd`, `--max-mcap-usd`,
`--min-adv`, `--sleep` (seconds between fetches; be polite to the source), `--quiet`.

### Market-cap tiers & US-only
`--cap-tier` sets the market-cap band from conventional USD tiers; `--us-only`
drops TSX / non-USD listings at the screen (belt-and-suspenders on top of a
US-only universe).

| Tier | Band (USD) |
|---|---|
| `mega` | $200B+ |
| `large` | $10B – $200B |
| `mid` | $2B – $10B |
| `small` | $300M – $2B |

Pass a comma list; the band spans the union (an open-ended tier removes the
ceiling). Explicit `--min-mcap-usd` / `--max-mcap-usd` take precedence.

```bash
# Mega + large cap, US market only (floor $10B, no ceiling):
python -m scanner.build_universe --indices sp500,nasdaq100,russell1000 \
    --out data/universe/us.csv          # note: no 'tsx'
python -m scanner.cli --universe data/universe/us.csv \
    --cap-tier mega,large --us-only --out report.md
```

### First live run / debugging schema drift
Yahoo's response shapes change over time. Before a full scan, dump one ticker
to see exactly what populated and what didn't:

```bash
python -m scanner.cli --ticker AAPL --exchange NASDAQ
python -m scanner.cli --ticker RY   --exchange TSX
```

This prints every field, the field-coverage ratio, counts of news/insider/
analyst rows, the own-history P/E points, the screen verdict, and all
data-quality notes — so a broken field-mapping surfaces immediately instead of
silently yielding an empty scan. If coverage is near-zero with `info unavailable`
notes, your network can't reach Yahoo (see above).

### Building the universe
`data/universe/sample_universe.csv` is a tiny illustrative slice. Build the real
mandate universe (S&P 500 + Nasdaq 100 + Russell 1000 + TSX Composite) from
index constituents:

```bash
python -m scanner.build_universe \
    --indices sp500,nasdaq100,russell1000,tsx \
    --out data/universe/full.csv
```

Each index is fetched independently — one source failing (page moved, schema
changed, network blocked) is reported and skipped, not fatal — and the result
is deduped across indices. Tickers are stored raw/human-readable; the Yahoo
provider converts them to Yahoo symbols (`BRK.B`→`BRK-B`, `RY`→`RY.TO`,
`CCL.B`→`CCL-B.TO`) at fetch time. Requires network + `pandas`/`lxml`. The CSV
remains the source of truth — edit it freely.

## How qualification works

A name qualifies only if **at least 2** of these hold (see `scanner/scoring.py`):
1. **C1** — a multiple is >1 SD cheap vs **both** sector median and own history.
2. **C2** — recent material bearish news looks over-punished vs fundamental impact.
3. **C3** — a sentiment indicator (insider buys, net analyst upgrades, social)
   contradicts current price weakness.
4. **C4** — an improving fundamental trend (margins, FCF, debt, growth) is not
   yet reflected in the multiple.

Output is ranked by **conviction (1-5)**, not upside, capped at 3 names per GICS
sector, ending with a "What I'm watching this week" macro/sector watchlist
scaffold.

## Tests

Offline logic (technicals, relative SD flags, screen, fair value, scoring) has
unit coverage that needs no network:

```bash
PYTHONPATH=. python tests/test_logic.py        # or: python -m pytest tests/ -q
```

## Layout

```
scanner/
  cli.py            orchestration (+ --ticker single-name debug mode)
  build_universe.py build a universe CSV from index constituents
  debug.py          single-ticker field-coverage dump
  config.py         env-driven config
  models.py         normalized data models
  universe.py       CSV loading + Wikipedia constituent parsers
  screen.py         exclusion filters (mcap, ADV, ADR, earnings blackout)
  technicals.py     RSI, DMA, downtrend
  relative.py       sector + own-history SD cheapness flags
  fairvalue.py      multiples-based fair value range
  scoring.py        2-of-4 gate + conviction
  report.py         markdown report + thesis scaffolds
  providers/        yahoo (backbone), finnhub, sec_edgar
tests/              offline logic tests
```
