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

### Changed (MR-Session 4 preregistration freeze, 2026-07-20)

- Froze the pre-outcome 528-run manifest core on the intended VPS as
  `657e910101933cdeab15209189a31b4087672b8c52018b5dc72f36dcd1c08e1a` after the
  complete ETHUSDT funding restoration, frozen Bybit contract capture, runtime/data
  preflight, and full quality gate. No Session-4 strategy run or metric was produced or
  read; the clean commit/tag and prepared-manifest provenance gates remain mandatory.

### Added (MR-Session 4 runner draft — blocked before freeze, 2026-07-15)

- Added the deterministic 528-run BTCUSDT/ETHUSDT × M5/M10 Session-4 contract,
  development-only streaming data boundary, frozen paginated Bybit contract capture,
  native Nautilus execution/evidence assembly, and an outcome-blind two-worker runner.
- Added atomic per-run staging/deep verification/rename, bounded two-attempt retry,
  crash-safe resume and quarantine, manifest core/provenance hashes, and a fail-closed
  preregistration draft. Manifest preparation now binds exactly one canonical
  preregistration tag to the frozen commit and supports the same in-repository custom
  Bybit-contract path as `lock-core`. No Session-4 strategy run or metric reveal is
  authorized yet.
- Added H1-versus-M5-derived-H1 divergence provenance with a frozen 25-bps price bound,
  NumPy/Pandas/runtime fingerprints, funding-interval contract validation, and a literal
  30-check invariant ledger.
- Added causal native funding valuation from source-precision completed H1 mark closes,
  including one pre-development mark bar for the boundary settlement, a PyO3 LAST/mark
  characterization, and an independent signed-quantity × mark × rate amount oracle.

### Fixed (research operations, 2026-07-15)

- `scripts/vps-sync.sh up --dry-run` no longer creates the remote destination directory;
  it performs only the requested non-mutating rsync preview.
- Bybit funding retrieval now paginates backward with a decreasing server-side V5
  `endTime`. This fixes the earlier forward-`since` loop which could silently save only
  the newest 200 settlements from a multi-page range; duplicate, overlapping, stalled,
  boundary-escaping, or incomplete pages now fail closed without touching reserved-tail
  settlements.
- Session-4 runtime capture now accepts the official pinned uv output with its platform
  suffix (for example `uv 0.11.28 (x86_64-unknown-linux-gnu)`), canonicalizes it to the
  exact `0.11.28` version, and explicitly rejects uv, Python, or implementation drift from
  uv 0.11.28 and CPython 3.12.13.
- Research-mode Nautilus event retention now keeps the state machine and observer fully
  informed while omitting high-volume marker snapshots, and no longer claims unproved
  server-side `closePosition` parity.

### Added (Phase 2 MR-Session 3 Beta Iteration 2 — evidence and M5/M10 fidelity, 2026-07-15)

- **Native Bybit mark-price pipeline:** `algo-fetch --price-source mark` routes through the
  Bybit mark-OHLCV endpoint; `algo-process` writes OHLC-only
  `bybit_<SYMBOL>_mark_1h.csv` and applies a fail-closed validator (positive OHLC, exact UTC
  grid, completed bars, no gaps and no synthetic fill). Local BTCUSDT/ETHUSDT H1 histories
  match the complete overlap of their trade-OHLCV series.
- **Isolated-margin evidence in `microstructure.py`:** frozen mark-price/risk-tier/position
  types, causal completed-bar lookup, Bybit risk-limit normalization, current UTA isolated
  liquidation formula and first-crossing detection (`Low` long / `High` short). A crossing
  is a recorded hard-stop economic outcome, not an evidence-quality rejection.
- **`BacktestResult` schema v2:** independent `FillMethod` and `MarginMethod`, explicit
  mark-price source and serialized liquidation events. Eligibility fails closed unless fills
  are `nautilus_native_bar` and margin is `mark_price_isolated`. Schema-v1 P9 artifacts are
  migrated in memory as `close_naive`/`none` without rewriting the originals.
- **Native M5/M10 marking stream:** `MarkingBarClosed` in the pure
  `MastermindStateMachine`, first-touch comparison against the last completed H1 Bands,
  strict interval ordering, one configured marking TF and two-step marking-before-H1
  execution. The thin Nautilus wrapper subscribes to and routes multiple native `BarType`s;
  H1-only remains an explicitly diagnostic fallback.
