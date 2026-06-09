# Module reference — `algo_bot.indicators.xtrender`

Custom momentum oscillator (Bryan G. Howell "Xtrender" variant) built on the primitives in `algo_bot.indicators.core`. It combines a short-term and a long-term RSI-of-EMA signal, smooths the short-term leg with a T3 moving average, and emits local-extremum markers ("dots"). The only current consumer is `bghtrend_pullback`, where the smoothed short-term leg is the momentum-confirmation filter at entry and the dots drive a profit-taking exit.

This is a deep reference for the indicator in isolation. For how the strategy *uses* it, see [strategy-bghtrend-pullback](strategy-bghtrend-pullback.md). For the underlying `ema` / `rsi` / `t3` primitives, see [`algo_bot.indicators.core`](../package-overview.md).

## At a glance

```python
from algo_bot.indicators import xtrender_components

short_term, long_term, short_t3, up_dot, down_dot = xtrender_components(
    df["Close"],
    short_l1=5, short_l2=20, short_l3=15,
    long_l1=20, long_l2=15,
    t3_len=5, t3_b=0.7,
)

# Momentum confirmation, as bghtrend_pullback uses it:
if short_t3.iloc[-1] > deadzone:      # bull momentum present
    ...
elif short_t3.iloc[-1] < -deadzone:   # bear momentum present
    ...
# else: |short_t3| <= deadzone -> "no clear momentum", skip the trade
```

## Input shape

A single `pd.Series` of close prices (`df["Close"]`) with a monotonic `DatetimeIndex`. The function is path-dependent through the EWM smoothers (`ema`, `rsi`, `t3` all use `ewm(adjust=False)`), so it must be fed a *contiguous* price series — slicing the tail and recomputing will not reproduce the same values unless enough warm-up history precedes the slice. `bghtrend_pullback` guards this with a `len(df) >= need + 5` check before calling.

No `High`/`Low`/`Volume` are read — Xtrender is a pure close-price oscillator.

## Formula

Three layered transforms, all on close:

```
short_term = rsi( ema(close, short_l1) - ema(close, short_l2), short_l3 ) - 50
long_term  = rsi( ema(close, long_l1), long_l2 )                          - 50
short_t3   = t3( short_term, t3_len, t3_b )
```

Reading it from the inside out:

1. **Short-term leg.** `ema(close, short_l1) - ema(close, short_l2)` is a fast-minus-slow EMA spread — a MACD-style momentum line. Feeding that spread into `rsi(..., short_l3)` normalises it into a bounded `0..100` oscillator, and subtracting `50` recentres it on zero. So `short_term` is "how stretched is the fast/slow EMA spread, on a normalised, mean-zero scale". Positive = fast EMA pulling above slow EMA (up-momentum).

2. **Long-term leg.** `rsi(ema(close, long_l1), long_l2) - 50` is the RSI of a smoothed price, recentred on zero — a slower, regime-level momentum reading. **Note:** `bghtrend_pullback` computes `long_term` but does not gate on it (see Consumers); it is available for diagnostics and future filters.

3. **T3 smoothing.** `short_t3 = t3(short_term, t3_len, t3_b)` runs the short-term leg through a Tillson T3 — a six-fold EWM with a volume-factor `b` that lets the filter "lead" rather than lag. This is the series the strategy actually thresholds against, because the raw `short_term` is too noisy for a clean deadzone test.

### T3 internals (from `core.t3`)

T3 is a weighted combination of six successive EWMs (`e1..e6`) of the input:

```
c1 = -b^3
c2 = 3b^2 + 3b^3
c3 = -6b^2 - 3b - 3b^3
c4 = 1 + 3b + b^3 + 3b^2
t3 = c1*e6 + c2*e5 + c3*e4 + c4*e3
```

`b` (the "volume factor", `t3_b` in params) controls responsiveness: higher `b` → more aggressive lead and more overshoot; lower `b` → smoother, more lag. Typical range `0.6–0.8`. `t3_len` is the span of each constituent EWM.

## Dots — local extrema markers

```python
up_dot   = (st > st.shift(1)) & (st.shift(1) < st.shift(2))   # local trough in short_t3
down_dot = (st < st.shift(1)) & (st.shift(1) > st.shift(2))   # local peak in short_t3
```

`up_dot[t]` is `True` when bar `t-1` was a local minimum of `short_t3` (the oscillator just turned up); `down_dot[t]` is `True` when bar `t-1` was a local maximum (just turned down). Both are returned with `.fillna(False)` so the first two bars are `False` rather than `NaN`.

In `bghtrend_pullback` these are used **only as in-profit exit triggers**: a `down_dot` exits a profitable long ("momentum peaked, bank it"), an `up_dot` exits a profitable short. They are deliberately not used for entries.

## Public API

```python
def xtrender_components(
    close: pd.Series,
    short_l1=5, short_l2=20, short_l3=15,
    long_l1=20, long_l2=15,
    t3_len=5, t3_b=0.7,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]
```

