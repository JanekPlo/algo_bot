"""Natywne wykonanie Nautilus i składanie evidence dla MR-Session 4."""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Literal, cast

import pandas as pd
from nautilus_trader import __version__ as nautilus_version
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.backtest_result import (
    BACKTEST_RESULT_SCHEMA_VERSION,
    BacktestResult,
    CostComponent,
    CostModel,
    CostProvenance,
    FillMethod,
    JsonValue,
    MarginMethod,
    SourceTreeState,
    assess_eligibility,
    normalize_json,
)
from algo_bot.engine.mr_session4_contract import (
    PerformanceAssessment,
    Session4RunSpec,
    assess_performance,
    build_run_matrix,
)
from algo_bot.engine.mr_session4_data import (
    HOLDOUT_START,
    MILLISECOND_NS,
    Session4DataBundle,
)
from algo_bot.engine.nautilus_mastermind import (
    PYO3_RESEARCH_EXECUTION_PROFILE,
    Pyo3ResearchMetadata,
    Pyo3SmokeRun,
    run_pyo3_mastermind_smoke,
)
from algo_bot.metrics import summarize
from algo_bot.microstructure import LeveragedPosition, LiquidationEvent, liquidation_check
from algo_bot.strategies.mastermind.model import (
    STRATEGY_VERSION,
    AccountEquityUpdated,
    BarClosed,
    CloseReason,
    CloseRequested,
    DomainEvent,
    FundingApplied,
    MarkingBarClosed,
    OrderFilled,
    OrderLifecycle,
    OrderPartiallyFilled,
    OrderRole,
    PositionBuild,
    PositionChanged,
    PositionClosed,
    RiskMode,
    Side,
)
from algo_bot.strategies.mastermind.state_machine import MastermindStateMachine

EXECUTION_SCHEMA_VERSION = "mr_session4_execution/2"
EXECUTION_PROFILE_ID = "NAUTILUS_BYBIT_NATIVE_BAR_MMS_FULL_STACK_V2"
MARGIN_PROFILE_ID = "CAUSAL_H1_MARK_SETUP_EQUITY_EFFECTIVE_LEVERAGE_PROXY_V3"
METRIC_PROFILE_ID = "NATIVE_NET_H1_8760_FRACTION_UNITS_V1"
COST_PROFILE_ID = "BYBIT_FIXED_FEE_HISTORICAL_MARK_FUNDING_ONE_TICK_NATIVE_BAR_V4"
LIQUIDATION_ACCOUNTING_PROFILE_ID = "DETECT_FAIL_TECHNICAL_FLATTEN_NO_LP_SETTLEMENT_V1"
STARTING_BALANCE = Decimal("10000")
DEFAULT_NATIVE_LEVERAGE = Decimal("2")
USDT_COMMISSION_QUANTUM = Decimal("0.00000001")
USDT_FUNDING_QUANTUM = Decimal("0.00000001")
HOUR_NS = 3_600_000_000_000

SESSION4_INVARIANT_CODES = (
    "INVARIANT_VIOLATION_COUNT_ZERO",
    "FINAL_DOMAIN_POSITION_FLAT",
    "FINAL_DOMAIN_QUANTITY_ZERO",
    "FINAL_NATIVE_QUANTITY_ZERO",
    "FINAL_ORDER_LIFECYCLE_NONE",
    "FINAL_SETUP_NONE",
    "NO_ACTIVE_DOMAIN_ORDERS",
    "NO_ACTIVE_NATIVE_ORDERS",
    "FINAL_OUTBOX_EMPTY",
    "DEVELOPMENT_EXIT_EMITTED_ONCE",
    "MANUAL_CUTOFF_POLICY_MATCHES_LIQUIDATION",
    "MARK_PRICE_BAR_COVERAGE_COMPLETE",
    "MARKING_EVENT_COUNT_COMPLETE",
    "DOMAIN_BAR_CUTOFF_COUNT",
    "STREAMING_MARKERS_NOT_RETAINED",
    "FUNDING_SETTLEMENT_IDS_UNIQUE",
    "FUNDING_LEDGER_RECONCILED",
    "NO_UNALLOCATED_FUNDING",
    "NATIVE_FUNDING_SETTLEMENTS_COMPLETE",
    "NATIVE_COMMISSION_EVIDENCE_PRESENT",
    "COMMISSION_LEDGER_RECONCILED",
    "FUNDING_AMOUNT_LEDGER_RECONCILED",
    "ONE_TICK_SLIPPAGE_LEDGER_RECONCILED",
    "RAW_DOMAIN_FILL_IDS_UNIQUE",
    "TRANSITION_OBSERVER_COUNT_EXACT",
    "FINAL_SNAPSHOT_ROUNDTRIP",
    "NO_HOLDOUT_NATIVE_DATA",
    "STRATEGY_HOLDOUT_ROWS_READ_ZERO",
    "MARGIN_PROFILE_HAS_RISK_TIERS",
    "LIQUIDATION_EVENT_AT_MOST_ONE",
)

_DOMAIN_FILL_TYPES = (OrderPartiallyFilled, OrderFilled)
_TERMINAL_NATIVE_ORDER_STATUSES = {
    "CANCELED",
    "DENIED",
    "EXPIRED",
    "FILLED",
    "REJECTED",
    "RELEASED",
}


class Session4ExecutionError(RuntimeError):
    """Run nie może zostać wykonany zgodnie z zamrożonym profilem."""


class Session4InvariantError(Session4ExecutionError):
    """Natywne dowody lub końcowy stan naruszają twardy niezmiennik."""


@dataclass(slots=True)
class Session4Accumulator:
    """Stałopamięciowy observer; nie zachowuje snapshotu na każdy marker."""

    machine: MastermindStateMachine
    transition_count: int = 0
    event_counts: Counter[str] = field(default_factory=Counter)
    marking_digest: Any = field(default_factory=hashlib.sha256, repr=False)
    max_committed_target_quote: Decimal = Decimal(0)
    max_gross_realized_exposure_quote: Decimal = Decimal(0)
    max_committed_exposure_multiplier: Decimal = Decimal(0)
    max_actual_gross_exposure_multiplier: Decimal = Decimal(0)
    scout_episode_bars: list[int] = field(default_factory=list)
    _scout_current_bars: int | None = None
    _previous_risk_mode: RiskMode = RiskMode.FULL

    def observe(self, event: DomainEvent) -> None:
        """Zlicza/haduje zdarzenie już po zastosowaniu go przez maszynę."""

        self.transition_count += 1
        self.event_counts[type(event).__name__] += 1
        if isinstance(event, MarkingBarClosed):
            self.marking_digest.update(event.event_id.encode("utf-8"))
            self.marking_digest.update(b"\n")
        self._observe_exposure()
        self._observe_scout_episode(event)

    @property
    def marking_event_count(self) -> int:
        return self.event_counts[MarkingBarClosed.__name__]

    @property
    def domain_bar_count(self) -> int:
        return self.event_counts[BarClosed.__name__]

    @property
    def scout_right_censored(self) -> bool:
        return self._previous_risk_mode is RiskMode.SCOUT

    def finalized_scout_episodes(self) -> list[int]:
        result = list(self.scout_episode_bars)
        if self.scout_right_censored:
            result.append(0 if self._scout_current_bars is None else self._scout_current_bars)
        return result

    def _observe_exposure(self) -> None:
        setup = self.machine.state.setup
        if setup is None:
            return
        committed = setup.base_target_notional + setup.addon_target_notional
        actual = setup.actual_entry_notional
        equity = setup.setup_start_equity
        self.max_committed_target_quote = max(self.max_committed_target_quote, committed)
        self.max_gross_realized_exposure_quote = max(
            self.max_gross_realized_exposure_quote,
            actual,
        )
        if equity > 0:
            self.max_committed_exposure_multiplier = max(
                self.max_committed_exposure_multiplier,
                committed / equity,
            )
            self.max_actual_gross_exposure_multiplier = max(
                self.max_actual_gross_exposure_multiplier,
                actual / equity,
            )

    def _observe_scout_episode(self, event: DomainEvent) -> None:
        mode = self.machine.state.risk_mode
        if self._previous_risk_mode is RiskMode.FULL and mode is RiskMode.SCOUT:
            self._scout_current_bars = 0
        if isinstance(event, BarClosed) and mode is RiskMode.SCOUT:
            if self._scout_current_bars is None:
                self._scout_current_bars = 0
            self._scout_current_bars += 1
        if self._previous_risk_mode is RiskMode.SCOUT and mode is RiskMode.FULL:
            self.scout_episode_bars.append(
                0 if self._scout_current_bars is None else self._scout_current_bars
            )
            self._scout_current_bars = None
        self._previous_risk_mode = mode


