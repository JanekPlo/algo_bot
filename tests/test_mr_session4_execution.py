from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pandas as pd
import pytest

from algo_bot.engine.mr_session4_contract import build_run_matrix
from algo_bot.engine.mr_session4_execution import (
    SESSION4_INVARIANT_CODES,
    CausalIsolatedMarginMonitor,
    Session4BoundaryController,
    Session4InvariantError,
    _build_metric_values,
    _development_exit_policy,
    _expected_funding_amounts,
    _expected_funding_settlements,
    _liquidation_metric_values,
    _reconcile_native_fills,
    _unique_domain_fills,
)
from algo_bot.microstructure import MaintenanceMarginTier, MarkPriceContext
from algo_bot.strategies.mastermind.model import (
    CloseReason,
    OrderFilled,
    OrderRole,
    PositionChanged,
    PositionClosed,
    Side,
)


def _domain_fill() -> OrderFilled:
    return OrderFilled(
        event_id="fill-event",
        strategy_id="strategy",
        instrument_id="BTCUSDT-PERP.BYBIT",
        occurred_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
        source="native",
        source_sequence=1,
        setup_id="setup-1",
        client_order_id="order-1",
        execution_id="trade-1",
        role=OrderRole.BASE_ENTRY,
        last_quantity=Decimal("2"),
        cumulative_quantity=Decimal("2"),
        price=Decimal("100"),
        commission=Decimal("0.11"),
        benchmark_price=Decimal("99.9"),
    )


def test_invariant_contract_has_30_unique_ordered_codes() -> None:
    assert len(SESSION4_INVARIANT_CODES) == 30
    assert len(set(SESSION4_INVARIANT_CODES)) == 30


def test_native_fill_reconciliation_uses_trade_id_qty_price_and_commission() -> None:
    native = pd.DataFrame.from_records(
        [
            {
                "trade_id": "trade-1",
                "last_qty": "2",
                "last_px": "100",
                "commission": "0.11 USDT",
                "liquidity_side": "TAKER",
            }
        ]
    )
    _reconcile_native_fills(
        (_domain_fill(),),
        native,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.00055"),
    )
    native.loc[0, "last_px"] = "100.1"
    with pytest.raises(Session4InvariantError, match="fill mismatch"):
        _reconcile_native_fills(
            (_domain_fill(),),
            native,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.00055"),
        )


def test_native_fill_reconciliation_rejects_wrong_commission_currency() -> None:
    native = pd.DataFrame.from_records(
        [
            {
                "trade_id": "trade-1",
                "last_qty": "2",
                "last_px": "100",
                "commission": "0.11 BTC",
                "liquidity_side": "TAKER",
            }
        ]
    )

    with pytest.raises(Session4InvariantError, match="commission currency mismatch"):
        _reconcile_native_fills(
            (_domain_fill(),),
            native,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.00055"),
        )


def test_native_fill_reconciliation_rejects_self_consistent_wrong_fee() -> None:
    tampered_fill = replace(_domain_fill(), commission=Decimal("0.12"))
    native = pd.DataFrame.from_records(
        [
            {
                "trade_id": "trade-1",
                "last_qty": "2",
                "last_px": "100",
                "commission": "0.12 USDT",
                "liquidity_side": "TAKER",
            }
        ]
    )

    with pytest.raises(Session4InvariantError, match="commission algebra mismatch"):
        _reconcile_native_fills(
            (tampered_fill,),
            native,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.00055"),
        )


def test_native_fill_reconciliation_uses_frozen_maker_rate() -> None:
    maker_fill = replace(_domain_fill(), commission=Decimal("0.04"))
    native = pd.DataFrame.from_records(
        [
            {
                "trade_id": "trade-1",
                "last_qty": "2",
                "last_px": "100",
                "commission": "0.04000000 USDT",
                "liquidity_side": "MAKER",
            }
        ]
    )

    _reconcile_native_fills(
        (maker_fill,),
        native,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.00055"),
    )


