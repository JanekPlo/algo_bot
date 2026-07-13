"""Frozen P9 development-data boundary for the MMS-inspired Beta smoke path.

This module prepares data only.  It never runs ``NautilusMastermindStrategy``,
reads strategy metrics, or writes a backtest result.  The CSV readers stop after
the last development row by count and timestamp, before requesting the first
holdout row from the file iterator.  Consequently holdout bytes are neither
parsed nor included in any digest.

CCXT/Binance OHLCV timestamps are bar-open times.  Native external bars map
``ts_event == ts_init`` to ``open + 1h - 1ms``, the close-time convention frozen
by P3.  Bollinger Bands and Stochastic are computed over warmup plus development
data, but only development bars and their causal close-keyed features are
exposed to the P7 PyO3 smoke runner.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import talib
from nautilus_trader import __version__ as nautilus_version
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.nautilus_mastermind import BarFeatures, BarFeatureSource

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OHLCV_PATH = PROJECT_ROOT / "bot_data/processed/binance_BTCUSDT_1h.csv"
DEFAULT_FUNDING_PATH = PROJECT_ROOT / "bot_data/processed/binance_BTCUSDT_funding.csv"

WARMUP_START = datetime(2023, 12, 23, 16, tzinfo=UTC)
DEVELOPMENT_START = datetime(2024, 1, 1, 0, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 7, 1, 0, tzinfo=UTC)
H1 = timedelta(hours=1)
FUNDING_INTERVAL = timedelta(hours=8)
FUNDING_JITTER_LIMIT = timedelta(seconds=1)
HOUR_NS = 3_600_000_000_000
MILLISECOND_NS = 1_000_000
FUNDING_UPDATE_LEAD_NS = 1

WARMUP_BARS = 200
DEVELOPMENT_BARS = 13_128
INPUT_OHLCV_ROWS = WARMUP_BARS + DEVELOPMENT_BARS
DEVELOPMENT_FUNDING_ROWS = 1_641

DATA_SCHEMA_VERSION = "mms_beta_development_data/1"
FEATURE_MODEL_ID = "TALIB_BB20_2_STOCH14_3_3_V1"
TIMESTAMP_PROFILE_ID = "CCXT_OPEN_TO_BINANCE_CLOSE_MINUS_1MS_V1"
FUNDING_PROFILE_ID = "HISTORICAL_RATE_UPDATE_1NS_BEFORE_SETTLEMENT_V1"
INSTRUMENT_ID = "BTCUSDT-PERP.BINANCE"
BAR_TYPE = f"{INSTRUMENT_ID}-1-HOUR-LAST-EXTERNAL"

_OHLCV_COLUMNS = ("datetime", "Open", "High", "Low", "Close", "Volume", "ts")
_FUNDING_COLUMNS = ("datetime", "funding_rate")
_SMA_MA_TYPE = cast(Any, talib).MA_Type.SMA


class MmsBetaDataError(ValueError):
    """Development input violates the frozen P9 data contract."""


@dataclass(frozen=True, slots=True)
class LoadedOhlcv:
    """Streaming OHLCV load plus a digest of included rows only."""

    frame: pd.DataFrame
    sha256: str


@dataclass(frozen=True, slots=True)
class LoadedFunding:
    """Streaming funding load plus a digest of included rows only."""

    frame: pd.DataFrame
    sha256: str


@dataclass(frozen=True, slots=True)
class MmsBetaDataMetadata:
    """Deterministic provenance for a development-only native input bundle."""

    schema_version: str
    instrument_id: str
    bar_type: str
    warmup_start_utc: str
    development_start_utc: str
    holdout_start_utc: str
    warmup_bars: int
    development_bars: int
    funding_updates: int
    timestamp_profile: str
    feature_model: str
    funding_profile: str
    ohlcv_hash: str
    funding_hash: str
    features_hash: str
    data_hash: str
    config_hash: str
    nautilus_version: str
    talib_version: str
    holdout_rows_read: int = 0

    def as_dict(self) -> dict[str, str | int]:
        """Return deterministic JSON-friendly metadata."""

        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "warmup_start_utc": self.warmup_start_utc,
            "development_start_utc": self.development_start_utc,
            "holdout_start_utc": self.holdout_start_utc,
            "warmup_bars": self.warmup_bars,
            "development_bars": self.development_bars,
            "funding_updates": self.funding_updates,
            "timestamp_profile": self.timestamp_profile,
            "feature_model": self.feature_model,
            "funding_profile": self.funding_profile,
            "ohlcv_hash": self.ohlcv_hash,
            "funding_hash": self.funding_hash,
            "features_hash": self.features_hash,
            "data_hash": self.data_hash,
            "config_hash": self.config_hash,
            "nautilus_version": self.nautilus_version,
            "talib_version": self.talib_version,
            "holdout_rows_read": self.holdout_rows_read,
        }


class CloseNsBarFeatureSource:
    """P7 ``BarFeatureSource`` indexed strictly by final bar-close nanoseconds."""

    def __init__(self, features_by_close_ns: Mapping[int, BarFeatures]) -> None:
        if not features_by_close_ns:
            raise MmsBetaDataError("feature source cannot be empty")
        keys = tuple(features_by_close_ns)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise MmsBetaDataError("feature close timestamps must be unique and sorted")
        self._features = dict(features_by_close_ns)

    def __call__(self, bar: Any) -> BarFeatures:
        """Resolve features using exactly the native bar's ``ts_init``."""

        return self.for_close_ns(int(bar.ts_init))

    def for_close_ns(self, close_ns: int) -> BarFeatures:
        """Resolve one close directly, raising rather than using nearest data."""

        try:
            return self._features[close_ns]
        except KeyError as exc:
            raise MmsBetaDataError(f"no causal features for close_ns={close_ns}") from exc

    @property
    def close_timestamps_ns(self) -> tuple[int, ...]:
        """Return the immutable ordered key set."""

        return tuple(self._features)

    def as_mapping(self) -> Mapping[int, BarFeatures]:
        """Return a defensive copy for audits and hashing."""

        return dict(self._features)

    def as_p7_source(self) -> BarFeatureSource:
        """Expose the exact callable type consumed by ``run_pyo3_mastermind_smoke``."""

        return self


