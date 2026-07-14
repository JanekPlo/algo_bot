"""Mandatory lifecycle, sequential-risk and idempotency fixtures for P6."""

from __future__ import annotations

import itertools
import json
import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from algo_bot.strategies.mastermind.model import (
    ZERO,
    AccountEquityUpdated,
    AddonTriggerPolicy,
    BarClosed,
    CancelOrder,
    CloseAll,
    CloseReason,
    CloseRequested,
    FundingApplied,
    MarkingBarClosed,
    MastermindConfig,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderLifecycle,
    OrderPartiallyFilled,
    OrderRejected,
    OrderRole,
    OrderStatus,
    OrderSubmitted,
    OrderTimedOut,
    PositionBuild,
    PositionChanged,
    PositionClosed,
    ReconciledOrder,
    ReconciliationCompleted,
    RecoverySnapshotLoaded,
    ReduceAddon,
    RequestReconciliation,
    RiskLimitTriggered,
    RiskMode,
    Side,
    SubmitAddonOrder,
    SubmitAddonStop,
    SubmitBaseOrder,
)
from algo_bot.strategies.mastermind.state_machine import MastermindStateMachine

D = Decimal
START = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass
class EventFactory:
    seq: int = 0

    def envelope(
        self,
        machine: MastermindStateMachine,
        name: str,
        *,
        occurred_at: datetime | None = None,
        setup_id: str | None = None,
        client_order_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self.seq += 1
        return {
            "event_id": f"{name}-{self.seq}",
            "strategy_id": machine.config.strategy_id,
            "instrument_id": machine.config.instrument_id,
            "occurred_at_utc": occurred_at or START + timedelta(minutes=self.seq),
            "source": "fixture",
            "source_sequence": self.seq,
            "setup_id": setup_id,
            "client_order_id": client_order_id,
            "correlation_id": correlation_id,
        }


def config(
    policy: AddonTriggerPolicy = AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH,
    *,
    instrument: str = "BTCUSDT-PERP.BINANCE",
    addon_enabled: bool = True,
    sequential_enabled: bool = True,
    marking_timeframe: str | None = None,
) -> MastermindConfig:
    return MastermindConfig(
        strategy_id="mms-v2",
        instrument_id=instrument,
        addon_trigger_policy=policy,
        addon_enabled=addon_enabled,
        sequential_enabled=sequential_enabled,
        marking_timeframe=marking_timeframe,
        quantity_step=D("0.001"),
        min_quantity=D("0.001"),
        min_notional=D("1"),
    )


def bar_event(
    machine: MastermindStateMachine,
    events: EventFactory,
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    upper: str = "102",
    lower: str = "98",
    k: str | None = "50",
    d: str | None = "50",
) -> BarClosed:
    open_time = START + timedelta(hours=index)
    close_time = open_time + timedelta(hours=1) - timedelta(milliseconds=1)
    return BarClosed(
        **events.envelope(machine, f"bar-{index}", occurred_at=close_time),
        bar_id=f"h1-{index}",
        open_time_utc=open_time,
        close_time_utc=close_time,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D("10"),
        bb_upper=D(upper),
        bb_lower=D(lower),
        stoch_k=None if k is None else D(k),
        stoch_d=None if d is None else D(d),
    )


def marking_bar_event(
    machine: MastermindStateMachine,
    events: EventFactory,
    index: int,
    *,
    timeframe: str,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
) -> MarkingBarClosed:
    minutes = 5 if timeframe == "5m" else 10
    open_time = START + timedelta(minutes=minutes * index)
    close_time = open_time + timedelta(minutes=minutes) - timedelta(milliseconds=1)
    return MarkingBarClosed(
        **events.envelope(machine, f"marking-{timeframe}-{index}", occurred_at=close_time),
        bar_id=f"{timeframe}-{index}",
        timeframe=timeframe,
        open_time_utc=open_time,
        close_time_utc=close_time,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D("1"),
    )


def bootstrap_base(
    *,
    risk_mode: RiskMode = RiskMode.FULL,
    policy: AddonTriggerPolicy = AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH,
    instrument: str = "BTCUSDT-PERP.BINANCE",
    addon_enabled: bool = True,
    sequential_enabled: bool = True,
) -> tuple[MastermindStateMachine, EventFactory, SubmitBaseOrder]:
    machine = MastermindStateMachine(
        config(
            policy,
            instrument=instrument,
            addon_enabled=addon_enabled,
            sequential_enabled=sequential_enabled,
        ),
        initial_risk_mode=risk_mode,
    )
    events = EventFactory()
    machine.apply(
        AccountEquityUpdated(
            **events.envelope(machine, "equity"),
            equity=D("10000"),
        )
    )
    machine.apply(
        bar_event(
            machine,
            events,
            0,
            open_="100",
            high="101",
            low="97",
            close="99",
            k="8",
            d="10",
        )
    )
    reaction = machine.apply(
        bar_event(
            machine,
            events,
            1,
            open_="99",
            high="100.5",
            low="99",
            close="100",
            k="10",
            d="12",
        )
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "base-fill",
                setup_id=setup_id,
                client_order_id=base.client_order_id,
                correlation_id=base.correlation_id,
            ),
            execution_id="base-exec",
            role=OrderRole.BASE_ENTRY,
            last_quantity=base.quantity,
            cumulative_quantity=base.quantity,
            price=D("100"),
            commission=ZERO,
        )
    )
    return machine, events, base


@pytest.mark.parametrize(("marking_timeframe", "bars_per_h1"), [("5m", 12), ("10m", 6)])
def test_marking_first_touch_arms_then_h1_reaction_executes(
    marking_timeframe: str,
    bars_per_h1: int,
) -> None:
    """M5/M10 first-touch używa poprzednich H1 BB i nie emituje entry samodzielnie."""

    machine = MastermindStateMachine(config(marking_timeframe=marking_timeframe))
    events = EventFactory()
    machine.apply(
        AccountEquityUpdated(
            **events.envelope(machine, "marking-equity"),
            equity=D("10000"),
        )
    )

    # Pierwsze okno tylko seeduje przyczynowe BB H1; marker nie ma jeszcze referencji.
    for index in range(bars_per_h1):
        machine.ingest_marking_bar(
            marking_bar_event(machine, events, index, timeframe=marking_timeframe)
        )
    machine.apply(
        bar_event(
            machine,
            events,
            0,
            open_="100",
            high="101",
            low="99",
            close="100",
            upper="102",
            lower="98",
        )
    )

    # Pierwszy bar drugiego okna dotyka lower BB; późniejszy opposite touch nie
    # nadpisuje first-touch. Żaden marking event nie tworzy order intentu.
    first = machine.ingest_marking_bar(
        marking_bar_event(
            machine,
            events,
            bars_per_h1,
            timeframe=marking_timeframe,
            low="97",
            close="99",
        )
    )
    assert not any(isinstance(intent, SubmitBaseOrder) for intent in first.intents)
    assert machine.state.signal.armed_side is Side.LONG
    assert machine.state.signal.touch_bar_id == f"{marking_timeframe}-{bars_per_h1}"

    for index in range(bars_per_h1 + 1, 2 * bars_per_h1):
        machine.ingest_marking_bar(
            marking_bar_event(
                machine,
                events,
                index,
                timeframe=marking_timeframe,
                high="103" if index == bars_per_h1 + 1 else "101",
            )
        )
    reaction = machine.apply(
        bar_event(
            machine,
            events,
            1,
            open_="99",
            high="102",
            low="98",
            close="101",
        )
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))
    assert base.side is Side.LONG
    assert machine.state.counters["marking_first_touches"] == 1


def test_marking_phase_rejects_missing_leading_subbar_before_h1() -> None:
    """Sam finalny M5 nie wystarcza: H1 wymaga pełnych 12 kolejnych barów."""

    machine = MastermindStateMachine(config(marking_timeframe="5m"))
    events = EventFactory()
    for index in range(1, 12):
        machine.ingest_marking_bar(marking_bar_event(machine, events, index, timeframe="5m"))

    with pytest.raises(ValueError, match="every marking sub-bar"):
        machine.apply(
            bar_event(
                machine,
                events,
                0,
                open_="100",
                high="101",
                low="99",
                close="100",
            )
        )


def test_marking_prefix_invariance_ignores_future_suffix() -> None:
    """Stan po wspólnym prefiksie M5 nie zależy od późniejszych barów."""

    left = MastermindStateMachine(config(marking_timeframe="5m"))
    right = MastermindStateMachine(config(marking_timeframe="5m"))
    left_events = EventFactory()
    right_events = EventFactory()
    for index in range(12):
        left.ingest_marking_bar(marking_bar_event(left, left_events, index, timeframe="5m"))
        right.ingest_marking_bar(marking_bar_event(right, right_events, index, timeframe="5m"))
    left.apply(bar_event(left, left_events, 0, open_="100", high="101", low="99", close="100"))
    right.apply(bar_event(right, right_events, 0, open_="100", high="101", low="99", close="100"))
    for index in range(12, 18):
        kwargs = {"low": "97", "close": "99"} if index == 12 else {}
        left.ingest_marking_bar(
            marking_bar_event(left, left_events, index, timeframe="5m", **kwargs)
        )
        right.ingest_marking_bar(
            marking_bar_event(right, right_events, index, timeframe="5m", **kwargs)
        )

    assert left.snapshot_json() == right.snapshot_json()
    left.ingest_marking_bar(marking_bar_event(left, left_events, 18, timeframe="5m", high="101"))
    right.ingest_marking_bar(marking_bar_event(right, right_events, 18, timeframe="5m", high="110"))
    assert left.state.signal.armed_side is right.state.signal.armed_side is Side.LONG


def trigger_addon(
    machine: MastermindStateMachine,
    events: EventFactory,
) -> SubmitAddonOrder:
    result = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="99.5",
            high="100.5",
            low="99",
            close="100",
            k="15",
            d="14",
        )
    )
    return next(intent for intent in result.intents if isinstance(intent, SubmitAddonOrder))


def fill_addon(
    machine: MastermindStateMachine,
    events: EventFactory,
    addon: SubmitAddonOrder,
    *,
    quantity: Decimal | None = None,
    terminal: bool = True,
    execution_id: str = "addon-exec",
    price: Decimal = D("100"),
) -> SubmitAddonStop | ReduceAddon:
    fill_quantity = addon.quantity if quantity is None else quantity
    event_type = OrderFilled if terminal else OrderPartiallyFilled
    result = machine.apply(
        event_type(
            **events.envelope(
                machine,
                "addon-fill",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
                correlation_id=addon.correlation_id,
            ),
            execution_id=execution_id,
            role=OrderRole.ADDON_ENTRY,
            last_quantity=fill_quantity,
            cumulative_quantity=fill_quantity,
            price=price,
            commission=ZERO,
        )
    )
    return next(
        intent for intent in result.intents if isinstance(intent, (SubmitAddonStop, ReduceAddon))
    )


def finalized_close(
    machine: MastermindStateMachine,
    events: EventFactory,
    reason: CloseReason,
    *,
    gross: str,
    commissions: str = "0",
    funding: str = "0",
    slippage: str = "0",
) -> None:
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    machine.apply(
        PositionClosed(
            **events.envelope(machine, "position-closed", setup_id=setup_id),
            close_reason=reason,
            realized_price_pnl=D(gross),
            commissions=D(commissions),
            funding=D(funding),
            realized_slippage_cost=D(slippage),
        )
    )


