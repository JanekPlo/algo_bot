"""P5 Tier-1 cross-engine equivalence and fail-fast scope tests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import pytest
from backtesting import Backtest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from algo_bot.engine.backtester import make_bt_wrapper
from algo_bot.engine.nautilus_adapter import (
    TIER1_EQUIVALENCE_TOLERANCES,
    NautilusAdapterResult,
    Tier1CompatibilityError,
    Tier1IntentKind,
    prepare_tier1_ccxt_ohlcv,
    run_nautilus_strategy_base,
)
from algo_bot.strategy_base import Signal, StrategyBase

HOUR_MS = 3_600_000
STARTING_BALANCE = Decimal("1000000")


@dataclass(frozen=True)
class _EquivalenceParams:
    trade_on_close: bool = True
    mode: str = "supported"
    allow_pyramiding: bool = False


class _EquivalenceStrategy(StrategyBase):
    """No-alpha scripted fixture with one explicit-unit round trip."""

    ParamSchema = _EquivalenceParams

    def __init__(self, params: object | None = None) -> None:
        super().__init__(params)
        self.observed: list[tuple[int, str | None, str | None, Decimal | None]] = []

    def on_bar(self, df: pd.DataFrame) -> Signal:
        if self.p.mode == "string_hold":
            signal = Signal(action="hold")
        elif len(df) == 2:
            if self.p.mode == "ambiguous_size":
                signal = Signal(action="enter", side="long")
            elif self.p.mode == "stop":
                signal = Signal(action="enter", side="long", size=1.0, sl_pct=0.02)
            else:
                signal = Signal(action="enter", side="long", size=1.0)
        elif len(df) == 3 and self.p.mode == "repeat_entry":
            signal = Signal(action="enter", side="long", size=1.0)
        elif len(df) == 4:
            signal = Signal(action="exit", side="long")
        else:
            signal = Signal()

        self.observed.append(
            (
                int(df.index[-1].value),
                signal.action,
                signal.side,
                None if signal.size is None else Decimal(str(signal.size)),
            )
        )
        return signal


def test_preregistered_market_on_close_cross_engine_equivalence() -> None:
    """Decision, intent and execution streams clear the frozen P5 tolerances."""

    instrument, bar_type, prepared = _prepared_fixture()
    params = _EquivalenceParams()

    wrapped = make_bt_wrapper(
        _EquivalenceStrategy,
        params,
        trade_on_close=True,
    )
    legacy_stats = Backtest(
        prepared.close_frame,
        wrapped,
        cash=float(STARTING_BALANCE),
        commission=0.0,
        trade_on_close=True,
        exclusive_orders=True,
        finalize_trades=False,
    ).run()
    native = run_nautilus_strategy_base(
        prepared_data=prepared,
        instrument=instrument,
        bar_type=bar_type,
        strategy_class=_EquivalenceStrategy,
        params=params,
        starting_balance=STARTING_BALANCE,
    )

    legacy_decisions = legacy_stats._strategy._algo.observed
    native_decisions = [
        (decision.ts_init_ns, decision.action, decision.side, decision.size)
        for decision in native.decisions
    ]
    assert native_decisions == legacy_decisions
    assert len(native.decisions) == 4  # first source bar is the shared warm-up
    assert [(d.action, d.side) for d in native.decisions if d.action is not None] == [
        ("enter", "long"),
        ("exit", "long"),
    ]

    expected_intent_timestamps = [
        int(prepared.close_frame.index[1].value),
        int(prepared.close_frame.index[3].value),
    ]
    assert [intent.ts_init_ns for intent in native.intents] == expected_intent_timestamps
    assert [intent.kind for intent in native.intents] == [
        Tier1IntentKind.ENTER_MARKET,
        Tier1IntentKind.EXIT_MARKET,
    ]
    assert [intent.order_side for intent in native.intents] == ["BUY", "SELL"]
    assert [intent.quantity for intent in native.intents] == [Decimal("1"), Decimal("1")]

    legacy_fills = _legacy_fills(legacy_stats._trades)
    native_fills = _native_fills(native)
    assert native_fills == legacy_fills
    assert len(native.orders) == len(native.fills) == len(legacy_fills) == 2
    assert set(native.orders["type"]) == {"MARKET"}
    assert list(native.orders.sort_values("ts_init")["side"]) == ["BUY", "SELL"]

    tolerances = TIER1_EQUIVALENCE_TOLERANCES
    assert tolerances.timestamp_ns == 0
    assert tolerances.fill_price_ticks == 0
    assert abs(native.final_equity - Decimal(str(legacy_stats["Equity Final [$]"]))) <= (
        tolerances.final_equity_abs
    )
    legacy_pnl = Decimal(str(legacy_stats._trades["PnL"].sum()))
    assert abs(native.total_pnl - legacy_pnl) <= tolerances.total_pnl_abs
    assert native.final_equity == Decimal("1000200.0")
    assert native.total_pnl == Decimal("200.0")
    assert len(native.equity) == len(prepared.close_frame)
    assert native.equity["Equity"].iloc[-1] == pytest.approx(1_000_200.0)
    assert len(native.positions) == 1
    assert native.positions.iloc[0]["side"] == "FLAT"


def test_explicit_engine_trade_on_close_controls_legacy_wrapper() -> None:
    """Runner execution policy overrides a contradictory strategy param."""

    params = _EquivalenceParams(trade_on_close=False)
    wrapped = make_bt_wrapper(
        _EquivalenceStrategy,
        params,
        trade_on_close=True,
    )
    assert wrapped.trade_on_close is True


def test_explicit_hold_action_is_a_noop() -> None:
    instrument, bar_type, prepared = _prepared_fixture()
    result = run_nautilus_strategy_base(
        prepared_data=prepared,
        instrument=instrument,
        bar_type=bar_type,
        strategy_class=_EquivalenceStrategy,
        params=_EquivalenceParams(mode="string_hold"),
        starting_balance=STARTING_BALANCE,
    )
    assert {decision.action for decision in result.decisions} == {"hold"}
    assert result.intents == ()
    assert result.orders.empty
    assert result.fills.empty


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param(
            _EquivalenceParams(mode="ambiguous_size"),
            "Signal.size=None is ambiguous",
            id="ambiguous-default-size",
        ),
        pytest.param(
            _EquivalenceParams(mode="stop"),
            "does not support Signal TP/SL",
            id="stop-field",
        ),
        pytest.param(
            _EquivalenceParams(mode="repeat_entry"),
            "rejects pyramiding",
            id="second-entry",
        ),
        pytest.param(
            _EquivalenceParams(allow_pyramiding=True),
            "does not support pyramiding",
            id="declared-pyramiding",
        ),
    ],
)
def test_unsupported_strategybase_surface_fails_loudly(
    params: _EquivalenceParams,
    message: str,
) -> None:
    instrument, bar_type, prepared = _prepared_fixture()
    with pytest.raises(Tier1CompatibilityError, match=message):
        run_nautilus_strategy_base(
            prepared_data=prepared,
            instrument=instrument,
            bar_type=bar_type,
            strategy_class=_EquivalenceStrategy,
            params=params,
            starting_balance=STARTING_BALANCE,
        )


def _prepared_fixture():
    instrument = _zero_fee_btcusdt_perpetual()
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")
    index = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC", name="datetime")
    closes = [50_000.0, 50_100.0, 50_200.0, 50_300.0, 50_400.0]
    source = pd.DataFrame(
        {
            "Open": closes,
            "High": [price + 10.0 for price in closes],
            "Low": [price - 10.0 for price in closes],
            "Close": closes,
            "Volume": [10.0] * len(closes),
        },
        index=index,
    )
    prepared = prepare_tier1_ccxt_ohlcv(
        source,
        instrument=instrument,
        bar_type=bar_type,
        interval_ms=HOUR_MS,
    )
    return instrument, bar_type, prepared


def _zero_fee_btcusdt_perpetual() -> CryptoPerpetual:
    values = CryptoPerpetual.to_dict(TestInstrumentProvider.btcusdt_perp_binance())
    values["maker_fee"] = "0"
    values["taker_fee"] = "0"
    return CryptoPerpetual.from_dict(values)


def _legacy_fills(trades: pd.DataFrame) -> list[tuple[int, str, Decimal, Decimal]]:
    assert len(trades) == 1
    trade = trades.iloc[0]
    quantity = Decimal(str(abs(int(trade["Size"]))))
    return [
        (
            int(pd.Timestamp(trade["EntryTime"]).value),
            "BUY",
            Decimal(str(trade["EntryPrice"])),
            quantity,
        ),
        (
            int(pd.Timestamp(trade["ExitTime"]).value),
            "SELL",
            Decimal(str(trade["ExitPrice"])),
            quantity,
        ),
    ]


def _native_fills(result: NautilusAdapterResult) -> list[tuple[int, str, Decimal, Decimal]]:
    fills = result.fills.sort_values("ts_event")
    return [
        (
            int(pd.Timestamp(row["ts_event"]).value),
            str(row["order_side"]),
            Decimal(str(row["last_px"])),
            Decimal(str(row["last_qty"])),
        )
        for _, row in fills.iterrows()
    ]
