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

**Deliverables:**
- [x] Naprawiony `requirements.txt` (literówki: `tmatplotlib`, `yaml` → `PyYAML`; dodano `python-dotenv`, `tzdata`)
- [x] `pyproject.toml` z editable install (`pip install -e .`), hatchling backend, ruff + mypy config
- [x] `Makefile` z komendami: `make env`, `make install`, `make test`, `make lint`, `make format`, `make typecheck`
- [x] Strukturę katalogów wyrównać: `algo_bot/` bezpośrednio w roocie repo (`src/` → `algo_bot/`, usunięto `sys.path` hacki)
- [x] `environment.yml` (conda: Python 3.11, ta-lib z conda-forge)
- [ ] Risk management module (`algo_bot/risk/limits.py`): max drawdown stop, max concurrent positions, daily loss limit, position sizing oparty o ryzyko (% equity per trade, nie sztywny USDT)
- [ ] Walk-forward analyzer (`algo_bot/engine/walkforward.py`) — out-of-sample, train/test split z rolowaniem okna
- [ ] Standardowe metryki risk-adjusted (Sharpe, Sortino, Calmar, MAR, profit factor, recovery time) — `algo_bot/metrics.py`
- [ ] CI (GitHub Actions): pytest + ruff + mypy na każdym PR
- [ ] Pre-commit hooks: ruff + black + isort + pytest na strategicznych testach
- [ ] Logging zamiast `print` w całym kodzie (structured logs z `loguru` albo stdlib `logging`)

**Kryteria sukcesu:**
- `make test` zielony lokalnie i na CI
- Powtarzalność: dwukrotny backtest tej samej strategii z tym samym seedem zwraca bit-identyczne metryki
- Risk module zatrzyma backtest gdy drawdown przekroczy próg

**Ryzyka:**
- TA-Lib na Windowsie/macOS bywa upierdliwe → udokumentować alternatywę (`pandas-ta` jako fallback)

---

## Faza 2 — Research & Backtest MVP

**Cel:** wybrana strategia ma statystycznie istotną przewagę w out-of-sample, nie tylko w in-sample sweep.

**Wybór strategii kandydatki:** **bghtrend_pullback** — jest najbardziej dopracowana, ma sensowną tezę ekonomiczną (trend + pullback z xtrenderem jako filtrem momentum), ma cooldown i ATR-trail (już zaimplementowane).

**Deliverables:**
- [ ] Pobranie danych: BTC/USDT + ETH/USDT, 15m + 1h + 4h, od 2019 (Binance Futures, perpetual)
- [ ] In-sample sweep (`b1..b4` configi już są — przejrzeć i ujednolicić)
- [ ] **Walk-forward** z minimum 5 fold (np. train 12mies, test 3mies, krok 3mies)
- [ ] Monte Carlo na trade sequence (resample trades → percentyle equity, max DD)
- [ ] Test stabilności parametrów: heatmapa metryki po 2 wymiarach parametrów (sąsiednie wartości muszą dawać podobny wynik — inaczej overfitting)
- [ ] Slippage + funding cost realistyczne: 5-10 bps slip, funding co 8h dla perp futures
- [ ] Stress test: 2018-12 (krach), 2020-03 (covid), 2022-06 (luna), 2022-11 (FTX) — strategia musi przeżyć
- [ ] Notebook `03_bghtrend_walkforward_analysis.ipynb` z pełną analizą
- [ ] **Decyzja**: czy bghtrend_pullback w obecnej formie nadaje się na MVP czy potrzebuje iteracji

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

**Deliverables:**
- [ ] Paper trading mode (lokalny symulator, bez wysyłania orderów) — strategia konsumuje real-time WebSocket data, generuje sygnały, journal jak gdyby były trade'y
- [ ] Testnet run (Binance Futures testnet) — minimum 2 tygodnie ciągłej pracy
- [ ] Sprawdzenie zgodności sygnałów backtest vs live: re-backtest na danych z okresu testnetu i porównać sygnały bar-by-bar
- [ ] Alerty (Telegram bot): entry, exit, SL hit, błąd systemu, drift w PnL
- [ ] Recovery: bot się crashuje → restart → wczytuje state z journalu → kontynuuje (nie otwiera duplikatów pozycji)
- [ ] Idempotentność orderów (client_order_id) — nie da się otworzyć dwóch identycznych pozycji

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

**Deliverables:**
- [ ] Switch konfiguracyjny `mode: testnet|mainnet` (jeden config, łatwy toggle)
- [ ] Reconciliation: codzienny report — equity z giełdy vs equity z journala (różnica → alert)
- [ ] Daily report do Telegrama: PnL dzienny/tygodniowy/total, liczba trade'ów, win rate, exposure
- [ ] Weekly retrospekcja (manualnie) — porównaj live performance vs backtest baseline z tego okresu

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

**Deliverables:**
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

*Dokument żywy. Po każdej fazie aktualizujemy kryteria i decyzje. Wersja: 0.2 — 2026-05-19.*