def test_native_fill_reconciliation_matches_nautilus_half_even_money_rounding() -> None:
    fill = replace(
        _domain_fill(),
        last_quantity=Decimal("5.15"),
        cumulative_quantity=Decimal("5.15"),
        price=Decimal("2008.53"),
        commission=Decimal("5.68916122"),
    )
    native = pd.DataFrame.from_records(
        [
            {
                "trade_id": "trade-1",
                "last_qty": "5.15",
                "last_px": "2008.53",
                "commission": "5.68916122 USDT",
                "liquidity_side": "TAKER",
            }
        ]
    )

    _reconcile_native_fills(
        (fill,),
        native,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.00055"),
    )

    half_up_fill = replace(fill, commission=Decimal("5.68916123"))
    native.loc[0, "commission"] = "5.68916123 USDT"
    with pytest.raises(Session4InvariantError, match="commission algebra mismatch"):
        _reconcile_native_fills(
            (half_up_fill,),
            native,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.00055"),
        )


def test_duplicate_domain_trade_id_is_never_silently_deduplicated() -> None:
    fill = _domain_fill()
    with pytest.raises(Session4InvariantError, match="duplicate domain execution ID"):
        _unique_domain_fills((fill, fill))


def test_funding_oracle_requires_every_settlement_inside_setup_interval() -> None:
    opened = datetime(2025, 1, 1, 1, tzinfo=UTC)
    fill = _domain_fill()
    fill = replace(fill, occurred_at_utc=opened)
    closed = PositionClosed(
        event_id="closed",
        strategy_id="strategy",
        instrument_id="BTCUSDT-PERP.BYBIT",
        occurred_at_utc=datetime(2025, 1, 1, 10, tzinfo=UTC),
        source="native",
        source_sequence=2,
        setup_id="setup-1",
        close_reason=CloseReason.TP,
        realized_price_pnl=Decimal("1"),
        commissions=Decimal("0.2"),
        funding=Decimal("0.01"),
        realized_slippage_cost=Decimal("0.2"),
    )
    updates = tuple(
        SimpleNamespace(next_funding_ns=int(pd.Timestamp(timestamp).value))
        for timestamp in (
            datetime(2025, 1, 1, 0, tzinfo=UTC),
            datetime(2025, 1, 1, 8, tzinfo=UTC),
            datetime(2025, 1, 1, 16, tzinfo=UTC),
        )
    )
    data = SimpleNamespace(funding_updates=updates)
    assert _expected_funding_settlements(cast(Any, data), (fill, closed)) == [
        int(pd.Timestamp("2025-01-01T08:00:00Z").value)
    ]


@pytest.mark.parametrize(
    ("signed_quantity", "rate", "expected"),
    [
        (Decimal("2"), Decimal("0.001"), Decimal("-0.24690000")),
        (Decimal("-2"), Decimal("0.001"), Decimal("0.24690000")),
        (Decimal("2"), Decimal("-0.001"), Decimal("0.24690000")),
    ],
)
def test_funding_amount_oracle_uses_signed_quantity_rate_and_completed_mark(
    signed_quantity: Decimal,
    rate: Decimal,
    expected: Decimal,
) -> None:
    opened = datetime(2025, 1, 1, 1, tzinfo=UTC)
    fill = replace(_domain_fill(), occurred_at_utc=opened)
    changed = PositionChanged(
        event_id="position-open",
        strategy_id="strategy",
        instrument_id="BTCUSDT-PERP.BYBIT",
        occurred_at_utc=opened,
        source="native",
        source_sequence=2,
        setup_id="setup-1",
        signed_quantity=signed_quantity,
        average_price=Decimal("100"),
    )
    closed = PositionClosed(
        event_id="closed",
        strategy_id="strategy",
        instrument_id="BTCUSDT-PERP.BYBIT",
        occurred_at_utc=datetime(2025, 1, 1, 10, tzinfo=UTC),
        source="native",
        source_sequence=3,
        setup_id="setup-1",
        close_reason=CloseReason.TP,
        realized_price_pnl=Decimal(0),
        commissions=Decimal(0),
        funding=expected,
        realized_slippage_cost=Decimal(0),
    )
    settlement = pd.Timestamp("2025-01-01T08:00:00Z")
    mark_open = pd.Timestamp("2025-01-01T07:00:00Z")
    data = SimpleNamespace(
        funding_updates=(SimpleNamespace(next_funding_ns=int(settlement.value)),),
        funding=pd.DataFrame(
            {"funding_rate": [rate]},
            index=pd.DatetimeIndex([settlement], name="datetime"),
        ),
        mark_context=SimpleNamespace(
            bars=pd.DataFrame(
                {"Close": [123.45]},
                index=pd.DatetimeIndex([mark_open], name="datetime"),
            )
        ),
    )

    assert _expected_funding_amounts(cast(Any, data), (fill, changed, closed)) == [
        (int(settlement.value), expected)
    ]