@dataclass(slots=True)
class _MarginPositionSnapshot:
    setup_id: str
    side: Side
    quantity: Decimal
    average_price: Decimal
    setup_start_equity: Decimal


@dataclass(slots=True)
class CausalIsolatedMarginMonitor:
    """Sprawdza mark wick dla każdej ekspozycji nachodzącej na bieżący H1."""

    machine: MastermindStateMachine
    data: Session4DataBundle
    liquidation_events: list[LiquidationEvent] = field(default_factory=list)
    mark_bars_observed: int = 0
    positioned_mark_bars_checked: int = 0
    overlap_positions: list[_MarginPositionSnapshot] = field(default_factory=list)
    carried_position: _MarginPositionSnapshot | None = None
    liquidation_evidence: list[dict[str, JsonValue]] = field(default_factory=list)

    @property
    def liquidated(self) -> bool:
        return bool(self.liquidation_events)

    def observe_transition(self, event: DomainEvent) -> None:
        """Zapamiętuje także pozycję otwartą i zamkniętą wewnątrz jednego H1."""

        if not isinstance(event, (PositionChanged, PositionClosed)):
            return
        snapshot = self._current_position()
        if snapshot is not None:
            self.overlap_positions.append(snapshot)

    def before_bar(self, bar: Any) -> tuple[DomainEvent, ...]:
        close_ns = int(bar.ts_init)
        open_ns = close_ns - HOUR_NS + MILLISECOND_NS
        open_time = pd.Timestamp(open_ns, unit="ns", tz="UTC")
        try:
            mark_row = cast("pd.Series[Any]", self.data.mark_context.bars.loc[open_time])
        except KeyError as exc:
            raise Session4InvariantError(
                f"missing exact mark-price H1 bar at {open_time.isoformat()}"
            ) from exc
        self.mark_bars_observed += 1
        if self.liquidated:
            return ()

        current = self._current_position()
        candidates = _unique_margin_positions(
            (
                *((self.carried_position,) if self.carried_position is not None else ()),
                *self.overlap_positions,
                *((current,) if current is not None else ()),
            )
        )
        self.overlap_positions.clear()
        self.carried_position = current
        if not candidates:
            return ()
        self.positioned_mark_bars_checked += 1
        liquidation: LiquidationEvent | None = None
        liquidated_snapshot: _MarginPositionSnapshot | None = None
        for snapshot in candidates:
            gross_entry_notional = snapshot.quantity * snapshot.average_price
            effective_leverage = max(
                Decimal(1),
                gross_entry_notional / snapshot.setup_start_equity,
            )
            side: Literal["long", "short"] = "long" if snapshot.side is Side.LONG else "short"
            position = LeveragedPosition(
                position_id=snapshot.setup_id,
                side=side,
                quantity=float(snapshot.quantity),
                entry_price=float(snapshot.average_price),
                leverage=float(effective_leverage),
                extra_margin=0.0,
            )
            tier = self.data.mark_context.tier_for(float(gross_entry_notional))
            adverse_mark = float(
                str(mark_row["Low"] if snapshot.side is Side.LONG else mark_row["High"])
            )
            checked = liquidation_check(
                position,
                adverse_mark,
                tier.maintenance_margin_rate,
                maintenance_margin_deduction=tier.maintenance_margin_deduction,
                taker_fee_rate=self.data.mark_context.taker_fee_rate,
                observed_at=pd.Timestamp(close_ns, unit="ns", tz="UTC"),
                source=f"{self.data.mark_context.source}#{MARGIN_PROFILE_ID}",
            )
            if checked is not False:
                liquidation = checked
                liquidated_snapshot = snapshot
                break
        if liquidation is None or liquidated_snapshot is None:
            return ()
        self.liquidation_events.append(liquidation)
        self.liquidation_evidence.append(
            {
                "margin_profile_id": MARGIN_PROFILE_ID,
                "overlap_policy": "ANY_POSITION_EXPOSURE_OVERLAPPING_COMPLETED_H1",
                "setup_id": liquidated_snapshot.setup_id,
                "side": liquidated_snapshot.side.value,
                "quantity": str(liquidated_snapshot.quantity),
                "average_entry_price": str(liquidated_snapshot.average_price),
                "setup_start_equity": str(liquidated_snapshot.setup_start_equity),
                "gross_entry_notional": str(
                    liquidated_snapshot.quantity * liquidated_snapshot.average_price
                ),
                "effective_leverage": str(effective_leverage),
                "extra_margin": "0",
                "risk_tier_max_position_value": tier.max_position_value,
                "maintenance_margin_rate": tier.maintenance_margin_rate,
                "maintenance_margin_deduction": tier.maintenance_margin_deduction,
                "mark_bar_open_utc": open_time.isoformat(),
                "mark_bar_inclusive_close_utc": pd.Timestamp(
                    close_ns, unit="ns", tz="UTC"
                ).isoformat(),
                "mark_bar_ohlc": {
                    column: float(str(mark_row[column]))
                    for column in ("Open", "High", "Low", "Close")
                },
                "adverse_mark_field": ("Low" if liquidated_snapshot.side is Side.LONG else "High"),
                "adverse_mark": adverse_mark,
                "liquidation_price": liquidation.liquidation_price,
                "source": liquidation.source,
            }
        )
        if current is None or current.setup_id != liquidated_snapshot.setup_id:
            # Pozycja mogła otworzyć się i zamknąć wewnątrz H1. Wynik pozostaje
            # liquidation outcome, lecz nie wolno zamykać późniejszego setupu.
            return ()
        return (
            CloseRequested(
                event_id=f"mr-s4-liquidation:{current.setup_id}:{close_ns}",
                strategy_id=self.machine.config.strategy_id,
                instrument_id=self.machine.config.instrument_id,
                occurred_at_utc=_datetime_from_ns(close_ns),
                source="mr_session4.mark_price_margin",
                source_sequence=1,
                setup_id=current.setup_id,
                close_reason=CloseReason.LIQUIDATION,
                reason="causal isolated mark-price liquidation crossing",
            ),
        )

    def _current_position(self) -> _MarginPositionSnapshot | None:
        state = self.machine.state
        setup = state.setup
        quantity = state.real_open_quantity
        average_price = state.real_average_price
        if setup is None or quantity <= 0 or average_price is None:
            return None
        return _MarginPositionSnapshot(
            setup_id=setup.setup_id,
            side=setup.side,
            quantity=quantity,
            average_price=average_price,
            setup_start_equity=setup.setup_start_equity,
        )


