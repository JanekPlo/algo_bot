# Reference — `algo_bot.microstructure`

Deep reference for the microstructure cost layer (slippage + perpetual-futures funding). For the rationale and rejected alternatives, see [ADR-011](../../adr/011-microstructure-adjustments.md). For a one-page concept orientation (why these costs matter, why ~bps numbers), see [concepts/microstructure](../../concepts/microstructure.md).

## Scope

`algo_bot.microstructure` is a **pure, I/O-free post-processing layer** applied to the raw output of a single `run_backtest` engine call. It models two costs the `backtesting.py` engine does not:

- **Slippage** — the gap between the idealised fill price the backtest assumes (bar Close/Open) and the realistic taker fill. Constant `slip_bps` per side, charged as a cash debit at entry and exit. Sits *on top of* the exchange commission (taker fee), which stays in the engine.
- **Funding** — the 8-hour perpetual-futures carry cost. `Funding Amount = Notional × Funding Rate`; longs pay shorts when the rate is positive; charged per settlement, only for positions open at the settlement instant.

Both are subtracted from the raw equity curve to produce `equity_adjusted`, and from per-trade PnL to produce `trades_pnl_adjusted`. The exchange **fee is part of "raw"** (it is the engine's `commission`); **slippage and funding are the "post" overlay**.

The module does **not** read files or call the network. Loading historical funding (`data_loader.load_funding`) and fetching it (`algo-fetch-funding`, `algo_bot.funding`) live elsewhere; the orchestrator receives an already-resolved funding `Series` or `None`.

## Public API

```python
from algo_bot.microstructure import (
    MicrostructureConfig,
    TradeCost,
    MicrostructureResult,
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
| `--microstructure {none,full}` | `full` | Master switch. `none` = backward-compatible raw mode. |
| `--slip_bps FLOAT` | `1.0` | Slippage per side (bps), on top of `--commission`. |
| `--funding_source {historical,synthetic,none}` | `historical` | Funding source. |
| `--funding_rate_synthetic FLOAT` | `0.0001` | Synthetic / fallback rate per 8 h. |

Funding history is fetched separately: `algo-fetch-funding --symbol BTC/USDT --start 2019-09-08` → `bot_data/processed/binance_BTCUSDT_funding.csv` (`datetime`,`funding_rate`).

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
