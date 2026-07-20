"""
tests/test_microstructure.py

Testy ``algo_bot/microstructure.py``. Bez mockow — niezalezna wyrocznia
matematyczna: wartosci referencyjne policzone recznie z formuly Binance
(``Funding Amount = Notional * Funding Rate``) i z definicji slippage
(``notional * bps / 1e4``), NIE przez ponowne zastosowanie pandas (lekcja
xtrender 2026-06-11). Kazdy literal w assertach jest wyliczony w komentarzu.

See also:
    docs/adr/011-microstructure-adjustments.md (decyzje 1-14, defaults)
    docs/captains-log/2026-06-11.md (lekcja: niezalezna wyrocznia, nie self-read)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from algo_bot.microstructure import (
    LeveragedPosition,
    MaintenanceMarginTier,
    MarkPriceContext,
    MicrostructureConfig,
    apply_microstructure,
    first_liquidation_event,
    funding_cost_for_trade,
    funding_flows_for_trade,
    liquidation_check,
    liquidation_price,
    maintenance_margin_tiers_from_bybit,
    resolve_funding,
    settlements_in_window,
    slippage_cost,
    synthetic_funding_series,
)

# ============================================================================
# Helpery (deterministyczne, bez logiki produkcyjnej)
# ============================================================================


def _hourly_index(n: int, start: str = "2024-01-01", tz: str | None = None) -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="h", tz=tz)


def _ts(value: str, tz: str | None = None) -> pd.Timestamp:
    t = pd.Timestamp(value)
    return t.tz_localize(tz) if tz else t


def _one_trade(
    *,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    entry_price: float,
    exit_price: float,
    size: float,
    pnl: float,
) -> pd.DataFrame:
    """Buduje jednowierszowy trades DataFrame w schemacie backtesting.py."""
    return pd.DataFrame(
        {
            "Size": [size],
            "EntryPrice": [entry_price],
            "ExitPrice": [exit_price],
            "EntryTime": [entry_time],
            "ExitTime": [exit_time],
            "PnL": [pnl],
        }
    )


# ============================================================================
# slippage_cost — czysta arytmetyka
# ============================================================================


class TestSlippageCost:
    def test_one_bp_on_15k(self):
        # 15000 * 1.0 / 1e4 = 1.5
        assert slippage_cost(15_000.0, 1.0) == pytest.approx(1.5)

    def test_five_bps_on_20k(self):
        # 20000 * 5.0 / 1e4 = 10.0
        assert slippage_cost(20_000.0, 5.0) == pytest.approx(10.0)

    def test_negative_notional_uses_abs(self):
        # |-15000| * 1.0 / 1e4 = 1.5
        assert slippage_cost(-15_000.0, 1.0) == pytest.approx(1.5)

    def test_zero_slip(self):
        assert slippage_cost(15_000.0, 0.0) == 0.0


# ============================================================================
# settlements_in_window — konwencja (entry, exit]
# ============================================================================


class TestSettlementsInWindow:
    def _grid(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(
            [
                _ts("2024-01-01 00:00"),
                _ts("2024-01-01 08:00"),
                _ts("2024-01-01 16:00"),
                _ts("2024-01-02 00:00"),
            ]
        )

    def test_interior_window(self):
        # (02:00, 20:00] → 08:00, 16:00 (00:00 wykluczone bo nie > entry)
        got = settlements_in_window(_ts("2024-01-01 02:00"), _ts("2024-01-01 20:00"), self._grid())
        assert list(got) == [_ts("2024-01-01 08:00"), _ts("2024-01-01 16:00")]

    def test_boundary_entry_exclusive_exit_inclusive(self):
        # (08:00, 16:00] → tylko 16:00 (entry==08 wykluczone, exit==16 wliczone)
        got = settlements_in_window(_ts("2024-01-01 08:00"), _ts("2024-01-01 16:00"), self._grid())
        assert list(got) == [_ts("2024-01-01 16:00")]

    def test_full_day_includes_inclusive_exit(self):
        # (00:00, 2024-01-02 00:00] → 08,16, oraz 01-02 00:00 (exit inclusive)
        got = settlements_in_window(_ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), self._grid())
        assert list(got) == [
            _ts("2024-01-01 08:00"),
            _ts("2024-01-01 16:00"),
            _ts("2024-01-02 00:00"),
        ]

    def test_no_settlement_in_window(self):
        # (09:00, 15:00] → brak (nastepny settlement 16:00 > 15:00)
        got = settlements_in_window(_ts("2024-01-01 09:00"), _ts("2024-01-01 15:00"), self._grid())
        assert len(got) == 0


# ============================================================================
# funding_cost_for_trade / funding_flows_for_trade — formula Binance
# ============================================================================


class TestFundingCost:
    def _funding(self, rate_16: float = 0.0002) -> pd.Series:
        # settlementy 08:00 (0.01%) i 16:00 (rate_16)
        return pd.Series(
            [0.0001, rate_16],
            index=pd.DatetimeIndex([_ts("2024-01-01 08:00"), _ts("2024-01-01 16:00")]),
            name="funding_rate",
        )

    def _mark(self) -> pd.Series:
        # mark = Close; rosnie w czasie zeby przetestowac wycene per settlement
        return pd.Series(
            [29_000.0, 30_000.0, 40_000.0, 41_000.0],
            index=pd.DatetimeIndex(
                [
                    _ts("2024-01-01 00:00"),
                    _ts("2024-01-01 08:00"),
                    _ts("2024-01-01 16:00"),
                    _ts("2024-01-01 20:00"),
                ]
            ),
        )

    def test_long_pays_positive_funding(self):
        # long, size 0.5, okno (00:00, 20:00] → settlementy 08:00, 16:00
        #   08:00: +1 * 0.5 * mark(30000) * 0.0001 = 1.5
        #   16:00: +1 * 0.5 * mark(40000) * 0.0002 = 4.0
        #   total = 5.5, n = 2
        total, n = funding_cost_for_trade(
            side="long",
            size=0.5,
            entry_time=_ts("2024-01-01 00:00"),
            exit_time=_ts("2024-01-01 20:00"),
            funding=self._funding(),
            mark=self._mark(),
        )
        assert total == pytest.approx(5.5)
        assert n == 2

    def test_short_receives_positive_funding(self):
        # short → side_sign -1 → total = -5.5 (otrzymuje)
        total, n = funding_cost_for_trade(
            side="short",
            size=0.5,
            entry_time=_ts("2024-01-01 00:00"),
            exit_time=_ts("2024-01-01 20:00"),
            funding=self._funding(),
            mark=self._mark(),
        )
        assert total == pytest.approx(-5.5)
        assert n == 2

    def test_long_negative_rate_is_credit(self):
        # rate_16 = -0.0002 → 16:00: 0.5 * 40000 * -0.0002 = -4.0
        #   total = 1.5 + (-4.0) = -2.5
        total, n = funding_cost_for_trade(
            side="long",
            size=0.5,
            entry_time=_ts("2024-01-01 00:00"),
            exit_time=_ts("2024-01-01 20:00"),
            funding=self._funding(rate_16=-0.0002),
            mark=self._mark(),
        )
        assert total == pytest.approx(-2.5)
        assert n == 2

    def test_no_settlement_zero_cost(self):
        total, n = funding_cost_for_trade(
            side="long",
            size=0.5,
            entry_time=_ts("2024-01-01 09:00"),
            exit_time=_ts("2024-01-01 15:00"),
            funding=self._funding(),
            mark=self._mark(),
        )
        assert total == 0.0
        assert n == 0

    def test_flows_carry_timestamps(self):
        flows = funding_flows_for_trade(
            side="long",
            size=0.5,
            entry_time=_ts("2024-01-01 00:00"),
            exit_time=_ts("2024-01-01 20:00"),
            funding=self._funding(),
            mark=self._mark(),
        )
        assert [ts for ts, _ in flows] == [_ts("2024-01-01 08:00"), _ts("2024-01-01 16:00")]
        assert [round(a, 6) for _, a in flows] == [1.5, 4.0]

    def test_empty_funding(self):
        total, n = funding_cost_for_trade(
            side="long",
            size=0.5,
            entry_time=_ts("2024-01-01 00:00"),
            exit_time=_ts("2024-01-01 20:00"),
            funding=pd.Series(dtype=float),
            mark=self._mark(),
        )
        assert total == 0.0
        assert n == 0


# ============================================================================
# synthetic_funding_series
# ============================================================================


class TestSyntheticFundingSeries:
    def test_one_day_grid(self):
        # [01-01 00:00, 01-02 00:00], hours 00/08/16 →
        #   01-01 00,08,16 + 01-02 00 (exit inclusive) = 4 settlementy
        s = synthetic_funding_series(
            _ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), 0.0001, (0, 8, 16)
        )
        assert len(s) == 4
        assert (s.to_numpy() == 0.0001).all()
        assert s.index[0] == _ts("2024-01-01 00:00")
        assert s.index[-1] == _ts("2024-01-02 00:00")

    def test_empty_when_end_before_start(self):
        s = synthetic_funding_series(
            _ts("2024-01-02 00:00"), _ts("2024-01-01 00:00"), 0.0001, (0, 8, 16)
        )
        assert s.empty


# ============================================================================
# resolve_funding — hybrid historical/synthetic (Decyzja 6c)
# ============================================================================


class TestResolveFunding:
    def test_none_source_empty(self):
        cfg = MicrostructureConfig(funding_source="none")
        s = resolve_funding(None, _ts("2024-01-01"), _ts("2024-01-02"), cfg)
        assert s.empty

    def test_synthetic_source(self):
        cfg = MicrostructureConfig(funding_source="synthetic", funding_rate_synthetic=0.0001)
        s = resolve_funding(None, _ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), cfg)
        expected = synthetic_funding_series(
            _ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), 0.0001, (0, 8, 16)
        )
        assert s.index.equals(expected.index)
        assert np.allclose(s.to_numpy(), expected.to_numpy())

    def test_historical_missing_falls_back_to_synthetic(self, caplog):
        cfg = MicrostructureConfig(funding_source="historical", funding_rate_synthetic=0.0001)
        with caplog.at_level(logging.WARNING, logger="algo_bot.microstructure"):
            s = resolve_funding(None, _ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), cfg)
        assert not s.empty
        assert (s.to_numpy() == 0.0001).all()
        assert any("missing" in rec.message for rec in caplog.records)

    def test_historical_full_coverage_no_synthetic(self, caplog):
        # historia pokrywa caly grid wartoscia 0.0005 (rozna od synthetic 0.0001)
        hist = synthetic_funding_series(
            _ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), 0.0005, (0, 8, 16)
        )
        cfg = MicrostructureConfig(funding_source="historical", funding_rate_synthetic=0.0001)
        with caplog.at_level(logging.WARNING, logger="algo_bot.microstructure"):
            s = resolve_funding(hist, _ts("2024-01-01 00:00"), _ts("2024-01-02 00:00"), cfg)
        assert len(s) == len(hist)
        assert (s.to_numpy() == 0.0005).all()  # historical, nie synthetic
        assert not any("partial" in rec.message for rec in caplog.records)

    def test_historical_partial_fills_gap(self, caplog):
        # historia tylko 01-02 (00,08,16) = 0.0005; zakres [01-01, 01-03]
        hist = pd.Series(
            [0.0005, 0.0005, 0.0005],
            index=pd.DatetimeIndex(
                [
                    _ts("2024-01-02 00:00"),
                    _ts("2024-01-02 08:00"),
                    _ts("2024-01-02 16:00"),
                ]
            ),
            name="funding_rate",
        )
        cfg = MicrostructureConfig(funding_source="historical", funding_rate_synthetic=0.0001)
        with caplog.at_level(logging.WARNING, logger="algo_bot.microstructure"):
            s = resolve_funding(hist, _ts("2024-01-01 00:00"), _ts("2024-01-03 00:00"), cfg)
        # hist (3) + synthetic gap: 01-01 00/08/16 + 01-03 00 = 4 → razem 7
        assert len(s) == 7
        assert s.loc[_ts("2024-01-02 08:00")] == pytest.approx(0.0005)  # historical
        assert s.loc[_ts("2024-01-01 08:00")] == pytest.approx(0.0001)  # synthetic gap
        assert any("partial" in rec.message for rec in caplog.records)


# ============================================================================
# apply_microstructure — overlay na equity (integracja warstwy, hand-computed)
# ============================================================================


class TestApplyMicrostructure:
    def _scenario(self, *, size: float = 0.5, funding: pd.Series | None = None):
        """Zwraca (equity_raw, trades, ohlcv, funding) dla jednego long trade'u.

        Trade: entry 02:00 @30000, exit 10:00 @31000, size 0.5, PnL 500.
        Funding (jesli podany): settlement 08:00 rate 0.0001, mark Close=30000.
        """
        idx = _hourly_index(24)  # 00:00..23:00
        equity_raw = pd.Series(1_000_000.0, index=idx)
        ohlcv = pd.DataFrame({"Close": 30_000.0}, index=idx)
        trades = _one_trade(
            entry_time=idx[2],  # 02:00
            exit_time=idx[10],  # 10:00
            entry_price=30_000.0,
            exit_price=31_000.0,
            size=size,
            pnl=500.0,
        )
        return equity_raw, trades, ohlcv, funding

    def _funding_at_8(self) -> pd.Series:
        return pd.Series(
            [0.0001], index=pd.DatetimeIndex([_ts("2024-01-01 08:00")]), name="funding_rate"
        )

    def test_slip_and_funding_breakdown(self):
        equity_raw, trades, ohlcv, _ = self._scenario()
        cfg = MicrostructureConfig(enabled=True, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw,
            trades=trades,
            ohlcv=ohlcv,
            funding=self._funding_at_8(),
            config=cfg,
        )
        tc = res.per_trade[0]
        # slip_entry = 0.5*30000*1/1e4 = 1.5 ; slip_exit = 0.5*31000*1/1e4 = 1.55
        assert tc.slip_cost_quote == pytest.approx(3.05)
        # funding 08:00: 0.5*30000*0.0001 = 1.5 (long pays)
        assert tc.funding_cost_quote == pytest.approx(1.5)
        assert tc.n_settlements == 1
        # pnl_post = 500 - 3.05 - 1.5 = 495.45
        assert tc.pnl_post == pytest.approx(495.45)
        assert res.total_slip_quote == pytest.approx(3.05)
        assert res.total_funding_quote == pytest.approx(1.5)
        assert res.trades_pnl_adjusted.iloc[0] == pytest.approx(495.45)

    def test_equity_overlay_timeline(self):
        equity_raw, trades, ohlcv, _ = self._scenario()
        cfg = MicrostructureConfig(enabled=True, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw,
            trades=trades,
            ohlcv=ohlcv,
            funding=self._funding_at_8(),
            config=cfg,
        )
        eq = res.equity_adjusted
        # bar 0 (00:00): brak kosztow → 1_000_000
        assert eq.iloc[0] == pytest.approx(1_000_000.0)
        # bar 2 (02:00): -slip_entry 1.5 → 999_998.5
        assert eq.iloc[2] == pytest.approx(999_998.5)
        # bar 8 (08:00): -slip_entry -funding = -3.0 → 999_997.0
        assert eq.iloc[8] == pytest.approx(999_997.0)
        # bar 23: -slip_entry -funding -slip_exit = -4.55 → 999_995.45
        assert eq.iloc[-1] == pytest.approx(999_995.45)

    def test_short_funding_is_credit(self):
        equity_raw, trades, ohlcv, _ = self._scenario(size=-0.5)
        cfg = MicrostructureConfig(enabled=True, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw,
            trades=trades,
            ohlcv=ohlcv,
            funding=self._funding_at_8(),
            config=cfg,
        )
        tc = res.per_trade[0]
        assert tc.side == "short"
        # short otrzymuje funding: -1.5
        assert tc.funding_cost_quote == pytest.approx(-1.5)
        # pnl_post = 500 - 3.05 - (-1.5) = 498.45
        assert tc.pnl_post == pytest.approx(498.45)

    def test_funding_none_slip_only(self):
        equity_raw, trades, ohlcv, _ = self._scenario()
        cfg = MicrostructureConfig(enabled=True, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw,
            trades=trades,
            ohlcv=ohlcv,
            funding=None,
            config=cfg,
        )
        tc = res.per_trade[0]
        assert tc.funding_cost_quote == 0.0
        assert tc.n_settlements == 0
        # pnl_post = 500 - 3.05 = 496.95
        assert tc.pnl_post == pytest.approx(496.95)
        assert res.equity_adjusted.iloc[-1] == pytest.approx(999_996.95)

    def test_disabled_returns_raw(self):
        equity_raw, trades, ohlcv, _ = self._scenario()
        cfg = MicrostructureConfig(enabled=False, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw,
            trades=trades,
            ohlcv=ohlcv,
            funding=self._funding_at_8(),
            config=cfg,
        )
        assert res.per_trade == ()
        assert res.total_slip_quote == 0.0
        assert res.total_funding_quote == 0.0
        assert np.allclose(res.equity_adjusted.to_numpy(), equity_raw.to_numpy())
        assert res.trades_pnl_adjusted.iloc[0] == pytest.approx(500.0)

    def test_no_trades_returns_raw(self):
        idx = _hourly_index(24)
        equity_raw = pd.Series(1_000_000.0, index=idx)
        ohlcv = pd.DataFrame({"Close": 30_000.0}, index=idx)
        empty = pd.DataFrame({"Size": [], "EntryPrice": [], "ExitPrice": [], "PnL": []})
        cfg = MicrostructureConfig(enabled=True, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw, trades=empty, ohlcv=ohlcv, funding=None, config=cfg
        )
        assert res.per_trade == ()
        assert np.allclose(res.equity_adjusted.to_numpy(), equity_raw.to_numpy())

    def test_slip_bps_five_scales(self):
        equity_raw, trades, ohlcv, _ = self._scenario()
        cfg = MicrostructureConfig(enabled=True, slip_bps=5.0)
        res = apply_microstructure(
            equity_raw=equity_raw, trades=trades, ohlcv=ohlcv, funding=None, config=cfg
        )
        # slip = 5x: 0.5*30000*5/1e4 + 0.5*31000*5/1e4 = 7.5 + 7.75 = 15.25
        assert res.per_trade[0].slip_cost_quote == pytest.approx(15.25)


# ============================================================================
# Tz handling — wynik niezalezny od strefy wejscia (tz-aware == tz-naive)
# ============================================================================


class TestTzHandling:
    def test_tz_aware_matches_naive(self):
        idx = _hourly_index(24, tz="UTC")
        equity_raw = pd.Series(1_000_000.0, index=idx)
        ohlcv = pd.DataFrame({"Close": 30_000.0}, index=idx)
        trades = _one_trade(
            entry_time=idx[2],
            exit_time=idx[10],
            entry_price=30_000.0,
            exit_price=31_000.0,
            size=0.5,
            pnl=500.0,
        )
        funding = pd.Series(
            [0.0001],
            index=pd.DatetimeIndex([_ts("2024-01-01 08:00", tz="UTC")]),
            name="funding_rate",
        )
        cfg = MicrostructureConfig(enabled=True, slip_bps=1.0)
        res = apply_microstructure(
            equity_raw=equity_raw, trades=trades, ohlcv=ohlcv, funding=funding, config=cfg
        )
        # identyczne liczby jak w wariancie tz-naive
        assert res.equity_adjusted.iloc[-1] == pytest.approx(999_995.45)
        assert res.per_trade[0].funding_cost_quote == pytest.approx(1.5)
        # indeks znormalizowany do tz-naive
        assert res.equity_adjusted.index.tz is None


# ============================================================================
# Bybit mark-price basis — niezależne ręczne wartości referencyjne
# ============================================================================


def test_bybit_isolated_liquidation_reference_and_crossing() -> None:
    position = LeveragedPosition(
        position_id="long-1",
        side="long",
        quantity=1.0,
        entry_price=40_000.0,
        leverage=50.0,
        extra_margin=3_000.0,
    )
    # Bybit UTA isolated formula:
    # numerator = 40000 - 800 - 3000/(1-0.00055) = 36198.349...
    # denominator = 1 * (1-0.005) = 0.995
    # LP = 36380.25 (official worked example, rounding to cents).
    threshold = liquidation_price(position, 0.005, taker_fee_rate=0.00055)
    assert threshold == pytest.approx(36_380.25, abs=0.01)
    assert liquidation_check(position, 36_381.0, 0.005) is False

    event = liquidation_check(
        position,
        36_000.0,
        0.005,
        observed_at=pd.Timestamp("2024-01-01T02:00:00Z"),
        source="handcomputed-mark-h1",
    )
    assert event is not False
    assert event.mark_price == 36_000.0
    assert event.liquidation_price == pytest.approx(36_380.25, abs=0.01)


def test_fully_collateralized_long_has_no_positive_liquidation_crossing() -> None:
    position = LeveragedPosition(
        position_id="one-x-long",
        side="long",
        quantity=1.0,
        entry_price=100.0,
        leverage=1.0,
    )

    with pytest.raises(ValueError, match="liquidation price musi być dodatni"):
        liquidation_price(position, 0.005)
    assert liquidation_check(position, 0.01, 0.005) is False


def test_mark_price_context_is_causal_and_emits_first_h1_range_crossing() -> None:
    index = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    bars = pd.DataFrame(
        {
            "Open": [40_000.0, 39_000.0, 37_000.0],
            "High": [40_200.0, 39_100.0, 37_200.0],
            "Low": [39_800.0, 36_000.0, 35_500.0],
            "Close": [40_000.0, 37_000.0, 36_000.0],
        },
        index=index,
    )
    context = MarkPriceContext(
        symbol="BTCUSDT",
        exchange="bybit",
        timeframe="1h",
        bars=bars,
        source="handcomputed-bybit-mark-h1",
        maintenance_margin_tiers=(MaintenanceMarginTier(None, 0.005),),
    )
    with pytest.raises(LookupError):
        context.completed_bar_at(pd.Timestamp("2024-01-01T00:59:59Z"))
    assert context.completed_bar_at(pd.Timestamp("2024-01-01T01:00:00Z")).close == 40_000.0

    position = LeveragedPosition("long-2", "long", 1.0, 40_000.0, 50.0, 3_000.0)
    event = first_liquidation_event(
        position,
        context,
        pd.Timestamp("2024-01-01T01:00:00Z"),
        pd.Timestamp("2024-01-01T03:00:00Z"),
    )
    assert event is not None
    assert event.observed_at == pd.Timestamp("2024-01-01T02:00:00Z")
    assert event.mark_price == 36_000.0


def test_bybit_risk_tiers_parse_empty_first_deduction() -> None:
    tiers = maintenance_margin_tiers_from_bybit(
        [
            {
                "riskLimitValue": "2000000",
                "maintenanceMargin": "0.5",
                "mmDeduction": "",
            },
            {
                "riskLimitValue": "2600000",
                "maintenanceMargin": "0.56",
                "mmDeduction": "1200",
            },
        ]
    )
    assert tiers == (
        MaintenanceMarginTier(2_000_000.0, 0.005, 0.0),
        MaintenanceMarginTier(2_600_000.0, 0.0056, 1_200.0),
    )
