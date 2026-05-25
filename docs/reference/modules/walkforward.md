# Module reference — `algo_bot.engine.walkforward`

Walk-forward analyzer for out-of-sample backtesting. Generates train/test folds from an input DataFrame, executes `run_backtest` per fold with reset RiskState, aggregates per-fold `MetricsSummary` into a distribution table, stitches per-fold equity into a continuous OOS curve, and writes artefacts to `results/walkforward/<wf_run_id>/`.

Decision context: [ADR-009](../../adr/009-walk-forward.md). Concept orientation: [concepts/walk-forward](../../concepts/walk-forward.md).

## At a glance

```python
from algo_bot.engine.walkforward import WalkForwardConfig, walk_forward
import pandas as pd

config = WalkForwardConfig(
    train=pd.Timedelta(days=365),
    test=pd.Timedelta(days=90),
    step=pd.Timedelta(days=90),   # default == test (no overlap)
    mode="rolling",
)

report = walk_forward(
    symbol="BTC/USDT",
    timeframe="1h",
    strategy="bghtrend_pullback",
    params={...},
    config=config,
)

print(report.mvp_pass)
# {'sharpe': True, 'max_drawdown_pct': True, 'profit_factor': True, 'n_trades': True}
print(report.distribution.loc["mean", ["sharpe", "max_drawdown_pct"]])
# sharpe              1.43
# max_drawdown_pct   -0.18
```

CLI:

```bash
algo-walkforward \
    --symbol BTC/USDT --timeframe 1h --strategy bghtrend_pullback \
    --params '{"ema_fast": 21, "ema_slow": 55}' \
    --train 365d --test 90d --step 90d --mode rolling \
    --max_dd_pct 0.20
```

## Input shape

`walk_forward(data=None)` defaults to loading OHLCV from `bot_data/processed/binance_<SYMBOL>_<TF>.csv` via `algo_bot.engine.backtester.load_ohlcv_csv`. Pass `data=` explicitly for tests or for data sources outside the standard CSV layout. The DataFrame must have:

- A monotonic-increasing `DatetimeIndex` (UTC strongly recommended).
- Columns `Open`, `High`, `Low`, `Close`, `Volume`.

`run_backtest` is invoked per fold with `data=<test_slice>`, so the analyzer never re-runs `load_ohlcv_csv` per fold.

## Fold generation

```python
generate_folds(index: pd.DatetimeIndex, config: WalkForwardConfig) -> tuple[Fold, ...]
compute_expected_folds(index: pd.DatetimeIndex, config: WalkForwardConfig) -> int
```

`generate_folds` produces the full sequence; `compute_expected_folds` is the cheap pre-flight count used by validations and CLI sanity output.

