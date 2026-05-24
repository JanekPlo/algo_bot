# ADR-007: Risk-adjusted metrics module — hand-rolled `algo_bot/metrics.py`

- **Status:** Accepted
- **Date:** 2026-05-22
- **Project phase:** 1 (Foundation)
- **Authors:** Janek Płoński, Claude

## Context

ROADMAP (line ~36) lists Decision D as a Phase 1 deliverable: *"Standard risk-adjusted metrics (Sharpe, Sortino, Calmar, MAR, profit factor, recovery time) — `algo_bot/metrics.py`"*. Until now the project has only the metrics that `backtesting.py` produces in its `Stats` object (`Sharpe Ratio`, `Sortino Ratio`, `Calmar Ratio`, `Max. Drawdown [%]`, `Profit Factor`, ...). They are usable but unsuitable as the canonical Phase 1 set for three reasons:

1. **Hidden conventions.** `backtesting.py` annualises with 252 trading days (TradFi calendar) and uses simple returns. For 24/7 crypto futures both choices are wrong by default — a 5-minute or hourly Sharpe gets implicitly scaled to a TradFi year, so cross-strategy comparisons drift unless every reader knows the convention.
2. **No diagnostic windowing.** The `Stats` object emits a single Sharpe per run. A static Sharpe summarises but hides regime-dependent decay, which is the most common overfitting smell in walk-forward (Decision F). Rolling-window Sharpe is the obvious diagnostic and `backtesting.py` does not expose it.
3. **Edge cases collapse silently.** Calmar at zero drawdown, profit factor with zero losing trades, recovery time on a single-bar run — `backtesting.py` returns `inf` or `nan` without context. Risk module (Decision E) and walk-forward (Decision F) will both consume metrics and need predictable behaviour at edges.

Decisions E (risk limits — needs `max_drawdown`) and F (walk-forward — needs per-fold metrics) both depend on this module. Implementing the metrics inline in each module would duplicate the conventions exactly when we want them centralised.

`backtester.run_backtest` (Phase 1, ADR-005) returns `(stats: dict, equity: DataFrame, trades: DataFrame)`. The `equity` DataFrame carries an `Equity` column with a `DatetimeIndex`; `trades` carries `EntryTime/ExitTime/EntryPrice/ExitPrice/Size/PnL` (and `PnL_adj` post microstructure adjustment). The metrics module should consume these primitives, not `backtesting.py`'s internal `Stats` object — staying agnostic from the engine leaves a clean path if we migrate to vectorbt / nautilus post-MVP (ADR-005 footer).

Engineering mindset rule #1 (stdlib-first) and rule #3 (no mocks in tests with integration value) shape the implementation: hand-written, pure functions over numpy/pandas; tests fed by deterministic equity series with hand-computed reference values (no `pytest-mock`, no `quantstats.utils.compsum`).

## Decision

**Hand-roll `algo_bot/metrics.py` as pure functions over `equity: pd.Series` (or returns) and `trades_pnl: pd.Series` (or `pd.DataFrame` with `PnL` column), plus a thin `summarize()` returning a `MetricsSummary` dataclass.** No external dependency beyond numpy and pandas (both already in `[project.dependencies]`).

The module exposes the following public surface:

### API contract

