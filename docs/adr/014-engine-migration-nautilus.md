# ADR-014: Engine migration to `nautilus_trader` — parallel coexistence with `backtesting.py`

- **Status:** Accepted
- **Date:** 2026-07-13
- **Project phase:** 2 (Research & Backtest MVP)
- **Authors:** Janek Płoński, Claude

## Context

Phase 2 has produced two negative verdicts. `bghtrend_pullback` was declared a no-go
([ADR-012](012-mvp-no-go-bghtrend.md), 2026-07-05): 0/150 post-microstructure configs
cleared the WF-eligibility filter. The mean-reversion pivot candidate,
`mean_reversion_bb_stoch`, was then swept in MR-Session 2 (2026-07-13) and failed
outright: **0/180 eligible, global best raw Sharpe −0.291, 169/180 post-cost equity
curves reach ≤ 0, best valid Sharpe_post −0.497**
(`results/experiments/mr_sweep_review.json`).

That second failure is narrower than it looks. `mean_reversion_bb_stoch` implements only
the **bare core** of the Mastermind MMS methodology (base position, constant notional).
MMS's *own* framing is that the bare core is expected to bleed in strong trends, and that
the protection — its actual claimed edge — is the deferred **position-sizing state
machine**: pyramiding (base x1 + up to two x1 add-ons, total capped at x2 / 3% equity
risk, add-on SL at the wick-pair extreme ≤ 1% move —
[mms/02](../references/mms/02-position-management-filters.md)) and sequential leverage
reduction (first full 2% SL → hard drop to x0.1 "scout" size → first TP restores x1 —
[mms/03](../references/mms/03-stop-loss-sequential.md)). A weak bare-core sweep therefore
does not falsify the methodology; it means we have not yet tested the layer that is
supposed to carry the edge (the MR-Session 2 caveat, recorded in the strategy reference).

Testing that layer needs an engine that can express a **state machine above single
positions**: multiple entry legs with independent stops, position sizing that depends on
the realized outcome of *previous* trades, and — for full MMS fidelity later —
multi-timeframe context (M5/M10 marking feeding H1 execution,
[mms/04](../references/mms/04-interval-marking.md)). `backtesting.py` is single-position by
construction. The current wrapper (`make_bt_wrapper` in `algo_bot/engine/backtester.py`)
does carry a partial pyramiding path (`allow_pyramiding` → `exclusive_orders=False` +
repeated `buy()/sell()`), so it is not literally true that "backtesting.py cannot pyramid".
The honest constraint is stronger: expressing per-leg wick-pair stops, netting-vs-hedging
add-on accounting, and across-trade sequential-leverage state inside `make_bt_wrapper`
means re-implementing an event-driven engine inside a wrapper — at which point we have
built a worse `nautilus_trader`. The strategy reference already flags this as an
engine-migration trigger under "Known limitations", and [ADR-005](005-backtesting-py-mvp-engine.md)'s
own footer named `nautilus_trader` as the primary post-MVP migration candidate. This ADR
activates that trigger.

The decision was framed in a pre-flight session with brain-Claude, which established four
anchors that this ADR formalizes. **(1)** The migration is justified *independently* of the
Mastermind rescue: `nautilus_trader` is an event-driven, backtest-and-live-unified,
multi-venue engine — a capability upgrade for any future event-driven candidate
(breakout+volume, funding arbitrage), not a one-off for MMS. **(2)** Migration is
**gradual and parallel**, not big-bang: `backtesting.py` stays; `nautilus_trader` is added
alongside. **(3)** Mastermind is treated as *inspiration, not scripture* — a real prop
track record with economically sensible theses we can keep drawing on for future
strategies, not a system we must resurrect at any cost. **(4)** The first user of
`nautilus_trader` is **`mean_reversion_bb_stoch` v2** with pyramiding + sequential leverage
(closes the mean-reversion loop, tests the full MMS system, and becomes the baseline
state-machine strategy for everything after it).

Per the project rule (*"decyzje architektoniczne PRZED implementacją"*), the eight
decisions below were aligned with options and trade-offs before any code. **This ADR is
architectural only — zero code changes in `algo_bot/`.** Implementation begins in
MR-Session 3 Beta.

