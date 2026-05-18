# algo_bot — RBI Trading Framework

> Quantitative trading framework dla kryptowalutowych perpetual futures, zbudowany wokół metodologii RBI (**R**esearch → **B**acktest → **I**mplement). Część projektu Digital Alchemy.

[![Status](https://img.shields.io/badge/status-alpha-orange)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

## Co tu jest

Lekki framework do:
- **Pobierania** historycznych danych OHLCV z giełd (Binance, Bybit) przez CCXT
- **Przetwarzania** surowych danych, liczenia wskaźników technicznych
- **Backtestowania** strategii (backtesting.py) z grid/random search po parametrach
- **Walk-forward analysis** dla rygorystycznego out-of-sample testowania *(faza 2)*
- **Live trading** na Binance Futures z hybrid TP/SL i journalingiem
- **Deployment na VPS** z monitoringiem 24/7 *(faza 5)*

## Quick start

```bash
# 1. Clone
git clone git@github.com:JanekPlo/algo_bot.git
cd algo_bot

# 2. Stwórz conda env (instaluje Python 3.11 + TA-Lib z conda-forge)
make env
conda activate algo_bot

# 3. Zainstaluj pakiet + dev deps
make install

# 4. Sprawdź setup
algo-backtest --help
make check     # ruff + mypy + pytest
```

Pełny walkthrough setupu: [docs/guides/getting-started.md](docs/guides/getting-started.md).

## Codzienna praca

```bash
# Backtest pojedynczej strategii
algo-backtest --symbol BTC/USDT --timeframe 4h --strategy bghtrend_pullback \
    --params '{"ema_fast":21,"ema_mid":89}'

# Sweep parametrów (grid lub random search)
algo-sweep --strategy bghtrend_pullback --symbols BTC/USDT --timeframes 4h \
    --start 2020-01-01 --end 2025-01-01 --space_file config/bghtrend_b1.yaml

# Testy / lint / typecheck
make test
make lint
make typecheck
make check       # wszystko razem (CI-style)
```

Więcej: [docs/guides/daily-workflow.md](docs/guides/daily-workflow.md), [docs/guides/makefile-cheatsheet.md](docs/guides/makefile-cheatsheet.md).

## Dokumentacja

Pełna dokumentacja w [`docs/`](docs/README.md):

- **[ROADMAP](docs/ROADMAP.md)** — plan rozwoju w 5 fazach (Foundation → Production na VPS)
- **[ARCHITECTURE](docs/ARCHITECTURE.md)** — warstwy systemu, mapa modułów
- **[Guides](docs/README.md#mapa-docs)** — getting started, daily workflow, makefile cheatsheet
- **[Reference](docs/reference/package-overview.md)** — encyklopedia (per moduł, config, metryki)
- **[Concepts](docs/concepts/glossary.md)** — koncepty, glossary, methodology
- **[ADR](docs/adr/README.md)** — Architecture Decision Records (dlaczego coś jest tak)
- **[CHANGELOG](docs/CHANGELOG.md)** — historia zmian

## Struktura repozytorium

```
algo_bot/                              # repo root (= ten plik)
├── algo_bot/                          # główny pakiet Python
│   ├── strategy_base.py               # StrategyBase + Signal — unified API
│   ├── strategy_loader.py             # dynamic loading strategii
│   ├── data_loader.py                 # CSV OHLCV reader/writer
│   ├── fetch_data.py                  # CCXT → bot_data/raw/*.csv
│   ├── process_data.py                # raw → processed z featurami
│   ├── executor.py                    # legacy CLI (FIXME)
│   ├── funding.py                     # funding rate (perp futures)
│   ├── engine/
│   │   ├── backtester.py              # silnik backtestowy (wrapper na backtesting.py)
│   │   ├── sweep.py                   # grid + random search
│   │   └── exchanges/binance_adapter  # CCXT wrapper
│   ├── strategies/                    # implementacje strategii
│   │   ├── bghtrend_pullback.py       # MVP candidate (trend + pullback + xtrender)
│   │   └── ... (6 więcej)
│   ├── indicators/                    # custom wskaźniki (xtrender, t3, ema, rsi, atr)
│   └── telemetry/journal.py           # CSV journal trades + equity
│
├── live/live_binance.py               # live trading loop (Binance Futures)
├── tests/                             # pytest
├── notebooks/                         # research notebooks (Jupyter)
├── scripts/                           # eksperymenty jednorazowe
├── config/                            # YAML configi strategii + globalne
├── docs/                              # dokumentacja (patrz docs/README.md)
├── bot_data/                          # dane (gitignored)
│   ├── raw/                           # surowe OHLCV
│   └── processed/                     # z featurami
├── results/                           # wyniki backtestów/sweepów/live (gitignored)
│
├── pyproject.toml                     # build + deps + tooling config (hatchling)
├── environment.yml                    # conda env (Python + TA-Lib)
├── requirements.txt                   # lockfile generowany przez pip-tools
├── Makefile                           # make help dla listy targetów
├── .gitignore
└── README.md                          # ten plik
```

Pełny opis: [docs/reference/package-overview.md](docs/reference/package-overview.md).

## Wymagania

- **Python 3.11+** (3.10 EOL paź 2026)
- **conda** lub **miniconda** (do envu z TA-Lib)
- **git**
- (opcjonalnie) konto Binance z API key dla live tradingu

System: testowane na WSL2 Ubuntu 22+. Powinno działać na Linux/macOS natywnie. Windows native nieoficjalnie wspierany (WSL preferowany).

## Status projektu

**Faza 1: Foundation** — w trakcie. Patrz [ROADMAP](docs/ROADMAP.md) i [CHANGELOG](docs/CHANGELOG.md).

Definicja sukcesu MVP: jedna strategia (kandydat: `bghtrend_pullback`) przejdzie ścieżkę research → in-sample backtest → walk-forward → testnet → mainnet (mały kapitał) → VPS 24/7 z alertami.

## Contributing

Repo jest aktualnie pojedynczego autora. Workflow w [docs/guides/daily-workflow.md](docs/guides/daily-workflow.md). Decyzje architektoniczne są dokumentowane jako [ADRs](docs/adr/README.md) — nowe znaczące decyzje wymagają nowego ADR.

Konwencje:
- Kod: ruff (lint + format), mypy strict-on-new-modules
- Docstrings: Google style
- Commits: imperative mood, conventional commits ish (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `build:`)
- Branches: aktualnie tylko `master`, feature branches gdy CI/PRs zostaną dodane

## Licencja

MIT. Autor: [JanekPlo](https://github.com/JanekPlo).

---

*"Możemy nie mamy miliardów jak Jane Street, ale musimy działać jak oni, a nie na odwal się."* — zasada projektu
