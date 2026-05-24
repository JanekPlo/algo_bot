# ADR-008: Risk limits module — `algo_bot/risk/limits.py` + backtester hook

- **Status:** Accepted
- **Date:** 2026-05-24
- **Project phase:** 1 (Foundation)
- **Authors:** Janek Płoński, Claude

## Context

ROADMAP (line ~34) lists Decision E as a Phase 1 deliverable: *"Risk management module (`algo_bot/risk/limits.py`): max drawdown stop, max concurrent positions, daily loss limit, position sizing oparty o ryzyko (% equity per trade)"*. Phase 1 success criterion (line 60) makes the engine-side stop explicit: *"Risk module zatrzyma backtest gdy drawdown przekroczy próg"*. So the deliverable is not only a library — the backtester must actually halt when a limit is breached.

The module sits between `algo_bot.metrics` (Decision D, ADR-007 — provides `max_drawdown` and the `equity` Series shape) and `algo_bot.engine.walkforward` (Decision F, future — needs a DD stop to avoid "fake recovery" within a fold). It consumes equity from the engine, decides whether limits are breached, and reports back. The kinds of limits split into two categorically different operations:

1. **Gates** — booleans against state (current equity vs peak, daily start vs current, open position count vs cap). Called every bar. Output is "limit breached or not", and a breach halts the run.
2. **Sizing** — a calculator. Given current equity, a stop distance, and a risk-per-trade percentage, return the position size that caps the loss at the configured percentage. Called at entry. Output is a number.

Conflating these into a single `RiskManager.check()` interface would muddy the contract — they take different arguments, fire at different points in the bar lifecycle, and have different return types. We keep them separate at the API layer (`check_*` functions and `position_size` helper) even though they live in the same module.

`backtesting.py` exposes `self.equity` on `Strategy` (cash + open-position mark-to-market value) on every bar, so a sub-trade resolution drawdown check is mechanically free — we already have a `Wrapped(BTStrategy)` adapter in `algo_bot/engine/backtester.py:make_bt_wrapper` whose `next()` runs at the start of every bar. Injecting a risk check at the very top of `next()` is the natural integration point.

