# Module reference — `algo_bot.metrics`

Risk-adjusted metrics over an equity series and a per-trade PnL series. Pure functions plus a thin `summarize()` returning a `MetricsSummary` dataclass. Engine-agnostic — consumes the shapes that `algo_bot.engine.backtester.run_backtest` returns, but couples to neither `backtesting.py` nor any external metrics library.

Decision context: [ADR-007](../../adr/007-risk-adjusted-metrics.md).

## At a glance

```python
from algo_bot.metrics import summarize

stats, equity, trades = run_backtest(symbol="BTC/USDT", timeframe="1h", strategy="bghtrend_pullback", params={...})
metrics = summarize(equity["Equity"], trades["PnL"])
print(metrics.sharpe, metrics.calmar, metrics.recovery_time_days)
```

## Input shape

All annualised metrics take `equity: pd.Series` with a `DatetimeIndex`. Why equity (not returns):

- Converting equity → returns is unambiguous; the reverse is not (log vs simple, base price, scaling).
- Drawdown, recovery time, MAR, and Calmar all need the absolute equity path, not returns.
- `run_backtest` and walk-forward folds naturally hand equity downstream.

Trade-level metrics (`profit_factor`, `win_rate`) take `trades_pnl: pd.Series` of per-trade PnL — positives are wins, negatives are losses. When `run_backtest` returns a trades DataFrame, pass `trades["PnL"]` (or `trades["PnL_adj"]` after microstructure adjustment).

## Return convention

Sharpe and Sortino use **log returns** internally:

```python
log_returns(equity) = np.log(equity).diff().dropna()
```

Log returns are additive across time, so annualisation by `√(periods_per_year)` is exact, not an approximation. `total_return`, `cagr`, and trade-level statistics use **simple returns** (intuitive percentage moves).

This is the main numerical difference between our metrics and `backtesting.py` / `quantstats`, both of which use simple returns for Sharpe. Expect a ~0.5–2% delta on a typical strategy. The log-returns choice is the mathematically clean one — documented here so the discrepancy doesn't look like a bug.

## Annualisation

Every annualised metric takes `periods_per_year: float | None`. When `None`, the module calls:

```python
infer_periods_per_year(index, calendar="crypto") -> float
```

This takes the median timedelta between index entries and divides 365 days by it. Examples:

| Bar frequency | `infer_periods_per_year("crypto")` |
|---|---|
| 1 day | 365 |
| 1 hour | 8 760 |
| 5 minutes | 105 120 |
| 1 minute | 525 600 |

`calendar="tradfi"` returns the 252-base equivalents (`252` for daily). Phase 1 doesn't use it; reserved for future cross-asset comparisons.

When inference fails (non-`DatetimeIndex`, single-bar series, non-positive median delta), the function falls back to `365.0` and emits a `logger.warning`. Callers that want a hard guarantee should pass `periods_per_year` explicitly.

## Sharpe — static and rolling

`sharpe(equity, periods_per_year=None, rf=0.0) -> float` returns one annualised Sharpe for the whole series. `rolling_sharpe(equity, window=30, ...)` returns a `pd.Series` of annualised Sharpe values, one per rolling window. The default window of 30 fits roughly one month of daily bars; higher-frequency equity needs a larger window for the rolling Sharpe to be meaningful.

Rolling Sharpe is the primary diagnostic for overfitting in walk-forward (Decision F). A strategy that posts a great in-sample Sharpe but a sharply decaying rolling Sharpe across the OOS period is the classic overfit signature.

`rf=0.0` default. Crypto has no canonical risk-free benchmark. Parameter kept for future portfolio work and TradFi comparisons.

## Sortino

`sortino(equity, periods_per_year=None, mar=0.0) -> float`.

Downside-only version of Sharpe. Penalises only deviations below the MAR (Minimum Acceptable Return) target, not symmetric volatility. Uses the population-form downside deviation (`sqrt(mean((min(0, ret - mar))²))`), consistent with the 1991 Sortino definition.

NaN when no downside exists (all returns ≥ MAR). This usually means the equity is monotonically increasing or the sample is too small to be meaningful — the warning fires either way.

## Drawdown and recovery

`max_drawdown(equity) -> tuple[float, pd.Timedelta]` returns:

1. The most negative drawdown reached (`equity / running_max - 1`), in `[-1, 0]`. `0.0` when equity is monotonically non-decreasing.
2. The longest contiguous "underwater" duration, as `pd.Timedelta`. **This is not the same as recovery time** — underwater duration is how long we stayed below any peak; recovery time is how long from the trough of the largest drawdown back to a new high.

`recovery_time(equity) -> pd.Timedelta` returns the time from the trough of the maximum drawdown to the next equity value at or above the preceding peak. Sentinel `pd.Timedelta.max` when the series ends without recovering — `MetricsSummary.recovery_time_days` maps this to `float("inf")` for JSON-friendly downstream handling.

## Calmar vs MAR — two distinct ratios

Both are defined as `CAGR / |max drawdown|`, but they differ on the window:

| Metric | Window | Default behaviour |
|---|---|---|
| `calmar` | Trailing 36 months (Young 1991) | Falls back to whole history with a `logger.warning` when the series is shorter than 36 months. |
| `mar_ratio` | Entire equity track record (MAR 1978) | Always uses everything available. |

On a track record long enough to span 36 months, the two values converge. On Phase 1 crypto backtests (typically 1–2 years), Calmar fires the fallback warning and reads identical to MAR; the warning is the signal that the canonical trailing-36m semantics aren't yet available.

