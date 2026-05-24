# Reference — `algo_bot.risk.limits`

Deep reference for the portfolio-level risk module. For the rationale and rejected alternatives, see [ADR-008](../../adr/008-risk-limits-module.md). For a one-page concept orientation, see [concepts/risk-management](../../concepts/risk-management.md).

## Scope

`algo_bot.risk.limits` provides two categorically different operations:

- **Gates** — pure boolean checks against per-bar state. Three of them: `check_drawdown`, `check_daily_loss`, `check_positions`. Each takes `(state, equity_now, [ts,] limits)` and returns `RiskBreach | None`.
- **Sizing** — a single helper, `position_size(equity_now, sl_distance, risk_per_trade_pct)`, returning a float. Caller-driven (a strategy calls it inside `on_bar` and writes the result into `Signal.size`); the backtester does **not** auto-inject sizing.

All limits are portfolio-level. Per-position stops are strategy scope (Signal.sl_pct / Signal.meta["sl"], per ADR-003).

## Public API

```python
from algo_bot.risk import (
    RiskLimits,
    RiskState,
    RiskBreach,
    RiskLimitBreached,
    init_state,
    update_state,
    check_drawdown,
    check_daily_loss,
    check_positions,
    check_all,
    position_size,
)
```

### `RiskLimits`

Frozen dataclass holding the configuration. Every field is optional; `None` disables the corresponding gate (or the sizing helper consumer).

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_drawdown_pct` | `float \| None` | `None` | DD threshold from peak equity, e.g. `0.20` = 20%. Halts the run when `(peak - equity_now) / peak >= max_drawdown_pct`. |
| `daily_loss_pct` | `float \| None` | `None` | Daily loss threshold from `daily_start_equity`, e.g. `0.05` = 5%. Window boundary defined by `daily_reset_tz`. |
| `max_concurrent_positions` | `int \| None` | `None` | Cap on simultaneous open positions. On MVP single-symbol typically left `None`. Breach when `open_positions > cap` (strict greater-than). |
| `risk_per_trade_pct` | `float \| None` | `None` | Risk percent used by `position_size`. Not auto-injected — strategies that want it must call `position_size(...)` themselves. |
| `daily_reset_tz` | `str` | `"UTC"` | IANA timezone string for the daily reset boundary. Configurable to `"Europe/Warsaw"` etc. |

### `RiskState`

Frozen dataclass holding the per-bar state. **Immutable** — `update_state` returns a new instance; callers (typically the backtester wrapper) hold the latest one in a local attribute.

| Field | Type | Meaning |
|---|---|---|
| `equity_peak` | `float` | Highest equity observed since the run began. Used by `check_drawdown`. Grows monotonically. |
| `daily_start_equity` | `float` | Equity at the start of the current daily window. Reset by `update_state` when the day rolls over. |
| `daily_start_day` | `pd.Timestamp` | Normalised day (00:00 in `daily_reset_tz`, naive) for the current window. Used to detect rollover. |
| `open_positions` | `int` | Number of open positions on the current bar. Backtester wrapper writes `int(bool(self.position))` for single-symbol. |

### `RiskBreach`

Frozen dataclass — the data half of breach reporting. Pure functions return it (or `None`), never an exception.

| Field | Type | Meaning |
|---|---|---|
| `kind` | `Literal["max_drawdown", "daily_loss", "max_positions"]` | Which limit fired. |
| `value` | `float` | Observed value. DD/daily_loss are reported as negative percentages (loss narrative); `max_positions` is the integer count. |
| `threshold` | `float` | The configured threshold from `RiskLimits`. |
| `ts` | `pd.Timestamp` | Bar timestamp at which the breach was detected. `check_all` overwrites this with the bar_ts passed in, so callers get deterministic timestamps. |
| `message` | `str` | Human-readable explanation, used in logs and reports. |

### `RiskLimitBreached(Exception)`

Halt-the-run signal raised by the backtester wrapper. Carries the originating `RiskBreach` on the `.breach` attribute.

```python
try:
    stats = bt.run()
except RiskLimitBreached as exc:
    breach: RiskBreach = exc.breach
