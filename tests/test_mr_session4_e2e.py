from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
import pytest

from algo_bot.engine.backtest_result import FillMethod, MarginMethod, SourceTreeState
from algo_bot.engine.mr_session4_contract import build_run_matrix
from algo_bot.engine.mr_session4_data import (
    DEVELOPMENT_START,
    H1,
    M5,
    M10,
    SYMBOL_SPECS,
    WARMUP_BARS,
    WARMUP_START,
    Session4DataBundle,
    Session4DataMetadata,
    aggregate_m5_to_m10,
    build_bybit_perpetual,
    build_native_bars,
    build_native_mark_price_updates,
)
from algo_bot.engine.mr_session4_execution import (
    SESSION4_INVARIANT_CODES,
    run_session4_spec,
)
from algo_bot.microstructure import MaintenanceMarginTier, MarkPriceContext

_ZERO_HASH = "0" * 64
_DEVELOPMENT_HOURS = 6


class _OutcomeLeakHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise AssertionError(f"outcome log leaked: {record.getMessage()}")


def _synthetic_bundle() -> Session4DataBundle:
    spec = SYMBOL_SPECS["BTCUSDT"]
    h1_index = pd.date_range(
        WARMUP_START,
        periods=WARMUP_BARS + _DEVELOPMENT_HOURS,
        freq="h",
    )
    closes = [100.0 + ((position % 20) - 10) / 10 for position in range(len(h1_index))]
    h1_with_warmup = pd.DataFrame(
        {
            "Open": closes,
            "High": [price + 1.0 for price in closes],
            "Low": [price - 1.0 for price in closes],
            "Close": closes,
            "Volume": [100.0] * len(h1_index),
        },
        index=h1_index,
    )
    h1_with_warmup.index.name = "datetime"
    development_h1 = h1_with_warmup.loc[h1_with_warmup.index >= DEVELOPMENT_START].copy(deep=True)

    development_end = DEVELOPMENT_START + timedelta(hours=_DEVELOPMENT_HOURS)
    m5_index = pd.date_range(
        DEVELOPMENT_START,
        development_end,
        freq="5min",
        inclusive="left",
    )
    development_m5 = pd.DataFrame(
        {
            "Open": [100.0] * len(m5_index),
            "High": [100.1] * len(m5_index),
            "Low": [99.9] * len(m5_index),
            "Close": [100.0] * len(m5_index),
            "Volume": [10.0] * len(m5_index),
        },
        index=m5_index,
    )
    development_m5.index.name = "datetime"
    development_m10 = aggregate_m5_to_m10(
        development_m5,
        start=DEVELOPMENT_START,
        end=development_end,
    )

    mark_index = pd.date_range(
        DEVELOPMENT_START - H1,
        development_end,
        freq="h",
        inclusive="left",
    )
    mark_closes = [100.05 + position / 100 for position in range(len(mark_index))]
    mark_with_warmup = pd.DataFrame(
        {
            "Open": mark_closes,
            "High": [price + 1.0 for price in mark_closes],
            "Low": [price - 1.0 for price in mark_closes],
            "Close": mark_closes,
        },
        index=mark_index,
    )
    mark_with_warmup.index.name = "datetime"
    development_mark = mark_with_warmup.loc[mark_with_warmup.index >= DEVELOPMENT_START].copy(
        deep=True
    )

    instrument, h1_bar_type, m5_bar_type, m10_bar_type = build_bybit_perpetual(spec)
    h1_bars = build_native_bars(development_h1, instrument, h1_bar_type, H1)
    m5_bars = build_native_bars(development_m5, instrument, m5_bar_type, M5)
    m10_bars = build_native_bars(development_m10, instrument, m10_bar_type, M10)
    mark_price_updates, mark_updates_hash = build_native_mark_price_updates(
        mark_with_warmup,
        instrument,
        start=DEVELOPMENT_START - H1,
        end=development_end,
    )
    native_data = tuple(
        sorted(
            (*mark_price_updates, *h1_bars),
            key=lambda item: int(item.ts_init),
        )
    )
    mark_context = MarkPriceContext(
        symbol=spec.symbol,
        exchange="bybit",
        timeframe="1h",
        bars=mark_with_warmup,
        source="synthetic://mr-session-4-e2e#one-predevelopment-h1+development",
        maintenance_margin_tiers=(MaintenanceMarginTier(1_000_000.0, 0.005, 0.0),),
        taker_fee_rate=float(spec.taker_fee),
    )
    metadata = Session4DataMetadata(
        symbol=spec.symbol,
        warmup_start_utc=WARMUP_START.isoformat(),
        development_start_utc=DEVELOPMENT_START.isoformat(),
        holdout_start_utc=(DEVELOPMENT_START + timedelta(days=1)).isoformat(),
        source_hashes={
            "h1_with_warmup": _ZERO_HASH,
            "m5": _ZERO_HASH,
            "funding": _ZERO_HASH,
            "mark_h1": _ZERO_HASH,
            "mark_h1_predevelopment_warmup": _ZERO_HASH,
        },
        source_rows={
            "h1_with_warmup": len(h1_with_warmup),
            "development_h1": len(development_h1),
            "m5": len(development_m5),
            "m10_derived": len(development_m10),
            "funding": 0,
            "mark_h1": len(development_mark),
            "mark_h1_predevelopment_warmup": 1,
            "native_mark_updates": len(mark_price_updates),
        },
        m10_derived_hash=_ZERO_HASH,
        native_mark_updates_hash=mark_updates_hash,
        h1_m5_alignment={"fixture": True},
        data_hash=_ZERO_HASH,
        risk_tiers_hash=_ZERO_HASH,
    )
    return Session4DataBundle(
        spec=spec,
        h1_with_warmup=h1_with_warmup,
        development_h1=development_h1,
        development_m5=development_m5,
        development_m10=development_m10,
        funding=pd.DataFrame(),
        mark_context=mark_context,
        instrument=instrument,
        h1_bar_type=h1_bar_type,
        m5_bar_type=m5_bar_type,
        m10_bar_type=m10_bar_type,
        h1_bars=h1_bars,
        m5_bars=m5_bars,
        m10_bars=m10_bars,
        funding_updates=(),
        mark_price_updates=mark_price_updates,
        native_data=native_data,
        metadata=metadata,
    )


