"""
tests/test_log.py

Testy integracyjne dla algo_bot.log — weryfikują że logger zachowuje się
zgodnie z ADR-006: handlery są dodawane idempotentnie, caplog widzi rekordy,
JsonFormatter emituje walidny JSON z extra fields, third-party libs są
wyciszone.

Konwencja: bez mocków loggera (mindset reguła #3 — brak mocków w testach
o wartości integracyjnej). Używamy pytest caplog + tmp_path + realny
RotatingFileHandler na realny plik w tmp_path.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

import pytest

from algo_bot.log import JsonFormatter, get_logger, setup_logging


@pytest.fixture
def clean_root_handlers():
    """Usuwa nasze handlery z root loggera po teście.

    Powod: setup_logging() dorzuca StreamHandler + RotatingFileHandler do root.
    Między testami chcemy świeży start — inaczej drugi test zobaczy idempotent
    branch zamiast pierwszej inicjalizacji.
    """
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_algo_bot_handler", False):
            root.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()


# ============================================================================
# setup_logging — idempotency i podstawowa konfiguracja
# ============================================================================


def test_setup_logging_dodaje_handlery_przy_pierwszym_wywolaniu(
    tmp_path: Path, clean_root_handlers: None
) -> None:
    """Pierwsze setup_logging() instaluje dokładnie 2 nasze handlery na root."""
    setup_logging(log_dir=tmp_path)
    root = logging.getLogger()
    algo_handlers = [h for h in root.handlers if getattr(h, "_algo_bot_handler", False)]
    assert len(algo_handlers) == 2, (
        "Powinny być dokładnie 2 handlery (StreamHandler + RotatingFileHandler)"
    )


def test_setup_logging_jest_idempotentne(tmp_path: Path, clean_root_handlers: None) -> None:
    """Drugie wywołanie setup_logging() NIE duplikuje handlerów."""
    setup_logging(log_dir=tmp_path)
    setup_logging(log_dir=tmp_path)
    setup_logging(log_dir=tmp_path, level=logging.DEBUG)
    root = logging.getLogger()
    algo_handlers = [h for h in root.handlers if getattr(h, "_algo_bot_handler", False)]
    assert len(algo_handlers) == 2, (
        "Re-init nie powinien dodawać kolejnych handlerów (idempotency setup_logging)"
    )


def test_setup_logging_tworzy_katalog_log_dir(tmp_path: Path, clean_root_handlers: None) -> None:
    """log_dir jest tworzony jeśli nie istnieje (parents=True)."""
    nested = tmp_path / "results" / "live" / "run_001"
    assert not nested.exists()
    setup_logging(log_dir=nested)
    assert nested.exists() and nested.is_dir()


def test_setup_logging_wycisza_third_party_libs(tmp_path: Path, clean_root_handlers: None) -> None:
    """Domyślnie ccxt i urllib3 są wyciszone do WARNING (żeby retry chatter nie zaśmiecał)."""
    setup_logging(log_dir=tmp_path)
    assert logging.getLogger("ccxt").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING


# ============================================================================
# caplog — pytest fixture widzi rekordy z loggera algo_bot
# ============================================================================


def test_logger_emituje_info_widoczne_przez_caplog(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, clean_root_handlers: None
) -> None:
    """Callsite: backtester.save_outputs — logger.info('Wyniki backtestu zapisane', extra=...).

    Wartość integracyjna: jeśli ten test pęknie, znaczy że nasze setup_logging
    blokuje caplog (typowy gotcha gdy stdlib propagation jest popsuty).
    """
    setup_logging(log_dir=tmp_path)
    caplog.set_level(logging.INFO)

    logger = get_logger("algo_bot.engine.backtester")
    logger.info("Wyniki backtestu zapisane", extra={"out_dir": "/tmp/x", "run_id": "abc"})

    matching = [r for r in caplog.records if "Wyniki backtestu zapisane" in r.getMessage()]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    # extra fields są dorzucane na rekord przez logging — wyciągamy z __dict__,
    # żeby nie polegać na dynamicznych atrybutach (ruff B009 nie znosi getattr ze stałymi).
    assert record.__dict__["out_dir"] == "/tmp/x"
    assert record.__dict__["run_id"] == "abc"


def test_logger_emituje_warning_z_extra_widoczny_przez_caplog(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, clean_root_handlers: None
) -> None:
    """Callsite: live_binance.py — logger.warning('cancel_all_orders nieudane', extra={'error': ...}).

    Wartość integracyjna: kluczowe warnings w live tradingu (TPSL nieudane,
    cancel_all_orders fail) muszą być testowalne.
    """
    setup_logging(log_dir=tmp_path)
    caplog.set_level(logging.WARNING)

    logger = get_logger("live.live_binance")
    logger.warning("cancel_all_orders nieudane", extra={"error": "timeout after 10s"})

    matching = [r for r in caplog.records if "cancel_all_orders nieudane" in r.getMessage()]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.__dict__["error"] == "timeout after 10s"


def test_logger_emituje_error_z_exception_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, clean_root_handlers: None
) -> None:
    """logger.exception(...) wewnątrz except: powinno wpisać traceback do rekordu.

    Wartość integracyjna: failure modes w live (OPEN nieudany, CLOSE nieudany)
    muszą mieć pełen kontekst do post-mortem.
    """
    setup_logging(log_dir=tmp_path)
    caplog.set_level(logging.ERROR)

    logger = get_logger("live.live_binance")
    try:
        raise ValueError("symulowany błąd API Binance")
    except ValueError:
        logger.exception("OPEN nieudany", extra={"side": "long"})

    matching = [r for r in caplog.records if "OPEN nieudany" in r.getMessage()]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "ERROR"
    assert record.exc_info is not None
    assert record.__dict__["side"] == "long"


# ============================================================================
# JsonFormatter — output do pliku jest walidnym JSON-em z oczekiwanymi polami
# ============================================================================


def test_json_formatter_emituje_valid_json_z_wymaganymi_polami(
    tmp_path: Path, clean_root_handlers: None
) -> None:
    """RotatingFileHandler + JsonFormatter zapisuje walidny JSON per linia.

    Wartość integracyjna: gdy w Fazie 5 podłączymy Loki/Promtail, parser musi
    wczytać linie bez błędu. Test sprawdza realny plik (bez mocków).
    """
    setup_logging(log_dir=tmp_path, level=logging.INFO)
    logger = get_logger("test.json_format")
    logger.info(
        "test_event",
        extra={"run_id": "run_xyz", "side": "short", "qty": 0.001},
    )

    # wymusz flush wszystkich handlerów (zwłaszcza RotatingFileHandler).
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = tmp_path / "algo_bot.log"
    assert log_file.exists(), "RotatingFileHandler powinien stworzyć plik logu"

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1

    # ostatnia linia powinna zawierać nasze event_event
    last = json.loads(lines[-1])
    assert last["message"] == "test_event"
    assert last["level"] == "INFO"
    assert last["logger"] == "test.json_format"
    assert "ts" in last and last["ts"].endswith("Z"), "ts powinno być UTC z suffixem Z"
    assert last["run_id"] == "run_xyz"
    assert last["side"] == "short"
    assert last["qty"] == 0.001


def test_json_formatter_dziala_bez_zewnetrznego_setup(clean_root_handlers: None) -> None:
    """JsonFormatter jako klasa działa standalone — bez setup_logging().

    Wartość: ułatwia testowanie format'u w izolacji, daje fallback gdy ktoś chce
    użyć JsonFormatter w innym contextzie (np. własny handler do Loki).
    """
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.custom_field = "value"  # symuluje extra={"custom_field": "value"}

    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test"
    assert parsed["line"] == 42
    assert parsed["custom_field"] == "value"
