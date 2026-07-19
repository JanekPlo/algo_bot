# MR-Session 4 full v2 in-sample sweep — preregistration draft

> **Status:** **DRAFT / BLOCKED — NOT FROZEN**  
> **Last updated:** 2026-07-15  
> **Scope:** BTCUSDT and ETHUSDT evaluated separately; H1 execution with M5 or
> M10 marking; native Nautilus bar fills; causal mark-price isolated-margin
> monitoring  
> **Authorization:** this document does **not** authorize the 528-run sweep

The inferential design below is fixed as the candidate contract, but the
preregistration is not yet valid. Operators must not create or use a frozen
manifest while any Session 4 blocker in
[Section 14](#14-current-freeze-blockers) remains. The runner enforces every
machine-checkable freeze condition; process evidence such as the completed
quality gate remains an operator responsibility. No strategy metric has been
produced, read, ranked, or used to revise this design.

## 1. Freeze identifiers

These identifiers are deliberately `PENDING`, rather than placeholders that
could be mistaken for evidence:

| Identifier | Current value |
|---|---|
| Preregistration SHA-256 | `PENDING — document is DRAFT` |
| Contract-core SHA-256 | `PENDING — lock-core inputs are incomplete` |
| Manifest-core SHA-256 | `PENDING — lock-core is blocked by missing inputs` |
| Manifest-provenance SHA-256 | `PENDING — prepare runs only from the clean tagged commit` |
| Git commit | `PENDING — final frozen commit does not exist` |
| Git tag | `PENDING — final preregistration tag does not exist` |
| `uv.lock` SHA-256 | `PENDING — captured by the final manifest` |
| Development-data hashes | `PENDING — ETHUSDT funding is missing` |
| Frozen Bybit-contract hash | `PENDING — contract artifact is missing` |

<!-- mr-session-4-manifest-core-sha256: PENDING -->

The freeze procedure is defined in Section 13. A commit or tag without a valid
manifest does not change this status.

The runner deliberately uses two hashes, so there is no circular-hash problem:

- `lock-core` builds a self-contained manifest core from the 528-run contract,
  profiles, runtime versions, `uv.lock`, runner-source hashes, frozen Bybit
  contracts, and development-only data metadata. It excludes this document and
  the Git tree, so its SHA-256 can be written into this document without
  changing the core.
- `prepare` runs after this document has been committed and tagged. It adds a
  separate provenance object containing the clean Git tree state, the one
  canonical preregistration tag pointing at that commit, and the final
  preregistration SHA-256. It then records `provenance_hash` beside the
  unchanged `manifest_core_hash`.

The provenance hash remains in the immutable manifest; it is not back-edited
into the already committed document. The exact ordering is frozen in Section
13 as `lock-core → write core hash here → commit/tag → prepare`.

## 2. Research question and permitted interpretation

The question is:

> Does the complete executable MMS v2 stack — base mean-reversion entry,
> sequential FULL/SCOUT exposure, one pyramiding add-on, and lower-timeframe
> marking — produce enough **post-cost, native-fill, mark-margin** in-sample
> evidence on BTCUSDT and ETHUSDT separately to justify a separately
> preregistered MR-Session 5?

MR-Session 4 is the last in-sample sweep in Phase 2. It is not a walk-forward
test, a holdout test, a production-readiness decision, or permission to trade.
The six strategy variants are ablations of one mechanism; 528 runs are not 528
independent hypotheses. Results may support only one of the precommitted
`SOLID`, `MARGINAL`, `FAILS`, or `INVALID / NO VERDICT` branches in Section 12.

The two instruments are independent strata:

- each starts with its own 10,000 USDT account;
- no signal, position, cash balance, risk budget, or margin is shared;
- no combined BTC+ETH portfolio metric is an eligibility input;
- cross-symbol comparisons use normalized metrics and paired configuration IDs,
  never nominal PnL equality.

## 3. Data windows, causality, and holdout policy

All boundaries are UTC and right-open.

| Role | Exact interval | Permitted use |
|---|---|---|
| H1 warm-up | `[2021-03-23 16:00, 2021-04-01 00:00)` | Exactly 200 H1 bars for indicators; trading disabled |
| Development | `[2021-04-01 00:00, 2025-07-01 00:00)` | The only Session 4 strategy and metric interval |
| Reserved future data | `[2025-07-01 00:00, +∞)` | Excluded from Session 4 strategy loading, features, metrics, ranking, and reporting |

The later matched start proposed in the kickoff is necessary because the local
ETHUSDT H1 history begins in March 2021. A 2020 matched development start cannot
provide the same 200-bar warm-up for both symbols from the current evidence set.

The strategy data path must enforce all of the following:

- H1 trade bars load the 200-bar warm-up and development rows only;
- M5 trade bars load development rows only;
- M10 is derived deterministically from the already truncated development M5
  frame, two consecutive M5 bars per M10 bar;
- historical funding loads development rows only; H1 mark-price loads exactly
  one additional pre-development H1 plus development rows, and that additional
  bar supplies only the last completed mark close for a funding settlement at
  the development boundary (never a strategy feature, signal, or metric);
- a reader stops after the final expected row and does not request the first
  reserved row;
- native data, features, per-run hashes, and metrics contain no timestamp at or
  after `2025-07-01 00:00:00Z`;
- the last development setup is flattened through the preregistered boundary
  controller, not valued using a later bar.

The independent H1 and M5 trade feeds are not silently reconciled. Before a
core can lock, the runner aggregates development M5 to H1 and freezes, for
Open/High/Low/Close, exact mismatch counts, beyond-one-tick counts, maximum
absolute/tick/basis-point deltas, a mismatch-ledger hash, and the derived-H1
hash. Timestamp grids must match exactly and the maximum price difference must
be at most `25 bps`; a larger difference fails data preflight. Volume mismatch
is reported separately and has no price-integrity threshold. This policy
accepts small revisions between independently fetched exchange intervals but
does not rewrite either source. Phase C may not conceal the frozen divergence
report when discussing M5/M10 effects.

### 3.1 Integrity-access disclosure

The original kickoff used the phrase “holdout unread.” That literal claim is no
longer accurate and must not appear in the frozen record.

During the 2026-07-15 runner/data audit, general integrity tooling accessed the
complete local CSV files. The audit computed or inspected full-file information
and exposed tail timestamps and price values after the `2025-07-01` boundary.
This was **operator/data-steward integrity access**, outside the strategy
evaluation lane. No Session 4 strategy was run on those rows; no feature,
strategy metric, candidate rank, parameter choice, or economic verdict used
them.

Consequently, the defensible claim is:

> `strategy_holdout_rows_read == 0`, while non-analytic operator integrity
> access to reserved rows has occurred and is disclosed.

This preserves a strategy-blind in-sample protocol, but it does not recreate a
strictly byte-unseen holdout. If MR-Session 5 requires a standard under which
even data-steward access contaminates a holdout, it must reserve a new forward
window. The future MR-Session 5 preregistration must make that choice explicitly.

## 4. Exact 528-run matrix

The inferential family is exactly:

```text
22 parameter sets × 6 MMS variants × 2 marking timeframes × 2 symbols = 528 runs
```

Multiplicity checks:

- 264 runs per symbol;
- 264 runs per marking timeframe;
- 132 runs per symbol × marking-timeframe stratum;
- 24 runs per parameter-set ID;
- 88 runs per variant ID.

The run seed is `20260715` for every run. The design is deterministic; there is
no runtime random sampling, Bayesian adaptation, early winner expansion, or
replacement point after seeing outcomes.

Run IDs have the form:

```text
{BTCUSDT|ETHUSDT}__{M5|M10}__{parameter_set_id}__{variant_id}
```

### 4.1 Literal parameter design

The 22 points are a sparse, preregistered design, not the Cartesian product of
all ranges. Points P01–P21 cover seven BB windows for each expiry and rotate the
seven standard-deviation values by fixed offsets. P22 repeats the conventional
central setting as an explicit anchor.

| ID | BB window | BB standard deviations | Arm expiry, H1 bars | Anchor |
|---|---:|---:|---:|---|
| `P01_W15_D18_E1` | 15 | 1.8 | 1 | no |
| `P02_W17_D19_E1` | 17 | 1.9 | 1 | no |
| `P03_W19_D20_E1` | 19 | 2.0 | 1 | no |
| `P04_W20_D21_E1` | 20 | 2.1 | 1 | no |
| `P05_W21_D22_E1` | 21 | 2.2 | 1 | no |
| `P06_W23_D23_E1` | 23 | 2.3 | 1 | no |
| `P07_W25_D24_E1` | 25 | 2.4 | 1 | no |
| `P08_W15_D20_E2` | 15 | 2.0 | 2 | no |
| `P09_W17_D21_E2` | 17 | 2.1 | 2 | no |
| `P10_W19_D22_E2` | 19 | 2.2 | 2 | no |
| `P11_W20_D23_E2` | 20 | 2.3 | 2 | no |
| `P12_W21_D24_E2` | 21 | 2.4 | 2 | no |
| `P13_W23_D18_E2` | 23 | 1.8 | 2 | no |
| `P14_W25_D19_E2` | 25 | 1.9 | 2 | no |
| `P15_W15_D22_E3` | 15 | 2.2 | 3 | no |
| `P16_W17_D23_E3` | 17 | 2.3 | 3 | no |
| `P17_W19_D24_E3` | 19 | 2.4 | 3 | no |
| `P18_W20_D18_E3` | 20 | 1.8 | 3 | no |
| `P19_W21_D19_E3` | 21 | 1.9 | 3 | no |
| `P20_W23_D20_E3` | 23 | 2.0 | 3 | no |
| `P21_W25_D21_E3` | 25 | 2.1 | 3 | no |
| `P22_W20_D20_E2_ANCHOR` | 20 | 2.0 | 2 | yes |

The explored values are therefore:

- BB window: `{15, 17, 19, 20, 21, 23, 25}`;
- BB standard deviations: `{1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4}`;
- arm expiry: `{1, 2, 3}` H1 bars.

The sparse design controls compute and multiple-search exposure while covering
the declared boundaries. It cannot identify every interaction in the full
`7 × 7 × 3 = 147` grid; no such claim is permitted.

### 4.2 Six MMS variants

| ID | Sequential FULL/SCOUT | Add-on | Trigger policy |
|---|---|---|---|
| `V1_BASE_ONLY` | off; base remains FULL | off | none |
| `V2_BASE_SEQ` | on | off | none |
| `V3_BASE_CC` | off; base remains FULL | on | confirming candle |
| `V4_BASE_STOCH` | off; base remains FULL | on | Stochastic cross |
| `V5_BASE_SEQ_CC` | on | on | confirming candle |
| `V6_BASE_SEQ_STOCH` | on | on | Stochastic cross |

`FIRST_OF_CANDLE_OR_STOCH`, `CANDLE_AND_STOCH`, and a combined
confirming-plus-Stochastic add-on variant are outside this experiment. They may
not be added after outcomes are visible.

### 4.3 Marking timeframes

Each parameter × variant × symbol combination runs once with M5 marking and
once with M10 marking. Both use H1 strategy decisions and H1 execution. M10 is
causally aggregated from the development-only M5 stream.

H1-only marking is an optional engineering diagnostic outside the 528-run
inferential family. If run, it requires a separate diagnostic manifest, must be
labelled non-inferential, and cannot be promoted into the Session 4 decision
because its outcome looks attractive.

## 5. Fixed strategy and instrument settings

The following values are fixed in all 528 runs:

- H1 strategy/execution timeframe;
- TA-Lib Bollinger Bands with SMA and the parameterized window/deviation above;
- Stochastic `14/3/3`, oversold `20`, overbought `80`;
- `require_reclaim=false`;
- base stop distance `0.02` from actual fill VWAP;
- FULL target exposure `1.0 × setup-start equity`;
- SCOUT target exposure `0.1 × setup-start equity`;
- at most one add-on, with target notional equal to the base target;
- add-on structural stop accepted only when its distance is at most `0.01`;
- starting balance `10,000 USDT` per run;
- NETTING account and reduce-only protective/exit orders;
- zero engine-message latency and deterministic seed `20260715`;
- instrument price/quantity increments from the frozen Bybit contract artifact,
  with current executable symbol defaults of BTC `0.1 / 0.001` and ETH
  `0.01 / 0.01` respectively;
- `fundingInterval=480` minutes for both symbols; any current instrument-contract
  drift from the historical 8-hour funding grid blocks the freeze;
- minimum notional `5 USDT`.

BB parameters, arm expiry, the six variant toggles, marking timeframe, and
symbol are the only dimensions in the matrix. Stochastic periods and base SL
are not swept because the executable state-machine contract freezes them; a
post-outcome expansion would be a different experiment.

## 6. Native execution and cost evidence

The intended execution profile is
`NAUTILUS_BYBIT_NATIVE_BAR_MMS_FULL_STACK_V1` on
`nautilus_trader.core.nautilus_pyo3.BacktestEngine` 1.230.0.

Mechanically:

1. M5 or M10 completed bars update the marking state before the corresponding
   final H1 decision.
2. Strategic market orders are delayed to the next final H1 bar. Native engine
   latency remains zero so protective orders created from a fill callback are
   not delayed by another hour.
3. Nautilus performs bar-based matching with adaptive high/low ordering.
4. Logical whole-position exits are represented by deterministic native
   reduce-only children in a NETTING account.
5. Engine/cache reports are reconciled to the domain fill and PnL ledgers.

Required cost evidence:

| Component | Frozen model | Provenance and limitation |
|---|---|---|
| Commission | Bybit maker `0.0002`, taker `0.00055` | Engine-applied modeled schedule; not a reconstruction of historical VIP tiers |
| Funding | Historical Bybit rates × completed H1 mark Close | Native `MarkPriceUpdate` preserves source precision; one pre-development H1 covers the first boundary; missing, duplicate, or amount-mismatched settlements fail the run |
| Slippage | One adverse instrument price tick on every fill | Native bar model with probability 1; no order-book depth or empirical impact |
| Execution | Native Nautilus bar matcher | Bar OHLC, not tick/L2 replay; adaptive intrabar ordering remains a model assumption |

Each mark update is timestamped at the inclusive close of a completed H1 mark
bar and is placed before an equal-timestamp H1 trade bar in the Python input
sequence. This is an input-construction guarantee, not a stronger claim about
Nautilus callback ordering. A real PyO3 characterization freezes both observed
effects: with LAST `100` and mark `200`, funding uses `200`; with an
equal-timestamp LAST `110` and mark `200`, account equity remains valued from
LAST and is not overwritten by the mark update.

The exact funding-price profiles are
`ONE_PREDEVELOPMENT_H1_FOR_FIRST_FUNDING_MARK_V1` and
`COMPLETED_H1_MARK_CLOSE_PRESERVE_SOURCE_PRECISION_FUNDING_BASIS_V1`; the full
cost profile is
`BYBIT_FIXED_FEE_HISTORICAL_MARK_FUNDING_ONE_TICK_NATIVE_BAR_V2`. For every
expected settlement, an independent oracle requires:

```text
expected_funding_amount = -signed_quantity × completed_H1_mark_close × funding_rate
```

The independent oracle replays signed position quantity from source
`PositionChanged` events, resets it to zero on `PositionClosed`, and rounds
half-up to `0.00000001 USDT`. A positive quantity is long, so a positive rate
produces a negative payment. Invariant 22 compares the oracle with native
funding amounts settlement by settlement in addition to reconciling ledger
totals. Missing position/rate/mark evidence or any amount mismatch is an
invariant failure.

There is no post-hoc ADR-011 overlay and no silent synthetic funding fallback.
The accepted absence of order-book/trade replay limits capacity and execution
realism conclusions. It does not permit relabelling close-based fills as native.

The wrapper does **not** claim server-side Bybit `closePosition` parity.
`close_position_parity=false` remains part of provenance; native reduce-only
behavior and final-flat invariants are what this runner verifies.

## 7. Mark-price isolated-margin model

Fill evidence and margin evidence are independent. An eligible artifact must
record both:

```text
fill_method   = nautilus_native_bar
margin_method = mark_price_isolated
```

For each completed H1 trade bar, the monitor maps the native inclusive-close
timestamp to the mark bar with the same open timestamp. This avoids a one-hour
lag. At that inclusive close it tests the adverse H1 mark wick (`Low` for a
long, `High` for a short) for every distinct position snapshot which may have
overlapped the interval:

- the position carried from the previous H1 boundary;
- every non-flat position snapshot captured by the transition observer inside
  the interval (`PositionChanged` captures an intrabar position even if a later
  `PositionClosed` makes the current state flat); and
- the position, if any, still open at the current inclusive close.

This includes a position which opened and closed inside one H1 bar. The same
full-hour wick is conservatively applied to every overlapping snapshot; there
is no claim that the extreme happened after entry and before exit. Missing mark
bars or an uncovered risk tier fail closed and are never forward-filled.
The frozen risk-limit normalization profile treats the Bybit V5
`maintenanceMargin` field as percentage points (`"0.5"` means 0.5%) and divides
it by 100 exactly once. Tiers and liquidation formulas consume the resulting
fraction (`0.005`); the raw pages and the unit/divisor provenance remain in the
contract artifact.

The margin profile is
`CAUSAL_H1_MARK_SETUP_EQUITY_EFFECTIVE_LEVERAGE_PROXY_V2`:

```text
gross_entry_notional = actual_open_quantity × actual_average_entry_price
effective_leverage   = max(1, gross_entry_notional / setup_start_equity)
initial_margin       = gross_entry_notional / effective_leverage
extra_margin         = 0
```

Consequences:

- a FULL base near 1× is checked near 1× effective leverage;
- a completed base plus equal-size add-on near 2× is checked near 2×;
- a 0.1× SCOUT is floored at 1× and uses approximately 0.1× setup equity as
  initial margin; the remaining wallet is not silently treated as extra margin;
- the native venue default leverage is `2`, while the independent liquidation
  evidence uses the effective-leverage formula above.

This liquidation price is a deterministic evidence proxy, not an exact Bybit
isolated-margin ledger. It freezes setup-start equity, derives leverage from
entry notional, sets `extra_margin=0`, and does not maintain dynamic isolated
collateral or debit dynamic realized PnL, commission, funding, or fee-to-close
liabilities into a per-position margin balance. It also omits insurance-fund,
ADL, wallet-transfer, and historical risk-tier changes. The full-hour
any-overlap wick policy is deliberately conservative in time, but the collateral
proxy is **not guaranteed conservative** relative to the venue ledger. The
frozen taker rate is cost provenance, not proof of exact fee-to-close
accounting. H1 mark OHLC detects a possible crossing, not its intra-hour
sequence or exchange settlement price.

The first crossing records one `LiquidationEvent` and stops further strategy
execution. It is valid negative evidence and may retain a passing evidence
gate, but it automatically fails the performance gate with
`LIQUIDATION_EVENT_PRESENT`.

If the crossed snapshot is the setup still open at the inclusive close, the
wrapper emits a liquidation close request and the conservative next-close
execution profile flattens it on the next native H1 bar. That native flatten,
its price, and its ledger values are **audit-only technical accounting**. They
are not a simulated Bybit liquidation settlement and may not be interpreted as
liquidation PnL, return, or equity. If an overlapping position had already
closed inside the interval, the event is still recorded without closing a
different later setup.

For every liquidated run, `economic_metrics_interpretable=false`; all reported
economic summary metrics and cost/PnL totals are `null`. Raw native frames and
`native_technical_flatten_accounting` remain only to prove deterministic
flattening and ledger reconciliation.

## 8. Evidence gate and performance gate

The two gates answer different questions and must never be collapsed into one
`eligible` flag.

### 8.1 Hard evidence gate

The evidence gate asks whether a run may be interpreted at all. It passes only
when:

- `fill_method == nautilus_native_bar`;
- `margin_method == mark_price_isolated`;
- a non-empty causal mark-price source and frozen complete risk-tier schedule
  are recorded;
- commission, funding, slippage, and execution components are complete and
  explicitly research-qualified;
- data, native/domain ledgers, final state, and artifact invariants all pass;
- the result class is `RESEARCH / ELIGIBLE`.

An evidence failure is fatal to that run and metrics are not considered. The
full economic verdict requires all 528 preregistered runs to complete with valid
evidence. Missing, corrupted, or silently downgraded evidence produces
`INVALID / NO VERDICT`, not `FAILS`.

A detected liquidation does not by itself fail this methodological gate. A run
can be `RESEARCH / ELIGIBLE` as evidence while being an automatic economic
failure with null, non-interpretable economic metrics.

### 8.2 Inclusive pre-WF performance gate

Performance assessment is strictly ordered:

1. If the evidence gate fails, performance is not considered and no metric is
   read.
2. If evidence passes but `liquidation_event_count > 0`, performance is
   considered and fails immediately with `LIQUIDATION_EVENT_PRESENT`; numeric
   economic metrics are not read and remain `null`.
3. Only an evidence-passing, non-liquidated run is assessed against all of
   these inclusive thresholds:

| Metric | Pass rule |
|---|---:|
| Post-cost Sharpe | `>= 1.0` |
| Profit factor | `>= 1.3` |
| Closed setups (`n_trades`) | `>= 100` |
| Maximum drawdown fraction | `>= -0.20` |
| Liquidation events | `== 0` |

For a non-liquidated run, all four numeric metrics must be present and finite.
Drawdown is stored as a fraction: `-0.20` means a 20% drawdown. A separate
display field may show `-20%`; the runner must not compare a percentage-points
value to the fractional threshold.

These are ADR-013 pre-walk-forward thresholds plus a zero-liquidation rule. They
are not ADR-009 post-WF go-live thresholds. Passing them means “worth a separate
MR-Session 5,” not “ready for testnet or live.”

## 9. Metric contract: post-cost only

For non-liquidated runs, the primary metrics are derived from native net equity
and closed-setup net PnL:

- H1 Sharpe annualized with `8,760` periods/year;
- profit factor from positive and negative `setup_net_pnl`;
- `n_trades` as the number of closed setup records;
- maximum drawdown as `equity / running_max(equity) - 1`;
- total return, CAGR, turnover, cost decomposition, and annual/regime values as
  descriptive fields.

`setup_net_pnl` reconciles gross price PnL, native commission, native historical
funding, and the declared one-tick slippage ledger.

For a liquidated run, Sharpe, Sortino, Calmar, MAR, profit factor, win rate,
trade count, return, CAGR, drawdown, recovery metrics, final equity, PnL, costs,
funding, slippage, and turnover are all `null` and non-interpretable. The
next-bar native flatten ledger is retained under
`native_technical_flatten_accounting` for audit only and is excluded from
thresholds, ranking, contrasts, and the decision matrix. Null liquidation
metrics are never replaced with zero and the run is never silently removed
from liquidation-rate denominators.

No synthetic “raw Sharpe” may be fabricated by adding costs back, replaying a
different engine, or copying a P9 statistic. If a genuine separately ledgered
pre-cost equity series is absent, `sharpe_raw` is `NA`. Selection, thresholds,
rankings, ablations, and the decision matrix use the post-cost native series
only.

## 10. Per-run invariant checklist

Each completed run must pass all 30 checks below. The runner records observed
and expected values, not only a combined boolean.

State and finalization:

1. `INVARIANT_VIOLATION_COUNT_ZERO`
2. `FINAL_DOMAIN_POSITION_FLAT`
3. `FINAL_DOMAIN_QUANTITY_ZERO`
4. `FINAL_NATIVE_QUANTITY_ZERO`
5. `FINAL_ORDER_LIFECYCLE_NONE`
6. `FINAL_SETUP_NONE`
7. `NO_ACTIVE_DOMAIN_ORDERS`
8. `NO_ACTIVE_NATIVE_ORDERS`
9. `FINAL_OUTBOX_EMPTY`
10. `DEVELOPMENT_EXIT_EMITTED_ONCE`
11. `MANUAL_CUTOFF_POLICY_MATCHES_LIQUIDATION`

Causal coverage and bounded-memory delivery:

12. `MARK_PRICE_BAR_COVERAGE_COMPLETE`
13. `MARKING_EVENT_COUNT_COMPLETE`
14. `DOMAIN_BAR_CUTOFF_COUNT`
15. `STREAMING_MARKERS_NOT_RETAINED`

Ledger and native-fill reconciliation:

16. `FUNDING_SETTLEMENT_IDS_UNIQUE`
17. `FUNDING_LEDGER_RECONCILED`
18. `NO_UNALLOCATED_FUNDING`
19. `NATIVE_FUNDING_SETTLEMENTS_COMPLETE`
20. `NATIVE_COMMISSION_EVIDENCE_PRESENT`
21. `COMMISSION_LEDGER_RECONCILED`
22. `FUNDING_AMOUNT_LEDGER_RECONCILED`
23. `ONE_TICK_SLIPPAGE_LEDGER_RECONCILED`
24. `RAW_DOMAIN_FILL_IDS_UNIQUE`
25. `TRANSITION_OBSERVER_COUNT_EXACT`
26. `FINAL_SNAPSHOT_ROUNDTRIP`

Data and margin evidence:

27. `NO_HOLDOUT_NATIVE_DATA`
28. `STRATEGY_HOLDOUT_ROWS_READ_ZERO`
29. `MARGIN_PROFILE_HAS_RISK_TIERS`
30. `LIQUIDATION_EVENT_AT_MOST_ONE`

A failed invariant is an invalid artifact. It is never converted into an
economic loss, omitted from the denominator, or replaced by a nearby parameter
configuration.

## 11. Outcome-blind execution, retries, and analysis

### 11.1 Launch and progress policy

Before the full launch, the four P22/V6 combinations (BTC and ETH × M5 and M10)
may run as an operational pilot. They are members of the 528 matrix and remain
in the final suite. During the pilot, only elapsed time, peak memory, disk use,
process health, error class, and artifact integrity may be inspected. Strategy
metrics and liquidation outcomes remain closed.

The pilot set is literally:

```text
BTCUSDT__M5__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH
BTCUSDT__M10__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH
ETHUSDT__M5__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH
ETHUSDT__M10__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH
```

The frozen operational defaults are:

- two spawned long-lived workers, one symbol per worker;
- at least 60 GiB free before launch;
- one writer in the parent process for outcome-blind progress;
- no shared legacy `index.csv` writes;
- progress fields limited to run ID, ordinal, status, attempts, elapsed time,
  failure class, and integrity/completion hashes.

Each run is written to a staging directory, deeply verified, given a completion
marker, and atomically renamed. Progress JSON is replaced atomically. On resume:

- a verified completed artifact is skipped;
- an interrupted staging artifact is reconciled or rerun under the same run ID
  and seed;
- a missing or tampered artifact marked complete is a fatal corruption error.

At most two attempts are permitted. Only `OSError` and `TimeoutError` are
retryable under `SAME_RUN_CONFIG_SEED_OSERROR_TIMEOUT_ONLY_V1`, with the same
run ID, config, seed, manifest, and data. Manifest, source, data, runtime,
invariant, evidence, and artifact-integrity failures are fail-fast. Weak
metrics and liquidation are completed outcomes and are never retry reasons.

No outcome-bearing results index is finalized until all 528 completion markers
pass deep verification. No partial ranking, top-N list, threshold count, or
parameter adjustment is allowed while the suite is running.

### 11.2 Precommitted contrasts

All contrasts are paired and descriptive; there are no p-values or post-hoc
searches.

Within every symbol × marking timeframe × parameter-set stratum:

- variant-minus-base: `V2−V1`, `V3−V1`, `V4−V1`, `V5−V1`, `V6−V1`;
- confirming interaction: `(V5−V2)−(V3−V1)`;
- Stochastic interaction: `(V6−V2)−(V4−V1)`.

Across marking timeframes, compute `M5−M10` for every exact symbol × parameter ×
variant match. Across symbols, compute `BTC−ETH` only for normalized metrics and
every exact marking × parameter × variant match. Contrasts must report both
members and sample counts; they do not create additional candidates. A contrast
with a liquidated member is `NA`; audit-only native-flatten numbers may not be
substituted.

## 12. Precommitted decision matrix

The experiment first checks the literal evidence contract:

- **INVALID / NO VERDICT:** fewer than 528 deeply verified runs, any unresolved
  evidence/invariant breach, manifest drift, source/data drift, or outcome
  leakage before suite completion. Fixing an implementation defect requires a
  new preregistration; it is not an economic `FAILS` result.

For this check, every completed artifact must have
`fill_method=nautilus_native_bar`, `margin_method=mark_price_isolated`, complete
research-qualified commission/funding/slippage/execution evidence, a causal
mark source and risk tiers, all 30 invariants passing, and result class
`RESEARCH / ELIGIBLE`. A liquidated run can satisfy all of these conditions; it
is valid negative evidence, not an invalid artifact.

If and only if the experiment is valid, a **performance-qualified candidate** is
one non-liquidated run that passes the evidence gate and all four literal
numeric thresholds: Sharpe `>= 1.0`, profit factor `>= 1.3`, closed setups
`>= 100`, and maximum drawdown fraction `>= -0.20`. A liquidation automatically
fails performance before those null metrics are accessed.

For each symbol, candidates are sorted by post-cost Sharpe descending, profit
factor descending, maximum drawdown descending (less negative first), trade
count descending, then run ID ascending. “Top 3” always means the first three
under this deterministic order.

For the rules below:

- a calendar regime is each UTC calendar-year slice intersecting development:
  partial 2021, full 2022–2024, and partial 2025, for five slices total;
- a positive regime has finite post-cost Sharpe strictly greater than zero;
- a boundary parameter has BB window `15` or `25`, BB deviation `1.8` or `2.4`,
  or arm expiry `1` or `3`;
- a cross-symbol candidate key is
  `(parameter_set_id, variant_id, marking_timeframe)`, excluding symbol;
- top-3 cross-symbol overlap is the Jaccard index of the BTC and ETH key sets;
- a symbol's liquidation share is liquidated evidence-valid runs divided by
  its 264 completed runs.

The economic verdict uses this precedence:

| Verdict | Exact rule | Roadmap branch |
|---|---|---|
| `FAILS` | Either symbol has zero performance-qualified candidates, **or** either symbol's liquidation share is `>= 0.50` | Do not reveal reserved data for this hypothesis; pivot or preregister a materially new hypothesis |
| `SOLID` | Each symbol has at least 3 performance-qualified candidates; each of the six top-3 candidates has at least 4/5 positive calendar regimes; no more than one top-3 candidate per symbol is a boundary parameter; and BTC/ETH top-3 Jaccard is `>= 0.50` | MR-Session 5 may be drafted for a preselected candidate set and an explicit holdout policy |
| `MARGINAL` | Both symbols have at least 1 performance-qualified candidate, liquidation does not trigger `FAILS`, but one or more `SOLID` robustness conditions fail | No reserved-data reveal; a new in-sample refinement requires a new preregistration |

`FAILS` takes precedence over `SOLID`; `SOLID` takes precedence over `MARGINAL`.
There is no discretionary upgrade based on attractive PnL, one exceptional
symbol, or a visually compelling chart.

## 13. Freeze and invalidation procedure

The status may change from `DRAFT / BLOCKED` to `FROZEN BEFORE METRIC READ` only
after all blockers are resolved and the following order succeeds:

1. Capture the complete paginated Bybit mainnet instrument/risk-limit responses
   for BTCUSDT and ETHUSDT into the declared immutable contract artifact.
2. Restore the missing ETHUSDT historical funding file and pass strict offline
   validation for both symbols.
3. Finalize runner sources, `uv.lock`, profiles, and the 528-run contract; run
   targeted tests and the full project quality gate.
4. In the exact intended runtime, run `algo-mr-session4 lock-core`. This command
   performs the complete offline data/contract preflight and prints the
   self-contained `manifest_core_hash` without reading this document or Git
   provenance.
5. Write that exact core hash into Section 1, change the document status to
   `FROZEN BEFORE METRIC READ`, and make no change to any core input.
6. Commit the runner, tests, frozen document, contract artifact, and required
   provenance metadata. Require a clean tree, then create the
   `mr-session-4-preregistration-YYYY-MM-DD` tag on that commit.
7. From that same clean tagged commit, run `algo-mr-session4 prepare`. It must
   recompute the identical core hash and write a separate provenance object
   containing the clean tree/commit, final preregistration SHA-256, and exactly
   one canonical `mr-session-4-preregistration-YYYY-MM-DD` tag which points at
   that commit. Its `provenance_hash` is stored in the manifest, not inserted
   back into this document.
8. Verify the manifest, core hash, provenance hash, runtime, contracts, and both
   development-only data bundles. Re-verify after VPS sync before any pilot or
   full run.

### 13.1 Exact operator command sequence

The following paths and run IDs are the declared operator sequence. Commands
which contact Bybit are data/contract preparation only and may be used to
resolve blockers 1–2; they do not authorize a strategy run. Every subsequent
command requires its preceding freeze step. The `run` commands are still
forbidden while Section 14 remains blocked.

```bash
uv run --locked algo-fetch-funding \
  --exchange bybit \
  --symbol ETH/USDT \
  --start 2021-04-01 \
  --end 2025-06-30T23:59:59.999

uv run --locked algo-mr-session4 freeze-bybit-contracts \
  --symbols BTCUSDT ETHUSDT \
  --output config/experiments/mr-session-4-bybit-contracts.json \
  --mainnet-public

uv run --locked algo-mr-session4 lock-core \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --data-root bot_data/processed
```

After inserting the printed core hash into this document, changing its status,
committing the exact frozen tree, and confirming that the tree is clean, create
one tag and prepare the manifest:

```bash
PREREG_TAG="mr-session-4-preregistration-$(date -u +%F)"
git tag "$PREREG_TAG"
git tag --points-at HEAD --list 'mr-session-4-preregistration-*'

uv run --locked algo-mr-session4 prepare \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md \
  --data-root bot_data/processed \
  --output results/experiments/mr-session-4-manifest.json

uv run --locked algo-mr-session4 plan \
  --manifest results/experiments/mr-session-4-manifest.json \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md
```

The tag-list command must print exactly the value of `PREREG_TAG`. The pilot
uses the final suite directory, so the later full command resumes those four
already verified matrix members:

```bash
S4_OUTPUT=results/experiments/mr-session-4-runs

uv run --locked algo-mr-session4 run \
  --manifest results/experiments/mr-session-4-manifest.json \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md \
  --data-root bot_data/processed \
  --output "$S4_OUTPUT" \
  --workers 2 \
  --max-attempts 2 \
  --min-free-gib 60 \
  --run-id BTCUSDT__M5__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH \
  --run-id BTCUSDT__M10__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH \
  --run-id ETHUSDT__M5__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH \
  --run-id ETHUSDT__M10__P22_W20_D20_E2_ANCHOR__V6_BASE_SEQ_STOCH

uv run --locked algo-mr-session4 verify \
  --manifest results/experiments/mr-session-4-manifest.json \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md \
  --output "$S4_OUTPUT" \
  --allow-incomplete \
  --no-metrics

uv run --locked algo-mr-session4 run \
  --manifest results/experiments/mr-session-4-manifest.json \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md \
  --data-root bot_data/processed \
  --output "$S4_OUTPUT" \
  --workers 2 \
  --max-attempts 2 \
  --min-free-gib 60 \
  --resume

uv run --locked algo-mr-session4 verify \
  --manifest results/experiments/mr-session-4-manifest.json \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md \
  --output "$S4_OUTPUT" \
  --no-metrics

uv run --locked algo-mr-session4 finalize \
  --manifest results/experiments/mr-session-4-manifest.json \
  --contracts config/experiments/mr-session-4-bybit-contracts.json \
  --preregistration docs/experiments/mr-session-4-preregistration.md \
  --output "$S4_OUTPUT"
```

`verify` may validate outcome-bearing fields internally for artifact integrity,
but it prints only the verified-run count. Operators must not open per-run
summaries during the pilot or partial suite. `finalize` remains forbidden until
all 528 completion markers pass deep verification.

If the core hash recomputed by `prepare` differs from the hash written here,
freeze fails. Return to `lock-core` and create a new frozen commit/tag; never
edit a core input in place under an existing preregistration tag.

After the first strategy metric is readable, changing dates, data, parameter
sets, variants, marking timeframes, cost/margin/execution profiles, thresholds,
decision rules, retry semantics, or run count invalidates the preregistration.
An operational retry is permitted only under Section 11 with identical bytes,
run ID, config hash, data hash, and seed.

## 14. Current freeze blockers

The following are observed blockers, not hypothetical risks:

1. **Missing ETHUSDT funding:**
   `bot_data/processed/bybit_ETHUSDT_funding.csv` does not exist. The kickoff's
   assumption that Bybit funding is complete for both symbols is therefore
   contradicted by the current filesystem.
2. **Missing frozen Bybit contracts:**
   `config/experiments/mr-session-4-bybit-contracts.json` does not exist. The
   mainnet instrument and complete paginated risk-limit schedule have not been
   frozen, so the isolated-margin tier hash cannot be created.
3. **Core lock is pending:** because blockers 1–2 remain, `lock-core` cannot
   produce the development-data, contract, implementation, and manifest-core
   hashes required for this document.
4. **Final freeze commit and tag are pending:** the implementation and blocked
   draft may be committed before the inputs are restored, but no commit yet
   contains the frozen contracts, inserted core hash, final frozen document,
   and required canonical preregistration tag. That tag must not be created for
   a draft commit.
5. **Manifest provenance is pending:** `prepare` must run only after the core
   hash is written here and the exact document is committed/tagged. Therefore
   preregistration SHA-256, clean tree/commit provenance, and provenance hash
   are still pending.
6. **Final verification is pending:** the completed source must pass the full
   quality gate and the exact VPS runtime/data preflight before launch.

Until blockers 1–6 are closed, the only permitted actions are runner/test work,
data restoration and validation, contract capture, manifest preparation, and
operational dry runs that do not expose strategy outcomes. The 528-run sweep
must not start.

The reserved-data access disclosed in Section 3.1 is a non-blocking forward
caveat for MR-Session 4: this experiment is strictly in-sample and does not use
those rows. It does require an explicit holdout choice in a future MR-Session 5
preregistration and prevents an unqualified claim that the current reserved
tail was literally never read.