@dataclass(frozen=True, slots=True)
class MmsBetaDevelopmentData:
    """Actual development inputs ready for ``run_pyo3_mastermind_smoke``."""

    ohlcv_with_warmup: pd.DataFrame
    development_ohlcv: pd.DataFrame
    funding_rates: pd.DataFrame
    feature_source: CloseNsBarFeatureSource
    instrument: Any
    bar_type: Any
    bars: tuple[Any, ...]
    funding_updates: tuple[Any, ...]
    native_data: tuple[Any, ...]
    metadata: MmsBetaDataMetadata


def load_mms_beta_development_data(
    *,
    ohlcv_path: Path = DEFAULT_OHLCV_PATH,
    funding_path: Path = DEFAULT_FUNDING_PATH,
) -> MmsBetaDevelopmentData:
    """Load, validate, feature, and convert only the frozen development window."""

    loaded_ohlcv = stream_development_ohlcv(ohlcv_path)
    loaded_funding = stream_development_funding(funding_path)
    feature_source, features_hash = build_bar_feature_source(loaded_ohlcv.frame)
    instrument, bar_type = build_btcusdt_perpetual()
    development = loaded_ohlcv.frame.loc[
        (loaded_ohlcv.frame.index >= DEVELOPMENT_START) & (loaded_ohlcv.frame.index < HOLDOUT_START)
    ].copy(deep=True)
    bars = build_close_timestamped_bars(development, instrument, bar_type)
    funding_updates = build_funding_rate_updates(loaded_funding.frame, instrument)
    native_data = tuple(sorted((*bars, *funding_updates), key=lambda item: int(item.ts_init)))

    config_values = _config_values()
    config_hash = _json_hash(config_values)
    data_hash = _json_hash(
        {
            "ohlcv_hash": loaded_ohlcv.sha256,
            "funding_hash": loaded_funding.sha256,
        }
    )
    metadata = MmsBetaDataMetadata(
        schema_version=DATA_SCHEMA_VERSION,
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        warmup_start_utc=_iso(WARMUP_START),
        development_start_utc=_iso(DEVELOPMENT_START),
        holdout_start_utc=_iso(HOLDOUT_START),
        warmup_bars=WARMUP_BARS,
        development_bars=len(development),
        funding_updates=len(funding_updates),
        timestamp_profile=TIMESTAMP_PROFILE_ID,
        feature_model=FEATURE_MODEL_ID,
        funding_profile=FUNDING_PROFILE_ID,
        ohlcv_hash=loaded_ohlcv.sha256,
        funding_hash=loaded_funding.sha256,
        features_hash=features_hash,
        data_hash=data_hash,
        config_hash=config_hash,
        nautilus_version=nautilus_version,
        talib_version=talib.__version__,
    )
    return MmsBetaDevelopmentData(
        ohlcv_with_warmup=loaded_ohlcv.frame.copy(deep=True),
        development_ohlcv=development,
        funding_rates=loaded_funding.frame.copy(deep=True),
        feature_source=feature_source,
        instrument=instrument,
        bar_type=bar_type,
        bars=bars,
        funding_updates=funding_updates,
        native_data=native_data,
        metadata=metadata,
    )


