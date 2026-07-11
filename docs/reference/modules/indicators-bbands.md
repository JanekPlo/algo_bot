# Module reference — `algo_bot.indicators.core.bbands`

Bollinger Bands: rolling mean ± `num_std` rolling standard deviations. Added in
MR-Session Beta (2026-07-10) for the `mean_reversion_bb_stoch` strategy; this deep
reference lands in MR-Session 1 (Audit, 2026-07-11). Pattern follows
[indicators-xtrender.md](indicators-xtrender.md).

## At a glance

```python
from algo_bot.indicators import bbands

upper, mid, lower = bbands(close, window=20, num_std=2.0)
```

- **Input:** `close: pd.Series` (any float series; strategy passes `df["Close"]`).
- **Output:** 3-tuple `(upper, mid, lower)` of `pd.Series`, indexed like the input,
  dtype float64. **Position order matters** — `upper` first. The strategy unpacks
  `upper, _mid, lower = bbands(...)`; verified against this contract in the
  MR-Session 1 oracle pass (no xtrender-style off-by-one).
- **Warmup:** first `window - 1` values are NaN (pandas `rolling` with default
  `min_periods = window`).

## Formula

```
mid[t]   = SMA(close, window)[t]
sd[t]    = STD_pop(close[t-window+1 .. t])          # ddof = 0 (population)
upper[t] = mid[t] + num_std · sd[t]
lower[t] = mid[t] − num_std · sd[t]
```

### Why `ddof=0` (population), not the pandas default `ddof=1`

Deliberate decision (Beta): **consistency with `talib.BBANDS`**, which divides by `n`,
not `n-1` — so backtest results stay comparable with any TA-Lib-based reference. The
difference is the factor `sqrt(n/(n-1))`; for `window=20` the ddof=0 bands are **~2.6%
narrower** than ddof=1 bands. Guarded by a dedicated regression test
(`test_population_std_not_sample`) that fails if anyone flips the ddof.

Note the economic side effect: narrower bands ⇒ *more* touches ⇒ slightly more
(weaker) setups than a ddof=1 implementation would produce at the same `num_std`.

## Edge cases

- **Zero variance** (flat window): `sd = 0` ⇒ `upper == mid == lower`. No guard is
  needed — no division occurs. Downstream consequence in the strategy: a flat market
  makes `High ≥ upper` and `Low ≤ lower` simultaneously true, which lands in the
  both-bands-touch branch and arms **nothing** (regression-tested in
  `TestAuditSeams::test_both_bands_touch_no_arm`).
- **NaN in input:** propagates through the rolling window (default `min_periods`) —
  every window containing a NaN yields NaN. Convention module-wide: no NaN handling,
  caller decides (see `core.py` header).
- **Ordering invariant:** `upper ≥ mid ≥ lower` wherever non-NaN (num_std ≥ 0);
  tested.

## Causality / precompute

`rolling` without `center` uses only data ≤ t, therefore
`bbands(full).iloc[:m] == bbands(prefix_m)` exactly (prefix invariance, tested at
rtol=1e-12). This makes the function safe for the `StrategyBase.precompute` cache:
the strategy computes bands **once** on full history and reads prefixes in `on_bar`
(the O(n²)→O(n) hook from the Session-4 perf fix).

## Hand-verifiable oracle identity

For `window=2, num_std=1`: population sd of a pair {a, b} is `|a−b|/2`, so
`upper = mean + sd = max(a, b)` and `lower = min(a, b)`. This identity — checkable
entirely in one's head — is encoded as a literal test
(`test_window2_numstd1_minmax_identity`), complementing the loop-based
first-principles oracle.

## Consumers

- `algo_bot/strategies/mean_reversion_bb_stoch.py` — arming levels (touch detection)
  and the live opposite-band TP. Uses `upper`/`lower`; `mid` is currently unused by
  any strategy (kept for API completeness and future `require_reclaim`-to-mid or
  zscore-style variants).

## Tests

`tests/test_indicators_bbands_stochastic.py::TestBBands` — first-principles oracle
(plain-Python SMA + population sd, no pandas), ddof regression, constant-price
literal, ordering/warmup, prefix invariance, window-2 identity, API contract
(3-tuple, index, dtype).

## See also

- [strategy-mean-reversion-bb-stoch.md](strategy-mean-reversion-bb-stoch.md) — the consumer.
- [indicators-stochastic.md](indicators-stochastic.md) — the paired oscillator.
- `docs/references/mms/01-position-building.md` — the methodological prior for band
  choice (BB as one of three interchangeable ATR-derivative envelopes in MMS).
