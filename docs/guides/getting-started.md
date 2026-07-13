# Getting Started

Pełny walkthrough setupu algo_bot od zera. Domyślny runtime Beta 0 to
**uv 0.11.28 + vanilla CPython 3.12.13 + `uv.lock`**. Pierwsza instalacja
zwykle zajmuje kilka minut.

## Wymagania systemowe

- **OS**: Linux (Ubuntu 22+) lub macOS. Na Windows używaj WSL2.
- **uv**: dokładnie 0.11.28.
- **Python**: nie musi być zainstalowany systemowo; uv pobierze vanilla CPython
  3.12.13 wskazany przez `.python-version`.
- **git**, **make** i **curl**.
- Około **3 GB** wolnego miejsca na interpreter, `.venv` i zależności.

Conda nie jest domyślnym ani równorzędnym workflow. Historyczny setup z
`environment.yml`, Minicondą i TA-Lib z conda-forge jest **superseded**.
Wróć do niego wyłącznie jako diagnostycznego fallbacku, jeżeli standardowy
smoke test uv ujawni konkretny blocker platformy.

### Sprawdź narzędzia bazowe

```bash
git --version
make --version
curl --version
```

## Krok 1 — zainstaluj przypięte uv

Oficjalny instalator z wersjonowanego URL:

```bash
curl -LsSf https://astral.sh/uv/0.11.28/install.sh | sh
```

Otwórz nowy terminal (albo wykonaj instrukcję aktualizacji `PATH` wypisaną
przez instalator), a następnie sprawdź wersję:

```bash
uv --version
# uv 0.11.28
```

Nie używaj nieprzypiętego `latest` w CI lub na VPS. Jeżeli masz inną wersję uv,
ponownie uruchom powyższy instalator.

## Krok 2 — sklonuj repo

```bash
mkdir -p ~/quant_projects
cd ~/quant_projects
git clone git@github.com:JanekPlo/algo_bot.git
cd algo_bot
```

Jeżeli nie masz skonfigurowanego klucza SSH:

```bash
git clone https://github.com/JanekPlo/algo_bot.git
```

## Krok 3 — odtwórz środowisko

```bash
make env
```

Target wykonuje `uv sync --locked`. uv:

- odczytuje `3.12.13` z `.python-version` i w razie potrzeby pobiera vanilla
  CPython;
- tworzy repozytoryjne `.venv`;
- instaluje projekt editable oraz domyślną grupę dev;
- odtwarza dokładny graf zależności zapisany w `uv.lock`.

W Beta 0 kluczowe przypięcia runtime to:

- **NautilusTrader 1.230.0** (stabilne wydanie, bez nightly/pre-release);
- **TA-Lib 0.7.0**; publikowany wheel zawiera również bibliotekę C, więc nie
  instaluj systemowego `libta-lib` ani pakietu z conda-forge;
- legacy **backtesting.py 0.6.5**, utrzymany na czas migracji silnika.

`make install` jest obecnie równoważnym aliasem do `uv sync --locked`. Po clone
wystarczy jeden z tych targetów.

## Krok 4 — uruchamiaj przez `uv run`

Nie aktywuj `.venv` i nie polegaj na globalnym `python`, `pip` ani CLI z
`PATH`. Standardem projektu jest `uv run`:

```bash
uv run python --version
uv run algo-backtest --help
uv run algo-fetch --help
uv run algo-process --help
uv run algo-sweep --help
uv run algo-walkforward --help
```

Makefile robi to samo z dodatkowym `--locked`, dlatego `make test`,
`make lint` i `make check` są również bezpiecznymi entry pointami.

## Krok 5 — zweryfikuj runtime

```bash
uv --version
uv run python --version
uv run python -c 'from importlib.metadata import version; print("TA-Lib", version("TA-Lib")); print("NautilusTrader", version("nautilus-trader"))'
uv run python -c 'import talib, nautilus_trader; print("runtime imports: OK")'
make check
```

Oczekiwane wersje to odpowiednio uv 0.11.28, Python 3.12.13, TA-Lib 0.7.0
i NautilusTrader 1.230.0. `make check` jest twardą bramką: lint,
format-check, mypy i pełny pytest muszą przejść.

