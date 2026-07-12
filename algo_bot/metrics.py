"""
algo_bot/metrics.py

Risk-adjusted metrics dla algo_bot — Sharpe, Sortino, Calmar, MAR, profit factor,
recovery time + helpery (CAGR, total return, max drawdown, win rate). Pure functions
nad ``pd.Series`` z equity / per-trade PnL. Bez zależności od ``backtesting.py`` —
agnostyczne wobec silnika.

Decyzja: patrz docs/adr/007-risk-adjusted-metrics.md.

Kluczowe konwencje:

- **Input shape.** Wszystkie metryki annualizowane przyjmują ``equity: pd.Series``
  z ``DatetimeIndex``. Konwersja equity → returns jest jednoznaczna, odwrotna nie.
  Metryki transakcyjne (``profit_factor``, ``win_rate``) biorą ``trades_pnl: pd.Series``.

- **Log returns vs simple returns.** Sharpe i Sortino używają **log returns**
  wewnętrznie (additywne w czasie → annualizacja przez ``√ppy`` jest dokładna,
  nie aproksymacja). ``total_return``, ``cagr`` i statystyki per-trade używają
  **simple returns** (intuicyjne, zgodne z narracją raportów).

- **Annualizacja.** Każda annualizowana metryka przyjmuje ``periods_per_year``.
  Default ``None`` → ``infer_periods_per_year(index, calendar="crypto")``: mediana
  odstępu między barami, podzielone na 365 dni dla crypto (24/7). Fallback przy
  braku możliwości oszacowania: 365 z ``logger.warning``.

- **Risk-free rate.** ``rf=0.0`` domyślnie (crypto nie ma kanonicznego benchmarku).

- **Calmar vs MAR.** Dwie osobne metryki, nie aliasy:
    * ``calmar`` — ``CAGR / |maxDD|`` na **trailing 36 miesiącach** (Young 1991).
      Przy krótszej serii fallback na całość equity z warningiem.
    * ``mar_ratio`` — ``CAGR / |maxDD|`` na **całej historii** (Managed Account
      Reports 1978).

- **Edge cases.** Wszystkie "metryka niezdefiniowana" zwracają ``NaN`` + emitują
  ``logger.warning``. Konkretnie: zero losing trades, zero drawdown, zero variance,
  pusta seria. NaN > +inf — sygnalizuje "nie liczone, mała próbka" zamiast
  cichego błędu w sortowaniu/porównaniach.

- **Recovery time.** Od dna max DD do nowego high. ``pd.Timedelta`` primary;
  ``pd.Timedelta.max`` jako sentinel "nigdy nie odzyskano" (w ``MetricsSummary``
  zamapowane na ``float('inf')`` dla serializacji).

Public API:
    Helpery:
        infer_periods_per_year(index, calendar) -> float
        log_returns(equity) -> pd.Series
        simple_returns(equity) -> pd.Series

    Metryki:
        total_return(equity) -> float
        cagr(equity, periods_per_year=None) -> float
        sharpe(equity, periods_per_year=None, rf=0.0) -> float
        rolling_sharpe(equity, window=30, periods_per_year=None, rf=0.0) -> pd.Series
        sortino(equity, periods_per_year=None, mar=0.0) -> float
        max_drawdown(equity) -> tuple[float, pd.Timedelta]
        calmar(equity, periods_per_year=None, window_months=36) -> float
        mar_ratio(equity, periods_per_year=None) -> float
        recovery_time(equity) -> pd.Timedelta
        profit_factor(trades_pnl) -> pd.Series
        win_rate(trades_pnl) -> float

    Agregacja:
        MetricsSummary (dataclass, frozen)
        summarize(equity, trades_pnl=None, periods_per_year=None, rf=0.0, mar_target=0.0) -> MetricsSummary

    Cross-strategy (portfolio-oriented, extension scope):
        strategy_correlation(equities, method, on) -> pd.DataFrame
        mean_pairwise_correlation(corr_matrix) -> float

See also:
    docs/adr/007-risk-adjusted-metrics.md (rationale, alternatives, edge cases)
    docs/adr/005-backtesting-py-mvp-engine.md (equity/trades shapes consumed here)
    docs/adr/006-logging-strategy.md (logger uzywany do warningow edge case)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
import pandas as pd

from algo_bot.log import get_logger

logger = get_logger(__name__)


# ============================================================================
# Stale
# ============================================================================

# Dni w roku dla kalendarza "crypto" (24/7 trading, brak weekendow ani swiat).
_DAYS_PER_YEAR_CRYPTO: int = 365

# Dni w roku dla kalendarza TradFi (252 dni handlowe).
_DAYS_PER_YEAR_TRADFI: int = 252

# Fallback ppy gdy inferencja zawiedzie. Wybor: crypto-default 365.
_DEFAULT_PERIODS_PER_YEAR: float = float(_DAYS_PER_YEAR_CRYPTO)

# Liczba sekund na dobe (do konwersji Timedelta → days float).
_SECONDS_PER_DAY: float = 86_400.0


# ============================================================================
# Helpery konwersji
# ============================================================================


def infer_periods_per_year(
    index: pd.Index,
    calendar: Literal["crypto", "tradfi"] = "crypto",
) -> float:
    """Inferuje liczbe probek na rok z czestotliwosci ``DatetimeIndex``.

    Bierze mediane odstepu miedzy kolejnymi barami i dzieli przez nia
    odpowiednia liczbe dni w roku (365 dla ``crypto``, 252 dla ``tradfi``).

    Args:
        index: ``pd.DatetimeIndex``. Inne typy → fallback z warningiem.
        calendar: ``"crypto"`` (24/7, 365 dni) albo ``"tradfi"`` (252 dni handlowe).

    Returns:
        Liczba probek na rok jako ``float``. Przyklad: dla 1h barow w trybie
        ``"crypto"`` zwraca ``8760.0``; dla dziennych — ``365.0``.

    Edge cases:
        Przy non-DatetimeIndex / krótszej niż 2 elementy serii / non-positive
        median deltcie → ``_DEFAULT_PERIODS_PER_YEAR`` (365) + ``logger.warning``.
    """
    if not isinstance(index, pd.DatetimeIndex):
        logger.warning(
            "infer_periods_per_year: index nie jest DatetimeIndex — uzywam fallback 365",
            extra={"index_type": type(index).__name__},
        )
        return _DEFAULT_PERIODS_PER_YEAR

    if len(index) < 2:
        logger.warning(
            "infer_periods_per_year: za malo probek do inferencji — uzywam fallback 365",
            extra={"n_samples": len(index)},
        )
        return _DEFAULT_PERIODS_PER_YEAR

    deltas = pd.Series(index[1:] - index[:-1])
    median_delta = deltas.median()

    if median_delta <= pd.Timedelta(0):
        logger.warning(
            "infer_periods_per_year: non-positive median delta — uzywam fallback 365",
            extra={"median_delta": str(median_delta)},
        )
        return _DEFAULT_PERIODS_PER_YEAR

    days_per_year = _DAYS_PER_YEAR_CRYPTO if calendar == "crypto" else _DAYS_PER_YEAR_TRADFI
    return float(pd.Timedelta(days=days_per_year) / median_delta)


def log_returns(equity: pd.Series) -> pd.Series:
    """Zwraca log returns z serii equity (``log(eq_t / eq_{t-1})``).

    Args:
        equity: seria equity (musi byc dodatnia).

    Returns:
        ``pd.Series`` log returns o dlugosci ``len(equity) - 1`` (pierwszy element
        odrzucony przez ``.diff()``).

    Note:
        Log returns sa additywne w czasie. Standardowa konwencja w quant dla
        Sharpe/Sortino — annualizacja przez ``√n`` jest dokladna, nie aproksymacja.
    """
    # Logarytm equity <= 0 jest niezdefiniowany. Bez jawnego guarda pandas
    # odrzuca NaN-y po bankructwie, a Sharpe bywa wtedy liczony tylko na prefiksie
    # przed ruiną i może wyglądać dodatnio. Cała ścieżka log-return jest w takim
    # przypadku nieważna — fail closed zamiast cichego skracania próbki.
    values = pd.to_numeric(equity, errors="coerce")
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        logger.warning("log_returns: equity zawiera NaN/inf → pusta seria")
        return pd.Series(dtype=float, name=equity.name)
    if (values <= 0).any():
        logger.warning("log_returns: equity <= 0 (bankructwo) → pusta seria")
        return pd.Series(dtype=float, name=equity.name)

    # cast: numpy/pandas stubs widzą np.log(Series) jako ndarray → Any. Runtime
    # to pd.Series (numpy fall-through dla pandas operands), więc .diff()/.dropna()
    # są poprawne.
    return cast(pd.Series, np.log(values).diff().dropna())


def simple_returns(equity: pd.Series) -> pd.Series:
    """Zwraca simple returns (``eq_t / eq_{t-1} - 1``) z serii equity.

    Args:
        equity: seria equity.

    Returns:
        ``pd.Series`` simple returns o dlugosci ``len(equity) - 1``.

    Note:
        Uzywane dla narracji/raportow (intuicyjne procentowe zmiany).
        Dla annualizacji preferujemy ``log_returns``.
    """
    return equity.pct_change().dropna()


# ============================================================================
# Metryki kumulatywne
# ============================================================================


def total_return(equity: pd.Series) -> float:
    """Calkowity zwrot serii equity (``eq_end / eq_start - 1``).

    Args:
        equity: seria equity (start i end z dwoch koncow).

    Returns:
        Zwrot jako ulamek (np. ``0.25`` = 25%). Przy ``len < 2`` zwraca ``0.0``.
    """
    if len(equity) < 2:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: float | None = None) -> float:
    """Compound Annual Growth Rate.

    Liczone z dlugosci kalendarzowej okresu jesli ``equity`` ma ``DatetimeIndex``
    (dokladniejsze — odporne na nieregularne barki). Fallback z ``periods_per_year``
    dla integer index'ow.

    Args:
        equity: seria equity z ``DatetimeIndex`` (preferowane) lub regularna.
        periods_per_year: uzywane tylko gdy index nie jest ``DatetimeIndex``.
            Default ``None`` → inferencja z indexu albo 365.

    Returns:
        CAGR jako ulamek (np. ``0.15`` = 15% rocznie). NaN gdy ``len < 2``
        lub ``years <= 0``. ``-1.0`` przy blow-up'ie (equity ≤ 0 na koncu).
    """
    if len(equity) < 2:
        return float("nan")

    total = float(equity.iloc[-1] / equity.iloc[0])
    if total <= 0:
        return -1.0

    if isinstance(equity.index, pd.DatetimeIndex):
        years = (equity.index[-1] - equity.index[0]) / pd.Timedelta(days=_DAYS_PER_YEAR_CRYPTO)
    else:
        ppy = periods_per_year if periods_per_year is not None else _DEFAULT_PERIODS_PER_YEAR
        years = (len(equity) - 1) / ppy

    if years <= 0:
        return float("nan")

    return float(total ** (1.0 / years) - 1.0)


# ============================================================================
# Sharpe i Sortino
# ============================================================================


def sharpe(
    equity: pd.Series,
    periods_per_year: float | None = None,
    rf: float = 0.0,
) -> float:
    """Annualizowany Sharpe ratio (na log returns).

    Args:
        equity: seria equity.
        periods_per_year: liczba probek na rok. Default ``None`` → inferencja z
            ``equity.index`` (calendar="crypto") albo 365 z warningiem.
        rf: roczna risk-free rate jako ulamek (default 0.0 dla crypto).

    Returns:
        Sharpe ratio jako float. NaN gdy:
            - serie zwroty puste (mniej niz 2 barki)
            - zero variance (constant returns) — emitowany ``logger.warning``
    """
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(equity.index)

    rets = log_returns(equity)
    if rets.empty:
        logger.warning("sharpe: pusta seria zwrotow → NaN")
        return float("nan")

    std = float(rets.std(ddof=1))
    # Tolerance 1e-12 łapie też "praktycznie zerową" wariancję z floating-point
    # noise (np. equity rosnący geometrycznie ze stałym log_return — math.exp()
    # wprowadza ~1e-15 noise w log_returns, std jest niezerowe ale w skali
    # pikometra; bez tolerance Sharpe wybucha do 10^13). 1e-12 jest na tyle
    # małe że nie maskuje realnych std (typowe std log_returns dla strategii to
    # 0.01–0.05 czyli 10 rzędów wielkości wyżej).
    if math.isnan(std) or std < 1e-12:
        logger.warning(
            "sharpe: zero variance (constant returns) → NaN",
            extra={"n_returns": len(rets), "std": std},
        )
        return float("nan")

    rf_per_period = rf / periods_per_year
    excess_mean = float(rets.mean() - rf_per_period)
    return float(excess_mean / std * math.sqrt(periods_per_year))


def rolling_sharpe(
    equity: pd.Series,
    window: int = 30,
    periods_per_year: float | None = None,
    rf: float = 0.0,
) -> pd.Series:
    """Rolling Sharpe ratio na oknie ``window`` barow.

    Glowna metryka diagnostyczna do wykrywania overfittingu w walk-forward
    (Decision F) — gdy strategia dziala tylko w pewnych regimes, rolling Sharpe
    pokazuje wyrazne plateau/decay.

    Args:
        equity: seria equity z ``DatetimeIndex``.
        window: dlugosc okna w barach. Default 30 ≈ miesiac na barach dziennych.
        periods_per_year: jak w ``sharpe``.
        rf: jak w ``sharpe``.

    Returns:
        ``pd.Series`` annualizowanych Sharpe ratios. Pierwsze ``window-1`` wartosci
        to NaN (niewystarczajaco danych w oknie).
    """
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(equity.index)

    rets = log_returns(equity)
    rf_per_period = rf / periods_per_year
    excess = rets - rf_per_period

    rolling_mean = excess.rolling(window).mean()
    rolling_std = excess.rolling(window).std(ddof=1)

    return (rolling_mean / rolling_std) * math.sqrt(periods_per_year)


def sortino(
    equity: pd.Series,
    periods_per_year: float | None = None,
    mar: float = 0.0,
) -> float:
    """Annualizowany Sortino ratio.

    Sortino = (mean excess return) / (downside deviation). Penalizuje tylko
    odchylenia ponizej MAR (Minimum Acceptable Return), nie obu stron jak Sharpe.

    Args:
        equity: seria equity.
        periods_per_year: jak w ``sharpe``.
        mar: roczna Minimum Acceptable Return jako ulamek (default 0.0).

    Returns:
        Sortino ratio jako float. NaN gdy:
            - serie zwroty puste
            - brak downside (wszystkie zwroty >= MAR) — ``logger.warning``

    Note:
        Downside deviation = ``sqrt(mean((min(0, ret - mar))^2))`` — populacyjna
        wersja (dzielnik N, nie N-1) zgodnie ze standardem Sortino 1991.
    """
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(equity.index)

    rets = log_returns(equity)
    if rets.empty:
        logger.warning("sortino: pusta seria zwrotow → NaN")
        return float("nan")

    mar_per_period = mar / periods_per_year
    excess = rets - mar_per_period
    downside = excess.clip(upper=0.0)
    downside_dev = float(np.sqrt((downside**2).mean()))

    if downside_dev == 0 or math.isnan(downside_dev):
        logger.warning(
            "sortino: brak downside (wszystkie excess returns >= 0) → NaN",
            extra={"n_returns": len(rets), "mar": mar},
        )
        return float("nan")

    return float(excess.mean() / downside_dev * math.sqrt(periods_per_year))


# ============================================================================
# Drawdown i recovery
# ============================================================================


def max_drawdown(equity: pd.Series) -> tuple[float, pd.Timedelta]:
    """Maksymalny drawdown serii equity + jego trwanie (longest underwater).

    Drawdown w danym momencie t to ``equity_t / running_max_t - 1`` (wartosc w
    ``[-1, 0]``). Trwanie to najdluzszy ciagly okres "underwater" (equity ponizej
    biezacego peaku).

    Args:
        equity: seria equity z ``DatetimeIndex``.

    Returns:
        Tupla ``(max_dd_pct, longest_underwater_duration)``:
            - ``max_dd_pct``: float w ``[-1, 0]``. ``0.0`` gdy equity monotonicznie rosnie.
            - duration: ``pd.Timedelta``. ``Timedelta(0)`` przy braku DD.
    """
    if len(equity) < 2:
        return 0.0, pd.Timedelta(0)

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    dd_pct = float(drawdown.min())

    in_dd = drawdown < 0.0
    if not in_dd.any():
        return 0.0, pd.Timedelta(0)

    # Grupuj kolejne barki "underwater" / "above water" — dlugosc max grupy underwater.
    group_id = (in_dd != in_dd.shift()).cumsum()
    durations: list[pd.Timedelta] = []
    for _, group in equity.groupby(group_id):
        if in_dd.loc[group.index[0]] and len(group) >= 2:
            durations.append(group.index[-1] - group.index[0])

    longest = max(durations) if durations else pd.Timedelta(0)
    return dd_pct, longest


def recovery_time(equity: pd.Series) -> pd.Timedelta:
    """Czas od dna maksymalnego drawdownu do osiagniecia nowego high.

    Args:
        equity: seria equity z ``DatetimeIndex``.

    Returns:
        ``pd.Timedelta``. ``Timedelta(0)`` gdy nie bylo DD (lub serie za krotka).
        ``pd.Timedelta.max`` jako sentinel gdy equity nigdy nie wraca powyzej
        peaku poprzedzajacego dno (emitowany ``logger.warning``).
    """
    if len(equity) < 2:
        return pd.Timedelta(0)

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0

    if (drawdown == 0.0).all():
        return pd.Timedelta(0)

    trough_idx = drawdown.idxmin()
    peak_value = float(running_max.loc[trough_idx])

    # Forward od dna: pierwszy moment ge peak.
    forward = equity.loc[trough_idx:]
    recovered = forward[forward >= peak_value]

    if recovered.empty:
        logger.warning(
            "recovery_time: equity nie wrocila powyzej peaku — zwracam Timedelta.max",
            extra={"trough_ts": str(trough_idx), "peak_value": peak_value},
        )
        return pd.Timedelta.max

    # cast: Index.__getitem__ → Timestamp w runtime; stubs zwracają Any po
    # arytmetyce Timestamp - Timestamp = Timedelta.
    return cast(pd.Timedelta, recovered.index[0] - trough_idx)


# ============================================================================
# Calmar i MAR
# ============================================================================


def calmar(
    equity: pd.Series,
    periods_per_year: float | None = None,
    window_months: int = 36,
) -> float:
    """Calmar ratio: ``CAGR / |maxDD|`` na trailing ``window_months`` miesiacach.

    Konwencja Terry W. Young 1991 / MAR Capital — domyslnie 36 miesiecy. Przy
    serii krotszej niz okno, fallback na cala historie equity z ``logger.warning``.

    Args:
        equity: seria equity z ``DatetimeIndex``.
        periods_per_year: jak w ``sharpe``.
        window_months: dlugosc trailing okna w miesiacach. Default 36.

    Returns:
        Calmar ratio jako float. NaN gdy:
            - zero drawdown (``logger.warning``)
            - CAGR jest NaN
    """
    if len(equity) < 2 or not isinstance(equity.index, pd.DatetimeIndex):
        return float("nan")

    end = equity.index[-1]
    window_start = end - pd.DateOffset(months=window_months)
    series_start = equity.index[0]

    if series_start > window_start:
        logger.warning(
            "calmar: serie krotsza niz window_months — fallback na cala historie",
            extra={
                "window_months": window_months,
                "available_days": (end - series_start).days,
            },
        )
        sub = equity
    else:
        sub = equity.loc[window_start:]

    cg = cagr(sub, periods_per_year=periods_per_year)
    dd_pct, _ = max_drawdown(sub)

    if dd_pct == 0:
        logger.warning("calmar: zero drawdown w oknie → NaN")
        return float("nan")
    if math.isnan(cg):
        return float("nan")

    return float(cg / abs(dd_pct))


def mar_ratio(equity: pd.Series, periods_per_year: float | None = None) -> float:
    """MAR ratio: ``CAGR / |maxDD|`` na **calej** historii equity.

    Konwencja Managed Account Reports (1978). Rozni sie od Calmar tym, ze nie ma
    trailing okna — bierze cala historie. Na dlugich track recordach Calmar i MAR
    zbiegaja sie; na krotkich (≤36m) MAR uzywa wszystkiego, Calmar fallback'uje.

    Args:
        equity: seria equity.
        periods_per_year: jak w ``sharpe``.

    Returns:
        MAR ratio jako float. NaN gdy zero drawdown (``logger.warning``) albo
        gdy CAGR jest NaN.
    """
    if len(equity) < 2:
        return float("nan")

    cg = cagr(equity, periods_per_year=periods_per_year)
    dd_pct, _ = max_drawdown(equity)

    if dd_pct == 0:
        logger.warning("mar_ratio: zero drawdown → NaN")
        return float("nan")
    if math.isnan(cg):
        return float("nan")

    return float(cg / abs(dd_pct))


# ============================================================================
# Metryki per-trade
# ============================================================================


def profit_factor(trades_pnl: pd.Series) -> float:
    """Profit factor: ``sum(wins) / |sum(losses)|`` po liscie PnL per trade.

    Args:
        trades_pnl: seria PnL per trade (dodatnie wins, ujemne losses).

    Returns:
        Profit factor jako float. NaN gdy:
            - brak trade'ow (``logger.warning``)
            - brak losing trades (``logger.warning``)
    """
    if trades_pnl.empty:
        logger.warning("profit_factor: brak trade'ow → NaN")
        return float("nan")

    wins = float(trades_pnl[trades_pnl > 0].sum())
    losses = float(-trades_pnl[trades_pnl < 0].sum())

    if losses == 0:
        logger.warning(
            "profit_factor: brak losing trades (mala probka lub blad sygnalu) → NaN",
            extra={"n_trades": len(trades_pnl), "sum_wins": wins},
        )
        return float("nan")

    return wins / losses


def win_rate(trades_pnl: pd.Series) -> float:
    """Stosunek wins do wszystkich trade'ow.

    Args:
        trades_pnl: seria PnL per trade.

    Returns:
        Win rate w ``[0, 1]``. NaN przy braku trade'ow.
    """
    if trades_pnl.empty:
        return float("nan")
    return float((trades_pnl > 0).sum() / len(trades_pnl))


# ============================================================================
# Agregacja
# ============================================================================


@dataclass(frozen=True)
class MetricsSummary:
    """Komplet metryk risk-adjusted dla pojedynczego runu / foldu.

    Wszystkie pola sa JSON-serializable (NaN/inf wymagaja custom encoder'a
    albo NaN→None pass'a przy zapisie). Pola ``_days`` to skalarne wersje
    timedelta-based metryk (``recovery_time`` jako ``float`` w dniach;
    ``inf`` gdy nigdy nie odzyskano).

    Wymiarowanie:
        - ``periods_per_year`` zapamietywane explicite — caller widzi z jaka
          konwencja annualizacji liczona byla seria.
        - ``n_trades`` ``0`` gdy ``trades_pnl is None / empty``.
    """

    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    mar: float
    max_drawdown_pct: float
    max_drawdown_duration_days: float
    recovery_time_days: float
    profit_factor: float
    win_rate: float
    n_trades: int
    periods_per_year: float


def _timedelta_to_days(td: pd.Timedelta) -> float:
    """Konwersja ``pd.Timedelta`` → float days. ``Timedelta.max`` → ``inf``."""
    if td == pd.Timedelta.max:
        return float("inf")
    return td.total_seconds() / _SECONDS_PER_DAY


def summarize(
    equity: pd.Series,
    trades_pnl: pd.Series | None = None,
    periods_per_year: float | None = None,
    rf: float = 0.0,
    mar_target: float = 0.0,
) -> MetricsSummary:
    """Buduje ``MetricsSummary`` z serii equity i opcjonalnej listy PnL per trade.

    Cienki orchestrator — wywoluje pojedyncze metryki i pakuje wynik w dataclass.
    Walk-forward (Decision F) bedzie wolal to per fold; risk module (Decision E)
    bedzie wolal ``max_drawdown`` bezposrednio.

    Args:
        equity: seria equity z ``DatetimeIndex``.
        trades_pnl: opcjonalna seria PnL per trade. ``None`` lub pusta → metryki
            transakcyjne ustawione na NaN, ``n_trades=0``.
        periods_per_year: jesli ``None``, inferowane raz z ``equity.index`` i
            przekazywane do wszystkich annualizowanych metryk (jedna konwencja
            w calym summary).
        rf: roczna risk-free rate (default 0).
        mar_target: roczna MAR (Minimum Acceptable Return) dla Sortino (default 0).

    Returns:
        ``MetricsSummary`` z 13 polami (zob. dataclass).
    """
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(equity.index)

    dd_pct, dd_dur = max_drawdown(equity)
    rec = recovery_time(equity)

    if trades_pnl is None or trades_pnl.empty:
        pf = float("nan")
        wr = float("nan")
        n_trades = 0
    else:
        pf = profit_factor(trades_pnl)
        wr = win_rate(trades_pnl)
        n_trades = len(trades_pnl)

    return MetricsSummary(
        total_return=total_return(equity),
        cagr=cagr(equity, periods_per_year=periods_per_year),
        sharpe=sharpe(equity, periods_per_year=periods_per_year, rf=rf),
        sortino=sortino(equity, periods_per_year=periods_per_year, mar=mar_target),
        calmar=calmar(equity, periods_per_year=periods_per_year),
        mar=mar_ratio(equity, periods_per_year=periods_per_year),
        max_drawdown_pct=dd_pct,
        max_drawdown_duration_days=_timedelta_to_days(dd_dur),
        recovery_time_days=_timedelta_to_days(rec),
        profit_factor=pf,
        win_rate=wr,
        n_trades=n_trades,
        periods_per_year=periods_per_year,
    )


# ============================================================================
# Cross-strategy / portfolio analytics
# ============================================================================


def strategy_correlation(
    equities: dict[str, pd.Series] | pd.DataFrame,
    method: Literal["pearson", "spearman"] = "pearson",
    on: Literal["log_returns", "simple_returns"] = "log_returns",
) -> pd.DataFrame:
    """Macierz korelacji miedzy strategiami (cross-strategy / portfolio).

    Liczy korelacje per-period zwrotow (log albo simple) na intersekcji czasowej
    serii equity. Idealna do oceny zdrowia portfolio: dwie strategie o korelacji
    0.9+ daja w portfelu prawie taki sam efekt jak jedna z nich w 2x sizing —
    diversification benefit zero. Cel = strategie nieskorelowane (|corr| < 0.3
    to typowa rule-of-thumb dla "naprawde rozne edge").

    Args:
        equities: ``dict[str, pd.Series]`` (mapowanie nazwa_strategii → equity)
            albo ``pd.DataFrame`` z kolumnami = strategie. Obie formy normalizowane
            wewnetrznie do DataFrame.
        method: ``"pearson"`` (parametryczna, default — standard portfolio) albo
            ``"spearman"`` (rangowa, robust to outliers — sensowna dla crypto fat tails).
        on: ``"log_returns"`` (default — additywne, standard quant) albo
            ``"simple_returns"`` (intuicyjne procentowe zmiany). Equity raw odrzucone
            jako wejscie: korelacja niestacjonarnych serii = artefakt trendu, nie
            zaleznosci.

    Returns:
        ``pd.DataFrame`` N×N z korelacjami w ``[-1, 1]``. Diagonala = 1.0.
        Index i columns = nazwy strategii (dla dict) lub kolumny DataFrame.

    Note:
        Jesli equity'a maja rozne timeframe'y / okresy backtestu, intersekcja
        czasowa jest robiona przez ``pd.concat(..., join="inner")``. Tracimy
        rozdzielczosc — caller swiadomy. Outer join + fillna dawalby sztuczne
        korelacje z imputacji, dlatego odrzucone.

    Example:
        >>> corr = strategy_correlation(
        ...     {"bghtrend": eq_a, "mean_reversion": eq_b, "funding_arb": eq_c}
        ... )
        >>> corr.loc["bghtrend", "funding_arb"]
        -0.05  # idealnie nieskorelowane
    """
    if isinstance(equities, dict):
        if not equities:
            return pd.DataFrame()
        df_equity = pd.concat(equities, axis=1, join="inner")
    else:
        df_equity = equities.copy()

    if df_equity.empty or df_equity.shape[1] < 2:
        # mniej niz 2 strategie albo brak overlap'u czasowego
        return pd.DataFrame(index=df_equity.columns, columns=df_equity.columns, dtype=float)

    if on == "log_returns":
        # cast: tak samo jak w log_returns — np.log(DataFrame) w runtime
        # zachowuje pandas, ale stubs widzą ndarray.
        df_rets = cast(pd.DataFrame, np.log(df_equity)).diff().dropna(how="all")
    else:  # simple_returns
        df_rets = df_equity.pct_change().dropna(how="all")

    return df_rets.corr(method=method)


def mean_pairwise_correlation(corr_matrix: pd.DataFrame) -> float:
    """Srednia z par-wise korelacji (off-diagonal upper triangle).

    Szybka diagnostyka "jak skorelowane jest portfolio jako calosc". Wartosc
    blisko 0 → dobrze zdywersyfikowane; bliska 1 → strategie graja praktycznie
    to samo, brak benefitu diversification.

    Args:
        corr_matrix: kwadratowa macierz korelacji (zwracana przez
            ``strategy_correlation``).

    Returns:
        Srednia korelacji par (bez diagonali). NaN gdy mniej niz 2 strategie
        albo macierz pusta.
    """
    n = corr_matrix.shape[0]
    if n < 2 or corr_matrix.empty:
        return float("nan")

    # Wez tylko upper triangle bez diagonali (k=1) — kazda para raz.
    mask = np.triu(np.ones((n, n), dtype=bool), k=1)
    pairwise = corr_matrix.values[mask]

    if pairwise.size == 0:
        return float("nan")

    return float(np.nanmean(pairwise))
