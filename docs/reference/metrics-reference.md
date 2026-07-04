# Metrics reference

> **Status: DRAFT (Phase 2 Session 4).** Covers the core ADR-007 metrics as
> they appear in `_metrics_summary_*` (summary.json) and the sweep index.
> Walk-forward-specific interpretation (per-fold distributions,
> `MVP_THRESHOLDS` reading) lands in Session 5.

All metrics are computed by `algo_bot/metrics.py` (hand-rolled, ADR-007): log
returns internally, annualisation inferred from the equity index
(crypto calendar, 365 days), degenerate inputs → `NaN` plus a WARNING, never
an exception. Deep dive: `docs/reference/modules/metrics.md`.

Every backtest emits two summaries (ADR-011): `_metrics_summary_raw` (engine
equity) and `_metrics_summary_post_microstructure` (after slippage +
funding). **Decisions use post; raw is a sanity cross-reference.**

---

## Field-by-field

| Field | Definition | Interpretation in bghtrend / crypto context |
|-------|------------|---------------------------------------------|
| `total_return` | Equity end / start − 1. | Raw growth; meaningless without risk context — never rank by it. |
| `cagr` | Annualised growth rate. | Crypto beta is huge; compare against buy-and-hold BTC over the same window before getting excited. |
| `sharpe` | Annualised mean/std of log returns, rf = 0. | The primary ranking metric. In crypto perp trading, IS Sharpe ≈ 1.5 is a *candidate*, not an edge — expect 0.4–0.7× decay OOS (hence the >1.5 IS filter vs the ≥1.0 OOS MVP threshold, ADR-009). IS Sharpe > 3 on 30 random samples usually means a lucky sample or a look-ahead bug — investigate, don't celebrate. |
| `sortino` | Like Sharpe but downside deviation only (MAR = 0). | Useful when return distribution is skewed (trend-following typically is: many small losses, few big wins) — Sortino > Sharpe is the expected signature. |
| `calmar` | CAGR / \|maxDD\| on the **trailing 36 months** (Young 1991); falls back to full history with a WARNING when the series is shorter. | Penalises strategies whose returns came from one old regime. > 1 is good for trend-following on crypto. |
| `mar` | CAGR / \|maxDD\| over full history. | Same idea, no window; compare with `calmar` to spot "all the alpha was in 2020–21". |
| `max_drawdown_pct` | Deepest peak-to-trough, negative fraction (−0.25 = −25%). | MVP OOS threshold is ≥ −0.25; the IS filter uses ≥ −0.20 as buffer. On leveraged perps, DD beyond −0.3 in-sample is disqualifying regardless of Sharpe. |
| `max_drawdown_duration_days` | Longest peak-to-recovery span in days. | Gauge of psychological viability; a 300-day drawdown will not survive contact with the operator. |
| `recovery_time_days` | Days from max-DD trough back to prior peak; `inf` when never recovered. | `inf` at the end of the sample is common and not automatically fatal — check *when* the trough happened. |
| `profit_factor` | Gross wins / gross losses over trades. | NaN when there are no losing trades — with a real sample that means n_trades is tiny, not that the strategy is perfect. MVP OOS threshold 1.3; IS filter 1.5. |
| `win_rate` | Fraction of trades with PnL > 0. | Trend-following pullback entries typically land 35–50%; a win rate > 65% with high RR target suggests something is mispriced in the backtest. |
| `n_trades` | Trade count in the slice. | Statistical fuel. < 100 IS means walk-forward folds starve (ADR-009 wants ≥ 50 per evaluation); treat any metric computed on < 30 trades as anecdote. |
| `periods_per_year` | Annualisation constant actually used. | Sanity check that TF inference worked (15m → ~35k, 1h → 8760, 4h → 2190). |

## Cross-metric reading order

1. `n_trades` first — is there enough data to believe anything else?
2. `sharpe` (post) for ranking, `sortino`/`calmar` as shape confirmation.
3. `max_drawdown_pct` + duration — survivability.
4. `profit_factor` + `win_rate` — internal consistency with the strategy's
   design (pullback trend-following: moderate win rate, PF from a fat right
   tail).
5. Raw vs post spread — cost sensitivity (heuristic C in
   `docs/guides/running-sweep.md`).

## Multiple-testing caveat

A sweep is a multiple-comparison machine: the *expected maximum* Sharpe of N
random samples grows with N even under the null. With Session 4's 240
backtests the deflated-Sharpe penalty `sqrt(2·ln(N)/T_bars)` stays < 0.03 on
every TF, so thresholds are unchanged — but the principle stands: never
interpret top-1 in isolation from the distribution it was drawn from
(heuristic B).
