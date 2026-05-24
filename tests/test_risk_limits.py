"""
tests/test_risk_limits.py

Deterministyczne testy ``algo_bot.risk.limits`` — pure unit tests + jeden
integration test który exercise'uje hook w ``run_backtest``.

Bez mocków (mindset rule #3) — fixture'y to ręcznie zbudowane sekwencje equity
i syntetyczny OHLCV. Reference values policzone na piechotę i wpisane do
asercji.

Strategia testowa: ``risk_breach_trigger`` — minimalna fake StrategyBase która
buy na pierwszym barze i hold-uje. Pozwala wymuszenie konkretnego DD przez
syntetyczne ceny.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from algo_bot.engine.backtester import run_backtest
from algo_bot.risk import (
    RiskBreach,
    RiskLimitBreached,
    RiskLimits,
    check_all,
    check_daily_loss,
    check_drawdown,
    check_positions,
    init_state,
    position_size,
    update_state,
)

# ============================================================================
# Pure unit tests — gates
# ============================================================================


def _ts(s: str) -> pd.Timestamp:
    """Helper: tz-aware Timestamp w UTC."""
    return pd.Timestamp(s, tz="UTC")


class TestCheckDrawdown:
    def test_returns_breach_when_dd_exceeds_threshold(self):
        # equity_peak=120, equity_now=80 → DD = (120-80)/120 = 0.333... > 0.30
        limits = RiskLimits(max_drawdown_pct=0.30)
        state = init_state(equity_start=100.0, ts=_ts("2024-01-01"), limits=limits)
        state = update_state(
            state, equity_now=120.0, ts=_ts("2024-01-02"), open_positions=1, limits=limits
        )

        breach = check_drawdown(state, equity_now=80.0, limits=limits)

        assert breach is not None
        assert breach.kind == "max_drawdown"
        assert breach.threshold == pytest.approx(0.30)
        # value to ujemny DD jako narracja "strata X%"
        assert breach.value == pytest.approx(-(40.0 / 120.0))

    def test_returns_none_when_within_threshold(self):
        # equity_peak=120, equity_now=100 → DD = 0.166... < 0.30
        limits = RiskLimits(max_drawdown_pct=0.30)
        state = init_state(equity_start=100.0, ts=_ts("2024-01-01"), limits=limits)
        state = update_state(
            state, equity_now=120.0, ts=_ts("2024-01-02"), open_positions=0, limits=limits
        )

        assert check_drawdown(state, equity_now=100.0, limits=limits) is None

    def test_returns_none_when_limit_disabled(self):
        limits = RiskLimits(max_drawdown_pct=None)
        state = init_state(equity_start=100.0, ts=_ts("2024-01-01"), limits=limits)

        # Nawet katastrofalny DD jest ignorowany gdy próg = None
        assert check_drawdown(state, equity_now=1.0, limits=limits) is None


class TestCheckDailyLoss:
    def test_returns_breach_when_daily_loss_exceeds(self):
        # daily_start=10_000, equity_now=9_400 → strata 6% > 5%
        limits = RiskLimits(daily_loss_pct=0.05)
        state = init_state(equity_start=10_000.0, ts=_ts("2024-01-01 12:00"), limits=limits)

        breach = check_daily_loss(
            state, equity_now=9_400.0, ts=_ts("2024-01-01 15:00"), limits=limits
        )

        assert breach is not None
        assert breach.kind == "daily_loss"
        assert breach.threshold == pytest.approx(0.05)
        assert breach.value == pytest.approx(-0.06)

    def test_returns_none_when_within_limit(self):
        limits = RiskLimits(daily_loss_pct=0.05)
        state = init_state(equity_start=10_000.0, ts=_ts("2024-01-01 12:00"), limits=limits)

        # 3% strata < 5%
        assert (
            check_daily_loss(state, equity_now=9_700.0, ts=_ts("2024-01-01 15:00"), limits=limits)
            is None
        )

    def test_returns_none_when_limit_disabled(self):
        limits = RiskLimits(daily_loss_pct=None)
        state = init_state(equity_start=10_000.0, ts=_ts("2024-01-01 12:00"), limits=limits)
        assert (
            check_daily_loss(state, equity_now=0.01, ts=_ts("2024-01-01 15:00"), limits=limits)
            is None
        )


class TestCheckPositions:
    def test_returns_breach_when_open_exceeds_cap(self):
        limits = RiskLimits(max_concurrent_positions=2)
        state = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)
        state = update_state(
            state, equity_now=10_000.0, ts=_ts("2024-01-01 01:00"), open_positions=3, limits=limits
        )

        breach = check_positions(state, limits=limits)

        assert breach is not None
        assert breach.kind == "max_positions"
        assert breach.value == pytest.approx(3.0)
        assert breach.threshold == pytest.approx(2.0)

    def test_returns_none_when_at_cap(self):
        # equal-to-cap nie jest breach (>, not >=). Klasyczna konwencja "max means
        # zezwalamy na dokładnie tyle".
        limits = RiskLimits(max_concurrent_positions=2)
        state = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)
        state = update_state(
            state, equity_now=10_000.0, ts=_ts("2024-01-01 01:00"), open_positions=2, limits=limits
        )

        assert check_positions(state, limits=limits) is None

    def test_returns_none_when_limit_disabled(self):
        limits = RiskLimits(max_concurrent_positions=None)
        state = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)
        state = update_state(
            state,
            equity_now=10_000.0,
            ts=_ts("2024-01-01 01:00"),
            open_positions=100,
            limits=limits,
        )

        assert check_positions(state, limits=limits) is None


# ============================================================================
# Pure unit tests — state management
# ============================================================================


class TestUpdateStateImmutability:
    def test_update_state_returns_new_instance(self):
        """update_state nie modyfikuje starej instancji (frozen dataclass)."""
        limits = RiskLimits(max_drawdown_pct=0.20)
        state_before = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)

        state_after = update_state(
            state_before,
            equity_now=12_000.0,
            ts=_ts("2024-01-01 01:00"),
            open_positions=1,
            limits=limits,
        )

        # Nowa instancja ma nowy peak; stara dalej ma 10_000
        assert state_after is not state_before
        assert state_after.equity_peak == pytest.approx(12_000.0)
        assert state_before.equity_peak == pytest.approx(10_000.0)

    def test_equity_peak_grows_monotonically(self):
        limits = RiskLimits()
        s = init_state(equity_start=100.0, ts=_ts("2024-01-01"), limits=limits)
        s = update_state(
            s, equity_now=120.0, ts=_ts("2024-01-01 01:00"), open_positions=1, limits=limits
        )
        s = update_state(
            s, equity_now=90.0, ts=_ts("2024-01-01 02:00"), open_positions=1, limits=limits
        )
        # Peak nie schodzi
        assert s.equity_peak == pytest.approx(120.0)


class TestDailyReset:
    def test_daily_start_resets_on_new_day_utc(self):
        limits = RiskLimits(daily_loss_pct=0.05, daily_reset_tz="UTC")
        s = init_state(equity_start=10_000.0, ts=_ts("2024-01-01 23:00"), limits=limits)

        # Bar w tym samym dniu — daily_start bez zmian
        s = update_state(
            s, equity_now=9_700.0, ts=_ts("2024-01-01 23:30"), open_positions=1, limits=limits
        )
        assert s.daily_start_equity == pytest.approx(10_000.0)

        # Bar następnego dnia (UTC) — reset, daily_start = bieżące equity
        s = update_state(
            s, equity_now=9_700.0, ts=_ts("2024-01-02 00:30"), open_positions=1, limits=limits
        )
        assert s.daily_start_equity == pytest.approx(9_700.0)

    def test_daily_reset_tz_warsaw_differs_from_utc(self):
        """Europe/Warsaw daily reset jest godzinę-dwie przesunięty względem UTC.
        Bar o 22:00 UTC = 23:00/00:00 Warsaw (CET/CEST) — przesuwa dzień.
        """
        limits = RiskLimits(daily_loss_pct=0.05, daily_reset_tz="Europe/Warsaw")
        # 2024-01-01 22:00 UTC = 2024-01-01 23:00 Warsaw (CET, UTC+1)
        s = init_state(equity_start=10_000.0, ts=_ts("2024-01-01 22:00"), limits=limits)

        # 2024-01-01 23:00 UTC = 2024-01-02 00:00 Warsaw → nowy dzień, reset
        s = update_state(
            s, equity_now=9_500.0, ts=_ts("2024-01-01 23:30"), open_positions=1, limits=limits
        )
        assert s.daily_start_equity == pytest.approx(9_500.0)

    def test_invalid_tz_raises_value_error(self):
        # Nieznana strefa → ValueError przy init_state (a nie przy konstrukcji RiskLimits).
        limits = RiskLimits(daily_loss_pct=0.05, daily_reset_tz="Nope/NotReal")
        with pytest.raises(ValueError, match="IANA timezone"):
            init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)


# ============================================================================
# check_all — first-hit ordering
# ============================================================================


class TestCheckAll:
    def test_first_hit_drawdown_before_daily_loss(self):
        """Gdy oba progi naruszone na tym samym barze — DD raportowany pierwszy."""
        limits = RiskLimits(max_drawdown_pct=0.10, daily_loss_pct=0.10)
        s = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)
        s = update_state(
            s, equity_now=10_000.0, ts=_ts("2024-01-01 01:00"), open_positions=1, limits=limits
        )
        # equity_now=8_000 → DD = 20% > 10%, daily_loss = 20% > 10%
        breach = check_all(s, equity_now=8_000.0, ts=_ts("2024-01-01 02:00"), limits=limits)
        assert breach is not None
        assert breach.kind == "max_drawdown"

    def test_returns_none_when_all_safe(self):
        limits = RiskLimits(max_drawdown_pct=0.30, daily_loss_pct=0.10)
        s = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)
        s = update_state(
            s, equity_now=10_500.0, ts=_ts("2024-01-01 01:00"), open_positions=1, limits=limits
        )
        assert check_all(s, equity_now=10_500.0, ts=_ts("2024-01-01 02:00"), limits=limits) is None

    def test_breach_carries_bar_ts(self):
        """check_all nadpisuje ts na bieżący bar_ts, niezależnie od źródłowej funkcji."""
        limits = RiskLimits(max_drawdown_pct=0.10)
        s = init_state(equity_start=10_000.0, ts=_ts("2024-01-01"), limits=limits)
        bar_ts = _ts("2024-03-15 14:30")
        breach = check_all(s, equity_now=8_000.0, ts=bar_ts, limits=limits)
        assert breach is not None
        assert breach.ts == bar_ts


# ============================================================================
# Position sizing
# ============================================================================


class TestPositionSize:
    def test_basic_calculation(self):
        # equity=10_000, sl_distance=100, risk=0.01 → size = 100 / 100 = 1.0
        assert position_size(
            equity_now=10_000.0, sl_distance=100.0, risk_per_trade_pct=0.01
        ) == pytest.approx(1.0)

    def test_doubling_risk_doubles_size(self):
        size_1pct = position_size(10_000.0, 100.0, 0.01)
        size_2pct = position_size(10_000.0, 100.0, 0.02)
        assert size_2pct == pytest.approx(2 * size_1pct)

    def test_halving_sl_distance_doubles_size(self):
        size_wide = position_size(10_000.0, 100.0, 0.01)
        size_tight = position_size(10_000.0, 50.0, 0.01)
        assert size_tight == pytest.approx(2 * size_wide)

    def test_zero_sl_distance_returns_zero(self):
        assert position_size(10_000.0, 0.0, 0.01) == 0.0

    def test_negative_sl_distance_returns_zero(self):
        assert position_size(10_000.0, -50.0, 0.01) == 0.0

    def test_zero_equity_returns_zero(self):
        assert position_size(0.0, 100.0, 0.01) == 0.0


# ============================================================================
# RiskLimitBreached exception
# ============================================================================


class TestRiskLimitBreached:
    def test_carries_breach_instance(self):
        breach = RiskBreach(
            kind="max_drawdown",
            value=-0.25,
            threshold=0.20,
            ts=_ts("2024-01-01"),
            message="test",
        )
        exc = RiskLimitBreached(breach)
        assert exc.breach is breach
        assert "test" in str(exc)


# ============================================================================
# Integration tests — run_backtest hook (ADR-008 §9, Phase 1 success criterion)
# ============================================================================


def _make_two_phase_ohlcv(
    n_bars: int = 100,
    rise_to: float = 200.0,
    crash_to: float = 50.0,
    start_price: float = 100.0,
    freq: str = "1h",
) -> pd.DataFrame:
    """Buduje deterministyczny OHLCV z dwufazowym ruchem: rise → crash.

    Pierwsza połowa: liniowy wzrost ``start_price → rise_to``.
    Druga połowa: liniowy spadek ``rise_to → crash_to``.

    Wybierany dla integration testów risk module — buy-and-hold otwarte na
    początku rise dostaje peak equity w połowie, potem crash przekracza DD próg.
    """
    half = n_bars // 2
    rise = np.linspace(start_price, rise_to, half)
    crash = np.linspace(rise_to, crash_to, n_bars - half)
    close = np.concatenate([rise, crash])
    open_ = np.empty_like(close)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) * 1.005,
            "Low": np.minimum(open_, close) * 0.995,
            "Close": close,
            "Volume": np.full(n_bars, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=n_bars, freq=freq, tz="UTC"),
    )


def test_run_backtest_halts_on_max_drawdown_breach():
    """
    Integration test (ADR-008 §9, ROADMAP Phase 1 success criterion line 60):
    run_backtest z risk_limits.max_drawdown_pct kończy run gdy próg
    przekroczony i wpisuje detale do stats["_risk_breach"].

    Strategia: buy_and_hold (deterministyczne wejście long na drugim barze,
    brak exit). Dane: dwufazowy ruch 100 → 200 → 50 → crash 75% od peak,
    a próg DD = 30%, więc breach musi się odpalić w fazie spadkowej.
    """
    df = _make_two_phase_ohlcv(n_bars=100, rise_to=200.0, crash_to=50.0)

    risk_limits = RiskLimits(max_drawdown_pct=0.30)

    stats, equity, trades = run_backtest(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="buy_and_hold",
        params={"side": "long"},
        cash=1_000_000.0,  # >> max(High) → brak warning'u fractional trading
        commission=0.0,
        trade_on_close=True,
        data=df,
        risk_limits=risk_limits,
    )

    # Run musi być przerwany przez risk module
    assert "_risk_breach" in stats, "Brak _risk_breach w stats — risk module nie zadziałał"
    breach = stats["_risk_breach"]
    assert breach["kind"] == "max_drawdown"
    assert breach["threshold"] == pytest.approx(0.30)
    # Naruszenie musi być co najmniej tak duże jak threshold
    assert abs(breach["value"]) >= 0.30

    # Konfiguracja jest również w stats (metadane)
    assert "_risk_limits" in stats
    assert stats["_risk_limits"]["max_drawdown_pct"] == pytest.approx(0.30)

    # Equity i trades zwrócone normalnie (nie None, nie crash)
    assert isinstance(equity, pd.DataFrame)
    assert isinstance(trades, pd.DataFrame)


def test_run_backtest_without_risk_limits_is_backward_compatible():
    """Sanity check: run_backtest bez risk_limits zachowuje stary behaviour
    — brak _risk_breach, brak _risk_limits w stats."""
    # Spokojny ruch — brak crashu, buy_and_hold trzyma do końca
    df = _make_two_phase_ohlcv(n_bars=60, rise_to=110.0, crash_to=105.0, start_price=100.0)

    stats, _equity, _trades = run_backtest(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="buy_and_hold",
        params={"side": "long"},
        cash=1_000_000.0,
        commission=0.0,
        trade_on_close=True,
        data=df,
        # risk_limits=None — default
    )

    assert "_risk_breach" not in stats
    assert "_risk_limits" not in stats
