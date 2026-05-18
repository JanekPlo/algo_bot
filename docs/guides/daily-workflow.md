# Daily Workflow

Co robisz codziennie pracując nad algo_bot. Cykl edit → test → commit → push.

## Poranny start

```bash
# 1. Aktywuj env (KAŻDORAZOWO po otwarciu nowego terminala)
conda activate algo_bot

# 2. Idź do repo
cd ~/quant_projects/algo_bot

# 3. Pobierz najnowszy stan z GitHuba
git pull origin master

# 4. Sprawdź czy nic się nie popsuło po pull (deps mogły się zmienić)
make check         # ruff + mypy + pytest
# Albo szybciej (bez mypy):
make test-fast
```

Jeśli `make check` faili po pull — najpewniej zmieniły się deps. Zsynchronizuj:
```bash
make sync          # = pip-sync requirements.txt + pip install -e . --no-deps
```

## Cykl pracy nad kodem

### Standardowy flow

```bash
# 1. Edytuj pliki (VSCode / PyCharm / nano / whatever)
vim algo_bot/strategies/my_new_strategy.py

# 2. Po większych zmianach — auto-format + lint fix
make format        # ruff format
make lint-fix      # ruff check --fix (naprawia co się da auto)

# 3. Sprawdź typy
make typecheck     # mypy algo_bot

# 4. Uruchom testy (tylko fast):
make test-fast     # pytest -m "not slow and not integration"

# 5. Jeśli wszystko OK — pełny check przed commit:
make check         # lint + format-check + typecheck + test

# 6. Commit
git add -A
git status         # zobacz co stage'owałeś
git diff --cached  # przegląd diff'a (kluczowy krok przed commit!)
git commit -m "<typ>: <co zrobiles>"

# 7. Push do GitHuba
git push origin master
```

### Iteracyjne tweakowanie (np. parametry strategii)

Gdy iterujesz na strategii — kompresujesz cykl:

```bash
# Loop:
# 1. Zmień parametr w strategii / configu
# 2. Odpal backtest:
algo-backtest --symbol BTC/USDT --timeframe 4h --strategy bghtrend_pullback \
    --params '{"ema_fast":34,"ema_mid":89,"ema_slow":200}'
# 3. Sprawdź wyniki w results/backtests/<run_id>/summary.json
cat results/backtests/<run_id>/summary.json | jq '."Sharpe Ratio"'
# 4. Powtórz z innym parametrem
```

Dla większej skali — sweep:
```bash
algo-sweep --strategy bghtrend_pullback --symbols BTC/USDT --timeframes 4h \
    --start 2022-01-01 --end 2025-01-01 \
    --space_file config/bghtrend_b1.yaml \
    --mode grid

# Wyniki agregują się w results/experiments/index.csv
# Posortuj po Sharpe:
sort -t',' -k<column> results/experiments/index.csv | head
```

### Notebooks (research)

```bash
# Notebooks dziedziczą Python z conda env "algo_bot" (przez ipykernel)
jupyter lab notebooks/

# Importy działają natywnie:
# from algo_bot.engine.backtester import run_backtest
# from algo_bot.indicators import ema, rsi
```

## Konwencje commitów

Format: `<typ>: <imperative description>`

**Typy:**
- `feat:` — nowa funkcjonalność (strategia, moduł, feature)
- `fix:` — naprawa buga
- `docs:` — zmiany w `docs/` lub docstringach
- `refactor:` — restrukturyzacja kodu bez zmiany zachowania
- `chore:` — drobne rzeczy (gitignore, requirements, configi)
- `build:` — zmiany w build system (pyproject.toml, Makefile, environment.yml)
- `test:` — dodanie/zmiana testów
- `perf:` — optymalizacja performance
- `style:` — formatowanie (ruff format)

**Przykłady:**
```
feat: add walk-forward analyzer w algo_bot/engine/walkforward.py
fix: poprawa overflow w xtrender przy danych >1M punktów
docs: ADR-007 risk management API
refactor: wydzielenie load_processed do osobnej funkcji
chore: bump backtesting do 0.3.3
test: dodaj fixture dla 4h BTC/USDT data
```

**Body** (opcjonalnie, gdy zmiana jest niebanalna):

