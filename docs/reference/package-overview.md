# Package Overview

Pełna mapa repo algo_bot — co siedzi w którym katalogu i pliku.

## Struktura repo (root)

```
algo_bot/                              # repo root
├── algo_bot/                          # główny pakiet Python (ważne pliki ↓)
├── live/                              # CLI dla live tradingu (nie-pakiet)
├── tests/                             # pytest
├── notebooks/                         # research notebooks
├── scripts/                           # eksperymenty jednorazowe
├── config/                            # YAML configi
├── docs/                              # dokumentacja (ten plik tu siedzi)
├── bot_data/                          # dane historyczne (gitignored)
├── results/                           # outputy backtestów/sweepów/live (gitignored)
│
├── pyproject.toml                     # build + deps + tooling
├── environment.yml                    # conda env definition
├── requirements.txt                   # generowany lockfile (pip-tools)
├── Makefile                           # codzienne komendy
├── README.md                          # entry point
├── .gitignore                         # comprehensive
└── .deploy_key                        # SSH key (gitignored)
```

---

## `algo_bot/` — główny pakiet

```
algo_bot/
├── __init__.py                        # pusty (package marker)
├── strategy_base.py                   # StrategyBase + Signal — unified API
├── strategy_loader.py                 # dynamic strategy loading by name
├── data_loader.py                     # CSV OHLCV reader/writer + validation
├── fetch_data.py                      # CLI: CCXT → bot_data/raw/*.csv
├── process_data.py                    # CLI: raw → processed z featurami
├── funding.py                         # funding rate scraping (perp futures)
│
├── engine/                            # silniki backtestowe
│   ├── __init__.py
│   ├── backtester.py                  # główny silnik backtest (wrapper na backtesting.py)
│   ├── sweep.py                       # grid + random search po przestrzeni parametrów
│   └── exchanges/                     # adaptery giełd
│       ├── __init__.py
│       └── binance_adapter.py         # CCXT wrapper dla Binance Futures
│
├── indicators/                        # custom wskaźniki techniczne
│   ├── __init__.py                    # re-exporty z core + xtrender
│   ├── core.py                        # ema, rsi, atr, t3 (formuły)
│   └── xtrender.py                    # xtrender oscillator + komponenty
│
├── strategies/                        # implementacje strategii tradingowych
│   ├── __init__.py
│   ├── bghtrend_pullback.py           # MVP candidate — trend + pullback + xtrender + ATR-trail
│   ├── bollinger_band_breakout_short.py
│   ├── dca_btc.py
│   ├── ema_cross_sig.py
│   ├── short_trend_following.py
│   ├── simple_momentum.py
│   ├── template.py                    # skeleton dla nowej strategii
│   └── bitcoin_breakout.py            # ⚠️ EMPTY (placeholder)
│
└── telemetry/                         # logging + journaling
    ├── __init__.py
    └── journal.py                     # CSV journal trades + equity snapshots
```

### Pliki kluczowe (core)

| Plik | Linie | Rola |
|---|---:|---|
| `strategy_base.py` | 88 | **Bazowa klasa wszystkich strategii + Signal dataclass.** Definuje `on_bar(df) -> Signal` jako jedyne wymagane API. Silniki (backtest, live) wywołują tę metodę. Patrz [ADR-003](../adr/003-strategybase-signal-api.md). |
| `strategy_loader.py` | 24 | `load_strategy(name, params)` — dynamic import z `algo_bot.strategies.<name>`, walidacja że dziedziczy po StrategyBase. |
| `data_loader.py` | 336 | Loader dla bot_data: `load_processed`, `count_missing_bars`, `get_processed_path`. Plus legacy: `fetch_ohlcv`, `load_csv_ohlcv`, `resample_ohlcv`. |
| `fetch_data.py` | 241 | CLI: pobiera OHLCV z giełdy przez CCXT, zapisuje do `bot_data/raw/`. Argparse-based. |
| `process_data.py` | 213 | CLI: raw → processed z indykatorami (BBANDS, RSI, etc.). Konfig features w `config/config.yaml`. |
| `funding.py` | 56 | Helper do pobierania funding rates z Binance (mały scraper; CLI: `python -m algo_bot.funding`). |

### `algo_bot/engine/`

