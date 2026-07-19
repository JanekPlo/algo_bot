from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from algo_bot.engine.mr_session4_data import (
    H1,
    SYMBOL_SPECS,
    Session4DataError,
    aggregate_m5_to_h1,
    aggregate_m5_to_m10,
    build_bybit_perpetual,
    build_funding_updates,
    build_h1_m5_alignment_report,
    build_native_bars,
    build_native_mark_price_updates,
    stream_ohlcv_window,
    validate_funding_mark_update_coverage,
    validate_native_bar_grid,
)


def _write_rows(path: Path, rows: list[str], tail: str) -> None:
    path.write_text(
        "datetime,Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n" + tail,
        encoding="utf-8",
    )


def test_stream_reader_never_requests_first_holdout_row(tmp_path: Path) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    path_a = tmp_path / "a.csv"
    path_b = tmp_path / "b.csv"
    rows = [
        f"{(start + timedelta(minutes=5 * index)).isoformat()},1,3,0.5,2,{index + 1}"
        for index in range(3)
    ]
    _write_rows(path_a, rows, "THIS HOLDOUT TAIL IS MALFORMED")
    _write_rows(path_b, rows, "DIFFERENT,HOLDOUT,BYTES,DO,NOT,MATTER")

    first = stream_ohlcv_window(path_a, start=start, end=end, interval=timedelta(minutes=5))
    second = stream_ohlcv_window(path_b, start=start, end=end, interval=timedelta(minutes=5))

    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert first.sha256 == second.sha256
    assert first.rows_read == 3
    assert first.holdout_rows_read == 0


def test_stream_reader_detects_development_change(tmp_path: Path) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=10)
    first_path = tmp_path / "first.csv"
    changed_path = tmp_path / "changed.csv"
    first_rows = [
        f"{start.isoformat()},1,3,0.5,2,1",
        f"{(start + timedelta(minutes=5)).isoformat()},2,4,1,3,2",
    ]
    changed_rows = [*first_rows]
    changed_rows[1] = f"{(start + timedelta(minutes=5)).isoformat()},2,5,1,3,2"
    _write_rows(first_path, first_rows, "poison")
    _write_rows(changed_path, changed_rows, "poison")

    first = stream_ohlcv_window(
        first_path,
        start=start,
        end=end,
        interval=timedelta(minutes=5),
    )
    changed = stream_ohlcv_window(
        changed_path,
        start=start,
        end=end,
        interval=timedelta(minutes=5),
    )
    assert first.sha256 != changed.sha256


def test_m10_aggregation_matches_handcomputed_oracle() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=20)
    index = pd.date_range(start, end, freq="5min", inclusive="left")
    frame = pd.DataFrame(
        {
            "Open": [10.0, 11.0, 20.0, 19.0],
            "High": [12.0, 14.0, 22.0, 21.0],
            "Low": [9.0, 10.0, 18.0, 17.0],
            "Close": [11.0, 13.0, 19.0, 18.0],
            "Volume": [1.0, 2.0, 3.0, 4.0],
        },
        index=index,
    )
    frame.index.name = "datetime"

    observed = aggregate_m5_to_m10(frame, start=start, end=end)
    expected = pd.DataFrame(
        {
            "Open": [10.0, 20.0],
            "High": [14.0, 22.0],
            "Low": [9.0, 17.0],
            "Close": [13.0, 18.0],
            "Volume": [3.0, 7.0],
        },
        index=pd.date_range(start, end, freq="10min", inclusive="left"),
    )
    expected.index.name = "datetime"
    pd.testing.assert_frame_equal(observed, expected)


def test_m10_rejects_incomplete_pair() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    index = pd.date_range(start, end, freq="5min", inclusive="left")
    frame = pd.DataFrame(
        {
            "Open": [1.0, 1.0, 1.0],
            "High": [2.0, 2.0, 2.0],
            "Low": [0.5, 0.5, 0.5],
            "Close": [1.0, 1.0, 1.0],
            "Volume": [1.0, 1.0, 1.0],
        },
        index=index,
    )
    frame.index.name = "datetime"
    with pytest.raises(Session4DataError, match=r"whole number|incomplete"):
        aggregate_m5_to_m10(frame, start=start, end=end)


def test_h1_m5_alignment_freezes_price_divergence_without_rewriting_feed() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)
    index = pd.date_range(start, end, freq="5min", inclusive="left")
    m5 = pd.DataFrame(
        {
            "Open": [100.0] * 24,
            "High": [102.0] * 24,
            "Low": [99.0] * 24,
            "Close": [101.0] * 24,
            "Volume": [1.0] * 24,
        },
        index=index,
    )
    m5.index.name = "datetime"
    h1 = aggregate_m5_to_h1(m5, start=start, end=end)
    h1.loc[h1.index[1], "Close"] = 101.2

    observed = build_h1_m5_alignment_report(
        h1,
        m5,
        tick_size=Decimal("0.1"),
        start=start,
        end=end,
    )

    assert observed["exact_price_mismatch_bar_count"] == 1
    assert observed["beyond_one_tick_price_bar_count"] == 1
    columns = observed["price_columns"]
    assert isinstance(columns, dict)
    assert isinstance(columns["Close"], dict)
    assert columns["Close"]["exact_mismatch_bars"] == 1
    assert observed["policy"] == "REPORT_ONLY_INDEPENDENT_FEEDS_NO_RECONCILIATION"
    assert observed["max_accepted_price_delta_bps"] == "25"


