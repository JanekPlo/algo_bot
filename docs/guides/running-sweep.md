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

## "Worth walk-forward" threshold — hard filter (`WF_ELIGIBILITY_THRESHOLDS`)

The pre-WF filter lives in code as `WF_ELIGIBILITY_THRESHOLDS`
(`algo_bot.engine.walkforward`, ADR-013) — **import it, do not re-hardcode**:

- `sharpe_post > 1.0`
- `profit_factor_post > 1.3`
- `n_trades_post > 100`
- `max_drawdown_pct_post > -0.20`

This is a **pre-WF** filter ("is this sample worth the expensive walk-forward?"),
not the ADR-009 `MVP_THRESHOLDS` **post-WF** go-live gate (Sharpe ≥ 1.0,
maxDD ≥ -0.25, PF ≥ 1.3, n_trades ≥ 50). ADR-013 recalibrated the Sharpe bar
from an earlier arbitrary `1.5` to `1.0`: with a realistic 0.5–0.7× IS→OOS
decay an in-sample Sharpe of 1.0 maps to ~0.5–0.7 OOS, which is enough to be
worth *seeing* the decay in a WF run. `n_trades` (100) and DD (-0.20) are
**stricter** than the go-live gate on purpose — in-sample it is cheap to
accumulate trades, and Session 4 showed high Sharpe sitting on `n_trades ≈ 1`
(statistically empty), so we demand more statistics and a tighter DD before
spending WF compute.

Rank survivors by `sharpe_post × n_trades / 1000` (rewards edge **and**
sample size), pick 2–3 per (symbol, timeframe). If nothing survives, that is
itself a result: the strategy has no in-sample edge and walk-forward would be
a waste of compute (exactly the bghtrend outcome — ADR-012).

## Regime robustness sanity check (soft gate — run after the hard filter)

Clearing the hard filter is necessary but not sufficient: a single bull regime
(e.g. 2020–2021) can carry a full-history Sharpe while the strategy bleeds in
every other year. After the hard filter, for each surviving candidate compute a
**rolling per-year Sharpe** — split the full backtest window (2019-H2 … 2025)
into calendar-year bins (6–7 bins) and read `sharpe_post` per bin
(`algo_bot.metrics.rolling_sharpe`, or a `groupby` on the equity curve's year).

Soft condition: **`n_positive_years ≥ 3` of the 6–7 bins.**

- **Hard pass + broad regime** (positive across ≥ 3 years, not clustered) →
  proceed to walk-forward with normal confidence.
- **Hard pass + concentrated regime** (positive only in 2020–2021, negative or
  flat elsewhere) → **soft NO-GO**, or at minimum treat any subsequent WF result
  with heavy suspicion: the WF folds landing in the good regime will look great
  and the rest will drag, and the aggregate can mask a strategy that only works
  when the whole market trends up.

This stays a **judgment call in the notebook / guide, not a hardcoded constant**:
seven per-year numbers are something an operator reads and weighs (is the good
run a regime the strategy is *designed* to exploit, or luck?), not a clean
threshold — hardwiring it into the code would add complexity without value.
Record the per-year table and the call in the captains-log next to the WF decision.

## Statistical awareness

30 samples × 8 sweeps = 240 in-sample backtests. The deflated-Sharpe penalty
`sqrt(2·ln(240)/T_bars)` is negligible on 15m (~0.007, T≈228k bars) and small
on 4h (~0.024, T≈14k bars) — keep it in mind when a candidate sits right at a
threshold.
