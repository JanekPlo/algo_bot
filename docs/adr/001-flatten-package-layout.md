# ADR-001: Flatten repo + `algo_bot/` package layout

- **Status:** Accepted
- **Data:** 2026-05-14
- **Faza projektu:** 1 (Foundation)
- **Autorzy:** Janek Płoński

## Context

Repo na początku fazy 1 miało kod zagnieżdżony 4 poziomy w głąb: `~/quant_projects/algo_bot/trading/backtesting/algo_bot/`. W środku siedział pełen kod (src/, strategies/, indicators/, live/, tests/, ...) — niezagnieżdżonego nic nie było (oprócz `.git/`).

Konsekwencje takiego stanu:
- Każda komenda wymagała `cd trading/backtesting/algo_bot/` lub bardzo długich ścieżek
- Importy działały tylko dzięki `sys.path.insert(0, PROJECT_ROOT)` w `executor.py` i `tests/conftest.py` — code smell, kruche, łamie się przy `python -m`
- Importy używały top-level nazw `src.*`, `strategies.*`, `indicators.*` — `src` jako nazwa pakietu jest niekonwencjonalne (zwyczajowo `src/` to dyrektywa layoutu, nie pakiet), a `strategies` i `indicators` jako top-level packages tworzą potencjalne konflikty namespace (np. z innymi pakietami pip o tej nazwie)
- CI, Makefile, Docker — wszystkie musiałyby obchodzić ten poziom zagnieżdżenia
- Nie można było zrobić `pip install -e .` bez najpierw zrestrukturyzowania

Decyzja kontekstowa: użytkownik potwierdził że `algo_bot` to jedyny projekt w repo — nie planuje innych projektów pod `trading/backtesting/`. To eliminuje argument "zagnieżdżenie jest po to bo to monorepo".

## Decision

**Wybieramy flatten + rename `src/` → `algo_bot/`.** Konkretnie:

1. Przenosimy CAŁY kod z `trading/backtesting/algo_bot/*` do roota repo (`~/quant_projects/algo_bot/*`)
2. Renaming `src/` → `algo_bot/` (top-level Python package)
3. `strategies/` i `indicators/` przenoszone JAKO submoduły wewnątrz `algo_bot/` (`algo_bot/strategies/`, `algo_bot/indicators/`)
4. `live/`, `tests/`, `notebooks/`, `scripts/`, `config/`, `docs/`, `bot_data/`, `results/` zostają na top-level (są to katalogi projektowe, nie pakiet)
5. Wszystkie importy `from src.X` → `from algo_bot.X`, `from strategies.X` → `from algo_bot.strategies.X`, `from indicators.X` → `from algo_bot.indicators.X`
6. Wszystkie dynamiczne importy (`importlib.import_module(f"strategies.{name}")`) zaktualizowane analogicznie
7. Usuwamy `sys.path.insert` hacki — niepotrzebne po `pip install -e .` (decyzja ADR-002)

Implementacja: dwa osobne commity dla łatwego review/revert:
- **Commit 1**: same `git mv` (Git widzi jako renames, zachowana historia per-file przez `git log --follow`)
- **Commit 2**: edycja importów we wszystkich `.py` (~17 plików)

## Consequences

**Pozytywne:**
- Repo navigable — `ls` w roocie pokazuje od razu strukturę projektu
- `pip install -e .` działa out-of-the-box po dodaniu `pyproject.toml` (ADR-002)
- Importy idiomatyczne: `from algo_bot.engine.backtester import run_backtest` zamiast `from src.engine.backtester import ...`
- Brak namespace konfliktów (`algo_bot.*` jest unikalny prefix)
- CI / Makefile / Docker bez extra `cd` step
- Git historia zachowana per-file dzięki `git mv` (rename detection)
- Każdy nowy moduł w fazach 2-5 ma jasną ścieżkę — `algo_bot.risk`, `algo_bot.metrics`, `algo_bot.engine.walkforward`

**Negatywne / koszty:**
- Jednorazowa praca: ~50 plików zostało przeniesionych, ~17 plików edytowanych pod importy
- Git log `--follow` wymagany do prześledzenia historii starszych plików (default `log` pokazuje tylko od rename)
- Każdy zewnętrzny dokument odwołujący się do `src.X` lub `trading/backtesting/algo_bot/X` jest teraz nieaktualny (zaktualizowane: ROADMAP, ARCHITECTURE, README; nie ma innych)

**Ryzyka:**
- Komplikacje z `git mv` na Windows mounted filesystems — zostało obeszą wykonaniem skryptu wprost w WSL native filesystem (nie przez `/mnt/c/`)
- Dwa pliki zostały zostawione z broken pre-flatten imports celowo (do dyskusji w follow-up): `algo_bot/executor.py` (broken `from algo_bot.backtester import optimize_backtest`), `tests/test_backtest.py` (niespójna sygnatura `run_backtest(df, cls)`). Nie naprawiamy ich w tym ADR — będzie osobna decyzja.

## Alternatives Considered

- **Opcja 1: Status quo** — zostawić `trading/backtesting/algo_bot/`, defer flatten do "po MVP". Odrzucone bo: im później, tym więcej plików do migracji. Faza 1 ma mało nowych plików, faza 2-5 ma dużo. Migracja staje się BARDZIEJ bolesna w przyszłości, nie mniej.

- **Opcja 2: Flatten bez rename src→algo_bot** — przesunąć pliki do roota, ale zostawić `src/`, `strategies/`, `indicators/` jako top-level (jak były). Odrzucone bo: `src` jako pakiet Python jest niekonwencjonalne (zazwyczaj `src/` to *dyrektywa layoutu* nie pakiet), `strategies` i `indicators` jako top-level packages tworzą namespace pollution. Nie rozwiązuje fundamentalnego problemu.

- **Opcja 3: src-layout (`src/algo_bot/`)** — modern Python convention, gdzie pakiet siedzi w `src/algo_bot/`. Odrzucone bo: src-layout jest przydatne dla bibliotek publishowanych do PyPI (wymusza editable install, prevent accidental imports). Dla prywatnego projektu jak nasz to over-engineering. Stracilibyśmy w czytelności (`algo_bot/algo_bot/...` w paths) na korzyść marginalnej cleanliness.

- **Opcja 4: Monorepo `trading/algo_bot/`** — usunąć tylko poziom `backtesting/`. Odrzucone bo: użytkownik potwierdził brak planów na inne projekty pod `trading/`. Half-measure bez konkretnego zysku.

## References

- Skrypt migracji: `migrate_flat.sh` (workspace folder strony, jednorazowe użycie)
- Commits implementacji:
  - `chore: flatten repo structure` (45234d4 + later commit message)
  - `refactor: src.* -> algo_bot.* po flatten` (next commit)
- [Python Packaging Authority — Packaging Python Projects](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- Powiązany: ADR-002 (pyproject + hatchling), który zależy od tej decyzji
