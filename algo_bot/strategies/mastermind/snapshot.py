"""Canonical, checksummed ``mms_state/1`` snapshot serialization.

``processed_event_ids`` remains a JSON list for schema compatibility, but it is an
ordered, bounded recent-ID window.  ``last_source_sequences`` is its durable replay
barrier after eviction; execution and funding settlement dedupe stays globally exact.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from algo_bot.strategies.mastermind.model import (
    RECENT_EVENT_ID_LIMIT,
    SCHEMA_VERSION,
    STRATEGY_VERSION,
    BarSnapshot,
    CancelOrder,
    CloseAll,
    CloseReason,
    ExternalIntent,
    IntentKind,
    MachineState,
    MastermindConfig,
    OrderLifecycle,
    OrderRecord,
    OrderRole,
    OrderStatus,
    PnlLedger,
    PositionBuild,
    ReduceAddon,
    ReplaceOrder,
    RequestReconciliation,
    RiskMode,
    SetupState,
    Side,
    SignalMemory,
    SubmitAddonOrder,
    SubmitAddonStop,
    SubmitBaseOrder,
    SubmitBaseStop,
    SubmitTakeProfit,
    TriggerKind,
    VirtualLeg,
)


class SnapshotError(ValueError):
    """The state is corrupt, incompatible, or violates a domain invariant."""


def canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise SnapshotError("snapshot cannot contain a non-finite Decimal")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def serialize_state(state: MachineState) -> str:
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "strategy_id": state.strategy_id,
        "instrument_id": state.instrument_id,
        "config_hash": state.config_hash,
        "snapshot_id": state.snapshot_id,
        "created_at_utc": _timestamp(state.created_at_utc),
        "risk_mode": state.risk_mode.value,
        "position_build": state.position_build.value,
        "order_lifecycle": state.order_lifecycle.value,
        "latest_confirmed_equity": _decimal_or_none(state.latest_confirmed_equity),
        "signal": _signal(state.signal),
        "setup": _setup(state.setup),
        "base_leg": _leg(state.base_leg),
        "addon_leg": _leg(state.addon_leg),
        "real_open_quantity": canonical_decimal(state.real_open_quantity),
        "real_average_price": _decimal_or_none(state.real_average_price),
        "orders": [_order(order) for _, order in sorted(state.orders.items())],
        "pnl": _pnl(state.pnl),
        "idempotency": {
            "processed_event_ids": list(state.processed_event_ids),
            "processed_execution_ids": sorted(state.processed_execution_ids),
            "emitted_intent_keys": sorted(state.emitted_intent_keys),
            "last_source_sequences": dict(sorted(state.last_source_sequences.items())),
        },
        "recovery": {
            "recovery_mode": state.recovery_mode,
            "unresolved_funding_settlement_ids": sorted(state.unresolved_funding_settlement_ids),
            "last_reconciliation_sequence": state.last_reconciliation_sequence,
            "observed_drift_signed_quantity": _decimal_or_none(
                state.observed_drift_signed_quantity
            ),
            "outbox": [_intent(intent) for intent in state.outbox],
        },
        "diagnostics": list(state.diagnostics),
        "counters": dict(sorted(state.counters.items())),
        "telemetry": {
            key: canonical_decimal(value) for key, value in sorted(state.telemetry.items())
        },
        "invariant_violation_count": state.invariant_violation_count,
    }
    checksum = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return _canonical_text({**body, "checksum": checksum})


def deserialize_state(raw: str, config: MastermindConfig) -> MachineState:
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SnapshotError("snapshot is not valid JSON") from exc
    if not isinstance(document, dict):
        raise SnapshotError("snapshot root must be an object")
    checksum = document.pop("checksum", None)
    if not isinstance(checksum, str):
        raise SnapshotError("snapshot checksum is missing")
    actual = hashlib.sha256(_canonical_bytes(document)).hexdigest()
    if not hmac.compare_digest(checksum, actual):
        raise SnapshotError("snapshot checksum mismatch")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError(f"unsupported snapshot schema {document.get('schema_version')!r}")
    if document.get("strategy_version") != STRATEGY_VERSION:
        raise SnapshotError("snapshot strategy version mismatch")
    if document.get("strategy_id") != config.strategy_id:
        raise SnapshotError("snapshot strategy scope mismatch")
    if document.get("instrument_id") != config.instrument_id:
        raise SnapshotError("snapshot instrument scope mismatch")
    if document.get("config_hash") != config.config_hash:
        raise SnapshotError("snapshot config hash mismatch")

    try:
        signal_data = _mapping(document["signal"])
        setup_data = document["setup"]
        idem = _mapping(document["idempotency"])
        recovery = _mapping(document["recovery"])
        state = MachineState(
            strategy_id=config.strategy_id,
            instrument_id=config.instrument_id,
            config_hash=config.config_hash,
            risk_mode=RiskMode(document["risk_mode"]),
            order_lifecycle=OrderLifecycle(document["order_lifecycle"]),
            signal=_restore_signal(signal_data),
            latest_confirmed_equity=_decimal_or_none_restore(document["latest_confirmed_equity"]),
            setup=None if setup_data is None else _restore_setup(_mapping(setup_data)),
            base_leg=_restore_leg(_mapping(document["base_leg"])),
            addon_leg=_restore_leg(_mapping(document["addon_leg"])),
            real_open_quantity=_decimal(document["real_open_quantity"]),
            real_average_price=_decimal_or_none_restore(document["real_average_price"]),
            orders=_restore_orders(document["orders"]),
            pnl=_restore_pnl(_mapping(document["pnl"])),
            processed_event_ids=_restore_recent_event_ids(idem["processed_event_ids"]),
            processed_execution_ids=set(cast(list[str], idem["processed_execution_ids"])),
            emitted_intent_keys=set(cast(list[str], idem["emitted_intent_keys"])),
            last_source_sequences={
                str(key): _int(value, f"last_source_sequences[{key}]")
                for key, value in _mapping(idem["last_source_sequences"]).items()
            },
            outbox=[_restore_intent(_mapping(item)) for item in recovery["outbox"]],
            recovery_mode=_bool(recovery["recovery_mode"], "recovery_mode"),
            unresolved_funding_settlement_ids=_restore_string_set(
                recovery.get("unresolved_funding_settlement_ids", []),
                "unresolved_funding_settlement_ids",
            ),
            observed_drift_signed_quantity=_decimal_or_none_restore(
                recovery.get("observed_drift_signed_quantity")
            ),
            last_reconciliation_sequence=(
                None
                if recovery["last_reconciliation_sequence"] is None
                else _int(
                    recovery["last_reconciliation_sequence"],
                    "last_reconciliation_sequence",
                )
            ),
            diagnostics=[str(item) for item in document["diagnostics"]],
            counters={
                str(key): _int(value, f"counters[{key}]")
                for key, value in _mapping(document["counters"]).items()
            },
            telemetry={
                str(key): _decimal(value) for key, value in _mapping(document["telemetry"]).items()
            },
            invariant_violation_count=_int(
                document["invariant_violation_count"],
                "invariant_violation_count",
            ),
            snapshot_id=str(document["snapshot_id"]),
            created_at_utc=_restore_timestamp(document["created_at_utc"]),
            verified_snapshot_checksum=checksum,
        )
        serialized_build = PositionBuild(document["position_build"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotError(f"invalid snapshot payload: {exc}") from exc
    if state.setup is not None:
        current_order_ids = {
            order.client_order_id for order in state.orders.values() if order.status.active
        }
        current_order_ids.update(
            client_id
            for client_id in (
                state.setup.tp_client_order_id,
                state.setup.base_stop_client_order_id,
            )
            if client_id is not None
        )
        for intent in state.outbox:
            if intent.setup_id != state.setup.setup_id:
                continue
            client_id = getattr(intent, "client_order_id", None)
            if isinstance(client_id, str):
                current_order_ids.add(client_id)
            if isinstance(intent, CancelOrder):
                current_order_ids.add(intent.target_client_order_id)
            if isinstance(intent, ReplaceOrder):
                current_order_ids.add(intent.previous_client_order_id)
        pending_role = (
            OrderRole.BASE_ENTRY
            if state.order_lifecycle is OrderLifecycle.BASE_PENDING
            else (
                OrderRole.ADDON_ENTRY
                if state.order_lifecycle is OrderLifecycle.ADDON_PENDING
                else None
            )
        )
        if pending_role is not None:
            current_order_ids.update(
                order.client_order_id
                for order in state.orders.values()
                if order.role is pending_role and order.status is OrderStatus.TIMED_OUT
            )
        for order in state.orders.values():
            if order.setup_id is None and order.client_order_id in current_order_ids:
                order.setup_id = state.setup.setup_id
        addon_fill_id = (
            next(iter(state.addon_leg.fill_execution_ids))
            if len(state.addon_leg.fill_execution_ids) == 1
            else None
        )
        for intent in state.outbox:
            if isinstance(intent, SubmitAddonStop):
                matched_order = state.orders.get(intent.client_order_id)
                if matched_order is not None and matched_order.protected_execution_id is None:
                    matched_order.protected_execution_id = intent.fill_execution_id
        if addon_fill_id is not None:
            for order in state.orders.values():
                if order.role is OrderRole.ADDON_STOP and order.protected_execution_id is None:
                    order.protected_execution_id = addon_fill_id
    if state.position_build is not serialized_build:
        raise SnapshotError("serialized build state does not match actual leg quantities")
    if state.risk_mode is RiskMode.SCOUT and state.position_build is PositionBuild.PYRAMIDED:
        raise SnapshotError("SCOUT+PYRAMIDED violates the base-only invariant")
    return state


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return _canonical_text(value).encode("utf-8")


def _canonical_text(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise SnapshotError("snapshot timestamp must be UTC")
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _restore_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SnapshotError("timestamp must be an ISO-8601 UTC string")
    restored = datetime.fromisoformat(value[:-1] + "+00:00")
    if restored.utcoffset() != UTC.utcoffset(restored):
        raise SnapshotError("timestamp must be UTC")
    return restored


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise SnapshotError("Decimal values must be strings")
    restored = Decimal(value)
    if not restored.is_finite():
        raise SnapshotError("Decimal values must be finite")
    return restored


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _decimal_or_none_restore(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _bar(bar: BarSnapshot) -> dict[str, Any]:
    return {
        "bar_id": bar.bar_id,
        "open_time_utc": _timestamp(bar.open_time_utc),
        "close_time_utc": _timestamp(bar.close_time_utc),
        "open": canonical_decimal(bar.open),
        "high": canonical_decimal(bar.high),
        "low": canonical_decimal(bar.low),
        "close": canonical_decimal(bar.close),
        "volume": canonical_decimal(bar.volume),
        "bb_upper": canonical_decimal(bar.bb_upper),
        "bb_lower": canonical_decimal(bar.bb_lower),
        "stoch_k": _decimal_or_none(bar.stoch_k),
        "stoch_d": _decimal_or_none(bar.stoch_d),
    }


def _restore_bar(data: dict[str, Any]) -> BarSnapshot:
    return BarSnapshot(
        bar_id=str(data["bar_id"]),
        open_time_utc=_restore_timestamp(data["open_time_utc"]),
        close_time_utc=_restore_timestamp(data["close_time_utc"]),
        open=_decimal(data["open"]),
        high=_decimal(data["high"]),
        low=_decimal(data["low"]),
        close=_decimal(data["close"]),
        volume=_decimal(data["volume"]),
        bb_upper=_decimal(data["bb_upper"]),
        bb_lower=_decimal(data["bb_lower"]),
        stoch_k=_decimal_or_none_restore(data["stoch_k"]),
        stoch_d=_decimal_or_none_restore(data["stoch_d"]),
    )


def _signal(signal: SignalMemory) -> dict[str, Any]:
    return {
        "armed_side": None if signal.armed_side is None else signal.armed_side.value,
        "armed_bars_remaining": signal.armed_bars_remaining,
        "touch_bar_id": signal.touch_bar_id,
        "reaction_bar": None if signal.reaction_bar is None else _bar(signal.reaction_bar),
        "recent_bars": [_bar(bar) for bar in signal.recent_bars],
        "confirming_candle_checked": signal.confirming_candle_checked,
        "seen_trigger_ids": sorted(signal.seen_trigger_ids),
        "last_marking_close_time_utc": (
            None
            if signal.last_marking_close_time_utc is None
            else _timestamp(signal.last_marking_close_time_utc)
        ),
        "marking_bars_in_phase": signal.marking_bars_in_phase,
    }


def _restore_signal(data: dict[str, Any]) -> SignalMemory:
    return SignalMemory(
        armed_side=None if data["armed_side"] is None else Side(data["armed_side"]),
        armed_bars_remaining=_int(data["armed_bars_remaining"], "armed_bars_remaining"),
        touch_bar_id=None if data["touch_bar_id"] is None else str(data["touch_bar_id"]),
        reaction_bar=(
            None if data["reaction_bar"] is None else _restore_bar(_mapping(data["reaction_bar"]))
        ),
        recent_bars=[_restore_bar(_mapping(item)) for item in data["recent_bars"]],
        confirming_candle_checked=_bool(
            data["confirming_candle_checked"],
            "confirming_candle_checked",
        ),
        seen_trigger_ids=set(cast(list[str], data["seen_trigger_ids"])),
        last_marking_close_time_utc=(
            None
            if data.get("last_marking_close_time_utc") is None
            else _restore_timestamp(data["last_marking_close_time_utc"])
        ),
        marking_bars_in_phase=_int(
            data.get("marking_bars_in_phase", 0),
            "marking_bars_in_phase",
        ),
    )


def _setup(setup: SetupState | None) -> dict[str, Any] | None:
    if setup is None:
        return None
    return {
        "setup_id": setup.setup_id,
        "side": setup.side.value,
        "reaction_bar": _bar(setup.reaction_bar),
        "setup_start_equity": canonical_decimal(setup.setup_start_equity),
        "exposure_multiplier": canonical_decimal(setup.exposure_multiplier),
        "base_target_notional": canonical_decimal(setup.base_target_notional),
        "addon_target_notional": canonical_decimal(setup.addon_target_notional),
        "base_requested_quantity": canonical_decimal(setup.base_requested_quantity),
        "addon_requested_quantity": canonical_decimal(setup.addon_requested_quantity),
        "addon_trigger_id": setup.addon_trigger_id,
        "addon_trigger_kind": (
            None if setup.addon_trigger_kind is None else setup.addon_trigger_kind.value
        ),
        "addon_structural_stop": _decimal_or_none(setup.addon_structural_stop),
        "addon_opportunity_consumed": setup.addon_opportunity_consumed,
        "add_on_lock": setup.add_on_lock,
        "pending_close_reason": (
            None if setup.pending_close_reason is None else setup.pending_close_reason.value
        ),
        "final_close_reason": (
            None if setup.final_close_reason is None else setup.final_close_reason.value
        ),
        "current_tp": _decimal_or_none(setup.current_tp),
        "tp_client_order_id": setup.tp_client_order_id,
        "base_stop_client_order_id": setup.base_stop_client_order_id,
        "actual_entry_notional": canonical_decimal(setup.actual_entry_notional),
        "realized_notional_drift": canonical_decimal(setup.realized_notional_drift),
        "closing_execution_ids": list(setup.closing_execution_ids),
        "finalization_fingerprint": setup.finalization_fingerprint,
    }


def _restore_setup(data: dict[str, Any]) -> SetupState:
    return SetupState(
        setup_id=str(data["setup_id"]),
        side=Side(data["side"]),
        reaction_bar=_restore_bar(_mapping(data["reaction_bar"])),
        setup_start_equity=_decimal(data["setup_start_equity"]),
        exposure_multiplier=_decimal(data["exposure_multiplier"]),
        base_target_notional=_decimal(data["base_target_notional"]),
        addon_target_notional=_decimal(data["addon_target_notional"]),
        base_requested_quantity=_decimal(data["base_requested_quantity"]),
        addon_requested_quantity=_decimal(data["addon_requested_quantity"]),
        addon_trigger_id=(
            None if data["addon_trigger_id"] is None else str(data["addon_trigger_id"])
        ),
        addon_trigger_kind=(
            None if data["addon_trigger_kind"] is None else TriggerKind(data["addon_trigger_kind"])
        ),
        addon_structural_stop=_decimal_or_none_restore(data["addon_structural_stop"]),
        addon_opportunity_consumed=_bool(
            data["addon_opportunity_consumed"],
            "addon_opportunity_consumed",
        ),
        add_on_lock=_bool(data["add_on_lock"], "add_on_lock"),
        pending_close_reason=(
            None
            if data["pending_close_reason"] is None
            else CloseReason(data["pending_close_reason"])
        ),
        final_close_reason=(
            None if data["final_close_reason"] is None else CloseReason(data["final_close_reason"])
        ),
        current_tp=_decimal_or_none_restore(data["current_tp"]),
        tp_client_order_id=(
            None if data["tp_client_order_id"] is None else str(data["tp_client_order_id"])
        ),
        base_stop_client_order_id=(
            None
            if data["base_stop_client_order_id"] is None
            else str(data["base_stop_client_order_id"])
        ),
        actual_entry_notional=_decimal(data["actual_entry_notional"]),
        realized_notional_drift=_decimal(data["realized_notional_drift"]),
        closing_execution_ids=[str(item) for item in data.get("closing_execution_ids", [])],
        finalization_fingerprint=(
            None
            if data.get("finalization_fingerprint") is None
            else str(data["finalization_fingerprint"])
        ),
    )


def _leg(leg: VirtualLeg) -> dict[str, Any]:
    return {
        "quantity": canonical_decimal(leg.quantity),
        "fill_vwap": _decimal_or_none(leg.fill_vwap),
        "realized_price_pnl": canonical_decimal(leg.realized_price_pnl),
        "fill_execution_ids": sorted(leg.fill_execution_ids),
        "fill_execution_order": list(leg.fill_execution_order),
        "fill_quantities": {
            key: canonical_decimal(value) for key, value in sorted(leg.fill_quantities.items())
        },
        "remaining_fill_quantities": {
            key: canonical_decimal(value)
            for key, value in sorted(leg.remaining_fill_quantities.items())
        },
        "reduced_quantity": canonical_decimal(leg.reduced_quantity),
        "stop_level": _decimal_or_none(leg.stop_level),
    }


def _restore_leg(data: dict[str, Any]) -> VirtualLeg:
    quantity = _decimal(data["quantity"])
    reduced = _decimal(data["reduced_quantity"])
    execution_ids = set(cast(list[str], data["fill_execution_ids"]))
    if "fill_execution_order" in data:
        execution_order = cast(list[str], data["fill_execution_order"])
        if (
            any(not isinstance(item, str) or not item for item in execution_order)
            or len(execution_order) != len(execution_ids)
            or set(execution_order) != execution_ids
        ):
            raise SnapshotError("leg fill execution order does not match execution IDs")
    elif len(execution_ids) <= 1:
        execution_order = list(execution_ids)
    else:
        raise SnapshotError("legacy leg lacks durable per-execution FIFO order")
    if "fill_quantities" in data:
        fill_quantities = _restore_decimal_mapping(data["fill_quantities"])
        remaining = _restore_decimal_mapping(data["remaining_fill_quantities"])
    elif not execution_ids:
        fill_quantities = {}
        remaining = {}
    elif len(execution_ids) == 1:
        execution_id = next(iter(execution_ids))
        fill_quantities = {execution_id: quantity + reduced}
        remaining = {execution_id: quantity}
    else:
        raise SnapshotError("legacy leg with multiple executions lacks per-execution quantities")
    return VirtualLeg(
        quantity=quantity,
        fill_vwap=_decimal_or_none_restore(data["fill_vwap"]),
        realized_price_pnl=_decimal(data["realized_price_pnl"]),
        fill_execution_ids=execution_ids,
        fill_execution_order=execution_order,
        fill_quantities=fill_quantities,
        remaining_fill_quantities=remaining,
        reduced_quantity=reduced,
        stop_level=_decimal_or_none_restore(data["stop_level"]),
    )


def _order(order: OrderRecord) -> dict[str, Any]:
    return {
        "role": order.role.value,
        "intent_id": order.intent_id,
        "correlation_id": order.correlation_id,
        "client_order_id": order.client_order_id,
        "venue_order_id": order.venue_order_id,
        "requested_quantity": canonical_decimal(order.requested_quantity),
        "filled_quantity": canonical_decimal(order.filled_quantity),
        "status": order.status.value,
        "side": order.side.value,
        "reduce_only": order.reduce_only,
        "close_position": order.close_position,
        "trigger_price": _decimal_or_none(order.trigger_price),
        "deadline_at_utc": (
            None if order.deadline_at_utc is None else _timestamp(order.deadline_at_utc)
        ),
        "replacement_of": order.replacement_of,
        "setup_id": order.setup_id,
        "protected_execution_id": order.protected_execution_id,
    }


def _restore_order(data: dict[str, Any]) -> OrderRecord:
    return OrderRecord(
        role=OrderRole(data["role"]),
        intent_id=str(data["intent_id"]),
        correlation_id=str(data["correlation_id"]),
        client_order_id=str(data["client_order_id"]),
        venue_order_id=None if data["venue_order_id"] is None else str(data["venue_order_id"]),
        requested_quantity=_decimal(data["requested_quantity"]),
        filled_quantity=_decimal(data["filled_quantity"]),
        status=OrderStatus(data["status"]),
        side=Side(data["side"]),
        reduce_only=_bool(data["reduce_only"], "reduce_only"),
        close_position=_bool(data["close_position"], "close_position"),
        trigger_price=_decimal_or_none_restore(data["trigger_price"]),
        deadline_at_utc=(
            None if data["deadline_at_utc"] is None else _restore_timestamp(data["deadline_at_utc"])
        ),
        replacement_of=(None if data["replacement_of"] is None else str(data["replacement_of"])),
        setup_id=None if data.get("setup_id") is None else str(data["setup_id"]),
        protected_execution_id=(
            None
            if data.get("protected_execution_id") is None
            else str(data["protected_execution_id"])
        ),
    )


def _pnl(pnl: PnlLedger) -> dict[str, Any]:
    return {
        "base_realized_price_pnl": canonical_decimal(pnl.base_realized_price_pnl),
        "addon_realized_price_pnl": canonical_decimal(pnl.addon_realized_price_pnl),
        "commissions": canonical_decimal(pnl.commissions),
        "funding": canonical_decimal(pnl.funding),
        "realized_slippage_cost": canonical_decimal(pnl.realized_slippage_cost),
        "addon_stop_realized_pnl": canonical_decimal(pnl.addon_stop_realized_pnl),
        "funding_settlement_ids": sorted(pnl.funding_settlement_ids),
    }


def _restore_pnl(data: dict[str, Any]) -> PnlLedger:
    return PnlLedger(
        base_realized_price_pnl=_decimal(data["base_realized_price_pnl"]),
        addon_realized_price_pnl=_decimal(data["addon_realized_price_pnl"]),
        commissions=_decimal(data["commissions"]),
        funding=_decimal(data["funding"]),
        realized_slippage_cost=_decimal(data["realized_slippage_cost"]),
        addon_stop_realized_pnl=_decimal(data["addon_stop_realized_pnl"]),
        funding_settlement_ids=set(cast(list[str], data["funding_settlement_ids"])),
    )


def _intent(intent: ExternalIntent) -> dict[str, Any]:
    payload: dict[str, Any] = {"intent_type": intent.kind.value}
    for item in fields(intent):
        payload[item.name] = _json_value(getattr(intent, item.name))
    return payload


def _json_value(value: object) -> Any:
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise SnapshotError(f"unsupported outbox value {type(value).__name__}")


def _restore_intent(data: dict[str, Any]) -> ExternalIntent:
    kind = IntentKind(data.pop("intent_type"))
    common: dict[str, Any] = {
        "intent_id": str(data["intent_id"]),
        "idempotency_key": str(data["idempotency_key"]),
        "strategy_id": str(data["strategy_id"]),
        "instrument_id": str(data["instrument_id"]),
        "setup_id": None if data["setup_id"] is None else str(data["setup_id"]),
        "causation_id": str(data["causation_id"]),
        "correlation_id": str(data["correlation_id"]),
        "reconciliation_policy": str(data["reconciliation_policy"]),
    }
    if kind is IntentKind.SUBMIT_BASE_ORDER:
        return SubmitBaseOrder(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            quantity=_decimal(data["quantity"]),
            reference_price=_decimal(data["reference_price"]),
            target_notional=_decimal(data["target_notional"]),
        )
    if kind is IntentKind.SUBMIT_ADDON_ORDER:
        return SubmitAddonOrder(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            quantity=_decimal(data["quantity"]),
            reference_price=_decimal(data["reference_price"]),
            target_notional=_decimal(data["target_notional"]),
            trigger_id=str(data["trigger_id"]),
            trigger_kind=TriggerKind(data["trigger_kind"]),
            structural_stop=_decimal(data["structural_stop"]),
        )
    if kind is IntentKind.SUBMIT_BASE_STOP:
        return SubmitBaseStop(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            reference_quantity=_decimal(data["reference_quantity"]),
            trigger_price=_decimal(data["trigger_price"]),
            close_position=_bool(data["close_position"], "close_position"),
            reduce_only=_bool(data["reduce_only"], "reduce_only"),
        )
    if kind is IntentKind.SUBMIT_ADDON_STOP:
        return SubmitAddonStop(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            quantity=_decimal(data["quantity"]),
            trigger_price=_decimal(data["trigger_price"]),
            fill_execution_id=str(data["fill_execution_id"]),
            close_position=_bool(data["close_position"], "close_position"),
            reduce_only=_bool(data["reduce_only"], "reduce_only"),
        )
    if kind is IntentKind.SUBMIT_TAKE_PROFIT:
        return SubmitTakeProfit(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            reference_quantity=_decimal(data["reference_quantity"]),
            trigger_price=_decimal(data["trigger_price"]),
            close_position=_bool(data["close_position"], "close_position"),
            reduce_only=_bool(data["reduce_only"], "reduce_only"),
        )
    if kind is IntentKind.CLOSE_ALL:
        return CloseAll(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            quantity=_decimal(data["quantity"]),
            close_reason=CloseReason(data["close_reason"]),
        )
    if kind is IntentKind.REDUCE_ADDON:
        return ReduceAddon(
            **common,
            client_order_id=str(data["client_order_id"]),
            side=Side(data["side"]),
            quantity=_decimal(data["quantity"]),
            reason=str(data["reason"]),
        )
    if kind is IntentKind.CANCEL_ORDER:
        return CancelOrder(
            **common,
            target_client_order_id=str(data["target_client_order_id"]),
            reason=str(data["reason"]),
        )
    if kind is IntentKind.REPLACE_ORDER:
        return ReplaceOrder(
            **common,
            previous_client_order_id=str(data["previous_client_order_id"]),
            client_order_id=str(data["client_order_id"]),
            role=OrderRole(data["role"]),
            side=Side(data["side"]),
            quantity=_decimal(data["quantity"]),
            trigger_price=_decimal(data["trigger_price"]),
            close_position=_bool(data["close_position"], "close_position"),
            reduce_only=_bool(data["reduce_only"], "reduce_only"),
        )
    if kind is IntentKind.REQUEST_RECONCILIATION:
        return RequestReconciliation(**common, reason=str(data["reason"]))
    raise SnapshotError(f"{kind.value} cannot be stored in the external outbox")


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotError("expected JSON object")
    return cast(dict[str, Any], value)


def _restore_decimal_mapping(value: object) -> dict[str, Decimal]:
    return {str(key): _decimal(item) for key, item in _mapping(value).items()}


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotError(f"{name} must be a JSON boolean")
    return value


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError(f"{name} must be a JSON integer")
    return value


def _restore_orders(value: object) -> dict[str, OrderRecord]:
    if not isinstance(value, list):
        raise SnapshotError("orders must be a JSON array")
    restored: dict[str, OrderRecord] = {}
    for item in value:
        order = _restore_order(_mapping(item))
        if order.client_order_id in restored:
            raise SnapshotError(f"duplicate client order ID {order.client_order_id}")
        restored[order.client_order_id] = order
    return restored


def _restore_recent_event_ids(value: object) -> dict[str, None]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SnapshotError("processed_event_ids must be a JSON string array")
    # Old mms_state/1 snapshots could contain an unbounded sorted list.  Keeping its
    # final window is safe because the same snapshot also carries source highwaters.
    recent = value[-RECENT_EVENT_ID_LIMIT:]
    if len(set(recent)) != len(recent):
        raise SnapshotError("processed_event_ids contains duplicates")
    return dict.fromkeys(recent)


def _restore_string_set(value: object, name: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SnapshotError(f"{name} must be a JSON array of non-empty strings")
    if len(set(value)) != len(value):
        raise SnapshotError(f"{name} contains duplicates")
    return set(value)
