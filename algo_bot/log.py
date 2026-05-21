"""
algo_bot/log.py

Centralna konfiguracja loggera dla całego algo_bot. Stdlib ``logging`` z dwoma
handlerami: konsola (plain, czytelne dla człowieka, Europe/Warsaw timestamp)
+ rotating file (JSON, machine-readable, UTC timestamp).

Decyzja: patrz docs/adr/006-logging-strategy.md (stdlib zamiast loguru/structlog;
zero zewnętrznych dependency; rewizja w Fazie 5 jeśli observability stack
wymusi structured-first).

Public API:
    setup_logging(level=..., log_dir=..., ...) -> None
        Idempotentna inicjalizacja root loggera. Wywołać raz przy starcie aplikacji
        (live_binance, backtester, sweep, scripts).

    get_logger(name) -> logging.Logger
        Thin wrapper na logging.getLogger(name). Konwencja: ``get_logger(__name__)``
        per moduł.

Użycie (typowe):
    from algo_bot.log import setup_logging, get_logger

    setup_logging(level=logging.INFO, log_dir="logs", run_id="run_001")
    logger = get_logger(__name__)
    logger.info("Pozycja otwarta", extra={"side": "long", "qty": 0.1})

Format wyjścia:
    Console (stderr, plain):
        2026-05-21 14:23:01 [INFO] algo_bot.engine.backtester: Wyniki zapisane...

    File (logs/algo_bot.log, JSON per linia):
        {"ts": "2026-05-21T12:23:01.123Z", "level": "INFO",
         "logger": "algo_bot.engine.backtester", "message": "...", "side": "long"}

See also:
    docs/adr/006-logging-strategy.md (rationale, alternatives, future migration)
    docs/adr/002-pyproject-hatchling-stack.md (mypy strict-on-new — obowiązuje ten moduł)
    algo_bot/telemetry/journal.py (osobny layer — event store dla trades+equity)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Wewnętrzny sentinel — pozwala wykryć że handler jest nasz (idempotency setup_logging).
_ALGO_BOT_HANDLER_ATTR = "_algo_bot_handler"

# Zarezerwowane atrybuty LogRecord ze stdlib — NIE wciągamy ich do "extra" w JSON,
# bo to wewnętrzne pola loggera (args, msg, levelname, itd.), nie payload usera.
_LOGRECORD_RESERVED: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",  # py>=3.12
    }
)


class JsonFormatter(logging.Formatter):
    """Formatter emitujący jedną linię JSON-a per rekord logu.

    Schemat wyjścia (zawsze obecne pola):
        ts        — UTC timestamp w ISO 8601 (z mikrosekundami i "Z" suffix)
        level     — string (INFO, WARNING, ERROR, ...)
        logger    — nazwa loggera (typowo ``__name__`` modułu)
        message   — human-readable message po sformatowaniu args
        module    — nazwa modułu (bez pakietu)
        function  — nazwa funkcji wywołującej
        line      — numer linii w pliku

    Pola opcjonalne (gdy obecne w rekordzie):
        exception — pełny traceback gdy wywołano logger.exception() lub exc_info=True
        wszystkie pola z ``extra={...}`` przekazane do logger.log/info/error itp.

    Konwencja czasu:
        ts jest zawsze w UTC. Plain console formatter ma osobny timestamp w
        Europe/Warsaw — tu trzymamy UTC, bo JSON ma trafić do machine sinków
        (Loki, Sentry) które standardowo operują w UTC.

    Args:
        Brak — formatter konfigurowalny, ale domyślny output wystarcza w 99% przypadków.

    Returns:
        Sformatowana linia JSON (bez końcowego newline, dodawany przez handler).
    """

    def format(self, record: logging.LogRecord) -> str:
        # Najpierw renderujemy message z args (np. logger.info("hello %s", name)).
        message = record.getMessage()

        # Bazowe pola — zawsze obecne.
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Exception info (traceback) — gdy obecne, dorzucamy jako string.
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text

        # Stack info (gdy logger.<level>(..., stack_info=True)) — debug pomocniczo.
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # "extra={...}" przy wywołaniu loggera trafia jako custom atrybuty na record.
        # Wciągamy wszystko co nie jest atrybutem zarezerwowanym przez stdlib.
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_RESERVED or key.startswith("_"):
                continue
            # JSON dump musi przejść — gdy wartość nie jest serializowalna, użyj repr.
            try:
                json.dumps(value, default=str)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        return json.dumps(payload, ensure_ascii=False, default=str)


class _LocalTimeFormatter(logging.Formatter):
    """Plain formatter dla konsoli — timestamp w Europe/Warsaw (zgodnie z konwencją
    użytkownika repo).

    Format:
        2026-05-21 14:23:01 [INFO] algo_bot.engine.backtester: <message>

    Powód osobnej klasy: stdlib ``logging.Formatter`` używa ``time.localtime`` z
    systemowej strefy. Na VPS może być UTC (typowe), na laptopie Europe/Warsaw —
    chcemy stabilności niezależnie od hosta. Czas naprzez ZoneInfo, deterministycznie.
    """

    _LOCAL_TZ = ZoneInfo("Europe/Warsaw")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=self._LOCAL_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def _has_algo_bot_handler(logger: logging.Logger) -> bool:
    """Zwraca True jeśli na loggerze jest już zainstalowany nasz handler.

    Używane do idempotency ``setup_logging`` — żeby ponowne wywołanie nie
    duplikowało handlerów (typowe gdy moduły importują się cyklicznie albo
    testy resetują state).
    """
    return any(getattr(h, _ALGO_BOT_HANDLER_ATTR, False) for h in logger.handlers)


def setup_logging(
    *,
    level: int = logging.INFO,
    log_dir: Path | str = "logs",
    log_filename: str = "algo_bot.log",
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 5,
    console_level: int | None = None,
    third_party_levels: dict[str, int] | None = None,
) -> None:
    """Inicjalizuje root logger z konsola + rotating JSON file.

    Idempotentne — kolejne wywołania (np. w testach albo gdy entry point wywoła
    setup_logging wielokrotnie) nie duplikują handlerów. Zamiast tego aktualizują
    levele istniejących handlerów.

    Args:
        level: Globalny poziom logowania (DEBUG/INFO/WARNING/ERROR/CRITICAL).
            Filtruje rekordy ZANIM trafią do handlerów. Default: INFO.
        log_dir: Katalog na pliki logu (utworzony jeśli nie istnieje). Default: "logs"
            w cwd.
        log_filename: Nazwa pliku logu (rotowanego). Default: "algo_bot.log".
        file_max_bytes: Maksymalny rozmiar pojedynczego pliku przed rotacją.
            Default: 10 MB.
        file_backup_count: Liczba zachowanych starych plików (po rotacji).
            Default: 5 → max 6 plików × 10 MB = 60 MB suma.
        console_level: Poziom dla console handlera (gdy chcemy console = WARNING,
            file = INFO). Default: None → użyje globalnego ``level``.
        third_party_levels: Mapping nazwa-loggera → poziom dla wyciszenia
            chatterowych libów. Default: ``{"ccxt": WARNING, "urllib3": WARNING}``.

    Returns:
        None.

    Raises:
        OSError: Jeśli ``log_dir`` nie da się utworzyć (permission denied, full disk).

    Note:
        Bezpieczne do wywołania wielokrotnego. Re-init tylko aktualizuje levele,
        nie tworzy nowych handlerów (sprawdzane przez sentinel ``_algo_bot_handler``).
    """
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    if third_party_levels is None:
        third_party_levels = {"ccxt": logging.WARNING, "urllib3": logging.WARNING}

    root = logging.getLogger()
    root.setLevel(level)

    if _has_algo_bot_handler(root):
        # Idempotency: tylko zaktualizuj levele.
        for handler in root.handlers:
            if not getattr(handler, _ALGO_BOT_HANDLER_ATTR, False):
                continue
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                handler.setLevel(console_level if console_level is not None else level)
            elif isinstance(handler, RotatingFileHandler):
                handler.setLevel(level)
    else:
        # Pierwsza inicjalizacja — załóż dwa handlery.
        console = logging.StreamHandler(stream=sys.stderr)
        console.setLevel(console_level if console_level is not None else level)
        console.setFormatter(
            _LocalTimeFormatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
        )
        setattr(console, _ALGO_BOT_HANDLER_ATTR, True)
        root.addHandler(console)

        file_path = log_dir_path / log_filename
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=file_max_bytes,
            backupCount=file_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        setattr(file_handler, _ALGO_BOT_HANDLER_ATTR, True)
        root.addHandler(file_handler)

    # Wycisz hałaśliwe third-party libs (ccxt retries, urllib3 connection pool).
    for logger_name, third_party_level in third_party_levels.items():
        logging.getLogger(logger_name).setLevel(third_party_level)


def get_logger(name: str) -> logging.Logger:
    """Zwraca logger o podanej nazwie.

    Konwencja wywołania: ``get_logger(__name__)`` w każdym module który loguje.
    Daje hierarchiczny logger (np. ``algo_bot.engine.backtester`` < ``algo_bot.engine``
    < ``algo_bot`` < root), co pozwala filtrować level per subpakiet.

    Args:
        name: Nazwa loggera. Konwencja: ``__name__`` w module.

    Returns:
        logging.Logger — w 100% zgodny z stdlib API (``.info()``, ``.warning()``,
        ``.error()``, ``.exception()``, ``.log(level, ...)`` itp.).

    Note:
        To jest thin wrapper na ``logging.getLogger(name)``. Wprowadzony żeby:
        (1) mieć single import point z modułu algo_bot.log,
        (2) zostawić miejsce na wstrzyknięcie własnej logiki w przyszłości
        (np. domyślny LoggerAdapter z run_id).
    """
    return logging.getLogger(name)
