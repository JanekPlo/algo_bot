# Module reference — `algo_bot.strategies.mean_reversion_bb_stoch`

> **Status: FULL** (MR-Session 1 Audit, 2026-07-11; supersedes the Beta DRAFT of
> 2026-07-10). Hybrid format per the bghtrend Session-1 pattern: critical paths
> verbatim, mechanical helpers summarised. Includes the parameter taxonomy with
> overfitting watchlist, the independent-oracle audit record, and the Mastermind
> alignment table with per-row citations into `docs/references/mms/`.

The Phase 2 MVP candidate after the bghtrend no-go
([ADR-012](../../adr/012-mvp-no-go-bghtrend.md)). A contrarian **mean-reversion**
strategy on Bollinger Bands with an optional Stochastic confirmation. It waits for
price to reach a band (a statistical stretch from the local mean), waits for the
candle to close, and enters on the first *reaction* candle back toward the mean.
Target is the opposite band (live); stop is a fixed percentage. Single-symbol,
single-position, both directions.

Methodological prior: **Mastermind MMS** (mastermindzx.pl), extracted and versioned
in [`docs/references/mms/`](../../references/mms/README.md). This module implements
the **bare core** of that methodology only; its actual claimed edge (pyramiding +
sequential leverage reduction) is explicitly out of scope — see
[Known limitations](#known-limitations).

Contracts: strategy/Signal API — [ADR-003](../../adr/003-strategybase-signal-api.md);
microstructure overlay — [ADR-011](../../adr/011-microstructure-adjustments.md);
WF-eligibility thresholds for the upcoming sweep —
[ADR-013](../../adr/013-wf-eligibility-thresholds.md).
Sweep configurations: `config/mr_b1..b3.yaml`.

## At a glance

```python
from algo_bot.engine.backtester import run_backtest

stats, equity, trades = run_backtest(
    symbol="BTC/USDT",
    timeframe="1h",
    strategy="mean_reversion_bb_stoch",
    params={"bb_window": 20, "bb_num_std": 2.0, "entry_mode": "bb_stoch"},
)
```

- **What it trades:** one symbol, one position at a time, either side (`side="both"`).
- **Timeframe:** TF-agnostic in code; configs tuned per TF — `mr_b1` (1h, strict),
  `mr_b2` (1h, relaxed), `mr_b3` (15m, fast). All carry `__implied_tf` meta-keys.
  The MMS-consistent baseline fixes `entry_mode="bb_only"` in b1/b3; b2 alone
  retains the empirical `bb_only` vs `bb_stoch` split.
- **Cadence:** once per closed bar via `on_bar(df) -> Signal`; position management
  runs before entry logic.
- **Indicators:** [`bbands`](indicators-bbands.md) (SMA ± num_std·σ, population σ) +
  [`stochastic`](indicators-stochastic.md) (slow, %K only) — both causal,
  precompute-cached.

## Economic thesis

When price reaches a Bollinger Band it is statistically stretched (`num_std`
standard deviations) from its local mean. Absent a fundamental reason for a durable
breakout, the market reverts. We enter **contrarian**: long at the lower band, short
at the upper. To avoid catching a falling knife we do not enter on the touch itself —
we wait for the touch candle to close and for the next candle to *react* back toward
the mean. Optionally the Stochastic confirms the touch coincides with a momentum
extreme.

The MMS framing adds an important honesty clause: the methodology *expects* the bare
core to bleed in strong trends — its protection is the deferred sizing layer
(sequentiality), not the entry filter
[[mms/03](../../references/mms/03-stop-loss-sequential.md)]. See
[Interpreting sweep results](#interpreting-sweep-results-mr-session-2-caveat).

## `on_bar` lifecycle

Order of evaluation per closed bar (verbatim behaviour, audited 2026-07-11):

```
1. Warmup guard: len(df) < max(bb_window, k+smooth+d) + 2 → Signal()
2. Indicator read: precompute prefix (backtest) or per-prefix recompute (live path)
3. Band-NaN guard → Signal()
4. IN POSITION?  → update TP to live opposite band → check exits → exit/hold
   (an exit RETURNS immediately: the exit bar can never arm — regression-tested)
5. ARMED?        → reaction check → enter, or decrement expiry (no refresh on
   re-touch — regression-tested), disarm at 0
6. FLAT & DISARMED → touch scan (wick vs band) + Stoch gate → arm long/short;
   both bands touched in one bar → arm nothing (regression-tested)
```

### Entry — "armed → reaction" (critical path, verbatim)

Touch scan with the Stochastic gate **at arming** (step 6):

```python
touch_long = (
    self.p.side in ("long", "both")
    and (l_now <= lower_now)
    and self._stoch_gate_ok("long", k_now)
)
touch_short = (
    self.p.side in ("short", "both")
    and (h_now >= upper_now)
    and self._stoch_gate_ok("short", k_now)
)
if touch_long and not touch_short:
    self._armed_side = "long"
    self._armed_bars = self.p.arm_expiry_bars
elif touch_short and not touch_long:
    self._armed_side = "short"
    self._armed_bars = self.p.arm_expiry_bars
```

Reaction (step 5) — body toward the mean, optional reclaim of the band interior
(**interior of the band, not the mid line** — docstring clarified in this audit):

```python
def _reaction_ok(self, side, o, c, lower, upper):
    if side == "long":
        if not (c > o):          # byczy korpus
            return False
        return (not self.p.require_reclaim) or (c > lower)
    if not (c < o):              # niedźwiedzi korpus
        return False
    return (not self.p.require_reclaim) or (c < upper)
```

**Design note (from Beta, verified in audit):** the Stochastic gate fires at the
*touch* bar, not the reaction bar. The reaction candle by definition turns back and
lifts %K, so a gate evaluated on R would structurally almost never pass — the
oscillator extreme lives where price touches the band. The MMS semi-auto EA gates on
the previous candle the same way
[[mms/06](../../references/mms/06-algotrading-semi-auto.md)].

Entry executes at the reaction bar's Close (`trade_on_close=True`), SL/TP recorded in
`Signal.meta`.

### Exit — precedence (critical path, verbatim)

TP = live opposite band, recomputed every bar *before* the hit check; SL = fixed
`sl_pct` from entry. Same-bar TP&SL resolved by `tp_has_priority` (default False →
**SL wins**, conservative):

```python
hit_tp / hit_sl computed from High/Low vs tp/sl ...
if hit_tp and hit_sl:
    return "tp" if self.p.tp_has_priority else "sl"
if hit_tp: return "tp"
if hit_sl: return "sl"
```

Exit reasons in `Signal.meta`: `"tp_band"` / `"sl_fixed"`. Exits execute at the hit
bar's Close — conservative relative to an idealised fill exactly on the level. No
trailing stop, no break-even, no timeout — deliberately (MMS explicitly rejects
trailing [[mms/01](../../references/mms/01-position-building.md)]; timeout belongs to
the future funding ADR).

### Mechanical parts (summarised)

`MeanReversionBBStochParams` — frozen dataclass, `__post_init__` fail-fast validation
(window ≥ 2, num_std > 0, stoch windows ≥ 1, 0 ≤ oversold < overbought ≤ 100,
entry_mode/side enums, 1 ≤ arm_expiry_bars, 0 < sl_pct < 1). Catches every
construction path via `coerce_params`. `precompute()` computes both indicators once,
vectorised; `on_bar` reads prefixes (equivalence proven by test). Class-level
`p: MeanReversionBBStochParams` annotation narrows `self.p` for mypy strict-on-new
(module is on the pyproject strict list).

## Parameters — taxonomy with overfitting watchlist

Categories per the bghtrend Session-1 convention: **core** (economic meaning, ranges
anchored in a thesis), **tuning** (mechanical/engine or frozen prior), **ambiguous**
(plausible rationale but easy overfitting vector). ⚠ = overfitting watchlist for
MR-Session 2 interpretation.

| Param | Type / default | Sweep range (b1 / b2 / b3) | Category | Rationale |
|---|---|---|---|---|
| `bb_window` | int, 20 | {18,20,22} / {15,20,25} / {10,14,20} | **core** | Local-equilibrium window; MMS "standard settings" prior = 20, author's own EA uses 31 [[mms/06](../../references/mms/06-algotrading-semi-auto.md)] — settings are declared parametrization targets [[mms/01](../../references/mms/01-position-building.md)] |
| `bb_num_std` | float, 2.0 | 2.0–2.5 / 1.5–2.0 / 1.5–2.2 (step 0.1) | **core** ⚠ | Stretch threshold = statistical rarity of the touch; ⚠ 0.1-stepping invites cherry-picking a lucky band width — read clusters, not single best |
| `stoch_k`/`stoch_d`/`stoch_smooth` | 14/3/3 | frozen / frozen / k∈{9,14} | tuning | MMS prior verbatim (classic 14/3/3 on H1) [[mms/02](../../references/mms/02-position-management-filters.md)] |
| `stoch_oversold`/`stoch_overbought` | 20/80 | frozen / {20,25}&{75,80} / {20,25}&{75,80} | tuning ⚠ | Classic thresholds; ⚠ threshold pairs are archetypal overfit knobs — expect near-flat response or distrust the result |
| `entry_mode` | `"bb_stoch"` | {bb_only} / both / {bb_only} | **core** | Structural diagnostic retained in b2 only: does oscillator confirmation add edge over bare bands? b1/b3 freeze the MMS-consistent baseline because MMS assigns Stoch to add-ons, not the base entry (see alignment row 8) |
| `arm_expiry_bars` | int, 2 | {1,2} / {2,3,4} / {1,2,3} | ambiguous ⚠ | Proxy for "reaction promptness" (MMS: *first* reaction candle ⇒ 1); >1 tolerates one-bar noise at the cost of staler setups. No hard MMS anchor beyond "first" |
| `require_reclaim` | bool, False | {F,T} / {F} / {F} | ambiguous ⚠ | Strictness toggle (Close back inside the band); plausible, but no MMS anchor — pure selectivity knob |
| `sl_pct` | float, 0.02 | 0.015–0.025 / 0.015–0.030 / 0.010–0.025 | **core** | The MMS hard rule: initial SL = 2% of price from base entry [[mms/01](../../references/mms/01-position-building.md)]; range spans TF-scaled variants (author's EA: 1.7% [[mms/06](../../references/mms/06-algotrading-semi-auto.md)]) |
| `tp_has_priority` | bool, False | frozen False | tuning | Same-bar accounting convention, conservative (SL-first); flipping it flatters the edge — keep frozen outside diagnostics |
| `side` | `"both"` | frozen | **core** | MMS is both-directions by construction (band-to-band flow) [[mms/01](../../references/mms/01-position-building.md)] |
| `trade_on_close` | bool, True | frozen | tuning | Engine convention (as bghtrend); conservative fills |

**Watchlist summary (⚠):** `bb_num_std` fine steps, `stoch_oversold/overbought`,
`arm_expiry_bars`, `require_reclaim`. Analogue of bghtrend's
deadzone/slope_thr/atr_mult set: parameters whose best-in-sweep values demand a
stability check (±1 step neighbourhood) before being believed.

## Independent oracle audit (2026-07-11)

Method per the 2026-06-11 lesson (xtrender): *never assume the code does what the
docs say — verify independently.* Scope: minimum-viable (Decision 4) targeted at
seams the Beta test suite does **not** cover.

Verified clean (manual trace of `on_bar` against the docstrings + this reference):

- **Tuple unpack order** — `upper, _mid, lower = bbands(...)`; `pct_k, _pct_d =
  stochastic(...)`. Positions match the API contracts; no xtrender-style
  off-by-one. Cross-checked against `indicators/core.py` return statements.
- **Stoch gate at arming** — `_stoch_gate_ok` is called only in the touch scan
  (step 6), never in the armed branch (step 5). Matches the documented design.
- **Same-bar precedence** — SL-first by default in `_hit_exit`, flag-controlled;
  matches Beta CHANGELOG claim ("sl_fixed default vs tp_band with
  tp_has_priority").
- **Warmup guard** — `max(bb_window, k+smooth+d) + 2` ≥ true indicator warmup
  (`k+smooth−1` for %K); conservative, no NaN leak into gating.

Findings (mechanised into regression tests, `TestAuditSeams` in
`tests/test_mean_reversion_bb_stoch.py`):

1. **Re-touch does not refresh the arming window** — the armed branch returns
   before the touch scan, so a second band touch during the armed window neither
   resets `_armed_bars` nor flips direction. Undocumented before; now documented
   here + tested.
2. **The exit bar cannot arm** — exit returns immediately; a crash bar that both
   breaks SL and pierces a band does not arm the reversal. Tested.
3. **Both-bands touch arms nothing** — explicit branch; includes the degenerate
   flat-market case where zero-width bands make both touches true. Tested.
4. **Docstring drift (fixed, zero behaviour change):** `require_reclaim` said
   "powrót do środka wstęgi" (ambiguous: mid line?) while the code checks the band
   *interior* (`c > lower` / `c < upper`). Docstrings clarified in three places.
5. **%D computed but unused** — the strategy gates on %K only; relevant for the
   alignment table (MMS classic signal is a %K & %D cross), not a bug.
6. Plus a hand-derivable indicator identity test: `bbands(·, window=2, num_std=1)`
   ⇒ upper=max, lower=min of each pair (see
   [indicators-bbands.md](indicators-bbands.md)).

## Mastermind alignment table

Every row cites its source file in `docs/references/mms/` — the chain is verifiable
end-to-end: code → this reference → mms extraction → screenshots in `mms/raw/`.

| # | MMS says [ref] | Beta implementation | Match | Rationale for divergence |
|---|---|---|---|---|
| 1 | Envelope = TMA / NW / **BB**, settings "currently irrelevant", parametrization decides [[mms/01](../../references/mms/01-position-building.md)] | Bollinger Bands, `bb_window`/`bb_num_std` swept | **Y** | Choice within MMS-sanctioned freedom; BB mainstream + in `core.py` |
| 2 | Both directions, band-to-band flow (TP of one side = arming level of the other) [[mms/01](../../references/mms/01-position-building.md)] | `side="both"`, TP = opposite live band | **Y** | — |
| 3 | Wait for candle close before acting [[mms/01](../../references/mms/01-position-building.md)] | Closed-bar `on_bar`, `trade_on_close` | **Y** | — |
| 4 | Entry on the *first reaction candle from the new interval*, marked on M5/M10 within H1 [[mms/01](../../references/mms/01-position-building.md), [mms/04](../../references/mms/04-interval-marking.md)] | Two-bar H1 proxy: wick-touch arms, next H1 body = reaction | **PARTIAL** | No intrabar data (Beta Decision 1); entry lags manual MMS by ≤1 H1 bar — conservative; revisit only if Sweep shows timing-attributable decay |
| 5 | TP = opposite band, simultaneously arming of the reverse setup [[mms/01](../../references/mms/01-position-building.md)] | `tp_band`, live (recomputed per bar) | **Y** | Live-band reading chosen in Beta (Decision 3); exit bar cannot arm the reversal (audit seam 2) — MMS flow would allow immediate re-setup, ours re-arms earliest next bar |
| 6 | Initial SL = **2%** price move from base position [[mms/01](../../references/mms/01-position-building.md)] | `sl_pct=0.02` default, swept 1–3% | **Y** | Range spans TF variants; author's own EA uses 1.7% [[mms/06](../../references/mms/06-algotrading-semi-auto.md)] |
| 7 | No trailing stops (parametrization showed worse results) [[mms/01](../../references/mms/01-position-building.md)] | No trail / BE / timeout | **Y** | — |
| 8 | Stochastic = **add-on (dokładka) filter**: %K & %D cross below 20 / above 80, 14/3/3 on H1, applied while position open [[mms/02](../../references/mms/02-position-management-filters.md)] | Optional **base-entry** gate at arming, **%K only**; swept only in b2, while b1/b3 use `bb_only` | **N — deliberate adaptation** | Beta has no pyramiding (deferred), so the oscillator was repurposed as a base-entry quality filter; %D cross dropped for simplicity (%D computed, available). MR-Session 2 b2 resolves empirically whether it adds edge |
| 9 | Stochastic defaults 14/3/3, thresholds 20/80 [[mms/02](../../references/mms/02-position-management-filters.md)] | Same defaults; frozen in b1, thresholds mildly swept in b2/b3 | **Y** | — |
| 10 | TF: H1 manual base; algo target M10–M30; H4/D1 = context [[mms/01](../../references/mms/01-position-building.md), [mms/05](../../references/mms/05-market-mechanics-patterns.md)] | b1/b2 = 1h, b3 = 15m (`__implied_tf`) | **Y (partial)** | 15m sits in the M10–M30 algo band; H4/D1 context layer not modelled (row 15) |
| 11 | Pyramiding: add-on #1 after first confirming candle, add-on #2 on Stoch signal, x1 each [[mms/02](../../references/mms/02-position-management-filters.md)] | Not implemented | **N (deferred)** | Single-position `backtesting.py` cannot express it; separate ADR post-Sweep (Decision 6) |
| 12 | Sequentiality: first full SL → x0.1 scouts → first TP → back to x1 [[mms/03](../../references/mms/03-stop-loss-sequential.md)] | Not implemented | **N (deferred)** | Same ADR; this is MMS's claimed edge — see sweep caveat below |
| 13 | Sizing semantics x1 = 1% move = 1% equity; hard cap x2 / 3% risk [[mms/01](../../references/mms/01-position-building.md), [mms/02](../../references/mms/02-position-management-filters.md)] | Bare `Signal` without `size`; runner default sizing | **N (deferred)** | Sizing deferred with pyramiding ADR |
| 14 | Add-on SL at wick-pair local extreme, ≤1% move [[mms/02](../../references/mms/02-position-management-filters.md), [mms/04](../../references/mms/04-interval-marking.md)] | Only the base 2% SL exists | **N (deferred)** | Structural SL belongs to the add-on layer |
| 15 | Multi-interval context: H4 trend-vs-range, D1 direction filter (D1 Stoch superordinate), W1 schemes [[mms/05](../../references/mms/05-market-mechanics-patterns.md), [mms/04](../../references/mms/04-interval-marking.md)] | Not modelled | **N (out of scope)** | Discretionary context; candidate regime-filter prior if bare core needs one post-Sweep |
| 16 | Instruments: BTC/XAU/NQ; prefer spot/P2P, avoid CFD [[mms/01](../../references/mms/01-position-building.md)] | Binance **USDT-M perpetual** BTC/ETH | **PARTIAL** | Perp ≠ spot: funding flows exist — measured, not mechanised, via ADR-011 overlay; note contrarian MR tends to sit on the funding-*receiving* side |

## Interpreting sweep results (MR-Session 2 caveat)

MMS itself declares the bare core unprofitable in strong trends and assigns the
rescue to the sequential sizing layer
[[mms/03](../../references/mms/03-stop-loss-sequential.md)]. Therefore:

- A **mediocre bare-core sweep does not falsify the methodology** — it may confirm
  that the edge lives in the deferred layer. The go/no-go after MR-Session 2 must
  answer "is the bare core viable alone?" and, separately, "did we test the actual
  Mastermind edge?" (we did not — by design).
- A **strong bare-core sweep** would be a pleasant surprise: pyramiding then becomes
  an enhancement, not a rescue, and the deferred ADR loses urgency (Decision 6).
- Eligibility gate: `WF_ELIGIBILITY_THRESHOLDS` (ADR-013) + per-year
  regime-robustness sanity check (`running-sweep.md`).

## Known limitations

- **`backtesting.py` native cannot express the deferred edge.** Pyramiding (scaling
  into positions) and sequential leverage reduction (x1 ↔ x0.1 across trades) are a
  state machine above single positions. If MR-Session 2+ demonstrates that
  pyramiding is required for viability, **engine migration (vectorbt /
  nautilus_trader) becomes a prerequisite for MVP go-live** — early warning, per
  Beta CHANGELOG.
- **No intrabar data:** the M5/M10 interval-marking mechanics
  [[mms/04](../../references/mms/04-interval-marking.md)] are proxied by two H1 (or
  15m) bars; entries lag the manual methodology by up to one bar.
- **Unbounded hold time × funding:** no timeout; funding measured post-hoc via the
  ADR-011 overlay (raw vs post). The sign may favour us (contrarian MR receives
  funding more often than it pays), but the tail is untested — future ADR (together
  with pyramiding).
- **Base position only:** results must be read as "the base, not the full system".

## Tests

- `tests/test_mean_reversion_bb_stoch.py` — params validation, execution helpers
  (SL math, precedence, reaction, stoch gate), both-direction entry gates,
  same-bar precedence integration, **audit seams (TestAuditSeams, 2026-07-11)**,
  precompute equivalence (live vs cached, bar by bar).
- `tests/test_indicators_bbands_stochastic.py` — independent-oracle tests for both
  indicators (first-principles loops, literals, prefix invariance, window-2
  identity, API contracts).

## See also

- [`docs/references/mms/README.md`](../../references/mms/README.md) — the extracted,
  versioned Mastermind prior (source disclaimer inside).
- [indicators-bbands.md](indicators-bbands.md), [indicators-stochastic.md](indicators-stochastic.md).
- [ADR-012](../../adr/012-mvp-no-go-bghtrend.md) — the pivot;
  [ADR-013](../../adr/013-wf-eligibility-thresholds.md) — sweep eligibility.
- [strategy-bghtrend-pullback.md](strategy-bghtrend-pullback.md) — the deep-reference
  pattern this document follows.
- ROADMAP → Phase 2 → MR-Session map.
