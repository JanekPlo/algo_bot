# Reference — `algo_bot.microstructure`

Deep reference for the execution-cost layer (slippage + perpetual-futures funding) and
the independent mark-price margin-safety layer. For the original cost-overlay rationale
and rejected alternatives, see [ADR-011](../../adr/011-microstructure-adjustments.md).

## Scope

The module now contains two orthogonal contracts:

1. the legacy, pure post-processing overlay applied to a `backtesting.py` result; and
2. a causal Bybit mark-price context used to detect isolated-margin liquidation.

The first contract models two costs the legacy engine does not:

- **Slippage** — the gap between the idealised fill price the backtest assumes (bar Close/Open) and the realistic taker fill. Constant `slip_bps` per side, charged as a cash debit at entry and exit. Sits *on top of* the exchange commission (taker fee), which stays in the engine.
- **Funding** — the 8-hour perpetual-futures carry cost. `Funding Amount = Notional × Funding Rate`; longs pay shorts when the rate is positive; charged per settlement, only for positions open at the settlement instant.

Both are subtracted from the raw equity curve to produce `equity_adjusted`, and from per-trade PnL to produce `trades_pnl_adjusted`. The exchange **fee is part of "raw"** (it is the engine's `commission`); **slippage and funding are the "post" overlay**.

The legacy orchestrator does **not** read files or call the network. Its funding input is
already resolved. `load_mark_price_context` is the one explicit file-I/O boundary in this
module; fetching and processing mark-price candles remain in `fetch_data` and
`process_data`. No function in this module calls the network.

## Public API

```python
from algo_bot.microstructure import (
    MicrostructureConfig,
    TradeCost,
    MicrostructureResult,
    MaintenanceMarginTier,
    MarkPriceBar,
    MarkPriceContext,
    LeveragedPosition,
    LiquidationEvent,
    load_mark_price_context,
    mark_price_at,
    maintenance_margin_tiers_from_bybit,
    liquidation_price,
    liquidation_check,
    first_liquidation_event,
    slippage_cost,
    settlements_in_window,
    synthetic_funding_series,
    resolve_funding,
    funding_flows_for_trade,
    funding_cost_for_trade,
    apply_microstructure,
)
```

### `MicrostructureConfig`

Frozen dataclass holding the configuration.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | `bool` | `True` | Master switch. `False` (`--microstructure none`) → equity/trades returned unchanged, zero cost. |
| `slip_bps` | `float` | `1.0` | Slippage per side in basis points, on top of the engine fee. Round-trip ≈ `2 × slip_bps`. |
| `funding_source` | `Literal["historical","synthetic","none"]` | `"historical"` | `historical` = CSV with synthetic fallback; `synthetic` = constant; `none` = no funding (slippage may still apply). |
| `funding_rate_synthetic` | `float` | `0.0001` | Constant rate per 8 h for synthetic / fallback (0.01 % = Binance interest component). |
| `settlement_hours_utc` | `tuple[int, ...]` | `(0, 8, 16)` | Settlement hours for the *synthetic* grid. Historical mode uses the real `fundingTime` from the CSV, not this grid. |

### `TradeCost`

Frozen dataclass — per-trade cost breakdown (audit trail). One per trade, in trade order.

| Field | Type | Meaning |
|---|---|---|
| `entry_time` / `exit_time` | `pd.Timestamp` | Trade boundaries (tz-naive UTC after normalisation). |
| `side` | `Literal["long","short"]` | From `Size` sign. |
| `notional_entry` / `notional_exit` | `float` | `|size| × price` at entry / exit. |
| `slip_cost_quote` | `float` | `slip_bps/1e4 × (notional_entry + notional_exit)`. Always ≥ 0. |
| `funding_cost_quote` | `float` | Σ over settlements; **positive = paid (debit), negative = received (credit)**. |
| `n_settlements` | `int` | Funding settlements inside `(entry, exit]`. |
| `pnl_raw` | `float` | Engine PnL (fee included, slip/funding excluded). |
| `pnl_post` | `float` | `pnl_raw − slip_cost_quote − funding_cost_quote`. |

### `MicrostructureResult`

Frozen dataclass — the orchestrator output.

| Field | Type | Meaning |
|---|---|---|
| `equity_adjusted` | `pd.Series` | Raw equity minus cumulative costs (tz-naive UTC index). `enabled=False` → identical to raw. |
| `trades_pnl_adjusted` | `pd.Series` | `pnl_post` per trade (RangeIndex; order irrelevant for profit_factor/win_rate). |
| `per_trade` | `tuple[TradeCost, ...]` | Per-trade breakdown. |
| `total_slip_quote` / `total_funding_quote` | `float` | Sums (funding netted with sign). |
| `config` | `MicrostructureConfig` | The config used. |

### Pure functions

| Function | Signature | Notes |
|---|---|---|
| `slippage_cost` | `(notional, slip_bps) -> float` | `|notional| × slip_bps / 1e4`. |
| `settlements_in_window` | `(entry_time, exit_time, funding_index) -> pd.DatetimeIndex` | Half-open convention `(entry, exit]` — see below. |
| `synthetic_funding_series` | `(start, end, rate, hours_utc) -> pd.Series` | Constant rate on the `hours_utc` grid. |
| `resolve_funding` | `(historical, start, end, config) -> pd.Series` | Hybrid historical/synthetic policy (warns on gaps). |
| `funding_flows_for_trade` | `(*, side, size, entry_time, exit_time, funding, mark) -> list[(ts, cost)]` | Per-settlement signed flows. |
| `funding_cost_for_trade` | `(...) -> (total, n_settlements)` | Sum of flows. |
| `apply_microstructure` | `(*, equity_raw, trades, ohlcv, funding, config) -> MicrostructureResult` | Orchestrator. Pure. |

### Mark-price and isolated-margin API

| Type / function | Contract |
|---|---|
| `MaintenanceMarginTier` | Frozen Bybit risk-limit tier: maximum position value, maintenance-margin rate and deduction. The complete schedule must be frozen with the experiment manifest. |
| `MarkPriceBar` | One completed OHLC mark-price bar with explicit open/close timestamps. |
| `MarkPriceContext` | Strictly validated, causal OHLC history plus venue, source, taker fee and risk tiers. `completed_bar_at(ts)` never returns the currently forming bar; `tier_for(value)` fails closed when no tier covers the position. |
| `LeveragedPosition` | Engine-independent isolated linear-USDT position: side, quantity, entry, leverage and optional extra margin. |
| `LiquidationEvent` | Serializable evidence containing the position, side, observed mark, liquidation threshold, risk-tier inputs, timestamp and source. |
| `load_mark_price_context` | Loads `bot_data/processed/<exchange>_<symbol>_mark_<tf>.csv`, keeps OHLC only and performs strict integrity validation. |
| `mark_price_at` | Convenience lookup returning the close of the last completed H1 mark-price bar. |
| `maintenance_margin_tiers_from_bybit` | Converts public Bybit risk-limit rows to sorted frozen tiers. |
| `liquidation_price` | Computes the current Bybit UTA isolated linear-USDT liquidation threshold, including maintenance-margin deduction, extra margin and estimated closing fee. |
| `liquidation_check` | Compares one mark observation with the threshold and returns `False` or a `LiquidationEvent`. |
| `first_liquidation_event` | Scans completed H1 mark bars over `(start, end]`; uses `Low` for a long and `High` for a short, and returns only the first crossing. |

Fill evidence and margin evidence are deliberately independent. `BacktestResult` schema v2
records `fill_method ∈ {close_naive, close_plus_slippage, nautilus_native_bar}` separately
from `margin_method ∈ {none, mark_price_isolated}`. Research evidence requires
`nautilus_native_bar` and `mark_price_isolated`; old P9 artifacts load as
`close_naive`/`none` in memory and are never rewritten.

| `BacktestResult` field | Meaning |
|---|---|
| `fill_method` | How prices in the fill ledger were produced. This is not inferred from the cost-model label. |
| `margin_method` | Whether the run applied causal isolated-margin safety (`mark_price_isolated`) or no margin model (`none`). |
| `mark_price_source` | Non-empty path/source identifier required by `mark_price_isolated`; forbidden when margin is `none`. |
| `liquidation_events` | Immutable tuple of serialized `LiquidationEvent`s. Empty means no observed crossing, not that a mark-price model was present. |

### Mark-price integrity and causality

Bybit mark-price timestamps denote bar opens. A bar is available only after
`open_time + timeframe`; this prevents use of an H1 close before that hour ends. Mark-price
validation is stricter than trade-OHLCV validation: positive OHLC, valid OHLC relations,
monotonic unique UTC timestamps, exact grid alignment, no future/incomplete bar and **zero
gaps**. Missing mark bars are never forward-filled because the missing High/Low may contain
a liquidation crossing.

The local Iteration-2 evidence set was fetched directly from Bybit and clipped to the exact
trade-OHLCV overlap:

| File | Rows | First open (UTC) | Last open (UTC) |
|---|---:|---|---|
| `bybit_BTCUSDT_mark_1h.csv` | 55,256 | 2020-03-25 10:00 | 2026-07-14 17:00 |
| `bybit_ETHUSDT_mark_1h.csv` | 46,746 | 2021-03-15 00:00 | 2026-07-14 17:00 |

Both reports have zero gaps and the same row count/start/end as their corresponding local
Bybit H1 trade series. These data files are intentionally gitignored; the fetch commands and
strict validator are the reproducibility boundary.

### Liquidation run semantics

A crossing is a **hard stop for that run**: the caller records the first event, terminates
further strategy execution and reports the economic outcome. It must not replace equity
with an arbitrary zero. A liquidation is a negative result, but it does not invalidate the
quality of otherwise native fill and mark-price evidence. Conversely, an incomplete mark
series or missing risk tier fails before the run and cannot be labelled eligible.

## Funding mechanics (verified against Binance docs)

- **Formula:** `Funding Amount = Notional × Funding Rate`, `Notional = Mark Price × Position Size`. The mark at a settlement is approximated by the OHLCV `Close` as-of the settlement instant (`Series.asof`) — the funding endpoint's own mark is not stored. Documented approximation; the intrabar error on an 8 h grid is small for liquid pairs.
- **Direction:** long pays short when the rate is positive. In code, `cost = side_sign × |size| × mark × rate`, `side_sign = +1` for long, `−1` for short. Positive `cost` = debit.
- **Settlement membership — `(entry, exit]` (half-open).** A position is charged at settlement `s` iff `entry < s <= exit`. Entry is exclusive (a market open fills just *after* the snapshot), exit is inclusive (you hold *into* the closing snapshot). This matters because on 1 h / 4 h timeframes bar timestamps coincide with 00/08/16 UTC: a trade opening exactly at 08:00 does **not** pay that settlement; one closing exactly at 16:00 **does**.
- **Cadence:** every 8 h (00/08/16 UTC); rate capped ±0.75 %/8 h by Binance. Historical mode follows the real `fundingTime` (robust to off-cycle settlements); synthetic uses the regular grid.

## Slippage model

Constant `slip_bps` per side, symmetric across direction and side. Charged as a cash debit `|notional| × slip_bps/1e4` at the entry bar and again at the exit bar. Size-aware impact (linear depth, √-impact) is out of scope (no order-book depth in `bot_data/`); revisit only if a sweep shows bghtrend trading notional large enough for impact (> ~$50k). Default `1.0` bp/side reflects the sub-bp top-of-book spread of liquid BTC/ETH USDT-M perpetuals at modest size.

## Pipeline integration

`run_backtest(..., microstructure=MicrostructureConfig | None = None)` (default `None` → off, byte-identical to pre-ADR-011 behaviour). When enabled it:

1. resolves funding (auto-loads `data_loader.load_funding(symbol)` for `historical`, `FileNotFoundError` → synthetic with a warning), sliced to `[df.index[0], df.index[-1]]`;
2. calls `apply_microstructure` on the raw equity/trades/OHLCV;
3. adds an **`Equity_adjusted`** column to the returned equity DataFrame;
4. adds **`pnl_raw`, `pnl_post`, `slip_cost_quote`, `funding_cost_quote`** columns to the returned trades DataFrame;
5. writes `stats["_microstructure"]` (totals + config), and `save_outputs` writes both `_metrics_summary_raw` and `_metrics_summary_post_microstructure` to `summary.json` (plus `_metrics_summary` as a backward-compat alias of raw).

**Equity curve, not just trades.** Because the costs are overlaid on the equity *curve*, the post-microstructure Sharpe / max drawdown / Calmar all reflect slippage and funding — the entire reason the layer exists. A trade-PnL-only adjustment (the cosmetic helper this ADR removed) could not move equity-derived metrics.

**Walk-forward** (`WalkForwardConfig.microstructure`) threads the config per fold; `run_fold` computes its `MetricsSummary` from `Equity_adjusted`/`pnl_post` when present (so MVP fold metrics are post-microstructure), and `stitch_equity` stitches the adjusted curve. Funding is sliced to each fold's range inside `run_backtest` — fold results are identical standalone or inside a walk-forward.

**Sweep** passes the same config to every run and surfaces `sharpe_raw` / `sharpe_post` plus the `ms:*` breakdown in `index.csv`.

## CLI

Shared across `algo-backtest`, `algo-sweep`, `algo-walkforward`:

| Flag | Default | Meaning |
|---|---|---|
| `--exchange {binance,bybit}` | `binance` | Venue: data prefix + cost defaults ([ADR-015](../../adr/015-exchange-migration-bybit.md)). |
| `--microstructure {none,full}` | `full` | Master switch. `none` = backward-compatible raw mode. |
| `--commission FLOAT` | per `--exchange` | Taker fee (fraction). `binance` 0.0004, `bybit` 0.00055. |
| `--slip_bps FLOAT` | per `--exchange` (1.0) | Slippage per side (bps), on top of `--commission`. |
| `--funding_source {historical,synthetic,none}` | `historical` | Funding source. |
| `--funding_rate_synthetic FLOAT` | per `--exchange` (0.0001) | Synthetic / fallback rate per 8 h. |

**Exchange-scoped defaults (ADR-015).** Commission / slippage / synthetic funding are
per-exchange (`EXCHANGE_DEFAULTS` in `backtester.py`), selected by `--exchange`; an explicit
flag still overrides. The taker-fee, slippage-magnitude and funding-default *justifications*
in §Funding mechanics and ADR-011 are **Binance-specific** — Bybit uses its own taker
(0.055 %) and the same slippage/funding-synthetic priors until re-measured.

Funding history is fetched per exchange, e.g. `algo-fetch-funding --exchange bybit --symbol BTC/USDT --start 2020-03-25` → `bot_data/processed/bybit_BTCUSDT_funding.csv` (`datetime`,`funding_rate`); Binance uses `--start 2019-09-08` → `binance_BTCUSDT_funding.csv`.

## Edge cases

- **No trades / disabled** → `equity_adjusted == equity_raw`, `per_trade = ()`, zero totals. Zero is a legitimate value, never NaN (contrast ADR-007 metrics).
- **Trade spanning no settlement** → `funding_cost_quote = 0.0`, `n_settlements = 0`.
- **Missing / partial historical funding** → `logger.warning` + synthetic fills the uncovered span (Decision 6c).
- **Timezones** → all timestamps normalised internally to tz-naive UTC, so equity / trades / funding comparisons are consistent regardless of input tz.

## Verification

`tests/test_microstructure.py` uses an **independent arithmetic oracle** (hand-computed literals, no pandas re-application) per the xtrender lesson (captains-log 2026-06-11): funding values are derived directly from `Notional × Rate` worked examples, slippage from `notional × bps/1e4`, and the equity overlay timeline is asserted bar-by-bar. A tz-aware case pins the normalisation.

## See also

- [ADR-011](../../adr/011-microstructure-adjustments.md) — decision, alternatives, defaults justification.
- [concepts/microstructure](../../concepts/microstructure.md) — perp-futures cost mechanics, reading raw vs post.
- [reference/modules/metrics](metrics.md) — `summarize()` consumed on both equity curves.
- [reference/modules/walkforward](walkforward.md) — per-fold microstructure semantics.
