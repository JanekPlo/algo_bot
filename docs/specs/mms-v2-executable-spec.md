# MMS-inspired v2 H1/BB mechanization — executable specification

- **Status:** P2 normative domain specification; P3 execution profile measured and
  preregistered; P4 measured and frozen to `OMS-A`; later implementation gates remain
- **Date:** 2026-07-13
- **Strategy version:** `mms_v2_h1_bb/1`
- **Snapshot schema:** `mms_state/1`
- **Scope key:** `(strategy_id, instrument_id)`
- **Engine architecture:** [ADR-014](../adr/014-engine-migration-nautilus.md)

## 1. Authority, purpose, and notation

This document is the single source of truth for the strategy called
**“MMS-inspired v2 H1/BB mechanization”**. It specifies causal signals, sizing,
state, order-lifecycle behavior, recovery, and acceptance criteria. It deliberately
does **not** call the result “full MMS”. The source material is an economic prior and
a collection of hypotheses, not proof of edge or an independently verified track
record.

[ADR-014](../adr/014-engine-migration-nautilus.md) remains authoritative for the
parallel engine migration. Its position-model Decision 9 is synchronized with the
measured P4 result below. The selected model must not be changed silently in strategy
code.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative. Monetary
amounts and PnL use the account's settlement currency. Prices and quantities use
`Decimal`-equivalent exact semantics at the domain boundary; binary float rounding
must not decide whether a limit is respected.

## 2. Evidence boundary and deliberate source differences

The primary local source summaries are
[base position](../references/mms/01-position-building.md),
[position management](../references/mms/02-position-management-filters.md),
[sequentiality](../references/mms/03-stop-loss-sequential.md), and
[interval marking](../references/mms/04-interval-marking.md). The author's published
backtests are inspiration only: they mainly use TMA, tune different parameters by
month, acknowledge overfitting, do not form an independent OOS record, do not always
identify whether add-ons are active, and do not fully define costs, funding, slippage,
or intrabar fills. This implementation MUST NOT be tuned to reproduce a displayed
equity curve.

| Topic | Source material | This specification | Consequence |
|---|---|---|---|
| Envelope | Mainly TMA in published tests; TMA/NW/BB described as alternatives | Bollinger Bands only | Results are for the H1/BB mechanization, not a TMA replication. |
| Entry timing | M5/M10 marking inside a new H1 interval | Closed H1 `armed -> reaction` proxy inherited from v1 | Entry can lag the manual description. No synthetic M5/M10 detail may be inferred from H1 OHLC. |
| Higher-timeframe context | H4/H3 trend/range context and D1/W1 direction/risk context | No H4, H3, D1, or W1 filter; no D1 Stochastic | Multi-timeframe context is deferred, not an implicit discretionary override. |
| Sequential size | Main chapters specify `x1 -> x0.1`; one backtest summary mentions reduction “as far as x0.01” | Binary `x1 <-> x0.1` only | `x0.01` is an unresolved source variant and MUST NOT be implemented or swept in Beta. |
| Stochastic role | Add-on confirmation/filter | H1 14/3/3 add-on trigger only | It MUST NOT gate the v2 base entry. |
| Claimed performance | Author-selected tests and an author-claimed prop track record | No assumed edge | Every result requires causal, cost-aware evaluation and untouched temporal holdout later. |

## 3. Scope and non-goals

The Beta strategy is single-instrument, H1, both-direction, one-setup-at-a-time mean
reversion. It contains:

- a contrarian BB base entry using a closed-bar `armed -> reaction` sequence;
- one logical base leg and at most one logical add-on leg;
- a fixed base stop at 2% from actual base fill VWAP;
- a structural add-on stop, rejected when its modeled distance exceeds 1%;
- a live opposite Bollinger Band target and no trailing stop;
- per-instrument binary FULL/SCOUT sequential exposure;
- pure domain events, intents, snapshots, and deterministic recovery.

The following are out of scope: M5/M10 reconstruction, H4/D1/W1 filters, more than
one add-on, exchange leverage selection, `x0.01`, portfolio-global risk mode, separate
long/short risk modes, trailing/break-even/timeout exits, six-instrument production
sweeps, a custom matching engine, and live trading with real capital.

## 4. Vocabulary and configuration contract

### 4.1 Core terms

- **Setup:** one base-entry attempt and, if the base gets any fill, all subsequent
  base/add-on activity until actual position quantity is zero and exit reconciliation
  has completed. `setup_id` is created immediately before the first base intent.
- **Logical leg:** the domain allocation of fills to `BASE` or `ADDON`; it is not an
  assertion that the venue exposes a separate position.
- **Logical add-on stop group:** one protection intent at one structural trigger,
  implemented on Binance as append-only `reduce_only` STOP_MARKET children, one per
  unique partial add-on fill. Active child quantities sum to actual add-on quantity.
- **Natural close:** a close produced by the strategy's TP or full base SL. Forced
  `RISK_LIMIT`, `MANUAL`, `LIQUIDATION`, and `ENGINE_ERROR` closes are not natural.
- **Full base SL:** the base protective stop actually closes the entire remaining
  setup exposure. A trigger, acceptance, partial stop fill, or canceled stop is not a
  full base SL.
- **Exposure multiplier:** target notional divided by immutable
  `setup_start_equity`. `x1` means a 1% price move is approximately a 1% equity move
  before costs. It is **not** Binance account leverage or margin mode.
- **Committed entry exposure:** filled entry notional plus the unfilled target
  notional of live entry orders. This, rather than mark-to-market notional after price
  moves, is the quantity used by the entry cap invariant.
- **Closed bar:** a bar whose complete OHLC is known at or after its real close time.
  An open timestamp relabeled as a close without adding the interval is not closed.

### 4.2 Frozen Beta parameters

| Parameter | Value / domain | Rule |
|---|---|---|
| `timeframe` | `1h` | Other timeframes are rejected by this strategy version. |
| `base_exposure_full` | `1.0` | FULL base target. |
| `base_exposure_scout` | `0.1` | SCOUT base target. |
| `addon_enabled` | boolean, default `true` | Preregistered ablation switch. When false, reserve zero add-on target and emit no add-on fact/order. |
| `sequential_enabled` | boolean, default `true` | Preregistered ablation switch. When false, only FULL is valid and final closes cannot change risk mode. |
| `scout_allows_addon` | `false` | Normative working assumption B. |
| `base_sl_pct` | `0.02` | Distance from actual base fill VWAP. |
| `addon_max_sl_pct` | `0.01` | Inclusive maximum modeled distance from actual add-on fill VWAP. |
| `stoch_k/d/smooth` | `14/3/3` | Slow Stochastic on closed H1 bars. |
| `stoch_oversold/overbought` | `20/80` | Strict current-bar zones: both values `<20` or both `>80`. |
| `max_addons` | `1` | Cannot be raised without a new spec version. |
| `tp_mode` | `LIVE_OPPOSITE_BB` | Re-evaluated causally on each closed bar. |
| `trailing_stop` | `false` | No break-even or trailing rule. |

`bb_window`, `bb_num_std`, `arm_expiry_bars`, and `require_reclaim` remain explicit,
versioned strategy parameters from the v1 signal mechanism. They may be selected only
by a preregistered experiment; they must never be inferred from future bars. All runs
MUST record the complete config hash and strategy version.

The two feature switches exist only to run the frozen base/sequential/add-on ablation;
they are not wrapper conditionals. They belong to the pure config hash and snapshot
compatibility boundary. `sequential_enabled=false` with initial/restored SCOUT is invalid.

## 5. Normative domain invariants

The pure reducer MUST check these after every non-duplicate event and after snapshot
restore. A violation emits no new risk-increasing intent, persists diagnostics, and
requests an `ENGINE_ERROR` fail-safe close/reconciliation where exposure exists.

1. There is at most one live setup for a scope key and exactly one logical base leg
   inside a live setup.
2. `active_addon_count <= 1`; an add-on count is one iff actual add-on filled quantity
   is positive.
3. A trigger, submission, or acceptance cannot change filled leg quantity or position
   build state. Only a unique fill/reconciliation event can do so.
4. `BASE -> PYRAMIDED` occurs on the first positive add-on fill, not on its trigger,
   submission, or acceptance. A partial add-on fill therefore means
   `PYRAMIDED + ADDON_PENDING`.
5. Base and add-on quantities are non-negative in their logical direction. Their sum
   must reconcile to the real strategy-owned open quantity within instrument precision.
6. A protective order can never increase exposure or reverse the position.
   Its reducible quantity MUST be no greater than the latest confirmed real open
   quantity; an add-on stop quantity MUST be no greater than actual add-on filled
   quantity.
7. Entry committed **target** exposure is the target allocation represented by actual
   filled quantity plus the unfilled reservation of every live entry order. It MUST NOT
   exceed `2 * setup_start_equity` in FULL or `0.1 * setup_start_equity` in SCOUT.
   Actual fill-price notional is separate telemetry: an adverse gap may exceed the target
   cap and MUST NOT by itself rewrite target sizing or manufacture an `ENGINE_ERROR`.
8. FULL targets are one base `x1` plus at most one add-on `x1`, for at most `x2`.
   Modeled pre-cost stop risk is at most 2% of setup-start equity for the base plus
   1% for the add-on, or 3% total. This is a target/modelled risk, not a guarantee:
   gaps, slippage, fees, and funding can make realized loss larger.
9. SCOUT is base-only at `x0.1`; it MUST NOT submit, fill, or restore an add-on.
   The non-implemented alternative `base x0.1 + addon x0.1` remains documented only.