def stream_development_ohlcv(path: Path) -> LoadedOhlcv:
    """Stream warmup+development OHLCV and stop before requesting holdout."""

    rows: list[dict[str, object]] = []
    digest = hashlib.sha256()
    digest.update(_canonical_json({"columns": list(_OHLCV_COLUMNS)}).encode("utf-8"))
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise MmsBetaDataError(f"cannot open OHLCV CSV {path}: {exc}") from exc
    with handle:
        header_line = handle.readline()
        header = _parse_csv_line(header_line, "OHLCV header")
        positions = _column_positions(header, _OHLCV_COLUMNS, "OHLCV")
        for raw_line in handle:
            timestamp_text = raw_line.partition(",")[0]
            timestamp = _parse_utc(timestamp_text, "OHLCV datetime")
            if timestamp < WARMUP_START:
                continue
            if len(rows) >= INPUT_OHLCV_ROWS:
                break
            values = _parse_csv_line(raw_line, f"OHLCV row {len(rows)}")
            row = _parse_ohlcv_row(values, positions)
            expected = WARMUP_START + len(rows) * H1
            observed = cast(datetime, row["datetime"])
            if observed != expected:
                _raise_hourly_boundary_error(observed, expected, rows)
            rows.append(row)
            digest.update(_canonical_json(_hashable_ohlcv_row(row)).encode("utf-8"))
            digest.update(b"\n")
            if len(rows) == INPUT_OHLCV_ROWS:
                # Deliberately return to the caller without requesting the next
                # file line, which is the first holdout bar in a contiguous CSV.
                break
    if len(rows) != INPUT_OHLCV_ROWS:
        raise MmsBetaDataError(
            f"OHLCV ended before development boundary: {len(rows)}/{INPUT_OHLCV_ROWS} rows"
        )
    frame = pd.DataFrame.from_records(rows).set_index("datetime")
    validate_hourly_ohlcv(
        frame,
        expected_start=WARMUP_START,
        expected_end=HOLDOUT_START,
        expected_rows=INPUT_OHLCV_ROWS,
    )
    return LoadedOhlcv(frame=frame, sha256=digest.hexdigest())