@dataclass(slots=True)
class Session4BoundaryController:
    """Łączy liquidation kill-switch z deterministycznym końcem development."""

    machine: MastermindStateMachine
    margin: CausalIsolatedMarginMonitor
    cutoff_close_ns: int
    final_close_ns: int
    manual_cutoff_count: int = 0

    def before_bar(self, bar: Any) -> tuple[DomainEvent, ...]:
        margin_events = self.margin.before_bar(bar)
        if margin_events:
            return margin_events
        close_ns = int(bar.ts_init)
        if close_ns != self.cutoff_close_ns or self.margin.liquidated:
            return ()
        if self.manual_cutoff_count:
            raise Session4InvariantError("manual development cutoff emitted twice")
        self.manual_cutoff_count += 1
        return (
            CloseRequested(
                event_id=f"mr-s4-manual-cutoff:{close_ns}",
                strategy_id=self.machine.config.strategy_id,
                instrument_id=self.machine.config.instrument_id,
                occurred_at_utc=_datetime_from_ns(close_ns),
                source="mr_session4.development_cutoff",
                source_sequence=1,
                close_reason=CloseReason.MANUAL,
                reason="frozen development boundary",
            ),
        )

    def deliver_domain_bar(self, bar: Any) -> bool:
        return not self.margin.liquidated and int(bar.ts_init) < self.cutoff_close_ns


@dataclass(frozen=True, slots=True)
class Session4RunArtifact:
    """Kompletny wynik runu przed atomowym zapisem przez orchestrator."""

    run_spec: Session4RunSpec
    result: BacktestResult
    counters: Mapping[str, int]
    invariant_ledger: tuple[dict[str, JsonValue], ...]
    final_snapshot: str
    performance: PerformanceAssessment

    def summary(self) -> dict[str, JsonValue]:
        return {
            "schema_version": EXECUTION_SCHEMA_VERSION,
            "run_id": self.run_spec.run_id,
            "ordinal": self.run_spec.ordinal,
            "run_spec_hash": self.run_spec.run_spec_hash,
            "artifact_hash": self.result.artifact_hash(),
            "evidence_gate_passed": self.result.eligibility.status.value == "ELIGIBLE",
            "performance_gate": self.performance.as_dict(),
            "counters": dict(self.counters),
            "invariant_ledger": list(self.invariant_ledger),
            "final_snapshot_sha256": hashlib.sha256(
                self.final_snapshot.encode("utf-8")
            ).hexdigest(),
        }


def run_session4_spec(
    run_spec: Session4RunSpec,
    data: Session4DataBundle,
    *,
    source_tree: SourceTreeState,
) -> Session4RunArtifact:
    """Wykonuje jeden prerejestrowany run i failuje przed użyciem słabego evidence."""

    expected = {item.run_id: item for item in build_run_matrix()}.get(run_spec.run_id)
    if expected is None or expected.as_dict() != run_spec.as_dict():
        raise Session4ExecutionError("run spec is outside the frozen 528 matrix")
    if data.spec.symbol != run_spec.symbol or data.metadata.symbol != run_spec.symbol:
        raise Session4ExecutionError("symbol data differs from run spec")
    if data.metadata.holdout_rows_read != 0:
        raise Session4ExecutionError("strategy data loader touched holdout rows")
    if len(data.h1_bars) < 2:
        raise Session4ExecutionError("development run needs at least two H1 bars")
    h1_timestamps = tuple(int(bar.ts_init) for bar in data.h1_bars)
    if h1_timestamps != tuple(sorted(set(h1_timestamps))):
        raise Session4ExecutionError("H1 native timestamps are not unique and sorted")
    holdout_ns = _to_ns(HOLDOUT_START)
    if max(int(item.ts_init) for item in data.native_data) >= holdout_ns:
        raise Session4ExecutionError("native execution data reaches holdout")

    machine = MastermindStateMachine(run_spec.machine_config)
    accumulator = Session4Accumulator(machine)
    margin = CausalIsolatedMarginMonitor(machine, data)
    boundary = Session4BoundaryController(
        machine,
        margin,
        cutoff_close_ns=h1_timestamps[-2],
        final_close_ns=h1_timestamps[-1],
    )
    feature_source, feature_hash = data.feature_source(
        run_spec.parameter_set.bb_window,
        run_spec.parameter_set.bb_num_std,
    )
    marking_bar_type, marking_data, marking_interval_ns, _marking_hash = data.marking_data(
        run_spec.marking_timeframe
    )
    native_namespace = cast(Any, nt)
    fill_model = native_namespace.DefaultFillModel(
        prob_fill_on_limit=1.0,
        prob_slippage=1.0,
        random_seed=run_spec.seed,
    )

    def observe_transition(event: DomainEvent) -> None:
        accumulator.observe(event)
        margin.observe_transition(event)

    native_run = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=run_spec.machine_config.strategy_id,
        instrument=data.instrument,
        bar_type=data.h1_bar_type,
        data=data.native_data,
        feature_source=feature_source.as_p7_source(),
        marking_bar_type=marking_bar_type,
        marking_data=marking_data,
        marking_interval_ns=marking_interval_ns,
        starting_balance=STARTING_BALANCE,
        default_leverage=DEFAULT_NATIVE_LEVERAGE,
        fill_model=fill_model,
        before_bar_domain_events=boundary.before_bar,
        deliver_domain_bar=boundary.deliver_domain_bar,
        slippage_per_unit=data.spec.price_increment,
        serialize_transition_snapshots=False,
        transition_observer=observe_transition,
        retain_domain_event=_retain_session4_event,
        run_metadata=Pyo3ResearchMetadata(),
    )
    if not isinstance(native_run.metadata, Pyo3ResearchMetadata):
        raise Session4InvariantError("native wrapper did not retain research provenance")
    final_snapshot = machine.snapshot_json()
    machine.assert_invariants()
    frames = _build_result_frames(native_run.domain_events, native_run)
    fills = _unique_domain_fills(native_run.domain_events)
    _reconcile_native_fills(
        fills,
        frames["fills"],
        maker_fee=data.spec.maker_fee,
        taker_fee=data.spec.taker_fee,
    )
    counters = _derive_counters(machine, native_run.domain_events, accumulator)
    invariant_ledger = _build_invariant_ledger(
        machine=machine,
        native_run=native_run,
        accumulator=accumulator,
        margin=margin,
        boundary=boundary,
        data=data,
        fills=fills,
        final_snapshot=final_snapshot,
        marking_expected=len(marking_data),
    )
    failures = [str(item["code"]) for item in invariant_ledger if item["passed"] is not True]
    if failures:
        raise Session4InvariantError(f"run {run_spec.run_id} failed invariants: {failures}")

    cost_model = _research_cost_model(data)
    eligibility = assess_eligibility(cost_model)
    evidence_passed = eligibility.status.value == "ELIGIBLE"
    if not evidence_passed:
        raise Session4InvariantError(
            f"research evidence gate failed before metrics: {eligibility.reasons}"
        )
    metric_values = (
        _liquidation_metric_values()
        if margin.liquidation_events
        else _build_metric_values(frames["equity"], frames["trades"])
    )
    performance = assess_performance(
        metric_values,
        evidence_gate_passed=True,
        liquidation_event_count=len(margin.liquidation_events),
    )
    stats = _build_stats(
        run_spec=run_spec,
        machine=machine,
        frames=frames,
        fills=fills,
        accumulator=accumulator,
        margin=margin,
        boundary=boundary,
        counters=counters,
        invariant_ledger=invariant_ledger,
        final_snapshot=final_snapshot,
        metric_values=metric_values,
        performance=performance,
    )
    result = BacktestResult(
        schema_version=BACKTEST_RESULT_SCHEMA_VERSION,
        engine="nautilus_trader.core.nautilus_pyo3.BacktestEngine",
        engine_version=nautilus_version,
        strategy_version=STRATEGY_VERSION,
        source_tree=source_tree,
        stats=stats,
        equity=frames["equity"],
        trades=frames["trades"],
        orders=frames["orders"],
        fills=frames["fills"],
        positions=frames["positions"],
        funding=frames["funding"],
        data_hash=data.run_data_hash(
            feature_hash=feature_hash,
            marking_timeframe=run_spec.marking_timeframe,
        ),
        config_hash=run_spec.config_hash,
        random_seed=run_spec.seed,
        cost_model=cost_model,
        eligibility=eligibility,
        fill_method=FillMethod.NAUTILUS_NATIVE_BAR,
        margin_method=MarginMethod.MARK_PRICE_ISOLATED,
        mark_price_source=data.mark_context.source,
        liquidation_events=tuple(margin.liquidation_events),
    )
    result.assert_research_eligible()
    return Session4RunArtifact(
        run_spec=run_spec,
        result=result,
        counters=counters,
        invariant_ledger=invariant_ledger,
        final_snapshot=final_snapshot,
        performance=performance,
    )