10. Once an add-on has been fully removed by its protective stop or invalid-fill
    safety unwind, `add_on_lock=true` until the setup closes; no re-add is possible.
11. `FLAT` has zero logical and reconciled real quantity and no active protective
    order. Orphan cancel intents may be pending, but such orders are not considered
    valid protection and must be canceled.
12. The live target is the opposite current BB; there is no trailing stop. Replacing
    a changed TP must not leave overlapping quantities that can reverse exposure.
13. A partial close, partial TP, partial reduction, add-on fill, or add-on SL cannot
    change risk mode.
14. FULL changes to SCOUT only after `PositionClosed(BASE_SL)` has frozen the complete
    ledger **and a later setup-scoped reconciliation confirms zero position and zero open
    orders**. SCOUT changes to FULL at the same final-flat boundary only for a natural TP
    whose complete setup net PnL is strictly positive.
15. `RISK_LIMIT`, `MANUAL`, `LIQUIDATION`, and `ENGINE_ERROR` closes preserve the
    pre-close risk mode. They cannot silently de-risk or re-arm sequentiality.
16. Risk mode is persisted per `(strategy_id, instrument_id)`. Long and short setups
    for the same key share it; instruments and strategy IDs do not.
17. Duplicate event IDs and duplicate execution IDs are idempotent: no quantity,
    PnL, state, or intent is applied twice.
18. Every external order intent has a stable logical `intent_id`, correlation ID, and
    deterministic client-order ID before submission. A restart cannot mint a second
    strategic order for the same intent.
19. Every order record retains its creating `setup_id`; each add-on-stop child also
    retains the exact entry `execution_id` it protects. A quantity-changing callback from
    an old setup cannot mutate the live setup. A stale final summary is diagnostic and
    requests reconciliation; it is not evidence to close a newer setup.
20. Signed venue truth controls emergency close direction. Before an actual-sign
    `CloseAll`, all stale entry/protection/exit submits are suppressed and canceled. A
    subsequent drift with different sign or quantity replaces the prior close.
21. Entry-fill original quantity, remaining quantity, and chronological execution order
    are durable. An add-on stop consumes its protected execution first; only documented
    race spillover uses the remaining deterministic order.
22. A provisional `PositionClosed` can be invalidated by a late exposure-increasing fill
    before final reconciliation. The reducer then requires a corrective close/finalization;
    the old sequential decision is never applied.

## 6. Signal semantics

Signal evaluation and order-lifecycle reduction are separate pure components. The
signal evaluator consumes closed H1 bars and produces domain trigger facts; only the
lifecycle reducer can emit orders. No NautilusTrader type may cross either boundary.

### 6.1 Base: closed-H1 `armed -> reaction`

The base uses the v1 causal proxy, with Stochastic removed from base gating:

1. While flat, fully reconciled, and disarmed, a closed bar arms LONG when its Low is
   at or below the live lower BB, or SHORT when its High is at or above the live upper
   BB. If both bands are touched on the same bar, neither side arms.
2. An armed LONG reacts on the first eligible closed bar with `Close > Open`; an
   armed SHORT reacts with `Close < Open`. If `require_reclaim=true`, LONG also
   requires `Close > lower_bb` and SHORT requires `Close < upper_bb` on that bar.
3. The armed state expires after the explicit `arm_expiry_bars` count. A re-touch
   while armed neither refreshes nor flips it, matching v1.
4. A valid reaction creates a setup, captures `setup_start_equity`, records the
   reaction bar, and emits exactly one logical `SubmitBaseOrder` intent.
5. No exit bar can also arm or enter a replacement setup. Earliest new arming is a
   later `BarClosed` event after exit reconciliation.

An add-on can be evaluated only when a base has a positive fill, the base entry order
is terminal, build state is `BASE`, lifecycle is `NONE`, risk mode is FULL, no
add-on has filled, and the current setup has no add-on lock.

### 6.2 Working assumption A — one add-on, explicit trigger policy

`addon_trigger_policy` is a required enum, not an implicit boolean expression:

| Enum | Trigger semantics |
|---|---|
| `CONFIRMING_CANDLE` | The first complete H1 bar after the base-entry reaction bar closes in the setup direction: `Close > Open` for LONG, `Close < Open` for SHORT. |
| `STOCH_CROSS` | LONG: previous `%K <= %D`, current `%K > %D`, and current `%K < 20` and `%D < 20`. SHORT: previous `%K >= %D`, current `%K < %D`, and current `%K > 80` and `%D > 80`. Only fully closed H1 values count. |
| `FIRST_OF_CANDLE_OR_STOCH` | The first eligible occurrence of either fact wins and latches the trigger kind. If both occur on one bar, `CONFIRMING_CANDLE` is the deterministic tie-breaker. Exactly one logical opportunity is emitted. |
| `CANDLE_AND_STOCH` | Both facts must be true on the same `BarClosed` event. There is no rolling AND window. |

These are preregistered ablation variants, not dimensions to combine casually in a
large random sweep. Every policy permits at most one active add-on. A trigger fact has
a deterministic `trigger_id=(setup_id, policy, bar_id, trigger_kind)` and is consumed
once. Both triggers on one bar cannot produce two intents.

Submission consumes the strategic opportunity for `CONFIRMING_CANDLE`,
`FIRST_OF_CANDLE_OR_STOCH`, and `CANDLE_AND_STOCH`. A terminal rejection/cancel/timeout
returns build state to `BASE` without exposure but does not reinterpret a later signal
as the same “first” opportunity. Under `STOCH_CROSS`, a later distinct cross may form a
new opportunity only while all add-on eligibility guards still hold. Execution-layer
resubmission of the same logical order, if P4 permits it, must retain the same
`intent_id`; it is not a second strategic add-on.

### 6.3 Working assumption F — H1 wick-pair add-on stop

M5/M10 marking is deferred, so the following H1 proxy is normative:

- Candle trigger pair: the base-entry reaction bar and the complete confirmation bar.
- Stochastic trigger pair: the two most recent fully closed H1 bars, including the
  trigger bar.
- LONG structural stop: minimum Low of the selected pair.
- SHORT structural stop: maximum High of the selected pair.
- For `CANDLE_AND_STOCH`, use the structurally farther level across both candidate
  pairs (minimum for LONG, maximum for SHORT), then apply the same distance test.
- For a simultaneous `FIRST_OF_CANDLE_OR_STOCH`, its candle tie-break uses the candle
  pair.

Distance is measured from the actual add-on fill VWAP:

`distance = abs(addon_fill_vwap - structural_stop) / addon_fill_vwap`.

For LONG, a valid stop must be strictly below the fill; for SHORT, strictly above it.
Zero distance, a stop on the wrong side, non-finite/missing OHLC, or
`distance > 0.01` is invalid. Exactly 1% is valid. The structural stop MUST NOT be
clamped to 1%.

Because a live market fill is unknowable at submit time, the pre-submit gate uses the
latest causal executable reference price. After every actual partial fill, distance
is recomputed from actual cumulative VWAP. If slippage makes an already-filled add-on
invalid, the system MUST NOT install a fake 1% stop; it emits `ReduceAddon` for the
actual filled quantity, sets the add-on lock, and records an invalid-fill safety exit.
This is not evidence that the intended structural add-on was valid.

Deterministic fixtures:

| Case | Side / pair / fill | Expected |
|---|---|---|
| Exact cap | LONG Lows `99.00, 99.40`, fill `100.00` | Stop `99.00`, distance `1.00%`, accepted. |
| Over cap | LONG Lows `98.99, 99.40`, fill `100.00` | Distance `1.01%`, no strategic add-on order. |
| Wrong side | LONG Lows `100.00, 100.20`, fill `100.00` | Zero/wrong-side distance, rejected; no clamp. |
| Short exact cap | SHORT Highs `100.60, 101.00`, fill `100.00` | Stop `101.00`, distance `1.00%`, accepted. |
| Stoch pair | LONG prior/trigger Lows `49_800, 49_650`, fill `50_000` | Stop `49_650`, distance `0.70%`, accepted. |

### 6.4 Base stop and live target

After each unique base fill, calculate cumulative base fill VWAP and replace protection
idempotently if the VWAP changed:

- LONG: `base_stop = base_fill_vwap * (1 - 0.02)`;
- SHORT: `base_stop = base_fill_vwap * (1 + 0.02)`.

The 2% distance is never calculated from the signal Close, requested price, averaged
venue position price after an add-on, or current equity. The base stop protects the
whole remaining setup exposure at execution level, subject to the P4-selected safe
mapping. Adding a leg MUST NOT move the base stop to the net-position average.

On each final H1 bar while exposed, the signal evaluator calculates the current
opposite BB: upper band for LONG and lower band for SHORT. A changed target produces
one idempotent replacement/cancel-submit action capped to real open quantity. Missing
or non-finite band values do not invent a target; existing risk-reducing protection
remains and the condition is reported. Whether a price crossed the old/new level
inside that bar is decided only by the selected P3 profile, not by retrospective
strategy preference.

## 7. Working assumptions B–E: sizing and sequentiality

### 7.1 B — SCOUT is base-only

FULL uses `current_risk_multiplier=1.0`; SCOUT uses `0.1`. SCOUT submits only a
base target at `x0.1`. Add-on signal facts may be observed for diagnostics but MUST
not emit an add-on intent. The alternative `x0.1 + x0.1` is not implemented.

### 7.2 C — re-arm only after complete net-profitable setup close

