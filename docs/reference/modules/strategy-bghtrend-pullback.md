# Module reference — `algo_bot.strategies.bghtrend_pullback`

The Phase 2 MVP candidate. A trend-following pullback strategy: it identifies a strong directional regime with a three-EMA stack, waits for price to retrace toward the mid EMA (EMA89), confirms re-acceleration with the Xtrender momentum oscillator, and enters in the trend direction with an ATR-based stop, a fixed reward:risk target, and an ATR trailing stop. Single-symbol, single-position, long/short.

This is a hybrid reference: critical paths (entry gate, exit precedence, SL/TP/trail math) are shown as the actual code; mechanical helpers (indicator computation, slope normalisation) are summarised. For the momentum oscillator in isolation see [indicators-xtrender](indicators-xtrender.md). The strategy/Signal API contract is [ADR-003](../../adr/003-strategybase-signal-api.md). Sweep configurations are documented in [config-reference](../config-reference.md).

## At a glance

```python
from algo_bot.engine.backtester import run_backtest

stats, equity, trades = run_backtest(
    symbol="BTC/USDT",
    timeframe="1h",
    strategy="bghtrend_pullback",
    params={"ema_fast": 21, "ema_mid": 89, "ema_slow": 200, "deadzone": 3.0},
)
```

- **What it trades:** one symbol, one position at a time, either side (`side="both"` default).
- **Timeframe:** TF-agnostic in code, but the sweep configs are implicitly tuned to TF bands — `bghtrend_b3` for fast (≈15m), `b1`/`b2` for medium (≈1h), `b4` for slow (≈4h). See [config-reference](../config-reference.md).
- **Signal cadence:** evaluated once per closed bar via `on_bar(df) -> Signal`. Exits are checked before entries.
- **Indicators:** EMA21/89/200 (trend), ATR (volatility, stops, pullback band), Xtrender `long_term` (regime momentum confirmation at entry) + dots from `short_t3` (in-profit exit).

## Economic thesis

The strategy bets on **three compounding edges**, all of which must agree before it commits:

1. **Trend persistence (regime).** Crypto perpetual futures exhibit autocorrelated directional runs — strong moves tend to continue more often than a random walk would predict. The EMA21 > EMA89 > EMA200 stack plus a slope filter on EMA89/EMA200 is the regime gate: it only fires when the medium and macro trends are aligned *and* sloping, not merely ordered. This filters out range-bound chop where trend-following bleeds.

2. **Mean-reversion on the pullback (entry timing).** Within an established trend, price oscillates around the mid EMA. Entering on a retracement *toward* EMA89 — rather than chasing an extended move — buys a better price and tucks the stop just beyond the EMA, where a breach genuinely invalidates the trend hypothesis. This is the "pullback" half: a local mean-reversion entry inside a global trend-following frame.

3. **Momentum confirmation (re-acceleration).** A pullback alone is ambiguous — it can be a pause or the start of a reversal. Xtrender's T3-smoothed short-term leg crossing out of its deadzone in the trend direction is the evidence that momentum has re-engaged, not stalled. This is the filter that distinguishes "trend resuming" from "trend ending".

The exit structure encodes the payoff asymmetry: a fixed R:R target (1.5–2.0) banks a defined multiple of risk, the ATR trailing stop lets winners run beyond target while ratcheting risk down, the Xtrender dot exits on momentum exhaustion *only when already in profit*, and a stale-bar timeout culls positions that go nowhere. Cooldown after a stop prevents revenge re-entry into the same failing setup.

The thesis fails when: the regime gate admits chop (whipsaw losses), pullbacks become reversals faster than the stop can adapt (gap risk), or momentum confirmation lags the actual turn (late entries near exhaustion). Phase 2 walk-forward + stress tests exist precisely to measure whether the three edges survive out-of-sample.

## Lifecycle — `on_bar(df) -> Signal`

`on_bar` is the single entry point (`StrategyBase` contract, ADR-003). It is called once per closed bar with the full history-to-date DataFrame. Order of operations:

```
1. Warm-up guard      -> Signal() (hold) if len(df) < need + 5
2. Compute indicators -> ema21/89/200, atr, xtrender components
3. Cooldown decrement -> Signal() if cooldown active
4. If in position:    EXIT logic (has priority)
5. If flat:           ENTRY logic (long then short)
```

State is held on instance attributes across bars: `_pos_side`, `_entry_price`, `_sl`, `_tp`, `_trail`, `_bars_in_trade`, `_cooldown_left`.

### Warm-up and indicator computation (summary)

