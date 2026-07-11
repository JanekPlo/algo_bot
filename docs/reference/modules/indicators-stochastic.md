# Module reference — `algo_bot.indicators.core.stochastic`

Stochastic Oscillator, **slow** variant. Added in MR-Session Beta (2026-07-10) for
the `mean_reversion_bb_stoch` strategy; deep reference from MR-Session 1 (Audit,
2026-07-11). Pattern follows [indicators-xtrender.md](indicators-xtrender.md).

## At a glance

```python
from algo_bot.indicators import stochastic

pct_k, pct_d = stochastic(df, k=14, d=3, smooth=3)
```

- **Input:** `df: pd.DataFrame` with `High`, `Low`, `Close` columns.
- **Output:** 2-tuple `(%K, %D)` of `pd.Series` in [0, 100], indexed like `df`,
  dtype float64. Strategy unpacks `pct_k, _pct_d = stochastic(...)` — order verified
  in the MR-Session 1 oracle pass.
- **Warmup:** raw %K needs `k` bars; returned %K is valid from bar `k + smooth − 2`
  (0-based), %D from `k + smooth + d − 3`. NaN before that.

## Formula (slow variant)

```
LL_k[t]    = min(Low[t-k+1 .. t])
HH_k[t]    = max(High[t-k+1 .. t])
%K_raw[t]  = 100 · (Close[t] − LL_k[t]) / (HH_k[t] − LL_k[t] + 1e-12)
%K[t]      = SMA_smooth(%K_raw)[t]        ← returned as %K ("slow %K")
%D[t]      = SMA_d(%K)[t]                 ← signal line
```

Standard "(14, 3, 3)" maps to `k=14, d=3, smooth=3`. Variants differ only in the
number of smoothings: `smooth=1` degenerates to the **fast** stochastic (raw %K).

### Zero-range guard

Flat window (`HH_k == LL_k`) would divide by zero; the `+1e-12` in the denominator
(same convention as `rsi`) makes the result finite and deterministic: the numerator
is then also 0, so `%K_raw = 0`. Degenerate but harmless — and in the strategy a NaN
or extreme-low %K in `bb_stoch` mode simply gates the long side. Literal-tested
(`test_flat_window_literal`).

## Semantics worth knowing

- **Bounded [0, 100]:** Close always lies within [LL, HH] of its own window, and SMA
  cannot escape the hull. Tested.
- **Smoothing dilutes extremes** — discovered during Beta testing: with `smooth=3`
  a single extreme bar rarely pushes %K past 20/80 thresholds on synthetic data;
  the *touch* bar has the extreme %K, the *reaction* bar already pulls it back.
  This is exactly why the strategy applies the Stoch gate **at arming**, not at the
  reaction bar (see the strategy reference), and why tests that need deterministic
  gating use `smooth=1`.
- **%D is currently unused by the strategy** — `on_bar` gates on %K only, and
  `precompute` caches only %K. This is a deliberate Beta simplification vs the MMS
  convention (the classic add-on signal in MMS is a **%K & %D cross** of 20/80 —
  `docs/references/mms/02-position-management-filters.md`). If a future iteration
  adopts the cross semantics, %D is already computed and tested.

## Causality / precompute

Rolling min/max/mean without `center` use only data ≤ t; prefix invariance holds
exactly and is tested (rtol=1e-12). Safe for `StrategyBase.precompute`.

## Consumers

- `algo_bot/strategies/mean_reversion_bb_stoch.py` — optional entry gate
  (`entry_mode="bb_stoch"`): long requires `%K < stoch_oversold` at the arming
  (touch) bar; short requires `%K > stoch_overbought`. NaN %K → gate closed.

## Tests

`tests/test_indicators_bbands_stochastic.py::TestStochastic` — first-principles
oracle (plain-Python rolling min/max + SMA chains), boundedness, flat-window literal,
close-at-high ⇒ %K=100 literal, prefix invariance, API contract (2-tuple, index,
dtype).

## See also

- [strategy-mean-reversion-bb-stoch.md](strategy-mean-reversion-bb-stoch.md) — the consumer.
- [indicators-bbands.md](indicators-bbands.md) — the paired band indicator.
- `docs/references/mms/02-position-management-filters.md` — MMS's actual use of the
  oscillator (add-on filter, %K & %D cross, 14/3/3 on H1).
