"""Walk-forward analyzer — out-of-sample train/test split z rolowaniem okna.

Generator foldów (rolling / anchored), executor wywołujący ``run_backtest``
per fold z reset RiskState, agregator metryk (folds_df, distribution z
mvp_threshold), equity stitching (rebase + compound), I/O do
``results/walkforward/<wf_run_id>/``.

Pełne rationale: ``docs/adr/009-walk-forward.md``.
Deep reference: ``docs/reference/modules/walkforward.md``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

from algo_bot.engine.backtester import (
    DEFAULT_CASH,
    DEFAULT_COMMISSION,
    PROJECT_ROOT,
    load_ohlcv_csv,
    run_backtest,
)
from algo_bot.log import get_logger, setup_logging
from algo_bot.metrics import MetricsSummary, summarize
from algo_bot.microstructure import MicrostructureConfig
from algo_bot.risk.limits import RiskLimits

logger = get_logger(__name__)

# Katalog docelowy artefaktów (analogicznie do OUT_DIR w backtester.py)
WF_OUT_DIR = os.path.join(PROJECT_ROOT, "results", "walkforward")

# Phase 2 MVP success criteria (ROADMAP linie 100-104). Wszystkie używają
# kierunku "wartość średnia >= próg". DD jest reprezentowany jako liczba
# ujemna (loss), więc >= -0.25 oznacza "strata <= 25%".
#
# UWAGA — dwa różne progi, dwa różne pytania (ADR-013):
#   * MVP_THRESHOLDS         = POST-WF go-live gate. "Czy strategia jest gotowa
#     na testnet/live po walk-forwardzie?" (ADR-009). NIE ruszamy tego bez
#     ekonomicznej podstawy — to brama na żywy kapitał.
#   * WF_ELIGIBILITY_THRESHOLDS = PRE-WF filter (niżej). "Czy in-sample sweep
#     result jest wart drogiego walk-forwardu?" — inne pytanie, inny (luźniejszy
#     n_trades / DD) próg, świadomie skalibrowany post-Sesja 4.
MVP_THRESHOLDS: dict[str, float] = {
    "sharpe": 1.0,
    "max_drawdown_pct": -0.25,
    "profit_factor": 1.3,
    "n_trades": 50.0,
}

# Pre-WF eligibility filter (ADR-013). Stosowany na in-sample sweep review
# (notebook 03, docs/guides/running-sweep.md) do decyzji "które konfiguracje
# są warte walk-forwardu". NIE jest wpięty w compute_mvp_pass — to filtr
# operatorski nad index.csv, nie post-WF gate.
#
# Kalibracja (ADR-013): pierwotny arbitralny pre-WF Sharpe 1.5 zawyżał bar.
# Przy realistycznym IS→OOS decay 0.5-0.7x, in-sample Sharpe 1.0 mapuje na
# ~0.5-0.7 OOS — wystarczy by wejść w WF i ZOBACZYĆ decay (aktywacja WF jest
# tania decyzyjnie względem straconej szansy na realny edge). Brak konfliktu
# z MVP_THRESHOLDS: tam Sharpe 1.0 to POST-WF go-live, tu 1.0 to PRE-WF wstęp.
# n_trades 100 (ostrzej niż MVP 50) i DD -0.20 (ostrzej niż MVP -0.25) —
# in-sample łatwiej nazbierać trade'ów, więc wymagamy więcej statystyki i
# ciaśniejszego DD zanim uznamy sweep sample za warty WF (Sesja 4 pokazała,
# że wysokie Sharpe siedzą na n_trades≈1 — dlatego twardy próg liczby trade'ów).
WF_ELIGIBILITY_THRESHOLDS: dict[str, float] = {
    "sharpe": 1.0,
    "profit_factor": 1.3,
    "n_trades": 100.0,
    "max_drawdown_pct": -0.20,
}


# =====================================================================
# Frozen dataclasses
# =====================================================================


@dataclass(frozen=True)
class WalkForwardConfig:
    """Konfiguracja walk-forward run.

    Attributes:
        train: Długość okna treningowego — int (bars) albo ``pd.Timedelta``.
        test: Długość okna testowego (OOS) — int (bars) albo ``pd.Timedelta``.
        step: Krok przesunięcia okna. ``None`` (default) → ``test`` (no-overlap).
        mode: ``"rolling"`` (sliding train window) albo ``"anchored"``
            (expanding train window od początku danych).
        min_folds_warn: Próg ostrzeżenia ``logger.warning`` przy expected
            fold count < this. Phase 2 MVP wymaga >=5 (ROADMAP linia 79).
        risk_limits: Opcjonalne limity ryzyka (ADR-008). Stosowane per fold
            z świeżym RiskState (decyzja §5 ADR-009).
        microstructure: Opcjonalna konfiguracja mikrostruktury (ADR-011).
            Przekazywana do run_backtest per fold; funding jest slice'owany do
            zakresu foldu wewnątrz run_backtest (Decyzja 11).
    """

    train: int | pd.Timedelta
    test: int | pd.Timedelta
    step: int | pd.Timedelta | None = None
    mode: Literal["rolling", "anchored"] = "rolling"
    min_folds_warn: int = 5
    risk_limits: RiskLimits | None = None
    microstructure: MicrostructureConfig | None = None


@dataclass(frozen=True)
class Fold:
    """Pojedynczy split train/test. Timestampy inclusive na obu końcach.

    Attributes:
        fold_id: 0-indexed identyfikator. Formatowanie do ``fold_<i:03d>``
            tylko w output paths / log messages.
        train_start: Pierwszy bar okna treningowego (inclusive).
        train_end: Ostatni bar okna treningowego (inclusive).
        test_start: Pierwszy bar okna testowego (inclusive). Invariant:
            ``test_start > train_end`` (strict — no leakage).
        test_end: Ostatni bar okna testowego (inclusive).
    """

    fold_id: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass(frozen=True)
class FoldResult:
    """Wynik wykonania pojedynczego foldu.

    Attributes:
        fold: Definicja foldu (train/test ranges).
        metrics: ``MetricsSummary`` policzony z OOS equity + trades.
        equity: Raw equity DataFrame z ``run_backtest`` (test window slice,
            nie rebased — rebase dzieje się dopiero w ``stitch_equity``).
        trades: Raw trades DataFrame z ``run_backtest``.
        risk_breach: ``stats["_risk_breach"]`` jeśli fold zakończył się
            breach'em, inaczej ``None``.
        boundary_closes: Liczba trade'ów zamkniętych dokładnie na
            ``fold.test_end`` (forced close przez backtesting.py).
        n_trades: Wygodne mirror ``metrics.n_trades`` (int).
    """

    fold: Fold
    metrics: MetricsSummary
    equity: pd.DataFrame
    trades: pd.DataFrame
    risk_breach: dict[str, Any] | None
    boundary_closes: int
    n_trades: int


@dataclass(frozen=True)
class WalkForwardReport:
    """Top-level wynik walk-forward run.

    Attributes:
        wf_run_id: ``wf_<symbol>_<timeframe>_<strategy>_<YYYYMMDD_HHMMSS>``.
        config: Konfiguracja użyta dla tego runu.
        symbol: Symbol instrumentu, np. ``"BTC/USDT"``.
        timeframe: Timeframe, np. ``"1h"``.
        strategy: Nazwa strategii (slug w ``strategies/``).
        params: Parametry strategii (przekazane do ``run_backtest`` per fold).
        folds: Krotka ``FoldResult`` w kolejności fold_id.
        folds_df: DataFrame z jednym rzędem per fold (indeks: fold_id).
        distribution: DataFrame rzędy mean/median/std/min/max/mvp_threshold,
            kolumny: metryki z ``MetricsSummary``.
        stitched_equity: Rebased + compounded OOS equity curve.
        mvp_pass: 4 boole — czy mean per metric spełnia próg Phase 2.
        elapsed_seconds: Czas wykonania całego walk-forward.
    """

    wf_run_id: str
    config: WalkForwardConfig
    symbol: str
    timeframe: str
    strategy: str
    params: dict[str, Any]
    folds: tuple[FoldResult, ...]
    folds_df: pd.DataFrame
    distribution: pd.DataFrame
    stitched_equity: pd.DataFrame
    mvp_pass: dict[str, bool]
    elapsed_seconds: float


# =====================================================================
# Helpers — index / bar conversion
# =====================================================================


def _median_dt(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Mediana odstępów między kolejnymi timestampami w indeksie."""
    diffs = pd.Series(index).diff().dropna()
    if diffs.empty:
        raise ValueError("Index zbyt krótki by policzyć median delta")
    median = diffs.median()
    if not isinstance(median, pd.Timedelta):
        # pandas zwraca Timedelta dla diff() na DatetimeIndex; defensywnie
        raise TypeError(f"median delta nie jest Timedelta: {type(median)}")
    return median