FULL returns only after `PositionClosed` has frozen the ledger, the adapter has drained
all fills and cost allocations through the close watermark, and a later setup-scoped
reconciliation confirms no position or open order. Its close reason must be `TP`, and:

`setup_net_pnl = realized_price_pnl - commissions + signed_funding - realized_slippage_cost > 0`.

`signed_funding` is positive when the account receives funding and negative when it pays;
this matches native `PositionAdjusted(FUNDING).pnl_change`.

Realized add-on-stop loss is part of `realized_price_pnl`. Partial TP, partial
reduction, an add-on fill, or a temporarily positive open PnL cannot re-arm. The close
reason and risk transition matrix is evaluated exactly once at that final reconciliation
boundary, never on the earlier `PositionClosed` callback:

| Final close reason | FULL result | SCOUT result |
|---|---|---|
| `TP` and net PnL `> 0` | stays FULL | re-arms FULL |
| `TP` and net PnL `<= 0` | stays FULL | stays SCOUT |
| `BASE_SL` (full) | changes to SCOUT | stays SCOUT |
| `RISK_LIMIT` | unchanged | unchanged |
| `MANUAL` | unchanged | unchanged |
| `LIQUIDATION` | unchanged | unchanged |
| `ENGINE_ERROR` | unchanged | unchanged |

An add-on SL is a leg close reason, not a setup close reason. A later full setup close
still uses one of the six reasons above.

### 7.3 D — sequentiality scope

Risk mode belongs to `(strategy_id, instrument_id)`. LONG and SHORT share it on one
instrument. No other instrument's loss or win modifies it. A process restart MUST
restore SCOUT as SCOUT; absent/corrupt state must fail closed rather than default to
FULL.

### 7.4 E — immutable setup-equity sizing

At setup creation:

1. Capture immutable `setup_start_equity` from the latest confirmed account-equity
   event available before the base intent.
2. Set `base_target_notional = setup_start_equity * exposure_multiplier`, where the
   multiplier is 1.0 in FULL and 0.1 in SCOUT.
3. In FULL set `addon_target_notional = base_target_notional` only when
   `addon_enabled=true`; in SCOUT or an add-on-disabled ablation it is zero.
4. Compute raw quantity from a causal current executable reference price:
   `raw_qty = target_notional / reference_price`.
5. Round quantity down to instrument precision/step so rounding cannot breach the
   cap. If the result violates minimum quantity/notional, do not submit and record a
   deterministic local rejection.
6. Do not resize from later equity. Add-on quantity uses its own causal reference
   price but the original, immutable `addon_target_notional`.
7. Record fill VWAP and realized notional drift. A live market order cannot know its
   final fill price beforehand; the implementation must not use a backtest fill as
   look-ahead to choose quantity.

Example: with start equity `10_000`, FULL base target is `10_000`. At reference price
`50_000`, raw BTC quantity is `0.2`. If the later add-on reference price is `40_000`,
its raw quantity is `0.25`, still targeting the original `10_000`, not current equity.

## 8. Three independent state dimensions

The state is a product, not a single flattened enum.

### 8.1 Risk mode

- `FULL`
- `SCOUT`

### 8.2 Position build

- `FLAT`: no actual setup quantity.
- `BASE`: positive base quantity, zero add-on quantity, add-on not stopped/locked.
- `PYRAMIDED`: positive add-on quantity, including a partial add-on fill.
- `BASE_LOCKED`: positive base quantity, zero add-on quantity, and the setup is
  permanently barred from re-adding after add-on stop/safety unwind.

The build state is derived from actual virtual-leg quantities plus `add_on_lock`; it
is never inferred from submitted orders.

### 8.3 Primary order lifecycle

- `NONE`
- `BASE_PENDING`
- `ADDON_PENDING`
- `REDUCE_PENDING`
- `EXIT_PENDING`

This dimension represents the active exposure-changing workflow. Accepted passive
protective orders live in the order ledger and do not force a permanent pending
state. Valid combinations include `FLAT + BASE_PENDING`, `BASE + BASE_PENDING` after
a partial base fill, `BASE + ADDON_PENDING` before the first add-on fill,
`PYRAMIDED + ADDON_PENDING` after a partial fill, and `FLAT + EXIT_PENDING` while
orphan cancellation/reconciliation completes.

Auxiliary signal memory (`armed_side`, expiry, reaction bar, previous Stochastic
values) and the add-on lock are snapshot fields, not replacements for these three
dimensions.

## 9. Typed domain protocol

Every event has an immutable envelope:

`event_id`, `event_type`, `strategy_id`, `instrument_id`, `occurred_at_utc`,
`source`, `source_sequence`, optional `setup_id`, `correlation_id`, `client_order_id`,
and `causation_id`. Quantity-changing events also have globally unique `execution_id`.
Events for another scope key are rejected. `source_sequence` MUST be strictly increasing
within each stable `source` namespace, including across restart; a source must not reset or
reuse its sequence without changing namespace. The reducer persists each source high-water
mark and treats an event at or below it as a stale replay. Envelope identifiers are
non-empty strings, sequences are exact non-boolean integers, all Decimals are finite, and
timestamps are UTC. `BarClosed` accepts the frozen inclusive-close H1 interval
(`close-open = 1h-1ms`) and `%K/%D` values only in `[0,100]`.

### 9.1 Events consumed

| Event | Required payload / meaning |
|---|---|
| `AccountEquityUpdated` | Latest causally confirmed positive account equity used only when a new setup freezes `setup_start_equity`. |
| `BarClosed` | `bar_id`, open/close timestamps, OHLCV, live BB values, current and previous `%K/%D`, and `is_final=true`. |
| `OrderSubmitted` | Logical role, stable intent/order IDs, requested quantity, side, and execution parameters. Acknowledges transport, not exposure. |
| `OrderAccepted` | Venue/broker acknowledgment. Does not change exposure. |
| `OrderRejected` | Terminal rejection reason and whether any earlier fill exists. |
| `OrderCanceled` | Terminal canceled remainder and cumulative filled quantity. |
| `OrderTimedOut` | Deterministic deadline and observed order status. Timeout is uncertain, enters recovery and requests reconciliation; it is never treated as a definitive zero-fill rejection. |
| `OrderPartiallyFilled` | Unique execution ID, last/cumulative quantity, price, commission, logical role. |
| `OrderFilled` | Unique execution ID, last/cumulative terminal quantity, price, commission, logical role. |
| `PositionChanged` | Reconciled signed real quantity and average price; detects drift, but cannot silently reassign fills between logical legs. |
| `PositionClosed` | Provisional zero-real-quantity attestation, close reason, realized price PnL, finalized cost totals, and causal closing order/fill IDs. It freezes the ledger but does not change risk until final reconciliation. |
| `FundingApplied` | Unique settlement ID/time, signed amount, setup attribution, and native/fixture source. |
| `RiskLimitTriggered` | Limit ID, observed equity/exposure, and deterministic reason. |
| `CloseRequested` | Explicit forced reason and operator/engine reason text. It may emit `CloseAll` for `MANUAL`, `RISK_LIMIT`, `LIQUIDATION`, or `ENGINE_ERROR`, and never changes risk mode by itself. |
| `RecoverySnapshotLoaded` | Schema/version/checksum and restored state identity. Its SHA-256 must exactly attest the checksum already verified during deserialize. It starts recovery mode, not trading. |
| `ReconciliationCompleted` | Creating `setup_id` (or explicit no-setup scope), signed actual position/average, complete open client IDs, optional rich immutable order rows (venue ID, role, active status, requested/filled quantities, side, reduce/close flags, setup), acknowledged intent IDs, and a monotonic integer as-of sequence. |

`MANUAL`, `LIQUIDATION`, and `ENGINE_ERROR` may arrive through `PositionClosed` or a
prior forced-close request, but the final causal reason must be explicit. Unknown
events fail validation; they are not treated as harmless fills.

A non-empty bare `open_client_order_ids` list is presence evidence only: it cannot
implicitly acknowledge and delete a replayable submit intent. Rich order rows must agree
with the immutable local ledger. A setup mismatch or stale as-of cannot finalize a newer
setup. Trusted signed position truth controls emergency-close side; untrusted or
directionless evidence requests reconciliation without guessing a direction.

### 9.2 Intents emitted

All intents have `intent_id`, `idempotency_key`, `setup_id`, scope key, causation ID,
and an expiry/reconciliation policy.

| Intent | Meaning |
|---|---|
| `SubmitBaseOrder` | Exposure-increasing base order for rounded base target quantity. |
| `SubmitAddonOrder` | At most one active add-on order for original add-on target notional, with trigger/structural-stop provenance. |
| `SubmitBaseStop` | Protect actual base/setup exposure at 2% from base fill VWAP; exact venue mapping is a P4 result. |
| `SubmitAddonStop` | Protect the logical add-on stop group at the structural level; on Binance, append one child for each unique actual fill delta. |
| `CloseAll` | Exposure-reducing full exit with one of the six setup close reasons. |
| `ReduceAddon` | Reduce no more than actual add-on quantity, including invalid-fill safety unwind. |
| `CancelOrder` | Cancel a known pending or orphan order. |
| `ReplaceOrder` | Atomic logical replacement using a new client ID linked to the same protection/exit intent; old and new total reducible quantity cannot overlap beyond real exposure. |
| `RequestReconciliation` | Read-only request for venue position and open-order truth after recovery or mismatch. |
| `PersistSnapshot` | Persist state, dedupe markers, and transactional outbox before external side effects. |

## 10. Transition table