```

Only the backtester wrapper raises this. The pure layer (`check_*`) never raises; it returns data.

### Gates

```python
def check_drawdown(state: RiskState, equity_now: float, limits: RiskLimits) -> RiskBreach | None
def check_daily_loss(state: RiskState, equity_now: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskBreach | None
def check_positions(state: RiskState, limits: RiskLimits) -> RiskBreach | None
def check_all(state: RiskState, equity_now: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskBreach | None
```

`check_all` runs the three gates in fixed order: **drawdown → daily_loss → max_positions** and returns the first breach found. Rationale: max DD is the most existential safety net; reporting it first when multiple fire on the same bar matches operator intuition. Documented in ADR-008 §6 and the function docstring.

### State management

```python
def init_state(equity_start: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskState
def update_state(state: RiskState, equity_now: float, ts: pd.Timestamp, open_positions: int, limits: RiskLimits) -> RiskState
```

`init_state` is called once at the start of a run. `update_state` is called every bar before any `check_*`. Both validate `limits.daily_reset_tz` lazily — an unknown IANA string raises `ValueError` (mapped from `zoneinfo.ZoneInfoNotFoundError`) at the first call rather than at `RiskLimits()` construction.

### Sizing

```python
def position_size(equity_now: float, sl_distance: float, risk_per_trade_pct: float) -> float
```

Returns `(equity_now * risk_per_trade_pct) / sl_distance` for non-degenerate inputs. Edge cases:

- `sl_distance <= 0` → returns `0.0` with `logger.warning`. The strategy can interpret this as "skip this entry, sizing undetermined".
- `equity_now <= 0` → returns `0.0` with `logger.warning`.

**Caller-driven, not auto-injected.** A strategy that wants risk-based sizing calls `position_size` in its `on_bar` and writes the result into `Signal.size`. The backtester wrapper does **not** inspect `Signal.sl_pct` and auto-compute size. This is a deliberate decision (ADR-008 §8) — `Signal` has `sl_pct` (relative) and `meta["sl"]` (absolute price) but no `sl_price` field, and not every strategy emits an SL before entry. Auto-injection would silently misbehave for mean-reversion-style strategies.

## Backtester integration

`algo_bot.engine.backtester.run_backtest` accepts an optional `risk_limits: RiskLimits | None = None`. When provided, `make_bt_wrapper` installs the risk gate at the top of `Wrapped(BTStrategy).next()`:

1. On the first bar, `init_state` runs with `equity_start = self.equity` and the first bar's timestamp.
2. On every subsequent bar, `update_state` advances the state with the current `self.equity`, bar timestamp, and `int(bool(self.position))`.
3. `check_all` runs against the freshly-updated state.
4. If a `RiskBreach` is returned, the wrapper force-closes the open position at the current bar's `Close` (so `trades.csv` reflects the final exposure), logs a structured warning, and raises `RiskLimitBreached(breach)`.
5. `run_backtest` catches `RiskLimitBreached`, populates `stats["_risk_breach"]` with the dict form of the breach, and returns normally with `(stats, equity, trades)`.

`run_backtest` also always writes `stats["_risk_limits"] = asdict(risk_limits)` when `risk_limits` is provided (regardless of whether a breach fired), so downstream consumers can see what was configured.

### CLI

`algo-backtest` (and `python -m algo_bot.engine.backtester`) expose four optional flags:

```
--max_dd_pct FLOAT         # fraction, e.g. 0.20 = 20%
--daily_loss_pct FLOAT     # fraction, e.g. 0.05 = 5%
--risk_per_trade_pct FLOAT # fraction, e.g. 0.01 = 1%; used by position_size helper
--daily_reset_tz STR       # IANA tz, default "UTC"
```

Omitting all four keeps the previous behaviour (no risk gate, no risk metadata in summary).

## Edge cases and conventions

- **Equal-to-cap is not a breach.** `check_positions` uses strict `>`, not `>=`. `max_concurrent_positions=2` allows exactly 2; 3 is a breach.
- **`>=` for percentage limits.** `check_drawdown` and `check_daily_loss` use `>=`, so a configured 20% DD limit fires exactly at -20.00%. Matches the operator intuition "20% or worse halts the bot".
- **Daily reset on bar at midnight.** A bar whose timestamp is exactly `00:00:00` in the reset TZ counts as the new day. Its PnL is treated as belonging to the new daily window. Matches Binance funding settlement convention.
- **Single-bar series.** `init_state` with one bar is fine; the first `check_all` will see `daily_start_equity == equity_peak == equity_now`, so all gates pass. The system is robust to short runs.
- **Multiple simultaneous breaches.** `check_all` reports only the first hit (drawdown wins). The other limits are still active — but for a halted run, the second breach is informational, not actionable.
- **Backward compatibility.** `risk_limits=None` (default) on `make_bt_wrapper`, `run_backtest`, and the CLI keeps the pre-ADR-008 behaviour exactly.

## Limitations

- **No live integration yet.** Halt semantics for `live/live_binance.py` are different (graceful shutdown, alert, manual restart switch) and warrant their own ADR. Phase 3 deliverable.
- **No legacy-strategy support.** Non-`StrategyBase` strategies don't go through `make_bt_wrapper`, so the risk hook is unreachable for them. `run_backtest` emits a `logger.warning` if `risk_limits` is passed alongside a legacy strategy.
- **Sizing is not auto-applied.** As described above, this is by design but is a footgun for strategies that expect it. Documented in [concepts/risk-management](../../concepts/risk-management.md).

## See also

- [ADR-008 — Risk limits module](../../adr/008-risk-limits-module.md) — full design rationale, alternatives, consequences
- [Concepts — Risk management](../../concepts/risk-management.md) — concept orientation, where this fits in the pipeline
- [Reference — algo_bot.metrics](metrics.md) — companion module; `max_drawdown` semantics align
- [ADR-006 — Logging strategy](../../adr/006-logging-strategy.md) — convention for `get_logger` and structured `extra={...}`
- [ADR-007 — Risk-adjusted metrics](../../adr/007-risk-adjusted-metrics.md) — consumed via `summarize()` in `save_outputs`