def test_h1_m5_alignment_rejects_price_divergence_above_frozen_boundary() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    index = pd.date_range(start, end, freq="5min", inclusive="left")
    m5 = pd.DataFrame(
        {
            "Open": [100.0] * 12,
            "High": [102.0] * 12,
            "Low": [99.0] * 12,
            "Close": [101.0] * 12,
            "Volume": [1.0] * 12,
        },
        index=index,
    )
    m5.index.name = "datetime"
    h1 = aggregate_m5_to_h1(m5, start=start, end=end)
    h1.loc[h1.index[0], "Close"] = 100.0

    with pytest.raises(Session4DataError, match="exceeds frozen 25 bps boundary"):
        build_h1_m5_alignment_report(
            h1,
            m5,
            tick_size=Decimal("0.1"),
            start=start,
            end=end,
        )


def test_native_bar_grid_rejects_price_that_engine_would_round() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.05],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.0],
            "Volume": [1.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2025-01-01T00:00:00Z")], name="datetime"),
    )
    with pytest.raises(Session4DataError, match="Open is off native increment"):
        validate_native_bar_grid(frame, SYMBOL_SPECS["BTCUSDT"], source_name="fixture")


def test_native_mark_updates_preserve_half_tick_source_precision() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)
    marks = pd.DataFrame(
        {
            "Open": [100.05, 100.82],
            "High": [101.05, 101.82],
            "Low": [99.05, 99.82],
            "Close": [100.05, 100.82],
        },
        index=pd.date_range(start, end, freq="h", inclusive="left", name="datetime"),
    )
    instrument, _h1_type, _m5_type, _m10_type = build_bybit_perpetual(SYMBOL_SPECS["BTCUSDT"])

    updates, update_hash = build_native_mark_price_updates(
        marks,
        instrument,
        start=start,
        end=end,
    )

    assert len(update_hash) == 64
    assert updates[0].value.as_decimal() == Decimal("100.05")
    assert updates[0].value.precision == 2
    assert updates[1].value.as_decimal() == Decimal("100.82")
    assert updates[1].value.precision == 2
    assert int(updates[0].ts_init) == int(pd.Timestamp(start + H1).value) - 1_000_000


def test_native_mark_update_precedes_h1_callback_on_equal_close_timestamp() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + H1
    marks = pd.DataFrame(
        {"Open": [100.05], "High": [101.05], "Low": [99.05], "Close": [100.82]},
        index=pd.DatetimeIndex([start], name="datetime"),
    )
    trades = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.0],
            "Volume": [1.0],
        },
        index=pd.DatetimeIndex([start], name="datetime"),
    )
    instrument, h1_type, _m5_type, _m10_type = build_bybit_perpetual(SYMBOL_SPECS["BTCUSDT"])
    mark_updates, _update_hash = build_native_mark_price_updates(
        marks,
        instrument,
        start=start,
        end=end,
    )
    h1_bars = build_native_bars(trades, instrument, h1_type, H1)

    native_data = tuple(sorted((*mark_updates, *h1_bars), key=lambda item: int(item.ts_init)))
    assert int(native_data[0].ts_init) == int(native_data[1].ts_init)
    assert native_data[0] is mark_updates[0]
    assert native_data[1] is h1_bars[0]


def test_first_development_funding_uses_completed_predevelopment_mark_h1() -> None:
    development_start = datetime(2025, 1, 1, tzinfo=UTC)
    mark_start = development_start - H1
    mark_end = development_start + H1
    marks = pd.DataFrame(
        {
            "Open": [100.05, 999.82],
            "High": [101.05, 1_000.82],
            "Low": [99.05, 998.82],
            "Close": [100.05, 999.82],
        },
        index=pd.date_range(mark_start, mark_end, freq="h", inclusive="left", name="datetime"),
    )
    funding = pd.DataFrame(
        {"funding_rate": [0.001]},
        index=pd.DatetimeIndex([development_start], name="datetime"),
    )
    instrument, _h1_type, _m5_type, _m10_type = build_bybit_perpetual(SYMBOL_SPECS["BTCUSDT"])
    mark_updates, _update_hash = build_native_mark_price_updates(
        marks,
        instrument,
        start=mark_start,
        end=mark_end,
    )
    funding_updates = build_funding_updates(funding, instrument)

    validate_funding_mark_update_coverage(mark_updates, funding_updates)
    settlement_ns = int(funding_updates[0].next_funding_ns)
    completed = next(
        update for update in mark_updates if int(update.ts_init) == settlement_ns - 1_000_000
    )
    assert completed.value.as_decimal() == Decimal("100.05")
    assert all(
        int(update.ts_init) < int(funding_updates[0].ts_init)
        for update in mark_updates
        if int(update.ts_init) <= settlement_ns - 1_000_000
    )