def reconcile_flat(machine: MastermindStateMachine, events: EventFactory) -> None:
    last = machine.state.last_reconciliation_sequence or 0
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    machine.apply(
        ReconciliationCompleted(
            **events.envelope(machine, "reconcile", setup_id=setup_id),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=max(events.seq + 1, last + 1),
        )
    )


@pytest.mark.parametrize(
    ("initial", "reason", "gross", "cost", "expected"),
    [
        (RiskMode.FULL, CloseReason.TP, "10", "0", RiskMode.FULL),
        (RiskMode.FULL, CloseReason.BASE_SL, "-200", "0", RiskMode.SCOUT),
        (RiskMode.SCOUT, CloseReason.TP, "2", "1", RiskMode.FULL),
        (RiskMode.SCOUT, CloseReason.TP, "2", "2", RiskMode.SCOUT),
        (RiskMode.SCOUT, CloseReason.TP, "2", "3", RiskMode.SCOUT),
        (RiskMode.SCOUT, CloseReason.BASE_SL, "-20", "0", RiskMode.SCOUT),
    ],
)
def test_sequential_risk_transition_matrix(
    initial: RiskMode,
    reason: CloseReason,
    gross: str,
    cost: str,
    expected: RiskMode,
) -> None:
    machine, events, _ = bootstrap_base(risk_mode=initial)

    finalized_close(machine, events, reason, gross=gross, commissions=cost)

    # PositionClosed freezes the complete setup ledger, but the sequential risk
    # decision is not committed until venue reconciliation confirms final flatness.
    assert machine.state.risk_mode is initial
    assert machine.state.position_build is PositionBuild.FLAT
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    reconcile_flat(machine, events)
    assert machine.state.risk_mode is expected
    assert machine.state.order_lifecycle is OrderLifecycle.NONE


@pytest.mark.parametrize(
    "reason",
    [
        CloseReason.RISK_LIMIT,
        CloseReason.MANUAL,
        CloseReason.LIQUIDATION,
        CloseReason.ENGINE_ERROR,
    ],
)
@pytest.mark.parametrize("initial", [RiskMode.FULL, RiskMode.SCOUT])
def test_forced_close_preserves_risk_mode(initial: RiskMode, reason: CloseReason) -> None:
    machine, events, _ = bootstrap_base(risk_mode=initial)

    finalized_close(machine, events, reason, gross="-999")

    assert machine.state.risk_mode is initial


def test_partial_take_profit_does_not_rearm_scout() -> None:
    machine, events, _ = bootstrap_base(risk_mode=RiskMode.SCOUT)
    tp = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.TAKE_PROFIT
    )

    machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "tp-partial",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
                client_order_id=tp.client_order_id,
            ),
            execution_id="tp-partial-exec",
            role=OrderRole.TAKE_PROFIT,
            last_quantity=machine.state.base_leg.quantity / D("2"),
            cumulative_quantity=machine.state.base_leg.quantity / D("2"),
            price=D("102"),
            commission=ZERO,
        )
    )

    assert machine.state.risk_mode is RiskMode.SCOUT
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.real_open_quantity > ZERO


def test_addon_trigger_is_one_intent_and_build_changes_only_on_fill() -> None:
    machine, events, _ = bootstrap_base()

    addon = trigger_addon(machine, events)

    assert machine.state.position_build is PositionBuild.BASE
    assert machine.state.order_lifecycle is OrderLifecycle.ADDON_PENDING
    addon_intents = [item for item in machine.state.outbox if isinstance(item, SubmitAddonOrder)]
    assert len(addon_intents) == 1

    machine.apply(
        OrderAccepted(
            **events.envelope(
                machine,
                "addon-accepted",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            role=OrderRole.ADDON_ENTRY,
        )
    )
    assert machine.state.position_build is PositionBuild.BASE

    stop = fill_addon(
        machine,
        events,
        addon,
        quantity=addon.quantity * D("0.4"),
        terminal=False,
    )
    assert isinstance(stop, SubmitAddonStop)
    assert stop.quantity == addon.quantity * D("0.4")
    assert machine.state.position_build is PositionBuild.PYRAMIDED
    assert machine.state.order_lifecycle is OrderLifecycle.ADDON_PENDING


@pytest.mark.parametrize("terminal_type", [OrderRejected, OrderCanceled, OrderTimedOut])
def test_addon_reject_cancel_timeout_with_zero_fill_returns_base(
    terminal_type: type[OrderRejected] | type[OrderCanceled] | type[OrderTimedOut],
) -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    common = events.envelope(
        machine,
        "addon-terminal",
        setup_id=addon.setup_id,
        client_order_id=addon.client_order_id,
    )
    if terminal_type is OrderTimedOut:
        terminal = terminal_type(
            **common,
            role=OrderRole.ADDON_ENTRY,
            deadline_at_utc=START,
            observed_status="UNKNOWN",
        )
    else:
        terminal = terminal_type(**common, role=OrderRole.ADDON_ENTRY, reason="fixture")

    machine.apply(terminal)

    assert machine.state.position_build is PositionBuild.BASE
    expected_lifecycle = (
        OrderLifecycle.ADDON_PENDING if terminal_type is OrderTimedOut else OrderLifecycle.NONE
    )
    assert machine.state.order_lifecycle is expected_lifecycle
    assert machine.state.recovery_mode is (terminal_type is OrderTimedOut)
    assert machine.state.addon_leg.quantity == ZERO


def test_addon_stop_locks_setup_and_prevents_readd() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    stop = fill_addon(machine, events, addon)
    assert isinstance(stop, SubmitAddonStop)

    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "addon-stop-fill",
                setup_id=addon.setup_id,
                client_order_id=stop.client_order_id,
            ),
            execution_id="addon-stop-exec",
            role=OrderRole.ADDON_STOP,
            last_quantity=addon.quantity,
            cumulative_quantity=addon.quantity,
            price=D("99"),
            commission=ZERO,
        )
    )

    assert machine.state.position_build is PositionBuild.BASE_LOCKED
    assert machine.state.setup is not None and machine.state.setup.add_on_lock
    later = machine.apply(
        bar_event(
            machine,
            events,
            3,
            open_="99",
            high="100",
            low="98.5",
            close="99.5",
            k="10",
            d="9",
        )
    )
    assert not any(isinstance(intent, SubmitAddonOrder) for intent in later.intents)


def test_pyramided_base_sl_finalization_is_flat_scout() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    fill_addon(machine, events, addon)

    finalized_close(machine, events, CloseReason.BASE_SL, gross="-300")

    assert machine.state.position_build is PositionBuild.FLAT
    assert machine.state.risk_mode is RiskMode.FULL
    reconcile_flat(machine, events)
    assert machine.state.risk_mode is RiskMode.SCOUT
    assert machine.state.base_leg.quantity == machine.state.addon_leg.quantity == ZERO


def test_pyramided_take_profit_finalizes_complete_setup_net_pnl() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    fill_addon(machine, events, addon)

    finalized_close(
        machine,
        events,
        CloseReason.TP,
        gross="50",
        commissions="10",
        funding="-3",
        slippage="2",
    )

    assert machine.state.position_build is PositionBuild.FLAT
    assert machine.state.risk_mode is RiskMode.FULL
    assert machine.state.pnl.setup_net_pnl == D("35")
    assert machine.state.setup is not None
    assert machine.state.setup.final_close_reason is CloseReason.TP


def test_setup_net_pnl_includes_commission_funding_and_prior_addon_loss() -> None:
    machine, events, _ = bootstrap_base(risk_mode=RiskMode.SCOUT)
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    machine.apply(
        FundingApplied(
            **events.envelope(machine, "funding", setup_id=setup_id),
            settlement_id="funding-1",
            amount=D("-2"),
        )
    )

    finalized_close(machine, events, CloseReason.TP, gross="10", commissions="8", funding="-2")

    assert machine.state.pnl.setup_net_pnl == ZERO
    assert machine.state.risk_mode is RiskMode.SCOUT


def test_scout_is_base_only_even_when_both_addon_facts_fire() -> None:
    machine, events, _ = bootstrap_base(risk_mode=RiskMode.SCOUT)

    result = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="99.5",
            high="100.5",
            low="99",
            close="100",
            k="15",
            d="14",
        )
    )

    assert not any(isinstance(intent, SubmitAddonOrder) for intent in result.intents)
    assert machine.state.position_build is PositionBuild.BASE
    assert machine.state.addon_leg.quantity == ZERO


def test_wick_stop_over_one_percent_emits_no_addon_order() -> None:
    machine, events, _ = bootstrap_base()

    result = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="99.5",
            high="100.5",
            low="98.9",
            close="100",
            k="15",
            d="14",
        )
    )

    assert not any(isinstance(intent, SubmitAddonOrder) for intent in result.intents)
    assert machine.state.position_build is PositionBuild.BASE
    assert machine.state.order_lifecycle is OrderLifecycle.NONE
    assert any("ADDON_PREVIEW_REJECTED" in item for item in machine.state.diagnostics)


def test_fill_slippage_invalidates_addon_and_emits_capped_unwind() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)

    unwind = fill_addon(machine, events, addon, price=D("100.01"))

    assert isinstance(unwind, ReduceAddon)
    assert unwind.quantity == machine.state.addon_leg.quantity
    assert machine.state.order_lifecycle is OrderLifecycle.REDUCE_PENDING
    assert machine.state.setup is not None and machine.state.setup.add_on_lock


def test_duplicate_fill_execution_is_idempotent_even_with_new_transport_id() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    quantity = addon.quantity * D("0.4")
    first = OrderPartiallyFilled(
        **events.envelope(
            machine,
            "partial",
            setup_id=addon.setup_id,
            client_order_id=addon.client_order_id,
        ),
        execution_id="same-execution",
        role=OrderRole.ADDON_ENTRY,
        last_quantity=quantity,
        cumulative_quantity=quantity,
        price=D("100"),
        commission=D("1"),
    )
    machine.apply(first)
    before = machine.snapshot_json()
    duplicate = OrderPartiallyFilled(
        **events.envelope(
            machine,
            "duplicate-transport",
            setup_id=addon.setup_id,
            client_order_id=addon.client_order_id,
        ),
        execution_id="same-execution",
        role=OrderRole.ADDON_ENTRY,
        last_quantity=quantity,
        cumulative_quantity=quantity,
        price=D("100"),
        commission=D("1"),
    )

    result = machine.apply(duplicate)

    assert result.duplicate
    assert result.intents == ()
    assert machine.snapshot_json() == before
    assert machine.state.pnl.commissions == D("1")