| Plik | Linie | Rola |
|---|---:|---|
| `backtester.py` | 532 | **Główny silnik backtestowy.** Wrapper na backtesting.py. Funkcje: `run_backtest()`, `save_outputs()`, `run_id()`. Adapter `make_bt_wrapper()` parsuje Signal na BTStrategy API. Wsparcie dla TP/SL/trail (cooldown w strategii), microstructure adjustment (`adjust_trades_df` z spread/slippage). Patrz [ADR-005](../adr/005-backtesting-py-mvp-engine.md). |
| `sweep.py` | 352 | **Grid + random search po parametrach.** Wczytuje przestrzeń z YAML (`config/bghtrend_b1.yaml`), generuje kombinacje, odpala backtest per kombinacja, agreguje wyniki w `results/experiments/index.csv`. Wsparcie dla walk-forward (rolling windows). |
| `exchanges/binance_adapter.py` | 117 | Klasa `BinanceFuturesAdapter` — CCXT wrapper dla Binance Futures (USDT-M perpetuals). Używana przez live runner i fetch_data. |

### `algo_bot/indicators/`

| Plik | Rola |
|---|---|
| `core.py` | Podstawowe wskaźniki: `ema()`, `rsi()`, `atr()`, `t3()`. Wszystkie operują na `pd.Series`. |
| `xtrender.py` | `xtrender_components()` — custom oscillator (Bryan G. Howell variant). Używany w `bghtrend_pullback.py`. |

### `algo_bot/strategies/`

Każda strategia = osobny plik. Konwencja: klasa `Strategy` dziedziczy po `StrategyBase`, ma `ParamSchema = SomeDataclass`, implementuje `on_bar(df) -> Signal`.

| Strategia | Linie | Co robi |
|---|---:|---|
| `bghtrend_pullback.py` | 333 | **MVP candidate.** Trend (EMA21/89/200) + pullback (cena spada w pobliże EMA89) + xtrender momentum confirm + ATR-trail SL + cooldown po SL. Najmocniej rozbudowana strategia. |
| `bollinger_band_breakout_short.py` | 46 | Klasyczna: przerwanie poniżej dolnego pasma Bollingera → short z TP/SL. Używa natywnego `backtesting.Strategy` (nie StrategyBase). |
| `simple_momentum.py` | 56 | EMA crossover (short vs long). Klasyczna MA cross. Używa StrategyBase. |
| `short_trend_following.py` | 69 | Death Cross + MACD + ATR trailing stop. Tylko short. Używa `backtesting.Strategy` (natywnie). |
| `dca_btc.py` | 148 | Dollar Cost Averaging dla BTC. Co N świec dokłada zakupu. Optionally skalowane przez Fear & Greed index. Używa StrategyBase z `allow_pyramiding=True`. |
| `ema_cross_sig.py` | 38 | Skeleton EMA cross signal generator (NIE używa StrategyBase ani backtesting.Strategy — to standalone helper class). |
| `template.py` | 53 | **Skeleton dla nowej strategii.** Kopiuj jako starter. Używa StrategyBase + Signal pattern. |
| `bitcoin_breakout.py` | 0 | ⚠️ **EMPTY**. Placeholder. |

### `algo_bot/telemetry/`

| Plik | Rola |
|---|---|
| `journal.py` | Klasa `Journal` — pisze CSV trades.csv + equity.csv per `run_id`. Używane przez `live_binance.py` i przez backtester (przez `save_outputs`). |

---

## `live/` — live trading CLI

```
live/
├── __init__.py
└── live_binance.py                    # 401 linii — pełen live runner dla Binance Futures
```

`live/` jest **na top-level (nie w algo_bot/)** świadomie — to entry-point CLI, nie biblioteka. Importuje z `algo_bot.*` (po `pip install -e .` działa z dowolnego cwd).

**`live_binance.py`** — argparse CLI z opcjami:
- `--symbol`, `--timeframe`, `--strategy`, `--params` — co tradujemy
- `--size_usdt`, `--leverage` — sizing
- `--tp_pct`, `--sl_pct` — risk levels
- `--data_source` (`testnet`/`mainnet`) — gdzie pobieramy dane
- `--tpsl_mode` (`server`/`local`/`hybrid`) — patrz [ADR-004](../adr/004-hybrid-tp-sl-mode.md)
- `--price_feed` (`mainnet_mark`/`mainnet_last`/`testnet_mark`/`testnet_last`) — źródło ceny dla local SL