def _retain_session4_event(event: DomainEvent) -> bool:
    return not isinstance(event, (BarClosed, MarkingBarClosed))


def _unique_margin_positions(
    positions: Sequence[_MarginPositionSnapshot],
) -> tuple[_MarginPositionSnapshot, ...]:
    result: list[_MarginPositionSnapshot] = []
    seen: set[tuple[object, ...]] = set()
    for position in positions:
        identity = (
            position.setup_id,
            position.side,
            position.quantity,
            position.average_price,
            position.setup_start_equity,
        )
        if identity not in seen:
            seen.add(identity)
            result.append(position)
    return tuple(result)


def _research_cost_model(data: Session4DataBundle) -> CostModel:
    return CostModel(
        identifier=COST_PROFILE_ID,
        commission=CostComponent(
            model_id=(
                "nautilus-native-bybit-fixed-maker-"
                f"{data.spec.maker_fee}-taker-{data.spec.taker_fee}-v1"
            ),
            provenance=CostProvenance.MODELLED,
            complete=True,
            research_eligible=True,
            notes=("engine-applied", "frozen-public-contract-proxy"),
        ),
        funding=CostComponent(
            model_id="nautilus-native-historical-bybit-completed-h1-mark-funding-v2",
            provenance=CostProvenance.HISTORICAL,
            complete=True,
            research_eligible=True,
            notes=(
                "native-settlement",
                "development-only historical rates",
                "completed-H1 mark-price notional",
            ),
        ),
        slippage=CostComponent(
            model_id="nautilus-native-bar-one-adverse-price-tick-v1",
            provenance=CostProvenance.MODELLED,
            complete=True,
            research_eligible=True,
            notes=("one-price-tick", "no-order-book limitation accepted in preregistration"),
        ),
        execution=CostComponent(
            model_id=PYO3_RESEARCH_EXECUTION_PROFILE,
            provenance=CostProvenance.MODELLED,
            complete=True,
            research_eligible=True,
            notes=("native-bar-matching", "adaptive-high-low-ordering", "netting-reduce-only"),
        ),
    )


def _build_result_frames(
    events: Sequence[DomainEvent],
    native_run: Pyo3SmokeRun,
) -> dict[str, pd.DataFrame]:
    equity_events = [event for event in events if isinstance(event, AccountEquityUpdated)]
    if not equity_events:
        raise Session4InvariantError("native run published no equity history")
    equity = pd.DataFrame(
        {"equity": [float(event.equity) for event in equity_events]},
        index=pd.DatetimeIndex(
            [event.occurred_at_utc for event in equity_events],
            name="timestamp",
        ),
    )
    closed = [event for event in events if isinstance(event, PositionClosed)]
    trades = pd.DataFrame.from_records(
        [
            {
                "event_id": event.event_id,
                "setup_id": event.setup_id,
                "exit_time": event.occurred_at_utc,
                "close_reason": event.close_reason.value,
                "gross_price_pnl": float(event.realized_price_pnl),
                "commissions": float(event.commissions),
                "funding_net": float(event.funding),
                "slippage_cost": float(event.realized_slippage_cost),
                "setup_net_pnl": float(
                    event.realized_price_pnl
                    - event.commissions
                    + event.funding
                    - event.realized_slippage_cost
                ),
            }
            for event in closed
        ]
    )
    funding_events = [event for event in events if isinstance(event, FundingApplied)]
    funding = pd.DataFrame.from_records(
        [
            {
                "settlement_id": event.settlement_id,
                "event_time": event.occurred_at_utc,
                "setup_id": event.setup_id,
                "amount": float(event.amount),
                "currency": "USDT",
                "provenance": "NAUTILUS_NATIVE_HISTORICAL_BYBIT_FUNDING",
            }
            for event in funding_events
        ]
    )
    return {
        "equity": _stable_frame(equity),
        "trades": _stable_frame(trades),
        "orders": _stable_frame(native_run.reports.orders),
        "fills": _stable_frame(native_run.reports.fills),
        "positions": _stable_frame(native_run.reports.positions),
        "funding": _stable_frame(funding),
    }