```
feat: add walk-forward analyzer w algo_bot/engine/walkforward.py

- Rolling window split (train_bars=N, test_bars=M, step=K)
- Aggregation: concatenacja trades z OOS folds + przeliczenie metryk
- Wsparcie dla custom objective (Sharpe, Calmar, profit factor)

Powiazany: ADR-007, ROADMAP faza 2.
```

**Co NIE robić:**
- ❌ `fix bug` (nie wiadomo jaki)
- ❌ `update` (nieinformacyjne)
- ❌ `WIP` na master (use feature branch jeśli WIP)
- ❌ Długie one-liner messages (jeśli zmiana wymaga >50 znaków opisu, daj body)

## Przed pushem — checklist

```bash
# 1. Diff review
git log @{u}..HEAD --stat   # co pushujesz vs co jest na remote
git diff @{u}..HEAD         # pełen diff

# 2. Czy testy przechodzą
make check

# 3. Czy commity są atomowe i opisane (jedna logiczna zmiana per commit)
git log @{u}..HEAD --oneline

# 4. Czy nie pushujesz secretów
git diff @{u}..HEAD | grep -iE "(api_key|secret|password|token)"

# 5. Push
git push origin master
```

## Po zmianie dependencies

Gdy zmienisz `pyproject.toml` `[project.dependencies]` lub `[project.optional-dependencies]`:

```bash
# 1. Regeneruj lockfile
make lock          # = pip-compile pyproject.toml -o requirements.txt

# 2. Zsynchronizuj env
make sync          # = pip-sync requirements.txt + pip install -e . --no-deps

# 3. Commit razem pyproject + requirements.txt
git add pyproject.toml requirements.txt
git commit -m "chore: bump <package> do <version> (powod: <powod>)"
```

**Reguła**: NIGDY nie commituj pyproject bez requirements.txt (lockfile niespójny z deklaracją).

## Praca z notebookami

Notebooks są w `notebooks/` ale ich `.ipynb` zawiera outputy które puchną git diffy. **Zalecane**:

```bash
# Przed commit notebooka — wyczyść outputy:
jupyter nbconvert --clear-output --inplace notebooks/03_my_research.ipynb

# Albo zainstaluj pre-commit hook nbstripout (TODO faza 1):
pip install nbstripout
nbstripout --install
```

## Częste komendy — cheatsheet

```bash
# Setup
conda activate algo_bot      # KAŻDY terminal

# Code quality
make check                   # wszystko CI-style
make lint                    # tylko lint
make format                  # auto-format
make typecheck               # tylko mypy
make test-fast               # szybkie testy

# Backtest
algo-backtest --symbol ... --timeframe ... --strategy ... --params '{...}'
algo-sweep --strategy ... --symbols ... --timeframes ... --space_file ...

# Maintenance
make clean                   # usuń cache (__pycache__, .pytest_cache, etc.)
make lock                    # regeneruj requirements.txt
make sync                    # zsynchronizuj env z lockfile

# Help
make help                    # lista wszystkich make targets
```

## Anti-patterns (czego NIE robić)

- ❌ **`git push --force` na master** — przepisze historię, inni stracą zmiany (przyszli inni)
- ❌ **Commit bez aktywnego conda env** — wymyka się testowanie, mypy nie znajdzie deps
- ❌ **Edycja requirements.txt ręcznie** — to lockfile generowany przez pip-tools, ręczna edycja = niespójność
- ❌ **Pomijanie `make check` przed push** — break master = wstyd
- ❌ **Bumpowanie wersji w `[project]` przy każdym commicie** — wersja to release marker, nie commit marker. Bump przy taggowaniu (git tag v0.2.0)
- ❌ **Commitowanie `.deploy_key`** — to private SSH key, gitignored, ale jak się przeoczy = compromise

## Synchroniczność docs i kodu

Każda zmiana zmieniająca publiczne API LUB dodająca nowy moduł = update docs w TYM SAMYM commicie:

- Nowy moduł → docstring header + ewentualnie `docs/reference/modules/<modul>.md`
- Zmiana sygnatury publicznej funkcji → update docstringu + ewentualnie ADR jeśli to architectural change
- Nowa CLI komenda → update `docs/guides/makefile-cheatsheet.md` ALBO `docs/guides/getting-started.md`
- Nowa decyzja architektoniczna → nowy ADR

To wymaga dyscypliny. Pre-commit hook sprawdzający "czy `docs/` zmienione gdy `algo_bot/` zmienione" — TODO.
