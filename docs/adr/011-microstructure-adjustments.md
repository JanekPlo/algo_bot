# ADR-011: Microstructure adjustments — `algo_bot/microstructure.py`

- **Status:** Accepted
- **Date:** 2026-06-19
- **Project phase:** 2 (Research & Backtest MVP)
- **Authors:** Janek Płoński, Claude

## Context

ROADMAP Phase 2 Session 3 requires that every backtest from Session 4 (sweep) onward be *post-microstructure*: the raw `backtesting.py` equity curve charges only the exchange commission, so an "in-sample Sharpe 1.5" is a fairy tale until slippage and perpetual-futures funding are subtracted. The MVP go/no-go (Session 8, ADR-012) needs to see how much of the edge survives realistic transaction costs — if a strategy's Sharpe collapses from 1.5 to 0.8 once funding and slippage are charged, it does not go to testnet.

Two facts in the current repo reshape the naive framing of this task.

**First, a cosmetic microstructure hook already exists and does nothing useful.** `algo_bot/engine/backtester.py` carries `apply_micro_price()` and `adjust_trades_df()` (plus `slippage_bps` / `spread_bps` parameters on `run_backtest` and the CLI). They compute `AdjEntryPrice` / `AdjExitPrice` / `PnL_adj` columns on the trades DataFrame — but nothing downstream reads them. `save_outputs` computes `_metrics_summary` from the raw `trades["PnL"]`, and the equity curve is never touched (the code comment says as much: *"equity pozostaje z silnika"*). So today slippage influences no metric. `algo_bot/engine/sweep.py` goes further: it has `--funding_csv` / `--funding_mode {events,accrual}` flags and passes them into `run_backtest` — which has no such parameters, so `--funding_csv` would raise. `extract_metrics` looks for a `_funding` key that is never set. ADR-005 promised *"funding w `funding.py` + post-processing w `adjust_trades_df`"*; it was sketched and never finished. `algo_bot/funding.py` is a legacy ad-hoc scraper (hard-coded BTCUSDT, 2024 only, writing to `bot_data/aux/`).

