"""Typed, engine-independent domain protocol for Mastermind v2.

Only Python's standard library is used here.  In particular, prices, quantities and
money never cross the domain boundary as binary floats.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

SCHEMA_VERSION = "mms_state/1"
STRATEGY_VERSION = "mms_v2_h1_bb/1"
# A small exact window handles near-term redelivery without making every serialized
# transition grow with process lifetime.  Older transport replay is still rejected by
# per-source sequence high-water marks; fills and funding settlements have separate,
# globally exact identifier sets.
RECENT_EVENT_ID_LIMIT = 256
ZERO = Decimal(0)
INITIAL_STATE_TIME = datetime(1970, 1, 1, tzinfo=UTC)


class RiskMode(StrEnum):
    FULL = "FULL"
    SCOUT = "SCOUT"


class PositionBuild(StrEnum):
    FLAT = "FLAT"
    BASE = "BASE"
    PYRAMIDED = "PYRAMIDED"
    BASE_LOCKED = "BASE_LOCKED"


class OrderLifecycle(StrEnum):
    NONE = "NONE"
    BASE_PENDING = "BASE_PENDING"
    ADDON_PENDING = "ADDON_PENDING"
    REDUCE_PENDING = "REDUCE_PENDING"
    EXIT_PENDING = "EXIT_PENDING"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> Decimal:
        return Decimal(1) if self is Side.LONG else Decimal(-1)

    @property
    def exit_side(self) -> Side:
        return Side.SHORT if self is Side.LONG else Side.LONG


class AddonTriggerPolicy(StrEnum):
    CONFIRMING_CANDLE = "CONFIRMING_CANDLE"
    STOCH_CROSS = "STOCH_CROSS"
    FIRST_OF_CANDLE_OR_STOCH = "FIRST_OF_CANDLE_OR_STOCH"
    CANDLE_AND_STOCH = "CANDLE_AND_STOCH"


class TriggerKind(StrEnum):
    CONFIRMING_CANDLE = "CONFIRMING_CANDLE"
    STOCH_CROSS = "STOCH_CROSS"
    CANDLE_AND_STOCH = "CANDLE_AND_STOCH"


class CloseReason(StrEnum):
    TP = "TP"
    BASE_SL = "BASE_SL"
    RISK_LIMIT = "RISK_LIMIT"
    MANUAL = "MANUAL"
    LIQUIDATION = "LIQUIDATION"
    ENGINE_ERROR = "ENGINE_ERROR"


class OrderRole(StrEnum):
    BASE_ENTRY = "BASE_ENTRY"
    ADDON_ENTRY = "ADDON_ENTRY"
    BASE_STOP = "BASE_STOP"
    ADDON_STOP = "ADDON_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    CLOSE_ALL = "CLOSE_ALL"
    REDUCE_ADDON = "REDUCE_ADDON"

    @property
    def is_protective(self) -> bool:
        return self in {
            OrderRole.BASE_STOP,
            OrderRole.ADDON_STOP,
            OrderRole.TAKE_PROFIT,
        }

    @property
    def increases_exposure(self) -> bool:
        return self in {OrderRole.BASE_ENTRY, OrderRole.ADDON_ENTRY}


class OrderStatus(StrEnum):
    INTENDED = "INTENDED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def terminal(self) -> bool:
        return self in {
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELED,
            OrderStatus.TIMED_OUT,
        }

    @property
    def active(self) -> bool:
        return self in {
            OrderStatus.INTENDED,
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        }


class IntentKind(StrEnum):
    SUBMIT_BASE_ORDER = "SubmitBaseOrder"
    SUBMIT_ADDON_ORDER = "SubmitAddonOrder"
    SUBMIT_BASE_STOP = "SubmitBaseStop"
    SUBMIT_ADDON_STOP = "SubmitAddonStop"
    SUBMIT_TAKE_PROFIT = "SubmitTakeProfit"
    CLOSE_ALL = "CloseAll"
    REDUCE_ADDON = "ReduceAddon"
    CANCEL_ORDER = "CancelOrder"
    REPLACE_ORDER = "ReplaceOrder"
    REQUEST_RECONCILIATION = "RequestReconciliation"
    PERSIST_SNAPSHOT = "PersistSnapshot"


def _require_decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class MastermindConfig:
    """Frozen Beta configuration; its hash is persisted with every state."""

    strategy_id: str
    instrument_id: str
    addon_trigger_policy: AddonTriggerPolicy
    addon_enabled: bool = True
    sequential_enabled: bool = True
    timeframe: str = "1h"
    marking_timeframe: str | None = None
    bb_window: int = 20
    bb_num_std: Decimal = Decimal("2")
    arm_expiry_bars: int = 2
    require_reclaim: bool = False
    base_exposure_full: Decimal = Decimal("1")
    base_exposure_scout: Decimal = Decimal("0.1")
    base_sl_pct: Decimal = Decimal("0.02")
    addon_max_sl_pct: Decimal = Decimal("0.01")
    stoch_oversold: Decimal = Decimal("20")
    stoch_overbought: Decimal = Decimal("80")
    quantity_step: Decimal = Decimal("0.001")
    min_quantity: Decimal = Decimal("0.001")
    min_notional: Decimal = Decimal("5")
    strategy_version: str = STRATEGY_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.strategy_id, str)
            or not self.strategy_id
            or not isinstance(self.instrument_id, str)
            or not self.instrument_id
        ):
            raise ValueError("strategy_id and instrument_id must be non-empty strings")
        if not isinstance(self.addon_enabled, bool) or not isinstance(
            self.sequential_enabled, bool
        ):
            raise TypeError("ablation flags must be bool")
        if not isinstance(self.addon_trigger_policy, AddonTriggerPolicy):
            raise TypeError("addon_trigger_policy must be AddonTriggerPolicy")
        if self.timeframe != "1h":
            raise ValueError("mms v2 Beta accepts only timeframe='1h'")
        if self.marking_timeframe not in (None, "5m", "10m"):
            raise ValueError("marking_timeframe musi być None, '5m' albo '10m'")
        if (
            isinstance(self.bb_window, bool)
            or not isinstance(self.bb_window, int)
            or isinstance(self.arm_expiry_bars, bool)
            or not isinstance(self.arm_expiry_bars, int)
        ):
            raise TypeError("bb_window and arm_expiry_bars must be integers")
        if self.bb_window < 2 or self.arm_expiry_bars < 1:
            raise ValueError("bb_window must be >=2 and arm_expiry_bars >=1")
        for name in (
            "bb_num_std",
            "base_exposure_full",
            "base_exposure_scout",
            "base_sl_pct",
            "addon_max_sl_pct",
            "quantity_step",
            "min_quantity",
            "min_notional",
        ):
            _require_decimal(getattr(self, name), name, positive=True)
        _require_decimal(self.stoch_oversold, "stoch_oversold")
        _require_decimal(self.stoch_overbought, "stoch_overbought")
        if self.base_exposure_full != Decimal("1"):
            raise ValueError("FULL exposure is frozen at x1")
        if self.base_exposure_scout != Decimal("0.1"):
            raise ValueError("SCOUT exposure is frozen at x0.1")
        if self.base_sl_pct != Decimal("0.02"):
            raise ValueError("base stop is frozen at 2%")
        if self.addon_max_sl_pct != Decimal("0.01"):
            raise ValueError("add-on maximum stop distance is frozen at 1%")
        if not (ZERO <= self.stoch_oversold < self.stoch_overbought <= Decimal("100")):
            raise ValueError("Stochastic thresholds must satisfy 0 <= low < high <= 100")
        if self.strategy_version != STRATEGY_VERSION:
            raise ValueError(f"unsupported strategy version {self.strategy_version!r}")

    @property
    def config_hash(self) -> str:
        data = {
            "addon_enabled": self.addon_enabled,
            "addon_trigger_policy": self.addon_trigger_policy.value,
            "addon_max_sl_pct": str(self.addon_max_sl_pct),
            "arm_expiry_bars": self.arm_expiry_bars,
            "base_exposure_full": str(self.base_exposure_full),
            "base_exposure_scout": str(self.base_exposure_scout),
            "base_sl_pct": str(self.base_sl_pct),
            "bb_num_std": str(self.bb_num_std),
            "bb_window": self.bb_window,
            "instrument_id": self.instrument_id,
            "min_notional": str(self.min_notional),
            "min_quantity": str(self.min_quantity),
            "quantity_step": str(self.quantity_step),
            "require_reclaim": self.require_reclaim,
            "stoch_overbought": str(self.stoch_overbought),
            "stoch_oversold": str(self.stoch_oversold),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "sequential_enabled": self.sequential_enabled,
            "timeframe": self.timeframe,
        }
        # Brak klucza zachowuje dokładny config hash zamrożonych runów P9.
        if self.marking_timeframe is not None:
            data["marking_timeframe"] = self.marking_timeframe
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class EventEnvelope:
    event_id: str
    strategy_id: str
    instrument_id: str
    occurred_at_utc: datetime
    source: str
    source_sequence: int
    setup_id: str | None = None
    correlation_id: str | None = None
    client_order_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountEquityUpdated(EventEnvelope):
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BarSnapshot:
    bar_id: str
    open_time_utc: datetime
    close_time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    bb_upper: Decimal
    bb_lower: Decimal
    stoch_k: Decimal | None
    stoch_d: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BarClosed(EventEnvelope):
    bar_id: str
    open_time_utc: datetime
    close_time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    bb_upper: Decimal
    bb_lower: Decimal
    stoch_k: Decimal | None
    stoch_d: Decimal | None
    previous_stoch_k: Decimal | None = None
    previous_stoch_d: Decimal | None = None
    is_final: bool = True

    def snapshot(self) -> BarSnapshot:
        return BarSnapshot(
            bar_id=self.bar_id,
            open_time_utc=self.open_time_utc,
            close_time_utc=self.close_time_utc,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            bb_upper=self.bb_upper,
            bb_lower=self.bb_lower,
            stoch_k=self.stoch_k,
            stoch_d=self.stoch_d,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkingBarClosed(EventEnvelope):
    """Finalny M5/M10 bar używany wyłącznie do uzbrojenia H1 execution."""

    bar_id: str
    timeframe: str
    open_time_utc: datetime
    close_time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_final: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderSubmitted(EventEnvelope):
    intent_id: str
    role: OrderRole
    requested_quantity: Decimal
    side: Side
    reduce_only: bool
    close_position: bool
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderAccepted(EventEnvelope):
    role: OrderRole
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRejected(EventEnvelope):
    role: OrderRole
    reason: str
    cumulative_filled_quantity: Decimal = ZERO


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderCanceled(EventEnvelope):
    role: OrderRole
    reason: str
    cumulative_filled_quantity: Decimal = ZERO


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderTimedOut(EventEnvelope):
    role: OrderRole
    deadline_at_utc: datetime
    observed_status: str
    cumulative_filled_quantity: Decimal = ZERO


@dataclass(frozen=True, slots=True, kw_only=True)
class FillEnvelope(EventEnvelope):
    execution_id: str
    role: OrderRole
    last_quantity: Decimal
    cumulative_quantity: Decimal
    price: Decimal
    commission: Decimal = ZERO
    benchmark_price: Decimal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderPartiallyFilled(FillEnvelope):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderFilled(FillEnvelope):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionChanged(EventEnvelope):
    signed_quantity: Decimal
    average_price: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionClosed(EventEnvelope):
    close_reason: CloseReason
    realized_price_pnl: Decimal
    commissions: Decimal
    funding: Decimal
    realized_slippage_cost: Decimal
    closing_execution_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class FundingApplied(EventEnvelope):
    settlement_id: str
    amount: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskLimitTriggered(EventEnvelope):
    limit_id: str
    observed_equity: Decimal
    observed_exposure: Decimal
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CloseRequested(EventEnvelope):
    close_reason: CloseReason
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoverySnapshotLoaded(EventEnvelope):
    schema_version: str
    checksum: str
    snapshot_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciledOrder:
    """Venue order truth carried by a reconciliation response when available."""

    client_order_id: str
    venue_order_id: str | None
    role: OrderRole
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    side: Side
    reduce_only: bool
    close_position: bool
    setup_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciliationCompleted(EventEnvelope):
    signed_open_quantity: Decimal
    average_price: Decimal | None
    open_client_order_ids: tuple[str, ...]
    as_of_sequence: int
    open_orders: tuple[ReconciledOrder, ...] = ()
    acknowledged_intent_ids: tuple[str, ...] = ()


type DomainEvent = (
    AccountEquityUpdated
    | BarClosed
    | MarkingBarClosed
    | OrderSubmitted
    | OrderAccepted
    | OrderRejected
    | OrderCanceled
    | OrderTimedOut
    | OrderPartiallyFilled
    | OrderFilled
    | PositionChanged
    | PositionClosed
    | FundingApplied
    | RiskLimitTriggered
    | CloseRequested
    | RecoverySnapshotLoaded
    | ReconciliationCompleted
)


@dataclass(frozen=True, slots=True, kw_only=True)
class IntentEnvelope:
    intent_id: str
    idempotency_key: str
    strategy_id: str
    instrument_id: str
    setup_id: str | None
    causation_id: str
    correlation_id: str
    reconciliation_policy: str = "QUERY_BY_CLIENT_ID"

    @property
    def kind(self) -> IntentKind:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitBaseOrder(IntentEnvelope):
    client_order_id: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    target_notional: Decimal

    @property
    def kind(self) -> IntentKind:
        return IntentKind.SUBMIT_BASE_ORDER


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitAddonOrder(IntentEnvelope):
    client_order_id: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    target_notional: Decimal
    trigger_id: str
    trigger_kind: TriggerKind
    structural_stop: Decimal

    @property
    def kind(self) -> IntentKind:
        return IntentKind.SUBMIT_ADDON_ORDER


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitBaseStop(IntentEnvelope):
    client_order_id: str
    side: Side
    reference_quantity: Decimal
    trigger_price: Decimal
    close_position: bool = True
    reduce_only: bool = False

    @property
    def kind(self) -> IntentKind:
        return IntentKind.SUBMIT_BASE_STOP


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitAddonStop(IntentEnvelope):
    client_order_id: str
    side: Side
    quantity: Decimal
    trigger_price: Decimal
    fill_execution_id: str
    close_position: bool = False
    reduce_only: bool = True

    @property
    def kind(self) -> IntentKind:
        return IntentKind.SUBMIT_ADDON_STOP


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitTakeProfit(IntentEnvelope):
    client_order_id: str
    side: Side
    reference_quantity: Decimal
    trigger_price: Decimal
    close_position: bool = True
    reduce_only: bool = False

    @property
    def kind(self) -> IntentKind:
        return IntentKind.SUBMIT_TAKE_PROFIT


@dataclass(frozen=True, slots=True, kw_only=True)
class CloseAll(IntentEnvelope):
    client_order_id: str
    side: Side
    quantity: Decimal
    close_reason: CloseReason

    @property
    def kind(self) -> IntentKind:
        return IntentKind.CLOSE_ALL


@dataclass(frozen=True, slots=True, kw_only=True)
class ReduceAddon(IntentEnvelope):
    client_order_id: str
    side: Side
    quantity: Decimal
    reason: str

    @property
    def kind(self) -> IntentKind:
        return IntentKind.REDUCE_ADDON


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelOrder(IntentEnvelope):
    target_client_order_id: str
    reason: str

    @property
    def kind(self) -> IntentKind:
        return IntentKind.CANCEL_ORDER


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplaceOrder(IntentEnvelope):
    previous_client_order_id: str
    client_order_id: str
    role: OrderRole
    side: Side
    quantity: Decimal
    trigger_price: Decimal
    close_position: bool
    reduce_only: bool

    @property
    def kind(self) -> IntentKind:
        return IntentKind.REPLACE_ORDER


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestReconciliation(IntentEnvelope):
    reason: str

    @property
    def kind(self) -> IntentKind:
        return IntentKind.REQUEST_RECONCILIATION


@dataclass(frozen=True, slots=True, kw_only=True)
class PersistSnapshot(IntentEnvelope):
    snapshot_id: str

    @property
    def kind(self) -> IntentKind:
        return IntentKind.PERSIST_SNAPSHOT


type DomainIntent = (
    SubmitBaseOrder
    | SubmitAddonOrder
    | SubmitBaseStop
    | SubmitAddonStop
    | SubmitTakeProfit
    | CloseAll
    | ReduceAddon
    | CancelOrder
    | ReplaceOrder
    | RequestReconciliation
    | PersistSnapshot
)

type ExternalIntent = (
    SubmitBaseOrder
    | SubmitAddonOrder
    | SubmitBaseStop
    | SubmitAddonStop
    | SubmitTakeProfit
    | CloseAll
    | ReduceAddon
    | CancelOrder
    | ReplaceOrder
    | RequestReconciliation
)


@dataclass(slots=True)
class SignalMemory:
    armed_side: Side | None = None
    armed_bars_remaining: int = 0
    touch_bar_id: str | None = None
    reaction_bar: BarSnapshot | None = None
    recent_bars: list[BarSnapshot] = field(default_factory=list)
    confirming_candle_checked: bool = False
    seen_trigger_ids: set[str] = field(default_factory=set)
    last_marking_close_time_utc: datetime | None = None
    marking_bars_in_phase: int = 0


@dataclass(slots=True)
class VirtualLeg:
    quantity: Decimal = ZERO
    fill_vwap: Decimal | None = None
    realized_price_pnl: Decimal = ZERO
    fill_execution_ids: set[str] = field(default_factory=set)
    fill_execution_order: list[str] = field(default_factory=list)
    fill_quantities: dict[str, Decimal] = field(default_factory=dict)
    remaining_fill_quantities: dict[str, Decimal] = field(default_factory=dict)
    reduced_quantity: Decimal = ZERO
    stop_level: Decimal | None = None


@dataclass(slots=True)
class SetupState:
    setup_id: str
    side: Side
    reaction_bar: BarSnapshot
    setup_start_equity: Decimal
    exposure_multiplier: Decimal
    base_target_notional: Decimal
    addon_target_notional: Decimal
    base_requested_quantity: Decimal
    addon_requested_quantity: Decimal = ZERO
    addon_trigger_id: str | None = None
    addon_trigger_kind: TriggerKind | None = None
    addon_structural_stop: Decimal | None = None
    addon_opportunity_consumed: bool = False
    add_on_lock: bool = False
    pending_close_reason: CloseReason | None = None
    final_close_reason: CloseReason | None = None
    current_tp: Decimal | None = None
    tp_client_order_id: str | None = None
    base_stop_client_order_id: str | None = None
    actual_entry_notional: Decimal = ZERO
    realized_notional_drift: Decimal = ZERO
    closing_execution_ids: list[str] = field(default_factory=list)
    finalization_fingerprint: str | None = None


@dataclass(slots=True)
class OrderRecord:
    role: OrderRole
    intent_id: str
    correlation_id: str
    client_order_id: str
    venue_order_id: str | None
    requested_quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus
    side: Side
    reduce_only: bool
    close_position: bool
    trigger_price: Decimal | None = None
    deadline_at_utc: datetime | None = None
    replacement_of: str | None = None
    setup_id: str | None = None
    protected_execution_id: str | None = None

    @property
    def remaining_quantity(self) -> Decimal:
        return max(ZERO, self.requested_quantity - self.filled_quantity)


@dataclass(slots=True)
class PnlLedger:
    base_realized_price_pnl: Decimal = ZERO
    addon_realized_price_pnl: Decimal = ZERO
    commissions: Decimal = ZERO
    funding: Decimal = ZERO
    realized_slippage_cost: Decimal = ZERO
    addon_stop_realized_pnl: Decimal = ZERO
    funding_settlement_ids: set[str] = field(default_factory=set)

    @property
    def realized_price_pnl(self) -> Decimal:
        return self.base_realized_price_pnl + self.addon_realized_price_pnl

    @property
    def setup_net_pnl(self) -> Decimal:
        return (
            self.realized_price_pnl - self.commissions + self.funding - self.realized_slippage_cost
        )


@dataclass(frozen=True, slots=True)
class RecoveryEntryFill:
    """Read-only entry-fill provenance needed to rebuild native protection."""

    execution_id: str
    role: OrderRole
    original_quantity: Decimal
    remaining_quantity: Decimal
    side: Side


@dataclass(frozen=True, slots=True)
class RecoveryOrder:
    """Immutable order-ledger row exposed across the pure adapter boundary."""

    role: OrderRole
    intent_id: str
    correlation_id: str
    client_order_id: str
    venue_order_id: str | None
    requested_quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus
    side: Side
    reduce_only: bool
    close_position: bool
    trigger_price: Decimal | None
    deadline_at_utc: datetime | None
    replacement_of: str | None
    setup_id: str | None
    protected_execution_id: str | None


@dataclass(frozen=True, slots=True)
class RecoveryView:
    """Engine-independent checkpoint view for a thin adapter restart."""

    active_setup_id: str | None
    setup_side: Side | None
    pending_close_reason: CloseReason | None
    final_close_reason: CloseReason | None
    base_realized_price_pnl: Decimal
    addon_realized_price_pnl: Decimal
    commissions: Decimal
    funding: Decimal
    realized_slippage_cost: Decimal
    addon_stop_realized_pnl: Decimal
    funding_settlement_ids: tuple[str, ...]
    unresolved_funding_settlement_ids: tuple[str, ...]
    closing_execution_ids: tuple[str, ...]
    entry_fills: tuple[RecoveryEntryFill, ...]
    orders: tuple[RecoveryOrder, ...]
    outbox: tuple[ExternalIntent, ...]


@dataclass(slots=True)
class MachineState:
    strategy_id: str
    instrument_id: str
    config_hash: str
    risk_mode: RiskMode
    order_lifecycle: OrderLifecycle = OrderLifecycle.NONE
    signal: SignalMemory = field(default_factory=SignalMemory)
    latest_confirmed_equity: Decimal | None = None
    setup: SetupState | None = None
    base_leg: VirtualLeg = field(default_factory=VirtualLeg)
    addon_leg: VirtualLeg = field(default_factory=VirtualLeg)
    real_open_quantity: Decimal = ZERO
    real_average_price: Decimal | None = None
    orders: dict[str, OrderRecord] = field(default_factory=dict)
    pnl: PnlLedger = field(default_factory=PnlLedger)
    # Bounded transport-event cache.  Insertion order is significant: old IDs are
    # evicted after ``RECENT_EVENT_ID_LIMIT`` entries, while durable per-source high
    # watermarks reject their later replay.  The compact 256-entry window is for
    # near-term redelivery only; execution and funding IDs remain globally exact.
    processed_event_ids: dict[str, None] = field(default_factory=dict)
    processed_execution_ids: set[str] = field(default_factory=set)
    emitted_intent_keys: set[str] = field(default_factory=set)
    last_source_sequences: dict[str, int] = field(default_factory=dict)
    outbox: list[ExternalIntent] = field(default_factory=list)
    recovery_mode: bool = False
    unresolved_funding_settlement_ids: set[str] = field(default_factory=set)
    observed_drift_signed_quantity: Decimal | None = None
    last_reconciliation_sequence: int | None = None
    diagnostics: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    telemetry: dict[str, Decimal] = field(default_factory=dict)
    invariant_violation_count: int = 0
    snapshot_id: str = "initial"
    created_at_utc: datetime = INITIAL_STATE_TIME
    # Runtime attestation set only by ``deserialize_state`` after checksum
    # verification.  It is deliberately excluded from the serialized body to avoid
    # a self-referential checksum.
    verified_snapshot_checksum: str | None = None

    @property
    def position_build(self) -> PositionBuild:
        if self.base_leg.quantity == ZERO and self.addon_leg.quantity == ZERO:
            return PositionBuild.FLAT
        if self.addon_leg.quantity > ZERO:
            return PositionBuild.PYRAMIDED
        if self.setup is not None and self.setup.add_on_lock:
            return PositionBuild.BASE_LOCKED
        return PositionBuild.BASE

    @property
    def total_logical_quantity(self) -> Decimal:
        return self.base_leg.quantity + self.addon_leg.quantity


def validate_event_envelope(event: EventEnvelope) -> None:
    required_ids = {
        "event_id": event.event_id,
        "strategy_id": event.strategy_id,
        "instrument_id": event.instrument_id,
        "source": event.source,
    }
    if any(not isinstance(value, str) or not value for value in required_ids.values()):
        raise ValueError("event envelope identifiers must be non-empty strings")
    optional_ids = {
        "setup_id": event.setup_id,
        "correlation_id": event.correlation_id,
        "client_order_id": event.client_order_id,
        "causation_id": event.causation_id,
    }
    if any(
        value is not None and (not isinstance(value, str) or not value)
        for value in optional_ids.values()
    ):
        raise ValueError("optional event envelope identifiers must be non-empty strings")
    if isinstance(event.source_sequence, bool) or not isinstance(event.source_sequence, int):
        raise TypeError("source_sequence must be an integer")
    if event.source_sequence < 0:
        raise ValueError("source_sequence must be non-negative")
    _utc(event.occurred_at_utc, "occurred_at_utc")


def validate_identifier(
    value: object,
    name: str,
    *,
    optional: bool = False,
) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def validate_decimal_event(value: Decimal, name: str, *, positive: bool = False) -> None:
    _require_decimal(value, name, positive=positive)


def validate_utc(value: datetime, name: str) -> None:
    _utc(value, name)
