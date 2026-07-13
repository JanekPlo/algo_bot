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
machine**: pyramiding (base x1 + **one** add-on x1 — fired by *either* a confirming candle
*or* a Stochastic cross — for x2 total / 3% equity risk, add-on SL at the wick-pair extreme
≤ 1% move — [mms/02](../references/mms/02-position-management-filters.md)) and sequential
leverage reduction (first full 2% SL → hard drop to x0.1 "scout" size → first TP restores x1 —
[mms/03](../references/mms/03-stop-loss-sequential.md)). A weak bare-core sweep therefore
does not falsify the methodology; it means we have not yet tested the layer that is
supposed to carry the edge (the MR-Session 2 caveat, recorded in the strategy reference).

Testing that layer needs an engine that can express a **state machine above single
positions**: multiple entry legs with independent stops, position sizing that depends on
the realized outcome of *previous* trades, and — for full MMS fidelity later —
multi-timeframe context (M5/M10 marking feeding H1 execution,
[mms/04](../references/mms/04-interval-marking.md)). The constraint is **not** that
`backtesting.py` is absolutely incapable — the library itself can hold multiple trades with
independent SL/TP, tags, and partial closes, and our `make_bt_wrapper` even carries a
partial pyramiding path (`allow_pyramiding` → `exclusive_orders=False` + repeated
`buy()/sell()`). The honest constraint is that **our current `StrategyBase` +
`make_bt_wrapper` adapter exposes aggregate single-position semantics**, and extending it to
the required live-equivalent order lifecycle (per-leg wick-pair stops, netting-vs-hedging
add-on accounting, across-trade sequential-leverage state) would be disproportionate — at
that point we would be re-implementing an event-driven engine inside a wrapper, i.e.
building a worse `nautilus_trader`. The strategy reference already flags this as an
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

One runtime fact reshapes the risk profile and is a hard prerequisite. The repo runs on
**Python 3.11**; `nautilus_trader` requires **Python ≥ 3.12** (per its installation docs),
officially recommends **vanilla CPython + `uv`**, treats **conda as not officially
supported**, and warns of possible breaking changes between versions. So "check whether
`nautilus_trader` + `backtesting.py` + TA-Lib coexist in the conda env" understates it: the
current 3.11 env will not run the current `nautilus_trader` at all. This is a **runtime
migration**, not merely a dependency-resolution check, and it is *not* on its own a reason
to build a custom engine — the first response to an env conflict is to migrate the runtime
(or use a separate env / container), never to jump to a bespoke event loop. It is scoped as
an explicit **Beta 0** step ahead of the adapter (Decision 6/7). The exact `nautilus_trader`
version, and the conda-3.12-vs-`uv` runtime choice, are pinned empirically in Beta 0, not
guessed here.

Per the project rule (*"decyzje architektoniczne PRZED implementacją"*), the nine
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
existing baselines are retained as pinned legacy, not migrated.** The nine specific
conventions:

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
- **Tier 2 — native strategies, with the logic kept engine-agnostic.** "Native" is about
  *runtime*, not about binding the strategy's brain to the library. The MMS logic lives in
  a **pure `MastermindStateMachine`** with no `nautilus_trader` imports — it consumes plain
  events (`BarEvent` / `FillEvent` / `PositionClosed`) and emits plain intents
  (`OrderIntent` / `ReduceIntent` / `CancelIntent`). A **thin `NautilusMastermindStrategy`**
  adapter maps `nautilus_trader` events to those inputs and the intents to real orders. The
  state machine's transitions are tested with ordinary `pytest`; `nautilus_trader`
  integration tests only assert the resulting orders/fills. This layering is deliberate
  given the 5-year horizon and `nautilus_trader`'s own breaking-change warnings — a library
  bump should touch the thin adapter, not the strategy logic. `on_bar(df) -> Signal` cannot
  carry pyramiding + sequential leverage, so **v2 does not go through the Tier-1 adapter**.

Consequence for planning: "minimal adapter layer" in Beta means the Tier-1 compat/
equivalence shim, **not** a host for v2. v2's brain is the pure `MastermindStateMachine`;
the native `NautilusMastermindStrategy` is only its runtime wrapper.

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