def _build_metric_values(equity: pd.DataFrame, trades: pd.DataFrame) -> dict[str, object]:
    trade_pnl = (
        None
        if trades.empty or "setup_net_pnl" not in trades.columns
        else trades["setup_net_pnl"].astype(float)
    )
    metric_logger = logging.getLogger("algo_bot.metrics")
    was_disabled = metric_logger.disabled
    try:
        # Ostrzeżenia edge-case są funkcją wyniku i nie mogą wyciekać na live
        # stderr procesu roboczego przed zakończeniem outcome-blind sweepu.
        metric_logger.disabled = True
        summary = summarize(
            equity["equity"].astype(float),
            trade_pnl,
            periods_per_year=8_760.0,
        )
    finally:
        metric_logger.disabled = was_disabled
    return {
        "total_return_fraction": _finite_or_none(summary.total_return),
        "cagr_fraction": _finite_or_none(summary.cagr),
        "sharpe": _finite_or_none(summary.sharpe),
        "sortino": _finite_or_none(summary.sortino),
        "calmar": _finite_or_none(summary.calmar),
        "mar": _finite_or_none(summary.mar),
        "max_drawdown_fraction": _finite_or_none(summary.max_drawdown_pct),
        "max_drawdown_display_pct": _finite_or_none(100.0 * summary.max_drawdown_pct),
        "max_drawdown_duration_days": _finite_or_none(summary.max_drawdown_duration_days),
        "recovery_time_days": _finite_or_none(summary.recovery_time_days),
        "profit_factor": _finite_or_none(summary.profit_factor),
        "win_rate_fraction": _finite_or_none(summary.win_rate),
        "n_trades": summary.n_trades,
        "periods_per_year": summary.periods_per_year,
    }


def _liquidation_metric_values() -> dict[str, object]:
    """Nie udaje settlementu po LP ceną technicznego close na kolejnym barze."""

    return {
        "total_return_fraction": None,
        "cagr_fraction": None,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "mar": None,
        "max_drawdown_fraction": None,
        "max_drawdown_display_pct": None,
        "max_drawdown_duration_days": None,
        "recovery_time_days": None,
        "profit_factor": None,
        "win_rate_fraction": None,
        "n_trades": None,
        "periods_per_year": 8_760.0,
    }


def _build_stats(
    *,
    run_spec: Session4RunSpec,
    machine: MastermindStateMachine,
    frames: Mapping[str, pd.DataFrame],
    fills: Sequence[OrderPartiallyFilled | OrderFilled],
    accumulator: Session4Accumulator,
    margin: CausalIsolatedMarginMonitor,
    boundary: Session4BoundaryController,
    counters: Mapping[str, int],
    invariant_ledger: Sequence[Mapping[str, JsonValue]],
    final_snapshot: str,
    metric_values: Mapping[str, object],
    performance: PerformanceAssessment,
) -> dict[str, JsonValue]:
    closed = list(_closed_events_from_frame(frames["trades"]))
    equity = frames["equity"]
    first_equity = float(equity["equity"].iloc[0])
    fill_notional = sum((event.price * event.last_quantity for event in fills), Decimal(0))
    scout_episodes = accumulator.finalized_scout_episodes()
    liquidated = bool(margin.liquidation_events)
    technical_accounting: dict[str, object] = {
        "gross_price_pnl": _frame_sum(frames["trades"], "gross_price_pnl"),
        "commissions": _frame_sum(frames["trades"], "commissions"),
        "funding_paid": -sum(
            min(0.0, float(value)) for value in frames["funding"].get("amount", [])
        ),
        "funding_received": sum(
            max(0.0, float(value)) for value in frames["funding"].get("amount", [])
        ),
        "funding_net": _frame_sum(frames["funding"], "amount"),
        "slippage_cost": _frame_sum(frames["trades"], "slippage_cost"),
        "setup_net_pnl": _frame_sum(frames["trades"], "setup_net_pnl"),
        "final_equity": float(equity["equity"].iloc[-1]),
        "turnover": float(fill_notional) / first_equity,
    }
    payload: dict[str, object] = {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "execution_profile_id": EXECUTION_PROFILE_ID,
        "margin_profile_id": MARGIN_PROFILE_ID,
        "margin_overlap_policy": "ANY_POSITION_EXPOSURE_OVERLAPPING_COMPLETED_H1",
        "margin_model_scope": "DETERMINISTIC_SETUP_EQUITY_PROXY_NOT_BYBIT_ACCOUNT_LEDGER",
        "margin_model_limitations": [
            "effective leverage is recomputed from current entry notional and setup-start equity",
            "extra margin is fixed at zero",
            "dynamic isolated collateral debits and releases are not reconstructed",
            "technical next-bar flatten is not liquidation-price settlement",
        ],
        "metric_profile_id": METRIC_PROFILE_ID,
        "run_id": run_spec.run_id,
        "ordinal": run_spec.ordinal,
        "symbol": run_spec.symbol,
        "marking_timeframe": run_spec.marking_timeframe,
        "parameter_set_id": run_spec.parameter_set.parameter_set_id,
        "variant_id": run_spec.variant.variant_id,
        "run_spec_hash": run_spec.run_spec_hash,
        "config_hash": run_spec.config_hash,
        "evidence_gate_passed": True,
        "performance_gate": performance.as_dict(),
        "economic_metrics_interpretable": not liquidated,
        "liquidation_accounting_profile_id": LIQUIDATION_ACCOUNTING_PROFILE_ID,
        "units": {
            "prices_notional_pnl_equity_cost_amounts": "USDT",
            "quantity_volume": "base_asset",
            "fee_rates_funding_rates_returns_drawdown": "fraction",
            "display_percent": "percentage_points",
        },
        "metrics": dict(metric_values),
        **dict(metric_values),
        "counters": dict(counters),
        **dict(counters),
        "scout_episode_bars": scout_episodes,
        "scout_episode_mean_bars": (
            0.0 if not scout_episodes else sum(scout_episodes) / len(scout_episodes)
        ),
        "scout_right_censored": accumulator.scout_right_censored,
        "max_committed_target_quote": accumulator.max_committed_target_quote,
        "max_gross_realized_exposure_quote": accumulator.max_gross_realized_exposure_quote,
        "max_committed_exposure_multiplier": accumulator.max_committed_exposure_multiplier,
        "max_actual_gross_exposure_multiplier": accumulator.max_actual_gross_exposure_multiplier,
        "gross_price_pnl": None if liquidated else technical_accounting["gross_price_pnl"],
        "commissions": None if liquidated else technical_accounting["commissions"],
        "funding_paid": None if liquidated else technical_accounting["funding_paid"],
        "funding_received": None if liquidated else technical_accounting["funding_received"],
        "funding_net": None if liquidated else technical_accounting["funding_net"],
        "slippage_cost": None if liquidated else technical_accounting["slippage_cost"],
        "setup_net_pnl": None if liquidated else technical_accounting["setup_net_pnl"],
        "final_equity": None if liquidated else technical_accounting["final_equity"],
        "turnover": None if liquidated else technical_accounting["turnover"],
        "native_technical_flatten_accounting": technical_accounting,
        "liquidation_event_count": len(margin.liquidation_events),
        "liquidation_evidence": list(margin.liquidation_evidence),
        "mark_bars_observed": margin.mark_bars_observed,
        "positioned_mark_bars_checked": margin.positioned_mark_bars_checked,
        "marking_event_sha256": accumulator.marking_digest.hexdigest(),
        "manual_cutoff_count": boundary.manual_cutoff_count,
        "cutoff_close_ns": boundary.cutoff_close_ns,
        "final_close_ns": boundary.final_close_ns,
        "final_state": {
            "risk_mode": machine.state.risk_mode.value,
            "position_build": machine.state.position_build.value,
            "order_lifecycle": machine.state.order_lifecycle.value,
            "real_open_quantity": machine.state.real_open_quantity,
            "active_domain_orders": sum(
                order.status.active for order in machine.state.orders.values()
            ),
            "outbox_size": len(machine.state.outbox),
        },
        "final_snapshot_sha256": hashlib.sha256(final_snapshot.encode("utf-8")).hexdigest(),
        "invariant_ledger": list(invariant_ledger),
        "closed_setup_rows": None if liquidated else len(closed),
    }
    normalized = normalize_json(payload)
    if not isinstance(normalized, dict):
        raise AssertionError("stats payload must normalize to an object")
    return normalized


