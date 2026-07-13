# Engine migration strategy

> **Status: ACTIVE — Beta complete, `ITERATE BETA`** (2026-07-14). User-facing orientation for the
> `backtesting.py` → `nautilus_trader` migration. The authoritative decision record is
> [ADR-014](../adr/014-engine-migration-nautilus.md); this document is the mental model and
> is expanded in later sessions as the migration proceeds.

## Why we are migrating

For its whole life so far the framework has run on `backtesting.py` — a small,
single-position, single-timeframe engine that has served Phase 1 and most of Phase 2 well
([ADR-005](../adr/005-backtesting-py-mvp-engine.md)). Two things made a second engine worth
the cost now.

The first is the shape of the Mastermind MMS methodology. Its claimed edge is not the entry
signal — it is a **position-sizing state machine**: scaling into a base position
(pyramiding) and cutting leverage hard after a loss, then restoring it after a win
(sequential leverage). That is logic *above* individual trades, with state that depends on
the outcomes of previous trades. `backtesting.py` is single-position by construction; you
can bolt partial pyramiding onto its wrapper, but expressing per-leg stops, netting-vs-
hedging add-on accounting, and across-trade leverage state means re-writing an event-driven
engine inside a wrapper. At that point you have built a worse version of a tool that already
exists.

The second is forward-looking. `nautilus_trader` is an event-driven, multi-venue engine
that runs the *same* strategy in backtest and live. That is a capability upgrade for any
future event-driven candidate (breakout+volume, funding arbitrage), and it directly
de-risks Phase 3, where today a separate live runner (`live/live_binance.py`) is a known
source of backtest/live drift. So the migration is justified **independently** of whether
the Mastermind rescue works — MMS is the first user, not the sole justification.

The bare-core mean-reversion sweep (MR-Session 2) failed, but that failure tested only the
base position, not the sizing layer that is supposed to carry the edge. The migration is
what lets us finally test the real claim.

## What is *not* changing

The migration is **parallel and gradual, never big-bang**. `backtesting.py` stays. The
existing baselines — `bghtrend_pullback` and `mean_reversion_bb_stoch` v1 — stay on
`backtesting.py`, which **remains supported throughout the migration** and is retained as a
**pinned legacy baseline**. There is no ROI in rewriting a strategy whose negative result is
already recorded. Their reproducibility is guaranteed by pinning (a git tag, a lockfile, a
results snapshot, test fixtures, optionally a container) rather than by a promise to
maintain the old runtime forever — if `nautilus_trader` is the engine for the next five
years, keeping two live runtimes indefinitely would be an odd commitment. Retiring the
`backtesting.py` runtime is a *future, separate* decision (its own ADR), not something this
migration forces.

The `StrategyBase` contract (`on_bar(df) -> Signal`) is frozen. It stays the simple,
single-position idiom for legacy and lightweight strategies. All the new expressiveness —
state machine, multi-timeframe, multiple legs — lives in strategies written *natively*
against `nautilus_trader`, not in an extended `StrategyBase`.

## How the two engines coexist

Think of it as two lanes bridged by an optional adapter.

- **Native lane.** A strategy that needs full control runs natively — but "native" is about
  *runtime*, not about welding the logic to the library. `mean_reversion_bb_stoch` v2 lives
  here as a **pure `MastermindStateMachine`** (no `nautilus_trader` imports; plain events in,
  plain order intents out, unit-tested with ordinary `pytest`) wrapped by a **thin
  `NautilusMastermindStrategy`** that maps events to intents to real orders. Pyramiding and
  sequential leverage need the state machine the `on_bar` idiom cannot carry; keeping that
  machine library-agnostic means a `nautilus_trader` breaking change lands on the thin
  wrapper, not the edge logic (a deliberate choice over a 5-year horizon).
- **Compatibility lane (Tier-1 adapter).** A thin adapter
  (`algo_bot/engine/nautilus_adapter.py`, built in Beta) lets an existing `StrategyBase`
  strategy run on `nautilus_trader`: it feeds bar-close events to `on_bar(df)` and
  translates the returned `Signal` into `nautilus_trader` orders. Its most valuable job is
  **cross-engine equivalence testing** — proving `nautilus_trader` reproduces a
  `backtesting.py` baseline, which is how we come to trust the new engine.

Which engine runs is decided by the strategy's base class (a native `nautilus_trader`
strategy can only run on `nautilus_trader`; a `StrategyBase` strategy defaults to
`backtesting.py`). A `StrategyBase` strategy opts into the adapter with an `__engine__ =
"nautilus"` class attribute, and a `--engine {backtesting_py, nautilus}` CLI flag can force
either engine for equivalence runs.