## Krok 6 — pre-commit hooks (opcjonalnie)

```bash
make precommit-install
make precommit-run      # ręczny przebieg po całym repo
```

Hook uruchamia szybkie kontrole plików oraz Ruff. Pełna bramka projektu
pozostaje w `make check`.

## Krok 7 — pierwszy backtest

Jeżeli nie masz `bot_data/processed/binance_BTCUSDT_4h.csv`, najpierw pobierz
i przetwórz dane:

```bash
uv run algo-fetch BTC/USDT 4h --start 2020-01-01
uv run algo-process
```

Potem uruchom backtest:

```bash
uv run algo-backtest \
    --symbol BTC/USDT \
    --timeframe 4h \
    --strategy bghtrend_pullback \
    --params '{"ema_fast":21,"ema_mid":89,"ema_slow":200}'
```

Output trafia do `results/backtests/<run_id>/`:

- `summary.json` — metryki i metadata;
- `equity.csv` — krzywa kapitału;
- `trades.csv` — log transakcji;
- `params.json` — użyte parametry.

## Krok 8 — notebooki i IDE (opcjonalnie)

Notebooki są osobną grupą zależności:

```bash
uv sync --locked --group notebooks
uv run --group notebooks jupyter lab notebooks/
```

W VS Code lub PyCharm wybierz interpreter `<repo>/.venv/bin/python` (na
Windows native: `<repo>/.venv/Scripts/python.exe`). Nie wybieraj dawnego
interpretera Conda. Dla VS Code wystarczy m.in.:

```json
{
    "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit"
        }
    },
    "python.testing.pytestEnabled": true,
    "python.testing.pytestArgs": ["tests"]
}
```

## CI

GitHub Actions uruchamia `make check` na pull requestach i pushach do
`master`. Workflow przypina uv 0.11.28 i Python 3.12.13, wykonuje
`uv sync --locked`, a następnie tę samą bramkę co lokalnie. Domyślny workflow
nie używa sekretów ani live exchange/API tests.

## Co dalej

- **[Daily Workflow](daily-workflow.md)** — codzienne komendy i cykl pracy
- **[Makefile Cheatsheet](makefile-cheatsheet.md)** — każdy target Makefile
- **[Data fetching](data-fetching.md)** — przygotowanie danych
- **[Running a backtest](running-backtest.md)** — pełny runbook backtestu
- **[Package Overview](../reference/package-overview.md)** — mapa repo

## Troubleshooting

### `uv: command not found`

Otwórz nowy terminal po instalacji i wykonaj instrukcję aktualizacji `PATH`
wypisaną przez instalator. Następnie `uv --version` musi zwrócić 0.11.28.

### `uv sync --locked` zgłasza nieaktualny lockfile

Najpierw zaktualizuj checkout (`git pull`) i upewnij się, że `pyproject.toml`
oraz `uv.lock` pochodzą z tego samego commita. Zwykły setup nie powinien
uruchamiać `uv lock`; ten target służy wyłącznie do świadomej zmiany deps.

### `import talib` lub `import nautilus_trader` kończy się błędem

```bash
make sync
uv run python -c 'import talib, nautilus_trader; print("OK")'
```

Nie diagnozuj importu przez systemowy `python`. Jeżeli uv nie znajduje wheel
dla konkretnej platformy, zachowaj pełny komunikat z `uv sync --locked` — to
jest warunek do rozważenia odseparowanego fallbacku, nie powód do zmiany
domyślnego workflow na Condę.

### `algo-backtest: command not found`

Użyj entry pointu przez środowisko projektu:

```bash
uv run algo-backtest --help
```

Jeżeli nadal go nie ma, wykonaj `make sync`; uv instaluje projekt editable z
sekcji `[project.scripts]`.

### `make: command not found` na macOS

```bash
xcode-select --install
```

### Import `algo_bot` nie działa

```bash
uv run python -c 'import algo_bot; print(algo_bot.__file__)'
uv pip show algo_bot
```

W razie braku pakietu wykonaj `uv sync --locked` zamiast ręcznego
`pip install -e .`.