```python
# Helpers
infer_periods_per_year(index: pd.DatetimeIndex, calendar: Literal["crypto", "tradfi"] = "crypto") -> float
log_returns(equity: pd.Series) -> pd.Series
simple_returns(equity: pd.Series) -> pd.Series

# Core metrics
total_return(equity: pd.Series) -> float
cagr(equity: pd.Series, periods_per_year: float | None = None) -> float
sharpe(equity: pd.Series, periods_per_year: float | None = None, rf: float = 0.0) -> float
rolling_sharpe(equity: pd.Series, window: int = 30, periods_per_year: float | None = None, rf: float = 0.0) -> pd.Series
sortino(equity: pd.Series, periods_per_year: float | None = None, mar: float = 0.0) -> float
max_drawdown(equity: pd.Series) -> tuple[float, pd.Timedelta]   # (dd_pct in [-1, 0], duration)
calmar(equity: pd.Series, periods_per_year: float | None = None, window_months: int = 36) -> float
mar_ratio(equity: pd.Series, periods_per_year: float | None = None) -> float
recovery_time(equity: pd.Series) -> pd.Timedelta
profit_factor(trades_pnl: pd.Series) -> float
win_rate(trades_pnl: pd.Series) -> float

# Aggregation
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

def summarize(
    equity: pd.Series,
    trades_pnl: pd.Series | None = None,
    periods_per_year: float | None = None,
    rf: float = 0.0,
    mar_target: float = 0.0,
) -> MetricsSummary
```

### Specific conventions

1. **Input shape.** All functions take `equity: pd.Series` with `DatetimeIndex`. We accept equity (not returns) at the boundary — converting equity → returns is unambiguous, the reverse is not. Functions internally call `log_returns()` or `simple_returns()` as appropriate. Trade-level metrics (`profit_factor`, `win_rate`) take `trades_pnl: pd.Series` of per-trade PnL.

2. **Return convention.** Sharpe and Sortino use **log returns** internally (`np.log(equity).diff().dropna()`). Log returns are additive across time, which means annualisation via `√(periods_per_year)` is exact rather than an approximation. `total_return`, `cagr`, and trade-level statistics use **simple returns** (intuitive, matches narrative reporting).

3. **Annualisation.** `periods_per_year` is a parameter on every annualised metric (Sharpe, Sortino, CAGR, Calmar, MAR). When omitted, `infer_periods_per_year(equity.index, calendar="crypto")` runs: takes the median timedelta between index entries, divides 365 days by that. So 1-day bars → 365, 1-hour bars → 8760, 5-minute bars → 105120. Fallback when inference fails (non-uniform index, single bar): 365 with a `logger.warning`. The `calendar="tradfi"` variant returns 252-based equivalents for future cross-asset work (not used in Phase 1).

4. **Risk-free rate.** `rf=0.0` default. Crypto has no canonical risk-free benchmark and Phase 1 is single-strategy so excess-return math is unnecessary. Parameter kept for future portfolio work.

5. **Sharpe — static and rolling.** Both forms. `sharpe()` returns one float for the whole series. `rolling_sharpe(window=30)` returns a `pd.Series` aligned to `equity.index[window:]`. Default window 30 fits roughly one month of daily bars; for higher-frequency equity the caller picks. Rolling Sharpe is the primary overfitting diagnostic for walk-forward (Decision F).

6. **Calmar vs MAR — two separate metrics, not aliases.** Two industry conventions exist:
   - **Calmar Ratio** (Terry W. Young, MAR Capital convention): `CAGR / |maxDD|` computed over the **trailing 36 months** of equity. Default 36 months because that's the literature standard; configurable via `window_months`.
   - **MAR Ratio** (Managed Account Reports, 1978): `CAGR / |maxDD|` computed over the **entire equity track record**.

   On equity series shorter than `window_months`, `calmar()` falls back to the whole-history computation **and logs a warning** (not NaN). Rationale: crypto MVP backtests typically span 1–2 years; returning NaN for the most common backtest length is harmful; the warning preserves the signal that "you're reading a Calmar that's not yet the canonical trailing-36m value".

