"""P4 order-safety PoC for MMS virtual legs over a NETTING position.

This module is deliberately narrower than the future Mastermind state machine.  It
does not model prices, signals, latency, matching, PnL, or persistence.  It only
models the quantities and protective-order instructions needed to prove that the
selected Binance/Nautilus OMS mapping cannot over-reduce or reverse a net position.

The companion tests also run the pinned Nautilus ``BacktestEngine`` to compare
strategy OMS ``NETTING`` and ``HEDGING`` over a venue ``NETTING`` position, and call
the pinned Binance adapter's real validation and wire-encoding methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

SELECTED_OMS_MODEL = "OMS-A_NETTING_VIRTUAL_LEGS_V1"
REJECTED_OMS_MODEL = "OMS-B_HEDGING_VIRTUAL_POSITIONS"
ADDON_PARTIAL_STOP_POLICY = "INCREMENTAL_REDUCE_ONLY_PER_FILL_V1"


class ProtectiveRole(StrEnum):
    """The two protective roles characterized by the P4 PoC."""

    BASE_CLOSE_POSITION = "BASE_CLOSE_POSITION"
    ADDON_REDUCE_ONLY = "ADDON_REDUCE_ONLY"


@dataclass(frozen=True)
class ProtectiveOrderSpec:
    """Venue instructions for one protective conditional order."""

    client_order_id: str
    role: ProtectiveRole
    quantity: Decimal
    reduce_only: bool
    close_position: bool


@dataclass(frozen=True)
class ProtectionSnapshot:
    """Inspectable result of one order-safety transition.

    This is not the versioned strategy recovery snapshot required by P6.
    """

    base_quantity: Decimal
    addon_quantity: Decimal
    active_orders: tuple[ProtectiveOrderSpec, ...]
    canceled_order_ids: tuple[str, ...]
    seen_execution_ids: tuple[str, ...]
    next_addon_stop_sequence: int

    @property
    def net_quantity(self) -> Decimal:
        return self.base_quantity + self.addon_quantity

    @property
    def active_addon_stop_quantity(self) -> Decimal:
        return sum(
            (
                order.quantity
                for order in self.active_orders
                if order.role is ProtectiveRole.ADDON_REDUCE_ONLY
            ),
            start=Decimal(0),
        )


@dataclass(frozen=True)
class ReconciliationDiff:
    """Order-ID comparison after restoring a P4 checkpoint.

    P4 only classifies missing and orphan IDs.  Submission/cancel races and their
    retry policy belong to the P6 lifecycle state machine.
    """

    expected_active_order_ids: tuple[str, ...]
    missing_at_venue: tuple[str, ...]
    orphan_at_venue: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        """Return whether local and venue active-order identities agree."""

        return not self.missing_at_venue and not self.orphan_at_venue


@dataclass
class _OrderState:
    spec: ProtectiveOrderSpec
    active: bool = True


class NettingProtectionProbe:
    """Minimal deterministic quantity ledger for the selected OMS-A mapping.

    A base stop is a Binance ``closePosition`` conditional order.  Its local
    ``quantity`` is only a Nautilus risk-check reference; when it triggers, Binance
    resolves the actual current net quantity server-side.  Each unique partial
    add-on fill receives a separate incremental ``reduceOnly`` stop child.  The
    active child quantities therefore sum to the actual filled add-on quantity
    without an unsafe cancel/replace overlap.
    """

    BASE_STOP_ID = "BASE-STOP"

    def __init__(self, base_quantity: Decimal) -> None:
        self._require_positive(base_quantity, "base_quantity")
        self._base_quantity = base_quantity
        self._addon_quantity = Decimal(0)
        self._orders: dict[str, _OrderState] = {
            self.BASE_STOP_ID: _OrderState(
                ProtectiveOrderSpec(
                    client_order_id=self.BASE_STOP_ID,
                    role=ProtectiveRole.BASE_CLOSE_POSITION,
                    quantity=base_quantity,
                    reduce_only=False,
                    close_position=True,
                )
            )
        }
        self._seen_execution_ids: set[str] = set()
        self._canceled_order_ids: set[str] = set()
        self._next_addon_stop = 1
        self.assert_safe()

    @classmethod
    def restore(cls, snapshot: ProtectionSnapshot) -> NettingProtectionProbe:
        """Restore quantity, execution dedupe, and stable protective client IDs.

        This is a minimal P4 checkpoint proof, not the versioned/checksummed P6
        persistence format.  Keeping ``next_addon_stop_sequence`` and known
        execution IDs ensures a restart neither re-applies a fill nor mints a
        second client ID for already protected exposure.
        """

        if not isinstance(snapshot, ProtectionSnapshot):
            raise TypeError("snapshot must be ProtectionSnapshot")
        for value, field in (
            (snapshot.base_quantity, "base_quantity"),
            (snapshot.addon_quantity, "addon_quantity"),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{field} must be Decimal")
            if not value.is_finite() or value < 0:
                raise ValueError(f"{field} must be finite and non-negative")
        if snapshot.next_addon_stop_sequence < 1:
            raise ValueError("next_addon_stop_sequence must be positive")

        probe = cls.__new__(cls)
        probe._base_quantity = snapshot.base_quantity
        probe._addon_quantity = snapshot.addon_quantity
        probe._orders = {}
        for spec in snapshot.active_orders:
            cls._require_identifier(spec.client_order_id, "client_order_id")
            if spec.client_order_id in probe._orders:
                raise ValueError(f"duplicate active client order ID: {spec.client_order_id}")
            probe._orders[spec.client_order_id] = _OrderState(spec)

        probe._seen_execution_ids = set(snapshot.seen_execution_ids)
        for execution_id in probe._seen_execution_ids:
            cls._require_identifier(execution_id, "execution_id")
        probe._canceled_order_ids = set(snapshot.canceled_order_ids)
        for client_order_id in probe._canceled_order_ids:
            cls._require_identifier(client_order_id, "canceled_order_id")
        if probe._orders.keys() & probe._canceled_order_ids:
            raise ValueError("an order cannot be both active and canceled")
        probe._next_addon_stop = snapshot.next_addon_stop_sequence
        probe.assert_safe()
        return probe

    @property
    def net_quantity(self) -> Decimal:
        """Return current long net quantity in the probe."""

        return self._base_quantity + self._addon_quantity

    def apply_addon_fill(
        self,
        *,
        execution_id: str,
        quantity: Decimal,
    ) -> ProtectiveOrderSpec | None:
        """Apply one unique partial add-on fill and protect exactly that delta.

        A duplicate execution ID is idempotent and returns ``None``.
        """

        self._require_identifier(execution_id, "execution_id")
        self._require_positive(quantity, "quantity")
        if execution_id in self._seen_execution_ids:
            return None

        self._seen_execution_ids.add(execution_id)
        self._addon_quantity += quantity
        client_order_id = f"ADDON-STOP-{self._next_addon_stop}"
        if client_order_id in self._orders or client_order_id in self._canceled_order_ids:
            raise AssertionError("restored add-on stop sequence would reuse a client order ID")
        self._next_addon_stop += 1
        spec = ProtectiveOrderSpec(
            client_order_id=client_order_id,
            role=ProtectiveRole.ADDON_REDUCE_ONLY,
            quantity=quantity,
            reduce_only=True,
            close_position=False,
        )
        self._orders[client_order_id] = _OrderState(spec)
        self.assert_safe()
        return spec

    def trigger_stop(self, *, client_order_id: str, execution_id: str) -> Decimal:
        """Trigger one active stop under serialized venue execution semantics.

        The returned quantity is the actual reduction.  Triggering an already
        terminal/canceled order or replaying an execution ID is idempotent.
        """

        self._require_identifier(client_order_id, "client_order_id")
        self._require_identifier(execution_id, "execution_id")
        if execution_id in self._seen_execution_ids:
            return Decimal(0)

        try:
            order = self._orders[client_order_id]
        except KeyError as exc:
            raise ValueError(f"unknown protective order: {client_order_id}") from exc

        self._seen_execution_ids.add(execution_id)
        if not order.active:
            return Decimal(0)

        if order.spec.role is ProtectiveRole.BASE_CLOSE_POSITION:
            reduced = self.net_quantity
            self._base_quantity = Decimal(0)
            self._addon_quantity = Decimal(0)
            order.active = False
            self._cancel_all_active(exclude={client_order_id})
        else:
            # Aggregate active add-on children never exceed actual add-on quantity,
            # so this minimum is a safety assertion rather than target sizing.
            reduced = min(order.spec.quantity, self._addon_quantity, self.net_quantity)
            self._addon_quantity -= reduced
            order.active = False

        self.assert_safe()
        return reduced

    def close_by_take_profit(self, *, execution_id: str) -> Decimal:
        """Close the current net quantity and cancel every surviving stop."""

        self._require_identifier(execution_id, "execution_id")
        if execution_id in self._seen_execution_ids:
            return Decimal(0)

        self._seen_execution_ids.add(execution_id)
        reduced = self.net_quantity
        self._base_quantity = Decimal(0)
        self._addon_quantity = Decimal(0)
        self._cancel_all_active(exclude=set())
        self.assert_safe()
        return reduced

    def snapshot(self) -> ProtectionSnapshot:
        """Return a deterministic, restorable checkpoint for the PoC tests."""

        active_orders = tuple(
            state.spec for _, state in sorted(self._orders.items()) if state.active
        )
        return ProtectionSnapshot(
            base_quantity=self._base_quantity,
            addon_quantity=self._addon_quantity,
            active_orders=active_orders,
            canceled_order_ids=tuple(sorted(self._canceled_order_ids)),
            seen_execution_ids=tuple(sorted(self._seen_execution_ids)),
            next_addon_stop_sequence=self._next_addon_stop,
        )

    def reconcile_order_ids(self, venue_active_order_ids: tuple[str, ...]) -> ReconciliationDiff:
        """Compare restored active identities without causing venue side effects."""

        venue_ids: set[str] = set()
        for client_order_id in venue_active_order_ids:
            self._require_identifier(client_order_id, "venue_client_order_id")
            if client_order_id in venue_ids:
                raise ValueError(f"duplicate venue client order ID: {client_order_id}")
            venue_ids.add(client_order_id)

        expected_ids = {
            client_order_id for client_order_id, state in self._orders.items() if state.active
        }
        return ReconciliationDiff(
            expected_active_order_ids=tuple(sorted(expected_ids)),
            missing_at_venue=tuple(sorted(expected_ids - venue_ids)),
            orphan_at_venue=tuple(sorted(venue_ids - expected_ids)),
        )

    def assert_safe(self) -> None:
        """Raise if a P4 quantity/order invariant is violated."""

        if self._base_quantity < 0 or self._addon_quantity < 0:
            raise AssertionError("protective execution reversed a logical quantity")

        active = [state.spec for state in self._orders.values() if state.active]
        active_base = [
            order for order in active if order.role is ProtectiveRole.BASE_CLOSE_POSITION
        ]
        active_addon = [order for order in active if order.role is ProtectiveRole.ADDON_REDUCE_ONLY]

        if self.net_quantity == 0 and active:
            raise AssertionError("FLAT cannot retain active protective orders")
        if self.net_quantity > 0 and len(active_base) != 1:
            raise AssertionError("an exposed setup requires exactly one base closePosition stop")
        if any(order.reduce_only or not order.close_position for order in active_base):
            raise AssertionError("base stop must be closePosition without reduceOnly")
        if any(not order.reduce_only or order.close_position for order in active_addon):
            raise AssertionError("add-on stops must be reduceOnly without closePosition")

        addon_coverage = sum((order.quantity for order in active_addon), start=Decimal(0))
        if addon_coverage != self._addon_quantity:
            raise AssertionError("active add-on stop coverage must equal actual add-on quantity")
        if addon_coverage > self.net_quantity:
            raise AssertionError("protective quantity exceeds real open quantity")

    def _cancel_all_active(self, *, exclude: set[str]) -> None:
        for client_order_id, state in self._orders.items():
            if state.active and client_order_id not in exclude:
                state.active = False
                self._canceled_order_ids.add(client_order_id)

    @staticmethod
    def _require_positive(value: Decimal, field: str) -> None:
        if not isinstance(value, Decimal):
            raise TypeError(f"{field} must be Decimal")
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{field} must be finite and positive")

    @staticmethod
    def _require_identifier(value: str, field: str) -> None:
        if not value:
            raise ValueError(f"{field} must be non-empty")