- **Regression coverage:** hand-computed liquidation oracle, causal/gap integrity cases,
  schema-v1 migration and schema-v2 liquidation round-trip, M5/M10 prefix invariance and
  a PyO3 two-stream ordering fixture.

### Changed (Phase 2 MR-Session 3 Beta Iteration 2 — Session-4 authorization, 2026-07-15)

- Accepted architecture `1a/2a/3R/4R/5c/6a/7R`. MR-Session 4 is now unconditional after
  preregistration, with BTCUSDT and ETHUSDT evaluated separately, H1 execution and M5/M10
  marking variants. The H1-only profile is diagnostic. No Session-4 strategy has loaded
  reserved rows; later operator-side full-file integrity access is explicitly disclosed in
  the Session-4 draft. P9 remains immutable `SMOKE_ONLY / NOT_ELIGIBLE` evidence.

### Added (Phase 2 MR-Session 3 Beta Iteration 1 — Exchange migration Binance→Bybit, 2026-07-14)
- **ADR-015 — venue migration to Bybit.** Forward-only work moves to Bybit v5 linear USDT
  perpetuals (Binance inactive in the EU for Janek; Bybit account active + MMS prior on Bybit).
  Binance historical CSVs kept unchanged as a pinned reference baseline. First of four
  MR-Session-4 blockers resolved. See `docs/adr/015-exchange-migration-bybit.md`.
- **Per-exchange data pipeline:** `--exchange {binance,bybit}` on `algo-fetch`,
  `algo-fetch-funding` (Bybit v5 `fetch_funding_rate_history`) and `algo-process`
  (exchange-prefixed RAW/PROCESSED naming, backward-compatible with legacy Binance files).
  M10 has no native Bybit kline → offline aggregation from M5 via `algo-process --resample-to 10m`.
- **Per-exchange cost model:** `EXCHANGE_DEFAULTS` in `backtester.py` (binance taker 0.0004,
  bybit 0.00055) with a shared `--exchange` flag on `algo-backtest`/`algo-sweep`/`algo-walkforward`
  that also selects which `<exchange>_*.csv` is loaded. Default `binance` is backward-compatible;
  explicit `--commission`/`--slip_bps`/`--funding_rate_synthetic` still override.
- **Bybit CCXT adapter + live runner:** `algo_bot/engine/exchanges/bybit_adapter.py` (One-Way
  mode, `reduce_only`, TP/SL via v5 position trading-stop; mypy strict) and testnet-first
  `algo_bot/live/live_bybit.py` with `close_all_positions()` implementing Bybit Close-All
  parity (sequential cancel-all-orders → market `reduce_only` close, logged, best-effort retry).
- **`.env.example`** documenting Bybit testnet/mainnet + Binance keys (secrets stay out of git).
- **Tests:** `tests/test_bybit_adapter.py` (pure `to_market_symbol` unit + opt-in Bybit-testnet
  smoke, no mocks, auto-skip without keys). Native `nautilus_trader.adapters.bybit` verified
  available in 1.230.0 (reserved for the future backtest lane).

### Added (Phase 2 MR-Session 3 Beta — P1–P9 complete, 2026-07-13)
- **Executable MMS v2 domain:** frozen specification, pure engine-independent
  `MastermindStateMachine`, deterministic identifiers, transition invariants, durable
  snapshots/outbox, idempotency and fail-safe recovery for late or unattributed fills.
- **Nautilus migration PoCs and adapters:** causal timestamp/execution probe, NETTING OMS
  probe with virtual base/add-on legs, Tier-1 legacy compatibility/equivalence adapter, and
  thin PyO3 `NautilusMastermindStrategy` wrapper with explicitly profiled smoke execution.
- **Auditable results:** versioned `BacktestResult` with engine/source/cost eligibility,
  tables for equity/trades/orders/fills/positions/funding, hashes and round-trip validation.
- **Frozen development-only P9 benchmark:** preregistered 2 × 6 matrix on BTCUSDT H1,
  native commissions/funding/slippage ledger, holdout boundary, 22 invariant checks per run
  and frozen ablation contrasts. The completed suite produced **12/12 runs, 264/264 passed
  invariant checks and 22 ablation rows**. Full report:
  `docs/experiments/mms-v2-beta-results.md`.