Downstream tooling does not move. Both `nautilus_trader` paths return the same
`(stats, equity, trades)` *format* that `run_backtest` returns today, so walk-forward and
sweep keep working unchanged. What is **not** shared is the cost *method*: the legacy
`backtesting.py` path keeps the post-hoc [ADR-011](../adr/011-microstructure-adjustments.md)
overlay, while the native-v2 PyO3 path takes fills, commissions and perpetual-funding
adjustments from the engine. The Cython engine used by Tier-1 equivalence does not settle
funding in 1.230.0, so it is not a cost-eligible substitute. Even PyO3 stays ineligible when
mark-price/order-book history or a declared fee/fill model is missing: "native" is an
accounting property, not proof of realism. Behind the tuple, the source result also carries
`orders`, `fills`, `positions`, cost metadata and an `engine_version` for auditability.

## Migration timeline (milestones)

The migration follows the framework's standing gating discipline: never spend the expensive
robustness budget on a strategy that has not first shown in-sample edge.

1. **MR-Session 3 Alpha (this session, done):** ADR-014 + this concept doc + ROADMAP.
   Zero code.
2. **MR-Session 3 Beta (completed):** Beta 0 runs on vanilla CPython 3.12.13
   with `uv==0.11.28`, `nautilus_trader==1.230.0`, `TA-Lib==0.7.0`,
   `backtesting==0.6.5`, and a committed `uv.lock`; full `make check` is green. Then: the
   executable v2 specification, timestamp/execution and OMS PoCs, Tier-1 compat adapter with a
   cross-engine equivalence test, and `mean_reversion_bb_stoch` v2 as a pure
   `MastermindStateMachine` + thin native wrapper — base entry + pyramiding (base x1 + **one**
   add-on x1 = x2, fired by **either** the confirming candle **or** the Stochastic cross) +
   binary sequential leverage (x1 → x0.1 scout after a full SL → x1 on the next TP). Position
   model: virtual base/add-on legs over a NETTING venue position with reduce-only stops,
   validated in the PoC. A small direction-check sweep closes the session. Runs entirely on
   H1 — M5/M10 deferred (the deferred edge's triggers are H1-native; M5 is fidelity, not
   capability). The frozen development-only P9 matrix completed 12/12 runs and 264/264
   invariant checks without reading holdout. Every result remains
   `SMOKE_ONLY / NOT_ELIGIBLE`; see the
   [P9 report](../experiments/mms-v2-beta-results.md). The resulting decision is
   **iterate Beta**, not bailout and not variant selection.
3. **MR-Session 4:** a full v2 sweep on `nautilus_trader`, conditional on a new
   preregistration after Binance Close-All parity, mark-price/fill/cost evidence, M5/M10
   fidelity and the intended multi-instrument scope are resolved. It must not inherit the
   earlier unconditional schedule from a non-eligible smoke profile.
4. **MR-Session 5:** walk-forward → Monte Carlo → stress → go/no-go, gated on the Session-4
   sweep clearing in-sample eligibility. This is the full-MMS-system verdict, the analogue
   of the bghtrend Session-8 decision.

**Bailout is explicit.** The runtime gate resolved cleanly with `uv`; Conda/container
fallbacks were not needed. Beyond that, if the equivalence test cannot reach
an acceptable tolerance, or the adapter consumes disproportionate effort with no strategy
progress, the migration stops and we either build a minimal custom event loop or abandon the
MMS full system and pivot. `vectorbt` is not the fallback here: it *can* express an
across-trade state machine via callback order functions (`from_order_func`), but that is
much less readable and — decisively — it has no live-execution path, so it does not give the
backtest-to-live parity we are migrating for. It stays a future sweep-speed candidate.

## See also

- [ADR-014](../adr/014-engine-migration-nautilus.md) — the decision record (nine decisions,
  alternatives, consequences).
- [ADR-005](../adr/005-backtesting-py-mvp-engine.md) — why `backtesting.py` was chosen, and
  the migration triggers this activates.
- [strategy-mean-reversion-bb-stoch.md](../reference/modules/strategy-mean-reversion-bb-stoch.md)
  — the candidate; "Known limitations" flags the engine-migration trigger.
- [`docs/references/mms/`](../references/mms/README.md) — the Mastermind prior: pyramiding
  (02), sequential leverage (03), interval marking (04).