7. **Edge cases — NaN + `logger.warning`.** Specifically:
   - `profit_factor` with zero losing trades → NaN + warning ("no losing trades — small sample or unrealistic")
   - `profit_factor` with zero trades → NaN + warning
   - `calmar` / `mar_ratio` with zero drawdown → NaN + warning ("zero drawdown — strategy never entered or single-bar series")
   - `recovery_time` when the series never reaches a new high after max DD → `pd.Timedelta.max` (sentinel: "never recovered") with warning. Stored in `MetricsSummary.recovery_time_days` as `inf` for JSON serialisation friendliness.
   - `sharpe` / `sortino` with constant returns (zero variance) → NaN + warning

   NaN over `+inf` everywhere because: JSON-serialisation in `save_outputs` already tolerates NaN (`json.dumps(nan)` is non-standard but `pandas.to_csv` handles it); `min()`/`max()`/sorting on a list of metrics with `+inf` is silently wrong, with NaN it's loud (numpy raises or propagates).

8. **Recovery time as `pd.Timedelta`.** The `recovery_time()` primary return is a `pd.Timedelta`. In `MetricsSummary` it is mirrored to `recovery_time_days: float` for serialisation (and `inf` for never-recovered). Same dual-form for `max_drawdown_duration`.

9. **`summarize()` is a thin orchestrator.** It calls each primitive in sequence and packages the result into the frozen dataclass. Callers wanting individual metrics use them directly; callers wanting a one-shot summary use `summarize()`. Walk-forward (Decision F) will call `summarize()` per fold; risk module (Decision E) will call `max_drawdown()` directly.

10. **mypy strict-on-new.** `algo_bot.metrics` is already declared in `[[tool.mypy.overrides]]` in `pyproject.toml` (line 174) with `disallow_untyped_defs = true`. No change required.

11. **Logging.** `metrics.py` uses `from algo_bot.log import get_logger` (ADR-006) for the warnings on edge cases. Per-function silence is the default — warnings only fire on the edge cases enumerated in §7.

12. **Docstrings.** Per `feedback_engineering_mindset` rule #5: docstrings in Polish, public names in English. Google style (ADR convention).

## Consequences

**Positive:**

- **Single source of truth for risk-adjusted metrics across backtest, walk-forward, and risk module.** Decisions E (risk) and F (walk-forward) consume the same primitives and the same `MetricsSummary` shape — no inline metric duplication, no convention drift.
- **Crypto-native annualisation.** Default 365-based inference matches the asset class. TradFi switch available for future cross-asset extension.
- **Rolling Sharpe makes walk-forward diagnostics trivial.** Decision F will plot rolling Sharpe per fold to detect overfitting regimes.
- **Pure functions over pandas/numpy.** Testable with hand-computed reference values, no mocking needed. Aligns with `feedback_engineering_mindset` rules #1 (stdlib-first) and #3 (no mocks).
- **`MetricsSummary` is JSON-serialisable.** `dataclasses.asdict()` plus a small NaN→None pass and the run summary in `results/backtests/<run_id>/summary.json` gains a structured `metrics` section.
- **Engine-agnostic.** No coupling to `backtesting.py`'s `Stats` object. If we migrate to vectorbt / nautilus (ADR-005 post-MVP rewrite), only `run_backtest` changes — metrics keep working over the same equity/trades shapes.

**Negative / costs:**

- **Reimplementation cost (~150–200 LOC).** Sharpe / Sortino / Calmar / max_drawdown / recovery / profit_factor are all small functions, but each has subtle edge-case handling. Worth the cost given how often we'll consume them, but it's code we have to maintain ourselves.
- **Two Calmar/MAR functions instead of one alias.** Industry usage is sloppy here; we explicitly separate them. Small documentation burden (`metrics.md` reference page must spell this out) and a potential foot-gun if a caller reaches for the wrong one.
- **Pragmatic Calmar fallback on short series.** Computing Calmar over a 1-year backtest with `window_months=36` falls back to whole-history with a warning. A reader who doesn't notice the warning may compare a "Calmar" from a 1-year backtest with a "Calmar" from a 5-year backtest and think they're the same metric. Mitigation: `MetricsSummary` includes the actual `periods_per_year` field; `metrics.md` documents the fallback.
- **Log-returns Sharpe diverges slightly from `backtesting.py`'s Sharpe.** Quantstats and `backtesting.py` use simple returns. Our log-return Sharpe will read ~0.5–2% different on typical strategies. We document this and accept it — log returns are the mathematically correct choice for an annualisation-by-√n.