### 5. Shared result **format**, engine-specific cost **method**; richer source result behind a compat facade

The contract that stays stable is the *format* `(stats, equity, trades)` — `equity` with an
`Equity` column (`pd.Series`, `DatetimeIndex`), `trades` with `EntryTime / ExitTime /
EntryPrice / ExitPrice / Size / PnL`, `stats` a dict with the `_metrics_summary_*` keys —
because that is what `walkforward._run_single_fold` and `sweep` unpack today. Both
`nautilus_trader` paths surface *this* facade so WalkForward and sweep keep working
unchanged.

**Cost accounting is not ported 1:1.** The two engines compute costs differently and that
is fine — only the output format is shared, not the method:

- **Legacy `backtesting.py` path:** keeps the [ADR-011](011-microstructure-adjustments.md)
  post-hoc overlay (slippage + funding on the equity curve). Unchanged.
- **`nautilus_trader` path:** costs come from the engine's **native fills, commissions,
  funding settlements, and execution/latency models** — historical funding can be settled
  directly inside the backtest. This is *more* accurate than a post-hoc overlay, and it
  handles multi-leg (pyramided) turnover natively — which matters because add-ons increase
  turnover and therefore cost. So we do **not** extend the single-position ADR-011
  `TradeCost` overlay onto the nautilus path; we let the engine cost the fills.

**Consequence:** approximate net-position costing is acceptable **only** for the Beta smoke
test; it must **not** be used for eligibility or the full sweep (Decision 6), precisely
because pyramiding inflates turnover and cost. MR-Session 4's sweep runs on native nautilus
costing.

**Richer source result behind the facade.** The tuple is a compatibility facade, not the
whole result — we do not discard the very data we migrated for. The native nautilus result
is a structured object carrying `orders`, `fills`, `positions`, `engine`, `engine_version`,
`data_hash`, and `config_hash` in addition to `stats / equity / trades`:

```python
@dataclass(frozen=True)
class BacktestResult:
    engine: str           # "nautilus" | "backtesting_py"
    engine_version: str   # pinned nautilus version (recorded per run — Decision 6)
    stats: dict[str, Any]
    equity: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame          # nautilus path; empty on legacy
    fills: pd.DataFrame           # nautilus path; empty on legacy
    positions: pd.DataFrame       # nautilus path; empty on legacy
    data_hash: str
    config_hash: str
```

Sweep / walk-forward keep consuming `stats / equity / trades`; debugging the pyramiding
state machine needs `orders` and `fills`, which the facade would otherwise throw away.

### 6. Migration completion — one full-featured strategy end-to-end, staged

"Engine migration done" = `mean_reversion_bb_stoch` v2 (native) runs the full pipeline on
`nautilus_trader` and reaches a full-MMS-system go/no-go verdict. Staged, respecting the
Phase-2 gating discipline (never run the expensive robustness layer on a strategy that
has not shown in-sample edge — the bghtrend Session-4 / MR-Session-2 lesson):

0. **Beta 0 — runtime migration (prerequisite, before any adapter):** bump the project to
   **Python 3.12**; select and **pin a specific stable `nautilus_trader` version**; decide
   **conda-3.12 vs the officially supported `uv`** runtime; run the full `make check` green
   on the new runtime (legacy `backtesting.py` + TA-Lib must still pass); start recording
   **`engine` + `engine_version`** in every backtest result (Decision 5). This step is a
   hard gate — no adapter work until it is green (Decision 7).
1. **Beta:** Tier-1 compat adapter, the pure `MastermindStateMachine` + thin
   `NautilusMastermindStrategy` (Decision 1), and a **mini-benchmark sweep** (1 symbol ×
   1-2 years × 10-20 samples) as a direction check. Approximate costing tolerated here only
   (Decision 5).
2. **MR-Session 4:** a **full v2 sweep** on `nautilus_trader` (6-symbol × full history) with
   **native nautilus costing** (Decision 5) — run **unconditionally**, because it is the
   first real test of the *claimed* edge (the deferred sizing layer), not of the
   already-failed bare core.
3. **MR-Session 5:** WF → Monte Carlo → stress → ADR go/no-go, **gated** on the Session-4
   sweep showing in-sample eligibility (the expensive robustness layer only for a strategy
   that cleared the sweep).
