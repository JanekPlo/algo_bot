# Getting Started

Pełny walkthrough setupu algo_bot od zera. Wymaga **~10 minut** pierwszym razem.

## Wymagania systemowe

- **OS**: Linux (Ubuntu 22+) lub macOS. Windows native niezalecane — używaj WSL2.
- **Python**: 3.11+ (instalowane automatycznie przez conda)
- **conda lub miniconda**: do zarządzania env z TA-Lib
- **git**: do clone'a repo
- **make**: standardowy GNU make (na Ubuntu domyślnie, na macOS: `xcode-select --install`)
- **~3 GB** wolnego miejsca (conda env + deps)

### Sprawdź co masz

```bash
git --version       # >= 2.0
make --version      # >= 3.81
conda --version     # >= 4.10 (jeśli zainstalowane)
```

### Brakuje conda?

```bash
# Linux/WSL:
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
# (zaakceptuj defaultową ścieżkę, na końcu wybierz `yes` dla conda init)
source ~/.bashrc

# macOS (Intel):
brew install --cask miniconda
# macOS (Apple Silicon):
brew install --cask miniconda

# Po instalacji w nowej shellu:
conda --version  # powinno działać
```

## Krok 1 — Clone repo

```bash
mkdir -p ~/quant_projects
cd ~/quant_projects
git clone git@github.com:JanekPlo/algo_bot.git
cd algo_bot
```

(Lub przez HTTPS: `git clone https://github.com/JanekPlo/algo_bot.git` jeśli nie masz SSH key skonfigurowanego.)

## Krok 2 — Stwórz conda env

```bash
make env
```

To uruchomi `conda env create -f environment.yml`. Tworzy env o nazwie `algo_bot` z:
- Python 3.11
- TA-Lib (system library + Python bindings z conda-forge)
- numpy, pandas, scipy (z MKL/OpenBLAS — szybsze niż PyPI wheels)
- jupyterlab + ipykernel (dla notebooków research)
- pip (do instalacji reszty)

Czas: **~3-5 minut** (conda ściąga ~500 MB).

Jeśli env już istnieje, `make env` zrobi `conda env update -f environment.yml --prune` (idempotentne).

## Krok 3 — Aktywuj env

```bash
conda activate algo_bot
```

Twój prompt powinien teraz zaczynać się od `(algo_bot)`:
```
(algo_bot) janek@hostname:~/quant_projects/algo_bot$
```

**WAŻNE**: env musi być aktywowane przed KAŻDĄ pracą. Stała konwencja: pierwszy krok dnia = `conda activate algo_bot`.

## Krok 4 — Zainstaluj pakiet

```bash
make install
```

To uruchomi `pip install -e ".[dev]"` — instaluje algo_bot w trybie editable z dev dependencies:
- Runtime: ccxt, pandas, backtesting, PyYAML, python-dotenv, tzdata, matplotlib
- Dev: pytest, pytest-cov, ruff, mypy, pip-tools, pre-commit, pandas-stubs, types-PyYAML

Czas: **~30-60 sekund**.

"Editable mode" znaczy że zmiany w plikach `.py` są widoczne natychmiast — nie trzeba reinstall po edycji.

## Krok 5 — Sprawdź setup

```bash
# CLI entries (powinny działać po pip install):
algo-backtest --help
algo-fetch --help
algo-process --help
algo-sweep --help

# Sprawdź import pakietu z dowolnego miejsca:
python -c "from algo_bot.strategy_base import StrategyBase; print('OK:', StrategyBase)"

# Sprawdź TA-Lib:
python -c "import talib; print('TA-Lib:', talib.__version__)"

# Sprawdź wszystko razem (CI-style):
make check
```

`make check` uruchamia:
1. `ruff check` (lint)
2. `ruff format --check` (style)
3. `mypy algo_bot` (typecheck)
4. `pytest` (testy)

Częściowe failures są OK na MVP (legacy kod ma TODO znane bugi). Powinno przejść **lint** i **typecheck** clean.

## Krok 6 — Pre-commit hooks (opcjonalnie)

```bash
make precommit-install
```

Installs `.git/hooks/pre-commit`. The hook runs fast local file checks:
standard whitespace/YAML/TOML checks plus `ruff-check` and `ruff-format`.
`mypy` stays in the heavier project gate through `make check` and CI.

