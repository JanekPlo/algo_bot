"""PyO3 backend for the pure Mastermind v2 state machine.

NautilusTrader 1.230.0's Rust/PyO3 backtest engine settles perpetual funding, but its
simulated exchange does not implement server-side ``closePosition=true`` semantics.
The adapter therefore uses native reduce-only orders and decomposes a logical
whole-position stop/target into deterministic per-fill children.  No custom matcher is
implemented here, and neither metadata profile claims server-side close-position
parity.

Strategic market orders are delayed by the wrapper to the next final H1 bar while the
engine itself runs at zero latency.  This preserves the preregistered conservative
next-close timing without delaying protective orders created from fill callbacks by an
entire H1 event.  Existing callers receive the non-eligible smoke profile.  A research
caller must opt in explicitly; its final evidence and performance decisions remain
outside this transport adapter.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, TypedDict, cast

import pandas as pd
from nautilus_trader.core import nautilus_pyo3 as nt
from nautilus_trader.core.nautilus_pyo3.trading import Strategy as Pyo3Strategy

from algo_bot.strategies.mastermind.model import (
    ZERO,
    AccountEquityUpdated,
    BarClosed,
    CancelOrder,
    CloseAll,
    CloseReason,
    DomainEvent,
    DomainIntent,
    FundingApplied,
    MarkingBarClosed,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
    OrderRole,
    OrderStatus,
    OrderSubmitted,
    PersistSnapshot,
    PositionChanged,
    PositionClosed,
    ReconciledOrder,
    ReconciliationCompleted,
    ReduceAddon,
    ReplaceOrder,
    RequestReconciliation,
    Side,
    SubmitAddonOrder,
    SubmitAddonStop,
    SubmitBaseOrder,
    SubmitBaseStop,
    SubmitTakeProfit,
)

PYO3_SMOKE_EXECUTION_PROFILE = "PYO3_WRAPPER_NEXT_CLOSE_ZERO_LATENCY_SMOKE_V1"
PYO3_SMOKE_POSITION_MODEL = "PYO3_NETTING_DECOMPOSED_CLOSEALL_SMOKE_V1"
PYO3_RESEARCH_EXECUTION_PROFILE = "PYO3_BYBIT_NATIVE_BAR_RESEARCH_V1"
PYO3_RESEARCH_POSITION_MODEL = "PYO3_NETTING_REDUCE_ONLY_BYBIT_V1"
PYO3_RECOVERY_SCHEMA_VERSION = "pyo3_mastermind_recovery/1"
EVIDENCE_TIER = "SMOKE_ONLY"
ELIGIBILITY = "NOT_ELIGIBLE"
HOUR_NS = 3_600_000_000_000
MILLISECOND_NS = 1_000_000


class Pyo3SmokeProfileError(RuntimeError):
    """An intent cannot be represented safely by the frozen smoke profile."""


class TransitionView(Protocol):
    """Narrow view of the P6 ``TransitionResult`` used by this adapter."""

    @property
    def intents(self) -> Sequence[DomainIntent]: ...

    @property
    def snapshot_json(self) -> str: ...


class MastermindMachinePort(Protocol):
    """Stable P6 boundary; the concrete state machine remains engine-independent."""

    @property
    def source_sequence_highwater(self) -> int: ...

    def handle(self, event: DomainEvent) -> TransitionView: ...

    def snapshot_json(self) -> str: ...


class RecoveryEntryFillView(Protocol):
    """Feature-detected P6 entry-fill recovery projection."""

    execution_id: str
    role: OrderRole
    original_quantity: Decimal
    remaining_quantity: Decimal
    side: Side


class RecoveryOrderView(Protocol):
    """Feature-detected P6 logical-order recovery projection."""

    role: OrderRole
    intent_id: str
    client_order_id: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus
    side: Side
    reduce_only: bool
    close_position: bool
    trigger_price: Decimal | None
    setup_id: str | None
    protected_execution_id: str | None


class MachineRecoveryView(Protocol):
    """Read-only state needed to reconstruct the native transport adapter."""

    active_setup_id: str | None
    setup_side: Side | None
    pending_close_reason: CloseReason | None
    final_close_reason: CloseReason | None
    commissions: Decimal
    funding: Decimal
    realized_slippage_cost: Decimal
    funding_settlement_ids: tuple[str, ...]
    unresolved_funding_settlement_ids: tuple[str, ...]
    closing_execution_ids: tuple[str, ...]
    entry_fills: tuple[RecoveryEntryFillView, ...]
    orders: tuple[RecoveryOrderView, ...]
    outbox: tuple[DomainIntent, ...]


@dataclass(frozen=True, slots=True)
class BarFeatures:
    """Causal indicator values already known at one final bar close."""

    bb_upper: Decimal
    bb_lower: Decimal
    stoch_k: Decimal | None = None
    stoch_d: Decimal | None = None
    previous_stoch_k: Decimal | None = None
    previous_stoch_d: Decimal | None = None


BarFeatureSource = Callable[[Any], BarFeatures]
BeforeBarDomainEvents = Callable[[Any], Iterable[DomainEvent]]
DeliverDomainBar = Callable[[Any], bool]
TransitionObserver = Callable[[DomainEvent], None]
RetainDomainEvent = Callable[[DomainEvent], bool]


@dataclass(frozen=True, slots=True)
class Pyo3RecoveryExposureFill:
    """One durable native-coverage unit reconstructed from a logical entry fill."""

    execution_id: str
    role: OrderRole
    original_quantity: Decimal
    remaining_quantity: Decimal
    side: Side


@dataclass(frozen=True, slots=True)
class Pyo3RecoveryOrderBinding:
    """Stable translation between one logical order and one native PyO3 order."""

    role: OrderRole
    intent_id: str
    logical_client_order_id: str
    actual_client_order_id: str
    side: Side
    requested_quantity: Decimal
    reduce_only: bool
    close_position: bool
    protected_execution_id: str | None
    close_reason: CloseReason | None
    smoke_helper: bool
    setup_id: str | None


@dataclass(frozen=True, slots=True)
class Pyo3RecoveryCoverageGroup:
    """Durable logical lifecycle for a decomposed close-position order."""

    logical_client_order_id: str
    intent_id: str
    role: OrderRole
    side: Side
    trigger_price: Decimal
    reference_quantity: Decimal
    setup_id: str | None
    helpers_by_execution_id: tuple[tuple[str, str], ...]
    submitted_published: bool
    accepted_published: bool
    terminal_published: bool
    cumulative_filled_quantity: Decimal


@dataclass(frozen=True, slots=True)
class Pyo3RecoveryCheckpoint:
    """Checksummed transport state required for deterministic PyO3 restart."""

    strategy_id: str
    instrument_id: str
    source_sequence: int
    active_setup_id: str | None = None
    native_commissions: Decimal = ZERO
    native_funding: Decimal = ZERO
    native_slippage_cost: Decimal = ZERO
    closing_execution_ids: tuple[str, ...] = ()
    last_close_reason: CloseReason | None = None
    exposure_fills: tuple[Pyo3RecoveryExposureFill, ...] = ()
    bindings: tuple[Pyo3RecoveryOrderBinding, ...] = ()
    coverage_groups: tuple[Pyo3RecoveryCoverageGroup, ...] = ()
    submitted_client_order_ids: tuple[str, ...] = ()
    scheduled_market_intent_ids: tuple[str, ...] = ()
    seen_adjustment_event_ids: tuple[str, ...] = ()
    seen_funding_settlement_ids: tuple[str, ...] = ()
    unresolved_funding_settlement_ids: tuple[str, ...] = ()
    seen_native_lifecycle_event_ids: tuple[str, ...] = ()
    seen_native_position_fingerprints: tuple[str, ...] = ()
    terminal_logical_order_ids: tuple[str, ...] = ()
    awaiting_flat_reconciliation: bool = False
    last_delivered_bar_close_ns: int | None = None
    last_published_equity_close_ns: int | None = None
    schema_version: str = PYO3_RECOVERY_SCHEMA_VERSION
    execution_profile: str = PYO3_SMOKE_EXECUTION_PROFILE

    def __post_init__(self) -> None:
        _validate_recovery_checkpoint(self)

    def to_json(self) -> str:
        """Return canonical JSON with a corruption-detecting checksum."""

        body = _recovery_checkpoint_body(self)
        checksum = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()
        return _canonical_json_text({**body, "checksum": checksum})

    @classmethod
    def from_json(cls, raw: str) -> Pyo3RecoveryCheckpoint:
        """Restore and validate a canonical recovery checkpoint fail-closed."""

        return _restore_recovery_checkpoint(raw)


PersistRecoveryTransition = Callable[[str, Pyo3RecoveryCheckpoint], None]


class _EventEnvelopeKwargs(TypedDict):
    event_id: str
    strategy_id: str
    instrument_id: str
    occurred_at_utc: datetime
    source: str
    source_sequence: int
    setup_id: str | None
    correlation_id: str | None
    client_order_id: str | None
    causation_id: str | None


@dataclass(frozen=True, slots=True)
class Pyo3SmokeMetadata:
    """Mandatory provenance which prevents a smoke run becoming research evidence."""

    evidence_tier: str = EVIDENCE_TIER
    eligibility: str = ELIGIBILITY
    execution_profile: str = PYO3_SMOKE_EXECUTION_PROFILE
    position_model: str = PYO3_SMOKE_POSITION_MODEL
    engine: str = "nautilus_trader.core.nautilus_pyo3.BacktestEngine"
    close_position_parity: bool = False
    custom_matching_engine: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        """Return JSON-friendly immutable run provenance."""

        return {
            "evidence_tier": self.evidence_tier,
            "eligibility": self.eligibility,
            "execution_profile": self.execution_profile,
            "position_model": self.position_model,
            "engine": self.engine,
            "close_position_parity": self.close_position_parity,
            "custom_matching_engine": self.custom_matching_engine,
        }


@dataclass(frozen=True, slots=True)
class Pyo3ResearchMetadata:
    """Jawne provenance ścieżki badawczej używanej dopiero przez Session 4.

    ``close_position_parity`` pozostaje fałszywe: profil korzysta z natywnych zleceń
    reduce-only, ale silnik nie dowodzi semantyki server-side ``closePosition``.
    """

    evidence_tier: str = field(default="RESEARCH", init=False)
    eligibility: str = field(default="EVIDENCE_GATE_PENDING", init=False)
    execution_profile: str = field(default=PYO3_RESEARCH_EXECUTION_PROFILE, init=False)
    position_model: str = field(default=PYO3_RESEARCH_POSITION_MODEL, init=False)
    engine: str = field(
        default="nautilus_trader.core.nautilus_pyo3.BacktestEngine",
        init=False,
    )
    close_position_parity: bool = field(default=False, init=False)
    custom_matching_engine: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, str | bool]:
        """Zwróć zamrożone provenance gotowe do serializacji JSON."""

        return {
            "evidence_tier": self.evidence_tier,
            "eligibility": self.eligibility,
            "execution_profile": self.execution_profile,
            "position_model": self.position_model,
            "engine": self.engine,
            "close_position_parity": self.close_position_parity,
            "custom_matching_engine": self.custom_matching_engine,
        }


@dataclass(frozen=True, slots=True)
class CacheReports:
    """Reports built directly from PyO3 cache objects, never Cython reporters."""

    orders: pd.DataFrame
    fills: pd.DataFrame
    positions: pd.DataFrame
    account_events: pd.DataFrame


@dataclass(frozen=True, slots=True)
class Pyo3SmokeRun:
    """Small P7 artifact, deliberately separate from the richer P8 result type."""

    metadata: Pyo3SmokeMetadata | Pyo3ResearchMetadata
    native_result: object
    reports: CacheReports
    domain_events: tuple[DomainEvent, ...]
    submitted_client_order_ids: tuple[str, ...]
    final_net_quantity: Decimal


@dataclass(frozen=True, slots=True)
class _OrderBinding:
    role: OrderRole
    intent_id: str
    logical_client_order_id: str
    actual_client_order_id: str
    side: Side
    requested_quantity: Decimal
    reduce_only: bool
    close_position: bool
    protected_execution_id: str | None = None
    close_reason: CloseReason | None = None
    smoke_helper: bool = False
    setup_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ExposureFill:
    execution_id: str
    role: OrderRole
    original_quantity: Decimal
    remaining_quantity: Decimal
    side: Side


@dataclass(slots=True)
class _CoverageGroup:
    """One logical dynamic Close-All instruction expanded into native children."""

    logical_client_order_id: str
    intent_id: str
    role: OrderRole
    side: Side
    trigger_price: Decimal
    reference_quantity: Decimal
    setup_id: str | None
    helpers_by_execution_id: dict[str, str] = field(default_factory=dict)
    submitted_published: bool = False
    accepted_published: bool = False
    terminal_published: bool = False
    cumulative_filled_quantity: Decimal = ZERO


class NautilusMastermindStrategy(Pyo3Strategy):
    """Thin PyO3 wrapper around an injected pure ``MastermindStateMachine``.

    The wrapper translates callbacks and intents; it does not contain signal logic,
    virtual-leg accounting, PnL policy, persistence, or a matching engine.
    """

    def __new__(cls, **_: object) -> NautilusMastermindStrategy:
        """Hide adapter-only kwargs from the PyO3 base class allocator."""

        return cast(NautilusMastermindStrategy, super().__new__(cls))

    def __init__(
        self,
        *,
        machine: MastermindMachinePort,
        strategy_id: str,
        instrument_id: Any,
        bar_type: Any,
        feature_source: BarFeatureSource,
        marking_bar_type: Any | None = None,
        marking_interval_ns: int | None = None,
        interval_ns: int = HOUR_NS,
        reconcile_on_start: bool = False,
        known_client_order_ids: Iterable[str] = (),
        persist_transition: Callable[[str], None] | None = None,
        persist_recovery_transition: PersistRecoveryTransition | None = None,
        recovery_checkpoint: Pyo3RecoveryCheckpoint | str | None = None,
        before_bar_domain_events: BeforeBarDomainEvents | None = None,
        deliver_domain_bar: DeliverDomainBar | None = None,
        slippage_per_unit: Decimal = ZERO,
        serialize_transition_snapshots: bool = True,
        transition_observer: TransitionObserver | None = None,
        retain_domain_event: RetainDomainEvent | None = None,
    ) -> None:
        if interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        if (marking_bar_type is None) != (marking_interval_ns is None):
            raise ValueError("marking_bar_type i marking_interval_ns muszą występować razem")
        if marking_interval_ns not in (None, 300_000_000_000, 600_000_000_000):
            raise ValueError("marking_interval_ns musi reprezentować M5 albo M10")
        if not slippage_per_unit.is_finite() or slippage_per_unit < ZERO:
            raise ValueError("slippage_per_unit must be finite and non-negative")
        if not serialize_transition_snapshots and (
            persist_transition is not None or persist_recovery_transition is not None
        ):
            raise Pyo3SmokeProfileError(
                "transition snapshot serialization is mandatory when persistence is configured"
            )
        checkpoint = (
            Pyo3RecoveryCheckpoint.from_json(recovery_checkpoint)
            if isinstance(recovery_checkpoint, str)
            else recovery_checkpoint
        )
        if checkpoint is not None and (
            checkpoint.strategy_id != strategy_id or checkpoint.instrument_id != str(instrument_id)
        ):
            raise Pyo3SmokeProfileError("recovery checkpoint scope mismatch")
        if checkpoint is not None and not reconcile_on_start:
            raise Pyo3SmokeProfileError("a recovery checkpoint requires reconcile_on_start=True")
        if checkpoint is not None and persist_recovery_transition is None:
            raise Pyo3SmokeProfileError(
                "checkpoint recovery requires atomic persist_recovery_transition"
            )
        super().__init__(
            nt.StrategyConfig(
                strategy_id=nt.StrategyId.from_str(strategy_id),
                oms_type=nt.OmsType.NETTING,
                log_events=False,
                log_commands=False,
            )
        )
        self._machine = machine
        self._domain_strategy_id = strategy_id
        self._instrument_id = instrument_id
        self._bar_type = bar_type
        self._feature_source = feature_source
        self._interval_ns = interval_ns
        self._marking_bar_type = marking_bar_type
        self._marking_interval_ns = marking_interval_ns
        self._marking_timeframe = (
            None
            if marking_interval_ns is None
            else ("5m" if marking_interval_ns == 300_000_000_000 else "10m")
        )
        self._reconcile_on_start = reconcile_on_start
        self._persist_transition = persist_transition
        self._persist_recovery_transition = persist_recovery_transition
        self._before_bar_domain_events = before_bar_domain_events
        self._deliver_domain_bar = deliver_domain_bar
        self._slippage_per_unit = slippage_per_unit
        self._serialize_transition_snapshots = serialize_transition_snapshots
        self._transition_observer = transition_observer
        self._retain_domain_event = retain_domain_event
        self._offline_transition_error: str | None = None
        self._instrument: Any | None = None
        self._sequence = max(
            machine.source_sequence_highwater,
            0 if checkpoint is None else checkpoint.source_sequence,
        )
        if self._sequence < 0:
            raise ValueError("machine source_sequence_highwater must be non-negative")
        self._scheduled_market_intents: list[DomainIntent] = []
        self._restored_scheduled_intent_ids: set[str] = set()
        self._routed_intent_keys: set[str] = set()
        self._restored_client_order_ids: set[str] = set(known_client_order_ids)
        self._submitted_client_order_ids: set[str] = set()
        self._bindings: dict[str, _OrderBinding] = {}
        self._logical_to_actual_ids: dict[str, set[str]] = {}
        self._exposure_fills: dict[str, _ExposureFill] = {}
        self._coverage_groups: dict[OrderRole, _CoverageGroup] = {}
        self._coverage_lifecycles: dict[str, _CoverageGroup] = {}
        self._seen_adjustment_event_ids: set[str] = set()
        self._seen_funding_settlement_ids: set[str] = set()
        self._unresolved_funding_settlement_ids: set[str] = set()
        self._seen_native_lifecycle_event_ids: set[str] = set()
        self._seen_native_position_fingerprints: set[str] = set()
        self._terminal_logical_order_ids: set[str] = set()
        self._native_commissions = ZERO
        self._native_funding = ZERO
        self._native_slippage_cost = ZERO
        self._closing_execution_ids: list[str] = []
        self._last_close_reason: CloseReason | None = None
        self._draining_adjustments = False
        self._reconciling = False
        self._deferred_reconciliation_reason: str | None = None
        self._awaiting_flat_reconciliation = False
        self._active_setup_id: str | None = None
        self._last_delivered_bar_close_ns: int | None = None
        self._last_published_equity_close_ns: int | None = None
        self._recovery_view = _feature_recovery_view(machine)
        self._recovery_outbox: tuple[DomainIntent, ...] = ()
        self._recovering_startup = False
        self.domain_events: list[DomainEvent] = []
        if checkpoint is not None:
            self._restore_transport_checkpoint(checkpoint)
        self._merge_machine_recovery_view(checkpoint is not None)

    @property
    def submitted_client_order_ids(self) -> tuple[str, ...]:
        """Return deterministic native IDs submitted or restored by the wrapper."""

        return tuple(sorted(self._submitted_client_order_ids))

    @property
    def offline_transition_error(self) -> str | None:
        """Return the first fail-closed error from the no-snapshot offline path."""

        return self._offline_transition_error

    def recovery_checkpoint(self) -> Pyo3RecoveryCheckpoint:
        """Capture all adapter-local state required for a fail-closed restart."""

        view = _feature_recovery_view(self._machine)
        if view is not None:
            self._unresolved_funding_settlement_ids = set(view.unresolved_funding_settlement_ids)
        active_setup_id = self._active_setup_id if view is None else view.active_setup_id
        domain_flat = view is not None and active_setup_id is None
        exposure_fills = (
            ()
            if domain_flat
            else tuple(
                Pyo3RecoveryExposureFill(
                    execution_id=fill.execution_id,
                    role=fill.role,
                    original_quantity=fill.original_quantity,
                    remaining_quantity=fill.remaining_quantity,
                    side=fill.side,
                )
                for fill in self._exposure_fills.values()
            )
        )
        bindings = tuple(
            Pyo3RecoveryOrderBinding(
                role=binding.role,
                intent_id=binding.intent_id,
                logical_client_order_id=binding.logical_client_order_id,
                actual_client_order_id=binding.actual_client_order_id,
                side=binding.side,
                requested_quantity=binding.requested_quantity,
                reduce_only=binding.reduce_only,
                close_position=binding.close_position,
                protected_execution_id=binding.protected_execution_id,
                close_reason=binding.close_reason,
                smoke_helper=binding.smoke_helper,
                setup_id=binding.setup_id,
            )
            for _, binding in sorted(self._bindings.items())
        )
        coverage_groups = (
            ()
            if domain_flat
            else tuple(
                Pyo3RecoveryCoverageGroup(
                    logical_client_order_id=group.logical_client_order_id,
                    intent_id=group.intent_id,
                    role=group.role,
                    side=group.side,
                    trigger_price=group.trigger_price,
                    reference_quantity=group.reference_quantity,
                    setup_id=group.setup_id,
                    helpers_by_execution_id=tuple(sorted(group.helpers_by_execution_id.items())),
                    submitted_published=group.submitted_published,
                    accepted_published=group.accepted_published,
                    terminal_published=group.terminal_published,
                    cumulative_filled_quantity=group.cumulative_filled_quantity,
                )
                for _, group in sorted(self._coverage_lifecycles.items())
            )
        )
        scheduled_ids = {
            intent.intent_id for intent in self._scheduled_market_intents
        } | self._restored_scheduled_intent_ids
        if domain_flat:
            scheduled_ids.clear()
        return Pyo3RecoveryCheckpoint(
            strategy_id=self._domain_strategy_id,
            instrument_id=str(self._instrument_id),
            source_sequence=self._sequence,
            active_setup_id=active_setup_id,
            native_commissions=ZERO if domain_flat else self._native_commissions,
            native_funding=ZERO if domain_flat else self._native_funding,
            native_slippage_cost=ZERO if domain_flat else self._native_slippage_cost,
            closing_execution_ids=(() if domain_flat else tuple(self._closing_execution_ids)),
            last_close_reason=None if domain_flat else self._last_close_reason,
            exposure_fills=exposure_fills,
            bindings=bindings,
            coverage_groups=coverage_groups,
            submitted_client_order_ids=tuple(sorted(self._submitted_client_order_ids)),
            scheduled_market_intent_ids=tuple(sorted(scheduled_ids)),
            seen_adjustment_event_ids=tuple(sorted(self._seen_adjustment_event_ids)),
            seen_funding_settlement_ids=tuple(sorted(self._seen_funding_settlement_ids)),
            unresolved_funding_settlement_ids=tuple(
                sorted(self._unresolved_funding_settlement_ids)
            ),
            seen_native_lifecycle_event_ids=tuple(sorted(self._seen_native_lifecycle_event_ids)),
            seen_native_position_fingerprints=tuple(
                sorted(self._seen_native_position_fingerprints)
            ),
            terminal_logical_order_ids=tuple(sorted(self._terminal_logical_order_ids)),
            awaiting_flat_reconciliation=(
                False if domain_flat else self._awaiting_flat_reconciliation
            ),
            last_delivered_bar_close_ns=self._last_delivered_bar_close_ns,
            last_published_equity_close_ns=self._last_published_equity_close_ns,
        )

    def _restore_transport_checkpoint(self, checkpoint: Pyo3RecoveryCheckpoint) -> None:
        self._active_setup_id = checkpoint.active_setup_id
        self._native_commissions = checkpoint.native_commissions
        self._native_funding = checkpoint.native_funding
        self._native_slippage_cost = checkpoint.native_slippage_cost
        self._closing_execution_ids = list(checkpoint.closing_execution_ids)
        self._last_close_reason = checkpoint.last_close_reason
        self._exposure_fills = {
            fill.execution_id: _ExposureFill(
                execution_id=fill.execution_id,
                role=fill.role,
                original_quantity=fill.original_quantity,
                remaining_quantity=fill.remaining_quantity,
                side=fill.side,
            )
            for fill in checkpoint.exposure_fills
        }
        self._bindings = {
            binding.actual_client_order_id: _OrderBinding(
                role=binding.role,
                intent_id=binding.intent_id,
                logical_client_order_id=binding.logical_client_order_id,
                actual_client_order_id=binding.actual_client_order_id,
                side=binding.side,
                requested_quantity=binding.requested_quantity,
                reduce_only=binding.reduce_only,
                close_position=binding.close_position,
                protected_execution_id=binding.protected_execution_id,
                close_reason=binding.close_reason,
                smoke_helper=binding.smoke_helper,
                setup_id=binding.setup_id,
            )
            for binding in checkpoint.bindings
        }
        self._logical_to_actual_ids = {}
        for actual_id, binding in self._bindings.items():
            self._logical_to_actual_ids.setdefault(binding.logical_client_order_id, set()).add(
                actual_id
            )
        for recovered in checkpoint.coverage_groups:
            group = _CoverageGroup(
                logical_client_order_id=recovered.logical_client_order_id,
                intent_id=recovered.intent_id,
                role=recovered.role,
                side=recovered.side,
                trigger_price=recovered.trigger_price,
                reference_quantity=recovered.reference_quantity,
                setup_id=recovered.setup_id,
                helpers_by_execution_id=dict(recovered.helpers_by_execution_id),
                submitted_published=recovered.submitted_published,
                accepted_published=recovered.accepted_published,
                terminal_published=recovered.terminal_published,
                cumulative_filled_quantity=recovered.cumulative_filled_quantity,
            )
            self._coverage_lifecycles[group.logical_client_order_id] = group
            if not group.terminal_published:
                self._coverage_groups[group.role] = group
        self._submitted_client_order_ids.update(checkpoint.submitted_client_order_ids)
        self._restored_scheduled_intent_ids.update(checkpoint.scheduled_market_intent_ids)
        self._seen_adjustment_event_ids.update(checkpoint.seen_adjustment_event_ids)
        self._seen_funding_settlement_ids.update(checkpoint.seen_funding_settlement_ids)
        self._unresolved_funding_settlement_ids.update(checkpoint.unresolved_funding_settlement_ids)
        self._seen_native_lifecycle_event_ids.update(checkpoint.seen_native_lifecycle_event_ids)
        self._seen_native_position_fingerprints.update(checkpoint.seen_native_position_fingerprints)
        self._terminal_logical_order_ids.update(checkpoint.terminal_logical_order_ids)
        self._awaiting_flat_reconciliation = checkpoint.awaiting_flat_reconciliation
        self._last_delivered_bar_close_ns = checkpoint.last_delivered_bar_close_ns
        self._last_published_equity_close_ns = checkpoint.last_published_equity_close_ns

    def _merge_machine_recovery_view(self, had_checkpoint: bool) -> None:
        view = self._recovery_view
        if view is None:
            if had_checkpoint or self._restored_client_order_ids:
                raise Pyo3SmokeProfileError(
                    "recovery checkpoint/client IDs require machine.recovery_view"
                )
            return
        self._recovery_outbox = tuple(view.outbox)
        domain_unresolved = set(view.unresolved_funding_settlement_ids)
        if had_checkpoint and self._unresolved_funding_settlement_ids != domain_unresolved:
            raise Pyo3SmokeProfileError("checkpoint/domain unresolved funding mismatch")
        self._unresolved_funding_settlement_ids = domain_unresolved
        if view.active_setup_id is None:
            if self._active_setup_id is not None and had_checkpoint:
                raise Pyo3SmokeProfileError(
                    "checkpoint has an active setup but domain recovery is flat"
                )
            return
        if self._active_setup_id not in {None, view.active_setup_id}:
            raise Pyo3SmokeProfileError("checkpoint/domain active setup mismatch")
        self._active_setup_id = view.active_setup_id
        if self._persist_recovery_transition is None:
            raise Pyo3SmokeProfileError(
                "active domain recovery requires atomic persist_recovery_transition"
            )
        if had_checkpoint:
            if (
                self._native_commissions != view.commissions
                or self._native_funding != view.funding
                or self._native_slippage_cost != view.realized_slippage_cost
            ):
                raise Pyo3SmokeProfileError("checkpoint/domain cost totals mismatch")
        else:
            self._native_commissions = view.commissions
            self._native_funding = view.funding
            self._native_slippage_cost = view.realized_slippage_cost
        self._seen_funding_settlement_ids.update(view.funding_settlement_ids)
        if had_checkpoint and tuple(self._closing_execution_ids) != tuple(
            view.closing_execution_ids
        ):
            raise Pyo3SmokeProfileError("checkpoint/domain closing executions mismatch")
        self._closing_execution_ids = list(view.closing_execution_ids)
        domain_reason = view.final_close_reason or view.pending_close_reason
        if had_checkpoint and self._last_close_reason not in {None, domain_reason}:
            raise Pyo3SmokeProfileError("checkpoint/domain close reason mismatch")
        self._last_close_reason = domain_reason or self._last_close_reason

        recovered_fills = tuple(
            _ExposureFill(
                execution_id=fill.execution_id,
                role=fill.role,
                original_quantity=fill.original_quantity,
                remaining_quantity=fill.remaining_quantity,
                side=fill.side,
            )
            for fill in view.entry_fills
        )
        if had_checkpoint and recovered_fills != tuple(self._exposure_fills.values()):
            raise Pyo3SmokeProfileError("checkpoint/domain exposure fill mismatch")
        self._exposure_fills = {fill.execution_id: fill for fill in recovered_fills}
        has_exposure = any(fill.remaining_quantity > ZERO for fill in recovered_fills)
        if has_exposure and not had_checkpoint:
            raise Pyo3SmokeProfileError(
                "active exposure restart requires a transport recovery checkpoint"
            )
        for order in view.orders:
            if order.status.terminal:
                self._terminal_logical_order_ids.add(order.client_order_id)
            self._restore_logical_order(order)
        known = self._restored_client_order_ids
        derivable = set(self._logical_to_actual_ids) | set(self._bindings)
        if not known <= derivable:
            missing = ",".join(sorted(known - derivable))
            raise Pyo3SmokeProfileError(
                f"known client order IDs lack recovery provenance: {missing}"
            )

    def _restore_logical_order(self, order: RecoveryOrderView) -> None:
        if order.role in {OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT}:
            if order.trigger_price is None:
                raise Pyo3SmokeProfileError("protective recovery order lacks trigger price")
            group = self._coverage_lifecycles.get(order.client_order_id)
            if group is None:
                group = _CoverageGroup(
                    logical_client_order_id=order.client_order_id,
                    intent_id=order.intent_id,
                    role=order.role,
                    side=order.side,
                    trigger_price=order.trigger_price,
                    reference_quantity=order.requested_quantity,
                    setup_id=order.setup_id,
                    submitted_published=order.status is not OrderStatus.INTENDED,
                    accepted_published=order.status
                    in {
                        OrderStatus.ACCEPTED,
                        OrderStatus.PARTIALLY_FILLED,
                        OrderStatus.FILLED,
                    },
                    terminal_published=order.status.terminal,
                    cumulative_filled_quantity=order.filled_quantity,
                )
                self._coverage_lifecycles[order.client_order_id] = group
            elif (
                group.role is not order.role
                or group.trigger_price != order.trigger_price
                or group.setup_id != order.setup_id
            ):
                raise Pyo3SmokeProfileError("checkpoint/domain coverage group mismatch")
            if order.status.active:
                self._coverage_groups[order.role] = group
            for execution_id, fill in sorted(self._exposure_fills.items()):
                if fill.remaining_quantity <= ZERO:
                    continue
                actual_id = group.helpers_by_execution_id.get(execution_id)
                expected_id = _helper_client_order_id(
                    group.logical_client_order_id,
                    group.role,
                    execution_id,
                    group.trigger_price,
                )
                if actual_id not in {None, expected_id}:
                    raise Pyo3SmokeProfileError(
                        "coverage helper ID is not deterministic: "
                        f"logical={group.logical_client_order_id} "
                        f"execution={execution_id} actual={actual_id} expected={expected_id}"
                    )
                group.helpers_by_execution_id[execution_id] = expected_id
                if expected_id not in self._bindings:
                    binding = _OrderBinding(
                        role=group.role,
                        intent_id=group.intent_id,
                        logical_client_order_id=group.logical_client_order_id,
                        actual_client_order_id=expected_id,
                        side=group.side,
                        requested_quantity=fill.original_quantity,
                        reduce_only=True,
                        close_position=False,
                        protected_execution_id=execution_id,
                        smoke_helper=True,
                        setup_id=group.setup_id,
                    )
                    self._register_recovered_binding(binding)
            return

        actual_id = order.client_order_id
        existing = self._bindings.get(actual_id)
        close_reason = (
            (existing.close_reason if existing is not None else self._last_close_reason)
            if order.role is OrderRole.CLOSE_ALL
            else None
        )
        binding = _OrderBinding(
            role=order.role,
            intent_id=order.intent_id,
            logical_client_order_id=order.client_order_id,
            actual_client_order_id=actual_id,
            side=order.side,
            requested_quantity=order.requested_quantity,
            reduce_only=order.reduce_only,
            close_position=order.close_position,
            protected_execution_id=order.protected_execution_id,
            close_reason=close_reason,
            smoke_helper=False,
            setup_id=order.setup_id,
        )
        if existing is not None and existing != binding:
            raise Pyo3SmokeProfileError("checkpoint/domain native binding mismatch")
        if existing is None:
            self._register_recovered_binding(binding)
        if order.status is not OrderStatus.INTENDED:
            self._submitted_client_order_ids.add(actual_id)

    def _register_recovered_binding(self, binding: _OrderBinding) -> None:
        self._bindings[binding.actual_client_order_id] = binding
        self._logical_to_actual_ids.setdefault(binding.logical_client_order_id, set()).add(
            binding.actual_client_order_id
        )

    def on_start(self) -> None:
        """Resolve cache dependencies, subscribe, and optionally reconcile recovery."""

        instrument = self.cache.instrument(self._instrument_id)
        if instrument is None:
            raise Pyo3SmokeProfileError(f"instrument {self._instrument_id} missing from cache")
        self._instrument = instrument
        self._recovering_startup = True
        try:
            self._bind_cached_orders_for_recovery()
            self._prime_restored_funding_adjustments()
            if self._marking_bar_type is not None:
                self.subscribe_bars(self._marking_bar_type)
            self.subscribe_bars(self._bar_type)
            self.subscribe_funding_rates(self._instrument_id)
            if self._reconcile_on_start:
                self._reconcile("startup recovery")
                self._replay_recovery_outbox_after_query()
        finally:
            self._recovering_startup = False

    def on_stop(self) -> None:
        """Drain a final native funding settlement before the strategy stops."""

        self._drain_funding_adjustments()
        self._try_finalize_flat_reconciliation()
        if self._marking_bar_type is not None:
            self.unsubscribe_bars(self._marking_bar_type)
        self.unsubscribe_bars(self._bar_type)
        self.unsubscribe_funding_rates(self._instrument_id)

    def on_bar(self, bar: Any) -> None:
        """Execute prior strategic intents, then deliver this final closed bar."""

        if self._marking_bar_type is not None and bar.bar_type == self._marking_bar_type:
            self._ingest_marking_bar(bar)
            return
        if bar.bar_type != self._bar_type:
            return

        self._drain_funding_adjustments()
        self._drain_deferred_reconciliation()
        close_ns = int(bar.ts_init)
        if (
            self._last_delivered_bar_close_ns is not None
            and close_ns <= self._last_delivered_bar_close_ns
        ):
            return
        self._try_finalize_flat_reconciliation()
        pending = tuple(self._scheduled_market_intents)
        self._scheduled_market_intents.clear()
        for intent in pending:
            self._submit_scheduled_market(intent)

        self._publish_equity(bar)
        if self._before_bar_domain_events is not None:
            for event in self._before_bar_domain_events(bar):
                self._apply_domain_event(event)
        if self._deliver_domain_bar is not None and not self._deliver_domain_bar(bar):
            self._last_delivered_bar_close_ns = close_ns
            self._persist_adapter_checkpoint()
            return

        features = self._feature_source(bar)
        open_ns = close_ns - self._interval_ns + MILLISECOND_NS
        event = BarClosed(
            **self._envelope(
                event_id=f"bar:{self._instrument_id}:{close_ns}",
                occurred_ns=close_ns,
                source="nautilus_pyo3.bar",
            ),
            bar_id=f"{self._instrument_id}:{close_ns}",
            open_time_utc=_datetime_from_ns(open_ns),
            close_time_utc=_datetime_from_ns(close_ns),
            open=bar.open.as_decimal(),
            high=bar.high.as_decimal(),
            low=bar.low.as_decimal(),
            close=bar.close.as_decimal(),
            volume=bar.volume.as_decimal(),
            bb_upper=features.bb_upper,
            bb_lower=features.bb_lower,
            stoch_k=features.stoch_k,
            stoch_d=features.stoch_d,
            previous_stoch_k=features.previous_stoch_k,
            previous_stoch_d=features.previous_stoch_d,
            is_final=True,
        )
        self._apply_domain_event(event)
        self._last_delivered_bar_close_ns = close_ns
        self._persist_adapter_checkpoint()

    def _ingest_marking_bar(self, bar: Any) -> None:
        """Mapuje natywny M5/M10 bar na czysty domain event bez signal logic."""

        if self._marking_interval_ns is None or self._marking_timeframe is None:
            raise Pyo3SmokeProfileError("marking callback without configured interval")
        close_ns = int(bar.ts_init)
        open_ns = close_ns - self._marking_interval_ns + MILLISECOND_NS
        event = MarkingBarClosed(
            **self._envelope(
                event_id=f"marking:{self._instrument_id}:{self._marking_timeframe}:{close_ns}",
                occurred_ns=close_ns,
                source=f"nautilus_pyo3.marking.{self._marking_timeframe}",
            ),
            bar_id=f"{self._instrument_id}:{self._marking_timeframe}:{close_ns}",
            timeframe=self._marking_timeframe,
            open_time_utc=_datetime_from_ns(open_ns),
            close_time_utc=_datetime_from_ns(close_ns),
            open=bar.open.as_decimal(),
            high=bar.high.as_decimal(),
            low=bar.low.as_decimal(),
            close=bar.close.as_decimal(),
            volume=bar.volume.as_decimal(),
            is_final=True,
        )
        self._apply_domain_event(event)
        self._persist_adapter_checkpoint()

    def on_order_submitted(self, event: Any) -> None:
        binding = self._binding(event.client_order_id)
        if not self._accept_native_lifecycle_event(event):
            return
        if binding.logical_client_order_id in self._terminal_logical_order_ids:
            self._persist_adapter_checkpoint()
            return
        if binding.smoke_helper:
            group = self._helper_group(binding)
            if group is None or group.submitted_published:
                self._persist_adapter_checkpoint()
                return
            group.submitted_published = True
            self._apply_domain_event(
                OrderSubmitted(
                    **self._logical_helper_envelope(event, binding),
                    intent_id=group.intent_id,
                    role=group.role,
                    requested_quantity=group.reference_quantity,
                    side=group.side,
                    reduce_only=False,
                    close_position=True,
                    venue_order_id=None,
                )
            )
            return
        self._apply_domain_event(
            OrderSubmitted(
                **self._native_envelope(event, binding),
                intent_id=binding.intent_id,
                role=binding.role,
                requested_quantity=binding.requested_quantity,
                side=binding.side,
                reduce_only=binding.reduce_only,
                close_position=binding.close_position,
                venue_order_id=None,
            )
        )

    def on_order_accepted(self, event: Any) -> None:
        binding = self._binding(event.client_order_id)
        if not self._accept_native_lifecycle_event(event):
            return
        if binding.logical_client_order_id in self._terminal_logical_order_ids:
            self._persist_adapter_checkpoint()
            return
        if binding.smoke_helper:
            group = self._helper_group(binding)
            if group is None or group.accepted_published:
                self._persist_adapter_checkpoint()
                return
            group.accepted_published = True
            self._apply_domain_event(
                OrderAccepted(
                    **self._logical_helper_envelope(event, binding),
                    role=group.role,
                    venue_order_id=None,
                )
            )
            return
        self._apply_domain_event(
            OrderAccepted(
                **self._native_envelope(event, binding),
                role=binding.role,
                venue_order_id=_optional_string(getattr(event, "venue_order_id", None)),
            )
        )

    def on_order_rejected(self, event: Any) -> None:
        self._publish_rejected(event, str(getattr(event, "reason", "venue rejected")))

    def on_order_denied(self, event: Any) -> None:
        self._publish_rejected(event, str(getattr(event, "reason", "risk denied")))

    def on_order_canceled(self, event: Any) -> None:
        binding = self._binding(event.client_order_id)
        if not self._accept_native_lifecycle_event(event):
            return
        if binding.logical_client_order_id in self._terminal_logical_order_ids:
            self._persist_adapter_checkpoint()
            self._try_finalize_flat_reconciliation()
            return
        if binding.smoke_helper:
            self._publish_helper_terminal(event, binding, rejected=False)
            self._try_finalize_flat_reconciliation()
            return
        self._apply_domain_event(
            OrderCanceled(
                **self._native_envelope(event, binding),
                role=binding.role,
                reason="venue canceled",
                cumulative_filled_quantity=self._cumulative_filled(event.client_order_id),
            )
        )
        self._try_finalize_flat_reconciliation()

    def on_order_filled(self, event: Any) -> None:
        """Map one unique native execution and install protection synchronously."""

        self._drain_funding_adjustments()
        binding = self._binding(event.client_order_id)
        if not self._accept_native_lifecycle_event(event):
            return
        execution_id = str(event.trade_id)
        last_quantity = event.last_qty.as_decimal()
        commission = ZERO
        if event.commission is not None:
            commission = event.commission.as_decimal()
            self._native_commissions += commission
        if binding.setup_id is not None:
            self._native_slippage_cost += last_quantity * self._slippage_per_unit

        if binding.role in {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY}:
            self._exposure_fills.setdefault(
                execution_id,
                _ExposureFill(
                    execution_id=execution_id,
                    role=binding.role,
                    original_quantity=last_quantity,
                    remaining_quantity=last_quantity,
                    side=binding.side,
                ),
            )

        native_order = self.cache.order(event.client_order_id)
        if native_order is None:
            raise Pyo3SmokeProfileError(f"filled order {event.client_order_id} missing")
        if binding.smoke_helper:
            group = self._helper_group(binding)
            if group is None:
                raise Pyo3SmokeProfileError(
                    f"helper {binding.actual_client_order_id} has no logical lifecycle"
                )
            group.cumulative_filled_quantity += last_quantity
            cumulative = group.cumulative_filled_quantity
            covered_quantity = sum(
                (
                    self._bindings[actual_id].requested_quantity
                    for actual_id in group.helpers_by_execution_id.values()
                ),
                start=ZERO,
            )
            partial = cumulative < max(covered_quantity, group.reference_quantity)
            if not partial:
                group.terminal_published = True
            envelope = self._logical_helper_envelope(event, binding)
        else:
            cumulative = _decimal_from_mapping(native_order.to_dict(), "filled_qty")
            partial = native_order.status == nt.OrderStatus.PARTIALLY_FILLED
            envelope = self._native_envelope(event, binding)
        if binding.role in {
            OrderRole.BASE_STOP,
            OrderRole.TAKE_PROFIT,
            OrderRole.CLOSE_ALL,
        }:
            self._consume_exposure_fills(last_quantity, addon_only=False)
            self._closing_execution_ids.append(execution_id)
            if binding.role is OrderRole.BASE_STOP:
                self._last_close_reason = CloseReason.BASE_SL
            elif binding.role is OrderRole.TAKE_PROFIT:
                self._last_close_reason = CloseReason.TP
            else:
                self._last_close_reason = binding.close_reason
        elif binding.role in {OrderRole.ADDON_STOP, OrderRole.REDUCE_ADDON}:
            if binding.role is OrderRole.ADDON_STOP and binding.protected_execution_id is None:
                raise Pyo3SmokeProfileError(
                    "structural add-on stop lacks protected execution attribution"
                )
            self._consume_exposure_fills(
                last_quantity,
                addon_only=True,
                preferred_execution_id=(
                    binding.protected_execution_id if binding.role is OrderRole.ADDON_STOP else None
                ),
            )
        event_type = OrderPartiallyFilled if partial else OrderFilled
        if not partial:
            self._terminal_logical_order_ids.add(binding.logical_client_order_id)
        fill_price = event.last_px.as_decimal()
        benchmark_price: Decimal | None = None
        if binding.setup_id is not None and self._slippage_per_unit > ZERO:
            benchmark_price = fill_price - self._slippage_per_unit * binding.side.sign
            if benchmark_price <= ZERO:
                raise Pyo3SmokeProfileError(
                    "configured slippage produces a non-positive benchmark price"
                )
        domain_fill = event_type(
            **envelope,
            execution_id=execution_id,
            role=binding.role,
            last_quantity=last_quantity,
            cumulative_quantity=cumulative,
            price=fill_price,
            commission=commission,
            benchmark_price=benchmark_price,
        )
        self._apply_domain_event(domain_fill)

        if binding.role in {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY}:
            self._ensure_dynamic_coverage()
        elif binding.role is OrderRole.ADDON_STOP and not partial:
            self._cancel_helpers_for_exposure(binding.protected_execution_id)
        elif binding.role is OrderRole.BASE_STOP:
            self._cancel_coverage_group(OrderRole.TAKE_PROFIT)
            self._cancel_structural_addon_stops()
        elif binding.role is OrderRole.TAKE_PROFIT:
            self._cancel_coverage_group(OrderRole.BASE_STOP)
            self._cancel_structural_addon_stops()

    def on_position_opened(self, event: Any) -> None:
        self._publish_position_changed(event)

    def on_position_changed(self, event: Any) -> None:
        self._publish_position_changed(event)

    def on_position_closed(self, event: Any) -> None:
        """Drain funding first, then finalize a fully attributed domain position."""

        self._drain_funding_adjustments()
        if not self._accept_native_position_lifecycle_event(event):
            return
        binding = self._position_event_binding(event)
        setup_id = None if binding is None else binding.setup_id
        current_setup = setup_id is not None and setup_id == self._active_setup_id
        if binding is not None and binding.role is OrderRole.BASE_STOP:
            reason = CloseReason.BASE_SL
        elif binding is not None and binding.role is OrderRole.TAKE_PROFIT:
            reason = CloseReason.TP
        elif binding is not None and binding.close_reason is not None:
            reason = binding.close_reason
        else:
            reason = (
                self._last_close_reason if current_setup else CloseReason.ENGINE_ERROR
            ) or CloseReason.ENGINE_ERROR
        if current_setup:
            native_realized = event.realized_pnl.as_decimal()
            realized_price_pnl = (
                native_realized
                - self._native_funding
                + self._native_commissions
                + self._native_slippage_cost
            )
            commissions = self._native_commissions
            funding = self._native_funding
            slippage = self._native_slippage_cost
            closing_execution_ids = tuple(self._closing_execution_ids)
        else:
            realized_price_pnl = ZERO
            commissions = ZERO
            funding = ZERO
            slippage = ZERO
            closing_execution_ids = ()
        self._apply_domain_event(
            PositionClosed(
                **self._envelope(
                    event_id=str(event.event_id),
                    occurred_ns=int(event.ts_event),
                    source="nautilus_pyo3.position",
                    client_order_id=_optional_string(getattr(event, "closing_order_id", None)),
                    setup_id=setup_id,
                    inherit_active_setup=False,
                ),
                close_reason=reason,
                realized_price_pnl=realized_price_pnl,
                commissions=commissions,
                funding=funding,
                realized_slippage_cost=slippage,
                closing_execution_ids=closing_execution_ids,
            )
        )
        if not current_setup:
            return
        self._awaiting_flat_reconciliation = True
        self._cancel_all_open_orders()
        self._try_finalize_flat_reconciliation()

    def _apply_domain_event(self, event: DomainEvent) -> None:
        if self._offline_transition_error is not None:
            return
        if self._retain_domain_event is None or self._retain_domain_event(event):
            self.domain_events.append(event)
        if self._serialize_transition_snapshots:
            result = self._machine.handle(event)
        else:
            fast_handler = getattr(self._machine, "handle_without_snapshot", None)
            if not callable(fast_handler):
                raise Pyo3SmokeProfileError(
                    "machine does not support offline transition observation"
                )
            try:
                result = cast(Callable[[DomainEvent], TransitionView], fast_handler)(event)
            except Exception as exc:
                self._offline_transition_error = (
                    f"{type(event).__name__}:{event.event_id}:"
                    f"setup={event.setup_id}:active_setup={self._active_setup_id}:"
                    f"client={event.client_order_id}:"
                    f"role={getattr(event, 'role', None)}:"
                    f"execution={getattr(event, 'execution_id', None)}:"
                    f"{type(exc).__name__}:{exc}"
                )
                return
        if self._persist_recovery_transition is not None:
            self._persist_recovery_transition(
                result.snapshot_json,
                self.recovery_checkpoint(),
            )
        if self._persist_transition is not None:
            self._persist_transition(result.snapshot_json)
        if self._transition_observer is not None:
            self._transition_observer(event)
        for intent in result.intents:
            self._route_intent(intent)

    def _persist_adapter_checkpoint(self) -> None:
        if self._persist_recovery_transition is None:
            return
        self._persist_recovery_transition(
            self._machine.snapshot_json(),
            self.recovery_checkpoint(),
        )

    def _route_intent(self, intent: DomainIntent) -> None:
        if isinstance(intent, RequestReconciliation) and self._reconciling:
            self._deferred_reconciliation_reason = intent.reason
            return
        if intent.idempotency_key in self._routed_intent_keys:
            return
        self._routed_intent_keys.add(intent.idempotency_key)
        if intent.setup_id is not None:
            if self._active_setup_id not in {None, intent.setup_id}:
                raise Pyo3SmokeProfileError("new setup routed before flat reconciliation")
            self._active_setup_id = intent.setup_id

        if isinstance(intent, (SubmitBaseOrder, SubmitAddonOrder)):
            self._scheduled_market_intents.append(intent)
            self._restored_scheduled_intent_ids.discard(intent.intent_id)
            self._persist_adapter_checkpoint()
            return
        if isinstance(intent, CloseAll):
            if intent.close_reason in {CloseReason.TP, CloseReason.MANUAL}:
                self._scheduled_market_intents.append(intent)
                self._restored_scheduled_intent_ids.discard(intent.intent_id)
                self._persist_adapter_checkpoint()
            else:
                self._submit_market_intent(intent, reduce_only=True)
            return
        if isinstance(intent, ReduceAddon):
            self._submit_market_intent(intent, reduce_only=True)
            return
        if isinstance(intent, SubmitBaseStop):
            self._install_coverage_group(
                logical_client_order_id=intent.client_order_id,
                intent_id=intent.intent_id,
                role=OrderRole.BASE_STOP,
                side=intent.side,
                trigger_price=intent.trigger_price,
                reference_quantity=intent.reference_quantity,
                setup_id=intent.setup_id,
            )
            return
        if isinstance(intent, SubmitTakeProfit):
            self._install_coverage_group(
                logical_client_order_id=intent.client_order_id,
                intent_id=intent.intent_id,
                role=OrderRole.TAKE_PROFIT,
                side=intent.side,
                trigger_price=intent.trigger_price,
                reference_quantity=intent.reference_quantity,
                setup_id=intent.setup_id,
            )
            return
        if isinstance(intent, SubmitAddonStop):
            self._submit_stop(
                client_order_id=intent.client_order_id,
                logical_client_order_id=intent.client_order_id,
                intent_id=intent.intent_id,
                role=OrderRole.ADDON_STOP,
                side=intent.side,
                quantity=intent.quantity,
                trigger_price=intent.trigger_price,
                protected_execution_id=intent.fill_execution_id,
                setup_id=intent.setup_id,
            )
            return
        if isinstance(intent, CancelOrder):
            self._cancel_logical_order(intent.target_client_order_id)
            return
        if isinstance(intent, ReplaceOrder):
            self._replace_order(intent)
            return
        if isinstance(intent, RequestReconciliation):
            self._reconcile(intent.reason)
            return
        if isinstance(intent, PersistSnapshot):
            return
        raise Pyo3SmokeProfileError(f"unsupported domain intent {type(intent).__name__}")

    def _submit_scheduled_market(self, intent: DomainIntent) -> None:
        if isinstance(intent, (SubmitBaseOrder, SubmitAddonOrder)):
            self._submit_market_intent(intent, reduce_only=False)
            return
        if isinstance(intent, CloseAll):
            self._submit_market_intent(intent, reduce_only=True)
            return
        raise Pyo3SmokeProfileError(f"invalid scheduled intent {type(intent).__name__}")

    def _submit_market_intent(
        self,
        intent: SubmitBaseOrder | SubmitAddonOrder | CloseAll | ReduceAddon,
        *,
        reduce_only: bool,
    ) -> None:
        instrument = self._require_instrument()
        if intent.client_order_id in self._submitted_client_order_ids:
            return
        if isinstance(intent, SubmitBaseOrder):
            role = OrderRole.BASE_ENTRY
            close_reason = None
        elif isinstance(intent, SubmitAddonOrder):
            role = OrderRole.ADDON_ENTRY
            close_reason = None
        elif isinstance(intent, CloseAll):
            role = OrderRole.CLOSE_ALL
            close_reason = intent.close_reason
        else:
            role = OrderRole.REDUCE_ADDON
            close_reason = None
        binding = _OrderBinding(
            role=role,
            intent_id=intent.intent_id,
            logical_client_order_id=intent.client_order_id,
            actual_client_order_id=intent.client_order_id,
            side=intent.side,
            requested_quantity=intent.quantity,
            reduce_only=reduce_only,
            close_position=False,
            close_reason=close_reason,
            setup_id=intent.setup_id,
        )
        order = self.order_factory.market(
            instrument_id=self._instrument_id,
            order_side=_native_order_side(intent.side),
            quantity=instrument.make_qty(intent.quantity),
            time_in_force=nt.TimeInForce.GTC,
            reduce_only=reduce_only,
            client_order_id=nt.ClientOrderId.from_str(intent.client_order_id),
        )
        self._submit_bound_order(order, binding)

    def _install_coverage_group(
        self,
        *,
        logical_client_order_id: str,
        intent_id: str,
        role: OrderRole,
        side: Side,
        trigger_price: Decimal,
        reference_quantity: Decimal,
        setup_id: str | None,
    ) -> None:
        if role not in {OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT}:
            raise AssertionError("dynamic coverage is only valid for base SL/TP")
        existing = self._coverage_groups.get(role)
        if existing is not None:
            if (
                existing.logical_client_order_id == logical_client_order_id
                and existing.trigger_price == trigger_price
                and existing.reference_quantity == reference_quantity
            ):
                self._ensure_dynamic_coverage()
                return
            self._cancel_coverage_group(role)
        group = _CoverageGroup(
            logical_client_order_id=logical_client_order_id,
            intent_id=intent_id,
            role=role,
            side=side,
            trigger_price=trigger_price,
            reference_quantity=reference_quantity,
            setup_id=setup_id,
        )
        self._coverage_groups[role] = group
        self._coverage_lifecycles[logical_client_order_id] = group
        self._ensure_dynamic_coverage()

    def _ensure_dynamic_coverage(self) -> None:
        for group in self._coverage_groups.values():
            for execution_id, exposure_fill in self._exposure_fills.items():
                if exposure_fill.remaining_quantity <= ZERO:
                    continue
                if execution_id in group.helpers_by_execution_id:
                    continue
                actual_id = _helper_client_order_id(
                    group.logical_client_order_id,
                    group.role,
                    execution_id,
                    group.trigger_price,
                )
                group.helpers_by_execution_id[execution_id] = actual_id
                if group.role is OrderRole.BASE_STOP:
                    self._submit_stop(
                        client_order_id=actual_id,
                        logical_client_order_id=group.logical_client_order_id,
                        intent_id=group.intent_id,
                        role=group.role,
                        side=group.side,
                        quantity=exposure_fill.remaining_quantity,
                        trigger_price=group.trigger_price,
                        protected_execution_id=execution_id,
                        smoke_helper=True,
                        setup_id=group.setup_id,
                    )
                else:
                    self._submit_target(
                        client_order_id=actual_id,
                        logical_client_order_id=group.logical_client_order_id,
                        intent_id=group.intent_id,
                        side=group.side,
                        quantity=exposure_fill.remaining_quantity,
                        trigger_price=group.trigger_price,
                        protected_execution_id=execution_id,
                        setup_id=group.setup_id,
                    )

    def _submit_stop(
        self,
        *,
        client_order_id: str,
        logical_client_order_id: str,
        intent_id: str,
        role: OrderRole,
        side: Side,
        quantity: Decimal,
        trigger_price: Decimal,
        protected_execution_id: str | None,
        smoke_helper: bool = False,
        setup_id: str | None = None,
    ) -> None:
        instrument = self._require_instrument()
        if client_order_id in self._submitted_client_order_ids:
            return
        binding = _OrderBinding(
            role=role,
            intent_id=intent_id,
            logical_client_order_id=logical_client_order_id,
            actual_client_order_id=client_order_id,
            side=side,
            requested_quantity=quantity,
            reduce_only=True,
            close_position=False,
            protected_execution_id=protected_execution_id,
            smoke_helper=smoke_helper,
            setup_id=setup_id,
        )
        order = self.order_factory.stop_market(
            instrument_id=self._instrument_id,
            order_side=_native_order_side(side),
            quantity=instrument.make_qty(quantity),
            trigger_price=instrument.make_price(trigger_price),
            trigger_type=nt.TriggerType.LAST_PRICE,
            time_in_force=nt.TimeInForce.GTC,
            reduce_only=True,
            client_order_id=nt.ClientOrderId.from_str(client_order_id),
        )
        self._submit_bound_order(order, binding)

    def _submit_target(
        self,
        *,
        client_order_id: str,
        logical_client_order_id: str,
        intent_id: str,
        side: Side,
        quantity: Decimal,
        trigger_price: Decimal,
        protected_execution_id: str,
        setup_id: str | None,
    ) -> None:
        instrument = self._require_instrument()
        if client_order_id in self._submitted_client_order_ids:
            return
        binding = _OrderBinding(
            role=OrderRole.TAKE_PROFIT,
            intent_id=intent_id,
            logical_client_order_id=logical_client_order_id,
            actual_client_order_id=client_order_id,
            side=side,
            requested_quantity=quantity,
            reduce_only=True,
            close_position=False,
            protected_execution_id=protected_execution_id,
            smoke_helper=True,
            setup_id=setup_id,
        )
        order = self.order_factory.limit(
            instrument_id=self._instrument_id,
            order_side=_native_order_side(side),
            quantity=instrument.make_qty(quantity),
            price=instrument.make_price(trigger_price),
            time_in_force=nt.TimeInForce.GTC,
            post_only=False,
            reduce_only=True,
            client_order_id=nt.ClientOrderId.from_str(client_order_id),
        )
        self._submit_bound_order(order, binding)

    def _submit_bound_order(self, order: Any, binding: _OrderBinding) -> None:
        actual_id = binding.actual_client_order_id
        if actual_id in self._submitted_client_order_ids:
            return
        self._bindings[actual_id] = binding
        self._logical_to_actual_ids.setdefault(binding.logical_client_order_id, set()).add(
            actual_id
        )
        self._submitted_client_order_ids.add(actual_id)
        self._restored_scheduled_intent_ids.discard(binding.intent_id)
        self._persist_adapter_checkpoint()
        self.submit_order(order)

    def _replace_order(self, intent: ReplaceOrder) -> None:
        self._cancel_logical_order(intent.previous_client_order_id)
        if intent.role in {OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT}:
            self._install_coverage_group(
                logical_client_order_id=intent.client_order_id,
                intent_id=intent.intent_id,
                role=intent.role,
                side=intent.side,
                trigger_price=intent.trigger_price,
                reference_quantity=intent.quantity,
                setup_id=intent.setup_id,
            )
            return
        self._submit_stop(
            client_order_id=intent.client_order_id,
            logical_client_order_id=intent.client_order_id,
            intent_id=intent.intent_id,
            role=intent.role,
            side=intent.side,
            quantity=intent.quantity,
            trigger_price=intent.trigger_price,
            protected_execution_id=None,
            setup_id=intent.setup_id,
        )

    def _cancel_logical_order(self, logical_client_order_id: str) -> None:
        retained: list[DomainIntent] = []
        removed_intent_ids: set[str] = set()
        for intent in self._scheduled_market_intents:
            if getattr(intent, "client_order_id", None) == logical_client_order_id:
                removed_intent_ids.add(intent.intent_id)
            else:
                retained.append(intent)
        if removed_intent_ids:
            self._scheduled_market_intents = retained
            self._restored_scheduled_intent_ids.difference_update(removed_intent_ids)
            self._persist_adapter_checkpoint()
        for actual_id in tuple(self._logical_to_actual_ids.get(logical_client_order_id, ())):
            self._cancel_actual_order(actual_id)

    def _cancel_actual_order(self, actual_client_order_id: str) -> None:
        native_id = nt.ClientOrderId.from_str(actual_client_order_id)
        order = self.cache.order(native_id)
        if order is not None and bool(getattr(order, "is_open", False)):
            self._persist_adapter_checkpoint()
            self.cancel_order(native_id)

    def _cancel_coverage_group(self, role: OrderRole) -> None:
        group = self._coverage_groups.pop(role, None)
        if group is None:
            return
        for actual_id in tuple(group.helpers_by_execution_id.values()):
            self._cancel_actual_order(actual_id)

    def _cancel_helpers_for_exposure(self, execution_id: str | None) -> None:
        if execution_id is None:
            return
        for group in self._coverage_groups.values():
            actual_id = group.helpers_by_execution_id.pop(execution_id, None)
            if actual_id is not None:
                self._cancel_actual_order(actual_id)

    def _cancel_structural_addon_stops(self) -> None:
        for actual_id, binding in tuple(self._bindings.items()):
            if binding.role is OrderRole.ADDON_STOP:
                self._cancel_actual_order(actual_id)

    def _cancel_all_open_orders(self) -> None:
        for order in tuple(self.cache.orders_open(instrument_id=self._instrument_id)):
            self._persist_adapter_checkpoint()
            self.cancel_order(order.client_order_id)

    def _helper_group(self, binding: _OrderBinding) -> _CoverageGroup | None:
        return self._coverage_lifecycles.get(binding.logical_client_order_id)

    def _accept_native_lifecycle_event(self, event: Any) -> bool:
        event_id = str(event.event_id)
        if event_id in self._seen_native_lifecycle_event_ids:
            return False
        self._seen_native_lifecycle_event_ids.add(event_id)
        return True

    def _accept_native_position_lifecycle_event(self, event: Any) -> bool:
        event_id = str(event.event_id)
        if event_id in self._seen_native_lifecycle_event_ids:
            return False
        fingerprint = _native_position_fingerprint(event)
        self._seen_native_lifecycle_event_ids.add(event_id)
        if fingerprint in self._seen_native_position_fingerprints:
            self._persist_adapter_checkpoint()
            return False
        self._seen_native_position_fingerprints.add(fingerprint)
        return True

    def _position_event_binding(self, event: Any) -> _OrderBinding | None:
        for attribute in ("closing_order_id", "opening_order_id"):
            native_id = getattr(event, attribute, None)
            if native_id is None:
                continue
            binding = self._bindings.get(str(native_id))
            if binding is not None:
                return binding
        return None

    def _consume_exposure_fills(
        self,
        quantity: Decimal,
        *,
        addon_only: bool,
        preferred_execution_id: str | None = None,
    ) -> None:
        remaining = quantity
        if preferred_execution_id is not None:
            preferred = self._exposure_fills.get(preferred_execution_id)
            if preferred is None or preferred.role is not OrderRole.ADDON_ENTRY:
                raise Pyo3SmokeProfileError("structural add-on stop lacks protected fill inventory")
        roles = (
            (OrderRole.ADDON_ENTRY,)
            if addon_only
            else (OrderRole.ADDON_ENTRY, OrderRole.BASE_ENTRY)
        )
        for role in roles:
            role_execution_ids = [
                execution_id
                for execution_id, fill in self._exposure_fills.items()
                if fill.role is role
            ]
            if preferred_execution_id is not None and role is OrderRole.ADDON_ENTRY:
                role_execution_ids = [preferred_execution_id] + [
                    execution_id
                    for execution_id in role_execution_ids
                    if execution_id != preferred_execution_id
                ]
            for execution_id in role_execution_ids:
                fill = self._exposure_fills[execution_id]
                if fill.remaining_quantity <= ZERO:
                    continue
                consumed = min(remaining, fill.remaining_quantity)
                self._exposure_fills[execution_id] = _ExposureFill(
                    execution_id=fill.execution_id,
                    role=fill.role,
                    original_quantity=fill.original_quantity,
                    remaining_quantity=fill.remaining_quantity - consumed,
                    side=fill.side,
                )
                remaining -= consumed
                if remaining == ZERO:
                    return
        if remaining != ZERO:
            raise Pyo3SmokeProfileError("native exit exceeds recovered exposure fills")

    def _logical_helper_envelope(
        self,
        event: Any,
        binding: _OrderBinding,
    ) -> _EventEnvelopeKwargs:
        return self._envelope(
            event_id=str(event.event_id),
            occurred_ns=int(event.ts_event),
            source="nautilus_pyo3.logical_close_position",
            client_order_id=binding.logical_client_order_id,
            correlation_id=binding.logical_client_order_id,
            setup_id=binding.setup_id,
            inherit_active_setup=False,
        )

    def _publish_helper_terminal(
        self,
        event: Any,
        binding: _OrderBinding,
        *,
        rejected: bool,
    ) -> None:
        group = self._helper_group(binding)
        if group is None or group.terminal_published:
            self._persist_adapter_checkpoint()
            return
        if not rejected:
            any_open = any(
                (order := self.cache.order(nt.ClientOrderId.from_str(actual_id))) is not None
                and bool(getattr(order, "is_open", False))
                for actual_id in group.helpers_by_execution_id.values()
            )
            if any_open:
                return
        group.terminal_published = True
        self._terminal_logical_order_ids.add(group.logical_client_order_id)
        envelope = self._logical_helper_envelope(event, binding)
        if rejected:
            self._apply_domain_event(
                OrderRejected(
                    **envelope,
                    role=group.role,
                    reason=str(getattr(event, "reason", "native helper rejected")),
                    cumulative_filled_quantity=group.cumulative_filled_quantity,
                )
            )
            for actual_id in group.helpers_by_execution_id.values():
                if actual_id != binding.actual_client_order_id:
                    self._cancel_actual_order(actual_id)
            return
        self._apply_domain_event(
            OrderCanceled(
                **envelope,
                role=group.role,
                reason="all native smoke helpers canceled",
                cumulative_filled_quantity=group.cumulative_filled_quantity,
            )
        )

    def _try_finalize_flat_reconciliation(self) -> None:
        if not self._awaiting_flat_reconciliation or self._reconciling:
            return
        if self.cache.positions_open(instrument_id=self._instrument_id):
            return
        if self.cache.orders_open(instrument_id=self._instrument_id):
            return
        self._drain_funding_adjustments()
        view = _feature_recovery_view(self._machine)
        if view is not None:
            self._unresolved_funding_settlement_ids = set(view.unresolved_funding_settlement_ids)
            if self._unresolved_funding_settlement_ids:
                self._persist_adapter_checkpoint()
                return
        self._reconcile("native flat lifecycle finalized")
        view = _feature_recovery_view(self._machine)
        if view is not None and view.active_setup_id is not None:
            self._persist_adapter_checkpoint()
            return
        self._awaiting_flat_reconciliation = False
        self._reset_setup_local_state()

    def _reset_setup_local_state(self) -> None:
        self._native_commissions = ZERO
        self._native_funding = ZERO
        self._native_slippage_cost = ZERO
        self._closing_execution_ids.clear()
        self._last_close_reason = None
        self._exposure_fills.clear()
        self._coverage_groups.clear()
        self._coverage_lifecycles.clear()
        self._unresolved_funding_settlement_ids.clear()
        self._active_setup_id = None
        self._recovery_outbox = ()
        self._persist_adapter_checkpoint()

    def _bind_cached_orders_for_recovery(self) -> None:
        open_orders = tuple(self.cache.orders_open(instrument_id=self._instrument_id))
        for order in open_orders:
            actual_id = str(order.client_order_id)
            if actual_id not in self._bindings:
                raise Pyo3SmokeProfileError(
                    f"open native order {actual_id} has no durable recovery binding"
                )
            self._submitted_client_order_ids.add(actual_id)
        if open_orders:
            self._persist_adapter_checkpoint()

    def _prime_restored_funding_adjustments(self) -> None:
        if not self._seen_funding_settlement_ids:
            return
        for position in self.cache.positions(instrument_id=self._instrument_id):
            for adjustment in position.adjustments():
                if adjustment.adjustment_type != nt.PositionAdjustmentType.FUNDING:
                    continue
                adjustment_id = str(adjustment.event_id)
                reason = str(adjustment.reason)
                settlement_id = reason.partition(":")[2] or adjustment_id
                if settlement_id in self._seen_funding_settlement_ids:
                    self._seen_adjustment_event_ids.add(adjustment_id)

    def _replay_recovery_outbox_after_query(self) -> None:
        view = _feature_recovery_view(self._machine)
        if view is None:
            return
        self._recovery_view = view
        self._recovery_outbox = tuple(view.outbox)
        open_logical_ids = {
            self._bindings[str(order.client_order_id)].logical_client_order_id
            for order in self.cache.orders_open(instrument_id=self._instrument_id)
            if str(order.client_order_id) in self._bindings
        }
        stale = [
            intent.intent_id
            for intent in view.outbox
            if getattr(intent, "client_order_id", None) in open_logical_ids
        ]
        if stale:
            raise Pyo3SmokeProfileError(
                "venue-received intents remained in durable outbox after query: "
                + ",".join(sorted(stale))
            )

    def _drain_funding_adjustments(self) -> None:
        if self._draining_adjustments or self._instrument is None:
            return
        self._draining_adjustments = True
        try:
            for position in self.cache.positions(instrument_id=self._instrument_id):
                for adjustment in position.adjustments():
                    if adjustment.adjustment_type != nt.PositionAdjustmentType.FUNDING:
                        continue
                    adjustment_id = str(adjustment.event_id)
                    reason = str(adjustment.reason)
                    settlement_id = reason.partition(":")[2] or adjustment_id
                    if adjustment.pnl_change is None:
                        raise Pyo3SmokeProfileError("funding adjustment lacks pnl_change")
                    self._publish_native_funding_adjustment(
                        adjustment_id=adjustment_id,
                        settlement_id=settlement_id,
                        amount=adjustment.pnl_change.as_decimal(),
                        occurred_ns=int(adjustment.ts_event),
                    )
        finally:
            self._draining_adjustments = False

    def _publish_native_funding_adjustment(
        self,
        *,
        adjustment_id: str,
        settlement_id: str,
        amount: Decimal,
        occurred_ns: int,
    ) -> None:
        if adjustment_id in self._seen_adjustment_event_ids:
            return
        self._seen_adjustment_event_ids.add(adjustment_id)
        if settlement_id in self._seen_funding_settlement_ids:
            self._persist_adapter_checkpoint()
            return
        self._seen_funding_settlement_ids.add(settlement_id)
        if self._active_setup_id is not None:
            self._native_funding += amount
        self._apply_domain_event(
            FundingApplied(
                **self._envelope(
                    event_id=adjustment_id,
                    occurred_ns=occurred_ns,
                    source="nautilus_pyo3.funding_adjustment",
                ),
                settlement_id=settlement_id,
                amount=amount,
            )
        )

    def _drain_deferred_reconciliation(self, *, allow_followup: bool = True) -> None:
        if self._reconciling or self._deferred_reconciliation_reason is None:
            return
        reason = self._deferred_reconciliation_reason
        self._deferred_reconciliation_reason = None
        self._reconcile(reason, _allow_followup=allow_followup)

    def _reconcile(self, reason: str, *, _allow_followup: bool = True) -> None:
        if self._reconciling:
            return
        self._reconciling = True
        try:
            positions = tuple(self.cache.positions_open(instrument_id=self._instrument_id))
            signed_quantity = sum(
                (Decimal(str(position.signed_qty)) for position in positions),
                start=ZERO,
            )
            average_price: Decimal | None = None
            if len(positions) == 1:
                average_price = Decimal(str(positions[0].avg_px_open))
            open_orders = tuple(self.cache.orders_open(instrument_id=self._instrument_id))
            actual_open_ids = tuple(str(order.client_order_id) for order in open_orders)
            missing_bindings = [
                actual_id for actual_id in actual_open_ids if actual_id not in self._bindings
            ]
            if missing_bindings:
                raise Pyo3SmokeProfileError(
                    "reconciliation found unbound native orders: "
                    + ",".join(sorted(missing_bindings))
                )
            native_by_logical: dict[str, list[Any]] = {}
            for order in open_orders:
                binding = self._bindings[str(order.client_order_id)]
                native_by_logical.setdefault(binding.logical_client_order_id, []).append(order)
            open_ids = tuple(sorted(native_by_logical))
            reconciled_orders = tuple(
                self._reconciled_order(logical_id, native_orders)
                for logical_id, native_orders in sorted(native_by_logical.items())
            )
            view = _feature_recovery_view(self._machine)
            outbox = () if view is None else view.outbox
            cancel_replays = self._prepare_absent_recovery_attempts(
                actual_open_ids=set(actual_open_ids),
                outbox=outbox,
            )
            acknowledged_intent_ids = tuple(
                sorted(
                    intent.intent_id
                    for intent in outbox
                    if getattr(intent, "client_order_id", None) in native_by_logical
                )
            )
            self._submitted_client_order_ids.update(actual_open_ids)
            now_ns = int(self.clock.timestamp_ns())
            self._apply_domain_event(
                ReconciliationCompleted(
                    **self._envelope(
                        event_id=f"reconcile:{self._sequence + 1}:{_stable_token(reason)}",
                        occurred_ns=now_ns,
                        source="nautilus_pyo3.cache_reconciliation",
                        setup_id=self._active_setup_id,
                    ),
                    signed_open_quantity=signed_quantity,
                    average_price=average_price,
                    open_client_order_ids=open_ids,
                    as_of_sequence=self._sequence,
                    open_orders=reconciled_orders,
                    acknowledged_intent_ids=acknowledged_intent_ids,
                )
            )
            self._ensure_dynamic_coverage()
            for logical_id in cancel_replays:
                self._cancel_logical_order(logical_id)
        finally:
            self._reconciling = False
        if _allow_followup:
            self._drain_deferred_reconciliation(allow_followup=False)

    def _prepare_absent_recovery_attempts(
        self,
        *,
        actual_open_ids: set[str],
        outbox: Sequence[DomainIntent],
    ) -> tuple[str, ...]:
        submit_types = (
            SubmitBaseOrder,
            SubmitAddonOrder,
            SubmitBaseStop,
            SubmitTakeProfit,
            SubmitAddonStop,
            CloseAll,
            ReduceAddon,
            ReplaceOrder,
        )
        replayable_logical_ids = {
            client_order_id
            for intent in outbox
            if isinstance(intent, submit_types)
            and (client_order_id := getattr(intent, "client_order_id", None)) is not None
        }
        canceling_logical_ids = {
            intent.target_client_order_id for intent in outbox if isinstance(intent, CancelOrder)
        }
        for logical_id in replayable_logical_ids:
            for actual_id in self._logical_to_actual_ids.get(logical_id, ()):
                binding = self._bindings[actual_id]
                if actual_id not in actual_open_ids and not binding.smoke_helper:
                    self._submitted_client_order_ids.discard(actual_id)
        for group in tuple(self._coverage_groups.values()):
            if group.logical_client_order_id in canceling_logical_ids:
                continue
            for execution_id, actual_id in tuple(group.helpers_by_execution_id.items()):
                fill = self._exposure_fills.get(execution_id)
                if (
                    actual_id not in actual_open_ids
                    and fill is not None
                    and fill.remaining_quantity > ZERO
                ):
                    self._submitted_client_order_ids.discard(actual_id)
                    del group.helpers_by_execution_id[execution_id]
        return tuple(
            sorted(
                logical_id
                for logical_id in canceling_logical_ids
                if any(
                    actual_id in actual_open_ids
                    for actual_id in self._logical_to_actual_ids.get(logical_id, ())
                )
            )
        )

    def _reconciled_order(
        self,
        logical_client_order_id: str,
        native_orders: Sequence[Any],
    ) -> ReconciledOrder:
        bindings = [self._bindings[str(order.client_order_id)] for order in native_orders]
        binding = bindings[0]
        if any(
            item.role is not binding.role
            or item.side is not binding.side
            or item.setup_id != binding.setup_id
            for item in bindings[1:]
        ):
            raise Pyo3SmokeProfileError("logical reconciliation group is inconsistent")
        group = self._coverage_lifecycles.get(logical_client_order_id)
        if group is not None:
            requested_quantity = max(
                group.reference_quantity,
                group.cumulative_filled_quantity,
            )
            filled_quantity = group.cumulative_filled_quantity
            reduce_only = False
            close_position = True
            venue_order_id = None
        else:
            requested_quantity = binding.requested_quantity
            filled_quantity = sum(
                (_decimal_from_mapping(order.to_dict(), "filled_qty") for order in native_orders),
                start=ZERO,
            )
            reduce_only = binding.reduce_only
            close_position = binding.close_position
            venue_order_id = _optional_string(getattr(native_orders[0], "venue_order_id", None))
        statuses = {_domain_order_status(order.status) for order in native_orders}
        status = (
            OrderStatus.PARTIALLY_FILLED
            if OrderStatus.PARTIALLY_FILLED in statuses
            else OrderStatus.ACCEPTED
        )
        return ReconciledOrder(
            client_order_id=logical_client_order_id,
            venue_order_id=venue_order_id,
            role=binding.role,
            status=status,
            requested_quantity=requested_quantity,
            filled_quantity=filled_quantity,
            side=binding.side,
            reduce_only=reduce_only,
            close_position=close_position,
            setup_id=binding.setup_id,
        )

    def _publish_equity(self, bar: Any) -> None:
        close_ns = int(bar.ts_init)
        if (
            self._last_published_equity_close_ns is not None
            and close_ns <= self._last_published_equity_close_ns
        ):
            return
        equity = self.portfolio.equity(venue=self._instrument_id.venue)
        if equity is None:
            return
        if hasattr(equity, "as_decimal"):
            value = equity.as_decimal()
        elif isinstance(equity, dict) and len(equity) == 1:
            value = next(iter(equity.values())).as_decimal()
        else:
            raise Pyo3SmokeProfileError(f"unsupported equity shape {type(equity).__name__}")
        self._last_published_equity_close_ns = close_ns
        self._apply_domain_event(
            AccountEquityUpdated(
                **self._envelope(
                    event_id=f"equity:{self._instrument_id}:{close_ns}",
                    occurred_ns=close_ns,
                    source="nautilus_pyo3.portfolio",
                ),
                equity=value,
            )
        )

    def _publish_rejected(self, event: Any, reason: str) -> None:
        binding = self._binding(event.client_order_id)
        if not self._accept_native_lifecycle_event(event):
            return
        if binding.logical_client_order_id in self._terminal_logical_order_ids:
            self._persist_adapter_checkpoint()
            return
        self._terminal_logical_order_ids.add(binding.logical_client_order_id)
        if binding.smoke_helper:
            self._publish_helper_terminal(event, binding, rejected=True)
            return
        self._apply_domain_event(
            OrderRejected(
                **self._native_envelope(event, binding),
                role=binding.role,
                reason=reason,
                cumulative_filled_quantity=self._cumulative_filled(event.client_order_id),
            )
        )

    def _publish_position_changed(self, event: Any) -> None:
        self._drain_funding_adjustments()
        if not self._accept_native_position_lifecycle_event(event):
            return
        binding = self._position_event_binding(event)
        setup_id = None if binding is None else binding.setup_id
        if setup_id is None or setup_id != self._active_setup_id:
            self._persist_adapter_checkpoint()
            if self._reconciling:
                self._deferred_reconciliation_reason = (
                    "stale or unattributed native position callback"
                )
            else:
                self._reconcile("stale or unattributed native position callback")
            return
        average = getattr(event, "avg_px_open", None)
        self._apply_domain_event(
            PositionChanged(
                **self._envelope(
                    event_id=str(event.event_id),
                    occurred_ns=int(event.ts_event),
                    source="nautilus_pyo3.position",
                    setup_id=setup_id,
                    inherit_active_setup=False,
                ),
                signed_quantity=Decimal(str(event.signed_qty)),
                average_price=None if average is None else Decimal(str(average)),
            )
        )

    def _cumulative_filled(self, client_order_id: Any) -> Decimal:
        order = self.cache.order(client_order_id)
        if order is None:
            return ZERO
        return _decimal_from_mapping(order.to_dict(), "filled_qty")

    def _binding(self, client_order_id: Any) -> _OrderBinding:
        actual_id = str(client_order_id)
        try:
            return self._bindings[actual_id]
        except KeyError as exc:
            raise Pyo3SmokeProfileError(f"unbound native order event {actual_id}") from exc

    def _native_envelope(
        self,
        event: Any,
        binding: _OrderBinding,
    ) -> _EventEnvelopeKwargs:
        return self._envelope(
            event_id=str(event.event_id),
            occurred_ns=int(event.ts_event),
            source="nautilus_pyo3.order",
            client_order_id=binding.actual_client_order_id,
            correlation_id=binding.logical_client_order_id,
            setup_id=binding.setup_id,
            inherit_active_setup=False,
        )

    def _envelope(
        self,
        *,
        event_id: str,
        occurred_ns: int,
        source: str,
        client_order_id: str | None = None,
        correlation_id: str | None = None,
        setup_id: str | None = None,
        inherit_active_setup: bool = True,
    ) -> _EventEnvelopeKwargs:
        self._sequence += 1
        return {
            "event_id": event_id,
            "strategy_id": self._domain_strategy_id,
            "instrument_id": str(self._instrument_id),
            "occurred_at_utc": _datetime_from_ns(occurred_ns),
            "source": source,
            "source_sequence": self._sequence,
            "setup_id": (
                self._active_setup_id if inherit_active_setup and setup_id is None else setup_id
            ),
            "correlation_id": correlation_id,
            "client_order_id": client_order_id,
            "causation_id": None,
        }

    def _require_instrument(self) -> Any:
        if self._instrument is None:
            raise Pyo3SmokeProfileError("strategy is not initialized")
        return self._instrument


def run_pyo3_mastermind_smoke(
    *,
    machine: MastermindMachinePort,
    strategy_id: str,
    instrument: Any,
    bar_type: Any,
    data: Sequence[Any],
    feature_source: BarFeatureSource,
    marking_bar_type: Any | None = None,
    marking_data: Sequence[Any] = (),
    marking_interval_ns: int | None = None,
    starting_balance: Decimal = Decimal("100000"),
    default_leverage: Decimal = Decimal(1),
    fill_model: Any | None = None,
    reconcile_on_start: bool = False,
    known_client_order_ids: Iterable[str] = (),
    persist_transition: Callable[[str], None] | None = None,
    persist_recovery_transition: PersistRecoveryTransition | None = None,
    recovery_checkpoint: Pyo3RecoveryCheckpoint | str | None = None,
    before_bar_domain_events: BeforeBarDomainEvents | None = None,
    deliver_domain_bar: DeliverDomainBar | None = None,
    slippage_per_unit: Decimal = ZERO,
    serialize_transition_snapshots: bool = True,
    transition_observer: TransitionObserver | None = None,
    retain_domain_event: RetainDomainEvent | None = None,
    run_metadata: Pyo3SmokeMetadata | Pyo3ResearchMetadata | None = None,
) -> Pyo3SmokeRun:
    """Uruchom adapter PyO3 i zwróć evidence odtworzone z cache silnika.

    Dotychczasowi callerzy otrzymują zamrożone smoke provenance. Session 4 musi
    przekazać :class:`Pyo3ResearchMetadata`; końcowa decyzja evidence nadal
    należy do ``BacktestResult`` i nie wynika z samego argumentu metadata.
    """

    if not data:
        raise ValueError("data must not be empty")
    if (marking_bar_type is None) != (marking_interval_ns is None):
        raise ValueError("marking bar type and interval must be configured together")
    if marking_bar_type is None and marking_data:
        raise ValueError("marking_data requires marking_bar_type")
    if marking_bar_type is not None and not marking_data:
        raise ValueError("configured marking_bar_type requires marking_data")
    if not starting_balance.is_finite() or starting_balance <= ZERO:
        raise ValueError("starting_balance must be finite and positive")
    if not default_leverage.is_finite() or default_leverage <= ZERO:
        raise ValueError("default_leverage must be finite and positive")
    settlement_currency = instrument.settlement_currency
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
        starting_balances=[nt.Money.from_str(f"{starting_balance} {settlement_currency.code}")],
        base_currency=settlement_currency,
        default_leverage=default_leverage,
        fill_model=fill_model
        or nt.DefaultFillModel(
            prob_fill_on_limit=1.0,
            prob_slippage=0.0,
            random_seed=7,
        ),
        latency_model=nt.StaticLatencyModel(base_latency_nanos=0),
        use_position_ids=False,
        use_reduce_only=True,
        use_message_queue=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    engine.add_instrument(instrument)
    engine.add_data([*marking_data, *data], sort=True)
    strategy = NautilusMastermindStrategy(
        machine=machine,
        strategy_id=strategy_id,
        instrument_id=instrument.id,
        bar_type=bar_type,
        feature_source=feature_source,
        marking_bar_type=marking_bar_type,
        marking_interval_ns=marking_interval_ns,
        reconcile_on_start=reconcile_on_start,
        known_client_order_ids=known_client_order_ids,
        persist_transition=persist_transition,
        persist_recovery_transition=persist_recovery_transition,
        recovery_checkpoint=recovery_checkpoint,
        before_bar_domain_events=before_bar_domain_events,
        deliver_domain_bar=deliver_domain_bar,
        slippage_per_unit=slippage_per_unit,
        serialize_transition_snapshots=serialize_transition_snapshots,
        transition_observer=transition_observer,
        retain_domain_event=retain_domain_event,
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        if strategy.offline_transition_error is not None:
            raise Pyo3SmokeProfileError(
                f"offline domain transition failed: {strategy.offline_transition_error}"
            )
        native_result = cast(object, engine.get_result())
        reports = build_cache_reports(engine.cache, instrument.id.venue)
        final_net = Decimal(str(engine.portfolio.net_position(instrument.id)))
        return Pyo3SmokeRun(
            metadata=run_metadata or Pyo3SmokeMetadata(),
            native_result=native_result,
            reports=reports,
            domain_events=tuple(strategy.domain_events),
            submitted_client_order_ids=strategy.submitted_client_order_ids,
            final_net_quantity=final_net,
        )
    finally:
        engine.dispose()


def build_cache_reports(cache: Any, venue: Any) -> CacheReports:
    """Build PyO3 reports without the incompatible legacy ``ReportProvider``."""

    orders = tuple(cache.orders())
    order_rows = [order.to_dict() for order in orders]
    fill_rows = [
        event.to_dict()
        for order in orders
        for event in order.events()
        if isinstance(event, nt.OrderFilled)
    ]
    position_rows = [position.to_dict() for position in cache.positions()]
    account = cache.account_for_venue(venue)
    account_rows = [] if account is None else [event.to_dict() for event in account.events]
    return CacheReports(
        orders=pd.DataFrame.from_records(order_rows),
        fills=pd.DataFrame.from_records(fill_rows),
        positions=pd.DataFrame.from_records(position_rows),
        account_events=pd.DataFrame.from_records(account_rows),
    )


def _native_order_side(side: Side) -> Any:
    return nt.OrderSide.BUY if side is Side.LONG else nt.OrderSide.SELL


def _helper_client_order_id(
    logical_id: str,
    role: OrderRole,
    execution_id: str,
    trigger_price: Decimal,
) -> str:
    seed = f"{logical_id}|{role.value}|{execution_id}|{_recovery_decimal(trigger_price)}"
    return f"MMS-{role.value[:2]}-{_stable_token(seed)[:24]}"


def _stable_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _native_position_fingerprint(event: object) -> str:
    attributes = (
        "position_id",
        "opening_order_id",
        "closing_order_id",
        "signed_qty",
        "quantity",
        "avg_px_open",
        "avg_px_close",
        "last_qty",
        "last_px",
        "realized_pnl",
        "ts_event",
        "ts_opened",
        "ts_closed",
    )
    values = [type(event).__name__]
    values.extend(str(getattr(event, attribute, None)) for attribute in attributes)
    return _stable_token("|".join(values))


def _datetime_from_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        seconds=seconds,
        microseconds=nanoseconds // 1_000,
    )


def _optional_string(value: object | None) -> str | None:
    return None if value is None else str(value)


def _decimal_from_mapping(values: dict[str, Any], key: str) -> Decimal:
    try:
        value = values[key]
    except KeyError as exc:
        raise Pyo3SmokeProfileError(f"native order report lacks {key}") from exc
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise Pyo3SmokeProfileError(f"native {key} is non-finite")
    return decimal


def _feature_recovery_view(machine: object) -> MachineRecoveryView | None:
    view = getattr(machine, "recovery_view", None)
    if view is None:
        return None
    required = (
        "active_setup_id",
        "setup_side",
        "pending_close_reason",
        "final_close_reason",
        "commissions",
        "funding",
        "realized_slippage_cost",
        "funding_settlement_ids",
        "unresolved_funding_settlement_ids",
        "closing_execution_ids",
        "entry_fills",
        "orders",
        "outbox",
    )
    missing = [name for name in required if not hasattr(view, name)]
    if missing:
        raise Pyo3SmokeProfileError("machine.recovery_view is incomplete: " + ",".join(missing))
    return cast(MachineRecoveryView, view)


def _domain_order_status(native_status: object) -> OrderStatus:
    name = getattr(native_status, "name", None)
    value = str(name if name is not None else native_status).rsplit(".", maxsplit=1)[-1]
    try:
        return OrderStatus(value)
    except ValueError as exc:
        raise Pyo3SmokeProfileError(f"unsupported native order status {value!r}") from exc


def _validate_recovery_checkpoint(checkpoint: Pyo3RecoveryCheckpoint) -> None:
    if checkpoint.schema_version != PYO3_RECOVERY_SCHEMA_VERSION:
        raise Pyo3SmokeProfileError("unsupported PyO3 recovery checkpoint schema")
    if checkpoint.execution_profile != PYO3_SMOKE_EXECUTION_PROFILE:
        raise Pyo3SmokeProfileError("recovery checkpoint execution profile mismatch")
    if not checkpoint.strategy_id or not checkpoint.instrument_id:
        raise Pyo3SmokeProfileError("recovery checkpoint scope must be non-empty")
    if type(checkpoint.source_sequence) is not int or checkpoint.source_sequence < 0:
        raise Pyo3SmokeProfileError("recovery source_sequence must be non-negative")
    for name, value in (
        ("native_commissions", checkpoint.native_commissions),
        ("native_funding", checkpoint.native_funding),
        ("native_slippage_cost", checkpoint.native_slippage_cost),
    ):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise Pyo3SmokeProfileError(f"recovery {name} must be a finite Decimal")
    if checkpoint.native_commissions < ZERO or checkpoint.native_slippage_cost < ZERO:
        raise Pyo3SmokeProfileError("recovery costs must be non-negative")
    for name, values in (
        ("closing_execution_ids", checkpoint.closing_execution_ids),
        ("submitted_client_order_ids", checkpoint.submitted_client_order_ids),
        ("scheduled_market_intent_ids", checkpoint.scheduled_market_intent_ids),
        ("seen_adjustment_event_ids", checkpoint.seen_adjustment_event_ids),
        ("seen_funding_settlement_ids", checkpoint.seen_funding_settlement_ids),
        (
            "unresolved_funding_settlement_ids",
            checkpoint.unresolved_funding_settlement_ids,
        ),
        (
            "seen_native_lifecycle_event_ids",
            checkpoint.seen_native_lifecycle_event_ids,
        ),
        (
            "seen_native_position_fingerprints",
            checkpoint.seen_native_position_fingerprints,
        ),
        ("terminal_logical_order_ids", checkpoint.terminal_logical_order_ids),
    ):
        if any(not value for value in values) or len(values) != len(set(values)):
            raise Pyo3SmokeProfileError(f"recovery {name} must contain unique IDs")
    for cursor_name, cursor_value in (
        ("last_delivered_bar_close_ns", checkpoint.last_delivered_bar_close_ns),
        (
            "last_published_equity_close_ns",
            checkpoint.last_published_equity_close_ns,
        ),
    ):
        if cursor_value is not None and (type(cursor_value) is not int or cursor_value < 0):
            raise Pyo3SmokeProfileError(f"recovery {cursor_name} must be non-negative")
    exposure_ids: set[str] = set()
    for fill in checkpoint.exposure_fills:
        if not fill.execution_id or fill.execution_id in exposure_ids:
            raise Pyo3SmokeProfileError("recovery exposure execution IDs must be unique")
        exposure_ids.add(fill.execution_id)
        if fill.role not in {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY}:
            raise Pyo3SmokeProfileError("recovery exposure role is not an entry")
        if (
            not fill.original_quantity.is_finite()
            or not fill.remaining_quantity.is_finite()
            or fill.original_quantity <= ZERO
            or fill.remaining_quantity < ZERO
            or fill.remaining_quantity > fill.original_quantity
        ):
            raise Pyo3SmokeProfileError("recovery exposure quantities are invalid")
    binding_ids: set[str] = set()
    bindings_by_id: dict[str, Pyo3RecoveryOrderBinding] = {}
    for binding in checkpoint.bindings:
        if (
            not binding.actual_client_order_id
            or not binding.logical_client_order_id
            or binding.actual_client_order_id in binding_ids
        ):
            raise Pyo3SmokeProfileError("recovery binding IDs must be non-empty and unique")
        if not binding.requested_quantity.is_finite() or binding.requested_quantity <= ZERO:
            raise Pyo3SmokeProfileError("recovery binding quantity must be positive")
        if binding.smoke_helper and (
            binding.role not in {OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT}
            or not binding.reduce_only
            or binding.close_position
            or binding.protected_execution_id not in exposure_ids
        ):
            raise Pyo3SmokeProfileError("recovery smoke-helper binding is inconsistent")
        binding_ids.add(binding.actual_client_order_id)
        bindings_by_id[binding.actual_client_order_id] = binding
    logical_group_ids: set[str] = set()
    for group in checkpoint.coverage_groups:
        if (
            not group.logical_client_order_id
            or group.logical_client_order_id in logical_group_ids
            or group.role not in {OrderRole.BASE_STOP, OrderRole.TAKE_PROFIT}
            or not group.trigger_price.is_finite()
            or group.trigger_price <= ZERO
            or not group.reference_quantity.is_finite()
            or group.reference_quantity <= ZERO
            or not group.cumulative_filled_quantity.is_finite()
            or group.cumulative_filled_quantity < ZERO
        ):
            raise Pyo3SmokeProfileError("recovery coverage group is invalid")
        logical_group_ids.add(group.logical_client_order_id)
        helper_executions: set[str] = set()
        helper_actual_ids: set[str] = set()
        for execution_id, actual_id in group.helpers_by_execution_id:
            if (
                execution_id in helper_executions
                or actual_id in helper_actual_ids
                or execution_id not in exposure_ids
                or actual_id not in bindings_by_id
            ):
                raise Pyo3SmokeProfileError("recovery coverage helper map is invalid")
            binding = bindings_by_id[actual_id]
            if (
                binding.logical_client_order_id != group.logical_client_order_id
                or binding.role is not group.role
                or binding.protected_execution_id != execution_id
            ):
                raise Pyo3SmokeProfileError("recovery coverage binding mismatch")
            helper_executions.add(execution_id)
            helper_actual_ids.add(actual_id)
    if not set(checkpoint.submitted_client_order_ids) <= binding_ids:
        raise Pyo3SmokeProfileError("submitted recovery IDs lack bindings")
    setup_local = (
        checkpoint.native_commissions != ZERO
        or checkpoint.native_funding != ZERO
        or checkpoint.native_slippage_cost != ZERO
        or bool(checkpoint.exposure_fills)
        or bool(checkpoint.coverage_groups)
        or bool(checkpoint.closing_execution_ids)
        or checkpoint.last_close_reason is not None
        or checkpoint.awaiting_flat_reconciliation
    )
    if setup_local and checkpoint.active_setup_id is None:
        raise Pyo3SmokeProfileError("setup-local recovery state lacks active_setup_id")


def _recovery_checkpoint_body(checkpoint: Pyo3RecoveryCheckpoint) -> dict[str, object]:
    return {
        "schema_version": checkpoint.schema_version,
        "execution_profile": checkpoint.execution_profile,
        "strategy_id": checkpoint.strategy_id,
        "instrument_id": checkpoint.instrument_id,
        "source_sequence": checkpoint.source_sequence,
        "active_setup_id": checkpoint.active_setup_id,
        "native_commissions": _recovery_decimal(checkpoint.native_commissions),
        "native_funding": _recovery_decimal(checkpoint.native_funding),
        "native_slippage_cost": _recovery_decimal(checkpoint.native_slippage_cost),
        "closing_execution_ids": list(checkpoint.closing_execution_ids),
        "last_close_reason": (
            None if checkpoint.last_close_reason is None else checkpoint.last_close_reason.value
        ),
        "exposure_fills": [
            {
                "execution_id": fill.execution_id,
                "role": fill.role.value,
                "original_quantity": _recovery_decimal(fill.original_quantity),
                "remaining_quantity": _recovery_decimal(fill.remaining_quantity),
                "side": fill.side.value,
            }
            for fill in checkpoint.exposure_fills
        ],
        "bindings": [
            {
                "role": binding.role.value,
                "intent_id": binding.intent_id,
                "logical_client_order_id": binding.logical_client_order_id,
                "actual_client_order_id": binding.actual_client_order_id,
                "side": binding.side.value,
                "requested_quantity": _recovery_decimal(binding.requested_quantity),
                "reduce_only": binding.reduce_only,
                "close_position": binding.close_position,
                "protected_execution_id": binding.protected_execution_id,
                "close_reason": (
                    None if binding.close_reason is None else binding.close_reason.value
                ),
                "smoke_helper": binding.smoke_helper,
                "setup_id": binding.setup_id,
            }
            for binding in checkpoint.bindings
        ],
        "coverage_groups": [
            {
                "logical_client_order_id": group.logical_client_order_id,
                "intent_id": group.intent_id,
                "role": group.role.value,
                "side": group.side.value,
                "trigger_price": _recovery_decimal(group.trigger_price),
                "reference_quantity": _recovery_decimal(group.reference_quantity),
                "setup_id": group.setup_id,
                "helpers_by_execution_id": [
                    {"execution_id": execution_id, "actual_client_order_id": actual_id}
                    for execution_id, actual_id in group.helpers_by_execution_id
                ],
                "submitted_published": group.submitted_published,
                "accepted_published": group.accepted_published,
                "terminal_published": group.terminal_published,
                "cumulative_filled_quantity": _recovery_decimal(group.cumulative_filled_quantity),
            }
            for group in checkpoint.coverage_groups
        ],
        "submitted_client_order_ids": list(checkpoint.submitted_client_order_ids),
        "scheduled_market_intent_ids": list(checkpoint.scheduled_market_intent_ids),
        "seen_adjustment_event_ids": list(checkpoint.seen_adjustment_event_ids),
        "seen_funding_settlement_ids": list(checkpoint.seen_funding_settlement_ids),
        "unresolved_funding_settlement_ids": list(checkpoint.unresolved_funding_settlement_ids),
        "seen_native_lifecycle_event_ids": list(checkpoint.seen_native_lifecycle_event_ids),
        "seen_native_position_fingerprints": list(checkpoint.seen_native_position_fingerprints),
        "terminal_logical_order_ids": list(checkpoint.terminal_logical_order_ids),
        "awaiting_flat_reconciliation": checkpoint.awaiting_flat_reconciliation,
        "last_delivered_bar_close_ns": checkpoint.last_delivered_bar_close_ns,
        "last_published_equity_close_ns": checkpoint.last_published_equity_close_ns,
    }


def _restore_recovery_checkpoint(raw: str) -> Pyo3RecoveryCheckpoint:
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise Pyo3SmokeProfileError("recovery checkpoint is not valid JSON") from exc
    if not isinstance(document, dict):
        raise Pyo3SmokeProfileError("recovery checkpoint root must be an object")
    checksum = document.pop("checksum", None)
    if not isinstance(checksum, str) or not checksum:
        raise Pyo3SmokeProfileError("recovery checkpoint checksum is missing")
    expected = hashlib.sha256(_canonical_json_bytes(document)).hexdigest()
    if checksum != expected:
        raise Pyo3SmokeProfileError("recovery checkpoint checksum mismatch")
    required = set(_recovery_checkpoint_body(_empty_recovery_checkpoint()))
    if set(document) != required:
        raise Pyo3SmokeProfileError("recovery checkpoint fields are incomplete or unknown")
    exposures = tuple(
        Pyo3RecoveryExposureFill(
            execution_id=_json_string(item, "execution_id"),
            role=OrderRole(_json_string(item, "role")),
            original_quantity=_json_decimal(item, "original_quantity"),
            remaining_quantity=_json_decimal(item, "remaining_quantity"),
            side=Side(_json_string(item, "side")),
        )
        for item in _json_mappings(document, "exposure_fills")
    )
    bindings = tuple(
        Pyo3RecoveryOrderBinding(
            role=OrderRole(_json_string(item, "role")),
            intent_id=_json_string(item, "intent_id"),
            logical_client_order_id=_json_string(item, "logical_client_order_id"),
            actual_client_order_id=_json_string(item, "actual_client_order_id"),
            side=Side(_json_string(item, "side")),
            requested_quantity=_json_decimal(item, "requested_quantity"),
            reduce_only=_json_bool(item, "reduce_only"),
            close_position=_json_bool(item, "close_position"),
            protected_execution_id=_json_optional_string(item, "protected_execution_id"),
            close_reason=(
                None
                if item.get("close_reason") is None
                else CloseReason(_json_string(item, "close_reason"))
            ),
            smoke_helper=_json_bool(item, "smoke_helper"),
            setup_id=_json_optional_string(item, "setup_id"),
        )
        for item in _json_mappings(document, "bindings")
    )
    groups: list[Pyo3RecoveryCoverageGroup] = []
    for item in _json_mappings(document, "coverage_groups"):
        helpers = tuple(
            (
                _json_string(helper, "execution_id"),
                _json_string(helper, "actual_client_order_id"),
            )
            for helper in _json_mappings(item, "helpers_by_execution_id")
        )
        groups.append(
            Pyo3RecoveryCoverageGroup(
                logical_client_order_id=_json_string(item, "logical_client_order_id"),
                intent_id=_json_string(item, "intent_id"),
                role=OrderRole(_json_string(item, "role")),
                side=Side(_json_string(item, "side")),
                trigger_price=_json_decimal(item, "trigger_price"),
                reference_quantity=_json_decimal(item, "reference_quantity"),
                setup_id=_json_optional_string(item, "setup_id"),
                helpers_by_execution_id=helpers,
                submitted_published=_json_bool(item, "submitted_published"),
                accepted_published=_json_bool(item, "accepted_published"),
                terminal_published=_json_bool(item, "terminal_published"),
                cumulative_filled_quantity=_json_decimal(item, "cumulative_filled_quantity"),
            )
        )
    last_reason = document["last_close_reason"]
    if last_reason is not None and not isinstance(last_reason, str):
        raise Pyo3SmokeProfileError("recovery last_close_reason must be a string")
    return Pyo3RecoveryCheckpoint(
        strategy_id=_json_string(document, "strategy_id"),
        instrument_id=_json_string(document, "instrument_id"),
        source_sequence=_json_int(document, "source_sequence"),
        active_setup_id=_json_optional_string(document, "active_setup_id"),
        native_commissions=_json_decimal(document, "native_commissions"),
        native_funding=_json_decimal(document, "native_funding"),
        native_slippage_cost=_json_decimal(document, "native_slippage_cost"),
        closing_execution_ids=_json_strings(document, "closing_execution_ids"),
        last_close_reason=None if last_reason is None else CloseReason(last_reason),
        exposure_fills=exposures,
        bindings=bindings,
        coverage_groups=tuple(groups),
        submitted_client_order_ids=_json_strings(document, "submitted_client_order_ids"),
        scheduled_market_intent_ids=_json_strings(document, "scheduled_market_intent_ids"),
        seen_adjustment_event_ids=_json_strings(document, "seen_adjustment_event_ids"),
        seen_funding_settlement_ids=_json_strings(document, "seen_funding_settlement_ids"),
        unresolved_funding_settlement_ids=_json_strings(
            document, "unresolved_funding_settlement_ids"
        ),
        seen_native_lifecycle_event_ids=_json_strings(document, "seen_native_lifecycle_event_ids"),
        seen_native_position_fingerprints=_json_strings(
            document, "seen_native_position_fingerprints"
        ),
        terminal_logical_order_ids=_json_strings(document, "terminal_logical_order_ids"),
        awaiting_flat_reconciliation=_json_bool(document, "awaiting_flat_reconciliation"),
        last_delivered_bar_close_ns=_json_optional_int(document, "last_delivered_bar_close_ns"),
        last_published_equity_close_ns=_json_optional_int(
            document, "last_published_equity_close_ns"
        ),
        schema_version=_json_string(document, "schema_version"),
        execution_profile=_json_string(document, "execution_profile"),
    )


def _empty_recovery_checkpoint() -> Pyo3RecoveryCheckpoint:
    return Pyo3RecoveryCheckpoint(
        strategy_id="scope", instrument_id="instrument", source_sequence=0
    )


def _recovery_decimal(value: Decimal) -> str:
    return "0" if value == ZERO else format(value.normalize(), "f")


def _canonical_json_bytes(value: object) -> bytes:
    return _canonical_json_text(value).encode("utf-8")


def _canonical_json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _json_mappings(values: dict[str, object], key: str) -> list[dict[str, object]]:
    value = values.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise Pyo3SmokeProfileError(f"recovery {key} must be a list of objects")
    return cast(list[dict[str, object]], value)


def _json_string(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise Pyo3SmokeProfileError(f"recovery {key} must be a non-empty string")
    return value


def _json_optional_string(values: dict[str, object], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise Pyo3SmokeProfileError(f"recovery {key} must be null or a string")
    return value


def _json_decimal(values: dict[str, object], key: str) -> Decimal:
    value = values.get(key)
    if not isinstance(value, str):
        raise Pyo3SmokeProfileError(f"recovery {key} must be a decimal string")
    try:
        decimal = Decimal(value)
    except Exception as exc:
        raise Pyo3SmokeProfileError(f"recovery {key} is not a Decimal") from exc
    if not decimal.is_finite():
        raise Pyo3SmokeProfileError(f"recovery {key} must be finite")
    return decimal


def _json_bool(values: dict[str, object], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise Pyo3SmokeProfileError(f"recovery {key} must be bool")
    return value


def _json_int(values: dict[str, object], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise Pyo3SmokeProfileError(f"recovery {key} must be int")
    return value


def _json_optional_int(values: dict[str, object], key: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise Pyo3SmokeProfileError(f"recovery {key} must be null or int")
    return value


def _json_strings(values: dict[str, object], key: str) -> tuple[str, ...]:
    value = values.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise Pyo3SmokeProfileError(f"recovery {key} must be a list of strings")
    return tuple(cast(list[str], value))
