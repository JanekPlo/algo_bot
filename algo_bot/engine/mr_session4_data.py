"""Wejścia Bybit ograniczone do development dla sweepu MMS MR-Session 4.

Readery zatrzymują się po ostatnim prerejestrowanym wierszu development i nigdy
nie proszą o pierwszy wiersz holdoutu. Uszkodzony lub zmieniony ogon holdoutu nie
może więc wpłynąć na załadowane ramki ani ich hashe. M10 powstaje z już przyciętej
ramki M5; ogólny resampler ``process_data`` nie jest używany, bo czyta cały plik.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import talib
from nautilus_trader import __version__ as nautilus_version
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.backtest_result import JsonValue, canonical_json, json_hash
from algo_bot.engine.mms_beta_data import CloseNsBarFeatureSource
from algo_bot.engine.nautilus_mastermind import BarFeatures
from algo_bot.microstructure import MaintenanceMarginTier, MarkPriceContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DATA_ROOT = PROJECT_ROOT / "bot_data/processed"

DATA_SCHEMA_VERSION = "mr_session4_data/2"
FEATURE_MODEL_VERSION = "TALIB_BB_VARIABLE_STOCH14_3_3_V1"
TIMESTAMP_PROFILE = "BYBIT_OPEN_TO_INCLUSIVE_CLOSE_V1"
FUNDING_PROFILE = "BYBIT_HISTORICAL_RATE_UPDATE_1NS_BEFORE_SETTLEMENT_V1"
MARK_PRICE_WARMUP_PROFILE = "ONE_PREDEVELOPMENT_H1_FOR_FIRST_FUNDING_MARK_V1"
NATIVE_MARK_UPDATE_PROFILE = "COMPLETED_H1_MARK_CLOSE_PRESERVE_SOURCE_PRECISION_FUNDING_BASIS_V1"
H1_M5_ALIGNMENT_PROFILE = "REPORT_INDEPENDENT_H1_VS_M5_AGGREGATE_DIVERGENCE_V1"
MAX_H1_M5_PRICE_DIVERGENCE_BPS = Decimal("25")
NATIVE_BAR_GRID_PROFILE = "FAIL_OFF_TICK_PRICE_OR_QUANTITY_STEP_V1"

WARMUP_START = datetime(2021, 3, 23, 16, tzinfo=UTC)
DEVELOPMENT_START = datetime(2021, 4, 1, 0, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 7, 1, 0, tzinfo=UTC)
WARMUP_BARS = 200

H1 = timedelta(hours=1)
M5 = timedelta(minutes=5)
M10 = timedelta(minutes=10)
FUNDING_INTERVAL = timedelta(hours=8)
FUNDING_UPDATE_LEAD_NS = 1
MILLISECOND_NS = 1_000_000

_OHLCV_COLUMNS = ("datetime", "Open", "High", "Low", "Close", "Volume")
_MARK_COLUMNS = ("datetime", "Open", "High", "Low", "Close")
_FUNDING_COLUMNS = ("datetime", "funding_rate")
_SMA_MA_TYPE = cast(Any, talib).MA_Type.SMA


class Session4DataError(ValueError):
    """Wejście Session 4 narusza zamrożoną granicę danych."""


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    symbol: str
    base_currency: str
    instrument_id: str
    price_precision: int
    size_precision: int
    price_increment: Decimal
    size_increment: Decimal
    min_quantity: Decimal
    min_notional: Decimal = Decimal("5")
    maker_fee: Decimal = Decimal("0.0002")
    taker_fee: Decimal = Decimal("0.00055")


SYMBOL_SPECS: Mapping[str, SymbolSpec] = {
    "BTCUSDT": SymbolSpec(
        symbol="BTCUSDT",
        base_currency="BTC",
        instrument_id="BTCUSDT-PERP.BYBIT",
        price_precision=1,
        size_precision=3,
        price_increment=Decimal("0.1"),
        size_increment=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    ),
    "ETHUSDT": SymbolSpec(
        symbol="ETHUSDT",
        base_currency="ETH",
        instrument_id="ETHUSDT-PERP.BYBIT",
        price_precision=2,
        size_precision=2,
        price_increment=Decimal("0.01"),
        size_increment=Decimal("0.01"),
        min_quantity=Decimal("0.01"),
    ),
}


@dataclass(frozen=True, slots=True)
class WindowFrame:
    frame: pd.DataFrame
    sha256: str
    rows_read: int
    holdout_rows_read: int = 0


@dataclass(frozen=True, slots=True)
class Session4DataMetadata:
    symbol: str
    warmup_start_utc: str
    development_start_utc: str
    holdout_start_utc: str
    source_hashes: Mapping[str, str]
    source_rows: Mapping[str, int]
    m10_derived_hash: str
    native_mark_updates_hash: str
    h1_m5_alignment: Mapping[str, JsonValue]
    data_hash: str
    risk_tiers_hash: str
    holdout_rows_read: int = 0
    schema_version: str = DATA_SCHEMA_VERSION

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "warmup_start_utc": self.warmup_start_utc,
            "development_start_utc": self.development_start_utc,
            "holdout_start_utc": self.holdout_start_utc,
            "source_hashes": dict(self.source_hashes),
            "source_rows": dict(self.source_rows),
            "m10_derived_hash": self.m10_derived_hash,
            "native_mark_updates_hash": self.native_mark_updates_hash,
            "h1_m5_alignment": dict(self.h1_m5_alignment),
            "data_hash": self.data_hash,
            "risk_tiers_hash": self.risk_tiers_hash,
            "holdout_rows_read": self.holdout_rows_read,
            "timestamp_profile": TIMESTAMP_PROFILE,
            "funding_profile": FUNDING_PROFILE,
            "mark_price_warmup_profile": MARK_PRICE_WARMUP_PROFILE,
            "native_mark_update_profile": NATIVE_MARK_UPDATE_PROFILE,
            "h1_m5_alignment_profile": H1_M5_ALIGNMENT_PROFILE,
            "native_bar_grid_profile": NATIVE_BAR_GRID_PROFILE,
            "feature_model_version": FEATURE_MODEL_VERSION,
        }


@dataclass(slots=True)
class Session4DataBundle:
    spec: SymbolSpec
    h1_with_warmup: pd.DataFrame
    development_h1: pd.DataFrame
    development_m5: pd.DataFrame
    development_m10: pd.DataFrame
    funding: pd.DataFrame
    mark_context: MarkPriceContext
    instrument: Any
    h1_bar_type: Any
    m5_bar_type: Any
    m10_bar_type: Any
    h1_bars: tuple[Any, ...]
    m5_bars: tuple[Any, ...]
    m10_bars: tuple[Any, ...]
    funding_updates: tuple[Any, ...]
    mark_price_updates: tuple[Any, ...]
    native_data: tuple[Any, ...]
    metadata: Session4DataMetadata
    _feature_cache: dict[tuple[int, str], tuple[CloseNsBarFeatureSource, str]] = field(
        default_factory=dict,
        repr=False,
    )

    def feature_source(
        self,
        bb_window: int,
        bb_num_std: Decimal,
    ) -> tuple[CloseNsBarFeatureSource, str]:
        """Buduje i trzyma mały LRU cech dla aktualnej grupy parametrów."""

        key = (bb_window, str(bb_num_std))
        cached = self._feature_cache.get(key)
        if cached is not None:
            return cached
        built = build_feature_source(
            self.h1_with_warmup,
            bb_window=bb_window,
            bb_num_std=bb_num_std,
        )
        if len(self._feature_cache) >= 2:
            oldest = next(iter(self._feature_cache))
            del self._feature_cache[oldest]
        self._feature_cache[key] = built
        return built

    def marking_data(self, timeframe: str) -> tuple[Any, tuple[Any, ...], int, str]:
        if timeframe == "5m":
            return (
                self.m5_bar_type,
                self.m5_bars,
                _to_ns_delta(M5),
                self.metadata.source_hashes["m5"],
            )
        if timeframe == "10m":
            return (
                self.m10_bar_type,
                self.m10_bars,
                _to_ns_delta(M10),
                self.metadata.m10_derived_hash,
            )
        raise Session4DataError(f"unsupported marking timeframe: {timeframe!r}")

    def run_data_hash(self, *, feature_hash: str, marking_timeframe: str) -> str:
        _bar_type, _bars, _interval, marking_hash = self.marking_data(marking_timeframe)
        return json_hash(
            {
                "bundle_data_hash": self.metadata.data_hash,
                "feature_hash": feature_hash,
                "marking_timeframe": marking_timeframe,
                "marking_hash": marking_hash,
                "risk_tiers_hash": self.metadata.risk_tiers_hash,
                "native_mark_updates_hash": self.metadata.native_mark_updates_hash,
                "native_mark_update_profile": NATIVE_MARK_UPDATE_PROFILE,
            }
        )


def load_session4_data(
    symbol: str,
    *,
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...],
    risk_tiers_hash: str,
    data_root: Path = PROCESSED_DATA_ROOT,
) -> Session4DataBundle:
    """Ładuje wyłącznie warmup+development dla jednego symbolu."""

    try:
        spec = SYMBOL_SPECS[symbol]
    except KeyError as exc:
        raise Session4DataError(f"unsupported symbol: {symbol!r}") from exc
    if not maintenance_margin_tiers:
        raise Session4DataError(f"{symbol} maintenance-margin tiers are missing")
    _require_sha256(risk_tiers_hash, "risk_tiers_hash")

    h1 = stream_ohlcv_window(
        data_root / f"bybit_{symbol}_1h.csv",
        start=WARMUP_START,
        end=HOLDOUT_START,
        interval=H1,
    )
    expected_h1 = _expected_rows(WARMUP_START, HOLDOUT_START, H1)
    if len(h1.frame) != expected_h1:
        raise Session4DataError(f"{symbol} H1 count drift")
    development_h1 = h1.frame.loc[
        (h1.frame.index >= DEVELOPMENT_START) & (h1.frame.index < HOLDOUT_START)
    ].copy(deep=True)
    if len(h1.frame.loc[h1.frame.index < DEVELOPMENT_START]) != WARMUP_BARS:
        raise Session4DataError(f"{symbol} does not have exactly {WARMUP_BARS} warmup H1 bars")

    m5 = stream_ohlcv_window(
        data_root / f"bybit_{symbol}_5m.csv",
        start=DEVELOPMENT_START,
        end=HOLDOUT_START,
        interval=M5,
    )
    m10 = aggregate_m5_to_m10(m5.frame)
    m10_hash = dataframe_content_hash(m10, _OHLCV_COLUMNS[1:])
    h1_m5_alignment = build_h1_m5_alignment_report(
        development_h1,
        m5.frame,
        tick_size=spec.price_increment,
    )
    validate_native_bar_grid(h1.frame, spec, source_name=f"{symbol} H1")
    validate_native_bar_grid(m5.frame, spec, source_name=f"{symbol} M5")
    validate_native_bar_grid(m10, spec, source_name=f"{symbol} derived M10")
    funding = stream_funding_window(
        data_root / f"bybit_{symbol}_funding.csv",
        start=DEVELOPMENT_START,
        end=HOLDOUT_START,
    )
    mark_path = data_root / f"bybit_{symbol}_mark_1h.csv"
    mark_warmup = stream_mark_window(
        mark_path,
        start=DEVELOPMENT_START - H1,
        end=DEVELOPMENT_START,
    )
    if len(mark_warmup.frame) != 1:
        raise Session4DataError(f"{symbol} requires exactly one pre-development mark H1")
    mark = stream_mark_window(
        mark_path,
        start=DEVELOPMENT_START,
        end=HOLDOUT_START,
    )
    mark_with_warmup = pd.concat((mark_warmup.frame, mark.frame), axis=0)
    _validate_frame(
        mark_with_warmup,
        start=DEVELOPMENT_START - H1,
        end=HOLDOUT_START,
        interval=H1,
        columns=_MARK_COLUMNS[1:],
    )

    instrument, h1_bar_type, m5_bar_type, m10_bar_type = build_bybit_perpetual(spec)
    h1_bars = build_native_bars(development_h1, instrument, h1_bar_type, H1)
    m5_bars = build_native_bars(m5.frame, instrument, m5_bar_type, M5)
    m10_bars = build_native_bars(m10, instrument, m10_bar_type, M10)
    funding_updates = build_funding_updates(funding.frame, instrument)
    mark_price_updates, mark_updates_hash = build_native_mark_price_updates(
        mark_with_warmup,
        instrument,
        start=DEVELOPMENT_START - H1,
        end=HOLDOUT_START,
    )
    validate_funding_mark_update_coverage(mark_price_updates, funding_updates)
    # Stabilny sort zachowuje mark update przed H1 barem przy wspólnym close_ns.
    native_data = tuple(
        sorted(
            (*mark_price_updates, *h1_bars, *funding_updates),
            key=lambda item: int(item.ts_init),
        )
    )

    mark_context = MarkPriceContext(
        symbol=symbol,
        exchange="bybit",
        timeframe="1h",
        bars=mark_with_warmup,
        source=(f"bot_data/processed/bybit_{symbol}_mark_1h.csv#one-predevelopment-h1+development"),
        maintenance_margin_tiers=maintenance_margin_tiers,
        taker_fee_rate=float(spec.taker_fee),
    )
    source_hashes = {
        "h1_with_warmup": h1.sha256,
        "m5": m5.sha256,
        "funding": funding.sha256,
        "mark_h1": mark.sha256,
        "mark_h1_predevelopment_warmup": mark_warmup.sha256,
    }
    source_rows = {
        "h1_with_warmup": len(h1.frame),
        "development_h1": len(development_h1),
        "m5": len(m5.frame),
        "m10_derived": len(m10),
        "h1_derived_from_m5": len(development_h1),
        "funding": len(funding.frame),
        "mark_h1": len(mark.frame),
        "mark_h1_predevelopment_warmup": len(mark_warmup.frame),
        "native_mark_updates": len(mark_price_updates),
    }
    data_hash = json_hash(
        {
            "schema_version": DATA_SCHEMA_VERSION,
            "symbol": symbol,
            "windows": {
                "warmup_start": _iso(WARMUP_START),
                "development_start": _iso(DEVELOPMENT_START),
                "holdout_start": _iso(HOLDOUT_START),
            },
            "source_hashes": source_hashes,
            "m10_derived_hash": m10_hash,
            "native_mark_updates_hash": mark_updates_hash,
            "mark_price_warmup_profile": MARK_PRICE_WARMUP_PROFILE,
            "native_mark_update_profile": NATIVE_MARK_UPDATE_PROFILE,
            "h1_m5_alignment": h1_m5_alignment,
            "risk_tiers_hash": risk_tiers_hash,
        }
    )
    metadata = Session4DataMetadata(
        symbol=symbol,
        warmup_start_utc=_iso(WARMUP_START),
        development_start_utc=_iso(DEVELOPMENT_START),
        holdout_start_utc=_iso(HOLDOUT_START),
        source_hashes=source_hashes,
        source_rows=source_rows,
        m10_derived_hash=m10_hash,
        native_mark_updates_hash=mark_updates_hash,
        h1_m5_alignment=h1_m5_alignment,
        data_hash=data_hash,
        risk_tiers_hash=risk_tiers_hash,
    )
    return Session4DataBundle(
        spec=spec,
        h1_with_warmup=h1.frame,
        development_h1=development_h1,
        development_m5=m5.frame,
        development_m10=m10,
        funding=funding.frame,
        mark_context=mark_context,
        instrument=instrument,
        h1_bar_type=h1_bar_type,
        m5_bar_type=m5_bar_type,
        m10_bar_type=m10_bar_type,
        h1_bars=h1_bars,
        m5_bars=m5_bars,
        m10_bars=m10_bars,
        funding_updates=funding_updates,
        mark_price_updates=mark_price_updates,
        native_data=native_data,
        metadata=metadata,
    )


def stream_ohlcv_window(
    path: Path,
    *,
    start: datetime,
    end: datetime,
    interval: timedelta,
) -> WindowFrame:
    return _stream_window(
        path,
        start=start,
        end=end,
        interval=interval,
        columns=_OHLCV_COLUMNS,
        parser=_parse_ohlcv,
    )


def stream_mark_window(path: Path, *, start: datetime, end: datetime) -> WindowFrame:
    return _stream_window(
        path,
        start=start,
        end=end,
        interval=H1,
        columns=_MARK_COLUMNS,
        parser=_parse_mark,
    )


def stream_funding_window(path: Path, *, start: datetime, end: datetime) -> WindowFrame:
    return _stream_window(
        path,
        start=start,
        end=end,
        interval=FUNDING_INTERVAL,
        columns=_FUNDING_COLUMNS,
        parser=_parse_funding,
    )


def _stream_window(
    path: Path,
    *,
    start: datetime,
    end: datetime,
    interval: timedelta,
    columns: Sequence[str],
    parser: Any,
) -> WindowFrame:
    _require_utc(start, "start")
    _require_utc(end, "end")
    expected_rows = _expected_rows(start, end, interval)
    records: list[dict[str, object]] = []
    digest = hashlib.sha256()
    digest.update(canonical_json({"columns": list(columns)}).encode("utf-8"))
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise Session4DataError(f"cannot open {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        missing = [column for column in columns if column not in (reader.fieldnames or ())]
        if missing:
            raise Session4DataError(f"{path} missing columns: {missing}")
        for raw in reader:
            timestamp = _parse_utc(raw.get("datetime", ""), f"{path.name} datetime")
            if timestamp < start:
                continue
            expected = start + len(records) * interval
            if timestamp != expected:
                raise Session4DataError(
                    f"{path.name} gap/boundary drift: observed={timestamp.isoformat()} "
                    f"expected={expected.isoformat()}"
                )
            record = cast(dict[str, object], parser(raw, timestamp))
            records.append(record)
            digest.update(canonical_json(_hashable_record(record)).encode("utf-8"))
            digest.update(b"\n")
            if len(records) == expected_rows:
                # Do not ask DictReader for the first holdout record.
                break
    if len(records) != expected_rows:
        raise Session4DataError(
            f"{path.name} ended before development boundary: {len(records)}/{expected_rows}"
        )
    frame = pd.DataFrame.from_records(records).set_index("datetime")
    _validate_frame(frame, start=start, end=end, interval=interval, columns=columns[1:])
    return WindowFrame(frame=frame, sha256=digest.hexdigest(), rows_read=len(frame))


def aggregate_m5_to_m10(
    frame: pd.DataFrame,
    *,
    start: datetime = DEVELOPMENT_START,
    end: datetime = HOLDOUT_START,
) -> pd.DataFrame:
    """Agreguje dokładnie dwie kompletne świece M5 w jedną M10."""

    _validate_frame(
        frame,
        start=start,
        end=end,
        interval=M5,
        columns=_OHLCV_COLUMNS[1:],
    )
    group_ids = np.arange(len(frame), dtype=np.int64) // 2
    counts = pd.Series(1, index=frame.index).groupby(group_ids).sum()
    if bool((counts != 2).any()):
        raise Session4DataError("M10 aggregation found an incomplete M5 pair")
    grouped = frame.groupby(group_ids, sort=True)
    result = pd.DataFrame(
        {
            "Open": grouped["Open"].first().to_numpy(),
            "High": grouped["High"].max().to_numpy(),
            "Low": grouped["Low"].min().to_numpy(),
            "Close": grouped["Close"].last().to_numpy(),
            "Volume": grouped["Volume"].sum().to_numpy(),
        },
        index=pd.DatetimeIndex(frame.index[::2], name="datetime"),
    )
    _validate_frame(
        result,
        start=start,
        end=end,
        interval=M10,
        columns=_OHLCV_COLUMNS[1:],
    )
    return result


def aggregate_m5_to_h1(
    frame: pd.DataFrame,
    *,
    start: datetime = DEVELOPMENT_START,
    end: datetime = HOLDOUT_START,
) -> pd.DataFrame:
    """Agreguje 12 kompletnych świec M5 do niezależnego oracle H1."""

    _validate_frame(
        frame,
        start=start,
        end=end,
        interval=M5,
        columns=_OHLCV_COLUMNS[1:],
    )
    group_ids = np.arange(len(frame), dtype=np.int64) // 12
    counts = pd.Series(1, index=frame.index).groupby(group_ids).sum()
    if bool((counts != 12).any()):
        raise Session4DataError("H1 aggregation found an incomplete M5 group")
    grouped = frame.groupby(group_ids, sort=True)
    result = pd.DataFrame(
        {
            "Open": grouped["Open"].first().to_numpy(),
            "High": grouped["High"].max().to_numpy(),
            "Low": grouped["Low"].min().to_numpy(),
            "Close": grouped["Close"].last().to_numpy(),
            "Volume": grouped["Volume"].sum().to_numpy(),
        },
        index=pd.DatetimeIndex(frame.index[::12], name="datetime"),
    )
    _validate_frame(
        result,
        start=start,
        end=end,
        interval=H1,
        columns=_OHLCV_COLUMNS[1:],
    )
    return result


def build_h1_m5_alignment_report(
    h1: pd.DataFrame,
    m5: pd.DataFrame,
    *,
    tick_size: Decimal,
    start: datetime = DEVELOPMENT_START,
    end: datetime = HOLDOUT_START,
) -> dict[str, JsonValue]:
    """Zamraża rozbieżności niezależnych feedów zamiast je ukrywać lub naprawiać."""

    _validate_frame(
        h1,
        start=start,
        end=end,
        interval=H1,
        columns=_OHLCV_COLUMNS[1:],
    )
    derived = aggregate_m5_to_h1(m5, start=start, end=end)
    if not h1.index.equals(derived.index):
        raise Session4DataError("H1 and M5-derived H1 timestamp grids differ")
    tick = float(tick_size)
    if not math.isfinite(tick) or tick <= 0:
        raise Session4DataError("tick_size must be positive and finite")

    price_columns = ("Open", "High", "Low", "Close")
    exact_union = np.zeros(len(h1), dtype=bool)
    beyond_tick_union = np.zeros(len(h1), dtype=bool)
    max_price_delta_bps = 0.0
    columns: dict[str, JsonValue] = {}
    for column in price_columns:
        delta = (h1[column] - derived[column]).abs().to_numpy(dtype=float)
        h1_values = h1[column].to_numpy(dtype=float)
        delta_bps = delta / h1_values * 10_000.0
        exact = delta > 0.0
        # ULP noise is much smaller than a venue tick; the tolerance only prevents
        # a binary-float boundary from classifying exactly one tick as larger.
        beyond_tick = delta > tick + max(1e-12, tick * 1e-12)
        exact_union |= exact
        beyond_tick_union |= beyond_tick
        max_delta = float(delta.max(initial=0.0))
        column_max_bps = float(delta_bps.max(initial=0.0))
        max_price_delta_bps = max(max_price_delta_bps, column_max_bps)
        columns[column] = {
            "exact_mismatch_bars": int(exact.sum()),
            "beyond_one_tick_bars": int(beyond_tick.sum()),
            "max_abs_delta": format(max_delta, ".17g"),
            "max_delta_ticks": format(max_delta / tick, ".17g"),
            "max_abs_delta_bps": format(column_max_bps, ".17g"),
        }

    allowed_bps = float(MAX_H1_M5_PRICE_DIVERGENCE_BPS)
    if max_price_delta_bps > allowed_bps:
        raise Session4DataError(
            "H1 vs M5-derived H1 price divergence exceeds frozen "
            f"{MAX_H1_M5_PRICE_DIVERGENCE_BPS} bps boundary: {max_price_delta_bps:.12g}"
        )

    volume_delta = (h1["Volume"] - derived["Volume"]).abs().to_numpy(dtype=float)
    mismatch_rows: list[dict[str, JsonValue]] = []
    for raw_position in np.flatnonzero(exact_union):
        position = int(raw_position)
        delta_record: dict[str, JsonValue] = {}
        for column in price_columns:
            h1_value = float(h1[column].iloc[position])
            derived_value = float(derived[column].iloc[position])
            delta_record[column] = format(abs(h1_value - derived_value), ".17g")
        mismatch_rows.append(
            {
                "datetime": pd.Timestamp(h1.index[position]).isoformat(),
                "delta": delta_record,
            }
        )
    return {
        "profile": H1_M5_ALIGNMENT_PROFILE,
        "policy": "REPORT_ONLY_INDEPENDENT_FEEDS_NO_RECONCILIATION",
        "acceptance_status": "ACCEPTED",
        "max_accepted_price_delta_bps": str(MAX_H1_M5_PRICE_DIVERGENCE_BPS),
        "observed_max_price_delta_bps": format(max_price_delta_bps, ".17g"),
        "row_count": len(h1),
        "tick_size": str(tick_size),
        "exact_price_mismatch_bar_count": int(exact_union.sum()),
        "beyond_one_tick_price_bar_count": int(beyond_tick_union.sum()),
        "price_mismatch_ledger_hash": json_hash(mismatch_rows),
        "price_columns": columns,
        "volume_mismatch_bar_count": int((volume_delta > 0.0).sum()),
        "max_abs_volume_delta": format(float(volume_delta.max(initial=0.0)), ".17g"),
        "m5_derived_h1_hash": dataframe_content_hash(derived, _OHLCV_COLUMNS[1:]),
    }


def validate_native_bar_grid(
    frame: pd.DataFrame,
    spec: SymbolSpec,
    *,
    source_name: str,
) -> None:
    """Odrzuca wartości, które natywny instrument zaokrągliłby po cichu."""

    increments = {
        "Open": float(spec.price_increment),
        "High": float(spec.price_increment),
        "Low": float(spec.price_increment),
        "Close": float(spec.price_increment),
        "Volume": float(spec.size_increment),
    }
    for column, increment in increments.items():
        if column not in frame.columns:
            raise Session4DataError(f"{source_name} missing native-grid column {column}")
        scaled = frame[column].to_numpy(dtype=float) / increment
        distance = np.abs(scaled - np.rint(scaled))
        failures = np.flatnonzero(distance > 1e-7)
        if failures.size:
            position = int(failures[0])
            timestamp = pd.Timestamp(frame.index[position]).isoformat()
            value = float(frame[column].iloc[position])
            raise Session4DataError(
                f"{source_name} {column} is off native increment {increment} "
                f"at {timestamp}: {value}"
            )


def build_feature_source(
    h1_with_warmup: pd.DataFrame,
    *,
    bb_window: int,
    bb_num_std: Decimal,
) -> tuple[CloseNsBarFeatureSource, str]:
    """Liczy parametryczne BB i zamrożony Stochastic wyłącznie przyczynowo."""

    if bb_window < 2 or bb_num_std <= 0 or not bb_num_std.is_finite():
        raise Session4DataError("invalid Bollinger parameters")
    high = h1_with_warmup["High"].to_numpy(dtype=np.float64)
    low = h1_with_warmup["Low"].to_numpy(dtype=np.float64)
    close = h1_with_warmup["Close"].to_numpy(dtype=np.float64)
    upper_raw, _middle, lower_raw = talib.BBANDS(
        close,
        timeperiod=bb_window,
        nbdevup=float(bb_num_std),
        nbdevdn=float(bb_num_std),
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
    digest = hashlib.sha256()
    for position, timestamp in enumerate(h1_with_warmup.index):
        timestamp_dt = cast(pd.Timestamp, timestamp).to_pydatetime()
        if timestamp_dt < DEVELOPMENT_START or timestamp_dt >= HOLDOUT_START:
            continue
        values = (upper[position], lower[position], stoch_k[position], stoch_d[position])
        previous = (stoch_k[position - 1], stoch_d[position - 1])
        if not all(math.isfinite(float(value)) for value in (*values, *previous)):
            raise Session4DataError(f"indicator warmup incomplete at {timestamp_dt.isoformat()}")
        close_ns = _close_ns(timestamp_dt, H1)
        features = BarFeatures(
            bb_upper=Decimal(str(float(upper[position]))),
            bb_lower=Decimal(str(float(lower[position]))),
            stoch_k=Decimal(str(float(stoch_k[position]))),
            stoch_d=Decimal(str(float(stoch_d[position]))),
            previous_stoch_k=Decimal(str(float(previous[0]))),
            previous_stoch_d=Decimal(str(float(previous[1]))),
        )
        by_close[close_ns] = features
        digest.update(
            canonical_json(
                {
                    "close_ns": close_ns,
                    "bb_upper": str(features.bb_upper),
                    "bb_lower": str(features.bb_lower),
                    "stoch_k": str(features.stoch_k),
                    "stoch_d": str(features.stoch_d),
                    "previous_stoch_k": str(features.previous_stoch_k),
                    "previous_stoch_d": str(features.previous_stoch_d),
                }
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return CloseNsBarFeatureSource(by_close), digest.hexdigest()


def build_bybit_perpetual(spec: SymbolSpec) -> tuple[Any, Any, Any, Any]:
    instrument_id = nt.InstrumentId.from_str(spec.instrument_id)
    usdt = nt.Currency.from_str("USDT")
    instrument = nt.CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=nt.Symbol.from_str(spec.symbol),
        base_currency=nt.Currency.from_str(spec.base_currency),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=spec.price_precision,
        size_precision=spec.size_precision,
        price_increment=nt.Price.from_str(str(spec.price_increment)),
        size_increment=nt.Quantity.from_str(str(spec.size_increment)),
        ts_event=0,
        ts_init=0,
        multiplier=nt.Quantity.from_str("1"),
        min_quantity=nt.Quantity.from_str(str(spec.min_quantity)),
        margin_init=Decimal("0.5"),
        margin_maint=Decimal("0.005"),
        maker_fee=spec.maker_fee,
        taker_fee=spec.taker_fee,
    )
    types = tuple(
        nt.BarType.from_str(f"{spec.instrument_id}-{step}-{unit}-LAST-EXTERNAL")
        for step, unit in ((1, "HOUR"), (5, "MINUTE"), (10, "MINUTE"))
    )
    return instrument, types[0], types[1], types[2]


def build_native_bars(
    frame: pd.DataFrame,
    instrument: Any,
    bar_type: Any,
    interval: timedelta,
) -> tuple[Any, ...]:
    bars: list[Any] = []
    for timestamp, row in frame.iterrows():
        opened = cast(pd.Timestamp, timestamp).to_pydatetime()
        close_ns = _close_ns(opened, interval)
        bars.append(
            nt.Bar(
                bar_type=bar_type,
                open=instrument.make_price(_decimal(row["Open"], "Open")),
                high=instrument.make_price(_decimal(row["High"], "High")),
                low=instrument.make_price(_decimal(row["Low"], "Low")),
                close=instrument.make_price(_decimal(row["Close"], "Close")),
                volume=instrument.make_qty(_decimal(row["Volume"], "Volume")),
                ts_event=close_ns,
                ts_init=close_ns,
            )
        )
    return tuple(bars)


def build_native_mark_price_updates(
    frame: pd.DataFrame,
    instrument: Any,
    *,
    start: datetime,
    end: datetime,
) -> tuple[tuple[Any, ...], str]:
    """Buduje przyczynowe H1 mark update'y bez zaokrąglania do trade ticka.

    Mark price Bybit może mieć większą precyzję niż cena transakcyjna (np.
    BTC ``.05`` przy ticku ``0.1``). Dlatego konstrukcja używa bezpośrednio
    :class:`Price.from_str`, a nie ``instrument.make_price``.
    """

    _validate_frame(
        frame,
        start=start,
        end=end,
        interval=H1,
        columns=_MARK_COLUMNS[1:],
    )
    updates: list[Any] = []
    digest = hashlib.sha256()
    digest.update(
        canonical_json(
            {
                "profile": NATIVE_MARK_UPDATE_PROFILE,
                "start": _iso(start),
                "end": _iso(end),
            }
        ).encode("utf-8")
    )
    for timestamp, row in frame.iterrows():
        opened = cast(pd.Timestamp, timestamp).to_pydatetime()
        close_ns = _close_ns(opened, H1)
        mark_close = _decimal(row["Close"], "mark Close")
        price_text = format(mark_close, "f")
        update = nt.MarkPriceUpdate(
            instrument.id,
            nt.Price.from_str(price_text),
            close_ns,
            close_ns,
        )
        updates.append(update)
        digest.update(b"\n")
        digest.update(
            canonical_json(
                {
                    "open_time_utc": cast(pd.Timestamp, timestamp).isoformat(),
                    "close_ns": close_ns,
                    "mark_close": price_text,
                }
            ).encode("utf-8")
        )
    return tuple(updates), digest.hexdigest()


def validate_funding_mark_update_coverage(
    mark_updates: Sequence[Any],
    funding_updates: Sequence[Any],
) -> None:
    """Wymaga ukończonego mark H1 dokładnie przed każdym settlementem."""

    mark_by_close = {int(update.ts_init): update for update in mark_updates}
    if len(mark_by_close) != len(mark_updates):
        raise Session4DataError("native mark-price update timestamps are not unique")
    for funding_update in funding_updates:
        settlement_ns = int(funding_update.next_funding_ns)
        expected_mark_close_ns = settlement_ns - MILLISECOND_NS
        try:
            mark_update = mark_by_close[expected_mark_close_ns]
        except KeyError as exc:
            raise Session4DataError(
                "funding settlement lacks the immediately preceding completed mark H1: "
                f"{settlement_ns}"
            ) from exc
        if int(mark_update.ts_init) >= int(funding_update.ts_init):
            raise Session4DataError("mark update is not causal before funding rate update")


def build_funding_updates(frame: pd.DataFrame, instrument: Any) -> tuple[Any, ...]:
    updates: list[Any] = []
    for timestamp, row in frame.iterrows():
        settlement_ns = _to_ns(cast(pd.Timestamp, timestamp).to_pydatetime())
        updates.append(
            nt.FundingRateUpdate(
                instrument.id,
                _decimal(row["funding_rate"], "funding_rate"),
                settlement_ns - FUNDING_UPDATE_LEAD_NS,
                settlement_ns - FUNDING_UPDATE_LEAD_NS,
                interval=28_800,
                next_funding_ns=settlement_ns,
            )
        )
    return tuple(updates)


def dataframe_content_hash(frame: pd.DataFrame, columns: Sequence[str]) -> str:
    digest = hashlib.sha256()
    digest.update(canonical_json({"columns": list(columns)}).encode("utf-8"))
    for timestamp, row in frame.iterrows():
        payload: dict[str, object] = {"datetime": cast(pd.Timestamp, timestamp).isoformat()}
        payload.update({column: float(row[column]) for column in columns})
        digest.update(canonical_json(payload).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _parse_ohlcv(raw: Mapping[str, str | None], timestamp: datetime) -> dict[str, object]:
    return {
        "datetime": timestamp,
        "Open": _finite(raw.get("Open"), "Open"),
        "High": _finite(raw.get("High"), "High"),
        "Low": _finite(raw.get("Low"), "Low"),
        "Close": _finite(raw.get("Close"), "Close"),
        "Volume": _finite(raw.get("Volume"), "Volume"),
    }


def _parse_mark(raw: Mapping[str, str | None], timestamp: datetime) -> dict[str, object]:
    record = _parse_ohlcv({**raw, "Volume": "0"}, timestamp)
    del record["Volume"]
    return record


def _parse_funding(raw: Mapping[str, str | None], timestamp: datetime) -> dict[str, object]:
    return {
        "datetime": timestamp,
        "funding_rate": _finite(raw.get("funding_rate"), "funding_rate"),
    }


def _validate_frame(
    frame: pd.DataFrame,
    *,
    start: datetime,
    end: datetime,
    interval: timedelta,
    columns: Sequence[str],
) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise Session4DataError("frame index must be timezone-aware")
    expected = pd.date_range(start, end, freq=pd.Timedelta(interval), inclusive="left")
    if not frame.index.equals(expected):
        raise Session4DataError("frame has gaps, duplicates, or wrong boundaries")
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise Session4DataError(f"frame missing columns: {missing}")
    numeric = frame[list(columns)].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise Session4DataError("frame contains non-finite values")
    if {"Open", "High", "Low", "Close"}.issubset(frame.columns):
        prices = frame[["Open", "High", "Low", "Close"]]
        if bool((prices <= 0).to_numpy().any()):
            raise Session4DataError("OHLC prices must be positive")
        if bool((frame["High"] < prices.max(axis=1)).any()):
            raise Session4DataError("OHLC High is inconsistent")
        if bool((frame["Low"] > prices.min(axis=1)).any()):
            raise Session4DataError("OHLC Low is inconsistent")
    if "Volume" in frame.columns and bool((frame["Volume"] < 0).any()):
        raise Session4DataError("volume must be non-negative")


def _hashable_record(record: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in record.items():
        if isinstance(value, datetime):
            result[key] = _iso(value)
        elif isinstance(value, float):
            result[key] = format(value, ".17g")
        else:
            result[key] = value
    return result


def _finite(value: str | None, field_name: str) -> float:
    try:
        parsed = float("" if value is None else value)
    except ValueError as exc:
        raise Session4DataError(f"invalid {field_name}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise Session4DataError(f"non-finite {field_name}")
    return parsed


def _decimal(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise Session4DataError(f"invalid decimal {field_name}: {value!r}") from exc
    if not parsed.is_finite():
        raise Session4DataError(f"non-finite decimal {field_name}")
    return parsed


def _parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise Session4DataError(f"invalid {field_name}: {value!r}") from exc
    _require_utc(parsed, field_name)
    return parsed.astimezone(UTC)


def _expected_rows(start: datetime, end: datetime, interval: timedelta) -> int:
    if end <= start or (end - start) % interval:
        raise Session4DataError("window does not contain a whole number of intervals")
    return int((end - start) / interval)


def _close_ns(open_time: datetime, interval: timedelta) -> int:
    return _to_ns(open_time + interval) - MILLISECOND_NS


def _to_ns(value: datetime) -> int:
    _require_utc(value, "timestamp")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (
        delta.days * 86_400_000_000_000 + delta.seconds * 1_000_000_000 + delta.microseconds * 1_000
    )


def _to_ns_delta(value: timedelta) -> int:
    return int(value.total_seconds() * 1_000_000_000)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise Session4DataError(f"{field_name} must be timezone-aware UTC")


def _iso(value: datetime) -> str:
    _require_utc(value, "datetime")
    return value.isoformat().replace("+00:00", "Z")


def _require_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise Session4DataError(f"{field_name} must be lowercase SHA-256")


def runtime_versions() -> dict[str, str]:
    return {
        "nautilus_trader": nautilus_version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "talib": talib.__version__,
    }
