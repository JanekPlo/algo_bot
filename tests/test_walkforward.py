"""
tests/test_walkforward.py

Deterministyczne testy ``algo_bot.engine.walkforward`` (ADR-009).

Bez mocków (mindset rule #3). Fixture'y:
- ``_make_drifting_ohlcv``: syntetyczny OHLCV (multi-year, hourly) z deterministycznym
  geometric drift + szumem (seed=42).
- ``_make_fold_result``: ręcznie zbudowany ``FoldResult`` dla unit testów agregacji
  (z dummy equity/trades — pure metrics computation).

Reference values dla agregacji policzone na piechotę w docstringach asercji.
Integration test ``test_walk_forward_smoke`` przechodzi pełen pipeline
(generate → run_fold → aggregate → stitch) na strategii ``buy_and_hold``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from algo_bot.engine.walkforward import (  # type: ignore[attr-defined]
    MVP_THRESHOLDS,
    WF_ELIGIBILITY_THRESHOLDS,
    Fold,
    FoldResult,
    WalkForwardConfig,
    WalkForwardReport,
    _parse_window,
    _to_bars,
    build_distribution,
    build_folds_df,
    compute_expected_folds,
    compute_mvp_pass,
    generate_folds,
    save_report,
    stitch_equity,
    walk_forward,
)
from algo_bot.metrics import MetricsSummary

# ============================================================================
# Fixtures / helpers
# ============================================================================


def _make_drifting_ohlcv(
    n_bars: int = 26_280,  # 3 lata godzinnych = 3 * 365 * 24
    freq: str = "1h",
    start_price: float = 100.0,
    annual_drift: float = 0.20,
    annual_vol: float = 0.50,
    seed: int = 42,
) -> pd.DataFrame:
    """Syntetyczny OHLCV z geometric drift + Gaussian noise.

    Deterministyczny (seed=42) — używany przez integration testy WF.
    """
    rng = np.random.default_rng(seed)
    bars_per_year = 365 * 24 if freq == "1h" else 365
    dt = 1.0 / bars_per_year
    log_returns = (annual_drift - 0.5 * annual_vol**2) * dt + annual_vol * np.sqrt(
        dt
    ) * rng.standard_normal(n_bars)
    close = start_price * np.exp(np.cumsum(log_returns))
    open_ = np.empty_like(close)
    open_[0] = start_price
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) * (1.0 + 0.002 * rng.random(n_bars)),
            "Low": np.minimum(open_, close) * (1.0 - 0.002 * rng.random(n_bars)),
            "Close": close,
            "Volume": np.full(n_bars, 1000.0),
        },
        index=pd.date_range("2023-01-01", periods=n_bars, freq=freq, tz="UTC"),
    )


def _make_short_uniform_index(n_bars: int = 100, freq: str = "1h") -> pd.DatetimeIndex:
    """Krótki uniform DatetimeIndex dla unit testów generate_folds."""
    return pd.date_range("2024-01-01", periods=n_bars, freq=freq, tz="UTC")


def _make_fold_result(
    fold_id: int,
    metrics_overrides: dict | None = None,
    equity_returns: list[float] | None = None,
) -> FoldResult:
    """Buduje syntetyczny FoldResult dla unit testów agregacji.

    Args:
        fold_id: identyfikator foldu.
        metrics_overrides: nadpisuje pola MetricsSummary.
        equity_returns: jeśli podane, equity = cumprod(1 + returns) * 100; inaczej
            prosty +10% (final/initial = 1.10).
    """
    base_metrics = {
        "total_return": 0.10,
        "cagr": 0.40,
        "sharpe": 1.0,
        "sortino": 1.5,
        "calmar": 2.0,
        "mar": 2.0,
        "max_drawdown_pct": -0.10,
        "max_drawdown_duration_days": 5.0,
        "recovery_time_days": 7.0,
        "profit_factor": 1.5,
        "win_rate": 0.55,
        "n_trades": 60,
        "periods_per_year": 8760.0,
    }
    if metrics_overrides:
        base_metrics.update(metrics_overrides)
    metrics = MetricsSummary(**base_metrics)

    if equity_returns is None:
        equity_returns = [0.0, 0.05, 0.10]
    equity_vals = 100.0 * np.cumprod(1.0 + np.array(equity_returns))
    equity_idx = pd.date_range(
        f"2024-{fold_id + 1:02d}-01", periods=len(equity_vals), freq="1h", tz="UTC"
    )
    equity = pd.DataFrame({"Equity": equity_vals}, index=equity_idx)
    trades = pd.DataFrame({"PnL": [1.0, 2.0, 3.0]})

    fold = Fold(
        fold_id=fold_id,
        train_start=pd.Timestamp(f"2023-{fold_id + 1:02d}-01", tz="UTC"),
        train_end=pd.Timestamp(f"2023-{fold_id + 6:02d}-01", tz="UTC"),
        test_start=equity_idx[0],
        test_end=equity_idx[-1],
    )
    return FoldResult(
        fold=fold,
        metrics=metrics,
        equity=equity,
        trades=trades,
        risk_breach=None,
        boundary_closes=0,
        n_trades=metrics.n_trades,
    )


# ============================================================================
# Unit tests — pure helpers
# ============================================================================


class TestToBars:
    def test_int_pass_through(self):
        assert _to_bars(100, pd.Timedelta(hours=1)) == 100

    def test_timedelta_conversion(self):
        # 1 dzień / 1 godzina = 24 bars
        assert _to_bars(pd.Timedelta(days=1), pd.Timedelta(hours=1)) == 24

    def test_timedelta_floor(self):
        # 25 godzin / 24h-window = 1 (floor)
        assert _to_bars(pd.Timedelta(hours=25), pd.Timedelta(days=1)) == 1

    def test_raises_on_zero_int(self):
        with pytest.raises(ValueError, match="dodatnia"):
            _to_bars(0, pd.Timedelta(hours=1))

    def test_raises_on_negative_int(self):
        with pytest.raises(ValueError, match="dodatnia"):
            _to_bars(-1, pd.Timedelta(hours=1))

    def test_raises_on_zero_timedelta(self):
        with pytest.raises(ValueError, match="dodatni"):
            _to_bars(pd.Timedelta(0), pd.Timedelta(hours=1))

    def test_raises_when_timedelta_smaller_than_median(self):
        # 30 min < 1h median → 0 bars → ValueError
        with pytest.raises(ValueError, match="0 bars"):
            _to_bars(pd.Timedelta(minutes=30), pd.Timedelta(hours=1))

    def test_raises_on_bool(self):
        # bool to int subclass — łapiemy explicit żeby uniknąć foot-gun
        with pytest.raises(TypeError, match="bool"):
            _to_bars(True, pd.Timedelta(hours=1))  # type: ignore[arg-type]

    def test_raises_on_string(self):
        with pytest.raises(TypeError, match="nieobslugiwany"):
            _to_bars("365d", pd.Timedelta(hours=1))  # type: ignore[arg-type]


class TestParseWindow:
    def test_parses_int(self):
        assert _parse_window("8760") == 8760

    def test_parses_timedelta_days(self):
        assert _parse_window("365d") == pd.Timedelta(days=365)

    def test_parses_timedelta_hours(self):
        assert _parse_window("12h") == pd.Timedelta(hours=12)

    def test_parses_timedelta_minutes(self):
        assert _parse_window("5min") == pd.Timedelta(minutes=5)

    def test_raises_on_garbage(self):
        with pytest.raises(ValueError, match="nie mogę zparsować"):
            _parse_window("not a window")


# ============================================================================
# Unit tests — fold generation
# ============================================================================


class TestComputeExpectedFolds:
    def test_typical_case_handcomputed(self):
        """1000 bars hourly, train=600, test=100, step=100.

        Pierwszy fold wymaga 700 bars (600+100). Pozostałe 300 bars / 100 step = 3.
        Łącznie: 1 + 3 = 4 foldy.
        """
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100)
        assert compute_expected_folds(index, config) == 4

    def test_single_fold_exactly(self):
        """700 bars, train=600, test=100, step=100 → expected=1."""
        index = _make_short_uniform_index(n_bars=700)
        config = WalkForwardConfig(train=600, test=100, step=100)
        assert compute_expected_folds(index, config) == 1

    def test_zero_folds_when_too_short(self):
        """500 bars, train=600 → 0 (data za krótkie nawet na pierwszy fold)."""
        index = _make_short_uniform_index(n_bars=500)
        config = WalkForwardConfig(train=600, test=100, step=100)
        assert compute_expected_folds(index, config) == 0

    def test_step_default_to_test(self):
        """step=None resolve do test_size."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=None)
        # Identyczne jak step=100 → 4
        assert compute_expected_folds(index, config) == 4

    def test_with_timedelta_inputs(self):
        """train=24h, test=6h, step=6h na 1h bars = train_bars=24, test_bars=6."""
        index = _make_short_uniform_index(n_bars=100, freq="1h")
        config = WalkForwardConfig(
            train=pd.Timedelta(hours=24),
            test=pd.Timedelta(hours=6),
            step=pd.Timedelta(hours=6),
        )
        # Pierwszy: 30 bars. Pozostałe 70 / 6 = 11 (floor). 1+11 = 12.
        assert compute_expected_folds(index, config) == 12


