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
├── .python-version                    # vanilla CPython 3.12.13
├── uv.lock                            # kanoniczny lockfile zależności
├── requirements.txt                   # generowany eksport kompatybilności
├── Makefile                           # codzienne komendy
├── README.md                          # entry point
├── .gitignore                         # comprehensive
└── .deploy_key                        # SSH key (gitignored)
```

### Runtime Beta 0

Domyślna ścieżka instalacyjna to uv 0.11.28: `uv sync --locked` odczytuje
`.python-version`, tworzy `.venv` i instaluje projekt editable. Komendy pakietu
uruchamiamy przez `uv run`; nie trzeba aktywować środowiska.

`pyproject.toml` przypina NautilusTrader 1.230.0, TA-Lib 0.7.0 i legacy
backtesting.py 0.6.5, a `uv.lock` zamraża resztę grafu. Wheel TA-Lib zawiera
bibliotekę C. Dawne `environment.yml` + Conda oraz lockowanie pip-tools są
**superseded** i nie stanowią równorzędnego workflow.

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
│   ├── backtester.py                  # legacy runner + compatibility facade
│   ├── backtest_result.py             # wersjonowany wynik, hashe i cost eligibility
│   ├── sweep.py                       # grid + random search po przestrzeni parametrów
│   ├── walkforward.py                 # rolling/anchored walk-forward
│   ├── nautilus_poc.py                # P3 timestamp/execution semantics
│   ├── nautilus_oms_poc.py            # P4 NETTING/stop-safety proof
│   ├── nautilus_adapter.py            # P5 Tier-1 compatibility/equivalence
│   ├── nautilus_mastermind.py         # P7 thin PyO3 transport wrapper
│   ├── mms_beta_data.py               # P9 development-only input boundary
│   ├── mms_beta_benchmark.py          # P9 frozen 2×6 ablation runner
│   └── exchanges/                     # adaptery giełd
│       ├── __init__.py
│       └── binance_adapter.py         # CCXT wrapper dla Binance Futures
│
├── indicators/                        # custom wskaźniki techniczne
│   ├── __init__.py                    # re-exporty z core + xtrender
│   ├── core.py                        # ema, rsi, atr, t3, bbands, stochastic (formuły)
│   └── xtrender.py                    # xtrender oscillator + komponenty
│
├── strategies/                        # implementacje strategii tradingowych
│   ├── __init__.py
│   ├── bghtrend_pullback.py           # baseline (NO-GO ADR-012; kept as reference)
│   ├── dca_btc.py
│   ├── ema_cross_sig.py
│   ├── mean_reversion_bb_stoch.py     # bare-core legacy baseline (NO-GO MR-Session 2)
│   ├── mastermind/                    # pure MMS-inspired v2 domain
│   │   ├── model.py                   # typed events/intents/config/state
│   │   ├── signals.py                 # engine-independent H1 facts
│   │   ├── state_machine.py           # reducer, invariants, outbox, recovery view
│   │   └── snapshot.py                # canonical checksummed persistence
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

| Plik | Rola |
|---|---|
| `strategy_base.py` | **Bazowa klasa strategii legacy + `Signal`.** Definiuje `on_bar(df) -> Signal`; nie jest rozszerzana o wielonogowy automat v2. Patrz [ADR-003](../adr/003-strategybase-signal-api.md) i [ADR-014](../adr/014-engine-migration-nautilus.md). |
| `strategy_loader.py` | `load_strategy(name, params)` — dynamiczny import oraz walidacja `StrategyBase`. |
| `data_loader.py` | Loader i walidacja plików `bot_data`. |
| `fetch_data.py` / `process_data.py` | CLI CCXT → raw → processed. |
| `funding.py` | Pobieranie historycznych funding rates (`uv run algo-fetch-funding`). |

### `algo_bot/engine/`

| Plik | Rola |
|---|---|
| `backtester.py` | Przypięty runner `backtesting.py`; `run_backtest()` zachowuje tuple, a `run_backtest_result()` buduje rich source result. |
| `backtest_result.py` | `BacktestResult` ze schema/version, engine/git/data/config hash, sześcioma ledgerami oraz fail-closed cost eligibility. |
| `sweep.py` | Grid/random search; konfiguracje są walidowane przed samplingiem. |
| `walkforward.py` | Rolling/anchored walk-forward i bramki MVP/WF. |
| `nautilus_poc.py` | P3: close timestamps, causal fill scheduling, gap i OHLC ordering. |
| `nautilus_oms_poc.py` | P4: wybór OMS-A NETTING + virtual legs, Close-All i incremental add-on stops. |
| `nautilus_adapter.py` | P5 Tier-1: wąski `StrategyBase` → Nautilus Cython profil z zamrożoną equivalence. Nie hostuje MMS v2. |
| `nautilus_mastermind.py` | P7: cienkie mapowanie PyO3 event↔domain intent, native costs, stable IDs, reconciliation i transport checkpoint. Profil pozostaje `SMOKE_ONLY / NOT_ELIGIBLE`. |
| `mms_beta_data.py` | P9: streaming warm-up+development, ścisła granica nietkniętego holdoutu, TA-Lib features i native funding updates. |
| `mms_beta_benchmark.py` | P9: prerejestrowana macierz 2 zestawy × 6 wariantów, manifest, invariant ledger i opisowe kontrasty ablation. |
| `exchanges/binance_adapter.py` | CCXT wrapper dla Binance Futures, używany przez fetch/live legacy. |

### `algo_bot/indicators/`

| Plik | Rola |
|---|---|
| `core.py` | Podstawowe wskaźniki: `ema()`, `rsi()`, `atr()`, `t3()`, `bbands()`, `stochastic()`. Wszystkie kauzalne (precompute-safe). Deep references: [indicators-bbands.md](modules/indicators-bbands.md), [indicators-stochastic.md](modules/indicators-stochastic.md). |
| `xtrender.py` | `xtrender_components()` — custom oscillator (Bryan G. Howell variant). Używany w `bghtrend_pullback.py`. |

### `algo_bot/strategies/`

Każda strategia = osobny plik. Konwencja: klasa `Strategy` dziedziczy po `StrategyBase`, ma `ParamSchema = SomeDataclass`, implementuje `on_bar(df) -> Signal`.

| Strategia/domena | Co robi |
|---|---|
| `mastermind/` | **MMS-inspired v2 H1/BB mechanization.** Czysty, engine-independent model eventów/intencji, sygnały, reducer z trzema wymiarami stanu i checksummed snapshot. Stochastic jest triggerem dokładki; SCOUT jest base-only. Źródło prawdy: [executable spec](../specs/mms-v2-executable-spec.md). |
| `mean_reversion_bb_stoch.py` | Legacy bare core: touch BB → armed→reaction; przeciwległe żywe pasmo TP; 2% SL; opcjonalny gate Stochastic. MR-Session 2 dał NO-GO dla bare core, więc plik jest baseline'em, nie gotowym kandydatem live. |
| `bghtrend_pullback.py` | **Historyczny baseline (NO-GO, ADR-012).** Trend + pullback + Xtrender + ATR trail. |
| `simple_momentum.py` | Klasyczny EMA crossover przez `StrategyBase`. |
| `short_trend_following.py` | Death Cross + MACD + ATR trailing stop, tylko short. |
| `dca_btc.py` | DCA z opcjonalnym skalowaniem Fear & Greed. |
| `ema_cross_sig.py` | Standalone helper sygnału EMA cross. |
| `template.py` | Skeleton nowej prostej strategii `StrategyBase`. |
| `bitcoin_breakout.py` | Pusty placeholder. |

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

`live/` jest **na top-level (nie w algo_bot/)** świadomie — to entry-point CLI, nie biblioteka. Importuje z `algo_bot.*`; `uv sync --locked` instaluje projekt editable, a `run_live.sh` uruchamia moduł przez `uv run --locked`.

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

Suite obejmuje legacy framework, risk/microstructure/walk-forward oraz bramki migracji:

- `test_nautilus_poc.py`, `test_nautilus_oms_poc.py`, `test_nautilus_adapter.py` —
  P3–P5 hard gates;
- `test_mastermind_signals.py`, `test_mastermind_state_machine.py`,
  `test_mastermind_snapshot.py`, `test_mastermind_dedupe.py` — czysta domena P6;
- `test_nautilus_mastermind.py` — PyO3 wrapper, native funding i lifecycle smoke;
- `test_backtest_result.py` — schema/ledger/hash/eligibility P8;
- `test_mms_beta_data.py`, `test_mms_beta_benchmark.py` — granice danych,
  prerejestracja, manifest i ablation P9;
- pozostałe `test_*.py` utrzymują wcześniejsze kontrakty projektu.

Pełna deterministyczna bramka to `make check`; testy live/network pozostają
oznaczone markerami i nie są wymagane w domyślnym CI.

---

## `notebooks/` — research

```
notebooks/
├── 01_data_exploration.ipynb          # eksploracja danych
├── 02_bollinger_analysis.ipynb        # analiza strategii Bollinger
├── 03_bghtrend_sweep_and_walkforward.ipynb
└── 04_mr_sweep_review.ipynb           # MR-Session 2 review/no-go evidence
```

Notebooks importują z `algo_bot.*` po `uv sync --locked --group notebooks`;
uruchamiaj je przez `uv run --group notebooks jupyter lab notebooks/`.
Konwencja:
- Eksperymentalne research → notebook
- Gdy logika stabilna i potrzebna w production → przenosimy do `algo_bot/<modul>.py` + test
- Outputy notebooków zalecane do clean przed commitem (zob. `daily-workflow.md`)

Notebook 03 zachowuje historyczny bghtrend research; notebook 04 dokumentuje
MR-Session 2. P9 Beta jest celowo kodowym, prerejestrowanym runnerem, a nie
interaktywnym notebookiem wybierającym wariant po obejrzeniu wyników.

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
├── bghtrend_b4.yaml                   # wariant 4
├── mr_b1.yaml                         # mean-reversion medium/strict
├── mr_b2.yaml                         # mean-reversion medium/ablation gate
└── mr_b3.yaml                         # mean-reversion fast/15m
```

