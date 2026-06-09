# Reference — configuration files

Everything under `config/`: the global project config (`config.yaml`) and the strategy sweep spaces (`bghtrend_b1..b4.yaml`). This document is the schema-and-semantics reference; for what the parameters *mean economically* see [strategy-bghtrend-pullback](modules/strategy-bghtrend-pullback.md), and for the sweep engine that consumes these files see `algo_bot/engine/sweep.py`.

## Global config — `config/config.yaml`

The project-wide config. Loaded for data paths, backtest defaults, and the feature-engineering pipeline. **Note:** the per-strategy `optimize` blocks in this file are the *legacy grid-search* format used by older strategies (`bollinger_band_breakout_short`, `short_trend_following`, `simple_momentum`). `bghtrend_pullback` does **not** use them — its parameter space lives in the dedicated `bghtrend_b*.yaml` files consumed by `algo-sweep --space_file`.

| Key | Type | Meaning |
|---|---|---|
| `data.raw_dir` | str | Directory for raw exchange CSVs, relative to project root. Default `bot_data/raw`. |
| `data.processed_dir` | str | Directory for processed (feature-computed) CSVs. Default `bot_data/processed`. The backtester reads `binance_<SYMBOL>_<TF>.csv` from here. |
| `backtest.cash` | int | Starting capital in quote currency (USD). Default `1_000_000`. Note `algo-sweep` overrides this with its own `--cash` default (`200_000`). |
| `backtest.commission` | float | Per-trade commission fraction. `0.002` = 0.2%. Note `algo-sweep`/`run_backtest` default to `0.0004` (4 bps) — the global value here is the legacy default, not what the Phase 2 sweep uses. |
| `backtest.trade_on_close` | bool | Whether orders execute on bar close. `true`. |
| `defaults.features` | list | Feature-engineering pipeline for `compute_features`: a list of `{type, params}` TA blocks (e.g. `BBANDS`, `RSI`). Applied during `algo-process`, independent of `bghtrend`'s own internally-computed indicators. |
| `strategies.<name>.run` | dict | Extra kwargs passed to `bt.run()` for that strategy. |
| `strategies.<name>.optimize` | dict | Legacy grid-search space (lists of values per param + `maximize` objective). Superseded for `bghtrend` by the `bghtrend_b*.yaml` files. |

There is a value mismatch worth knowing: `config.yaml` carries `cash=1_000_000` / `commission=0.002`, but the Phase 2 sweep path (`algo-sweep`) defaults to `cash=200_000` / `commission=0.0004`. The CLI defaults — not `config.yaml` — govern Phase 2 backtests unless explicitly overridden. Harmonising these is a minor follow-up, not a blocker.

## Strategy sweep configs — `config/bghtrend_b1..b4.yaml`

These are **not four parameter sets** — they are four *random-search spaces*. Each file describes, per parameter, a distribution to sample from. `algo-sweep --space_file config/bghtrend_b1.yaml` draws `__n` samples and runs a backtest per sample.

### Control keys (`__`-prefixed)

| Key | Meaning |
|---|---|
| `__mode` | `random` or `grid`. All four bghtrend configs use `random`. In random mode each parameter is sampled from its spec; in grid mode every combination of listed values is enumerated. |
| `__n` | Number of random samples to draw. All four use `5`. With ~17 effective dimensions, 5 samples is a *sparse* probe, not a thorough search — see "Sweep coverage caveat" below. |
| `__seed` | RNG seed for reproducibility. b1=101, b2=202, b3=303, b4=404. Same seed + same space ⇒ bit-identical sample set. The distinct seeds mean the four configs draw independent sample sets even where their ranges overlap. |

`sweep.py` strips all `__`-prefixed keys, then treats the rest as the parameter space. `coerce_params` further filters to fields that exist on `XtrenderPullbackParams`, so stray keys can't blow up the strategy.

### Per-parameter spec grammar

Each parameter maps to one of three spec shapes (parsed by `_sample_from_spec`):

| Spec | Form | Sampling |
|---|---|---|
| **int** | `{type: int, min: A, max: B}` | `randint(A, B)` — uniform integer in `[A, B]` inclusive. |
| **float** | `{type: float, min: A, max: B, step: S}` | uniform in `[A, B]`, then snapped to the nearest multiple of `S` (rounded to 10 dp). `step` optional; omit for continuous. |
| **choice** | `{type: choice, values: [...]}` | `choice(values)` — uniform over the listed values. Works for ints, floats, bools, strings, and `null`. |