## Decision

**Adopt `nautilus_trader` as the primary engine for event-driven and state-machine
strategies, coexisting in parallel with `backtesting.py`, which remains the legacy engine
for single-position, single-timeframe baselines. The two engines are bridged by a thin,
optional compatibility adapter; new strategies that need full control (pyramiding,
per-leg stops, multi-TF) are written natively against `nautilus_trader`. The engine
migration is complete when one full-featured strategy — `mean_reversion_bb_stoch` v2 —
runs end-to-end on `nautilus_trader` and reaches a full-MMS-system go/no-go verdict; the
existing baselines are never migrated.** The eight specific conventions:

### 1. Adapter layer — two tiers (compat shim + native), not a single wrapper

The `StrategyBase` contract `on_bar(df) -> Signal` is single-position, one-decision-per-bar
by construction; it structurally cannot express pyramiding or per-leg stops. The adapter
is therefore split:

- **Tier 1 — compatibility adapter** (`algo_bot/engine/nautilus_adapter.py`, to be built
  in Beta): subscribes to `nautilus_trader` bar-close events, feeds the growing bar frame
  to `StrategyBase.on_bar(df)`, and translates the returned `Signal` into
  `nautilus_trader` orders. Purpose: run existing `StrategyBase` strategies on the new
  engine, and — the highest-value use — **cross-engine equivalence testing** (prove
  `nautilus_trader` reproduces a `backtesting.py` baseline). Loose coupling, minimal
  surface: one file of event handlers calling the unchanged `StrategyBase` contract.
- **Tier 2 — native strategies**: subclass the `nautilus_trader` strategy base directly
  and use its event handlers. **`mean_reversion_bb_stoch` v2 is native** — it does *not*
  go through the Tier-1 adapter, because pyramiding + sequential leverage require the
  state machine the `on_bar` idiom cannot carry.

Consequence for planning: "minimal adapter layer" in Beta means the Tier-1 compat/
equivalence shim, **not** a host for v2. v2 is written natively.

### 2. Coexistence — dispatch by base class, `__engine__` opt-in, `--engine` override

The dispatch key is the strategy's base class, resolvable with `issubclass`: a native
`nautilus_trader` strategy can only run on `nautilus_trader`; a `raw backtesting.Strategy`
only on `backtesting.py`; a `StrategyBase` strategy runs on `backtesting.py` by default and
may opt into the Tier-1 adapter. For that last, ambiguous case a class attribute
`__engine__ = "nautilus"` declares the non-default choice. A CLI flag `--engine
{backtesting_py, nautilus}` on `algo-backtest` / `algo-sweep` / `algo-walkforward`
overrides the default — its main job is forcing cross-engine equivalence runs. No
file-location or YAML-only convention (both are implicit or redundant given the class
already carries the information).

### 3. Multi-TF data — defer M5, run the v2 PoC on H1

M5/M10 data is **not** fetched before Beta. The deferred edge's triggers are all
H1-native: add-on #1 fires on the close of the first confirming H1 candle, add-on #2 on the
H1 Stochastic 14/3/3 %K&%D cross, and sequential leverage is across-trade state
([mms/02](../references/mms/02-position-management-filters.md),
[mms/03](../references/mms/03-stop-loss-sequential.md)). M5/M10 marking
([mms/04](../references/mms/04-interval-marking.md)) governs only entry-timing precision
and wick-pair add-on stop placement — *fidelity*, not *capability*, and Beta already
proxies it with two H1 bars. Running the v2 PoC on H1 keeps a single experimental variable
(the sizing state machine), instead of confounding it with a simultaneous multi-TF
data-plumbing change. `nautilus_trader`'s native multi-timeframe support means M5 can be
added cheaply later if the PoC is promising and full MMS fidelity is wanted. Parked to-do
(non-blocking): confirm `algo-fetch` handles a 5m timeframe.

### 4. `StrategyBase` API — frozen, does not evolve for v2