**Second — and this is the decisive constraint — the metrics that gate the MVP are computed from the equity curve, not from the trade list.** `metrics.summarize()` derives Sharpe, Sortino, Calmar, MAR, max drawdown, CAGR and total return from `equity: pd.Series`; only `profit_factor` / `win_rate` / `n_trades` come from `trades_pnl`. Therefore a microstructure layer that adjusts only the trade PnL (which is what the existing `adjust_trades_df` does, and what a literal reading of the kickoff's "modify fill price" suggests) cannot change Sharpe or max drawdown — exactly the numbers Session 8 cares about. To make microstructure visible in the metrics that matter, **we need a microstructure-adjusted *equity curve*.**

`backtesting.py` does not expose a market fill price for taker orders — the only knob that feeds equity natively is the relative `commission` parameter. So the realistic options are: (A) run the engine once and overlay costs on the resulting equity curve post-hoc, or (B) fold slippage into the engine `commission` (exact compounding) while funding is necessarily post-hoc, which forces a second engine run (or a reconstruction) to produce the raw-vs-adjusted pair. The session aligned on (A): one engine run, microstructure stays a pure post-processing layer (the ADR-005 intent and the `algo_bot.risk` pattern — pure functions plus an integration hook), no doubling of sweep runtime, and the whole layer is testable against an independent arithmetic oracle.

The funding mathematics were verified against Binance's published mechanics *before* writing tests, per the xtrender lesson (captains-log 2026-06-11): a self-consistent re-read of one's own code is not verification for subtle semantics. Binance's own documentation gives `Funding Amount = Nominal Value of Position × Funding Rate`, with `Nominal Value = Mark Price × Position Size`; longs pay shorts when the rate is positive; **a position pays or receives only if it is open at the exact settlement instant**; settlement is every 8 hours (00:00 / 08:00 / 16:00 UTC) and the rate is capped at ±0.75 % per interval. That "open at the exact instant" rule is precisely the per-settlement-event semantics chosen below, not a per-bar accrual.

Decisions were aligned up-front with Janek (the *"PRZED implementacją"* rule from `feedback_engineering_mindset`). The kickoff enumerated 14 decisions with options and trade-offs; their resolutions are the numbered conventions below.

## Decision

**Implement `algo_bot/microstructure.py` as pure functions over frozen dataclasses (`MicrostructureConfig`, `TradeCost`, `MicrostructureResult`). Microstructure is a post-hoc cost overlay on the raw equity curve from a single `run_backtest` engine call: slippage is a constant bps-per-side cash debit at each entry and exit; funding is a per-settlement (8 h) cash debit at every settlement instant a position is open, using the historical funding rate when available and a synthetic constant otherwise. Both costs are subtracted from the raw equity curve to produce `equity_adjusted`, and from per-trade PnL to produce `trades_pnl_adjusted`; `summarize()` runs on both, yielding `_metrics_summary_raw` and `_metrics_summary_post_microstructure` in `summary.json`. The cosmetic `apply_micro_price` / `adjust_trades_df` path and the dead `funding_csv` / `funding_mode` scaffold are removed. The legacy `funding.py` is rewritten into a parametrised fetcher with an `algo-fetch-funding` CLI; funding history is stored per symbol as `bot_data/processed/binance_<SYMBOL>_funding.csv` and loaded via `data_loader.load_funding`. CLI flags `--microstructure {none,full}`, `--slip_bps`, `--funding_source {historical,synthetic,none}`, `--funding_rate_synthetic` are wired into `algo-backtest`, `algo-sweep` and `algo-walkforward`; walk-forward applies microstructure per fold with funding sliced to the fold range.**

### API surface

```python
# algo_bot/microstructure.py

@dataclass(frozen=True)
class MicrostructureConfig:
    """Konfiguracja korekt mikrostrukturalnych. Frozen — niezmienna po utworzeniu."""
    enabled: bool = True                                  # --microstructure none → False
    slip_bps: float = 1.0                                 # per side; uzasadnienie w ADR §3
    funding_source: Literal["historical", "synthetic", "none"] = "historical"
    funding_rate_synthetic: float = 0.0001                # 0.01%/8h — interest component Binance
    settlement_hours_utc: tuple[int, ...] = (0, 8, 16)    # synthetic grid; historical używa fundingTime


@dataclass(frozen=True)
class TradeCost:
    """Rozbicie kosztów mikrostrukturalnych dla pojedynczego trade'u (audit trail)."""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: Literal["long", "short"]
    notional_entry: float
    slip_cost_quote: float        # slip_bps/1e4 * (notional_entry + notional_exit)
    funding_cost_quote: float     # Σ side_sign * notional(settlement) * rate(settlement)
    n_settlements: int
    pnl_raw: float
    pnl_post: float               # pnl_raw - slip_cost_quote - funding_cost_quote


@dataclass(frozen=True)
class MicrostructureResult:
    """Wynik nałożenia warstwy mikrostruktury na surowy backtest."""
    equity_adjusted: pd.Series            # equity_raw - skumulowane koszty (DatetimeIndex)
    trades_pnl_adjusted: pd.Series        # pnl_post per trade
    per_trade: tuple[TradeCost, ...]
    total_slip_quote: float
    total_funding_quote: float
    config: MicrostructureConfig


# --- Pure helpers ------------------------------------------------------------

def slippage_cost(notional: float, slip_bps: float) -> float:
    """Koszt slippage jednej nogi: notional * slip_bps / 1e4."""

def settlements_in_window(
    entry: pd.Timestamp, exit: pd.Timestamp, funding_index: pd.DatetimeIndex
) -> pd.DatetimeIndex:
    """Timestampy settlementów, w których pozycja [entry, exit) była otwarta."""

def synthetic_funding_series(
    start: pd.Timestamp, end: pd.Timestamp, rate: float, hours_utc: tuple[int, ...]
) -> pd.Series:
    """Syntetyczna seria stałego funding rate na siatce 00/08/16 UTC."""

def funding_cost_for_trade(
    *, side: str, size: float, entry: pd.Timestamp, exit: pd.Timestamp,
    funding: pd.Series, mark: pd.Series,
) -> tuple[float, int]:
    """Koszt funding dla trade'u: Σ side_sign * |size| * mark(s) * rate(s). Zwraca (koszt, n_settlements)."""


# --- Orchestrator ------------------------------------------------------------

def apply_microstructure(
    *,
    equity_raw: pd.Series,
    trades: pd.DataFrame,
    ohlcv: pd.DataFrame,
    funding: pd.Series | None,
    config: MicrostructureConfig,
) -> MicrostructureResult:
    """Nakłada slippage + funding na surowy equity/trades. Czysta funkcja — bez I/O."""
```

```python
# algo_bot/data_loader.py (dodane)
def load_funding(symbol: str, *, exchange: str = "binance",
                 start: str | None = None, end: str | None = None) -> pd.Series:
    """Wczytuje bot_data/processed/binance_<SYMBOL>_funding.csv → Series[funding_rate] (UTC index)."""
```

### Specific conventions

1. **Module scope — new top-level `algo_bot/microstructure.py` (Decision 1b).** Pure functions over frozen dataclasses, mirroring `algo_bot.risk`. The slippage and funding logic is extracted out of `backtester.py`; a future `market_impact_estimator` belongs in the same namespace. Not a subpackage (1c) — over-engineered for the current surface; not an in-file extension of `backtester.py` (1a) — that is precisely the entanglement the existing cosmetic helpers demonstrate.

2. **Slippage application — post-hoc cash debit, not a fill-price change (Decision 2, resolved to overlay).** `backtesting.py` offers no market fill price for taker orders, and adjusting OHLC would shift signals. Each filled leg pays `notional × slip_bps / 1e4` as a cash debit attributed to the leg's bar timestamp. bghtrend enters with market orders and exits via bracket SL/TP (Session 1), so a per-leg debit at entry and exit captures the realistic cost without touching the strategy's TP/SL logic. The literal kickoff option 2a ("modify fill price in `Strategy.next()`") is unreachable in this engine; option 2b (pure trade-PnL adjustment) cannot move equity-derived metrics.

3. **Slippage magnitude — constant bps per side (Decision 3a), default `slip_bps = 1.0`.** Binance USDT-M BTC/ETH perpetuals have a top-of-book spread on the order of one tick (≈ 0.1–0.3 bp), so crossing the book as a taker at the modest notional bghtrend trades costs roughly half a spread plus negligible impact — ~0.5–2 bp per side. We default to **1.0 bp per side** (≈ 2 bp round-trip) on *top of* the exchange commission. The ROADMAP's "5–10 bps" is read as a total transaction-cost envelope (≈ 8 bp round-trip taker fee + ~2 bp slippage). `--slip_bps` is the knob; the raw-vs-post diagnostic (§9) makes sensitivity inspectable, so a conservative stress run at `--slip_bps 5` is one flag away. Size-aware models (linear depth, Kyle's √-impact) are deferred to a future flag and only justified if a sweep shows bghtrend trading notional large enough for impact to matter (typically > $50k).