def _build_invariant_ledger(
    *,
    machine: MastermindStateMachine,
    native_run: Pyo3SmokeRun,
    accumulator: Session4Accumulator,
    margin: CausalIsolatedMarginMonitor,
    boundary: Session4BoundaryController,
    data: Session4DataBundle,
    fills: Sequence[OrderPartiallyFilled | OrderFilled],
    final_snapshot: str,
    marking_expected: int,
) -> tuple[dict[str, JsonValue], ...]:
    state = machine.state
    closed = [event for event in native_run.domain_events if isinstance(event, PositionClosed)]
    funding = [event for event in native_run.domain_events if isinstance(event, FundingApplied)]
    fill_commissions = sum((event.commission for event in fills), Decimal(0))
    closed_commissions = sum((event.commissions for event in closed), Decimal(0))
    funding_net = sum((event.amount for event in funding), Decimal(0))
    closed_funding = sum((event.funding for event in closed), Decimal(0))
    expected_slippage = sum(
        (
            event.last_quantity * data.spec.price_increment
            for event in fills
            if event.benchmark_price is not None
        ),
        Decimal(0),
    )
    closed_slippage = sum((event.realized_slippage_cost for event in closed), Decimal(0))
    active_domain = sorted(
        order.client_order_id for order in state.orders.values() if order.status.active
    )
    active_native = _native_open_order_ids(native_run.reports.orders)
    funding_ids = [event.settlement_id for event in funding]
    expected_funding_timestamps = _expected_funding_settlements(
        data,
        native_run.domain_events,
    )
    observed_funding_timestamps = sorted(_to_ns(event.occurred_at_utc) for event in funding)
    expected_funding_amounts = _expected_funding_amounts(data, native_run.domain_events)
    observed_funding_amounts = sorted(
        (_to_ns(event.occurred_at_utc), event.amount) for event in funding
    )
    unallocated_funding = [
        diagnostic
        for diagnostic in state.diagnostics
        if diagnostic.startswith("UNALLOCATED_FUNDING:")
    ]
    try:
        restored = MastermindStateMachine.from_snapshot(machine.config, final_snapshot)
        snapshot_roundtrip = restored.snapshot_json() == final_snapshot
    except (TypeError, ValueError):
        snapshot_roundtrip = False
    retained_forbidden = [
        type(event).__name__
        for event in native_run.domain_events
        if isinstance(event, (BarClosed, MarkingBarClosed))
    ]
    development_exit_count, expected_manual_count = _development_exit_policy(
        margin=margin,
        boundary=boundary,
    )
    checks: tuple[tuple[str, object, object, bool], ...] = (
        (
            "INVARIANT_VIOLATION_COUNT_ZERO",
            state.invariant_violation_count,
            0,
            state.invariant_violation_count == 0,
        ),
        (
            "FINAL_DOMAIN_POSITION_FLAT",
            state.position_build.value,
            PositionBuild.FLAT.value,
            state.position_build is PositionBuild.FLAT,
        ),
        ("FINAL_DOMAIN_QUANTITY_ZERO", state.real_open_quantity, 0, state.real_open_quantity == 0),
        (
            "FINAL_NATIVE_QUANTITY_ZERO",
            native_run.final_net_quantity,
            0,
            native_run.final_net_quantity == 0,
        ),
        (
            "FINAL_ORDER_LIFECYCLE_NONE",
            state.order_lifecycle.value,
            OrderLifecycle.NONE.value,
            state.order_lifecycle is OrderLifecycle.NONE,
        ),
        ("FINAL_SETUP_NONE", state.setup is None, True, state.setup is None),
        ("NO_ACTIVE_DOMAIN_ORDERS", active_domain, [], not active_domain),
        ("NO_ACTIVE_NATIVE_ORDERS", active_native, [], not active_native),
        ("FINAL_OUTBOX_EMPTY", len(state.outbox), 0, not state.outbox),
        (
            "DEVELOPMENT_EXIT_EMITTED_ONCE",
            development_exit_count,
            1,
            development_exit_count == 1,
        ),
        (
            "MANUAL_CUTOFF_POLICY_MATCHES_LIQUIDATION",
            boundary.manual_cutoff_count,
            expected_manual_count,
            boundary.manual_cutoff_count == expected_manual_count,
        ),
        (
            "MARK_PRICE_BAR_COVERAGE_COMPLETE",
            margin.mark_bars_observed,
            len(data.h1_bars),
            margin.mark_bars_observed == len(data.h1_bars),
        ),
        (
            "MARKING_EVENT_COUNT_COMPLETE",
            accumulator.marking_event_count,
            marking_expected,
            accumulator.marking_event_count == marking_expected,
        ),
        (
            "DOMAIN_BAR_CUTOFF_COUNT",
            accumulator.domain_bar_count,
            f"<= {len(data.h1_bars) - 2}",
            accumulator.domain_bar_count <= len(data.h1_bars) - 2,
        ),
        ("STREAMING_MARKERS_NOT_RETAINED", retained_forbidden, [], not retained_forbidden),
        (
            "FUNDING_SETTLEMENT_IDS_UNIQUE",
            len(set(funding_ids)),
            len(funding_ids),
            len(set(funding_ids)) == len(funding_ids),
        ),
        (
            "FUNDING_LEDGER_RECONCILED",
            sorted(state.pnl.funding_settlement_ids),
            sorted(set(funding_ids)),
            state.pnl.funding_settlement_ids == set(funding_ids),
        ),
        ("NO_UNALLOCATED_FUNDING", unallocated_funding, [], not unallocated_funding),
        (
            "NATIVE_FUNDING_SETTLEMENTS_COMPLETE",
            observed_funding_timestamps,
            expected_funding_timestamps,
            observed_funding_timestamps == expected_funding_timestamps,
        ),
        (
            "NATIVE_COMMISSION_EVIDENCE_PRESENT",
            [event.commission for event in fills],
            "positive commission per native fill or no fills",
            not fills or all(event.commission > 0 for event in fills),
        ),
        (
            "COMMISSION_LEDGER_RECONCILED",
            closed_commissions,
            fill_commissions,
            closed_commissions == fill_commissions,
        ),
        (
            "FUNDING_AMOUNT_LEDGER_RECONCILED",
            {
                "closed_total": closed_funding,
                "native_total": funding_net,
                "native_by_settlement": observed_funding_amounts,
            },
            {
                "native_total": funding_net,
                "oracle_by_settlement": expected_funding_amounts,
            },
            closed_funding == funding_net and observed_funding_amounts == expected_funding_amounts,
        ),
        (
            "ONE_TICK_SLIPPAGE_LEDGER_RECONCILED",
            closed_slippage,
            expected_slippage,
            closed_slippage == expected_slippage,
        ),
        (
            "RAW_DOMAIN_FILL_IDS_UNIQUE",
            sum(isinstance(event, _DOMAIN_FILL_TYPES) for event in native_run.domain_events),
            len(fills),
            sum(isinstance(event, _DOMAIN_FILL_TYPES) for event in native_run.domain_events)
            == len(fills),
        ),
        (
            "TRANSITION_OBSERVER_COUNT_EXACT",
            accumulator.transition_count,
            len(native_run.domain_events)
            + accumulator.marking_event_count
            + accumulator.domain_bar_count,
            accumulator.transition_count
            == len(native_run.domain_events)
            + accumulator.marking_event_count
            + accumulator.domain_bar_count,
        ),
        ("FINAL_SNAPSHOT_ROUNDTRIP", snapshot_roundtrip, True, snapshot_roundtrip),
        (
            "NO_HOLDOUT_NATIVE_DATA",
            max(int(item.ts_init) for item in data.native_data),
            f"< {_to_ns(HOLDOUT_START)}",
            all(int(item.ts_init) < _to_ns(HOLDOUT_START) for item in data.native_data),
        ),
        (
            "STRATEGY_HOLDOUT_ROWS_READ_ZERO",
            data.metadata.holdout_rows_read,
            0,
            data.metadata.holdout_rows_read == 0,
        ),
        (
            "MARGIN_PROFILE_HAS_RISK_TIERS",
            len(data.mark_context.maintenance_margin_tiers),
            "> 0",
            bool(data.mark_context.maintenance_margin_tiers),
        ),
        (
            "LIQUIDATION_EVENT_AT_MOST_ONE",
            {
                "events": len(margin.liquidation_events),
                "evidence_records": len(margin.liquidation_evidence),
            },
            "equal counts and <= 1",
            len(margin.liquidation_events) == len(margin.liquidation_evidence)
            and len(margin.liquidation_events) <= 1,
        ),
    )
    ledger = tuple(_check_record(*check) for check in checks)
    observed_codes = tuple(str(item["code"]) for item in ledger)
    if observed_codes != SESSION4_INVARIANT_CODES:
        raise AssertionError("Session 4 invariant code/order drift")
    return ledger