Flow:
1. Załaduj `.env` (API keys)
2. Inicjalizuj `BinanceFuturesAdapter` + `Journal`
3. Załaduj strategię (przez `load_strategy()`)
4. Pętla:
   - `wait_for_next_close()` — czekaj na zamknięcie świecy
   - Pobierz ostatnie N świec (zamknięte!)
   - `strategy.on_bar(df) -> Signal`
   - Parsuj Signal:
     - `enter` → wyślij order (z TP/SL zgodnie z `tpsl_mode`)
     - `exit` → close position
     - hold/levels-update → opcjonalnie update SL/trail
   - Log do journala
5. Restart-safe: wczytuje state z journala przy starcie (recovery scenario)

W przyszłości (faza 1-2): przeniesienie do `algo_bot/live/` jako submoduł + CLI entry `algo-live`.

---

## `tests/` — pytest

```
tests/
├── conftest.py                        # shared fixtures (placeholder)
├── test_backtest.py                   # ⚠️ broken signature (TODO faza D)
└── fetching_data_test.py              # smoke test CCXT
```

Aktualnie tylko 1 funkcjonalny test. Plan rozbudowy w fazach 2-5:
- `test_strategy_base.py` — testy `StrategyBase` + `Signal` parsing
- `test_risk.py` — risk module (po decyzji E)
- `test_walkforward.py` — walk-forward (po decyzji F)
- `test_metrics.py` — metryki (po decyzji D)
- `test_idempotency.py` — live order idempotency (faza 3)

`conftest.py` jest aktualnie pustym placeholder po usunięciu `sys.path.insert` hacka (niepotrzebny po `pip install -e .`).

---

## `notebooks/` — research

```
notebooks/
├── 01_data_exploration.ipynb          # eksploracja danych
└── 02_bollinger_analysis.ipynb        # analiza strategii bollinger
```

Notebooks importują z `algo_bot.*` (po aktywacji conda env + `pip install -e .`). Konwencja:
- Eksperymentalne research → notebook
- Gdy logika stabilna i potrzebna w production → przenosimy do `algo_bot/<modul>.py` + test
- Outputy notebooków zalecane do clean przed commitem (zob. `daily-workflow.md`)

Plan (faza 2):
- `03_bghtrend_walkforward_analysis.ipynb` — walk-forward MVP strategii
- `04_microstructure_impact.ipynb` — wpływ spread/slippage na PnL
- `05_parameter_stability.ipynb` — heatmapy stabilności parametrów

---

## `scripts/` — eksperymenty

```
scripts/
├── binance_connect.py                 # quick check że Binance API działa
├── binance_ws.py                      # WebSocket client (early prototype)
├── bybit_testnet.py                   # Bybit testnet connection check
└── fear_greed.py                      # scrape Fear & Greed index
```

**Standalone scripts** — nie importują algo_bot, nie są częścią pakietu. Eksperymenty jednorazowe, prototypy.

---

## `config/` — YAML configi

```
config/
├── config.yaml                        # globalny config (data paths, backtest defaults, features)
├── bghtrend_b1.yaml                   # przestrzeń parametrów bghtrend_pullback — wariant 1
├── bghtrend_b2.yaml                   # wariant 2
├── bghtrend_b3.yaml                   # wariant 3
└── bghtrend_b4.yaml                   # wariant 4
```

`config.yaml`:
- `data.raw_dir` / `data.processed_dir` — ścieżki do danych
- `backtest.cash`, `backtest.commission` — defaulty
- `defaults.features` — lista featurów do compute (BBANDS, RSI, ...)
- `strategies.<name>.run` / `.optimize` — params per strategia

`bghtrend_b{1,2,3,4}.yaml` — przestrzenie parametrów dla `algo-sweep`. Każda zawiera różne zakresy/granularności do testowania. Patrz [config-reference.md] (TBD).

---

## `docs/` — dokumentacja

```
docs/
├── README.md                          # TOC (start here)
├── ROADMAP.md                         # 5-fazowy plan rozwoju
├── ARCHITECTURE.md                    # wysokopoziomowa architektura
├── CHANGELOG.md                       # historia zmian
├── adr/                               # Architecture Decision Records
├── guides/                            # how-to
├── reference/                         # encyklopedia (ten katalog)
└── concepts/                          # narrative explanations
```