def test_order_submission_acceptance_and_position_confirmation_do_not_change_exposure() -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq"), equity=D("10000")))
    machine.apply(bar_event(machine, events, 0, open_="100", high="101", low="97", close="99"))
    result = machine.apply(
        bar_event(machine, events, 1, open_="99", high="100.5", low="99", close="100")
    )
    base = next(intent for intent in result.intents if isinstance(intent, SubmitBaseOrder))
    setup_id = machine.state.setup.setup_id if machine.state.setup else None

    machine.apply(
        OrderSubmitted(
            **events.envelope(
                machine,
                "submitted",
                setup_id=setup_id,
                client_order_id=base.client_order_id,
            ),
            intent_id=base.intent_id,
            role=OrderRole.BASE_ENTRY,
            requested_quantity=base.quantity,
            side=Side.LONG,
            reduce_only=False,
            close_position=False,
        )
    )
    machine.apply(
        OrderAccepted(
            **events.envelope(
                machine,
                "accepted",
                setup_id=setup_id,
                client_order_id=base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
        )
    )
    machine.apply(
        PositionChanged(
            **events.envelope(machine, "position", setup_id=setup_id),
            signed_quantity=ZERO,
            average_price=None,
        )
    )

    assert machine.state.position_build is PositionBuild.FLAT
    assert machine.state.real_open_quantity == ZERO


def test_risk_limit_emits_close_without_changing_risk_mode() -> None:
    machine, events, _ = bootstrap_base(risk_mode=RiskMode.SCOUT)

    result = machine.apply(
        RiskLimitTriggered(
            **events.envelope(machine, "risk-limit"),
            limit_id="daily-dd",
            observed_equity=D("9900"),
            observed_exposure=D("1000"),
            reason="daily drawdown",
        )
    )

    assert machine.state.risk_mode is RiskMode.SCOUT
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert any(intent.kind.value == "CloseAll" for intent in result.intents)


def test_manual_close_request_is_typed_idempotent_and_preserves_risk_mode() -> None:
    machine, events, _ = bootstrap_base(risk_mode=RiskMode.SCOUT)
    request = CloseRequested(
        **events.envelope(machine, "manual-close"),
        close_reason=CloseReason.MANUAL,
        reason="end of smoke window",
    )

    result = machine.apply(request)
    duplicate = machine.apply(request)

    assert machine.state.risk_mode is RiskMode.SCOUT
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert any(intent.kind.value == "CloseAll" for intent in result.intents)
    assert duplicate.duplicate and duplicate.intents == ()


def test_every_consumed_event_type_is_short_circuited_by_duplicate_event_id() -> None:
    machine = MastermindStateMachine(config())
    base = {
        "strategy_id": machine.config.strategy_id,
        "instrument_id": machine.config.instrument_id,
        "occurred_at_utc": START,
        "source": "duplicate-fixture",
        "source_sequence": 1,
    }
    events_to_replay = [
        AccountEquityUpdated(**base, event_id="dup-equity", equity=D("1")),
        bar_event(machine, EventFactory(), 0, open_="100", high="101", low="99", close="100"),
        OrderSubmitted(
            **base,
            event_id="dup-submitted",
            client_order_id="client",
            intent_id="intent",
            role=OrderRole.BASE_ENTRY,
            requested_quantity=D("1"),
            side=Side.LONG,
            reduce_only=False,
            close_position=False,
        ),
        OrderAccepted(
            **base,
            event_id="dup-accepted",
            client_order_id="client",
            role=OrderRole.BASE_ENTRY,
        ),
        OrderRejected(
            **base,
            event_id="dup-rejected",
            client_order_id="client",
            role=OrderRole.BASE_ENTRY,
            reason="x",
        ),
        OrderCanceled(
            **base,
            event_id="dup-canceled",
            client_order_id="client",
            role=OrderRole.BASE_ENTRY,
            reason="x",
        ),
        OrderTimedOut(
            **base,
            event_id="dup-timeout",
            client_order_id="client",
            role=OrderRole.BASE_ENTRY,
            deadline_at_utc=START,
            observed_status="UNKNOWN",
        ),
        OrderPartiallyFilled(
            **base,
            event_id="dup-partial",
            client_order_id="client",
            execution_id="partial-exec",
            role=OrderRole.BASE_ENTRY,
            last_quantity=D("1"),
            cumulative_quantity=D("1"),
            price=D("1"),
        ),
        OrderFilled(
            **base,
            event_id="dup-fill",
            client_order_id="client",
            execution_id="fill-exec",
            role=OrderRole.BASE_ENTRY,
            last_quantity=D("1"),
            cumulative_quantity=D("1"),
            price=D("1"),
        ),
        PositionChanged(
            **base,
            event_id="dup-position",
            signed_quantity=ZERO,
            average_price=None,
        ),
        PositionClosed(
            **base,
            event_id="dup-closed",
            close_reason=CloseReason.MANUAL,
            realized_price_pnl=ZERO,
            commissions=ZERO,
            funding=ZERO,
            realized_slippage_cost=ZERO,
        ),
        FundingApplied(
            **base,
            event_id="dup-funding",
            settlement_id="settlement",
            amount=ZERO,
        ),
        RiskLimitTriggered(
            **base,
            event_id="dup-risk",
            limit_id="limit",
            observed_equity=ZERO,
            observed_exposure=ZERO,
            reason="x",
        ),
        CloseRequested(
            **base,
            event_id="dup-close-request",
            close_reason=CloseReason.MANUAL,
            reason="x",
        ),
        RecoverySnapshotLoaded(
            **base,
            event_id="dup-recovery",
            schema_version="mms_state/1",
            checksum="checksum",
            snapshot_id="initial",
        ),
        ReconciliationCompleted(
            **base,
            event_id="dup-reconciliation",
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=1,
        ),
    ]
    machine.state.processed_event_ids.update(
        dict.fromkeys(event.event_id for event in events_to_replay)
    )
    before = machine.snapshot_json()

    for domain_event in events_to_replay:
        result = machine.apply(domain_event)
        assert result.duplicate and result.intents == ()
        assert machine.snapshot_json() == before


def test_flat_reconciliation_cancels_orphans_then_completes() -> None:
    machine, events, _ = bootstrap_base()
    finalized_close(machine, events, CloseReason.TP, gross="10")

    result = machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "reconcile-orphan",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=("unknown-conditional",),
            as_of_sequence=99,
        )
    )

    assert any(
        isinstance(intent, CancelOrder) and intent.target_client_order_id == "unknown-conditional"
        for intent in result.intents
    )
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    reconcile_flat(machine, events)
    assert machine.state.setup is None


def test_expected_cancel_of_replaced_take_profit_preserves_normal_lifecycle() -> None:
    machine, events, _ = bootstrap_base(policy=AddonTriggerPolicy.STOCH_CROSS)
    old_tp = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.TAKE_PROFIT
    )
    replacement = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="100.5",
            high="101",
            low="99.5",
            close="100",
            upper="103",
            lower="98",
            k="9",
            d="10",
        )
    )
    assert any(intent.kind.value == "ReplaceOrder" for intent in replacement.intents)
    assert machine.state.orders[old_tp.client_order_id].status is OrderStatus.CANCEL_PENDING

    canceled = machine.apply(
        OrderCanceled(
            **events.envelope(
                machine,
                "expected-old-tp-cancel",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
                client_order_id=old_tp.client_order_id,
            ),
            role=OrderRole.TAKE_PROFIT,
            reason="replaced",
        )
    )

    assert machine.state.order_lifecycle is OrderLifecycle.NONE
    assert not any(isinstance(intent, RequestReconciliation) for intent in canceled.intents)
    machine.assert_invariants()


def test_scope_is_per_strategy_and_instrument() -> None:
    btc, btc_events, _ = bootstrap_base(instrument="BTCUSDT-PERP.BINANCE")
    finalized_close(btc, btc_events, CloseReason.BASE_SL, gross="-200")
    reconcile_flat(btc, btc_events)
    eth = MastermindStateMachine(config(instrument="ETHUSDT-PERP.BINANCE"))

    assert btc.state.risk_mode is RiskMode.SCOUT
    assert eth.state.risk_mode is RiskMode.FULL


def test_funding_settlement_duplicate_is_applied_once() -> None:
    machine, events, _ = bootstrap_base()
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    funding = FundingApplied(
        **events.envelope(machine, "funding", setup_id=setup_id),
        settlement_id="funding-settlement",
        amount=D("-3.25"),
    )

    machine.apply(funding)
    duplicate = machine.apply(funding)

    assert duplicate.duplicate
    assert machine.state.pnl.funding == D("-3.25")
    assert machine.state.counters["funding_settlements"] == 1


@pytest.mark.parametrize("ordering", list(itertools.permutations(("addon", "base"))))
def test_gap_through_both_stops_in_either_fill_order_never_reverses(
    ordering: tuple[str, str],
) -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    addon_stop = fill_addon(machine, events, addon)
    assert isinstance(addon_stop, SubmitAddonStop)
    base_stop = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.BASE_STOP
    )
    for item in ordering:
        if machine.state.real_open_quantity == ZERO:
            break
        if item == "addon":
            machine.apply(
                OrderFilled(
                    **events.envelope(
                        machine,
                        "gap-addon",
                        setup_id=addon.setup_id,
                        client_order_id=addon_stop.client_order_id,
                    ),
                    execution_id=f"gap-addon-{ordering}",
                    role=OrderRole.ADDON_STOP,
                    last_quantity=machine.state.addon_leg.quantity,
                    cumulative_quantity=addon.quantity,
                    price=D("95"),
                    commission=ZERO,
                )
            )
        else:
            quantity = machine.state.real_open_quantity
            machine.apply(
                OrderFilled(
                    **events.envelope(
                        machine,
                        "gap-base",
                        setup_id=addon.setup_id,
                        client_order_id=base_stop.client_order_id,
                    ),
                    execution_id=f"gap-base-{ordering}",
                    role=OrderRole.BASE_STOP,
                    last_quantity=quantity,
                    cumulative_quantity=quantity,
                    price=D("94"),
                    commission=ZERO,
                )
            )
        machine.assert_invariants()

    if machine.state.real_open_quantity > ZERO:
        quantity = machine.state.real_open_quantity
        machine.apply(
            OrderFilled(
                **events.envelope(
                    machine,
                    "gap-final",
                    setup_id=addon.setup_id,
                    client_order_id=base_stop.client_order_id,
                ),
                execution_id=f"gap-final-{ordering}",
                role=OrderRole.BASE_STOP,
                last_quantity=quantity,
                cumulative_quantity=quantity,
                price=D("94"),
                commission=ZERO,
            )
        )
    assert machine.state.real_open_quantity == ZERO
    assert machine.state.base_leg.quantity >= ZERO
    assert machine.state.addon_leg.quantity >= ZERO


def test_deterministic_random_valid_sequences_preserve_caps_and_quantities() -> None:
    rng = random.Random(20260713)
    for case in range(40):
        machine, events, _ = bootstrap_base()
        addon = trigger_addon(machine, events)
        fraction = D(str(rng.choice(("0.1", "0.25", "0.4", "0.5", "1"))))
        quantity = addon.quantity * fraction
        stop = fill_addon(
            machine,
            events,
            addon,
            quantity=quantity,
            terminal=fraction == D("1"),
            execution_id=f"random-entry-{case}",
        )
        assert isinstance(stop, SubmitAddonStop)
        machine.assert_invariants()
        setup = machine.state.setup
        assert setup is not None
        assert setup.base_target_notional + setup.addon_target_notional <= D("2") * D("10000")
        assert machine.state.addon_leg.quantity <= quantity
        assert machine.state.real_open_quantity == machine.state.total_logical_quantity


def test_unknown_position_drift_requests_reconciliation_and_fails_safe() -> None:
    machine, events, _ = bootstrap_base()

    result = machine.apply(
        PositionChanged(
            **events.envelope(machine, "drift"),
            signed_quantity=machine.state.real_open_quantity + D("1"),
            average_price=D("100"),
        )
    )

    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING


def test_late_reduce_only_fill_after_partial_tp_cancel_race_never_reverses() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    addon_stop = fill_addon(machine, events, addon)
    assert isinstance(addon_stop, SubmitAddonStop)
    tp = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.TAKE_PROFIT
    )

    machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "partial-whole-tp",
                setup_id=addon.setup_id,
                client_order_id=tp.client_order_id,
            ),
            execution_id="partial-whole-tp-exec",
            role=OrderRole.TAKE_PROFIT,
            last_quantity=D("50"),
            cumulative_quantity=D("50"),
            price=D("102"),
            commission=ZERO,
        )
    )
    assert machine.state.addon_leg.quantity == D("50")
    assert machine.state.orders[addon_stop.client_order_id].status.name == "CANCEL_PENDING"

    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "late-addon-stop",
                setup_id=addon.setup_id,
                client_order_id=addon_stop.client_order_id,
            ),
            execution_id="late-addon-stop-exec",
            role=OrderRole.ADDON_STOP,
            last_quantity=D("100"),
            cumulative_quantity=D("100"),
            price=D("97"),
            commission=ZERO,
        )
    )

    assert machine.state.addon_leg.quantity == ZERO
    assert machine.state.base_leg.quantity == D("50")
    assert machine.state.real_open_quantity == D("50")
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.setup is not None
    assert machine.state.setup.pending_close_reason is CloseReason.TP
    assert machine.state.invariant_violation_count == 0
    machine.assert_invariants()


def test_base_partial_timeout_keeps_actual_base_and_protection() -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq"), equity=D("10000")))
    machine.apply(bar_event(machine, events, 0, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 1, open_="99", high="100.5", low="99", close="100")
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))
    partial = base.quantity * D("0.4")
    machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "base-partial",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="base-partial",
            role=OrderRole.BASE_ENTRY,
            last_quantity=partial,
            cumulative_quantity=partial,
            price=D("100"),
            commission=ZERO,
        )
    )

    machine.apply(
        OrderTimedOut(
            **events.envelope(
                machine,
                "base-timeout",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            deadline_at_utc=START,
            observed_status="PARTIALLY_FILLED",
            cumulative_filled_quantity=partial,
        )
    )

    assert machine.state.position_build is PositionBuild.BASE
    assert machine.state.order_lifecycle is OrderLifecycle.BASE_PENDING
    assert machine.state.recovery_mode
    assert machine.state.base_leg.quantity == partial
    assert any(order.role is OrderRole.BASE_STOP for order in machine.state.orders.values())


def test_addon_partial_timeout_keeps_actual_addon_and_enters_recovery() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    partial = addon.quantity * D("0.4")
    stop = fill_addon(
        machine,
        events,
        addon,
        quantity=partial,
        terminal=False,
    )
    assert isinstance(stop, SubmitAddonStop)

    result = machine.apply(
        OrderTimedOut(
            **events.envelope(
                machine,
                "addon-partial-timeout",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            role=OrderRole.ADDON_ENTRY,
            deadline_at_utc=START,
            observed_status="PARTIALLY_FILLED",
            cumulative_filled_quantity=partial,
        )
    )

    assert machine.state.position_build is PositionBuild.PYRAMIDED
    assert machine.state.addon_leg.quantity == partial
    assert machine.state.order_lifecycle is OrderLifecycle.ADDON_PENDING
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)


@pytest.mark.parametrize("terminal_type", [OrderRejected, OrderCanceled])
def test_addon_terminal_after_partial_fill_keeps_only_real_exposure(
    terminal_type: type[OrderRejected] | type[OrderCanceled],
) -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    partial = addon.quantity * D("0.4")
    fill_addon(machine, events, addon, quantity=partial, terminal=False)

    machine.apply(
        terminal_type(
            **events.envelope(
                machine,
                "addon-partial-terminal",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            role=OrderRole.ADDON_ENTRY,
            reason="terminal remainder",
            cumulative_filled_quantity=partial,
        )
    )

    assert machine.state.position_build is PositionBuild.PYRAMIDED
    assert machine.state.addon_leg.quantity == partial
    assert machine.state.order_lifecycle is OrderLifecycle.NONE
    active_stop_quantity = sum(
        (
            order.remaining_quantity
            for order in machine.state.orders.values()
            if order.role is OrderRole.ADDON_STOP and order.status.active
        ),
        start=ZERO,
    )
    assert active_stop_quantity == partial


def test_submitted_addon_rejection_does_not_create_virtual_exposure() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    machine.apply(
        OrderSubmitted(
            **events.envelope(
                machine,
                "addon-submitted",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            intent_id=addon.intent_id,
            role=OrderRole.ADDON_ENTRY,
            requested_quantity=addon.quantity,
            side=Side.LONG,
            reduce_only=False,
            close_position=False,
        )
    )
    machine.apply(
        OrderRejected(
            **events.envelope(
                machine,
                "addon-rejected",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            role=OrderRole.ADDON_ENTRY,
            reason="venue reject",
        )
    )

    assert machine.state.addon_leg.quantity == ZERO
    assert machine.state.position_build is PositionBuild.BASE


def test_addon_disabled_ablation_reserves_zero_and_emits_no_trigger_or_order() -> None:
    machine, events, _ = bootstrap_base(addon_enabled=False)
    assert machine.state.setup is not None
    assert machine.state.setup.addon_target_notional == ZERO

    result = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="99.5",
            high="100.5",
            low="99",
            close="100",
            k="15",
            d="14",
        )
    )

    assert not any(isinstance(intent, SubmitAddonOrder) for intent in result.intents)
    assert machine.state.counters.get("addon_trigger_facts", 0) == 0
    machine.assert_invariants()


def test_sequential_disabled_ablation_stays_full_and_rejects_initial_scout() -> None:
    with pytest.raises(ValueError, match="cannot start in SCOUT"):
        MastermindStateMachine(config(sequential_enabled=False), initial_risk_mode=RiskMode.SCOUT)
    machine, events, _ = bootstrap_base(sequential_enabled=False)

    finalized_close(machine, events, CloseReason.BASE_SL, gross="-200")

    assert machine.state.risk_mode is RiskMode.FULL
    assert machine.state.counters.get("full_to_scout_transitions", 0) == 0


def test_oversize_entry_fill_is_fail_closed_without_exposure_increase() -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq"), equity=D("10000")))
    machine.apply(bar_event(machine, events, 0, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 1, open_="99", high="100.5", low="99", close="100")
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))
    oversize = base.quantity + D("0.001")

    result = machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "oversize-fill",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="oversize-exec",
            role=OrderRole.BASE_ENTRY,
            last_quantity=oversize,
            cumulative_quantity=oversize,
            price=D("100"),
            commission=ZERO,
        )
    )

    assert machine.state.real_open_quantity == ZERO
    assert machine.state.invariant_violation_count == 1
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    assert close.quantity == oversize
    assert machine.state.orders[base.client_order_id].status is OrderStatus.CANCEL_PENDING
    assert not any(isinstance(intent, SubmitBaseOrder) for intent in machine.state.outbox)


def test_zero_fill_base_timeout_preserves_setup_and_requires_reconciliation() -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq"), equity=D("10000")))
    machine.apply(bar_event(machine, events, 0, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 1, open_="99", high="100.5", low="99", close="100")
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))

    result = machine.apply(
        OrderTimedOut(
            **events.envelope(
                machine,
                "base-timeout-zero",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            deadline_at_utc=START,
            observed_status="UNKNOWN",
        )
    )

    assert machine.state.setup is not None
    assert machine.state.order_lifecycle is OrderLifecycle.BASE_PENDING
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)


def test_new_setup_resets_virtual_leg_vwap_stops_and_fill_ids() -> None:
    machine, events, _ = bootstrap_base()
    assert machine.state.base_leg.fill_execution_ids == {"base-exec"}
    machine.apply(
        FundingApplied(
            **events.envelope(
                machine,
                "funding-before-next-setup",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            settlement_id="funding-global-dedupe",
            amount=D("-1"),
        )
    )
    finalized_close(machine, events, CloseReason.TP, gross="10")
    reconcile_flat(machine, events)
    machine.apply(bar_event(machine, events, 3, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 4, open_="99", high="100.5", low="99", close="100")
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))

    assert machine.state.base_leg.fill_vwap is None
    assert machine.state.base_leg.stop_level is None
    assert machine.state.base_leg.fill_execution_ids == set()
    assert "funding-global-dedupe" in machine.state.pnl.funding_settlement_ids
    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "base-fill-second",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="base-exec-second",
            role=OrderRole.BASE_ENTRY,
            last_quantity=base.quantity,
            cumulative_quantity=base.quantity,
            price=D("100"),
            commission=ZERO,
        )
    )
    assert machine.state.base_leg.fill_execution_ids == {"base-exec-second"}


def test_sizing_uses_immutable_setup_equity_and_rounds_down_to_step() -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq"), equity=D("10000")))
    machine.apply(
        bar_event(
            machine,
            events,
            0,
            open_="50000",
            high="50500",
            low="48000",
            close="49500",
            upper="51000",
            lower="49000",
        )
    )
    reaction = machine.apply(
        bar_event(
            machine,
            events,
            1,
            open_="49500",
            high="50100",
            low="39600",
            close="50000",
            upper="51000",
            lower="49000",
            k="10",
            d="12",
        )
    )
    base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))
    assert base.quantity == D("0.2")
    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "sizing-base-fill",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="sizing-base-exec",
            role=OrderRole.BASE_ENTRY,
            last_quantity=base.quantity,
            cumulative_quantity=base.quantity,
            price=D("50000"),
            commission=ZERO,
        )
    )
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq-later"), equity=D("20000")))

    addon_result = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="39900",
            high="40100",
            low="39600",
            close="40000",
            upper="51000",
            lower="39000",
            k="15",
            d="14",
        )
    )
    addon = next(intent for intent in addon_result.intents if isinstance(intent, SubmitAddonOrder))

    assert addon.target_notional == D("10000")
    assert addon.quantity == D("0.25")
    assert machine.state.setup is not None
    assert machine.state.setup.setup_start_equity == D("10000")


def _pending_base(
    *,
    risk_mode: RiskMode = RiskMode.FULL,
) -> tuple[MastermindStateMachine, EventFactory, SubmitBaseOrder]:
    machine = MastermindStateMachine(config(), initial_risk_mode=risk_mode)
    events = EventFactory()
    machine.apply(AccountEquityUpdated(**events.envelope(machine, "eq"), equity=D("10000")))
    machine.apply(bar_event(machine, events, 0, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 1, open_="99", high="100.5", low="99", close="100")
    )
    return (
        machine,
        events,
        next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder)),
    )


def _reconcile_matching_position(
    machine: MastermindStateMachine,
    events: EventFactory,
) -> None:
    setup = machine.state.setup
    signed_quantity = ZERO if setup is None else machine.state.real_open_quantity * setup.side.sign
    open_ids = tuple(
        sorted(
            order.client_order_id for order in machine.state.orders.values() if order.status.active
        )
    )
    machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "matching-reconciliation",
                setup_id=None if setup is None else setup.setup_id,
            ),
            signed_open_quantity=signed_quantity,
            average_price=None if signed_quantity == ZERO else D("100"),
            open_client_order_ids=open_ids,
            as_of_sequence=max(
                events.seq,
                (machine.state.last_reconciliation_sequence or 0) + 1,
            ),
        )
    )