Examples from the configs: `ema_fast: {type: int, min: 13, max: 21}`, `pullback_atr_mult: {type: float, min: 0.10, max: 0.15, step: 0.01}`, `ema_mid: {type: choice, values: [55, 89]}`, `require_rebound: {type: choice, values: [true]}`.

A single-value `choice` (e.g. `slope_mode: {type: choice, values: ["pct"]}`) is the idiom for "pin this parameter" — it stays in the space for completeness but never varies.

## Comparison — b1 vs b2 vs b3 vs b4

Read side by side, the four configs are **not ad-hoc**. They form a coherent 2-D design over two axes:

- **Axis 1 — regime timescale** (EMA speed, window lengths, stop/hold tightness): `b3` (fast) → `b1`/`b2` (medium) → `b4` (slow).
- **Axis 2 — selectivity** (filter strictness, at the same timescale): `b1` (strict) vs `b2` (permissive).

| | **b1** | **b2** | **b3** | **b4** |
|---|---|---|---|---|
| **Role** | medium / strict (baseline) | medium / permissive | fast / agile | slow / macro |
| **Implied TF** | ≈1h | ≈1h | ≈15m | ≈4h |
| `ema_fast` | 13–21 | 13–21 | **9–15** | **21–25** |
| `ema_mid` | {55,89} | {55,89} | {45,55,89} | **89–110** |
| `ema_slow` | {200} | {200} | {200} | {200,220} |
| `slope_lookback` | {21,34} | {21,34} | {21} | **{34,55}** |
| `slope_thr_mid` | **5e-5–1e-4** (high) | **2e-5–6e-5** (low) | 3e-5–8e-5 | 3e-5–7e-5 |
| `slope_thr_slow` | 3e-5–6e-5 | 1.5e-5–4e-5 | 2e-5–5e-5 | 2.5e-5–5e-5 |
| `deadzone` | {3,4,5} (high) | {2,3,4} | **{1.5,2,3}** (low) | {3,4,5} (high) |
| `pullback_lookback` | {10,15,20} | {12,15,20} | **{8,12,15}** | **{15,20,24}** |
| `pullback_atr_mult` | 0.10–0.15 (tight) | **0.15–0.25** (loose) | 0.08–0.18 | 0.10–0.20 |
| `entry_max_atr_mult` | 0.50–0.80 | **0.70–1.20** (loose) | 0.40–0.90 | 0.40–0.70 (tight) |
| `require_rebound` | **true** | **false** | {false,true} | **true** |
| `rr_target` | {1.5,2.0} | {1.2,1.5,2.0} | {1.2,1.5,1.8,2.0} | {1.5,2.0} |
| `sl_atr_mult` | 0.40–0.60 | 0.40–0.70 | **0.30–0.55** (tight) | **0.45–0.75** (wide) |
| `trail_atr_mult` | 1.5–2.5 | 1.5–2.8 | **1.2–2.2** (tight) | **1.8–3.0** (wide) |
| `stale_max_bars` | {30,40,60} | {30,40,60} | **{20,30,40}** (short) | **{40,60,80}** (long) |
| `cooldown_bars` | {5,10,20} | {5,10,20} | {5,10} | {10,15,20} |
| `zscore_window` | {80,100,140} | {80,100,140} | {60,80,100} | {100,140,160} |
| `short/long_l*`, `t3_*` | narrow | wider | wider | slightly slower |
| `side`, `slope_mode`, `tp_has_priority`, `trade_on_close`, `tp_pct`, `sl_pct` | both / pct / true / true / null / null — identical across all four | | | |

### Interpretation (hypothesis — author intent)

- **b1 = strict medium baseline.** Strongest trend threshold, tight pullback band, rebound required, high momentum bar. Fewest but highest-conviction entries.
- **b2 = permissive medium.** Same EMA timescale as b1 but every filter relaxed: weaker slope threshold, lower deadzone, wider pullback band, **rebound off**, lower R:R allowed. b1 vs b2 isolates the selectivity axis — same speed, different strictness. This is the principled A/B for "does selectivity pay?".
- **b3 = fast/agile.** Faster EMAs, shortest windows, tightest stops and shortest holds — built for a lower timeframe and faster regime turnover.
- **b4 = slow/macro.** Slowest EMAs (the only one that varies `ema_mid` and `ema_slow` upward continuously), longest windows, widest stops, longest holds — position-style on a higher timeframe.