class TestGenerateFoldsRolling:
    def test_basic_shape(self):
        """1000 bars, train=600, test=100, step=100, rolling → 4 foldy."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="rolling")
        folds = generate_folds(index, config)
        assert len(folds) == 4
        # fold_id monotonic 0, 1, 2, 3
        assert [f.fold_id for f in folds] == [0, 1, 2, 3]

    def test_first_fold_indices(self):
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="rolling")
        folds = generate_folds(index, config)
        f0 = folds[0]
        # Fold 0: train [0..599], test [600..699]
        assert f0.train_start == index[0]
        assert f0.train_end == index[599]
        assert f0.test_start == index[600]
        assert f0.test_end == index[699]

    def test_train_window_slides_forward(self):
        """W rolling: każdy kolejny fold ma train przesunięty o step bars."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="rolling")
        folds = generate_folds(index, config)
        for i in range(1, len(folds)):
            # train_start[i] = train_start[i-1] + step
            assert folds[i].train_start > folds[i - 1].train_start

    def test_no_leakage_strict(self):
        """test_start > train_end (strict) dla każdego foldu — fundamentalny invariant."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="rolling")
        folds = generate_folds(index, config)
        for f in folds:
            assert f.test_start > f.train_end, (
                f"leakage in fold {f.fold_id}: "
                f"test_start {f.test_start} not > train_end {f.train_end}"
            )

    def test_monotonic_test_progression(self):
        """fold[i+1].test_start > fold[i].test_start dla kolejnych foldów."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="rolling")
        folds = generate_folds(index, config)
        for i in range(1, len(folds)):
            assert folds[i].test_start > folds[i - 1].test_start