def stream_development_funding(path: Path) -> LoadedFunding:
    """Stream every development funding slot and stop before holdout bytes."""

    rows: list[dict[str, object]] = []
    digest = hashlib.sha256()
    digest.update(_canonical_json({"columns": list(_FUNDING_COLUMNS)}).encode("utf-8"))
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise MmsBetaDataError(f"cannot open funding CSV {path}: {exc}") from exc
    with handle:
        header_line = handle.readline()
        header = _parse_csv_line(header_line, "funding header")
        positions = _column_positions(header, _FUNDING_COLUMNS, "funding")
        for raw_line in handle:
            timestamp_text = raw_line.partition(",")[0]
            timestamp = _parse_utc(timestamp_text, "funding datetime")
            if timestamp < DEVELOPMENT_START:
                continue
            if len(rows) >= DEVELOPMENT_FUNDING_ROWS:
                break
            values = _parse_csv_line(raw_line, f"funding row {len(rows)}")
            row = _parse_funding_row(values, positions)
            expected_slot = DEVELOPMENT_START + len(rows) * FUNDING_INTERVAL
            observed = cast(datetime, row["datetime"])
            _validate_funding_slot(observed, expected_slot)
            rows.append(row)
            digest.update(_canonical_json(_hashable_funding_row(row)).encode("utf-8"))
            digest.update(b"\n")
            if len(rows) == DEVELOPMENT_FUNDING_ROWS:
                break
    if len(rows) != DEVELOPMENT_FUNDING_ROWS:
        raise MmsBetaDataError(
            "funding ended before development boundary: "
            f"{len(rows)}/{DEVELOPMENT_FUNDING_ROWS} rows"
        )
    frame = pd.DataFrame.from_records(rows).set_index("datetime")
    validate_funding_boundaries(
        frame,
        expected_start=DEVELOPMENT_START,
        expected_end=HOLDOUT_START,
        expected_rows=DEVELOPMENT_FUNDING_ROWS,
    )
    return LoadedFunding(frame=frame, sha256=digest.hexdigest())