4. **Slippage direction — symmetric (Decision 4a).** Constant bps both directions and both sides. Asymmetric slippage (wider on the crowded side under extreme funding) requires order-book depth history that `bot_data/` does not contain; it is not modelled.

5. **Funding application — per settlement event (Decision 5b).** For each trade, find the settlement timestamps at which the position is open and charge `side_sign × |size| × mark(settlement) × rate(settlement)` per settlement, where `side_sign = +1` for long (longs pay when the rate is positive) and `−1` for short. This matches Binance's "pay/receive only if open at the settlement instant" rule. Per-bar fractional accrual (5a) was rejected: it charges a position that closed 7 h after the last settlement for funding it never paid, and credits one that opened mid-interval. The mark price at a settlement is approximated by the OHLCV `Close` of the bar containing the settlement instant (documented approximation; the funding endpoint's own mark is not stored).

6. **Settlement timing — driven by the historical `fundingTime`, synthetic falls back to the 00/08/16 UTC grid.** Historical funding CSVs carry the actual `fundingTime` from `/fapi/v1/fundingRate`, which is robust to Binance's occasional off-cycle settlements and per-contract schedule changes; the per-settlement scan uses those timestamps directly. Synthetic mode generates a regular grid at `settlement_hours_utc = (0, 8, 16)`.

