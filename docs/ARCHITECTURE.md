# Architektura — algo_bot

> Stan: 2026-05-11 (v0.1). Dokument opisuje aktualną architekturę + cel docelowy. Sekcja **TODO** wskazuje co trzeba dobudować w fazie 1 roadmapy.

---

## Pryncypia

1. **Jedna strategia, dwa silniki**. Klasa strategii działa identycznie w backteście i live — implementuje `on_bar(df) -> Signal`. Silniki (backtest / live) wywołują tę metodę i interpretują sygnał.
2. **Determinizm**. Backtest ma być powtarzalny co do bitu (z tym samym seedem, danymi i wersją kodu). Brak `random` bez seed, brak `datetime.now()` w hot path.
3. **Konfiguracja > kod**. Parametry strategii, sweep grid, ryzyko, sizing — wszystko w YAML. Zmiana parametru = edit configu, nie kodu.
4. **Idempotentność live**. Każdy order ma `client_order_id` deterministycznie wyliczony z (run_id, bar_ts, strategy, side). Restart bota nie tworzy duplikatów.
5. **Risk first**. Risk module siedzi pomiędzy strategią a executorem. Strategia mówi "chcę kupić", risk decyduje "wolno mi" + "ile".
6. **Observability by default**. Każdy moduł loguje structured events. Każdy live run ma run_id i journal.

---