“Same” below means all three state dimensions remain unchanged; every row still
updates audit/dedupe data and persists when appropriate. `q_base`, `q_addon`, and
`q_real` always mean actual filled/reconciled quantities.

| Current risk / build / lifecycle | Event and guard | Required effects and intents | Next build / lifecycle; risk |
|---|---|---|---|
| `* / FLAT / NONE` | Valid base reaction; no recovery/reconciliation block | Create setup, capture equity/targets, emit one `SubmitBaseOrder`, persist before submit | `FLAT / BASE_PENDING`; risk same |
| `* / FLAT / BASE_PENDING` | Base `OrderSubmitted` or `OrderAccepted` | Record IDs/status only | Same |
| `* / FLAT / BASE_PENDING` | First unique partial base fill | Allocate fill to base; commission ledger; install/replace protection for actual quantity | `BASE / BASE_PENDING`; risk same |
| `* / BASE / BASE_PENDING` | Further partial base fill | Increase base VWAP/quantity once; replace protection without over-cover | `BASE / BASE_PENDING`; risk same |
| `* / {FLAT,BASE} / BASE_PENDING` | Terminal base fill | Apply unique last fill; protect actual quantity | `BASE / NONE` if `q_base>0`, otherwise invalid; risk same |
| `* / FLAT / BASE_PENDING` | Base reject/cancel/timeout with zero fill | Record terminal order; abandon setup after reconciliation | `FLAT / NONE`; risk same |
| `* / BASE / BASE_PENDING` | Base reject/cancel/timeout after partial fill | Keep actual partial base and protection; no fictitious target fill | `BASE / NONE`; risk same |
| `FULL / BASE / NONE` | Eligible add-on trigger and valid pre-submit wick distance | Latch trigger, compute original-target quantity, emit one `SubmitAddonOrder` | `BASE / ADDON_PENDING`; FULL |
| `SCOUT / BASE / NONE` | Any add-on trigger | Diagnostic only; no risk-increasing intent | Same |
| `* / {PYRAMIDED,BASE_LOCKED} / *` | Any add-on trigger | Ignore as ineligible; no intent | Same |
| `FULL / BASE / ADDON_PENDING` | Add-on submitted/accepted | Record only; position does not change | Same |
| `FULL / BASE / ADDON_PENDING` | First unique partial add-on fill, actual stop valid | Allocate actual fill; append one add-on stop child for that fill delta; aggregate active group equals cumulative actual fill | `PYRAMIDED / ADDON_PENDING`; FULL |
| `FULL / PYRAMIDED / ADDON_PENDING` | Further partial add-on fill, stop valid | Update VWAP/quantity once; append one non-overlapping child for this delta, never target quantity | Same |
| `FULL / {BASE,PYRAMIDED} / ADDON_PENDING` | Terminal add-on fill | Apply unique fill and verify aggregate protection equals actual add-on quantity | `PYRAMIDED / NONE`; FULL |
| `FULL / BASE / ADDON_PENDING` | Add-on reject/cancel/timeout with zero fill | Remove pending reservation; no virtual exposure change | `BASE / NONE`; FULL |
| `FULL / PYRAMIDED / ADDON_PENDING` | Add-on reject/cancel/timeout after partial fill | Keep actual add-on, cancel unfilled remainder, protect actual cumulative quantity | `PYRAMIDED / NONE`; FULL |
| `FULL / PYRAMIDED / {ADDON_PENDING,NONE}` | Actual fill makes structural stop invalid | Cancel entry remainder/protection group as needed; set lock; emit `ReduceAddon(q_addon)` | `PYRAMIDED / REDUCE_PENDING`; FULL |
| `FULL / PYRAMIDED / NONE` | Add-on stop child triggers/submits | Emit or record `ReduceAddon` capped to that child and actual add-on quantity | `PYRAMIDED / REDUCE_PENDING`; FULL |
| `FULL / PYRAMIDED / REDUCE_PENDING` | Partial unique add-on reduction fill | Decrease add-on only; cancel consumed children and cap aggregate protection to `q_real` | `PYRAMIDED / REDUCE_PENDING`; FULL |
| `FULL / PYRAMIDED / REDUCE_PENDING` | Add-on fully reduced, base remains | Set permanent add-on lock; cancel add-on protection; keep base protection | `BASE_LOCKED / NONE`; FULL |
| `FULL / PYRAMIDED / REDUCE_PENDING` | Reduction rejected/canceled/timeout while add-on remains | Reconcile, retain exposure and valid protection; same logical reduction may be replaced without exceeding quantity | `PYRAMIDED / REDUCE_PENDING`; FULL |
| `* / {BASE,PYRAMIDED,BASE_LOCKED} / NONE` | Live TP exit condition or accepted TP protective action | Emit/record `CloseAll(TP)` and cancel conflicting entries | same build / `EXIT_PENDING`; risk same |
| `* / {BASE,PYRAMIDED,BASE_LOCKED} / *` | Base stop begins execution | Cancel add-on entry/reduction remainder as necessary; close all remaining exposure with `BASE_SL` attribution | same build / `EXIT_PENDING`; risk same until finalized close |
| `* / {BASE,PYRAMIDED,BASE_LOCKED} / *` | `RiskLimitTriggered` | Emit `CloseAll(RISK_LIMIT)`; no sequential-mode change | same build / `EXIT_PENDING`; risk same |
| `* / * / EXIT_PENDING` | Partial unique exit fill | Allocate reductions without making any leg negative; cap/cancel protections to current real quantity | build derived from remaining legs / `EXIT_PENDING`; risk same |
| `* / * / EXIT_PENDING` | Exit reject/cancel/timeout and `q_real>0` | Reconcile first; retain/reinstall non-overlapping protection; replace same logical exit or escalate `ENGINE_ERROR` per bounded execution policy | derived build / `EXIT_PENDING`; risk same |
| `FULL / * / EXIT_PENDING` | `PositionClosed(BASE_SL)` freezes complete ledger | Zero legs; cancel all open orders; retain setup/final reason until reconciliation | `FLAT / EXIT_PENDING`; still FULL |
| `SCOUT / * / EXIT_PENDING` | `PositionClosed(BASE_SL)` freezes complete ledger | Same provisional close cleanup | `FLAT / EXIT_PENDING`; still SCOUT |
| `SCOUT / * / EXIT_PENDING` | `PositionClosed(TP)` freezes net PnL | Same provisional close cleanup | `FLAT / EXIT_PENDING`; still SCOUT regardless of provisional net |
| `FULL / * / EXIT_PENDING` | `PositionClosed(TP)` freezes net PnL | Same provisional close cleanup | `FLAT / EXIT_PENDING`; still FULL |
| `* / * / EXIT_PENDING` | `PositionClosed` forced reason (`RISK_LIMIT`, `MANUAL`, `LIQUIDATION`, `ENGINE_ERROR`) | Same provisional cleanup; final reconciliation never applies a sequential transition | `FLAT / EXIT_PENDING`; risk unchanged |
| `* / FLAT / EXIT_PENDING` | Setup-scoped reconciliation confirms zero position/no open orders after `PositionClosed` and funding drain | Apply the close matrix exactly once; clear setup/outbox; persist | `FLAT / NONE`; derived final risk |
| `* / FLAT / EXIT_PENDING` | Flat reconciliation arrives before `PositionClosed` cost/reason summary | Retain setup; request/wait for ledger finalization | `FLAT / EXIT_PENDING`; risk unchanged |
| `* / FLAT / EXIT_PENDING` | Late entry fill invalidates provisional finalization | Revoke provisional fingerprint/reason, cancel stale orders, actual-sign `CloseAll(ENGINE_ERROR)`, require corrective `PositionClosed` | `FLAT / EXIT_PENDING` logical view; recovery on; risk unchanged |
| `* / * / *` | Duplicate `event_id` or execution ID | Return prior acknowledgment; emit no new strategic intent and apply no PnL/qty | Same |
| `* / * / *` | `RecoverySnapshotLoaded` | Block new signals; emit `RequestReconciliation`; do not reset risk/build/pending state | same dimensions; recovery flag on |
| `* / * / *` | Rich reconciliation exactly matches snapshot | Acknowledge explicit/richly proven intents, replay pre-submit outbox IDs unchanged, resolve causal timeout | dimensions derived from confirmed truth; recovery flag off |
| `* / * / *` | Accepted/submitted protection is absent at venue | Mark absent and use bounded reinstall or fail-safe actual-sign close; never loop forever | `EXIT_PENDING` if fail-safe; risk unchanged |
| `* / * / *` | Reconciliation mismatch cannot be safely attributed | Cancel venue orphans and all stale local submits, persist diagnostics, use signed actual truth for `CloseAll(ENGINE_ERROR)` only when scope/as-of is trusted | `EXIT_PENDING` if exposed; risk unchanged |

### 10.1 Gap and concurrent-order rules

- If price gaps through both stops, fills—not theoretical trigger order—drive state.
  If the base stop closes all first, a later add-on stop is canceled/ignored or capped
  to zero. If the add-on reduction fills first, the base stop may close only the
  remainder. The position can never cross through zero.
- Each add-on stop child reduces the remaining inventory of its own protected entry
  execution first. If a concurrent whole exit already consumed that inventory, any
  unavoidable spill is allocated in the persisted chronological fill order.
- A close request, risk limit, or first whole-exit fill immediately cancels and removes
  every still-unfilled base/add-on entry reservation from the dispatchable outbox.
- When the base stop completes a pyramided setup, final reason is `BASE_SL`; any
  earlier add-on-stop loss remains in the setup ledger.