`on_bar(df) -> Signal` and `precompute(df)` are unchanged. v2 is native (Decision 1) and
does not use `StrategyBase`, so multi-TF and state-machine expressiveness live in the
native `nautilus_trader` strategy, not in an extended `StrategyBase`. The alternative of
aggregating M5→H1 inside `precompute` (feeding a single H1 frame with sub-bar-derived
columns) is only relevant if a *future* `backtesting.py` strategy needs multi-TF — not
planned. Keeping `StrategyBase` frozen aligns with the backward-compat depth of Decision 8.

### 5. Adapter contract — return `(stats, equity, trades)` in the identical schema

Both `nautilus_trader` execution paths (native and Tier-1 adapter) surface results through
the existing `run_backtest` return contract: `(stats, equity, trades)` where `equity`
carries an `Equity` column (`pd.Series`, `DatetimeIndex`; plus `Equity_adjusted` when
microstructure runs), `trades` carries `EntryTime / ExitTime / EntryPrice / ExitPrice /
Size / PnL`, and `stats` is a dict with `_metrics_summary_raw` /
`_metrics_summary_post_microstructure`. This is exactly what `walkforward._run_single_fold`
and `sweep` unpack today, and what the [ADR-011](011-microstructure-adjustments.md) overlay
consumes — so WalkForward, sweep, and microstructure keep working with no changes at their
level.

**Caveat (an honest cost, not free "engine-agnosticism").** The ADR-011 overlay assumes
**single-position** trades (one entry/exit/size per trade). A **pyramided** position has
multiple entry legs at different times and prices; it does not map cleanly onto the
per-trade `TradeCost` model. For the v2 PoC we accept **approximate microstructure**
(slippage on the net position, funding on the netted exposure) and **defer exact
multi-leg costing** — a bounded overlay extension (per-leg slip; funding on the aggregate)
to be done alongside the pyramiding work in MR-Session 4/5. The ADR-011 post-hoc,
equity-curve, engine-agnostic overlay design is unchanged for single-position strategies.

### 6. Migration completion — one full-featured strategy end-to-end, staged

"Engine migration done" = `mean_reversion_bb_stoch` v2 (native) runs the full pipeline on
`nautilus_trader` and reaches a full-MMS-system go/no-go verdict. Staged, respecting the
Phase-2 gating discipline (never run the expensive robustness layer on a strategy that
has not shown in-sample edge — the bghtrend Session-4 / MR-Session-2 lesson):

1. **Beta:** env setup, Tier-1 compat adapter, v2 native implementation, and a
   **mini-benchmark sweep** (1 symbol × 1-2 years × 10-20 samples) as a direction check.
2. **MR-Session 4:** a **full v2 sweep** on `nautilus_trader` (6-symbol × full history) —
   run **unconditionally**, because it is the first real test of the *claimed* edge (the
   deferred sizing layer), not of the already-failed bare core.
3. **MR-Session 5:** WF → Monte Carlo → stress → ADR go/no-go, **gated** on the Session-4
   sweep showing in-sample eligibility (the expensive robustness layer only for a strategy
   that cleared the sweep).
4. **Adapter validation:** a cross-engine equivalence test on one legacy strategy (Tier-1
   adapter) — the deliverable that earns trust in `nautilus_trader`.
5. **CLI:** dual-engine support (Decision 2).

`bghtrend_pullback` and `mean_reversion_bb_stoch` v1 are **not** migrated (Decision 8).

### 7. Bailout criteria — time tripwires plus capability tripwires

- **Time:** if Beta spends > 8h on the adapter layer alone without visible progress on the
  strategy implementation → review scope; > 12h → consider a fallback.
- **Capability (checked earliest, cheapest to act on):** if `nautilus_trader` +
  `backtesting.py` + TA-Lib cannot resolve into one conda env without a version conflict →
  hard blocker, bail at Beta Task 0 **before** writing any adapter. If the cross-engine
  equivalence test cannot reach an acceptable tolerance on the *same* simple strategy →
  trust problem, stop and diagnose before building v2 on top.
- **Fallbacks, correctly ranked:** (a) a **custom minimal event loop** (full control,
  ~2-3 weeks) or (b) **abandon the MMS full system** and pivot to a Phase-2-pivot-list
  candidate (breakout+volume, funding arb) on `backtesting.py`. **`vectorbt` is *not* a
  fallback for pyramiding**: it is vectorized and structurally weak at path-dependent,
  across-trade state machines. `vectorbt` belongs to a *post-MVP sweep-speed* decision, not
  a state-machine rescue.

