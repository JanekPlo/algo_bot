# Module reference — `algo_bot.log`

Centralised logging configuration for `algo_bot`. Stdlib `logging` with two handlers: console (plain, Europe/Warsaw timestamp, human-friendly) plus a rotating file handler (JSON-per-line, UTC timestamp, machine-readable). Idempotent setup function so multiple entry points can call it safely, and a thin wrapper around `logging.getLogger` for module-level convention.

Decision context: [ADR-006](../../adr/006-logging-strategy.md).

## At a glance

```python
from algo_bot.log import get_logger, setup_logging

# In an entry point (CLI main, live runner) — once per process.
setup_logging()

# In every module that logs — convention is module-level get_logger(__name__).
logger = get_logger(__name__)

logger.info("Pozycja otwarta", extra={"side": "long", "qty": 0.1, "symbol": "BTC/USDT"})
logger.warning("Slippage exceeded budget", extra={"requested": 5.0, "got": 12.4})
logger.exception("Order rejected")  # exc_info=True automatically
```

Console output (stderr, Europe/Warsaw):

```
2026-05-24 18:23:01 [INFO] algo_bot.live.binance: Pozycja otwarta
```

File output (`logs/algo_bot.log`, JSON, UTC):

```json
{"ts": "2026-05-24T16:23:01.123Z", "level": "INFO", "logger": "algo_bot.live.binance",
 "message": "Pozycja otwarta", "module": "binance", "function": "open_position",
 "line": 142, "side": "long", "qty": 0.1, "symbol": "BTC/USDT"}
```

## Public API

```python
def setup_logging(
    *,
    level: int = logging.INFO,
    log_dir: Path | str = "logs",
    log_filename: str = "algo_bot.log",
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 5,
    console_level: int | None = None,
    third_party_levels: dict[str, int] | None = None,
) -> None

def get_logger(name: str) -> logging.Logger
```

### `setup_logging`

Installs the two handlers on the root logger and creates the log directory if missing. Call **once per process**, from each entry point that produces logs (`algo-backtest`, `algo-sweep`, `algo-fetch`, `algo-process`, the live trader). The function is **idempotent** — repeated calls update levels on existing handlers without duplicating them, so module imports that accidentally invoke `setup_logging` more than once won't double-print.

Parameters:

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `level` | `int` | `logging.INFO` | Global filter on root logger and both handlers. Records below this level are dropped before reaching any handler. |
| `log_dir` | `Path \| str` | `"logs"` | Directory for the rotating file. Created if it doesn't exist. Relative paths resolve against cwd. |
| `log_filename` | `str` | `"algo_bot.log"` | Name of the rotating log file under `log_dir`. |
| `file_max_bytes` | `int` | `10 * 1024 * 1024` (10 MB) | Max size of one file before rotation. |
| `file_backup_count` | `int` | `5` | Number of rotated backups kept. Total disk: `(backup_count + 1) * max_bytes` = 60 MB by default. |
| `console_level` | `int \| None` | `None` (uses `level`) | Override level for the console handler (e.g. `level=DEBUG` for file, `console_level=INFO` for clean terminal). |
| `third_party_levels` | `dict[str, int] \| None` | `{"ccxt": WARNING, "urllib3": WARNING}` | Silences chatty third-party loggers. Override to bring them back for debugging. |

Returns `None`. Raises `OSError` if `log_dir` cannot be created.

### `get_logger`

Thin wrapper around `logging.getLogger(name)`. Convention is `get_logger(__name__)` at module level — produces a hierarchical logger (`algo_bot.engine.backtester` < `algo_bot.engine` < `algo_bot` < root), which lets you filter by subpackage:

```python
logging.getLogger("algo_bot.engine.sweep").setLevel(logging.DEBUG)  # noisy iteration logs
```

The wrapper exists for three reasons:
1. Single import point — no scattered `import logging; logger = logging.getLogger(__name__)` boilerplate.
2. Future hook — leaves room to inject a `LoggerAdapter` with run_id / strategy / fold_id without touching call sites.
3. Tooling — easier to grep "who logs in algo_bot" via `from algo_bot.log import get_logger`.

## Conventions

### Structured extras

Logging should be **structured**, not free-form. Use `extra={...}` for any field a future operator might want to filter, group, or aggregate by:

```python
# YES — searchable, aggregable, machine-readable in JSON output.
logger.info("Pozycja otwarta", extra={"symbol": "BTC/USDT", "side": "long", "qty": 0.1})

# NO — interpolated into the message; impossible to filter without regex.
logger.info(f"Pozycja otwarta {symbol} {side} {qty}")
```

Standard structured fields used across the codebase (not exhaustive, but a convention to lean on):

| Field | Where |
|---|---|
| `symbol`, `timeframe` | Anywhere touching a market. |
| `strategy`, `params` | Backtest/sweep context. |
| `run_id`, `fold_id` | Backtest output identifier, walk-forward fold index. |
| `side`, `qty`, `price` | Order events. |
| `order_id`, `client_order_id` | Live trading idempotency. |
| `error_type`, `error_msg` | Caught exceptions where you want the type as a separate dimension. |
| `out_path`, `rows` | File-write events (fetch, process, save_outputs). |