- When TP and SL are reachable inside one OHLC bar, the selected P3 execution profile
  decides ordering. Strategy code MUST NOT choose whichever result is profitable.
- On FLAT, every remaining TP, base stop, add-on stop, and entry remainder is orphaned
  and must receive a stable `CancelOrder` intent.

## 11. PnL, fees, funding, and finalization

The setup ledger stores each fill and the cumulative fields:

- `realized_price_pnl` by virtual leg;
- `commissions` from native fill events;
- signed `funding` from unique native settlement events (receipt positive, payment negative);
- `realized_slippage_cost` when the execution profile supplies an auditable benchmark;
- `setup_net_pnl` as the signed sum;
- `addon_stop_realized_pnl` separately for attribution;
- final setup close reason.

`FundingApplied` is allocated only when the setup was exposed at the settlement instant
according to the native engine's verified convention. Before emitting the final flat
`ReconciliationCompleted`, the adapter MUST drain native funding adjustments through the
close watermark; that reconciliation is its attestation that no attributable settlement
remains queued. Therefore re-arm and FULL→SCOUT occur only at final reconciliation after
`PositionClosed`, never at the provisional callback.

A nonzero funding settlement arriving with no attributable live setup is stored in the
global exact dedupe set **and** in a durable `unresolved_funding_settlement_ids` recovery
block. Later flat reconciliations cannot clear that block or admit a new signal. Resolution
requires explicit operator/adapter attribution; the reducer never retroactively and
silently flips risk mode. Zero-valued late settlements are deduped but do not create the
block.

## 12. Versioned snapshot and restart contract

### 12.1 `mms_state/1` logical schema

The serialized snapshot MUST contain at least:

| Group | Required fields |
|---|---|
| Identity/version | `schema_version`, `strategy_version`, `strategy_id`, `instrument_id`, config hash, snapshot ID, created-at UTC, checksum. |
| Three dimensions | `risk_mode`, `position_build`, `order_lifecycle`. |
| Entry signal memory | armed side/count, touch bar ID, reaction bar OHLC/ID, last two final H1 bars needed by wick rules, previous `%K/%D`. |
| Setup | setup ID/direction/status, immutable `setup_start_equity`, exposure multiplier, base/add-on target notionals, rounded requested quantities, trigger policy and winning trigger, add-on opportunity/lock flags, close reason. |
| Virtual legs | Base/add-on actual quantities, fill VWAPs, unique fill IDs, original and remaining quantity per execution, durable chronological fill order, stop levels, partial reduction totals. |
| Orders | Every order's creating setup ID, logical role, intent/correlation/client/venue IDs, requested/filled/remaining quantities, status, deadline, replacement lineage, execution parameters, and protected entry execution for add-on-stop children. |
| PnL | Realized price PnL by leg, commissions, funding settlements/IDs, slippage cost, add-on-stop PnL, and current net PnL. |
| Idempotency | Last durable source sequence per source, an insertion-ordered window of the 256 most recent transport event IDs, globally exact processed execution/funding settlement IDs, and emitted intent keys. |
| Recovery/outbox | Recovery flag, last reconciliation as-of, observed signed drift, unresolved funding IDs, and durable unacknowledged intent outbox. |

Enums serialize by stable string name, quantities/prices/money as decimal strings,
and timestamps as timezone-aware UTC ISO-8601. Map ordering must not affect checksum.

### 12.2 Persistence and recovery

1. Reducer transition, dedupe markers, and emitted intents are committed atomically to
   the snapshot/outbox before an external order side effect.
2. The wrapper uses the stored deterministic client-order ID to submit or query; it
   does not generate a new order after an uncertain acknowledgment.
3. On restart, deserialize and verify checksum/schema/config, retain that verified SHA-256
   as a runtime attestation, require `RecoverySnapshotLoaded.checksum` to match it, set
   recovery mode, then reconcile real position and all open orders before new bars.
4. A snapshot with a newer unknown schema is rejected. An older schema requires an
   explicit, tested pure migration; missing fields never default risk mode to FULL.
5. Corrupt or missing state for a scope with possible venue exposure blocks trading
   and requests reconciliation/fail-safe handling.
6. Restore must preserve FULL, SCOUT, BASE, PYRAMIDED, BASE_LOCKED, and every pending
   lifecycle exactly.
7. An order/fill callback or rich reconciliation row acknowledges only its matching
   intent. Bare open IDs do not delete the outbox. If venue truth proves an INTENDED
   order absent, restart re-emits the exact same stored intent/client ID; it never mints
   another strategic order.
8. Current `mms_state/1` migration may infer a missing order `setup_id` only from
   provably-current active/setup-reference/outbox evidence. Ambiguous historical terminal
   rows remain unattributed and can never mutate the current setup. A multi-fill legacy
   leg without durable FIFO order fails restore rather than inventing chronology.

The bounded transport-ID window prevents snapshot work from growing quadratically during
long H1 runs. Eviction does not weaken replay safety: durable per-source high-water marks
reject older transport replays, while quantity/PnL-changing `execution_id` values and
funding `settlement_id` values are retained exactly for the lifetime of the scope. A source
which cannot guarantee monotonic durable sequences is not compatible with this snapshot
schema and must use a durable journal before entering the reducer.

Canonical serialize→deserialize→serialize MUST be byte-stable and state-equal; two fresh
reducers with the same config also have identical initial snapshots (the initial timestamp
is a fixed UTC sentinel). Required round-trip fixtures include
FULL + PYRAMIDED + ADDON_PENDING with partial fill, funding, and an outbox item, plus a
separate SCOUT + BASE_PENDING snapshot. `SCOUT + PYRAMIDED` is invalid and restore must
reject it rather than normalize it.

## 13. P3 execution-profile contract — measured hard gate

P2 freezes domain behavior but does not invent fills. P3 MUST run a minimal synthetic
PoC and record the selected profile ID/version here and in every result before the
research wrapper is eligible.

### 13.1 Preregistered profiles

| Profile | Purpose | Allowed semantics |
|---|---|---|
| `EQUIVALENCE_ON_CLOSE_V1` | Tier-1 comparison with v1/backtesting.py | Signal on final bar close and synthetic on-close fill matching `trade_on_close=True`; zero commission/funding/latency in the equivalence fixture. It is not research evidence. |
| `RESEARCH_CAUSAL_NEXT_CLOSE_V1` | H1 MMS-inspired research | A final `BarClosed` is delivered at Binance close time. The order submitted then has a positive 1 ns latency and, with an H1-only external-bar stream, fills at the **next H1 Close**. It MUST NOT be called a next-open fill. |

The research profile MUST NOT use the just-closed OHLC to generate a signal and then
pretend an already elapsed price was available. If bar-only simulation cannot provide
a causal post-close fill without a custom matching engine, P3 fails and dependent
work stops.

### 13.2 Required P3 decisions and fixtures

| ID | Must be demonstrated and recorded |
|---|---|
| `P3-TIME-1` | A raw CCXT OHLCV timestamp is verified as bar-open time for the actual ingestion path. |
| `P3-TIME-2` | The logical H1 boundary is `open_time + 1h`; Binance's inclusive `closeTime`, used by its Nautilus adapter, is `open_time + 1h - 1 ms`. Nautilus `ts_event`/`ts_init` mapping represents availability at that close, is monotonic, and does not expose the bar at raw open time. |
| `P3-FILL-1` | Exact market-order fill event/price after an `on_bar`/bar-close submission with zero latency. |
| `P3-FILL-2` | The same with nonzero deterministic latency. |
| `P3-GAP-1` | Stop behavior when the next executable price gaps beyond the level; realized fill, not the stop level, determines loss. |
| `P3-OHLC-1` | High-first/low-first path and a bar touching TP and SL are measured with `bar_adaptive_high_low_ordering=false` and `true`. |
| `P3-OHLC-2` | One policy/value is preregistered for research and cannot vary by run outcome. Because OHLC cannot reveal the real path, disclose same-bar results as a smoke/model assumption; eligibility later requires finer data or a separately preregistered policy. |
| `P3-LOOK-1` | Perturbing bars after decision time cannot change earlier intents, quantities, fills, or snapshots. |

### 13.3 Pinned P3 observation — `nautilus_trader==1.230.0`

The deterministic fixture is
[`tests/test_nautilus_poc.py`](../../tests/test_nautilus_poc.py), and the deliberately
small reusable boundary is
[`algo_bot/engine/nautilus_poc.py`](../../algo_bot/engine/nautilus_poc.py). The fixture
uses a zero-fee BTCUSDT perpetual, venue OMS `NETTING`, an L1 MBP book,
external H1 LAST bars, `bar_execution=True`, full deterministic limit fills, zero
fill-model slippage, and self-managed reduce-only SL/TP orders rather than a bracket.

Measured results and binding choices:

1. **Timestamp source and mapping.** Installed CCXT's Binance parser maps normalized
   OHLCV element zero from Binance `openTime`; the repo RAW writer persists it unchanged.
   CCXT omits Binance `closeTime`. For interval `I_ms`, the P3 boundary reconstructs
   `close_ns = (open_ms + I_ms - 1) * 1_000_000` and assigns both `Bar.ts_event` and
   `Bar.ts_init` to it. This matches the pinned Nautilus Binance adapter's use of
   `BinanceKline.close_time`; the next bar opens 1 ms later. Historical ingestion
   latency is zero in this mapping. A live adapter may have `ts_init > ts_event`.