def _development_exit_policy(
    *,
    margin: CausalIsolatedMarginMonitor,
    boundary: Session4BoundaryController,
) -> tuple[int, int]:
    """Rozstrzyga terminalny outcome i oczekiwany cutoff bez podwójnego close.

    Crossing może zostać wykryty dopiero na ostatnim H1, już po wykonaniu
    wcześniej wysłanego manualnego cutoffu. Wtedy liquidation zastępuje
    interpretację ekonomiczną runu, ale manualny request pozostaje jedynym
    technicznym żądaniem zamknięcia.
    """

    liquidation_count = len(margin.liquidation_events)
    development_exit_count = int(bool(boundary.manual_cutoff_count or liquidation_count))
    if not liquidation_count:
        return development_exit_count, 1
    observed_at = margin.liquidation_events[0].observed_at
    if observed_at is None:
        raise Session4InvariantError("liquidation event has no causal observation timestamp")
    liquidation_close_ns = int(pd.Timestamp(observed_at).value)
    expected_manual_count = int(liquidation_close_ns > boundary.cutoff_close_ns)
    return development_exit_count, expected_manual_count


def _derive_counters(
    machine: MastermindStateMachine,
    events: Sequence[DomainEvent],
    accumulator: Session4Accumulator,
) -> dict[str, int]:
    state = machine.state
    fills = _unique_domain_fills(events)
    result = {
        "transitions": accumulator.transition_count,
        "marking_bars": accumulator.marking_event_count,
        "domain_h1_bars": accumulator.domain_bar_count,
        "setups_started": len({event.setup_id for event in fills if event.setup_id is not None}),
        "fills": len(fills),
        "position_closes": sum(isinstance(event, PositionClosed) for event in events),
        "funding_settlements": sum(isinstance(event, FundingApplied) for event in events),
        "addon_trigger_facts": state.counters.get("addon_trigger_facts", 0),
        "addon_intents": state.counters.get("addon_intents", 0),
        "addon_rejections": state.counters.get("addon_rejections", 0),
        "full_to_scout_transitions": state.counters.get("full_to_scout_transitions", 0),
        "scout_to_full_rearms": state.counters.get("scout_to_full_rearms", 0),
        "invariant_violation_count": state.invariant_violation_count,
    }
    return result


def _expected_funding_settlements(
    data: Session4DataBundle,
    events: Sequence[DomainEvent],
) -> list[int]:
    first_entries: dict[str, int] = {}
    closes: dict[str, int] = {}
    for event in events:
        setup_id = event.setup_id
        if setup_id is None:
            continue
        if isinstance(event, _DOMAIN_FILL_TYPES) and event.role is OrderRole.BASE_ENTRY:
            event_ns = _to_ns(event.occurred_at_utc)
            first_entries[setup_id] = min(first_entries.get(setup_id, event_ns), event_ns)
        elif isinstance(event, PositionClosed):
            closes[setup_id] = _to_ns(event.occurred_at_utc)
    if set(first_entries) != set(closes):
        raise Session4InvariantError("cannot derive closed setup intervals for funding audit")
    intervals = [(first_entries[key], closes[key]) for key in sorted(first_entries)]
    return sorted(
        int(update.next_funding_ns)
        for update in data.funding_updates
        if any(start <= int(update.next_funding_ns) < end for start, end in intervals)
    )