def test_zero_and_partial_entry_timeouts_resolve_only_after_reconciliation() -> None:
    zero_machine, zero_events, zero_base = _pending_base()
    zero_machine.apply(
        OrderTimedOut(
            **zero_events.envelope(
                zero_machine,
                "zero-base-timeout",
                setup_id=zero_base.setup_id,
                client_order_id=zero_base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            deadline_at_utc=START,
            observed_status="UNKNOWN",
        )
    )
    _reconcile_matching_position(zero_machine, zero_events)
    assert zero_machine.state.setup is None
    assert zero_machine.state.order_lifecycle is OrderLifecycle.NONE
    assert not zero_machine.state.recovery_mode

    partial_machine, partial_events, partial_base = _pending_base()
    partial = partial_base.quantity * D("0.4")
    partial_machine.apply(
        OrderPartiallyFilled(
            **partial_events.envelope(
                partial_machine,
                "partial-base-fill",
                setup_id=partial_base.setup_id,
                client_order_id=partial_base.client_order_id,
            ),
            execution_id="partial-base-execution",
            role=OrderRole.BASE_ENTRY,
            last_quantity=partial,
            cumulative_quantity=partial,
            price=D("100"),
        )
    )
    partial_machine.apply(
        OrderTimedOut(
            **partial_events.envelope(
                partial_machine,
                "partial-base-timeout",
                setup_id=partial_base.setup_id,
                client_order_id=partial_base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            deadline_at_utc=START,
            observed_status="PARTIALLY_FILLED",
            cumulative_filled_quantity=partial,
        )
    )
    _reconcile_matching_position(partial_machine, partial_events)
    assert partial_machine.state.position_build is PositionBuild.BASE
    assert partial_machine.state.base_leg.quantity == partial
    assert partial_machine.state.order_lifecycle is OrderLifecycle.NONE
    assert not partial_machine.state.recovery_mode


def test_zero_and_partial_addon_timeouts_reconcile_to_real_inventory() -> None:
    zero_machine, zero_events, _ = bootstrap_base(policy=AddonTriggerPolicy.STOCH_CROSS)
    zero_addon = trigger_addon(zero_machine, zero_events)
    zero_machine.apply(
        OrderTimedOut(
            **zero_events.envelope(
                zero_machine,
                "zero-addon-timeout",
                setup_id=zero_addon.setup_id,
                client_order_id=zero_addon.client_order_id,
            ),
            role=OrderRole.ADDON_ENTRY,
            deadline_at_utc=START,
            observed_status="UNKNOWN",
        )
    )
    _reconcile_matching_position(zero_machine, zero_events)
    assert zero_machine.state.position_build is PositionBuild.BASE
    assert zero_machine.state.order_lifecycle is OrderLifecycle.NONE
    assert not zero_machine.state.recovery_mode
    assert zero_machine.state.setup is not None
    assert not zero_machine.state.setup.addon_opportunity_consumed

    partial_machine, partial_events, _ = bootstrap_base()
    partial_addon = trigger_addon(partial_machine, partial_events)
    partial = partial_addon.quantity * D("0.4")
    fill_addon(
        partial_machine,
        partial_events,
        partial_addon,
        quantity=partial,
        terminal=False,
        execution_id="partial-addon-execution",
    )
    partial_machine.apply(
        OrderTimedOut(
            **partial_events.envelope(
                partial_machine,
                "partial-addon-timeout-resolve",
                setup_id=partial_addon.setup_id,
                client_order_id=partial_addon.client_order_id,
            ),
            role=OrderRole.ADDON_ENTRY,
            deadline_at_utc=START,
            observed_status="PARTIALLY_FILLED",
            cumulative_filled_quantity=partial,
        )
    )
    _reconcile_matching_position(partial_machine, partial_events)
    assert partial_machine.state.position_build is PositionBuild.PYRAMIDED
    assert partial_machine.state.addon_leg.quantity == partial
    assert partial_machine.state.order_lifecycle is OrderLifecycle.NONE
    assert not partial_machine.state.recovery_mode


def test_restart_replays_same_unacknowledged_base_intent_when_venue_is_empty() -> None:
    machine, _, base = _pending_base()
    raw = machine.snapshot_json()
    checksum = str(json.loads(raw)["checksum"])
    restored = MastermindStateMachine.from_snapshot(machine.config, raw)
    restored.apply(
        RecoverySnapshotLoaded(
            event_id="restore-attestation",
            strategy_id=restored.config.strategy_id,
            instrument_id=restored.config.instrument_id,
            occurred_at_utc=START + timedelta(days=1),
            source="restart",
            source_sequence=1,
            setup_id=base.setup_id,
            schema_version="mms_state/1",
            checksum=checksum,
            snapshot_id=restored.state.snapshot_id,
        )
    )

    result = restored.apply(
        ReconciliationCompleted(
            event_id="restore-empty-venue",
            strategy_id=restored.config.strategy_id,
            instrument_id=restored.config.instrument_id,
            occurred_at_utc=START + timedelta(days=1, minutes=1),
            source="restart",
            source_sequence=2,
            setup_id=base.setup_id,
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=2,
        )
    )

    replay = next(intent for intent in result.intents if isinstance(intent, SubmitBaseOrder))
    assert replay.intent_id == base.intent_id
    assert replay.client_order_id == base.client_order_id
    assert restored.state.setup is not None
    assert restored.state.order_lifecycle is OrderLifecycle.BASE_PENDING
    assert not restored.state.recovery_mode


def test_fill_implicitly_acknowledges_submit_and_final_reconciliation_drains_outbox() -> None:
    machine, events, base = bootstrap_base()
    assert all(intent.intent_id != base.intent_id for intent in machine.state.outbox)

    finalized_close(machine, events, CloseReason.TP, gross="10")
    reconcile_flat(machine, events)

    assert machine.state.outbox == []
    assert machine.state.setup is None


@pytest.mark.parametrize(
    ("event_type", "expected_side"),
    [("position", Side.LONG), ("reconciliation", Side.LONG)],
)
def test_opposite_signed_drift_closes_the_actual_venue_side(
    event_type: str,
    expected_side: Side,
) -> None:
    machine, events, _ = bootstrap_base()
    if event_type == "position":
        result = machine.apply(
            PositionChanged(
                **events.envelope(machine, "opposite-position"),
                signed_quantity=D("-2"),
                average_price=D("100"),
            )
        )
        assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    else:
        result = machine.apply(
            ReconciliationCompleted(
                **events.envelope(
                    machine,
                    "opposite-reconciliation",
                    setup_id=machine.state.setup.setup_id if machine.state.setup else None,
                ),
                signed_open_quantity=D("-2"),
                average_price=D("100"),
                open_client_order_ids=(),
                as_of_sequence=1,
            )
        )
    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    assert close.side is expected_side
    assert close.quantity == D("2")
    assert all(
        order.role is OrderRole.CLOSE_ALL
        for order in machine.state.orders.values()
        if order.status.active
    )
    assert not any(
        isinstance(intent, (SubmitBaseOrder, SubmitAddonOrder, SubmitAddonStop))
        for intent in machine.state.outbox
    )


def test_position_closed_is_idempotent_by_payload_and_conflicts_fail_closed() -> None:
    machine, events, _ = bootstrap_base()
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    first = PositionClosed(
        **events.envelope(machine, "close-first", setup_id=setup_id),
        close_reason=CloseReason.TP,
        realized_price_pnl=D("10"),
        commissions=D("1"),
        funding=D("-1"),
        realized_slippage_cost=D("0.5"),
        closing_execution_ids=("close-b", "close-a"),
    )
    machine.apply(first)
    before_count = machine.state.invariant_violation_count
    machine.apply(
        PositionClosed(
            **events.envelope(machine, "close-equivalent", setup_id=setup_id),
            close_reason=CloseReason.TP,
            realized_price_pnl=D("10"),
            commissions=D("1"),
            funding=D("-1"),
            realized_slippage_cost=D("0.5"),
            closing_execution_ids=("close-a", "close-b"),
        )
    )
    assert machine.state.invariant_violation_count == before_count
    assert machine.state.setup is not None
    assert machine.state.setup.final_close_reason is CloseReason.TP

    conflict = machine.apply(
        PositionClosed(
            **events.envelope(machine, "close-conflict", setup_id=setup_id),
            close_reason=CloseReason.BASE_SL,
            realized_price_pnl=D("-10"),
            commissions=D("1"),
            funding=D("-1"),
            realized_slippage_cost=D("0.5"),
        )
    )
    assert machine.state.invariant_violation_count == before_count + 1
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in conflict.intents)
    assert machine.state.setup is not None
    assert machine.state.setup.final_close_reason is CloseReason.TP


def test_unknown_entry_fill_preserves_ledger_and_waits_for_signed_reconciliation() -> None:
    machine, events, _ = bootstrap_base()
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    before_quantity = machine.state.real_open_quantity

    result = machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "unknown-addon-fill",
                setup_id=setup_id,
                client_order_id="unknown-addon-client",
            ),
            execution_id="unknown-addon-execution",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=D("1"),
            cumulative_quantity=D("1"),
            price=D("100"),
        )
    )

    assert machine.state.real_open_quantity == before_quantity
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    assert not any(isinstance(intent, CloseAll) for intent in result.intents)


def test_adverse_fill_price_does_not_turn_target_cap_into_actual_notional_cap() -> None:
    machine, events, base = _pending_base()
    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "slipped-base-fill",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="slipped-base-execution",
            role=OrderRole.BASE_ENTRY,
            last_quantity=base.quantity,
            cumulative_quantity=base.quantity,
            price=D("101"),
        )
    )

    result = machine.apply(
        bar_event(
            machine,
            events,
            2,
            open_="99.5",
            high="100.5",
            low="99",
            close="100",
            k="15",
            d="14",
        )
    )

    addon = next(intent for intent in result.intents if isinstance(intent, SubmitAddonOrder))
    assert addon.target_notional == D("10000")
    assert machine.state.order_lifecycle is OrderLifecycle.ADDON_PENDING
    assert machine.state.telemetry["max_actual_gross_exposure_multiplier"] > D("1")


