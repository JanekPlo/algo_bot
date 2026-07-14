# ADR-015: Exchange migration Binance → Bybit for forward-only work

- **Status:** Accepted
- **Date:** 2026-07-14
- **Project phase:** 2 (Research & Backtest MVP) — MR-Session 3 Beta Iteration 1
- **Authors:** Janek Płoński, Claude

## Context

Every prior decision assumed **Binance USDT-M perpetuals** as the venue: the historical
CSVs (`bot_data/processed/binance_*.csv`, 2019-09 → today), the microstructure fee/funding
defaults ([ADR-011](011-microstructure-adjustments.md)), and the Close-All position model
([ADR-014](014-engine-migration-nautilus.md) §9). Two facts break that assumption for
*forward* work. **(1)** Janek's Binance account is inactive in the EU (regulatory), so
Binance is no longer a path to Phase-3/4 live trading. **(2)** Janek holds an active Bybit
account, and the Mastermind MMS prior — the strategy edge this line is chasing — carries an
author-claimed prop track record **on Bybit**. Continuing to research on a venue we cannot
trade live, whose fee/funding microstructure differs from where the edge was observed, is a
backtest/live-parity liability.

One capability was a hard prerequisite and was verified in-session: `nautilus_trader`
**1.230.0 ships a native Bybit adapter** (`nautilus_trader.adapters.bybit` imports clean,
alongside `binance`/`okx`/`bitmex`/`dydx`/`hyperliquid`). The Bybit v5 API was also checked
against the official docs: **system-default position mode is One-Way** for linear USDT perp
(`positionIdx=0`), `reduce_only` is supported (no Binance-style Hedge-Mode restriction),
standard non-VIP taker fee is **0.055%**, and Bybit **linear** USDT perp only launched
**2020-03-25** — so a 2019-09 Binance-parity window is impossible on this instrument.

Per the project rule (*"decyzje architektoniczne PRZED implementacją"*), the seven decisions
below were aligned with options and trade-offs before any code.

## Decision

**Adopt Bybit (v5, linear USDT perpetuals) as the venue for all forward-only work — fresh
historical data, live adapter, and cost model. Binance historical CSVs are kept unchanged as
a pinned reference baseline; no Binance code is removed.** The seven conventions:

1. **Nautilus adapter — native, verified only.** The native Bybit adapter exists in 1.230.0;
   we record that for the future backtest lane. This iteration's live/exchange path is CCXT
   (analog to the Binance path), so no custom Nautilus code is written now.
2. **Data scope — BTC + ETH × {5m, 10m, 15m, 1h, 4h} = 10 OHLCV files + 2 funding.** M5 and
   M10 pulled now (Iteration-2 multi-TF preview). Bybit has no native 10m kline; **M10 is
   aggregated offline from M5** (`process_data --resample-to 10m`).
3. **Historical window — from Bybit linear inception (~2020-03-25) to today** (~6.3 y).
   A 2019-09 Binance-parity window is not available on linear USDT perp; the difference is
   documented so cross-exchange comparisons stay honest.
4. **Live adapter depth — testnet-first (`algo_bot/live/live_bybit.py`).** Base structure from
   `live/live_binance.py` + credentials/endpoints from `scripts/bybit_testnet.py`; testnet by
   default (zero-risk), mainnet extension deferred to Phase 3. Includes `close_all_positions()`.
5. **Fee model — per-exchange defaults driven by `--exchange`.** `EXCHANGE_DEFAULTS` in
   `backtester.py` (`binance`: commission 0.0004; `bybit`: 0.00055) with a `--exchange
   {binance,bybit}` flag on `algo-backtest`/`algo-sweep`/`algo-walkforward` that also selects
   which `<exchange>_*.csv` is loaded. Default `binance` preserves current behaviour
   (backward-compatible); an explicit `--commission`/`--slip_bps` still overrides.
6. **Position model — One-Way (NETTING).** Matches Bybit's system default and
   [ADR-014](014-engine-migration-nautilus.md) §9 (NETTING + virtual legs). `reduce_only`
   works on Bybit, so the Binance Hedge-Mode caveat does not apply. ADR-014 §9 gets a
   Bybit-specific note.
7. **Close-All parity — sequential cancel-then-close.** `close_all_positions()`: cancel all
   open orders per symbol → market `reduce_only` close of the net position; each step logged;
   transient errors retried best-effort (safety path fails toward flat, does not raise).

## Consequences

**Positive:** a real path to Phase-3/4 live trading on an account Janek can actually use;
consistency with the venue where the MMS prior's track record was observed; a clean
per-exchange cost abstraction reusable for any future venue; native Nautilus Bybit support
available for the backtest lane.

**Negative / costs:** a fresh multi-year historical fetch (operator-side on the VPS, not
free); a second live adapter + CCXT wrapper to maintain; fee/funding now per-exchange config
(more surface); position model must be re-verified per venue (done here for Bybit v5); the
Bybit window is ~6 months shorter than Binance (no 2019-09→2020-03 data on linear perp).

## Alternatives Considered

- **OKX / Bitget.** Comparable derivatives venues, but Janek has no account there — extra
  onboarding effort and no MMS track-record link. Rejected.
- **Binance testnet-only.** Keeps the existing pipeline, but a testnet does not reflect the
  live edge (fees, funding, liquidity, and — decisively — it is not a live path for an
  EU-restricted account). Rejected.
- **Bybit inverse BTCUSD (history from 2019-11).** Longer history, but a different instrument
  (coin-margined) that mismatches the USDT-settled research line. Rejected.
- **Global `DEFAULT_COMMISSION` via config.yaml.** Rejected in favour of `--exchange`-driven
  `EXCHANGE_DEFAULTS`: the CLI already states the venue, so defaults should follow it
  automatically rather than living in a separate config the code must read.

## References

- Code: `algo_bot/engine/exchanges/bybit_adapter.py`, `algo_bot/live/live_bybit.py`,
  `algo_bot/fetch_data.py`, `algo_bot/funding.py`, `algo_bot/process_data.py`,
  `algo_bot/engine/backtester.py` (`EXCHANGE_DEFAULTS`), `sweep.py`, `walkforward.py`.
- Tests: `tests/test_bybit_adapter.py` (pure unit + opt-in testnet smoke, no mocks).
- Related ADRs: [ADR-014](014-engine-migration-nautilus.md) §9 (position model — Bybit note
  added), [ADR-011](011-microstructure-adjustments.md) (per-exchange cost defaults),
  [ADR-005](005-backtesting-py-mvp-engine.md) (historical "Binance USDT-M" scope note).
- Bybit v5 API: position mode <https://bybit-exchange.github.io/docs/v5/position/position-mode>,
  fee rate <https://bybit-exchange.github.io/docs/v5/account/fee-rate>. Linear USDT perp
  launched 2020-03-25.
- Deferred to Iteration 2: mark-price / native fills, M5/M10 marking fidelity (MMS multi-TF).