The guard requires `len(df) >= need + 5` where `need = max(ema_slow, long_l1, short_l2, 60)` — enough history for the slowest EMA and the Xtrender legs to be meaningful. Below that, it returns a no-op `Signal()`.

Indicators are recomputed from the full series every bar (no incremental state): `ema(c, ema_fast/mid/slow)`, `atr(df, pullback_atr_len)`, and `xtrender_components(c, ...)`. ATR here uses the EWM definition from `core.atr` (span = `pullback_atr_len`), and it is reused for three distinct jobs: the pullback band width, the entry-distance cap, and the SL/trail padding.

## Entry logic

All entry gates are ANDed. A long requires **trend + pullback + rebound + momentum + distance**, in that evaluation order. The long branch verbatim:

```python
# LONG
if (
    self.p.side in ("long", "both")
    and self._trend_ok(ema21, ema89, ema200, "long")
    and self._pullback_seen(df, ema89, atr_s, "long")
    and self._rebound_ok(c, ema21, "long")
    and self._xtr_ok(x_long, "long")
):
    entry = float(c.iloc[-1])
    e89 = float(ema89.iloc[-1])
    a = float(atr_s.iloc[-1])
    if not self._entry_distance_ok(entry, e89, a):   # post-gate distance cap
        return Signal()
    sl, tp = self._compute_sl_tp(entry, e89, a, "long")
    self._set_pos("long", entry, sl, tp, a)
    return Signal("enter", "long", meta={"sl": sl, "tp": tp, "trail_atr_mult": ...})
```

The short branch is the mirror image (`side in ("short","both")`, reversed EMA ordering, `x_long < -deadzone`, SL above EMA89).

### Gate 1 — `_trend_ok` (regime)

Two conditions:

1. **EMA stack ordering** on the last bar: long needs `ema21 > ema89 > ema200`; short needs `ema200 > ema89 > ema21`.
2. **Slope filter** on EMA89 and EMA200, normalised so the threshold is timeframe-comparable. `slope_mode="pct"` (the only mode used in all sweep configs) computes a per-bar fractional slope `(series[-1] - series[-1-L]) / (|series[-1-L]| * L)` over `slope_lookback` bars; long requires `slope89 >= slope_thr_mid` **and** `slope200 >= slope_thr_slow`. A `slope_mode="zscore"` path exists (rolling z-score of the slope) but is **not exercised** by any current config.

So "trend" = aligned EMA stack *that is also sloping with enough force*. Pure ordering is not enough.

### Gate 2 — `_pullback_seen` (entry timing)

Over the last `pullback_lookback` bars, checks whether price came within `pullback_atr_mult × ATR` of EMA89 — for a long, whether any bar's `Low` touched the band below/around EMA89; for a short, whether any `High` touched the band above. `any()` over the window, so it answers "did we recently retrace to the mid EMA", not "are we there right now".

### Gate 3 — `_rebound_ok` (re-engagement, toggleable)

When `require_rebound=True`: a long needs the last close back above EMA21 **and** rising (`close[-1] >= ema21[-1]` and `close[-1] > close[-2]`); short is mirrored. When `require_rebound=False` the gate is a no-op (`return True`). This is the only entry gate that can be switched off entirely (and `bghtrend_b2` does exactly that).

### Gate 4 — `_xtr_ok` (momentum)

**CORRECTION 2026-06-11 (tail-end cleanup), resolved same day:** this section previously claimed `x_long` is the `short_t3` leg — a misreading of the unpacking order. The strategy unpacks `_x_short, x_long, _x_t3, up_dot, down_dot`, so `x_long` binds **`long_term`** (position 1 of the 5-tuple) — and this is **deliberate**, confirmed against the original Pine Script where `longTermXtrender` is the "B-Xtrender Trend" (regime) component. Long entry requires `long_term[-1] > deadzone`; short requires `< -deadzone`; the stale-exit deadzone test also evaluates `long_term`. Economically coherent: at a pullback the short-term leg has just been crushed, so gating on it would block the very entries the strategy hunts; `long_term` asserts the regime still holds. `short_t3` feeds the dots (in-profit exits) only. Full evidence chain: [indicators-xtrender](indicators-xtrender.md) → Edge cases. The deadzone makes regime momentum a three-state signal.

### Post-gate — `_entry_distance_ok` (don't chase)

After all gates pass, a final veto: `|entry − EMA89| <= entry_max_atr_mult × ATR`. Even a valid setup is skipped if price has already run too far from the mid EMA, because the stop (anchored at EMA89) would then be too wide and the R:R geometry degrades. This is what keeps entries near the pullback rather than mid-extension.

