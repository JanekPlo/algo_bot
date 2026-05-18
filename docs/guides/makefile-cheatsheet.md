# Makefile Cheatsheet

Każdy `make <target>` w algo_bot wytłumaczony — co robi, kiedy używać, przykłady.

`make help` (lub po prostu `make` bez argumentów) wypisuje wszystkie dostępne targety.

## Setup

### `make env`

**Co**: tworzy conda env `algo_bot` z `environment.yml`. Jeśli env już istnieje — update (`conda env update --prune`).

**Kiedy używać**:
- Pierwszy raz po clone'ie repo
- Po zmianie `environment.yml` (np. bumpujemy Python version)
- Gdy chcesz wyczyścić env i zacząć od nowa (`conda env remove -n algo_bot` + `make env`)

**Pod spodem**:
```bash
conda env create -f environment.yml
# lub gdy env istnieje:
conda env update -f environment.yml --prune
```

**Czas**: ~3-5 min pierwszy raz (ściąganie pakietów), ~30s przy update.

**Co dalej**: `conda activate algo_bot && make install`

---

### `make install`

**Co**: `pip install -e ".[dev]"` — instaluje pakiet algo_bot w trybie editable z dev dependencies.

**Kiedy używać**:
- Po `make env` (pierwsza instalacja)
- Po zmianie struktury pakietu (np. dodanie nowego subpakietu w `algo_bot/`)
- Po zmianie `[project.scripts]` w pyproject.toml (regeneruje shim scripts dla CLI)

**Pod spodem**:
```bash
pip install -e ".[dev]"
```

**WAŻNE**: musi być uruchomione **wewnątrz aktywnego conda env** (`conda activate algo_bot` najpierw).

**Editable mode** znaczy że pip linkuje pakiet do tego folderu — zmiany w `.py` widoczne natychmiast, bez reinstall.

---

### `make lock`

**Co**: generuje `requirements.txt` z `pyproject.toml` używając pip-tools. Tworzy deterministic lockfile z dokładnymi wersjami + transitive deps.

**Kiedy używać**:
- Po dodaniu nowej deps do `[project.dependencies]` w pyproject.toml
- Po dodaniu deps do `[project.optional-dependencies] dev`
- Okresowo (raz na miesiąc) żeby pull najnowsze patch versions

**Pod spodem**:
```bash
pip-compile --extra dev --output-file=requirements.txt pyproject.toml
```

**Output**: `requirements.txt` zostaje nadpisany z lockfilem (z hashami, transitive deps, exact versions).

**Co dalej**: `make sync` żeby zsynchronizować env z lockfile.

---

### `make sync`

**Co**: instaluje DOKŁADNIE wersje z `requirements.txt` (lockfile). Usuwa wszystko czego nie ma w lockfile.

**Kiedy używać**:
- Po `make lock` (synchronizacja)
- Po `git pull` jeśli ktoś inny zmienił deps (zauważalne po `make check` failing z import errors)
- Gdy podejrzewasz że env się rozjechał z lockfile

**Pod spodem**:
```bash
pip-sync requirements.txt
pip install -e . --no-deps         # reinstall pakietu (bo pip-sync go usuwa)
```

**UWAGA**: `pip-sync` jest agresywne — usuwa pakiety których nie ma w `requirements.txt`. Jeśli ręcznie zainstalowałeś coś w env (np. `pip install ipdb`), `make sync` to usunie.

---

## Test / Lint / Type

### `make test`

**Co**: uruchamia pełny pytest na `tests/`.

**Kiedy używać**:
- Przed commitem (lepiej: `make check`)
- Po większych refactorach
- Gdy chcesz sprawdzić czy zmiana niczego nie psuje

**Pod spodem**:
```bash
pytest
```

Konfiguracja w `pyproject.toml [tool.pytest.ini_options]`:
- `testpaths = ["tests"]`
- `addopts = ["-v", "--strict-markers", "--tb=short"]`
- Markers: `slow`, `integration`, `live`

---

### `make test-fast`

**Co**: pytest pomijając slow i integration testy.

**Kiedy używać**:
- W codziennym cyklu edit→test→commit (gdy chcesz szybki feedback)
- Slow tests (np. backtest na 5 lat danych) odpalasz osobno gdy potrzeba

**Pod spodem**:
```bash
pytest -m "not slow and not integration"
```

---

### `make test-cov`

**Co**: pytest + coverage report. Generuje HTML w `htmlcov/`.

**Kiedy używać**:
- Przy review pokrycia testami (sprawdzasz które linie nieprzetestowane)
- Przed major release (sanity check że nic ważnego nie jest untested)