Pełen index w [`docs/README.md`](../README.md).

---

## `bot_data/` — dane historyczne

```
bot_data/                              # gitignored
├── raw/                               # surowe OHLCV z giełd (CCXT)
│   ├── BTC_USDT-4h.csv
│   ├── ETH_USDT-1h.csv
│   └── ...
└── processed/                         # po process_data — z featurami
    ├── binance_BTCUSDT_4h.csv
    └── ...
```

**Gitignored** — dane są duże (MB-GB) i regenerowalne (`make fetch && make process`).

Konwencja nazewnictwa:
- Raw: `<SYMBOL>-<TIMEFRAME>.csv` (np. `BTC_USDT-4h.csv`)
- Processed: `binance_<SYMBOL_NO_SLASH>_<TIMEFRAME>.csv` (np. `binance_BTCUSDT_4h.csv`)

Kolumny:
- Raw: `datetime,timestamp,Open,High,Low,Close,Volume`
- Processed: `datetime,Open,High,Low,Close,Volume,<feature1>,<feature2>,...`

---

## `results/` — outputy

```
results/                               # gitignored
├── backtests/<run_id>/                # per pojedynczy backtest
│   ├── summary.json                   # metryki
│   ├── params.json                    # użyte parametry + metadata
│   ├── equity.csv                     # equity per bar
│   └── trades.csv                     # log transakcji (z spread/slippage adj)
├── experiments/                       # sweepy
│   ├── index.csv                      # agregat: wszystkie run_id + metryki
│   └── <run_id>/                      # per kombinacja parametrów (jak backtests/)
└── live/<run_id>/                     # live trading sessions
    ├── trades.csv                     # log live trades
    └── equity.csv                     # equity snapshots
```

**Gitignored** — wyniki są regenerowalne i często duże.

`run_id` format: `<TIMESTAMP_UTC>_<STRATEGY>_<SYMBOL>_<TIMEFRAME>_<PARAMS_HASH>`. Np. `20260514_103000_bghtrend_pullback_BTCUSDT_4h_a3b8e7f1`.

---

## Pliki rootowe — szybki przewodnik

| Plik | Rola |
|---|---|
| `pyproject.toml` | Single source of truth dla pakietu. Patrz [ADR-002](../adr/002-pyproject-hatchling-stack.md). |
| `environment.yml` | Conda env definition (`algo_bot` env z Python 3.11 + TA-Lib). |
| `requirements.txt` | Lockfile generowany przez `pip-tools` z pyproject.toml. NIE edytuj ręcznie — `make lock`. |
| `Makefile` | Codzienne komendy. Patrz [makefile-cheatsheet.md](../guides/makefile-cheatsheet.md). |
| `README.md` | Entry point repo. Quick start + linki do docs. |
| `.gitignore` | Comprehensive (secrets, Python cache, venvs, IDE, OS, bot_data, results). |
| `.deploy_key` (gitignored) | SSH private key dla GitHub deploy access. |

---

## Co jeszcze przyjdzie

Pliki/katalogi które dorobimy w fazach 1-5 zgodnie z [ROADMAP](../ROADMAP.md):

### Faza 1 (dokończenie)
- `algo_bot/risk/limits.py` — risk management (po decyzji E)
- `algo_bot/engine/walkforward.py` — walk-forward analyzer (po decyzji F)
- `algo_bot/metrics.py` — Sharpe, Sortino, Calmar, MAR, ... (po decyzji D)
- `algo_bot/log.py` — logging setup (po decyzji C)
- `.pre-commit-config.yaml` — pre-commit hooks
- `.github/workflows/ci.yml` — GitHub Actions

### Faza 3
- `algo_bot/alerts/telegram.py` — alerty na Telegram
- Refaktor `live/live_binance.py` → `algo_bot/live/binance.py` + module structure

### Faza 5
- `Dockerfile` + `docker-compose.yml`
- `deploy/setup_vps.sh`
- `deploy/systemd/algo_bot.service`
- `algo_bot/monitoring/exporter.py` — Prometheus exporter

Każdy nowy moduł dochodzi z **per-file header docstring** (5-15 linii) + ewentualnie `docs/reference/modules/<modul>.md` (dla rozbudowanych).
