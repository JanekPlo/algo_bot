# ADR-009: Walk-forward analyzer — `algo_bot/engine/walkforward.py`

- **Status:** Accepted
- **Date:** 2026-05-25
- **Project phase:** 1 (Foundation), consumed in Phase 2 (Research & Backtest MVP)
- **Authors:** Janek Płoński, Claude

## Context

ROADMAP line ~35 lists Decision F as a Phase 1 deliverable: *"Walk-forward analyzer (`algo_bot/engine/walkforward.py`) — out-of-sample, train/test split z rolowaniem okna"*. Phase 2 success criteria (lines 99–104) make walk-forward the mandatory gate before any live deployment: WF Sharpe > 1.0, WF max DD < 25%, profit factor > 1.3 in OOS, >50 OOS trades. Every one of those numbers is *produced by* the analyzer this ADR delivers — without it Phase 2 cannot reach a go/no-go on bghtrend_pullback.

Walk-forward is the standard mitigation for the most common overfitting mode in systematic trading: sweep finds a parameter combination that looks great on the entire historical window, but the result is partly memorisation of the training period. WF answers a different question — *"does this parameter combination keep working when we slide forward in time onto data the parameters didn't see?"*. Pardo (2008) and Aronson (2007) both make the point that any backtest that hasn't been walk-forwarded is statistically unreliable.

The deliverable lands at the end of Phase 1 because every Phase 1 building block is in place:

- **`algo_bot.metrics`** (ADR-007) — provides `summarize()` and `MetricsSummary` per fold; `rolling_sharpe()` for regime decay diagnostics.
- **`algo_bot.risk.limits`** (ADR-008) — `RiskLimits` passed per fold; `RiskState` reset by construction since each `run_backtest` call builds its own state.
- **`algo_bot.engine.backtester.run_backtest`** (ADR-005 + ADR-008 §11) — `data: pd.DataFrame | None = None` parameter accepts a per-fold slice; `risk_limits` parameter wires the gate in; `(stats, equity, trades)` return shape is already consumed downstream.
- **`algo_bot.log`** (ADR-006) — structured logging with `extra={...}` for fold_id, train_range, test_range, n_trades, boundary_closes.

So this ADR is mostly *composition* — none of the heavy lifting (engine, metrics, risk, logging) is reinvented. The analyzer is responsible for: generating folds from a config, executing `run_backtest` per fold, aggregating per-fold `MetricsSummary` into distribution statistics, stitching per-fold equity into a continuous OOS curve, and writing artefacts to `results/walkforward/<wf_run_id>/`.

Two design pressures shape the decisions below:

