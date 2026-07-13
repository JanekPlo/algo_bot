# Makefile Cheatsheet

Każdy aktualny `make <target>` w algo_bot — co robi, kiedy go używać i co
uruchamia pod spodem. `make help` (albo samo `make`) wypisuje tę listę z
bieżącego Makefile.

## Kontrakt runtime Beta 0

Makefile zakłada **uv 0.11.28**, `.python-version` z **CPython 3.12.13** oraz
kanoniczny `uv.lock`. Nie trzeba aktywować `.venv`. Targety wykonujące Python
korzystają z:

```make
UV     ?= uv
UV_RUN := $(UV) run --locked
```

Dlatego `make test` i pozostałe bramki zawsze działają w środowisku projektu.
Conda/`environment.yml` oraz pip-tools jako domyślny workflow są
**superseded**.

## Setup i zależności

### `make env`

Tworzy lub synchronizuje `.venv` dokładnie według lockfile:

```bash
uv sync --locked
```

Użyj po pierwszym clone, po zmianie `uv.lock` na branchu albo gdy lokalne
środowisko się rozjechało. uv odczytuje Python 3.12.13 z `.python-version` i w
razie potrzeby pobiera vanilla CPython. Instalowane są m.in. przypięte
NautilusTrader 1.230.0 i TA-Lib 0.7.0; wheel TA-Lib zawiera bibliotekę C.

### `make install`

Obecnie jest czytelnym aliasem do tej samej operacji:

```bash
uv sync --locked
```

`uv sync` instaluje projekt editable, entry pointy `[project.scripts]` i grupę
dev. Nie wykonuj po nim osobnego `pip install -e .`.

### `make sync`

Również wykonuje `uv sync --locked`. Nazwy `env`, `install` i `sync` zachowano
dla istniejących runbooków; wszystkie prowadzą do jednego, reprodukowalnego
mechanizmu.

Najczęstsze użycie:

```bash
git pull origin master
make sync
make check
```

### `make lock`

Aktualizuje kanoniczny lockfile:

```bash
uv lock
```

Uruchamiaj tylko po świadomej zmianie zależności w `pyproject.toml` lub gdy
celowo odświeżasz wersje dozwolone przez constrainty. Potem wykonaj
`make sync` i pełne `make check`. `uv.lock` commituj razem z deklaracją deps.

### `make export-requirements`

Generuje niekanoniczny eksport kompatybilności:

```bash
uv export --locked --group dev --no-emit-project --no-hashes --output-file requirements.txt
```

`requirements.txt` nie jest lockfilem używanym przez lokalny setup ani CI.
Nie edytuj go ręcznie; odświeżaj tym targetem po `make lock`, jeśli eksport ma
pozostać zsynchronizowany.

## Testy, lint i typy

Wszystkie poniższe komendy rozwijają się do `uv run --locked ...`.

### `make test`

Pełny pytest:

```bash
uv run --locked pytest
```

Użyj po zmianach funkcjonalnych albo przez `make check` przed pushem.

### `make test-fast`

Pomija testy `slow` i `integration`:

```bash
uv run --locked pytest -m "not slow and not integration"
```

To najszybsza pętla podczas iteracji.

### `make test-cov`

Uruchamia testy z raportem terminalowym i HTML w `htmlcov/`:

```bash
uv run --locked pytest --cov=algo_bot --cov-report=term-missing --cov-report=html
```

### `make lint`

Read-only lint całego kodu:

```bash
uv run --locked ruff check algo_bot tests scripts live
```

### `make lint-fix`

Stosuje bezpieczne automatyczne poprawki Ruff:

```bash
uv run --locked ruff check --fix algo_bot tests scripts live
```

Zawsze obejrzyj diff przed commitem.

### `make format`

Formatuje kod:

```bash
uv run --locked ruff format algo_bot tests scripts live
```

### `make format-check`

Sprawdza format bez zmiany plików i zwraca błąd, jeśli formatter miałby coś
zmienić:

```bash
uv run --locked ruff format --check algo_bot tests scripts live
```

### `make typecheck`

Uruchamia mypy dla pakietu:

```bash
uv run --locked mypy algo_bot
```

Konfiguracja w `pyproject.toml` używa Python 3.12; legacy jest łagodniejsze,
a nowe moduły objęte wskazanymi override'ami są strict.

### `make check`

Pełna lokalna i CI bramka:

1. `make lint`
2. `make format-check`
3. `make typecheck`
4. `make test`

Pierwszy błąd zatrzymuje pipeline. Używaj przed każdym pushem; Beta 0 wymaga
zielonej pełnej bramki, nie tylko lint/typecheck.

## Komendy bota

### `make backtest`

Alias do `uv run --locked algo-backtest $(ARGS)`:

```bash
make backtest ARGS="--symbol BTC/USDT --timeframe 4h --strategy bghtrend_pullback --params '{}'"
```

Bez Makefile równoważna forma to:

```bash
uv run algo-backtest --symbol BTC/USDT --timeframe 4h \
  --strategy bghtrend_pullback --params '{}'
```

### `make sweep`

Alias do `uv run --locked algo-sweep $(ARGS)`:

```bash
make sweep ARGS="--strategy bghtrend_pullback --symbols BTC/USDT --timeframes 4h --start 2020-01-01 --end 2025-01-01 --space_file config/bghtrend_b1.yaml"
```

Bez Makefile użyj `uv run algo-sweep ...`.

## VPS research runner

### `make sync-up`

Wysyła `bot_data/processed/` z PC na VPS przez `scripts/vps-sync.sh`:

```bash
make sync-up VPS_HOST=algo-vps
```

### `make sync-down`

Pobiera `results/` z VPS na PC:

```bash
make sync-down VPS_HOST=algo-vps
```

Oba targety wymagają `VPS_HOST`. Nie uruchamiają Pythona; pełny runbook jest w
[vps-research-runner.md](vps-research-runner.md).

## Pre-commit

### `make precommit-install`

Instaluje hook repozytorium przez środowisko uv:

```bash
uv run --locked pre-commit install
```

Wykonaj raz po clone.

### `make precommit-run`

Uruchamia hooki po wszystkich plikach:

```bash
uv run --locked pre-commit run --all-files
```

## Maintenance

### `make clean`

Usuwa cache i artefakty (`__pycache__`, `.pytest_cache`, `.mypy_cache`,
`.ruff_cache`, `*.egg-info`, `dist`, `build`, `htmlcov`, coverage). Nie usuwa
`.venv`, `bot_data/`, `results/` ani źródeł.

### `make tree`

Pokazuje strukturę repo do głębokości 3, pomijając cache, środowiska, dane i
wyniki. Wymaga systemowego programu `tree`.

### `make help`

Wypisuje dostępne targety na podstawie komentarzy `##` w Makefile:

```bash
make help
```

## Override `UV`

Jedyną aktualnie nadpisywalną zmienną narzędziową jest `UV`:

```bash
make check UV="$HOME/.local/bin/uv"
```

Ścieżka musi prowadzić do uv 0.11.28. Historyczne override'y `PYTHON`, `PIP`,
`PIP_COMPILE` i `PIP_SYNC` już nie obowiązują.

## Szybkie reguły

- `make` bez argumentów to `make help`.
- Codziennie używaj `make sync`, a komendy CLI wywołuj jako `uv run algo-*`.
- `make env`, `make install`, `make sync` i kontrole jakości są idempotentne.
- `make backtest` i `make sweep` tworzą nowe wyniki.
- Po zmianie deps: `make lock`, `make sync`, `make export-requirements`,
  `make check`.
- Nie aktywuj dawnego env Conda i nie używaj globalnego `pip`.