You can run the same hooks manually:

```bash
make precommit-run
```

## Krok 6a — CI behaviour

GitHub Actions runs `make check` on every pull request and every push to
`master`. CI uses `environment.yml` through micromamba, so it gets the same
Python 3.11 + TA-Lib setup as local development. CI does not use secrets and
does not run live exchange/API tests by default.

## Krok 7 — Pierwszy backtest (smoke test)

**Najpierw potrzebne dane historyczne**. Jeśli nie masz `bot_data/processed/binance_BTCUSDT_4h.csv`:

```bash
# Pobierz dane (CCXT → bot_data/raw/):
algo-fetch BTC/USDT 4h --start 2020-01-01

# Przetwórz raw → processed z indykatorami:
algo-process
```

Potem pierwszy backtest:

```bash
algo-backtest \
    --symbol BTC/USDT \
    --timeframe 4h \
    --strategy bghtrend_pullback \
    --params '{"ema_fast":21,"ema_mid":89,"ema_slow":200}'
```

Output trafia do `results/backtests/<run_id>/`:
- `summary.json` — metryki (Sharpe, Calmar, drawdown, win rate, ...)
- `equity.csv` — equity per bar
- `trades.csv` — log transakcji
- `params.json` — użyte parametry + metadata

## Krok 8 — IDE setup (opcjonalnie, ale zalecane)

### VSCode

Stwórz `.vscode/settings.json`:
```json
{
    "python.defaultInterpreterPath": "~/miniconda3/envs/algo_bot/bin/python",
    "python.linting.enabled": false,
    "python.formatting.provider": "none",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit"
        }
    },
    "ruff.organizeImports": true,
    "python.testing.pytestEnabled": true,
    "python.testing.pytestArgs": ["tests"]
}
```

Rozszerzenia VSCode do zainstalowania:
- **Python** (Microsoft) — Pylance, debug
- **Ruff** (charliermarsh) — auto-format on save
- **Even Better TOML** — syntax highlighting dla `pyproject.toml`
- **Jupyter** — notebooks

### PyCharm

Settings → Project → Python Interpreter → Add → Conda Environment → Existing → wybierz env `algo_bot`.

## Co dalej

- **[Daily Workflow](daily-workflow.md)** — codzienne komendy i cykl pracy
- **[Makefile Cheatsheet](makefile-cheatsheet.md)** — każdy `make <target>` wytłumaczony
- **[Package Overview](../reference/package-overview.md)** — co siedzi w którym katalogu
- **[Architecture](../ARCHITECTURE.md)** — wysokopoziomowa mapa systemu

## Troubleshooting

### `make env` fails z "package not found"

Czasem conda-forge channel ma chwilowe problemy. Spróbuj:
```bash
conda clean --all
conda env create -f environment.yml --force-reinstall
```

### `import talib` ImportError

TA-Lib NIE jest w pip dependencies — musi być z conda. Sprawdź:
```bash
conda activate algo_bot
conda list | grep ta-lib
# Powinno pokazać: ta-lib    <version>    <build>    conda-forge
```

Jeśli brak: `conda install -c conda-forge ta-lib`

### `algo-backtest: command not found`

CLI entries są tworzone przez `pip install -e .` i żyją w env's `bin/`. Sprawdź:
```bash
which algo-backtest
# Powinno pokazać: ~/miniconda3/envs/algo_bot/bin/algo-backtest
```

Jeśli brak — env nie jest aktywowany albo `make install` nie został uruchomiony.

### `make: command not found` (macOS)

```bash
xcode-select --install
```

### `git clone` fails z permission denied (SSH)

Albo nie masz SSH key w GitHubie, albo użyj HTTPS:
```bash
git clone https://github.com/JanekPlo/algo_bot.git
```

### Importy `from algo_bot.X` ImportError mimo `pip install -e .`

Sprawdź czy env aktywne (`conda activate algo_bot`) i czy install się powiódł:
```bash
pip show algo_bot
# Powinno pokazać: Location: /path/to/repo (editable install)
```

Jeśli brak:
```bash
pip install -e ".[dev]"  # reinstall
```