## Warstwy

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              CONFIG LAYER                                │
│  config/*.yaml — parametry strategii, sweep grids, risk limits, exchange │
└──────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────┐      ┌────────────────────────────────────────┐
│      DATA LAYER         │      │           STRATEGY LAYER               │
│  ┌─────────────────┐    │      │  ┌──────────────────────────────────┐  │
│  │ fetch_data.py   │    │      │  │ strategy_base.py (StrategyBase,  │  │
│  │ (CCXT → OHLCV)  │    │      │  │  Signal)                         │  │
│  └────────┬────────┘    │      │  └────────┬─────────────────────────┘  │
│           ▼             │      │           ▼                            │
│  ┌─────────────────┐    │      │  ┌──────────────────────────────────┐  │
│  │ process_data.py │    │      │  │ strategies/*.py                  │  │
│  │ (indykatory,    │    │      │  │  - bghtrend_pullback (MVP)       │  │
│  │  feature eng.)  │    │      │  │  - bollinger_band_breakout_short │  │
│  └────────┬────────┘    │      │  │  - simple_momentum, ...          │  │
│           ▼             │      │  └────────┬─────────────────────────┘  │
│  bot_data/processed/    │      │           ▼                            │
│  (CSV: OHLCV + feats)   │      │  ┌──────────────────────────────────┐  │
│                         │      │  │ indicators/ (xtrender, core,     │  │
│                         │      │  │  t3, ema, atr, rsi)              │  │
│                         │      │  └──────────────────────────────────┘  │
└─────────────────────────┘      └────────────────────────────────────────┘
              │                                  │
              ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          RISK LAYER (TODO faza 1)                        │
│   src/risk/limits.py — drawdown stop, daily loss, max positions,         │
│   position sizing (% equity per trade)                                   │
└──────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│        BACKTEST ENGINE           │  │          LIVE ENGINE             │
│  src/engine/backtester.py        │  │  live/live_binance.py            │
│   - adapter BTStrategy           │  │   - WebSocket / poll loop        │
│   - run_backtest, save_outputs   │  │   - wait_for_next_close          │
│  src/engine/sweep.py             │  │   - state recovery z journala    │
│   - grid + random search         │  │  src/engine/exchanges/           │
│  src/engine/walkforward.py       │  │   - binance_adapter.py           │
│   (TODO faza 1)                  │  │   - (TODO: bybit_adapter)        │
└────────────┬─────────────────────┘  └────────────┬─────────────────────┘
             │                                     │
             ▼                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          TELEMETRY / JOURNAL                             │
│  src/telemetry/journal.py — CSV trades + equity per run_id               │
│  results/backtests/<run_id>/                                             │
│  results/experiments/<sweep_id>/  + index.csv                            │
│  results/live/<run_id>/                                                  │
└──────────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       MONITORING / ALERTS (TODO faza 3-5)                │
│   Telegram bot (events) | Prometheus + Grafana | Loki (logs)             │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Komponenty — gdzie co jest

### Już istnieje

| Plik | Linie | Rola |
|---|---:|---|
| `src/strategy_base.py` | 88 | Bazowa klasa strategii + dataclass Signal |
| `src/data_loader.py` | 336 | Wczytywanie CSV OHLCV, walidacja kolumn |
| `src/fetch_data.py` | 241 | CLI: ściąga OHLCV z giełd przez CCXT |
| `src/process_data.py` | 213 | Feature engineering, indykatory |
| `src/strategy_loader.py` | 24 | Dynamiczny import strategii po nazwie |
| `src/funding.py` | 23 | Funding rate (perp futures) |
| `src/engine/backtester.py` | 532 | Główny silnik backtestu (adapter `backtesting.py`) |
| `src/engine/sweep.py` | 352 | Grid + random search po parametrach |
| `src/engine/exchanges/binance_adapter.py` | 117 | CCXT wrapper na Binance Futures |
| `src/telemetry/journal.py` | ~80 | CSV journal trades + equity snapshots |
| `live/live_binance.py` | 401 | Live trading loop z TP/SL hybrid |
| `strategies/bghtrend_pullback.py` | 333 | Najmocniejsza strategia: trend + pullback + xtrender + ATR-trail |
| `indicators/xtrender.py` | - | Xtrender + komponenty momentum |
| `indicators/core.py` | - | EMA, RSI, ATR, T3 |
| `scripts/binance_ws.py`, `bybit_testnet.py`, `fear_greed.py` | - | Eksperymentalne |
| `notebooks/01_data_exploration.ipynb`, `02_bollinger_analysis.ipynb` | - | Research |
| `tests/test_backtest.py` | ~15 | Jeden test smoke |
| `config/config.yaml`, `bghtrend_b1..b4.yaml` | - | Configi |

### Do dobudowy (faza 1)

| Plik | Rola | Priorytet |
|---|---|---|
| `src/risk/limits.py` | Risk manager: drawdown stop, daily loss, position sizing | **wysoki** |
| `src/engine/walkforward.py` | Walk-forward analyzer (rolling train/test) | **wysoki** |
| `src/metrics.py` | Sharpe, Sortino, Calmar, MAR, profit factor | wysoki |
| `src/alerts/telegram.py` | Bot Telegram do alertów | średni (faza 3) |
| `src/monitoring/exporter.py` | Prometheus metrics exporter | niski (faza 5) |
| `pyproject.toml` | Editable install, dependencies, ruff/mypy config | wysoki |
| `Makefile` | `make backtest/sweep/live/test/lint` | średni |
| `Dockerfile` + `docker-compose.yml` | Containerization | niski (faza 5) |
| `.github/workflows/check.yml` | CI: `make check` (ruff + format-check + mypy + pytest) | wysoki |
| `deploy/systemd/algo_bot.service` | Systemd unit dla VPS | niski (faza 5) |
| `deploy/setup_vps.sh` | Bootstrap nowego VPSa | niski (faza 5) |

---

## Flow danych — backtest

```
CCXT API           bot_data/raw/         bot_data/processed/        Strategy
   │                    │                       │                       │
   │ fetch_data.py      │ process_data.py       │ data_loader.py        │
   ├───────────────────►├──────────────────────►├──────────────────────►│
   │ (CLI)              │ (CLI)                 │ (load_csv_ohlcv)      │
   │                    │ OHLCV.csv             │ OHLCV+features.csv    │ on_bar(df)
                                                                        │
                                                                        ▼
                                                                    Signal
                                                                        │
                                                          ┌─────────────┴───────────┐
                                                          ▼                         ▼
                                                  Risk module check        backtester adapter
                                                  (TODO faza 1)            (BTStrategy wrapper)
                                                          │                         │
                                                          ▼                         ▼
                                                  approved/rejected          backtesting.py
                                                                                    │
                                                                                    ▼
                                                                            results/backtests/
                                                                            (JSON + equity + trades)
```

## Flow danych — live

```
Binance WS / REST         live_binance.py        Strategy
        │                       │                    │
        │ poll candles          │ wait_for_next_close│ on_bar(df)
        ├──────────────────────►├───────────────────►│
        │                       │                    │
                                                     ▼
                                                 Signal
                                                     │
                                            ┌────────┴────────┐
                                            ▼                 ▼
                                     Risk check        client_order_id
                                     (TODO)            (idempotent)
                                            │                 │
                                            └────────┬────────┘
                                                     ▼
                                            binance_adapter
                                            (CCXT submit)
                                                     │
                                                     ▼
                                            Order ACK
                                                     │
                                                     ▼
                                            journal.log_entry()
                                                     │
                                            ┌────────┴────────┐
                                            ▼                 ▼
                                     TP/SL filled        alerts/telegram
                                            │            (TODO faza 3)
                                            ▼
                                     journal.log_exit()
```

---

## Decyzje architektoniczne (ADRs lite)

### ADR-001: Jeden interfejs strategii dla backtest + live
- **Decyzja**: `StrategyBase.on_bar(df) -> Signal`
- **Alternatywa odrzucona**: osobne klasy dla backtest i live z osobnymi sygnałami
- **Powód**: Duplikacja logiki = bugi. Jeden interface = "wystarczy raz zaimplementować, działa wszędzie". Już zaimplementowane.

### ADR-002: backtesting.py jako silnik MVP (nie vectorbt, nie nautilus)
- **Decyzja**: zostajemy z `backtesting.py` do końca fazy 4
- **Alternatywa**: `vectorbt` (10-100x szybciej), `nautilus_trader` (event-driven, production)
- **Powód**: koszt migracji > zysk dopóki nie mamy działającego MVP. Po fazie 4 — rewizja, prawdopodobnie migracja na vectorbt dla sweepów i nautilus dla live.

### ADR-003: TP/SL w trybie hybrid na live
- **Decyzja**: TP serwerowo (Binance OCO), SL lokalnie (bot sam zamyka pozycję gdy cena spadnie poniżej threshold)
- **Alternatywa**: full server-side
- **Powód**: testnet (i czasem mainnet) ma "knoty" — pojedyncze świece z ekstremami które trigerują SL serwerowy fałszywie. Local SL z `price_feed=mainnet_mark` jest bardziej stabilny. Już zaimplementowane.

### ADR-004: Risk module pomiędzy strategią a executorem (TODO)
- **Decyzja**: dedykowany moduł `src/risk/limits.py` który filtruje sygnały ze strategii
- **Alternatywa**: risk wbudowany w każdą strategię
- **Powód**: rozdzielenie odpowiedzialności. Strategia mówi "chcę open long X", risk mówi "OK ale tylko 50% sizing bo daily loss limit blisko". Strategia nie musi znać stanu portfela.

### ADR-005: Walk-forward obowiązkowy przed live (TODO)
- **Decyzja**: żadna strategia nie idzie na testnet/mainnet bez przejścia walk-forward
- **Alternatywa**: ufamy in-sample sweepowi
- **Powód**: in-sample sweep = overfitting. WF jest jedynym pragmatycznym proxy na out-of-sample performance.

### ADR-006: Wszystko przez git, brak Dropbox/Drive na configi
- **Decyzja**: configi, kod, deploy scripts — wszystko w repo. Secrety w `.env` (gitignored) + przykład w `.env.example`
- **Powód**: powtarzalność, audit trail, rollback (cofnij commit = wracasz do starych parametrów).

---

## Struktura katalogów docelowa (po fazie 1)

```
algo_bot/                          # docelowo: root repo (obecnie zagnieżdżone)
├── pyproject.toml                 # NEW
├── Makefile                       # NEW
├── README.md
├── .env.example                   # NEW
├── .github/workflows/ci.yml       # NEW
├── docs/
│   ├── ROADMAP.md                 # ✓ stworzone
│   ├── ARCHITECTURE.md            # ✓ stworzone (ten plik)
│   └── ADR/                       # NEW (przyszłość)
├── config/
│   ├── config.yaml
│   ├── bghtrend_b1..b4.yaml
│   └── risk.yaml                  # NEW: limity ryzyka
├── src/
│   ├── strategy_base.py
│   ├── strategy_loader.py
│   ├── data_loader.py
│   ├── fetch_data.py
│   ├── process_data.py
│   ├── funding.py
│   ├── metrics.py                 # NEW
│   ├── engine/
│   │   ├── backtester.py
│   │   ├── sweep.py
│   │   ├── walkforward.py         # NEW
│   │   └── exchanges/
│   │       ├── binance_adapter.py
│   │       └── bybit_adapter.py   # NEW
│   ├── risk/
│   │   └── limits.py              # NEW
│   ├── alerts/
│   │   └── telegram.py            # NEW (faza 3)
│   ├── monitoring/
│   │   └── exporter.py            # NEW (faza 5)
│   └── telemetry/
│       └── journal.py
├── strategies/
│   ├── bghtrend_pullback.py       # MVP
│   ├── bollinger_band_breakout_short.py
│   ├── simple_momentum.py
│   ├── short_trend_following.py
│   ├── ema_cross_sig.py
│   ├── dca_btc.py
│   └── template.py
├── indicators/
│   ├── core.py
│   └── xtrender.py
├── live/
│   └── live_binance.py
├── scripts/
│   └── (eksperymenty)
├── notebooks/
│   ├── 01_data_exploration.ipynb
│   ├── 02_bollinger_analysis.ipynb
│   └── 03_bghtrend_walkforward.ipynb   # NEW (faza 2)
├── tests/
│   ├── conftest.py
│   ├── test_backtest.py
│   ├── test_strategy_base.py      # NEW
│   ├── test_risk.py               # NEW
│   ├── test_walkforward.py        # NEW
│   └── test_idempotency.py        # NEW (faza 3)
├── bot_data/                      # gitignored
│   ├── raw/
│   └── processed/
├── results/                       # gitignored
│   ├── backtests/
│   ├── experiments/
│   └── live/
└── deploy/                        # NEW (faza 5)
    ├── Dockerfile
    ├── docker-compose.yml
    ├── systemd/algo_bot.service
    └── setup_vps.sh
```
