# Roadmap — algo_bot

> Cel: zbudować pełny, profesjonalny szkielet algotradingowy w którym możemy komfortowo (a) badać strategie, (b) backtestować je rygorystycznie, (c) odpalać paper trading, (d) wjechać na realne pieniądze, (e) trzymać bota na VPS 24/7 z monitoringiem.
>
> Metodologia: **RBI** (Research → Backtest → Implement) + walk-forward + monitoring.
>
> Definicja sukcesu MVP: jedna strategia w pełni przejdzie ścieżkę research → in-sample backtest → walk-forward → testnet → mainnet (mały kapitał) → VPS 24/7 z alertami.

---

## Mapa faz

```
Faza 1: Foundation (framework)         → 2-3 tyg.
Faza 2: Research & Backtest MVP        → 2-4 tyg.
Faza 3: Paper / Testnet MVP            → 1-2 tyg.
Faza 4: Live Mainnet MVP (mały kap.)   → 2-4 tyg.
Faza 5: Production na VPS              → 1-2 tyg.
```

---

## Faza 1 — Foundation (framework gotowy do pracy)

> **Historical implementation note (2026-07-13):** checked Python 3.11/Conda/pip-tools
> bullets below record how Phase 1 was completed; they are not current setup instructions.
> MR-Session 3 Beta replaced that environment with managed CPython 3.12, `uv`, `uv.lock`,
> and a binary TA-Lib wheel. See ADR-002's supersession note and ADR-014.

**Cel:** repo jest porządne, dependencies dzialaja, mamy CI, mamy spójny system konfiguracji i wynik backtestu jest powtarzalny co do bitu.

**Deliverables (kod):**
- [x] Naprawiony `requirements.txt` (literówki: `tmatplotlib`, `yaml`; dodane `python-dotenv`, `tzdata`; TA-Lib z conda-forge nie z pip) — **DONE 2026-05-14**
- [x] `pyproject.toml` (hatchling) z editable install (`pip install -e .`), porzucone `sys.path.insert` hacki — **DONE 2026-05-14** (decyzja B, ADR-002)
- [x] `Makefile` z komendami: `make env`, `make install`, `make test`, `make lint`, `make typecheck`, `make check`, `make backtest`, `make sweep`, `make lock`, `make sync`, `make clean` — **DONE 2026-05-14**
- [x] `environment.yml` — conda env `algo_bot` z Python 3.11 + TA-Lib z conda-forge — **DONE 2026-05-14**
- [x] Strukturę katalogów wyrównać: `algo_bot/` w roocie (vs `trading/backtesting/algo_bot/`) — **DONE 2026-05-14** (decyzja A, ADR-001)
- [x] Konfiguracja ruff (lint + format) i mypy (strict-on-new) w pyproject.toml — **DONE 2026-05-14**
- [x] Risk management module (`algo_bot/risk/limits.py`): max drawdown stop, max concurrent positions, daily loss limit, position sizing oparty o ryzyko (% equity per trade) — **decyzja E** — **DONE 2026-05-24** (ADR-008; pure functions + frozen dataclasses; gates first-hit ordering DD→daily_loss→max_positions; position_size jako caller-driven pure helper bez auto-injection; backtester hook w `Wrapped.next()` z forced-exit + `RiskLimitBreached`; CLI `--max_dd_pct` / `--daily_loss_pct` / `--risk_per_trade_pct`; `MetricsSummary` embed w `save_outputs`)
- [x] Walk-forward analyzer (`algo_bot/engine/walkforward.py`) — out-of-sample, train/test split z rolowaniem okna — **decyzja F** — **DONE 2026-05-25** (ADR-009; pure functions + frozen dataclasses `WalkForwardConfig`/`Fold`/`FoldResult`/`WalkForwardReport`; rolling default + anchored toggle; `int | pd.Timedelta` granulation; reset RiskState per fold (literature convention); rebase+compound equity stitching; `mvp_threshold` row w distribution + `compute_mvp_pass` zwraca 4 boole; output `results/walkforward/<wf_run_id>/`; CLI `algo-walkforward`; sequential MVP, parallel jako future flag)
- [x] Standardowe metryki risk-adjusted (Sharpe, Sortino, Calmar, MAR, profit factor, recovery time) — `algo_bot/metrics.py` — **decyzja D** — **DONE 2026-05-22** (ADR-007; pure functions + `MetricsSummary` dataclass + `summarize()`; log returns wewnętrznie, Calmar i MAR osobno, edge cases → NaN + warning)
- [x] Logging framework + setup (`algo_bot/log.py`) zamiast `print` w całym kodzie — **decyzja C** — **DONE 2026-05-21** (ADR-006; retrofit `live/live_binance.py` + `algo_bot/engine/backtester.py` w tej sesji, pozostałe pliki w follow-up)
- [x] Logging retrofit follow-up (ADR-006): `algo_bot/fetch_data.py`, `algo_bot/process_data.py`, `algo_bot/engine/sweep.py`, `algo_bot/engine/exchanges/binance_adapter.py`, `algo_bot/funding.py`, `algo_bot/data_loader.py` → `get_logger(__name__)` + strukturalne `extra={...}` — **DONE 2026-05-24** (sesja wykończeniowa; `executor.py` deprecated zamiast retrofit; `algo-sweep --log-level` dodany; zero `print()` zostało w `algo_bot/`)
- [x] CI (GitHub Actions): `make check` na każdym PR/push — **DONE 2026-05-25** (ADR-010; `.github/workflows/check.yml`; micromamba from `environment.yml`; no secrets/live API calls)
- [x] Pre-commit hooks: `.pre-commit-config.yaml` z ruff + standard file hooks — **DONE 2026-05-25** (ADR-010; mypy zostaje w `make check`/CI, nie w pre-commit)
- [x] Cleanup po decyzjach — **DONE 2026-05-24** (`executor.py` deprecated w sesji wykończeniowej — broken since flatten, 0 referencji, sweep ma własny `algo-sweep`; `tests/test_backtest.py` sygnatura naprawiona w sesji Decyzji E).
- [x] CLI entries: `algo-fetch`, `algo-process` — **DONE 2026-05-25** (`main()` już istniał; dodane `[project.scripts]` + `--log-level`)

**Deliverables (docs):**
- [x] `docs/README.md` (TOC) — **DONE 2026-05-14**
- [x] `docs/CHANGELOG.md` (keep-a-changelog format) — **DONE 2026-05-14**
- [x] `docs/adr/` template + 5 ADR retroactive (001..005) — **DONE 2026-05-14**
- [x] `docs/guides/getting-started.md`, `daily-workflow.md`, `makefile-cheatsheet.md` — **DONE 2026-05-14**
- [x] `docs/reference/package-overview.md` — **DONE 2026-05-14**
- [x] `docs/concepts/glossary.md` — **DONE 2026-05-14**
- [x] `README.md` (root) update — entry point pod docs/ — **DONE 2026-05-14**
- [x] Per-file header docstrings w 10 kluczowych plikach pakietu — **DONE 2026-05-14**
- [x] ADR-006..010 per każda nowa decyzja w fazie 1 (logging, metrics, risk, walk-forward, CI/pre-commit). Stan: ADR-006 (logging) ✓, ADR-007 (metrics) ✓, ADR-008 (risk) ✓, ADR-009 (walk-forward) ✓, ADR-010 (CI/pre-commit) ✓ — **DONE 2026-05-25**.
- [x] `docs/reference/modules/<modul>.md` dla każdego NOWEGO modułu — deep reference. Stan: `metrics.md` (Decyzja D), `risk-limits.md` (Decyzja E), `log.md` (sesja wykończeniowa 2026-05-24), `walkforward.md` (Decyzja F). — **DONE 2026-05-25** (4/4 nowe moduły Fazy 1 mają deep reference). Plus retroactive z Fazy 2 Sesji 1: `indicators-xtrender.md` (2026-06-05).
- [x] `docs/concepts/risk-management.md` — **DONE 2026-05-24** (Decyzja E; thin Phase 1 deliverable, rozbudowywany w Fazie 4 do `risk-management-production.md`). `walk-forward.md` — **DONE 2026-05-25** (Decyzja F; thin Phase 1 → rozbudowywany w Fazie 2 razem z bghtrend WF analysis). `microstructure.md` — TBD.