The kickoff brief (captain's log 2026-05-24) flagged a hard prerequisite: `tests/test_backtest.py` has been `@pytest.mark.skip` since the 2026-05-14 flatten because the test signature is stale. Repairing it is in scope of this ADR because the same fixture (synthetic OHLCV → `run_backtest`) is reused for the risk-module integration test. While doing the test fix we noticed `run_backtest` cannot be exercised at all without a CSV file on disk (`bot_data/processed/binance_<SYMBOL>_<TF>.csv`); the natural fix is an optional `data: pd.DataFrame | None = None` argument that bypasses `load_ohlcv_csv`. Walk-forward (Decision F) will need this hook regardless to inject sliced per-fold DataFrames, so we lock the parameter in now as a side-effect of E.

Engineering mindset rule #1 (stdlib-first) and rule #3 (no mocks with integration value) shape the implementation: pure functions over `pd.Timestamp` / numeric primitives, frozen dataclasses for config and state, no `pytest-mock` — tests are fed by deterministic equity sequences (e.g. `[100, 120, 80]` with `max_drawdown_pct=0.30` → breach) and a synthetic OHLCV `run_backtest` integration.

## Decision

**Implement `algo_bot/risk/limits.py` as pure functions over frozen dataclasses (`RiskLimits` config, `RiskState` per-bar state, `RiskBreach` result), plus a `position_size` calculator helper. Translate `RiskBreach` to a `RiskLimitBreached` exception inside the backtester wrapper, where halt-the-run semantics belong.**

### API surface

```python
# algo_bot/risk/limits.py

@dataclass(frozen=True)
class RiskLimits:
    """Konfiguracja limitów ryzyka portfolio-level. None disables individual limits."""
    max_drawdown_pct: float | None = None        # 0.20 → halt at 20% DD from peak
    daily_loss_pct: float | None = None          # 0.05 → halt at 5% loss vs daily start
    max_concurrent_positions: int | None = None  # None → unenforced (MVP single-symbol)
    risk_per_trade_pct: float | None = None      # 0.01 → 1% equity per trade (sizing)
    daily_reset_tz: str = "UTC"                  # IANA tz string; default UTC

@dataclass(frozen=True)
class RiskState:
    """Per-bar state utrzymywany przez caller (backtester) — immutable updates."""
    equity_peak: float
    daily_start_equity: float
    daily_start_day: pd.Timestamp  # normalized day in daily_reset_tz, naive
    open_positions: int

@dataclass(frozen=True)
class RiskBreach:
    kind: Literal["max_drawdown", "daily_loss", "max_positions"]
    value: float          # observed value (e.g. -0.25 for -25% DD)
    threshold: float      # configured limit (e.g. 0.20)
    ts: pd.Timestamp      # timestamp when the breach was detected
    message: str          # human-readable for logs/reports

class RiskLimitBreached(Exception):
    """Sygnał halt-the-run podnoszony w backtester wrapperze."""
    def __init__(self, breach: RiskBreach) -> None: ...
    @property
    def breach(self) -> RiskBreach: ...

# Pure gates — wszystkie zwracają RiskBreach | None
def check_drawdown(state: RiskState, equity_now: float, limits: RiskLimits) -> RiskBreach | None: ...
def check_daily_loss(state: RiskState, equity_now: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskBreach | None: ...
def check_positions(state: RiskState, limits: RiskLimits) -> RiskBreach | None: ...
def check_all(state: RiskState, equity_now: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskBreach | None: ...
    # first-hit semantics — kolejność: drawdown → daily_loss → max_positions

# State management — immutable transition
def update_state(state: RiskState, equity_now: float, ts: pd.Timestamp, open_positions: int, limits: RiskLimits) -> RiskState: ...
def init_state(equity_start: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskState: ...

# Sizing — pure helper, caller-driven (no auto-injection)
def position_size(equity_now: float, sl_distance: float, risk_per_trade_pct: float) -> float: ...
```

### Specific conventions

1. **Limit semantics.** All limits are **portfolio-level** safety nets, not per-position. Per-position stops are strategy scope (Signal.sl_pct / meta["sl"] per ADR-003). A risk module that overrides strategy SL would entangle two responsibilities. ROADMAP "Decyzje architektoniczne" line ~243 (`% equity per trade + max DD stop`) is honoured at the portfolio layer; per-position sizing is the strategy's job.

2. **`None` disables a limit.** `RiskLimits(max_drawdown_pct=None, daily_loss_pct=0.05, ...)` enables only daily loss. This keeps the dataclass non-discriminated and avoids a separate `Optional[Limit]` wrapper per field. The `check_*` function for a `None`-configured limit returns `None` immediately.

3. **`check_*` are pure functions, not methods on `RiskLimits`.** Pure function over `(state, equity, ts, limits)` keeps testability trivial — feed any state, get a deterministic answer. Mirrors the design in `algo_bot.metrics` (ADR-007 §1 "Input shape").

4. **`RiskState` is immutable.** `update_state` returns a new instance. Caller (backtester wrapper) holds the latest state in a local variable. No global state, no class-level mutable singletons. Mirrors the frozen `MetricsSummary` design.

5. **Daily reset is timezone-aware.** `daily_reset_tz` (default `"UTC"`, configurable to e.g. `"Europe/Warsaw"`) determines when a day boundary triggers a daily-loss reset. `update_state` compares `ts.tz_convert(daily_reset_tz).normalize().tz_localize(None)` with `state.daily_start_day`. If they differ, the new state carries the current equity as `daily_start_equity` and the new day as `daily_start_day`. Crypto markets are 24/7 so UTC is the industry default (Binance, Bybit), but Janek lives in Europe/Warsaw and may want to align reports — configurable is essentially free.

6. **`check_all` first-hit ordering.** When multiple limits breach on the same bar, the order is `drawdown → daily_loss → max_positions`. Rationale: max DD is the most-existential safety net (a hard stop on total exposure) and should be reported first when both fire on the same bar. The order is deterministic and documented in the docstring — no surprises.

7. **`RiskBreach` is data, `RiskLimitBreached` is control flow.** The pure functions return `RiskBreach | None` — composable, no side effects, easy to test. Only the backtester wrapper translates a breach into the `RiskLimitBreached` exception that halts the engine. This separation keeps the library usable from non-engine contexts (live trader, future paper trading, walk-forward fold inspection) where halt semantics may differ.

8. **Position sizing — caller-driven, NOT auto-injection.** `position_size(equity, sl_distance, risk_per_trade_pct) -> float` is a pure helper. A strategy that wants risk-based sizing calls it inside its `on_bar` and populates `Signal.size` itself. The backtester wrapper does **not** inspect `sig.sl_pct` to auto-compute size. Rationale (Janek's call, 2026-05-24 session):
   - Not every strategy emits an SL before entry (e.g. mean-reversion with signal-driven exits). Auto-injection would silently misbehave for them.
   - `Signal` has `sl_pct` (relative) and `meta["sl"]` (absolute price) but no `sl_price` field — the inference path is fragile.
   - Existing strategies (bghtrend_pullback) already do their own sizing; auto-injection would create ambiguous priority.
   - Caller-driven sizing is the textbook contract; auto-injection is convenience that can be added later (e.g. via a `RiskAwareStrategy` mixin) once we have a concrete use-case from walk-forward.

   `position_size` returns `max((equity * risk_pct) / sl_distance, 0.0)`. Zero-or-negative `sl_distance` returns `0.0` with a `logger.warning("position_size: non-positive sl_distance — returning 0")`.

9. **Backtester hook location.** Risk gating is wired into `algo_bot.engine.backtester.make_bt_wrapper` — the `Wrapped(BTStrategy).next()` method. Specifically:
   - `make_bt_wrapper(StratClass, params_obj, risk_limits: RiskLimits | None = None)` takes an optional `risk_limits` argument.
   - In `Wrapped.init`, when `risk_limits` is not None, the wrapper initialises `self._risk_state = init_state(equity_start=self.equity, ts=<first bar timestamp>, limits=risk_limits)`.
   - At the top of `Wrapped.next()`, before `sig = self._algo.on_bar(df)`, the wrapper updates state and runs `check_all`. On a breach: force-close any open position at the last close price (so the trades log includes the exit), then `raise RiskLimitBreached(breach)`.
   - `run_backtest` catches `RiskLimitBreached`, extracts the breach, marks `stats["_risk_breach"] = asdict(breach)` and returns normally with `(stats, equity, trades)`. The caller sees a clean halt rather than an uncaught exception.

10. **Backward compatibility.** `risk_limits` defaults to `None` on every layer (`make_bt_wrapper`, `run_backtest`, CLI). Existing callers that don't pass a limit see identical behaviour to before this ADR. The new path is opt-in.

11. **`run_backtest` data injection (`data=` parameter).** As a side-effect of this ADR — required for the prerequisite `tests/test_backtest.py` repair and for Decision F prep — `run_backtest` accepts an optional `data: pd.DataFrame | None = None`. When provided, it bypasses `load_ohlcv_csv` and uses the injected frame directly (validated for `Open/High/Low/Close/Volume` columns and `DatetimeIndex`). The `symbol`/`timeframe` arguments remain for run_id, microstructure, and `save_outputs` metadata. This is a narrow, backward-compatible extension; documented here so it doesn't surprise a reader who expected only risk-module changes.

12. **`MetricsSummary` embedded in `save_outputs` (post-ADR-007 follow-up).** `save_outputs` now computes `algo_bot.metrics.summarize(equity, trades)` and writes `stats["_metrics_summary"] = asdict(summary)` to `summary.json` (with `NaN → None` substitution for JSON friendliness). Done in this ADR's session because the engine file is being touched anyway; closes the ADR-007 Notes follow-up.

13. **CLI flags.** `algo-backtest` gains two optional flags: `--max_dd_pct` and `--daily_loss_pct`. When set, the CLI assembles a `RiskLimits` instance and passes it through. Omitting both keeps backward compatibility. `--risk_per_trade_pct` is configurable but not auto-injected (per §8); it surfaces as part of `stats["_risk_limits"]` for downstream consumers.

14. **mypy strict-on-new.** `algo_bot.risk.*` is already declared in `[[tool.mypy.overrides]]` in `pyproject.toml` (line 171) with `disallow_untyped_defs = true`. Verified — no pyproject changes needed in this session.

15. **Logging.** `algo_bot.risk.limits` uses `from algo_bot.log import get_logger` (ADR-006). Edge cases emit `logger.warning`: non-positive `sl_distance` in `position_size`, unknown timezone string in `RiskLimits.daily_reset_tz` (caught at first `update_state` call). Breach detection itself emits `logger.warning("Risk limit breached", extra={...})` from the backtester wrapper (not from the pure check function — keep purity).

16. **Docstrings.** Per `feedback_engineering_mindset` rule #5: docstrings in Polish, public API names in English. Google style.

## Consequences

**Positive:**

- **Phase 1 success criterion ("Risk module zatrzyma backtest gdy drawdown przekroczy próg") is fully satisfied.** The integration test in `tests/test_risk_limits.py` drives a synthetic equity sequence that triggers a max-DD breach mid-run and asserts that `run_backtest` returns with `stats["_risk_breach"]` populated and forced-exit reflected in the trades DataFrame.
- **Clean separation of pure logic from engine semantics.** `check_*` and `position_size` are testable as pure functions over numeric inputs; `RiskLimitBreached` exception handling lives where it belongs (the engine adapter). Live trading, paper trading, and walk-forward can reuse the pure layer with their own halt semantics.
- **Two responsibilities cleanly split.** Gates (portfolio safety) vs sizing (per-trade math) have different signatures and different fire points. Future readers don't have to untangle a god-object `RiskManager.check()`.
- **`tests/test_backtest.py` unstuck.** The prerequisite repair lands here; the test no longer skips. The `data=` parameter doubles as a walk-forward hook for Decision F, so the cost is amortised across two future deliverables.
- **`MetricsSummary` flows into `summary.json`.** Post-ADR-007 follow-up closed — `summarize()` is now naturally consumed by the engine, no documentation drift between ADR-007 ("the engine should embed this") and reality.

**Negative / costs:**

- **Backtester adapter complexity ticks up.** `make_bt_wrapper` already had non-trivial branching (TP/SL/trail/cooldown/pyramiding); adding a risk gate at the top of `next()` is one more concern. Mitigation: the risk path is a small, well-commented prologue with an early-raise on breach. The rest of `next()` is unchanged.
- **`Signal.sl_pct` ↔ `position_size` boundary is not enforced.** Strategies that want risk-based sizing must call `position_size` themselves and pass the result via `Signal.size`. A strategy that forgets to call it silently uses backtesting.py's default sizing. Documented in `risk-limits.md` and in `concepts/risk-management.md`; revisited when Decision F shows whether walk-forward needs a consistent-sizing harness.
- **`daily_reset_tz` is a string, not a `ZoneInfo`.** A typo (e.g. `"Eu/Warsaw"`) is caught at first `update_state` call, not at `RiskLimits` construction. Mitigation: validation runs in `init_state` (first call) and emits a clear `logger.error` + raises `ValueError`. Acceptable trade-off for the dataclass remaining trivially serialisable.
- **First-hit ordering hides simultaneous breaches.** If both DD and daily loss trigger on the same bar, only the DD breach is reported. The other limit is still active on the next run — but for a halted run, the second breach is informational, not actionable. Documented in `risk-limits.md`.

**Risks:**

- **`self.equity` semantics in backtesting.py.** Our DD check uses `self.equity` (cash + mark-to-market). Library behaviour at exact bar boundaries when a TP/SL hit happens intrabar is documented as "computed after order execution" — but library docs are thin. Mitigation: integration test asserts that a DD breach mid-run produces a sensible equity/trades output; if a future bump of `backtesting.py` shifts semantics, the test catches it.
- **Daily reset TZ at session boundaries.** A bar whose timestamp is exactly midnight in the reset TZ counts as "new day". This means the bar's PnL is treated as belonging to the new day. Acceptable — matches industry convention (Binance funding settlement uses the same rule).
- **`position_size` returning 0.0 on bad input.** A strategy that calls `position_size` with a bug-y `sl_distance` gets size=0 (no position opened), logs a warning. Mitigation: warning is loud, downstream test catches "no trades" silence. Long-term: a stricter mode that raises instead of warning may be worth a follow-up.

## Alternatives Considered

- **Single `RiskManager` class with `update(bar)` + `should_halt()` interface.** Object-oriented stateful manager; backtester calls `manager.update(equity, ts)` per bar, then `if manager.should_halt(): break`. Rejected: hides the limit-by-limit decomposition in state mutations, makes pure unit testing awkward (need to drive the manager through a sequence to test a single limit), and conflates the "is this limit breached" question with "should the engine stop" — which are different concerns (the second is engine policy).

- **Auto-inject `position_size` in the backtester wrapper.** When `sig.sl_pct` is not None and `risk_limits.risk_per_trade_pct` is set and `sig.size` is None, wrapper would overwrite `sig.size` with `position_size(...)`. Rejected (Janek's call, 2026-05-24): not all strategies emit SL before entry; existing strategies do their own sizing and would have ambiguous priority; `Signal.sl_pct` is relative and the conversion to absolute distance is fragile. Re-evaluate when Decision F has a concrete walk-forward use-case.

- **Raise `RiskLimitBreached` directly from the pure `check_*` functions.** Pure becomes side-effectful; impossible to use the library outside the engine context (e.g. walk-forward fold inspection that wants to *count* breaches per fold, not halt). Rejected — return `RiskBreach | None` data, translate to exception only at the engine boundary.

- **Boolean + side-effect halt (e.g. set `self.bt._stop = True`).** Closest to what some `backtesting.py` examples do. Rejected: `backtesting.py` doesn't expose a clean public halt; we'd be monkey-patching internals. Raising an exception is the documented Python way.

- **Per-position SL inside risk module.** Cap each trade's max loss to `risk_per_trade_pct * equity` regardless of strategy-declared SL. Rejected (per §1): strategy scope. Risk module is portfolio-level. Strategies that want per-position caps express them via `Signal.sl_pct`.

- **Defer the `data=` parameter to Decision F.** Smaller diff in this ADR, but then `tests/test_backtest.py` either stays skipped (carrying the prerequisite forward into F) or relies on a real CSV (introducing a dependency on data-on-disk for the test suite). Rejected: the parameter is a trivial addition, walk-forward already needs it, and unblocking the test now lets the risk-module integration test reuse the same fixture without a separate hack.

- **Configurable first-hit order or "report all breaches" mode.** Returning `list[RiskBreach]` from `check_all` instead of the first-hit. Rejected for MVP: one halt event per run is enough operationally; the marginal complexity of `list[RiskBreach]` everywhere isn't justified until walk-forward Tabulate-breaches-per-fold becomes a real use-case.

## References

- File (created in this session): `algo_bot/risk/__init__.py`
- File (created in this session): `algo_bot/risk/limits.py`
- File (created in this session): `tests/test_risk_limits.py`
- File (created in this session): `docs/reference/modules/risk-limits.md`
- File (created in this session): `docs/concepts/risk-management.md`
- File (modified in this session): `algo_bot/engine/backtester.py` — adds `data=` parameter, risk hook in `make_bt_wrapper`, `MetricsSummary` in `save_outputs`, CLI flags `--max_dd_pct` / `--daily_loss_pct`.
- File (modified in this session): `tests/test_backtest.py` — signature repaired, synthetic-OHLCV smoke test, integration test gated on CSV availability.
- Consumers (downstream, future):
  - `algo_bot/engine/walkforward.py` (Decision F) — will pass `data=` per fold and inspect `stats["_risk_breach"]` for fold-level halt accounting.
  - `algo_bot/live/binance.py` (Phase 3) — will reuse `check_*` with its own halt semantics (graceful shutdown + alert + manual restart), not exception-driven.
- Related ADRs:
  - ADR-002 (pyproject-hatchling-stack) — mypy strict-on-new policy for `algo_bot.risk.*`
  - ADR-003 (strategybase-signal-api) — `Signal` shape consumed in `position_size` discussion (§8)
  - ADR-005 (backtesting.py-mvp-engine) — `Wrapped(BTStrategy).next()` hook location
  - ADR-006 (logging-strategy) — `get_logger(__name__)` for breach warnings
  - ADR-007 (risk-adjusted-metrics) — consumed via `algo_bot.metrics.summarize` in `save_outputs`; `max_drawdown` semantics align with the gate

## Notes

- **`make check` validation.** `algo_bot.risk.*` mypy override (pyproject.toml line 171) is pre-existing — no `pyproject.toml` change in this session. Validation chain unchanged: `make lint` (ruff), `make typecheck` (mypy on `algo_bot.risk.limits` with full strict), `make test` (`tests/test_risk_limits.py` + repaired `tests/test_backtest.py`), `make check` rolls them together.

- **Live trading integration deferred.** Hook in `live/live_binance.py` is explicitly out of scope. Halt semantics for live are different (graceful shutdown of open orders, Telegram alert, manual restart switch — none of which match a thrown `RiskLimitBreached`). Will be a dedicated ADR alongside paper trading in Phase 3.

- **Concepts doc `docs/concepts/risk-management.md`** is intentionally thin in this session — Phase 1 deliverable per ROADMAP line 55. The production-grade variant `docs/concepts/risk-management-production.md` is planned for Phase 4 (line 168). The thin doc here just orients a reader to: what limits exist, where they're configured, how they fire, and where to read more.

- **Open follow-up — strategy migration to caller-driven sizing.** Strategies that want to honour `risk_per_trade_pct` need explicit code (`size = position_size(self.equity, sl_distance, self.p.risk_per_trade_pct)`). Currently no strategy does this. Folding it into bghtrend_pullback is a Phase 2 task once walk-forward shows whether consistent sizing across folds materially changes the result.

- **`stats["_risk_breach"]` JSON shape.** When a breach fires, `save_outputs` emits the dict form of `RiskBreach` (kind, value, threshold, ts, message). Empty/no breach → key absent. Downstream sweep aggregation (ROADMAP Phase 2) will need to handle both cases.