### Changed (Phase 2 MR-Session 3 Beta — final decision, 2026-07-14)
- **Decision: `ITERATE BETA`.** All P9 results remain unconditionally
  `SMOKE_ONLY / NOT_ELIGIBLE`; they are descriptive mechanics evidence, not a ranking or
  profitability claim. MR-Session 4 is blocked until Binance Close-All parity,
  mark-price/order-book or equivalent fill/cost evidence, M5/M10 fidelity and the exact
  multi-instrument scope are resolved and preregistered. Holdout remains unread.
- `README`, ROADMAP, ADR-014, the engine-migration concept and docs index now point to the
  final P9 evidence and replace the earlier unconditional Session-4 schedule.

### Added (Phase 2 MR-Session 3 Beta — P0/Beta 0, 2026-07-13)
- **Python 3.12 + `uv` runtime:** `.python-version` pins CPython 3.12.13;
  `uv==0.11.28` is enforced in `pyproject.toml`; `uv.lock` is the authoritative lock and
  `requirements.txt` is now a generated compatibility export. The primary Conda
  `environment.yml` was removed after the wheel smoke test made the fallback unnecessary.
- **Pinned engine dependencies:** `nautilus_trader==1.230.0`, `TA-Lib==0.7.0`, and the
  legacy baseline `backtesting==0.6.5`. Runtime regressions assert the exact versions and
  execute a C-backed TA-Lib SMA, rather than checking imports only.
- **P0 sweep-config guard:** `load_space_from_any` now rejects non-mapping random parameter
  specs while loading. A missing YAML comment marker can no longer survive until sampling;
  repository-wide config and malformed-fixture tests pin the behavior.

### Changed (Phase 2 MR-Session 3 Beta — P0/Beta 0, 2026-07-13)
- `Makefile` setup/check/CLI targets and CI now run through locked `uv`; CI pins Ubuntu
  22.04, Python 3.12.13, uv 0.11.28, and the official setup action. Ruff targets `py312`,
  mypy checks Python 3.12, and the pre-commit Ruff hook matches the lock.
- Full local hard gate passed on the new runtime: **282 passed, 1 skipped**. The P0 baseline
  immediately before migration also passed on Python 3.11: **280 passed, 1 skipped**.
- Corrected `mr_b3.yaml`'s accidental `git#` first line and synchronized the Alpha docs:
  nine decisions, pinned legacy wording, native Nautilus costs, VectorBT callback capability
  without a live path, one add-on with trigger A/B, the positions link, and
  “author-claimed prop track record”.

### Added (Phase 2 MR-Session 3 Alpha — Engine migration ADR, 2026-07-13)
- **ADR-014 `docs/adr/014-engine-migration-nautilus.md`** (English, format ADR-011) — adopt
  `nautilus_trader` as the primary engine for event-driven / state-machine strategies,
  **coexisting in parallel** with `backtesting.py` (retained as a pinned legacy baseline
  for single-position strategies). Motivation: MR-Session 2's bare-core failure did not test
  the deferred MMS sizing layer (pyramiding + sequential leverage), which is a state machine
  above single positions that `backtesting.py` cannot express cleanly; migration is also
  justified independently as a capability upgrade (backtest-live parity, multi-TF, portfolio)
  for future event-driven candidates. Nine decisions formalised with options + trade-offs:
  (1) **two-tier adapter** — a Tier-1 compat shim (`on_bar`→nautilus, for legacy strategies +
  cross-engine equivalence testing) plus **native** strategies where the MMS logic is a
  **pure `MastermindStateMachine`** (no nautilus imports, `pytest`-tested) wrapped by a thin
  `NautilusMastermindStrategy` — a library breaking change lands on the wrapper, not the edge
  logic; (2) coexistence dispatch by base class + `__engine__` opt-in + `--engine` CLI
  override; (3) **M5 data deferred** — the deferred edge's triggers are H1-native (fidelity,
  not capability), v2 PoC runs on H1; (4) `StrategyBase` frozen (v2 is native, so it does not
  evolve); (5) shared result **format** `(stats, equity, trades)` (WF/sweep unchanged) but
  engine-specific cost **method** — legacy keeps the ADR-011 overlay, **nautilus uses native
  fills/commissions/funding** (multi-leg pyramided turnover costed natively; approximate
  net-position costing only for the Beta smoke test, never for eligibility); native result is
  a richer `BacktestResult` (orders/fills/positions/`engine_version`/hashes) behind the tuple
  facade; (6) staged completion with a **Beta 0 runtime step** first (v2 native end-to-end →
  full-MMS go/no-go); (7) bailout tripwires — a runtime/env conflict is a **runtime-migration
  task** (Python 3.12, `uv`, or container), *not* a custom-engine trigger; time (>8h/>12h) and
  trust (equivalence tolerance) tripwires beyond that; `vectorbt` is not the pyramiding
  fallback (it *can* via `from_order_func` callbacks, but less readably and with **no live
  path**); (8) `backtesting.py` **retained as a pinned legacy baseline**
  (tag/lockfile/snapshot/fixtures/container), supported through the migration, retirement a
  separate ADR (softened from "forever/sacred"); (9) **position model** — virtual base/add-on
  legs over a NETTING venue position with reduce-only conditional stops, given the Binance
  adapter's constraints (no bracket orders; `reduce_only` disabled in Hedge Mode), validated
  in the PoC. Supersedes the migration note in ADR-005 (triggers activated). **Runtime
  constraint:** repo Python 3.11 → **≥3.12** (nautilus requirement; CPython+`uv` recommended,
  conda not officially supported) as Beta 0. **MMS math fix:** pyramiding is **one** add-on x1
  (two alternative triggers), total x2 — not "two add-ons" (x1+x1+x1=x3 was contradictory);
  sequential leverage binary x1↔x0.1. The confirming-candle and Stochastic paths are trigger
  A/B policies for the same single add-on, not add-on #1/#2. **Zero code.**
