"""P3 hard-gate characterization against NautilusTrader 1.230.0."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import ccxt
import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    AccountType,
    OmsType,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CryptoPerpetual, Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.model.orders import Order
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

from algo_bot.engine.nautilus_poc import (
    MILLISECONDS_TO_NANOSECONDS,
    P3ExecutionProfile,
    binance_bar_close_ns,
    ccxt_ohlcv_to_nautilus_bar,
    latency_model_for_profile,
)
from algo_bot.fetch_data import _save_append

HOUR_MS = 3_600_000
FIRST_OPEN_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z
TRADE_QUANTITY = Decimal("0.100")


@dataclass(frozen=True)
class _BarObservation:
    ts_init: int
    clock_ns: int
    close: Decimal


@dataclass(frozen=True)
class _FillObservation:
    order_type: OrderType
    price: Decimal
    quantity: Decimal
    ts_event: int
    ts_init: int


@dataclass(frozen=True)
class _MarketResult:
    bars: tuple[_BarObservation, ...]
    fills: tuple[_FillObservation, ...]
    submit_ts: int


@dataclass(frozen=True)
class _ProtectiveResult:
    fills: tuple[_FillObservation, ...]
    stop_status: OrderStatus
    target_status: OrderStatus


class _MarketProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class _MarketProbe(Strategy):
    def __init__(self, config: _MarketProbeConfig) -> None:
        super().__init__(config)
        self.bars: list[_BarObservation] = []
        self.fills: list[_FillObservation] = []
        self.submit_ts = 0
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self.bars.append(
            _BarObservation(
                ts_init=bar.ts_init,
                clock_ns=self.clock.timestamp_ns(),
                close=bar.close.as_decimal(),
            )
        )
        if self._sent:
            return

        instrument = self.cache.instrument(self.config.instrument_id)
        assert instrument is not None
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(TRADE_QUANTITY),
            time_in_force=TimeInForce.GTC,
        )
        self._sent = True
        self.submit_ts = order.ts_init
        self.submit_order(order)

    def on_order_filled(self, event: OrderFilled) -> None:
        self.fills.append(_fill_observation(event))


class _ProtectiveProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    stop_price: str
    target_price: str


class _ProtectiveProbe(Strategy):
    def __init__(self, config: _ProtectiveProbeConfig) -> None:
        super().__init__(config)
        self.fills: list[_FillObservation] = []
        self.entry_order: Order | None = None
        self.stop_order: Order | None = None
        self.target_order: Order | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        del bar
        if self.entry_order is not None:
            return

        instrument = self.cache.instrument(self.config.instrument_id)
        assert instrument is not None
        self.entry_order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(TRADE_QUANTITY),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(self.entry_order)

    def on_order_filled(self, event: OrderFilled) -> None:
        self.fills.append(_fill_observation(event))
        instrument = self.cache.instrument(self.config.instrument_id)
        assert instrument is not None

        if (
            self.entry_order is not None
            and event.client_order_id == self.entry_order.client_order_id
        ):
            self.stop_order = self.order_factory.stop_market(
                instrument_id=instrument.id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(TRADE_QUANTITY),
                trigger_price=instrument.make_price(Decimal(self.config.stop_price)),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
            )
            self.target_order = self.order_factory.limit(
                instrument_id=instrument.id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(TRADE_QUANTITY),
                price=instrument.make_price(Decimal(self.config.target_price)),
                time_in_force=TimeInForce.GTC,
                post_only=False,
                reduce_only=True,
            )
            self.submit_order(self.stop_order)
            self.submit_order(self.target_order)
            return

        if self.stop_order is not None and event.client_order_id == self.stop_order.client_order_id:
            if self.target_order is not None and self.target_order.is_open:
                self.cancel_order(self.target_order)
        elif (
            self.target_order is not None
            and event.client_order_id == self.target_order.client_order_id
            and self.stop_order is not None
            and self.stop_order.is_open
        ):
            self.cancel_order(self.stop_order)


def test_ccxt_open_time_survives_ingestion_and_maps_to_binance_close(
    tmp_path: Path,
) -> None:
    """CCXT drops closeTime; repo RAW keeps openTime until this boundary."""

    close_time_ms = FIRST_OPEN_MS + HOUR_MS - 1
    raw_binance_kline = [
        FIRST_OPEN_MS,
        "1000.0",
        "1010.0",
        "990.0",
        "1005.0",
        "10.000",
        close_time_ms,
        "10000.0",
        10,
        "5.000",
        "5000.0",
        "0",
    ]
    normalized = ccxt.binance().parse_ohlcv(raw_binance_kline, {"inverse": False})
    assert normalized[0] == FIRST_OPEN_MS

    raw_path = tmp_path / "BTC_USDT-1h.csv"
    frame = pd.DataFrame(
        [normalized],
        columns=["ts", "Open", "High", "Low", "Close", "Volume"],
    )
    frame["datetime"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    _save_append(raw_path, frame)
    persisted = pd.read_csv(raw_path)
    assert int(persisted.loc[0, "ts"]) == FIRST_OPEN_MS

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    bar_type = _bar_type(instrument)
    bar = ccxt_ohlcv_to_nautilus_bar(
        normalized,
        instrument=instrument,
        bar_type=bar_type,
        interval_ms=HOUR_MS,
    )
    expected_close_ns = close_time_ms * MILLISECONDS_TO_NANOSECONDS
    assert expected_close_ns == binance_bar_close_ns(FIRST_OPEN_MS, HOUR_MS)
    assert bar.ts_event == expected_close_ns
    assert bar.ts_init == expected_close_ns
    next_open_ns = (FIRST_OPEN_MS + HOUR_MS) * MILLISECONDS_TO_NANOSECONDS
    assert next_open_ns - bar.ts_init == MILLISECONDS_TO_NANOSECONDS


def test_zero_latency_on_bar_market_fills_same_bar_close() -> None:
    """The zero-latency behavior is on-close equivalence, not next-open research."""

    bars, instrument, bar_type = _bars(
        [
            (1000, 1100, 900, 1050),
            (1200, 1250, 1180, 1240),
        ]
    )
    result = _run_market_probe(
        bars,
        instrument,
        bar_type,
        P3ExecutionProfile.EQUIVALENCE_ON_CLOSE_V1,
    )

    assert result.bars[0].clock_ns == bars[0].ts_init
    assert result.submit_ts == bars[0].ts_init
    assert result.fills == (
        _FillObservation(
            order_type=OrderType.MARKET,
            price=Decimal("1050"),
            quantity=TRADE_QUANTITY,
            ts_event=bars[0].ts_init,
            ts_init=bars[0].ts_init,
        ),
    )


def test_positive_latency_h1_bar_only_fills_next_bar_close_not_open() -> None:
    """One nanosecond defers the market order to the next H1 Close event."""

    bars, instrument, bar_type = _bars(
        [
            (1000, 1020, 990, 1010),
            (1200, 1250, 1180, 1240),
            (1300, 1350, 1280, 1340),
        ]
    )
    result = _run_market_probe(
        bars,
        instrument,
        bar_type,
        P3ExecutionProfile.RESEARCH_CAUSAL_NEXT_CLOSE_V1,
    )

    assert result.submit_ts == bars[0].ts_init
    assert result.fills[0].price == Decimal("1240")
    assert result.fills[0].price != Decimal("1200")  # next Open is not available as a fill rule
    assert result.fills[0].ts_event == bars[1].ts_init
    assert result.fills[0].ts_init == bars[1].ts_init


def test_stop_market_gapping_beyond_trigger_fills_at_gap_open() -> None:
    bars, instrument, bar_type = _bars(
        [
            (1000, 1010, 990, 1000),
            (900, 920, 850, 880),
        ]
    )
    result = _run_protective_probe(
        bars,
        instrument,
        bar_type,
        adaptive=False,
        stop_price="950",
        target_price="1100",
    )

    assert [fill.order_type for fill in result.fills] == [OrderType.MARKET, OrderType.STOP_MARKET]
    assert result.fills[1].price == Decimal("900")
    assert result.fills[1].price != Decimal("950")
    assert result.fills[1].ts_event == bars[1].ts_init
    assert result.stop_status is OrderStatus.FILLED
    assert result.target_status is OrderStatus.CANCELED


@pytest.mark.parametrize(
    ("adaptive", "ambiguous_bar", "expected_exit"),
    [
        pytest.param(False, (1000, 1100, 940, 1000), OrderType.LIMIT, id="fixed-high-first"),
        pytest.param(
            True, (1000, 1100, 940, 1000), OrderType.STOP_MARKET, id="adaptive-low-closer"
        ),
        pytest.param(True, (1000, 1060, 900, 1000), OrderType.LIMIT, id="adaptive-high-closer"),
        pytest.param(
            True, (1000, 1050, 950, 1000), OrderType.STOP_MARKET, id="adaptive-tie-low-first"
        ),
    ],
)
def test_same_bar_tp_sl_precedence(
    adaptive: bool,
    ambiguous_bar: tuple[int, int, int, int],
    expected_exit: OrderType,
) -> None:
    bars, instrument, bar_type = _bars(
        [
            (1000, 1010, 990, 1000),
            ambiguous_bar,
        ]
    )
    result = _run_protective_probe(
        bars,
        instrument,
        bar_type,
        adaptive=adaptive,
        stop_price="950",
        target_price="1050",
    )

    assert [fill.order_type for fill in result.fills] == [OrderType.MARKET, expected_exit]
    if expected_exit is OrderType.LIMIT:
        assert result.fills[1].price == Decimal("1050")
        assert result.target_status is OrderStatus.FILLED
        assert result.stop_status is OrderStatus.CANCELED
    else:
        assert result.fills[1].price == Decimal("950")
        assert result.stop_status is OrderStatus.FILLED
        assert result.target_status is OrderStatus.CANCELED


def test_future_bar_perturbation_does_not_change_earlier_intent_or_fill() -> None:
    common = [
        (1000, 1020, 990, 1010),
        (1200, 1250, 1180, 1240),
    ]
    bars_a, instrument_a, bar_type_a = _bars([*common, (1300, 1350, 1280, 1340)])
    bars_b, instrument_b, bar_type_b = _bars([*common, (2000, 2200, 1800, 2100)])

    result_a = _run_market_probe(
        bars_a,
        instrument_a,
        bar_type_a,
        P3ExecutionProfile.RESEARCH_CAUSAL_NEXT_CLOSE_V1,
    )
    result_b = _run_market_probe(
        bars_b,
        instrument_b,
        bar_type_b,
        P3ExecutionProfile.RESEARCH_CAUSAL_NEXT_CLOSE_V1,
    )

    assert result_a.submit_ts == result_b.submit_ts
    assert result_a.bars[:2] == result_b.bars[:2]
    assert result_a.fills == result_b.fills


def _fill_observation(event: OrderFilled) -> _FillObservation:
    return _FillObservation(
        order_type=event.order_type,
        price=event.last_px.as_decimal(),
        quantity=event.last_qty.as_decimal(),
        ts_event=event.ts_event,
        ts_init=event.ts_init,
    )


def _bar_type(instrument: Instrument) -> BarType:
    return BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")


def _bars(
    prices: list[tuple[int, int, int, int]],
) -> tuple[list[Bar], Instrument, BarType]:
    instrument = _zero_fee_btcusdt_perpetual()
    bar_type = _bar_type(instrument)
    bars = [
        ccxt_ohlcv_to_nautilus_bar(
            [FIRST_OPEN_MS + index * HOUR_MS, *ohlc, 10.000],
            instrument=instrument,
            bar_type=bar_type,
            interval_ms=HOUR_MS,
        )
        for index, ohlc in enumerate(prices)
    ]
    return bars, instrument, bar_type


def _zero_fee_btcusdt_perpetual() -> CryptoPerpetual:
    values = CryptoPerpetual.to_dict(TestInstrumentProvider.btcusdt_perp_binance())
    values["maker_fee"] = "0"
    values["taker_fee"] = "0"
    return CryptoPerpetual.from_dict(values)


def _engine(
    instrument: Instrument,
    *,
    profile: P3ExecutionProfile,
    adaptive: bool,
) -> BacktestEngine:
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
        base_currency=USDT,
        fill_model=FillModel(
            prob_fill_on_limit=1.0,
            prob_slippage=0.0,
            random_seed=7,
        ),
        latency_model=latency_model_for_profile(profile),
        use_reduce_only=True,
        use_message_queue=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=adaptive,
    )
    engine.add_instrument(instrument)
    return engine


def _run_market_probe(
    bars: list[Bar],
    instrument: Instrument,
    bar_type: BarType,
    profile: P3ExecutionProfile,
) -> _MarketResult:
    engine = _engine(instrument, profile=profile, adaptive=False)
    engine.add_data(bars)
    strategy = _MarketProbe(
        _MarketProbeConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            log_events=False,
            log_commands=False,
        )
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        return _MarketResult(
            bars=tuple(strategy.bars),
            fills=tuple(strategy.fills),
            submit_ts=strategy.submit_ts,
        )
    finally:
        engine.dispose()


def _run_protective_probe(
    bars: list[Bar],
    instrument: Instrument,
    bar_type: BarType,
    *,
    adaptive: bool,
    stop_price: str,
    target_price: str,
) -> _ProtectiveResult:
    engine = _engine(
        instrument,
        profile=P3ExecutionProfile.EQUIVALENCE_ON_CLOSE_V1,
        adaptive=adaptive,
    )
    engine.add_data(bars)
    strategy = _ProtectiveProbe(
        _ProtectiveProbeConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            stop_price=stop_price,
            target_price=target_price,
            log_events=False,
            log_commands=False,
        )
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        assert strategy.stop_order is not None
        assert strategy.target_order is not None
        return _ProtectiveResult(
            fills=tuple(strategy.fills),
            stop_status=strategy.stop_order.status,
            target_status=strategy.target_order.status,
        )
    finally:
        engine.dispose()