### 8. Backward-compat depth — legacy frozen on `backtesting.py` forever

`bghtrend_pullback` and `mean_reversion_bb_stoch` v1 stay on `backtesting.py` as historical
baselines and framework validators; they carry no ROI from migration.
`algo_bot/engine/backtester.py` (the `backtesting.py` wrapper) is **sacred infrastructure**
— minimal maintenance, not deprecated. `nautilus_trader` is for forward-only work. The
Tier-1 compat adapter may re-run a legacy strategy on `nautilus_trader` for equivalence
testing; that is additive and does not touch the `backtesting.py` path, which stays the
source of truth for the baselines.

## Consequences

**Positive:**

- **Native state machine.** Pyramiding, per-leg stops, and across-trade sequential leverage
  become first-class instead of hacks inside a wrapper — the whole reason MMS's claimed
  edge can finally be tested.
- **Multi-TF and portfolio native.** M5/M10→H1 and multi-asset are supported by the engine,
  not reconstructed in `precompute`; opens the door to portfolio strategies post-MVP.
- **Backtest–live parity.** `nautilus_trader` runs the same strategy in backtest and live,
  which directly de-risks Phase 3 (the current `live/live_binance.py` is a second, separate
  engine — a known source of backtest/live drift).
- **Forward capability, not a one-off.** Justified independently of MMS: any future
  event-driven candidate inherits the engine.
- **Legacy untouched.** Baselines and the framework's validation story stay exactly as they
  are; the negative results remain reproducible.

**Negative / costs:**

- **Learning curve.** ~2-3 days for the first native strategy (actor/message-bus model,
  event handlers, order/position lifecycle).
- **Adapter maintenance.** The Tier-1 compat layer plus dual-engine CLI is ongoing surface
  area to keep working as either engine evolves.
- **Microstructure overlay extension.** Multi-leg (pyramided) trades need a bounded
  extension to the ADR-011 overlay (Decision 5); until then, PoC costing is approximate.
- **Extra dependencies.** `nautilus_trader` and its transitive deps are a strategic (not
  casual) addition; they must coexist with `backtesting.py` and conda-forge TA-Lib without
  a version conflict — a real risk (see below), verified at Beta Task 0.

**Risks:**

- **Environment dependency conflict.** `nautilus_trader` vs TA-Lib (conda-forge) vs
  `backtesting.py` version resolution is the single biggest unknown; it is a hard bailout
  tripwire (Decision 7) checked before any adapter work, on both the WSL box and the VPS.
- **`nautilus_trader` API pitfalls not surfaced by the docs.** Where an adapter-layer
  decision needs deeper investigation than the docs allow, it is parked as a Beta TODO
  rather than blocking this ADR (the pragmatism rule).
- **Equivalence tolerance.** `nautilus_trader` and `backtesting.py` may fill/settle subtly
  differently (open vs close, fee model); the equivalence test defines the acceptable
  tolerance and is a trust gate, not a formality.
- **Over-investment if v2 also fails.** The bailout criteria (Decision 7) bound the effort;
  a failed v2 formalizes the implemented MR-line no-go and pivots — it does not loop.
- **`nautilus_trader` is relatively young and opinionated.** Fewer tutorials than mature
  engines; the API mismatch against our `StrategyBase` idiom is precisely why the adapter
  is two-tier (Decision 1) rather than a forced one-to-one mapping.

## Alternatives Considered

- **Engine: `vectorbt`.** Raw vectorized speed, multi-asset, great for large sweeps.
  Rejected as the primary engine because it has no state machine — path-dependent
  across-trade sizing (sequential leverage) and per-leg pyramiding are exactly what a
  vectorized framework handles badly. Retained as a *post-MVP sweep-speed* candidate, not a
  pyramiding solution.
- **Engine: `backtrader`.** Mature, multi-asset, event-driven, production-ish live.
  Rejected: ~10× the code surface of `backtesting.py`, a more complex API, and a community
  that has gone quiet (last release 2023) — a neglected dependency is a liability for a
  single-developer project.