- **`docs/concepts/engine-migration-strategy.md`** (English, DRAFT) — user-facing overview:
  why we migrate, what is not changing (backward-compat depth), how the two engines coexist
  (native lane + compat lane), and the migration timeline milestones (Alpha done → Beta PoC →
  Session-4 full sweep → Session-5 go/no-go). Expanded in later sessions.

### Changed (Phase 2 MR-Session 3 Alpha, 2026-07-13)
- **`docs/ROADMAP.md`** — MR-Session 3 split into Alpha (this session, done) + Beta (nautilus
  PoC + v2, next), with MR-Session 4 (unconditional full v2 sweep) and MR-Session 5
  (conditional WF/MC/stress + full-MMS go/no-go). Engine migration recorded as an active
  Phase-2 strand; ADR planning table gains ADR-014; the "Po MVP" engine-migration note updated
  (nautilus migration started early in Phase 2; vectorbt reframed as a post-MVP sweep-speed
  candidate, not a pyramiding solution). `docs/adr/README.md` index gains the ADR-014 row.

### Added (Phase 2 MR-Session 2 — Sweep, 2026-07-13)
- **`notebooks/04_mr_sweep_review.ipynb`** — dedicated mean-reversion sweep review with a prior recorded before loading results; heuristics A–E, imported `WF_ELIGIBILITY_THRESHOLDS`, MR-specific b2 `entry_mode` split (F), and calendar-year top-3 regime robustness (G). The executed notebook audits post-cost equity positivity before accepting log-return metrics and exports the evidence record.
- **`results/experiments/mr_sweep_review.json`** — six matched-TF groups × 30 samples (180 runs): group summaries, raw top-10 params/clustering, cross-symbol consistency, b2 entry-mode split, eligibility survivors and per-year regime tables. Verdict: **bare core FAILS** — 0/180 eligible, global best raw Sharpe −0.291, 169/180 post-cost curves reach equity ≤ 0, best valid Sharpe_post −0.497.

### Changed (Phase 2 MR-Session 2, 2026-07-13)
- **`config/mr_b1.yaml` + `mr_b3.yaml`: `entry_mode="bb_only"` frozen** — MMS-consistent base-entry baseline; Stochastic belongs to the deferred add-on layer. b2 alone retains `entry_mode` as an empirical diagnostic. Its raw distributions mildly favour `bb_only` on both assets, but neither mode has usable edge.
- **`docs/ROADMAP.md`** — MR-Session 2 closed with all six sweep/analysis deliverables; direct WF rejected. MR-Session 3 is now a bounded pyramiding-ADR and engine-migration cost/benefit decision, not an automatic pivot and not WF on the failed base.

### Fixed (Phase 2 MR-Session 2, 2026-07-13)
- **Bankruptcy-safe log-return metrics** — `metrics.log_returns()` now fails closed with an empty series when equity contains non-finite or non-positive values. Previously `np.log(equity <= 0)` produced NaNs which `dropna()` silently removed, allowing Sharpe to be calculated on the pre-bankruptcy prefix and occasionally appear positive. Regression coverage in `tests/test_metrics.py` pins zero/negative/NaN/inf equity and the misleading-prefix-Sharpe case.