Both `train`, `test`, and `step` accept either `int` (bars, pass-through) or `pd.Timedelta` (converted via the index's median delta). When `step is None`, it resolves to `test` — the no-overlap default.

### Rolling vs anchored

| Mode | Train window | Test window | Notes |
|---|---|---|---|
| `rolling` | Sliding (fixed size, advances by `step`) | Sliding | Default. Literature standard (Pardo 2008). |
| `anchored` | Expanding (starts at `index[0]`, grows) | Sliding | Sanity check — disagreement vs rolling signals regime sensitivity. |

Both modes produce the same fold count for the same config. The only difference is the train-start computation:

- Rolling: `train_start = test_start - train_bars`
- Anchored: `train_start = index[0]` (constant)

### No-leakage invariant

`generate_folds` enforces and asserts:

1. `fold.test_start > fold.train_end` (strict — train and test never share a bar).
2. `fold[i+1].test_start > fold[i].test_start` (monotonic progression of test windows).
3. No bar appears in two test windows, unless `step < test` (overlap accepted with a warning, never silently).

A leakage assertion failure raises `AssertionError` at generation time, not at fold execution.

### Step convention and validations

| Condition | Outcome |
|---|---|
| `step <= 0` | `ValueError("step must be positive")` |
| `step > train + test` | `ValueError("degenerate config, fold count ~0")` |
| `step < test` | `logger.warning("overlapping test windows; folds not statistically independent")` |
| `step > test` and `step <= train + test` | `logger.warning("gaps; some bars never tested OOS")` |
| `expected_folds == 0` | `ValueError("data too short for one fold")` |
| `expected_folds == 1` | `ValueError("single fold is not walk-forward; use run_backtest")` |
| `expected_folds < min_folds_warn` | `logger.warning("Phase 2 MVP criteria require ≥N folds")` |

`min_folds_warn` defaults to 5 (Phase 2 success criterion, ROADMAP line 79). Configurable for short sanity runs in Phase 3 paper trading.

## Public API

```python
from algo_bot.engine.walkforward import (
    WalkForwardConfig,
    Fold,
    FoldResult,
    WalkForwardReport,
    generate_folds,
    compute_expected_folds,
    run_fold,
    walk_forward,
    build_folds_df,
    build_distribution,
    compute_mvp_pass,
    stitch_equity,
    save_report,
)
```

### `WalkForwardConfig`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `train` | `int \| pd.Timedelta` | required | Train window length. `int` → bars; `pd.Timedelta` → converted via median index spacing. |
| `test` | `int \| pd.Timedelta` | required | Test window length. |
| `step` | `int \| pd.Timedelta \| None` | `None` (= `test`) | Window advancement per fold. |
| `mode` | `Literal["rolling", "anchored"]` | `"rolling"` | See "Rolling vs anchored" above. |
| `min_folds_warn` | `int` | `5` | Warn when expected fold count is below this. |
| `risk_limits` | `RiskLimits \| None` | `None` | Passed to `run_backtest` per fold. RiskState is reset per fold by construction. |

### `Fold`

Frozen dataclass — one train/test split. Timestamps are inclusive on both ends.

| Field | Type | Meaning |
|---|---|---|
| `fold_id` | `int` | 0-indexed identifier. Format `f"fold_{fold_id:03d}"` for paths/logs. |
| `train_start` | `pd.Timestamp` | First bar of the training window. |
| `train_end` | `pd.Timestamp` | Last bar of the training window. |
| `test_start` | `pd.Timestamp` | First bar of the test window. Strictly greater than `train_end`. |
| `test_end` | `pd.Timestamp` | Last bar of the test window. |

### `FoldResult`

Outcome of one fold execution.

| Field | Type | Meaning |
|---|---|---|
| `fold` | `Fold` | The fold definition. |
| `metrics` | `MetricsSummary` | From `algo_bot.metrics.summarize(equity, trades_pnl)`. |
| `equity` | `pd.DataFrame` | Raw equity from `run_backtest` (NOT rebased; rebase happens in `stitch_equity`). |
| `trades` | `pd.DataFrame` | Raw trades from `run_backtest`. |
| `risk_breach` | `dict \| None` | `stats["_risk_breach"]` if a limit fired, else `None`. |
| `boundary_closes` | `int` | Number of trades closed exactly at `fold.test_end`. |
| `n_trades` | `int` | Mirror of `metrics.n_trades`. |

### `WalkForwardReport`

Top-level output. JSON/CSV-friendly via `save_report`.

| Field | Type | Meaning |
|---|---|---|
| `wf_run_id` | `str` | `wf_<symbol>_<timeframe>_<strategy>_<YYYYMMDD_HHMMSS>`. |
| `config` | `WalkForwardConfig` | The configuration used. |
| `symbol`, `timeframe`, `strategy`, `params` | various | Pass-through inputs. |
| `folds` | `tuple[FoldResult, ...]` | Per-fold detail, ordered by `fold_id`. |
| `folds_df` | `pd.DataFrame` | One row per fold, indexed by `fold_id`. Columns: train/test ranges, `boundary_closes`, `risk_breach_kind`, and all fields from `MetricsSummary`. |
| `distribution` | `pd.DataFrame` | Rows `mean`, `median`, `std`, `min`, `max`, `mvp_threshold`. Columns: numeric metrics from `MetricsSummary`. |
| `stitched_equity` | `pd.DataFrame` | Rebased + compounded OOS curve. Columns: `timestamp`, `equity`, `fold_id`. |
| `mvp_pass` | `dict[str, bool]` | Four booleans: `sharpe`, `max_drawdown_pct`, `profit_factor`, `n_trades`. |
| `elapsed_seconds` | `float` | Wall-clock execution time. |

## Per-fold execution

```python
run_fold(
    fold: Fold,
    data: pd.DataFrame,
    *,
    symbol: str, timeframe: str, strategy: str, params: dict,
    risk_limits: RiskLimits | None,
    cash: float = 100_000.0, commission: float = 0.0004,
) -> FoldResult
```

For each fold the analyzer:

1. Slices `data.loc[fold.test_start : fold.test_end]`.
2. Calls `run_backtest(data=test_slice, risk_limits=risk_limits, ...)`.
3. Computes `MetricsSummary` from the returned equity and trade PnL.
4. Counts `boundary_closes` — trades whose `ExitTime == fold.test_end`.
5. Captures any `stats["_risk_breach"]`.

Boundary closes occur naturally because `backtesting.py` closes any position still open at `data.index[-1]`. When `boundary_closes >= 0.5 * n_trades` for a fold, a warning fires — signal that the test window is too short relative to typical trade duration.

## Risk module per fold

`RiskState` is **reset per fold** (literature convention, Pardo / Aronson). The reset is automatic: each `run_backtest` call internally invokes `init_state` on the first bar of its data, so the analyzer doesn't carry any per-fold state — passing `config.risk_limits` per fold is sufficient.

Consequence: a fold that trips the max-DD limit halts that fold and is reflected in `FoldResult.risk_breach`, but the next fold starts from a fresh state. This is the right statistical treatment (folds are independent realisations) and the wrong "realistic-live" treatment (live trading is one continuous trajectory). For Phase 4 a `risk_mode="continuous"` variant may be added.

## Aggregation

### `build_folds_df(folds) -> pd.DataFrame`

Per-fold table indexed by `fold_id`. Columns: `train_start`, `train_end`, `test_start`, `test_end`, `boundary_closes`, `risk_breach_kind`, plus all numeric fields from `MetricsSummary`.

Useful for ad-hoc analysis: `report.folds_df.query("sharpe < 0.5")`, `report.folds_df.sort_values("max_drawdown_pct")`, etc.

### `build_distribution(folds_df) -> pd.DataFrame`

5 + 1 rows × N numeric metric columns:

| Row | Source |
|---|---|
| `mean` | `folds_df[col].mean()` |
| `median` | `folds_df[col].median()` |
| `std` | `folds_df[col].std()` (pandas default ddof=1) |
| `min` | `folds_df[col].min()` |
| `max` | `folds_df[col].max()` |
| `mvp_threshold` | `MVP_THRESHOLDS` for the four MVP metrics; `NaN` for everything else |

The `mvp_threshold` row lets an operator compare distribution stats against Phase 2 criteria in one place. The four thresholds:

| Metric | Threshold | Direction |
|---|---|---|
| `sharpe` | `1.0` | `mean >= threshold` |
| `max_drawdown_pct` | `-0.25` | `mean >= threshold` (DD is negative; `-0.20 >= -0.25` means "20% loss is OK") |
| `profit_factor` | `1.3` | `mean >= threshold` |
| `n_trades` | `50` | `mean >= threshold` |

### `compute_mvp_pass(distribution) -> dict[str, bool]`

Returns four booleans (one per MVP metric). When the `mean` row is missing or the relevant cell is `NaN`, the boolean is `False`. The four-key shape is stable — even an empty distribution returns `{"sharpe": False, ...}`.

## Equity stitching

```python
stitch_equity(folds: Sequence[FoldResult], initial_cash: float = 100_000.0) -> pd.DataFrame
```

Two-step rebase + compound (ADR-009 §8):

1. **Per fold:** compute `fold_return = equity[-1] / equity[0] - 1` (simple return over the fold's test window).
2. **Compound across folds:** `cum_capital_i = initial_cash * Π(1 + fold_return_k for k <= i)`.

Inside each fold, every intra-fold equity point is rescaled multiplicatively so the curve starts at `cum_capital_(i-1)` and ends at `cum_capital_i`, preserving the *shape* of the in-fold drawdown trajectory.

Worked example (three folds, returns `+10%`, `+5%`, `-5%`, initial cash `100,000`):

| Fold | Start equity | End equity | Compounded |
|---|---|---|---|
| 0 | 100,000.00 | 110,000.00 | × 1.10 |
| 1 | 110,000.00 | 115,500.00 | × 1.05 |
| 2 | 115,500.00 | 109,725.00 | × 0.95 |

Final stitched equity: `100,000 × 1.10 × 1.05 × 0.95 = 109,725.00`.

This is the **mathematically correct** chained-returns curve. Raw concatenation (option A in ADR-009) creates artificial gaps when `step != test`. Rescaling fold N+1 to start where fold N ended (option C) ignores that each fold starts with the same capital and produces a fake-continuous curve.

The raw per-fold equity is preserved in `fold_<i>/equity.csv` for forensic verification.

## Output layout

```
results/walkforward/<wf_run_id>/
├── walkforward_summary.json       # config, mvp_pass, distribution, n_folds, elapsed_seconds
├── walkforward_folds.csv          # one row per fold (folds_df)
├── walkforward_distribution.csv   # mean/median/std/min/max/mvp_threshold
├── walkforward_equity.csv         # rebased + compounded OOS curve
└── fold_<i>/                      # 0-indexed, zero-padded to 3 digits
    ├── summary.json               # fold range, n_trades, boundary_closes, risk_breach, metrics
    ├── equity.csv                 # raw per-fold equity (not rebased)
    └── trades.csv                 # raw per-fold trades
```

`wf_run_id` defaults to `wf_<symbol>_<timeframe>_<strategy>_<YYYYMMDD_HHMMSS>` (UTC). Pass an explicit value for tests or reproducibility.

The fold subdirectory mirrors `results/backtests/<run_id>/` shape — an operator can open any `fold_<i>/` and debug it as if it were a standalone backtest.

`walkforward_summary.json` shape (top level):

```json
{
  "wf_run_id": "wf_BTCUSDT_1h_bghtrend_pullback_20260525_120000",
  "config": {...},
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "strategy": "bghtrend_pullback",
  "params": {...},
  "n_folds": 7,
  "mvp_pass": {"sharpe": true, "max_drawdown_pct": true, "profit_factor": true, "n_trades": true},
  "distribution": {...},
  "elapsed_seconds": 42.7
}
```

`NaN` and `inf` are replaced with `null` before serialisation.

## CLI

```
algo-walkforward
    --symbol      <e.g. BTC/USDT>          required
    --timeframe   <e.g. 1h>                required
    --strategy    <strategy module name>   required
    --params      <JSON inline>            default '{}'
    --train       <int bars or '365d'>     required
    --test        <int bars or '90d'>      required
    --step        <int bars or '90d'>      default = test
    --mode        rolling|anchored         default rolling
    --min_folds_warn <int>                 default 5
    --cash        <float>                  default 100000.0
    --commission  <float>                  default 0.0004
    --max_dd_pct        <fraction>         optional (e.g. 0.20)
    --daily_loss_pct    <fraction>         optional
    --risk_per_trade_pct <fraction>        optional
    --daily_reset_tz    <IANA TZ>          default UTC
    --log-level   DEBUG|INFO|WARNING|ERROR default INFO
```

Output paths and summary line are printed to stdout. Structured logs (per-fold milestones, breach detection) land in `logs/algo_bot.log` per ADR-006.

## Edge cases and conventions

- **`step = test` default.** No overlap, no gaps. The Phase 2 standard (5 folds × 3-month test = 15 months OOS coverage).
- **Reset RiskState per fold.** Each `run_backtest` builds a fresh `RiskState`, no carry-over.
- **Boundary closes are detected, not enforced.** `backtesting.py` already closes open positions at the last bar; the analyzer just counts them and warns when they dominate.
- **`compute_expected_folds` is cheap.** Used by validations and CLI without generating the full fold sequence.
- **Equity stitching uses simple returns per fold.** Compounded geometrically across folds. NOT log returns — log compounding inside a fold would conflict with the natural multiplicative rebase.
- **Sequential execution.** Per-fold runtime is ~1–3s; 5–20 folds = ~10s–1min. Parallel as a future flag.
- **`MVP_THRESHOLDS` are module-level constants.** When Phase 2 calibrates them, edit `MVP_THRESHOLDS` in `walkforward.py` and the `mvp_threshold` row + `mvp_pass` automatically follow.

## Limitations

- **No per-fold parameter optimisation.** Single `params` dict across all folds. Per-fold sub-sweep is a planned future ADR (Phase 2 deliverable, alongside the bghtrend_pullback WF analysis).
- **No continuous RiskState mode.** "Reset per fold" only. The hybrid mode (reset state, continuous equity) is a Phase 4 realistic-live extension.
- **Sequential only.** No parallel execution in MVP. Architecture allows it (folds are independent) — future flag.
- **`pd.Timedelta` → bars conversion uses median spacing.** Non-uniform data (gaps, exchange downtime) trips a warning above 10% CoV. Pass `int` bars explicitly for sparse data.
- **No optimisation around `save_outputs`.** Walk-forward owns its I/O; the engine's `save_outputs` is not reused. This duplicates ~40 LOC of write-helpers but keeps the engine API unchanged.

## Consumers

- `algo_bot/engine/sweep.py` (Phase 2, planned) — will call `walk_forward` per parameter combination for parameter stability analysis.
- Phase 2 notebook `03_bghtrend_walkforward_analysis.ipynb` (ROADMAP line 84, planned) — primary research consumer.
- Phase 4 ADR (planned) — extension to `risk_mode="continuous"` for realistic-live simulation.

## See also

- [ADR-009](../../adr/009-walk-forward.md) — full decision record, alternatives considered
- [Concepts — Walk-forward](../../concepts/walk-forward.md) — methodology, why mandatory before live
- [Reference — algo_bot.metrics](metrics.md) — `summarize()` consumed per fold
- [Reference — algo_bot.risk.limits](risk-limits.md) — `RiskLimits` consumed per fold; reset semantics
- [ADR-005](../../adr/005-backtesting-py-mvp-engine.md) — `run_backtest` signature consumed
- [ADR-006](../../adr/006-logging-strategy.md) — `get_logger(__name__)` convention
- Source: `algo_bot/engine/walkforward.py`
- Tests: `tests/test_walkforward.py`
