# Data fetching

How to fetch and process the historical OHLCV dataset that every later Phase 2
session (sweep, walk-forward, Monte Carlo, stress tests) depends on, and how to
verify its integrity.

> **TL;DR:** `uv run algo-fetch` pulls raw klines from Binance into
> `bot_data/raw/`, `uv run algo-process` standardises them into
> `bot_data/processed/`, and `uv run pytest tests/test_data_integrity.py
> -m integration` proves the result is clean.

---

## The Phase 2 dataset

Six files, two symbols × three timeframes, on **Binance USDT-M perpetual
futures**, from **2019-09-08** to present:

```
bot_data/processed/binance_BTCUSDT_15m.csv
bot_data/processed/binance_BTCUSDT_1h.csv
bot_data/processed/binance_BTCUSDT_4h.csv
bot_data/processed/binance_ETHUSDT_15m.csv
bot_data/processed/binance_ETHUSDT_1h.csv
bot_data/processed/binance_ETHUSDT_4h.csv
```

### Decisions behind this dataset (Phase 2 Session 2)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Exchange / market | Binance Futures USDT-M | Matches ADR-005 and `live/live_binance.py`. |
| 2 | Fetch strategy | Native per timeframe | Zero new code; true native volume per TF; Binance 15m/1h/4h klines come from the same trade stream so there is no meaningful source divergence within one exchange. |
| 3 | Start date | 2019-09-08 | Binance Futures launch. ~6.5 years → 5+ walk-forward folds (train 12m / test 3m / step 3m). |
| 4 | Volume column | Base volume only | `bghtrend` does not use volume. Quote volume deferred until a VWAP-style strategy needs it (would require a fetcher change). |
| 5 | Storage format | CSV | Existing convention, text-diffable, debuggable. Parquet deferred until I/O hurts. |
| 6 | Gap handling | Fill small, abort large, **report all** | `process_data` forward-fills gaps ≤ 0.5% (synthetic bar, `Volume=0`) and aborts above that. The integrity validator logs every gap > 3×TF as a WARNING, so synthetic regions are always visible. |
| 7 | Sanity checks | `algo_bot/data_integrity.py` + gated pytest | Single source of integrity truth, reusable by later sessions before a sweep. Does not touch `fetch_data`/`process_data`. |
| 8 | Resume | Resume by default | `algo-fetch` continues from the last timestamp in an existing raw file. Force a refetch by deleting the raw file (see below). |

> **Note on ETH:** the ETH-USDT perpetual was listed later than BTC (late
> November 2019). The ETH files therefore start a couple of months after the BTC
> ones even with the same `--start`. This is expected, not a bug.

---

## Prerequisites

Run from the **WSL terminal** (not from Cowork — the sandbox cannot reach the
UNC mount or the network). Standard Beta 0 setup uses uv 0.11.28 and the
locked Python 3.12.13 environment; no activation is needed:

```bash
cd ~/quant_projects/algo_bot
uv --version       # uv 0.11.28
make sync          # uv sync --locked; gives algo-fetch / algo-process
```

The CLI entry points (`pyproject.toml [project.scripts]`, ADR-010):

- `uv run algo-fetch <SYMBOL> <TF> --start <DATE>` → writes `bot_data/raw/<BASE>_<QUOTE>-<TF>.csv`
- `uv run algo-process [RAW_PATH]` → writes `bot_data/processed/binance_<SYMBOL>_<TF>.csv`

Both accept `--log-level {DEBUG,INFO,WARNING,ERROR}` (default `INFO`).

---

## Step 1 — Fetch raw klines

`--market` defaults to `future`, so each call below targets USDT-M perpetuals.

```bash
uv run algo-fetch BTCUSDT 15m --start 2019-09-08
uv run algo-fetch BTCUSDT 1h  --start 2019-09-08
uv run algo-fetch BTCUSDT 4h  --start 2019-09-08
uv run algo-fetch ETHUSDT 15m --start 2019-09-08
uv run algo-fetch ETHUSDT 1h  --start 2019-09-08
uv run algo-fetch ETHUSDT 4h  --start 2019-09-08
```

The fetcher batches 1000 candles per request with CCXT's built-in rate limiter
(`enableRateLimit=True`), retries network errors with backoff, and flushes to
disk every 12 batches. The 15m pull is the longest (~228k bars per symbol,
roughly 150 requests); the whole set takes a few minutes.

Raw files land as (legacy naming):

```
bot_data/raw/BTC_USDT-15m.csv
bot_data/raw/BTC_USDT-1h.csv
bot_data/raw/BTC_USDT-4h.csv
bot_data/raw/ETH_USDT-15m.csv
bot_data/raw/ETH_USDT-1h.csv
bot_data/raw/ETH_USDT-4h.csv
```

Columns: `ts, datetime, Open, High, Low, Close, Volume` (base volume).

---

## Step 2 — Process into the standard layout

Batch mode processes every CSV in `bot_data/raw/`:

```bash
uv run algo-process
```

Or one file at a time (useful when iterating on a single TF):