`config.yaml`:
- `data.raw_dir` / `data.processed_dir` — ścieżki do danych
- `backtest.cash`, `backtest.commission` — defaulty
- `defaults.features` — lista featurów do compute (BBANDS, RSI, ...)
- `strategies.<name>.run` / `.optimize` — params per strategia

`bghtrend_b{1,2,3,4}.yaml` i `mr_b{1,2,3}.yaml` są historycznymi przestrzeniami
legacy `algo-sweep`. Loader od P0 odrzuca każdy non-meta parametr random sweepu,
który nie jest mapping/spec. Szczegóły: [config-reference.md](config-reference.md).

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
├── concepts/                          # narrative explanations
├── specs/                             # wykonywalne kontrakty domenowe
└── experiments/                       # prerejestracje i kompaktowe raporty
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

**Gitignored** — dane są duże (MB-GB) i regenerowalne (`uv run algo-fetch ...`
oraz `uv run algo-process`).

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
| `.python-version` | Przypięty vanilla CPython 3.12.13, automatycznie wybierany/pobierany przez uv. |
| `uv.lock` | Kanoniczny lockfile całego grafu deps. Generuj przez `make lock`, instaluj przez `make sync`; nie edytuj ręcznie. |
| `requirements.txt` | Eksport kompatybilności z `uv.lock`. Generuj przez `make export-requirements`; lokalny setup i CI go nie używają. |
| `Makefile` | Codzienne komendy. Patrz [makefile-cheatsheet.md](../guides/makefile-cheatsheet.md). |
| `run_live.sh` | Legacy live runner uruchamiany z repo przez `uv run --locked`; nie aktywuje Condy. |
| `README.md` | Entry point repo. Quick start + linki do docs. |
| `.gitignore` | Comprehensive (secrets, Python cache, venvs, IDE, OS, bot_data, results). |
| `.deploy_key` (gitignored) | SSH private key dla GitHub deploy access. |

---

## Co jeszcze przyjdzie

Planowane katalogi po obecnej fazie, zgodnie z [ROADMAP](../ROADMAP.md):

### Faza 3
- `algo_bot/alerts/telegram.py` — alerty na Telegram
- Refaktor `live/live_binance.py` → `algo_bot/live/binance.py` + module structure

### Faza 5
- `Dockerfile` + `docker-compose.yml`
- `deploy/setup_vps.sh`
- `deploy/systemd/algo_bot.service`
- `algo_bot/monitoring/exporter.py` — Prometheus exporter

Każdy nowy moduł dochodzi z **per-file header docstring** (5-15 linii) + ewentualnie `docs/reference/modules/<modul>.md` (dla rozbudowanych).