## Exit logic

When in a position, exits are evaluated **before** any entry and in a fixed precedence. Trailing is updated first, then same-bar TP/SL, then momentum-dot, then stale timeout:

```python
if self._pos_side is not None:
    self._update_trailing(c_now, atr_now)              # 1. ratchet trail/SL

    hit = self._same_bar_hit(h_now, l_now, side)        # 2. TP/SL on this bar
    if hit == "tp":  ... return Signal("exit", reason="tp_hit")
    if hit == "sl":  ... self._cooldown_left = cooldown_bars
                     return Signal("exit", reason="sl_hit")

    if self._in_profit(c_now):                          # 3. momentum exhaustion, in profit only
        if side == "long" and down_dot[-1]:  return Signal("exit", reason="xtrender_peak")
        if side == "short" and up_dot[-1]:   return Signal("exit", reason="xtrender_trough")

    self._bars_in_trade += 1                            # 4. stale timeout
    if self._bars_in_trade >= stale_max_bars and abs(long_term[-1]) <= deadzone:
        return Signal("exit", reason="time_limit")

    return Signal("hold", side, meta={"sl", "tp", "trail"})
```

### 1. SL/TP computation — `_compute_sl_tp`

Stop is anchored to EMA89, not to entry:

```python
pad = sl_atr_mult * atr_now
# long:
sl   = ema89_now - pad
risk = max(1e-9, entry - sl)
tp   = entry + rr_target * risk
```

So **risk distance = entry − (EMA89 − pad)**, and the target is `rr_target ×` that distance. Because the stop sits just beyond the EMA the trade was entered against, a stop-out is a genuine regime-invalidation signal, not noise. The `max(1e-9, ...)` floor prevents a zero/negative risk distance (which would happen if entry were at or below the padded EMA) from producing a degenerate target.

### 2. Trailing stop — `_update_trailing` + `_same_bar_hit`

Each bar, the trail is recomputed as `close ∓ trail_atr_mult × ATR` and the SL is ratcheted monotonically (`max` for long, `min` for short) — it only ever tightens, never loosens. An initial trail is also seeded at entry in `_set_pos`. `_same_bar_hit` then checks whether the bar's High/Low crossed TP or SL; if **both** are hit on the same bar, `tp_has_priority` (default `True`) decides — TP wins. This is an optimistic same-bar resolution; the realistic-pessimistic alternative is noted under Limitations.

### 3. Momentum-dot exit (in profit only)

A `down_dot` (Xtrender just peaked) exits a profitable long; an `up_dot` exits a profitable short. Gated on `_in_profit` so the strategy never takes a momentum-exhaustion *loss* — losses are reserved for the stop. Reason codes: `xtrender_peak` / `xtrender_trough`.

### 4. Stale timeout

After `stale_max_bars` bars in trade, **and** only if regime momentum has gone flat (`|long_term| <= deadzone`), the position is closed (`time_limit`). This frees capital from positions that are neither winning to target nor stopping out — the "going nowhere in chop" case. Note the timeout is *conditional* on flat momentum; a position still showing momentum is held past the bar count.

### Cooldown

A stop-out sets `_cooldown_left = cooldown_bars`. While the counter is positive it is decremented at the top of `on_bar` and the bar returns a no-op `Signal()` — no exits evaluated, no entries taken. Prevents immediate re-entry into a setup that just failed.

### Exit edge cases

- **Gaps:** no special handling. A gap through the SL is filled at the bar's High/Low test in `_same_bar_hit`, not at the stop price — backtest fills are optimistic relative to a live gap. Flagged for the microstructure work (ADR-011, Session 3).
- **TP and SL same bar:** resolved by `tp_has_priority`, default TP wins (optimistic).
- **Backtester boundary close:** `backtesting.py` force-closes any open position on the last bar of a run/fold; the walk-forward analyzer counts these as `boundary_closes`. Not a strategy behaviour, but it shapes per-fold trade stats.

## Indicators used

| Indicator | Source | Role |
|---|---|---|
| EMA21 / EMA89 / EMA200 | `core.ema` | Trend stack (ordering) + slope filter (EMA89/200). EMA89 also anchors the pullback band and the stop. |
| ATR | `core.atr` (EWM, span = `pullback_atr_len`) | Pullback band width, entry-distance cap, SL padding, trailing-stop step. |
| Xtrender `long_term` | `xtrender_components` | Regime momentum confirmation at entry + stale-exit flatness test (vs deadzone). |
| Xtrender `short_t3` (via dots) | `xtrender_components` | In-profit exit timing — local extrema (`up_dot`/`down_dot`). |
| Xtrender `up_dot` / `down_dot` | `xtrender_components` | In-profit momentum-exhaustion exits. |