```bash
uv run algo-process bot_data/raw/BTC_USDT-15m.csv
uv run algo-process bot_data/raw/BTC_USDT-1h.csv
uv run algo-process bot_data/raw/BTC_USDT-4h.csv
uv run algo-process bot_data/raw/ETH_USDT-15m.csv
uv run algo-process bot_data/raw/ETH_USDT-1h.csv
uv run algo-process bot_data/raw/ETH_USDT-4h.csv
```

`algo-process` validates the time grid, forward-fills gaps up to 0.5% (synthetic
bars get `Volume=0`), and writes `bot_data/processed/binance_<SYMBOL>_<TF>.csv`
with a UTC `datetime` column followed by `Open, High, Low, Close, Volume`. We do
**not** compute features here (Decision 12) — processed files stay raw OHLCV so
one file serves every strategy; indicators are computed live by each strategy.

---

## Step 3 — Verify integrity

The integrity test is gated: its `integration`-marked cases skip when a processed
file is missing, and run for real once the data is on disk.

```bash
# Integration checks against the real files (run after fetch + process):
uv run pytest tests/test_data_integrity.py -m integration -v

# Everything, including the deterministic unit tests:
uv run pytest tests/test_data_integrity.py -v

# Full project gate (lint + typecheck + tests):
make check
```

Each file is checked for:

- **Monotonic timestamps** — no duplicates, no out-of-order rows (hard failure).
- **OHLCV invariants** — `High ≥ max(Open, Close)`, `Low ≤ min(Open, Close)`,
  `High ≥ Low`, `Volume ≥ 0`, no NaN in OHLCV (hard failure).
- **Gaps** — any interval longer than 3×TF is logged as a WARNING (soft; does not
  fail the report, because real downtime is a legitimate live event — Decision 6).

### Ad-hoc check from a REPL

```python
from algo_bot.data_loader import load_processed
from algo_bot.data_integrity import check_integrity

df = load_processed("BTC/USDT", "1h")
report = check_integrity(df, "1h", symbol="BTC/USDT")
print(report.ok, report.n_rows, report.n_gaps)
```

`check_integrity` returns an `IntegrityReport` (frozen dataclass) with
`monotonic`, `invariants`, and `gaps` sub-results. `report.ok` is the hard
verdict; `report.gaps` lists every detected gap with `prev_ts`, `next_ts`,
`gap_ms`, and `missing_bars`.

---

## Resuming an interrupted fetch

`algo-fetch` resumes automatically. If a pull is interrupted (Ctrl-C, network
drop, laptop sleep), just re-run the exact same command — it reads the last
timestamp from the existing raw file, logs `"Resuming fetch from existing file"`,
and continues from there. Running it again after completion is a cheap no-op that
also tops the file up to the current candle.

## Forcing a full refetch

There is no `--force-refetch` flag (Decision 8 — kept minimal). To refetch from
scratch (e.g. if you suspect a Binance historical revision), delete the raw file
first:

```bash
rm bot_data/raw/BTC_USDT-1h.csv
uv run algo-fetch BTCUSDT 1h --start 2019-09-08
uv run algo-process bot_data/raw/BTC_USDT-1h.csv
```

---

## Troubleshooting

**`algo-process` aborts with "Too many missing bars".** The processor refuses to
fill when the gap ratio exceeds 0.5% (`DEFAULT_MAX_MISSING_RATIO`). Over 6.5
years this is unlikely, but a long exchange outage in the window could trip it.
First inspect where the gaps are (run `check_integrity` on the raw file, or read
the abort message). If the gaps are genuine Binance downtime and you accept
synthetic fill, raise the threshold explicitly:

```bash
uv run algo-process bot_data/raw/BTC_USDT-15m.csv --max-missing-ratio 0.01
```

If you would rather keep the real gaps unfilled, that is a different design
(Decision 6 alternative) and needs a code change — raise it as its own session.

**ETH files start later than BTC.** Expected — the ETH-USDT perpetual launched
after BTC (see note above). The `--start 2019-09-08` is a lower bound; the fetcher
simply begins at the first candle Binance actually has.

**`algo-fetch: command not found`.** Do not rely on an activated environment or
global entry point. Run `make sync`, then `uv run algo-fetch --help`.

**Integration tests all skip.** That means no processed files were found — run
Steps 1 and 2 first. The skip message points back here.

**Gap WARNINGs in the logs.** Informational. Gaps > 3×TF are reported so you know
where synthetic (filled) regions or real downtime sit; they do not fail integrity.

---

## File layout reference

| Stage | Path | Naming | Columns |
|-------|------|--------|---------|
| Raw | `bot_data/raw/` | `<BASE>_<QUOTE>-<TF>.csv` | `ts, datetime, Open, High, Low, Close, Volume` |
| Processed | `bot_data/processed/` | `binance_<SYMBOL>_<TF>.csv` | `datetime, Open, High, Low, Close, Volume` |

Both directories are git-ignored (`.gitignore` → `bot_data/`). The dataset lives
on disk locally; it is not committed.

---

*Phase 2 Session 2 deliverable. See `docs/ROADMAP.md` → Phase 2 → Session 2 and
`algo_bot/data_integrity.py` for the validator.*