- **Engine: custom event loop.** Full control, no dependency risk. Rejected as
  disproportionate now (~2-3 weeks to reach parity with what `nautilus_trader` gives out of
  the box); retained as the primary *bailout* fallback if `nautilus_trader` proves
  unworkable (Decision 7).
- **Engine: Lean (QuantConnect).** Production-grade, but a C# core with Python bindings,
  heavy setup, awkward VPS deployment. Rejected as scope creep for this project.
- **Adapter design: abstract `StrategyBase` base + two concrete backends** (a
  `BacktestingPyStrategy` and a `NautilusStrategy` subclass). Rejected: couples
  `StrategyBase` to both engines, and still cannot let `on_bar` express pyramiding — the
  two-tier split (Decision 1) keeps `StrategyBase` frozen and puts state-machine logic where
  it belongs (native strategies).
- **Adapter design: full new API / rewrite everything on `nautilus_trader`.** Rejected:
  breaks backward compatibility, forces rewrites of `bghtrend` and v1 for zero ROI, and
  discards the baselines' reproducibility (contradicts Decision 8).
- **Big-bang migration.** Rejected in the pre-flight: parallel/gradual coexistence lets the
  baselines stay put and bounds the risk to one new strategy at a time.

## References

- Supersedes the migration note in [ADR-005](005-backtesting-py-mvp-engine.md) (which named
  `nautilus_trader` as the primary post-MVP candidate and set the migration triggers — now
  activated). `backtesting.py` itself is retained per Decision 8.
- [ADR-009](009-walk-forward.md) — WalkForward calls `run_backtest` per fold; the adapter
  preserves that contract (Decision 5).
- [ADR-011](011-microstructure-adjustments.md) — post-hoc equity-curve overlay; engine-
  agnostic for single-position, needs a multi-leg extension for pyramiding (Decision 5).
- [ADR-012](012-mvp-no-go-bghtrend.md), [ADR-013](013-wf-eligibility-thresholds.md) — the
  Phase-2 pivot precedent and the eligibility gate reused in Decision 6.
- [strategy-mean-reversion-bb-stoch.md](../reference/modules/strategy-mean-reversion-bb-stoch.md)
  — "Known limitations" flags the engine-migration trigger; the alignment table cites the
  MMS extractions for pyramiding + multi-TF.
- MMS prior: [mms/02](../references/mms/02-position-management-filters.md) (pyramiding),
  [mms/03](../references/mms/03-stop-loss-sequential.md) (sequential leverage x1↔x0.1),
  [mms/04](../references/mms/04-interval-marking.md) (M5/M10→H1 multi-TF).
- Evidence of record: `results/experiments/mr_sweep_review.json` (MR-Session 2 bare-core
  negative).
- Concept doc (user-facing overview): `docs/concepts/engine-migration-strategy.md`.
- External: `nautilus_trader` documentation — <https://nautilustrader.io/docs/latest/>.

## Notes

- **Zero code in this session.** Files named for Beta (`algo_bot/engine/nautilus_adapter.py`,
  the native v2 strategy, dual-engine CLI wiring) are named for orientation, not created here.
- **Sequential-leverage numbers corrected vs the kickoff.** The kickoff sketched a ladder
  `x1 → x0.5 → x0.25 → x0.1`; MMS ([mms/03](../references/mms/03-stop-loss-sequential.md)) is
  a **binary** switch: first full 2% SL → x0.1 scout, first TP → back to x1. Pyramiding
  ([mms/02](../references/mms/02-position-management-filters.md)) is base x1 + up to two x1
  add-ons, total cap x2 / 3% equity. These are the numbers Beta implements.
- **mypy strict-on-new.** The Beta adapter (`algo_bot/engine/nautilus_adapter.py`) and the
  native v2 strategy go on the `pyproject.toml` strict override list, per project convention.
- **Status `Accepted`** — the eight decisions were signed off in-session with Janek (M5
  deferred per Decision 3; unconditional full v2 sweep at MR-Session 4 per Decision 6).
  Implementation lands in MR-Session 3 Beta; `make check` remains the operator's WSL gate.
