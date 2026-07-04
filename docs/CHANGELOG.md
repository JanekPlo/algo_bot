# Changelog

Wszystkie istotne zmiany w algo_bot będą tutaj rejestrowane.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Wersjonowanie: [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — `MAJOR.MINOR.PATCH`.

Sekcje na każdą wersję:
- **Added** — nowe funkcjonalności
- **Changed** — zmiany w istniejących funkcjonalnościach (kompatybilne wstecz)
- **Deprecated** — funkcjonalności do usunięcia w przyszłości
- **Removed** — usunięte funkcjonalności
- **Fixed** — naprawione bugi
- **Security** — łatki bezpieczeństwa

---

## [Unreleased]

### Added (Phase 2 Session 4b — VPS research runner, 2026-07-04)
- **`scripts/vps-sync.sh`** — rsync wrapper with `up` (`bot_data/processed/` PC→VPS) and `down` (`results/` VPS→PC) subcommands. Config via `VPS_HOST` / `VPS_REPO` / `RSYNC_OPTS`; `--dry-run` supported. No `--delete` (a wrong `VPS_HOST` can never wipe local `results/`); no secrets touched (Decision 5 — only market data and results move).
- **Makefile targets `sync-up` / `sync-down`** — thin wrappers over `scripts/vps-sync.sh`, require `VPS_HOST=...`.
- **`docs/guides/vps-research-runner.md`** (English) — thin runbook: miniforge install, read-only deploy-key clone, `conda env create` + `make check` smoke, dataset sync, running sweeps in tmux, results pull-back, end-to-end smoke test, and anti-patterns (no parallel writes to `index.csv`, no `algo-fetch` on the VPS, no secrets, no `rsync --delete`).
- **Decisions (5):** (1) env = miniforge + `environment.yml` — TA-Lib atomically from conda-forge, matches WSL; (2) data source = rsync `processed/` from PC — reproducibility over freshness (a VPS-side `algo-fetch` would drift the dataset and break PC↔VPS comparability); (3) parallelism = sequential in tmux — `results/experiments/index.csv` append is not multi-process safe, `--index_csv`-per-run deferred; (4) results transport = rsync `results/` back to PC where notebook 03 / brain-Claude live; (5) security = fresh read-only deploy key generated on the VPS, no `.env`/API keys on the box.
- **No `algo_bot/` code touched** — sequential-in-tmux (Decision 3) needs zero code change; the parallelism flag is an explicit follow-up, not part of 4b.
- **Operator-run items** (sandbox cannot reach the VPS or the network): VPS provisioning, `make check` on the VPS, `sync-up`, and the smoke sweep are executed from the WSL/VPS terminals per the runbook and confirmed back. Reference host: OVH VPS-2, 6 vCores / 12 GB RAM / 100 GB, Ubuntu 22.04 LTS, `os-waw2`.

### Added (Phase 2 Session 4 — in-sample sweep, 2026-07-04)
- **`StrategyBase.precompute(df)` hook** — optional one-shot vectorised indicator precomputation called by the backtest wrapper with the full dataset before the bar loop. Causality contract in the docstring: only indicators where value at `t` depends on data `<= t` may be cached. Implemented in `bghtrend_pullback` (3×EMA + ATR + xtrender cached, `on_bar` reads `.iloc[:m]` prefixes); live path unchanged (fallback recomputes per prefix).
- **`tests/test_bghtrend_precompute_equivalence.py`** — no-mock equivalence proof: (a) prefix-invariance of EMA/ATR/x_long (`rtol=1e-12`), (b) bar-by-bar identical `Signal` sequence live-path vs precompute-path on 700 synthetic bars, with a non-vacuous guard (fixture must produce real entries+exits).
- **Sweep `index.csv` selection columns** — `extract_metrics` now exports the full ADR-007 selection set from both summaries (`sharpe/calmar/profit_factor/max_drawdown_pct/n_trades` × `_raw`/`_post`, keys always emitted → stable header for `DictWriter`) plus a `space_file` column (b1 vs b2 were indistinguishable on the same symbol/TF). WF-candidate filters compute directly from `index.csv`.
- **Docs (English):** `docs/guides/running-sweep.md` (CLI, index schema, interpretation heuristics A-E, worth-WF thresholds), `docs/guides/running-backtest.md` (CLI, outputs, troubleshooting), `docs/reference/metrics-reference.md` (DRAFT — ADR-007 metrics with crypto context; WF section lands in Session 5).
- **`notebooks/03_bghtrend_sweep_and_walkforward.ipynb`** — section 1 "Sweep review": PRIOR (recorded before results), heuristics A-E, WF-candidate filters, dumps `results/experiments/sweep_review.json`. Sections 2-6 reserved for Sessions 5-8.
- **ROADMAP: Session 4b (ad-hoc) — VPS research runner** — sweeps/backtests on a VPS in tmux (env via deploy key + conda/TA-Lib, rsync data/results, `--index_csv`-per-run or file lock before parallelism).

### Changed (Phase 2 Session 4)
- **`config/bghtrend_b1..b4.yaml`: `__n: 5 → 30`** — 5 random samples per config was statistically vacuous for top-N interpretation; 30 gives the clustering heuristics signal. Note: `__n` in the space file overrides `--n_samples` (documented in running-sweep.md).
- **Backtest wrapper `_current_df()` no longer copies** — per-bar full-prefix `.copy()` was one of two O(n²) sources (86+ min for a single 15m backtest; ~4.5 min after the fix, ~20× faster). Contract: strategies treat the df as read-only.

### Fixed (Phase 2 Session 4)
- **O(n²) backtest loop** — `bghtrend_pullback.on_bar` recomputed all indicators on the whole prefix every bar; combined with the per-bar prefix copy this made 15m full-history sweeps infeasible (ETA ~43 h for 30 samples). Fixed via `precompute` + no-copy (see Added/Changed).
- **`data_loader._ensure_datetime_index`** — Binance `fundingTime` has millisecond jitter (`08:00:00.001000` next to `08:00:00`); pandas 2.x infers the format from the first row and hard-fails on mixed precision. `format="ISO8601"` parses both variants (shared with the OHLCV path, which is ISO-compatible).

### Added (Phase 2 Session 3 — ADR-011 microstructure adjustments, 2026-06-19)
- **`algo_bot/microstructure.py`** — pure, I/O-free overlay charging slippage + perpetual funding on top of the raw backtest. Frozen dataclasses (`MicrostructureConfig`, `TradeCost`, `MicrostructureResult`) + pure functions (`slippage_cost`, `settlements_in_window`, `synthetic_funding_series`, `resolve_funding`, `funding_flows_for_trade`, `funding_cost_for_trade`, `apply_microstructure`). Slippage = constant `slip_bps`/side cash debit at entry+exit; funding = per-settlement (8 h) `Notional × Rate`, longs pay shorts when positive, charged only for positions open at the settlement instant (half-open `(entry, exit]`). Both subtracted from the raw equity curve → `equity_adjusted` and from trade PnL → `pnl_post`. mypy strict-on-new.
- **`tests/test_microstructure.py`** — independent arithmetic oracle (handcomputed literals, no pandas re-application, no mocks): funding from `Notional × Rate` worked examples, slippage from `notional × bps/1e4`, bar-by-bar equity overlay timeline, tz-aware normalisation. Funding math reproduced from Binance docs before writing tests (xtrender lesson 2026-06-11).
- **`algo_bot/funding.py` rewritten** — legacy hardcoded scraper → parametrised `fetch_funding(symbol, start, end)` + `algo-fetch-funding` CLI; output `bot_data/processed/binance_<SYMBOL>_funding.csv` (`datetime`,`funding_rate`, UTC).
- **`data_loader.load_funding(symbol)` + `get_funding_path`** — loads per-symbol funding history (Decision 7a).
- **`run_backtest(..., microstructure=, funding_historical=)`** — opt-in overlay (default `None` = off, backward-compatible). Adds `Equity_adjusted` to the equity DataFrame, 4 breakdown columns to trades (`pnl_raw`/`pnl_post`/`slip_cost_quote`/`funding_cost_quote`), and `_metrics_summary_raw` + `_metrics_summary_post_microstructure` + `_microstructure` to `summary.json` (`_metrics_summary` kept as backward-compat alias of raw).
- **`WalkForwardConfig.microstructure`** — threaded per fold; per-fold `MetricsSummary` and the stitched OOS curve become post-microstructure when enabled; funding sliced to the fold range.
- **Shared CLI flags** `--microstructure {none,full}` / `--slip_bps` (default 1.0) / `--funding_source {historical,synthetic,none}` / `--funding_rate_synthetic` (default 0.0001) across `algo-backtest`, `algo-sweep`, `algo-walkforward`; `algo-fetch-funding` entry-point in `pyproject.toml`. Sweep `index.csv` gains `sharpe_raw` / `sharpe_post`.
- **Docs (English):** ADR-011, `docs/reference/modules/microstructure.md` (deep reference), `docs/concepts/microstructure.md` (perp cost mechanics, why ~10 bps round-trip is realistic, reading raw vs post). Cross-references added to `strategy-bghtrend-pullback.md` and `walkforward.md`.

### Removed (Phase 2 Session 3 — ADR-011)
- **Cosmetic microstructure scaffold** in `backtester.py` (`apply_micro_price`, `adjust_trades_df`, `--slippage_bps`, `--spread_bps`) — computed unread `AdjEntry/AdjExit/PnL_adj` columns, never touched equity or metrics. Superseded by the `microstructure` overlay (which adjusts the equity curve, hence Sharpe/maxDD).
- **Dead funding scaffold** in `sweep.py` (`--funding_csv`, `--funding_mode`, the `_funding` extract) — passed kwargs `run_backtest` never accepted (would have raised on use). Replaced by the shared microstructure flags.

### Added
- **Phase 2 tail-end cleanup (2026-06-11) — code health, 5 parking-lot items z ROADMAP:**
  - **EMA monotonicity validation runtime** — `XtrenderPullbackParams.__post_init__` egzekwuje `ema_fast < ema_mid < ema_slow` (ostra nierówność) i podnosi `ValueError` z czytelnym komunikatem. Łapie każdą ścieżkę konstrukcji paramów (`algo-backtest`/`algo-sweep`/`algo-walkforward` — wszystkie przez `coerce_params` → `schema(**clean)`). Wcześniej odwrócony zestaw "po cichu" robił zero trades. Testy: `tests/test_bghtrend_params.py` (5 testów, w tym dowód że brzegowe kombinacje z b1..b4 przechodzą).
  - **`tests/test_xtrender.py`** — 8 standalone testów wskaźnika (wcześniej coverage tylko pośredni przez bghtrend). Metoda: niezależna wyrocznia z definicji matematycznej (rekurencje EMA/RSI/T3 zwykłymi pętlami, bez pandas, bez mocków) dla short/long leg i pełnego łańcucha T3; handcomputed literal dla stałej ceny (wszystkie trzy legi = −50, współczynniki Tillsona sumują się do 1); dots na syntetycznej V-kształtnej cenie; wiring dots-z-short_t3; kontrakt 5-tki (shape/dtype/index/warmup fillna).
  - **`__implied_tf` meta-key** w `config/bghtrend_b1..b4.yaml` (b1/b2 → `1h`, b3 → `15m`, b4 → `4h`) + komentarz nagłówka per plik. `algo-sweep` (`load_space_from_any` zwraca piąty element) porównuje z każdym `--timeframes` i loguje **WARNING** na mismatch — run leci dalej (świadome cross-TF eksperymenty możliwe), ale przypadkowe "b3 na 4h" zostawia ślad (ADR-006). Brak klucza / `--space_json` → brak checku (backward compatible).

### Fixed (Phase 2 tail-end cleanup 2026-06-11)
- **`config.yaml` commission 0.002 → 0.0004** — relikt spot, 5× za wysoko vs realna taker fee Binance USDT-M (4 bps). Sekcja `backtest:` oznaczona jako informacyjna (żaden kod jej nie czyta — patrz Changed).
- **Korekta błędu audytu Sesji 1 w docs — ROZSTRZYGNIĘTE w tej samej sesji: kod = intencja.** `strategy-bghtrend-pullback.md` (Gate 4) i `indicators-xtrender.md` twierdziły, że filtr entry to `short_t3`, a `long_term` jest "computed-but-unused". Błędne odczytanie kolejności unpacku: `_x_short, x_long, _x_t3, ...` wiąże `x_long` = **`long_term`** (pozycja 1) — i to `long_term` jest faktycznym gate'em entry (`_xtr_ok`) oraz testem stale-exit; `short_t3` napędza tylko dots. Werdykt po porównaniu z oryginalnym Pine Scriptem (B-Xtrender @Puppytherapy, dostarczony przez operatora): `longTermXtrender` to w oryginale komponent "B-Xtrender Trend" (reżimowy), linia T3 z kropkami daje timing — strategia używa ich dokładnie tak; nazwy zmiennych mapują 1:1 na pozycje tupli; filtr short-term na pullbacku blokowałby właściwe wejścia. Naprawiono: docstring strategii (filtr entry nr 3), Gate 4 + stale-exit + tabela indicators w strategy doc, At a glance / Formula / tuple table / Interpretation / Edge cases / Consumers w xtrender doc, glossary `Deadzone`. Zero zmian zachowania — dotychczasowe backtesty ważne.

### Changed (Phase 2 tail-end cleanup 2026-06-11)
- **Single source of truth dla cash/commission:** module-level `DEFAULT_CASH = 1_000_000.0` i `DEFAULT_COMMISSION = 0.0004` w `algo_bot/engine/backtester.py`; `sweep.py` i `walkforward.py` (argparse + sygnatury `run_fold`/`walk_forward`) importują je zamiast trzymać własne kopie. Wcześniejszy drift: `algo-backtest` 100k / `algo-sweep` 200k / `algo-walkforward` 100k / `run_backtest` 1M — teraz wszędzie 1M (>> max(High) BTC, bez warningu fractional trading; na metryki procentowe bez wpływu). Żaden test nie polegał na defaultach (wszystkie podają explicit).
- **Docstring `algo_bot/indicators/xtrender.py`** zaktualizowany do faktycznej 5-tki `(short_term, long_term, short_t3, up_dot, down_dot)` z opisem per pozycja, formułami dots i konwencją warmup/fillna (deklarował 3-tkę — drift sprzed dodania dots).

### Removed (Phase 2 tail-end cleanup 2026-06-11)
- **Phantom `zscore_window` sweep dimension** z `config/bghtrend_b1..b4.yaml` (4 pliki × 1 linia) — wymiar był samplowany, ale inert: `slope_mode` zafiksowany na `pct` we wszystkich configach, gałąź `_slope_zscore` nieosiągalna. Default dataclass (100) i uśpiona gałąź w strategii pozostają; ponowne otwarcie `slope_mode=zscore` wymaga ADR. Przestrzeń sweep: ~17 → ~16 efektywnych wymiarów.
- **Phase 2 Session 2 — historical OHLCV dataset + data integrity validation:**
  - `algo_bot/data_integrity.py` — single source of truth for OHLCV sanity checks (Decision 7: new module + gated pytest, no changes to `fetch_data`/`process_data`). Pure functions over frozen dataclasses: `check_monotonic` (duplicates + out-of-order timestamps), `check_ohlcv_invariants` (high ≥ max(open,close), low ≤ min(open,close), high ≥ low, volume ≥ 0, no NaN), `detect_gaps` (intervals > 3×TF), and the `check_integrity` orchestrator returning `IntegrityReport`. Monotonicity + invariants are hard failures (`report.ok`); gaps are soft (logged as WARNING, do not fail — Decision 6, strategy must see real downtime). Logging per ADR-006/preferences: INFO for "Integrity OK" milestone, WARNING per violation and per gap.
  - `tests/test_data_integrity.py` — deterministic unit tests (handcomputed clean grid + injected violations: duplicate/out-of-order timestamps, high/low/volume/NaN violations, single-gap detection with handcomputed `missing_bars=9`) plus `integration`-marked tests parametrised per (symbol, timeframe) that load real `bot_data/processed/binance_*.csv` via `data_loader.load_processed` and assert hard integrity. Gated: skip gracefully when the file is absent (no mocks — integration value only on real data, mindset rule #3).
  - `docs/guides/data-fetching.md` — runbook (English) for the full Phase 2 set: `algo-fetch` + `algo-process` for BTC/ETH × 15m/1h/4h on Binance USDT-M from 2019-09-08, integrity verification, resume after interruption, force-refetch by deleting the raw file, troubleshooting (missing-ratio abort, ETH later listing, gap WARNINGs).
  - mypy strict-on-new override for `algo_bot.data_integrity` in `pyproject.toml`.
- **Data fetching decisions (Phase 2 Session 2):** (1/3) Binance Futures USDT-M from 2019-09-08 — matches ADR-005/live, ~6.5 years for 5+ WF folds; (2) native fetch per timeframe — zero new code, native volume per TF, no meaningful source divergence within one exchange (the kickoff's resample-from-15m suggestion was reconsidered after reading the code); (4) base volume only — `bghtrend` does not use volume, quote volume deferred; (5) CSV kept; (6) keep `process_data` fill behaviour (forward-fill ≤0.5%, abort above) and surface every gap > 3×TF via the validator; (8) resume by default, force-refetch via raw-file delete. The six processed CSVs are produced by the operator's WSL run (sandbox cannot reach the network/UNC mount); the gated integration test verifies them.
- **Phase 2 Session 1 — strategy audit + config reference + parameter taxonomy** (docs-only, no code changes in `algo_bot/`):
  - `docs/reference/modules/strategy-bghtrend-pullback.md` — deep reference for the MVP candidate. Hybrid format (critical entry/exit/SL-TP-trail paths verbatim, mechanical helpers summarised). Sections: economic thesis (trend persistence + pullback mean-reversion + xtrender momentum confirmation), `on_bar` lifecycle, entry gates (`_trend_ok`/`_pullback_seen`/`_rebound_ok`/`_xtr_ok` + `_entry_distance_ok` veto), exit precedence (trail → same-bar TP/SL → in-profit dot → stale timeout → cooldown), indicators table, full parameter taxonomy (core/tuning/ambiguous with economic rationale per parameter), known limitations, consumers.
  - `docs/reference/modules/indicators-xtrender.md` — deep reference for the Xtrender oscillator (Decision 4: standalone doc rather than a section). Formula (rsi-of-EMA-spread short leg, rsi-of-smoothed-price long leg, T3 smoothing), T3 internals, dots (local extrema), 5-tuple public API, interpretation, edge cases (path dependence, NaN handling), limitations (source docstring drift — describes 3-tuple, returns 5-tuple; no standalone tests; `long_term` computed-but-unused).
  - `docs/reference/config-reference.md` — schema and semantics for `config/config.yaml` (global) and `config/bghtrend_b1..b4.yaml` (sweep spaces). Control keys (`__mode`/`__n`/`__seed`), per-parameter spec grammar (int/float/choice), b1..b4 comparison table, validation rules.
  - Parameter taxonomy with three categories (Decision 2: core / tuning / ambiguous) — overfitting watchlist for Session 6 explicitly flagged (`deadzone`, slope thresholds, `trail_atr_mult`, `pullback_atr_mult`, `sl_atr_mult`, xtrender block).
  - Glossary additions: `Deadzone`, `R:R (Risk:Reward)`, `T3 (Tillson T3)`.
- **Config analysis findings (Decision 3 — configs kept as-is, documented):** b1..b4 are a principled 2-D design — Axis 1 regime timescale (b3 fast ≈15m → b1/b2 medium ≈1h → b4 slow ≈4h), Axis 2 selectivity (b1 strict vs b2 permissive at same timescale). Differences are intentional, not ad-hoc; no harmonisation performed (Decision 5 — kept). Two imperfections documented and deferred: (1) `zscore_window` is a phantom sweep dimension (sampled by all configs but inert because `slope_mode` is pinned to `pct`); (2) implied-TF mapping not encoded in the YAML headers (confirm in Session 4). Also noted: `config.yaml` cash/commission drift vs `algo-sweep` CLI defaults; EMA monotonicity guaranteed by config construction but not validated at runtime (strategy fails silently with zero trades on inverted params).
- GitHub Actions workflow `.github/workflows/check.yml` running `make check` on pull requests and pushes to `master`
- `.pre-commit-config.yaml` with standard file hygiene hooks plus Ruff lint/format hooks
- CLI entry `algo-fetch` for `algo_bot.fetch_data:main`
- CLI entry `algo-process` for `algo_bot.process_data:main`
- `docs/guides/working-with-claude.md` — konwencja pracy z Claudem (Cowork): model "jedna sesja per deliverable" z kickoff/closeout protokołem, rola mózg-Claude (weekly audit + pre-flight on-demand), warstwa trwałości (ROADMAP + ADR + CHANGELOG + memory)
- Pełna dokumentacja w `docs/`: README (TOC), guides (getting-started, daily-workflow, makefile-cheatsheet), reference (package-overview), concepts (glossary), 5 ADR retroactive
- Decyzje fazy 1 podjęte: layout (flatten + algo_bot package), Python 3.11, hatchling, conda env + pip-tools, ruff, mypy strict-on-new
- Konwencja docstring: Google style
- ROADMAP.md zaktualizowany o docs strand w każdej fazie
- **Decyzja C (ADR-006) — logging framework**: `algo_bot/log.py` ze `setup_logging()` (idempotentne) + `get_logger()` + `JsonFormatter`. Konsola (plain, Europe/Warsaw) + rotating file (JSON, UTC, 10 MB × 5 backupów). Zero zewnętrznych dep, stdlib `logging`. Third-party libs (ccxt, urllib3) wyciszone do WARNING.
- Testy `tests/test_log.py` — idempotency setupu, caplog widzi info/warning/exception, JsonFormatter emituje walidny JSON z extra fields. Bez mocków (mindset reguła #3).
- mypy strict-on-new override dla `algo_bot.log` w `pyproject.toml`
- **Decyzja D (ADR-007) — risk-adjusted metrics**: `algo_bot/metrics.py` z pure functions (Sharpe static+rolling, Sortino, Calmar, MAR jako osobne metryki, profit factor, recovery time, max drawdown, CAGR, win rate, total return) + `MetricsSummary` dataclass + thin `summarize()`. Log returns wewnętrznie dla annualizacji (additive across time → dokładne √n scaling), simple returns dla narracji. Annualizacja przez `infer_periods_per_year(index, calendar="crypto")` z fallback 365. Edge cases (zero variance, zero DD, no losing trades, no trades, never-recovered) → `NaN` + `logger.warning`. Recovery time jako `pd.Timedelta` primary, `pd.Timedelta.max` jako sentinel "never recovered" (mapowane na `inf` w `MetricsSummary.recovery_time_days`). Calmar default trailing 36m z pragmatic fallback na całość przy krótszej serii.
- Testy `tests/test_metrics.py` — deterministyczne fixtures (handcomputed Sharpe z log_returns=[0.01, 0.03], handcomputed DD na sekwencji [100,110,...,90,...,110], handcomputed profit_factor=3.0 z trades=[10,-5,20,-10,15]), weryfikacja edge case warningów przez `caplog`. Bez mocków.
- **Cross-strategy correlation** (extension w tej samej sesji, na wniosek Janka — antycypacja portfolio post-MVP): `strategy_correlation(equities, method, on) -> pd.DataFrame` (Pearson default + Spearman opt-in, log_returns default, inner-join na intersekcji czasowej, akceptuje `dict[str, pd.Series]` lub `pd.DataFrame`) + `mean_pairwise_correlation(corr_matrix) -> float` (diagnostyka "jak skorelowane jest portfolio"). Zostaje w `algo_bot.metrics`; spin-off do `algo_bot.portfolio` odłożony do momentu gdy portfolio analytics rozrośnie się (Sharpe portfolio, weighted equity, rebalancing).
- mypy strict-on-new override dla `algo_bot.metrics` w `pyproject.toml` (już istniał z ADR-002, aktywuje się z tym modułem)
- **Decyzja E (ADR-008) — risk limits module**: `algo_bot/risk/__init__.py` + `algo_bot/risk/limits.py` z pure functions over frozen dataclasses. Trzy gates (`check_drawdown`, `check_daily_loss`, `check_positions`) + `check_all` (first-hit ordering: drawdown → daily_loss → max_positions) + immutable state management (`init_state`, `update_state`). Sizing jako pure helper `position_size(equity, sl_distance, risk_pct)` — caller-driven, NIE auto-injection w wrapperze (decyzja Janka — nie wszystkie strategie mają SL przed entry, Signal nie ma `sl_price` field). Daily reset TZ configurable, default UTC. `None` w polach `RiskLimits` wyłącza limit.
- **Backtester risk hook** (`algo_bot/engine/backtester.py`): `make_bt_wrapper` przyjmuje opcjonalny `risk_limits: RiskLimits | None`; w `Wrapped.next()` na początku update_state + check_all; na breach forced exit po Close + raise `RiskLimitBreached`. `run_backtest` łapie exception, wpisuje detale do `stats["_risk_breach"]` i `stats["_risk_limits"]`, zwraca normalnie. ROADMAP Phase 1 success criterion ("Risk module zatrzyma backtest gdy drawdown przekroczy próg") spełniony.
- **CLI flags risk** (`algo-backtest`): `--max_dd_pct`, `--daily_loss_pct`, `--risk_per_trade_pct`, `--daily_reset_tz`. Pominięcie wszystkich = backward-compatible (brak gate'ów).
- **`run_backtest` accepts pre-loaded `data: pd.DataFrame | None`** (ADR-008 §11) — bypassuje `load_ohlcv_csv` gdy podane. Wymagana dla deterministycznych testów (bez pliku CSV w repo) i przygotowane pod walk-forward (Decyzja F) per-fold slicing.
- **`MetricsSummary` embed w `save_outputs`** (post-ADR-007 follow-up, ADR-008 §12): summary.json teraz zawiera `_metrics_summary` z wynikiem `algo_bot.metrics.summarize`. NaN/inf → None pre JSON serialization.
- Testy `tests/test_risk_limits.py` — deterministyczne fixture'y (handcomputed equity sequences, exact DD values, daily reset boundary cases UTC vs Europe/Warsaw), integration test z syntetycznym OHLCV który exercise'uje pełną ścieżkę `run_backtest` → breach → halt. Bez mocków.
- Testy `tests/test_backtest.py` — sygnatura naprawiona po flatten 2026-05-14. Smoke test z syntetycznym OHLCV (zawsze działa, bez wymogu CSV w bot_data/processed/) + integration test gated na obecność pliku. `@pytest.mark.skip` usunięty.
- `docs/reference/modules/risk-limits.md` — deep reference (API, edge cases, backtester integration, CLI, limitations)
- `docs/concepts/risk-management.md` — cienki concept doc (ROADMAP linia 55), Phase 1 deliverable, rozbudowywany w Fazie 4
- `algo_bot/strategies/buy_and_hold.py` — baseline strategia kup-i-trzymaj (deterministyczne wejście na drugim barze, brak exit). Używana jako test fixture dla risk module integration test i jako baseline benchmark do porównań strategy-vs-HODL w research workflow.
- **Decyzja F (ADR-009) — walk-forward analyzer**: `algo_bot/engine/walkforward.py` z pure functions + frozen dataclasses (`WalkForwardConfig`, `Fold`, `FoldResult`, `WalkForwardReport`). Generator foldów (rolling default + anchored toggle, `int | pd.Timedelta` granulation) + executor wywołujący `run_backtest(data=fold_slice, risk_limits=...)` per fold z fresh RiskState (literature convention) + aggregator (`folds_df`, `distribution` z mean/median/std/min/max/mvp_threshold, `compute_mvp_pass`) + equity stitching (rebase + compound). Output: `results/walkforward/<wf_run_id>/{walkforward_summary.json, walkforward_folds.csv, walkforward_distribution.csv, walkforward_equity.csv, fold_<i>/}`. `wf_run_id` convention `wf_<symbol>_<timeframe>_<strategy>_<YYYYMMDD_HHMMSS>`. Sekwencyjnie w MVP (foldy są niezależne — paralelizacja jako future flag w razie per-fold optimization w Fazie 2). No-leakage invariant (`test_start > train_end` strict) asercjonowany przy generacji. Boundary closes detekcja (trades z `ExitTime == test_end`) z warning gdy dominuje fold.
- **MVP threshold row** w distribution DataFrame: Phase 2 success criteria (ROADMAP linie 100-104) — sharpe ≥ 1.0, max_drawdown_pct ≥ -0.25, profit_factor ≥ 1.3, n_trades ≥ 50 — encodowane jako moduł-level `MVP_THRESHOLDS` dict, `compute_mvp_pass(distribution)` zwraca 4 boole (mean per metric vs próg). Operator widzi pass/fail w `walkforward_summary.json` bez post-processingu.
- **CLI `algo-walkforward`** z argumentami `--symbol --timeframe --strategy --params --train --test --step --mode --min_folds_warn --max_dd_pct --daily_loss_pct --risk_per_trade_pct --daily_reset_tz --log-level`. `--train`/`--test`/`--step` akceptują int bars (np. `8760`) albo `pd.Timedelta` string (np. `365d`, `12h`, `5min`). Wpis w `pyproject.toml [project.scripts]`.
- Testy `tests/test_walkforward.py` — deterministyczny synthetic OHLCV (3 lata, 1h, geometric drift z seed=42), unit tests pure helpers (`_to_bars` int/Timedelta/edge cases, `_parse_window`), fold generation (rolling vs anchored, no-leakage invariant, monotoniczne progression, error/warning cases — empty/non-monotonic/too-short/single-fold/degenerate-step/overlap/gaps/min-folds-warn), agregacja (handcomputed mean/median/std/mvp_pass — DD boundary case `-0.25` exact pass), equity stitching (handcomputed compound 1.10 × 1.05 × 0.95 = 109_725), integration test `walk_forward` end-to-end na `buy_and_hold`, persistance test (`save_report` → wszystkie pliki + valid JSON). Bez mocków.
- `docs/reference/modules/walkforward.md` — deep reference (At a glance, Input shape, Fold generation, Public API, Per-fold execution, Risk module per fold, Aggregation, Equity stitching, Output layout, CLI, Edge cases, Limitations, Consumers, See also).
- `docs/concepts/walk-forward.md` — Phase 2 concept doc (cienki, ROADMAP linia 91), methodology, why mandatory before live, jak czytać per-fold/distribution/stitched_equity, rolling vs anchored sanity check, dlaczego reset RiskState per fold, parameter stability jako follow-up Fazy 2, when WF isn't enough (survivor bias, microstructure, regime change).
- mypy strict-on-new override dla `algo_bot.engine.walkforward` w `pyproject.toml` (już istniał z ADR-002, aktywuje się z tym modułem).

### Fixed
- **`algo_bot/metrics.py` regresje** złapane przy ADR-008 closeout (`make check` pierwszy run 2026-05-24). Pre-existing fails od Decyzji D close 2026-05-22 — najprawdopodobniej regresja od bump'a pandas-stubs:
  - **mypy `no-any-return`** w 4 miejscach (`log_returns`, `recovery_time`, `strategy_correlation` x2): cast(pd.Series/pd.Timedelta/pd.DataFrame, ...) — numpy/pandas stubs widzą `np.log(Series/DataFrame)` jako `ndarray` zwracane jako Any. Runtime nieruszony.
  - **Sharpe zero-variance edge case**: `std == 0` zamienione na `std < 1e-12 or math.isnan(std)`. Powód: `math.exp(0.001*i)` w teście wprowadza ~1e-15 floating-point noise w log_returns, std jest niezerowe w skali pikometra, Sharpe wybuchał do 10^13 zamiast NaN. 1e-12 jest 10 rzędów wielkości poniżej typowego std log_returns realnej strategii.
  - **`tests/test_metrics.py::TestCAGR::test_two_years_21_pct_total` tolerance** poluzowane z 1e-4 do 1e-3. Powód: 2024-01-01→2026-01-01 to 731 dni (2024 leap), więc przy `_DAYS_PER_YEAR_CRYPTO=365` (ADR-007 crypto convention) years=2.00274 i CAGR=0.0998 zamiast dokładnego 0.10. Leap-day drift na 2-letnim runie jest poniżej noise floor decyzji inwestycyjnych.
  - **`tests/test_metrics.py::TestStrategyCorrelation::test_spearman_method_runs`** przepisany — monotoniczne `linspace` po `.diff()` dają stałe returns, Spearman correlation constant series jest niezdefiniowany. Dodany mały szum + osłabienie assertu z `== -1.0` do `< 0` (negatywna korelacja przy anti-trending equity). Test teraz sprawdza co nazwa sugeruje (metoda działa), nie strict numerical match.

### Changed
- Konwencja pisania docs — synchronicznie z kodem (każde public API change = update docs)
- **`live/live_binance.py`** — retrofit 27 `print()` → `logger.info/warning/error` z `extra={...}` dla strukturalnych pól (symbol, side, qty, order_id, run_id, error). Usunięta funkcja `ts()` (timestamp teraz w formatterze). `_graceful_exit` zostawiony jako `print` (emergency exit, niezależny od loggera). Log file: `results/live/<run_id>/algo_bot.log`.
- **`algo_bot/engine/backtester.py`** — retrofit `print("[OK] Wyniki zapisane...")` → `logger.info(..., extra={"out_dir": ..., "run_id": ...})`. `main()` wywołuje `setup_logging()` na entry point.
- **`tests/test_backtest.py`** — oznaczony `@pytest.mark.skip` z explicit reason (broken signature od flatten'a 2026-05-14). Refaktor odłożony do dedykowanej sesji follow-up przed Decision E. Patrz docs/adr/007-risk-adjusted-metrics.md Notes.

### Fixed
- `algo_bot/process_data.py:process_file` — wyrównana sygnatura `feature_cfg: dict[str, Any] | None` → `list[dict[str, Any]] | None` (zgodnie z `compute_features` i docstringiem, który zawsze opisywał listę featurów). Pre-existing bug wykryty incydentalnie przy `make typecheck` podczas Decyzji C. Czysto annotation, runtime bez zmiany.

### Tail-end Fazy 1 (sesja wykończeniowa, 2026-05-24)

Domknięcie trzech parked items z ROADMAP Fazy 1: logging retrofit follow-up (ADR-006), `executor.py` FIXME, brakujący `log.md` deep reference. Jedna sesja "wykończeniowa", niskie ryzyko, mechaniczne.

#### Added
- `docs/reference/modules/log.md` — deep reference dla `algo_bot/log.py` (At a glance, Public API, Conventions, Edge cases, Limitations / Future migration). Wzorzec analogiczny do `metrics.md` i `risk-limits.md`. Domyka lukę zgłoszoną w captain's log 2026-05-24 jako drift #2 (jedyny moduł Fazy 1 bez deep reference).
- `algo-sweep --log-level {DEBUG,INFO,WARNING,ERROR}` CLI flag — operator może wymusić DEBUG (per-iter detail) lub WARNING (ciche długie sweepy) bez edycji kodu. Default INFO.

#### Changed
- **Logging retrofit follow-up (ADR-006)** — wszystkie pliki `algo_bot/` używają `from algo_bot.log import get_logger; logger = get_logger(__name__)` z `extra={...}` dla strukturalnych pól. Konwencja level (DEBUG/INFO/WARNING/ERROR/CRITICAL) opisana w `docs/reference/modules/log.md`. Per plik:
  - `algo_bot/process_data.py` — 5 print() → 2 logger.warning + 1 logger.info (z `extra={out_path, rows, symbol, timeframe}`) + 1 logger.warning + 1 logger.exception. `main()` woła `setup_logging()` na entry.
  - `algo_bot/fetch_data.py` — 6 print() → 1 logger.warning (retry CCXT z `extra={error_type, retry, max_retries, symbol, timeframe}`) + 1 logger.info (resume from existing file) + 2 logger.info (batch flush) + 1 logger.info (fetch completed) + 1 logger.exception. `main()` woła `setup_logging()`.
  - `algo_bot/engine/exchanges/binance_adapter.py` — 2 print() → 2 logger.warning z `exc_info=True` (TP/SL order failed; structurally with `extra={symbol, side, tp_price/sl_price}`).
  - `algo_bot/engine/sweep.py` — 3 print() → 3 logger.info milestones (start sweep, progress co 10 jobów, sweep completed). `main()` woła `setup_logging(level=args.log_level)` z nowym `--log-level` flag.
  - `algo_bot/funding.py` — 1 print() → 1 logger.info. Plus naprawiony strukturalny bug: cały kod żył na module level (importowanie pliku wykonywało scraping), opakowany teraz w `main()` + `if __name__ == "__main__":`.
  - `algo_bot/data_loader.py` — 1 print() → 1 logger.exception (legacy `batch_fetch_symbols` per-symbol error handler).
- Stan po retrofitcie: **zero `print()` w `algo_bot/`** (poza `executor.py` — usunięty patrz niżej).

#### Removed
- **`algo_bot/executor.py`** — legacy CLI deprecated. Powód: broken since 2026-05-14 flatten (`from algo_bot.backtester import optimize_backtest, run_backtest` → moduł `algo_bot.backtester` nie istnieje; sweep żyje w `algo_bot.engine.sweep`, `optimize_backtest` jako funkcja w ogóle nie istniała w nowej architekturze). Verified: zero referencji w innych modułach (grep `algo_bot.executor`), brak entry `algo-optimize` w `pyproject.toml` (sweep ma własny `algo-sweep`), 10+ dni nikt nie odpalił `python -m algo_bot.executor`. Cleanup, nie decyzja architektoniczna — stąd CHANGELOG entry, nie ADR. Domyka ROADMAP linia 41 (cleanup `executor.py` FIXME).
- README.md / docs/reference/package-overview.md / docs/ARCHITECTURE.md — usunięte referencje do `executor.py` z file trees i list modułów.

Pierwsza faza foundation. Repo gotowe do pracy nad strategiami z proper toolingiem, layoutem i workflowem.

### Added
- `pyproject.toml` — single source of truth dla pakietu, build backend = hatchling, Python 3.11+
- `environment.yml` — conda env `algo_bot` z `ta-lib` z conda-forge + Python 3.11
- `Makefile` — wszystkie codzienne komendy (env, install, lock, sync, test, lint, format, typecheck, check, backtest, sweep, clean)
- `[tool.ruff]` config — lint + format (zastępuje black+isort+flake8+pyupgrade)
- `[tool.mypy]` config — strict-on-new policy (`algo_bot.risk.*`, `algo_bot.engine.walkforward`, `algo_bot.metrics`)
- `[tool.pytest.ini_options]` — markers (slow, integration, live), strict-markers
- CLI entries: `algo-backtest`, `algo-sweep` (po `pip install -e .`)
- `docs/ROADMAP.md` — plan 5-fazowy do production na VPS
- `docs/ARCHITECTURE.md` — warstwy, mapa modułów, ADRs lite
- `algo_bot/engine/__init__.py` — explicit subpakiet (oryginał polegał na PEP 420 namespace packages)
- Comprehensive `.gitignore` (secrets, Python cache, venvs, IDE, OS, bot_data, results)

### Changed
- **Layout repo: flatten + rename `src/` → `algo_bot/`** (decyzja A). Cały kod migracja z `trading/backtesting/algo_bot/*` do roota. Pakiet teraz `algo_bot` z subpakietami `engine/`, `indicators/`, `strategies/`, `telemetry/`, `engine/exchanges/`. Zachowana historia git (rename detection przez `git mv`).
- **Importy: `src.*` → `algo_bot.*`** (drugi commit migracji). Wszystkie statyczne i dynamiczne (`importlib.import_module`) importy zaktualizowane. Usunięte `sys.path.insert` hacki w `executor.py` i `tests/conftest.py`.
- `requirements.txt` — header zmieniony, teraz oznaczony jako lockfile generowany przez `pip-tools` (`make lock`). Stara ręczna lista zostaje jako fallback do pierwszego `pip-compile`.

### Fixed
- `requirements.txt` literówki: `yaml` → `PyYAML`, `tmatplotlib` → `matplotlib`
- `requirements.txt` brakujące: dodane `python-dotenv` (używane w `live/live_binance.py`), `tzdata` (zoneinfo na Windowsie)
- `requirements.txt` orphan: usunięte `distlib` (brak użycia w kodzie)

### Known Issues
- `algo_bot/executor.py` ma broken import `from algo_bot.backtester import optimize_backtest` — broken też było przed flatten (`src.backtester` nie istniało; `optimize_backtest` żyje w `engine/sweep.py`). FIXME w pliku, do decyzji w fazie 1 czy deprecation czy migracja.
- `tests/test_backtest.py` niespójna sygnatura — wywołuje `run_backtest(df, StrategyClass)` ale nowy backtester ma sygnaturę `run_backtest(symbol, timeframe, strategy, params, ...)`. TODO w pliku, do refaktoru przy decyzji D (metrics + test fixtures).
- `algo_bot/strategies/bitcoin_breakout.py` — pusty plik (placeholder bez implementacji).

---

## [0.0.1] — przed 2026-05-11

Początkowy stan repo (przed naszą pracą). Funkcjonalności już istniejące w kodzie:

### Added (pre-existing)
- `StrategyBase` + `Signal` — unified API dla backtest i live
- Engine: `backtester.py` (532 linie, wrapper na backtesting.py z TP/SL/trail), `sweep.py` (352 linie, grid + random search)
- Live: `live_binance.py` (401 linii, hybrid TP/SL mode server/local/hybrid)
- 7 strategii: `bghtrend_pullback` (333 linie, najbardziej rozbudowana — trend + pullback + xtrender + ATR-trail), `bollinger_band_breakout_short`, `simple_momentum`, `short_trend_following`, `ema_cross_sig`, `dca_btc`, `template`
- Data pipeline: `fetch_data.py` (CCXT → OHLCV), `process_data.py` (raw → processed z featurami), `data_loader.py`
- Indicators: `core.py` (ema, rsi, t3, atr), `xtrender.py`
- Telemetry: `journal.py` (CSV trades + equity per run_id)
- Configi YAML: `config/config.yaml` + 4 warianty `bghtrend_b1..b4.yaml`
- Notebooks: `01_data_exploration.ipynb`, `02_bollinger_analysis.ipynb`
- Tests: smoke test dla bollinger backtest

[Unreleased]: https://github.com/JanekPlo/algo_bot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JanekPlo/algo_bot/releases/tag/v0.1.0