def _monitor(mark_low: float) -> tuple[CausalIsolatedMarginMonitor, Any]:
    open_time = pd.Timestamp("2025-01-01T00:00:00Z")
    marks = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [110.0],
            "Low": [mark_low],
            "Close": [90.0],
        },
        index=pd.DatetimeIndex([open_time], name="datetime"),
    )
    context = MarkPriceContext(
        symbol="BTCUSDT",
        exchange="bybit",
        timeframe="1h",
        bars=marks,
        source="mark-fixture",
        maintenance_margin_tiers=(MaintenanceMarginTier(1_000.0, 0.005, 0.0),),
        taker_fee_rate=0.00055,
    )
    spec = build_run_matrix()[0].machine_config
    setup = SimpleNamespace(
        setup_id="setup-1",
        side=Side.LONG,
        setup_start_equity=Decimal("100"),
    )
    machine = SimpleNamespace(
        config=spec,
        state=SimpleNamespace(
            setup=setup,
            real_open_quantity=Decimal("2"),
            real_average_price=Decimal("100"),
        ),
    )
    data = SimpleNamespace(mark_context=context)
    monitor = CausalIsolatedMarginMonitor(cast(Any, machine), cast(Any, data))
    inclusive_close_ns = int((open_time + pd.Timedelta(hours=1)).value) - 1_000_000
    bar = SimpleNamespace(ts_init=inclusive_close_ns)
    return monitor, bar


def test_margin_monitor_maps_equal_inclusive_close_without_one_hour_lag() -> None:
    monitor, bar = _monitor(mark_low=50.0)
    events = monitor.before_bar(bar)
    assert monitor.mark_bars_observed == 1
    assert monitor.positioned_mark_bars_checked == 1
    assert len(monitor.liquidation_events) == 1
    assert len(events) == 1
    assert events[0].event_id.startswith("mr-s4-liquidation")
    assert monitor.liquidation_events[0].observed_at == pd.Timestamp("2025-01-01T00:59:59.999Z")
    assert len(monitor.liquidation_evidence) == 1
    evidence = monitor.liquidation_evidence[0]
    assert evidence["quantity"] == "2"
    assert evidence["average_entry_price"] == "100"
    assert evidence["setup_start_equity"] == "100"
    assert evidence["gross_entry_notional"] == "200"
    assert evidence["effective_leverage"] == "2"
    assert evidence["adverse_mark_field"] == "Low"
    assert evidence["adverse_mark"] == 50.0


def test_margin_monitor_handcomputed_non_crossing() -> None:
    # qty=2, entry=100, setup equity=100 -> effective leverage=2;
    # long LP ~= 50.25, więc Low=60 nie przecina progu.
    monitor, bar = _monitor(mark_low=60.0)
    assert monitor.before_bar(bar) == ()
    assert monitor.liquidation_events == []


def test_margin_monitor_catches_position_opened_and_closed_inside_h1() -> None:
    monitor, bar = _monitor(mark_low=50.0)
    monitor.observe_transition(
        PositionChanged(
            event_id="position-open",
            strategy_id="strategy",
            instrument_id="BTCUSDT-PERP.BYBIT",
            occurred_at_utc=datetime(2025, 1, 1, 0, 30, tzinfo=UTC),
            source="fixture",
            source_sequence=1,
            setup_id="setup-1",
            signed_quantity=Decimal("2"),
            average_price=Decimal("100"),
        )
    )
    monitor.machine.state.setup = None
    monitor.machine.state.real_open_quantity = Decimal(0)
    monitor.machine.state.real_average_price = None

    # Nie ma już czego technicznie zamknąć, ale crossing pozostaje negatywnym
    # wynikiem evidence zamiast zniknąć z causalnego skanu.
    assert monitor.before_bar(bar) == ()
    assert len(monitor.liquidation_events) == 1