def validate_hourly_ohlcv(
    frame: pd.DataFrame,
    *,
    expected_start: datetime,
    expected_end: datetime,
    expected_rows: int,
) -> None:
    """Validate UTC H1 boundaries, duplicates, gaps, finiteness, and OHLC."""

    if not isinstance(frame.index, pd.DatetimeIndex):
        raise MmsBetaDataError("OHLCV index must be a DatetimeIndex")
    _validate_utc_index(frame.index, "OHLCV")
    if frame.index.has_duplicates:
        raise MmsBetaDataError("OHLCV contains duplicate timestamps")
    if not frame.index.is_monotonic_increasing:
        raise MmsBetaDataError("OHLCV timestamps are not strictly increasing")
    if len(frame) != expected_rows:
        raise MmsBetaDataError(f"OHLCV row count {len(frame)} != {expected_rows}")
    expected_index = pd.date_range(
        expected_start,
        expected_end,
        freq="1h",
        inclusive="left",
    )
    if len(expected_index) != expected_rows or not frame.index.equals(expected_index):
        raise MmsBetaDataError("OHLCV has an H1 gap or incorrect development boundary")
    required = {"Open", "High", "Low", "Close", "Volume", "ts"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise MmsBetaDataError(f"OHLCV missing columns: {missing}")
    numeric = frame[["Open", "High", "Low", "Close", "Volume"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise MmsBetaDataError("OHLCV contains NaN or non-finite values")
    prices = frame[["Open", "High", "Low", "Close"]]
    if (prices <= 0).to_numpy().any():
        raise MmsBetaDataError("OHLC prices must be positive")
    if (frame["Volume"] < 0).any():
        raise MmsBetaDataError("OHLCV volume must be non-negative")
    if (frame["High"] < prices[["Open", "Close", "Low"]].max(axis=1)).any():
        raise MmsBetaDataError("OHLC High is below another price")
    if (frame["Low"] > prices[["Open", "Close", "High"]].min(axis=1)).any():
        raise MmsBetaDataError("OHLC Low is above another price")
    scaled_prices = prices.to_numpy(dtype=float) * 10.0
    if not np.allclose(scaled_prices, np.rint(scaled_prices), rtol=0.0, atol=1e-9):
        raise MmsBetaDataError("OHLC price is not aligned to the 0.1 instrument tick")
    scaled_volume = frame["Volume"].to_numpy(dtype=float) * 1_000.0
    if not np.allclose(scaled_volume, np.rint(scaled_volume), rtol=0.0, atol=1e-6):
        raise MmsBetaDataError("OHLCV volume is not aligned to the 0.001 size step")
    expected_ms = np.array(
        [_to_ns(timestamp.to_pydatetime()) // 1_000_000 for timestamp in frame.index]
    )
    observed_ms = pd.to_numeric(frame["ts"], errors="coerce").to_numpy()
    if not np.array_equal(observed_ms, expected_ms):
        raise MmsBetaDataError("OHLCV ts does not equal the CCXT bar-open timestamp")


def validate_funding_boundaries(
    frame: pd.DataFrame,
    *,
    expected_start: datetime,
    expected_end: datetime,
    expected_rows: int,
) -> None:
    """Validate one finite historical rate per nominal 8-hour slot."""

    if not isinstance(frame.index, pd.DatetimeIndex):
        raise MmsBetaDataError("funding index must be a DatetimeIndex")
    _validate_utc_index(frame.index, "funding")
    if frame.index.has_duplicates:
        raise MmsBetaDataError("funding contains duplicate timestamps")
    if not frame.index.is_monotonic_increasing:
        raise MmsBetaDataError("funding timestamps are not strictly increasing")
    if len(frame) != expected_rows:
        raise MmsBetaDataError(f"funding row count {len(frame)} != {expected_rows}")
    if "funding_rate" not in frame.columns:
        raise MmsBetaDataError("funding_rate column is missing")
    rates = pd.to_numeric(frame["funding_rate"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(rates).all():
        raise MmsBetaDataError("funding contains NaN or non-finite rates")
    for ordinal, timestamp in enumerate(frame.index):
        expected_slot = expected_start + ordinal * FUNDING_INTERVAL
        _validate_funding_slot(timestamp.to_pydatetime(), expected_slot)
    if expected_start + expected_rows * FUNDING_INTERVAL != expected_end:
        raise MmsBetaDataError("funding expected boundaries do not form complete 8h slots")


def build_bar_feature_source(
    ohlcv_with_warmup: pd.DataFrame,
    *,
    development_start: datetime = DEVELOPMENT_START,
    holdout_start: datetime = HOLDOUT_START,
) -> tuple[CloseNsBarFeatureSource, str]:
    """Compute causal TA-Lib BB20/2 and Slow STOCH 14/3/3 at final closes."""

    for column in ("High", "Low", "Close"):
        if column not in ohlcv_with_warmup.columns:
            raise MmsBetaDataError(f"feature input missing {column}")
    high = ohlcv_with_warmup["High"].to_numpy(dtype=np.float64)
    low = ohlcv_with_warmup["Low"].to_numpy(dtype=np.float64)
    close = ohlcv_with_warmup["Close"].to_numpy(dtype=np.float64)
    if not np.isfinite(np.column_stack((high, low, close))).all():
        raise MmsBetaDataError("feature input contains non-finite prices")

    upper_raw, _middle_raw, lower_raw = talib.BBANDS(
        close,
        timeperiod=20,
        nbdevup=2.0,
        nbdevdn=2.0,
        matype=_SMA_MA_TYPE,
    )
    stoch_k_raw, stoch_d_raw = talib.STOCH(
        high,
        low,
        close,
        fastk_period=14,
        slowk_period=3,
        slowk_matype=_SMA_MA_TYPE,
        slowd_period=3,
        slowd_matype=_SMA_MA_TYPE,
    )
    upper = cast(np.ndarray[Any, np.dtype[np.float64]], upper_raw)
    lower = cast(np.ndarray[Any, np.dtype[np.float64]], lower_raw)
    stoch_k = cast(np.ndarray[Any, np.dtype[np.float64]], stoch_k_raw)
    stoch_d = cast(np.ndarray[Any, np.dtype[np.float64]], stoch_d_raw)

    by_close: dict[int, BarFeatures] = {}
    hash_rows: list[dict[str, str | int | None]] = []
    for position, timestamp in enumerate(ohlcv_with_warmup.index):
        timestamp_dt = timestamp.to_pydatetime()
        if timestamp_dt < development_start or timestamp_dt >= holdout_start:
            continue
        values = (upper[position], lower[position], stoch_k[position], stoch_d[position])
        if not all(math.isfinite(float(value)) for value in values):
            raise MmsBetaDataError(
                f"indicator warmup incomplete at development bar {timestamp_dt.isoformat()}"
            )
        previous_k = stoch_k[position - 1] if position > 0 else np.nan
        previous_d = stoch_d[position - 1] if position > 0 else np.nan
        if not math.isfinite(float(previous_k)) or not math.isfinite(float(previous_d)):
            raise MmsBetaDataError(f"previous Stochastic unavailable at {timestamp_dt.isoformat()}")
        close_ns = _bar_close_ns(timestamp_dt)
        features = BarFeatures(
            bb_upper=_decimal_float(upper[position]),
            bb_lower=_decimal_float(lower[position]),
            stoch_k=_decimal_float(stoch_k[position]),
            stoch_d=_decimal_float(stoch_d[position]),
            previous_stoch_k=_decimal_float(previous_k),
            previous_stoch_d=_decimal_float(previous_d),
        )
        by_close[close_ns] = features
        hash_rows.append(
            {
                "close_ns": close_ns,
                "bb_upper": str(features.bb_upper),
                "bb_lower": str(features.bb_lower),
                "stoch_k": _optional_decimal_string(features.stoch_k),
                "stoch_d": _optional_decimal_string(features.stoch_d),
                "previous_stoch_k": _optional_decimal_string(features.previous_stoch_k),
                "previous_stoch_d": _optional_decimal_string(features.previous_stoch_d),
            }
        )
    source = CloseNsBarFeatureSource(by_close)
    return source, _json_hash(hash_rows)


def build_btcusdt_perpetual() -> tuple[Any, Any]:
    """Build the real PyO3 BTCUSDT perpetual and external H1 BarType."""

    instrument_id = nt.InstrumentId.from_str(INSTRUMENT_ID)
    usdt = nt.Currency.from_str("USDT")
    instrument = nt.CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=nt.Symbol.from_str("BTCUSDT"),
        base_currency=nt.Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=nt.Price.from_str("0.1"),
        size_increment=nt.Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        multiplier=nt.Quantity.from_str("1"),
        min_quantity=nt.Quantity.from_str("0.001"),
        margin_init=Decimal("0.05"),
        margin_maint=Decimal("0.025"),
        maker_fee=Decimal("0.0004"),
        taker_fee=Decimal("0.0004"),
    )
    return instrument, nt.BarType.from_str(BAR_TYPE)


def build_close_timestamped_bars(
    development_ohlcv: pd.DataFrame,
    instrument: Any,
    bar_type: Any,
) -> tuple[Any, ...]:
    """Convert development open-time rows into final close-time PyO3 bars."""

    bars: list[Any] = []
    for timestamp, row in development_ohlcv.iterrows():
        timestamp_dt = cast(pd.Timestamp, timestamp).to_pydatetime()
        if timestamp_dt < DEVELOPMENT_START or timestamp_dt >= HOLDOUT_START:
            raise MmsBetaDataError("native bar conversion received non-development data")
        close_ns = _bar_close_ns(timestamp_dt)
        bars.append(
            nt.Bar(
                bar_type=bar_type,
                open=instrument.make_price(_decimal_value(row["Open"], "Open")),
                high=instrument.make_price(_decimal_value(row["High"], "High")),
                low=instrument.make_price(_decimal_value(row["Low"], "Low")),
                close=instrument.make_price(_decimal_value(row["Close"], "Close")),
                volume=instrument.make_qty(_decimal_value(row["Volume"], "Volume")),
                ts_event=close_ns,
                ts_init=close_ns,
            )
        )
    if len(bars) != DEVELOPMENT_BARS:
        raise MmsBetaDataError(f"native bar count {len(bars)} != {DEVELOPMENT_BARS}")
    return tuple(bars)


def build_funding_rate_updates(funding: pd.DataFrame, instrument: Any) -> tuple[Any, ...]:
    """Build historical rates which settle at their exact Binance timestamps.

    The PyO3 backtester needs a rate update before ``next_funding_ns``.  The
    update is therefore timestamped one nanosecond before settlement.  The
    strategy wrapper has no funding-rate signal callback, so this cannot enter
    the trading thesis; it only enables the native settlement adjustment.
    """

    updates: list[Any] = []
    for timestamp, row in funding.iterrows():
        settlement_ns = _to_ns(cast(pd.Timestamp, timestamp).to_pydatetime())
        rate = _decimal_value(row["funding_rate"], "funding_rate")
        updates.append(
            nt.FundingRateUpdate(
                instrument.id,
                rate,
                settlement_ns - FUNDING_UPDATE_LEAD_NS,
                settlement_ns - FUNDING_UPDATE_LEAD_NS,
                interval=28_800,
                next_funding_ns=settlement_ns,
            )
        )
    if len(updates) != DEVELOPMENT_FUNDING_ROWS:
        raise MmsBetaDataError(
            f"native funding update count {len(updates)} != {DEVELOPMENT_FUNDING_ROWS}"
        )
    return tuple(updates)


def _parse_ohlcv_row(values: Sequence[str], positions: Mapping[str, int]) -> dict[str, object]:
    timestamp = _parse_utc(values[positions["datetime"]], "OHLCV datetime")
    row: dict[str, object] = {
        "datetime": timestamp,
        "Open": _finite_float(values[positions["Open"]], "Open"),
        "High": _finite_float(values[positions["High"]], "High"),
        "Low": _finite_float(values[positions["Low"]], "Low"),
        "Close": _finite_float(values[positions["Close"]], "Close"),
        "Volume": _finite_float(values[positions["Volume"]], "Volume"),
        "ts": _integer(values[positions["ts"]], "ts"),
    }
    return row


def _parse_funding_row(values: Sequence[str], positions: Mapping[str, int]) -> dict[str, object]:
    return {
        "datetime": _parse_utc(values[positions["datetime"]], "funding datetime"),
        "funding_rate": _finite_float(
            values[positions["funding_rate"]],
            "funding_rate",
        ),
    }


def _parse_csv_line(raw_line: str, label: str) -> list[str]:
    if not raw_line:
        raise MmsBetaDataError(f"missing {label}")
    try:
        values = next(csv.reader([raw_line]))
    except (csv.Error, StopIteration) as exc:
        raise MmsBetaDataError(f"invalid {label}: {exc}") from exc
    return values


def _column_positions(header: Sequence[str], required: Sequence[str], label: str) -> dict[str, int]:
    positions: dict[str, int] = {}
    for column in required:
        try:
            positions[column] = header.index(column)
        except ValueError as exc:
            raise MmsBetaDataError(f"{label} CSV missing column {column}") from exc
    return positions


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MmsBetaDataError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MmsBetaDataError(f"{field} must be explicit UTC: {value!r}")
    return parsed.astimezone(UTC)


def _finite_float(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise MmsBetaDataError(f"invalid numeric {field}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise MmsBetaDataError(f"non-finite {field}: {value!r}")
    return parsed


def _integer(value: str, field: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise MmsBetaDataError(f"invalid integer {field}: {value!r}") from exc


def _decimal_value(value: object, field: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MmsBetaDataError(f"invalid decimal {field}: {value!r}") from exc
    if not decimal.is_finite():
        raise MmsBetaDataError(f"non-finite decimal {field}: {value!r}")
    return decimal


def _decimal_float(value: np.floating[Any] | float) -> Decimal:
    number = float(value)
    if not math.isfinite(number):
        raise MmsBetaDataError("indicator value is non-finite")
    return Decimal(str(number))


def _optional_decimal_string(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _validate_utc_index(index: pd.DatetimeIndex, label: str) -> None:
    if index.tz is None:
        raise MmsBetaDataError(f"{label} timestamps must be timezone-aware UTC")
    if str(index.tz) != "UTC":
        raise MmsBetaDataError(f"{label} timestamps must use UTC, got {index.tz}")


def _validate_funding_slot(observed: datetime, expected: datetime) -> None:
    delta = observed - expected
    if delta < timedelta(0) or delta >= FUNDING_JITTER_LIMIT:
        raise MmsBetaDataError(
            "funding timestamp is outside its nominal 8h slot: "
            f"observed={observed.isoformat()} expected={expected.isoformat()}"
        )


def _raise_hourly_boundary_error(
    observed: datetime,
    expected: datetime,
    rows: Sequence[Mapping[str, object]],
) -> None:
    if rows:
        previous = cast(datetime, rows[-1]["datetime"])
        if observed == previous:
            raise MmsBetaDataError(f"duplicate OHLCV timestamp {observed.isoformat()}")
        if observed < previous:
            raise MmsBetaDataError("OHLCV timestamps are not monotonic")
    raise MmsBetaDataError(
        f"OHLCV H1 gap/boundary mismatch: {observed.isoformat()} != {expected.isoformat()}"
    )


def _hashable_ohlcv_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "datetime": _iso(cast(datetime, row["datetime"])),
        "Open": row["Open"],
        "High": row["High"],
        "Low": row["Low"],
        "Close": row["Close"],
        "Volume": row["Volume"],
        "ts": row["ts"],
    }


def _hashable_funding_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "datetime": _iso(cast(datetime, row["datetime"])),
        "funding_rate": row["funding_rate"],
    }


def _config_values() -> dict[str, object]:
    return {
        "schema_version": DATA_SCHEMA_VERSION,
        "instrument_id": INSTRUMENT_ID,
        "bar_type": BAR_TYPE,
        "warmup_start": _iso(WARMUP_START),
        "development_start": _iso(DEVELOPMENT_START),
        "holdout_start": _iso(HOLDOUT_START),
        "warmup_bars": WARMUP_BARS,
        "indicators": {
            "bb": {"period": 20, "std_up": 2.0, "std_down": 2.0, "matype": 0},
            "stoch": {
                "fastk_period": 14,
                "slowk_period": 3,
                "slowk_matype": 0,
                "slowd_period": 3,
                "slowd_matype": 0,
            },
        },
        "timestamp_profile": TIMESTAMP_PROFILE_ID,
        "funding_profile": FUNDING_PROFILE_ID,
        "instrument": {
            "maker_fee": "0.0004",
            "taker_fee": "0.0004",
            "price_increment": "0.1",
            "size_increment": "0.001",
        },
        "nautilus_version": nautilus_version,
        "talib_version": talib.__version__,
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bar_close_ns(open_time: datetime) -> int:
    return _to_ns(open_time) + HOUR_NS - MILLISECOND_NS


def _to_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise MmsBetaDataError("timestamp must be aware UTC before ns conversion")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return (
        delta.days * 86_400_000_000_000 + delta.seconds * 1_000_000_000 + delta.microseconds * 1_000
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
