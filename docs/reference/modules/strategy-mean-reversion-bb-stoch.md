# Module reference — `algo_bot.strategies.mean_reversion_bb_stoch`

> **🚧 DRAFT skeleton (MR-Session Beta, 2026-07-10).** This is the initial
> reference scaffold created alongside the strategy implementation. The full
> deep walkthrough — worked entry/exit examples, parameter taxonomy with
> overfitting watchlist, Mastermind cross-check — lands in **MR-Session 1
> (Audit)**, the mean-reversion analogue of the bghtrend Session 1. Sections
> below marked _(TBD)_ are placeholders.

The Phase 2 MVP candidate after the bghtrend no-go ([ADR-012](../../adr/012-mvp-no-go-bghtrend.md)).
A contrarian **mean-reversion** strategy on Bollinger Bands with an optional
Stochastic confirmation. It waits for price to reach a band (a statistical
stretch from the local mean), waits for the candle to close, and enters on the
first *reaction* candle back toward the mean. Target is the opposite band; stop
is a fixed percentage. Single-symbol, single-position, both directions.

Methodological prior: **Mastermind MMS** (mastermindzx.pl) — see the project
memory note `reference-mastermind-mms`. This module implements the *bare core*
of that methodology only; its actual claimed edge (pyramiding + sequential
leverage reduction) is explicitly out of scope — see [Scope](#mvp-scope--whats-deferred).

The strategy/Signal API contract is [ADR-003](../../adr/003-strategybase-signal-api.md).
Microstructure (slippage + funding) overlay is [ADR-011](../../adr/011-microstructure-adjustments.md).
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

- **What it trades:** one symbol, one position at a time, either side (`side="both"` default).
- **Timeframe:** TF-agnostic in code; sweep configs tuned to TF bands — `mr_b1`/`mr_b2` for 1h, `mr_b3` for 15m.
- **Signal cadence:** evaluated once per closed bar via `on_bar(df) -> Signal`. Position management is checked before new entries.
- **Indicators:** Bollinger Bands (`bbands`, mean ± num_std·σ, population σ) + Stochastic slow (`stochastic`, 14/3/3) — both added to `algo_bot/indicators/core.py` in this session, both causal (precompute-safe).

## Economic thesis

When price reaches a Bollinger Band it is statistically stretched (`num_std`
standard deviations) from its local mean. Absent a fundamental reason for a
durable breakout, the market reverts. We enter **contrarian**: long at the
lower band, short at the upper. To avoid catching a falling knife we do **not**
enter on the touch itself — we wait for the touch candle to close and then for
the next candle to *react* back toward the mean (bullish body for a long,
bearish for a short). Optionally the Stochastic confirms the touch coincides
with a momentum extreme (oversold / overbought).

This is a pure counter-trend bet and is the structural opposite of the
(retired) bghtrend trend-following candidate — deliberately so, to test a
different edge on the same framework.

## Entry mechanics — "armed → reaction"

Both-directions, symmetric. Decided in the MR-Session Beta kickoff (options +
trade-offs recorded below):

1. **ARMED.** Touch candle S reaches the band by wick: `Low ≤ lower` (long) /
   `High ≥ upper` (short). In `entry_mode="bb_stoch"` the touch must coincide
   with a Stochastic extreme (`%K < oversold` for long / `> overbought` for
   short). Arms the direction for `arm_expiry_bars` subsequent bars.
2. **ENTRY.** The first armed *reaction* candle R (body toward the mean:
   long → `Close > Open`; short → `Close < Open`; optional `require_reclaim`
   also demands `Close` back inside the band). Entry executes on R's close
   (`trade_on_close`). No reaction within the window → disarm.

**Design note (discovered while writing the Beta tests):** the Stochastic gate
is applied at **arming (the touch)**, not at the reaction candle. A reaction
candle by definition turns back toward the mean and lifts `%K`, so an
oversold/overbought gate evaluated on R would almost never fire — the
oscillator extreme lives where price touches the band.

## Exit mechanics

No trailing / break-even / timeout — deliberately (see Scope).

- **TP = opposite, live band.** Recomputed every bar (long → current upper;
  short → current lower). A dynamic target that chases price back through the
  mean. Decision 3: live band, not the band frozen at entry.
- **SL = fixed `sl_pct`** (default 2%) from entry price.
- **Same-bar TP&SL:** resolved by `tp_has_priority` (default **False → SL wins**,
  conservative: do not overstate edge on a bar that pierces both levels).

Exits execute at the touch bar's close (`trade_on_close`), which is conservative
relative to an idealised limit/stop fill exactly on the level.

## Parameters

`ParamSchema = MeanReversionBBStochParams` (frozen dataclass, `__post_init__`
fail-fast validation). Full core-vs-tuning taxonomy with overfitting watchlist:
_(TBD — MR-Session 1)_.

| Group | Params | Notes |
|---|---|---|
| Bollinger Bands | `bb_window`, `bb_num_std` | Core. Prior: 20±5, 2.0±0.5 (Mastermind). Population σ (TA-Lib-consistent). |
| Stochastic | `stoch_k`, `stoch_d`, `stoch_smooth`, `stoch_oversold`, `stoch_overbought` | Prior: 14/3/3, 20/80 (frozen in b1). |
| Entry | `entry_mode` (`bb_only`/`bb_stoch`), `arm_expiry_bars`, `require_reclaim` | `entry_mode` is the key sweep dimension (Decision 2). |
| Exit | `sl_pct`, `tp_has_priority` | SL fixed ~2%; TP structural (opposite band). |
| Scope | `side`, `trade_on_close` | `both` default. |

## Session decisions (MR-Session Beta, 2026-07-10)

Resolved up-front with options + trade-offs (per project mindset):

1. **Entry mechanization on pure H1 OHLC** — chose the two-bar *armed → reaction*
   proxy (wick touch arms; body-direction reaction triggers). M5 sub-bar marking
   is out of scope (no intrabar data). Alternatives considered: single-bar
   close-through (falling-knife risk), pierce-and-reclaim.
2. **Stochastic role** — `entry_mode ∈ {bb_only, bb_stoch}` sweep dimension
   rather than hard-drop or hard-gate; the sweep (MR-Session 2) decides
   empirically whether Stochastic adds edge. Gate applied at arming (see note).
3. **TP = opposite band** — live (recomputed each bar), not frozen at entry.
4. **Funding** — no strategy mechanics; rely on the existing ADR-011 overlay
   (`--microstructure full`), report raw vs post. See below.

## Funding interaction (ADR-011)

With no timeout, a position can be held long, so funding is a real flow.
Note the sign: **contrarian MR tends to be on the funding-*receiving* side**
(short in euphoria / long in capitulation) — a potential tailwind, not merely a
cost, and the opposite of trend-following. We do **not** mechanize this in MVP;
we measure it via the post-microstructure metrics. The genuine tail is
unbounded hold-time × funding — flagged for the future ADR.

## MVP scope — what's deferred

This module is the **bare core** only. Explicitly deferred to a separate ADR
(possible early trigger for a backtest-engine migration, since they are a state
machine above single positions that `backtesting.py` cannot express natively):

- **Pyramiding** (adding to a winning base position).
- **Sequential leverage reduction** (anti-martingale x1 → x0.1 after a full SL).
- **Position sizing** (strategy returns a bare `Signal` without `size`).
- **Timeout / funding-aware exits** (first thing the future ADR should test).

Beta results must be read as "the base, not the full system."

## Tests

- `tests/test_indicators_bbands_stochastic.py` — independent-oracle for `bbands`/`stochastic` (handcomputed, prefix-invariance, literals, API contract).
- `tests/test_mean_reversion_bb_stoch.py` — params validation, execution helpers, both-direction entry gates, exit precedence, precompute equivalence.

## See also

- [ADR-003](../../adr/003-strategybase-signal-api.md) — StrategyBase / Signal API.
- [ADR-011](../../adr/011-microstructure-adjustments.md) — microstructure overlay.
- [ADR-012](../../adr/012-mvp-no-go-bghtrend.md) — bghtrend no-go / the pivot.
- `algo_bot/indicators/core.py` — `bbands`, `stochastic`.
- ROADMAP → Phase 2 → MR-Session map.
