# Running a parameter sweep

How to run an in-sample parameter sweep with `algo-sweep`, read the results in
`results/experiments/index.csv`, and decide which parameter sets are worth
promoting to walk-forward.

> **TL;DR:** `algo-sweep --strategy <name> --symbols ... --timeframes ...
> --start ... --end ... --space_file config/<space>.yaml --microstructure full`
> runs N backtests sampled from the YAML space and appends one row per run to
> `results/experiments/index.csv`. Rank by `sharpe_post`, then apply the
> interpretation heuristics below before trusting anything.

---

## Invocation

Run from the WSL terminal inside the `algo_bot` conda env:

```bash
cd ~/quant_projects/algo_bot && conda activate algo_bot

algo-sweep --strategy bghtrend_pullback \
  --symbols BTC/USDT ETH/USDT \
  --timeframes 1h \
  --start 2019-09-08 --end 2026-07-04 \
  --space_file config/bghtrend_b1.yaml \
  --microstructure full
```

Notes:

- **Matched implied timeframe.** Each space file declares `__implied_tf`
  (b1/b2 → 1h, b3 → 15m, b4 → 4h; see `docs/reference/config-reference.md`).
  Running a space on a different timeframe logs a WARNING but proceeds —
  intentional cross-TF experiments are allowed, accidental ones leave a trace.
- **Meta-keys override CLI.** `__mode`, `__n` and `__seed` in the space file
  take precedence over `--mode`, `--n_samples` and `--seed`. To change the
  sample count, edit `__n` in the YAML (Session 4 default: 30).
- **Microstructure** defaults to `full` (ADR-011): slippage (`--slip_bps`,
  default 1.0/side) plus historical funding. Funding needs
  `bot_data/processed/binance_<SYMBOL>_funding.csv` — fetch with
  `algo-fetch-funding --symbol ETH/USDT --start 2019-11-01` if missing
  (synthetic fallback logs a WARNING).
- The engine runs **once** per sample; microstructure is a post-hoc overlay on
  the equity curve. A separate `--microstructure none` sweep is therefore
  redundant: the raw metrics are already in the `*_raw` columns.

## Output structure

- `results/experiments/index.csv` — one row per (symbol, timeframe, sample).
  Global append-only index across all sweeps.
- `results/backtests/<run_id>/` — per-run details: `summary.json`,
  `params.json`, `equity.csv`, `trades.csv` (see
  `docs/guides/running-backtest.md`). The `path` column in the index points
  here.

Key index columns (post Session 4 schema):

| Column | Meaning |
|--------|---------|
| `space_file` | Which YAML space produced the row (distinguishes b1 vs b2). |
| `params` | Sampled parameter set as sorted JSON. |
| `sharpe_raw` / `sharpe_post` | Annualised Sharpe before / after microstructure. **Rank by `sharpe_post`** — that is the realistic edge. |
| `calmar_raw` / `calmar_post` | CAGR / \|maxDD\| on trailing 36 months. |
| `profit_factor_raw` / `profit_factor_post` | Gross wins / gross losses. |
| `max_drawdown_pct_raw` / `max_drawdown_pct_post` | Max drawdown (negative fraction). |
| `n_trades_raw` / `n_trades_post` | Trade count (identical unless a run failed). |
| `ms:*` | Microstructure cost breakdown totals (ADR-011). |

## Reading top-N — interpretation heuristics

Sort by `sharpe_post` descending, take the top 10 per (space, symbol), then
apply all five checks. A high top-1 Sharpe **by itself means nothing** with
30 random samples.

- **A — Core-parameter clustering.** For each core parameter (taxonomy in
  `docs/reference/modules/strategy-bghtrend-pullback.md`), count values across
  the top 10. Dominated by one value (e.g. 9/10 share `ema_slow=200`) → the
  parameter carries signal. Spread as if uniform → no edge in that dimension.
- **B — Sharpe distribution shape.** Look at all 30 samples, not top-1.
  Healthy: monotonic decay from top-1 to top-30 with a small top-1 vs top-5
  gap. Unhealthy: top-1 stands alone (e.g. 1.8 → 1.1 → noise) → likely a
  lucky sample.
- **C — Raw vs post spread.** `sharpe_raw - sharpe_post` per sample. Stable
  spread (~0.2) → robust to costs. Large and variable (0.1–0.8) → the top
  samples are high-turnover and fragile to slippage/funding assumptions.
- **D — n_trades sanity.** Candidates need `n_trades > 100` so a 5-fold
  walk-forward keeps ≥ 50 trades per fold in spirit of ADR-009 thresholds.
  Top results with n_trades ≈ 20 are unusable regardless of Sharpe.
- **E — Cross-symbol consistency.** Compare top-3 parameter sets for the same
  space on BTC vs ETH. Overlapping values → cross-asset edge. Disjoint →
  asset-specific overfit.

## "Worth walk-forward" threshold

ADR-009 MVP thresholds are **out-of-sample** targets (Sharpe ≥ 1.0,
maxDD ≥ -0.25, PF ≥ 1.3, n_trades ≥ 50). In-sample results decay roughly
0.4–0.7× IS→OOS, so a candidate must clear all of:

- `sharpe_post > 1.5`
- `profit_factor_post > 1.5`
- `n_trades_post > 100`
- `max_drawdown_pct_post > -0.20`

Rank survivors by `sharpe_post × n_trades / 1000` (rewards edge **and**
sample size), pick 2–3 per (symbol, timeframe). If nothing survives, that is
itself a result: the strategy has no in-sample edge and walk-forward would be
a waste of compute.

## Statistical awareness

30 samples × 8 sweeps = 240 in-sample backtests. The deflated-Sharpe penalty
`sqrt(2·ln(240)/T_bars)` is negligible on 15m (~0.007, T≈228k bars) and small
on 4h (~0.024, T≈14k bars) — keep it in mind when a candidate sits right at a
threshold.