def test_final_exposure_hour_liquidation_keeps_single_manual_close_outcome() -> None:
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2025-01-01T00:00:00Z"),
            pd.Timestamp("2025-01-01T01:00:00Z"),
        ],
        name="datetime",
    )
    marks = pd.DataFrame(
        {
            "Open": [100.0, 100.0],
            "High": [101.0, 101.0],
            "Low": [60.0, 50.0],
            "Close": [100.0, 90.0],
        },
        index=index,
    )
    context = MarkPriceContext(
        symbol="BTCUSDT",
        exchange="bybit",
        timeframe="1h",
        bars=marks,
        source="mark-fixture",
        maintenance_margin_tiers=(MaintenanceMarginTier(1_000.0, 0.005, 0.0),),
        taker_fee_rate=0.00055,
    )
    config = build_run_matrix()[0].machine_config
    setup = SimpleNamespace(
        setup_id="setup-1",
        side=Side.LONG,
        setup_start_equity=Decimal("100"),
    )
    state = SimpleNamespace(
        setup=setup,
        real_open_quantity=Decimal("2"),
        real_average_price=Decimal("100"),
    )
    machine = SimpleNamespace(config=config, state=state)
    monitor = CausalIsolatedMarginMonitor(
        cast(Any, machine),
        cast(Any, SimpleNamespace(mark_context=context)),
    )
    cutoff_close_ns = int(pd.Timestamp("2025-01-01T00:59:59.999Z").value)
    final_close_ns = int(pd.Timestamp("2025-01-01T01:59:59.999Z").value)
    boundary = Session4BoundaryController(
        cast(Any, machine),
        monitor,
        cutoff_close_ns=cutoff_close_ns,
        final_close_ns=final_close_ns,
    )

    cutoff_events = boundary.before_bar(SimpleNamespace(ts_init=cutoff_close_ns))
    assert len(cutoff_events) == 1
    assert boundary.manual_cutoff_count == 1
    assert monitor.liquidation_events == []

    # Zaplanowany manualny close filluje na finalnym close. Carried snapshot
    # nadal prawidłowo reprezentuje ekspozycję przez całą ostatnią godzinę.
    state.setup = None
    state.real_open_quantity = Decimal(0)
    state.real_average_price = None
    assert boundary.before_bar(SimpleNamespace(ts_init=final_close_ns)) == ()
    assert len(monitor.liquidation_events) == 1
    assert boundary.manual_cutoff_count == 1
    assert _development_exit_policy(margin=monitor, boundary=boundary) == (1, 1)


def test_metric_drawdown_uses_fraction_and_separate_display_percent() -> None:
    index = pd.date_range(datetime(2025, 1, 1, tzinfo=UTC), periods=3, freq="h")
    equity = pd.DataFrame({"equity": [100.0, 110.0, 88.0]}, index=index)
    trades = pd.DataFrame({"setup_net_pnl": [10.0, -22.0]})
    observed = _build_metric_values(equity, trades)
    assert observed["max_drawdown_fraction"] == pytest.approx(-0.20)
    assert observed["max_drawdown_display_pct"] == pytest.approx(-20.0)
    assert observed["periods_per_year"] == 8_760.0


def test_metric_warnings_cannot_leak_outcomes_to_worker_stderr() -> None:
    class PoisonHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise AssertionError(f"outcome log leaked: {record.getMessage()}")

    logger = logging.getLogger("algo_bot.metrics")
    handler = PoisonHandler()
    logger.addHandler(handler)
    logger.disabled = False
    try:
        index = pd.date_range(datetime(2025, 1, 1, tzinfo=UTC), periods=2, freq="h")
        equity = pd.DataFrame({"equity": [100.0, 100.0]}, index=index)
        observed = _build_metric_values(equity, pd.DataFrame())
    finally:
        logger.removeHandler(handler)
    assert observed["n_trades"] == 0
    assert logger.disabled is False


def test_liquidation_metrics_are_explicitly_non_interpretable() -> None:
    observed = _liquidation_metric_values()
    economic_fields = set(observed) - {"periods_per_year"}
    assert economic_fields
    assert all(observed[field] is None for field in economic_fields)