def _expected_funding_amounts(
    data: Session4DataBundle,
    events: Sequence[DomainEvent],
) -> list[tuple[int, Decimal]]:
    """Liczy niezależny oracle: signed qty × ukończony mark Close × rate."""

    expected_timestamps = _expected_funding_settlements(data, events)
    transitions: list[tuple[int, int, Decimal]] = []
    for sequence, event in enumerate(events):
        if isinstance(event, PositionChanged):
            transitions.append((_to_ns(event.occurred_at_utc), sequence, event.signed_quantity))
        elif isinstance(event, PositionClosed):
            transitions.append((_to_ns(event.occurred_at_utc), sequence, Decimal(0)))
    transitions.sort()

    result: list[tuple[int, Decimal]] = []
    transition_position = 0
    signed_quantity = Decimal(0)
    for settlement_ns in expected_timestamps:
        while (
            transition_position < len(transitions)
            and transitions[transition_position][0] <= settlement_ns
        ):
            signed_quantity = transitions[transition_position][2]
            transition_position += 1
        if signed_quantity == 0:
            raise Session4InvariantError(
                f"funding oracle found no open position at settlement {settlement_ns}"
            )
        settlement_time = pd.Timestamp(settlement_ns, unit="ns", tz="UTC")
        mark_open_time = pd.Timestamp(settlement_ns - HOUR_NS, unit="ns", tz="UTC")
        try:
            funding_row = cast("pd.Series[Any]", data.funding.loc[settlement_time])
            mark_row = cast("pd.Series[Any]", data.mark_context.bars.loc[mark_open_time])
        except KeyError as exc:
            raise Session4InvariantError(
                f"funding oracle lacks rate or completed mark H1 at {settlement_time.isoformat()}"
            ) from exc
        rate = _decimal_text(funding_row["funding_rate"], "funding_rate")
        mark_close = _decimal_text(mark_row["Close"], "mark Close")
        expected = (-signed_quantity * mark_close * rate).quantize(
            USDT_FUNDING_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        result.append((settlement_ns, expected))
    return result


def _unique_domain_fills(
    events: Sequence[DomainEvent],
) -> tuple[OrderPartiallyFilled | OrderFilled, ...]:
    unique: list[OrderPartiallyFilled | OrderFilled] = []
    payloads: dict[str, tuple[object, ...]] = {}
    for event in events:
        if not isinstance(event, _DOMAIN_FILL_TYPES):
            continue
        payload = (
            event.role,
            event.last_quantity,
            event.cumulative_quantity,
            event.price,
            event.commission,
            event.benchmark_price,
            event.client_order_id,
            event.setup_id,
        )
        previous = payloads.get(event.execution_id)
        if previous is None:
            payloads[event.execution_id] = payload
            unique.append(event)
        elif previous != payload:
            raise Session4InvariantError(
                f"execution ID {event.execution_id} has conflicting domain payloads"
            )
        else:
            raise Session4InvariantError(f"duplicate domain execution ID {event.execution_id}")
    return tuple(unique)


def _reconcile_native_fills(
    domain_fills: Sequence[OrderPartiallyFilled | OrderFilled],
    native_fills: pd.DataFrame,
    *,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> None:
    """Dowodzi zgodności filli oraz frozen prowizji Bybit w walucie USDT."""

    if native_fills.empty:
        if domain_fills:
            raise Session4InvariantError("domain fills exist without native cache fills")
        return
    required = {"trade_id", "last_qty", "last_px", "commission", "liquidity_side"}
    missing = sorted(required - set(native_fills.columns))
    if missing:
        raise Session4InvariantError(f"native fill report missing columns: {missing}")
    rows: dict[str, pd.Series[Any]] = {}
    for _, row in native_fills.iterrows():
        trade_id = str(row["trade_id"])
        if trade_id in rows:
            raise Session4InvariantError(f"duplicate native trade_id {trade_id}")
        rows[trade_id] = row
    domain_ids = {event.execution_id for event in domain_fills}
    if domain_ids != set(rows):
        raise Session4InvariantError("domain execution IDs differ from native trade IDs")
    for event in domain_fills:
        row = rows[event.execution_id]
        commission_amount, commission_currency = _money_amount_currency(row["commission"])
        if commission_currency != "USDT":
            raise Session4InvariantError(
                f"native commission currency mismatch for {event.execution_id}: "
                f"{commission_currency!r} != 'USDT'"
            )
        observed = (
            _decimal_text(row["last_qty"], "last_qty"),
            _decimal_text(row["last_px"], "last_px"),
            commission_amount,
        )
        expected = (event.last_quantity, event.price, event.commission)
        if observed != expected:
            raise Session4InvariantError(
                f"native/domain fill mismatch for {event.execution_id}: {observed} != {expected}"
            )
        liquidity_side = str(row["liquidity_side"]).strip().upper()
        if liquidity_side == "MAKER":
            fee_rate = maker_fee
        elif liquidity_side == "TAKER":
            fee_rate = taker_fee
        else:
            raise Session4InvariantError(
                f"invalid native liquidity_side for {event.execution_id}: {liquidity_side!r}"
            )
        # Nautilus 1.230.0's MakerTakerFeeModel converts the exact Decimal
        # commission through Money::from_decimal. Money rounds to the quote
        # currency precision with midpoint-to-even semantics. Keep this oracle
        # strict and reproduce that rule exactly; a one-quantum tolerance would
        # also admit genuinely incorrect commissions.
        notional = (event.last_quantity * event.price).quantize(
            USDT_COMMISSION_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        expected_commission = (notional * fee_rate).quantize(
            USDT_COMMISSION_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        if commission_amount != expected_commission:
            raise Session4InvariantError(
                f"native commission algebra mismatch for {event.execution_id}: "
                f"{commission_amount} != {expected_commission} "
                f"({liquidity_side} rate={fee_rate})"
            )


def _native_open_order_ids(orders: pd.DataFrame) -> list[str]:
    if orders.empty:
        return []
    if "status" not in orders.columns:
        raise Session4InvariantError("native order report has no status column")
    id_column = "client_order_id" if "client_order_id" in orders.columns else None
    result: list[str] = []
    for index, row in orders.iterrows():
        if str(row["status"]).upper() not in _TERMINAL_NATIVE_ORDER_STATUSES:
            result.append(str(row[id_column] if id_column is not None else index))
    return sorted(result)


def _stable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    if result.empty:
        return result.reset_index(drop=True) if isinstance(result.index, pd.RangeIndex) else result
    if isinstance(result.index, pd.DatetimeIndex):
        return result.sort_index(kind="stable")
    preferred = [
        name
        for name in (
            "ts_event",
            "ts_init",
            "event_time",
            "exit_time",
            "client_order_id",
            "event_id",
            "settlement_id",
        )
        if name in result.columns
    ]
    if preferred:
        result = result.sort_values(preferred, kind="stable")
    return result.reset_index(drop=True)


def _check_record(
    code: str, observed: object, expected: object, passed: bool
) -> dict[str, JsonValue]:
    normalized = normalize_json(
        {"code": code, "observed": observed, "expected": expected, "passed": passed}
    )
    if not isinstance(normalized, dict):
        raise AssertionError("invariant record must normalize to object")
    return normalized


def _frame_sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(frame[column].astype(float).sum())


def _closed_events_from_frame(frame: pd.DataFrame) -> Sequence[object]:
    return () if frame.empty else tuple(frame.itertuples(index=False))


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _decimal_text(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise Session4InvariantError(f"invalid native {field_name}") from exc
    if not parsed.is_finite():
        raise Session4InvariantError(f"non-finite native {field_name}")
    return parsed


def _money_amount_currency(value: object) -> tuple[Decimal, str]:
    """Rozbija kanoniczny raport Money i nie pozwala zgubić waluty prowizji."""

    text = str(value).strip()
    parts = text.split()
    if len(parts) != 2 or not parts[1]:
        raise Session4InvariantError(f"invalid native commission money: {text!r}")
    return _decimal_text(parts[0], "commission"), parts[1].upper()


def _to_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise Session4ExecutionError("timestamp must be UTC")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (
        delta.days * 86_400_000_000_000 + delta.seconds * 1_000_000_000 + delta.microseconds * 1_000
    )


def _datetime_from_ns(value: int) -> datetime:
    return pd.Timestamp(value, unit="ns", tz="UTC").to_pydatetime()
