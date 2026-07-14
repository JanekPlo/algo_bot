# AGENTS.md

## Cursor Cloud specific instructions

`algo_bot` is a single Python (3.12.13) quantitative crypto-futures trading
framework managed with **uv 0.11.28** (pinned via `[tool.uv] required-version`).
There is **no server, database, or container** — the "application" is a set of
batch CLIs plus a long-running live-trading loop. Standard commands live in the
`Makefile` (`make help`), `README.md`, and `pyproject.toml`; prefer those over
duplicating them here.

Notes and gotchas that are not obvious from the docs:

- **Environment / dependency refresh** is handled by the startup update script
  (`uv sync --locked`). `uv` installs to `~/.local/bin` and is already on `PATH`
  via `~/.bashrc`. `uv sync` fetches its own pinned CPython 3.12.13, so the
  system `python3` version does not matter. The TA-Lib wheel bundles the C
  library — no system TA-Lib/Conda is needed.
- **Quality gate:** `make check` runs `lint` + `format-check` + `typecheck` +
  `test` (the same gate as CI in `.github/workflows/check.yml`). Use
  `make test-fast` to skip `slow`/`integration` markers.
- **No network egress to exchanges in this environment.** `algo-fetch`,
  `algo-fetch-funding`, and any `live`/`integration` test that reaches Binance
  will fail with retry/`RuntimeError` errors. This is an environment limitation,
  not a code bug. The default `pytest` run already passes because networked
  tests are marked and skipped/deselected.
- **Running a backtest offline:** the engine reads processed CSVs from
  `bot_data/processed/binance_<SYMBOL>_<TF>.csv` (columns:
  `datetime,Open,High,Low,Close,Volume`, UTC). Since data cannot be fetched
  here, generate a synthetic CSV in that path/format to exercise
  `uv run algo-backtest ...` end-to-end. Pass `--funding_source synthetic` to
  avoid needing a funding CSV. Outputs land in `results/backtests/<run_id>/`
  (`summary.json`, `equity.csv`, `trades.csv`, `params.json`). `bot_data/` and
  `results/` are gitignored.
- **`uv run algo-backtest --help` crashes** with `TypeError: must be real
  number, not dict` — a pre-existing argparse bug (an unescaped `%`/dict in a
  help string). The command itself works fine when given real arguments; read
  arg definitions in `algo_bot/engine/backtester.py` (`parse_args`) instead.
- **Live trading** (`live/live_binance.py` / `run_live.sh`) needs Binance
  Futures **testnet** keys in `.env` (`BINANCE_FUTURES_API_KEY_TESTNET`,
  `BINANCE_FUTURES_API_SECRET_TESTNET`) and network egress — not runnable in
  this environment.