### Added (Phase 2 MR-Session 1 — Audit, 2026-07-11)
- **`docs/references/mms/`** — the Mastermind MMS prior extracted into a versioned internal reference: `README.md` (source disclaimer, copyright, not-for-redistribution), 4 HIGH-priority tab extractions in own words + key parameters (`01-position-building`, `02-position-management-filters`, `03-stop-loss-sequential` — the deferred pyramiding/sequential-leverage edge, primary input for the future ADR — and `04-interval-marking`, the M5/M10→H1 origin of the armed→reaction proxy), 2 MEDIUM tabs (`05-market-mechanics-patterns`, `06-algotrading-semi-auto`) with captions, plus 33 full-tab screenshots in `raw/mastermind/` (permanent record — the site may change). `09-backtests-2025-2026.md` pending (source screenshots not yet captured). Key audit finding: in MMS the Stochastic is the **add-on (pyramiding) filter** (%K & %D cross 20/80, 14/3/3 H1), not a base-entry gate — Beta's `entry_mode="bb_stoch"` is a documented deliberate adaptation, resolved empirically in MR-Session 2.
- **`docs/reference/modules/strategy-mean-reversion-bb-stoch.md` — DRAFT → FULL** (hybrid format per the bghtrend pattern): `on_bar` lifecycle with verbatim critical paths (touch scan + Stoch-gate-at-arming, reaction, exit precedence), full parameter taxonomy (core/tuning/ambiguous) with an overfitting watchlist (`bb_num_std` fine steps, Stoch thresholds, `arm_expiry_bars`, `require_reclaim`), the independent-oracle audit record, a 16-row **Mastermind alignment table with per-row citations into `docs/references/mms/`** (verifiable end-to-end: code → reference → extraction → screenshots), the MR-Session 2 interpretation caveat (MMS itself expects the bare core to bleed in trends — a weak sweep does not falsify the methodology), and Known limitations (backtesting.py cannot express pyramiding/sequential leverage → engine-migration early warning).
- **`docs/reference/modules/indicators-bbands.md` + `indicators-stochastic.md`** — deep references for the Beta indicators: formulas, the `ddof=0` rationale (TA-Lib consistency, ~2.6% narrower bands at window=20, regression-tested), zero-variance/zero-range edge cases, causality/prefix-invariance (precompute contract), warmup indices, the smoothing-dilutes-extremes note (why the strategy gates at arming), and the %D-computed-but-unused note.
- **`TestAuditSeams` in `tests/test_mean_reversion_bb_stoch.py`** — the independent-oracle pass mechanised into 3 regression tests for state-machine seams the Beta suite did not cover: (a) a re-touch during the armed window does **not** refresh the expiry counter, (b) the exit bar can never arm a direction (exit returns early), (c) a bar touching both bands arms nothing. Plus `test_window2_numstd1_minmax_identity` in `tests/test_indicators_bbands_stochastic.py` — a hand-derivable bbands identity (window=2, num_std=1 ⇒ upper=max, lower=min of each pair).

### Changed (Phase 2 MR-Session 1, 2026-07-11)
- **`docs/reference/package-overview.md` + `docs/ARCHITECTURE.md` drift fix** (Beta follow-up): removed `bollinger_band_breakout_short.py` references, added `mean_reversion_bb_stoch.py` as the MVP candidate, re-labelled `bghtrend_pullback` as baseline (NO-GO ADR-012), updated `indicators/core.py` listings with `bbands`/`stochastic`. `docs/reference/config-reference.md` header extended with `mr_b1..b3.yaml` + pointer to the new taxonomy.
- **`.gitignore`** — ignore Windows `*Zone.Identifier` artefacts (appear when copying files from NTFS into the WSL repo).

### Fixed (Phase 2 MR-Session 1, 2026-07-11)
- **`require_reclaim` docstring drift in `mean_reversion_bb_stoch.py`** (3 places, zero behaviour change): docstrings said Close must return "do środka wstęgi" (ambiguous — mid line?), while the code checks the band *interior* (`c > lower` / `c < upper`). Clarified to "wnętrze wstęgi (nad dolną / pod górną — NIE linia środkowa)". The only strategy-code touch of the audit; no real drift or bug found in behaviour (unpack order, gate placement, precedence, warmup guard — all verified clean).