Full Xtrender formula and parameter meanings: [indicators-xtrender](indicators-xtrender.md).

## Parameters and taxonomy

Every field of `XtrenderPullbackParams` is below, classified **core / tuning / ambiguous**:

- **core** — changing it by ~20% changes the strategy *economically* (a different regime is detected, a different payoff structure, a different entry timescale). These define what the strategy *is*.
- **tuning** — changing it by ~20% changes the *numbers* (more/fewer signals, slightly different exits) but not the decision semantics. These are the overfitting-prone knobs; in Session 6 (parameter stability) they must show a robust plateau, not a knife edge.
- **ambiguous** — genuinely on the border, with the reason stated. Treated as "core-leaning, watch like tuning" in stability analysis.

Ranges are the union across `bghtrend_b1..b4` (see [config-reference](../config-reference.md) for per-config detail).

| Parameter | Type | Range (b1..b4) | Category | Rationale |
|---|---|---|---|---|
| `ema_fast` | int | 9–25 | **core** | Fast-trend speed and rebound reference. 9–15 (b3) vs 21–25 (b4) is a different definition of "short-term trend". |
| `ema_mid` | int/choice | 45–110 | **core** | The pullback anchor and stop reference. Moving EMA89→EMA55 changes *where* the trade is entered and stopped. |
| `ema_slow` | choice/int | 200–220 | **core** | Macro-regime definition. 200 is the canonical macro EMA; only b4 widens to 220. |
| `slope_lookback` | choice | 21–55 | **core** | Horizon over which trend slope is measured — sets the regime timescale. 21 (b3) vs 55 (b4) is a different "trend". |
| `rr_target` | choice | 1.2–2.0 | **core** | Reward:risk multiple — the payoff structure. 1.2 vs 2.0 changes win-rate/expectancy regime fundamentally. |
| `side` | choice | both | **core** | Trade direction. Fixed to `both` in every config, but it is a first-class strategy definition. |
| `sl_atr_mult` | float | 0.30–0.75 | *ambiguous* | Sets stop padding beyond EMA89 → directly sets the *risk distance*, which (with `rr_target`) sets the target. Numerically a knob, economically it co-defines R. |
| `pullback_atr_mult` | float | 0.08–0.25 | *ambiguous* | Width of the "near EMA89" band → defines what a pullback *is*. Tight (0.10) vs loose (0.25) is arguably a semantic change in entry geometry. |
| `require_rebound` | choice | false/true | *ambiguous* | Toggles an entire entry gate on/off. Boolean, but switching it removes a confirmation condition (b2 runs without it). |
| `short_l1`/`short_l2`/`short_l3`/`long_l1`/`long_l2`/`t3_len`/`t3_b` | int/float | see config-ref | *ambiguous* | Xtrender internals. Individually each is a tuning knob; *collectively* they define the momentum filter the thesis relies on. Treat as a block. |
| `slope_thr_mid` | float | 2e-5–1e-4 | tuning | Strictness of the EMA89 slope gate. Higher = fewer, stronger-trend entries. Same logic, different count. |
| `slope_thr_slow` | float | 1.5e-5–6e-5 | tuning | Strictness of the EMA200 slope gate. As above for the macro leg. |
| `deadzone` | float | 1.5–5.0 | tuning | Momentum threshold (also the stale-exit flatness bar). Higher = fewer entries, same logic. Top overfitting suspect. |
| `pullback_lookback` | int | 8–24 | tuning | How many bars back to scan for a pullback touch. Window size, not semantics. |
| `pullback_atr_len` | int | 10–20 | tuning | ATR span used for the band/stop. Smoothing length of the volatility estimate. |
| `entry_max_atr_mult` | float | 0.40–1.20 | tuning | Max entry distance from EMA89 — the "don't chase" cap. Tighter = stricter, same logic. |
| `trail_atr_mult` | float | 1.2–3.0 | tuning | Trailing-stop width. Tighter = exits winners sooner. Affects exit timing, not entry semantics. |
| `stale_max_bars` | int | 20–80 | tuning | Bars before the flat-momentum timeout. Patience knob. |
| `cooldown_bars` | int | 5–20 | tuning | Pause after a stop-out. Anti-revenge knob. |
| `slope_mode` | choice | pct | tuning (fixed) | Slope normalisation method. Fixed to `pct` in all configs; `zscore` path exists but is never selected. |
| `zscore_window` | choice | 60–160 | tuning (**inert**) | Window for the z-score slope — **sampled by every config but never used**, because `slope_mode` is always `pct`. A phantom sweep dimension. |
| `tp_has_priority` | choice | true | tuning (fixed) | TP-vs-SL same-bar tie-break. Fixed `true` everywhere. |
| `trade_on_close` | choice | true | tuning (fixed) | Execution-on-close flag. Cosmetic/engine-level, fixed `true`. |
| `tp_pct` / `sl_pct` | float/None | null | tuning (inert) | Alternative percentage TP/SL. Always `null`; the strategy uses ATR/EMA stops instead. |

