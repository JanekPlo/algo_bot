# ADR-002: pyproject.toml + hatchling + conda + pip-tools + ruff + mypy

- **Status:** Superseded in part by ADR-014 / MR-Session 3 Beta
- **Data:** 2026-05-14
- **Faza projektu:** 1 (Foundation)
- **Autorzy:** Janek Płoński

> **Implementation note (2026-07-13):** this ADR remains the historical record for the
> package layout, Hatchling, Ruff, and mypy choices. Its Python 3.11, Conda, pip-tools,
> and TA-Lib installation decisions were replaced by the Beta 0 runtime described in
> [ADR-014](014-engine-migration-nautilus.md): managed CPython 3.12, `uv`, `uv.lock`, and
> the binary TA-Lib wheel. The context and rejected alternatives below are intentionally
> not rewritten as if that later evidence had been available in May.

## Context

Po ADR-001 (flatten) mamy clean package layout, ale brak konfiguracji buildu, deps managementu, linting i type-checking. Stan przed tą decyzją:

- Brak `pyproject.toml` — pakietu nie da się zainstalować przez `pip install -e .`
- `requirements.txt` ręcznie utrzymywany (po ADR-001 naprawione literówki, dodane brakujące), brak lockfile z deterministycznymi wersjami
- Brak linting (czarny / isort / flake8) — kod ma niespójny styl
- Brak type checkingu (mypy / pyright)
- Brak deklaracji Python version constraint (kod używa `from __future__ import annotations` w niektórych miejscach, sugerując 3.8+, ale `match` statement nie jest używany — sugeruje 3.10-)
- TA-Lib jest hard requirement dla strategii — wymaga systemowego libta-lib który NIE-jest na PyPI, tylko w conda-forge lub apt
- Live trading na produkcji = potrzebujemy deterministycznych deps (faza 4-5), żeby Docker w VPS dał identyczne zachowanie jak lokalny dev

Cztery wymiary do zdecydowania:
- **B1**: Python version
- **B2**: Build backend (jak budujemy pakiet)
- **B3**: Env + dependency management (jak instalujemy)
- **B4**: Type checker policy (jak rygorystycznie sprawdzamy typy)

Każdy wymiar był analizowany osobno z 3-5 opcjami i trade-offami — patrz Alternatives Considered.

## Decision

### B1: Python 3.11+

`requires-python = ">=3.11"` w pyproject.toml. Lower bound 3.11.

### B2: hatchling jako build backend

```toml
[build-system]
requires = ["hatchling>=1.18"]
build-backend = "hatchling.build"
```

Pełne `pyproject.toml` używa PEP 621 (`[project]`) — żadnego `[tool.poetry]` ani `[tool.setuptools]` legacy. Hatchling auto-detektuje `algo_bot/` jako pakiet.

### B3: conda env `algo_bot` + pip-tools

- `environment.yml` definiuje conda env z `python=3.11`, `ta-lib` (z conda-forge), `numpy`/`pandas`/`scipy` (z MKL/OpenBLAS), `jupyterlab`
- Reszta deps idzie przez pip wewnątrz env: `pip install -e ".[dev]"`
- `pip-tools` generuje deterministic lockfile: `pip-compile pyproject.toml -o requirements.txt`
- TA-Lib świadomie NIE w `pyproject.toml dependencies` — przychodzi z conda-forge

### B4: mypy strict-on-new

- Default lenient (`disallow_untyped_defs = false`) dla legacy code
- `[[tool.mypy.overrides]]` z `disallow_untyped_defs = true` dla nowo pisanych modułów: `algo_bot.risk.*`, `algo_bot.engine.walkforward`, `algo_bot.metrics`
- Dla bibliotek bez stubów (`backtesting.*`, `ccxt.*`, `talib.*`, `dotenv.*`) — `ignore_errors = true`

### Bonus (B5): ruff jako linter + formatter

ruff zastępuje black + isort + flake8 + pyupgrade. Konfig w `[tool.ruff]` z 10 rule sets aktywnymi (E, F, W, I, B, UP, SIM, RUF, C4, PIE).

### CLI entries

```toml
[project.scripts]
algo-backtest = "algo_bot.engine.backtester:main"
algo-sweep    = "algo_bot.engine.sweep:main"
```

Po `pip install -e .` masz globalne komendy `algo-backtest` i `algo-sweep` w aktywnym conda env.

## Consequences

**Pozytywne:**
- `pip install -e .` działa — importy `from algo_bot.X` działają z dowolnego katalogu po aktywacji env
- Deterministic deps: `requirements.txt` jako lockfile (z pip-compile) gwarantuje że Docker w VPS dostanie identyczne wersje co lokalny dev
- TA-Lib działa OOTB (conda-forge zapakował system lib + Python bindings razem)
- Standardowy PEP 621 `pyproject.toml` — kompatybilny z każdym IDE, CI, build tool
- ruff: jeden config, jeden tool, ~100x szybsze niż black+isort+flake8 (~50ms zamiast ~5s na codebase)
- mypy strict-on-new: Jane Street vibe na nowy kod, bez tygodnia refaktoru legacy
- CLI entries: profesjonalny UX, `algo-backtest --help` jak prawdziwa tool
- Minimal pyproject (~140 linii z całym configiem) — łatwy w utrzymaniu

**Negatywne / koszty:**
- Dwa pliki konfiguracyjne dla env (pyproject + environment.yml). Stała dyscyplina trzymania ich sync (TA-Lib tylko w conda, reszta tylko w pyproject)
- Pierwszy raz `make env && make install` dłuższy bo conda env create musi ściągnąć ta-lib + python + numpy/pandas (~3-5 min jednorazowo)
- mypy strict-on-new wymaga osobnego śledzenia która moduł jest "nowy" (strict) vs legacy (lenient) — config w pyproject jasno to mówi
- pip-tools jest extra dep + trzeba pamiętać `make lock` po dodaniu nowej deps do `[project.dependencies]`