### Added (Phase 2 MR-Session Beta — mean-reversion BB+Stoch core, 2026-07-10)
- **`bbands()` + `stochastic()` in `algo_bot/indicators/core.py`** — the two indicators the mean-reversion candidate needs (previously only `ema/rsi/atr/t3` existed). `bbands(close, window, num_std) -> (upper, mid, lower)` = rolling SMA ± `num_std`·σ with **population σ (`ddof=0`, `talib.BBANDS`-consistent)**, not pandas-default `ddof=1` (bands ~2.6% narrower on window=20; chosen for comparability with any TA-Lib reference). `stochastic(df, k, d, smooth) -> (%K, %D)` = slow variant (`%K_raw = 100·(C−LL_k)/(HH_k−LL_k)`, `%K = SMA_smooth(%K_raw)`, `%D = SMA_d(%K)`), zero-range guard `+1e-12`. Both causal (rolling without `center`) → safe to cache in `precompute`.
- **`algo_bot/strategies/mean_reversion_bb_stoch.py`** — contrarian Bollinger mean-reversion, both-directions, `StrategyBase` + `precompute` (BB+Stoch cached, `on_bar` reads `.iloc[:m]` prefix). Entry = "armed → reaction": a wick touch of the band arms the direction, the first reaction candle (body toward the mean) within `arm_expiry_bars` enters. Exit = **TP at the opposite, live band** (recomputed each bar) + **fixed `sl_pct` SL (2%)**, no trail/BE/timeout (bare core). `MeanReversionBBStochParams` frozen dataclass with `__post_init__` fail-fast validation. mypy **strict-on-new** (module added to the strict override list; `self.p` type narrowed in the subclass so `warn_return_any` is satisfied while `attr-defined` stays disabled repo-wide for strategies).
- **`config/mr_b1..b3.yaml`** — sweep spaces informed by the Mastermind MMS prior (BB 20±5, num_std 2.0±0.5, Stoch 14/3/3, SL ~2%). Two principled axes like the bghtrend b-family: **selectivity** (b1 strict = `bb_stoch`-only, deeper bands; b2 relaxed = `entry_mode` swept, shallower bands, longer arm window) and **timescale** (b1/b2 → 1h, b3 → 15m). `__implied_tf` set per file (`algo-sweep` warns on mismatch).
- **`tests/test_indicators_bbands_stochastic.py`** — no-mock independent oracle for both indicators: handcomputed SMA + population-σ and slow-stochastic reference (plain-Python loops), prefix-invariance (causality proof), constant/flat-price literals, a regression test pinning `ddof=0` (bands `sqrt((n-1)/n)`× narrower than `ddof=1`), and the tuple/shape/index API contract.
- **`tests/test_mean_reversion_bb_stoch.py`** — params validation (`__post_init__`, incl. through `StrategyBase`), execution-helper arithmetic (SL math, same-bar exit precedence, reaction body, stoch gate), both-direction entry gates on deterministic OHLCV, same-bar TP-vs-SL precedence (`sl_fixed` default vs `tp_band` with `tp_has_priority`), and live-vs-precompute `Signal` equivalence bar-by-bar (+ direct precompute prefix-vs-recompute proof). No mocks.
- **`docs/reference/modules/strategy-mean-reversion-bb-stoch.md`** — DRAFT skeleton (full deep walkthrough deferred to MR-Session 1 Audit).
- **Decisions (options + trade-offs before code):** (1) entry mechanization on pure H1 OHLC = two-bar *armed → reaction* proxy — M5 sub-bar marking out of scope (no intrabar data); alternatives (single-bar close-through, pierce-and-reclaim) rejected. (2) Stochastic role = `entry_mode ∈ {bb_only, bb_stoch}` **sweep dimension**, not hard-drop/hard-gate; gate applied at **arming** (a reaction-bar gate is structurally self-defeating — the reaction candle lifts `%K` — discovered while writing the Beta tests). (3) TP = opposite **live** band (recomputed each bar), not the band frozen at entry. (4) Funding = existing ADR-011 overlay only, **no strategy mechanics**; note that contrarian MR tends to be on the funding-*receiving* side (short in euphoria / long in capitulation) — a potential tailwind, opposite of trend-following.