**Kryteria sukcesu:**
- `make check` zielony lokalnie i na CI
- Powtarzalność: dwukrotny backtest tej samej strategii z tym samym seedem zwraca bit-identyczne metryki
- Risk module zatrzyma backtest gdy drawdown przekroczy próg
- Każdy plik w `algo_bot/` ma per-file header docstring (Google style)
- Każdy ADR dla decyzji architektonicznej fazy 1 napisany

**Ryzyka:**
- TA-Lib na Windowsie/macOS bywa upierdliwe → udokumentowane w `docs/guides/getting-started.md` (conda-forge zalecane na każdym OS)
- Polityka "docs sync z kodem" wymaga dyscypliny — pre-commit hook sprawdzający `docs/` changes when `algo_bot/` changes to follow-up TODO

---

## Faza 2 — Research & Backtest MVP

**Cel:** wybrana strategia ma statystycznie istotną przewagę w out-of-sample, nie tylko w in-sample sweep.

**Wybór strategii kandydatki:** ~~**bghtrend_pullback**~~ → **NO-GO (ADR-012, 2026-07-05)**. bghtrend była pierwszą kandydatką (najbardziej dopracowana, teza trend+pullback+xtrender), ale Sesja 4 sweep nie znalazła edge'u. Pivot na **mean-reversion (Bollinger Bands + Stochastic)** — patrz status i MR-Session map niżej. (Pierwotnie planowane jako BB+RSI; MR-Session Beta zmieniła oscylator na Stochastic zgodnie z priorem Mastermind MMS.)

**Kontekst:** Faza 1 zamknięta 2026-05-25 (Decyzje A-G + C/D/E/F + ADR-010 CI). Foundation kompletna — `algo_bot.metrics`, `algo_bot.risk`, `algo_bot.engine.walkforward`, logging, CI, pre-commit, wszystkie CLI entries. Wchodzimy w research / walidację strategii. Sesje stają się dłuższe (notebooki, analiza, interpretacja statystyczna) niż w Fazie 1, ale mniej decyzji architektonicznych skali D/E/F — większość pracy to operacje na istniejącym frameworze.

> **Status update (2026-07-05) — PIVOT.** The bghtrend candidate was declared a
> **no-go** in [ADR-012](adr/012-mvp-no-go-bghtrend.md): the Session 4 in-sample sweep
> (2026-07-04) found no exploitable edge — 0/150 post-microstructure configs cleared the
> WF-eligibility filter, high Sharpe appeared only on 1-3-trade samples, and the edge did
> not transfer BTC→ETH (evidence: `results/experiments/sweep_review.json`). Sessions 1-4/4b
> below are **kept as the historical bghtrend record** (done, valid work — the framework
> professionally disqualified its first candidate). The walk-forward / Monte Carlo / stress /
> go-no-go sessions originally numbered **5-8 are retired** as a bghtrend line, superseded by
> the **MR-Session map** below; they stay readable as a conceptual template for any candidate.

#### Phase 2 pivot — MR-Session map (mean-reversion candidate, from 2026-07-05)

- **MR-Session Alpha (Pivot A+B) — DONE 2026-07-05** — ADR-012 (bghtrend no-go),
  ADR-013 + new `WF_ELIGIBILITY_THRESHOLDS` constant (pre-WF filter calibration 1.5→1.0),
  regime-robustness sanity-check step in `running-sweep.md`. (This session.)
- **MR-Session Beta (Selection + Implementation start) — DONE 2026-07-10** — new strategy
  `algo_bot/strategies/mean_reversion_bb_stoch.py` (contrarian **both-directions**: touch of a
  Bollinger band → armed → first *reaction* candle enters; TP = opposite **live** band;
  **fixed 2% SL**; **Stochastic** 14/3/3 as an `entry_mode ∈ {bb_only, bb_stoch}` sweep
  dimension gated at arming; **no** trail/BE/timeout — bare core). `bbands()` + `stochastic()`
  added to `indicators/core.py` (were absent — only `ema/rsi/atr/t3` existed),
  `config/mr_b1..b3.yaml` sweep spaces informed by the Mastermind prior (mastermindzx.pl),
  tests (indicator oracles, params, both-dir entry gates, exit precedence, precompute
  equivalence), legacy `bollinger_band_breakout_short.py` removed, deep-reference DRAFT
  skeleton. Decisions + deferred edge (pyramiding + sequential leverage, sizing, timeout →
  separate ADR) recorded in CHANGELOG [Unreleased]. **Spec reconciled vs the original
  Pivot-A bullet:** `bb_stoch` not `bb_rsi`, both-dir not long-only, Stochastic not RSI,
  fixed 2% SL not 2×ATR, TP = opposite band, no timeout.
- **MR-Session 1 (Audit) — DONE 2026-07-11** — full deep reference + parameter
  taxonomy + Mastermind cross-check (analog to the bghtrend Session 1). Delivered:
  **`docs/references/mms/`** (MMS prior extracted + versioned: README w/ copyright
  note, 4 HIGH + 2 MEDIUM tab files, 33 full-tab screenshots in `raw/`; `09-backtests`
  pending source screenshots); `strategy-mean-reversion-bb-stoch.md` **DRAFT → FULL**
  (hybrid, parameter taxonomy w/ overfitting watchlist ⚠ `bb_num_std` steps / Stoch
  thresholds / `arm_expiry_bars` / `require_reclaim`, 16-row Mastermind alignment
  table citing mms/ per row, MR-Session 2 interpretation caveat, known limitations);
  `indicators-bbands.md` + `indicators-stochastic.md` deep references; independent
  oracle pass (unpack order / gate-at-arming / SL-first precedence verified clean)
  mechanised into `TestAuditSeams` (3 state-machine seams) + a hand-derivable bbands
  identity test; `require_reclaim` docstring drift fixed (zero behaviour change — no
  real bug found); package-overview/ARCHITECTURE/config-reference drift fix (Beta
  follow-up closed). Key finding for MR-Session 2: MMS uses Stochastic as the
  **add-on (pyramiding) filter** (%K&%D cross), not a base-entry gate — Beta's
  `entry_mode` is a deliberate adaptation; and MMS itself expects the bare core to
  bleed in strong trends, so a weak sweep ≠ methodology falsified (the deferred
  sizing layer is the claimed edge).
- **MR-Session 2 (Sweep) — DONE 2026-07-13** — matched-TF sweep on `mr_b1..b3`
  under `WF_ELIGIBILITY_THRESHOLDS` + rolling per-year regime robustness.
  - [x] Task 0: MMS-consistent `entry_mode="bb_only"` frozen in b1/b3; b2 retains
    the empirical `bb_only` vs `bb_stoch` split.
  - [x] Six sequential VPS/tmux sweeps: BTC/ETH × b1/1h, b2/1h, b3/15m,
    30 samples each (180 total), full microstructure and real historical funding.
  - [x] `notebooks/04_mr_sweep_review.ipynb`: prior recorded before results;
    heuristics A–G, imported eligibility gate, b2 entry-mode split, top-3/year
    regime check and bankruptcy-safe post-cost audit.
  - [x] Evidence export: `results/experiments/mr_sweep_review.json`.
  - [x] Verdict: **bare core FAILS** — 0/180 eligible; best raw Sharpe −0.291;
    169/180 post-cost curves reach equity ≤ 0 and best valid Sharpe_post is −0.497.
    This does not test/falsify the deferred MMS pyramiding + sequential-sizing edge.