def test_late_submitted_callback_never_regresses_a_filled_order() -> None:
    machine, events, base = bootstrap_base()
    assert machine.state.orders[base.client_order_id].status is OrderStatus.FILLED

    machine.apply(
        OrderSubmitted(
            **events.envelope(
                machine,
                "late-submitted",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            intent_id=base.intent_id,
            role=OrderRole.BASE_ENTRY,
            requested_quantity=base.quantity,
            side=base.side,
            reduce_only=False,
            close_position=False,
            venue_order_id="venue-late",
        )
    )

    assert machine.state.orders[base.client_order_id].status is OrderStatus.FILLED


@pytest.mark.parametrize(
    "invalid",
    ["nan-stoch", "out-of-range-stoch", "early-occurrence", "wrong-duration"],
)
def test_invalid_bar_decimal_or_timestamp_is_transactionally_rejected(invalid: str) -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    valid = bar_event(machine, events, 0, open_="100", high="101", low="97", close="99")
    if invalid == "nan-stoch":
        malformed = replace(valid, stoch_k=D("NaN"))
    elif invalid == "out-of-range-stoch":
        malformed = replace(valid, previous_stoch_d=D("101"))
    elif invalid == "early-occurrence":
        malformed = replace(
            valid,
            occurred_at_utc=valid.close_time_utc - timedelta(seconds=1),
        )
    else:
        malformed = replace(
            valid,
            close_time_utc=valid.close_time_utc - timedelta(minutes=1),
        )
    before = machine.snapshot_json()

    with pytest.raises(ValueError):
        machine.apply(malformed)

    assert machine.snapshot_json() == before


def test_nonfinite_position_average_is_transactionally_rejected() -> None:
    machine, events, _ = bootstrap_base()
    before = machine.snapshot_json()

    with pytest.raises(ValueError):
        machine.apply(
            PositionChanged(
                **events.envelope(machine, "nan-average"),
                signed_quantity=machine.state.real_open_quantity,
                average_price=D("NaN"),
            )
        )

    assert machine.snapshot_json() == before


def test_rich_reconciliation_acknowledges_open_orders_and_promotes_status() -> None:
    machine, events, _ = bootstrap_base()
    active = tuple(order for order in machine.state.orders.values() if order.status.active)
    details = tuple(
        ReconciledOrder(
            client_order_id=order.client_order_id,
            venue_order_id=f"venue-{index}",
            role=order.role,
            status=OrderStatus.ACCEPTED,
            requested_quantity=order.requested_quantity,
            filled_quantity=order.filled_quantity,
            side=order.side,
            reduce_only=order.reduce_only,
            close_position=order.close_position,
            setup_id=order.setup_id,
        )
        for index, order in enumerate(active)
    )

    machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "rich-reconciliation",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            signed_open_quantity=machine.state.real_open_quantity,
            average_price=D("100"),
            open_client_order_ids=tuple(order.client_order_id for order in active),
            as_of_sequence=1,
            open_orders=details,
        )
    )

    assert all(
        machine.state.orders[order.client_order_id].status is OrderStatus.ACCEPTED
        for order in active
    )
    active_ids = {order.client_order_id for order in active}
    assert all(
        getattr(intent, "client_order_id", None) not in active_ids
        for intent in machine.state.outbox
    )


def test_conflicting_rich_reconciliation_fails_closed_without_ledger_mutation() -> None:
    machine, events, _ = bootstrap_base()
    order = next(order for order in machine.state.orders.values() if order.status.active)
    bad = ReconciledOrder(
        client_order_id=order.client_order_id,
        venue_order_id="venue-conflict",
        role=order.role,
        status=OrderStatus.ACCEPTED,
        requested_quantity=order.requested_quantity + D("1"),
        filled_quantity=order.filled_quantity,
        side=order.side,
        reduce_only=order.reduce_only,
        close_position=order.close_position,
        setup_id=order.setup_id,
    )
    before_requested = order.requested_quantity

    machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "rich-conflict",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            signed_open_quantity=machine.state.real_open_quantity,
            average_price=D("100"),
            open_client_order_ids=(order.client_order_id,),
            as_of_sequence=1,
            open_orders=(bad,),
        )
    )

    assert machine.state.invariant_violation_count == 1
    assert machine.state.recovery_mode
    assert machine.state.orders[order.client_order_id].requested_quantity == before_requested


def test_late_old_setup_terminal_is_harmless_but_fill_fails_safe() -> None:
    machine, events, old_base = bootstrap_base()
    old_setup_id = old_base.setup_id
    finalized_close(machine, events, CloseReason.TP, gross="10")
    reconcile_flat(machine, events)
    machine.apply(bar_event(machine, events, 3, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 4, open_="99", high="100.5", low="99", close="100")
    )
    new_base = next(intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder))

    machine.apply(
        OrderCanceled(
            **events.envelope(
                machine,
                "late-old-cancel",
                setup_id=old_setup_id,
                client_order_id=old_base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            reason="late terminal",
            cumulative_filled_quantity=old_base.quantity,
        )
    )
    assert machine.state.setup is not None
    assert machine.state.setup.setup_id == new_base.setup_id
    assert machine.state.order_lifecycle is OrderLifecycle.BASE_PENDING

    late_fill = machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "late-old-fill",
                setup_id=old_setup_id,
                client_order_id=old_base.client_order_id,
            ),
            execution_id="late-old-execution",
            role=OrderRole.BASE_ENTRY,
            last_quantity=D("1"),
            cumulative_quantity=old_base.quantity + D("1"),
            price=D("100"),
        )
    )
    assert machine.state.real_open_quantity == ZERO
    assert machine.state.recovery_mode
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert any(isinstance(intent, RequestReconciliation) for intent in late_fill.intents)
    assert any(isinstance(intent, CloseAll) for intent in late_fill.intents)


def test_late_nonzero_funding_after_final_reconciliation_fails_closed() -> None:
    machine, events, _ = bootstrap_base(risk_mode=RiskMode.SCOUT)
    old_setup_id = machine.state.setup.setup_id if machine.state.setup else None
    finalized_close(machine, events, CloseReason.TP, gross="2", commissions="1")
    reconcile_flat(machine, events)
    assert machine.state.risk_mode is RiskMode.FULL
    before_funding = machine.state.pnl.funding

    result = machine.apply(
        FundingApplied(
            **events.envelope(machine, "late-funding", setup_id=old_setup_id),
            settlement_id="late-after-watermark",
            amount=D("-5"),
        )
    )

    assert machine.state.recovery_mode
    assert machine.state.risk_mode is RiskMode.FULL
    assert machine.state.pnl.funding == before_funding
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)

    second = machine.apply(
        ReconciliationCompleted(
            **events.envelope(machine, "late-funding-reconciliation"),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=(machine.state.last_reconciliation_sequence or 0) + 1,
        )
    )
    assert machine.state.recovery_mode
    assert machine.state.unresolved_funding_settlement_ids == {"late-after-watermark"}
    assert any(isinstance(intent, RequestReconciliation) for intent in second.intents)


def test_recovery_view_preserves_per_execution_remaining_quantities_across_restart() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    first_quantity = D("40")
    first_stop = fill_addon(
        machine,
        events,
        addon,
        quantity=first_quantity,
        terminal=False,
        execution_id="addon-fill-one",
    )
    assert isinstance(first_stop, SubmitAddonStop)
    second_quantity = D("30")
    second_result = machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "addon-fill-two",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            execution_id="addon-fill-two",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=second_quantity,
            cumulative_quantity=first_quantity + second_quantity,
            price=D("100"),
        )
    )
    second_stop = next(
        intent for intent in second_result.intents if isinstance(intent, SubmitAddonStop)
    )
    machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "partial-addon-reduction",
                setup_id=addon.setup_id,
                client_order_id=first_stop.client_order_id,
            ),
            execution_id="addon-stop-partial",
            role=OrderRole.ADDON_STOP,
            last_quantity=D("20"),
            cumulative_quantity=D("20"),
            price=D("99"),
        )
    )

    view = machine.recovery_view
    addon_fills = {
        fill.execution_id: (fill.original_quantity, fill.remaining_quantity)
        for fill in view.entry_fills
        if fill.role is OrderRole.ADDON_ENTRY
    }
    assert addon_fills == {
        "addon-fill-one": (D("40"), D("20")),
        "addon-fill-two": (D("30"), D("30")),
    }
    protected = {
        order.client_order_id: order.protected_execution_id
        for order in view.orders
        if order.role is OrderRole.ADDON_STOP
    }
    assert protected[first_stop.client_order_id] == "addon-fill-one"
    assert protected[second_stop.client_order_id] == "addon-fill-two"

    restored = MastermindStateMachine.from_snapshot(machine.config, machine.snapshot_json())
    assert restored.recovery_view == view


def test_stale_reconciliation_cannot_finalize_a_new_setup() -> None:
    machine, events, base = _pending_base()

    result = machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "stale-old-setup-reconciliation",
                setup_id="superseded-setup",
            ),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=1,
        )
    )

    assert machine.state.setup is not None
    assert machine.state.setup.setup_id == base.setup_id
    assert machine.state.order_lifecycle is OrderLifecycle.BASE_PENDING
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    assert not any(isinstance(intent, CloseAll) for intent in result.intents)


def test_second_drift_replaces_close_when_actual_sign_and_quantity_change() -> None:
    machine, events, _ = bootstrap_base()
    first = machine.apply(
        PositionChanged(
            **events.envelope(machine, "first-opposite-drift"),
            signed_quantity=D("-2"),
            average_price=D("100"),
        )
    )
    first_close = next(intent for intent in first.intents if isinstance(intent, CloseAll))

    second = machine.apply(
        PositionChanged(
            **events.envelope(machine, "second-opposite-drift"),
            signed_quantity=D("3"),
            average_price=D("100"),
        )
    )
    second_close = next(intent for intent in second.intents if isinstance(intent, CloseAll))

    assert first_close.side is Side.LONG and first_close.quantity == D("2")
    assert second_close.side is Side.SHORT and second_close.quantity == D("3")
    assert second_close.client_order_id != first_close.client_order_id
    assert machine.state.orders[first_close.client_order_id].status is OrderStatus.CANCEL_PENDING
    assert any(
        isinstance(intent, CancelOrder)
        and intent.target_client_order_id == first_close.client_order_id
        for intent in second.intents
    )


def test_historical_timeout_cannot_clear_a_later_manual_exit() -> None:
    machine, events, base = _pending_base()
    partial = base.quantity * D("0.4")
    machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "historical-timeout-fill",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="historical-timeout-execution",
            role=OrderRole.BASE_ENTRY,
            last_quantity=partial,
            cumulative_quantity=partial,
            price=D("100"),
        )
    )
    machine.apply(
        OrderTimedOut(
            **events.envelope(
                machine,
                "historical-timeout",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            deadline_at_utc=START,
            observed_status="PARTIALLY_FILLED",
            cumulative_filled_quantity=partial,
        )
    )
    _reconcile_matching_position(machine, events)
    assert machine.state.order_lifecycle is OrderLifecycle.NONE
    machine.apply(
        CloseRequested(
            **events.envelope(
                machine,
                "manual-after-timeout",
                setup_id=base.setup_id,
            ),
            close_reason=CloseReason.MANUAL,
            reason="operator",
        )
    )

    _reconcile_matching_position(machine, events)

    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.setup is not None
    assert machine.state.setup.pending_close_reason is CloseReason.MANUAL


def test_position_mismatch_cancels_unknown_and_known_live_orders_before_close() -> None:
    machine, events, _ = bootstrap_base()

    result = machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "mismatch-with-orphan",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            signed_open_quantity=D("-2"),
            average_price=D("100"),
            open_client_order_ids=("unknown-live-order",),
            as_of_sequence=1,
        )
    )

    assert any(
        isinstance(intent, CancelOrder) and intent.target_client_order_id == "unknown-live-order"
        for intent in result.intents
    )
    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    assert close.side is Side.LONG and close.quantity == D("2")
    assert all(
        order.role is OrderRole.CLOSE_ALL
        for order in machine.state.orders.values()
        if order.status.active
    )


