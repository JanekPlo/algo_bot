# Engine migration strategy

> **Status: DRAFT** (MR-Session 3 Alpha, 2026-07-13). User-facing orientation for the
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
existing baselines — `bghtrend_pullback` and `mean_reversion_bb_stoch` v1 — remain on
`backtesting.py` **forever**, as historical baselines and as validators of the framework
itself. `algo_bot/engine/backtester.py` is treated as sacred infrastructure: minimal
maintenance, not deprecated. There is no ROI in rewriting a strategy whose negative result
is already recorded and reproducible.

The `StrategyBase` contract (`on_bar(df) -> Signal`) is frozen. It stays the simple,
single-position idiom for legacy and lightweight strategies. All the new expressiveness —
state machine, multi-timeframe, multiple legs — lives in strategies written *natively*
against `nautilus_trader`, not in an extended `StrategyBase`.

## How the two engines coexist

Think of it as two lanes bridged by an optional adapter.

- **Native lane.** A strategy that needs full control subclasses the `nautilus_trader`
  strategy base directly and uses its event handlers. `mean_reversion_bb_stoch` v2 lives
  here — pyramiding and sequential leverage need the state machine that the `on_bar` idiom
  cannot carry.
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
`(stats, equity, trades)` shape that `run_backtest` returns today, so walk-forward, sweep,
and the microstructure overlay keep working unchanged. The one honest gap: the
microstructure overlay ([ADR-011](../adr/011-microstructure-adjustments.md)) assumes
single-position trades; pyramided (multi-leg) positions need a bounded overlay extension,
done later alongside the pyramiding work. Until then the PoC uses approximate costing on the
net position.

## Migration timeline (milestones)

The migration follows the framework's standing gating discipline: never spend the expensive
robustness budget on a strategy that has not first shown in-sample edge.

1. **MR-Session 3 Alpha (this session, done):** ADR-014 + this concept doc + ROADMAP.
   Zero code.
2. **MR-Session 3 Beta:** set up the `nautilus_trader` environment (the make-or-break check
   is that `nautilus_trader` + `backtesting.py` + TA-Lib coexist in one conda env); build the
   Tier-1 compat adapter; implement `mean_reversion_bb_stoch` v2 natively (base entry +
   pyramiding + binary sequential leverage x1↔x0.1 per MMS); run a small direction-check
   sweep. This session runs entirely on H1 — M5/M10 data is deferred (the deferred edge's
   triggers are H1-native; M5 is fidelity, not capability).
3. **MR-Session 4:** a full v2 sweep on `nautilus_trader`, run unconditionally — it is the
   first real test of the *claimed* edge, not the already-failed bare core.
4. **MR-Session 5:** walk-forward → Monte Carlo → stress → go/no-go, gated on the Session-4
   sweep clearing in-sample eligibility. This is the full-MMS-system verdict, the analogue
   of the bghtrend Session-8 decision.

**Bailout is explicit.** If the environment cannot be made to coexist, or the equivalence
test cannot reach an acceptable tolerance, or the adapter consumes disproportionate effort
with no strategy progress, the migration stops and we either build a minimal custom event
loop or abandon the MMS full system and pivot. (`vectorbt` is not a fallback here — it is
fast but vectorized, and cannot express the across-trade state machine; it belongs to a
future sweep-speed decision.)

## See also

- [ADR-014](../adr/014-engine-migration-nautilus.md) — the decision record (eight decisions,
  alternatives, consequences).
- [ADR-005](../adr/005-backtesting-py-mvp-engine.md) — why `backtesting.py` was chosen, and
  the migration triggers this activates.
- [strategy-mean-reversion-bb-stoch.md](../reference/modules/strategy-mean-reversion-bb-stoch.md)
  — the candidate; "Known limitations" flags the engine-migration trigger.
- [`docs/references/mms/`](../references/mms/README.md) — the Mastermind prior: pyramiding
  (02), sequential leverage (03), interval marking (04).