4. **Adapter validation:** a cross-engine equivalence test on one legacy strategy (Tier-1
   adapter) — the deliverable that earns trust in `nautilus_trader`.
5. **CLI:** dual-engine support (Decision 2).

`bghtrend_pullback` and `mean_reversion_bb_stoch` v1 are **not** migrated (Decision 8).

### 7. Bailout criteria — time tripwires plus capability tripwires

- **Runtime is a migration task, not a bailout trigger.** An env conflict
  (`nautilus_trader` + `backtesting.py` + TA-Lib on one runtime) is resolved in **Beta 0**
  by migrating the runtime — Python 3.12, a pinned nautilus version, conda-3.12 or the
  officially supported `uv`, or a separate env / container for nautilus. A single-env
  problem is explicitly **not** a reason to build a custom engine. Only if the runtime
  itself cannot be made to work at all — across conda, `uv`, *and* a container — does the
  bailout ladder below apply.
- **Time:** if Beta (after Beta 0 is green) spends > 8h on the adapter layer alone without
  visible progress on the strategy → review scope; > 12h → consider a fallback.
- **Trust:** if the cross-engine equivalence test cannot reach an acceptable tolerance on
  the *same* simple strategy → stop and diagnose before building v2 on top.
- **Fallbacks, correctly ranked:** (a) a **custom minimal event loop** (full control,
  ~2-3 weeks) or (b) **abandon the MMS full system** and pivot to a Phase-2-pivot-list
  candidate (breakout+volume, funding arb) on `backtesting.py`. **`vectorbt` is not the
  fallback here**: it *can* express an across-trade state machine through callback-driven
  order functions (`Portfolio.from_order_func`), but doing so is markedly less readable
  than a native event-driven strategy and — decisively — does **not** solve our
  backtest-to-live runtime requirement (vectorbt has no live-execution path). `vectorbt`
  remains a legitimate *post-MVP sweep-speed* candidate, not a state-machine rescue.

### 8. Backward-compat depth — `backtesting.py` retained as a pinned legacy baseline

`backtesting.py` **remains supported throughout the migration** and is retained as a
**pinned legacy baseline** — not declared eternal. `bghtrend_pullback` and
`mean_reversion_bb_stoch` v1 stay on it as historical baselines and framework validators;
they carry no ROI from migration. The reproducibility of those baselines is guaranteed not
by promising to maintain the old runtime forever, but by **pinning**: a git tag/release, a
lockfile, a snapshot of the results, test fixtures, and — if needed — an optional `legacy`
dependency group or a container with the old environment. `nautilus_trader` is for
forward-only work; the Tier-1 compat adapter may re-run a legacy strategy on it for
equivalence testing (additive, does not touch the `backtesting.py` path). **The eventual
retirement of the `backtesting.py` runtime is out of scope here and requires a separate
ADR** — this ADR neither promises to keep two live engines forever (that would be an odd
commitment if nautilus is the 5-year target) nor retires the old one now.

### 9. Position model — NETTING venue position + virtual base/add-on legs + reduce-only stops

MMS needs *logical* base and add-on legs with independent stops, but the venue position
model constrains how they can be realized. `nautilus_trader` can run NETTING (one position
per instrument), HEDGING (separate position IDs), or virtual strategy-level positions above
a venue netting position. The Binance adapter, at time of writing, **supports conditional
stop orders**, **does not support bracket orders**, and offers **`reduce_only`** on futures
which is **disabled in Hedge Mode**. Those constraints point to one realistic design:

> **Virtual base/add-on legs tracked inside the `MastermindStateMachine`, over a single
> real NETTING position on Binance, with the per-leg stops realized as independent
> `reduce_only` conditional stop orders of a specific quantity.**

The implication for Decision 5's "per-leg stops": they are **not** solved by the engine for
free — they are a strategy-level construct (virtual legs) mapped to venue orders, and that
mapping is a **first-class thing to validate in the Beta PoC** before it is treated as
working. The netting add-on averages the entry into a larger single position, and its "stop"
is a reduce-by-one-leg order — exactly the netting mechanics MMS itself describes
([mms/02](../references/mms/02-position-management-filters.md)). HEDGING is not chosen
because `reduce_only` is disabled there and it complicates the Binance execution path for no
benefit at MVP scale.