2. **No look-ahead at delivery.** `on_bar` observes the engine clock equal to the
   close-mapped `bar.ts_init`, never raw open time. Perturbing a later bar after the
   characterized decision and fill leaves the earlier intent timestamp, quantity,
   bars, and fill unchanged.
3. **Zero latency is equivalence-only.** Passing `latency_model=None` is Nautilus's
   true zero-latency path. A market order submitted from `on_bar` fills at that same
   bar's `Close`, with the same nanosecond timestamp. The exchange has already
   processed that bar's O/H/L/C before publishing it to the strategy. This is exactly
   the behavior needed to characterize `trade_on_close=True`, but it MUST NOT be used
   as research evidence or described as next-open execution.
4. **Selected causal research entry.** `RESEARCH_CAUSAL_NEXT_CLOSE_V1` uses
   `LatencyModel(base_latency_nanos=1)`. In an H1-only bar stream the command is no
   longer due in the signal bar's settlement pass. At the next H1 event the engine
   processes that bar and then drains the command, so the fill price and event time are
   the next bar's `Close`/`ts_init`, not its `Open`. This intentionally conservative
   one-bar delay is the selected Beta research policy. Adding finer execution data or
   engine timers changes the event grid and requires a new versioned profile and PoC.
5. **Stops and gaps.** During ordinary H/L traversal, a stop-market fills at its
   trigger. If the next bar opens through the stop, it fills at that bar's `Open`, not
   the theoretical stop; therefore realized risk can exceed the modeled cap. Every
   synthetic O/H/L/C point carries the enclosing bar's close timestamp, so the event
   timestamp alone MUST NOT be mislabeled as the intrabar open time.
6. **OHLC order.** With `bar_adaptive_high_low_ordering=False`, processing is always
   O→H→L→C. With `True`, the extreme strictly closer to Open is processed first; if
   distances tie, the 1.230.0 implementation chooses Low first. The research profile
   preregisters `True`. Thus a long bar touching TP and SL resolves to SL when Low is
   closer (and on a tie), but to TP when High is closer. Short positions inherit the
   inverse economic asymmetry. This is a deterministic heuristic, not reconstruction
   of the real intrabar path, and strategy code may not switch it after seeing results.
   Same-bar outcomes remain a disclosed smoke/model assumption; eligibility-quality
   research later requires finer-grained data or a separately preregistered policy.

`FundingRateUpdate` is market data in 1.230.0. The Cython `BacktestEngine` used by P3/P5
does not settle perpetual funding; it only delivers/caches the update. The Rust/PyO3
`BacktestEngine`, characterized later for P7/P8, does settle funding as signed
`PositionAdjusted(FUNDING)` events. P3 therefore does not fabricate a settlement from a
rate update. A real PyO3 settlement fixture and domain-ledger dedupe remain mandatory
before any cost claim.

The following scoped command passed on Python 3.12 with the pinned engine:
`pytest -q tests/test_nautilus_poc.py` (9 tests). This closes P3 only; it does not decide
P4 OMS or authorize P5 equivalence claims.

## 14. P4 position/OMS result — `OMS-A` selected

P4 compared two hypotheses over a venue NETTING account on pinned
`nautilus_trader==1.230.0`:

| Hypothesis | Strategy OMS | Venue | Leg ownership | Result |
|---|---|---|---|---|
| `OMS-A` | NETTING | NETTING | Pure state machine owns virtual base/add-on legs. | **Selected** (`OMS-A_NETTING_VIRTUAL_LEGS_V1`). |
| `OMS-B` | HEDGING | NETTING | Nautilus virtual strategy positions with `use_position_ids=False`. | **Rejected**: whole-net Close-All leaves offsetting open virtual positions. |

Nautilus strategy OMS HEDGING is **not** Binance account Hedge Mode. No conclusion may
be inferred from the shared word “hedging”. The account remains Binance one-way/NETTING,
where futures `reduce_only` is available.

The P4 acceptance contract on the pinned engine/adapter was:

1. A base protective stop can close the whole current net exposure with
   `close_position=True` semantics.
2. An add-on protective stop can reduce only actual add-on filled quantity with
   `reduce_only=True`.
3. `close_position` is never combined with `reduce_only` on one order.
4. No bracket-order assumption is made; conditional orders are submitted and canceled
   explicitly.
5. Partial add-on fills update aggregate active stop coverage to cumulative actual fill
   only.
6. Add-on SL leaves the base and its full base protection intact.
7. TP/base SL cancels all surviving conditional orders.
8. Reversed event ordering, duplicate fills, cancel/fill races, and gaps through both
   stops cannot over-reduce or reverse the net position.
9. Restart between submit and fill reconciles by stable client ID without duplication.
10. The result works in the real backtest engine and an adapter-level deterministic
    harness without
    relying on Binance Hedge Mode.

### 14.1 Measured selection and rejection evidence

The real `BacktestEngine` probe used venue OMS `NETTING`, `use_position_ids=False`,
one base BUY `1.000`, one add-on BUY `1.000`, then one whole-net SELL `2.000`
associated with the base position ID:

- strategy OMS `NETTING` produced one position record, zero portfolio net, and no
  open position: the venue and strategy accounting agree;
- strategy OMS `HEDGING` produced three position records: the original base closed,
  the add-on remained virtually LONG `1.000`, and the excess of the whole-net fill
  opened a virtual SHORT `1.000`. Portfolio net was zero but two offsetting virtual
  positions remained open. This cannot represent Binance Close-All truth and rejects
  `OMS-B` for this strategy.

This is an engine observation, not a hand-written matching-engine assumption. The
quantity-only companion probe exists solely to enumerate protective-order permutations
which the bar backtester cannot represent as Binance server-side Close-All.

### 14.2 Frozen order mapping

Both roles are explicit independent conditional orders; linked bracket/OCO lists are
forbidden by the pinned Binance adapter.

| Logical role | Nautilus / Binance instruction | Quantity | Flags | Trigger and TIF |
|---|---|---|---|---|
| Base/setup SL | `StopMarketOrder` / `STOP_MARKET`; SELL for LONG, BUY for SHORT | Latest real quantity is supplied locally for risk reference; Binance omits quantity and resolves the complete current position at trigger time | `params={"close_position": True}`; `reduce_only=False`; wire `closePosition=true` | Base-fill VWAP ±2%; `LAST_PRICE` → Binance `CONTRACT_PRICE`; `GTC`; `NO_TRIGGER` emulation |
| Add-on SL child | `StopMarketOrder` / `STOP_MARKET`; SELL for LONG, BUY for SHORT | Exactly one unique partial add-on fill delta | `reduce_only=True`; no `close_position`; wire includes quantity and `reduceOnly` | Frozen structural level from §6.3; `LAST_PRICE` → Binance `CONTRACT_PRICE`; `GTC`; `NO_TRIGGER` emulation |

`close_position` and `reduce_only` MUST NOT coexist on one order. The adapter test
confirms that Close-All omits both `quantity` and `reduceOnly` on the Binance request,
while an add-on child sends its exact quantity and `reduceOnly`. The adapter also
rejects `close_position` on MARKET orders and rejects the combined flags.

### 14.3 Partial fills, gaps, and cleanup

The selected add-on policy is
`INCREMENTAL_REDUCE_ONLY_PER_FILL_V1`: after every unique actual partial fill, append
one child at the same structural trigger for that fill delta. The active logical group's
sum therefore equals cumulative actual add-on fill. This is the safe concrete meaning
of “update cumulative coverage”; it is not a singular cancel/replace. The pinned adapter
rejects STOP_MARKET modification. Cancel-old-first would leave a protection gap, while
submit-new-first with cumulative quantity would temporarily over-cover. Incremental
children have neither interval.

All six orderings of one base Close-All plus two partial-fill children (`0.400` and
`0.600`) end at zero without negative logical quantity or reversal. If children fill
first, they remove at most the add-on and the base Close-All resolves the remainder. If
the base fills first, the system cancels all children; a venue-racing `reduce_only`
child cannot open or reverse a flat position. A TP likewise closes current net and
explicitly cancels every surviving conditional order. Duplicate executions and
terminal-order replays are idempotent.

### 14.4 Restart evidence and lifecycle boundary

The minimal P4 checkpoint preserves active protective specs, stable client-order IDs,
known execution IDs, canceled IDs, and the next deterministic child sequence. A
round-trip restore reconciles expected IDs against venue IDs, a replayed partial fill
does not create another child, and the next distinct fill receives the next stable ID.

P4 classifies missing and orphan IDs but intentionally does not implement asynchronous
submit/reject/cancel/timeout policy. Those races, durable outbox behavior, bounded retry,
and fail-safe escalation belong to P6. They may change exposure only when a unique fill
is reconciled; P4's selection is not a claim that production recovery is already built.
There is also an unavoidable operational interval between an add-on fill and acceptance
of its child stop; P6 must fail closed if protection cannot be installed. No exchange
credentials or live-capital call was used.

Evidence is executable in
`algo_bot/engine/nautilus_oms_poc.py` and
`tests/test_nautilus_oms_poc.py`. The scoped gate passed on Python 3.12:
`pytest -q tests/test_nautilus_oms_poc.py` (16 tests). This closes the P4 model-choice
gate and is synchronized to ADR-014 Decision 9; it does not close P6 lifecycle safety.

## 15. P5 Tier-1 compatibility result — exact scoped equivalence

P5 adds `algo_bot/engine/nautilus_adapter.py`, a deliberately narrow adapter from the
existing `StrategyBase.on_bar(df) -> Signal` contract to the real Nautilus
`BacktestEngine`. It is an equivalence shim only; it contains no MMS v2 thesis,
sequentiality, virtual-leg accounting, or protective-order policy.