- **MR-Session 3 Alpha (Engine migration ADR) — DONE 2026-07-13** — do **not** run
  WF on the failed bare core. [ADR-014](adr/014-engine-migration-nautilus.md):
  adopt `nautilus_trader` as the primary engine for event-driven / state-machine
  strategies, **coexisting in parallel** with `backtesting.py` (a pinned legacy baseline
  for single-position strategies). Nine decisions formalised: two-tier adapter
  (compat shim + native), coexistence dispatch, **M5 deferred** (deferred edge is
  H1-native — fidelity not capability), `StrategyBase` frozen, adapter returns the
  `(stats, equity, trades)` contract (WF/sweep facade unchanged; Nautilus uses native fills,
  commissions and funding rather than extending ADR-011 to multiple legs), staged completion
  criteria, time+capability bailout tripwires (`vectorbt` can express the state machine via
  callbacks but has no live path), and a pinned legacy baseline whose retirement requires a
  separate decision.
  Migration justified independently of the MMS rescue (capability upgrade for future
  event-driven candidates). Plus `docs/concepts/engine-migration-strategy.md` (DRAFT).
  Zero code. (This session.)
- **MR-Session 3 Beta (nautilus PoC + `mean_reversion_bb_stoch` v2) — DONE 2026-07-13; `ITERATE BETA`** —
  - [x] **Beta 0 runtime hard gate:** vanilla CPython **3.12.13** + `uv==0.11.28`,
    `nautilus_trader==1.230.0`, `TA-Lib==0.7.0`, `backtesting==0.6.5`, committed
    `uv.lock`; full `make check` green (**282 passed, 1 live-network test skipped**).
    Conda fallback was not needed. Result `engine`/version/hash metadata remains in P8.
  - [x] Executable v2 spec → timestamp/execution PoC → OMS/position PoC (hard gates),
    Tier-1 compat adapter + cross-engine equivalence test, pure P6 state machine,
    thin P7 PyO3 wrapper and rich P8 `BacktestResult`.
  **v2 = pure `MastermindStateMachine` (bez importów nautilusa) + cienki
  `NautilusMastermindStrategy`** (ADR-014 Decision 1); logika: base entry
  (H1 armed→reaction jak v1) + **pyramiding** (base x1 + **jedna** dokładka x1 =
  x2 total; **dwa alternatywne triggery**: świeca potwierdzająca **lub** Stoch %K&%D
  cross; add-on SL wick-pair ≤1%; cap 3% equity — [mms/02](references/mms/02-position-management-filters.md))
  + **binary sequential leverage** (pierwszy pełny 2% SL → x0.1 scout → pierwszy TP → x1 —
  [mms/03](references/mms/03-stop-loss-sequential.md)); position model = **virtual legs
  over NETTING + reduce-only conditional stops** (ADR-014 Decision 9, do walidacji w PoC).
  Zamrożony development-only P9 na H1 wykonał **12/12** runów i **264/264**
  kontroli invariantów; holdout nie został odczytany. Wszystkie wyniki są
  `SMOKE_ONLY / NOT_ELIGIBLE`, więc nie służą do rankingu ani wyboru parametrów.
  Raport: [mms-v2-beta-results.md](experiments/mms-v2-beta-results.md).
  Decyzja post-PoC: **iterate Beta** — mechanika działa, lecz nie spełnia jeszcze
  warunków pełnego sweepu.
- **MR-Session 3 Beta Iteration 1 (Exchange migration Binance→Bybit) — DONE 2026-07-14** —
  pierwszy z 4 blockerów przed Session 4 rozwiązany: **venue = Bybit** (Binance nieaktywny
  w UE dla Janka; Bybit account aktywny + prior MMS na Bybit). [ADR-015](adr/015-exchange-migration-bybit.md):
  Bybit v5 linear USDT perp dla pracy forward, Binance CSV zostają jako pinned reference.
  Dostarczone: `--exchange {binance,bybit}` w fetch/funding/process + backtest/sweep/walkforward
  (per-exchange `EXCHANGE_DEFAULTS`: bybit taker 0.00055), CCXT `bybit_adapter.py` (One-Way,
  reduce_only, TP/SL trading-stop), testnet-first `algo_bot/live/live_bybit.py` z
  `close_all_positions()` (Close-All parity: cancel-all → market reduce-only close), M10
  offline z M5. Native `nautilus_trader.adapters.bybit` zweryfikowany (dostępny w 1.230.0,
  zarezerwowany dla backtest lane). ADR-014 §9 / ADR-011 / ADR-005 uzupełnione notami Bybit.
  Dane historyczne fetchowane operator-side na VPS (Bybit linear inception ~2020-03-25).
- **MR-Session 3 Beta Iteration 2 (native fills evidence + mark-price + M5/M10 fidelity) —
  DONE 2026-07-15** — zaakceptowane `1a/2a/3R/4R/5c/6a/7R`. Dostarczone:
  pełne H1 mark-price Bybit dla BTC/ETH na dokładnym overlapie OHLCV (zero gapów,
  bez fillowania); isolated liquidation wg risk tieru, jako hard-stop i ujemny outcome;
  ortogonalne `FillMethod`/`MarginMethod` w `BacktestResult` schema v2 z migracją P9
  tylko in-memory; generic `MarkingBarClosed` M5/M10 porównujący pierwszy wick touch z
  ostatnimi ukończonymi H1 Bands; natywne multiple `BarType` w wrapperze i porządek
  all marking sub-bars → H1 execution. P9 pozostaje niezmienione i niekwalifikowalne.
- **MR-Session 4 (full v2 sweep on nautilus) — REVISION 2 PREFREEZE AFTER
  OUTCOME-BLIND PILOT DEFECT** — kontrakt obejmuje **BTCUSDT i ETHUSDT osobno**, H1 execution
  oraz marking **M5 i M10** (528 runów); H1-only pozostaje poza rodziną inferencyjną.
  Outcome-blind runner, native evidence, funding wyceniany po ukończonym H1 mark Close,
  causal mark-margin proxy, atomic artifacts, bounded retry/resume, canonical tag
  provenance i prerejestracja są gotowe. Revision-1 pilot został uruchomiony, ale
  zatrzymał się przed odczytem metryk na błędnej regule zaokrąglenia oracle prowizji;
  nie jest to wynik ekonomiczny. Funding ETH,
  paginowane instrument/risk-limit contracts i dokładny VPS preflight są zamknięte;
  stary manifest core `657e9101…c08e1a` jest zachowany wyłącznie audytowo. Revision 2
  wymaga nowego quality gate, core hash, clean commita, taga `-r2`, `prepare`/`plan`
  i świeżego czterorunowego pilota przed pełnym sweepem.
  Strategia nie ładuje reserved rows, ale wcześniejszy operator-side integrity audit
  dotknął pełnych plików; dokładne ujawnienie i przyszła polityka holdout są zapisane w
  [zamrożonej prerejestracji](experiments/mr-session-4-preregistration.md).
- **MR-Session 5 (conditional WF → MC → Stress → ADR go/no-go)** — full-MMS-system
  verdict (analogue of the bghtrend Session-8 go/no-go), **gated** on the Session-4
  sweep clearing in-sample eligibility (don't run the expensive robustness layer on a
  strategy that has not shown edge). If v2 fails, formalise the implemented MR-line
  no-go and pivot.

---

**Mapa sesji Fazy 2 — bghtrend line (Sesje 1-4 historical / done; Sesje 5-8 retired per ADR-012)** (oryginalnie 8 sesji, ~16-22h; retained for reference):