**Positive:**

- **Native state machine.** Pyramiding, per-leg stops, and across-trade sequential leverage
  become first-class instead of hacks inside a wrapper — the whole reason MMS's claimed
  edge can finally be tested.
- **Multi-TF and portfolio native.** M5/M10→H1 and multi-asset are supported by the engine,
  not reconstructed in `precompute`; opens the door to portfolio strategies post-MVP.
- **Backtest–live parity.** `nautilus_trader` runs the same strategy in backtest and live,
  which directly de-risks Phase 3 (the current `live/live_binance.py` is a second, separate
  engine — a known source of backtest/live drift).
- **Native, accurate cost accounting.** Fills, commissions, funding settlements, and
  execution/latency come from the engine, not a post-hoc overlay — and multi-leg
  (pyramided) turnover is costed natively, which the single-position ADR-011 overlay could
  not do faithfully (Decision 5).
- **Richer, reproducible results.** The `BacktestResult` carries `orders / fills /
  positions` (needed to debug the pyramiding state machine) plus `engine_version` and
  `data_hash / config_hash` — a stronger reproducibility record than the tuple alone.
- **Testable strategy logic decoupled from the library.** The pure `MastermindStateMachine`
  (Decision 1) is unit-tested without `nautilus_trader`, so a library breaking change lands
  on the thin adapter, not the edge logic.
- **Forward capability, not a one-off.** Justified independently of MMS: any future
  event-driven candidate inherits the engine.
- **Legacy retained and reproducible.** Baselines and the framework's validation story stay
  intact via pinning (Decision 8); the negative results remain reproducible.

**Negative / costs:**

- **Runtime migration.** Python 3.11 → 3.12, a pinned `nautilus_trader` version, and a
  conda-3.12-vs-`uv` decision are prerequisite work (Beta 0) before any adapter — the
  single biggest cost and the first hard gate.
- **Learning curve.** ~2-3 days for the first native strategy (actor/message-bus model,
  event handlers, order/position lifecycle).
- **Adapter maintenance.** The Tier-1 compat layer plus dual-engine CLI is ongoing surface
  area to keep working as either engine evolves.
- **Two runtimes during migration.** Supporting `backtesting.py` (pinned legacy) alongside
  `nautilus_trader` is deliberate but not free; the eventual retirement of the old runtime
  is a separate ADR (Decision 8).

**Risks:**

- **Runtime migration is the first hard gate.** Python 3.12 + a pinned `nautilus_trader`
  + TA-Lib (conda-forge) + `backtesting.py`, on conda or `uv`, on both the WSL box and the
  VPS, is the single biggest unknown. It is resolved in Beta 0 (runtime migration or a
  separate env/container — Decision 7), not by abandoning the engine; only a total failure
  across conda, `uv`, and a container escalates to the bailout ladder.
- **Position-model / per-leg stops need PoC validation.** The virtual-legs-over-netting +
  `reduce_only` conditional-stop design (Decision 9) is the realistic path given the Binance
  adapter's constraints (no bracket orders; `reduce_only` disabled in Hedge Mode), but it is
  an assumption until the Beta PoC demonstrates it — not a solved-by-engine given.
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
  `vectorbt` *can* in fact express a path-dependent, across-trade state machine through its
  callback-driven order functions (`Portfolio.from_order_func`) — so the rejection is not
  "it cannot". It is rejected as the primary engine because (a) coding the MMS state machine
  as order-function callbacks is markedly less readable than a native event-driven strategy,
  and (b) decisively, `vectorbt` has **no live-execution path**, so it does not satisfy the
  backtest-to-live parity that is a core reason for this migration. Retained as a legitimate
  *post-MVP sweep-speed* candidate.
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
- External `nautilus_trader` docs (facts feeding Decisions 1/5/6/7/9; version specifics to
  be re-confirmed and pinned at Beta 0):
  - Installation (Python ≥ 3.12, CPython + `uv` recommended, conda not officially
    supported, breaking-change warning) — <https://nautilustrader.io/docs/latest/getting_started/installation/>
  - Backtesting (native fills / commissions / funding settlements / execution + latency
    models) — <https://nautilustrader.io/docs/latest/concepts/backtesting/>
  - Position model (NETTING / HEDGING / virtual positions) — <https://nautilustrader.io/docs/latest/concepts/orders/>
  - Binance integration (conditional stops yes, bracket orders no, `reduce_only` on futures
    disabled in Hedge Mode) — <https://nautilustrader.io/docs/latest/integrations/binance/>