The only supported profile is `EQUIVALENCE_ON_CLOSE_V1`:

- one instrument, strategy OMS NETTING and venue OMS NETTING;
- external close-timestamped bars produced by the P3 CCXT boundary;
- the first source bar is a common warm-up, matching the pinned `backtesting.py`
  wrapper's first `next()` call on bar index one;
- `Signal()` and explicit `action="hold"` are no-ops;
- market entry with an explicit positive whole-unit `Signal.size`, plus a full market
  exit while a position exists;
- zero commission, funding, latency, and fill-model slippage;
- no stops, targets, trailing logic, reversal, or pyramiding.

The adapter fails before making an unsupported claim when size is `None`, fractional,
or otherwise ambiguous under `backtesting.py`'s overloaded equity-fraction convention;
when TP/SL/protective metadata appears; when pyramiding is declared; or when a second
entry arrives while non-flat. Different stop/intrabar models are therefore explicitly
outside P5 equivalence, not silently compared.

The acceptance tolerances were frozen in `TIER1_EQUIVALENCE_TOLERANCES` before the
fixture ran:

| Compared stream/value | Frozen tolerance |
|---|---:|
| Decision count, action, side, and ordering | exact |
| Intent timestamps | `0 ns` |
| Order and individual-fill counts | exact |
| Fill timestamps | `0 ns` |
| Fill prices | `0 ticks` on the tick-aligned fixture (stronger than the one-tick ceiling) |
| Final equity | absolute `1e-8 USDT` |
| Total PnL | absolute `1e-8 USDT` |

The deterministic fixture uses five BTCUSDT-perpetual H1 bars, explicit quantity
`1.000`, one LONG entry at `50_100`, and one exit at `50_300`. Both engines consume the
same frame reconstructed from Nautilus-rounded bars and indexed by Binance inclusive
close time. The complete four-decision streams match; both execution streams contain
exactly two market orders and two fills at identical timestamps/prices; both finish
FLAT with PnL `200 USDT` and equity `1,000,200 USDT`.

The native artifact retains the profile ID, native engine result, decisions, intents,
bar-close marked equity, orders, individual fills, positions, account events, final
equity, and total PnL. P8 will wrap this information with versions and hashes; P5 does
not prematurely invent that result schema.

The legacy runner had two independent close controls: the real
`Backtest(..., trade_on_close=...)` broker flag and a wrapper class attribute read from
strategy params. `run_backtest` now passes its engine flag into `make_bt_wrapper`, making
the explicit-exit branch use the same policy; direct callers retain the old parameter
fallback. A regression test covers a contradictory strategy parameter overridden by the
engine policy.

Evidence: `tests/test_nautilus_adapter.py` (7 tests). The scoped Python 3.12 gate passed
with exact equivalence and all unsupported-surface fail-fast cases. This earns trust for
the Tier-1 market-only shim; it is not general stop equivalence and does not close P6/P7.

### 15.1 P7 PyO3 wrapper profiles — safe smoke, not Binance parity

The native-v2 wrapper uses the Rust/PyO3 `BacktestEngine`, because pinned Cython does not
settle perpetual funding. Two measured limitations prevent an eligible backtest profile in
1.230.0:

1. PyO3 accepts `params={"close_position": true}` but the simulator ignores the Binance
   Close-All semantic. A STOP_MARKET quantity 1 over a net position of 2 fills only 1.
   `Strategy.close_position()` submits an immediate MARKET and is not a conditional-stop
   substitute.
2. `StaticLatencyModel(1 ns)` also delays protective orders created by fill callbacks
   until the next H1 event. Directly reusing P3's research latency would create a full-bar
   unprotected window.

P7 therefore freezes two separately named, always-ineligible profiles:

| Profile | Binding behavior |
|---|---|
| `PYO3_WRAPPER_NEXT_CLOSE_ZERO_LATENCY_SMOKE_V1` | Engine latency is zero. The wrapper queues only strategic market entry/exit generated by `BarClosed(N)` and submits them at the start of `BarClosed(N+1)`, producing the next-Close fill. Protective orders are submitted synchronously from the fill path before the current bar is evaluated by the domain machine. |
| `PYO3_NETTING_DECOMPOSED_CLOSEALL_SMOKE_V1` | Every unique actual base/add-on fill delta receives a base-level STOP_MARKET `reduce_only=True` child; an add-on fill also receives its structural reduce-only child. TP/full-exit coverage follows the same actual-fill ledger. Native reduce-only clipping prevents reversal; all surviving helpers are canceled at FLAT. |

The decomposed group is deliberately not the live P4 order trace. Continuous and gap
fixtures show it reaches net zero without reversal even when add-on and base helpers trigger
in either order, but it cannot prove Binance `closePosition` parity. Building a custom
matching engine to hide this difference is forbidden.

The wrapper remains thin:

- it composes the pure `MastermindStateMachine`, converts final native callbacks into
  typed domain events, and converts domain intents into native commands;
- stable client/correlation IDs and the native cache, not the original Python order object,
  are the source of order truth; one native `OrderFilled` callback is classified as partial
  or complete from the cached cumulative status;
- before every domain-facing callback, position close and stop, it drains unseen
  `PositionAdjusted(FUNDING)` adjustments by event ID so funding reaches P6 before setup
  finalization;
- reports are assembled from `engine.cache` objects/events. In 1.230.0 PyO3 the mixed
  Python helpers for fills/order-fills/account reports are not usable with PyO3 objects;
  cache `to_dict()` data is the pinned compatibility boundary;
- restart first restores the P6 snapshot/outbox, then reconciles stable client IDs and the
  real net position before accepting a new bar.
- canceling a logical order also removes any matching strategic MARKET command still queued
  for a later bar. A canceled add-on/base intent may never be submitted from stale scheduler
  state after the setup has closed.

Production calls use `MastermindStateMachine.handle()`: every accepted transition has a
canonical pre-transition checkpoint and transactional rollback. The P9 offline benchmark
may opt into `handle_without_snapshot()` plus a compact transition observer to avoid retaining
tens of thousands of full snapshots. That path still validates every invariant and aborts the
run on the first transition error; suspicious exposure-increasing late fills retain an
explicit rollback checkpoint so the fail-safe close can be emitted. It is a bounded,
development-only performance profile and MUST NOT replace the production/restart path.

Every result from these profiles must contain `SMOKE_ONLY`, `eligible=false`, both profile
IDs, and reason `BACKTEST_CLOSEALL_NOT_BINANCE_PARITY`. Missing funding boundaries,
mark-price history, commission/fill configuration, or a non-PyO3 backend add further
fail-closed reasons.

## 16. Example event sequences

### 16.1 FULL base TP

`BarClosed(touch) -> BarClosed(reaction) -> SubmitBaseOrder -> Accepted -> Filled ->`
`BASE/NONE -> TP exit -> partial/full fills -> PositionClosed(TP, net>0) ->`
`cancel orphans -> setup-scoped reconcile FLAT/NONE`. Risk remains FULL throughout;
`PositionClosed` alone does not decide it.

### 16.2 FULL base SL, SCOUT losses, then re-arm

`FULL base fill -> full BASE_SL -> PositionClosed -> final flat reconciliation` changes
FULL→SCOUT. A later SCOUT
base `BASE_SL` remains SCOUT. A later SCOUT TP whose setup net is zero after funding
remains SCOUT. Only a later complete SCOUT TP with strictly positive finalized net PnL
changes SCOUT→FULL.

### 16.3 Partial add-on fill and add-on SL

`FULL/BASE/NONE -> StochCross -> BASE/ADDON_PENDING -> 40% fill ->`
`PYRAMIDED/ADDON_PENDING + add-on stop(q=40%) -> cancel remainder ->`
`PYRAMIDED/NONE -> add-on stop partial fill -> REDUCE_PENDING -> full add-on reduction ->`
`BASE_LOCKED/NONE`. A later Stoch cross emits nothing. Base TP may still close the
setup; if it is net profitable, FULL merely stays FULL.

### 16.4 Rejected add-on and duplicate events

One simultaneous candle+Stoch event under `FIRST_OF_CANDLE_OR_STOCH` emits one candle
intent. `OrderRejected` with zero fill returns `BASE/NONE`; build never became
PYRAMIDED. Replaying the same trigger or rejection event changes nothing. The consumed
FIRST_OF opportunity is not reassigned to the later Stoch branch.

### 16.5 Gap through both stops

In PYRAMIDED, both stop orders become marketable. If the add-on reduction fills first,
its unique fill reduces only `q_addon`; the base stop then closes the remainder and
final reason is `BASE_SL`. If the base stop fills first, real quantity is zero and the
add-on stop is canceled/capped to zero. Either sequence ends FLAT without a reversed
position; FULL changes to SCOUT only after finalization.

### 16.6 Restart between submit and fill, including SCOUT

Snapshot/outbox contains `SubmitBaseOrder(intent-7, client-7)` and
`BASE_PENDING`. The process dies after venue receipt but before acknowledgment.
Recovery restores the exact prior risk mode (including SCOUT), queries `client-7`,
and applies the returned unique fill or status. If the venue is empty before submit, it
replays the same durable `intent-7/client-7`; if a bare presence ID is returned, the
outbox remains until rich truth or a callback acknowledges it. It never emits `client-8`
for the same strategy intent. New `BarClosed` signals remain blocked until reconciliation
completes.

### 16.7 Out-of-order finalization and late funding