def test_accepted_missing_protection_uses_bounded_fail_safe_exit() -> None:
    machine, events, _ = bootstrap_base()
    stop = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.BASE_STOP
    )
    machine.apply(
        OrderAccepted(
            **events.envelope(
                machine,
                "accept-base-stop",
                setup_id=stop.setup_id,
                client_order_id=stop.client_order_id,
            ),
            role=OrderRole.BASE_STOP,
            venue_order_id="venue-base-stop",
        )
    )
    other_open_ids = tuple(
        order.client_order_id
        for order in machine.state.orders.values()
        if order.status.active and order.client_order_id != stop.client_order_id
    )

    result = machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "missing-accepted-stop",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            signed_open_quantity=machine.state.real_open_quantity,
            average_price=D("100"),
            open_client_order_ids=other_open_ids,
            as_of_sequence=1,
        )
    )

    assert machine.state.orders[stop.client_order_id].status is OrderStatus.CANCELED
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.recovery_mode
    assert any(isinstance(intent, CloseAll) for intent in result.intents)
    assert not any(
        order.role.is_protective for order in machine.state.orders.values() if order.status.active
    )


def test_bare_open_ids_do_not_ack_or_destroy_replayable_intent() -> None:
    machine, events, base = _pending_base()

    machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "bare-open-id",
                setup_id=base.setup_id,
            ),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(base.client_order_id,),
            as_of_sequence=1,
        )
    )

    assert machine.state.orders[base.client_order_id].status is OrderStatus.INTENDED
    assert any(intent.intent_id == base.intent_id for intent in machine.state.outbox)


@pytest.mark.parametrize("event_type", ["position", "reconciliation"])
def test_unattributed_signed_position_is_closed_without_a_setup(event_type: str) -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    if event_type == "position":
        result = machine.apply(
            PositionChanged(
                **events.envelope(machine, "unattributed-position"),
                signed_quantity=D("-3"),
                average_price=D("100"),
            )
        )
    else:
        result = machine.apply(
            ReconciliationCompleted(
                **events.envelope(machine, "unattributed-reconciliation"),
                signed_open_quantity=D("-3"),
                average_price=D("100"),
                open_client_order_ids=(),
                as_of_sequence=1,
            )
        )

    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    assert close.setup_id is None
    assert close.side is Side.LONG
    assert close.quantity == D("3")
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.recovery_mode


def test_rich_identity_conflict_uses_trusted_actual_sign_and_cancels_reported_order() -> None:
    machine, events, _ = bootstrap_base()
    known = next(order for order in machine.state.orders.values() if order.status.active)
    conflict = ReconciledOrder(
        client_order_id=known.client_order_id,
        venue_order_id="conflicting-venue",
        role=known.role,
        status=OrderStatus.ACCEPTED,
        requested_quantity=known.requested_quantity + D("1"),
        filled_quantity=known.filled_quantity,
        side=known.side,
        reduce_only=known.reduce_only,
        close_position=known.close_position,
        setup_id=known.setup_id,
    )

    result = machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "opposite-rich-conflict",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            signed_open_quantity=D("-4"),
            average_price=D("100"),
            open_client_order_ids=(known.client_order_id, "reported-orphan"),
            as_of_sequence=1,
            open_orders=(
                conflict,
                ReconciledOrder(
                    client_order_id="reported-orphan",
                    venue_order_id="orphan-venue",
                    role=OrderRole.ADDON_ENTRY,
                    status=OrderStatus.ACCEPTED,
                    requested_quantity=D("1"),
                    filled_quantity=ZERO,
                    side=Side.SHORT,
                    reduce_only=False,
                    close_position=False,
                ),
            ),
        )
    )

    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    assert close.side is Side.LONG and close.quantity == D("4")
    assert any(
        isinstance(intent, CancelOrder) and intent.target_client_order_id == "reported-orphan"
        for intent in result.intents
    )


def test_fill_fifo_and_stop_child_attribution_are_durable_across_restart() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    first_quantity = D("40")
    fill_addon(
        machine,
        events,
        addon,
        quantity=first_quantity,
        terminal=False,
        execution_id="z-first-fill",
    )
    second_quantity = D("30")
    second_result = machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "a-second-fill",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            execution_id="a-second-fill",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=second_quantity,
            cumulative_quantity=first_quantity + second_quantity,
            price=D("100"),
        )
    )
    second_stop = next(
        intent for intent in second_result.intents if isinstance(intent, SubmitAddonStop)
    )
    raw = machine.snapshot_json()
    fifo_restored = MastermindStateMachine.from_snapshot(machine.config, raw)
    tp = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.TAKE_PROFIT
    )
    fifo_event = OrderPartiallyFilled(
        **events.envelope(
            machine,
            "fifo-whole-exit",
            setup_id=addon.setup_id,
            client_order_id=tp.client_order_id,
        ),
        execution_id="fifo-whole-exit",
        role=OrderRole.TAKE_PROFIT,
        last_quantity=D("20"),
        cumulative_quantity=D("20"),
        price=D("102"),
    )

    machine.apply(fifo_event)
    fifo_restored.apply(fifo_event)

    assert machine.state.addon_leg.remaining_fill_quantities == {
        "z-first-fill": D("20"),
        "a-second-fill": D("30"),
    }
    assert (
        fifo_restored.state.addon_leg.remaining_fill_quantities
        == machine.state.addon_leg.remaining_fill_quantities
    )

    attributed = MastermindStateMachine.from_snapshot(machine.config, raw)
    attributed_event = OrderPartiallyFilled(
        event_id="second-stop-first",
        strategy_id=attributed.config.strategy_id,
        instrument_id=attributed.config.instrument_id,
        occurred_at_utc=fifo_event.occurred_at_utc + timedelta(minutes=1),
        source=fifo_event.source,
        source_sequence=fifo_event.source_sequence + 1,
        setup_id=addon.setup_id,
        client_order_id=second_stop.client_order_id,
        execution_id="second-stop-first",
        role=OrderRole.ADDON_STOP,
        last_quantity=D("20"),
        cumulative_quantity=D("20"),
        price=D("99"),
    )
    attributed.apply(attributed_event)

    assert attributed.state.addon_leg.remaining_fill_quantities == {
        "z-first-fill": D("40"),
        "a-second-fill": D("10"),
    }


def test_unattributed_close_is_retired_by_flat_truth_before_next_setup() -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    drift = machine.apply(
        PositionChanged(
            **events.envelope(machine, "unattributed-before-flat"),
            signed_quantity=D("-3"),
            average_price=D("100"),
        )
    )
    close = next(intent for intent in drift.intents if isinstance(intent, CloseAll))

    machine.apply(
        ReconciliationCompleted(
            **events.envelope(machine, "unattributed-now-flat"),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=1,
        )
    )

    assert machine.state.orders[close.client_order_id].status is OrderStatus.CANCELED
    assert machine.state.order_lifecycle is OrderLifecycle.NONE
    assert not machine.state.recovery_mode
    assert machine.state.outbox == []
    machine.apply(
        AccountEquityUpdated(**events.envelope(machine, "next-equity"), equity=D("10000"))
    )
    machine.apply(bar_event(machine, events, 0, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 1, open_="99", high="100.5", low="99", close="100")
    )
    assert any(isinstance(intent, SubmitBaseOrder) for intent in reaction.intents)


def test_late_position_closed_summary_never_closes_the_current_setup() -> None:
    machine, events, old_base = bootstrap_base()
    old_setup_id = old_base.setup_id
    finalized_close(machine, events, CloseReason.TP, gross="10")
    reconcile_flat(machine, events)
    machine.apply(bar_event(machine, events, 3, open_="100", high="101", low="97", close="99"))
    reaction = machine.apply(
        bar_event(machine, events, 4, open_="99", high="100.5", low="99", close="100")
    )
    current_base = next(
        intent for intent in reaction.intents if isinstance(intent, SubmitBaseOrder)
    )
    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "current-base-fill",
                setup_id=current_base.setup_id,
                client_order_id=current_base.client_order_id,
            ),
            execution_id="current-base-execution",
            role=OrderRole.BASE_ENTRY,
            last_quantity=current_base.quantity,
            cumulative_quantity=current_base.quantity,
            price=D("100"),
        )
    )
    before_quantity = machine.state.real_open_quantity

    result = machine.apply(
        PositionClosed(
            **events.envelope(
                machine,
                "late-old-position-closed",
                setup_id=old_setup_id,
            ),
            close_reason=CloseReason.TP,
            realized_price_pnl=D("10"),
            commissions=ZERO,
            funding=ZERO,
            realized_slippage_cost=ZERO,
        )
    )

    assert machine.state.setup is not None
    assert machine.state.setup.setup_id == current_base.setup_id
    assert machine.state.setup.final_close_reason is None
    assert machine.state.real_open_quantity == before_quantity
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    assert not any(isinstance(intent, CloseAll) for intent in result.intents)


@pytest.mark.parametrize("field", ["event_id", "source", "setup_id", "source_sequence"])
def test_malformed_event_envelope_cannot_poison_a_restorable_snapshot(field: str) -> None:
    machine = MastermindStateMachine(config())
    payload: dict[str, Any] = {
        "event_id": "valid-event",
        "strategy_id": machine.config.strategy_id,
        "instrument_id": machine.config.instrument_id,
        "occurred_at_utc": START,
        "source": "valid-source",
        "source_sequence": 1,
        "equity": D("10000"),
    }
    payload[field] = 7 if field != "source_sequence" else 1.5
    before = machine.snapshot_json()

    with pytest.raises((TypeError, ValueError)):
        machine.apply(AccountEquityUpdated(**payload))  # type: ignore[arg-type]

    assert machine.snapshot_json() == before
    MastermindStateMachine.from_snapshot(machine.config, before)


@pytest.mark.parametrize("as_of_sequence", [True, 1.5])
def test_non_integer_reconciliation_sequence_is_rejected_without_snapshot_poison(
    as_of_sequence: bool | float,
) -> None:
    machine = MastermindStateMachine(config())
    events = EventFactory()
    before = machine.snapshot_json()

    with pytest.raises(TypeError):
        machine.apply(
            ReconciliationCompleted(
                **events.envelope(machine, "invalid-as-of"),
                signed_open_quantity=ZERO,
                average_price=None,
                open_client_order_ids=(),
                as_of_sequence=as_of_sequence,  # type: ignore[arg-type]
            )
        )

    assert machine.snapshot_json() == before
    MastermindStateMachine.from_snapshot(machine.config, before)


@pytest.mark.parametrize(
    "field",
    ["bb_window", "arm_expiry_bars"],
)
def test_boolean_config_integer_fields_are_rejected(field: str) -> None:
    kwargs: dict[str, Any] = {field: True}
    with pytest.raises(TypeError, match="must be integers"):
        MastermindConfig(
            strategy_id="mms-v2",
            instrument_id="BTCUSDT-PERP.BINANCE",
            addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
            **kwargs,
        )


