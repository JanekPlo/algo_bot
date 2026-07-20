"""P7 integration gate for the PyO3 Mastermind smoke backend."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.nautilus_mastermind import (
    ELIGIBILITY,
    EVIDENCE_TIER,
    HOUR_NS,
    PYO3_RESEARCH_EXECUTION_PROFILE,
    PYO3_RESEARCH_POSITION_MODEL,
    PYO3_SMOKE_EXECUTION_PROFILE,
    PYO3_SMOKE_POSITION_MODEL,
    BarFeatures,
    NautilusMastermindStrategy,
    Pyo3RecoveryCheckpoint,
    Pyo3RecoveryCoverageGroup,
    Pyo3RecoveryExposureFill,
    Pyo3RecoveryOrderBinding,
    Pyo3ResearchMetadata,
    Pyo3SmokeMetadata,
    Pyo3SmokeProfileError,
    _native_signed_position_quantity,
    run_pyo3_mastermind_smoke,
)
from algo_bot.strategies.mastermind.model import (
    AccountEquityUpdated,
    AddonTriggerPolicy,
    BarClosed,
    CancelOrder,
    CloseAll,
    CloseReason,
    CloseRequested,
    DomainEvent,
    DomainIntent,
    FundingApplied,
    MarkingBarClosed,
    MastermindConfig,
    OrderFilled,
    OrderLifecycle,
    OrderPartiallyFilled,
    OrderRole,
    OrderStatus,
    PositionBuild,
    PositionChanged,
    PositionClosed,
    ReconciliationCompleted,
    RecoverySnapshotLoaded,
    RequestReconciliation,
    Side,
    SubmitAddonOrder,
    SubmitAddonStop,
    SubmitBaseOrder,
    SubmitBaseStop,
    SubmitTakeProfit,
)
from algo_bot.strategies.mastermind.state_machine import MastermindStateMachine

STRATEGY_ID = "MMS-PYO3-001"
INSTRUMENT = "BTCUSDT-PERP.BINANCE"
FIVE_MINUTES_NS = 300_000_000_000


@dataclass(frozen=True)
class _Transition:
    intents: tuple[DomainIntent, ...] = ()
    snapshot_json: str = "{}"


class _ScriptedMachine:
    """No-alpha pure port used to drive real PyO3 orders deterministically."""

    def __init__(
        self,
        *,
        base_quantity: Decimal = Decimal(1),
        addon: bool = False,
        take_profit: bool = True,
        close_on_bar: int | None = None,
        restore: bool = False,
        base_stop: Decimal = Decimal(90),
        addon_stop: Decimal = Decimal(95),
        target: Decimal = Decimal(110),
    ) -> None:
        self.base_quantity = base_quantity
        self.addon = addon
        self.take_profit = take_profit
        self.close_on_bar = close_on_bar
        self.restore = restore
        self.base_stop = base_stop
        self.addon_stop = addon_stop
        self.target = target
        self.events: list[DomainEvent] = []
        self.bar_count = 0
        self.base_intent_emitted = False
        self.protection_emitted = False
        self.addon_intent_emitted = False
        self.close_emitted = False

    @property
    def source_sequence_highwater(self) -> int:
        return 0

    def handle(self, event: DomainEvent) -> _Transition:
        self.events.append(event)
        intents: list[DomainIntent] = []
        if isinstance(event, ReconciliationCompleted) and self.restore:
            intents.append(self._base_order(event, client_order_id="RESTORE-BASE"))
            self.base_intent_emitted = True
        elif isinstance(event, BarClosed):
            self.bar_count += 1
            if not self.base_intent_emitted:
                intents.append(
                    self._base_order(
                        event,
                        client_order_id="RESTORE-BASE" if self.restore else "BASE-ENTRY",
                    )
                )
                self.base_intent_emitted = True
            if self.close_on_bar == self.bar_count and not self.close_emitted:
                intents.append(self._close_all(event))
                self.close_emitted = True
        elif isinstance(event, (OrderPartiallyFilled, OrderFilled)):
            if event.role is OrderRole.BASE_ENTRY:
                if not self.protection_emitted:
                    intents.extend(self._protection(event))
                    self.protection_emitted = True
                else:
                    # A replayed logical instruction must not create another group.
                    intents.append(self._base_stop_intent(event))
                if self.addon and isinstance(event, OrderFilled) and not self.addon_intent_emitted:
                    intents.append(self._addon_order(event))
                    self.addon_intent_emitted = True
            elif event.role is OrderRole.ADDON_ENTRY:
                intents.append(self._addon_stop_intent(event))
        return _Transition(tuple(intents))

    def _common(self, event: DomainEvent, name: str) -> dict[str, Any]:
        return {
            "intent_id": f"intent-{name}",
            "idempotency_key": f"idem-{name}",
            "strategy_id": STRATEGY_ID,
            "instrument_id": INSTRUMENT,
            "setup_id": "setup-1",
            "causation_id": event.event_id,
            "correlation_id": "setup-1",
        }

    def _base_order(self, event: DomainEvent, *, client_order_id: str) -> SubmitBaseOrder:
        return SubmitBaseOrder(
            **self._common(event, "base-entry"),
            client_order_id=client_order_id,
            side=Side.LONG,
            quantity=self.base_quantity,
            reference_price=Decimal(100),
            target_notional=Decimal(100) * self.base_quantity,
        )

    def _addon_order(self, event: DomainEvent) -> SubmitAddonOrder:
        return SubmitAddonOrder(
            **self._common(event, "addon-entry"),
            client_order_id="ADDON-ENTRY",
            side=Side.LONG,
            quantity=Decimal(1),
            reference_price=Decimal(100),
            target_notional=Decimal(100),
            trigger_id="addon-trigger",
            trigger_kind="CONFIRMING_CANDLE",
            structural_stop=self.addon_stop,
        )

    def _protection(self, event: DomainEvent) -> list[DomainIntent]:
        intents: list[DomainIntent] = [self._base_stop_intent(event)]
        if self.take_profit:
            intents.append(
                SubmitTakeProfit(
                    **self._common(event, "take-profit"),
                    client_order_id="TAKE-PROFIT",
                    side=Side.SHORT,
                    reference_quantity=event.cumulative_quantity,
                    trigger_price=self.target,
                    close_position=True,
                    reduce_only=False,
                )
            )
        return intents

    def _base_stop_intent(self, event: OrderPartiallyFilled | OrderFilled) -> SubmitBaseStop:
        return SubmitBaseStop(
            **self._common(event, "base-stop"),
            client_order_id="BASE-STOP",
            side=Side.SHORT,
            reference_quantity=event.cumulative_quantity,
            trigger_price=self.base_stop,
            close_position=True,
            reduce_only=False,
        )

    def _addon_stop_intent(self, event: OrderPartiallyFilled | OrderFilled) -> SubmitAddonStop:
        return SubmitAddonStop(
            **self._common(event, f"addon-stop-{event.execution_id}"),
            client_order_id=f"AS-{event.execution_id}",
            side=Side.SHORT,
            quantity=event.last_quantity,
            trigger_price=self.addon_stop,
            fill_execution_id=event.execution_id,
        )

    def _close_all(self, event: DomainEvent) -> CloseAll:
        return CloseAll(
            **self._common(event, "close-all"),
            client_order_id="CLOSE-ALL",
            side=Side.SHORT,
            quantity=self.base_quantity,
            close_reason=CloseReason.TP,
        )


@pytest.mark.parametrize(
    ("signed_qty", "quantity", "expected"),
    [
        (1.9999999999999996, "2.00", Decimal("2.00")),
        (-1.9999999999999996, "2.00", Decimal("-2.00")),
        (0.0, "0.00", Decimal(0)),
    ],
)
def test_native_signed_position_quantity_uses_fixed_point_magnitude(
    signed_qty: float,
    quantity: str,
    expected: Decimal,
) -> None:
    position = SimpleNamespace(
        signed_qty=signed_qty,
        quantity=nt.Quantity.from_str(quantity),
    )

    assert _native_signed_position_quantity(position) == expected


@pytest.mark.parametrize(
    ("signed_qty", "quantity"),
    [
        (float("nan"), "2.00"),
        (0.0, "2.00"),
        (1.0, "0.00"),
    ],
)
def test_native_signed_position_quantity_rejects_inconsistent_evidence(
    signed_qty: float,
    quantity: str,
) -> None:
    position = SimpleNamespace(
        signed_qty=signed_qty,
        quantity=nt.Quantity.from_str(quantity),
    )

    with pytest.raises(Pyo3SmokeProfileError, match=r"native .*quantity"):
        _native_signed_position_quantity(position)


def test_next_close_scheduler_installs_protection_at_fill_close() -> None:
    machine = _ScriptedMachine(take_profit=True)
    result = _run(
        machine,
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 85, 90),
        ],
    )

    orders = result.reports.orders
    entry = orders.loc[orders["client_order_id"] == "BASE-ENTRY"].iloc[0]
    stop = orders.loc[orders["type"] == "STOP_MARKET"].iloc[0]
    assert entry["ts_init"] == 2 * HOUR_NS
    assert stop["ts_init"] == entry["ts_init"]
    assert stop["is_reduce_only"]
    assert result.final_net_quantity == 0
    fills = result.reports.fills.sort_values("ts_event")
    assert list(fills["last_px"]) == ["100.0", "90.0"]


def test_close_position_param_is_ignored_by_pyo3_backtest_guard() -> None:
    """Prevent a future adapter from treating params as simulated Close-All."""

    instrument, bar_type = _instrument_fixture()

    class ClosePositionProbe(nt.Strategy):
        def __init__(self) -> None:
            super().__init__(
                nt.StrategyConfig(
                    strategy_id=nt.StrategyId.from_str("CLOSE-PROBE-001"),
                    oms_type=nt.OmsType.NETTING,
                )
            )
            self.entries = 0

        def on_start(self) -> None:
            self.subscribe_bars(bar_type)

        def on_bar(self, bar: Any) -> None:
            del bar
            if self.entries == 0:
                self.entries = 1
                self._entry()

        def on_order_filled(self, event: Any) -> None:
            if self.entries == 1:
                self.entries = 2
                self._entry()
            elif self.entries == 2:
                self.entries = 3
                stop = self.order_factory.stop_market(
                    instrument_id=instrument.id,
                    order_side=nt.OrderSide.SELL,
                    quantity=instrument.make_qty(Decimal(1)),
                    trigger_price=instrument.make_price(Decimal(90)),
                    trigger_type=nt.TriggerType.LAST_PRICE,
                    time_in_force=nt.TimeInForce.GTC,
                    reduce_only=False,
                )
                self.submit_order(stop, params={"close_position": True})
            del event

        def _entry(self) -> None:
            self.submit_order(
                self.order_factory.market(
                    instrument_id=instrument.id,
                    order_side=nt.OrderSide.BUY,
                    quantity=instrument.make_qty(Decimal(1)),
                    time_in_force=nt.TimeInForce.GTC,
                )
            )

    engine = _engine(instrument)
    engine.add_instrument(instrument)
    engine.add_data(
        _bars(
            instrument,
            bar_type,
            [(100, 101, 99, 100), (100, 101, 99, 100), (80, 85, 75, 82)],
        )
    )
    engine.add_strategy(ClosePositionProbe())
    try:
        engine.run()
        assert Decimal(str(engine.portfolio.net_position(instrument.id))) == Decimal(1)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "exit_bar",
    [
        pytest.param((100, 101, 80, 85), id="continuous"),
        pytest.param((80, 85, 75, 82), id="gap"),
    ],
)
def test_decomposed_base_stop_never_reverses_after_addon(exit_bar: tuple[int, ...]) -> None:
    machine = _ScriptedMachine(addon=True, take_profit=True)
    result = _run(
        machine,
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            exit_bar,
        ],
    )

    assert result.final_net_quantity == 0
    signed = [
        event.signed_quantity for event in machine.events if isinstance(event, PositionChanged)
    ]
    assert signed
    assert min(signed) >= 0
    exits = result.reports.orders.loc[result.reports.orders["type"].isin(["STOP_MARKET", "LIMIT"])]
    assert exits["is_reduce_only"].all()


def test_partial_entry_creates_one_stable_base_child_per_unique_fill() -> None:
    machine = _ScriptedMachine(base_quantity=Decimal(12), take_profit=False)
    result = _run(
        machine,
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 85, 90),
        ],
        fill_model=nt.LimitOrderPartialFillModel(random_seed=7),
    )

    base_fills = [
        event
        for event in machine.events
        if isinstance(event, (OrderPartiallyFilled, OrderFilled))
        and event.role is OrderRole.BASE_ENTRY
    ]
    assert [event.last_quantity for event in base_fills] == [Decimal(5), Decimal(7)]
    stops = result.reports.orders.loc[result.reports.orders["type"] == "STOP_MARKET"]
    assert len(stops) == 2
    assert set(stops["quantity"]) == {"5.000", "7.000"}
    assert stops["client_order_id"].is_unique
    assert result.final_net_quantity == 0


@pytest.mark.parametrize(
    ("mode", "expected_tp_fills"),
    [
        pytest.param("BASE", 1, id="base"),
        pytest.param("PYRAMIDED", 2, id="pyramided"),
        pytest.param("BASE_LOCKED", 1, id="base-locked"),
    ],
)
def test_take_profit_dynamic_coverage_and_cleanup(mode: str, expected_tp_fills: int) -> None:
    addon = mode != "BASE"
    bars = [(100, 101, 99, 100), (100, 101, 99, 100)]
    if addon:
        bars.append((100, 101, 99, 100))
    if mode == "BASE_LOCKED":
        bars.append((100, 101, 94, 96))
    bars.append((100, 115, 99, 110))
    result = _run(_ScriptedMachine(addon=addon, take_profit=True), bars)

    assert result.final_net_quantity == 0
    orders = result.reports.orders
    tp_ids = set(
        orders.loc[
            (orders["type"] == "LIMIT") & (orders["status"] == "FILLED"),
            "client_order_id",
        ]
    )
    assert len(tp_ids) == expected_tp_fills
    assert orders.loc[orders["type"] == "LIMIT", "is_reduce_only"].all()


def test_same_bar_tp_sl_uses_frozen_adaptive_low_first_tie() -> None:
    machine = _ScriptedMachine(take_profit=True, base_stop=Decimal(90), target=Decimal(110))
    result = _run(
        machine,
        [(100, 101, 99, 100), (100, 101, 99, 100), (100, 110, 90, 100)],
    )

    assert result.final_net_quantity == 0
    fills = result.reports.fills.sort_values("ts_event")
    assert list(fills["last_px"]) == ["100.0", "90.0"]
    closed = [event for event in machine.events if isinstance(event, PositionClosed)]
    assert closed[-1].close_reason is CloseReason.BASE_SL


def test_native_funding_is_drained_once_before_position_close() -> None:
    instrument, bar_type = _instrument_fixture()
    machine = _ScriptedMachine(
        take_profit=False,
        close_on_bar=3,
        base_stop=Decimal(50),
    )
    bars = _bars(
        instrument,
        bar_type,
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),
            (100, 100, 100, 100),
            (100, 100, 100, 100),
        ],
        timestamps=[HOUR_NS, 2 * HOUR_NS, 9 * HOUR_NS, 10 * HOUR_NS],
    )
    funding = nt.FundingRateUpdate(
        instrument.id,
        Decimal("0.01"),
        3 * HOUR_NS,
        3 * HOUR_NS,
        interval=28_800,
        next_funding_ns=8 * HOUR_NS,
    )
    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=[*bars, funding],
        feature_source=_features,
    )

    funding_events = [
        event for event in machine.events if event.__class__.__name__ == "FundingApplied"
    ]
    closed = [event for event in machine.events if isinstance(event, PositionClosed)]
    reconciled = [event for event in machine.events if isinstance(event, ReconciliationCompleted)]
    assert len(funding_events) == 1
    assert len(reconciled) == 1
    assert funding_events[0].amount == Decimal("-1.00000000")
    assert machine.events.index(funding_events[0]) < machine.events.index(closed[0])
    assert machine.events.index(closed[0]) < machine.events.index(reconciled[0])
    assert funding_events[0].setup_id == reconciled[0].setup_id
    assert closed[0].funding == Decimal("-1.00000000")
    assert result.final_net_quantity == 0


def test_native_funding_uses_completed_mark_price_update_not_last_trade() -> None:
    instrument, bar_type = _instrument_fixture()
    machine = _ScriptedMachine(
        take_profit=False,
        close_on_bar=3,
        base_stop=Decimal(50),
    )
    bars = _bars(
        instrument,
        bar_type,
        [(100, 100, 100, 100)] * 4,
        timestamps=[HOUR_NS, 2 * HOUR_NS, 9 * HOUR_NS, 10 * HOUR_NS],
    )
    mark = nt.MarkPriceUpdate(
        instrument.id,
        nt.Price.from_str("200.00"),
        8 * HOUR_NS - 1_000_000,
        8 * HOUR_NS - 1_000_000,
    )
    funding = nt.FundingRateUpdate(
        instrument.id,
        Decimal("0.01"),
        8 * HOUR_NS - 1,
        8 * HOUR_NS - 1,
        interval=28_800,
        next_funding_ns=8 * HOUR_NS,
    )

    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=[mark, funding, *bars],
        feature_source=_features,
    )

    funding_events = [event for event in result.domain_events if isinstance(event, FundingApplied)]
    assert len(funding_events) == 1
    assert funding_events[0].amount == Decimal("-2.00000000")


def test_native_funding_money_rounds_exact_midpoint_to_even() -> None:
    instrument, bar_type = _instrument_fixture()
    machine = _ScriptedMachine(take_profit=False, close_on_bar=3, base_stop=Decimal(50))
    bars = _bars(
        instrument,
        bar_type,
        [(100, 100, 100, 100)] * 4,
        timestamps=[HOUR_NS, 2 * HOUR_NS, 9 * HOUR_NS, 10 * HOUR_NS],
    )
    funding = nt.FundingRateUpdate(
        instrument.id,
        Decimal("0.00000000025"),
        3 * HOUR_NS,
        3 * HOUR_NS,
        interval=28_800,
        next_funding_ns=8 * HOUR_NS,
    )

    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=[*bars, funding],
        feature_source=_features,
    )

    funding_events = [event for event in result.domain_events if isinstance(event, FundingApplied)]
    assert funding_events[0].amount == Decimal("-0.00000002")


def test_same_timestamp_mark_update_does_not_replace_last_price_equity() -> None:
    instrument, bar_type = _instrument_fixture()
    machine = _ScriptedMachine(take_profit=False, base_stop=Decimal(50))
    bars = _bars(
        instrument,
        bar_type,
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),
            (110, 110, 110, 110),
            (110, 110, 110, 110),
        ],
    )
    mark = nt.MarkPriceUpdate(
        instrument.id,
        nt.Price.from_str("200.00"),
        3 * HOUR_NS,
        3 * HOUR_NS,
    )

    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=[mark, *bars],
        feature_source=_features,
    )

    equity = [event for event in result.domain_events if isinstance(event, AccountEquityUpdated)]
    at_third_close = next(event for event in equity if event.occurred_at_utc.hour == 3)
    assert at_third_close.equity == Decimal("100010.00000000")


def test_native_multi_tf_routes_all_m5_before_equal_close_h1() -> None:
    """Nautilus 1.230.0 dostarcza M5 phase przed H1 przy wspólnym close."""

    instrument, h1_type = _instrument_fixture()
    m5_type = nt.BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-EXTERNAL")
    marking = _bars(
        instrument,
        m5_type,
        [(100, 101, 99, 100)] * 12,
        timestamps=[(index + 1) * FIVE_MINUTES_NS for index in range(12)],
    )
    execution = _bars(
        instrument,
        h1_type,
        [(100, 101, 99, 100)],
        timestamps=[HOUR_NS],
    )
    machine = _ScriptedMachine()
    run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=h1_type,
        data=execution,
        feature_source=_features,
        marking_bar_type=m5_type,
        marking_data=marking,
        marking_interval_ns=FIVE_MINUTES_NS,
    )

    routed = [event for event in machine.events if isinstance(event, (MarkingBarClosed, BarClosed))]
    assert len(routed) == 13
    assert all(isinstance(event, MarkingBarClosed) for event in routed[:12])
    assert isinstance(routed[-1], BarClosed)
    assert routed[-2].close_time_utc == routed[-1].close_time_utc


def test_unresolved_funding_watermark_blocks_final_reconciliation() -> None:
    class FundingGateMachine(_ScriptedMachine):
        def __init__(self) -> None:
            super().__init__(take_profit=False, close_on_bar=3, base_stop=Decimal(50))
            self.unresolved = False

        @property
        def recovery_view(self) -> SimpleNamespace:
            active_setup_id = "setup-1" if self.base_intent_emitted else None
            return SimpleNamespace(
                active_setup_id=active_setup_id,
                setup_side=None if active_setup_id is None else Side.LONG,
                pending_close_reason=None,
                final_close_reason=(CloseReason.TP if self.unresolved else None),
                commissions=Decimal(0),
                funding=Decimal(0),
                realized_slippage_cost=Decimal(0),
                funding_settlement_ids=("late-settlement",) if self.unresolved else (),
                unresolved_funding_settlement_ids=(("late-settlement",) if self.unresolved else ()),
                closing_execution_ids=(),
                entry_fills=(),
                orders=(),
                outbox=(),
            )

        def handle(self, event: DomainEvent) -> _Transition:
            transition = super().handle(event)
            if isinstance(event, PositionClosed):
                self.unresolved = True
            return transition

        def snapshot_json(self) -> str:
            return "{}"

    captured: list[Pyo3RecoveryCheckpoint] = []
    machine = FundingGateMachine()
    result = _run(
        machine,
        [(100, 100, 100, 100)] * 4,
        persist_recovery_transition=lambda _state, checkpoint: captured.append(checkpoint),
    )

    assert result.final_net_quantity == 0
    assert not any(isinstance(event, ReconciliationCompleted) for event in machine.events)
    assert captured[-1].awaiting_flat_reconciliation
    assert captured[-1].unresolved_funding_settlement_ids == ("late-settlement",)


def test_late_funding_after_flat_reset_persists_unallocated_watermark() -> None:
    class FlatFundingMachine(_ScriptedMachine):
        def __init__(self) -> None:
            super().__init__(take_profit=False)
            self.unresolved = False

        @property
        def recovery_view(self) -> SimpleNamespace:
            return SimpleNamespace(
                active_setup_id=None,
                setup_side=None,
                pending_close_reason=None,
                final_close_reason=None,
                commissions=Decimal(0),
                funding=Decimal(0),
                realized_slippage_cost=Decimal(0),
                funding_settlement_ids=("late-flat",) if self.unresolved else (),
                unresolved_funding_settlement_ids=(("late-flat",) if self.unresolved else ()),
                closing_execution_ids=(),
                entry_fills=(),
                orders=(),
                outbox=(),
            )

        def handle(self, event: DomainEvent) -> _Transition:
            transition = super().handle(event)
            if event.__class__.__name__ == "FundingApplied":
                self.unresolved = True
            return transition

        def snapshot_json(self) -> str:
            return "{}"

    instrument, bar_type = _instrument_fixture()
    captured: list[Pyo3RecoveryCheckpoint] = []
    strategy = NautilusMastermindStrategy(
        machine=FlatFundingMachine(),
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=_features,
        persist_recovery_transition=lambda _state, checkpoint: captured.append(checkpoint),
    )
    strategy._publish_native_funding_adjustment(
        adjustment_id="late-flat-adjustment",
        settlement_id="late-flat",
        amount=Decimal("-0.25"),
        occurred_ns=10 * HOUR_NS,
    )

    checkpoint = captured[-1]
    assert checkpoint.active_setup_id is None
    assert checkpoint.native_funding == 0
    assert checkpoint.seen_funding_settlement_ids == ("late-flat",)
    assert checkpoint.unresolved_funding_settlement_ids == ("late-flat",)
    assert not checkpoint.awaiting_flat_reconciliation
    assert Pyo3RecoveryCheckpoint.from_json(checkpoint.to_json()) == checkpoint


def test_untyped_known_client_ids_fail_closed_without_recovery_provenance() -> None:
    machine = _ScriptedMachine(take_profit=False, restore=True)
    with pytest.raises(Pyo3SmokeProfileError, match=r"machine\.recovery_view"):
        _run(
            machine,
            [(100, 101, 99, 100), (100, 101, 99, 100)],
            reconcile_on_start=True,
            known_client_order_ids=("RESTORE-BASE",),
        )


@pytest.mark.parametrize("persistent", [False, True], ids=["transient", "persistent"])
def test_reentrant_reconciliation_is_deferred_and_bounded(persistent: bool) -> None:
    class ReentrantMachine(_ScriptedMachine):
        def __init__(self) -> None:
            super().__init__(take_profit=False)
            self.reconciliation_count = 0

        def handle(self, event: DomainEvent) -> _Transition:
            transition = super().handle(event)
            if not isinstance(event, ReconciliationCompleted):
                return transition
            self.reconciliation_count += 1
            if not persistent and self.reconciliation_count > 1:
                return transition
            request = RequestReconciliation(
                intent_id=f"reconcile-{self.reconciliation_count}",
                idempotency_key=f"reconcile-key-{self.reconciliation_count}",
                strategy_id=STRATEGY_ID,
                instrument_id=INSTRUMENT,
                setup_id=None,
                causation_id=event.event_id,
                correlation_id="reconciliation-loop",
                reason="transient query race" if not persistent else "persistent block",
            )
            return _Transition((*transition.intents, request))

    machine = ReentrantMachine()
    _run(
        machine,
        [(100, 101, 99, 100)],
        reconcile_on_start=True,
    )

    assert machine.reconciliation_count == (4 if persistent else 2)


def test_cancel_removes_unsent_scheduled_market_intent() -> None:
    class ScheduleThenCancelMachine(_ScriptedMachine):
        def handle(self, event: DomainEvent) -> _Transition:
            transition = super().handle(event)
            addon = next(
                (intent for intent in transition.intents if isinstance(intent, SubmitAddonOrder)),
                None,
            )
            if addon is None:
                return transition
            cancel = CancelOrder(
                intent_id="cancel-unsent-addon",
                idempotency_key="cancel-unsent-addon",
                strategy_id=STRATEGY_ID,
                instrument_id=INSTRUMENT,
                setup_id=addon.setup_id,
                causation_id=event.event_id,
                correlation_id=addon.correlation_id,
                target_client_order_id=addon.client_order_id,
                reason="setup closed before next-close submission",
            )
            return _Transition((*transition.intents, cancel))

    machine = ScheduleThenCancelMachine(addon=True, take_profit=False)
    result = _run(
        machine,
        [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)],
    )

    assert "ADDON-ENTRY" not in result.submitted_client_order_ids
    assert not any(
        isinstance(event, (OrderPartiallyFilled, OrderFilled))
        and event.role is OrderRole.ADDON_ENTRY
        for event in result.domain_events
    )


def test_prepared_cancel_is_replayed_only_when_query_still_finds_target() -> None:
    instrument, bar_type = _instrument_fixture()
    cancel = CancelOrder(
        intent_id="cancel-intent",
        idempotency_key="cancel-key",
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        setup_id="active-setup",
        causation_id="cause",
        correlation_id="active-setup",
        target_client_order_id="TARGET",
        reason="recovery cancel",
    )

    class CancelRecoveryMachine(_ScriptedMachine):
        @property
        def recovery_view(self) -> SimpleNamespace:
            return SimpleNamespace(
                active_setup_id="active-setup",
                setup_side=Side.LONG,
                pending_close_reason=None,
                final_close_reason=None,
                commissions=Decimal(0),
                funding=Decimal(0),
                realized_slippage_cost=Decimal(0),
                funding_settlement_ids=(),
                unresolved_funding_settlement_ids=(),
                closing_execution_ids=(),
                entry_fills=(),
                orders=(),
                outbox=(cancel,),
            )

        def snapshot_json(self) -> str:
            return "{}"

    binding = Pyo3RecoveryOrderBinding(
        role=OrderRole.ADDON_STOP,
        intent_id="target-intent",
        logical_client_order_id="TARGET",
        actual_client_order_id="TARGET",
        side=Side.SHORT,
        requested_quantity=Decimal(1),
        reduce_only=True,
        close_position=False,
        protected_execution_id=None,
        close_reason=None,
        smoke_helper=False,
        setup_id="active-setup",
    )
    strategy = NautilusMastermindStrategy(
        machine=CancelRecoveryMachine(take_profit=False),
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=_features,
        reconcile_on_start=True,
        recovery_checkpoint=Pyo3RecoveryCheckpoint(
            strategy_id=STRATEGY_ID,
            instrument_id=INSTRUMENT,
            source_sequence=5,
            active_setup_id="active-setup",
            bindings=(binding,),
            submitted_client_order_ids=("TARGET",),
        ),
        persist_recovery_transition=lambda _state, _transport: None,
    )

    assert strategy._prepare_absent_recovery_attempts(
        actual_open_ids={"TARGET"},
        outbox=(cancel,),
    ) == ("TARGET",)
    assert not strategy._prepare_absent_recovery_attempts(
        actual_open_ids=set(),
        outbox=(cancel,),
    )


def test_actual_restored_machine_resumes_above_durable_source_sequence_highwater() -> None:
    """A wrapper restart must not make every new callback look like a stale replay."""

    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
        addon_enabled=False,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    original = MastermindStateMachine(config)
    original.apply(
        AccountEquityUpdated(
            event_id="pre-restart-equity",
            strategy_id=STRATEGY_ID,
            instrument_id=INSTRUMENT,
            occurred_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
            source="nautilus_pyo3.bar",
            source_sequence=5_000,
            equity=Decimal(100),
        )
    )
    raw = original.snapshot_json()
    document = json.loads(raw)
    restored = MastermindStateMachine.from_snapshot(config, raw)
    restored.apply(
        RecoverySnapshotLoaded(
            event_id="recovery-loaded-after-highwater",
            strategy_id=STRATEGY_ID,
            instrument_id=INSTRUMENT,
            occurred_at_utc=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
            source="recovery.loader",
            source_sequence=1,
            schema_version="mms_state/1",
            checksum=str(document["checksum"]),
            snapshot_id=str(document["snapshot_id"]),
        )
    )
    assert restored.state.recovery_mode
    assert restored.source_sequence_highwater == 5_000

    instrument, bar_type = _instrument_fixture()
    result = run_pyo3_mastermind_smoke(
        machine=restored,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(
            instrument,
            bar_type,
            [(100, 101, 99, 100), (100, 101, 99, 100)],
        ),
        feature_source=lambda _bar: BarFeatures(
            bb_upper=Decimal(110),
            bb_lower=Decimal(90),
        ),
        reconcile_on_start=True,
        starting_balance=Decimal(100),
    )

    reconciled = [
        event for event in result.domain_events if isinstance(event, ReconciliationCompleted)
    ]
    bars = [event for event in result.domain_events if isinstance(event, BarClosed)]
    assert len(reconciled) == 1
    assert len(bars) == 2
    assert reconciled[0].source_sequence > 5_000
    assert min(event.source_sequence for event in bars) > 5_000
    assert not restored.state.recovery_mode
    assert restored.state.invariant_violation_count == 0


def test_actual_pending_submit_replays_same_id_after_absent_venue_query() -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
        addon_enabled=False,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    machine = MastermindStateMachine(config)
    instrument, bar_type = _instrument_fixture()
    captured: list[tuple[str, Pyo3RecoveryCheckpoint]] = []
    run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(
            instrument,
            bar_type,
            [(100, 101, 97, 99), (99, 100, 99, 100)],
        ),
        feature_source=lambda _bar: BarFeatures(
            bb_upper=Decimal(102),
            bb_lower=Decimal(98),
        ),
        starting_balance=Decimal(100),
        persist_recovery_transition=lambda snapshot, checkpoint: captured.append(
            (snapshot, checkpoint)
        ),
    )

    snapshot, checkpoint = captured[-1]
    base = next(
        order for order in machine.recovery_view.orders if order.role is OrderRole.BASE_ENTRY
    )
    assert machine.state.order_lifecycle is OrderLifecycle.BASE_PENDING
    assert checkpoint.scheduled_market_intent_ids
    assert not checkpoint.submitted_client_order_ids
    assert Pyo3RecoveryCheckpoint.from_json(checkpoint.to_json()) == checkpoint

    document = json.loads(snapshot)
    restored = MastermindStateMachine.from_snapshot(config, snapshot)
    restored.apply(
        RecoverySnapshotLoaded(
            event_id="pending-recovery-loaded",
            strategy_id=STRATEGY_ID,
            instrument_id=INSTRUMENT,
            occurred_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
            source="recovery.loader",
            source_sequence=1,
            setup_id=restored.recovery_view.active_setup_id,
            schema_version="mms_state/1",
            checksum=str(document["checksum"]),
            snapshot_id=str(document["snapshot_id"]),
        )
    )
    resumed: list[tuple[str, Pyo3RecoveryCheckpoint]] = []
    result = run_pyo3_mastermind_smoke(
        machine=restored,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(
            instrument,
            bar_type,
            [(100, 101, 99, 100), (100, 101, 99, 100)],
            timestamps=[3 * HOUR_NS, 4 * HOUR_NS],
        ),
        feature_source=lambda _bar: BarFeatures(
            bb_upper=Decimal(110),
            bb_lower=Decimal(90),
        ),
        starting_balance=Decimal(100),
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint.to_json(),
        persist_recovery_transition=lambda state, transport: resumed.append((state, transport)),
    )

    market_ids = list(
        result.reports.orders.loc[result.reports.orders["type"] == "MARKET", "client_order_id"]
    )
    assert market_ids == [base.client_order_id]
    assert result.submitted_client_order_ids.count(base.client_order_id) == 1
    assert not restored.state.recovery_mode
    assert restored.state.position_build is PositionBuild.BASE
    assert restored.state.invariant_violation_count == 0
    assert resumed


def test_prepared_submit_crash_replays_same_id_after_absent_query() -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
        addon_enabled=False,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    machine = MastermindStateMachine(config)
    instrument, bar_type = _instrument_fixture()
    prepared: list[tuple[str, Pyo3RecoveryCheckpoint]] = []

    def crash_after_prepare(
        snapshot: str,
        checkpoint: Pyo3RecoveryCheckpoint,
    ) -> None:
        if checkpoint.submitted_client_order_ids and not prepared:
            prepared.append((snapshot, checkpoint))
            raise RuntimeError("simulated crash after PREPARED persistence")

    with pytest.raises(Pyo3SmokeProfileError, match="native replay incomplete"):
        run_pyo3_mastermind_smoke(
            machine=machine,
            strategy_id=STRATEGY_ID,
            instrument=instrument,
            bar_type=bar_type,
            data=_bars(
                instrument,
                bar_type,
                [
                    (100, 101, 97, 99),
                    (99, 100, 99, 100),
                    (100, 101, 99, 100),
                ],
            ),
            feature_source=lambda _bar: BarFeatures(
                bb_upper=Decimal(102),
                bb_lower=Decimal(98),
            ),
            starting_balance=Decimal(100),
            persist_recovery_transition=crash_after_prepare,
        )

    snapshot, checkpoint = prepared[0]
    assert len(checkpoint.submitted_client_order_ids) == 1
    prepared_id = checkpoint.submitted_client_order_ids[0]
    assert {binding.actual_client_order_id for binding in checkpoint.bindings} == {prepared_id}
    assert not checkpoint.scheduled_market_intent_ids

    document = json.loads(snapshot)
    restored = MastermindStateMachine.from_snapshot(config, snapshot)
    restored.apply(
        RecoverySnapshotLoaded(
            event_id="prepared-submit-recovery-loaded",
            strategy_id=STRATEGY_ID,
            instrument_id=INSTRUMENT,
            occurred_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
            source="recovery.loader",
            source_sequence=1,
            setup_id=restored.recovery_view.active_setup_id,
            schema_version="mms_state/1",
            checksum=str(document["checksum"]),
            snapshot_id=str(document["snapshot_id"]),
        )
    )
    result = run_pyo3_mastermind_smoke(
        machine=restored,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(
            instrument,
            bar_type,
            [(100, 101, 99, 100), (100, 101, 99, 100)],
            timestamps=[3 * HOUR_NS, 4 * HOUR_NS],
        ),
        feature_source=_features,
        starting_balance=Decimal(100),
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint,
        persist_recovery_transition=lambda _state, _transport: None,
    )

    market_ids = list(
        result.reports.orders.loc[result.reports.orders["type"] == "MARKET", "client_order_id"]
    )
    assert market_ids == [prepared_id]
    assert result.submitted_client_order_ids.count(prepared_id) == 1
    assert not restored.state.recovery_mode


@pytest.mark.parametrize("addon_enabled", [False, True], ids=["base", "pyramided"])
def test_actual_active_setup_checkpoint_rehydrates_native_transport(
    addon_enabled: bool,
) -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=(
            AddonTriggerPolicy.STOCH_CROSS
            if addon_enabled
            else AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH
        ),
        addon_enabled=addon_enabled,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    machine = MastermindStateMachine(config)
    instrument, bar_type = _instrument_fixture()
    prices = [
        (100, 101, 97, 99),
        (99, 100, 99, 100),
        (99, 101, 99, 100),
    ]
    if addon_enabled:
        prices.extend([(100, 101, 99, 100), (100, 101, 99, 100)])
    captured: list[tuple[str, Pyo3RecoveryCheckpoint]] = []
    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(instrument, bar_type, prices),
        feature_source=lambda bar: BarFeatures(
            bb_upper=Decimal(110),
            bb_lower=Decimal(98),
            stoch_k=(Decimal(15) if int(bar.ts_init) == 4 * HOUR_NS else Decimal(10)),
            stoch_d=(Decimal(14) if int(bar.ts_init) == 4 * HOUR_NS else Decimal(12)),
        ),
        starting_balance=Decimal(100),
        slippage_per_unit=Decimal("0.1"),
        persist_recovery_transition=lambda snapshot, checkpoint: captured.append(
            (snapshot, checkpoint)
        ),
    )
    expected_build = PositionBuild.PYRAMIDED if addon_enabled else PositionBuild.BASE
    assert machine.state.position_build is expected_build, {
        "diagnostics": machine.state.diagnostics,
        "counters": machine.state.counters,
        "orders": [(order.role, order.status) for order in machine.state.orders.values()],
    }
    assert result.final_net_quantity == (Decimal(2) if addon_enabled else Decimal(1))

    snapshot, checkpoint = captured[-1]
    expected_roles = {OrderRole.BASE_ENTRY}
    if addon_enabled:
        expected_roles.add(OrderRole.ADDON_ENTRY)
    assert {fill.role for fill in checkpoint.exposure_fills} == expected_roles
    assert checkpoint.active_setup_id == machine.recovery_view.active_setup_id
    assert checkpoint.coverage_groups
    assert checkpoint.bindings
    assert checkpoint.native_slippage_cost == machine.recovery_view.realized_slippage_cost
    assert checkpoint.native_slippage_cost > 0

    document = json.loads(snapshot)
    unsafe_restore = MastermindStateMachine.from_snapshot(config, snapshot)
    with pytest.raises(
        Pyo3SmokeProfileError,
        match="active exposure restart requires a transport recovery checkpoint",
    ):
        NautilusMastermindStrategy(
            machine=unsafe_restore,
            strategy_id=STRATEGY_ID,
            instrument_id=instrument.id,
            bar_type=bar_type,
            feature_source=_features,
            reconcile_on_start=True,
            persist_recovery_transition=lambda _state, _transport: None,
        )

    restored = MastermindStateMachine.from_snapshot(config, snapshot)
    restored.apply(
        RecoverySnapshotLoaded(
            event_id=f"active-{expected_build.value}-recovery-loaded",
            strategy_id=STRATEGY_ID,
            instrument_id=INSTRUMENT,
            occurred_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
            source="recovery.loader",
            source_sequence=1,
            setup_id=restored.recovery_view.active_setup_id,
            schema_version="mms_state/1",
            checksum=str(document["checksum"]),
            snapshot_id=str(document["snapshot_id"]),
        )
    )
    resumed: list[tuple[str, Pyo3RecoveryCheckpoint]] = []
    strategy = NautilusMastermindStrategy(
        machine=restored,
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=_features,
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint.to_json(),
        persist_recovery_transition=lambda state, transport: resumed.append((state, transport)),
    )
    rehydrated = strategy.recovery_checkpoint()

    assert rehydrated.active_setup_id == checkpoint.active_setup_id
    assert rehydrated.exposure_fills == checkpoint.exposure_fills
    assert rehydrated.native_commissions == checkpoint.native_commissions
    assert rehydrated.native_funding == checkpoint.native_funding
    assert rehydrated.native_slippage_cost == checkpoint.native_slippage_cost
    assert set(rehydrated.submitted_client_order_ids) == set(checkpoint.submitted_client_order_ids)
    assert {binding.actual_client_order_id for binding in rehydrated.bindings} == {
        binding.actual_client_order_id for binding in checkpoint.bindings
    }


def test_recovery_semantic_bar_highwater_skips_old_bar_and_equity() -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
        addon_enabled=False,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    machine = MastermindStateMachine(config)
    instrument, bar_type = _instrument_fixture()
    checkpoint = Pyo3RecoveryCheckpoint(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        source_sequence=10,
        last_delivered_bar_close_ns=2 * HOUR_NS,
        last_published_equity_close_ns=2 * HOUR_NS,
    )
    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(
            instrument,
            bar_type,
            [(100, 101, 99, 100), (100, 101, 99, 100)],
            timestamps=[2 * HOUR_NS, 3 * HOUR_NS],
        ),
        feature_source=_features,
        starting_balance=Decimal(100),
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint,
        persist_recovery_transition=lambda _state, _transport: None,
    )

    bars = [event for event in result.domain_events if isinstance(event, BarClosed)]
    equities = [event for event in result.domain_events if isinstance(event, AccountEquityUpdated)]
    assert [event.close_time_utc.hour for event in bars] == [3]
    assert [event.occurred_at_utc.hour for event in equities] == [3]


def test_recovery_preserves_fill_fifo_targets_addon_and_stabilizes_group_identity() -> None:
    instrument, bar_type = _instrument_fixture()
    fills = (
        Pyo3RecoveryExposureFill(
            execution_id="base-exec",
            role=OrderRole.BASE_ENTRY,
            original_quantity=Decimal(1),
            remaining_quantity=Decimal(1),
            side=Side.LONG,
        ),
        Pyo3RecoveryExposureFill(
            execution_id="z-addon-exec",
            role=OrderRole.ADDON_ENTRY,
            original_quantity=Decimal(1),
            remaining_quantity=Decimal(1),
            side=Side.LONG,
        ),
        Pyo3RecoveryExposureFill(
            execution_id="a-addon-exec",
            role=OrderRole.ADDON_ENTRY,
            original_quantity=Decimal(1),
            remaining_quantity=Decimal(1),
            side=Side.LONG,
        ),
    )
    helper_ids = {fill.execution_id: f"helper-{fill.execution_id}" for fill in fills}
    bindings = tuple(
        Pyo3RecoveryOrderBinding(
            role=OrderRole.BASE_STOP,
            intent_id="protect-intent",
            logical_client_order_id="logical-protection",
            actual_client_order_id=helper_ids[fill.execution_id],
            side=Side.SHORT,
            requested_quantity=fill.original_quantity,
            reduce_only=True,
            close_position=False,
            protected_execution_id=fill.execution_id,
            close_reason=None,
            smoke_helper=True,
            setup_id="active-setup",
        )
        for fill in fills
    )
    group = Pyo3RecoveryCoverageGroup(
        logical_client_order_id="logical-protection",
        intent_id="protect-intent",
        role=OrderRole.BASE_STOP,
        side=Side.SHORT,
        trigger_price=Decimal(90),
        reference_quantity=Decimal(3),
        setup_id="active-setup",
        helpers_by_execution_id=tuple(helper_ids.items()),
        submitted_published=True,
        accepted_published=True,
        terminal_published=False,
        cumulative_filled_quantity=Decimal(0),
    )
    checkpoint = Pyo3RecoveryCheckpoint(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        source_sequence=10,
        active_setup_id="active-setup",
        exposure_fills=fills,
        bindings=bindings,
        coverage_groups=(group,),
        submitted_client_order_ids=tuple(helper_ids.values()),
    )

    class RecoveryMachine(_ScriptedMachine):
        def __init__(
            self,
            entry_fills: tuple[Pyo3RecoveryExposureFill, ...],
        ) -> None:
            super().__init__(take_profit=False)
            self.entry_fills = entry_fills

        @property
        def recovery_view(self) -> SimpleNamespace:
            return SimpleNamespace(
                active_setup_id="active-setup",
                setup_side=Side.LONG,
                pending_close_reason=None,
                final_close_reason=None,
                commissions=Decimal(0),
                funding=Decimal(0),
                realized_slippage_cost=Decimal(0),
                funding_settlement_ids=(),
                unresolved_funding_settlement_ids=(),
                closing_execution_ids=(),
                entry_fills=self.entry_fills,
                orders=(),
                outbox=(),
            )

        def snapshot_json(self) -> str:
            return "{}"

    strategy = NautilusMastermindStrategy(
        machine=RecoveryMachine(fills),
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=_features,
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint,
        persist_recovery_transition=lambda _state, _transport: None,
    )
    strategy._consume_exposure_fills(
        Decimal(1),
        addon_only=True,
        preferred_execution_id="a-addon-exec",
    )
    strategy._consume_exposure_fills(Decimal(1), addon_only=False)
    recovered = strategy.recovery_checkpoint()

    assert [fill.execution_id for fill in recovered.exposure_fills] == [
        "base-exec",
        "z-addon-exec",
        "a-addon-exec",
    ]
    assert [fill.remaining_quantity for fill in recovered.exposure_fills] == [
        Decimal(1),
        Decimal(0),
        Decimal(0),
    ]
    assert Pyo3RecoveryCheckpoint.from_json(recovered.to_json()) == recovered

    accepted = SimpleNamespace(name="ACCEPTED")
    first_truth = strategy._reconciled_order(
        "logical-protection",
        [
            SimpleNamespace(
                client_order_id=helper_ids["z-addon-exec"],
                venue_order_id="venue-helper-1",
                status=accepted,
            ),
            SimpleNamespace(
                client_order_id=helper_ids["a-addon-exec"],
                venue_order_id="venue-helper-2",
                status=accepted,
            ),
        ],
    )
    shrunk_truth = strategy._reconciled_order(
        "logical-protection",
        [
            SimpleNamespace(
                client_order_id=helper_ids["a-addon-exec"],
                venue_order_id="venue-helper-2",
                status=accepted,
            )
        ],
    )
    assert first_truth.venue_order_id is None
    assert shrunk_truth.venue_order_id is None
    assert first_truth.setup_id == shrunk_truth.setup_id == "active-setup"

    reordered = (fills[0], fills[2], fills[1])
    with pytest.raises(Pyo3SmokeProfileError, match="exposure fill mismatch"):
        NautilusMastermindStrategy(
            machine=RecoveryMachine(reordered),
            strategy_id=STRATEGY_ID,
            instrument_id=instrument.id,
            bar_type=bar_type,
            feature_source=_features,
            reconcile_on_start=True,
            recovery_checkpoint=checkpoint,
            persist_recovery_transition=lambda _state, _transport: None,
        )


def test_recovery_dedupes_old_native_order_and_position_callbacks_exactly() -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
        addon_enabled=False,
    )
    machine = MastermindStateMachine(config)
    instrument, bar_type = _instrument_fixture()
    binding = Pyo3RecoveryOrderBinding(
        role=OrderRole.BASE_ENTRY,
        intent_id="old-intent",
        logical_client_order_id="OLD-ORDER",
        actual_client_order_id="OLD-ORDER",
        side=Side.LONG,
        requested_quantity=Decimal(1),
        reduce_only=False,
        close_position=False,
        protected_execution_id=None,
        close_reason=None,
        smoke_helper=False,
        setup_id="old-setup",
    )
    checkpoint = Pyo3RecoveryCheckpoint(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        source_sequence=10,
        bindings=(binding,),
        submitted_client_order_ids=("OLD-ORDER",),
        seen_native_lifecycle_event_ids=(
            "native-old-position-changed",
            "native-old-position-closed",
            "native-old-submitted",
        ),
        terminal_logical_order_ids=("OLD-ORDER",),
    )
    strategy = NautilusMastermindStrategy(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=_features,
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint,
        persist_recovery_transition=lambda _state, _transport: None,
    )
    before = machine.snapshot_json()

    strategy.on_order_submitted(
        SimpleNamespace(
            event_id="native-old-submitted",
            client_order_id="OLD-ORDER",
        )
    )
    strategy.on_position_changed(SimpleNamespace(event_id="native-old-position-changed"))
    strategy.on_position_closed(SimpleNamespace(event_id="native-old-position-closed"))

    assert machine.snapshot_json() == before
    assert strategy.recovery_checkpoint().seen_native_lifecycle_event_ids == (
        "native-old-position-changed",
        "native-old-position-closed",
        "native-old-submitted",
    )


def test_historical_close_binding_scopes_and_dedupes_reminted_position_close() -> None:
    instrument, bar_type = _instrument_fixture()
    old_binding = Pyo3RecoveryOrderBinding(
        role=OrderRole.CLOSE_ALL,
        intent_id="old-close-intent",
        logical_client_order_id="OLD-CLOSE",
        actual_client_order_id="OLD-CLOSE",
        side=Side.SHORT,
        requested_quantity=Decimal(1),
        reduce_only=True,
        close_position=False,
        protected_execution_id=None,
        close_reason=CloseReason.TP,
        smoke_helper=False,
        setup_id="setup-1",
    )
    checkpoint = Pyo3RecoveryCheckpoint(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        source_sequence=20,
        active_setup_id="setup-2",
        last_close_reason=CloseReason.MANUAL,
        bindings=(old_binding,),
        submitted_client_order_ids=("OLD-CLOSE",),
    )
    old_order = SimpleNamespace(
        role=OrderRole.CLOSE_ALL,
        intent_id="old-close-intent",
        client_order_id="OLD-CLOSE",
        requested_quantity=Decimal(1),
        filled_quantity=Decimal(1),
        status=OrderStatus.FILLED,
        side=Side.SHORT,
        reduce_only=True,
        close_position=False,
        trigger_price=None,
        setup_id="setup-1",
        protected_execution_id=None,
    )

    class TwoSetupMachine(_ScriptedMachine):
        @property
        def recovery_view(self) -> SimpleNamespace:
            return SimpleNamespace(
                active_setup_id="setup-2",
                setup_side=Side.LONG,
                pending_close_reason=CloseReason.MANUAL,
                final_close_reason=None,
                commissions=Decimal(0),
                funding=Decimal(0),
                realized_slippage_cost=Decimal(0),
                funding_settlement_ids=(),
                unresolved_funding_settlement_ids=(),
                closing_execution_ids=(),
                entry_fills=(),
                orders=(old_order,),
                outbox=(),
            )

        def snapshot_json(self) -> str:
            return "{}"

    machine = TwoSetupMachine(take_profit=False)
    persisted: list[Pyo3RecoveryCheckpoint] = []
    strategy = NautilusMastermindStrategy(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=_features,
        reconcile_on_start=True,
        recovery_checkpoint=checkpoint,
        persist_recovery_transition=lambda _state, transport: persisted.append(transport),
    )

    class Amount:
        def as_decimal(self) -> Decimal:
            return Decimal(5)

        def __str__(self) -> str:
            return "5"

    common = {
        "closing_order_id": "OLD-CLOSE",
        "opening_order_id": "OLD-ENTRY",
        "position_id": "POSITION-1",
        "signed_qty": 0,
        "quantity": 1,
        "avg_px_open": 100,
        "avg_px_close": 105,
        "last_qty": 1,
        "last_px": 105,
        "realized_pnl": Amount(),
        "ts_event": 100,
        "ts_opened": 10,
        "ts_closed": 100,
    }
    strategy.on_position_closed(SimpleNamespace(event_id="old-close-native-1", **common))
    strategy.on_position_closed(SimpleNamespace(event_id="old-close-native-reminted", **common))

    stale = [event for event in machine.events if isinstance(event, PositionClosed)]
    assert len(stale) == 1
    assert stale[0].setup_id == "setup-1"
    assert strategy.recovery_checkpoint().active_setup_id == "setup-2"
    assert not strategy.recovery_checkpoint().awaiting_flat_reconciliation
    assert len(strategy.recovery_checkpoint().seen_native_position_fingerprints) == 1
    assert "old-close-native-reminted" in persisted[-1].seen_native_lifecycle_event_ids


def test_recovery_checkpoint_corruption_fails_closed() -> None:
    checkpoint = Pyo3RecoveryCheckpoint(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        source_sequence=1,
    )
    document = json.loads(checkpoint.to_json())
    document["source_sequence"] = 2

    with pytest.raises(Pyo3SmokeProfileError, match="checksum mismatch"):
        Pyo3RecoveryCheckpoint.from_json(json.dumps(document))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("source_sequence", 1.5),
        ("source_sequence", True),
        ("last_delivered_bar_close_ns", 1.5),
        ("last_published_equity_close_ns", False),
    ],
)
def test_recovery_checkpoint_requires_exact_integer_sequences_and_cursors(
    field: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "strategy_id": STRATEGY_ID,
        "instrument_id": INSTRUMENT,
        "source_sequence": 1,
        field: invalid,
    }
    with pytest.raises(Pyo3SmokeProfileError, match="must be non-negative"):
        Pyo3RecoveryCheckpoint(**values)  # type: ignore[arg-type]

    valid = Pyo3RecoveryCheckpoint(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        source_sequence=1,
        last_delivered_bar_close_ns=2,
        last_published_equity_close_ns=2,
    )
    assert Pyo3RecoveryCheckpoint.from_json(valid.to_json()) == valid


def test_active_base_restart_queries_existing_native_orders_without_duplicates() -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
        addon_enabled=False,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    instrument, bar_type = _instrument_fixture()
    bars = _bars(
        instrument,
        bar_type,
        [
            (100, 101, 97, 99),
            (99, 100, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 99, 100),
        ],
    )
    engine = _engine(instrument, starting_balance="100 USDT")
    engine.add_instrument(instrument)
    engine.add_data(bars)
    machine = MastermindStateMachine(config)
    captured: list[tuple[str, Pyo3RecoveryCheckpoint]] = []
    first = NautilusMastermindStrategy(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=lambda _bar: BarFeatures(bb_upper=Decimal(110), bb_lower=Decimal(98)),
        persist_recovery_transition=lambda state, transport: captured.append((state, transport)),
    )
    engine.add_strategy(first)
    try:
        engine.run(end=3 * HOUR_NS, streaming=True)
        assert machine.state.position_build is PositionBuild.BASE
        assert Decimal(str(engine.portfolio.net_position(instrument.id))) == Decimal(1)
        snapshot, checkpoint = captured[-1]
        open_before = tuple(engine.cache.orders_open(instrument_id=instrument.id))
        assert len(open_before) == 2

        document = json.loads(snapshot)
        restored = MastermindStateMachine.from_snapshot(config, snapshot)
        first._machine = restored
        first._apply_domain_event(
            RecoverySnapshotLoaded(
                event_id="venue-received-recovery-loaded",
                strategy_id=STRATEGY_ID,
                instrument_id=INSTRUMENT,
                occurred_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
                source="recovery.loader",
                source_sequence=1,
                setup_id=restored.recovery_view.active_setup_id,
                schema_version="mms_state/1",
                checksum=str(document["checksum"]),
                snapshot_id=str(document["snapshot_id"]),
            )
        )
        engine.run(start=3 * HOUR_NS + 1, streaming=True)
        engine.end()

        all_orders = tuple(engine.cache.orders())
        assert len(all_orders) == 3
        assert len(engine.cache.orders_open(instrument_id=instrument.id)) == 2
        assert Decimal(str(engine.portfolio.net_position(instrument.id))) == Decimal(1)
        assert not restored.state.recovery_mode
        assert restored.state.invariant_violation_count == 0
        assert Pyo3RecoveryCheckpoint.from_json(checkpoint.to_json()) == checkpoint
    finally:
        engine.dispose()


def test_cutoff_hook_closes_next_bar_without_delivering_post_cutoff_bars() -> None:
    class ManualCutoffMachine(_ScriptedMachine):
        def handle(self, event: DomainEvent) -> _Transition:
            if isinstance(event, CloseRequested):
                self.events.append(event)
                return _Transition(
                    (
                        CloseAll(
                            **self._common(event, "manual-cutoff"),
                            client_order_id="MANUAL-CLOSE",
                            side=Side.SHORT,
                            quantity=Decimal(1),
                            close_reason=CloseReason.MANUAL,
                        ),
                    )
                )
            return super().handle(event)

    injected = False
    feature_calls: list[int] = []

    def before_bar(bar: Any) -> Iterable[DomainEvent]:
        nonlocal injected
        if int(bar.ts_init) != 3 * HOUR_NS or injected:
            return ()
        injected = True
        return (
            CloseRequested(
                event_id="manual-cutoff",
                strategy_id=STRATEGY_ID,
                instrument_id=INSTRUMENT,
                occurred_at_utc=datetime.fromtimestamp(int(bar.ts_init) / 1e9, tz=UTC),
                source="fixture.cutoff",
                source_sequence=1,
                close_reason=CloseReason.MANUAL,
                reason="development boundary",
            ),
        )

    def features(bar: Any) -> BarFeatures:
        feature_calls.append(int(bar.ts_init))
        return _features(bar)

    instrument, bar_type = _instrument_fixture()
    result = run_pyo3_mastermind_smoke(
        machine=ManualCutoffMachine(take_profit=False, base_stop=Decimal(50)),
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(instrument, bar_type, [(100, 101, 99, 100)] * 4),
        feature_source=features,
        before_bar_domain_events=before_bar,
        deliver_domain_bar=lambda bar: int(bar.ts_init) < 3 * HOUR_NS,
    )

    bars = [event for event in result.domain_events if isinstance(event, BarClosed)]
    closed = [event for event in result.domain_events if isinstance(event, PositionClosed)]
    assert [event.close_time_utc.hour for event in bars] == [1, 2]
    assert feature_calls == [HOUR_NS, 2 * HOUR_NS]
    assert closed[-1].close_reason is CloseReason.MANUAL
    assert result.final_net_quantity == 0


def test_native_callback_error_cannot_return_a_partial_replay_as_success() -> None:
    callbacks = 0

    def explode_on_second_bar(_bar: Any) -> tuple[DomainEvent, ...]:
        nonlocal callbacks
        callbacks += 1
        if callbacks == 2:
            raise ValueError("synthetic callback failure")
        return ()

    with pytest.raises(Pyo3SmokeProfileError, match="native replay incomplete"):
        _run(
            _ScriptedMachine(take_profit=False, base_stop=Decimal(50)),
            [(100, 101, 99, 100)] * 4,
            before_bar_domain_events=explode_on_second_bar,
        )
    assert callbacks >= 2


def test_transition_snapshot_is_persisted_before_native_submission() -> None:
    timeline: list[tuple[str, object]] = []

    class TrackingMachine(_ScriptedMachine):
        def __init__(self) -> None:
            super().__init__(take_profit=False)
            self.current_snapshot = "{}"

        def handle(self, event: DomainEvent) -> _Transition:
            timeline.append(("handle", event.event_id))
            transition = super().handle(event)
            result = _Transition(
                transition.intents,
                f'{{"last_event":"{event.event_id}"}}',
            )
            self.current_snapshot = result.snapshot_json
            return result

        def snapshot_json(self) -> str:
            return self.current_snapshot

    def persist(snapshot_json: str) -> None:
        timeline.append(("persist", snapshot_json))

    atomic: list[tuple[str, Pyo3RecoveryCheckpoint]] = []

    def persist_recovery(
        snapshot_json: str,
        checkpoint: Pyo3RecoveryCheckpoint,
    ) -> None:
        atomic.append((snapshot_json, checkpoint))
        timeline.append(("atomic", len(atomic) - 1))

    result = _run(
        TrackingMachine(),
        [(100, 101, 99, 100), (100, 101, 99, 100)],
        persist_transition=persist,
        persist_recovery_transition=persist_recovery,
    )

    first_bar = next(event for event in result.domain_events if isinstance(event, BarClosed))
    submitted = next(
        event for event in result.domain_events if event.__class__.__name__ == "OrderSubmitted"
    )
    persisted_bar = ("persist", f'{{"last_event":"{first_bar.event_id}"}}')
    submitted_handle = ("handle", submitted.event_id)
    pre_submit_index = next(
        index
        for index, (kind, payload) in enumerate(timeline)
        if kind == "atomic"
        and "BASE-ENTRY" in atomic[int(payload)][1].submitted_client_order_ids
        and any(
            binding.actual_client_order_id == "BASE-ENTRY"
            for binding in atomic[int(payload)][1].bindings
        )
    )
    assert timeline.index(persisted_bar) < timeline.index(submitted_handle)
    assert pre_submit_index < timeline.index(submitted_handle)


def test_actual_machine_two_setups_funding_cleanup_reconcile_and_exact_snapshot() -> None:
    config = MastermindConfig(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT,
        addon_trigger_policy=AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH,
        addon_enabled=False,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(1),
    )
    machine = MastermindStateMachine(config)
    instrument, bar_type = _instrument_fixture()
    timestamps = [
        HOUR_NS,
        2 * HOUR_NS,
        3 * HOUR_NS,
        9 * HOUR_NS,
        10 * HOUR_NS,
        11 * HOUR_NS,
        12 * HOUR_NS,
        13 * HOUR_NS,
    ]
    bars = _bars(
        instrument,
        bar_type,
        [
            (100, 101, 97, 99),
            (99, 100, 99, 100),
            (100, 101, 99, 100),
            (100, 103, 99, 102),
            (100, 101, 97, 99),
            (99, 100, 99, 100),
            (100, 101, 99, 100),
            (100, 103, 99, 102),
        ],
        timestamps=timestamps,
    )
    band_by_timestamp = {
        HOUR_NS: (Decimal(102), Decimal(98)),
        2 * HOUR_NS: (Decimal(102), Decimal(98)),
        3 * HOUR_NS: (Decimal(102), Decimal(98)),
        9 * HOUR_NS: (Decimal(110), Decimal(98)),
        10 * HOUR_NS: (Decimal(110), Decimal(98)),
        11 * HOUR_NS: (Decimal(102), Decimal(98)),
        12 * HOUR_NS: (Decimal(102), Decimal(98)),
        13 * HOUR_NS: (Decimal(110), Decimal(98)),
    }

    def causal_features(bar: Any) -> BarFeatures:
        upper, lower = band_by_timestamp[int(bar.ts_init)]
        return BarFeatures(bb_upper=upper, bb_lower=lower)

    funding = nt.FundingRateUpdate(
        instrument.id,
        Decimal("0.00001"),
        3 * HOUR_NS,
        3 * HOUR_NS,
        interval=28_800,
        next_funding_ns=8 * HOUR_NS,
    )
    persisted: list[str] = []
    result = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=[*bars, funding],
        feature_source=causal_features,
        persist_transition=persisted.append,
        starting_balance=Decimal(100),
    )

    closed = [event for event in result.domain_events if isinstance(event, PositionClosed)]
    reconciled = [
        event for event in result.domain_events if isinstance(event, ReconciliationCompleted)
    ]
    funding_events = [
        event for event in result.domain_events if event.__class__.__name__ == "FundingApplied"
    ]
    protective = result.reports.orders.loc[
        result.reports.orders["type"].isin(["STOP_MARKET", "LIMIT"])
    ]
    assert len(closed) == 2
    assert len(reconciled) == 2, machine.state.diagnostics
    assert len(funding_events) == 1
    assert funding_events[0].setup_id == closed[0].setup_id
    first_reconciliation = next(
        event for event in reconciled if event.setup_id == closed[0].setup_id
    )
    assert result.domain_events.index(funding_events[0]) < result.domain_events.index(
        first_reconciliation
    )
    assert closed[0].funding < 0
    assert closed[1].funding == 0
    assert len(protective) == 4
    assert protective["client_order_id"].is_unique
    assert (
        len({execution_id for event in closed for execution_id in event.closing_execution_ids}) == 2
    )
    assert machine.state.position_build is PositionBuild.FLAT
    assert machine.state.order_lifecycle is OrderLifecycle.NONE
    assert machine.state.setup is None
    assert machine.state.counters["base_entries"] == 2
    assert machine.state.invariant_violation_count == 0
    machine.assert_invariants()
    assert persisted[-1] == machine.snapshot_json()
    restored = MastermindStateMachine.from_snapshot(config, persisted[-1])
    assert restored.snapshot_json() == persisted[-1]


def test_one_tick_slippage_is_separate_and_net_algebra_matches_native() -> None:
    machine = _ScriptedMachine(take_profit=False, close_on_bar=3, base_stop=Decimal(50))
    result = _run(
        machine,
        [(100, 100, 100, 100)] * 4,
        fill_model=nt.DefaultFillModel(
            prob_fill_on_limit=1.0,
            prob_slippage=1.0,
            random_seed=7,
        ),
        slippage_per_unit=Decimal("0.1"),
    )

    closed = next(event for event in machine.events if isinstance(event, PositionClosed))
    fills = result.reports.fills.sort_values("ts_event")
    native_net = Decimal(str(fills.iloc[-1]["last_px"])) - Decimal(str(fills.iloc[0]["last_px"]))
    domain_net = (
        closed.realized_price_pnl
        - closed.commissions
        + closed.funding
        - closed.realized_slippage_cost
    )
    assert closed.realized_slippage_cost == Decimal("0.2")
    assert domain_net == native_net


def test_every_run_is_unambiguously_smoke_only_and_cache_reported() -> None:
    result = _run(_ScriptedMachine(take_profit=False), [(100, 101, 99, 100)] * 2)
    metadata = result.metadata

    assert metadata == Pyo3SmokeMetadata()
    assert metadata.evidence_tier == EVIDENCE_TIER == "SMOKE_ONLY"
    assert metadata.eligibility == ELIGIBILITY == "NOT_ELIGIBLE"
    assert metadata.execution_profile == PYO3_SMOKE_EXECUTION_PROFILE
    assert metadata.position_model == PYO3_SMOKE_POSITION_MODEL
    assert not metadata.close_position_parity
    assert not metadata.custom_matching_engine
    assert not result.reports.orders.empty
    assert not result.reports.fills.empty
    assert not result.reports.account_events.empty


def test_research_metadata_is_explicit_and_does_not_claim_close_position_parity() -> None:
    metadata = Pyo3ResearchMetadata()
    result = _run(
        _ScriptedMachine(take_profit=False),
        [(100, 101, 99, 100)] * 2,
        run_metadata=metadata,
    )

    assert result.metadata is metadata
    assert metadata.as_dict() == {
        "evidence_tier": "RESEARCH",
        "eligibility": "EVIDENCE_GATE_PENDING",
        "execution_profile": PYO3_RESEARCH_EXECUTION_PROFILE,
        "position_model": PYO3_RESEARCH_POSITION_MODEL,
        "engine": "nautilus_trader.core.nautilus_pyo3.BacktestEngine",
        "close_position_parity": False,
        "custom_matching_engine": False,
    }


def test_domain_retention_filter_does_not_skip_machine_or_observer() -> None:
    machine = _ScriptedMachine(take_profit=False)
    observed: list[DomainEvent] = []
    result = _run(
        machine,
        [(100, 101, 99, 100)] * 2,
        transition_observer=observed.append,
        retain_domain_event=lambda event: not isinstance(event, BarClosed),
    )

    assert observed == machine.events
    assert any(isinstance(event, BarClosed) for event in observed)
    assert result.domain_events
    assert not any(isinstance(event, BarClosed) for event in result.domain_events)


def test_default_leverage_is_forwarded_to_native_margin_account() -> None:
    leverage_one = _run(
        _ScriptedMachine(take_profit=False),
        [(100, 101, 99, 100)] * 2,
        default_leverage=Decimal(1),
    )
    leverage_two = _run(
        _ScriptedMachine(take_profit=False),
        [(100, 101, 99, 100)] * 2,
        default_leverage=Decimal(2),
    )

    locked_one = Decimal(leverage_one.reports.account_events.iloc[-1]["balances"][0]["locked"])
    locked_two = Decimal(leverage_two.reports.account_events.iloc[-1]["balances"][0]["locked"])
    assert locked_one == Decimal("2.50000000")
    assert locked_two == Decimal("1.25000000")


@pytest.mark.parametrize(
    "default_leverage",
    [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")],
)
def test_default_leverage_must_be_finite_and_positive(default_leverage: Decimal) -> None:
    with pytest.raises(ValueError, match="default_leverage must be finite and positive"):
        _run(
            _ScriptedMachine(take_profit=False),
            [(100, 101, 99, 100)] * 2,
            default_leverage=default_leverage,
        )


def _run(
    machine: Any,
    prices: list[tuple[int, ...]],
    *,
    fill_model: Any | None = None,
    reconcile_on_start: bool = False,
    known_client_order_ids: tuple[str, ...] = (),
    persist_transition: Callable[[str], None] | None = None,
    persist_recovery_transition: (Callable[[str, Pyo3RecoveryCheckpoint], None] | None) = None,
    before_bar_domain_events: Callable[[Any], Iterable[DomainEvent]] | None = None,
    deliver_domain_bar: Callable[[Any], bool] | None = None,
    slippage_per_unit: Decimal = Decimal(0),
    default_leverage: Decimal = Decimal(1),
    transition_observer: Callable[[DomainEvent], None] | None = None,
    retain_domain_event: Callable[[DomainEvent], bool] | None = None,
    run_metadata: Pyo3SmokeMetadata | Pyo3ResearchMetadata | None = None,
):
    instrument, bar_type = _instrument_fixture()
    return run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=STRATEGY_ID,
        instrument=instrument,
        bar_type=bar_type,
        data=_bars(instrument, bar_type, prices),
        feature_source=_features,
        fill_model=fill_model,
        reconcile_on_start=reconcile_on_start,
        known_client_order_ids=known_client_order_ids,
        persist_transition=persist_transition,
        persist_recovery_transition=persist_recovery_transition,
        before_bar_domain_events=before_bar_domain_events,
        deliver_domain_bar=deliver_domain_bar,
        slippage_per_unit=slippage_per_unit,
        default_leverage=default_leverage,
        transition_observer=transition_observer,
        retain_domain_event=retain_domain_event,
        run_metadata=run_metadata,
    )


def _features(_bar: Any) -> BarFeatures:
    return BarFeatures(bb_upper=Decimal(110), bb_lower=Decimal(90))


def _instrument_fixture() -> tuple[Any, Any]:
    instrument_id = nt.InstrumentId.from_str(INSTRUMENT)
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
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
    )
    bar_type = nt.BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")
    return instrument, bar_type


def _bars(
    instrument: Any,
    bar_type: Any,
    prices: list[tuple[int, ...]],
    *,
    timestamps: list[int] | None = None,
) -> list[Any]:
    times = timestamps or [(index + 1) * HOUR_NS for index in range(len(prices))]
    return [
        nt.Bar(
            bar_type=bar_type,
            open=instrument.make_price(Decimal(ohlc[0])),
            high=instrument.make_price(Decimal(ohlc[1])),
            low=instrument.make_price(Decimal(ohlc[2])),
            close=instrument.make_price(Decimal(ohlc[3])),
            volume=instrument.make_qty(Decimal(100)),
            ts_event=timestamp,
            ts_init=timestamp,
        )
        for timestamp, ohlc in zip(times, prices, strict=True)
    ]


def _engine(instrument: Any, *, starting_balance: str = "100000 USDT") -> Any:
    engine = nt.BacktestEngine(
        nt.BacktestEngineConfig(
            logging=nt.LoggerConfig(bypass_logging=True),
            run_analysis=False,
            shutdown_on_error=True,
        )
    )
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=nt.OmsType.NETTING,
        account_type=nt.AccountType.MARGIN,
        starting_balances=[nt.Money.from_str(starting_balance)],
        base_currency=instrument.settlement_currency,
        latency_model=nt.StaticLatencyModel(base_latency_nanos=0),
        use_position_ids=False,
        use_reduce_only=True,
        use_message_queue=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    return engine