**Pod spodem**:
```bash
pytest --cov=algo_bot --cov-report=term-missing --cov-report=html
```

**Output**: terminal pokazuje % i miss linie. HTML: otwórz `htmlcov/index.html` w przeglądarce dla interaktywnego widoku.

---

### `make lint`

**Co**: ruff check — sprawdza style/błędy. Read-only (nie modyfikuje plików).

**Kiedy używać**:
- Przed commitem (lepiej: `make check`)
- Po edycji żeby sprawdzić warnings

**Pod spodem**:
```bash
ruff check algo_bot tests scripts live
```

Konfiguracja w `pyproject.toml [tool.ruff.lint]`. Aktywne rule sets:
- `E` (pycodestyle errors), `F` (pyflakes), `W` (pycodestyle warnings)
- `I` (isort), `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify), `RUF` (ruff-specific)
- `C4` (comprehensions), `PIE` (flake8-pie)

---

### `make lint-fix`

**Co**: ruff check --fix — naprawia co się da automatycznie.

**Kiedy używać**:
- Po napisaniu kawałka kodu, gdy widzisz warnings z `make lint`
- Wiele rzeczy ruff naprawi automatycznie (np. nieużywane importy, kolejność importów)

**Pod spodem**:
```bash
ruff check --fix algo_bot tests scripts live
```

**WAŻNE**: ruff fix jest bezpieczny dla większości rzeczy, ale **review diff przed commit**! Niektóre auto-fixy zmieniają semantykę (rzadko, ale możliwe).

---

### `make format`

**Co**: ruff format — auto-formatuje pliki (jak black, ale szybciej i bardziej deterministyczne).

**Kiedy używać**:
- Po napisaniu nowego kodu
- Przed commit żeby utrzymać spójny styl

**Pod spodem**:
```bash
ruff format algo_bot tests scripts live
```

Konfiguracja w `pyproject.toml [tool.ruff.format]`:
- `quote-style = "double"` (zawsze podwójne cudzysłowy)
- `indent-style = "space"`
- `line-length = 100` (z `[tool.ruff]`)

---

### `make format-check`

**Co**: ruff format --check — sprawdza czy pliki są sformatowane, ale NIE modyfikuje. Fail-exit jeśli coś jest niesformatowane.

**Kiedy używać**:
- W CI (sprawdzenie czy ktoś commituje niesformatowany kod)
- Część `make check`

**Pod spodem**:
```bash
ruff format --check algo_bot tests scripts live
```

---

### `make typecheck`

**Co**: mypy — statyczna analiza typów.

**Kiedy używać**:
- Przed commitem (lepiej: `make check`)
- Po dodaniu typehintów (sprawdzenie czy poprawne)
- Po zmianie sygnatur funkcji (czy callers są zgodni)

**Pod spodem**:
```bash
mypy algo_bot
```

Konfiguracja w `pyproject.toml [tool.mypy]`:
- Default lenient (legacy code)
- `[[tool.mypy.overrides]]` strict dla: `algo_bot.risk.*`, `algo_bot.engine.walkforward`, `algo_bot.metrics`
- Ignore errors dla bibliotek bez stubów: `backtesting.*`, `ccxt.*`, `talib.*`, `dotenv.*`

---

### `make check`

**Co**: uruchamia WSZYSTKO razem CI-style: lint + format-check + typecheck + test.

**Kiedy używać**:
- **Przed każdym push**
- Jako single command do sprawdzenia "czy wszystko OK"
- W CI workflow (gdy będzie skonfigurowany)

**Pod spodem**:
```bash
ruff check algo_bot tests scripts live && \
ruff format --check algo_bot tests scripts live && \
mypy algo_bot && \
pytest
```

Każdy fail wycina kolejne kroki — pierwsza rzecz która padnie zatrzymuje pipeline.

---

## Bot komendy

### `make backtest` / `make sweep`

**Co**: aliasy na CLI entries `algo-backtest` i `algo-sweep`. Pozwalają przekazywać argumenty przez `ARGS="..."`.

**Kiedy używać**:
- Konsystencja w skryptach automatyzacji
- Krócej niż `algo-backtest` w niektórych kontekstach (subjective)

**Przykład**:
```bash
make backtest ARGS="--symbol BTC/USDT --timeframe 4h --strategy bghtrend_pullback --params '{}'"
make sweep ARGS="--strategy bghtrend_pullback --symbols BTC/USDT --timeframes 4h --start 2020-01-01 --end 2025-01-01 --space_file config/bghtrend_b1.yaml"
```

**Pod spodem**:
```bash
algo-backtest $(ARGS)
algo-sweep $(ARGS)
```

W praktyce — używaj bezpośrednio `algo-backtest ...` bo czytelniej. Te aliasy istnieją dla konsystencji `make <wszystko>`.

---

## Pre-commit

### `make precommit-install`

**Co**: instaluje pre-commit hook do `.git/hooks/pre-commit`.

**Kiedy używać**:
- Jednorazowo po setupie repo
- Po zmianie `.pre-commit-config.yaml` (gdy powstanie)

**Pod spodem**:
```bash
pre-commit install
```

Po instalacji każdy `git commit` automatycznie uruchomi hooks (ruff, mypy etc.) na zmienionych plikach. Commit zostaje cofnięty jeśli hooks fail.

---

### `make precommit-run`

**Co**: uruchamia pre-commit na WSZYSTKICH plikach (nie tylko zmienionych).

**Kiedy używać**:
- Po dodaniu nowych hooks do `.pre-commit-config.yaml`
- Sanity check że całe repo jest "zgodne" z hooks

**Pod spodem**:
```bash
pre-commit run --all-files
```

---

## Maintenance

### `make clean`

**Co**: usuwa cache i build artifacts.

**Kiedy używać**:
- Gdy mypy/pytest cache się "zacina" (rzadko)
- Przed major operation żeby zacząć clean
- Przed `make tree` jeśli chcesz zobaczyć tylko prawdziwy content

**Pod spodem**: usuwa
- `__pycache__/`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `*.egg-info/`
- `dist/`, `build/`
- `htmlcov/`, `.coverage`, `coverage.xml`

NIE usuwa: `bot_data/`, `results/`, `notebooks/`, `.venv/` (jeśli jest), nic z source.

---

### `make tree`

**Co**: pokazuje strukturę repo (ignoruje cache i gitignored).

**Kiedy używać**:
- Quick orientation w repo
- Pre-commit check że nie ma dziwnych plików

**Pod spodem**:
```bash
tree -I '__pycache__|*.egg-info|.pytest_cache|.mypy_cache|.ruff_cache|htmlcov|dist|build|bot_data|results|.venv|venv' -L 3
```

`-L 3` = pokazuje 3 poziomy głęboko (więcej = noise).

Wymaga `tree` zainstalowane: `sudo apt install tree` (Ubuntu) lub `brew install tree` (macOS).

---

## `make help`

**Co**: lista wszystkich dostępnych targetów z opisami.

**Kiedy używać**: zawsze gdy zapomnisz co masz dostępne.

**Pod spodem**: awk parsuje komentarze `## <opis>` przy targetach w Makefile.