### Removed (Phase 2 MR-Session Beta)
- **`algo_bot/strategies/bollinger_band_breakout_short.py`** — legacy `backtesting.py`-native strategy (`talib.BBANDS`, not `StrategyBase`), superseded by `mean_reversion_bb_stoch`. No code imported it (dynamic loader only). Its inert `optimize` block in `config/config.yaml` removed too (no code reads `config.yaml` — it is an informational mirror). Historical mentions (ADR-003, `notebooks/02_bollinger_analysis.ipynb`, earlier CHANGELOG entries) left as point-in-time record; `docs/reference/package-overview.md` and `docs/ARCHITECTURE.md` still list the file — flagged for the next docs-drift pass, not rewritten mid-session.

### Changed (Phase 2 MR-Session Beta)
- **`docs/ROADMAP.md` MR-Session Beta bullet** — reconciled to the Beta kickoff spec (the Pivot-A ROADMAP text was stale): `mean_reversion_bb_stoch` not `_bb_rsi`, **both-directions** not long-only, **Stochastic** not RSI, **fixed 2% SL** not 2×ATR, **TP = opposite band**, **no stale timeout**. Marked DONE 2026-07-10.
- **`pyproject.toml`** — `algo_bot.strategies.mean_reversion_bb_stoch` added to the mypy strict override list (strict-on-new per the project convention; bghtrend and other strategies remain lenient).
- **Deferred to a separate ADR (explicitly NOT in Beta):** pyramiding (adds to a base position) + sequential leverage reduction (anti-martingale x1→x0.1) — Mastermind's actual claimed edge, a state machine *above* single positions that `backtesting.py` cannot express natively (possible early trigger for the engine migration); plus position sizing and timeout/funding-aware exits. Beta backtests the base only — results to be labelled as such.

### Added (Phase 2 Session Pivot Alpha — bghtrend no-go + WF-eligibility calibration, 2026-07-05)
- **ADR-012 `docs/adr/012-mvp-no-go-bghtrend.md`** (English) — formal **no-go** for `bghtrend_pullback` as the Phase 2 MVP candidate. The Session 4 sweep found no exploitable edge: 0/150 post-microstructure configs cleared the WF-eligibility filter; high Sharpe appeared only on 1-3-trade samples (`b4/BTC/4h` top-6 = 0.775 on `n_trades=1`); the edge did not transfer (`b1` median Sharpe_post +0.283 on BTC vs −0.402 on ETH, pct_positive 0.767 vs 0.067); raw→post spread small (0.03–0.04 on 1h) so microstructure is not the killer. Decision: **kept as historical baseline / zero-edge comparator** (not deleted — working code with a documented negative result; benchmark for the next candidate + evidence the framework filters bad strategies). Evidence of record: `results/experiments/sweep_review.json`.
- **ADR-013 `docs/adr/013-wf-eligibility-thresholds.md`** (English, mini) — introduces `WF_ELIGIBILITY_THRESHOLDS` as a **pre-WF** filter, distinct from the ADR-009 `MVP_THRESHOLDS` **post-WF** go-live gate. Recalibrates the in-sample Sharpe bar from an arbitrary `1.5` to `1.0` (rationale: 0.5–0.7× IS→OOS decay → IS 1.0 ≈ 0.5–0.7 OOS, worth *seeing* the decay in WF; no conflict with the go-live `1.0` which is post-WF).
- **`WF_ELIGIBILITY_THRESHOLDS` in `algo_bot/engine/walkforward.py`** — `{sharpe 1.0, profit_factor 1.3, n_trades 100, max_drawdown_pct -0.20}` alongside the **unchanged** `MVP_THRESHOLDS`. Not wired into `compute_mvp_pass` — it is an operator-facing filter over `index.csv`, not a go-live gate. `n_trades`/DD are stricter than the go-live gate on purpose (in-sample it is cheap to accumulate trades; Session 4 high Sharpe sat on `n_trades ≈ 1`).
- **`tests/test_walkforward.py::TestWfEligibilityThresholds`** — 5 tests: exact values, distinct object from `MVP_THRESHOLDS`, Sharpe/PF aligned with go-live, `n_trades` and DD stricter than go-live.

