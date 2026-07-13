# Running a single backtest

How to run one backtest with `algo-backtest` and read its outputs. For
parameter sweeps see `docs/guides/running-sweep.md`; for the metric
definitions see `docs/reference/metrics-reference.md`.

> **TL;DR:** `uv run algo-backtest --symbol BTC/USDT --timeframe 1h --strategy
> bghtrend_pullback --params '<json>'` writes
> `results/backtests/<run_id>/{summary.json, params.json, equity.csv,
> trades.csv}`. Read `_metrics_summary_post_microstructure` in `summary.json`
> — that is the realistic result.

---

## Invocation

```bash
cd ~/quant_projects/algo_bot
make sync       # first run or after uv.lock changes; no env activation

uv run algo-backtest --symbol BTC/USDT --timeframe 1h \
  --strategy bghtrend_pullback \
  --params '{"ema_fast": 17, "ema_mid": 55, "ema_slow": 200, "rr_target": 1.5}' \
  --start 2019-09-08 --end 2026-07-04 \
  --microstructure full
```

Frequently used flags (defaults in parentheses):

- `--params` — strategy parameters as JSON; keys are filtered against the
  strategy's `ParamSchema`, unknown keys are silently dropped.
- `--cash` / `--commission` — engine economics (defaults in
  `algo_bot/engine/backtester.py`).
- `--microstructure {none,full}` (`full`), `--slip_bps` (1.0),
  `--funding_source {historical,synthetic,none}` (`historical`),
  `--funding_rate_synthetic` (0.0001) — ADR-011.
- Risk limits (ADR-008): `--max_dd_pct`, `--daily_loss_pct`,
  `--risk_per_trade_pct`, `--daily_reset_tz`.

## Reading the output

`results/backtests/<run_id>/`:

- `summary.json` — full backtesting.py stats plus:
  - `_metrics_summary_raw` — ADR-007 metrics on the raw engine equity;
  - `_metrics_summary_post_microstructure` — same metrics after slippage +
    funding. **Use this one for decisions.**
  - `_microstructure` — cost breakdown totals and config.
- `params.json` — the exact parameter set (reproducibility).
- `equity.csv` — equity curve; `Equity_adjusted` column is post-microstructure.
- `trades.csv` — trade log with microstructure breakdown columns.

## Troubleshooting

- **`FileNotFoundError` on a CSV** — the dataset
  `bot_data/processed/binance_<SYMBOL>_<TF>.csv` is missing. Fetch it per
  `docs/guides/data-fetching.md`.
- **Funding WARNING (synthetic fallback)** — historical funding CSV missing;
  run `uv run algo-fetch-funding --symbol <SYM> --start <YYYY-MM-DD>`.
- **Params seem ignored** — key not in `ParamSchema` (typo?), it was filtered
  out. Check `params.json` in the output dir to see what was actually used.
- **`n_trades = 0` / NaN metrics** — entry conditions never fired on the
  slice; ADR-007 metrics emit NaN plus a WARNING instead of crashing. Widen
  the date range or loosen selectivity parameters.
- **Timeframe mismatch WARNING** — you ran a config space on a timeframe it
  was not designed for (`__implied_tf`); fine if intentional.