**Overfitting watchlist for Session 6:** `deadzone`, `slope_thr_mid`, `slope_thr_slow`, `trail_atr_mult`, `pullback_atr_mult`, `sl_atr_mult`, and the Xtrender block. These are where a great in-sample Sharpe most easily turns out to be a knife edge.

## Known limitations

- **Single-symbol, single-position.** No portfolio context; `_pos_side` holds at most one open trade. Multi-asset / concurrent positions are out of scope until a portfolio layer exists (post-MVP).
- **No position sizing inside the strategy.** `on_bar` emits `enter`/`exit` with SL/TP in `meta` but does not set `Signal.size`. Risk-based sizing (`algo_bot.risk.position_size`) is caller-driven and not wired here — the backtester uses its default sizing. See [risk-limits](risk-limits.md).
- **Execution assumption is taker-on-close, optimistic fills.** Entries/exits resolve at bar Close (or High/Low for SL/TP tests) with no spread, no slippage, no funding. Same-bar TP+SL resolves in TP's favour, and gaps fill at the bar's range rather than the stop price. All of this makes backtest fills rosier than live — the explicit motivation for ADR-011 microstructure adjustments (Session 3). The strategy is **not** maker-aware; it does not post limit orders or model queue position.
- **Indicators recomputed per bar.** No incremental indicator state — correct but O(n) per bar; fine for backtests, a consideration for high-frequency live loops.
- **`zscore` slope mode is dead code in practice.** Supported but never selected by any config. The phantom `zscore_window` sweep dimension was removed from `bghtrend_b1..b4.yaml` on 2026-06-11 (tail-end cleanup); the dataclass default (100) and the dormant `_slope_zscore` branch remain. Re-opening the branch requires an ADR.
- **EMA monotonicity — validated at runtime since 2026-06-11 (tail-end cleanup).** `XtrenderPullbackParams.__post_init__` enforces `ema_fast < ema_mid < ema_slow` (strict) and raises `ValueError` with a readable message on violation. Previously an inverted set would silently trade zero times; now every construction path (`algo-backtest`, `algo-sweep`, `algo-walkforward` — all via `coerce_params`) fails fast. Tests: `tests/test_bghtrend_params.py`.

## Consumers

- `algo_bot/engine/backtester.py` (`run_backtest`) — direct execution engine; loads the strategy by name, drives `on_bar` per bar.
- `algo_bot/engine/sweep.py` (`algo-sweep`) — parameter-space exploration; consumes `config/bghtrend_b1..b4.yaml` as random-search spaces.
- `algo_bot/engine/walkforward.py` (`algo-walkforward`) — out-of-sample validation per fold; the primary Phase 2 consumer ([walkforward](walkforward.md)).
- Phase 2 notebook `03_bghtrend_walkforward_analysis.ipynb` (planned, Session 5) — research/interpretation surface.

## See also

- [Reference — indicators-xtrender](indicators-xtrender.md) — the momentum oscillator in detail
- [Reference — config-reference](../config-reference.md) — `bghtrend_b1..b4` sweep spaces, per-parameter ranges, validation rules
- [Reference — walkforward](walkforward.md) — out-of-sample harness consuming this strategy
- [Reference — risk-limits](risk-limits.md) — portfolio gates and `position_size` (not auto-wired here)
- [ADR-003](../../adr/003-strategybase-signal-api.md) — `StrategyBase` / `Signal` contract
- [ADR-004](../../adr/004-hybrid-tp-sl-mode.md) — TP/SL execution modes (relevant for live)
- [Concepts — Glossary](../../concepts/glossary.md) — pullback, trend following, ATR trail, R:R, cooldown, xtrender
- Source: `algo_bot/strategies/bghtrend_pullback.py`
