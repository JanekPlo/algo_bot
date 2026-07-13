# =============================================================================
# Makefile — algo_bot
#
# Wszystkie codzienne komendy projektu. `make help` listuje dostepne targety.
# =============================================================================

# Domyslny target — pokaz help (gdy `make` bez argumentu)
.DEFAULT_GOAL := help

# Narzedzia. UV mozna nadpisac, np. `make check UV=$$HOME/.local/bin/uv`.
UV     ?= uv
UV_RUN := $(UV) run --locked

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
env:  ## Utworz/zsynchronizuj uv .venv z przypietym CPython 3.12
	$(UV) sync --locked

.PHONY: install
install:  ## Zsynchronizuj .venv z uv.lock (projekt + dev group)
	$(UV) sync --locked

.PHONY: lock
lock:  ## Generuj/aktualizuj uv.lock
	$(UV) lock

.PHONY: export-requirements
export-requirements:  ## Eksport kompatybilnosci requirements.txt z uv.lock
	$(UV) export --locked --group dev --no-emit-project --no-hashes --output-file requirements.txt

.PHONY: sync
sync:  ## Zainstaluj DOKLADNIE wg uv.lock
	$(UV) sync --locked

# === Testowanie / linting / typecheck =======================================
.PHONY: test
test:  ## pytest (pelny)
	$(UV_RUN) pytest

.PHONY: test-fast
test-fast:  ## pytest bez slow / integration markers
	$(UV_RUN) pytest -m "not slow and not integration"

.PHONY: test-cov
test-cov:  ## pytest + coverage report (HTML w htmlcov/)
	$(UV_RUN) pytest --cov=algo_bot --cov-report=term-missing --cov-report=html

.PHONY: lint
lint:  ## ruff check (lint errors)
	$(UV_RUN) ruff check $(SRC_DIRS)

.PHONY: lint-fix
lint-fix:  ## ruff check --fix (auto-naprawa)
	$(UV_RUN) ruff check --fix $(SRC_DIRS)

.PHONY: format
format:  ## ruff format (apply)
	$(UV_RUN) ruff format $(SRC_DIRS)

.PHONY: format-check
format-check:  ## ruff format --check (fail gdy nie sformatowane)
	$(UV_RUN) ruff format --check $(SRC_DIRS)

.PHONY: typecheck
typecheck:  ## mypy (legacy lenient, nowe moduly strict)
	$(UV_RUN) mypy algo_bot

.PHONY: check
check: lint format-check typecheck test  ## Wszystkie sprawdzenia (CI-style)
	@echo ""
	@echo "==> Wszystkie sprawdzenia OK"

# === Bot komendy (alias na CLI entries) ======================================
# Uzycie z argumentami:  make backtest ARGS="--symbol BTC/USDT --timeframe 4h ..."
.PHONY: backtest
backtest:  ## algo-backtest [ARGS=...]  (skrot)
	$(UV_RUN) algo-backtest $(ARGS)

.PHONY: sweep
sweep:  ## algo-sweep [ARGS=...]  (skrot)
	$(UV_RUN) algo-sweep $(ARGS)

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
	$(UV_RUN) pre-commit install

.PHONY: precommit-run
precommit-run:  ## Uruchom pre-commit na wszystkich plikach
	$(UV_RUN) pre-commit run --all-files

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