## Notes

- **Zero code in this session.** Files named for Beta (`algo_bot/engine/nautilus_adapter.py`,
  the native v2 strategy, dual-engine CLI wiring) are named for orientation, not created here.
- **MMS numbers corrected vs the kickoff (and vs the first draft of this ADR).** Two fixes:
  - *Sequential leverage* is a **binary** switch, not a ladder: the kickoff's `x1 → x0.5 →
    x0.25 → x0.1` is wrong; MMS ([mms/03](../references/mms/03-stop-loss-sequential.md)) does
    first full 2% SL → **x0.1** scout, first TP → back to **x1**.
  - *Pyramiding* is **one add-on, two alternative triggers** — not two cumulative add-ons.
    Base x1 + one add-on x1 = **x2 total** (`Total ≤ x2`, `Base 2% + add-on ≤ 1%` = 3%
    equity cap; [mms/02](../references/mms/02-position-management-filters.md)). The two
    triggers (confirming-candle **or** Stochastic %K&%D cross) are alternative ways to fire
    that single add-on. "base x1 + up to two x1 add-ons, total x2" was internally
    contradictory (x1+x1+x1 = x3) and is retracted.
- **Pyramiding + sequential-leverage state-machine skeleton (seed for Beta — full table +
  tests precede code).** Two orthogonal dimensions:
  - *Leverage regime* `L` (a scalar on base notional): `FULL` (x1) → on full base 2% SL →
    `SCOUT` (x0.1); `SCOUT` → on first profitable close (TP) → `FULL`. A TP in `FULL` keeps
    `FULL`.
  - *Position build* `P` (within one setup):

    | State | Event | Action | Next |
    |---|---|---|---|
    | `FLAT` | base setup fires | open base at regime size | `BASE` |
    | `BASE` | add-on trigger (confirming candle **or** Stoch cross) | add one leg = base size | `PYRAMIDED` |
    | `PYRAMIDED` | add-on SL (wick-pair ≤ 1%) | reduce the add-on leg, lock (no re-add this setup) | `BASE_LOCKED` |
    | `BASE` / `BASE_LOCKED` / `PYRAMIDED` | band TP | close all | `FLAT` (regime per `L`) |
    | `BASE` / `BASE_LOCKED` | base 2% SL | close all | `FLAT` (+ `L → SCOUT`) |

  - **Open interpretation points to resolve in Beta (do not hard-code silently):**
    (i) is pyramiding available in `SCOUT`, or are scouts base-only? (working assumption:
    base-only); (ii) confirm the two add-on triggers are mutually exclusive (one add-on),
    per the x2 cap; (iii) "first profitable trade" that re-arms `FULL` — net-profitable full
    close, or any partial TP? (working assumption: net-profitable full close). These are
    strategy-spec decisions for the Beta kickoff, flagged here so the implementation carries
    no hidden reading of MMS.
- **`engine_version` recorded per run.** Every backtest result carries `engine` +
  `engine_version` (Decisions 5/6) so a result is always attributable to a pinned runtime —
  important precisely because `nautilus_trader` warns of breaking changes between versions.
- **mypy strict-on-new.** The Beta adapter (`algo_bot/engine/nautilus_adapter.py`), the pure
  `MastermindStateMachine`, and the native `NautilusMastermindStrategy` go on the
  `pyproject.toml` strict override list, per project convention.
- **Status `Accepted`** — the nine decisions were signed off in-session with Janek, then
  revised in a second review round that added the Beta-0 runtime migration (Python
  3.12/`uv`/pin), the pure-`MastermindStateMachine` layering (Decision 1), the position
  model (Decision 9), native nautilus costing + the richer `BacktestResult` (Decision 5),
  softer backward-compat (Decision 8), and the pyramiding-math fix. Implementation lands in
  MR-Session 3 Beta (starting with Beta 0); `make check` remains the operator's WSL gate.