### Changed (Phase 2 Session Pivot Alpha, 2026-07-05)
- **`docs/ROADMAP.md` Phase 2 pivot** — bghtrend candidate marked NO-GO (ADR-012); Sessions 1-4/4b kept as the historical bghtrend record; Sessions 5-8 **retired** (superseded, retained as conceptual template) and replaced by an **MR-Session map** (Alpha done → Beta implementation → MR-Session 1 Audit → 2 Sweep → 3+ WF/MC/Stress/go-no-go) targeting a mean-reversion (Bollinger Bands + RSI) candidate. ADR planning table updated (ADR-012 = no-go delivered early; ADR-013 repurposed to WF-eligibility).
- **`docs/reference/modules/strategy-bghtrend-pullback.md`** — `DEPRECATED as MVP candidate per ADR-012` banner on top; content retained for the comparator role.
- **`notebooks/03_bghtrend_sweep_and_walkforward.ipynb` §1** — the WF-candidate filter now imports `WF_ELIGIBILITY_THRESHOLDS` instead of hardcoding `> 1.5` (still yields 0 candidates — best Sharpe_post 0.775 < 1.0 — so the recorded output is unchanged and honest).
- **`docs/guides/running-sweep.md`** — "Worth walk-forward" section rewritten around `WF_ELIGIBILITY_THRESHOLDS` (import, don't re-hardcode; 1.5→1.0 rationale); new **"Regime robustness sanity check"** soft gate: after the hard filter, compute rolling per-year Sharpe over 2019-H2…2025 (6–7 bins), require `n_positive_years ≥ 3`; hard-pass-but-concentrated (only 2020–2021) → soft NO-GO or cautious WF interpretation. Kept a judgment call in the guide, not a hardcoded constant.

### Added (Phase 2 Session 4b — VPS research runner, 2026-07-04)
- **`scripts/vps-sync.sh`** — rsync wrapper with `up` (`bot_data/processed/` PC→VPS) and `down` (`results/` VPS→PC) subcommands. Config via `VPS_HOST` / `VPS_REPO` / `RSYNC_OPTS`; `--dry-run` supported. No `--delete` (a wrong `VPS_HOST` can never wipe local `results/`); no secrets touched (Decision 5 — only market data and results move).
- **Makefile targets `sync-up` / `sync-down`** — thin wrappers over `scripts/vps-sync.sh`, require `VPS_HOST=...`.
- **`docs/guides/vps-research-runner.md`** (English) — thin runbook: miniforge install, read-only deploy-key clone, `conda env create` + `make check` smoke, dataset sync, running sweeps in tmux, results pull-back, end-to-end smoke test, and anti-patterns (no parallel writes to `index.csv`, no `algo-fetch` on the VPS, no secrets, no `rsync --delete`).
- **Decisions (5):** (1) env = miniforge + `environment.yml` — TA-Lib atomically from conda-forge, matches WSL; (2) data source = rsync `processed/` from PC — reproducibility over freshness (a VPS-side `algo-fetch` would drift the dataset and break PC↔VPS comparability); (3) parallelism = sequential in tmux — `results/experiments/index.csv` append is not multi-process safe, `--index_csv`-per-run deferred; (4) results transport = rsync `results/` back to PC where notebook 03 / brain-Claude live; (5) security = fresh read-only deploy key generated on the VPS, no `.env`/API keys on the box.
- **Minimal `algo_bot/` change** — the design (sequential-in-tmux, Decision 3) needs zero code; the only code change is the walkforward mypy fix below, forced by the VPS smoke test. The parallelism flag stays an explicit follow-up.
- **Operator-run items** (sandbox cannot reach the VPS or the network): VPS provisioning, `make check` on the VPS, `sync-up`, and the smoke sweep are executed from the WSL/VPS terminals per the runbook and confirmed back. Reference host: OVH VPS-2, 6 vCores / 12 GB RAM / 100 GB, Ubuntu 22.04 LTS, `os-waw2`.
- **`requirements.txt` lockfile committed** — `make lock` output, pinning the pip toolchain so a fresh VPS env resolves the same versions instead of grabbing latest (the smoke test surfaced this drift). Note: `make sync` (`pip-sync`) is destructive on a conda env (would uninstall the conda-provided TA-Lib); apply the lockfile on the VPS with `pip install -r requirements.txt` + `pip install -e . --no-deps`. Making `make sync` conda-safe is a deferred follow-up.

### Fixed (Phase 2 Session 4b)
- **`walkforward.py:614` mypy `arg-type` under newer pandas-stubs** — `float(means[k])` where `means = distribution.loc["mean"]` is a Series; newer `pandas-stubs` type `Series.__getitem__` as `Any | Series`, so strict mypy rejected the `float()` argument (green on the PC's older stubs, red on a freshly resolved env). Fixed with `float(cast(float, means[k]))` — a no-op at runtime, version-robust, no behaviour change. Surfaced by the Session 4b VPS smoke test; the only `algo_bot/` code change in 4b.

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
