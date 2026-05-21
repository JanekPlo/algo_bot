# Changelog

Wszystkie istotne zmiany w algo_bot będą tutaj rejestrowane.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Wersjonowanie: [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — `MAJOR.MINOR.PATCH`.

Sekcje na każdą wersję:
- **Added** — nowe funkcjonalności
- **Changed** — zmiany w istniejących funkcjonalnościach (kompatybilne wstecz)
- **Deprecated** — funkcjonalności do usunięcia w przyszłości
- **Removed** — usunięte funkcjonalności
- **Fixed** — naprawione bugi
- **Security** — łatki bezpieczeństwa

---

## [Unreleased]

### Added
- `docs/guides/working-with-claude.md` — konwencja pracy z Claudem (Cowork): model "jedna sesja per deliverable" z kickoff/closeout protokołem, rola mózg-Claude (weekly audit + pre-flight on-demand), warstwa trwałości (ROADMAP + ADR + CHANGELOG + memory)
- Pełna dokumentacja w `docs/`: README (TOC), guides (getting-started, daily-workflow, makefile-cheatsheet), reference (package-overview), concepts (glossary), 5 ADR retroactive
- Decyzje fazy 1 podjęte: layout (flatten + algo_bot package), Python 3.11, hatchling, conda env + pip-tools, ruff, mypy strict-on-new
- Konwencja docstring: Google style
- ROADMAP.md zaktualizowany o docs strand w każdej fazie

### Changed
- Konwencja pisania docs — synchronicznie z kodem (każde public API change = update docs)

---

## [0.1.0] — 2026-05-14

Pierwsza faza foundation. Repo gotowe do pracy nad strategiami z proper toolingiem, layoutem i workflowem.

### Added
- `pyproject.toml` — single source of truth dla pakietu, build backend = hatchling, Python 3.11+
- `environment.yml` — conda env `algo_bot` z `ta-lib` z conda-forge + Python 3.11
- `Makefile` — wszystkie codzienne komendy (env, install, lock, sync, test, lint, format, typecheck, check, backtest, sweep, clean)
- `[tool.ruff]` config — lint + format (zastępuje black+isort+flake8+pyupgrade)
- `[tool.mypy]` config — strict-on-new policy (`algo_bot.risk.*`, `algo_bot.engine.walkforward`, `algo_bot.metrics`)
- `[tool.pytest.ini_options]` — markers (slow, integration, live), strict-markers
- CLI entries: `algo-backtest`, `algo-sweep` (po `pip install -e .`)
- `docs/ROADMAP.md` — plan 5-fazowy do production na VPS
- `docs/ARCHITECTURE.md` — warstwy, mapa modułów, ADRs lite
- `algo_bot/engine/__init__.py` — explicit subpakiet (oryginał polegał na PEP 420 namespace packages)
- Comprehensive `.gitignore` (secrets, Python cache, venvs, IDE, OS, bot_data, results)

### Changed
- **Layout repo: flatten + rename `src/` → `algo_bot/`** (decyzja A). Cały kod migracja z `trading/backtesting/algo_bot/*` do roota. Pakiet teraz `algo_bot` z subpakietami `engine/`, `indicators/`, `strategies/`, `telemetry/`, `engine/exchanges/`. Zachowana historia git (rename detection przez `git mv`).
- **Importy: `src.*` → `algo_bot.*`** (drugi commit migracji). Wszystkie statyczne i dynamiczne (`importlib.import_module`) importy zaktualizowane. Usunięte `sys.path.insert` hacki w `executor.py` i `tests/conftest.py`.
- `requirements.txt` — header zmieniony, teraz oznaczony jako lockfile generowany przez `pip-tools` (`make lock`). Stara ręczna lista zostaje jako fallback do pierwszego `pip-compile`.

### Fixed
- `requirements.txt` literówki: `yaml` → `PyYAML`, `tmatplotlib` → `matplotlib`
- `requirements.txt` brakujące: dodane `python-dotenv` (używane w `live/live_binance.py`), `tzdata` (zoneinfo na Windowsie)
- `requirements.txt` orphan: usunięte `distlib` (brak użycia w kodzie)

### Known Issues
- `algo_bot/executor.py` ma broken import `from algo_bot.backtester import optimize_backtest` — broken też było przed flatten (`src.backtester` nie istniało; `optimize_backtest` żyje w `engine/sweep.py`). FIXME w pliku, do decyzji w fazie 1 czy deprecation czy migracja.
- `tests/test_backtest.py` niespójna sygnatura — wywołuje `run_backtest(df, StrategyClass)` ale nowy backtester ma sygnaturę `run_backtest(symbol, timeframe, strategy, params, ...)`. TODO w pliku, do refaktoru przy decyzji D (metrics + test fixtures).
- `algo_bot/strategies/bitcoin_breakout.py` — pusty plik (placeholder bez implementacji).

---

## [0.0.1] — przed 2026-05-11

Początkowy stan repo (przed naszą pracą). Funkcjonalności już istniejące w kodzie:

### Added (pre-existing)
- `StrategyBase` + `Signal` — unified API dla backtest i live
- Engine: `backtester.py` (532 linie, wrapper na backtesting.py z TP/SL/trail), `sweep.py` (352 linie, grid + random search)
- Live: `live_binance.py` (401 linii, hybrid TP/SL mode server/local/hybrid)
- 7 strategii: `bghtrend_pullback` (333 linie, najbardziej rozbudowana — trend + pullback + xtrender + ATR-trail), `bollinger_band_breakout_short`, `simple_momentum`, `short_trend_following`, `ema_cross_sig`, `dca_btc`, `template`
- Data pipeline: `fetch_data.py` (CCXT → OHLCV), `process_data.py` (raw → processed z featurami), `data_loader.py`
- Indicators: `core.py` (ema, rsi, t3, atr), `xtrender.py`
- Telemetry: `journal.py` (CSV trades + equity per run_id)
- Configi YAML: `config/config.yaml` + 4 warianty `bghtrend_b1..b4.yaml`
- Notebooks: `01_data_exploration.ipynb`, `02_bollinger_analysis.ipynb`
- Tests: smoke test dla bollinger backtest

[Unreleased]: https://github.com/JanekPlo/algo_bot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JanekPlo/algo_bot/releases/tag/v0.1.0