We keep them separate (rather than aliasing) because the literature definitions are distinct. A hedge-fund-style Calmar that uses the 36-month convention is not the same number as a "Calmar" computed over a 1-year backtest, and conflating them in code makes that confusion easy.

## Edge cases — NaN over `+inf`

Every "metric is mathematically undefined" path returns `float("nan")` and emits a `logger.warning` rather than `+inf` or a sentinel. Specifically:

- `sharpe` / `sortino` with zero variance (constant returns) → `NaN` + warning
- `sharpe` / `sortino` with no returns at all (single-bar equity) → `NaN`
- `profit_factor` with no losing trades → `NaN` + warning
- `profit_factor` with no trades → `NaN` + warning
- `calmar` / `mar_ratio` with zero drawdown → `NaN` + warning
- `recovery_time` when equity never recovers above the pre-drawdown peak → `pd.Timedelta.max` + warning

The rationale (from ADR-007 §7): `+inf` silently breaks comparisons and ordering of metric tables; `NaN` is loud — sorting, aggregation, and serialisation treat it explicitly. Downstream consumers (sweep aggregation, walk-forward fold ranking) must handle `NaN` explicitly. Sweep CSVs store `NaN` as empty cell; walk-forward fold reports surface `NaN` in the per-fold table.

## `MetricsSummary` dataclass

```python
@dataclass(frozen=True)
class MetricsSummary:
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    mar: float
    max_drawdown_pct: float
    max_drawdown_duration_days: float
    recovery_time_days: float
    profit_factor: float
    win_rate: float
    n_trades: int
    periods_per_year: float
```

Frozen (immutable). All fields JSON-serialisable with one caveat: `NaN` and `inf` need either a custom encoder or a `NaN→None`, `inf→None` pass before `json.dumps`. The CSV path (pandas `.to_csv`) handles both natively.

`periods_per_year` is stored on the summary explicitly so a reader downstream knows which annualisation convention produced the Sharpe / Calmar / MAR they see. Especially important when multiple timeframes are aggregated (e.g. cross-strategy comparison).

`recovery_time_days` is `float("inf")` when equity never recovered. `max_drawdown_duration_days` is `float`, always finite.

## `summarize()` — orchestrator

```python
def summarize(
    equity: pd.Series,
    trades_pnl: pd.Series | None = None,
    periods_per_year: float | None = None,
    rf: float = 0.0,
    mar_target: float = 0.0,
) -> MetricsSummary
```

Thin wrapper that calls each metric and packages the result. `periods_per_year` is inferred once and passed to all annualised primitives — guarantees a single convention across the summary.

`trades_pnl=None` or empty Series → trade-level metrics are `NaN`, `n_trades=0`. Useful when running a synthetic equity test or aggregating per-fold equity without per-trade granularity.

## Cross-strategy correlation

```python
strategy_correlation(
    equities: dict[str, pd.Series] | pd.DataFrame,
    method: Literal["pearson", "spearman"] = "pearson",
    on: Literal["log_returns", "simple_returns"] = "log_returns",
) -> pd.DataFrame
```

Computes the N×N correlation matrix between strategies on per-period returns. Default is Pearson on log returns — the portfolio-analytics standard. Spearman opt-in is the robust-to-fat-tails choice and a reasonable default in crypto. Equity-raw is intentionally not accepted as the correlation input: non-stationary series produce trend artefacts, not dependency signals.

Misaligned indices (different timeframes or backtest windows) are reconciled via `pd.concat(join="inner")` — only timestamps present in every series participate. Outer-join + fillna is explicitly avoided because imputation manufactures synthetic correlations.

`mean_pairwise_correlation(corr_matrix) -> float` averages the off-diagonal upper triangle — a single number for "how diversified is this portfolio" diagnostics. Close to 0 = well-diversified; close to 1 = strategies play almost the same game and the diversification benefit is gone.

Scope and home: the function lives in `algo_bot.metrics` rather than a dedicated `algo_bot.portfolio` module. Two pure functions are too small to justify a separate module. When portfolio analytics grow (Sharpe-portfolio, weighted equity, rebalancing schedules), a spin-off ADR will move them out.

```python
from algo_bot.metrics import strategy_correlation, mean_pairwise_correlation

equities = {
    "bghtrend":       eq_a,
    "mean_reversion": eq_b,
    "funding_arb":    eq_c,
}
corr = strategy_correlation(equities)
print(corr)
#                    bghtrend  mean_reversion  funding_arb
# bghtrend             1.00          -0.10        -0.05
# mean_reversion      -0.10           1.00         0.02
# funding_arb         -0.05           0.02         1.00

print(f"avg pairwise correlation: {mean_pairwise_correlation(corr):+.3f}")
# avg pairwise correlation: -0.043
```

## Consumers

- `algo_bot/engine/backtester.py` — could enrich `summary.json` with a serialised `MetricsSummary` block (follow-up, not landed in ADR-007 session).
- `algo_bot/risk/limits.py` (Decision E, planned) — will use `max_drawdown()` directly for the drawdown stop.
- `algo_bot/engine/walkforward.py` (Decision F, planned) — will call `summarize()` per fold and aggregate (mean, stdev) across folds. `rolling_sharpe()` for per-fold overfitting diagnostics.

## See also

- [ADR-007](../../adr/007-risk-adjusted-metrics.md) — full decision record, alternatives considered, edge-case rationale
- [ADR-005](../../adr/005-backtesting-py-mvp-engine.md) — engine that produces the equity / trades shapes this module consumes
- [ADR-006](../../adr/006-logging-strategy.md) — `get_logger(__name__)` is the channel for edge-case warnings
- Source: `algo_bot/metrics.py`
- Tests: `tests/test_metrics.py`