### Level semantics

| Level | When to use |
|---|---|
| `DEBUG` | Per-iteration sweep details, per-bar strategy state dumps, anything that would flood the file at INFO. Operator must opt in via `--log-level DEBUG` (`algo-sweep`) or programmatic call. |
| `INFO` | Milestones an operator wants to see by default — startup, run completion, file writes, progress checkpoints, position open/close. **Default level.** |
| `WARNING` | Recoverable conditions — retry attempt, missing optional feature, fallback path taken, edge case in metrics (zero variance, no trades). Run continues. |
| `ERROR` | Unrecoverable for the current operation but not for the process — failed batch element, rejected order, save failure. Use `logger.exception(...)` when an exception is in scope (auto-attaches traceback). |
| `CRITICAL` | Process-level failure — corrupted state, broken invariant, force-exit. Rare. |

### Exception logging

Inside an `except` block use `logger.exception(...)` (or pass `exc_info=True`) — both attach the full traceback to the file handler's JSON output. The console handler shows only the message line, the JSON file gets the stack:

```python
try:
    risky_call()
except Exception:
    logger.exception("Order placement failed", extra={"symbol": symbol, "side": side})
    raise  # or handle locally
```

Avoid `logger.error(f"Failed: {e}")` — it loses the traceback and is harder to aggregate.

## Edge cases

- **Idempotency.** `setup_logging` checks a sentinel attribute (`_algo_bot_handler`) on existing handlers; subsequent calls update levels but do not add new handlers. Safe to call from multiple entry points or in test fixtures.
- **File rotation.** `RotatingFileHandler` with 10 MB × 5 backups by default. Total disk footprint is bounded — production VPS won't fill the disk with old logs.
- **Third-party noise.** `ccxt` and `urllib3` are silenced to WARNING by default. Override via the `third_party_levels` argument if you need to debug a CCXT-level issue (e.g. `setup_logging(third_party_levels={"ccxt": logging.DEBUG})`).
- **JSON serialisation of `extra`.** The `JsonFormatter` runs `json.dumps(value, default=str)` on each extra field. Non-serialisable values (custom objects, numpy types, pandas timestamps) round-trip via `repr()` instead of crashing the log line. Prefer passing primitives (`str(ts)` rather than the `pd.Timestamp` itself) for cleaner output.
- **Reserved attribute names.** Stdlib `LogRecord` has reserved attribute names (`args`, `msg`, `name`, `levelname`, etc.). Don't put them in `extra` — they collide with internal logger machinery. The JSON formatter filters them out via `_LOGRECORD_RESERVED` if you try, but the safer move is to avoid the collision in the first place.
- **Timezone.** Console uses Europe/Warsaw (Janek's local). JSON file uses UTC. Why split: console is for humans during dev, UTC is the convention machine sinks expect (Loki, Sentry, future Phase 5 observability stack).
- **Non-existent log directory.** `setup_logging(log_dir="/missing/path")` will create the directory via `Path.mkdir(parents=True, exist_ok=True)`. Raises `OSError` only if the path is actively unwritable (permission denied, full disk).

## Limitations / Future migration

- **No structlog.** ADR-006 explicitly rejected `structlog` and `loguru` for Phase 1 — stdlib `logging` is enough until we have a real observability stack. Re-evaluation trigger is **Phase 5** (production VPS with Prometheus + Grafana + Alertmanager). If we migrate, the public API of this module stays the same (`get_logger`, `setup_logging`) — the underlying implementation swaps, call sites unchanged. The `extra={...}` convention already prepares us for structured-first logging.
- **No log shipping.** Current setup writes to local file only. Phase 5 plan: ship the JSON file to Loki via a sidecar (Promtail/Vector). The JSON format is already Loki-friendly — no code changes needed, only deployment config.
- **No async-safe handler.** `RotatingFileHandler` is blocking. For high-throughput live trading we'd want a `QueueHandler` + listener thread. Not a Phase 1 concern (current logging rate is single-digit events per second).
- **Sentinel hack for idempotency.** `_ALGO_BOT_HANDLER_ATTR` is a private attribute on handler instances. Works, but a more elegant approach would be a subclass `AlgoBotHandler` that the check matches via `isinstance`. Cosmetic — current approach is robust enough.

## See also

- [ADR-006 — Logging strategy](../../adr/006-logging-strategy.md) — rationale for stdlib over structlog/loguru, alternatives considered, Phase 5 re-evaluation trigger.
- [Reference — `algo_bot.metrics`](metrics.md) — uses `get_logger(__name__)` for edge-case warnings (sibling pattern).
- [Reference — `algo_bot.risk.limits`](risk-limits.md) — same.
- [ADR-002 — pyproject + hatchling stack](../../adr/002-pyproject-hatchling-stack.md) — mypy strict-on-new policy that this module passes.