If a whole-exit fill and flat reconciliation arrive before `PositionClosed`, the reducer
retires all now-absent close/protection intents, keeps `FLAT/EXIT_PENDING`, and waits for
the cost/reason summary. If a late entry fill invalidates a provisional summary, it emits
an actual-sign fail-safe close and requires a corrective summary. A nonzero funding event
after final setup removal creates the durable unresolved-funding block; another flat
reconciliation cannot clear it.

## 17. Acceptance and test matrix

All domain fixtures are deterministic. “P6” denotes pure state-machine tests; stages
P3/P4/P5 own engine/execution fixtures but are still mandatory for end-to-end Beta.

| ID | Stage | Fixture / action | Required assertion |
|---|---|---|---|
| `RISK-01` | P6 | FULL base TP, positive net | FLAT; risk FULL. |
| `RISK-02` | P6 | FULL full base SL | FLAT; FULL→SCOUT only after finalized close. |
| `RISK-03` | P6 | SCOUT profitable complete TP | SCOUT→FULL. |
| `RISK-04` | P6 | SCOUT loss or net-zero TP | Remains SCOUT. |
| `RISK-05` | P6 | Partial TP/close | No re-arm. |
| `RISK-06` | P6 | Risk/manual/liquidation/engine-error close in each mode | Risk mode unchanged. |
| `SCOPE-01` | P6 | Long loss then short on same instrument | Short starts SCOUT. |
| `SCOPE-02` | P6 | Loss on instrument A, setup on B | B's mode unchanged. |
| `ADD-01` | P6 | Each enum policy with qualifying/non-qualifying bars | Exactly specified trigger behavior. |
| `ADD-02` | P6 | Both triggers same bar under OR/AND | At most one add-on intent; deterministic provenance. |
| `ADD-03` | P6 | Add-on rejected with zero fill | Returns BASE, no virtual exposure change. |
| `ADD-04` | P6/P4 | Partial add-on fill | PYRAMIDED+ADDON_PENDING; incremental stop-group coverage equals cumulative filled quantity only. |
| `ADD-05` | P6 | Add-on SL | BASE_LOCKED; realized loss retained; no re-add. |
| `ADD-06` | P6 | Trigger in SCOUT | No add-on intent. |
| `ADD-07` | P6 | Candle wick fixtures in §6.3 | Exact 1% accepted; >1%, zero, wrong side rejected; no clamp. |
| `ADD-08` | P6 | Stoch two-closed-bar wick fixture | Uses exactly prior+trigger bar and actual fill VWAP. |
| `ADD-09` | P6 | Fill slippage turns valid preview invalid | Immediate capped `ReduceAddon`, lock, no fake stop. |
| `BUILD-01` | P6 | Trigger/submitted/accepted without fill | Build state does not change. |
| `BUILD-02` | P6 | First partial add-on fill | BASE→PYRAMIDED only on unique positive fill. |
| `EXIT-01` | P6 | PYRAMIDED + base SL | FLAT + SCOUT after finalized close; all quantities zero. |
| `EXIT-02` | P6 | PYRAMIDED + TP after prior add-on loss | Correct complete setup net PnL controls SCOUT re-arm. |
| `EXIT-03` | P3/P4 | Gap through both stops in both event orders | No over-reduction/reversal; correct final reason/PnL. |
| `EXIT-04` | P3 | TP and SL on one bar | Result follows preregistered execution profile/ordering flag. |
| `EXIT-05` | P6/P4 | FLAT with residual conditional orders | Stable cancel intents; reconciliation ends with no orphans. |
| `OMS-01` | P4 | Base `close_position` fixture | Closes exact current net exposure, never reverses. |
| `OMS-02` | P4 | Add-on `reduce_only` fixture and partials | Reduces no more than actual add-on fill; base survives. |
| `OMS-03` | P6/P4 | Reject/cancel/timeout races | P4 freezes quantity/ID invariants; P6 proves asynchronous safe recovery; exposure changes only on fills. |
| `IDEM-01` | P6 | Replay every event type | State/intents/PnL equal single delivery. |
| `IDEM-02` | P6 | Same execution under distinct transport event ID | Execution ID still prevents double fill/PnL. |
| `TIMEOUT-01` | P6 | Base/add-on timeout with zero and partial fills | Correct FLAT/BASE/PYRAMIDED result; no invented fill. |
| `TIMEOUT-02` | P6 | Timeout reconciles, then a later manual exit reconciles | Historical timeout cannot reset `EXIT_PENDING`. |
| `SNAP-01` | P6 | Round-trip FULL, SCOUT, BASE, PYRAMIDED, BASE_LOCKED and all pending states | State equality and canonical serialization. |
| `SNAP-02` | P6/P4 | Restart between submit and fill | Reconcile same client ID; no duplicate order. |
| `SNAP-03` | P6 | Restart in SCOUT | Never defaults to FULL. |
| `SNAP-04` | P6 | Corrupt/newer snapshot | Fail closed; no risk-increasing intent. |
| `SNAP-05` | P6 | Wrong well-formed recovery checksum attestation | Recovery/fail-safe; checksum must match verified deserialize result. |
| `SNAP-06` | P6 | Non-lexical multi-fill FIFO restart | Same remaining per-execution inventory before/after restore. |
| `OUTBOX-01` | P6 | Crash before submit; venue empty | Replay exact stored intent/client ID. |
| `OUTBOX-02` | P6 | Bare presence ID, rich ack, fill ack, and flat cancel truth | Ack only proven intents; final outbox drains without losing replayability. |
| `SCOPE-03` | P6 | Stale reconciliation/PositionClosed from setup 1 during setup 2 | Setup 2 ledger/lifecycle/risk unchanged; reconciliation only. |
| `DRIFT-01` | P6 | Actual sign/quantity differs twice, including opposite side | Cancel stale orders; replace with close side/quantity derived from latest signed truth. |
| `FINAL-01` | P6 | Flat reconciliation arrives before PositionClosed | Retain setup and retire stale close outbox until summary arrives. |
| `FINAL-02` | P6 | Entry fill after close initiation/provisional summary | No logical reopen; invalidate provisional result and require corrective finalization. |
| `FUND-02` | P6 | Nonzero funding after setup removal, then another flat reconcile | Durable unresolved block remains; no retroactive risk transition. |
| `CAP-01` | P6/property | Random valid event sequences | One base, ≤one add-on, entry committed exposure ≤x2 FULL and ≤x0.1 SCOUT. |
| `CAP-03` | P6 | Adverse fill-price gap at valid target quantity | Actual-notional telemetry may exceed target; no false target-cap error. |
| `CAP-02` | P6/property | Partial/reordered protective fills | Protective quantity ≤real open quantity; position never reverses. |
| `SIZE-01` | P6 | Equity/price example in §7.4 plus precision steps | Immutable target notionals; round down; no equity resize. |
| `PNL-01` | P6 | TP gross-positive but net-negative after commission/funding/add-on SL | SCOUT does not re-arm. |
| `FUND-01` | P7/P8 | Native PyO3 funding settlement fixture with duplicate settlement | One signed allocation; correct net PnL; duplicate ignored. |
| `TIME-01` | P3 | CCXT open timestamp to H1 close mapping | No bar delivered before actual close. |
| `TIME-02` | P3 | Future-bar perturbation | Earlier intents/fills unchanged; no look-ahead. |
| `EQUIV-01` | P5 | Legacy simple strategy, synthetic data, zero costs/latency, on-close profile | Exact signal direction/count/timestamps and order/fill count; price ≤1 tick tolerance; equity/PnL within preregistered numeric tolerance. |
| `P7-SCHED-01` | P7 | Strategic market order plus fill-created protection | Entry fills next H1 Close while protection is active without another H1 delay. |
| `P7-SCHED-02` | P7 | Logical cancel before a queued strategic MARKET is sent | Matching unsent command is removed and can never enter after final flat. |
| `P7-OFFLINE-01` | P6/P7 | Snapshot-free benchmark transition raises | First error is recorded with event/setup/order context and the suite aborts; no later routing. |
| `P7-CLOSEALL-01` | P7 | PyO3 `params.close_position` characterization | Simulator limitation stays detected; eligible mode fails closed. |
| `P7-SMOKE-01` | P7 | Decomposed base/add-on stops, continuous and gap traversal | Native net ends zero, never reverses; result remains NOT_ELIGIBLE. |
| `P7-REPORT-01` | P7/P8 | Cache-derived orders/fills/positions/account/funding | Complete auditable ledgers without broken mixed-backend report helpers. |

No benchmark or sweep is eligible if any invariant test fails, the backend is not PyO3,
required funding boundaries/mark prices or commission/fill configuration are missing, the
execution profile is unselected, or live/backtest Close-All parity is unresolved.
Approximate costs or the P7 decomposed-stop profile may label only a mini result as
`SMOKE_ONLY / NOT_ELIGIBLE`.

## 18. Traceability and change control

Any implementation MUST expose counters for base entries, add-on facts/intents/
submissions/fills/rejections, add-on SLs, full base SLs, FULL→SCOUT transitions,
SCOUT setups, SCOUT→FULL re-arms, time in SCOUT, maximum committed/gross exposure,
costs by type, and invariant violations (expected zero).

Changing any of the following requires a new strategy/spec version and new fixtures:
SCOUT add-ons, risk-mode scope, re-arm predicate, trigger enum semantics, wick-pair
selection, percentage caps, fill-driven build semantics, or snapshot schema. P3's
measured execution choice and P4's selected OMS model must be recorded as gated
appendices to this document rather than hidden in adapter defaults.