Conclusion: the differences are **intentional and economically motivated**, not accidental, so they are kept as-is for Phase 2. No harmonisation is performed in Session 1. Two clean-ups are *documented but deferred* (see below).

### Known imperfections (documented, deferred)

1. **`zscore_window` is a phantom dimension.** It is sampled by all four configs but never read, because `slope_mode` is pinned to `pct` everywhere (the `zscore` slope path in the strategy is unreachable). It consumes one of the ~17 dimensions with zero effect on results. Cleanest fix: drop it from the spaces (or unpin `slope_mode`). Deferred — not changing YAML this session.
2. **Implied-TF mapping is not encoded.** The b3→15m / b1,b2→1h / b4→4h mapping above is inferred, not declared in the files. Session 4 (sweep) should confirm each config is run against its intended TF band, or add a comment header to each YAML.
3. **Global-config value drift** (`cash`/`commission` between `config.yaml` and the CLI defaults) — see the Global config section.

### Sweep coverage caveat

`__n: 5` over ~17 effective dimensions is a very sparse sample (5 points in a 17-D space). Running all four configs gives 20 parameter sets total — useful as an *orientation* probe in Session 4, but far from a saturating search. If Session 4 finds promising clusters, expect to raise `__n` substantially (or switch promising regions to grid mode) before drawing stability conclusions in Session 6. This is a property of the sweep design, not a defect to fix now.

## Validation rules

What combinations are illegal or degenerate, and whether anything catches them:

| Rule | Enforced? | Consequence if violated |
|---|---|---|
| **EMA monotonicity** `ema_fast < ema_mid < ema_slow` | **Not enforced in code.** Guaranteed by config construction (in every config, `max(ema_fast) < min(ema_mid) < min(ema_slow)`). | An inverted set would never satisfy `_trend_ok`'s ordering check → **zero trades, silently**. No error raised. |
| **Xtrender spread ordering** `short_l1 < short_l2` (fast < slow EMA in the spread) | Not enforced. Configs respect it (e.g. `short_l1∈{5,7,9}`, `short_l2∈{15,20,25}`). | Inverted would flip the momentum sign — the deadzone test would systematically misfire. No error. |
| **Positive risk distance** `entry − (EMA89 − pad) > 0` | Soft-handled. `_compute_sl_tp` floors risk at `1e-9`. | A degenerate (≤0) risk distance produces a target essentially at entry; the `max(1e-9, …)` floor prevents a div-by-zero / inverted TP but yields a near-useless trade. |
| **`slope_mode` ∈ {pct, zscore}** | Implicit. Strategy branches on the string; an unknown value falls through to the `pct` branch. | Unknown `slope_mode` silently behaves as `pct` rather than erroring. |
| **`side` ∈ {long, short, both}** | Implicit via membership checks (`side in ("long","both")` etc.). | An unknown value disables both entry branches → no trades. |
| **`__n` ≥ 1, `__seed` int** | Coerced in `load_space_from_any` (`int(...)`). | Non-int raises at parse time. |
| **`step` (float spec)** | Snaps to grid; no bounds check that `step ≤ (max−min)`. | An oversized `step` collapses the range to a single snapped value — not an error, just degenerate sampling. |

The recurring theme: **the strategy fails *silently* (zero trades) rather than loudly on malformed parameters.** For Phase 2 that is acceptable because the configs are hand-constructed and respect every rule. A defensive `__post_init__` on `XtrenderPullbackParams` asserting monotonicity would surface mistakes earlier — a candidate code-touching follow-up, out of scope for this docs-only session.

## See also

- [Reference — strategy-bghtrend-pullback](modules/strategy-bghtrend-pullback.md) — what each parameter means economically, core/tuning/ambiguous taxonomy
- [Reference — indicators-xtrender](modules/indicators-xtrender.md) — the `short_l*` / `long_l*` / `t3_*` parameters in context
- [Reference — walkforward](modules/walkforward.md) — out-of-sample harness; the next consumer of these parameter sets
- `algo_bot/engine/sweep.py` — the sweep engine (`expand_param_space`, `_sample_from_spec`, `load_space_from_any`)
- [Concepts — Glossary](../concepts/glossary.md) — grid search, random search, sweep, overfitting
- Source: `config/config.yaml`, `config/bghtrend_b1.yaml` .. `config/bghtrend_b4.yaml`