class TestGenerateFoldsAnchored:
    def test_train_start_fixed_at_index_zero(self):
        """W anchored: train_start == index[0] dla każdego foldu."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="anchored")
        folds = generate_folds(index, config)
        for f in folds:
            assert f.train_start == index[0]

    def test_train_end_grows(self):
        """W anchored: train_end rośnie z fold_id."""
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="anchored")
        folds = generate_folds(index, config)
        for i in range(1, len(folds)):
            assert folds[i].train_end > folds[i - 1].train_end

    def test_same_fold_count_as_rolling(self):
        """Oba mode'y produkują tę samą liczbę foldów dla tego samego configu."""
        index = _make_short_uniform_index(n_bars=1000)
        rolling = generate_folds(
            index, WalkForwardConfig(train=600, test=100, step=100, mode="rolling")
        )
        anchored = generate_folds(
            index, WalkForwardConfig(train=600, test=100, step=100, mode="anchored")
        )
        assert len(rolling) == len(anchored)

    def test_no_leakage_in_anchored(self):
        index = _make_short_uniform_index(n_bars=1000)
        config = WalkForwardConfig(train=600, test=100, step=100, mode="anchored")
        folds = generate_folds(index, config)
        for f in folds:
            assert f.test_start > f.train_end


class TestGenerateFoldsErrors:
    def test_raises_on_empty_index(self):
        empty_idx = pd.DatetimeIndex([])
        with pytest.raises(ValueError, match="pusty"):
            generate_folds(empty_idx, WalkForwardConfig(train=10, test=5))

    def test_raises_on_non_datetime_index(self):
        idx = pd.RangeIndex(100)
        with pytest.raises(TypeError, match="DatetimeIndex"):
            generate_folds(idx, WalkForwardConfig(train=10, test=5))  # type: ignore[arg-type]

    def test_raises_on_non_monotonic_index(self):
        idx = pd.DatetimeIndex([pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-01")])
        with pytest.raises(ValueError, match="monotonicznie"):
            generate_folds(idx, WalkForwardConfig(train=1, test=1))

    def test_raises_on_data_too_short(self):
        index = _make_short_uniform_index(n_bars=50)
        with pytest.raises(ValueError, match="za krótkie"):
            generate_folds(index, WalkForwardConfig(train=600, test=100))

    def test_raises_on_single_fold(self):
        """Dokładnie train+test bars → expected=1 → ValueError ('use run_backtest')."""
        index = _make_short_uniform_index(n_bars=700)
        with pytest.raises(ValueError, match="single fold"):
            generate_folds(index, WalkForwardConfig(train=600, test=100, step=100))

    def test_raises_on_step_too_large(self):
        """step > train+test → degenerate."""
        index = _make_short_uniform_index(n_bars=1000)
        with pytest.raises(ValueError, match="degenerate"):
            generate_folds(index, WalkForwardConfig(train=600, test=100, step=800))

    def test_raises_on_unknown_mode(self):
        index = _make_short_uniform_index(n_bars=1000)
        # mode wprost obchodzimy Literal type check przez # type: ignore
        config = WalkForwardConfig(train=600, test=100, step=100, mode="nonsense")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="nieznany mode"):
            generate_folds(index, config)


class TestGenerateFoldsWarnings:
    def test_warns_on_overlap_step_below_test(self, caplog):
        """step < test → warning 'overlapping'."""
        index = _make_short_uniform_index(n_bars=1000)
        with caplog.at_level("WARNING"):
            generate_folds(index, WalkForwardConfig(train=600, test=100, step=50))
        assert any("overlapping" in r.message for r in caplog.records)

    def test_warns_on_gaps_step_above_test(self, caplog):
        """step > test (ale <= train+test) → warning 'gaps'."""
        index = _make_short_uniform_index(n_bars=1000)
        with caplog.at_level("WARNING"):
            generate_folds(index, WalkForwardConfig(train=600, test=100, step=200))
        assert any("gaps" in r.message for r in caplog.records)

    def test_warns_on_low_fold_count(self, caplog):
        """expected_folds < min_folds_warn → warning."""
        index = _make_short_uniform_index(n_bars=900)
        # 900-700 = 200, step 100 → 1+2=3 foldy < 5
        with caplog.at_level("WARNING"):
            generate_folds(
                index,
                WalkForwardConfig(train=600, test=100, step=100, min_folds_warn=5),
            )
        assert any("statystycznej istotności" in r.message for r in caplog.records)


# ============================================================================
# Unit tests — aggregation
# ============================================================================


class TestBuildFoldsDf:
    def test_three_folds_shape(self):
        folds = [_make_fold_result(i) for i in range(3)]
        df = build_folds_df(folds)
        assert len(df) == 3
        assert df.index.name == "fold_id"
        # MetricsSummary columns obecne
        for col in ("sharpe", "sortino", "max_drawdown_pct", "n_trades"):
            assert col in df.columns
        # Fold metadata obecne
        for col in ("train_start", "test_end", "boundary_closes", "risk_breach_kind"):
            assert col in df.columns

    def test_risk_breach_kind_propagates(self):
        folds = [_make_fold_result(0)]
        # Wstrzykuję breach do drugiego foldu
        fr1 = _make_fold_result(1)
        fr1_with_breach = FoldResult(
            fold=fr1.fold,
            metrics=fr1.metrics,
            equity=fr1.equity,
            trades=fr1.trades,
            risk_breach={"kind": "max_drawdown", "value": -0.30, "threshold": 0.25},
            boundary_closes=0,
            n_trades=fr1.n_trades,
        )
        df = build_folds_df([folds[0], fr1_with_breach])
        assert df.loc[0, "risk_breach_kind"] is None
        assert df.loc[1, "risk_breach_kind"] == "max_drawdown"


class TestBuildDistribution:
    def test_handcomputed_mean_median_std(self):
        """3 foldy z sharpe=[0.5, 1.0, 1.5] → mean=1.0, median=1.0, std=0.5."""
        folds = [
            _make_fold_result(0, {"sharpe": 0.5}),
            _make_fold_result(1, {"sharpe": 1.0}),
            _make_fold_result(2, {"sharpe": 1.5}),
        ]
        folds_df = build_folds_df(folds)
        dist = build_distribution(folds_df)
        assert dist.loc["mean", "sharpe"] == pytest.approx(1.0)
        assert dist.loc["median", "sharpe"] == pytest.approx(1.0)
        # pandas std uses ddof=1 (sample std): sqrt(((-0.5)² + 0² + 0.5²) / 2) = sqrt(0.25) = 0.5
        assert dist.loc["std", "sharpe"] == pytest.approx(0.5)
        assert dist.loc["min", "sharpe"] == pytest.approx(0.5)
        assert dist.loc["max", "sharpe"] == pytest.approx(1.5)

    def test_mvp_threshold_row_present(self):
        folds = [_make_fold_result(i) for i in range(3)]
        dist = build_distribution(build_folds_df(folds))
        assert "mvp_threshold" in dist.index
        # progi z MVP_THRESHOLDS
        assert dist.loc["mvp_threshold", "sharpe"] == pytest.approx(MVP_THRESHOLDS["sharpe"])
        assert dist.loc["mvp_threshold", "profit_factor"] == pytest.approx(
            MVP_THRESHOLDS["profit_factor"]
        )
        assert dist.loc["mvp_threshold", "max_drawdown_pct"] == pytest.approx(
            MVP_THRESHOLDS["max_drawdown_pct"]
        )
        assert dist.loc["mvp_threshold", "n_trades"] == pytest.approx(MVP_THRESHOLDS["n_trades"])

    def test_mvp_threshold_nan_for_non_mvp_metrics(self):
        """Pola które NIE są w MVP_THRESHOLDS mają NaN w mvp_threshold rzędzie."""
        folds = [_make_fold_result(i) for i in range(3)]
        dist = build_distribution(build_folds_df(folds))
        # sortino, calmar, mar — nie są w MVP criteria
        for col in ("sortino", "calmar", "mar", "win_rate"):
            if col in dist.columns:
                assert pd.isna(dist.loc["mvp_threshold", col])


class TestComputeMvpPass:
    def test_all_pass(self):
        """Mean spełnia wszystkie 4 progi."""
        folds = [
            _make_fold_result(
                i,
                {
                    "sharpe": 1.5,
                    "profit_factor": 1.5,
                    "max_drawdown_pct": -0.10,
                    "n_trades": 60,
                },
            )
            for i in range(3)
        ]
        dist = build_distribution(build_folds_df(folds))
        pass_dict = compute_mvp_pass(dist)
        assert pass_dict == {
            "sharpe": True,
            "profit_factor": True,
            "max_drawdown_pct": True,
            "n_trades": True,
        }

    def test_sharpe_fail(self):
        folds = [_make_fold_result(i, {"sharpe": 0.5}) for i in range(3)]
        pass_dict = compute_mvp_pass(build_distribution(build_folds_df(folds)))
        assert pass_dict["sharpe"] is False

    def test_dd_boundary_pass(self):
        """DD dokładnie na progu (-0.25) → pass (>=)."""
        folds = [_make_fold_result(i, {"max_drawdown_pct": -0.25}) for i in range(3)]
        pass_dict = compute_mvp_pass(build_distribution(build_folds_df(folds)))
        assert pass_dict["max_drawdown_pct"] is True

    def test_dd_fail_below_threshold(self):
        """DD gorszy niż -0.25 → fail."""
        folds = [_make_fold_result(i, {"max_drawdown_pct": -0.30}) for i in range(3)]
        pass_dict = compute_mvp_pass(build_distribution(build_folds_df(folds)))
        assert pass_dict["max_drawdown_pct"] is False

    def test_empty_distribution_all_false(self):
        empty = pd.DataFrame()
        pass_dict = compute_mvp_pass(empty)
        assert pass_dict == dict.fromkeys(MVP_THRESHOLDS, False)


class TestWfEligibilityThresholds:
    """WF_ELIGIBILITY_THRESHOLDS to PRE-WF filter (ADR-013), rozłączny semantycznie
    z MVP_THRESHOLDS (POST-WF go-live gate). Testy pilnują wartości i relacji
    między progami — regresję łapiemy, gdyby ktoś je pomylił/zbił."""

    def test_exact_values(self):
        """Wartości z ADR-013 / kickoffu Pivot A."""
        assert WF_ELIGIBILITY_THRESHOLDS == {
            "sharpe": 1.0,
            "profit_factor": 1.3,
            "n_trades": 100.0,
            "max_drawdown_pct": -0.20,
        }

    def test_distinct_object_from_mvp(self):
        """To osobna stała, nie alias MVP_THRESHOLDS."""
        assert WF_ELIGIBILITY_THRESHOLDS is not MVP_THRESHOLDS
        assert WF_ELIGIBILITY_THRESHOLDS != MVP_THRESHOLDS

    def test_sharpe_pf_aligned_with_mvp(self):
        """Sharpe i PF są celowo równe MVP go-live (pre-WF 1.0 IS ≈ post-WF 1.0 OOS
        po decay — patrz ADR-013), więc filtr eligibility nie jest luźniejszy na
        tych dwóch osiach niż finalna brama."""
        assert WF_ELIGIBILITY_THRESHOLDS["sharpe"] == MVP_THRESHOLDS["sharpe"]
        assert WF_ELIGIBILITY_THRESHOLDS["profit_factor"] == MVP_THRESHOLDS["profit_factor"]

    def test_n_trades_stricter_than_mvp(self):
        """Pre-WF wymaga WIĘCEJ trade'ów niż MVP: in-sample łatwo nazbierać
        statystyki, a Sesja 4 pokazała wysokie Sharpe na n_trades≈1 (puste)."""
        assert WF_ELIGIBILITY_THRESHOLDS["n_trades"] > MVP_THRESHOLDS["n_trades"]

    def test_drawdown_stricter_than_mvp(self):
        """Pre-WF wymaga CIAŚNIEJSZEGO DD (-0.20) niż MVP go-live (-0.25):
        DD to liczba ujemna, więc 'ciaśniejszy' = bliżej zera = większy."""
        assert WF_ELIGIBILITY_THRESHOLDS["max_drawdown_pct"] > MVP_THRESHOLDS["max_drawdown_pct"]


# ============================================================================
# Unit tests — equity stitching
# ============================================================================


class TestStitchEquity:
    def test_empty_folds_returns_empty_df(self):
        result = stitch_equity([], initial_cash=100_000.0)
        assert result.empty
        assert list(result.columns) == ["timestamp", "equity", "fold_id"]

    def test_handcomputed_compound_three_folds(self):
        """3 foldy z deterministycznymi returns.

        Fold 0: equity [100, 110] → return +10%, cum_capital = 100000 * 1.10 = 110000
        Fold 1: equity [100, 105] → return +5%, cum_capital = 110000 * 1.05 = 115500
        Fold 2: equity [100, 95]  → return -5%, cum_capital = 115500 * 0.95 = 109725

        Stitched ostatni punkt: 109725 (= 100000 * 1.10 * 1.05 * 0.95)
        """
        folds = [
            _make_fold_result(0, equity_returns=[0.0, 0.10]),
            _make_fold_result(1, equity_returns=[0.0, 0.05]),
            _make_fold_result(2, equity_returns=[0.0, -0.05]),
        ]
        stitched = stitch_equity(folds, initial_cash=100_000.0)
        assert not stitched.empty
        # Każdy fold ma 2 punkty (start + end). Łącznie 6 wierszy.
        assert len(stitched) == 6

        # Sprawdź końcowy compound
        last_eq = float(stitched.iloc[-1]["equity"])
        expected = 100_000.0 * 1.10 * 1.05 * 0.95
        assert last_eq == pytest.approx(expected, rel=1e-6)

    def test_fold_id_column_marks_membership(self):
        folds = [_make_fold_result(i) for i in range(3)]
        stitched = stitch_equity(folds, initial_cash=100_000.0)
        assert set(stitched["fold_id"].unique()) == {0, 1, 2}

    def test_starts_at_initial_cash(self):
        """Pierwszy punkt stitched = initial_cash (eq[0] * cum_capital / eq[0])."""
        folds = [_make_fold_result(0, equity_returns=[0.0, 0.10])]
        stitched = stitch_equity(folds, initial_cash=50_000.0)
        assert float(stitched.iloc[0]["equity"]) == pytest.approx(50_000.0)


# ============================================================================
# Integration tests — end-to-end walk_forward
# ============================================================================


@pytest.fixture(scope="module")
def synthetic_3y_hourly() -> pd.DataFrame:
    """3 lata godzinnych bars z deterministycznym geometric drift (seed=42).

    Module-scoped — synthesis jest deterministyczna i kosztowna (26280 wierszy),
    więc cache'ujemy.
    """
    return _make_drifting_ohlcv()


def test_walk_forward_smoke(synthetic_3y_hourly: pd.DataFrame):
    """Pełny pipeline: 3y/12m-train/3m-test/3m-step na buy_and_hold.

    Oczekiwane:
    - ~7 foldów (3y - 1y train = 2y test coverage, krok 3m → ~8 foldów; faktycznie
      mniej z powodu boundary). 1 < n < 12.
    - WalkForwardReport ma niepuste folds_df, distribution, stitched_equity.
    - Wszystkie no-leakage invariants spełnione.
    """
    config = WalkForwardConfig(
        train=pd.Timedelta(days=365),
        test=pd.Timedelta(days=90),
        step=pd.Timedelta(days=90),
        mode="rolling",
        min_folds_warn=2,  # zatłumiamy warning żeby test był ciszej
    )
    report = walk_forward(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="buy_and_hold",
        params={"side": "long"},
        config=config,
        data=synthetic_3y_hourly,
        cash=1_000_000.0,
        commission=0.0,
        save=False,
    )

    assert isinstance(report, WalkForwardReport)
    assert 2 <= len(report.folds) <= 12
    assert not report.folds_df.empty
    assert not report.distribution.empty
    assert "mvp_threshold" in report.distribution.index
    assert not report.stitched_equity.empty
    # mvp_pass musi mieć 4 klucze
    assert set(report.mvp_pass.keys()) == set(MVP_THRESHOLDS.keys())
    # elapsed_seconds > 0
    assert report.elapsed_seconds > 0


def test_walk_forward_no_leakage_invariant(synthetic_3y_hourly: pd.DataFrame):
    """Po pełnym run: test_start > train_end strict dla każdego foldu."""
    config = WalkForwardConfig(
        train=pd.Timedelta(days=365),
        test=pd.Timedelta(days=90),
        step=pd.Timedelta(days=90),
        mode="rolling",
        min_folds_warn=2,
    )
    report = walk_forward(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="buy_and_hold",
        params={"side": "long"},
        config=config,
        data=synthetic_3y_hourly,
        cash=1_000_000.0,
        commission=0.0,
        save=False,
    )
    for fr in report.folds:
        assert fr.fold.test_start > fr.fold.train_end, f"leakage in fold {fr.fold.fold_id}"


def test_walk_forward_anchored_mode(synthetic_3y_hourly: pd.DataFrame):
    """Anchored: train_start jest fixed dla każdego foldu."""
    config = WalkForwardConfig(
        train=pd.Timedelta(days=365),
        test=pd.Timedelta(days=90),
        step=pd.Timedelta(days=90),
        mode="anchored",
        min_folds_warn=2,
    )
    report = walk_forward(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="buy_and_hold",
        params={"side": "long"},
        config=config,
        data=synthetic_3y_hourly,
        cash=1_000_000.0,
        commission=0.0,
        save=False,
    )
    first_train_start = report.folds[0].fold.train_start
    for fr in report.folds:
        assert fr.fold.train_start == first_train_start


def test_walk_forward_saves_artefacts(synthetic_3y_hourly: pd.DataFrame, tmp_path: Path):
    """save=True z eksplicytnym wf_run_id → wszystkie artefakty na dysku."""
    config = WalkForwardConfig(
        train=pd.Timedelta(days=365),
        test=pd.Timedelta(days=90),
        step=pd.Timedelta(days=90),
        mode="rolling",
        min_folds_warn=2,
    )
    report = walk_forward(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="buy_and_hold",
        params={"side": "long"},
        config=config,
        data=synthetic_3y_hourly,
        cash=1_000_000.0,
        commission=0.0,
        wf_run_id="wf_test_smoke",
        save=False,  # save=False ponieważ chcemy używać tmp_path nie WF_OUT_DIR
    )

    out_dir = tmp_path / "wf_test_smoke"
    save_report(report, out_dir)

    # Top-level pliki
    assert (out_dir / "walkforward_summary.json").exists()
    assert (out_dir / "walkforward_folds.csv").exists()
    assert (out_dir / "walkforward_distribution.csv").exists()
    assert (out_dir / "walkforward_equity.csv").exists()

    # Każdy fold ma subdir z 3 plikami
    for fr in report.folds:
        fold_dir = out_dir / f"fold_{fr.fold.fold_id:03d}"
        assert fold_dir.exists()
        assert (fold_dir / "summary.json").exists()
        assert (fold_dir / "equity.csv").exists()
        assert (fold_dir / "trades.csv").exists()

    # JSON jest valid + zawiera mvp_pass
    summary = json.loads((out_dir / "walkforward_summary.json").read_text())
    assert "mvp_pass" in summary
    assert set(summary["mvp_pass"].keys()) == set(MVP_THRESHOLDS.keys())
    assert "n_folds" in summary
    assert summary["n_folds"] == len(report.folds)


def test_walk_forward_data_validation():
    """Niepoprawny ``data`` (brak kolumn) → ValueError."""
    bad_df = pd.DataFrame(
        {"Close": [1.0, 2.0]},
        index=pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC"),
    )
    config = WalkForwardConfig(train=1, test=1, step=1)
    with pytest.raises(ValueError, match="brak kolumn"):
        walk_forward(
            symbol="X",
            timeframe="1h",
            strategy="buy_and_hold",
            params={"side": "long"},
            config=config,
            data=bad_df,
            save=False,
        )


def test_walk_forward_non_datetime_index():
    """``data`` z RangeIndex zamiast DatetimeIndex → ValueError."""
    bad_df = pd.DataFrame(
        {
            "Open": [1.0, 2.0],
            "High": [1.0, 2.0],
            "Low": [1.0, 2.0],
            "Close": [1.0, 2.0],
            "Volume": [1.0, 2.0],
        }
    )
    config = WalkForwardConfig(train=1, test=1, step=1)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        walk_forward(
            symbol="X",
            timeframe="1h",
            strategy="buy_and_hold",
            params={"side": "long"},
            config=config,
            data=bad_df,
            save=False,
        )
