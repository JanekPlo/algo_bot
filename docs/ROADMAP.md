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
- [x] `docs/reference/modules/<modul>.md` dla każdego NOWEGO modułu — deep reference. Stan: `metrics.md` (Decyzja D), `risk-limits.md` (Decyzja E), `log.md` (sesja wykończeniowa 2026-05-24), `walkforward.md` (Decyzja F). — **DONE 2026-05-25** (4/4 nowe moduły Fazy 1 mają deep reference)
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

**Wybór strategii kandydatki:** **bghtrend_pullback** — jest najbardziej dopracowana, ma sensowną tezę ekonomiczną (trend + pullback z xtrenderem jako filtrem momentum), ma cooldown i ATR-trail (już zaimplementowane).

**Kontekst:** Faza 1 zamknięta 2026-05-25 (Decyzje A-G + C/D/E/F + ADR-010 CI). Foundation kompletna — `algo_bot.metrics`, `algo_bot.risk`, `algo_bot.engine.walkforward`, logging, CI, pre-commit, wszystkie CLI entries. Wchodzimy w research / walidację strategii. Sesje stają się dłuższe (notebooki, analiza, interpretacja statystyczna) niż w Fazie 1, ale mniej decyzji architektonicznych skali D/E/F — większość pracy to operacje na istniejącym frameworze.

**Mapa sesji Fazy 2** (8 sesji, ~16-22h pracy łącznie, planowo 2-4 tygodnie):

### Sesja 1 — Audit strategii + configi + parameter taxonomy

**Cel:** zrozumieć co dokładnie sweep'ujemy w sesji 4 i czemu te zakresy a nie inne. Bez tego sweep b1..b4 to shotgun, nie eksperyment.

**Sub-deliverables (docs):**
- [ ] `docs/reference/modules/strategy-bghtrend-pullback.md` — deep walkthrough: teza ekonomiczna, formuły entry/exit, EMA21/89/200 + xtrender + pullback + ATR-trail + cooldown logic, R:R 1:1.5, edge cases
- [ ] `docs/reference/config-reference.md` — kompletny schema YAML: `config/config.yaml` (global) + `config/bghtrend_b1..b4.yaml` (sweep spaces). Co znaczy każdy parametr, jego dimensjonalność, ekonomiczne uzasadnienie zakresu
- [ ] Parameter taxonomy table w strategy reference: core (ekonomiczne uzasadnienie — np. trend window, ATR multiplier) vs tuning (kosmetyczne — np. thresholds, deadzones) — tuning parameters są największym wektorem overfittingu

**Sub-deliverables (kod):**
- [ ] (opcjonalnie) Ujednolicenie `b1..b4` jeśli analiza wykryje rozjazdy (np. dwa configi sweepujące te same parametry z różnymi zakresami bez uzasadnienia)

**Prerequisite:** brak. To research session, używa tylko istniejącego kodu.

**Wartość:** każda kolejna sesja Fazy 2 ma punkt odniesienia. Sesja 4 (sweep) wie co jest core, co tuning. Sesja 8 (decyzja MVP) ma materiał do interpretacji "czy ten Sharpe to edge czy luck".

### Sesja 2 — Pobranie danych BTC/ETH od 2019

**Cel:** mieć datasets pod każdą kolejną sesję, jeden raz prawidłowo zrobione.

**Sub-deliverables (kod):**
- [ ] `bot_data/processed/binance_BTCUSDT_15m.csv` od 2019-01 do bieżąco
- [ ] `bot_data/processed/binance_BTCUSDT_1h.csv`
- [ ] `bot_data/processed/binance_BTCUSDT_4h.csv`
- [ ] `bot_data/processed/binance_ETHUSDT_15m.csv`
- [ ] `bot_data/processed/binance_ETHUSDT_1h.csv`
- [ ] `bot_data/processed/binance_ETHUSDT_4h.csv`
- [ ] Decyzja: fetchujemy native dla każdego TF (więcej API calls) vs resampling z 1m/15m (mniej calls, mniej storage) — drobna decyzja, możliwe że w sesji
- [ ] Sanity check: brak gap'ów dłuższych niż X, monotonic timestamps, OHLCV invariants (high ≥ open/close/low, low ≤ open/close)

**Sub-deliverables (docs):**
- [ ] `docs/guides/data-fetching.md` lub sekcja w `running-backtest.md` — jak odpalić `algo-fetch` + `algo-process` dla pełnego setu Fazy 2, jak weryfikować integralność

**Prerequisite:** Sesja 1 (żeby wiedzieć którego TF najpierw potrzebujemy — strategia może mieć "ulubiony" TF z docstringu)

**Wartość:** pierwszy end-to-end test że `algo-fetch` + `algo-process` z ADR-010 działają w realnych warunkach. Plus prerequisite dla każdej kolejnej sesji 4-7.

### Sesja 3 — ADR-011 microstructure adjustments

**Cel:** określić jak slippage (5-10 bps) i funding cost (8h cycle dla perp futures) są aplikowane w backteście, żeby wyniki sweep'a i WF były realistic, nie naive.

