"""Pure event reducer for the MMS-inspired v2 position state machine."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from typing import TypedDict

from algo_bot.strategies.mastermind.model import (
    RECENT_EVENT_ID_LIMIT,
    ZERO,
    AccountEquityUpdated,
    AddonTriggerPolicy,
    BarClosed,
    CancelOrder,
    CloseAll,
    CloseReason,
    CloseRequested,
    DomainEvent,
    DomainIntent,
    EventEnvelope,
    ExternalIntent,
    FillEnvelope,
    FundingApplied,
    MachineState,
    MarkingBarClosed,
    MastermindConfig,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderLifecycle,
    OrderPartiallyFilled,
    OrderRecord,
    OrderRejected,
    OrderRole,
    OrderStatus,
    OrderSubmitted,
    OrderTimedOut,
    PersistSnapshot,
    PnlLedger,
    PositionBuild,
    PositionChanged,
    PositionClosed,
    ReconciliationCompleted,
    RecoveryEntryFill,
    RecoveryOrder,
    RecoverySnapshotLoaded,
    RecoveryView,
    ReduceAddon,
    ReplaceOrder,
    RequestReconciliation,
    RiskLimitTriggered,
    RiskMode,
    SetupState,
    Side,
    SubmitAddonOrder,
    SubmitAddonStop,
    SubmitBaseOrder,
    SubmitBaseStop,
    SubmitTakeProfit,
    VirtualLeg,
    validate_decimal_event,
    validate_event_envelope,
    validate_identifier,
    validate_utc,
)
from algo_bot.strategies.mastermind.signals import (
    AddonTriggerFact,
    SignalContext,
    TargetFact,
    evaluate_bar,
    validate_structural_stop,
)
from algo_bot.strategies.mastermind.snapshot import deserialize_state, serialize_state


class InvariantViolation(RuntimeError):
    """A state/order invariant was violated before an external side effect."""


@dataclass(frozen=True, slots=True)
class TransitionResult:
    intents: tuple[DomainIntent, ...]
    duplicate: bool
    snapshot_json: str


class MastermindStateMachine:
    """Deterministic domain reducer with a transactional-outbox state.

    ``apply`` mutates only the in-memory domain state and returns intents.  A wrapper
    must durably commit ``snapshot_json`` before dispatching any external intent.
    """

    def __init__(
        self,
        config: MastermindConfig,
        *,
        initial_risk_mode: RiskMode = RiskMode.FULL,
        _state: MachineState | None = None,
    ) -> None:
        if not config.sequential_enabled and initial_risk_mode is RiskMode.SCOUT and _state is None:
            raise ValueError("sequential-disabled ablation cannot start in SCOUT")
        self.config = config
        self.state = _state or MachineState(
            strategy_id=config.strategy_id,
            instrument_id=config.instrument_id,
            config_hash=config.config_hash,
            risk_mode=initial_risk_mode,
        )
        self.assert_invariants()

    @classmethod
    def from_snapshot(
        cls,
        config: MastermindConfig,
        raw: str,
    ) -> MastermindStateMachine:
        """Restore exact state; caller then applies ``RecoverySnapshotLoaded``."""

        return cls(config, _state=deserialize_state(raw, config))

    def snapshot_json(self) -> str:
        return serialize_state(self.state)

    @property
    def source_sequence_highwater(self) -> int:
        """Return the durable floor a restarted event adapter must exceed.

        The reducer rejects a source sequence at or below its persisted per-source
        high-water mark.  Adapters which multiplex their callbacks through one
        monotonically increasing counter can safely resume above the maximum.
        """

        return max(self.state.last_source_sequences.values(), default=0)

    @property
    def source_sequence_highwaters(self) -> dict[str, int]:
        """Return a copy of durable per-source replay cursors for adapter recovery."""

        return dict(self.state.last_source_sequences)

    @property
    def recovery_view(self) -> RecoveryView:
        """Return an immutable, engine-independent view needed to resume an adapter."""

        setup = self.state.setup
        entry_fills: list[RecoveryEntryFill] = []
        if setup is not None:
            for role, leg in (
                (OrderRole.BASE_ENTRY, self.state.base_leg),
                (OrderRole.ADDON_ENTRY, self.state.addon_leg),
            ):
                for execution_id in leg.fill_execution_order:
                    original = leg.fill_quantities[execution_id]
                    entry_fills.append(
                        RecoveryEntryFill(
                            execution_id=execution_id,
                            role=role,
                            original_quantity=original,
                            remaining_quantity=leg.remaining_fill_quantities.get(
                                execution_id, ZERO
                            ),
                            side=setup.side,
                        )
                    )
        orders = tuple(
            RecoveryOrder(
                role=order.role,
                intent_id=order.intent_id,
                correlation_id=order.correlation_id,
                client_order_id=order.client_order_id,
                venue_order_id=order.venue_order_id,
                requested_quantity=order.requested_quantity,
                filled_quantity=order.filled_quantity,
                status=order.status,
                side=order.side,
                reduce_only=order.reduce_only,
                close_position=order.close_position,
                trigger_price=order.trigger_price,
                deadline_at_utc=order.deadline_at_utc,
                replacement_of=order.replacement_of,
                setup_id=order.setup_id,
                protected_execution_id=order.protected_execution_id,
            )
            for _, order in sorted(self.state.orders.items())
        )
        pnl = self.state.pnl
        return RecoveryView(
            active_setup_id=None if setup is None else setup.setup_id,
            setup_side=None if setup is None else setup.side,
            pending_close_reason=None if setup is None else setup.pending_close_reason,
            final_close_reason=None if setup is None else setup.final_close_reason,
            base_realized_price_pnl=pnl.base_realized_price_pnl,
            addon_realized_price_pnl=pnl.addon_realized_price_pnl,
            commissions=pnl.commissions,
            funding=pnl.funding,
            realized_slippage_cost=pnl.realized_slippage_cost,
            addon_stop_realized_pnl=pnl.addon_stop_realized_pnl,
            funding_settlement_ids=tuple(sorted(pnl.funding_settlement_ids)),
            unresolved_funding_settlement_ids=tuple(
                sorted(self.state.unresolved_funding_settlement_ids)
            ),
            closing_execution_ids=(() if setup is None else tuple(setup.closing_execution_ids)),
            entry_fills=tuple(entry_fills),
            orders=orders,
            outbox=tuple(self.state.outbox),
        )

    def handle(self, event: DomainEvent) -> TransitionResult:
        """Adapter-friendly alias for :meth:`apply`."""

        return self.apply(event)

    def ingest_marking_bar(self, bar_m5_or_m10: MarkingBarClosed) -> TransitionResult:
        """Wprowadza finalny M5/M10 marker przez ten sam event-sourced reducer."""

        return self.apply(bar_m5_or_m10)

    def handle_without_snapshot(self, event: DomainEvent) -> TransitionResult:
        """Apply one event without serializing the full recovery snapshot.

        This is restricted to offline engine runs without restart persistence. It
        retains full invariant checks but omits rollback and JSON materialization;
        the caller must stop routing after the first raised transition.
        """

        return self._apply(
            event,
            serialize_snapshot=False,
            transactional_rollback=False,
        )

    def apply(self, event: DomainEvent) -> TransitionResult:
        return self._apply(
            event,
            serialize_snapshot=True,
            transactional_rollback=True,
        )

    def _apply(
        self,
        event: DomainEvent,
        *,
        serialize_snapshot: bool,
        transactional_rollback: bool,
    ) -> TransitionResult:
        validate_event_envelope(event)
        if (event.strategy_id, event.instrument_id) != (
            self.config.strategy_id,
            self.config.instrument_id,
        ):
            raise ValueError("event belongs to another strategy/instrument scope")
        if event.event_id in self.state.processed_event_ids:
            return TransitionResult(
                (),
                True,
                self.snapshot_json() if serialize_snapshot else "",
            )
        source_highwater = self.state.last_source_sequences.get(event.source)
        if source_highwater is not None and event.source_sequence <= source_highwater:
            # The exact ID may have fallen out of the bounded recent window.  Source
            # sequences are required to be durable and strictly increasing per source.
            return TransitionResult(
                (),
                True,
                self.snapshot_json() if serialize_snapshot else "",
            )
        execution_id = event.execution_id if isinstance(event, FillEnvelope) else None
        if execution_id is not None and execution_id in self.state.processed_execution_ids:
            return TransitionResult(
                (),
                True,
                self.snapshot_json() if serialize_snapshot else "",
            )
        if (
            isinstance(event, FundingApplied)
            and event.settlement_id in self.state.pnl.funding_settlement_ids
        ):
            return TransitionResult(
                (),
                True,
                self.snapshot_json() if serialize_snapshot else "",
            )

        setup = self.state.setup
        suspicious_entry_fill = (
            isinstance(event, FillEnvelope)
            and event.role.increases_exposure
            and (
                setup is None
                or event.setup_id != setup.setup_id
                or setup.pending_close_reason is not None
            )
        )
        before = (
            copy.deepcopy(self.state) if transactional_rollback or suspicious_entry_fill else None
        )
        emitted: list[ExternalIntent] = []
        try:
            self._remember_event(event)
            if execution_id is not None:
                self.state.processed_execution_ids.add(execution_id)
            self._dispatch(event, emitted)
            self._store_outbox(emitted)
            self.assert_invariants()
        except InvariantViolation as exc:
            if before is None:
                raise
            self.state = before
            emitted = []
            self._remember_event(event)
            if execution_id is not None:
                self.state.processed_execution_ids.add(execution_id)
            if isinstance(event, FundingApplied):
                self.state.pnl.funding_settlement_ids.add(event.settlement_id)
                if event.amount != ZERO:
                    self.state.unresolved_funding_settlement_ids.add(event.settlement_id)
            self.state.invariant_violation_count += 1
            self.state.recovery_mode = True
            self.state.diagnostics.append(f"INVARIANT:{event.event_id}:{exc}")
            if isinstance(event, ReconciliationCompleted):
                live_setup_id = None if self.state.setup is None else self.state.setup.setup_id
                trusted_scope = event.setup_id == live_setup_id
                trusted_sequence = (
                    self.state.last_reconciliation_sequence is None
                    or event.as_of_sequence >= self.state.last_reconciliation_sequence
                )
                if trusted_scope and trusted_sequence:
                    known_active_ids = {
                        client_id
                        for client_id, order in self.state.orders.items()
                        if order.status.active
                    }
                    for client_id in sorted(set(event.open_client_order_ids) - known_active_ids):
                        self._emit(
                            self._cancel_intent(
                                event,
                                client_id,
                                "INVALID_RECONCILIATION_ORDER",
                            ),
                            emitted,
                        )
                    self._cancel_all_orphans(
                        event,
                        emitted,
                        reason="INVALID_RECONCILIATION",
                    )
                    actual = event.signed_open_quantity
                    self.state.observed_drift_signed_quantity = actual
                    self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                    if self.state.setup is not None:
                        self.state.setup.pending_close_reason = CloseReason.ENGINE_ERROR
                    if actual != ZERO:
                        close = self._close_all_intent(
                            event,
                            CloseReason.ENGINE_ERROR,
                            "invalid-reconciliation",
                            quantity=abs(actual),
                            side=Side.SHORT if actual > ZERO else Side.LONG,
                            emitted=emitted,
                        )
                        self._emit(close, emitted)
                    else:
                        self._emit(
                            self._reconciliation_intent(
                                event,
                                f"invalid reconciliation: {exc}",
                            ),
                            emitted,
                        )
                else:
                    self._emit(
                        self._reconciliation_intent(
                            event,
                            f"untrusted reconciliation ignored: {exc}",
                        ),
                        emitted,
                    )
            elif isinstance(event, FillEnvelope) and event.role.increases_exposure:
                self._emit(
                    self._reconciliation_intent(event, "unattributed entry fill"),
                    emitted,
                )
                event_client_id = event.client_order_id
                record = None if event_client_id is None else self.state.orders.get(event_client_id)
                if record is not None:
                    setup = self.state.setup
                    if setup is not None and setup.final_close_reason is not None:
                        self.state.diagnostics.append(
                            "PROVISIONAL_FINALIZATION_INVALIDATED_BY_LATE_ENTRY_FILL"
                        )
                        setup.final_close_reason = None
                        setup.finalization_fingerprint = None
                    known_before_fill = self.state.observed_drift_signed_quantity
                    if known_before_fill is None:
                        known_before_fill = (
                            ZERO
                            if setup is None
                            else self.state.real_open_quantity * setup.side.sign
                        )
                    observed = known_before_fill + event.last_quantity * record.side.sign
                    self.state.observed_drift_signed_quantity = observed
                    if observed != ZERO:
                        self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                        self._cancel_all_orphans(
                            event,
                            emitted,
                            reason="STALE_ENTRY_DRIFT",
                        )
                        close = self._close_all_intent(
                            event,
                            CloseReason.ENGINE_ERROR,
                            "attributed-stale-entry-fill",
                            quantity=abs(observed),
                            side=Side.SHORT if observed > ZERO else Side.LONG,
                            emitted=emitted,
                        )
                        self._emit(close, emitted)
            elif self.state.real_open_quantity > ZERO and self.state.setup is not None:
                self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                self._cancel_all_orphans(
                    event,
                    emitted,
                    reason="INVARIANT_FAILSAFE",
                )
                close = self._close_all_intent(
                    event,
                    CloseReason.ENGINE_ERROR,
                    "invariant-failsafe",
                    emitted=emitted,
                )
                self._emit(close, emitted)
            else:
                self._emit(
                    self._reconciliation_intent(event, f"invariant violation: {exc}"),
                    emitted,
                )
            self._store_outbox(emitted)
            self.assert_invariants()
        except Exception:
            if before is not None:
                self.state = before
            raise

        self.state.snapshot_id = self._stable_id("snapshot", event.event_id, length=32)
        self.state.created_at_utc = event.occurred_at_utc
        persist = self._persist_intent(event)
        return TransitionResult(
            (*emitted, persist),
            False,
            self.snapshot_json() if serialize_snapshot else "",
        )

    def assert_invariants(self) -> None:
        state = self.state
        for name, value in (
            ("base quantity", state.base_leg.quantity),
            ("addon quantity", state.addon_leg.quantity),
            ("real open quantity", state.real_open_quantity),
        ):
            if not value.is_finite() or value < ZERO:
                raise InvariantViolation(f"{name} must be finite and non-negative")
        if state.observed_drift_signed_quantity is not None and not (
            state.observed_drift_signed_quantity.is_finite()
        ):
            raise InvariantViolation("observed drift quantity must be finite")
        if any(not settlement_id for settlement_id in state.unresolved_funding_settlement_ids):
            raise InvariantViolation("unresolved funding IDs must be non-empty")
        for name, leg in (("base", state.base_leg), ("addon", state.addon_leg)):
            if leg.fill_quantities:
                if set(leg.fill_quantities) != leg.fill_execution_ids:
                    raise InvariantViolation(f"{name} entry-fill provenance is incomplete")
                if (
                    len(leg.fill_execution_order) != len(leg.fill_execution_ids)
                    or set(leg.fill_execution_order) != leg.fill_execution_ids
                ):
                    raise InvariantViolation(f"{name} entry-fill execution order is incomplete")
                if set(leg.remaining_fill_quantities) != leg.fill_execution_ids:
                    raise InvariantViolation(f"{name} remaining-fill provenance is incomplete")
                if any(
                    quantity <= ZERO or not quantity.is_finite()
                    for quantity in leg.fill_quantities.values()
                ):
                    raise InvariantViolation(f"{name} original fill quantities must be positive")
                if any(
                    quantity < ZERO or not quantity.is_finite()
                    for quantity in leg.remaining_fill_quantities.values()
                ):
                    raise InvariantViolation(
                        f"{name} remaining fill quantities must be non-negative"
                    )
                if sum(leg.remaining_fill_quantities.values(), start=ZERO) != leg.quantity:
                    raise InvariantViolation(f"{name} remaining fills do not equal leg quantity")
        if state.total_logical_quantity != state.real_open_quantity:
            raise InvariantViolation("logical leg quantities do not reconcile to real quantity")
        if state.addon_leg.quantity > ZERO and state.setup is None:
            raise InvariantViolation("an add-on cannot exist without a setup")
        if state.base_leg.quantity > ZERO and state.setup is None:
            raise InvariantViolation("a base cannot exist without a setup")
        if state.risk_mode is RiskMode.SCOUT and state.addon_leg.quantity > ZERO:
            raise InvariantViolation("SCOUT is base-only")
        if not self.config.sequential_enabled and state.risk_mode is not RiskMode.FULL:
            raise InvariantViolation("sequential-disabled ablation must remain FULL")
        if state.position_build is PositionBuild.BASE_LOCKED and (
            state.setup is None or not state.setup.add_on_lock
        ):
            raise InvariantViolation("BASE_LOCKED requires a persistent add-on lock")
        if state.position_build is PositionBuild.PYRAMIDED and state.addon_leg.quantity <= ZERO:
            raise InvariantViolation("PYRAMIDED requires an actual add-on fill")
        if len(state.signal.recent_bars) > 2:
            raise InvariantViolation("signal memory may retain at most two H1 bars")

        active_orders = [order for order in state.orders.values() if order.status.active]
        active_protective = [order for order in active_orders if order.role.is_protective]
        if state.position_build is PositionBuild.FLAT and active_protective:
            raise InvariantViolation("FLAT cannot retain active protective orders")
        if any(order.close_position and order.reduce_only for order in active_orders):
            raise InvariantViolation("close_position and reduce_only are mutually exclusive")
        active_addon_entries = [
            order for order in active_orders if order.role is OrderRole.ADDON_ENTRY
        ]
        active_base_entries = [
            order for order in active_orders if order.role is OrderRole.BASE_ENTRY
        ]
        if len(active_base_entries) > 1:
            raise InvariantViolation("at most one logical base entry may be active")
        if len(active_addon_entries) > 1:
            raise InvariantViolation("at most one add-on entry may be active")
        if setup := state.setup:
            if any(order.setup_id != setup.setup_id for order in active_orders):
                raise InvariantViolation("active order belongs to another setup")
        elif any(order.role.increases_exposure for order in active_orders):
            raise InvariantViolation("active entry order requires a live setup")
        addon_entry_outbox = any(isinstance(intent, SubmitAddonOrder) for intent in state.outbox)
        if state.risk_mode is RiskMode.SCOUT and (active_addon_entries or addon_entry_outbox):
            raise InvariantViolation("SCOUT cannot submit an add-on")
        if not self.config.addon_enabled and (
            state.addon_leg.quantity > ZERO or active_addon_entries or addon_entry_outbox
        ):
            raise InvariantViolation("add-on-disabled ablation cannot carry an add-on")
        if len({intent.intent_id for intent in state.outbox}) != len(state.outbox):
            raise InvariantViolation("transactional outbox contains duplicate intent IDs")
        addon_stop_quantity = sum(
            (
                order.remaining_quantity
                for order in active_orders
                if order.role is OrderRole.ADDON_STOP
            ),
            start=ZERO,
        )
        if addon_stop_quantity > state.addon_leg.quantity:
            raise InvariantViolation("add-on stop quantity exceeds actual add-on quantity")
        if addon_stop_quantity > state.real_open_quantity:
            raise InvariantViolation("protective quantity exceeds real open quantity")
        for order in active_orders:
            if state.setup is not None:
                expected_side = (
                    state.setup.side
                    if order.role.increases_exposure
                    else state.setup.side.exit_side
                )
                if (
                    order.role is OrderRole.CLOSE_ALL
                    and state.recovery_mode
                    and state.observed_drift_signed_quantity not in {None, ZERO}
                ):
                    assert state.observed_drift_signed_quantity is not None
                    expected_side = (
                        Side.SHORT if state.observed_drift_signed_quantity > ZERO else Side.LONG
                    )
                if order.side is not expected_side:
                    raise InvariantViolation("order side could increase or reverse exposure")
            if order.role is OrderRole.ADDON_STOP and (
                not order.reduce_only or order.close_position
            ):
                raise InvariantViolation("add-on stop must be reduce-only")
            if order.role in {OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT} and (
                order.reduce_only or not order.close_position
            ):
                raise InvariantViolation("whole-position protection must use close_position")

        setup = state.setup
        if state.order_lifecycle is OrderLifecycle.BASE_PENDING and setup is None:
            raise InvariantViolation("BASE_PENDING requires a setup")
        if state.order_lifecycle is OrderLifecycle.ADDON_PENDING and (
            setup is None or state.base_leg.quantity <= ZERO
        ):
            raise InvariantViolation("ADDON_PENDING requires actual base exposure")
        if state.order_lifecycle is OrderLifecycle.REDUCE_PENDING and (
            setup is None or state.addon_leg.quantity <= ZERO
        ):
            raise InvariantViolation("REDUCE_PENDING requires actual add-on exposure")
        if setup is not None and setup.final_close_reason is None:
            if setup.setup_start_equity <= ZERO:
                raise InvariantViolation("setup equity must be positive")
            committed_target = self._committed_entry_notional()
            if state.risk_mode is RiskMode.SCOUT:
                cap = Decimal("0.1") * setup.setup_start_equity
                safety_exit = (
                    state.order_lifecycle is OrderLifecycle.EXIT_PENDING
                    and setup.pending_close_reason is CloseReason.ENGINE_ERROR
                )
                if setup.addon_target_notional != ZERO or (
                    committed_target > cap and not safety_exit
                ):
                    raise InvariantViolation("SCOUT target reservation exceeds x0.1")
            else:
                cap = Decimal("2") * setup.setup_start_equity
                safety_unwind = (
                    setup.add_on_lock and state.order_lifecycle is OrderLifecycle.REDUCE_PENDING
                )
                safety_exit = (
                    state.order_lifecycle is OrderLifecycle.EXIT_PENDING
                    and setup.pending_close_reason is CloseReason.ENGINE_ERROR
                )
                if committed_target > cap and not (safety_unwind or safety_exit):
                    raise InvariantViolation("FULL target reservation exceeds x2")
            if setup.add_on_lock and state.order_lifecycle is OrderLifecycle.ADDON_PENDING:
                raise InvariantViolation("a locked setup cannot have an add-on entry pending")
            if not self.config.addon_enabled and setup.addon_target_notional != ZERO:
                raise InvariantViolation("add-on-disabled ablation must reserve zero add-on target")

    def _dispatch(self, event: DomainEvent, emitted: list[ExternalIntent]) -> None:
        if isinstance(event, AccountEquityUpdated):
            self._on_equity(event)
        elif isinstance(event, MarkingBarClosed):
            self._on_marking_bar(event)
        elif isinstance(event, BarClosed):
            self._on_bar(event, emitted)
        elif isinstance(event, OrderSubmitted):
            self._on_order_submitted(event)
        elif isinstance(event, OrderAccepted):
            self._on_order_accepted(event)
        elif isinstance(event, (OrderRejected, OrderCanceled, OrderTimedOut)):
            self._on_order_terminal(event, emitted)
        elif isinstance(event, (OrderPartiallyFilled, OrderFilled)):
            self._on_fill(event, emitted)
        elif isinstance(event, PositionChanged):
            self._on_position_changed(event, emitted)
        elif isinstance(event, PositionClosed):
            self._on_position_closed(event, emitted)
        elif isinstance(event, FundingApplied):
            self._on_funding(event, emitted)
        elif isinstance(event, RiskLimitTriggered):
            self._on_risk_limit(event, emitted)
        elif isinstance(event, CloseRequested):
            self._on_close_requested(event, emitted)
        elif isinstance(event, RecoverySnapshotLoaded):
            self._on_recovery_loaded(event, emitted)
        elif isinstance(event, ReconciliationCompleted):
            self._on_reconciliation(event, emitted)

    def _on_equity(self, event: AccountEquityUpdated) -> None:
        validate_decimal_event(event.equity, "equity", positive=True)
        self.state.latest_confirmed_equity = event.equity

    def _on_marking_bar(self, event: MarkingBarClosed) -> None:
        """Uzbraja first-touch względem BB ostatniej ukończonej H1."""

        self._validate_marking_bar(event)
        signal = self.state.signal
        signal.last_marking_close_time_utc = event.close_time_utc
        signal.marking_bars_in_phase += 1
        setup = self.state.setup
        flat_eligible = (
            not self.state.recovery_mode
            and setup is None
            and self.state.position_build is PositionBuild.FLAT
            and self.state.order_lifecycle is OrderLifecycle.NONE
        )
        if not flat_eligible or signal.armed_side is not None or not signal.recent_bars:
            return
        previous_h1 = signal.recent_bars[-1]
        touch_long = event.low <= previous_h1.bb_lower
        touch_short = event.high >= previous_h1.bb_upper
        if touch_long == touch_short:
            return
        signal.armed_side = Side.LONG if touch_long else Side.SHORT
        signal.armed_bars_remaining = self.config.arm_expiry_bars
        signal.touch_bar_id = event.bar_id
        self._increment("marking_first_touches")

    def _on_bar(self, event: BarClosed, emitted: list[ExternalIntent]) -> None:
        self._validate_bar(event)
        state = self.state
        if state.risk_mode is RiskMode.SCOUT:
            self._increment("scout_closed_bars")
        setup = state.setup
        flat_eligible = (
            not state.recovery_mode
            and setup is None
            and state.position_build is PositionBuild.FLAT
            and state.order_lifecycle is OrderLifecycle.NONE
        )
        addon_observable = (
            self.config.addon_enabled
            and not state.recovery_mode
            and setup is not None
            and state.base_leg.quantity > ZERO
            and state.addon_leg.quantity == ZERO
            and not setup.add_on_lock
            and state.order_lifecycle is OrderLifecycle.NONE
            and state.position_build is PositionBuild.BASE
        )
        result = evaluate_bar(
            self.config,
            state.signal,
            SignalContext(
                flat_entry_eligible=flat_eligible,
                exposed=state.real_open_quantity > ZERO,
                addon_observable=addon_observable,
                addon_opportunity_consumed=(
                    False if setup is None else setup.addon_opportunity_consumed
                ),
                setup_side=None if setup is None else setup.side,
                reaction_bar=None if setup is None else setup.reaction_bar,
                marking_enabled=self.config.marking_timeframe is not None,
            ),
            event,
        )
        state.signal = result.memory
        state.signal.marking_bars_in_phase = 0
        if result.base_reaction is not None:
            self._create_base_setup(event, result.base_reaction.side, emitted)
        if result.addon_trigger is not None:
            self._consume_addon_fact(event, result.addon_trigger, emitted)
        if result.target is not None:
            self._upsert_take_profit(event, result.target, emitted)

    def _create_base_setup(
        self,
        event: BarClosed,
        side: Side,
        emitted: list[ExternalIntent],
    ) -> None:
        equity = self.state.latest_confirmed_equity
        if equity is None:
            self.state.diagnostics.append("BASE_REACTION_WITHOUT_CONFIRMED_EQUITY")
            return
        multiplier = (
            self.config.base_exposure_full
            if self.state.risk_mode is RiskMode.FULL
            else self.config.base_exposure_scout
        )
        target = equity * multiplier
        quantity = self._round_quantity(target / event.close)
        if not self._meets_minimum(quantity, event.close):
            self.state.diagnostics.append("BASE_LOCAL_MINIMUM_REJECTION")
            self._increment("base_local_rejections")
            return
        setup_id = self._stable_id("setup", event.bar_id, side.value, length=24)
        addon_target = (
            target if self.state.risk_mode is RiskMode.FULL and self.config.addon_enabled else ZERO
        )
        self.state.setup = SetupState(
            setup_id=setup_id,
            side=side,
            reaction_bar=event.snapshot(),
            setup_start_equity=equity,
            exposure_multiplier=multiplier,
            base_target_notional=target,
            addon_target_notional=addon_target,
            base_requested_quantity=quantity,
        )
        self.state.base_leg = VirtualLeg()
        self.state.addon_leg = VirtualLeg()
        # Funding settlement IDs are a global exact dedupe set, even though the
        # monetary ledger itself is reset for the new setup.
        funding_ids = set(self.state.pnl.funding_settlement_ids)
        self.state.pnl = PnlLedger(funding_settlement_ids=funding_ids)
        for key in (
            "current_commissions",
            "current_funding",
            "current_slippage_cost",
            "current_realized_notional_drift",
        ):
            self.state.telemetry[key] = ZERO
        self._set_max(
            "max_committed_exposure_multiplier",
            (target + addon_target) / equity,
        )
        self.state.order_lifecycle = OrderLifecycle.BASE_PENDING
        key = f"base-entry:{setup_id}"
        ids = self._intent_ids(key)
        intent = SubmitBaseOrder(
            **self._common_intent_kwargs(event, key, ids),
            client_order_id=ids.client_order_id,
            side=side,
            quantity=quantity,
            reference_price=event.close,
            target_notional=target,
        )
        self._emit(intent, emitted)
        self._register_order_intent(intent, OrderRole.BASE_ENTRY)
        self._increment("base_entries")
        if self.state.risk_mode is RiskMode.SCOUT:
            self._increment("scout_setups")

    def _consume_addon_fact(
        self,
        event: BarClosed,
        fact: AddonTriggerFact,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None:
            return
        if not self.config.addon_enabled:
            return
        self._increment("addon_trigger_facts")
        policy = self.config.addon_trigger_policy
        consumes_first_opportunity = policy is not AddonTriggerPolicy.STOCH_CROSS
        if consumes_first_opportunity:
            setup.addon_opportunity_consumed = True
        if self.state.risk_mode is RiskMode.SCOUT:
            self.state.diagnostics.append(f"SCOUT_ADDON_FACT:{fact.trigger_id}")
            return
        if not fact.preview_valid:
            self.state.diagnostics.append(
                f"ADDON_PREVIEW_REJECTED:{fact.trigger_id}:{fact.invalid_reason}"
            )
            self._increment("addon_rejections")
            return
        if not self._addon_eligible():
            return
        quantity = self._round_quantity(setup.addon_target_notional / fact.reference_price)
        if not self._meets_minimum(quantity, fact.reference_price):
            self.state.diagnostics.append("ADDON_LOCAL_MINIMUM_REJECTION")
            self._increment("addon_rejections")
            return
        committed_after_submit = self._committed_entry_notional() + setup.addon_target_notional
        if committed_after_submit > Decimal("2") * setup.setup_start_equity:
            setup.addon_opportunity_consumed = True
            self.state.diagnostics.append("ADDON_COMMITTED_EXPOSURE_CAP_REJECTION")
            self._increment("addon_rejections")
            return
        setup.addon_opportunity_consumed = True
        setup.addon_requested_quantity = quantity
        setup.addon_trigger_id = fact.trigger_id
        setup.addon_trigger_kind = fact.trigger_kind
        setup.addon_structural_stop = fact.structural_stop
        self.state.order_lifecycle = OrderLifecycle.ADDON_PENDING
        key = f"addon-entry:{setup.setup_id}:{fact.trigger_id}"
        ids = self._intent_ids(key)
        intent = SubmitAddonOrder(
            **self._common_intent_kwargs(event, key, ids),
            client_order_id=ids.client_order_id,
            side=setup.side,
            quantity=quantity,
            reference_price=fact.reference_price,
            target_notional=setup.addon_target_notional,
            trigger_id=fact.trigger_id,
            trigger_kind=fact.trigger_kind,
            structural_stop=fact.structural_stop,
        )
        if self._emit(intent, emitted):
            self._increment("addon_intents")
        self._register_order_intent(intent, OrderRole.ADDON_ENTRY)
        self._increment("addon_submissions")

    def _upsert_take_profit(
        self,
        event: EventEnvelope,
        target: TargetFact,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None or self.state.real_open_quantity <= ZERO:
            return
        if self.state.order_lifecycle is OrderLifecycle.EXIT_PENDING:
            return
        if setup.current_tp == target.trigger_price and setup.tp_client_order_id is not None:
            return
        if setup.tp_client_order_id is None:
            key = f"tp:{setup.setup_id}:{target.trigger_price}"
            ids = self._intent_ids(key)
            submit_intent = SubmitTakeProfit(
                **self._common_intent_kwargs(event, key, ids),
                client_order_id=ids.client_order_id,
                side=setup.side.exit_side,
                reference_quantity=self.state.real_open_quantity,
                trigger_price=target.trigger_price,
            )
            self._emit(submit_intent, emitted)
            self._register_order_intent(submit_intent, OrderRole.TAKE_PROFIT)
            setup.tp_client_order_id = ids.client_order_id
        else:
            previous = setup.tp_client_order_id
            key = f"tp-replace:{setup.setup_id}:{previous}:{target.trigger_price}"
            ids = self._intent_ids(key)
            replace_intent = ReplaceOrder(
                **self._common_intent_kwargs(event, key, ids),
                previous_client_order_id=previous,
                client_order_id=ids.client_order_id,
                role=OrderRole.TAKE_PROFIT,
                side=setup.side.exit_side,
                quantity=self.state.real_open_quantity,
                trigger_price=target.trigger_price,
                close_position=True,
                reduce_only=False,
            )
            self._emit(replace_intent, emitted)
            self._register_replacement(replace_intent)
            setup.tp_client_order_id = ids.client_order_id
        setup.current_tp = target.trigger_price

    def _on_order_submitted(self, event: OrderSubmitted) -> None:
        client_id = self._client_id(event)
        validate_identifier(event.intent_id, "intent_id")
        validate_identifier(event.venue_order_id, "venue_order_id", optional=True)
        validate_decimal_event(event.requested_quantity, "requested_quantity", positive=True)
        if event.close_position and event.reduce_only:
            raise InvariantViolation("submitted order combines close_position and reduce_only")
        record = self.state.orders.get(client_id)
        if record is None:
            raise InvariantViolation("submitted order has no stored intent")
        self._validate_order_event_scope(event, record, require_live=False)
        flags_match = (
            record.reduce_only is event.reduce_only
            and record.close_position is event.close_position
        )
        if (
            record.role is OrderRole.CLOSE_ALL
            and not record.reduce_only
            and not record.close_position
        ):
            flags_match = event.reduce_only is not event.close_position
        if (
            record.intent_id != event.intent_id
            or record.role is not event.role
            or record.requested_quantity != event.requested_quantity
            or record.side is not event.side
            or not flags_match
        ):
            raise InvariantViolation("submitted order does not match stored intent")
        self._ack_outbox_intent(record.intent_id)
        if record.status in {OrderStatus.INTENDED, OrderStatus.SUBMITTED}:
            record.status = OrderStatus.SUBMITTED
            record.venue_order_id = event.venue_order_id
            record.reduce_only = event.reduce_only
            record.close_position = event.close_position

    def _on_order_accepted(self, event: OrderAccepted) -> None:
        validate_identifier(event.venue_order_id, "venue_order_id", optional=True)
        order = self._known_order(event)
        self._validate_order_event_scope(event, order, require_live=False)
        if order.role is not event.role:
            raise InvariantViolation("accepted role does not match order")
        self._ack_outbox_intent(order.intent_id)
        if order.status in {
            OrderStatus.INTENDED,
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
        }:
            order.status = OrderStatus.ACCEPTED
            order.venue_order_id = event.venue_order_id or order.venue_order_id

    def _on_order_terminal(
        self,
        event: OrderRejected | OrderCanceled | OrderTimedOut,
        emitted: list[ExternalIntent],
    ) -> None:
        order = self._known_order(event)
        self._validate_order_event_scope(event, order, require_live=False)
        if order.role is not event.role:
            raise InvariantViolation("terminal role does not match order")
        self._ack_outbox_intent(order.intent_id)
        cumulative = event.cumulative_filled_quantity
        validate_decimal_event(cumulative, "cumulative_filled_quantity")
        if cumulative != order.filled_quantity:
            raise InvariantViolation("terminal cumulative quantity does not match applied fills")
        if order.status.terminal:
            return
        expected_cancel = order.status is OrderStatus.CANCEL_PENDING
        timed_out = isinstance(event, OrderTimedOut)
        if isinstance(event, OrderRejected):
            order.status = OrderStatus.REJECTED
            reason = event.reason
        elif isinstance(event, OrderCanceled):
            order.status = OrderStatus.CANCELED
            reason = event.reason
        else:
            validate_utc(event.deadline_at_utc, "deadline_at_utc")
            if event.occurred_at_utc < event.deadline_at_utc:
                raise ValueError("OrderTimedOut cannot occur before its deadline")
            order.status = OrderStatus.TIMED_OUT
            order.deadline_at_utc = event.deadline_at_utc
            reason = f"timeout:{event.observed_status}"
        self.state.diagnostics.append(f"ORDER_TERMINAL:{order.role.value}:{reason}")

        if isinstance(event, OrderCanceled):
            self._ack_cancel_outbox(order.client_order_id)

        current_setup_id = None if self.state.setup is None else self.state.setup.setup_id
        if order.setup_id != current_setup_id:
            return
        if self.state.setup is not None and self.state.setup.final_close_reason is not None:
            return
        if (
            self.state.setup is not None
            and self.state.setup.pending_close_reason is not None
            and order.role.increases_exposure
        ):
            return

        if timed_out:
            self.state.recovery_mode = True
            self._emit(
                self._reconciliation_intent(event, f"uncertain timeout: {order.role.value}"),
                emitted,
            )
            return
        if expected_cancel and isinstance(event, OrderCanceled):
            return

        if order.role is OrderRole.BASE_ENTRY:
            self.state.order_lifecycle = OrderLifecycle.NONE
            if self.state.base_leg.quantity == ZERO:
                self.state.setup = None
                self.state.signal.reaction_bar = None
        elif order.role is OrderRole.ADDON_ENTRY:
            self.state.order_lifecycle = OrderLifecycle.NONE
            if (
                self.config.addon_trigger_policy is AddonTriggerPolicy.STOCH_CROSS
                and self.state.addon_leg.quantity == ZERO
                and self.state.setup is not None
            ):
                self.state.setup.addon_opportunity_consumed = False
            self._increment("addon_rejections")
        elif order.role in {OrderRole.ADDON_STOP, OrderRole.REDUCE_ADDON}:
            if self.state.addon_leg.quantity > ZERO:
                self.state.order_lifecycle = OrderLifecycle.REDUCE_PENDING
                self._emit(
                    self._reconciliation_intent(event, "add-on reduction terminal with exposure"),
                    emitted,
                )
            else:
                self.state.order_lifecycle = OrderLifecycle.NONE
        elif order.role in {OrderRole.CLOSE_ALL, OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT}:
            self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
            if self.state.real_open_quantity > ZERO:
                self._emit(
                    self._reconciliation_intent(event, "whole exit terminal with exposure"),
                    emitted,
                )

    def _on_fill(self, event: FillEnvelope, emitted: list[ExternalIntent]) -> None:
        self._validate_fill(event)
        order = self._order_for_fill(event)
        if (
            event.role.increases_exposure
            and self.state.setup is not None
            and self.state.setup.pending_close_reason is not None
        ):
            raise InvariantViolation("entry fill arrived after close initiation")
        self._ack_outbox_intent(order.intent_id)
        expected_cumulative = order.filled_quantity + event.last_quantity
        if event.cumulative_quantity != expected_cumulative:
            raise InvariantViolation("fill cumulative quantity has a gap or overlap")
        if (
            event.role
            in {
                OrderRole.BASE_ENTRY,
                OrderRole.ADDON_ENTRY,
                OrderRole.ADDON_STOP,
                OrderRole.REDUCE_ADDON,
            }
            and event.cumulative_quantity > order.requested_quantity
        ):
            raise InvariantViolation("exact-quantity fill exceeds requested quantity")
        order.filled_quantity = event.cumulative_quantity
        order.status = (
            OrderStatus.FILLED if isinstance(event, OrderFilled) else OrderStatus.PARTIALLY_FILLED
        )
        self.state.pnl.commissions += event.commission
        self.state.telemetry["current_commissions"] = self.state.pnl.commissions
        if event.benchmark_price is not None:
            self.state.pnl.realized_slippage_cost += (
                abs(event.price - event.benchmark_price) * event.last_quantity
            )
            self.state.telemetry["current_slippage_cost"] = self.state.pnl.realized_slippage_cost
        if event.role is OrderRole.BASE_ENTRY:
            self._apply_entry_fill(event, self.state.base_leg, is_addon=False, emitted=emitted)
        elif event.role is OrderRole.ADDON_ENTRY:
            self._apply_entry_fill(event, self.state.addon_leg, is_addon=True, emitted=emitted)
        elif event.role in {OrderRole.ADDON_STOP, OrderRole.REDUCE_ADDON}:
            self._apply_addon_reduction(event, emitted)
        else:
            self._apply_whole_exit_fill(event, emitted)

    def _apply_entry_fill(
        self,
        event: FillEnvelope,
        leg: VirtualLeg,
        *,
        is_addon: bool,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None:
            raise InvariantViolation("entry fill has no live setup")
        old_quantity = leg.quantity
        new_quantity = old_quantity + event.last_quantity
        old_notional = ZERO if leg.fill_vwap is None else leg.fill_vwap * old_quantity
        leg.quantity = new_quantity
        leg.fill_vwap = (old_notional + event.price * event.last_quantity) / new_quantity
        leg.fill_execution_ids.add(event.execution_id)
        leg.fill_execution_order.append(event.execution_id)
        leg.fill_quantities[event.execution_id] = event.last_quantity
        leg.remaining_fill_quantities[event.execution_id] = event.last_quantity
        self.state.real_open_quantity = self.state.total_logical_quantity
        setup.actual_entry_notional += event.price * event.last_quantity
        base_cumulative = self.state.base_leg.quantity + self.state.base_leg.reduced_quantity
        addon_cumulative = self.state.addon_leg.quantity + self.state.addon_leg.reduced_quantity
        base_fraction = min(Decimal(1), base_cumulative / setup.base_requested_quantity)
        addon_fraction = (
            ZERO
            if setup.addon_requested_quantity == ZERO
            else min(Decimal(1), addon_cumulative / setup.addon_requested_quantity)
        )
        target_to_date = (
            setup.base_target_notional * base_fraction
            + setup.addon_target_notional * addon_fraction
        )
        setup.realized_notional_drift = setup.actual_entry_notional - target_to_date
        self.state.telemetry["current_realized_notional_drift"] = setup.realized_notional_drift
        self._set_max(
            "max_actual_gross_exposure_multiplier",
            setup.actual_entry_notional / setup.setup_start_equity,
        )

        cap_multiplier = Decimal("0.1") if self.state.risk_mode is RiskMode.SCOUT else Decimal("2")
        if self._committed_entry_notional() > cap_multiplier * setup.setup_start_equity:
            setup.add_on_lock = True
            self.state.recovery_mode = True
            self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
            self.state.diagnostics.append("COMMITTED_EXPOSURE_CAP_BREACH_AFTER_FILL")
            self._cancel_all_orphans(
                event,
                emitted,
                reason="COMMITTED_CAP_BREACH",
            )
            close = self._close_all_intent(
                event,
                CloseReason.ENGINE_ERROR,
                "committed-exposure-cap",
                emitted=emitted,
            )
            self._emit(close, emitted)
            return

        terminal = isinstance(event, OrderFilled)
        if is_addon:
            self._increment("addon_fills")
            structural_stop = setup.addon_structural_stop
            if structural_stop is None or leg.fill_vwap is None:
                raise InvariantViolation("add-on fill lacks structural-stop provenance")
            valid, _distance, reason = validate_structural_stop(
                side=setup.side,
                structural_stop=structural_stop,
                fill_or_reference_price=leg.fill_vwap,
                max_distance=self.config.addon_max_sl_pct,
            )
            if not valid:
                setup.add_on_lock = True
                self.state.order_lifecycle = OrderLifecycle.REDUCE_PENDING
                self.state.diagnostics.append(f"ADDON_INVALID_AFTER_FILL:{reason}")
                self._cancel_role_orders(
                    event, {OrderRole.ADDON_ENTRY, OrderRole.ADDON_STOP}, emitted
                )
                self._emit(
                    self._reduce_addon_intent(event, leg.quantity, "INVALID_STRUCTURAL_STOP"),
                    emitted,
                )
                return
            leg.stop_level = structural_stop
            self._emit_addon_stop_child(event, event.last_quantity, structural_stop, emitted)
            self.state.order_lifecycle = (
                OrderLifecycle.NONE if terminal else OrderLifecycle.ADDON_PENDING
            )
        else:
            if leg.fill_vwap is None:
                raise InvariantViolation("base VWAP missing after fill")
            leg.stop_level = (
                leg.fill_vwap * (Decimal(1) - self.config.base_sl_pct)
                if setup.side is Side.LONG
                else leg.fill_vwap * (Decimal(1) + self.config.base_sl_pct)
            )
            self._upsert_base_stop(event, emitted)
            self._submit_current_target(event, emitted)
            self.state.order_lifecycle = (
                OrderLifecycle.NONE if terminal else OrderLifecycle.BASE_PENDING
            )

    def _apply_addon_reduction(
        self,
        event: FillEnvelope,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None or event.last_quantity > self.state.real_open_quantity:
            raise InvariantViolation("add-on reduction exceeds real open quantity")
        was_exit_pending = self.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
        addon_quantity = min(event.last_quantity, self.state.addon_leg.quantity)
        addon_pnl = ZERO
        if addon_quantity > ZERO:
            protected_execution_id: str | None = None
            if event.role is OrderRole.ADDON_STOP:
                stop_order = self.state.orders[self._client_id(event)]
                protected_execution_id = stop_order.protected_execution_id
                if protected_execution_id is None:
                    raise InvariantViolation("add-on stop lacks protected execution attribution")
            addon_pnl = self._reduce_leg(
                self.state.addon_leg,
                addon_quantity,
                event.price,
                setup.side,
                preferred_execution_id=protected_execution_id,
            )
            self.state.pnl.addon_realized_price_pnl += addon_pnl
        base_remainder = event.last_quantity - addon_quantity
        base_pnl = ZERO
        if base_remainder > ZERO:
            if not was_exit_pending:
                raise InvariantViolation("add-on reduction exceeds actual add-on quantity")
            base_pnl = self._reduce_leg(
                self.state.base_leg,
                base_remainder,
                event.price,
                setup.side,
            )
            self.state.pnl.base_realized_price_pnl += base_pnl
            self.state.diagnostics.append("LATE_ADDON_STOP_RACE_REDUCED_BASE")
        if event.role is OrderRole.ADDON_STOP:
            self.state.pnl.addon_stop_realized_pnl += addon_pnl + base_pnl
        self.state.real_open_quantity = self.state.total_logical_quantity
        if self.state.real_open_quantity == ZERO:
            setup.closing_execution_ids.append(event.execution_id)
        setup.add_on_lock = True
        if was_exit_pending:
            self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
            if self.state.real_open_quantity == ZERO:
                self._cancel_all_orphans(event, emitted)
        elif self.state.addon_leg.quantity == ZERO:
            self.state.order_lifecycle = OrderLifecycle.NONE
            self._cancel_role_orders(event, {OrderRole.ADDON_STOP}, emitted)
            self._increment("addon_stop_count")
            self._refresh_take_profit_quantity(event, emitted)
        else:
            self.state.order_lifecycle = OrderLifecycle.REDUCE_PENDING

    def _apply_whole_exit_fill(
        self,
        event: FillEnvelope,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None or event.last_quantity > self.state.real_open_quantity:
            raise InvariantViolation("whole-exit fill exceeds real open quantity")
        self._cancel_role_orders(
            event,
            {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY},
            emitted,
        )
        remaining = event.last_quantity
        addon_reduction = min(remaining, self.state.addon_leg.quantity)
        if addon_reduction > ZERO:
            addon_pnl = self._reduce_leg(
                self.state.addon_leg,
                addon_reduction,
                event.price,
                setup.side,
            )
            self.state.pnl.addon_realized_price_pnl += addon_pnl
            remaining -= addon_reduction
            self._cancel_role_orders(event, {OrderRole.ADDON_STOP}, emitted)
        if remaining > ZERO:
            base_pnl = self._reduce_leg(self.state.base_leg, remaining, event.price, setup.side)
            self.state.pnl.base_realized_price_pnl += base_pnl
        self.state.real_open_quantity = self.state.total_logical_quantity
        setup.closing_execution_ids.append(event.execution_id)
        self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
        if event.role is OrderRole.BASE_STOP:
            setup.pending_close_reason = CloseReason.BASE_SL
        elif event.role is OrderRole.TAKE_PROFIT:
            setup.pending_close_reason = CloseReason.TP
        if self.state.real_open_quantity == ZERO:
            self._cancel_all_orphans(event, emitted)
        else:
            self._refresh_take_profit_quantity(event, emitted)

    def _on_position_changed(
        self,
        event: PositionChanged,
        emitted: list[ExternalIntent],
    ) -> None:
        validate_decimal_event(event.signed_quantity, "signed_quantity")
        if event.average_price is not None:
            validate_decimal_event(event.average_price, "average_price", positive=True)
        expected_signed = self._expected_signed_quantity()
        if event.signed_quantity != expected_signed:
            self.state.recovery_mode = True
            self.state.observed_drift_signed_quantity = event.signed_quantity
            self.state.diagnostics.append(
                f"POSITION_DRIFT:expected={expected_signed}:actual={event.signed_quantity}"
            )
            self._emit(
                self._reconciliation_intent(event, "position quantity mismatch"),
                emitted,
            )
            self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
            if self.state.setup is not None:
                self.state.setup.pending_close_reason = CloseReason.ENGINE_ERROR
            self._cancel_all_orphans(
                event,
                emitted,
                reason="POSITION_DRIFT",
            )
            if event.signed_quantity != ZERO:
                self._emit(
                    self._close_all_intent(
                        event,
                        CloseReason.ENGINE_ERROR,
                        "position-drift",
                        quantity=abs(event.signed_quantity),
                        side=(Side.SHORT if event.signed_quantity > ZERO else Side.LONG),
                        emitted=emitted,
                    ),
                    emitted,
                )
            return
        self.state.observed_drift_signed_quantity = None
        self.state.real_average_price = event.average_price

    def _on_position_closed(
        self,
        event: PositionClosed,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None:
            self.state.recovery_mode = True
            self.state.diagnostics.append("STALE_POSITION_CLOSED_WITHOUT_SETUP")
            self._emit(
                self._reconciliation_intent(event, "stale PositionClosed without setup"),
                emitted,
            )
            return
        if event.setup_id != setup.setup_id:
            self.state.recovery_mode = True
            self.state.diagnostics.append(
                f"STALE_POSITION_CLOSED:{event.setup_id}:{setup.setup_id}"
            )
            self._emit(
                self._reconciliation_intent(event, "stale PositionClosed attribution"),
                emitted,
            )
            return
        for name, value in (
            ("realized_price_pnl", event.realized_price_pnl),
            ("commissions", event.commissions),
            ("funding", event.funding),
            ("realized_slippage_cost", event.realized_slippage_cost),
        ):
            validate_decimal_event(value, name)
        if event.commissions < ZERO or event.realized_slippage_cost < ZERO:
            raise InvariantViolation("finalized costs must be non-negative")
        for execution_id in event.closing_execution_ids:
            validate_identifier(execution_id, "closing execution ID")
        if len(set(event.closing_execution_ids)) != len(event.closing_execution_ids):
            raise InvariantViolation("closing execution IDs must be unique")
        fingerprint = self._position_close_fingerprint(event)
        if setup.finalization_fingerprint is not None:
            if setup.finalization_fingerprint == fingerprint:
                return
            raise InvariantViolation("setup received conflicting PositionClosed finalization")
        if setup.actual_entry_notional <= ZERO:
            raise InvariantViolation("PositionClosed cannot finalize a never-filled setup")
        if (
            setup.pending_close_reason is not None
            and setup.pending_close_reason is not event.close_reason
        ):
            raise InvariantViolation("final close reason conflicts with pending causal reason")
        setup.finalization_fingerprint = fingerprint
        addon_realized = self.state.pnl.addon_realized_price_pnl
        self.state.pnl.base_realized_price_pnl = event.realized_price_pnl - addon_realized
        self.state.pnl.commissions = event.commissions
        self.state.pnl.funding = event.funding
        self.state.pnl.realized_slippage_cost = event.realized_slippage_cost
        self.state.telemetry["current_commissions"] = event.commissions
        self.state.telemetry["current_funding"] = event.funding
        self.state.telemetry["current_slippage_cost"] = event.realized_slippage_cost
        self.state.processed_execution_ids.update(event.closing_execution_ids)
        for execution_id in event.closing_execution_ids:
            if execution_id not in setup.closing_execution_ids:
                setup.closing_execution_ids.append(execution_id)
        for leg in (self.state.base_leg, self.state.addon_leg):
            leg.reduced_quantity += leg.quantity
            leg.quantity = ZERO
            for execution_id in leg.remaining_fill_quantities:
                leg.remaining_fill_quantities[execution_id] = ZERO
        self.state.real_open_quantity = ZERO
        self.state.real_average_price = None
        setup.final_close_reason = event.close_reason
        setup.pending_close_reason = event.close_reason
        self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
        self._cancel_all_orphans(event, emitted)

    def _on_funding(
        self,
        event: FundingApplied,
        emitted: list[ExternalIntent],
    ) -> None:
        validate_decimal_event(event.amount, "funding amount")
        validate_identifier(event.settlement_id, "funding settlement_id")
        # Settlement dedupe is global even when no live setup can receive the amount.
        self.state.pnl.funding_settlement_ids.add(event.settlement_id)
        setup = self.state.setup
        if setup is None:
            self.state.diagnostics.append(f"UNALLOCATED_FUNDING:{event.settlement_id}")
            if event.amount != ZERO:
                self.state.unresolved_funding_settlement_ids.add(event.settlement_id)
                self.state.recovery_mode = True
                self._emit(
                    self._reconciliation_intent(
                        event, "funding arrived without attributable setup"
                    ),
                    emitted,
                )
            return
        if event.setup_id != setup.setup_id:
            raise InvariantViolation("funding attribution does not match live setup")
        if self.state.real_open_quantity <= ZERO and setup.final_close_reason is None:
            raise InvariantViolation("funding cannot be allocated to an unfilled setup")
        if setup.final_close_reason is not None:
            self.state.diagnostics.append(f"FUNDING_DURING_FINALIZATION:{event.settlement_id}")
        self.state.pnl.funding += event.amount
        self.state.telemetry["current_funding"] = self.state.pnl.funding
        self._increment("funding_settlements")

    def _on_risk_limit(
        self,
        event: RiskLimitTriggered,
        emitted: list[ExternalIntent],
    ) -> None:
        validate_decimal_event(event.observed_equity, "observed_equity")
        validate_decimal_event(event.observed_exposure, "observed_exposure")
        if self.state.real_open_quantity <= ZERO or self.state.setup is None:
            return
        self.state.setup.pending_close_reason = CloseReason.RISK_LIMIT
        self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
        self._cancel_role_orders(
            event,
            {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY},
            emitted,
        )
        self._emit(
            self._close_all_intent(
                event,
                CloseReason.RISK_LIMIT,
                event.limit_id,
                emitted=emitted,
            ),
            emitted,
        )

    def _on_close_requested(
        self,
        event: CloseRequested,
        emitted: list[ExternalIntent],
    ) -> None:
        if event.close_reason not in {
            CloseReason.MANUAL,
            CloseReason.LIQUIDATION,
            CloseReason.ENGINE_ERROR,
            CloseReason.RISK_LIMIT,
        }:
            raise ValueError("CloseRequested accepts only forced close reasons")
        if self.state.real_open_quantity <= ZERO or self.state.setup is None:
            return
        self.state.setup.pending_close_reason = event.close_reason
        self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
        self._cancel_role_orders(
            event,
            {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY},
            emitted,
        )
        self._emit(
            self._close_all_intent(
                event,
                event.close_reason,
                event.reason,
                emitted=emitted,
            ),
            emitted,
        )

    def _on_recovery_loaded(
        self,
        event: RecoverySnapshotLoaded,
        emitted: list[ExternalIntent],
    ) -> None:
        if event.schema_version != "mms_state/1":
            raise InvariantViolation("recovery event schema mismatch")
        if event.snapshot_id != self.state.snapshot_id:
            raise InvariantViolation("recovery event snapshot identity mismatch")
        if (
            len(event.checksum) != 64
            or event.checksum.lower() != event.checksum
            or any(character not in "0123456789abcdef" for character in event.checksum)
        ):
            raise InvariantViolation("recovery event checksum is not a SHA-256 attestation")
        if self.state.verified_snapshot_checksum is None:
            raise InvariantViolation("recovery event has no verified snapshot attestation")
        if event.checksum != self.state.verified_snapshot_checksum:
            raise InvariantViolation("recovery event checksum does not attest restored snapshot")
        self.state.recovery_mode = True
        self._emit(self._reconciliation_intent(event, "snapshot loaded"), emitted)

    def _on_reconciliation(
        self,
        event: ReconciliationCompleted,
        emitted: list[ExternalIntent],
    ) -> None:
        validate_decimal_event(event.signed_open_quantity, "signed_open_quantity")
        live_setup_id = None if self.state.setup is None else self.state.setup.setup_id
        if event.setup_id != live_setup_id:
            raise InvariantViolation("reconciliation setup attribution mismatch")
        if event.average_price is not None:
            validate_decimal_event(event.average_price, "average_price", positive=True)
        if isinstance(event.as_of_sequence, bool) or not isinstance(event.as_of_sequence, int):
            raise TypeError("reconciliation as_of_sequence must be an integer")
        if event.as_of_sequence < 0:
            raise ValueError("reconciliation as_of_sequence must be non-negative")
        if (
            self.state.last_reconciliation_sequence is not None
            and event.as_of_sequence < self.state.last_reconciliation_sequence
        ):
            raise InvariantViolation("stale reconciliation snapshot")
        for client_id in event.open_client_order_ids:
            validate_identifier(client_id, "open client order ID")
        if len(set(event.open_client_order_ids)) != len(event.open_client_order_ids):
            raise ValueError("open client order IDs must be unique")
        self._validate_reconciled_orders(event)
        for intent_id in event.acknowledged_intent_ids:
            validate_identifier(intent_id, "acknowledged intent ID")
            self._ack_outbox_intent(intent_id)
        self.state.outbox = [
            intent for intent in self.state.outbox if not isinstance(intent, RequestReconciliation)
        ]
        actual = event.signed_open_quantity
        expected = self._expected_signed_quantity()
        self.state.last_reconciliation_sequence = event.as_of_sequence
        open_ids = set(event.open_client_order_ids)
        if event.open_orders:
            for client_id in open_ids:
                known = self.state.orders.get(client_id)
                if known is not None:
                    self._ack_outbox_intent(known.intent_id)
        for intent in tuple(self.state.outbox):
            if isinstance(intent, CancelOrder) and intent.target_client_order_id not in open_ids:
                self._ack_cancel_outbox(intent.target_client_order_id)
        known_active = {
            client_id for client_id, order in self.state.orders.items() if order.status.active
        }
        orphans = open_ids - known_active
        for client_id in sorted(orphans):
            self._emit(self._cancel_intent(event, client_id, "RECONCILIATION_ORPHAN"), emitted)
        if actual != expected:
            self.state.recovery_mode = True
            self.state.observed_drift_signed_quantity = actual
            self.state.diagnostics.append(
                f"RECONCILIATION_POSITION_MISMATCH:expected={expected}:actual={actual}"
            )
            self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
            if self.state.setup is not None:
                self.state.setup.pending_close_reason = CloseReason.ENGINE_ERROR
            self._cancel_all_orphans(
                event,
                emitted,
                reason="RECONCILIATION_DRIFT",
            )
            if actual != ZERO:
                self._emit(
                    self._close_all_intent(
                        event,
                        CloseReason.ENGINE_ERROR,
                        "reconciliation-mismatch",
                        quantity=abs(actual),
                        side=Side.SHORT if actual > ZERO else Side.LONG,
                        emitted=emitted,
                    ),
                    emitted,
                )
            else:
                self._emit(
                    self._reconciliation_intent(event, "unattributed reconciliation mismatch"),
                    emitted,
                )
            return
        if self.state.unresolved_funding_settlement_ids:
            self.state.recovery_mode = True
            unresolved = ",".join(sorted(self.state.unresolved_funding_settlement_ids))
            self.state.diagnostics.append(f"UNRESOLVED_FUNDING_BLOCK:{unresolved}")
            self._emit(
                self._reconciliation_intent(
                    event,
                    f"unresolved unattributed funding: {unresolved}",
                ),
                emitted,
            )
            return
        self.state.observed_drift_signed_quantity = None
        if actual == ZERO:
            if open_ids:
                active_entry_ids = {
                    client_id
                    for client_id, order in self.state.orders.items()
                    if order.status.active and order.role.increases_exposure
                }
                if (
                    self.state.order_lifecycle
                    in {OrderLifecycle.BASE_PENDING, OrderLifecycle.ADDON_PENDING}
                    and open_ids <= active_entry_ids
                ):
                    self.state.recovery_mode = False
                    return
                self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                self.state.recovery_mode = True
                return
            setup = self.state.setup
            if setup is not None and setup.final_close_reason is not None:
                self.state.outbox.clear()
                self._apply_sequential_transition(setup.final_close_reason)
                self.state.order_lifecycle = OrderLifecycle.NONE
                self.state.recovery_mode = False
                self.state.setup = None
                self.state.signal.reaction_bar = None
                return
            if (
                setup is not None
                and setup.actual_entry_notional > ZERO
                and (
                    self.state.order_lifecycle is OrderLifecycle.EXIT_PENDING
                    or setup.pending_close_reason is not None
                )
            ):
                self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                self.state.recovery_mode = True
                for order in self.state.orders.values():
                    if order.status.active or order.status is OrderStatus.CANCEL_PENDING:
                        order.status = OrderStatus.CANCELED
                self.state.outbox.clear()
                self._emit(
                    self._reconciliation_intent(
                        event,
                        "flat venue truth awaiting PositionClosed ledger finalization",
                    ),
                    emitted,
                )
                return
            if self.state.order_lifecycle is OrderLifecycle.BASE_PENDING and setup is not None:
                base_entries = [
                    order
                    for order in self.state.orders.values()
                    if order.setup_id == setup.setup_id and order.role is OrderRole.BASE_ENTRY
                ]
                if any(order.status is OrderStatus.TIMED_OUT for order in base_entries):
                    self.state.outbox.clear()
                    self.state.order_lifecycle = OrderLifecycle.NONE
                    self.state.recovery_mode = False
                    self.state.setup = None
                    self.state.signal.reaction_bar = None
                    return
                confirmed_absent = [
                    order
                    for order in base_entries
                    if order.status
                    in {
                        OrderStatus.SUBMITTED,
                        OrderStatus.ACCEPTED,
                        OrderStatus.PARTIALLY_FILLED,
                    }
                ]
                if confirmed_absent:
                    for order in confirmed_absent:
                        order.status = OrderStatus.CANCELED
                    self.state.outbox.clear()
                    self.state.order_lifecycle = OrderLifecycle.NONE
                    self.state.recovery_mode = False
                    self.state.setup = None
                    self.state.signal.reaction_bar = None
                    return
                replayed = self._replay_intended_orders(
                    {order.client_order_id for order in base_entries}, emitted
                )
                if replayed:
                    self.state.recovery_mode = False
                    return
                self.state.recovery_mode = True
                self._emit(
                    self._reconciliation_intent(
                        event, "base entry absent without replayable outbox"
                    ),
                    emitted,
                )
                return
            self.state.order_lifecycle = OrderLifecycle.NONE
            self.state.recovery_mode = False
            self.state.setup = None
            self.state.signal.reaction_bar = None
            for order in self.state.orders.values():
                if order.status.active:
                    order.status = OrderStatus.CANCELED
            self.state.outbox.clear()
            return
        missing = known_active - open_ids
        unresolved_missing = missing - self._replay_intended_orders(missing, emitted)
        if unresolved_missing:
            missing_orders = [
                self.state.orders[client_id] for client_id in sorted(unresolved_missing)
            ]
            for order in missing_orders:
                order.status = OrderStatus.CANCELED
                self._ack_outbox_intent(order.intent_id)
            if any(order.role.is_protective for order in missing_orders):
                setup = self.state.setup
                if setup is None:
                    raise InvariantViolation("missing protection has no live setup")
                self.state.recovery_mode = True
                self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                self._cancel_all_orphans(
                    event,
                    emitted,
                    reason="MISSING_PROTECTION",
                )
                close = self._close_all_intent(
                    event,
                    CloseReason.ENGINE_ERROR,
                    "missing-protection:" + ",".join(sorted(unresolved_missing)),
                    quantity=abs(actual),
                    side=Side.SHORT if actual > ZERO else Side.LONG,
                    emitted=emitted,
                )
                self._emit(close, emitted)
                return
            if any(order.role is OrderRole.CLOSE_ALL for order in missing_orders):
                setup = self.state.setup
                if setup is None:
                    raise InvariantViolation("missing whole exit has no live setup")
                self.state.recovery_mode = True
                self.state.order_lifecycle = OrderLifecycle.EXIT_PENDING
                reason = setup.pending_close_reason or CloseReason.ENGINE_ERROR
                close = self._close_all_intent(
                    event,
                    reason,
                    f"missing-whole-exit:{event.as_of_sequence}",
                    quantity=abs(actual),
                    side=Side.SHORT if actual > ZERO else Side.LONG,
                    emitted=emitted,
                )
                self._emit(close, emitted)
                return
            if any(not order.role.increases_exposure for order in missing_orders):
                self.state.recovery_mode = True
                self._emit(
                    self._reconciliation_intent(
                        event,
                        "known order missing from venue: " + ",".join(sorted(unresolved_missing)),
                    ),
                    emitted,
                )
                return
        setup = self.state.setup
        if setup is not None and self.state.order_lifecycle in {
            OrderLifecycle.BASE_PENDING,
            OrderLifecycle.ADDON_PENDING,
        }:
            pending_role = (
                OrderRole.BASE_ENTRY
                if self.state.order_lifecycle is OrderLifecycle.BASE_PENDING
                else OrderRole.ADDON_ENTRY
            )
            terminal_entries = [
                order
                for order in self.state.orders.values()
                if order.setup_id == setup.setup_id
                and order.role is pending_role
                and order.status
                in {
                    OrderStatus.TIMED_OUT,
                    OrderStatus.CANCELED,
                    OrderStatus.REJECTED,
                }
            ]
            if any(order.client_order_id in open_ids for order in terminal_entries):
                self.state.recovery_mode = True
                return
            if terminal_entries:
                self.state.order_lifecycle = OrderLifecycle.NONE
                if (
                    self.config.addon_trigger_policy is AddonTriggerPolicy.STOCH_CROSS
                    and self.state.addon_leg.quantity == ZERO
                ):
                    setup.addon_opportunity_consumed = False
        self.state.recovery_mode = False

    def _validate_reconciled_orders(self, event: ReconciliationCompleted) -> None:
        if not event.open_orders:
            return
        by_id = {order.client_order_id: order for order in event.open_orders}
        if len(by_id) != len(event.open_orders):
            raise ValueError("reconciled open orders must have unique client IDs")
        if set(by_id) != set(event.open_client_order_ids):
            raise ValueError("reconciled order details must match open client IDs")
        for client_id, reconciled in by_id.items():
            validate_identifier(client_id, "reconciled client order ID")
            validate_identifier(
                reconciled.venue_order_id,
                "reconciled venue order ID",
                optional=True,
            )
            validate_identifier(
                reconciled.setup_id,
                "reconciled setup ID",
                optional=True,
            )
            for name, value in (
                ("requested_quantity", reconciled.requested_quantity),
                ("filled_quantity", reconciled.filled_quantity),
            ):
                validate_decimal_event(value, f"reconciled {name}")
            if (
                reconciled.requested_quantity <= ZERO
                or reconciled.filled_quantity < ZERO
                or reconciled.filled_quantity > reconciled.requested_quantity
            ):
                raise ValueError("reconciled order quantities are inconsistent")
            if not reconciled.status.active:
                raise ValueError("an open reconciled order must have an active status")
            if reconciled.close_position and reconciled.reduce_only:
                raise ValueError("reconciled order combines close_position and reduce_only")
            known = self.state.orders.get(client_id)
            if known is None:
                continue
            identity_conflict = (
                known.role is not reconciled.role
                or known.side is not reconciled.side
                or known.requested_quantity != reconciled.requested_quantity
                or known.filled_quantity != reconciled.filled_quantity
                or known.reduce_only is not reconciled.reduce_only
                or known.close_position is not reconciled.close_position
                or (reconciled.setup_id is not None and known.setup_id != reconciled.setup_id)
                or (
                    known.venue_order_id is not None
                    and reconciled.venue_order_id is not None
                    and known.venue_order_id != reconciled.venue_order_id
                )
            )
            if identity_conflict:
                raise InvariantViolation("reconciled order identity conflicts with ledger")
            if known.status.terminal and known.status is not OrderStatus.TIMED_OUT:
                raise InvariantViolation("terminal order unexpectedly remains open at venue")
            known.venue_order_id = reconciled.venue_order_id or known.venue_order_id
            if known.status in {
                OrderStatus.INTENDED,
                OrderStatus.SUBMITTED,
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                known.status = reconciled.status

    def _replay_intended_orders(
        self,
        client_order_ids: set[str],
        emitted: list[ExternalIntent],
    ) -> set[str]:
        replayed: set[str] = set()
        for client_order_id in sorted(client_order_ids):
            order = self.state.orders.get(client_order_id)
            if order is None or order.status is not OrderStatus.INTENDED:
                continue
            for intent in self.state.outbox:
                if getattr(intent, "client_order_id", None) == client_order_id:
                    emitted.append(intent)
                    replayed.add(client_order_id)
                    self.state.diagnostics.append(f"OUTBOX_REPLAY:{intent.intent_id}")
                    break
        return replayed

    def _apply_sequential_transition(self, reason: CloseReason) -> None:
        old_risk = self.state.risk_mode
        if reason is CloseReason.BASE_SL:
            self._increment("full_base_sl_count")
        if self.config.sequential_enabled:
            if reason is CloseReason.BASE_SL:
                self.state.risk_mode = RiskMode.SCOUT
            elif (
                old_risk is RiskMode.SCOUT
                and reason is CloseReason.TP
                and self.state.pnl.setup_net_pnl > ZERO
            ):
                self.state.risk_mode = RiskMode.FULL
                self._increment("scout_to_full_rearms")
        if old_risk is RiskMode.FULL and self.state.risk_mode is RiskMode.SCOUT:
            self._increment("full_to_scout_transitions")

    def _upsert_base_stop(self, event: FillEnvelope, emitted: list[ExternalIntent]) -> None:
        setup = self.state.setup
        level = self.state.base_leg.stop_level
        if setup is None or level is None:
            raise InvariantViolation("cannot protect base without stop level")
        if setup.base_stop_client_order_id is None:
            key = f"base-stop:{setup.setup_id}:{level}"
            ids = self._intent_ids(key)
            submit_intent = SubmitBaseStop(
                **self._common_intent_kwargs(event, key, ids),
                client_order_id=ids.client_order_id,
                side=setup.side.exit_side,
                reference_quantity=self.state.real_open_quantity,
                trigger_price=level,
            )
            self._emit(submit_intent, emitted)
            self._register_order_intent(submit_intent, OrderRole.BASE_STOP)
            setup.base_stop_client_order_id = ids.client_order_id
        else:
            previous = setup.base_stop_client_order_id
            previous_order = self.state.orders.get(previous)
            if previous_order is not None and previous_order.trigger_price == level:
                return
            key = f"base-stop-replace:{setup.setup_id}:{previous}:{level}"
            ids = self._intent_ids(key)
            replace_intent = ReplaceOrder(
                **self._common_intent_kwargs(event, key, ids),
                previous_client_order_id=previous,
                client_order_id=ids.client_order_id,
                role=OrderRole.BASE_STOP,
                side=setup.side.exit_side,
                quantity=self.state.real_open_quantity,
                trigger_price=level,
                close_position=True,
                reduce_only=False,
            )
            self._emit(replace_intent, emitted)
            self._register_replacement(replace_intent)
            setup.base_stop_client_order_id = ids.client_order_id

    def _emit_addon_stop_child(
        self,
        event: FillEnvelope,
        quantity: Decimal,
        trigger_price: Decimal,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None:
            raise InvariantViolation("add-on stop has no setup")
        key = f"addon-stop:{setup.setup_id}:{event.execution_id}"
        ids = self._intent_ids(key)
        intent = SubmitAddonStop(
            **self._common_intent_kwargs(event, key, ids),
            client_order_id=ids.client_order_id,
            side=setup.side.exit_side,
            quantity=quantity,
            trigger_price=trigger_price,
            fill_execution_id=event.execution_id,
        )
        self._emit(intent, emitted)
        self._register_order_intent(intent, OrderRole.ADDON_STOP)

    def _submit_current_target(
        self,
        event: FillEnvelope,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if setup is None or not self.state.signal.recent_bars:
            return
        latest = self.state.signal.recent_bars[-1]
        level = latest.bb_upper if setup.side is Side.LONG else latest.bb_lower
        if level.is_finite() and level > ZERO:
            self._upsert_take_profit(event, TargetFact(level), emitted)

    def _refresh_take_profit_quantity(
        self,
        event: EventEnvelope,
        emitted: list[ExternalIntent],
    ) -> None:
        setup = self.state.setup
        if (
            setup is None
            or setup.tp_client_order_id is None
            or setup.current_tp is None
            or self.state.real_open_quantity <= ZERO
        ):
            return
        previous = setup.tp_client_order_id
        key = f"tp-resize:{setup.setup_id}:{previous}:{self.state.real_open_quantity}"
        ids = self._intent_ids(key)
        intent = ReplaceOrder(
            **self._common_intent_kwargs(event, key, ids),
            previous_client_order_id=previous,
            client_order_id=ids.client_order_id,
            role=OrderRole.TAKE_PROFIT,
            side=setup.side.exit_side,
            quantity=self.state.real_open_quantity,
            trigger_price=setup.current_tp,
            close_position=True,
            reduce_only=False,
        )
        self._emit(intent, emitted)
        self._register_replacement(intent)
        setup.tp_client_order_id = ids.client_order_id

    def _cancel_all_orphans(
        self,
        event: EventEnvelope,
        emitted: list[ExternalIntent],
        *,
        reason: str = "FLAT_ORPHAN",
    ) -> None:
        for client_id, order in sorted(self.state.orders.items()):
            if order.status.active:
                order.status = OrderStatus.CANCEL_PENDING
                self._emit(self._cancel_intent(event, client_id, reason), emitted)
        self.state.outbox = [
            intent
            for intent in self.state.outbox
            if not isinstance(
                intent,
                (
                    SubmitBaseOrder,
                    SubmitAddonOrder,
                    SubmitBaseStop,
                    SubmitAddonStop,
                    SubmitTakeProfit,
                    CloseAll,
                    ReduceAddon,
                    ReplaceOrder,
                ),
            )
        ]

    def _cancel_role_orders(
        self,
        event: EventEnvelope,
        roles: set[OrderRole],
        emitted: list[ExternalIntent],
    ) -> None:
        for client_id, order in sorted(self.state.orders.items()):
            if order.role in roles and order.status.active:
                order.status = OrderStatus.CANCEL_PENDING
                self._ack_outbox_intent(order.intent_id)
                self._emit(self._cancel_intent(event, client_id, "ROLE_CANCEL"), emitted)

    def _addon_eligible(self) -> bool:
        state = self.state
        setup = state.setup
        return (
            state.risk_mode is RiskMode.FULL
            and setup is not None
            and state.position_build is PositionBuild.BASE
            and state.order_lifecycle is OrderLifecycle.NONE
            and state.base_leg.quantity > ZERO
            and state.addon_leg.quantity == ZERO
            and not setup.add_on_lock
        )

    def _reduce_leg(
        self,
        leg: VirtualLeg,
        quantity: Decimal,
        exit_price: Decimal,
        setup_side: Side,
        *,
        preferred_execution_id: str | None = None,
    ) -> Decimal:
        if leg.fill_vwap is None or quantity > leg.quantity:
            raise InvariantViolation("logical leg reduction exceeds inventory")
        pnl = (exit_price - leg.fill_vwap) * quantity * setup_side.sign
        leg.quantity -= quantity
        leg.reduced_quantity += quantity
        leg.realized_price_pnl += pnl
        if leg.fill_quantities:
            remaining = quantity
            if (
                preferred_execution_id is not None
                and preferred_execution_id not in leg.fill_execution_ids
            ):
                raise InvariantViolation("protected execution is absent from leg inventory")
            allocation_order = (
                [preferred_execution_id]
                + [
                    execution_id
                    for execution_id in leg.fill_execution_order
                    if execution_id != preferred_execution_id
                ]
                if preferred_execution_id is not None
                else leg.fill_execution_order
            )
            for execution_id in allocation_order:
                available = leg.remaining_fill_quantities[execution_id]
                consumed = min(available, remaining)
                leg.remaining_fill_quantities[execution_id] = available - consumed
                remaining -= consumed
                if remaining == ZERO:
                    break
            if remaining != ZERO:
                raise InvariantViolation("per-execution fill inventory is incomplete")
        return pnl

    def _validate_bar(self, event: BarClosed) -> None:
        if not event.is_final:
            raise ValueError("only final H1 bars are accepted")
        validate_utc(event.open_time_utc, "open_time_utc")
        validate_utc(event.close_time_utc, "close_time_utc")
        if event.close_time_utc <= event.open_time_utc:
            raise ValueError("bar close must follow its open")
        if event.close_time_utc - event.open_time_utc != (
            timedelta(hours=1) - timedelta(milliseconds=1)
        ):
            raise ValueError("BarClosed must cover exactly one H1 interval")
        if event.occurred_at_utc < event.close_time_utc:
            raise ValueError("BarClosed cannot occur before its close timestamp")
        for name in ("open", "high", "low", "close", "volume", "bb_upper", "bb_lower"):
            validate_decimal_event(getattr(event, name), name)
        for name in (
            "stoch_k",
            "stoch_d",
            "previous_stoch_k",
            "previous_stoch_d",
        ):
            value = getattr(event, name)
            if value is not None:
                validate_decimal_event(value, name)
                if not ZERO <= value <= Decimal("100"):
                    raise ValueError(f"{name} must be within [0, 100]")
        if (
            min(event.open, event.high, event.low, event.close, event.bb_upper, event.bb_lower)
            <= ZERO
        ):
            raise ValueError("OHLC and Bollinger values must be positive")
        if event.low > min(event.open, event.close) or event.high < max(event.open, event.close):
            raise ValueError("bar OHLC is inconsistent")
        if event.bb_lower >= event.bb_upper:
            raise ValueError("lower Bollinger Band must be below upper band")
        if self.state.signal.recent_bars:
            previous = self.state.signal.recent_bars[-1]
            if event.close_time_utc <= previous.close_time_utc:
                raise ValueError("BarClosed events must be strictly chronological")
        if (
            self.config.marking_timeframe is not None
            and self.state.signal.last_marking_close_time_utc != event.close_time_utc
        ):
            raise ValueError("H1 execution requires a complete preceding marking phase")
        if self.config.marking_timeframe is not None:
            expected_marking_bars = 12 if self.config.marking_timeframe == "5m" else 6
            if self.state.signal.marking_bars_in_phase != expected_marking_bars:
                raise ValueError("H1 execution requires every marking sub-bar")

    def _validate_marking_bar(self, event: MarkingBarClosed) -> None:
        """Waliduje finalny, chronologiczny M5/M10 bar bez importów engine."""

        expected = self.config.marking_timeframe
        if expected is None:
            raise ValueError("state machine działa w H1-only fallback")
        if event.timeframe != expected:
            raise ValueError("marking bar timeframe differs from configuration")
        if not event.is_final:
            raise ValueError("only final marking bars are accepted")
        validate_utc(event.open_time_utc, "open_time_utc")
        validate_utc(event.close_time_utc, "close_time_utc")
        minutes = 5 if expected == "5m" else 10
        if event.close_time_utc - event.open_time_utc != (
            timedelta(minutes=minutes) - timedelta(milliseconds=1)
        ):
            raise ValueError("MarkingBarClosed has an invalid interval")
        if event.occurred_at_utc < event.close_time_utc:
            raise ValueError("MarkingBarClosed cannot occur before its close timestamp")
        for name in ("open", "high", "low", "close", "volume"):
            validate_decimal_event(getattr(event, name), name)
        if min(event.open, event.high, event.low, event.close) <= ZERO:
            raise ValueError("marking OHLC must be positive")
        if event.volume < ZERO:
            raise ValueError("marking volume cannot be negative")
        if event.low > min(event.open, event.close) or event.high < max(event.open, event.close):
            raise ValueError("marking bar OHLC is inconsistent")
        previous_marking_close = self.state.signal.last_marking_close_time_utc
        if previous_marking_close is not None and event.close_time_utc <= previous_marking_close:
            raise ValueError("marking bars must be strictly chronological")
        if previous_marking_close is not None and event.open_time_utc != (
            previous_marking_close + timedelta(milliseconds=1)
        ):
            raise ValueError("marking bars must form a gap-free phase")
        if self.state.signal.recent_bars:
            last_execution_close = self.state.signal.recent_bars[-1].close_time_utc
            if (
                self.state.signal.marking_bars_in_phase == 0
                and event.open_time_utc != last_execution_close + timedelta(milliseconds=1)
            ):
                raise ValueError("marking phase must start after the last H1 execution close")

    def _validate_fill(self, event: FillEnvelope) -> None:
        for name in ("last_quantity", "cumulative_quantity", "price"):
            validate_decimal_event(getattr(event, name), name, positive=True)
        validate_decimal_event(event.commission, "commission")
        if event.commission < ZERO:
            raise ValueError("commission must be non-negative")
        if event.benchmark_price is not None:
            validate_decimal_event(event.benchmark_price, "benchmark_price", positive=True)
        validate_identifier(event.execution_id, "execution_id")

    def _order_for_fill(self, event: FillEnvelope) -> OrderRecord:
        client_id = self._client_id(event)
        order = self.state.orders.get(client_id)
        if order is None:
            raise InvariantViolation("fill has no stored order intent")
        if order.role is not event.role:
            raise InvariantViolation("fill role does not match stored order")
        self._validate_order_event_scope(event, order, require_live=True)
        return order

    def _known_order(self, event: DomainEvent) -> OrderRecord:
        client_id = self._client_id(event)
        try:
            return self.state.orders[client_id]
        except KeyError as exc:
            raise InvariantViolation(f"unknown client order ID {client_id}") from exc

    def _validate_order_event_scope(
        self,
        event: EventEnvelope,
        order: OrderRecord,
        *,
        require_live: bool,
    ) -> None:
        if event.setup_id != order.setup_id:
            raise InvariantViolation("order event setup attribution mismatch")
        if require_live:
            live_setup_id = None if self.state.setup is None else self.state.setup.setup_id
            if order.setup_id != live_setup_id:
                raise InvariantViolation("quantity-changing callback belongs to an old setup")

    @staticmethod
    def _client_id(event: EventEnvelope) -> str:
        if event.client_order_id is None or not event.client_order_id:
            raise ValueError("order event requires client_order_id")
        return event.client_order_id

    def _round_quantity(self, raw: Decimal) -> Decimal:
        steps = (raw / self.config.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return steps * self.config.quantity_step

    def _meets_minimum(self, quantity: Decimal, price: Decimal) -> bool:
        return quantity >= self.config.min_quantity and quantity * price >= self.config.min_notional

    def _expected_signed_quantity(self) -> Decimal:
        setup = self.state.setup
        if setup is None:
            return ZERO
        return self.state.real_open_quantity * setup.side.sign

    def _committed_entry_notional(self) -> Decimal:
        setup = self.state.setup
        if setup is None:
            return ZERO
        base_cumulative = self.state.base_leg.quantity + self.state.base_leg.reduced_quantity
        addon_cumulative = self.state.addon_leg.quantity + self.state.addon_leg.reduced_quantity
        committed = setup.base_target_notional * min(
            Decimal(1),
            base_cumulative / setup.base_requested_quantity,
        )
        if setup.addon_requested_quantity > ZERO:
            committed += setup.addon_target_notional * min(
                Decimal(1),
                addon_cumulative / setup.addon_requested_quantity,
            )
        for order in self.state.orders.values():
            if not order.status.active or not order.role.increases_exposure:
                continue
            if order.requested_quantity <= ZERO:
                raise InvariantViolation("entry order requested quantity must be positive")
            target = (
                setup.base_target_notional
                if order.role is OrderRole.BASE_ENTRY
                else setup.addon_target_notional
            )
            committed += target * order.remaining_quantity / order.requested_quantity
        return committed

    def _increment(self, name: str) -> None:
        self.state.counters[name] = self.state.counters.get(name, 0) + 1

    def _ack_outbox_intent(self, intent_id: str) -> None:
        self.state.outbox = [
            intent for intent in self.state.outbox if intent.intent_id != intent_id
        ]

    def _ack_cancel_outbox(self, client_order_id: str) -> None:
        self.state.outbox = [
            intent
            for intent in self.state.outbox
            if not (
                isinstance(intent, CancelOrder) and intent.target_client_order_id == client_order_id
            )
        ]

    def _remember_event(self, event: EventEnvelope) -> None:
        self.state.processed_event_ids[event.event_id] = None
        while len(self.state.processed_event_ids) > RECENT_EVENT_ID_LIMIT:
            oldest = next(iter(self.state.processed_event_ids))
            del self.state.processed_event_ids[oldest]
        self.state.last_source_sequences[event.source] = max(
            event.source_sequence,
            self.state.last_source_sequences.get(event.source, -1),
        )

    def _set_max(self, name: str, value: Decimal) -> None:
        self.state.telemetry[name] = max(value, self.state.telemetry.get(name, ZERO))

    @dataclass(frozen=True, slots=True)
    class _IntentIds:
        intent_id: str
        idempotency_key: str
        correlation_id: str
        client_order_id: str

    class _CommonIntentKwargs(TypedDict):
        intent_id: str
        idempotency_key: str
        strategy_id: str
        instrument_id: str
        setup_id: str | None
        causation_id: str
        correlation_id: str

    def _intent_ids(self, key: str) -> _IntentIds:
        return self._IntentIds(
            intent_id=self._stable_id("intent", key, length=28),
            idempotency_key=key,
            correlation_id=self._stable_id("correlation", key, length=24),
            client_order_id=self._stable_id("order", key, length=28),
        )

    def _common_intent_kwargs(
        self,
        event: EventEnvelope,
        key: str,
        ids: _IntentIds,
    ) -> _CommonIntentKwargs:
        return {
            "intent_id": ids.intent_id,
            "idempotency_key": key,
            "strategy_id": self.config.strategy_id,
            "instrument_id": self.config.instrument_id,
            "setup_id": None if self.state.setup is None else self.state.setup.setup_id,
            "causation_id": event.event_id,
            "correlation_id": ids.correlation_id,
        }

    def _stable_id(self, namespace: str, *parts: str, length: int) -> str:
        setup_id = "none" if self.state.setup is None else self.state.setup.setup_id
        material = "|".join(
            (
                self.config.strategy_id,
                self.config.instrument_id,
                setup_id,
                namespace,
                *parts,
            )
        )
        return f"mms-{hashlib.sha256(material.encode()).hexdigest()[:length]}"

    @staticmethod
    def _position_close_fingerprint(event: PositionClosed) -> str:
        material = "|".join(
            (
                event.close_reason.value,
                str(event.realized_price_pnl),
                str(event.commissions),
                str(event.funding),
                str(event.realized_slippage_cost),
                *sorted(event.closing_execution_ids),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def _emit(self, intent: ExternalIntent, emitted: list[ExternalIntent]) -> bool:
        if intent.idempotency_key in self.state.emitted_intent_keys:
            return False
        self.state.emitted_intent_keys.add(intent.idempotency_key)
        emitted.append(intent)
        return True

    def _store_outbox(self, emitted: list[ExternalIntent]) -> None:
        existing = {intent.intent_id for intent in self.state.outbox}
        self.state.outbox.extend(intent for intent in emitted if intent.intent_id not in existing)

    def _register_order_intent(
        self,
        intent: SubmitBaseOrder
        | SubmitAddonOrder
        | SubmitBaseStop
        | SubmitAddonStop
        | SubmitTakeProfit
        | CloseAll
        | ReduceAddon,
        role: OrderRole,
    ) -> None:
        if isinstance(intent, (SubmitBaseOrder, SubmitAddonOrder)):
            quantity = intent.quantity
            trigger_price = None
            reduce_only = False
            close_position = False
        elif isinstance(intent, SubmitAddonStop):
            quantity = intent.quantity
            trigger_price = intent.trigger_price
            reduce_only = True
            close_position = False
        elif isinstance(intent, (SubmitBaseStop, SubmitTakeProfit)):
            quantity = intent.reference_quantity
            trigger_price = intent.trigger_price
            reduce_only = False
            close_position = True
        else:
            quantity = intent.quantity
            trigger_price = None
            reduce_only = isinstance(intent, ReduceAddon)
            close_position = False
        self.state.orders[intent.client_order_id] = OrderRecord(
            role=role,
            intent_id=intent.intent_id,
            correlation_id=intent.correlation_id,
            client_order_id=intent.client_order_id,
            venue_order_id=None,
            requested_quantity=quantity,
            filled_quantity=ZERO,
            status=OrderStatus.INTENDED,
            side=intent.side,
            reduce_only=reduce_only,
            close_position=close_position,
            trigger_price=trigger_price,
            setup_id=intent.setup_id,
            protected_execution_id=(
                intent.fill_execution_id if isinstance(intent, SubmitAddonStop) else None
            ),
        )

    def _register_replacement(self, intent: ReplaceOrder) -> None:
        previous = self.state.orders.get(intent.previous_client_order_id)
        if previous is not None and previous.status.active:
            previous.status = OrderStatus.CANCEL_PENDING
        self.state.orders[intent.client_order_id] = OrderRecord(
            role=intent.role,
            intent_id=intent.intent_id,
            correlation_id=intent.correlation_id,
            client_order_id=intent.client_order_id,
            venue_order_id=None,
            requested_quantity=intent.quantity,
            filled_quantity=ZERO,
            status=OrderStatus.INTENDED,
            side=intent.side,
            reduce_only=intent.reduce_only,
            close_position=intent.close_position,
            trigger_price=intent.trigger_price,
            replacement_of=intent.previous_client_order_id,
            setup_id=intent.setup_id,
        )

    def _cancel_intent(
        self,
        event: EventEnvelope,
        client_order_id: str,
        reason: str,
    ) -> CancelOrder:
        key = f"cancel:{client_order_id}:{reason}"
        ids = self._intent_ids(key)
        return CancelOrder(
            **self._common_intent_kwargs(event, key, ids),
            target_client_order_id=client_order_id,
            reason=reason,
        )

    def _reconciliation_intent(
        self,
        event: EventEnvelope,
        reason: str,
    ) -> RequestReconciliation:
        key = f"reconcile:{event.event_id}:{reason}"
        ids = self._intent_ids(key)
        return RequestReconciliation(
            **self._common_intent_kwargs(event, key, ids),
            reason=reason,
        )

    def _close_all_intent(
        self,
        event: EventEnvelope,
        reason: CloseReason,
        discriminator: str,
        *,
        quantity: Decimal | None = None,
        side: Side | None = None,
        emitted: list[ExternalIntent] | None = None,
    ) -> CloseAll:
        setup = self.state.setup
        if setup is None and (quantity is None or side is None):
            raise InvariantViolation(
                "unattributed exposure close requires explicit quantity and side"
            )
        effective_quantity = self.state.real_open_quantity if quantity is None else quantity
        if side is not None:
            effective_side = side
        else:
            assert setup is not None
            effective_side = setup.side.exit_side
        quantity_key = format(effective_quantity.normalize(), "f")
        setup_key = "unattributed" if setup is None else setup.setup_id
        key = (
            f"close-all:{setup_key}:{reason.value}:{discriminator}:"
            f"{effective_side.value}:{quantity_key}"
        )
        ids = self._intent_ids(key)
        intent = CloseAll(
            **self._common_intent_kwargs(event, key, ids),
            client_order_id=ids.client_order_id,
            side=effective_side,
            quantity=effective_quantity,
            close_reason=reason,
        )
        if setup is not None:
            setup.pending_close_reason = reason
        if key not in self.state.emitted_intent_keys:
            for existing in self.state.orders.values():
                if (
                    existing.setup_id == (None if setup is None else setup.setup_id)
                    and existing.role is OrderRole.CLOSE_ALL
                    and existing.status.active
                    and existing.client_order_id != intent.client_order_id
                ):
                    existing.status = OrderStatus.CANCEL_PENDING
                    if emitted is not None:
                        self._emit(
                            self._cancel_intent(
                                event,
                                existing.client_order_id,
                                "CLOSE_ALL_REPLACE",
                            ),
                            emitted,
                        )
            self._register_order_intent(intent, OrderRole.CLOSE_ALL)
        return intent

    def _reduce_addon_intent(
        self,
        event: EventEnvelope,
        quantity: Decimal,
        reason: str,
    ) -> ReduceAddon:
        setup = self.state.setup
        if setup is None:
            raise InvariantViolation("cannot reduce add-on without a setup")
        key = f"reduce-addon:{setup.setup_id}:{reason}"
        ids = self._intent_ids(key)
        intent = ReduceAddon(
            **self._common_intent_kwargs(event, key, ids),
            client_order_id=ids.client_order_id,
            side=setup.side.exit_side,
            quantity=quantity,
            reason=reason,
        )
        if key not in self.state.emitted_intent_keys:
            self._register_order_intent(intent, OrderRole.REDUCE_ADDON)
        return intent

    def _persist_intent(self, event: EventEnvelope) -> PersistSnapshot:
        key = f"persist:{event.event_id}"
        ids = self._intent_ids(key)
        return PersistSnapshot(
            **self._common_intent_kwargs(event, key, ids),
            snapshot_id=self.state.snapshot_id,
        )
