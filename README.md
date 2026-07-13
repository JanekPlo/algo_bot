# algo_bot — RBI Trading Framework

> Quantitative trading framework dla kryptowalutowych perpetual futures, zbudowany wokół metodologii RBI (**R**esearch → **B**acktest → **I**mplement). Część projektu Digital Alchemy.

[![Status](https://img.shields.io/badge/status-Beta%20iterate-blue)]()
[![CI](https://github.com/JanekPlo/algo_bot/actions/workflows/check.yml/badge.svg?branch=master)](https://github.com/JanekPlo/algo_bot/actions/workflows/check.yml)
[![Python](https://img.shields.io/badge/python-3.12.13-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

## Co tu jest

Lekki framework do:
- **Pobierania** historycznych danych OHLCV z giełd (Binance, Bybit) przez CCXT
- **Przetwarzania** surowych danych, liczenia wskaźników technicznych
- **Backtestowania** strategii: przypięty `backtesting.py` dla ścieżki legacy oraz
  event-driven NautilusTrader dla nowych automatów stanów
- **Walk-forward analysis** dla rygorystycznego out-of-sample testowania *(faza 2)*
- **Live trading** na Binance Futures z hybrid TP/SL i journalingiem
- **Deployment na VPS** z monitoringiem 24/7 *(faza 5)*

## Quick start

```bash
# 1. Clone
git clone git@github.com:JanekPlo/algo_bot.git
cd algo_bot

# 2. Zainstaluj przypięte uv (jednorazowo)
curl -LsSf https://astral.sh/uv/0.11.28/install.sh | sh
uv --version       # uv 0.11.28

# 3. Odtwórz środowisko z .python-version i uv.lock
make env            # uv sync --locked; tworzy .venv z CPython 3.12.13

# 4. Sprawdź setup
uv run algo-backtest --help
make check     # ruff + mypy + pytest

# 5. Optional: enable local pre-commit hooks
make precommit-install
```

Pełny walkthrough setupu: [docs/guides/getting-started.md](docs/guides/getting-started.md).

## Codzienna praca

```bash
# Backtest pojedynczej strategii
uv run algo-backtest --symbol BTC/USDT --timeframe 4h --strategy bghtrend_pullback \
    --params '{"ema_fast":21,"ema_mid":89}'

# Pobranie i przetworzenie danych
uv run algo-fetch BTC/USDT 4h --start 2020-01-01
uv run algo-process

# Sweep parametrów (grid lub random search)
uv run algo-sweep --strategy bghtrend_pullback --symbols BTC/USDT --timeframes 4h \
    --start 2020-01-01 --end 2025-01-01 --space_file config/bghtrend_b1.yaml

# Testy / lint / typecheck
make test
make lint
make typecheck
make check       # wszystko razem (CI-style)
```

GitHub Actions runs `make check` on pull requests and pushes to `master`.
Pre-commit runs fast local file checks (`make precommit-install` to enable it).

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
│   ├── funding.py                     # funding rate (perp futures)
│   ├── engine/
│   │   ├── backtester.py              # legacy backtesting.py + rich result factory
│   │   ├── backtest_result.py         # wersjonowany wynik i audyt kosztów
│   │   ├── nautilus_adapter.py        # Tier-1 compatibility/equivalence
│   │   ├── nautilus_mastermind.py     # cienki wrapper PyO3 dla MMS v2
│   │   ├── mms_beta_data.py           # development-only data boundary
│   │   ├── mms_beta_benchmark.py      # zamrożony runner ablation P9
│   │   ├── sweep.py                   # grid + random search
│   │   └── exchanges/binance_adapter  # CCXT wrapper
│   ├── strategies/                    # implementacje strategii
│   │   ├── mastermind/                # pure, engine-independent MMS v2 domain
│   │   ├── mean_reversion_bb_stoch.py # legacy bare-core baseline (NO-GO)
│   │   └── bghtrend_pullback.py       # historyczny baseline (NO-GO)
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
├── .python-version                    # przypięty vanilla CPython 3.12.13
├── uv.lock                            # kanoniczny lockfile całego grafu deps
├── requirements.txt                   # eksport kompatybilności z uv.lock
├── Makefile                           # make help dla listy targetów
├── .gitignore
└── README.md                          # ten plik
```

Pełny opis: [docs/reference/package-overview.md](docs/reference/package-overview.md).

## Wymagania

- **uv 0.11.28** (domyślny i wspierany manager środowiska)
- **vanilla CPython 3.12.13** (przypięty w `.python-version`, pobierany przez uv)
- **git**
- (opcjonalnie) konto Binance z API key dla live tradingu

`uv sync --locked` instaluje z `uv.lock` m.in. dokładnie
**NautilusTrader 1.230.0** i **TA-Lib 0.7.0**. Wheel TA-Lib zawiera bibliotekę
C, więc systemowe TA-Lib i Conda nie są potrzebne. Dawny workflow
`environment.yml` + Conda jest materiałem historycznym i został zastąpiony;
Conda może służyć tylko jako awaryjna ścieżka po wykazaniu konkretnego blockera.

System: testowane na WSL2 Ubuntu 22+. Powinno działać na Linux/macOS natywnie. Windows native nieoficjalnie wspierany (WSL preferowany).

## Status projektu

**MR-Session 3 Beta — zakończona, decyzja `ITERATE BETA`** — Python 3.12.13 + uv + przypięte zależności; równoległe
ścieżki `backtesting.py` i NautilusTrader 1.230.0; wykonywalna specyfikacja oraz
implementacja MMS-inspired v2 H1/BB. Zamrożony P9 ukończył 12/12 runów i 264/264
kontrole invariantów, ale wszystkie wyniki pozostają `SMOKE_ONLY / NOT_ELIGIBLE`.
Nie stanowią dowodu edge ani gotowości live; Session 4 czeka na usunięcie blokad
parytetu wykonania i jakości danych/kosztów. Aktualny zakres: [raport P9](docs/experiments/mms-v2-beta-results.md), [ROADMAP](docs/ROADMAP.md),
[ADR-014](docs/adr/014-engine-migration-nautilus.md) i
[specyfikacja v2](docs/specs/mms-v2-executable-spec.md).

Definicja sukcesu MVP: jedna strategia przejdzie ścieżkę research → in-sample
backtest → walk-forward → testnet → mainnet (mały kapitał) → VPS 24/7 z alertami.
`bghtrend_pullback` i bare-core mean reversion pozostają udokumentowanymi
negatywnymi baseline'ami, nie aktualnymi kandydatami do wdrożenia.

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