@pytest.mark.parametrize("finalized", [False, True])
def test_entry_fill_after_close_initiation_never_reopens_or_overwrites_exit(
    finalized: bool,
) -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    machine.apply(
        CloseRequested(
            **events.envelope(
                machine,
                "close-before-addon-fill",
                setup_id=addon.setup_id,
            ),
            close_reason=CloseReason.MANUAL,
            reason="operator",
        )
    )
    if finalized:
        finalized_close(machine, events, CloseReason.MANUAL, gross="0")
    before_quantity = machine.state.real_open_quantity

    result = machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "late-addon-after-close",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            execution_id=f"late-addon-after-close-{finalized}",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=addon.quantity,
            cumulative_quantity=addon.quantity,
            price=D("100"),
        )
    )

    assert machine.state.real_open_quantity == before_quantity
    assert machine.state.addon_leg.quantity == ZERO
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.recovery_mode
    assert not any(isinstance(intent, SubmitAddonStop) for intent in result.intents)
    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    expected_actual = before_quantity + addon.quantity
    assert close.quantity == expected_actual
    if finalized:
        assert machine.state.setup is not None
        assert machine.state.setup.final_close_reason is None
        assert machine.state.setup.pending_close_reason is CloseReason.ENGINE_ERROR
        machine.apply(
            PositionClosed(
                **events.envelope(
                    machine,
                    "corrective-position-closed",
                    setup_id=addon.setup_id,
                ),
                close_reason=CloseReason.ENGINE_ERROR,
                realized_price_pnl=D("-1"),
                commissions=ZERO,
                funding=ZERO,
                realized_slippage_cost=ZERO,
                closing_execution_ids=("corrective-close-execution",),
            )
        )
        reconcile_flat(machine, events)
        assert machine.state.setup is None


def test_repeated_entry_fills_after_close_accumulate_observed_exposure() -> None:
    machine, events, base = bootstrap_base()
    addon = trigger_addon(machine, events)
    machine.apply(
        CloseRequested(
            **events.envelope(
                machine,
                "close-before-repeated-addon-fills",
                setup_id=addon.setup_id,
            ),
            close_reason=CloseReason.MANUAL,
            reason="operator",
        )
    )
    first_quantity = addon.quantity * D("0.4")
    second_quantity = addon.quantity * D("0.3")

    first = machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "first-late-addon-fill",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            execution_id="first-late-addon-execution",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=first_quantity,
            cumulative_quantity=first_quantity,
            price=D("100"),
        )
    )
    second = machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "second-late-addon-fill",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            execution_id="second-late-addon-execution",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=second_quantity,
            cumulative_quantity=first_quantity + second_quantity,
            price=D("100"),
        )
    )

    first_close = next(intent for intent in first.intents if isinstance(intent, CloseAll))
    second_close = next(intent for intent in second.intents if isinstance(intent, CloseAll))
    assert first_close.quantity == base.quantity + first_quantity
    assert second_close.quantity == base.quantity + first_quantity + second_quantity
    assert machine.state.observed_drift_signed_quantity == second_close.quantity
    assert machine.state.real_open_quantity == base.quantity
    assert machine.state.addon_leg.quantity == ZERO
    assert machine.state.recovery_mode


def test_late_entry_fill_after_final_flat_emits_unattributed_sign_safe_close() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    machine.apply(
        CloseRequested(
            **events.envelope(
                machine,
                "close-before-final-flat-late-fill",
                setup_id=addon.setup_id,
            ),
            close_reason=CloseReason.MANUAL,
            reason="operator",
        )
    )
    finalized_close(machine, events, CloseReason.MANUAL, gross="0")
    reconcile_flat(machine, events)
    assert machine.state.setup is None

    result = machine.handle_without_snapshot(
        OrderFilled(
            **events.envelope(
                machine,
                "late-addon-fill-after-final-flat",
                setup_id=addon.setup_id,
                client_order_id=addon.client_order_id,
            ),
            execution_id="late-addon-after-final-flat-execution",
            role=OrderRole.ADDON_ENTRY,
            last_quantity=addon.quantity,
            cumulative_quantity=addon.quantity,
            price=D("100"),
        )
    )

    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))
    assert close.setup_id is None
    assert close.side is Side.SHORT
    assert close.quantity == addon.quantity
    assert machine.state.observed_drift_signed_quantity == addon.quantity
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.recovery_mode
    assert machine.state.real_open_quantity == ZERO
    assert result.snapshot_json == ""


@pytest.mark.parametrize(
    ("reduce_only", "close_position"),
    [(True, False), (False, True)],
)
def test_close_all_submission_persists_safe_transport_flags(
    reduce_only: bool,
    close_position: bool,
) -> None:
    machine, events, _ = bootstrap_base()
    result = machine.apply(
        CloseRequested(
            **events.envelope(
                machine,
                "close-before-safe-submit",
                setup_id=machine.state.setup.setup_id if machine.state.setup else None,
            ),
            close_reason=CloseReason.MANUAL,
            reason="operator",
        )
    )
    close = next(intent for intent in result.intents if isinstance(intent, CloseAll))

    submitted = machine.apply(
        OrderSubmitted(
            **events.envelope(
                machine,
                "safe-close-all-submitted",
                setup_id=close.setup_id,
                client_order_id=close.client_order_id,
            ),
            intent_id=close.intent_id,
            role=OrderRole.CLOSE_ALL,
            requested_quantity=close.quantity,
            side=close.side,
            reduce_only=reduce_only,
            close_position=close_position,
        )
    )

    record = machine.state.orders[close.client_order_id]
    assert not any(
        isinstance(intent, (CloseAll, RequestReconciliation)) for intent in submitted.intents
    )
    assert record.status is OrderStatus.SUBMITTED
    assert record.reduce_only is reduce_only
    assert record.close_position is close_position
    assert machine.state.invariant_violation_count == 0


def test_late_entry_terminal_after_finalization_preserves_sequential_decision() -> None:
    machine, events, base = _pending_base()
    partial = base.quantity * D("0.4")
    machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "partial-before-finalization",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id="partial-before-finalization",
            role=OrderRole.BASE_ENTRY,
            last_quantity=partial,
            cumulative_quantity=partial,
            price=D("100"),
        )
    )
    finalized_close(machine, events, CloseReason.BASE_SL, gross="-40")
    machine.apply(
        OrderRejected(
            **events.envelope(
                machine,
                "late-reject-after-finalization",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            role=OrderRole.BASE_ENTRY,
            reason="late remainder reject",
            cumulative_filled_quantity=partial,
        )
    )

    assert machine.state.setup is not None
    assert machine.state.setup.final_close_reason is CloseReason.BASE_SL
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    reconcile_flat(machine, events)
    assert machine.state.setup is None
    assert machine.state.risk_mode is RiskMode.SCOUT


@pytest.mark.parametrize(
    ("role", "reason", "expected_risk"),
    [
        (OrderRole.TAKE_PROFIT, CloseReason.TP, RiskMode.FULL),
        (OrderRole.BASE_STOP, CloseReason.BASE_SL, RiskMode.SCOUT),
    ],
)
def test_flat_reconciliation_waits_for_out_of_order_position_closed(
    role: OrderRole,
    reason: CloseReason,
    expected_risk: RiskMode,
) -> None:
    machine, events, _ = bootstrap_base()
    exit_order = next(order for order in machine.state.orders.values() if order.role is role)
    setup_id = machine.state.setup.setup_id if machine.state.setup else None
    quantity = machine.state.real_open_quantity
    machine.apply(
        OrderFilled(
            **events.envelope(
                machine,
                "whole-exit-before-summary",
                setup_id=setup_id,
                client_order_id=exit_order.client_order_id,
            ),
            execution_id=f"whole-exit-{role.value}",
            role=role,
            last_quantity=quantity,
            cumulative_quantity=quantity,
            price=D("102") if role is OrderRole.TAKE_PROFIT else D("98"),
        )
    )

    first_reconciliation = machine.apply(
        ReconciliationCompleted(
            **events.envelope(
                machine,
                "flat-before-position-closed",
                setup_id=setup_id,
            ),
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(),
            as_of_sequence=1,
        )
    )
    assert machine.state.setup is not None
    assert machine.state.setup.final_close_reason is None
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.recovery_mode
    assert any(isinstance(intent, RequestReconciliation) for intent in first_reconciliation.intents)
    assert all(isinstance(intent, RequestReconciliation) for intent in machine.state.outbox)
    assert not any(order.status.active for order in machine.state.orders.values())

    machine.apply(
        PositionClosed(
            **events.envelope(
                machine,
                "delayed-position-closed",
                setup_id=setup_id,
            ),
            close_reason=reason,
            realized_price_pnl=D("10") if reason is CloseReason.TP else D("-200"),
            commissions=ZERO,
            funding=ZERO,
            realized_slippage_cost=ZERO,
            closing_execution_ids=(f"whole-exit-{role.value}",),
        )
    )
    reconcile_flat(machine, events)

    assert machine.state.setup is None
    assert machine.state.risk_mode is expected_risk


def test_partial_whole_exit_immediately_cancels_pending_entry_remainder() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)
    tp = next(
        order for order in machine.state.orders.values() if order.role is OrderRole.TAKE_PROFIT
    )

    result = machine.apply(
        OrderPartiallyFilled(
            **events.envelope(
                machine,
                "partial-exit-with-addon-pending",
                setup_id=addon.setup_id,
                client_order_id=tp.client_order_id,
            ),
            execution_id="partial-exit-with-addon-pending",
            role=OrderRole.TAKE_PROFIT,
            last_quantity=D("20"),
            cumulative_quantity=D("20"),
            price=D("102"),
        )
    )

    assert machine.state.orders[addon.client_order_id].status is OrderStatus.CANCEL_PENDING
    assert any(
        isinstance(intent, CancelOrder) and intent.target_client_order_id == addon.client_order_id
        for intent in result.intents
    )
    assert not any(intent.intent_id == addon.intent_id for intent in machine.state.outbox)
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING


@pytest.mark.parametrize("identifier", ["execution", "settlement"])
def test_non_string_quantity_or_pnl_identifier_cannot_poison_snapshot(
    identifier: str,
) -> None:
    machine, events, base = bootstrap_base()
    before = machine.snapshot_json()
    if identifier == "execution":
        malformed: Any = OrderFilled(
            **events.envelope(
                machine,
                "bad-execution-id",
                setup_id=base.setup_id,
                client_order_id=base.client_order_id,
            ),
            execution_id=7,
            role=OrderRole.BASE_ENTRY,
            last_quantity=D("1"),
            cumulative_quantity=base.quantity + D("1"),
            price=D("100"),
        )
    else:
        malformed = FundingApplied(
            **events.envelope(
                machine,
                "bad-settlement-id",
                setup_id=base.setup_id,
            ),
            settlement_id=7,
            amount=D("-1"),
        )

    with pytest.raises(ValueError):
        machine.apply(malformed)

    assert machine.snapshot_json() == before
    MastermindStateMachine.from_snapshot(machine.config, before)


def test_zero_actual_drift_suppresses_every_order_that_could_reopen_position() -> None:
    machine, events, _ = bootstrap_base()
    addon = trigger_addon(machine, events)

    result = machine.apply(
        PositionChanged(
            **events.envelope(machine, "venue-flat-drift"),
            signed_quantity=ZERO,
            average_price=None,
        )
    )

    assert machine.state.orders[addon.client_order_id].status is OrderStatus.CANCEL_PENDING
    assert machine.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
    assert machine.state.setup is not None
    assert machine.state.setup.pending_close_reason is CloseReason.ENGINE_ERROR
    assert not any(order.status.active for order in machine.state.orders.values())
    assert not any(
        isinstance(intent, (SubmitBaseOrder, SubmitAddonOrder, SubmitAddonStop))
        for intent in machine.state.outbox
    )
    assert any(isinstance(intent, RequestReconciliation) for intent in result.intents)
    assert not any(isinstance(intent, CloseAll) for intent in result.intents)