### Sesja 1 — Audit strategii + configi + parameter taxonomy

**Cel:** zrozumieć co dokładnie sweep'ujemy w sesji 4 i czemu te zakresy a nie inne. Bez tego sweep b1..b4 to shotgun, nie eksperyment.

**Sub-deliverables (docs):**
- [x] `docs/reference/modules/strategy-bghtrend-pullback.md` — deep walkthrough: teza ekonomiczna, formuły entry/exit, EMA21/89/200 + xtrender + pullback + ATR-trail + cooldown logic, R:R 1:1.5, edge cases — **DONE 2026-06-05** (hybrid format — Decyzja 1)
- [x] `docs/reference/config-reference.md` — kompletny schema YAML: `config/config.yaml` (global) + `config/bghtrend_b1..b4.yaml` (sweep spaces). Co znaczy każdy parametr, jego dimensjonalność, ekonomiczne uzasadnienie zakresu — **DONE 2026-06-05**
- [x] Parameter taxonomy table w strategy reference: core (ekonomiczne uzasadnienie — np. trend window, ATR multiplier) vs tuning (kosmetyczne — np. thresholds, deadzones) — tuning parameters są największym wektorem overfittingu — **DONE 2026-06-05** (3 kategorie core/tuning/ambiguous — Decyzja 2; overfitting watchlist dla Sesji 6 oznaczony)
- [x] `docs/reference/modules/indicators-xtrender.md` — osobny deep reference oscylatora xtrender — **DONE 2026-06-05** (Decyzja 4 — standalone doc zamiast sekcji)

**Sub-deliverables (kod):**
- [x] (opcjonalnie) Ujednolicenie `b1..b4` — **NIE WYKONANE, świadoma decyzja (Decyzja 3/5, 2026-06-05).** Analiza side-by-side wykazała że b1..b4 to principled 2-D design (oś timescale: b3 fast → b1/b2 medium → b4 slow; oś selectivity: b1 strict vs b2 permissive), nie ad-hoc rozjazdy. Configi zostają nietknięte. Dwa odłożone clean-upy udokumentowane w config-reference.md: phantom `zscore_window` (inert bo `slope_mode=pct`), brak jawnego TF-mappingu w nagłówkach YAML (potwierdzić w Sesji 4).

**Prerequisite:** brak. To research session, używa tylko istniejącego kodu.

**Wartość:** każda kolejna sesja Fazy 2 ma punkt odniesienia. Sesja 4 (sweep) wie co jest core, co tuning. Sesja 8 (decyzja MVP) ma materiał do interpretacji "czy ten Sharpe to edge czy luck".

### Sesja 2 — Pobranie danych BTC/ETH od 2019

**Cel:** mieć datasets pod każdą kolejną sesję, jeden raz prawidłowo zrobione.

**Sub-deliverables (kod):**
- [x] `bot_data/processed/binance_BTCUSDT_15m.csv` od 2019-09-08 do bieżąco — **DONE 2026-06-10** (operator run, integrity pass)
- [x] `bot_data/processed/binance_BTCUSDT_1h.csv` — **DONE 2026-06-10**
- [x] `bot_data/processed/binance_BTCUSDT_4h.csv` — **DONE 2026-06-10**
- [x] `bot_data/processed/binance_ETHUSDT_15m.csv` — **DONE 2026-06-10** (ETH perp listed ~2019-11, start naturalnie później)
- [x] `bot_data/processed/binance_ETHUSDT_1h.csv` — **DONE 2026-06-10**
- [x] `bot_data/processed/binance_ETHUSDT_4h.csv` — **DONE 2026-06-10**
- [x] Decyzja: native per TF vs resampling — **DONE 2026-06-10** — wybór: **native per TF** (Decyzja 2; Binance native klines spójne w obrębie giełdy, native volume per TF, zero nowego kodu). Plus Decyzje 1/3: **Binance Futures USDT-M, start 2019-09-08**.
- [x] Sanity check (monotonic, OHLCV invariants, gap detection > 3×TF) — **DONE 2026-06-10** — walidator `algo_bot/data_integrity.py` + `tests/test_data_integrity.py` (gated per (symbol, tf)). **Pass na wszystkich 6 plikach** (`pytest -m integration` zielony, `make check` 171 passed).

**Sub-deliverables (docs):**
- [x] `docs/guides/data-fetching.md` — runbook `algo-fetch` + `algo-process` dla pełnego setu Fazy 2, weryfikacja integralności, resume, troubleshooting — **DONE 2026-06-10**

**Prerequisite:** Sesja 1 (żeby wiedzieć którego TF najpierw potrzebujemy — strategia może mieć "ulubiony" TF z docstringu)

**Wartość:** pierwszy end-to-end test że `algo-fetch` + `algo-process` z ADR-010 działają w realnych warunkach. Plus prerequisite dla każdej kolejnej sesji 4-7.

### Sesja 3 — ADR-011 microstructure adjustments

**Cel:** określić jak slippage (5-10 bps) i funding cost (8h cycle dla perp futures) są aplikowane w backteście, żeby wyniki sweep'a i WF były realistic, nie naive.

**Sub-deliverables (kod):**
- [x] `algo_bot/microstructure.py` — pure functions + frozen dataclasses adjustujące equity/trades o slippage + funding — **DONE 2026-06-19** (ADR-011 Decyzja 1b: nowy moduł top-level, pattern jak `algo_bot.risk`; mypy strict-on-new)
- [x] Decyzja architektoniczna post-hoc vs in-loop — **DONE 2026-06-19** (post-hoc overlay na **equity curve** z jednego runu silnika, nie tylko na trades_pnl — daje realne post-microstructure Sharpe/maxDD; stary cosmetic `adjust_trades_df` wyrwany; ADR-011 §2/§15)
- [x] Decyzja stały slip vs size-aware — **DONE 2026-06-19** (stały `slip_bps`/side, symmetric; default 1.0 bp; size-aware jako future flag; ADR-011 §3/§4)
- [x] Funding rate source — **DONE 2026-06-19** (hybrid: historical CSV + synthetic fallback z WARNING; per-settlement 8h sterowane realnym `fundingTime`; ADR-011 §5/§6/§7; `algo-fetch-funding` + `data_loader.load_funding`)
- [x] CLI flags — **DONE 2026-06-19** (`--microstructure {none,full}` / `--slip_bps` / `--funding_source` / `--funding_rate_synthetic` w `algo-backtest`/`algo-sweep`/`algo-walkforward`)
- [x] `MetricsSummary` extension — **DONE 2026-06-19** (`_metrics_summary_raw` + `_metrics_summary_post_microstructure` w `summary.json`; `Equity_adjusted` w equity + 4 kolumny breakdown w trades; Decyzja 9a/10a)
- [x] `tests/test_microstructure.py` bez mocków — **DONE 2026-06-19** (niezależna wyrocznia arytmetyczna; funding math zreprodukowane z Binance docs; handcomputed literals)

**Sub-deliverables (docs):**
- [x] ADR-011 `docs/adr/011-microstructure-adjustments.md` po angielsku — **DONE 2026-06-19** (format ADR-009; decyzje 1-14 + alternatives + consequences + defaults z published source)
- [x] `docs/concepts/microstructure.md` — **DONE 2026-06-19** (mechanika perp futures Binance, fee vs slippage vs funding, dlaczego ~10 bps round-trip realistic, jak czytać raw vs post)
- [x] `docs/reference/modules/microstructure.md` — **DONE 2026-06-19** (deep reference, wzorzec metrics.md/risk-limits.md; + wzmianki w strategy-bghtrend-pullback.md i walkforward.md)