---

## Override zmiennych

Makefile używa zmiennych których możesz override:

```bash
# Użyj innego Pythona (np. dla testów kompatybilności):
make test PYTHON=python3.12

# Użyj innego pip (np. uv pip):
make install PIP="uv pip"

# Inny lockfile output path:
make lock PIP_COMPILE="pip-compile --output-file=lockfiles/dev.txt"
```

Aktualnie zdefiniowane override-owalne:
- `PYTHON` (default: `python`)
- `PIP` (default: `pip`)
- `PIP_COMPILE` (default: `pip-compile`)
- `PIP_SYNC` (default: `pip-sync`)

## Tips

- Run `make` (bez args) = `make help`
- Use tab completion: `make <tab><tab>` w bashu pokazuje wszystkie targety (wymaga `bash-completion` zainstalowane)
- Większość targetów jest **idempotentna** (możesz odpalić wielokrotnie bez efektów ubocznych): `make env`, `make install`, `make check`, `make clean`
- Nie idempotentne: `make backtest` (tworzy nowe wyniki), `make sweep` (j.w.) — bo to faktyczne uruchomienia bota

## Co zostanie dodane w przyszłych fazach

| Target | Faza | Co będzie robił |
|---|---|---|
| `make fetch` | 1 (po `main()` w fetch_data) | Pobiera dane OHLCV z Binance |
| `make process` | 1 (po `main()` w process_data) | Surowy CSV → processed z featurami |
| `make walkforward` | 2 (po decyzji F) | Walk-forward analiza |
| `make live-testnet` | 3 | Uruchamia live_binance na testnecie |
| `make live-mainnet` | 4 | Live na mainnecie z confirmation prompt |
| `make docker-build` | 5 | Build Docker image dla VPS |
| `make deploy` | 5 | Deploy na VPS |
| `make docs-serve` | post-MVP | Lokalny MkDocs preview (gdy migrujemy) |
| `make docs-deploy` | post-MVP | Deploy docs na GitHub Pages |