def _check_uniform(index: pd.DatetimeIndex, threshold: float = 0.10) -> None:
    """Loguje warning gdy odstępy w indeksie nie są jednorodne.

    Args:
        index: ``DatetimeIndex`` do sprawdzenia.
        threshold: Próg coefficient of variation (default 10%). Powyżej
            tej wartości pojawia się ``logger.warning`` — konwersja
            ``pd.Timedelta`` → bars przez medianę staje się niedokładna.
    """
    diffs = pd.Series(index).diff().dropna()
    if diffs.empty:
        return
    median = diffs.median()
    if median == pd.Timedelta(0):
        return
    # CoV: średnia absolutnej dewiacji od mediany / mediana
    cov = float((diffs - median).abs().mean() / median)
    if cov > threshold:
        logger.warning(
            "Non-uniform bar spacing detected",
            extra={
                "cov": cov,
                "median_dt": str(median),
                "threshold": threshold,
            },
        )


def _to_bars(value: int | pd.Timedelta, median_dt: pd.Timedelta) -> int:
    """Konwertuje ``int | pd.Timedelta`` na liczbę bars.

    Args:
        value: ``int`` (bars, pass-through) lub ``pd.Timedelta``.
        median_dt: Mediana odstępu między barami w indeksie.

    Returns:
        Dodatnia liczba bars.

    Raises:
        ValueError: Gdy wartość nie-dodatnia albo Timedelta < median_dt.
        TypeError: Nieobsługiwany typ.
    """
    if isinstance(value, bool):  # bool to int subclass — łapiemy żeby nie myliło
        raise TypeError(f"bool jest niedozwolony, użyj int lub pd.Timedelta: {value}")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"liczba bars musi być dodatnia, dostalem {value}")
        return value
    if isinstance(value, pd.Timedelta):
        if value <= pd.Timedelta(0):
            raise ValueError(f"Timedelta musi być dodatni, dostalem {value}")
        n = int(value // median_dt)
        if n <= 0:
            raise ValueError(
                f"Timedelta {value} < median_dt {median_dt} → 0 bars (okno za krotkie)"
            )
        return n
    raise TypeError(f"nieobslugiwany typ dla _to_bars: {type(value).__name__}")


def _format_fold_id(fold_id: int) -> str:
    """Format ``fold_<i:03d>`` dla output paths i log messages."""
    return f"fold_{fold_id:03d}"


# =====================================================================
# Fold generation
# =====================================================================


def compute_expected_folds(index: pd.DatetimeIndex, config: WalkForwardConfig) -> int:
    """Liczy oczekiwany fold count bez generowania pełnego splitu.

    Args:
        index: ``DatetimeIndex`` z danych OHLCV.
        config: Konfiguracja walk-forward.

    Returns:
        Oczekiwana liczba foldów (>= 0). 0 oznacza że dane są za krótkie.
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError("index musi być DatetimeIndex")
    if len(index) == 0:
        return 0
    median = _median_dt(index)
    train_bars = _to_bars(config.train, median)
    test_bars = _to_bars(config.test, median)
    step_bars = _to_bars(config.step if config.step is not None else config.test, median)
    n = len(index)
    if n < train_bars + test_bars:
        return 0
    # Pierwszy fold zajmuje train_bars + test_bars. Każdy kolejny +step_bars.
    available_after_first = n - (train_bars + test_bars)
    extra = available_after_first // step_bars
    return 1 + int(extra)


def generate_folds(index: pd.DatetimeIndex, config: WalkForwardConfig) -> tuple[Fold, ...]:
    """Generuje sekwencję foldów na podstawie indeksu i konfiguracji.

    Args:
        index: ``DatetimeIndex`` z danych OHLCV (monotonicznie rosnący).
        config: Konfiguracja walk-forward.

    Returns:
        Niepusta krotka ``Fold`` w kolejności fold_id (0, 1, 2, ...).

    Raises:
        TypeError: ``index`` nie jest ``DatetimeIndex``.
        ValueError: ``index`` pusty / niemonotoniczny / dane za krótkie /
            zerowa lub jedyna jedna iteracja foldów (single fold == backtest)
            / step > train+test (degenerate).
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError("index musi być DatetimeIndex")
    if len(index) == 0:
        raise ValueError("index jest pusty")
    if not index.is_monotonic_increasing:
        raise ValueError("index musi być monotonicznie rosnący")
    _check_uniform(index)

    median = _median_dt(index)
    train_bars = _to_bars(config.train, median)
    test_bars = _to_bars(config.test, median)
    step_bars = _to_bars(config.step if config.step is not None else config.test, median)

    # Walidacje step (§9 ADR-009)
    if step_bars > train_bars + test_bars:
        raise ValueError(
            f"step ({step_bars} bars) > train+test ({train_bars + test_bars} bars) "
            f"— degenerate config, fold count ~0"
        )
    if step_bars < test_bars:
        logger.warning(
            "step < test → overlapping test windows; folds NIE są statystycznie niezależne",
            extra={"step_bars": step_bars, "test_bars": test_bars},
        )
    elif step_bars > test_bars:
        logger.warning(
            "step > test → gaps; niektóre bary nigdy nie są testowane OOS",
            extra={"step_bars": step_bars, "test_bars": test_bars},
        )

    n = len(index)
    if n < train_bars + test_bars:
        raise ValueError(f"dane za krótkie: potrzeba {train_bars + test_bars} bars, jest {n}")

    expected = compute_expected_folds(index, config)
    if expected == 0:
        raise ValueError("dane za krótkie na żaden fold pod tę konfigurację")
    if expected == 1:
        raise ValueError("single fold to nie walk-forward — użyj run_backtest bezpośrednio")
    if expected < config.min_folds_warn:
        logger.warning(
            f"expected folds < {config.min_folds_warn} — Phase 2 MVP wymaga "
            f">={config.min_folds_warn} dla statystycznej istotności",
            extra={"expected": expected, "min_folds_warn": config.min_folds_warn},
        )

    folds: list[Fold] = []
    for fold_id in range(expected):
        # test window: ostatni bar test = train + test + step*fold_id - 1
        test_end_idx = train_bars + test_bars + step_bars * fold_id - 1
        test_start_idx = test_end_idx - test_bars + 1
        if config.mode == "rolling":
            train_end_idx = test_start_idx - 1
            train_start_idx = train_end_idx - train_bars + 1
        elif config.mode == "anchored":
            train_start_idx = 0
            train_end_idx = test_start_idx - 1
        else:
            raise ValueError(f"nieznany mode: {config.mode}")

        # Granice — po expected liczbie iteracji indeksy powinny być valid,
        # ale defensywnie sprawdzamy. Jeśli wypada poza n, kończymy.
        if test_end_idx >= n or train_start_idx < 0 or train_end_idx < 0:
            break

        fold = Fold(
            fold_id=fold_id,
            train_start=index[train_start_idx],
            train_end=index[train_end_idx],
            test_start=index[test_start_idx],
            test_end=index[test_end_idx],
        )
        # Invariant: test_start > train_end (no leakage). Strict.
        if not (fold.test_start > fold.train_end):
            raise AssertionError(
                f"leakage in fold {fold_id}: test_start {fold.test_start} "
                f"not > train_end {fold.train_end}"
            )
        folds.append(fold)

    if not folds:
        raise ValueError("nie udało się wygenerować żadnego foldu (logika brzegowa)")

    return tuple(folds)


# =====================================================================
# Execution
# =====================================================================


def run_fold(
    fold: Fold,
    data: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    params: dict[str, Any],
    risk_limits: RiskLimits | None,
    cash: float = DEFAULT_CASH,
    commission: float = DEFAULT_COMMISSION,
    microstructure: MicrostructureConfig | None = None,
) -> FoldResult:
    """Wykonuje pojedynczy fold przez ``run_backtest`` na test slice.

    Każdy fold ma świeży ``RiskState`` przez konstrukcję — ``run_backtest``
    woła ``init_state`` na pierwszym barze (decyzja §5 ADR-009).

    Args:
        fold: Definicja foldu (z ``generate_folds``).
        data: Pełne dane OHLCV (test slice tnięty wewnętrznie przez ``.loc``).
        symbol: Symbol — przekazywany do ``run_backtest`` dla metadanych.
        timeframe: Timeframe — j.w.
        strategy: Nazwa strategii.
        params: Parametry strategii.
        risk_limits: Opcjonalne limity ryzyka.
        cash: Kapitał startowy w foldzie (rebase do tego w stitched curve).
        commission: Prowizja per trade jako fraction.

    Returns:
        ``FoldResult`` z metrykami, raw equity/trades, breach info i
        liczbą boundary closes.

    Raises:
        ValueError: Test slice jest pusty (możliwe gdy fold poza zakresem).
    """
    test_data = data.loc[fold.test_start : fold.test_end].copy()
    if test_data.empty:
        raise ValueError(f"fold {fold.fold_id}: pusty test slice")

    logger.debug(
        "running fold",
        extra={
            "fold_id": fold.fold_id,
            "train_range": [str(fold.train_start), str(fold.train_end)],
            "test_range": [str(fold.test_start), str(fold.test_end)],
            "n_bars_test": len(test_data),
        },
    )

    stats, equity, trades = run_backtest(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        params=params,
        data=test_data,
        risk_limits=risk_limits,
        cash=cash,
        commission=commission,
        microstructure=microstructure,
    )

    # MetricsSummary z OOS equity — preferuj post-microstructure gdy dostępne
    # (ADR-011): Equity_adjusted / pnl_post. Inaczej raw (backward-compatible).
    if "Equity_adjusted" in equity.columns:
        equity_series = equity["Equity_adjusted"]
    elif "Equity" in equity.columns:
        equity_series = equity["Equity"]
    else:
        equity_series = equity.iloc[:, 0]
    pnl_col = "pnl_post" if "pnl_post" in trades.columns else "PnL"
    trades_pnl: pd.Series | None = (
        trades[pnl_col] if (not trades.empty and pnl_col in trades.columns) else None
    )
    metrics = summarize(equity_series, trades_pnl)

    # Boundary closes — trade'y zamknięte dokładnie na test_end (§6 ADR-009)
    boundary_closes = 0
    if not trades.empty and "ExitTime" in trades.columns:
        end_ts = pd.Timestamp(fold.test_end)
        exit_times = pd.to_datetime(trades["ExitTime"])
        boundary_closes = int((exit_times == end_ts).sum())

    if metrics.n_trades > 0 and boundary_closes >= 0.5 * metrics.n_trades:
        logger.warning(
            f"fold {fold.fold_id}: boundary_closes dominuje "
            f"({boundary_closes}/{metrics.n_trades}) — test window prawdopodobnie za krótkie",
            extra={
                "fold_id": fold.fold_id,
                "boundary_closes": boundary_closes,
                "n_trades": metrics.n_trades,
            },
        )

    risk_breach = stats.get("_risk_breach")

    logger.info(
        f"fold {fold.fold_id} completed",
        extra={
            "fold_id": fold.fold_id,
            "n_trades": metrics.n_trades,
            "sharpe": metrics.sharpe,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "boundary_closes": boundary_closes,
            "risk_breach": risk_breach is not None,
        },
    )

    return FoldResult(
        fold=fold,
        metrics=metrics,
        equity=equity,
        trades=trades,
        risk_breach=risk_breach,
        boundary_closes=boundary_closes,
        n_trades=metrics.n_trades,
    )


# =====================================================================
# Aggregation
# =====================================================================


def build_folds_df(folds: Sequence[FoldResult]) -> pd.DataFrame:
    """Zbiera per-fold MetricsSummary w jednym DataFrame.

    Zwracany DataFrame jest indeksowany po ``fold_id``; kolumny zawierają
    train/test ranges, ``boundary_closes``, ``risk_breach_kind`` i pełen
    set pól z ``MetricsSummary``.
    """
    rows: list[dict[str, Any]] = []
    for fr in folds:
        row: dict[str, Any] = {
            "fold_id": fr.fold.fold_id,
            "train_start": fr.fold.train_start,
            "train_end": fr.fold.train_end,
            "test_start": fr.fold.test_start,
            "test_end": fr.fold.test_end,
            "boundary_closes": fr.boundary_closes,
            "risk_breach_kind": (fr.risk_breach["kind"] if fr.risk_breach else None),
        }
        # mirror wszystkie pola MetricsSummary
        for field_name, field_val in asdict(fr.metrics).items():
            row[field_name] = field_val
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    risk_breach_kind = [
        None if isinstance(v, float) and math.isnan(v) else v
        for v in df["risk_breach_kind"].astype(object).to_list()
    ]
    df["risk_breach_kind"] = pd.Series(risk_breach_kind, index=df.index, dtype=object)
    return df.set_index("fold_id")


def build_distribution(folds_df: pd.DataFrame) -> pd.DataFrame:
    """Distribution stats (mean/median/std/min/max) + rząd mvp_threshold.

    Args:
        folds_df: Output z ``build_folds_df`` (indeks: fold_id).

    Returns:
        DataFrame z rzędami ``["mean","median","std","min","max","mvp_threshold"]``
        i kolumnami z numerycznych pól ``MetricsSummary``.
    """
    metric_cols = [
        "total_return",
        "cagr",
        "sharpe",
        "sortino",
        "calmar",
        "mar",
        "max_drawdown_pct",
        "max_drawdown_duration_days",
        "recovery_time_days",
        "profit_factor",
        "win_rate",
        "n_trades",
    ]
    available = [c for c in metric_cols if c in folds_df.columns]
    if not available:
        return pd.DataFrame()
    sub = folds_df[available]
    # ``recovery_time_days`` może być ``inf`` gdy fold nigdy nie odzyskał
    # peak'u (MetricsSummary sentinel z ADR-007 §8). ``.std()`` na kolumnie
    # z ``inf`` produkuje RuntimeWarning z numpy (poprawnie NaN jako wynik,
    # ale hałas w logach). Replace inf → NaN przed agregacją — NaN
    # propaguje się czysto przez wszystkie agregacje bez ostrzeżeń.
    sub = sub.replace([float("inf"), float("-inf")], float("nan"))
    dist = pd.DataFrame(
        {
            "mean": sub.mean(numeric_only=True),
            "median": sub.median(numeric_only=True),
            "std": sub.std(numeric_only=True),
            "min": sub.min(numeric_only=True),
            "max": sub.max(numeric_only=True),
        }
    ).T  # rzędy: agregacje, kolumny: metryki
    # mvp_threshold row — tylko te 4 metryki które mają zdefiniowany próg
    mvp_row: dict[str, float] = dict.fromkeys(available, float("nan"))
    for k, v in MVP_THRESHOLDS.items():
        if k in mvp_row:
            mvp_row[k] = v
    dist.loc["mvp_threshold"] = pd.Series(mvp_row)
    return dist


def compute_mvp_pass(distribution: pd.DataFrame) -> dict[str, bool]:
    """Zwraca 4 boole — czy mean per metric spełnia próg Phase 2.

    Wszystkie 4 progi w ``MVP_THRESHOLDS`` używają kierunku "mean >= threshold"
    (z DD jako liczbą ujemną — ``-0.20 >= -0.25`` znaczy "loss 20% jest OK").
    """
    out: dict[str, bool] = {}
    if "mean" not in distribution.index:
        return dict.fromkeys(MVP_THRESHOLDS, False)
    means = distribution.loc["mean"]
    for k, threshold in MVP_THRESHOLDS.items():
        if k not in means.index:
            out[k] = False
            continue
        try:
            # means[k] to skalar, ale pandas-stubs typuje Series.__getitem__ jako
            # Any | Series — cast informuje mypy, że to float (no-op w runtime).
            val = float(cast(float, means[k]))
        except (TypeError, ValueError):
            out[k] = False
            continue
        if math.isnan(val):
            out[k] = False
            continue
        out[k] = val >= threshold
    return out


def stitch_equity(folds: Sequence[FoldResult], initial_cash: float = 100_000.0) -> pd.DataFrame:
    """Rebase + compound per-fold OOS equity w continuous curve.

    Algorytm (§8 ADR-009):

    Per fold i: ``fold_return_i = equity[-1] / equity[0] - 1``.
    Skumulowany kapitał po foldzie i: ``cum_i = initial * Π(1 + r_k for k <= i)``.
    Wewnątrz foldu i punkty są skalowane mnożnikowo:
    ``stitched[t] = (equity[t] / equity[0]) * cum_(i-1)``.

    Args:
        folds: Sekwencja ``FoldResult`` w kolejności fold_id.
        initial_cash: Kapitał startowy dla foldu 0.

    Returns:
        DataFrame z kolumnami ``timestamp``, ``equity``, ``fold_id``.
        Pusty DataFrame gdy ``folds`` jest pusty.
    """
    if not folds:
        return pd.DataFrame(columns=["timestamp", "equity", "fold_id"])

    rows: list[dict[str, Any]] = []
    cum_capital = float(initial_cash)
    for fr in folds:
        # Preferuj post-microstructure (ADR-011) — spójnie z metrykami foldu.
        if "Equity_adjusted" in fr.equity.columns:
            eq = fr.equity["Equity_adjusted"]
        elif "Equity" in fr.equity.columns:
            eq = fr.equity["Equity"]
        else:
            eq = fr.equity.iloc[:, 0]
        if eq.empty:
            continue
        eq0 = float(eq.iloc[0])
        if eq0 == 0.0:
            logger.warning(
                f"fold {fr.fold.fold_id}: equity startuje od 0, pomijam w stitch",
                extra={"fold_id": fr.fold.fold_id},
            )
            continue
        scaled = (eq / eq0) * cum_capital
        for ts, val in scaled.items():
            rows.append(
                {
                    "timestamp": ts,
                    "equity": float(val),
                    "fold_id": fr.fold.fold_id,
                }
            )
        # update kapitał skumulowany returnem fold'u
        fold_return = float(eq.iloc[-1] / eq0 - 1)
        cum_capital = cum_capital * (1 + fold_return)

    return pd.DataFrame(rows)


# =====================================================================
# Top-level orchestrator
# =====================================================================


def walk_forward(
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    params: dict[str, Any],
    config: WalkForwardConfig,
    data: pd.DataFrame | None = None,
    cash: float = DEFAULT_CASH,
    commission: float = DEFAULT_COMMISSION,
    wf_run_id: str | None = None,
    save: bool = True,
) -> WalkForwardReport:
    """Top-level entry: generuje foldy, wykonuje, agreguje, opcjonalnie zapisuje.

    Args:
        symbol: Symbol instrumentu (np. ``"BTC/USDT"``).
        timeframe: Timeframe (np. ``"1h"``).
        strategy: Nazwa strategii (slug w ``strategies/``).
        params: Parametry strategii.
        config: Konfiguracja walk-forward.
        data: Opcjonalne pre-loaded OHLCV (omija ``load_ohlcv_csv``).
            Używane przez testy deterministyczne — w produkcji ``None``.
        cash: Kapitał startowy per fold.
        commission: Prowizja per trade.
        wf_run_id: Opcjonalny eksplicytny run_id (test/reproducibility).
            ``None`` → generowane automatycznie z timestamp UTC.
        save: ``True`` → zapis artefaktów do ``results/walkforward/<wf_run_id>/``.

    Returns:
        ``WalkForwardReport`` z pełnym setem agregatów.
    """
    t_start = time.time()

    if data is None:
        df = load_ohlcv_csv(symbol, timeframe)
    else:
        df = data.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("data musi mieć DatetimeIndex")
        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(df.columns):
            raise ValueError(f"data brak kolumn: {need - set(df.columns)}")

    if wf_run_id is None:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        symbol_clean = symbol.replace("/", "")
        wf_run_id = f"wf_{symbol_clean}_{timeframe}_{strategy}_{ts}"

    df_index = df.index
    if not isinstance(df_index, pd.DatetimeIndex):
        raise ValueError("data musi mieć DatetimeIndex")

    folds = generate_folds(df_index, config)

    logger.info(
        "walk-forward starting",
        extra={
            "wf_run_id": wf_run_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": strategy,
            "n_folds_expected": len(folds),
            "mode": config.mode,
        },
    )

    fold_results: list[FoldResult] = []
    for fold in folds:
        fr = run_fold(
            fold=fold,
            data=df,
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            params=params,
            risk_limits=config.risk_limits,
            cash=cash,
            commission=commission,
            microstructure=config.microstructure,
        )
        fold_results.append(fr)

    folds_df = build_folds_df(fold_results)
    distribution = build_distribution(folds_df)
    mvp_pass = compute_mvp_pass(distribution)
    stitched = stitch_equity(fold_results, initial_cash=cash)
    elapsed = time.time() - t_start

    report = WalkForwardReport(
        wf_run_id=wf_run_id,
        config=config,
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        params=params,
        folds=tuple(fold_results),
        folds_df=folds_df,
        distribution=distribution,
        stitched_equity=stitched,
        mvp_pass=mvp_pass,
        elapsed_seconds=elapsed,
    )

    logger.info(
        "walk-forward completed",
        extra={
            "wf_run_id": wf_run_id,
            "n_folds_executed": len(fold_results),
            "mvp_pass": mvp_pass,
            "elapsed_seconds": elapsed,
        },
    )

    if save:
        save_report(report, os.path.join(WF_OUT_DIR, wf_run_id))

    return report


# =====================================================================
# I/O
# =====================================================================


def _serialise_config(config: WalkForwardConfig) -> dict[str, Any]:
    """Konwertuje ``WalkForwardConfig`` na JSON-friendly dict."""
    out: dict[str, Any] = {}
    for k, v in asdict(config).items():
        if isinstance(v, pd.Timedelta):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _replace_nan_inf(obj: Any) -> Any:
    """Rekurencyjnie zamienia NaN/inf na None (JSON-safe)."""
    if isinstance(obj, dict):
        return {k: _replace_nan_inf(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_replace_nan_inf(x) for x in obj]
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if obj in (float("inf"), float("-inf")):
            return None
        return obj
    return obj


def _save_fold_outputs(fold_dir: Path, fr: FoldResult) -> None:
    """Zapisuje summary.json / equity.csv / trades.csv dla pojedynczego foldu."""
    fold_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "fold_id": fr.fold.fold_id,
        "train_start": str(fr.fold.train_start),
        "train_end": str(fr.fold.train_end),
        "test_start": str(fr.fold.test_start),
        "test_end": str(fr.fold.test_end),
        "n_trades": fr.n_trades,
        "boundary_closes": fr.boundary_closes,
        "risk_breach": fr.risk_breach,
        "metrics": asdict(fr.metrics),
    }
    summary = _replace_nan_inf(summary)
    (fold_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    fr.equity.to_csv(fold_dir / "equity.csv")
    fr.trades.to_csv(fold_dir / "trades.csv", index=False)


def save_report(report: WalkForwardReport, out_dir: str | Path) -> Path:
    """Zapisuje pełen set artefaktów walk-forward do ``out_dir``.

    Struktura output (§7 ADR-009)::

        <out_dir>/
        ├── walkforward_summary.json
        ├── walkforward_folds.csv
        ├── walkforward_distribution.csv
        ├── walkforward_equity.csv
        └── fold_<i>/
            ├── summary.json
            ├── equity.csv
            └── trades.csv
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for fr in report.folds:
        _save_fold_outputs(out / _format_fold_id(fr.fold.fold_id), fr)

    summary: dict[str, Any] = {
        "wf_run_id": report.wf_run_id,
        "config": _serialise_config(report.config),
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "strategy": report.strategy,
        "params": report.params,
        "n_folds": len(report.folds),
        "mvp_pass": report.mvp_pass,
        "distribution": report.distribution.to_dict(),
        "elapsed_seconds": report.elapsed_seconds,
    }
    summary = _replace_nan_inf(summary)
    (out / "walkforward_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    report.folds_df.to_csv(out / "walkforward_folds.csv")
    report.distribution.to_csv(out / "walkforward_distribution.csv")
    report.stitched_equity.to_csv(out / "walkforward_equity.csv", index=False)

    logger.info("walk-forward artefacts saved", extra={"out_dir": str(out)})
    return out


# =====================================================================
# CLI
# =====================================================================


def _parse_window(val: str) -> int | pd.Timedelta:
    """Parsuje wartość okna z CLI.

    ``'8760'`` → ``int(8760)`` (bars). ``'365d'`` / ``'12h'`` / ``'5min'`` →
    ``pd.Timedelta``.

    Forward-compat z pandas 4: lowercase trailing ``'d'`` jest deprecowany
    (pandas oczekuje uppercase ``'D'``). Normalizujemy defensywnie żeby
    użytkownik CLI mógł nadal pisać ``--train 365d`` bez ostrzeżeń.
    """
    try:
        return int(val)
    except ValueError:
        pass
    # Pandas 4 deprecates lowercase 'd' for days — normalize defensively.
    # Inne jednostki ('h', 'min', 's') pozostają lower-case (pandas nadal je akceptuje).
    normalized = val
    if normalized.endswith("d") and not normalized.endswith(("ed", "id", "nd")):
        normalized = normalized[:-1] + "D"
    try:
        return pd.Timedelta(normalized)
    except Exception as e:
        raise ValueError(f"nie mogę zparsować wartości okna: {val!r}") from e


def parse_args() -> Any:
    """Argparse setup dla ``algo-walkforward``."""
    import argparse

    ap = argparse.ArgumentParser(
        description="algo-walkforward — walk-forward analyzer (ADR-009)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--symbol", required=True, help="np. BTC/USDT")
    ap.add_argument("--timeframe", required=True, help="np. 1h, 4h, 1d")
    ap.add_argument("--strategy", required=True, help="nazwa modułu strategii")
    ap.add_argument(
        "--params", default="{}", help="JSON z parametrami strategii (np. '{\"period\": 14}')"
    )
    ap.add_argument(
        "--train",
        required=True,
        help="int bars lub pd.Timedelta string (np. '365d', '12h', '8760')",
    )
    ap.add_argument(
        "--test",
        required=True,
        help="int bars lub pd.Timedelta string (np. '90d', '2160')",
    )
    ap.add_argument(
        "--step",
        default=None,
        help="int bars / pd.Timedelta string. Brak → step = test (no-overlap).",
    )
    ap.add_argument(
        "--mode",
        choices=["rolling", "anchored"],
        default="rolling",
        help="rolling = sliding train; anchored = expanding train od początku",
    )
    ap.add_argument(
        "--min_folds_warn",
        type=int,
        default=5,
        help="próg ostrzeżenia gdy expected_folds < this",
    )
    # Defaults z backtester.py (single source of truth)
    ap.add_argument("--cash", type=float, default=DEFAULT_CASH)
    ap.add_argument("--commission", type=float, default=DEFAULT_COMMISSION)
    # Risk flags (ADR-008)
    ap.add_argument(
        "--max_dd_pct",
        type=float,
        default=None,
        help="Max drawdown stop per fold jako fraction (np. 0.20 = 20%%)",
    )
    ap.add_argument(
        "--daily_loss_pct",
        type=float,
        default=None,
        help="Daily loss limit jako fraction (np. 0.05 = 5%%)",
    )
    ap.add_argument(
        "--risk_per_trade_pct",
        type=float,
        default=None,
        help="%% equity per trade dla position_size helper (strategia musi zawołać)",
    )
    ap.add_argument(
        "--daily_reset_tz",
        type=str,
        default="UTC",
        help="IANA timezone dla daily reset",
    )
    ap.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    # Microstructure flags (ADR-011) — stosowane per fold
    ap.add_argument(
        "--microstructure",
        choices=["none", "full"],
        default="full",
        help="Master switch korekt mikrostruktury per fold. full = slippage + funding.",
    )
    ap.add_argument(
        "--slip_bps",
        type=float,
        default=1.0,
        help="Slippage per side w bps, na TOP of fee. Default 1.0.",
    )
    ap.add_argument(
        "--funding_source",
        choices=["historical", "synthetic", "none"],
        default="historical",
    )
    ap.add_argument(
        "--funding_rate_synthetic",
        type=float,
        default=0.0001,
        help="Stały funding rate per 8h dla synthetic/fallback (default 0.0001).",
    )
    return ap.parse_args()


def main() -> None:
    """Entry point dla ``algo-walkforward``."""
    args = parse_args()
    setup_logging(level=getattr(logging, args.log_level))

    try:
        params: dict[str, Any] = json.loads(args.params)
    except Exception as e:
        raise SystemExit(f"Niepoprawny JSON w --params: {e}") from e

    train = _parse_window(args.train)
    test = _parse_window(args.test)
    step = _parse_window(args.step) if args.step is not None else None

    risk_limits: RiskLimits | None = None
    if any(v is not None for v in (args.max_dd_pct, args.daily_loss_pct, args.risk_per_trade_pct)):
        risk_limits = RiskLimits(
            max_drawdown_pct=args.max_dd_pct,
            daily_loss_pct=args.daily_loss_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            daily_reset_tz=args.daily_reset_tz,
        )

    microstructure = MicrostructureConfig(
        enabled=(args.microstructure == "full"),
        slip_bps=args.slip_bps,
        funding_source=args.funding_source,
        funding_rate_synthetic=args.funding_rate_synthetic,
    )

    config = WalkForwardConfig(
        train=train,
        test=test,
        step=step,
        mode=args.mode,
        min_folds_warn=args.min_folds_warn,
        risk_limits=risk_limits,
        microstructure=microstructure,
    )

    report = walk_forward(
        symbol=args.symbol,
        timeframe=args.timeframe,
        strategy=args.strategy,
        params=params,
        config=config,
        cash=args.cash,
        commission=args.commission,
    )

    print(f"walk-forward done: {report.wf_run_id}")
    print(f"folds executed: {len(report.folds)}")
    print(f"MVP pass: {report.mvp_pass}")
    print(f"results: {os.path.join(WF_OUT_DIR, report.wf_run_id)}")


if __name__ == "__main__":
    main()
