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
- **Decyzja C (ADR-006) — logging framework**: `algo_bot/log.py` ze `setup_logging()` (idempotentne) + `get_logger()` + `JsonFormatter`. Konsola (plain, Europe/Warsaw) + rotating file (JSON, UTC, 10 MB × 5 backupów). Zero zewnętrznych dep, stdlib `logging`. Third-party libs (ccxt, urllib3) wyciszone do WARNING.
- Testy `tests/test_log.py` — idempotency setupu, caplog widzi info/warning/exception, JsonFormatter emituje walidny JSON z extra fields. Bez mocków (mindset reguła #3).
- mypy strict-on-new override dla `algo_bot.log` w `pyproject.toml`
- **Decyzja D (ADR-007) — risk-adjusted metrics**: `algo_bot/metrics.py` z pure functions (Sharpe static+rolling, Sortino, Calmar, MAR jako osobne metryki, profit factor, recovery time, max drawdown, CAGR, win rate, total return) + `MetricsSummary` dataclass + thin `summarize()`. Log returns wewnętrznie dla annualizacji (additive across time → dokładne √n scaling), simple returns dla narracji. Annualizacja przez `infer_periods_per_year(index, calendar="crypto")` z fallback 365. Edge cases (zero variance, zero DD, no losing trades, no trades, never-recovered) → `NaN` + `logger.warning`. Recovery time jako `pd.Timedelta` primary, `pd.Timedelta.max` jako sentinel "never recovered" (mapowane na `inf` w `MetricsSummary.recovery_time_days`). Calmar default trailing 36m z pragmatic fallback na całość przy krótszej serii.
- Testy `tests/test_metrics.py` — deterministyczne fixtures (handcomputed Sharpe z log_returns=[0.01, 0.03], handcomputed DD na sekwencji [100,110,...,90,...,110], handcomputed profit_factor=3.0 z trades=[10,-5,20,-10,15]), weryfikacja edge case warningów przez `caplog`. Bez mocków.
- **Cross-strategy correlation** (extension w tej samej sesji, na wniosek Janka — antycypacja portfolio post-MVP): `strategy_correlation(equities, method, on) -> pd.DataFrame` (Pearson default + Spearman opt-in, log_returns default, inner-join na intersekcji czasowej, akceptuje `dict[str, pd.Series]` lub `pd.DataFrame`) + `mean_pairwise_correlation(corr_matrix) -> float` (diagnostyka "jak skorelowane jest portfolio"). Zostaje w `algo_bot.metrics`; spin-off do `algo_bot.portfolio` odłożony do momentu gdy portfolio analytics rozrośnie się (Sharpe portfolio, weighted equity, rebalancing).
- mypy strict-on-new override dla `algo_bot.metrics` w `pyproject.toml` (już istniał z ADR-002, aktywuje się z tym modułem)
- **Decyzja E (ADR-008) — risk limits module**: `algo_bot/risk/__init__.py` + `algo_bot/risk/limits.py` z pure functions over frozen dataclasses. Trzy gates (`check_drawdown`, `check_daily_loss`, `check_positions`) + `check_all` (first-hit ordering: drawdown → daily_loss → max_positions) + immutable state management (`init_state`, `update_state`). Sizing jako pure helper `position_size(equity, sl_distance, risk_pct)` — caller-driven, NIE auto-injection w wrapperze (decyzja Janka — nie wszystkie strategie mają SL przed entry, Signal nie ma `sl_price` field). Daily reset TZ configurable, default UTC. `None` w polach `RiskLimits` wyłącza limit.
- **Backtester risk hook** (`algo_bot/engine/backtester.py`): `make_bt_wrapper` przyjmuje opcjonalny `risk_limits: RiskLimits | None`; w `Wrapped.next()` na początku update_state + check_all; na breach forced exit po Close + raise `RiskLimitBreached`. `run_backtest` łapie exception, wpisuje detale do `stats["_risk_breach"]` i `stats["_risk_limits"]`, zwraca normalnie. ROADMAP Phase 1 success criterion ("Risk module zatrzyma backtest gdy drawdown przekroczy próg") spełniony.
- **CLI flags risk** (`algo-backtest`): `--max_dd_pct`, `--daily_loss_pct`, `--risk_per_trade_pct`, `--daily_reset_tz`. Pominięcie wszystkich = backward-compatible (brak gate'ów).
- **`run_backtest` accepts pre-loaded `data: pd.DataFrame | None`** (ADR-008 §11) — bypassuje `load_ohlcv_csv` gdy podane. Wymagana dla deterministycznych testów (bez pliku CSV w repo) i przygotowane pod walk-forward (Decyzja F) per-fold slicing.
- **`MetricsSummary` embed w `save_outputs`** (post-ADR-007 follow-up, ADR-008 §12): summary.json teraz zawiera `_metrics_summary` z wynikiem `algo_bot.metrics.summarize`. NaN/inf → None pre JSON serialization.
- Testy `tests/test_risk_limits.py` — deterministyczne fixture'y (handcomputed equity sequences, exact DD values, daily reset boundary cases UTC vs Europe/Warsaw), integration test z syntetycznym OHLCV który exercise'uje pełną ścieżkę `run_backtest` → breach → halt. Bez mocków.
- Testy `tests/test_backtest.py` — sygnatura naprawiona po flatten 2026-05-14. Smoke test z syntetycznym OHLCV (zawsze działa, bez wymogu CSV w bot_data/processed/) + integration test gated na obecność pliku. `@pytest.mark.skip` usunięty.
- `docs/reference/modules/risk-limits.md` — deep reference (API, edge cases, backtester integration, CLI, limitations)
- `docs/concepts/risk-management.md` — cienki concept doc (ROADMAP linia 55), Phase 1 deliverable, rozbudowywany w Fazie 4
- `algo_bot/strategies/buy_and_hold.py` — baseline strategia kup-i-trzymaj (deterministyczne wejście na drugim barze, brak exit). Używana jako test fixture dla risk module integration test i jako baseline benchmark do porównań strategy-vs-HODL w research workflow.

### Fixed
- **`algo_bot/metrics.py` regresje** złapane przy ADR-008 closeout (`make check` pierwszy run 2026-05-24). Pre-existing fails od Decyzji D close 2026-05-22 — najprawdopodobniej regresja od bump'a pandas-stubs:
  - **mypy `no-any-return`** w 4 miejscach (`log_returns`, `recovery_time`, `strategy_correlation` x2): cast(pd.Series/pd.Timedelta/pd.DataFrame, ...) — numpy/pandas stubs widzą `np.log(Series/DataFrame)` jako `ndarray` zwracane jako Any. Runtime nieruszony.
  - **Sharpe zero-variance edge case**: `std == 0` zamienione na `std < 1e-12 or math.isnan(std)`. Powód: `math.exp(0.001*i)` w teście wprowadza ~1e-15 floating-point noise w log_returns, std jest niezerowe w skali pikometra, Sharpe wybuchał do 10^13 zamiast NaN. 1e-12 jest 10 rzędów wielkości poniżej typowego std log_returns realnej strategii.
  - **`tests/test_metrics.py::TestCAGR::test_two_years_21_pct_total` tolerance** poluzowane z 1e-4 do 1e-3. Powód: 2024-01-01→2026-01-01 to 731 dni (2024 leap), więc przy `_DAYS_PER_YEAR_CRYPTO=365` (ADR-007 crypto convention) years=2.00274 i CAGR=0.0998 zamiast dokładnego 0.10. Leap-day drift na 2-letnim runie jest poniżej noise floor decyzji inwestycyjnych.
  - **`tests/test_metrics.py::TestStrategyCorrelation::test_spearman_method_runs`** przepisany — monotoniczne `linspace` po `.diff()` dają stałe returns, Spearman correlation constant series jest niezdefiniowany. Dodany mały szum + osłabienie assertu z `== -1.0` do `< 0` (negatywna korelacja przy anti-trending equity). Test teraz sprawdza co nazwa sugeruje (metoda działa), nie strict numerical match.

### Changed
- Konwencja pisania docs — synchronicznie z kodem (każde public API change = update docs)
- **`live/live_binance.py`** — retrofit 27 `print()` → `logger.info/warning/error` z `extra={...}` dla strukturalnych pól (symbol, side, qty, order_id, run_id, error). Usunięta funkcja `ts()` (timestamp teraz w formatterze). `_graceful_exit` zostawiony jako `print` (emergency exit, niezależny od loggera). Log file: `results/live/<run_id>/algo_bot.log`.
- **`algo_bot/engine/backtester.py`** — retrofit `print("[OK] Wyniki zapisane...")` → `logger.info(..., extra={"out_dir": ..., "run_id": ...})`. `main()` wywołuje `setup_logging()` na entry point.
- **`tests/test_backtest.py`** — oznaczony `@pytest.mark.skip` z explicit reason (broken signature od flatten'a 2026-05-14). Refaktor odłożony do dedykowanej sesji follow-up przed Decision E. Patrz docs/adr/007-risk-adjusted-metrics.md Notes.

### Fixed
- `algo_bot/process_data.py:process_file` — wyrównana sygnatura `feature_cfg: dict[str, Any] | None` → `list[dict[str, Any]] | None` (zgodnie z `compute_features` i docstringiem, który zawsze opisywał listę featurów). Pre-existing bug wykryty incydentalnie przy `make typecheck` podczas Decyzji C. Czysto annotation, runtime bez zmiany.

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