1. **Engineering mindset** (rule #1 stdlib-first, rule #3 no mocks). Pure functions over frozen dataclasses; tests fed by deterministic synthetic OHLCV (3 years, hourly, known drift + noise) with hand-computed reference values.
2. **Phase 2 consumes this**. The analyzer must be operable as a CLI (`algo-walkforward`) by a human running bghtrend_pullback walk-forward as a one-off, and as a library by a future per-fold optimisation extension (future ADR — see §3).

The kickoff brief enumerated 11 architectural decisions with sketched options and trade-offs; alignment with Janek happened up-front in the session (the "PRZED implementacją" rule from `feedback_engineering_mindset`). The 11 + 4 follow-ups landed in the "Specific conventions" section below.

## Decision

**Implement `algo_bot/engine/walkforward.py` as pure functions over frozen dataclasses (`WalkForwardConfig`, `Fold`, `FoldResult`, `WalkForwardReport`). A generator produces folds from the config and an input DataFrame; an executor runs `run_backtest` per fold with a fresh per-fold RiskState; an aggregator packages per-fold `MetricsSummary` into a distribution DataFrame, a per-fold DataFrame, and a rebased+compounded equity curve. Reset RiskState per fold (literature convention). Default mode `rolling`, anchored as a toggle. Output structure under `results/walkforward/<wf_run_id>/`. Sequential MVP, parallel as a future flag.**

### API surface

```python
# algo_bot/engine/walkforward.py

@dataclass(frozen=True)
class WalkForwardConfig:
    """Walk-forward run configuration. All parameters frozen at construction."""
    train: int | pd.Timedelta            # bars OR time-based train window
    test: int | pd.Timedelta             # bars OR time-based test window
    step: int | pd.Timedelta | None = None  # None → defaults to `test` (no-overlap)
    mode: Literal["rolling", "anchored"] = "rolling"
    min_folds_warn: int = 5              # warn when expected folds < this
    risk_limits: RiskLimits | None = None  # passed to run_backtest per fold


@dataclass(frozen=True)
class Fold:
    """One train/test split. Timestamps are inclusive on both ends."""
    fold_id: int                # 0-indexed
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass(frozen=True)
class FoldResult:
    """Outcome of a single fold execution."""
    fold: Fold
    metrics: MetricsSummary                          # per-fold summarize()
    equity: pd.DataFrame                             # raw equity from run_backtest (test slice)
    trades: pd.DataFrame                             # raw trades from run_backtest
    risk_breach: dict[str, Any] | None               # stats["_risk_breach"] if any
    boundary_closes: int                             # trades closed exactly at test_end
    n_trades: int                                    # convenience mirror of metrics.n_trades


@dataclass(frozen=True)
class WalkForwardReport:
    """Aggregated walk-forward run output. JSON/CSV-serialisable shape."""
    wf_run_id: str
    config: WalkForwardConfig
    symbol: str
    timeframe: str
    strategy: str
    params: dict[str, Any]
    folds: tuple[FoldResult, ...]                    # per-fold detail
    folds_df: pd.DataFrame                           # one row per fold, MetricsSummary columns
    distribution: pd.DataFrame                       # rows: mean/median/std/min/max/mvp_threshold
    stitched_equity: pd.DataFrame                    # rebased+compounded OOS curve


# --- Fold generation -----------------------------------------------------------

def generate_folds(
    index: pd.DatetimeIndex,
    config: WalkForwardConfig,
) -> tuple[Fold, ...]:
    """Generuje sekwencję foldów na podstawie indeksu OHLCV i konfiguracji."""

def compute_expected_folds(
    index: pd.DatetimeIndex,
    config: WalkForwardConfig,
) -> int:
    """Tani policzony oczekiwany fold count bez generowania pełnego split'u."""


# --- Execution ----------------------------------------------------------------

def run_fold(
    fold: Fold,
    data: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    params: dict[str, Any],
    risk_limits: RiskLimits | None,
    cash: float = 100_000.0,
    commission: float = 0.0004,
) -> FoldResult:
    """Wykonuje pojedynczy fold (test window) przez run_backtest."""

def walk_forward(
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    params: dict[str, Any],
    config: WalkForwardConfig,
    data: pd.DataFrame | None = None,
    cash: float = 100_000.0,
    commission: float = 0.0004,
    wf_run_id: str | None = None,
    save: bool = True,
) -> WalkForwardReport:
    """Top-level entry: generuje foldy, wykonuje, agreguje, opcjonalnie zapisuje."""


# --- Aggregation --------------------------------------------------------------

def build_folds_df(folds: Sequence[FoldResult]) -> pd.DataFrame:
    """Zbiera per-fold MetricsSummary w jeden DataFrame (fold_id × metryki)."""

def build_distribution(folds_df: pd.DataFrame) -> pd.DataFrame:
    """Distribution stats (mean/median/std/min/max) + rząd mvp_threshold."""

def stitch_equity(folds: Sequence[FoldResult], initial_cash: float = 100_000.0) -> pd.DataFrame:
    """Rebase + compound per-fold OOS equity into continuous curve."""


# --- I/O ----------------------------------------------------------------------

def save_report(report: WalkForwardReport, out_dir: str | Path) -> Path:
    """Zapisuje walkforward_summary.json + walkforward_folds.csv + walkforward_distribution.csv + walkforward_equity.csv + fold_<i>/ subkatalogi."""


# --- CLI ----------------------------------------------------------------------

def main() -> None:
    """Entry point dla `algo-walkforward`. Argumenty: --symbol --timeframe --strategy --params --train --test --step --mode --max_dd_pct --daily_loss_pct --risk_per_trade_pct --log-level."""
```

### Specific conventions

1. **Window mode — `rolling` default, `anchored` toggle.** `WalkForwardConfig.mode: Literal["rolling", "anchored"]`. Rolling: train window slides forward each step; size stays fixed. Anchored: train window starts at `data.index[0]` and *grows* (expanding) by `step` each fold; test window slides as in rolling. Both share the same fold-generation skeleton — only the train-start computation differs. Rationale (Janek's call): both modes are cheap to implement together, and a disagreement between rolling and anchored Sharpe on the same strategy is a useful red-flag (regime sensitivity vs systematic edge). Default rolling because the literature standard (Pardo 2008) and because anchored implicitly down-weights recent data as train grows.

2. **Input granulation — `int | pd.Timedelta` for `train`, `test`, `step`.** Internally normalised to bars via `median_dt = pd.Series(index).diff().median()`. Conversion: `n_bars = int(td // median_dt)` for `pd.Timedelta` inputs; pass-through for `int`. When the coefficient of variation of `index.diff()` exceeds `0.10` (10%), a `logger.warning` fires — non-uniform bar spacing means "365 days" may resolve to a different number of bars at different parts of the series (data gaps, exchange downtime, sparse pre-2017 history). Phase 1 data from Binance perpetual futures post-2019 is uniformly spaced, so this is a safety net for future data sources.

3. **Re-fit policy — single config across folds (MVP).** One `params` dict for the whole walk-forward. No per-fold sweep, no per-fold optimisation. Rationale: per-fold optimisation requires a definition of "best params" (Sharpe? composite? Top-K stability?) and is best designed alongside the full Phase 2 sweep integration. Future ADR will introduce `WalkForwardConfig.optimise_per_fold: SweepSpec | None = None`. Notably out of scope for ADR-009.

4. **Fold aggregation — `folds_df` + `distribution` + `stitched_equity` as attributes, not just files.** `WalkForwardReport.folds_df: pd.DataFrame` (one row per fold, columns from `MetricsSummary` plus `fold_id`, `train_start/end`, `test_start/end`, `n_trades`, `boundary_closes`, `risk_breach_kind`). `WalkForwardReport.distribution: pd.DataFrame` (rows: `mean`, `median`, `std`, `min`, `max`, `mvp_threshold`; columns: same metric set). The `mvp_threshold` row encodes Phase 2 success criteria (Sharpe ≥ 1.0, max DD ≤ 0.25 abs, profit factor ≥ 1.3, n_trades ≥ 50) — operator sees pass/fail in one glance. Rationale: making these attributes (not files-only) lets a caller `report.folds_df.query("sharpe < 0.5")` without I/O round-trip.

5. **RiskState reset per fold.** Each fold invokes `run_backtest(data=fold_slice, risk_limits=config.risk_limits, ...)` — and because `make_bt_wrapper` builds a fresh `RiskState` via `init_state` at the start of every run, reset semantics are *automatic* with no extra code in walk-forward. Rationale (literature convention, Pardo / Aronson): folds are independent statistical realisations. Mixing state across folds creates "fake recovery" — fold 3 trips the DD limit, fold 4 starts from a naked state and reports recovery that wouldn't happen in live. The continuous-state variant (hybrid: reset state but continue equity from previous fold end) is a Phase 4 "realistic-live" mode — out of scope here; flagged as future `WalkForwardConfig.risk_mode: Literal["per_fold_reset", "continuous"]`.

6. **Cross-fold trade boundary — force-close + boundary_closes counter.** `backtesting.py` automatically closes every open position at the last bar of the supplied DataFrame, so passing `fold.test_data` to `run_backtest` *already* force-closes any trade still open at `test_end`. The analyzer doesn't re-implement that — it *detects* it after the fact: after each `run_backtest`, count `trades` rows where `ExitTime == fold.test_end` AND `EntryTime < fold.test_end`. That count lands on `FoldResult.boundary_closes` and surfaces in `folds_df.boundary_closes`. A `logger.warning` fires when `boundary_closes >= 0.5 * n_trades` for the fold — signal that `test_size` is too short relative to typical trade duration (caller should widen the test window or shorten the strategy holding horizon). Rationale: alternative (carry-over to next fold) entangles fold state across boundaries, defeating the independence convention from §5.

7. **Output structure — walk-forward owns its artefacts, doesn't call `save_outputs`.** Layout:

   ```
   results/walkforward/<wf_run_id>/
   ├── walkforward_summary.json       # top-level: config, distribution, mvp_threshold pass/fail
   ├── walkforward_folds.csv          # per-fold table (folds_df)
   ├── walkforward_distribution.csv   # mean/median/std/min/max/mvp_threshold
   ├── walkforward_equity.csv         # stitched rebased+compounded OOS curve
   └── fold_<i>/                      # one subdir per fold (0-indexed, zero-padded)
       ├── summary.json               # the run_backtest stats dict (incl. _risk_breach, _metrics_summary)
       ├── equity.csv                 # raw per-fold equity (not rebased)
       └── trades.csv                 # raw per-fold trades
   ```

   Rationale: `save_outputs` writes to `OUT_DIR = results/backtests/<run_id>/` and embeds `MetricsSummary` in `summary.json`. Walk-forward wants those artefacts in a *different* tree, and wants per-fold subdirectories grouped under one parent. Reusing `save_outputs` would require either monkeypatching `OUT_DIR` (fragile) or threading an `out_dir` parameter through the backtester (out of scope for this ADR). Internal `_save_fold_outputs(fold_dir, stats, equity, trades)` and `_save_wf_outputs(wf_dir, report)` are ~40 LOC total and decouple walk-forward I/O from the engine entirely.

8. **Equity stitching — rebase + compound.** Two-step algorithm:

   - Per fold `i`: compute `fold_return_i = equity_i[-1] / equity_i[0] - 1`. This is the *simple* return over the fold's test window.
   - Compound across folds: `stitched_equity[fold_i_end] = initial_cash * prod(1 + fold_return_k for k <= i)`.
   - Inside each fold, intra-fold equity points are rebased so that fold `i`'s curve starts at `stitched_equity[fold_(i-1)_end]` and ends at `stitched_equity[fold_i_end]`, preserving the *shape* of the in-fold drawdown trajectory but anchored to the compounded starting capital.

   Rationale: this matches how institutional funds report compounded returns — each period stands on its own returns, and the overall track record is the geometric chain. Raw concat (option A in the brief) creates artificial gaps when `step != test_size`. Rescaling (option C) fakes continuity by stretching fold N+1 to start where fold N ended — fine visually, wrong arithmetically. Raw per-fold equity stays in `fold_<i>/equity.csv` for forensics.

9. **Step convention — default `step = test`, validations.** `WalkForwardConfig.step is None` resolves to `step = test` (no overlap, no gaps). Validations on `compute_expected_folds`:
   - `step <= 0` → `ValueError("step must be positive")`
   - `step > train + test` → `ValueError("step exceeds window — degenerate, fold count likely 0")`
   - `step < test` → `logger.warning("step < test → overlapping test windows; folds are not statistically independent")`
   - `step > test and step <= train + test` → `logger.warning("step > test → gaps; some bars are never tested OOS")`

   The "no overlap" convention (step == test) is the Phase 2 default (5 folds × 3-month test = 15 months OOS coverage).

10. **Minimum fold count — warnings and errors.**
    - `expected_n_folds == 0` → `ValueError("data too short for one fold under given config")`
    - `expected_n_folds == 1` → `ValueError("single fold is not walk-forward; use run_backtest directly")`
    - `expected_n_folds < config.min_folds_warn` (default 5) → `logger.warning("expected folds < 5 — Phase 2 MVP criteria require ≥5 for statistical significance")`

    `min_folds_warn` is configurable so a Phase 3 (paper trading) caller running short windows for sanity can suppress the warning by lowering the threshold.

11. **Parallelisation — sequential MVP, future flag.** Sequential `for fold in folds: run_fold(...)`. Per-fold runtime on hourly BTC 1-year backtest is ~1–3s; 5–20 folds is ~10s–1min — acceptable. Folds are *fully independent* (reset RiskState §5, no shared state), so parallelisation is architecturally free. Future ADR will add `WalkForwardConfig.parallel: int = 1` using `concurrent.futures.ProcessPoolExecutor`. Sensible only when per-fold optimisation (§3 future) lands — sequential WF stays cheap.

12. **`fold_id` typing — `int` 0-indexed.** Internally `int`. Formatting `f"fold_{fold_id:03d}"` only at path generation and log message points. `Fold` is sortable by `fold_id`. Zero-padded to 3 digits in paths so directory listings sort lexicographically.

13. **`wf_run_id` convention — `wf_<symbol>_<timeframe>_<strategy>_<YYYYMMDD_HHMMSS>`.** Mirrors the existing `run_id` convention from `algo_bot.engine.backtester` with a `wf_` prefix to keep `results/walkforward/` distinct from `results/backtests/`. When `walk_forward(wf_run_id=None)`, the ID is built deterministically from inputs + UTC timestamp. Callers may pass an explicit ID for reproducibility (e.g. tests, scheduled re-runs).

14. **No-leakage invariant.** Generation guarantees, asserted in `generate_folds` and tested:
    - For every fold: `fold.test_start > fold.train_end` (strict — train and test never touch the same bar).
    - For consecutive folds `i, i+1`: `fold[i+1].test_start > fold[i].test_start` (monotonic progression).
    - No bar appears in two test windows UNLESS `step < test` (overlap accepted with §9 warning, never silently).

    All three invariants are deterministic from `(index, config)` and are checked at fold-generation time, not deferred to test-time.

15. **Distribution columns + `mvp_threshold` row.** Aggregated columns mirror `MetricsSummary` fields plus a few derived: `sharpe`, `sortino`, `calmar`, `mar`, `max_drawdown_pct`, `max_drawdown_duration_days`, `recovery_time_days`, `profit_factor`, `win_rate`, `total_return`, `cagr`, `n_trades`. Aggregation rows: `mean`, `median`, `std`, `min`, `max`. Extra row `mvp_threshold` encodes Phase 2 success criteria:
    - `sharpe = 1.0` (≥)
    - `max_drawdown_pct = -0.25` (≥, since DD is negative; equivalent to "loss ≤ 25%")
    - `profit_factor = 1.3` (≥)
    - `n_trades = 50` (≥)
    - other columns `NaN`

    A companion `mvp_pass: dict[str, bool]` lands on `WalkForwardReport` and `walkforward_summary.json` — operator sees four booleans without recomputing.

16. **mypy strict-on-new.** `algo_bot.engine.walkforward` is already declared in `[[tool.mypy.overrides]]` in `pyproject.toml` (line 172) with `disallow_untyped_defs = true` (verified in this session). No `pyproject.toml` change required.

17. **Logging.** `algo_bot.engine.walkforward` uses `from algo_bot.log import get_logger` (ADR-006). Convention:
    - `logger.info("walk-forward starting", extra={"wf_run_id", "symbol", "timeframe", "strategy", "n_folds_expected", "mode"})` — once at the top.
    - `logger.info("fold completed", extra={"fold_id", "train_range", "test_range", "n_trades", "sharpe", "max_drawdown_pct", "boundary_closes"})` — per fold milestone.
    - `logger.debug("fold detail", extra={...full FoldResult fields...})` — DEBUG-only, full per-fold payload.
    - `logger.warning(...)` — boundary close ratio, step convention violation, sub-min-folds count, non-uniform bar spacing.
    - `logger.info("walk-forward completed", extra={"wf_run_id", "n_folds_executed", "mvp_pass": {...}, "elapsed_seconds"})` — once at the bottom.

    Aligns with the sweep retrofit convention (sesja 2026-05-24): INFO for milestones, DEBUG for per-iteration detail, structured `extra={...}` everywhere.

18. **Docstrings.** Per `feedback_engineering_mindset` rule #5: docstrings in Polish, public API names in English. Google style.

## Consequences

**Positive:**

- **Phase 1 deliverable closes Foundation.** The last big architectural decision of Phase 1 lands here. Remaining Phase 1 work (CI + pre-commit + minor CLI entries) is mechanical, not architectural.
- **Phase 2 unblocked end-to-end.** The criteria on ROADMAP lines 99–104 are now actually computable: feed bghtrend_pullback through `algo-walkforward` and the resulting `walkforward_summary.json` contains the four booleans for MVP go/no-go.
- **Clean composition of existing modules.** `metrics.summarize`, `risk.limits.RiskLimits`, `engine.backtester.run_backtest(data=, risk_limits=)`, `log.get_logger` — no new infrastructure invented, all building blocks consumed at their stable APIs.
- **Engine-agnostic at the WF level.** The analyzer talks to `run_backtest` through its public signature. A future engine migration (vectorbt, nautilus_trader) only touches `run_backtest`'s implementation; walk-forward keeps working over the same `(stats, equity, trades)` shapes.
- **Folds are independent → parallelisation is architecturally free.** Sequential MVP today; flip to `ProcessPoolExecutor` tomorrow without rewriting fold semantics.
- **Outputs match operator intuition.** `fold_<i>/` looks identical to a regular `results/backtests/<run_id>/` — operator can open any fold subdir and forensically debug it as if it were a standalone backtest.

**Negative / costs:**

- **Equity stitching has one non-obvious math step.** Rebase + compound is the right algorithm but it's not what a naive reader expects ("just concat equity curves?"). Mitigated by: documented in `walkforward.md` §"Equity stitching" with a worked example; raw per-fold equity preserved in `fold_<i>/equity.csv` for anyone who wants to verify by hand.
- **`folds_df` and `distribution` shape are soft API contracts.** Adding columns is safe; renaming or removing breaks downstream consumers (notebooks, sweep aggregation in Phase 2). Mitigated by: dataclass-style frozen `WalkForwardReport` makes the shape visible, CHANGELOG entry will flag any future schema change.
- **`save_outputs` is not reused** — walk-forward duplicates ~40 LOC of "write summary.json + equity.csv + trades.csv" logic. Acceptable given the alternative (threading `out_dir` through `save_outputs` and `run_backtest`) is a larger surface change. If a third consumer needs the same write pattern, factor out `algo_bot.engine.io.save_run_outputs(out_dir, stats, equity, trades)` in a follow-up.
- **Single-config-only (no per-fold optimisation) limits Phase 2 sweep+WF integration.** Recognised cost; the future-ADR pointer is in §3 and `Alternatives Considered`.
- **`min_folds_warn` is a soft check.** A user can configure 2-fold runs and get a single warning, not an error. Intentional: paper trading sanity runs need flexibility. The Phase 2 MVP criteria (n_folds ≥ 5) live in the operator's checklist, not in code-level enforcement.

**Risks:**

- **Non-uniform bar spacing breaks `pd.Timedelta` → bar conversion.** The §2 warning covers detection. The fallback (using median delta) is best-effort; a caller who knows their data is sparse should pass `int` bars explicitly.
- **`backtesting.py` boundary-close semantics.** We rely on the library closing open positions at `data.index[-1]`. Documented library behaviour, but a future bump might shift it. Mitigated by: integration test asserts that a fold with a trade open at `test_end` produces an `ExitTime == test_end` row in `trades` — a regression breaks the test loudly.
- **Compound stitching with large per-fold losses.** A fold returning -90% pulls all downstream compounded equity down sharply — visually correct (that's what would happen live), but the equity curve becomes dominated by the worst fold. Not a bug; documented in `walk-forward.md` concept doc as an expected property of compound returns over WF.
- **`mvp_threshold` row encodes Phase 2 criteria in code.** When Phase 2 calibrates the thresholds (after seeing bghtrend WF numbers), the row's values may shift. Acceptable: it's a single named cell per metric, easy to change, will be a Phase 2 ADR if criteria move materially.

## Alternatives Considered

- **Single window mode (rolling only).** Smaller config surface (no `mode` field), simpler generator. Rejected: anchored is ~10 LOC of additional code in `generate_folds` and provides a useful sanity check (rolling vs anchored disagreement is a regime-sensitivity signal). The marginal complexity is well below the threshold of "feature for the sake of it".

- **Per-fold parameter optimisation in MVP.** The "classical" walk-forward optimisation: sub-sweep on each train window, apply best params to test. Rejected for ADR-009: requires a definition of "best params" (Sharpe? PF? composite?) and a sweep harness wired into walk-forward — both are sizeable design decisions that deserve their own ADR. The current MVP (single config across folds) is the standard tool for *parameter stability* analysis, which is the primary Phase 2 use-case for `algo_bot`.

- **Continuous RiskState across folds (hybrid mode).** Reset RiskState but propagate equity continuously from fold to fold. Rejected for MVP: violates the statistical independence of folds (literature convention). The use-case (Phase 4 realistic-live simulation) is genuine but premature — we don't have live infrastructure yet and the question "does walk-forward correctly predict live" is itself a Phase 4 deliverable. Future `risk_mode: Literal["per_fold_reset", "continuous"]` flag noted in §5.

- **Carry-over open trades across fold boundaries.** Trade opened in fold N continues in fold N+1. Rejected: entangles fold state, makes `MetricsSummary` per fold ambiguous (whose PnL is this trade?). The `boundary_closes` counter (§6) tracks how often this happens and warns when it dominates a fold — actionable information without the entanglement.

- **Raw concat of equity curves (no rebase).** Stitched equity = `pd.concat([f.equity for f in folds])`. Rejected: produces artificial discontinuities when `step != test_size` (gaps between fold N end and fold N+1 start in the test windows) and ignores that each fold starts with the same capital. Rebase + compound (§8) gives the mathematically correct chained-returns curve.

- **Reuse `save_outputs` with a `out_dir` parameter.** Thread `out_dir: str | None = None` through `save_outputs`, defaulting to `OUT_DIR = results/backtests/`. Rejected for ADR-009 scope: surface change to the engine API; walk-forward needs *grouped* output (one parent + per-fold subdirs), which the current `save_outputs` shape doesn't express. The `_save_fold_outputs` / `_save_wf_outputs` internal helpers are small and keep the engine API untouched.

- **Parallel execution in MVP (`ProcessPoolExecutor` from day one).** Rejected: sequential WF runtime is 10s–1min for typical Phase 2 use; parallelisation overhead (pickling per-fold data, process spawn) eats most of the gain for low fold counts. The future-flag note in §11 keeps the door open without paying the cost today.

- **`int`-only granulation (`train: int`, no `pd.Timedelta`).** Rejected: timeframe-agnostic time-based configuration is the operator's mental model ("12 months train, 3 months test"), not bar count ("8760 bars train, 2190 bars test"). The `int | pd.Timedelta` union (§2) costs ~5 LOC of conversion logic.

## References

- File (created in this session): `algo_bot/engine/walkforward.py`
- File (created in this session): `tests/test_walkforward.py`
- File (created in this session): `docs/reference/modules/walkforward.md`
- File (created in this session): `docs/concepts/walk-forward.md`
- File (modified in this session): `pyproject.toml` — adds `algo-walkforward = "algo_bot.engine.walkforward:main"` entry-point (mypy override pre-existing on line 172).
- File (modified in this session): `docs/CHANGELOG.md` — `[Unreleased]` entry.
- File (modified in this session): `docs/ROADMAP.md` — line ~35 walk-forward checkbox marked DONE.
- Related ADRs:
  - ADR-002 (pyproject-hatchling-stack) — mypy strict-on-new policy for the new module
  - ADR-003 (strategybase-signal-api) — `Signal` consumed transitively through `run_backtest`
  - ADR-005 (backtesting.py-mvp-engine) — engine signature consumed
  - ADR-006 (logging-strategy) — `get_logger(__name__)` for INFO/DEBUG/WARNING convention
  - ADR-007 (risk-adjusted-metrics) — `summarize()`, `MetricsSummary` consumed per fold
  - ADR-008 (risk-limits-module) — `RiskLimits` passed per fold; `data=` parameter on `run_backtest` consumed
- Literature:
  - Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies* (2nd ed.). Wiley.
  - Aronson, D. R. (2007). *Evidence-Based Technical Analysis*. Wiley.
  - White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*, 68 (5).

## Notes

- **Phase 2 concept doc (`docs/concepts/walk-forward.md`)** is intentionally thin in this session — orients a reader to "what is WF, why mandatory before live, how to read mean Sharpe vs distribution". It will be extended in Phase 2 alongside the bghtrend_pullback walk-forward analysis (ROADMAP line 91 → bghtrend WF notebook).

- **Open follow-up — per-fold parameter optimisation.** When Phase 2 reaches "parameter stability" (ROADMAP line 81), the WF optimisation question (§3) becomes acute. Expected ADR scope: integrate `algo_bot.engine.sweep` per-fold; define "best params" (likely Top-K stability rather than Top-1 Sharpe); decide on Out-Of-Bag aggregation for the optimised metrics. Tagged in `WalkForwardConfig` as an absent field rather than a deprecated one — addition is forward-compatible.

- **Open follow-up — parallel execution.** When per-fold optimisation lands, sequential WF becomes 20+ folds × 100 param combinations × 5s ≈ 3h. Parallelisation becomes mandatory at that point. Implementation: `concurrent.futures.ProcessPoolExecutor`, with `WalkForwardConfig.parallel: int = 1` (`-1` for "all cores"). Folds are independent, so the pattern is trivial — but data pickling for per-fold slices needs benchmarking on real OHLCV.

- **`make check` validation.** This module's mypy override is already in `pyproject.toml` (line 172, committed in ADR-002 era). After implementation: `make lint` (ruff on the new file), `make typecheck` (mypy on `algo_bot.engine.walkforward` with full strict), `make test` (`tests/test_walkforward.py`), and `make check` rolls them together. User runs in WSL terminal per ADR working-with-claude.md.

- **`stitched_equity` shape in `walkforward_equity.csv`.** Columns: `timestamp` (UTC), `equity` (rebased+compounded), `fold_id` (which fold this bar belongs to). The `fold_id` column lets a downstream consumer split or annotate the curve without re-deriving fold boundaries.

- **`walkforward_summary.json` shape.** Top-level keys: `wf_run_id`, `config` (dict from `asdict(config)`), `symbol`, `timeframe`, `strategy`, `params`, `n_folds`, `mvp_pass` (4 booleans), `distribution` (dict-of-dicts from `distribution.to_dict()`), `elapsed_seconds`. Per-fold detail lives in `fold_<i>/summary.json`, not duplicated at the top level.
