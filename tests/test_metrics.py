"""
tests/test_metrics.py

Testy ``algo_bot/metrics.py``. Bez mockow — wszystkie fixtures sa deterministyczne,
recznie skonstruowane equity / trade sequences z hand-policzonymi wartosciami
referencyjnymi (zgodnie z feedback_engineering_mindset regula #3 oraz ADR-006 #8 —
weryfikacja warningow przez ``caplog``).

See also:
    docs/adr/007-risk-adjusted-metrics.md (decyzja D — opcje + trade-offs)
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
import pytest

from algo_bot.metrics import (
    MetricsSummary,
    cagr,
    calmar,
    infer_periods_per_year,
    log_returns,
    mar_ratio,
    max_drawdown,
    mean_pairwise_correlation,
    profit_factor,
    recovery_time,
    rolling_sharpe,
    sharpe,
    simple_returns,
    sortino,
    strategy_correlation,
    summarize,
    total_return,
    win_rate,
)

# ============================================================================
# Fixtures pomocnicze
# ============================================================================


def _daily_index(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    """Genrate ``n`` daily timestamps starting at ``start``."""
    return pd.date_range(start=start, periods=n, freq="D")


def _hourly_index(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="h")


def _five_min_index(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="5min")


# ============================================================================
# infer_periods_per_year
# ============================================================================


class TestInferPeriodsPerYear:
    def test_daily_index_returns_365(self):
        idx = _daily_index(10)
        assert infer_periods_per_year(idx, calendar="crypto") == pytest.approx(365.0)

    def test_hourly_index_returns_8760(self):
        idx = _hourly_index(48)
        assert infer_periods_per_year(idx, calendar="crypto") == pytest.approx(24 * 365)

    def test_five_min_index_returns_105120(self):
        idx = _five_min_index(100)
        # 5-min → 12 per hour → 12*24*365 = 105120
        assert infer_periods_per_year(idx, calendar="crypto") == pytest.approx(12 * 24 * 365)

    def test_tradfi_calendar_returns_252_base(self):
        idx = _daily_index(10)
        assert infer_periods_per_year(idx, calendar="tradfi") == pytest.approx(252.0)

    def test_non_datetime_index_fallbacks_to_365(self, caplog):
        idx = pd.RangeIndex(10)
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = infer_periods_per_year(idx)
        assert result == 365.0
        assert any("nie jest DatetimeIndex" in rec.message for rec in caplog.records)

    def test_too_short_index_fallbacks_to_365(self, caplog):
        idx = _daily_index(1)
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = infer_periods_per_year(idx)
        assert result == 365.0
        assert any("za malo probek" in rec.message for rec in caplog.records)


# ============================================================================
# log_returns / simple_returns
# ============================================================================


class TestReturns:
    def test_log_returns_basic(self):
        equity = pd.Series([100.0, math.e * 100, math.e**2 * 100], index=_daily_index(3))
        rets = log_returns(equity)
        assert len(rets) == 2
        np.testing.assert_allclose(rets.values, [1.0, 1.0], atol=1e-10)

    def test_simple_returns_basic(self):
        equity = pd.Series([100.0, 110.0, 121.0], index=_daily_index(3))
        rets = simple_returns(equity)
        assert len(rets) == 2
        np.testing.assert_allclose(rets.values, [0.10, 0.10], atol=1e-10)


# ============================================================================
# total_return / cagr
# ============================================================================


class TestTotalReturn:
    def test_simple_gain(self):
        equity = pd.Series([100.0, 121.0], index=_daily_index(2))
        assert total_return(equity) == pytest.approx(0.21)

    def test_loss(self):
        equity = pd.Series([100.0, 90.0], index=_daily_index(2))
        assert total_return(equity) == pytest.approx(-0.10)

    def test_single_element_returns_zero(self):
        equity = pd.Series([100.0], index=_daily_index(1))
        assert total_return(equity) == 0.0


class TestCAGR:
    def test_two_years_21_pct_total(self):
        # 100 → 121 przez 2 lata → CAGR = sqrt(1.21) - 1 = 0.10
        idx = pd.DatetimeIndex(["2024-01-01", "2026-01-01"])
        equity = pd.Series([100.0, 121.0], index=idx)
        assert cagr(equity) == pytest.approx(0.10, abs=1e-4)

    def test_one_year_15_pct(self):
        idx = pd.DatetimeIndex(["2024-01-01", "2025-01-01"])
        equity = pd.Series([100.0, 115.0], index=idx)
        # ~365.25 days vs 365 → drobny offset, ale powinno byc bardzo blisko 0.15
        assert cagr(equity) == pytest.approx(0.15, abs=1e-2)

    def test_blowup_negative_equity(self):
        idx = pd.DatetimeIndex(["2024-01-01", "2025-01-01"])
        equity = pd.Series([100.0, -10.0], index=idx)
        assert cagr(equity) == -1.0

    def test_single_element_returns_nan(self):
        equity = pd.Series([100.0], index=_daily_index(1))
        assert math.isnan(cagr(equity))


# ============================================================================
# Sharpe
# ============================================================================


class TestSharpe:
    def test_zero_variance_returns_nan_with_warning(self, caplog):
        # equity rosnie geometrycznie ze stalym log_return → zero variance
        eq_values = [100.0 * math.exp(0.001 * i) for i in range(50)]
        equity = pd.Series(eq_values, index=_daily_index(50))
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = sharpe(equity)
        assert math.isnan(result)
        assert any("zero variance" in rec.message for rec in caplog.records)

    def test_empty_returns_returns_nan(self, caplog):
        equity = pd.Series([100.0], index=_daily_index(1))
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = sharpe(equity)
        assert math.isnan(result)

    def test_known_value_handcomputed(self):
        # log_returns = [0.01, 0.03] — 3-element equity
        # mean = 0.02, std (ddof=1) = sqrt(((0.01-0.02)^2 + (0.03-0.02)^2) / 1) = sqrt(0.0002)
        # Sharpe daily, ppy=365 → 0.02 / sqrt(0.0002) * sqrt(365)
        idx = _daily_index(3)
        eq = [100.0, 100.0 * math.exp(0.01), 100.0 * math.exp(0.01 + 0.03)]
        equity = pd.Series(eq, index=idx)
        expected = 0.02 / math.sqrt(0.0002) * math.sqrt(365)
        assert sharpe(equity) == pytest.approx(expected, rel=1e-6)

    def test_with_rf_nonzero(self):
        idx = _daily_index(3)
        eq = [100.0, 100.0 * math.exp(0.01), 100.0 * math.exp(0.01 + 0.03)]
        equity = pd.Series(eq, index=idx)
        # rf=0.365 (1/365 per day = 0.001)
        # excess returns = [0.009, 0.029]; mean=0.019; std unchanged
        rf = 0.365
        expected = (0.02 - rf / 365) / math.sqrt(0.0002) * math.sqrt(365)
        assert sharpe(equity, rf=rf) == pytest.approx(expected, rel=1e-6)


class TestRollingSharpe:
    def test_returns_series_with_correct_length(self):
        n = 100
        equity = pd.Series(
            [100.0 * (1.001) ** i + np.sin(i / 5) for i in range(n)],
            index=_daily_index(n),
        )
        rs = rolling_sharpe(equity, window=10)
        assert isinstance(rs, pd.Series)
        # log_returns ma n-1 elementow; rolling z window=10 ma pierwsze 9 NaN
        assert rs.iloc[:9].isna().all()
        assert not rs.iloc[10:].isna().any()


# ============================================================================
# Sortino
# ============================================================================


class TestSortino:
    def test_no_downside_returns_nan_with_warning(self, caplog):
        # equity rosnie monotonicznie → wszystkie zwroty dodatnie → brak downside
        eq = [100.0 * math.exp(0.01 * i) for i in range(20)]
        equity = pd.Series(eq, index=_daily_index(20))
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = sortino(equity)
        assert math.isnan(result)
        assert any("brak downside" in rec.message for rec in caplog.records)

    def test_with_downside_known_form(self):
        # log_returns = [-0.02, 0.04] → mean = 0.01
        # downside = [-0.02, 0]; downside_dev = sqrt(((-0.02)^2 + 0)/2) = sqrt(0.0002) = 0.01414
        # Sortino daily, ppy=365 → 0.01 / 0.01414 * sqrt(365)
        idx = _daily_index(3)
        eq = [100.0, 100.0 * math.exp(-0.02), 100.0 * math.exp(-0.02 + 0.04)]
        equity = pd.Series(eq, index=idx)
        expected = 0.01 / math.sqrt(0.0002) * math.sqrt(365)
        assert sortino(equity) == pytest.approx(expected, rel=1e-6)


# ============================================================================
# Max drawdown
# ============================================================================


class TestMaxDrawdown:
    def test_no_drawdown_monotonic(self):
        equity = pd.Series([100.0, 110.0, 121.0], index=_daily_index(3))
        dd_pct, dur = max_drawdown(equity)
        assert dd_pct == 0.0
        assert dur == pd.Timedelta(0)

    def test_known_dd_handcomputed(self):
        # equity = [100, 110, 105, 95, 100, 90, 95, 100, 110]
        # running_max = 110 od bar 1
        # drawdown bottom = 90/110 - 1 = -0.181818...
        # underwater od bar 2 (105) do bar 7 (100) — bar 8 jest juz na nowym high (110)
        # duration = 7-2 = 5 dni
        equity = pd.Series(
            [100.0, 110.0, 105.0, 95.0, 100.0, 90.0, 95.0, 100.0, 110.0],
            index=_daily_index(9),
        )
        dd_pct, dur = max_drawdown(equity)
        assert dd_pct == pytest.approx(-20.0 / 110.0)
        assert dur == pd.Timedelta(days=5)


# ============================================================================
# Recovery time
# ============================================================================


class TestRecoveryTime:
    def test_known_recovery_handcomputed(self):
        # Trough = bar 5 (equity 90, peak 110). Recovered = bar 8 (equity 110).
        # Recovery time = 8 - 5 = 3 dni.
        equity = pd.Series(
            [100.0, 110.0, 105.0, 95.0, 100.0, 90.0, 95.0, 100.0, 110.0],
            index=_daily_index(9),
        )
        assert recovery_time(equity) == pd.Timedelta(days=3)

    def test_never_recovered_returns_max_with_warning(self, caplog):
        # Trough w srodku, koniec ponizej peaku
        equity = pd.Series(
            [100.0, 120.0, 110.0, 100.0, 90.0, 95.0, 100.0],
            index=_daily_index(7),
        )
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = recovery_time(equity)
        assert result == pd.Timedelta.max
        assert any("nie wrocila powyzej" in rec.message for rec in caplog.records)

    def test_no_drawdown_returns_zero(self):
        equity = pd.Series([100.0, 110.0, 121.0], index=_daily_index(3))
        assert recovery_time(equity) == pd.Timedelta(0)


# ============================================================================
# Calmar
# ============================================================================


class TestCalmar:
    def test_short_series_fallback_with_warning(self, caplog):
        # 1 rok danych, window_months=36 → fallback + warning
        idx = pd.date_range("2024-01-01", "2025-01-01", freq="D")
        # Equity: start 100, w polowie spada do 90, koniec 110 → DD = -10%, CAGR≈10%
        # Calmar≈1.0
        equity_vals = np.concatenate(
            [
                np.linspace(100.0, 90.0, len(idx) // 2),
                np.linspace(90.0, 110.0, len(idx) - len(idx) // 2),
            ]
        )
        equity = pd.Series(equity_vals, index=idx)
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = calmar(equity, window_months=36)
        assert not math.isnan(result)
        assert any("fallback na cala historie" in rec.message for rec in caplog.records)

    def test_zero_dd_returns_nan_with_warning(self, caplog):
        # equity monotonicznie rosnie → zero DD → NaN
        idx = pd.date_range("2024-01-01", periods=200, freq="D")
        equity = pd.Series(np.linspace(100.0, 150.0, 200), index=idx)
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = calmar(equity, window_months=36)
        assert math.isnan(result)
        assert any("zero drawdown" in rec.message for rec in caplog.records)


# ============================================================================
# MAR ratio
# ============================================================================


class TestMARRatio:
    def test_basic_handcomputed(self):
        # 2-letni okres: 100 → 90 (DD = -10%) → 121 (final). CAGR ≈ 10%, DD = -10%.
        # MAR = 0.10 / 0.10 = 1.0 (z drobnym offset z powodu interpolacji)
        idx = pd.date_range("2024-01-01", "2026-01-01", freq="D")
        midpoint = len(idx) // 2
        equity_vals = np.concatenate(
            [
                np.linspace(100.0, 90.0, midpoint),
                np.linspace(90.0, 121.0, len(idx) - midpoint),
            ]
        )
        equity = pd.Series(equity_vals, index=idx)
        result = mar_ratio(equity)
        assert result == pytest.approx(1.0, abs=0.2)
        assert result > 0

    def test_zero_dd_returns_nan_with_warning(self, caplog):
        idx = pd.date_range("2024-01-01", periods=200, freq="D")
        equity = pd.Series(np.linspace(100.0, 150.0, 200), index=idx)
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = mar_ratio(equity)
        assert math.isnan(result)
        assert any("zero drawdown" in rec.message for rec in caplog.records)


# ============================================================================
# Profit factor
# ============================================================================


class TestProfitFactor:
    def test_known_value(self):
        # wins = 10+20+15 = 45, losses = 5+10 = 15 → pf = 3.0
        trades = pd.Series([10.0, -5.0, 20.0, -10.0, 15.0])
        assert profit_factor(trades) == pytest.approx(3.0)

    def test_no_losing_trades_returns_nan_with_warning(self, caplog):
        trades = pd.Series([10.0, 20.0, 30.0])
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = profit_factor(trades)
        assert math.isnan(result)
        assert any("brak losing trades" in rec.message for rec in caplog.records)

    def test_no_trades_returns_nan_with_warning(self, caplog):
        trades = pd.Series([], dtype=float)
        with caplog.at_level(logging.WARNING, logger="algo_bot.metrics"):
            result = profit_factor(trades)
        assert math.isnan(result)
        assert any("brak trade'ow" in rec.message for rec in caplog.records)


class TestWinRate:
    def test_known_value(self):
        trades = pd.Series([10.0, -5.0, 20.0, -10.0, 15.0])
        # 3 wins na 5 → 0.6
        assert win_rate(trades) == pytest.approx(0.6)

    def test_empty_returns_nan(self):
        trades = pd.Series([], dtype=float)
        assert math.isnan(win_rate(trades))


# ============================================================================
# summarize — end-to-end
# ============================================================================


class TestSummarize:
    def test_returns_metrics_summary_dataclass(self):
        idx = _daily_index(365)
        equity = pd.Series(
            np.linspace(100.0, 110.0, 365) + np.sin(np.arange(365) / 10) * 2,
            index=idx,
        )
        trades = pd.Series([1.5, -0.5, 2.0, -1.0, 3.0])
        result = summarize(equity, trades)
        assert isinstance(result, MetricsSummary)
        assert result.n_trades == 5
        assert result.periods_per_year == pytest.approx(365.0)

    def test_no_trades_yields_nan_for_trade_metrics(self):
        idx = _daily_index(100)
        equity = pd.Series(np.linspace(100.0, 105.0, 100), index=idx)
        result = summarize(equity, trades_pnl=None)
        assert result.n_trades == 0
        assert math.isnan(result.profit_factor)
        assert math.isnan(result.win_rate)

    def test_periods_per_year_passed_through(self):
        idx = _daily_index(50)
        equity = pd.Series(
            np.linspace(100.0, 105.0, 50) + np.sin(np.arange(50) / 5),
            index=idx,
        )
        ppy_override = 252.0
        result = summarize(equity, periods_per_year=ppy_override)
        assert result.periods_per_year == ppy_override

    def test_recovery_time_max_sentinel_to_inf(self):
        # equity nigdy nie odzyskala — recovery_time → Timedelta.max → inf
        equity = pd.Series(
            [100.0, 120.0, 110.0, 100.0, 90.0, 95.0, 100.0],
            index=_daily_index(7),
        )
        result = summarize(equity)
        assert math.isinf(result.recovery_time_days)
        assert result.recovery_time_days > 0


# ============================================================================
# Cross-strategy correlation
# ============================================================================


class TestStrategyCorrelation:
    def test_identical_series_yield_perfect_correlation(self):
        idx = _daily_index(50)
        eq = pd.Series(
            np.linspace(100.0, 110.0, 50) + np.sin(np.arange(50) / 3),
            index=idx,
        )
        equities = {"a": eq.copy(), "b": eq.copy()}
        corr = strategy_correlation(equities)
        assert corr.loc["a", "b"] == pytest.approx(1.0, abs=1e-10)
        assert corr.loc["a", "a"] == pytest.approx(1.0)

    def test_anticorrelated_series_yield_negative_one(self):
        # Equity rosnaca i jej "lustro": log_returns sa anti-correlated
        idx = _daily_index(50)
        rets = np.array([0.01, -0.02, 0.03, -0.01, 0.02] * 10)
        eq_a = pd.Series(100.0 * np.exp(np.cumsum(rets)), index=idx)
        eq_b = pd.Series(100.0 * np.exp(np.cumsum(-rets)), index=idx)
        corr = strategy_correlation({"a": eq_a, "b": eq_b})
        assert corr.loc["a", "b"] == pytest.approx(-1.0, abs=1e-10)

    def test_dataframe_input_equivalent_to_dict(self):
        idx = _daily_index(40)
        eq_a = pd.Series(np.linspace(100.0, 110.0, 40), index=idx)
        eq_b = pd.Series(np.linspace(100.0, 105.0, 40) + np.sin(np.arange(40) / 4), index=idx)
        df = pd.DataFrame({"a": eq_a, "b": eq_b})
        corr_df = strategy_correlation(df)
        corr_dict = strategy_correlation({"a": eq_a, "b": eq_b})
        # Te same wartosci niezaleznie od typu wejscia
        np.testing.assert_allclose(corr_df.values, corr_dict.values, atol=1e-12)

    def test_inner_join_on_misaligned_indices(self):
        # Dwie serie z czesciowo nakladajacymi sie zakresami — bierzemy intersekcje
        idx_a = pd.date_range("2024-01-01", periods=30, freq="D")
        idx_b = pd.date_range("2024-01-15", periods=30, freq="D")
        eq_a = pd.Series(np.linspace(100.0, 110.0, 30), index=idx_a)
        eq_b = pd.Series(np.linspace(100.0, 105.0, 30), index=idx_b)
        # Overlap: 2024-01-15 do 2024-01-30 = 16 dni
        corr = strategy_correlation({"a": eq_a, "b": eq_b})
        # Nie sprawdzamy konkretnej wartosci — wystarczy ze nie wybucha i zwraca 2x2
        assert corr.shape == (2, 2)
        assert -1.0 <= corr.loc["a", "b"] <= 1.0

    def test_spearman_method_runs(self):
        idx = _daily_index(50)
        eq_a = pd.Series(np.linspace(100.0, 110.0, 50), index=idx)
        eq_b = pd.Series(np.linspace(100.0, 95.0, 50), index=idx)
        corr = strategy_correlation({"a": eq_a, "b": eq_b}, method="spearman")
        assert corr.shape == (2, 2)
        # Monotonicznie rosnaca vs monotonicznie malejaca → Spearman -1
        assert corr.loc["a", "b"] == pytest.approx(-1.0, abs=1e-10)

    def test_simple_returns_input(self):
        idx = _daily_index(30)
        eq_a = pd.Series(np.linspace(100.0, 110.0, 30) + np.sin(np.arange(30)), index=idx)
        eq_b = pd.Series(np.linspace(100.0, 108.0, 30) + np.cos(np.arange(30)), index=idx)
        corr_log = strategy_correlation({"a": eq_a, "b": eq_b}, on="log_returns")
        corr_simple = strategy_correlation({"a": eq_a, "b": eq_b}, on="simple_returns")
        # Wartosci podobne ale nie identyczne (log vs simple)
        assert corr_log.shape == corr_simple.shape == (2, 2)

    def test_empty_dict_returns_empty_dataframe(self):
        corr = strategy_correlation({})
        assert corr.empty

    def test_single_strategy_returns_1x1_or_empty(self):
        idx = _daily_index(20)
        eq = pd.Series(np.linspace(100.0, 110.0, 20), index=idx)
        corr = strategy_correlation({"only_one": eq})
        # Mniej niz 2 strategie → metoda zwraca pusty/strukturalnie OK DF
        assert corr.shape[0] <= 1


class TestMeanPairwiseCorrelation:
    def test_two_strategies_returns_off_diagonal(self):
        # 2x2 matrix → tylko jedna para off-diagonal → srednia = ta korelacja
        matrix = pd.DataFrame(
            [[1.0, 0.5], [0.5, 1.0]],
            index=["a", "b"],
            columns=["a", "b"],
        )
        assert mean_pairwise_correlation(matrix) == pytest.approx(0.5)

    def test_three_strategies_mean_of_pairs(self):
        # 3x3 → 3 pary off-diagonal (ab, ac, bc), srednia = (0.5 + 0.3 + 0.7) / 3
        matrix = pd.DataFrame(
            [
                [1.0, 0.5, 0.3],
                [0.5, 1.0, 0.7],
                [0.3, 0.7, 1.0],
            ],
            index=["a", "b", "c"],
            columns=["a", "b", "c"],
        )
        expected = (0.5 + 0.3 + 0.7) / 3
        assert mean_pairwise_correlation(matrix) == pytest.approx(expected)

    def test_single_strategy_returns_nan(self):
        matrix = pd.DataFrame([[1.0]], index=["a"], columns=["a"])
        assert math.isnan(mean_pairwise_correlation(matrix))

    def test_empty_matrix_returns_nan(self):
        matrix = pd.DataFrame()
        assert math.isnan(mean_pairwise_correlation(matrix))
