"""P4 position/OMS gate against NautilusTrader and its Binance adapter."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from nautilus_trader.adapters.binance.common.enums import (
    BinanceAccountType,
    BinanceFuturesPositionSide,
    BinanceTimeInForce,
)
from nautilus_trader.adapters.binance.execution import BinanceCommonExecutionClient
from nautilus_trader.adapters.binance.futures.enums import BinanceFuturesEnumParser
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.common.component import TestClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    BarAggregation,
    OmsType,
    OrderSide,
    PriceType,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, PositionId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.orders import Order, OrderList, StopMarketOrder
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.commands import TestCommandStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.trading.strategy import Strategy

from algo_bot.engine.nautilus_oms_poc import (
    ADDON_PARTIAL_STOP_POLICY,
    REJECTED_OMS_MODEL,
    SELECTED_OMS_MODEL,
    NettingProtectionProbe,
    ProtectiveRole,
)

BASE_QTY = Decimal("1.000")
PARTIALS = (Decimal("0.400"), Decimal("0.600"))


@dataclass(frozen=True)
class _OmsPositionResult:
    net_quantity: Decimal
    open_signed_quantities: tuple[Decimal, ...]
    all_position_count: int


class _OmsProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class _OmsProbeStrategy(Strategy):
    """Open base+add-on, then attach a whole-net close fill to the base ID."""

    def __init__(self, config: _OmsProbeConfig) -> None:
        super().__init__(config)
        self._roles: dict[ClientOrderId, str] = {}
        self._started = False
        self._base_position_id: PositionId | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        del bar
        if self._started:
            return
        self._started = True
        self._submit_entry("base")

    def on_order_filled(self, event: OrderFilled) -> None:
        role = self._roles[event.client_order_id]
        if role == "base":
            self._base_position_id = event.position_id
            self._submit_entry("addon")
        elif role == "addon":
            instrument = self.cache.instrument(self.config.instrument_id)
            assert instrument is not None
            close = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(BASE_QTY * 2),
                time_in_force=TimeInForce.GTC,
            )
            self._roles[close.client_order_id] = "close"
            # This association is the critical OMS-B behavior: Binance Close-All
            # produces one whole-net fill, but HEDGING attaches it to one virtual leg.
            self.submit_order(close, position_id=self._base_position_id)

    def _submit_entry(self, role: str) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        assert instrument is not None
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(BASE_QTY),
            time_in_force=TimeInForce.GTC,
        )
        self._roles[order.client_order_id] = role
        self.submit_order(order)


class _CaptureHttpAccount:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def new_algo_order(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _BinanceWireProbe:
    def __init__(self) -> None:
        self._binance_account_type = BinanceAccountType.USDT_FUTURES
        self._http_account = _CaptureHttpAccount()
        self._enum_parser = BinanceFuturesEnumParser()
        self._recv_window = 5_000

    @staticmethod
    def _determine_time_in_force(order: Order) -> BinanceTimeInForce:
        del order
        return BinanceTimeInForce.GTC

    @staticmethod
    def _determine_good_till_date(
        order: Order,
        time_in_force: BinanceTimeInForce | None,
    ) -> None:
        del order, time_in_force

    @staticmethod
    def _determine_reduce_only_str(order: Order) -> str:
        return str(order.is_reduce_only)


class _OrderListProbe:
    def __init__(self) -> None:
        self.denied: list[tuple[ClientOrderId, str]] = []

    @staticmethod
    def _get_position_side_from_position_id(
        position_id: PositionId | None,
        exec_spawn_id: ClientOrderId | None,
    ) -> BinanceFuturesPositionSide:
        del position_id, exec_spawn_id
        return BinanceFuturesPositionSide.BOTH

    def _deny_order_pre_submit(self, order: Order, reason: str) -> None:
        self.denied.append((order.client_order_id, reason))

    async def _submit_order_inner(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("linked conditional list must be denied before submission")


class _ModifyStopProbe:
    def __init__(self, order: StopMarketOrder) -> None:
        self._binance_account_type = BinanceAccountType.USDT_FUTURES
        self._cache = SimpleNamespace(order=lambda _client_order_id: order)
        self._triggered_algo_order_ids: set[ClientOrderId] = set()
        self._log = SimpleNamespace(error=lambda _message: None)
        self._clock = TestClock()
        self.rejection_reasons: list[str] = []

    def generate_order_modify_rejected(self, *_args: Any) -> None:
        self.rejection_reasons.append(str(_args[4]))


def test_real_binance_adapter_encodes_distinct_base_and_addon_stop_contracts() -> None:
    """Close-All omits quantity/reduceOnly; add-on sends exact reduceOnly qty."""

    base_stop = _stop_order(quantity=Decimal("1.000"), reduce_only=False)
    addon_stop = _stop_order(quantity=Decimal("0.400"), reduce_only=True)
    validator = SimpleNamespace(_binance_account_type=BinanceAccountType.USDT_FUTURES)

    assert (
        BinanceCommonExecutionClient._extract_close_position(
            validator,
            base_stop,
            {"close_position": True},
        )
        is True
    )

    wire = _BinanceWireProbe()
    asyncio.run(
        BinanceCommonExecutionClient._submit_stop_market_order(
            wire,
            base_stop,
            BinanceFuturesPositionSide.BOTH,
            None,
            True,
        )
    )
    asyncio.run(
        BinanceCommonExecutionClient._submit_stop_market_order(
            wire,
            addon_stop,
            BinanceFuturesPositionSide.BOTH,
            None,
            False,
        )
    )

    base_wire, addon_wire = wire._http_account.calls
    assert base_wire["side"].value == "SELL"
    assert base_wire["order_type"].value == "STOP_MARKET"
    assert base_wire["position_side"] is BinanceFuturesPositionSide.BOTH
    assert base_wire["trigger_price"] == "90.0"
    assert base_wire["time_in_force"] is BinanceTimeInForce.GTC
    assert base_wire["working_type"] == "CONTRACT_PRICE"
    assert base_wire["good_till_date"] is None
    assert base_wire["close_position"] == "true"
    assert "quantity" not in base_wire
    assert "reduce_only" not in base_wire
    assert addon_wire["side"].value == "SELL"
    assert addon_wire["order_type"].value == "STOP_MARKET"
    assert addon_wire["position_side"] is BinanceFuturesPositionSide.BOTH
    assert addon_wire["trigger_price"] == "90.0"
    assert addon_wire["time_in_force"] is BinanceTimeInForce.GTC
    assert addon_wire["working_type"] == "CONTRACT_PRICE"
    assert addon_wire["good_till_date"] is None
    assert addon_wire["quantity"] == "0.400"
    assert addon_wire["reduce_only"] == "True"
    assert "close_position" not in addon_wire


def test_real_binance_adapter_rejects_close_position_with_reduce_only() -> None:
    addon_stop = _stop_order(quantity=Decimal("0.400"), reduce_only=True)
    validator = SimpleNamespace(_binance_account_type=BinanceAccountType.USDT_FUTURES)

    with pytest.raises(ValueError, match="cannot be combined with `reduce_only`"):
        BinanceCommonExecutionClient._extract_close_position(
            validator,
            addon_stop,
            {"close_position": True},
        )

    market = _order_factory().market(
        instrument_id=_instrument().id,
        order_side=OrderSide.SELL,
        quantity=_instrument().make_qty(BASE_QTY),
    )
    with pytest.raises(ValueError, match="not supported for order type MARKET"):
        BinanceCommonExecutionClient._extract_close_position(
            validator,
            market,
            {"close_position": True},
        )


def test_real_binance_adapter_denies_linked_conditional_order_lists() -> None:
    """The Binance adapter denies bracket/OCO-style linked conditional lists."""

    factory = _order_factory()
    instrument = _instrument()
    bracket: OrderList = factory.bracket(
        instrument_id=instrument.id,
        order_side=OrderSide.BUY,
        quantity=instrument.make_qty(BASE_QTY),
        tp_price=instrument.make_price(Decimal("110.0")),
        sl_trigger_price=instrument.make_price(Decimal("90.0")),
    )
    command = TestCommandStubs.submit_order_list_command(bracket)
    probe = _OrderListProbe()

    asyncio.run(BinanceCommonExecutionClient._submit_order_list(probe, command))

    assert len(probe.denied) == len(bracket.orders)
    assert {reason for _, reason in probe.denied} == {"UNSUPPORTED_OCO_CONDITIONAL_ORDERS"}


def test_real_binance_adapter_rejects_amending_stop_market_quantity() -> None:
    """Incremental children avoid a cancel/replace gap because STOP_MARKET cannot amend."""

    addon_stop = _stop_order(quantity=PARTIALS[0], reduce_only=True)
    command = TestCommandStubs.modify_order_command(
        order=addon_stop,
        quantity=_instrument().make_qty(sum(PARTIALS, start=Decimal(0))),
    )
    probe = _ModifyStopProbe(addon_stop)

    asyncio.run(BinanceCommonExecutionClient._modify_order(probe, command))

    assert probe.rejection_reasons == [
        "only LIMIT orders supported by the venue (was STOP_MARKET)",
    ]


def test_oms_a_netting_has_one_flat_position_after_whole_net_close() -> None:
    result = _run_real_oms_probe(OmsType.NETTING)

    assert SELECTED_OMS_MODEL == "OMS-A_NETTING_VIRTUAL_LEGS_V1"
    assert result.net_quantity == 0
    assert result.open_signed_quantities == ()
    assert result.all_position_count == 1


def test_oms_b_virtual_positions_diverge_from_flat_net_venue() -> None:
    """A whole-net fill tied to base flips it and leaves the add-on virtual leg."""

    result = _run_real_oms_probe(OmsType.HEDGING)

    assert REJECTED_OMS_MODEL == "OMS-B_HEDGING_VIRTUAL_POSITIONS"
    assert result.net_quantity == 0
    assert result.open_signed_quantities == (Decimal("-1.000"), Decimal("1.000"))
    assert result.all_position_count == 3  # closed base + open add-on + flipped short


def test_partial_addon_fills_get_non_overlapping_incremental_stop_children() -> None:
    probe = NettingProtectionProbe(BASE_QTY)

    first = probe.apply_addon_fill(execution_id="entry-fill-1", quantity=PARTIALS[0])
    second = probe.apply_addon_fill(execution_id="entry-fill-2", quantity=PARTIALS[1])
    duplicate = probe.apply_addon_fill(execution_id="entry-fill-2", quantity=PARTIALS[1])
    snapshot = probe.snapshot()

    assert ADDON_PARTIAL_STOP_POLICY == "INCREMENTAL_REDUCE_ONLY_PER_FILL_V1"
    assert first is not None and first.quantity == PARTIALS[0]
    assert second is not None and second.quantity == PARTIALS[1]
    assert duplicate is None
    assert snapshot.addon_quantity == sum(PARTIALS, start=Decimal(0))
    assert snapshot.active_addon_stop_quantity == snapshot.addon_quantity
    assert all(
        order.reduce_only and not order.close_position
        for order in snapshot.active_orders
        if order.role is ProtectiveRole.ADDON_REDUCE_ONLY
    )


def test_restart_restores_stable_ids_dedupe_and_reconciles_without_duplication() -> None:
    probe = NettingProtectionProbe(BASE_QTY)
    first = probe.apply_addon_fill(execution_id="entry-fill-1", quantity=PARTIALS[0])
    assert first is not None
    checkpoint = probe.snapshot()

    restored = NettingProtectionProbe.restore(checkpoint)
    assert restored.snapshot() == checkpoint
    assert restored.reconcile_order_ids(
        (NettingProtectionProbe.BASE_STOP_ID, first.client_order_id)
    ).is_clean

    # Replayed fill is already covered by the restored client ID; no second child.
    assert restored.apply_addon_fill(execution_id="entry-fill-1", quantity=PARTIALS[0]) is None
    second = restored.apply_addon_fill(
        execution_id="entry-fill-2",
        quantity=PARTIALS[1],
    )
    assert second is not None and second.client_order_id == "ADDON-STOP-2"
    assert tuple(order.client_order_id for order in restored.snapshot().active_orders) == (
        "ADDON-STOP-1",
        "ADDON-STOP-2",
        NettingProtectionProbe.BASE_STOP_ID,
    )

    diff = restored.reconcile_order_ids((NettingProtectionProbe.BASE_STOP_ID, "VENUE-ORPHAN"))
    assert diff.missing_at_venue == ("ADDON-STOP-1", "ADDON-STOP-2")
    assert diff.orphan_at_venue == ("VENUE-ORPHAN",)


def test_all_addon_stop_children_leave_base_and_base_close_position_stop() -> None:
    probe = _pyramided_probe()

    assert (
        probe.trigger_stop(client_order_id="ADDON-STOP-1", execution_id="sl-fill-1") == PARTIALS[0]
    )
    assert (
        probe.trigger_stop(client_order_id="ADDON-STOP-2", execution_id="sl-fill-2") == PARTIALS[1]
    )
    snapshot = probe.snapshot()

    assert snapshot.base_quantity == BASE_QTY
    assert snapshot.addon_quantity == 0
    assert snapshot.net_quantity == BASE_QTY
    assert tuple(order.client_order_id for order in snapshot.active_orders) == (
        NettingProtectionProbe.BASE_STOP_ID,
    )


@pytest.mark.parametrize(
    "ordering",
    list(itertools.permutations(("BASE-STOP", "ADDON-STOP-1", "ADDON-STOP-2"))),
)
def test_gap_through_both_stops_is_safe_for_every_venue_ordering(
    ordering: tuple[str, str, str],
) -> None:
    """Venue serialization plus Close-All/reduceOnly never over-reduces or reverses."""

    probe = _pyramided_probe()
    total_reduced = Decimal(0)
    for index, client_order_id in enumerate(ordering):
        total_reduced += probe.trigger_stop(
            client_order_id=client_order_id,
            execution_id=f"gap-{index}",
        )
        probe.assert_safe()

    snapshot = probe.snapshot()
    assert total_reduced == BASE_QTY + sum(PARTIALS, start=Decimal(0))
    assert snapshot.net_quantity == 0
    assert snapshot.active_orders == ()


def test_flat_tp_cancels_all_orphans_and_duplicate_close_is_idempotent() -> None:
    probe = _pyramided_probe()

    assert probe.close_by_take_profit(execution_id="tp-fill") == Decimal("2.000")
    assert probe.close_by_take_profit(execution_id="tp-fill") == 0
    snapshot = probe.snapshot()

    assert snapshot.net_quantity == 0
    assert snapshot.active_orders == ()
    assert set(snapshot.canceled_order_ids) == {
        "BASE-STOP",
        "ADDON-STOP-1",
        "ADDON-STOP-2",
    }


def _run_real_oms_probe(strategy_oms: OmsType) -> _OmsPositionResult:
    instrument = _instrument()
    bar_type = BarType(
        instrument.id,
        BarSpecification(1, BarAggregation.HOUR, PriceType.LAST),
    )
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=False,
        )
    )
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money.from_str("100000 USDT")],
        default_leverage=Decimal(1),
        use_position_ids=False,
        use_reduce_only=True,
        use_message_queue=False,
    )
    engine.add_instrument(instrument)
    strategy = _OmsProbeStrategy(
        _OmsProbeConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            oms_type=strategy_oms,
        )
    )
    engine.add_strategy(strategy)
    engine.add_data(_probe_bars(instrument, bar_type))
    try:
        engine.run()
        positions = engine.cache.positions()
        signed = tuple(
            sorted(
                (position.signed_decimal_qty() for position in positions if position.is_open),
            )
        )
        return _OmsPositionResult(
            net_quantity=engine.portfolio.net_position(instrument.id),
            open_signed_quantities=signed,
            all_position_count=len(positions),
        )
    finally:
        engine.dispose()


def _probe_bars(instrument: Any, bar_type: BarType) -> list[Bar]:
    return [
        Bar(
            bar_type=bar_type,
            open=instrument.make_price(Decimal("100.0")),
            high=instrument.make_price(Decimal("101.0")),
            low=instrument.make_price(Decimal("99.0")),
            close=instrument.make_price(Decimal("100.0")),
            volume=instrument.make_qty(Decimal("1000.000")),
            ts_event=index * 3_600_000_000_000,
            ts_init=index * 3_600_000_000_000,
        )
        for index in range(1, 5)
    ]


def _pyramided_probe() -> NettingProtectionProbe:
    probe = NettingProtectionProbe(BASE_QTY)
    for index, quantity in enumerate(PARTIALS, start=1):
        probe.apply_addon_fill(execution_id=f"entry-fill-{index}", quantity=quantity)
    return probe


def _stop_order(*, quantity: Decimal, reduce_only: bool) -> StopMarketOrder:
    instrument = _instrument()
    return _order_factory().stop_market(
        instrument_id=instrument.id,
        order_side=OrderSide.SELL,
        quantity=instrument.make_qty(quantity),
        trigger_price=instrument.make_price(Decimal("90.0")),
        trigger_type=TriggerType.LAST_PRICE,
        time_in_force=TimeInForce.GTC,
        reduce_only=reduce_only,
    )


def _order_factory() -> OrderFactory:
    return OrderFactory(
        trader_id=TestIdStubs.trader_id(),
        strategy_id=TestIdStubs.strategy_id(),
        clock=TestClock(),
    )


def _instrument() -> Any:
    return TestInstrumentProvider.btcusdt_perp_binance()