Returns a **5-tuple**: `(short_term, long_term, short_t3, up_dot, down_dot)`.

| Position | Name | dtype | Meaning |
|---|---|---|---|
| 0 | `short_term` | `float` series | Raw short-term oscillator, mean-zero. Noisy; not thresholded directly. |
| 1 | `long_term` | `float` series | Slow regime-level momentum, mean-zero. Currently diagnostic only. |
| 2 | `short_t3` | `float` series | T3-smoothed short-term leg. **This is the momentum filter.** |
| 3 | `up_dot` | `bool` series | Local trough of `short_t3` (turned up). In-profit short exit. |
| 4 | `down_dot` | `bool` series | Local peak of `short_t3` (turned down). In-profit long exit. |

### Parameters

| Param | Type | Default | Meaning |
|---|---|---|---|
| `short_l1` | int | 5 | Fast EMA span in the short-term spread. |
| `short_l2` | int | 20 | Slow EMA span in the short-term spread. `short_l2 > short_l1` is the sane ordering (fast minus slow). |
| `short_l3` | int | 15 | RSI length applied to the EMA spread. |
| `long_l1` | int | 20 | EMA span feeding the long-term RSI. |
| `long_l2` | int | 15 | RSI length of the long-term leg. |
| `t3_len` | int | 5 | Span of each of the six EWMs inside the T3. |
| `t3_b` | float | 0.7 | T3 volume factor (responsiveness vs smoothness), typically 0.6–0.8. |

All parameters are plumbed straight through from `XtrenderPullbackParams` of the strategy; the indicator does no validation of its own (`core` convention — caller owns sanity).

## Interpretation

- `short_t3 > 0` and rising → bull momentum.
- `short_t3 < 0` and falling → bear momentum.
- `|short_t3| < deadzone` (deadzone ≈ 1.5–5 in the sweep configs) → no clear momentum; the strategy filters the trade out. The deadzone lives on the *strategy* side, not the indicator — `xtrender_components` just returns the raw smoothed value.

The deadzone is what turns a continuous oscillator into a three-state signal (bull / flat / bear) and is one of the most overfitting-prone knobs in the strategy (see the parameter taxonomy in the strategy reference).

## Edge cases and conventions

- **No NaN handling on the float legs.** `short_term`, `long_term`, `short_t3` carry the EWM warm-up `NaN`/transient behaviour straight through (the `core` convention is "caller decides dropna/fillna"). Only the boolean dots are `.fillna(False)`.
- **Path dependence.** Because every layer is `ewm(adjust=False)`, the result on bar `t` depends on all prior bars. Recompute on a freshly sliced window only after enough warm-up, or the leading values differ from a full-history computation.
- **`rsi` denominator guard.** `core.rsi` adds `1e-12` to the average-loss denominator, so a flat or strictly monotonic input does not divide by zero; it saturates toward 100 (or 0) instead.
- **`long_term` is computed but unused by the only consumer.** It is intentionally kept in the return tuple for diagnostics and as a hook for a future regime filter. Do not assume it influences any current trade.

## Limitations

- **Docstring drift in the source.** The module docstring in `xtrender.py` describes the return as `tuple(st, lt, st_t3)` (a 3-tuple), but the function returns a **5-tuple** including `up_dot` / `down_dot`. The 5-tuple is authoritative; the docstring predates the dots and should be corrected in a future code-touching session (out of scope for this docs-only session).
- **Hand-rolled, not TA-Lib.** This is our own implementation on top of `core` primitives, so it will not bit-match TradingView's or any vendor's "Xtrender". Treat absolute values as internal-only; only relative comparisons (vs deadzone, vs prior bars) are meaningful across environments.
- **No standalone tests.** Coverage today is indirect, through the `bghtrend_pullback` path. A dedicated `tests/test_xtrender.py` (handcomputed `short_t3` on a small fixture, dot detection on a synthetic up/down sequence) is a sensible follow-up if Xtrender is ever reused by a second strategy.

## Consumers

- `algo_bot/strategies/bghtrend_pullback.py` — calls `xtrender_components` once per bar in `on_bar`; uses `short_t3` against `deadzone` for entry confirmation (`_xtr_ok`) and `up_dot`/`down_dot` for in-profit exits. `short_term` and `long_term` are received but ignored.

## See also

- [Reference — strategy-bghtrend-pullback](strategy-bghtrend-pullback.md) — the consuming strategy, entry/exit flow, parameter taxonomy
- [Reference — package-overview](../package-overview.md) — `algo_bot.indicators.core` (`ema`, `rsi`, `atr`, `t3`)
- [Concepts — Glossary](../../concepts/glossary.md) — `xtrender`, `RSI`, `EMA`, `deadzone`
- Source: `algo_bot/indicators/xtrender.py`, `algo_bot/indicators/core.py`