**Ryzyka:**
- Hatchling jest młody (2022) — jeśli Astral przestanie go rozwijać, switching out kosztuje ~5 min (zmiana 2 linii `[build-system]` + dodanie kilku linii configu setuptools)
- TA-Lib z conda-forge może mieć inną wersję niż TA-Lib z PyPI — w praktyce zgodne, ale teoretycznie możliwe niespójności
- mypy strict-on-new policy może być trudna w egzekwowaniu — jeśli zaczniemy "tylko ten jeden raz dodam Any" w nowych modułach, policy się rozmywa. Pre-commit hook na mypy z fail-on-strict-modules zapobiega.

## Alternatives Considered

### B1 Python version

- **3.10** — EOL paź 2026 (za 5 mies. po decyzji). Migration debt w środku fazy 2. Odrzucone.
- **3.12** — +30% perf vs 3.10, type syntax improvements. Akceptowalne, ale 3.11 jest bardziej dojrzałe (4 lata) i ma niewielki zysk perf vs 3.12 (~5%). Może upgrade po MVP.
- **3.13** — najnowszy, ale JIT/free-threaded mode niestabilne (alpha-tier). Odrzucone dla production-grade.

### B2 Build backend

- **setuptools** — działa wszędzie, ale verbose config (`[tool.setuptools.packages.find]` itd.), dużo legacy magii. Odrzucone bo: hatchling daje to samo z mniejszą konfiguracją.
- **poetry** — all-in-one (build + deps + venv), opinionated, popularne. Odrzucone bo: nie używa standardu PEP 621 (własny `[tool.poetry]`), wymaga workaround dla `pip install -e .`, wolny resolver, społeczność quant od poetry odchodzi (mlfinlab, vectorbt, Hudson River). Switching out painful.
- **PDM** — kompromis: standards-first + lockfile native. Akceptowalne, ale wymaga `pdm` jako extra tool w CI/Docker, podczas gdy hatchling + pip-tools nie wymagają niczego ekstra.
- **uv** (Astral) — super szybkie pip+venv+resolver, ale nie jest build backendem (tylko installer/resolver). Możemy go używać jako pip replacement RAZEM z hatchling — to ortogonalne decyzje.

### B3 Env + deps

- **Conda + pip + requirements.txt** (poprzedni stan) — dwa źródła prawdy, słaby lockfile. Odrzucone.
- **Pure venv + uv** — modern stack, najszybszy install, ale tracimy MKL goodies z conda i TA-Lib wymaga `sudo apt install libta-lib-dev` (więcej friction na nowym systemie). Akceptowalne, ale conda jest lepsze dla quant workload.
- **Pure conda** (environment.yml zamiast pyproject) — pełny lockdown w conda, ale nie da się `pip install -e .` , trudno dockerify minimalnie. Odrzucone — brak elastyczności.

### B4 Type checker

- **mypy full strict** — wszystko wymaga typehints. Odrzucone bo: tydzień refaktoru legacy (10+ plików, w tym `bghtrend_pullback` 333 linie) zanim cokolwiek zacznie się testować w strict mode. Wartość vs koszt nieproporcjonalna.
- **pyright via Pylance** — szybszy mypy (3x), lepszy IDE integration. Akceptowalne, ale wymaga Node.js w CI, mniej tutoriali dla zaawansowanych patternów. Możemy używać dodatkowo do mypy (pyright via VSCode dla IDE feedback, mypy w CI dla correctness).
- **Brak type checkera** — tylko ruff. Odrzucone bo: Jane Street mindset = static analysis ma znaczenie, zwłaszcza dla risk module i nowych krytycznych komponentów.

### B5 Linter (bonus)

- **black + isort + flake8 (klasyczny stack)** — battle-tested, ale 3 tools, 3 configi, 100x wolniej niż ruff. Odrzucone w 2026.
- **pylint** — wolny i często zbyt opinionated. Odrzucone.

## References

- Pliki implementacji:
  - `pyproject.toml` (root) — pełen config
  - `environment.yml` (root) — conda env definition
  - `Makefile` (root) — komendy z tego stacku zawinięte w make targets
- Skeleton pyproject.toml ze wszystkimi sekcjami — patrz w samym repo
- [PEP 621 — Storing project metadata in pyproject.toml](https://peps.python.org/pep-0621/)
- [PEP 517 — A build-system independent format for source trees](https://peps.python.org/pep-0517/)
- [hatchling documentation](https://hatch.pypa.io/latest/config/build/)
- [ruff documentation](https://docs.astral.sh/ruff/)
- [mypy strict mode](https://mypy.readthedocs.io/en/stable/getting_started.html#strict-mode-and-configuration)
- Wymaga: ADR-001 (flatten layout) — bez tego `pip install -e .` nie miałby sensu

## Notes

- **Po MVP (faza 4-5)** rozważać upgrade `pip-tools` → `uv pip compile` (10x szybciej, kompatybilne) — może to być osobny ADR
- **Pre-commit hooks** (ruff + mypy on changed files) zostały odroczone do `.pre-commit-config.yaml` w follow-up commicie — patrz Faza 1 deliverables
- **CI workflow** (GitHub Actions z `make check`) odroczony do follow-up — patrz Faza 1 deliverables
- Path dla future: gdy projekt dojrzeje, możemy publikować `algo_bot` jako private package na własnym PyPI mirror albo w GitHub Packages — hatchling już jest gotowy do tego, wymaga tylko `python -m build && twine upload`
