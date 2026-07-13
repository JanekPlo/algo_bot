"""Versioned canonical snapshot, recovery and fail-closed fixtures for P6."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from algo_bot.strategies.mastermind.model import (
    ZERO,
    AddonTriggerPolicy,
    BarSnapshot,
    CloseAll,
    MastermindConfig,
    OrderFilled,
    OrderLifecycle,
    OrderRecord,
    OrderRole,
    OrderStatus,
    PositionBuild,
    ReconciliationCompleted,
    RecoverySnapshotLoaded,
    RiskMode,
    SetupState,
    Side,
    SubmitAddonStop,
    SubmitBaseOrder,
    TriggerKind,
    VirtualLeg,
)
from algo_bot.strategies.mastermind.snapshot import SnapshotError
from algo_bot.strategies.mastermind.state_machine import MastermindStateMachine

D = Decimal
NOW = datetime(2025, 2, 1, tzinfo=UTC)


def config() -> MastermindConfig:
    return MastermindConfig(
        strategy_id="mms-v2",
        instrument_id="BTCUSDT-PERP.BINANCE",
        addon_trigger_policy=AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH,
        quantity_step=D("0.001"),
        min_quantity=D("0.001"),
        min_notional=D("1"),
    )


def bar(index: int) -> BarSnapshot:
    opened = NOW + timedelta(hours=index)
    return BarSnapshot(
        bar_id=f"bar-{index}",
        open_time_utc=opened,
        close_time_utc=opened + timedelta(hours=1) - timedelta(milliseconds=1),
        open=D("99"),
        high=D("100.5"),
        low=D("99"),
        close=D("100"),
        volume=D("1"),
        bb_upper=D("102"),
        bb_lower=D("98"),
        stoch_k=D("15"),
        stoch_d=D("14"),
    )


def full_partial_addon_machine() -> MastermindStateMachine:
    machine = MastermindStateMachine(config())
    reaction = bar(0)
    machine.state.latest_confirmed_equity = D("10000")
    machine.state.signal.reaction_bar = reaction
    machine.state.signal.recent_bars = [reaction, bar(1)]
    machine.state.signal.confirming_candle_checked = True
    machine.state.signal.seen_trigger_ids = {"trigger-1"}
    machine.state.setup = SetupState(
        setup_id="setup-full",
        side=Side.LONG,
        reaction_bar=reaction,
        setup_start_equity=D("10000"),
        exposure_multiplier=D("1"),
        base_target_notional=D("10000"),
        addon_target_notional=D("10000"),
        base_requested_quantity=D("100"),
        addon_requested_quantity=D("100"),
        addon_trigger_id="trigger-1",
        addon_trigger_kind=TriggerKind.CONFIRMING_CANDLE,
        addon_structural_stop=D("99"),
        addon_opportunity_consumed=True,
        current_tp=D("102"),
        actual_entry_notional=D("14000"),
    )
    machine.state.base_leg = VirtualLeg(
        quantity=D("100"),
        fill_vwap=D("100"),
        fill_execution_ids={"base-fill"},
        fill_execution_order=["base-fill"],
        fill_quantities={"base-fill": D("100")},
        remaining_fill_quantities={"base-fill": D("100")},
        stop_level=D("98"),
    )
    machine.state.addon_leg = VirtualLeg(
        quantity=D("40"),
        fill_vwap=D("100"),
        fill_execution_ids={"addon-fill"},
        fill_execution_order=["addon-fill"],
        fill_quantities={"addon-fill": D("40")},
        remaining_fill_quantities={"addon-fill": D("40")},
        stop_level=D("99"),
    )
    machine.state.real_open_quantity = D("140")
    machine.state.real_average_price = D("100")
    machine.state.order_lifecycle = OrderLifecycle.ADDON_PENDING
    machine.state.pnl.funding = D("-1.25")
    machine.state.pnl.funding_settlement_ids = {"funding-1"}
    machine.state.processed_event_ids = dict.fromkeys(("event-1", "event-2"))
    machine.state.processed_execution_ids = {"base-fill", "addon-fill"}
    machine.state.emitted_intent_keys = {"addon-stop-key"}
    stop = SubmitAddonStop(
        intent_id="intent-addon-stop",
        idempotency_key="addon-stop-key",
        strategy_id=machine.config.strategy_id,
        instrument_id=machine.config.instrument_id,
        setup_id="setup-full",
        causation_id="event-2",
        correlation_id="correlation-addon-stop",
        client_order_id="client-addon-stop",
        side=Side.SHORT,
        quantity=D("40"),
        trigger_price=D("99"),
        fill_execution_id="addon-fill",
    )
    machine.state.orders[stop.client_order_id] = OrderRecord(
        role=OrderRole.ADDON_STOP,
        intent_id=stop.intent_id,
        correlation_id=stop.correlation_id,
        client_order_id=stop.client_order_id,
        venue_order_id=None,
        requested_quantity=D("40"),
        filled_quantity=ZERO,
        status=OrderStatus.INTENDED,
        side=Side.SHORT,
        reduce_only=True,
        close_position=False,
        trigger_price=D("99"),
        setup_id="setup-full",
        protected_execution_id="addon-fill",
    )
    machine.state.outbox = [stop]
    machine.state.snapshot_id = "snapshot-full"
    machine.state.created_at_utc = NOW
    machine.assert_invariants()
    return machine


def scout_base_pending_machine() -> tuple[MastermindStateMachine, SubmitBaseOrder]:
    machine = MastermindStateMachine(config(), initial_risk_mode=RiskMode.SCOUT)
    reaction = bar(0)
    machine.state.latest_confirmed_equity = D("10000")
    machine.state.signal.reaction_bar = reaction
    machine.state.signal.recent_bars = [reaction]
    machine.state.setup = SetupState(
        setup_id="setup-scout",
        side=Side.LONG,
        reaction_bar=reaction,
        setup_start_equity=D("10000"),
        exposure_multiplier=D("0.1"),
        base_target_notional=D("1000"),
        addon_target_notional=ZERO,
        base_requested_quantity=D("10"),
    )
    machine.state.order_lifecycle = OrderLifecycle.BASE_PENDING
    intent = SubmitBaseOrder(
        intent_id="intent-scout-base",
        idempotency_key="scout-base-key",
        strategy_id=machine.config.strategy_id,
        instrument_id=machine.config.instrument_id,
        setup_id="setup-scout",
        causation_id="reaction-event",
        correlation_id="correlation-scout-base",
        client_order_id="client-scout-base",
        side=Side.LONG,
        quantity=D("10"),
        reference_price=D("100"),
        target_notional=D("1000"),
    )
    machine.state.orders[intent.client_order_id] = OrderRecord(
        role=OrderRole.BASE_ENTRY,
        intent_id=intent.intent_id,
        correlation_id=intent.correlation_id,
        client_order_id=intent.client_order_id,
        venue_order_id=None,
        requested_quantity=D("10"),
        filled_quantity=ZERO,
        status=OrderStatus.INTENDED,
        side=Side.LONG,
        reduce_only=False,
        close_position=False,
        setup_id="setup-scout",
    )
    machine.state.outbox = [intent]
    machine.state.emitted_intent_keys = {intent.idempotency_key}
    machine.state.snapshot_id = "snapshot-scout"
    machine.state.created_at_utc = NOW
    machine.assert_invariants()
    return machine, intent


def test_canonical_round_trip_full_pyramided_addon_pending_with_funding_and_outbox() -> None:
    machine = full_partial_addon_machine()
    raw = machine.snapshot_json()

    restored = MastermindStateMachine.from_snapshot(machine.config, raw)

    assert restored.snapshot_json() == raw
    assert restored.state.risk_mode is RiskMode.FULL
    assert restored.state.position_build is PositionBuild.PYRAMIDED
    assert restored.state.order_lifecycle is OrderLifecycle.ADDON_PENDING
    assert restored.state.addon_leg.quantity == D("40")
    assert restored.state.pnl.funding == D("-1.25")
    assert restored.state.pnl.funding_settlement_ids == {"funding-1"}
    assert len(restored.state.outbox) == 1
    assert isinstance(restored.state.outbox[0], SubmitAddonStop)


def test_canonical_round_trip_scout_base_pending_never_defaults_full() -> None:
    machine, _intent = scout_base_pending_machine()
    raw = machine.snapshot_json()

    restored = MastermindStateMachine.from_snapshot(machine.config, raw)

    assert restored.snapshot_json() == raw
    assert restored.state.risk_mode is RiskMode.SCOUT
    assert restored.state.position_build is PositionBuild.FLAT
    assert restored.state.order_lifecycle is OrderLifecycle.BASE_PENDING


@pytest.mark.parametrize(
    ("build", "lifecycle"),
    [
        (PositionBuild.BASE, OrderLifecycle.NONE),
        (PositionBuild.BASE_LOCKED, OrderLifecycle.NONE),
        (PositionBuild.PYRAMIDED, OrderLifecycle.REDUCE_PENDING),
        (PositionBuild.FLAT, OrderLifecycle.EXIT_PENDING),
    ],
)
def test_round_trip_additional_build_and_pending_dimensions(
    build: PositionBuild,
    lifecycle: OrderLifecycle,
) -> None:
    machine = full_partial_addon_machine()
    machine.state.order_lifecycle = lifecycle
    if build is PositionBuild.BASE:
        machine.state.addon_leg = VirtualLeg()
        machine.state.real_open_quantity = machine.state.base_leg.quantity
        for order in machine.state.orders.values():
            order.status = OrderStatus.CANCELED
        machine.state.outbox = []
    elif build is PositionBuild.BASE_LOCKED:
        machine.state.addon_leg = VirtualLeg()
        machine.state.real_open_quantity = machine.state.base_leg.quantity
        assert machine.state.setup is not None
        machine.state.setup.add_on_lock = True
        for order in machine.state.orders.values():
            order.status = OrderStatus.CANCELED
        machine.state.outbox = []
    elif build is PositionBuild.FLAT:
        machine.state.base_leg.reduced_quantity += machine.state.base_leg.quantity
        machine.state.addon_leg.reduced_quantity += machine.state.addon_leg.quantity
        machine.state.base_leg.quantity = ZERO
        machine.state.addon_leg.quantity = ZERO
        machine.state.base_leg.remaining_fill_quantities = dict.fromkeys(
            machine.state.base_leg.remaining_fill_quantities, ZERO
        )
        machine.state.addon_leg.remaining_fill_quantities = dict.fromkeys(
            machine.state.addon_leg.remaining_fill_quantities, ZERO
        )
        machine.state.real_open_quantity = ZERO
        for order in machine.state.orders.values():
            order.status = OrderStatus.CANCEL_PENDING
        assert machine.state.setup is not None
        machine.state.setup.final_close_reason = None
    machine.assert_invariants()

    restored = MastermindStateMachine.from_snapshot(machine.config, machine.snapshot_json())

    assert restored.state.position_build is build
    assert restored.state.order_lifecycle is lifecycle


def test_restart_between_submit_and_fill_reconciles_same_client_id_without_duplicate() -> None:
    machine, base = scout_base_pending_machine()
    raw = machine.snapshot_json()
    document = cast(dict[str, Any], json.loads(raw))
    restored = MastermindStateMachine.from_snapshot(machine.config, raw)
    loaded = restored.apply(
        RecoverySnapshotLoaded(
            event_id="recovery-loaded",
            strategy_id=restored.config.strategy_id,
            instrument_id=restored.config.instrument_id,
            occurred_at_utc=NOW + timedelta(minutes=1),
            source="fixture",
            source_sequence=1,
            setup_id="setup-scout",
            schema_version="mms_state/1",
            checksum=str(document["checksum"]),
            snapshot_id="snapshot-scout",
        )
    )
    assert loaded.intents
    reconciled = restored.apply(
        ReconciliationCompleted(
            event_id="reconciled",
            strategy_id=restored.config.strategy_id,
            instrument_id=restored.config.instrument_id,
            occurred_at_utc=NOW + timedelta(minutes=2),
            source="fixture",
            source_sequence=2,
            setup_id="setup-scout",
            signed_open_quantity=ZERO,
            average_price=None,
            open_client_order_ids=(base.client_order_id,),
            as_of_sequence=2,
        )
    )
    assert not restored.state.recovery_mode
    assert restored.state.order_lifecycle is OrderLifecycle.BASE_PENDING
    assert not any(isinstance(intent, SubmitBaseOrder) for intent in reconciled.intents)

    restored.apply(
        OrderFilled(
            event_id="recovered-fill",
            strategy_id=restored.config.strategy_id,
            instrument_id=restored.config.instrument_id,
            occurred_at_utc=NOW + timedelta(minutes=3),
            source="fixture",
            source_sequence=3,
            setup_id="setup-scout",
            client_order_id=base.client_order_id,
            execution_id="recovered-execution",
            role=OrderRole.BASE_ENTRY,
            last_quantity=base.quantity,
            cumulative_quantity=base.quantity,
            price=D("100"),
            commission=ZERO,
        )
    )
    assert restored.state.risk_mode is RiskMode.SCOUT
    assert restored.state.position_build is PositionBuild.BASE


@pytest.mark.parametrize("mutation", ["checksum", "schema", "bool"])
def test_corrupt_or_newer_snapshot_fails_closed(mutation: str) -> None:
    machine = full_partial_addon_machine()
    document = cast(dict[str, Any], json.loads(machine.snapshot_json()))
    if mutation == "checksum":
        document["checksum"] = "0" * 64
    elif mutation == "schema":
        document["schema_version"] = "mms_state/999"
        _resign(document)
    else:
        recovery = cast(dict[str, Any], document["recovery"])
        recovery["recovery_mode"] = "false"
        _resign(document)

    with pytest.raises(SnapshotError):
        MastermindStateMachine.from_snapshot(machine.config, _canonical(document))


def test_scout_pyramided_restore_fails_closed() -> None:
    machine = full_partial_addon_machine()
    document = cast(dict[str, Any], json.loads(machine.snapshot_json()))
    document["risk_mode"] = "SCOUT"
    _resign(document)

    with pytest.raises(SnapshotError, match=r"SCOUT\+PYRAMIDED"):
        MastermindStateMachine.from_snapshot(machine.config, _canonical(document))


def test_duplicate_client_order_id_in_snapshot_is_rejected() -> None:
    machine = full_partial_addon_machine()
    document = cast(dict[str, Any], json.loads(machine.snapshot_json()))
    orders = cast(list[dict[str, Any]], document["orders"])
    orders.append(copy.deepcopy(orders[0]))
    _resign(document)

    with pytest.raises(SnapshotError, match="duplicate client order ID"):
        MastermindStateMachine.from_snapshot(machine.config, _canonical(document))


def test_recovery_loaded_checksum_must_attest_the_verified_snapshot() -> None:
    machine = full_partial_addon_machine()
    raw = machine.snapshot_json()
    document = cast(dict[str, Any], json.loads(raw))
    restored = MastermindStateMachine.from_snapshot(machine.config, raw)
    assert restored.state.verified_snapshot_checksum == document["checksum"]

    result = restored.apply(
        RecoverySnapshotLoaded(
            event_id="bad-recovery-attestation",
            strategy_id=restored.config.strategy_id,
            instrument_id=restored.config.instrument_id,
            occurred_at_utc=NOW + timedelta(days=1),
            source="recovery-attestation",
            source_sequence=1,
            setup_id="setup-full",
            schema_version="mms_state/1",
            checksum="0" * 64,
            snapshot_id="snapshot-full",
        )
    )

    assert restored.state.invariant_violation_count == 1
    assert restored.state.recovery_mode
    assert any(isinstance(intent, CloseAll) for intent in result.intents)


def test_unreleased_schema_migration_infers_only_provably_current_order_scope() -> None:
    machine = full_partial_addon_machine()
    machine.state.orders["historical-terminal"] = OrderRecord(
        role=OrderRole.BASE_ENTRY,
        intent_id="historical-intent",
        correlation_id="historical-correlation",
        client_order_id="historical-terminal",
        venue_order_id="historical-venue",
        requested_quantity=D("10"),
        filled_quantity=D("10"),
        status=OrderStatus.FILLED,
        side=Side.SHORT,
        reduce_only=False,
        close_position=False,
        setup_id="old-setup",
    )
    document = cast(dict[str, Any], json.loads(machine.snapshot_json()))
    for leg_name in ("base_leg", "addon_leg"):
        leg = cast(dict[str, Any], document[leg_name])
        leg.pop("fill_quantities")
        leg.pop("remaining_fill_quantities")
        leg.pop("fill_execution_order")
    for order in cast(list[dict[str, Any]], document["orders"]):
        order.pop("setup_id")
        order.pop("protected_execution_id")
    _resign(document)

    restored = MastermindStateMachine.from_snapshot(machine.config, _canonical(document))

    current = restored.state.orders["client-addon-stop"]
    historical = restored.state.orders["historical-terminal"]
    assert current.setup_id == "setup-full"
    assert current.protected_execution_id == "addon-fill"
    assert historical.setup_id is None
    assert restored.state.base_leg.fill_execution_order == ["base-fill"]
    assert restored.state.addon_leg.fill_execution_order == ["addon-fill"]


def _resign(document: dict[str, Any]) -> None:
    body = dict(document)
    body.pop("checksum", None)
    document["checksum"] = hashlib.sha256(_canonical(body).encode()).hexdigest()


def _canonical(document: dict[str, Any]) -> str:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