**Prerequisite:** Sesja 1 (audit strategii pokaże czy bghtrend jest maker-friendly czy taker-only — wpływa na slip model). Opcjonalnie Sesja 2 dla testów na realnych danych.

**Wartość:** każdy backtest od sesji 4 wzwyż może być "post-microstructure". Bez tego "Sharpe 1.5 in-sample" to fairytale gdy Binance bierze 4 bps × 2 (open+close).

### Sesja 4 — In-sample sweep b1..b4 na BTC/ETH × 3 timeframes

**Cel:** pierwsza orientacja: które parametry bghtrend dają sensowny Sharpe na pełnej historii, czy są clustery w parameter space (dobry znak) czy random (zły).

**Sub-deliverables (kod):**
- [x] Sweep runs — **DONE (partial, świadoma decyzja) 2026-07-04.** Scope zmieniony na matched-TF (Decyzja 1a sesji): 8 runów (b1/b2→1h, b3→15m, b4→4h × BTC+ETH), `__n: 30` (nie 5 — za mało statystycznie). Wykonane 5/8 (b3-BTC-15m, b1×2, b4×2); b2×2 i b3-ETH **wstrzymane celowo** po jednoznacznie negatywnym sygnale z 5 grup (patrz Manual review). Bonus sesji: perf fix O(n²) w pętli backtestu (precompute hook, ~20× szybciej — bez tego sweep 15m był infeasible, ETA 43h) + fix parsowania funding CSV (ISO8601).
- [x] `index.csv` — **DONE 2026-07-04** — w `results/experiments/index.csv` (nie `results/sweeps/` — tak działa istniejący kod), rozszerzony o `space_file` + pełen zestaw selekcyjny `{sharpe,calmar,profit_factor,max_drawdown_pct,n_trades}×{raw,post}`. Top-10 per grupa w `results/experiments/sweep_review.json` (generowany przez notebook 03).
- [x] Manual review — **DONE 2026-07-04, wynik NEGATYWNY.** Heurystyki A-E na 5 grupach × 30 sampli: **0 kandydatów po filtrach WF** (sharpe_post>1.5 ∧ PF>1.5 ∧ n_trades>100 ∧ DD>-0.20). Najlepsze top-1 sharpe_post: 0.775 (b4-BTC, ale n_trades=1!), 0.658 (b1-BTC, n_trades=14), b3-15m całe ≤0, b1-ETH prawie całe <0 (cross-symbol fail). Kluczowy pattern: wysokie Sharpe TYLKO przy n_trades rzędu 1-30 (statystycznie puste), sample z setkami trades głęboko ujemne. Per Decyzja 5 kickoffu: brak IS edge → **WF wstrzymany, eskalacja do sesji decyzyjnej pivot/refactor strategii z mózgiem-Claude** (przed Sesją 5).

**Sub-deliverables (docs):**
- [x] `docs/guides/running-sweep.md` — **DONE 2026-07-04** (CLI, schemat index.csv, heurystyki A-E, progi worth-WF)
- [x] `docs/guides/running-backtest.md` — **DONE 2026-07-04**
- [x] `docs/reference/metrics-reference.md` — **DONE 2026-07-04 (DRAFT** — WF-specific interpretation w Sesji 5)
- [x] `notebooks/03_bghtrend_sweep_and_walkforward.ipynb` sekcja "Sweep review" — **DONE 2026-07-04** (PRIOR + heurystyki + zrzut sweep_review.json; sekcje 2-6 czekają na Sesje 5-8)

**Prerequisite:** Sesja 1 (parameter taxonomy), Sesja 2 (dane), Sesja 3 (microstructure — sweep powinien być post-slip).

**Wartość:** orientacja, pierwsze decyzje "co wartość brać do WF". Bez tego WF byłby na losowo wybranym param set.

### Sesja 4b (ad-hoc) — VPS research runner

**Cel:** sweepy/backtesty/WF odpalane na VPS w tmux zamiast na desktopie — komp nie musi być włączony przez wielogodzinne kolejki; docelowo research compute 24/7. Zidentyfikowane w Sesji 4 (2026-07-04), gdy kolejka 8 sweepów zajęła ~6h desktopa.

**Decyzje (5) uzgodnione 2026-07-04** (opcje + trade-offs w CHANGELOG [Unreleased] / `docs/guides/vps-research-runner.md`): (1) env = miniforge + `environment.yml`; (2) źródło danych = rsync `processed/` z PC (powtarzalność > świeżość); (3) równoległość = sekwencyjnie w tmux, flaga `--index_csv` odłożona; (4) transport wyników = rsync `results/` VPS→PC; (5) bezpieczeństwo = świeży read-only deploy key na VPS, zero sekretów. Host: OVH VPS-2, 6 vCore / 12 GB / 100 GB, Ubuntu 22.04 LTS, `os-waw2`.

**Sub-deliverables (kod/ops):**
- [x] Data sync tooling — **DONE 2026-07-04** — `scripts/vps-sync.sh` (`up`/`down`, `--dry-run`, bez `--delete`, bez sekretów) + Makefile targets `sync-up` / `sync-down` (wymagają `VPS_HOST=`).
- [x] Równoległość — **DONE 2026-07-04 (decyzja)** — sekwencyjnie w tmux (Decyzja 3, zero zmian kodu); `results/experiments/index.csv` append nie jest multi-process safe; flaga `--index_csv <path>` per run to świadomie odłożony follow-up (osobny mini-deliverable z testem, gdy sekwencyjnie zacznie boleć na 6 vCore).
- [x] `docs/guides/vps-research-runner.md` — **DONE 2026-07-04** — thin runbook (EN): miniforge, read-only deploy key + clone, `make env`/`make install`/`make check` smoke, sync-up/down, tmux, end-to-end smoke sweep, anti-patterns.
- [x] Env na VPS: klon repo (deploy key) + `conda env create -f environment.yml` + smoke `make check` — **DONE 2026-07-04 (operator run).** miniforge już był na hoście; klon przez świeży read-only deploy key; `make check` **zielony (208 passed, 8 skipped)** po fixie env-drift (`walkforward.py:614` cast pod nowszy pandas-stubs — CHANGELOG Fixed 4b). `requirements.txt` lockfile zacommitowany; na VPS aplikowany przez `pip install -r` (nie `make sync` — destrukcyjny na conda/TA-Lib).
- [x] Smoke test: krótki `algo-sweep` w tmux + `sync-down`, wiersz wraca na PC — **DONE 2026-07-04 (operator run).** b4/BTC/4h, 2024-01→06; sweep completed w tmux, `sync-up` (dane) + `sync-down` (wyniki) OK, wiersze dopisane do `index.csv` i ściągnięte na PC. `n_trades=0` w tym oknie (metryki NaN) — bez znaczenia, to weryfikacja hydrauliki (env→dane→compute→transport), nie research. Backtesty merytoryczne nadal wstrzymane do decyzji pivot.

**Prerequisite:** Sesja 4 (żeby wiedzieć ile compute realnie potrzeba). Nie blokuje Sesji 5 — WF na top-kandydatach może iść jeszcze na desktopie.

**Wartość:** research odklejony od włączonego PC; przygotowuje grunt pod VPS Fazy 5 (ten sam host może służyć za live runner później).

### Sesja 5 — Walk-forward bghtrend na top params + Notebook 03 — RETIRED (ADR-012 pivot; template for MR-Session 3)

**Cel:** serce Fazy 2. Walk-forward (minimum 5 fold, np. train 12m / test 3m / step 3m) na 2-3 najlepszych konfiguracjach z sesji 4. Pierwsza próba pass/fail kryteriów MVP.