**Risks:**

- **If a future strategy emits equity with a non-uniform DatetimeIndex** (e.g. only on entry/exit timestamps, not per bar), `infer_periods_per_year` median-timedelta inference will be wrong. Mitigation: every metric consuming `periods_per_year` accepts the parameter explicitly; callers from `run_backtest` always have the bar timeframe and can pass it. Walk-forward will pass it per fold. Default inference is best-effort, not authoritative.
- **`MetricsSummary` dataclass shape becomes a soft API contract.** Adding fields is safe; renaming or removing fields breaks downstream code (walk-forward CSVs, risk module reports). Mitigation: keep the dataclass minimal in Phase 1, version it in CHANGELOG if extended.
- **Profit factor / recovery time / Calmar all have `NaN` outputs on small samples.** Downstream consumers (sweep aggregation, walk-forward fold ranking) must handle NaN explicitly. Mitigation: documented in module docstring + `metrics.md`; sweep CSV will store NaN as empty cell.

## Alternatives Considered

- **`quantstats`** — popular crypto/equity metrics library, batteries-included (Sharpe, Sortino, Calmar, tearsheets, plots). Rejected because: bakes in 252-day annualisation by default with non-obvious `periods` parameter; uses simple returns for Sharpe (different from our log-returns choice); ships an entire tearsheet/plotting stack we don't need in Phase 1; `quantstats.utils.compsum` and related helpers have known precision quirks documented in their own issues; would import matplotlib transitively for a numbers-only module. Convenience wins on a notebook, not on a library boundary.

- **`empyrical`** — Quantopian's metrics library (`empyrical.sharpe_ratio`, etc.). Conceptually closest to what we want — minimal, library-friendly, no plotting deps. Rejected because: Quantopian shut down in 2020 and `empyrical` is unmaintained (last meaningful commit 2020, open issues for Python 3.10+ compatibility). Phase 5 production on a VPS shouldn't depend on an unmaintained package.

- **`empyrical-reloaded`** — community fork of `empyrical` keeping it alive on modern Python. Rejected because: still a small community fork without stable maintenance signals; we'd be one of few users; introducing a dependency for ~150 LOC of arithmetic is a poor cost/benefit when `feedback_engineering_mindset` rule #1 says stdlib-first.

- **Use `backtesting.py`'s `Stats` object directly** — read the `Sharpe Ratio`, `Sortino Ratio`, `Calmar Ratio`, etc. that the engine already emits. Rejected because: locks Phase 1 conventions to TradFi 252-day annualisation; no rolling Sharpe; no MAR (only Calmar); edge cases (zero DD, zero losing trades) collapse silently to `inf`/`nan` without context; tightly couples downstream consumers (walk-forward, risk module) to `backtesting.py`, breaking the engine-agnostic stance of ADR-005.

