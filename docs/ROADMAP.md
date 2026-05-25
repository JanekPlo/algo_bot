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

**Deliverables (kod):**
- [ ] Pobranie danych: BTC/USDT + ETH/USDT, 15m + 1h + 4h, od 2019 (Binance Futures, perpetual)
- [ ] In-sample sweep (`b1..b4` configi już są — przejrzeć i ujednolicić)
- [ ] **Walk-forward** z minimum 5 fold (np. train 12mies, test 3mies, krok 3mies)
- [ ] Monte Carlo na trade sequence (resample trades → percentyle equity, max DD)
- [ ] Test stabilności parametrów: heatmapa metryki po 2 wymiarach parametrów (sąsiednie wartości muszą dawać podobny wynik — inaczej overfitting)
- [ ] Slippage + funding cost realistyczne: 5-10 bps slip, funding co 8h dla perp futures
- [ ] Stress test: 2018-12 (krach), 2020-03 (covid), 2022-06 (luna), 2022-11 (FTX) — strategia musi przeżyć
- [ ] Notebook `03_bghtrend_walkforward_analysis.ipynb` z pełną analizą
- [ ] **Decyzja**: czy bghtrend_pullback w obecnej formie nadaje się na MVP czy potrzebuje iteracji

**Deliverables (docs):**
- [ ] `docs/guides/running-backtest.md` — example argumenty, jak czytać output, troubleshooting
- [ ] `docs/guides/running-sweep.md` — YAML space format, grid vs random, interpretacja index.csv
- [ ] `docs/guides/walk-forward-howto.md` — krok-po-kroku jak odpalić WF, jak interpretować
- [ ] `docs/concepts/walk-forward.md` — methodology (rolling vs anchored, dlaczego obowiązkowe przed live)
- [ ] `docs/concepts/microstructure.md` — spread, slippage, funding — co adjustujemy w backteście
- [ ] `docs/reference/modules/strategy-bghtrend-pullback.md` — deep walkthrough strategii MVP (formuły, decisions)
- [ ] `docs/reference/config-reference.md` — kompletny schema YAML configów (config.yaml + bghtrend_b*.yaml)
- [ ] `docs/reference/metrics-reference.md` — interpretacja każdej metryki w summary.json
- [ ] ADR per znaczące decyzje fazy 2 (np. "Czy bghtrend MVP" — ADR-XXX z analizą WF wyników)
- [ ] CHANGELOG entry v0.2.0 — wszystkie deliverables fazy 2

**Kryteria sukcesu (MVP):**
- Walk-forward Sharpe > 1.0
- Walk-forward max DD < 25%
- Profit factor > 1.3 w OOS
- Stabilność parametrów: ±20% wartości parametru → ±15% wyniku metryki
- Liczba trade'ów w OOS > 50 (statystyczna istotność)

**Ryzyka:**
- Overfitting na sweepie — mitygacja: walk-forward + Monte Carlo + parameter stability
- Survivor bias w danych — mitygacja: tylko BTC/ETH (no survivor bias issue dla tych)

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