7. **Funding source — hybrid historical/synthetic (Decision 6c).** `funding_source="historical"` loads the per-symbol CSV; if it is absent or does not cover the backtest range, the layer emits `logger.warning` and substitutes the synthetic constant for the missing span. This keeps the pipeline (and the Session 4 sweep) runnable before `algo-fetch-funding` has been run, while stress tests (Session 7) get the real Luna/FTX funding tails once the fetch is done. `funding_source="synthetic"` forces the constant; `"none"` disables funding entirely (slippage may still apply).

8. **Funding default rate — `0.0001` (0.01 % / 8 h).** This is Binance's fixed interest-rate component, the documented baseline around which the variable premium oscillates — an economically grounded default, not a round number. Capped conceptually at ±0.75 %/8 h per Binance; the synthetic path never exceeds the constant, and historical values carry their own (already-capped) magnitudes.

9. **Metrics — raw and post side by side (Decision 9a).** `summary.json` gains `_metrics_summary_raw` (engine output, i.e. *including* the exchange commission/taker fee, which has always lived in the engine) and `_metrics_summary_post_microstructure` (raw equity and raw trade PnL minus slippage and funding). The boundary is explicit: the taker **fee is part of "raw"** (it is the engine's `commission`); **slippage and funding are the microstructure overlay**. Walk-forward and sweep aggregate whichever the operator reads; Session 8 reads the spread between the two.

10. **Per-trade breakdown — four columns on `trades.csv` (Decision 10a).** `slip_cost_quote`, `funding_cost_quote`, `pnl_raw`, `pnl_post`. The journal layer (ADR-006 separation) stays the per-trade audit trail; an aggregate `_microstructure_breakdown` (totals + config) also lands in `summary.json`.

11. **Walk-forward integration — per fold (Decision 11).** `run_backtest` applies microstructure on its own equity/trades; walk-forward inherits it through the existing per-fold `run_backtest(data=fold_slice, ...)` call. Funding history is sliced to `[fold.test_start, fold.test_end]` before the fold runs. Microstructure is deterministic, so a fold's adjusted metrics are identical whether the fold runs standalone or inside a walk-forward.

12. **Edge cases — zero is a legitimate value, not NaN (Decision 12).** No trades → all microstructure fields `0.0` (a legitimate outcome, unlike the "undefined metric" NaN of ADR-007). A trade spanning no settlement → `funding_cost_quote = 0.0`, `n_settlements = 0`. Missing historical funding → `logger.warning` + synthetic substitution (§7). `enabled=False` (`--microstructure none`) → `equity_adjusted is equity_raw`, `trades_pnl_adjusted` is the raw PnL, zero costs.

13. **CLI surface — shared across the three entry points (Decision 8).** `--microstructure {none,full}` is the master switch (default `full`); `--slip_bps FLOAT` (default `1.0`), `--funding_source {historical,synthetic,none}` (default `historical`), `--funding_rate_synthetic FLOAT` (default `0.0001`) override the config. The legacy `--slippage_bps` / `--spread_bps` (backtester) and `--funding_csv` / `--funding_mode` (sweep) flags are removed; `--microstructure none` is the backward-compatible "raw" mode (the kickoff's `raw` switch value is dropped as a redundant alias of `none`).

14. **ADR scope — one record for slippage and funding (Decision 14a).** They are the same concept (post-trade cost adjustments), live in one module, share one CLI surface and one `summary.json` schema change. A single ADR records them as one coherent decision rather than splitting ADR-011/012.

15. **Compounding approximation — documented.** `equity_adjusted[t] = equity_raw[t] − cumulative_costs(≤ t)`, a parallel downward shift that grows as costs accumulate. This is an approximation of true compounding (reduced capital would itself earn less going forward); at MVP-scale costs (tens of bps) the difference is second-order. The exact alternative (re-simulate with reduced capital, or fold slippage into engine `commission`) is rejected for the reasons in *Alternatives Considered*. Raw per-fold/-run equity stays on disk for hand-verification.

16. **mypy strict-on-new.** `algo_bot.microstructure` is added to the `[[tool.mypy.overrides]]` strict module list in `pyproject.toml` (`disallow_untyped_defs = true`, …), alongside `risk`, `walkforward`, `metrics`, `data_integrity`.

17. **Logging (ADR-006 convention).** `logger.info` for milestones (microstructure init, funding history loaded with row count and coverage); `logger.debug` for per-trade slip/funding detail; `logger.warning` for missing/short funding history and synthetic substitution. Structured `extra={...}` throughout.

18. **Docstrings.** Polish, Google style; public API names in English (`feedback_engineering_mindset` rule #5). New documentation (this ADR, the deep reference, the concept doc) is in English.

## Consequences

**Positive:**

- **Every metric becomes honest.** Because the overlay rebuilds the equity curve, Sharpe / Sortino / Calmar / MAR / max drawdown all reflect slippage and funding — not just `profit_factor`. Session 8's go/no-go reads a genuine `raw → post` Sharpe spread.
- **Single engine run.** No sweep-runtime penalty; the overlay is cheap pandas arithmetic over the trade list and a funding series.
- **Pure layer, independently testable.** `apply_microstructure` is I/O-free and deterministic; tests feed hand-computed costs and assert exact debits — the independent oracle the xtrender lesson demands.
- **Dead and cosmetic code removed.** The misleading `adjust_trades_df` and the crash-on-use `--funding_csv` scaffold go away; one code path, one source of truth.
- **Funding infrastructure finally real.** `algo-fetch-funding` + `load_funding` + per-symbol CSVs complete the promise ADR-005 deferred, and feed the Session 7 stress tests with real funding tails.
- **Composes with existing modules.** `metrics.summarize` is reused verbatim on the adjusted series; walk-forward inherits the behaviour with no new code at the WF level.

**Negative / costs:**

- **Compounding is approximate (§15).** Documented; second-order at MVP cost scale; raw equity preserved for verification.
- **Mark price at settlement is approximated by bar `Close` (§5).** The funding endpoint's own mark is not stored. For 8 h settlements on liquid pairs the intrabar gap is small; documented in the concept doc.
- **Settlement-window boundary conventions need care.** "Open at the instant" forces an explicit half-open `[entry, exit)` convention and a decision on positions opened/closed exactly at a settlement timestamp — specified in the deep reference and pinned by tests.
- **CLI flag removal is a (minor) breaking change.** `--slippage_bps` / `--spread_bps` / `--funding_csv` / `--funding_mode` disappear. They were either cosmetic or non-functional, so no real run depended on them, but the CHANGELOG must call it out.
- **Funding fetch is a manual prerequisite for realistic runs.** Until `algo-fetch-funding` is run in WSL, historical mode silently degrades to synthetic (with a warning). Acceptable by Decision 6c; the warning is the signal.

**Risks:**

- **Funding sign error is silent and plausible-looking.** Getting `side_sign` backwards turns a cost into a credit. Mitigated by the independent oracle reproducing Binance's worked formula and by an explicit long-pays-when-positive test.
- **Off-cycle / per-contract settlement schedules.** Driving settlements from the historical `fundingTime` (§6) rather than a hard-coded grid mitigates this; synthetic mode is explicitly the regular-grid approximation.
- **Slippage default may be too low for less-liquid regimes or larger size.** The default is justified for liquid BTC/ETH at bghtrend size; `--slip_bps` and the raw-vs-post diagnostic exist precisely to bound this. Revisit if Session 4 shows large notional.
- **`backtesting.py` trade-column names.** The overlay reads `EntryTime` / `ExitTime` / `EntryPrice` / `ExitPrice` / `Size`. A library bump renaming these breaks the overlay loudly (a test asserts the contract), not silently.

## Alternatives Considered

- **Slippage folded into engine `commission` (option B).** Mathematically exact compounding, equity adjusted natively. Rejected as the primary mechanism because funding cannot go through the engine at all (it is path-dependent on 8 h settlements during the hold), so this still needs a post-hoc funding overlay; and producing the raw-vs-adjusted *pair* then needs either a second engine run (≈ 2× sweep time) or a reconstruction that re-introduces the post-hoc machinery anyway. The single-run overlay (§2, §15) is simpler and uniform across both cost types, at the price of the documented compounding approximation.
- **Pure trade-PnL adjustment (the existing `adjust_trades_df`, kickoff option 2b).** Rejected: cannot move equity-derived metrics (Sharpe, max drawdown), which is the entire point. This is why the current hook is cosmetic.
- **Per-bar fractional funding accrual (5a).** Smoother and slightly simpler, but charges/credits positions that are not open at the settlement instant — contradicting Binance's documented rule. Rejected for fidelity.
- **Historical funding as a hard dependency (6a).** Cleaner semantics (no silent synthetic), but blocks every backtest until the fetch is run — friction during Session 4 iteration. Rejected in favour of the hybrid with a loud warning (6c).
- **Single combined `_metrics_summary` with adjusted PnL only (9b), or a separate `_microstructure_breakdown` over raw metrics (9c).** Both lose the at-a-glance raw→post comparison Session 8 needs. Rejected in favour of two full summaries (9a) plus the breakdown.
- **Subpackage `algo_bot/microstructure/{slippage,funding}.py` (1c).** Over-engineered for two cost types; revisit only if market-impact and borrow/locate models accrete.
- **Keeping the cosmetic hook and building alongside it.** Rejected: two inconsistent slippage paths is a future trap; the session chose to rip out and replace.

## References

- File (created in this session): `algo_bot/microstructure.py`
- File (created in this session): `tests/test_microstructure.py`
- File (created in this session): `docs/reference/modules/microstructure.md`
- File (created in this session): `docs/concepts/microstructure.md`
- File (rewritten in this session): `algo_bot/funding.py` — parametrised fetcher + `algo-fetch-funding` CLI
- File (modified in this session): `algo_bot/engine/backtester.py` — remove cosmetic hook, wire overlay + raw/post summaries
- File (modified in this session): `algo_bot/engine/sweep.py` — remove dead funding scaffold, new flags
- File (modified in this session): `algo_bot/engine/walkforward.py` — per-fold funding slice + flags pass-through
- File (modified in this session): `algo_bot/data_loader.py` — `load_funding`
- File (modified in this session): `pyproject.toml` — strict mypy override + `algo-fetch-funding` entry-point
- File (modified in this session): `docs/CHANGELOG.md`, `docs/ROADMAP.md`
- Related ADRs: ADR-005 (engine; this supersedes its deferred microstructure note), ADR-006 (logging), ADR-007 (metrics consumed for raw/post), ADR-008 (pure-functions + hook pattern), ADR-009 (walk-forward consumes `run_backtest`)
- External (verified before tests):
  - Binance, *Introduction to Binance Futures Funding Rates* — `Funding Amount = Nominal Value × Funding Rate`, longs pay shorts when positive, pay/receive only if open at settlement instant, 8 h cadence. <https://www.binance.com/en/support/faq/detail/360033525031>
  - Binance, *Perpetual Futures Trading Rules* — settlement times, ±0.75 %/8 h cap, interest component ~0.01 %/8 h. <https://www.binance.com/en/futures/trading-rules/perpetual>

## Notes

- **One number to confirm before implementation: `slip_bps` default.** Set to `1.0` bp/side here on the liquidity argument in §3. If you prefer the kickoff's more conservative `5.0`, it is a one-line default change — say so and I will set it. The MVP go/no-go is intended to be run at both ends of the plausible range anyway.
- **`funding.py` rewrite scope.** Parametrised `fetch_funding(symbol, start, end)` + `main()` for `algo-fetch-funding`; output `bot_data/processed/binance_<SYMBOL>_funding.csv` with columns `datetime,funding_rate` (UTC). The actual network fetch runs in WSL (sandbox has no network/conda here); tests use synthetic funding, so `make check` is green without the fetch.
- **Authors field.** Following ADR-009's precedent ("Janek Płoński, Claude"); the parked standardisation question (captains-log 2026-06-11) is left for a deliberate template change, not decided ad hoc here.
- **Status `Accepted`** — all 14 decisions signed off in-session (incl. `slip_bps=1.0` default). Implementation landed in the same session; `make check` is run by the operator in WSL (sandbox has no conda/network) as the final verification gate.