- **Aliasing Calmar = MAR (single function, two names)** — closer to industry "in the wild" usage where the two are conflated. Rejected (Janek's call) because: separating them keeps the historical definitions distinct and forces the reader to consciously choose. Conflation is convenient until someone tries to compare our Calmar to a hedge fund's Calmar that uses the trailing-36m convention.

- **Compute everything inline in `run_backtest` (no separate module)** — bypass the module, populate `stats` dict with metrics directly in the backtester. Rejected because: walk-forward would have to call `run_backtest` per fold to get metrics, but walk-forward needs per-fold aggregation of metrics (mean, stdev across folds), not raw equity per fold. Centralising in `algo_bot.metrics` makes both `run_backtest` and `walkforward` thin consumers of the same primitives.

## References

- File (created in this session): `algo_bot/metrics.py`
- File (created in this session): `tests/test_metrics.py`
- File (optional, this session): `docs/reference/modules/metrics.md`
- Consumers (downstream, future): `algo_bot/risk/limits.py` (Decision E — uses `max_drawdown`), `algo_bot/engine/walkforward.py` (Decision F — uses `summarize`, `rolling_sharpe`), `algo_bot/engine/backtester.py` (could optionally enrich `summary.json` with `MetricsSummary` — follow-up, not this session)
- Related ADRs:
  - ADR-002 (pyproject-hatchling-stack) — mypy strict-on-new policy for the new module
  - ADR-005 (backtesting.py MVP engine) — equity/trades shapes consumed here, engine-agnostic stance
  - ADR-006 (logging-strategy) — `get_logger(__name__)` for edge-case warnings
- Literature:
  - Sharpe, W. F. (1966). "Mutual Fund Performance." Journal of Business, 39 (1).
  - Young, T. W. (1991). "Calmar Ratio: A Smoother Tool." Futures Magazine.
  - Managed Account Reports (1978) — original MAR ratio definition.

## Notes

- **Calmar default window.** 36 months follows Young (1991). Configurable via `window_months` should we ever want 60-month or 12-month variants. On Phase 1 crypto data (typical backtest 2019–2025), the trailing 36 months will be the default behaviour.

- **`recovery_time` on never-recovered equity.** Returns `pd.Timedelta.max` as the primary sentinel and `float("inf")` in `MetricsSummary.recovery_time_days`. Both round-trip cleanly through pandas; `pd.Timedelta.max` is a documented sentinel (not a hack). JSON serialisation: `summarize()` consumers should NaN-replace `inf` if they need pure JSON.

- **Follow-up sessions tracked:**
  - `algo_bot/engine/backtester.py` `save_outputs` extension to embed `MetricsSummary` into `summary.json` — small change, deferred so this session stays focused on the metrics module itself.
  - `tests/test_backtest.py` signature fix (broken since 2026-05-14) — explicitly out of scope this session; will be a small standalone session before Decision E.
  - `algo_bot/executor.py` FIXME (broken `optimize_backtest` import) — unchanged from the captains-log `2026-05-21` open question; not coupled to Decision D.

- **In-session extension — cross-strategy correlation.** `strategy_correlation(equities, method, on)` and `mean_pairwise_correlation(corr_matrix)` were added to `algo_bot/metrics.py` in the same session as the core single-strategy metrics, on Janek's request anticipating the post-MVP portfolio direction (ROADMAP "Po MVP" — portfolio z 2-3 strategii nieskorelowanych). Rationale for keeping in `metrics.py` rather than spinning off to `algo_bot/portfolio.py` immediately: scope is small (two pure functions), the input/output shapes (`pd.Series`/`pd.DataFrame`) are already conventions of this module, and a separate portfolio module is premature until we have Sharpe-portfolio, weighted-equity, and rebalancing primitives to host alongside. Conventions chosen (no new sub-decision required, all defaults industry-standard): Pearson default + Spearman opt-in; log returns default + simple returns opt-in; equity-raw rejected (correlation of non-stationary series is a trend artefact, not a dependency signal); `pd.concat(join="inner")` on misaligned indices (rejecting outer+fillna because imputation creates synthetic correlations). **Re-evaluation trigger:** when portfolio analytics grow beyond correlation (Sharpe-portfolio, position-sizing rules, rebalancing schedules), spin off `algo_bot/portfolio.py` in a dedicated ADR.

- **`make check` validation.** This module's strict-on-new mypy override is already in `pyproject.toml` (committed in ADR-002 era). After implementation, the validation chain is: `make lint` (ruff), `make typecheck` (mypy on `algo_bot.metrics` with full strict), `make test` (`tests/test_metrics.py`), and `make check` rolls them together. User runs this in WSL terminal (per ADR working-with-claude.md "Setup techniczny").