def test_session4_synthetic_full_path_is_flat_auditable_and_outcome_blind(
    capsys: pytest.CaptureFixture[str],
) -> None:
    metric_logger = logging.getLogger("algo_bot.metrics")
    leak_handler = _OutcomeLeakHandler()
    was_disabled = metric_logger.disabled
    metric_logger.disabled = False
    metric_logger.addHandler(leak_handler)
    try:
        artifact = run_session4_spec(
            build_run_matrix()[0],
            _synthetic_bundle(),
            source_tree=SourceTreeState("synthetic-e2e", False, _ZERO_HASH),
        )
    finally:
        metric_logger.removeHandler(leak_handler)
        metric_logger.disabled = was_disabled

    observed_codes = tuple(str(check["code"]) for check in artifact.invariant_ledger)
    assert observed_codes == SESSION4_INVARIANT_CODES
    assert len(observed_codes) == 30
    assert all(check["passed"] is True for check in artifact.invariant_ledger)
    assert artifact.result.fill_method is FillMethod.NAUTILUS_NATIVE_BAR
    assert artifact.result.margin_method is MarginMethod.MARK_PRICE_ISOLATED
    assert artifact.result.stats["final_state"] == {
        "active_domain_orders": 0,
        "order_lifecycle": "NONE",
        "outbox_size": 0,
        "position_build": "FLAT",
        "real_open_quantity": "0",
        "risk_mode": "FULL",
    }
    assert artifact.result.stats["manual_cutoff_count"] == 1
    assert artifact.result.stats["liquidation_event_count"] == 0
    assert len(artifact.result.equity) == _DEVELOPMENT_HOURS
    assert capsys.readouterr() == ("", "")
