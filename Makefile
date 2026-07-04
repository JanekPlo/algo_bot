# =============================================================================
# Makefile — algo_bot
#
# Wszystkie codzienne komendy projektu. `make help` listuje dostepne targety.
# =============================================================================

# Domyslny target — pokaz help (gdy `make` bez argumentu)
.DEFAULT_GOAL := help

# Zmienne narzedzi (mozesz override z linii komend: make test PYTHON=python3.12)
PYTHON      := python
PIP         := pip
PIP_COMPILE := pip-compile
PIP_SYNC    := pip-sync

# Katalog z kodem zrodlowym dla narzedzi (ruff, mypy, coverage)
SRC_DIRS := algo_bot tests scripts live

# === Pomoc ===================================================================
.PHONY: help
help:  ## Pokaz wszystkie dostepne komendy
	@echo ""
	@echo "  algo_bot — Makefile commands"
	@echo "  ============================"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

# === Setup ===================================================================
.PHONY: env
env:  ## Stworz conda env "algo_bot" z environment.yml (lub update)
	@if conda env list | grep -q "^algo_bot "; then \
		echo "==> Env 'algo_bot' juz istnieje, robie update --prune"; \
		conda env update -f environment.yml --prune; \
	else \
		echo "==> Tworze env 'algo_bot' z environment.yml"; \
		conda env create -f environment.yml; \
	fi
	@echo ""
	@echo "==> NASTEPNY KROK: conda activate algo_bot && make install"

.PHONY: install
install:  ## pip install -e ".[dev]"  (po aktywacji conda env)
	$(PIP) install -e ".[dev]"

.PHONY: lock
lock:  ## Generuj requirements.txt z pyproject.toml (deterministic lockfile)
	$(PIP_COMPILE) --extra dev --output-file=requirements.txt pyproject.toml
	@echo "==> Lockfile zaktualizowany. Aby zsynchronizowac env: make sync"

.PHONY: sync
sync:  ## Zainstaluj DOKLADNIE wg requirements.txt (lockfile) + reinstall pakietu
	$(PIP_SYNC) requirements.txt
	$(PIP) install -e . --no-deps

# === Testowanie / linting / typecheck =======================================
.PHONY: test
test:  ## pytest (pelny)
	pytest

.PHONY: test-fast
test-fast:  ## pytest bez slow / integration markers
	pytest -m "not slow and not integration"

.PHONY: test-cov
test-cov:  ## pytest + coverage report (HTML w htmlcov/)
	pytest --cov=algo_bot --cov-report=term-missing --cov-report=html

.PHONY: lint
lint:  ## ruff check (lint errors)
	ruff check $(SRC_DIRS)

.PHONY: lint-fix
lint-fix:  ## ruff check --fix (auto-naprawa)
	ruff check --fix $(SRC_DIRS)

.PHONY: format
format:  ## ruff format (apply)
	ruff format $(SRC_DIRS)

.PHONY: format-check
format-check:  ## ruff format --check (fail gdy nie sformatowane)
	ruff format --check $(SRC_DIRS)

.PHONY: typecheck
typecheck:  ## mypy (legacy lenient, nowe moduly strict)
	mypy algo_bot

.PHONY: check
check: lint format-check typecheck test  ## Wszystkie sprawdzenia (CI-style)
	@echo ""
	@echo "==> Wszystkie sprawdzenia OK"

# === Bot komendy (alias na CLI entries) ======================================
# Uzycie z argumentami:  make backtest ARGS="--symbol BTC/USDT --timeframe 4h ..."
.PHONY: backtest
backtest:  ## algo-backtest [ARGS=...]  (skrot)
	algo-backtest $(ARGS)

.PHONY: sweep
sweep:  ## algo-sweep [ARGS=...]  (skrot)
	algo-sweep $(ARGS)

# === VPS research runner (Sesja 4b) =========================================
# Sync danych/wynikow z VPS. Wymaga VPS_HOST (SSH alias lub user@host).
# Szczegoly: docs/guides/vps-research-runner.md
.PHONY: sync-up
sync-up:  ## rsync bot_data/processed/ PC->VPS (wymaga VPS_HOST=...)
	VPS_HOST=$(VPS_HOST) scripts/vps-sync.sh up

.PHONY: sync-down
sync-down:  ## rsync results/ VPS->PC (wymaga VPS_HOST=...)
	VPS_HOST=$(VPS_HOST) scripts/vps-sync.sh down

# === Pre-commit ==============================================================
.PHONY: precommit-install
precommit-install:  ## Zainstaluj pre-commit hooks (jednorazowo)
	pre-commit install

.PHONY: precommit-run
precommit-run:  ## Uruchom pre-commit na wszystkich plikach
	pre-commit run --all-files

# === Maintenance =============================================================
.PHONY: clean
clean:  ## Usun cache, build artifacts (.pytest_cache, .mypy_cache, __pycache__, dist/, build/)
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf dist build htmlcov .coverage coverage.xml
	@echo "==> Cache i build artifacts wyczyszczone"

.PHONY: tree
tree:  ## Pokaz strukture repo (ignoruje gitignored)
	@tree -I '__pycache__|*.egg-info|.pytest_cache|.mypy_cache|.ruff_cache|htmlcov|dist|build|bot_data|results|.venv|venv' -L 3