**Sub-deliverables (kod):**
- [ ] `algo-walkforward` runs dla każdej top configuration (2-3 setów params × 6 (symbol, TF) = 12-18 WF runs)
- [ ] `results/walkforward/<wf_run_id>/` z pełnym output (walkforward_summary.json, folds.csv, distribution.csv, stitched equity)
- [ ] `compute_mvp_pass` evaluation per WF run (z ADR-009 — sharpe ≥ 1.0, max DD ≥ -0.25, PF ≥ 1.3, n_trades ≥ 50)
- [ ] `notebooks/03_bghtrend_walkforward_analysis.ipynb` — pełna analiza: per-fold metryki, equity stitched, IS vs OOS spread, boundary closes statistics, distribution wykres

**Sub-deliverables (docs):**
- [ ] `docs/guides/walk-forward-howto.md` — krok-po-kroku od `algo-walkforward` do interpretacji wyniku, kiedy fold count jest za mały, jak czytać `mvp_threshold` row
- [ ] `docs/concepts/walk-forward.md` — rozbudowa thin doc z Fazy 1 (Decyzja F) o praktyczne lessons z bghtrend WF: jak interpretować IS/OOS spread w kontekście tej konkretnej strategii

**Prerequisite:** Sesja 4 (top params z sweep'a)

**Wartość:** podstawowa walidacja MVP. Po tej sesji wiemy czy bghtrend ma szansę, czy iterujemy.

### Sesja 6 — Monte Carlo bootstrap + parameter stability heatmap — RETIRED (ADR-012 pivot; template for later MR-Session)

**Cel:** robustness checks. Czy WF Sharpe to edge czy lucky path? Czy strategia jest wrażliwa na małe zmiany parametrów?

**Sub-deliverables (kod):**
- [ ] `algo_bot/diagnostics/monte_carlo.py` (lub w `notebooks/`) — funkcje shuffling trade sequence, computing distribution of final equity / max DD / Sharpe per shuffle. 10 000 iteracji minimum
- [ ] `algo_bot/diagnostics/parameter_stability.py` — sąsiedzkie param sets (±1 step w 2 wymiarach), heatmap Sharpe / max DD
- [ ] Wyniki w `notebooks/03_bghtrend_walkforward_analysis.ipynb` (rozszerzenie z sesji 5): bootstrap percentyle (5/50/95) dla każdej metryki, heatmapa parameter stability

**Sub-deliverables (docs):**
- [ ] `docs/concepts/backtest-robustness.md` — Monte Carlo trade shuffling vs price bootstrap, parameter stability jako overfitting smell, Lopez de Prado Deflated Sharpe / PBO
- [ ] `docs/reference/modules/diagnostics.md` jeśli osobny pakiet `algo_bot.diagnostics`

**Prerequisite:** Sesja 5 (WF wyniki + trade journal)

**Wartość:** weryfikacja czy MVP pass z sesji 5 to nie szczęście. Jeśli realny max DD jest w 5% lewym ogonie bootstrap distribution → trzeba przemyśleć. Heatmap stability mówi czy strategia jest "robust plateau" czy "knife edge".

### Sesja 7 — Stress test na 4 reżimach — RETIRED (ADR-012 pivot; template for later MR-Session)

**Cel:** czy strategia przeżyła historyczne katastrofy. Trend-following typowo radzi sobie z grindującymi bear markets ale dostaje po głowie od V-shape recovery i mass liquidation events.

**Sub-deliverables (kod):**
- [ ] Backtests na 4 izolowanych okresach: 2018-12 (BTC -50% w 30 dni), 2020-03 (covid mass liquidation, March 12-13), 2022-06 (Luna/Terra collapse), 2022-11 (FTX implozja). Konkretne daty start/end per regime do zdefiniowania w sesji
- [ ] Per-regime metryki: max DD w okresie, n_trades, win_rate, czy strategia weszła w short side w czasie krachu (trend-following zalety) czy zaknięty long bag (catastrophic)

**Sub-deliverables (docs):**
- [ ] Sekcja w `notebooks/03_bghtrend_walkforward_analysis.ipynb` — per-regime performance summary
- [ ] `docs/concepts/regime-tests.md` lub sekcja w `concepts/backtest-robustness.md` — czemu te 4 okresy, jakie są typowe failure modes trend-following w każdym

**Prerequisite:** Sesja 2 (dane obejmujące 2018-12; sprawdzić że Binance USDT-M perpetual ma dane tak daleko wstecz, jeśli nie — alternatywa Bybit lub OKX)

**Wartość:** sanity check. Strategia która przeżyła wszystkie 4 reżimy jest gotowa na testnet. Strategia która umarła w 2020-03 nie idzie na żywy kapitał bez review.

### Sesja 8 — Decyzja MVP go/no-go + ADR-012 — RETIRED (ADR-012 delivered early 2026-07-05 as the bghtrend NO-GO, not a go; template for MR-Session go/no-go)

**Cel:** formalna decyzja: bghtrend w obecnej formie idzie do Fazy 3 (testnet/paper) lub iteracja parametrów / strategii.

**Sub-deliverables (docs):**
- [ ] ADR-012 `docs/adr/012-mvp-go-no-go-decision.md` po angielsku — analiza WF wyników, Monte Carlo, parameter stability, stress tests. Pass/fail per kryterium MVP. Final decision z uzasadnieniem.
- [ ] CHANGELOG entry v0.2.0 — full set Fazy 2 deliverables
- [ ] Update `docs/ROADMAP.md` Fazy 3 z konkretami (param set zafrozowany na testnet, na ile długo, jakie alerty)

**Sub-deliverables (kod):**
- [ ] Git tag `v0.2.0-phase2-complete` (lub `mvp-go` / `mvp-iterate` jeśli decyzja jest iteracja)

**Prerequisite:** wszystkie poprzednie sesje

**Wartość:** czyste wyjście z Fazy 2, brama do Fazy 3.

### Decyzje architektoniczne planowane w Fazie 2

| ADR | Sesja | Decyzja |
|---|---|---|
| ADR-011 | 3 | Microstructure adjustments — gdzie i jak |
| ADR-012 | Pivot A (2026-07-05) | MVP **no-go** dla bghtrend (nie "go" — sweep bez edge'u); kept-as-baseline |
| ADR-013 | Pivot A (2026-07-05) | WF-eligibility thresholds — pre-WF filter `WF_ELIGIBILITY_THRESHOLDS` (przeniesienie z pierwotnego "diagnostics package"; diagnostics wróci jako osobny ADR w MR-cyklu jeśli wydzielimy `algo_bot.diagnostics`) |
| ADR-014 | MR-Session 3 Alpha (2026-07-13) | Engine migration to `nautilus_trader` — parallel coexistence with `backtesting.py`; primary engine for event-driven / state-machine strategies (first user: `mean_reversion_bb_stoch` v2 with pyramiding + sequential leverage); legacy baselines frozen on `backtesting.py` |

### Kryteria sukcesu MVP (z ADR-009 `MVP_THRESHOLDS`, hardcoded w `algo_bot.engine.walkforward`)

- Walk-forward Sharpe ≥ 1.0
- Walk-forward max DD ≥ -0.25 (czyli max DD ≤ 25%)
- Profit factor ≥ 1.3 w OOS
- Liczba trade'ów w OOS ≥ 50 (statystyczna istotność)

**Plus jakościowe** (nie hardcoded, manualne sprawdzenie w sesji 8):
- Stabilność parametrów: ±20% wartości parametru → ±15% wyniku metryki
- Strategia przeżyła wszystkie 4 stress regimes
- Bootstrap 5-95 percentyl dla final equity nie obejmuje -100% (catastrophic ruin)

### Ryzyka

- **Overfitting na sweepie** — mitygacja: walk-forward (sesja 5) + Monte Carlo (sesja 6) + parameter stability (sesja 6). Plus świadomość Deflated Sharpe / PBO przy interpretacji.
- **Survivor bias** — mitygacja: tylko BTC/ETH (no survivor bias issue dla tych dwóch).
- **Funding rate cost niedoszacowany** — mitygacja: ADR-011 używa rzeczywistych historycznych funding rates jeśli dostępne.
- **Boundary closes degenerated WF** — jeśli `algo-walkforward` log pokazuje że dominują force-closes na granicach foldów, test window jest za krótkie vs typowy trade duration bghtrend. Mitygacja: ADR-009 boundary close detection już to flaguje.
- **Brak historycznych danych 2018-12** — Binance Futures uruchomiony 2019-09, więc 2018-12 stress test może wymagać spot data (z `binance` zamiast `binance.future`) lub alternatywnej giełdy. Decyzja w sesji 7.

### Tail-end follow-ups (code health)

Drobne znaleziska z Sesji 1 (2026-06-05) audytu strategii i configi. Żadne nie blokuje Sesji 2-8 Fazy 2, ale do zamknięcia przed Fazą 3 (testnet) — kod produkcyjny nie powinien iść na żywe pieniądze z silent fail modes ani phantom parameters. Naturalna home: CHANGELOG entries, nie ADR-y (to cleanup, nie architektura).

- [x] **Phantom `zscore_window` parameter** w `config/bghtrend_b1..b4.yaml` — **DONE 2026-06-11** (sesja cleanup; usunięty z 4 configów, default dataclass (100) i uśpiona gałąź `_slope_zscore` zostają; ponowne otwarcie `slope_mode=zscore` wymaga ADR).
- [x] **Implied-TF mapping nie encoded w YAML headers** — **DONE 2026-06-11** (`__implied_tf` meta-key + komentarz nagłówka w b1..b4: b1/b2→1h, b3→15m, b4→4h; `algo-sweep` WARNING na mismatch vs `--timeframes`, run leci dalej — świadome cross-TF możliwe).
- [x] **`config.yaml` cash/commission drift vs `algo-sweep` CLI defaults** — **DONE 2026-06-11** (rozstrzygnięcie INNE niż sugestia: audit wykazał że ŻADEN kod nie czyta `config.yaml`, więc SoT to stałe `DEFAULT_CASH`/`DEFAULT_COMMISSION` w `backtester.py` importowane przez wszystkie 3 CLI; `config.yaml backtest:` jest informacyjnym lustrem; commission 0.002→0.0004 fix).
- [x] **EMA monotonicity validation runtime** — **DONE 2026-06-11** (wariant `__post_init__` na `XtrenderPullbackParams` — szerszy niż `__init__` strategii, łapie też sweep/walkforward przez `coerce_params`; `ValueError` z czytelnym message; `tests/test_bghtrend_params.py`).
- [x] **Xtrender code health** — **DONE 2026-06-11** (3 z 3; trzeci sub-item przerodził się w decyzję rozstrzygniętą tego samego dnia — patrz niżej):
  - [x] Docstring drift: zsynchronizowany do 5-tuple z opisem per pozycja.
  - [x] Standalone tests: `tests/test_xtrender.py`, 8 testów (first-principles oracle, literal −50 dla stałej ceny, dots na V-shape, wiring, kontrakt API).
  - [x] `long_term` — **NIE jest dead code.** Znalezisko sesji: audyt Sesji 1 błędnie odczytał unpack — `x_long` w strategii wiąże `long_term` (pozycja 1), nie `short_t3`, i to `long_term` jest faktycznym filtrem entry + stale-exit. Docs poprawione z flagą DISCREPANCY. Nic nie usunięto.
- [x] **PARKED DECISION: `x_long` = `long_term` vs docstring "short_t3"** — **RESOLVED 2026-06-11 (ta sama sesja): kod = intencja.** Janek dostarczył oryginalny Pine Script (B-Xtrender @Puppytherapy) — `longTermXtrender` to komponent "B-Xtrender Trend" (reżimowy gate), linia T3 z kropkami daje timing; dokładnie tak strategia tego używa (entry gate na `long_term`, dots na in-profit exit). Plus nazwy zmiennych mapują 1:1 na pozycje tupli (nie off-by-one) i sens ekonomiczny (filtr short-term na pullbacku blokowałby właściwe wejścia). Bug był w docstringu strategii (poprawiony) + propagacja przez audyt Sesji 1 do docs (poprawione: strategy-bghtrend-pullback.md, indicators-xtrender.md, glossary Deadzone). Zero zmian zachowania — dotychczasowe backtesty ważne, git archeology niepotrzebna.

---

## Faza 3 — Paper / Testnet MVP

**Cel:** strategia działa identycznie w live jak w backteście. Wszystkie różnice są zrozumiane i udokumentowane.

**Deliverables (kod):**
- [ ] Paper trading mode (lokalny symulator, bez wysyłania orderów) — strategia konsumuje real-time WebSocket data, generuje sygnały, journal jak gdyby były trade'y
- [ ] Testnet run (Binance Futures testnet) — minimum 2 tygodnie ciągłej pracy
- [ ] Sprawdzenie zgodności sygnałów backtest vs live: re-backtest na danych z okresu testnetu i porównać sygnały bar-by-bar
- [ ] Alerty (Telegram bot): entry, exit, SL hit, błąd systemu, drift w PnL
- [ ] Recovery: bot się crashuje → restart → wczytuje state z journalu → kontynuuje (nie otwiera duplikatów pozycji)
- [ ] Idempotentność orderów (client_order_id) — nie da się otworzyć dwóch identycznych pozycji
- [ ] Refaktor `live/live_binance.py` → `algo_bot/live/binance.py` + CLI entry `algo-live`

**Deliverables (docs):**
- [ ] `docs/guides/live-trading-checklist.md` — pre-flight checklist (API keys, .env setup, testnet config)
- [ ] `docs/guides/paper-trading.md` — jak odpalić paper mode, jak interpretować output
- [ ] `docs/reference/modules/live-binance.md` — deep reference (flow, recovery, edge cases)
- [ ] `docs/concepts/backtest-live-mismatch.md` — taksonomia różnic (knoty, slippage, funding, timing) i jak je adresujemy
- [ ] `docs/guides/telegram-alerts-setup.md` — jak skonfigurować bota Telegram + .env vars
- [ ] ADR per znaczące decyzje (format alertów, recovery strategy)
- [ ] CHANGELOG entry v0.3.0

**Kryteria sukcesu:**
- 2 tygodnie testnetu bez crash'a
- Sygnały testnet vs backtest na tych samych barach: 100% zgodności (jeśli nie — bug)
- Telegram dostaje wszystkie krytyczne eventy

**Ryzyka:**
- Klock między backtestem i live: backtest używa zamkniętych świec, live musi czekać na ich zamknięcie — `wait_for_next_close` jest już zaimplementowane, trzeba sprawdzić edge cases (gap'y, ws disconnects)
- Funding rate w trakcie pozycji — uwzględnić w PnL live

---

## Faza 4 — Live Mainnet MVP (małe pieniądze)

**Cel:** bot zarabia lub traci kontrolowanie na prawdziwym kapitale przez minimum 1 miesiąc.

**Pre-flight checklist:**
- [ ] Strategia + parametry zafrozowane (commit + tag w git)
- [ ] Kapitał startowy: $100-500 (akceptowalna strata to "cena lekcji", nie ból finansowy)
- [ ] Maksymalny dzienny loss limit aktywny (np. 5% equity)
- [ ] Maksymalny total drawdown auto-stop (np. 15% equity → bot się wyłącza, alert do Telegram)
- [ ] Klucz API z permisjami tylko trading (no withdrawal), IP whitelist na VPS
- [ ] 2FA aktywne na koncie giełdowym
- [ ] Backup planu: jak wyłączyć bota awaryjnie (kill switch z Telegrama: `/stop` → graceful shutdown z zamknięciem pozycji)
- [ ] Dokumentacja DR (disaster recovery): co robić jak VPS padnie, jak ręcznie zamknąć pozycje przez giełdę

**Deliverables (kod):**
- [ ] Switch konfiguracyjny `mode: testnet|mainnet` (jeden config, łatwy toggle)
- [ ] Reconciliation: codzienny report — equity z giełdy vs equity z journala (różnica → alert)
- [ ] Daily report do Telegrama: PnL dzienny/tygodniowy/total, liczba trade'ów, win rate, exposure
- [ ] Weekly retrospekcja (manualnie) — porównaj live performance vs backtest baseline z tego okresu

**Deliverables (docs):**
- [ ] `docs/guides/going-live-mainnet.md` — full procedure od testnetu do mainnetu z confirmation prompts
- [ ] `docs/guides/disaster-recovery.md` — co robić gdy bot padnie/VPS umrze/exchange hack
- [ ] `docs/concepts/risk-management-production.md` — sizing rules, kill switches, daily loss limits w praktyce
- [ ] `docs/reference/journal-format.md` — pełen format trades.csv + equity.csv (kolumny, jednostki, edge cases)
- [ ] Weekly retrospekcja template (w docs/guides/ albo notebook)
- [ ] CHANGELOG entry v0.4.0
- [ ] ADR finalny: "MVP go-live decision" (kryteria pass/fail, sign-off)

**Kryteria sukcesu:**
- 1 miesiąc bez krytycznych błędów
- Live PnL nie odbiega więcej niż 30% (relative) od backtest baseline na tym samym okresie
- Bot przeżył minimum jeden niespodziewany event (CPI release, FOMC, etc.) bez crashu

**Ryzyka:**
- Likwidacja: leverage 3x + krach → szybka likwidacja. Mitygacja: niska dźwignia, mały sizing, SL realistyczny
- Hack giełdy: tylko mała kwota na koncie tradingowym, reszta cold wallet

---

## Faza 5 — Production na VPS

**Cel:** bot chodzi 24/7 bez nadzoru z odpornością na zwykłe failure modes.

**Deliverables (kod):**
- [ ] Dockerfile dla aplikacji + docker-compose (bot + prometheus + grafana + alertmanager)
- [ ] Systemd unit (alternatywa dla Dockera dla minimalistycznego setupu)
- [ ] VPS setup playbook (Ansible lub bash script): firewall, unattended-upgrades, fail2ban, swap, log rotation
- [ ] Monitoring stack:
  - Prometheus exporter w bocie (custom metrics: pnl, positions, last_signal_age, ws_disconnects, api_errors)
  - Grafana dashboard z 8-12 kluczowymi metrykami
  - Alertmanager → Telegram (alerty na: bot down, ws disconnect > 5 min, drawdown > X, PnL anomaly)
- [ ] Strukturalne logi → `/var/log/algo_bot/` z rotacją, opcjonalnie sink do Loki
- [ ] Automated backup journalu i configów (rsync do drugiego serwera albo S3 daily)
- [ ] Healthcheck endpoint (`/health` na localhost) + cron-based watchdog (jeśli health == fail → restart)
- [ ] Deployment workflow: lokalny push → CI → SSH deploy → restart service (lub `docker-compose pull && up -d`)

**Deliverables (docs):**
- [ ] `docs/guides/deploying-to-vps.md` — step-by-step deployment guide
- [ ] `docs/guides/vps-maintenance.md` — daily/weekly/monthly tasks na VPS
- [ ] `docs/guides/troubleshooting.md` — comprehensive: bot down, ws disconnect, PnL drift, etc.
- [ ] `docs/reference/monitoring-metrics.md` — wszystkie Prometheus metrics + Grafana dashboard guide
- [ ] `docs/reference/alerts-reference.md` — wszystkie typy alertów + severity + action items
- [ ] `docs/concepts/operations.md` — operational philosophy (defensive defaults, fail-safe modes)
- [ ] **MkDocs migration** (decyzja G follow-up po MVP) — przeniesienie docs do MkDocs + Material z mkdocstrings, deploy na GitHub Pages
- [ ] CHANGELOG entry v1.0.0 — pełen MVP done

**Kryteria sukcesu:**
- Bot chodzi 30 dni bez ręcznej interwencji
- Uptime > 99.5%
- Wszystkie alerty (Telegram) docierają w < 2 min od eventu
- Disaster: zabicie procesu → systemd/docker restart w < 30s, bot odzyskuje state

**Ryzyka:**
- Drift między backtestem a live z czasem (regime change). Mitygacja: weekly retrospekcja, re-walk-forward co kwartał, gotowość do wyłączenia bota
- VPS provider down → mitygacja: drugi VPS gotowy do hot-takeover (manualnie OK na MVP, automated później)

---

## Po MVP — kierunki rozwoju

- **Portfolio z 2-3 strategii nieskorelowanych** (np. trend-following BTC + mean-reversion ETH + funding arb)
- **Migracja silnika backtestowego**: ~~decyzja po MVP~~ → **rozpoczęta w Fazie 2** ([ADR-014](adr/014-engine-migration-nautilus.md), 2026-07-13): `nautilus_trader` jako primary engine dla strategii event-driven / state-machine, parallel coexistence z `backtesting.py`. `vectorbt` (super szybkie, multi-asset; state machine da się przez callbacki `from_order_func`, ale mniej czytelnie i **bez live path**) pozostaje kandydatem na *sweep-speed* po MVP, nie na pyramiding ani backtest-live parity
- **Live execution na wielu giełdach**: Bybit, OKX, dYdX (już jest `binance_ws.py` i `bybit_testnet.py` scaffolding)
- **Smart order routing**: TWAP/VWAP, iceberg orders dla większego kapitału
- **Research pipeline**: notebooki + literatura + zlecone strategie z papers, np. funding arb, basis trade

---

## Decyzje architektoniczne podjęte na ten moment

| Decyzja | Co | Dlaczego |
|---|---|---|
| API strategii | `StrategyBase` + `Signal`, `on_bar(df) -> Signal` | Jedno API dla backtestu i live — strategia agnostyczna |
| Silnik backtestowy | Zostajemy przy `backtesting.py` do MVP | Działa, koszt migracji za duży zanim zarobimy pierwszy $ |
| Giełda na start | Binance Futures (USDT-M perpetual) | Najlepsza płynność, niskie fee, dobre API |
| Język | Python 3.10+ | Cały istniejący kod, ekosystem TA-Lib, pandas, ccxt |
| Tryby TP/SL | server / local / hybrid (już zaimpl.) | server zawodzi przy "knotach" na testnecie, hybrid pragmatyczny |
| Kwantyfikacja ryzyka | % equity per trade + max DD stop | Pozycja w USDT skaluje się z portfelem, sztywne USDT nie |
| Deployment | VPS (single-node) na MVP | Prosty, tani, wystarczy. K8s/multi-region dopiero przy skali |

---

*Dokument żywy. Po każdej fazie aktualizujemy kryteria i decyzje. Wersja: 0.2 — 2026-05-21.*

**Konwencja językowa dokumentacji (od 2026-05-21):** nowe dokumenty (ADR, guides, reference, captains-log, concepts) piszemy **po angielsku** — oszczędza tokeny i wyrównuje konwencję z public API. Istniejąca dokumentacja PL pozostaje bez zmian; migracja na EN zostanie zaplanowana jako oddzielny deliverable w przyszłej fazie (best-effort, nie blocking). Docstringi w kodzie pozostają PL zgodnie z `feedback_engineering_mindset` (reguła #5 — to wewnętrzna konwencja kodu, nie publiczna dokumentacja).