**Sub-deliverables (kod):**
- [ ] `algo_bot/microstructure.py` lub extension na `algo_bot/engine/backtester.py` — funkcje adjustujące trade PnL o slippage + funding
- [ ] Decyzja architektoniczna: post-hoc na trades (prostsze, nie wpływa na entry timing) vs in-loop w backtester (skomplikowane, ale ekspresja "trade nie wszedł bo slip był wyższy niż edge")
- [ ] Decyzja: stały slip czy size-aware (linear / square-root market impact)
- [ ] Funding rate source: rzeczywiste historyczne dane z Binance (preferowane) vs syntetyczne 0.01%/8h jako baseline
- [ ] CLI flags w `algo-backtest`: `--slip_bps`, `--funding_source` (lub `--no_funding` dla raw mode)
- [ ] `MetricsSummary` extension: dodatkowe `_metrics_summary_raw` + `_metrics_summary_post_microstructure` w `summary.json`?
- [ ] `tests/test_microstructure.py` bez mocków

**Sub-deliverables (docs):**
- [ ] ADR-011 `docs/adr/011-microstructure-adjustments.md` po angielsku
- [ ] `docs/concepts/microstructure.md` — spread / slippage / funding mechanics dla perp futures Binance, jak je adjustujemy, dlaczego 5-10 bps jest realistic dla typowego size'a bghtrend
- [ ] `docs/reference/modules/microstructure.md` jeśli osobny moduł powstaje

**Prerequisite:** Sesja 1 (audit strategii pokaże czy bghtrend jest maker-friendly czy taker-only — wpływa na slip model). Opcjonalnie Sesja 2 dla testów na realnych danych.

**Wartość:** każdy backtest od sesji 4 wzwyż może być "post-microstructure". Bez tego "Sharpe 1.5 in-sample" to fairytale gdy Binance bierze 4 bps × 2 (open+close).

### Sesja 4 — In-sample sweep b1..b4 na BTC/ETH × 3 timeframes

**Cel:** pierwsza orientacja: które parametry bghtrend dają sensowny Sharpe na pełnej historii, czy są clustery w parameter space (dobry znak) czy random (zły).

**Sub-deliverables (kod):**
- [ ] 6 sweep runs (BTC/ETH × 15m/1h/4h × każdy konfig b1..b4) — łącznie 24 sweep runs, każdy z 5 random samples = 120 backtestów (orientacyjnie, zależy od `__n` w configach po ujednoliceniu)
- [ ] `results/sweeps/<run_id>/index.csv` × 24, plus top-10 wynik per (symbol, TF)
- [ ] Manual review: czy top-10 wyników klastruje w parameter space (sąsiednie param sets dają podobny Sharpe) czy losowo rozrzucone

**Sub-deliverables (docs):**
- [ ] `docs/guides/running-sweep.md` — YAML space format (random vs grid mode), interpretacja `index.csv`, jak czytać top-N, jak ocenić clustering vs random
- [ ] `docs/guides/running-backtest.md` — example argumenty, jak czytać `summary.json`, troubleshooting (CSV missing, params nie pasują do strategii, itd.)
- [ ] `docs/reference/metrics-reference.md` — interpretacja każdej metryki w `_metrics_summary` (post-ADR-007). Co znaczy Sharpe 1.2 w crypto? Calmar trailing 36m vs całość? Recovery time `inf`?

**Prerequisite:** Sesja 1 (parameter taxonomy), Sesja 2 (dane), Sesja 3 (microstructure — sweep powinien być post-slip).

**Wartość:** orientacja, pierwsze decyzje "co wartość brać do WF". Bez tego WF byłby na losowo wybranym param set.

### Sesja 5 — Walk-forward bghtrend na top params + Notebook 03

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

### Sesja 6 — Monte Carlo bootstrap + parameter stability heatmap

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

### Sesja 7 — Stress test na 4 reżimach

**Cel:** czy strategia przeżyła historyczne katastrofy. Trend-following typowo radzi sobie z grindującymi bear markets ale dostaje po głowie od V-shape recovery i mass liquidation events.

**Sub-deliverables (kod):**
- [ ] Backtests na 4 izolowanych okresach: 2018-12 (BTC -50% w 30 dni), 2020-03 (covid mass liquidation, March 12-13), 2022-06 (Luna/Terra collapse), 2022-11 (FTX implozja). Konkretne daty start/end per regime do zdefiniowania w sesji
- [ ] Per-regime metryki: max DD w okresie, n_trades, win_rate, czy strategia weszła w short side w czasie krachu (trend-following zalety) czy zaknięty long bag (catastrophic)

**Sub-deliverables (docs):**
- [ ] Sekcja w `notebooks/03_bghtrend_walkforward_analysis.ipynb` — per-regime performance summary
- [ ] `docs/concepts/regime-tests.md` lub sekcja w `concepts/backtest-robustness.md` — czemu te 4 okresy, jakie są typowe failure modes trend-following w każdym

**Prerequisite:** Sesja 2 (dane obejmujące 2018-12; sprawdzić że Binance USDT-M perpetual ma dane tak daleko wstecz, jeśli nie — alternatywa Bybit lub OKX)

**Wartość:** sanity check. Strategia która przeżyła wszystkie 4 reżimy jest gotowa na testnet. Strategia która umarła w 2020-03 nie idzie na żywy kapitał bez review.

### Sesja 8 — Decyzja MVP go/no-go + ADR-012

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
| ADR-012 | 8 | MVP go/no-go |
| (opc.) ADR-013 | 5 lub 6 | Diagnostics package layout (Monte Carlo + parameter stability) jeśli wydzielamy z notebooka do `algo_bot.diagnostics` |

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
- **Migracja silnika backtestowego**: backtesting.py jest wygodne, ale wolne. Kandydaci: `vectorbt` (super szybkie, multi-asset), `nautilus_trader` (event-driven, production-grade) — decyzja po MVP
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
