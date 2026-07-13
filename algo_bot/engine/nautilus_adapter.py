"""Tier-1 ``StrategyBase`` compatibility adapter for NautilusTrader.

This module has one deliberately narrow purpose: run a simple, legacy
``StrategyBase`` market-entry/market-exit strategy through the real
``BacktestEngine`` so that its decision and execution streams can be compared
with the pinned ``backtesting.py`` baseline.

The supported equivalence profile is intentionally smaller than the complete
``Signal`` surface:

* one NETTING position on one instrument;
* explicit, positive whole-unit quantities;
* market enter and full market exit only;
* close-timestamped external bars, zero latency, zero fees, zero funding, and
  deterministic zero-slippage fills; and
* no stops, targets, trailing logic, reversals, or pyramiding.

Unsupported behavior fails loudly.  In particular, this adapter is not the MMS
v2 wrapper and must never host its position-management state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.backtest.results import BacktestResult as NautilusBacktestResult
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency, Money, Quantity
from nautilus_trader.trading.strategy import Strategy as NautilusStrategy

from algo_bot.engine.nautilus_poc import (
    P3ExecutionProfile,
    ccxt_ohlcv_to_nautilus_bar,
    latency_model_for_profile,
)
from algo_bot.strategy_base import Signal, StrategyBase

OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class Tier1CompatibilityError(ValueError):
    """Raised when a signal is outside the preregistered Tier-1 profile."""


class Tier1IntentKind(StrEnum):
    """The only two order intents supported by the Tier-1 adapter."""

    ENTER_MARKET = "ENTER_MARKET"
    EXIT_MARKET = "EXIT_MARKET"


@dataclass(frozen=True)
class Tier1EquivalenceTolerances:
    """Acceptance thresholds frozen before the cross-engine fixture is run."""

    timestamp_ns: int
    fill_price_ticks: Decimal
    final_equity_abs: Decimal
    total_pnl_abs: Decimal


# Preregistered before executing tests/test_nautilus_adapter.py.  Tick-aligned
# integer fixture prices permit a stronger 0-tick gate than the <=1 tick ceiling
# requested by the session brief.  USDT accounting has eight decimal places.
TIER1_EQUIVALENCE_TOLERANCES = Tier1EquivalenceTolerances(
    timestamp_ns=0,
    fill_price_ticks=Decimal("0"),
    final_equity_abs=Decimal("0.00000001"),
    total_pnl_abs=Decimal("0.00000001"),
)


@dataclass(frozen=True)
class PreparedTier1Data:
    """A single causal input shared by both engines in an equivalence test."""

    bars: tuple[Bar, ...]
    close_frame: pd.DataFrame


@dataclass(frozen=True)
class Tier1Decision:
    """One invocation of ``StrategyBase.on_bar`` at a canonical close time."""

    ts_init_ns: int
    action: str | None
    side: str | None
    size: Decimal | None


@dataclass(frozen=True)
class Tier1Intent:
    """A native market-order intent emitted from one supported decision."""

    ts_init_ns: int
    kind: Tier1IntentKind
    order_side: str
    quantity: Decimal
    client_order_id: str


@dataclass(frozen=True)
class Tier1EquityPoint:
    """Native account balance plus mark-to-close unrealized PnL."""

    ts_init_ns: int
    balance_total: Decimal
    unrealized_pnl: Decimal

    @property
    def equity(self) -> Decimal:
        """Return total marked equity for this bar close."""

        return self.balance_total + self.unrealized_pnl


@dataclass(frozen=True)
class NautilusAdapterResult:
    """Native P5 artifact designed to feed the richer P8 result later."""

    profile: str
    native_result: NautilusBacktestResult
    decisions: tuple[Tier1Decision, ...]
    intents: tuple[Tier1Intent, ...]
    equity: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    positions: pd.DataFrame
    account_events: pd.DataFrame
    final_equity: Decimal
    total_pnl: Decimal


class StrategyBaseAdapterConfig(StrategyConfig, frozen=True):
    """Nautilus configuration for the Tier-1 compatibility strategy."""

    instrument_id: InstrumentId
    bar_type: BarType


class NautilusStrategyBaseAdapter(NautilusStrategy):  # type: ignore[misc, unused-ignore]
    """Translate a deliberately small ``StrategyBase`` subset to native orders."""

    def __init__(
        self,
        config: StrategyBaseAdapterConfig,
        *,
        strategy_class: type[StrategyBase],
        params: object,
        close_frame: pd.DataFrame,
    ) -> None:
        super().__init__(config)
        self._strategy_class = strategy_class
        self._params = params
        self._close_frame = close_frame.copy(deep=True)
        self._algo = self._new_algo()
        self._instrument: Instrument | None = None
        self._settlement_currency: Currency | None = None
        self._cursor = 0
        self.decisions: list[Tier1Decision] = []
        self.intents: list[Tier1Intent] = []
        self.equity_points: list[Tier1EquityPoint] = []

    def _new_algo(self) -> StrategyBase:
        algo = self._strategy_class(self._params)
        if bool(getattr(algo.p, "allow_pyramiding", False)):
            raise Tier1CompatibilityError("Tier-1 equivalence does not support pyramiding")
        algo.precompute(self._close_frame)
        return algo

    def on_start(self) -> None:
        """Resolve the instrument and subscribe to its external bar stream."""

        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            raise Tier1CompatibilityError(
                f"Instrument {self.config.instrument_id} is absent from the Nautilus cache"
            )
        settlement_currency = instrument.get_settlement_currency()
        if settlement_currency is None:
            raise Tier1CompatibilityError("Tier-1 requires a settlement currency")
        self._instrument = instrument
        self._settlement_currency = settlement_currency
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """Expose a growing closed-bar prefix and translate a supported signal."""

        if self._instrument is None or self._settlement_currency is None:
            raise Tier1CompatibilityError("Adapter received a bar before initialization")
        if self._cursor >= len(self._close_frame):
            raise Tier1CompatibilityError("Nautilus delivered more bars than prepared")

        expected_ts = int(self._close_frame.index[self._cursor].value)
        if bar.ts_init != expected_ts:
            raise Tier1CompatibilityError(
                f"Bar/frame timestamp mismatch: bar={bar.ts_init}, frame={expected_ts}"
            )

        self._cursor += 1
        self._record_equity(bar)

        # backtesting.py starts Wrapped.next() at bar index 1 because the
        # StrategyBase wrapper registers no `self.I` indicators.  Keeping the
        # first bar as warm-up makes the complete decision streams equivalent,
        # including stateful strategies such as DCA.
        if self._cursor == 1:
            return

        signal = self._algo.on_bar(self._close_frame.iloc[: self._cursor])
        decision = Tier1Decision(
            ts_init_ns=bar.ts_init,
            action=signal.action,
            side=signal.side,
            size=_optional_decimal(signal.size),
        )
        self.decisions.append(decision)
        self._validate_signal(signal)

        if signal.action == "enter":
            self._submit_entry(signal, bar.ts_init)
        elif signal.action == "exit":
            self._submit_exit(bar.ts_init)

    def _validate_signal(self, signal: Signal) -> None:
        if signal.action not in (None, "hold", "enter", "exit"):
            raise Tier1CompatibilityError(f"Unsupported Signal.action: {signal.action!r}")
        if signal.side not in (None, "long", "short"):
            raise Tier1CompatibilityError(f"Unsupported Signal.side: {signal.side!r}")
        if signal.tp_pct is not None or signal.sl_pct is not None:
            raise Tier1CompatibilityError("Tier-1 equivalence does not support Signal TP/SL fields")
        protected_keys = {"sl", "tp", "trail", "tp_has_priority"}
        if signal.meta and protected_keys.intersection(signal.meta):
            raise Tier1CompatibilityError(
                "Tier-1 equivalence does not support protective-order metadata"
            )

    def _submit_entry(self, signal: Signal, ts_init_ns: int) -> None:
        if self._instrument is None:
            raise Tier1CompatibilityError("Adapter instrument is not initialized")
        if signal.side not in ("long", "short"):
            raise Tier1CompatibilityError("Market entry requires an explicit long/short side")
        if not self.portfolio.is_flat(self.config.instrument_id):
            raise Tier1CompatibilityError(
                "Tier-1 equivalence rejects pyramiding and position reversal"
            )

        quantity = _explicit_whole_quantity(signal.size, self._instrument)
        order_side = OrderSide.BUY if signal.side == "long" else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
        )
        self.intents.append(
            Tier1Intent(
                ts_init_ns=ts_init_ns,
                kind=Tier1IntentKind.ENTER_MARKET,
                order_side=order_side.name,
                quantity=quantity.as_decimal(),
                client_order_id=str(order.client_order_id),
            )
        )
        self.submit_order(order)

    def _submit_exit(self, ts_init_ns: int) -> None:
        if self._instrument is None:
            raise Tier1CompatibilityError("Adapter instrument is not initialized")
        net_position = cast(Decimal, self.portfolio.net_position(self.config.instrument_id))
        if net_position == 0:
            return  # Matches the legacy wrapper: EXIT while flat is ignored.

        order_side = OrderSide.SELL if net_position > 0 else OrderSide.BUY
        quantity = self._instrument.make_qty(abs(net_position))
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
        )
        self.intents.append(
            Tier1Intent(
                ts_init_ns=ts_init_ns,
                kind=Tier1IntentKind.EXIT_MARKET,
                order_side=order_side.name,
                quantity=quantity.as_decimal(),
                client_order_id=str(order.client_order_id),
            )
        )
        self.submit_order(order)

    def _record_equity(self, bar: Bar) -> None:
        if self._settlement_currency is None:
            raise Tier1CompatibilityError("Adapter settlement currency is not initialized")
        account = self.portfolio.account(venue=self.config.instrument_id.venue)
        if account is None:
            raise Tier1CompatibilityError("Nautilus account is unavailable")
        balance = account.balance_total(self._settlement_currency)
        unrealized = self.portfolio.unrealized_pnl(
            self.config.instrument_id,
            price=bar.close,
            target_currency=self._settlement_currency,
        )
        if balance is None or unrealized is None:
            raise Tier1CompatibilityError("Nautilus could not calculate native equity")
        self.equity_points.append(
            Tier1EquityPoint(
                ts_init_ns=bar.ts_init,
                balance_total=balance.as_decimal(),
                unrealized_pnl=unrealized.as_decimal(),
            )
        )

    def on_stop(self) -> None:
        """Unsubscribe without force-closing; legacy finalize_trades is false."""

        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        """Reset retained state for a supported repeated BacktestEngine run."""

        self._algo = self._new_algo()
        self._instrument = None
        self._settlement_currency = None
        self._cursor = 0
        self.decisions.clear()
        self.intents.clear()
        self.equity_points.clear()


def prepare_tier1_ccxt_ohlcv(
    data: pd.DataFrame,
    *,
    instrument: Instrument,
    bar_type: BarType,
    interval_ms: int,
) -> PreparedTier1Data:
    """Convert a CCXT-open-timestamped frame into one shared causal input.

    The returned frame is indexed by Binance's inclusive close timestamp and
    reconstructed from the rounded Nautilus values.  Feeding that exact frame
    to the legacy wrapper prevents timestamp and instrument-precision drift in
    the equivalence fixture.
    """

    missing = set(OHLCV_COLUMNS).difference(data.columns)
    if missing:
        raise ValueError(f"OHLCV data is missing columns: {sorted(missing)}")
    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("OHLCV data must use a DatetimeIndex")
    if data.empty:
        raise ValueError("OHLCV data must not be empty")
    if data.index.tz is None:
        raise ValueError("CCXT OHLCV timestamps must be timezone-aware")
    if not data.index.is_monotonic_increasing or not data.index.is_unique:
        raise ValueError("CCXT OHLCV timestamps must be unique and increasing")
    if bar_type.instrument_id != instrument.id:
        raise ValueError("bar_type and instrument must have the same instrument_id")

    bars: list[Bar] = []
    for timestamp, row in data.loc[:, list(OHLCV_COLUMNS)].iterrows():
        # A DatetimeIndex guarantees Timestamp keys at runtime; pandas-stubs
        # conservatively types the key yielded by iterrows() as Hashable.
        open_ts = cast(pd.Timestamp, timestamp).tz_convert("UTC")
        if open_ts.value % 1_000_000 != 0:
            raise ValueError("CCXT OHLCV timestamps must have millisecond precision")
        bars.append(
            ccxt_ohlcv_to_nautilus_bar(
                [
                    open_ts.value // 1_000_000,
                    row["Open"],
                    row["High"],
                    row["Low"],
                    row["Close"],
                    row["Volume"],
                ],
                instrument=instrument,
                bar_type=bar_type,
                interval_ms=interval_ms,
            )
        )

    close_index = pd.to_datetime([bar.ts_init for bar in bars], unit="ns", utc=True)
    close_frame = pd.DataFrame(
        {
            "Open": [bar.open.as_double() for bar in bars],
            "High": [bar.high.as_double() for bar in bars],
            "Low": [bar.low.as_double() for bar in bars],
            "Close": [bar.close.as_double() for bar in bars],
            "Volume": [bar.volume.as_double() for bar in bars],
        },
        index=close_index,
    )
    close_frame.index.name = data.index.name or "datetime"
    return PreparedTier1Data(bars=tuple(bars), close_frame=close_frame)


def run_nautilus_strategy_base(
    *,
    prepared_data: PreparedTier1Data,
    instrument: Instrument,
    bar_type: BarType,
    strategy_class: type[StrategyBase],
    params: object,
    starting_balance: Decimal,
    random_seed: int = 7,
) -> NautilusAdapterResult:
    """Run the fixed Tier-1 on-close equivalence profile on BacktestEngine."""

    if starting_balance <= 0 or not starting_balance.is_finite():
        raise ValueError("starting_balance must be positive and finite")
    if bar_type.instrument_id != instrument.id:
        raise ValueError("bar_type and instrument must have the same instrument_id")
    if instrument.maker_fee != 0 or instrument.taker_fee != 0:
        raise Tier1CompatibilityError("Tier-1 equivalence requires a zero-fee instrument")
    settlement_currency = instrument.get_settlement_currency()
    if settlement_currency is None:
        raise Tier1CompatibilityError("Tier-1 requires a settlement currency")

    profile = P3ExecutionProfile.EQUIVALENCE_ON_CLOSE_V1
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=True,
        )
    )
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(starting_balance, settlement_currency)],
        base_currency=settlement_currency,
        default_leverage=Decimal("1"),
        fill_model=FillModel(
            prob_fill_on_limit=1.0,
            prob_slippage=0.0,
            random_seed=random_seed,
        ),
        latency_model=latency_model_for_profile(profile),
        use_position_ids=False,
        use_reduce_only=True,
        use_message_queue=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=False,
    )
    engine.add_instrument(instrument)
    engine.add_data(list(prepared_data.bars))
    adapter = NautilusStrategyBaseAdapter(
        StrategyBaseAdapterConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            oms_type="NETTING",
            log_events=False,
            log_commands=False,
        ),
        strategy_class=strategy_class,
        params=params,
        close_frame=prepared_data.close_frame,
    )
    engine.add_strategy(adapter)

    try:
        engine.run()
        native_result = cast(NautilusBacktestResult, engine.get_result())
        equity = _equity_frame(adapter.equity_points)
        if equity.empty:
            raise Tier1CompatibilityError("Nautilus produced no equity observations")
        final_equity = Decimal(str(equity["Equity"].iloc[-1]))
        return NautilusAdapterResult(
            profile=profile.value,
            native_result=native_result,
            decisions=tuple(adapter.decisions),
            intents=tuple(adapter.intents),
            equity=equity,
            orders=engine.trader.generate_orders_report().copy(deep=True),
            fills=engine.trader.generate_fills_report().copy(deep=True),
            positions=engine.trader.generate_positions_report().copy(deep=True),
            account_events=engine.trader.generate_account_report(instrument.id.venue).copy(
                deep=True
            ),
            final_equity=final_equity,
            total_pnl=final_equity - starting_balance,
        )
    finally:
        engine.dispose()


def _equity_frame(points: list[Tier1EquityPoint]) -> pd.DataFrame:
    index = pd.to_datetime([point.ts_init_ns for point in points], unit="ns", utc=True)
    frame = pd.DataFrame(
        {
            "BalanceTotal": [float(point.balance_total) for point in points],
            "UnrealizedPnL": [float(point.unrealized_pnl) for point in points],
            "Equity": [float(point.equity) for point in points],
        },
        index=index,
    )
    frame.index.name = "ts_init"
    return frame


def _optional_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value, "Signal.size")


def _explicit_whole_quantity(value: float | None, instrument: Instrument) -> Quantity:
    if value is None:
        raise Tier1CompatibilityError(
            "Signal.size=None is ambiguous (.9999 equity in backtesting.py); "
            "Tier-1 requires an explicit whole-unit quantity"
        )
    decimal = _decimal(value, "Signal.size")
    if decimal <= 0 or decimal != decimal.to_integral_value():
        raise Tier1CompatibilityError(
            "Tier-1 requires Signal.size to be a positive whole-unit quantity"
        )
    quantity = instrument.make_qty(decimal)
    if quantity.as_decimal() != decimal:
        raise Tier1CompatibilityError("Signal.size is not exactly representable by the instrument")
    return quantity


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise Tier1CompatibilityError(f"{field} must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise Tier1CompatibilityError(f"{field} must be numeric") from exc
    if not decimal.is_finite():
        raise Tier1CompatibilityError(f"{field} must be finite")
    return decimal
