"""Pinned NautilusTrader P3 data and execution-profile primitives.

This module is intentionally small.  It records only the facts needed by the
MR-Session 3 P3 hard gate:

* CCXT/Binance OHLCV timestamps are bar-open timestamps;
* the normalized CCXT row must be moved to Binance's inclusive bar close before
  it is exposed to NautilusTrader; and
* the two characterized execution profiles use either no latency (equivalence
  only) or the smallest positive latency (causal H1 bar-only research).

It is not the Tier-1 adapter and contains no strategy thesis or OMS policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity

MILLISECONDS_TO_NANOSECONDS = 1_000_000
RESEARCH_CAUSAL_MIN_LATENCY_NS = 1


class P3ExecutionProfile(StrEnum):
    """Execution profiles characterized by the synthetic P3 BacktestEngine tests."""

    EQUIVALENCE_ON_CLOSE_V1 = "EQUIVALENCE_ON_CLOSE_V1"
    RESEARCH_CAUSAL_NEXT_CLOSE_V1 = "RESEARCH_CAUSAL_NEXT_CLOSE_V1"


def binance_bar_close_ns(open_time_ms: int, interval_ms: int) -> int:
    """Return Binance's inclusive kline close timestamp in UNIX nanoseconds.

    Binance reports ``closeTime`` as the final millisecond inside the interval.
    CCXT keeps only ``openTime`` in its normalized six-element OHLCV row, so the
    equivalent close is ``open + interval - 1 ms``.  The next bar opens exactly
    one millisecond later.
    """

    if isinstance(open_time_ms, bool) or not isinstance(open_time_ms, int):
        raise TypeError("open_time_ms must be an int")
    if isinstance(interval_ms, bool) or not isinstance(interval_ms, int):
        raise TypeError("interval_ms must be an int")
    if open_time_ms < 0:
        raise ValueError("open_time_ms must be non-negative")
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")

    close_time_ms = open_time_ms + interval_ms - 1
    return close_time_ms * MILLISECONDS_TO_NANOSECONDS


def ccxt_ohlcv_to_nautilus_bar(
    row: Sequence[object],
    *,
    instrument: Instrument,
    bar_type: BarType,
    interval_ms: int,
) -> Bar:
    """Convert one normalized CCXT OHLCV row into a close-timestamped Bar.

    ``row`` must use CCXT's canonical order ``[open_ms, O, H, L, C, volume]``.
    For historical data with no modeled ingestion delay, both ``ts_event`` and
    ``ts_init`` are the reconstructed Binance close timestamp.  This prevents a
    completed bar from reaching ``on_bar`` at its raw open timestamp.
    """

    if len(row) < 6:
        raise ValueError("CCXT OHLCV row must contain at least six values")
    if bar_type.instrument_id != instrument.id:
        raise ValueError("bar_type and instrument must have the same instrument_id")

    open_time = _integer_timestamp(row[0])
    close_ns = binance_bar_close_ns(open_time, interval_ms)

    return Bar(
        bar_type=bar_type,
        open=Price(_finite_decimal(row[1], "open"), instrument.price_precision),
        high=Price(_finite_decimal(row[2], "high"), instrument.price_precision),
        low=Price(_finite_decimal(row[3], "low"), instrument.price_precision),
        close=Price(_finite_decimal(row[4], "close"), instrument.price_precision),
        volume=Quantity(_finite_decimal(row[5], "volume"), instrument.size_precision),
        ts_event=close_ns,
        ts_init=close_ns,
    )


def latency_model_for_profile(profile: P3ExecutionProfile) -> LatencyModel | None:
    """Return the exact latency model characterized for a P3 profile.

    ``None`` is NautilusTrader's true zero-latency path.  Constructing a default
    ``LatencyModel`` is not equivalent: it has a positive base latency.  The
    research profile deliberately uses one nanosecond, which the H1-only engine
    fixture observes as a fill at the next bar's close (never its open).
    """

    if profile is P3ExecutionProfile.EQUIVALENCE_ON_CLOSE_V1:
        return None
    if profile is P3ExecutionProfile.RESEARCH_CAUSAL_NEXT_CLOSE_V1:
        return LatencyModel(base_latency_nanos=RESEARCH_CAUSAL_MIN_LATENCY_NS)
    raise ValueError(f"Unsupported P3 execution profile: {profile!r}")


def _integer_timestamp(value: object) -> int:
    decimal = _finite_decimal(value, "timestamp")
    if decimal != decimal.to_integral_value():
        raise ValueError("CCXT OHLCV timestamp must be an integer number of milliseconds")
    return int(decimal)


def _finite_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not decimal.is_finite():
        raise ValueError(f"{field} must be finite")
    return decimal
