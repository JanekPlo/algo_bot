"""Development-only P9 data tests; no strategy or metric execution occurs here."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.mms_beta_data import (
    BAR_TYPE,
    DATA_SCHEMA_VERSION,
    DEFAULT_FUNDING_PATH,
    DEFAULT_OHLCV_PATH,
    DEVELOPMENT_BARS,
    DEVELOPMENT_FUNDING_ROWS,
    DEVELOPMENT_START,
    FEATURE_MODEL_ID,
    FUNDING_INTERVAL,
    FUNDING_PROFILE_ID,
    FUNDING_UPDATE_LEAD_NS,
    H1,
    HOLDOUT_START,
    HOUR_NS,
    INPUT_OHLCV_ROWS,
    MILLISECOND_NS,
    TIMESTAMP_PROFILE_ID,
    WARMUP_BARS,
    WARMUP_START,
    MmsBetaDataError,
    build_bar_feature_source,
    load_mms_beta_development_data,
    stream_development_funding,
    stream_development_ohlcv,
    validate_funding_boundaries,
    validate_hourly_ohlcv,
)

EXPECTED_ACTUAL_HASHES = {
    "ohlcv": "5acd3750ba0e63cff67e1c06bbbc995b30780f5fc754d5814d46c5f763d31e68",
    "funding": "55bf51bfb8adacfa787bf3b0dd506c7b7e47a1669a144e83fdbeaa320fb25c63",
    "features": "5da38d5e3d97e205dc52b4b6a6aaac28bff2c836772a667dae75df441ec945b4",
    "data": "3f7f1aa135e9aeb3fc95e1eabe9a1379093335e4db132173b90466adeffbf67e",
    "config": "bd39136efbd364d07e2debe6c3208c96e40a5031ed1d88b3fbee35eab396525c",
}


def _milliseconds(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1_000)


def _nanoseconds(timestamp: datetime) -> int:
    return _milliseconds(timestamp) * 1_000_000


def _write_synthetic_ohlcv(path: Path, *, holdout_tail: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["datetime", "Open", "High", "Low", "Close", "Volume", "ts"])
        for ordinal in range(INPUT_OHLCV_ROWS):
            timestamp = WARMUP_START + ordinal * H1
            price = Decimal("100.0") + Decimal(ordinal % 100) / Decimal(10)
            writer.writerow(
                [
                    timestamp.isoformat(),
                    str(price),
                    str(price + Decimal("1.0")),
                    str(price - Decimal("1.0")),
                    str(price + Decimal("0.1")),
                    "10.000",
                    _milliseconds(timestamp),
                ]
            )
        handle.write(holdout_tail)


def _write_synthetic_funding(path: Path, *, holdout_tail: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["datetime", "funding_rate"])
        for ordinal in range(DEVELOPMENT_FUNDING_ROWS):
            jitter = timedelta(milliseconds=ordinal % 17)
            timestamp = DEVELOPMENT_START + ordinal * FUNDING_INTERVAL + jitter
            writer.writerow([timestamp.isoformat(), "0.0001"])
        handle.write(holdout_tail)


def test_streaming_readers_never_request_or_hash_holdout_tail(tmp_path: Path) -> None:
    ohlcv_a = tmp_path / "ohlcv_a.csv"
    ohlcv_b = tmp_path / "ohlcv_b.csv"
    funding_a = tmp_path / "funding_a.csv"
    funding_b = tmp_path / "funding_b.csv"
    _write_synthetic_ohlcv(ohlcv_a, holdout_tail="THIS HOLDOUT ROW IS NOT CSV\n")
    _write_synthetic_ohlcv(ohlcv_b, holdout_tail="DIFFERENT,UNPARSEABLE,HOLDOUT\n")
    _write_synthetic_funding(funding_a, holdout_tail="INVALID HOLDOUT FUNDING\n")
    _write_synthetic_funding(funding_b, holdout_tail="ANOTHER,INVALID,TAIL\n")

    loaded_ohlcv_a = stream_development_ohlcv(ohlcv_a)
    loaded_ohlcv_b = stream_development_ohlcv(ohlcv_b)
    loaded_funding_a = stream_development_funding(funding_a)
    loaded_funding_b = stream_development_funding(funding_b)

    assert len(loaded_ohlcv_a.frame) == INPUT_OHLCV_ROWS
    assert loaded_ohlcv_a.frame.index[-1] == HOLDOUT_START - H1
    assert loaded_ohlcv_a.sha256 == loaded_ohlcv_b.sha256
    assert len(loaded_funding_a.frame) == DEVELOPMENT_FUNDING_ROWS
    assert loaded_funding_a.sha256 == loaded_funding_b.sha256


def _small_ohlcv() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0],
            "High": [102.0, 103.0, 104.0, 105.0],
            "Low": [99.0, 100.0, 101.0, 102.0],
            "Close": [101.0, 102.0, 103.0, 104.0],
            "Volume": [10.0, 10.0, 10.0, 10.0],
            "ts": [_milliseconds(timestamp.to_pydatetime()) for timestamp in index],
        },
        index=index,
    )


def _validate_small(frame: pd.DataFrame) -> None:
    validate_hourly_ohlcv(
        frame,
        expected_start=datetime(2024, 1, 1, tzinfo=UTC),
        expected_end=datetime(2024, 1, 1, 4, tzinfo=UTC),
        expected_rows=4,
    )


def test_hourly_validation_accepts_canonical_frame() -> None:
    _validate_small(_small_ohlcv())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param("duplicate", "duplicate", id="duplicate"),
        pytest.param("gap", "gap", id="gap"),
        pytest.param("nan", "NaN", id="nan"),
        pytest.param("bad_high", "High", id="bad-high"),
        pytest.param("off_tick", "0.1 instrument tick", id="off-tick"),
        pytest.param("bad_ts", "bar-open", id="bad-ts"),
        pytest.param("naive", "timezone-aware UTC", id="naive"),
    ],
)
def test_hourly_validation_rejects_invalid_data(mutation: str, message: str) -> None:
    frame = _small_ohlcv()
    if mutation == "duplicate":
        frame.index = pd.DatetimeIndex(
            [frame.index[0], frame.index[1], frame.index[1], frame.index[3]]
        )
    elif mutation == "gap":
        frame.index = pd.DatetimeIndex(
            [frame.index[0], frame.index[1], frame.index[3], frame.index[3] + H1]
        )
    elif mutation == "nan":
        frame.iloc[1, frame.columns.get_loc("Close")] = np.nan
    elif mutation == "bad_high":
        frame.iloc[1, frame.columns.get_loc("High")] = 50.0
    elif mutation == "off_tick":
        frame.iloc[1, frame.columns.get_loc("Close")] = 102.05
    elif mutation == "bad_ts":
        frame.iloc[1, frame.columns.get_loc("ts")] += 1
    else:
        frame.index = frame.index.tz_localize(None)

    with pytest.raises(MmsBetaDataError, match=message):
        _validate_small(frame)


def _small_funding() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, 8, 0, 0, 1_000, tzinfo=UTC),
            datetime(2024, 1, 1, 16, 0, 0, 16_000, tzinfo=UTC),
        ]
    )
    return pd.DataFrame({"funding_rate": [0.0001, -0.0002, 0.0003]}, index=index)


def _validate_small_funding(frame: pd.DataFrame) -> None:
    validate_funding_boundaries(
        frame,
        expected_start=datetime(2024, 1, 1, tzinfo=UTC),
        expected_end=datetime(2024, 1, 2, tzinfo=UTC),
        expected_rows=3,
    )


def test_funding_validation_accepts_millisecond_jitter() -> None:
    _validate_small_funding(_small_funding())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param("duplicate", "duplicate", id="duplicate"),
        pytest.param("off_slot", "nominal 8h slot", id="off-slot"),
        pytest.param("nan", "non-finite", id="nan"),
        pytest.param("naive", "timezone-aware UTC", id="naive"),
    ],
)
def test_funding_validation_rejects_invalid_boundaries(mutation: str, message: str) -> None:
    frame = _small_funding()
    if mutation == "duplicate":
        frame.index = pd.DatetimeIndex([frame.index[0], frame.index[0], frame.index[2]])
    elif mutation == "off_slot":
        index = list(frame.index)
        index[1] = index[1] + timedelta(seconds=2)
        frame.index = pd.DatetimeIndex(index)
    elif mutation == "nan":
        frame.iloc[1, 0] = np.nan
    else:
        frame.index = frame.index.tz_localize(None)

    with pytest.raises(MmsBetaDataError, match=message):
        _validate_small_funding(frame)


def _feature_frame() -> pd.DataFrame:
    index = pd.date_range(WARMUP_START, periods=260, freq="1h", tz="UTC")
    phase = np.linspace(0.0, 8.0 * np.pi, len(index))
    close = 100.0 + np.linspace(0.0, 10.0, len(index)) + np.sin(phase) * 3.0
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 1.0,
            "Low": np.minimum(open_, close) - 1.0,
            "Close": close,
            "Volume": np.full(len(index), 100.0),
        },
        index=index,
    )


def test_future_perturbation_cannot_change_earlier_close_features() -> None:
    frame = _feature_frame()
    local_development_start = WARMUP_START + WARMUP_BARS * H1
    local_holdout_start = WARMUP_START + 260 * H1
    original, _ = build_bar_feature_source(
        frame,
        development_start=local_development_start,
        holdout_start=local_holdout_start,
    )
    perturbed_frame = frame.copy(deep=True)
    perturbed_frame.iloc[231:, perturbed_frame.columns.get_loc("High")] *= 3.0
    perturbed_frame.iloc[231:, perturbed_frame.columns.get_loc("Low")] *= 0.3
    perturbed_frame.iloc[231:, perturbed_frame.columns.get_loc("Close")] *= 2.0
    perturbed, _ = build_bar_feature_source(
        perturbed_frame,
        development_start=local_development_start,
        holdout_start=local_holdout_start,
    )
    decision_close_ns = _nanoseconds(WARMUP_START + 230 * H1) + HOUR_NS - MILLISECOND_NS

    earlier_keys = [key for key in original.close_timestamps_ns if key <= decision_close_ns]
    assert earlier_keys
    for key in earlier_keys:
        assert original.for_close_ns(key) == perturbed.for_close_ns(key)


@pytest.mark.integration
def test_actual_development_bundle_is_frozen_and_native_ready() -> None:
    if not DEFAULT_OHLCV_PATH.exists() or not DEFAULT_FUNDING_PATH.exists():
        pytest.skip("local BTCUSDT OHLCV/funding files are unavailable")

    bundle = load_mms_beta_development_data()
    metadata = bundle.metadata

    assert len(bundle.ohlcv_with_warmup) == INPUT_OHLCV_ROWS
    assert len(bundle.development_ohlcv) == DEVELOPMENT_BARS
    assert len(bundle.bars) == DEVELOPMENT_BARS
    assert len(bundle.funding_rates) == DEVELOPMENT_FUNDING_ROWS
    assert len(bundle.funding_updates) == DEVELOPMENT_FUNDING_ROWS
    assert len(bundle.native_data) == DEVELOPMENT_BARS + DEVELOPMENT_FUNDING_ROWS
    assert bundle.ohlcv_with_warmup.index[0] == WARMUP_START
    assert bundle.ohlcv_with_warmup.index[-1] == HOLDOUT_START - H1
    assert bundle.development_ohlcv.index[0] == DEVELOPMENT_START
    assert bundle.development_ohlcv.index[-1] == HOLDOUT_START - H1

    assert isinstance(bundle.instrument, nt.CryptoPerpetual)
    assert str(bundle.instrument.id) == "BTCUSDT-PERP.BINANCE"
    assert str(bundle.bar_type) == BAR_TYPE
    assert bundle.instrument.maker_fee == Decimal("0.0004")
    assert bundle.instrument.taker_fee == Decimal("0.0004")
    assert str(bundle.instrument.price_increment) == "0.1"
    assert str(bundle.instrument.size_increment) == "0.001"

    first_open_ns = _nanoseconds(DEVELOPMENT_START)
    first_close_ns = first_open_ns + HOUR_NS - MILLISECOND_NS
    last_close_ns = _nanoseconds(HOLDOUT_START - H1) + HOUR_NS - MILLISECOND_NS
    assert bundle.bars[0].ts_event == bundle.bars[0].ts_init == first_close_ns
    assert bundle.bars[-1].ts_event == bundle.bars[-1].ts_init == last_close_ns
    assert bundle.feature_source.close_timestamps_ns[0] == first_close_ns
    assert bundle.feature_source.close_timestamps_ns[-1] == last_close_ns
    assert bundle.feature_source(bundle.bars[0]).bb_upper.is_finite()
    assert all(int(item.ts_init) < _nanoseconds(HOLDOUT_START) for item in bundle.native_data)

    first_funding = bundle.funding_updates[0]
    assert isinstance(first_funding, nt.FundingRateUpdate)
    assert first_funding.next_funding_ns - first_funding.ts_init == FUNDING_UPDATE_LEAD_NS
    assert first_funding.interval == 28_800

    assert metadata.schema_version == DATA_SCHEMA_VERSION
    assert metadata.warmup_bars == WARMUP_BARS
    assert metadata.feature_model == FEATURE_MODEL_ID
    assert metadata.timestamp_profile == TIMESTAMP_PROFILE_ID
    assert metadata.funding_profile == FUNDING_PROFILE_ID
    assert metadata.holdout_rows_read == 0
    assert metadata.ohlcv_hash == EXPECTED_ACTUAL_HASHES["ohlcv"]
    assert metadata.funding_hash == EXPECTED_ACTUAL_HASHES["funding"]
    assert metadata.features_hash == EXPECTED_ACTUAL_HASHES["features"]
    assert metadata.data_hash == EXPECTED_ACTUAL_HASHES["data"]
    assert metadata.config_hash == EXPECTED_ACTUAL_HASHES["config"]


def test_actual_hash_constants_are_lowercase_sha256() -> None:
    for digest in EXPECTED_ACTUAL_HASHES.values():
        assert len(digest) == 64
        assert digest == digest.lower()
        int(digest, 16)
