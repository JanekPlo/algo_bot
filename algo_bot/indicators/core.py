"""
algo_bot/indicators/core.py

Podstawowe wskaźniki techniczne implementowane na pandas.Series. Cienka warstwa
nad pandas ewm() / rolling() / diff() bez zależności od TA-Lib (te są dostępne
przez `import talib` w strategiach).

Public API:
- ema(series, length) — Exponential Moving Average → pd.Series
- rsi(series, length) — Relative Strength Index (0-100) → pd.Series
- atr(df, length) — Average True Range (wymaga df z High/Low/Close) → pd.Series
- t3(series, length, b) — T3 moving average (Tillson) → pd.Series
- bbands(close, window, num_std) — Bollinger Bands → (upper, mid, lower)
- stochastic(df, k, d, smooth) — Stochastic Oscillator "slow" → (%K, %D)
  (wymaga df z High/Low/Close)

Konwencja:
- Brak handlingu NaN — caller decyduje czy dropna/fillna (rolling() zwraca NaN
  w oknie rozgrzewkowym: pierwsze window-1 / k-1 barów)
- length/window/k/d/smooth: int > 0 (sanity check przez pandas, nie my)
- Wartości zwracane indeksowane jak input series
- Kauzalność: wszystkie funkcje używają tylko danych <= t (ewm/rolling bez
  center, shift wstecz) — bezpieczne do cache'owania w StrategyBase.precompute
- Funkcje wielowyjściowe (bbands, stochastic) zwracają krotki pd.Series

See also:
- algo_bot/indicators/xtrender.py — custom oscillator używający tych primitiv
- algo_bot/indicators/__init__.py — re-exporty (możesz `from algo_bot.indicators import ema`)
- TA-Lib (przez `import talib`) — alternative implementations, używane w niektórych strategiach
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(up, index=series.index).ewm(alpha=1 / length, adjust=False).mean()
    roll_down = pd.Series(down, index=series.index).ewm(alpha=1 / length, adjust=False).mean()
    rs = roll_up / (roll_down + 1e-12)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    hl = (df["High"] - df["Low"]).abs()
    hc = (df["High"] - df["Close"].shift(1)).abs()
    lc = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def t3(series: pd.Series, length: int = 5, b: float = 0.7) -> pd.Series:
    e1 = series.ewm(span=length, adjust=False).mean()
    e2 = e1.ewm(span=length, adjust=False).mean()
    e3 = e2.ewm(span=length, adjust=False).mean()
    e4 = e3.ewm(span=length, adjust=False).mean()
    e5 = e4.ewm(span=length, adjust=False).mean()
    e6 = e5.ewm(span=length, adjust=False).mean()
    c1 = -(b**3)
    c2 = 3 * b**2 + 3 * b**3
    c3 = -6 * b**2 - 3 * b - 3 * b**3
    c4 = 1 + 3 * b + b**3 + 3 * b**2
    return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3


def bbands(
    close: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wstęgi Bollingera: średnia krocząca ± num_std odchyleń standardowych.

    Klasyczny wskaźnik zmienności. Środek = prosta średnia krocząca (SMA) z
    okna ``window``. Wstęgi = środek ± ``num_std`` × krocząca zmienność. Gdy
    cena wychodzi poza wstęgę, jest statystycznie "rozciągnięta" względem
    swojej lokalnej średniej — podstawa strategii mean-reversion.

    Odchylenie standardowe liczone jest jako **populacyjne** (``ddof=0``), a nie
    próbkowe (pandas-default ``ddof=1``). To świadoma decyzja: zgodność z
    ``talib.BBANDS`` (TA-Lib dzieli przez n, nie n-1), żeby wyniki backtestu
    były porównywalne z dowolnym odniesieniem liczonym na TA-Lib. Różnica to
    czynnik sqrt(n/(n-1)) — dla window=20 wstęgi węższe o ~2.6% niż z ddof=1.

    Args:
        close: Szereg cen zamknięcia.
        window: Okno średniej i odchylenia (liczba barów). Domyślnie 20.
        num_std: Liczba odchyleń standardowych na wstęgę. Domyślnie 2.0.

    Returns:
        Krotka ``(upper, mid, lower)`` — trzy pd.Series indeksowane jak
        ``close``. Pierwsze ``window - 1`` barów to NaN (okno rozgrzewkowe).

    Note:
        Kauzalne — ``rolling`` bez ``center`` używa tylko danych <= t. Bezpieczne
        do policzenia raz w ``precompute`` i czytania prefiksem w ``on_bar``.
    """
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std(ddof=0)
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    return upper, mid, lower


def stochastic(
    df: pd.DataFrame, k: int = 14, d: int = 3, smooth: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Oscylator stochastyczny w wariancie "slow" (%K wygładzone, %D = SMA %K).

    Pozycjonuje bieżące zamknięcie względem zakresu High-Low z ostatnich ``k``
    barów: 0 = przy minimum okna, 100 = przy maksimum. Progi 20/80 markują
    wyprzedanie/wykupienie — w mean-reversion oscylator potwierdza, że dojście
    do wstęgi zbiega się z ekstremum momentum.

    Warianty stochastica różnią się liczbą wygładzeń. "Slow" (używany tu):
      * ``%K_raw = 100 · (Close - LL_k) / (HH_k - LL_k)`` — surowy "fast %K",
      * ``%K = SMA_smooth(%K_raw)`` — wygładzony (to jest zwracane jako %K),
      * ``%D = SMA_d(%K)`` — sygnałowa linia.
    Standardowe (14, 3, 3) → ``k=14, d=3, smooth=3``.

    Guard dzielenia przez zero: gdy ``HH_k == LL_k`` (płaskie okno) mianownik
    jest 0; dodajemy 1e-12 (jak w ``rsi``). Wtedy ``Close - LL_k`` też jest 0,
    więc ``%K_raw = 0`` — zdegenerowany, ale skończony i deterministyczny.

    Args:
        df: DataFrame z kolumnami ``High``, ``Low``, ``Close``.
        k: Okno zakresu High-Low (lookback surowego %K). Domyślnie 14.
        d: Okno SMA linii sygnałowej %D. Domyślnie 3.
        smooth: Okno SMA wygładzającego surowy %K do "slow" %K. Domyślnie 3.

    Returns:
        Krotka ``(%K, %D)`` — dwie pd.Series (0-100) indeksowane jak ``df``.
        Bary rozgrzewkowe (``k-1`` dla surowego %K, plus wygładzenia) to NaN.

    Note:
        Kauzalne — ``rolling`` min/max/mean bez ``center`` używa tylko danych
        <= t. Bezpieczne do ``precompute``.
    """
    lowest_low = df["Low"].rolling(k).min()
    highest_high = df["High"].rolling(k).max()
    pct_k_raw = 100.0 * (df["Close"] - lowest_low) / (highest_high - lowest_low + 1e-12)
    pct_k = pct_k_raw.rolling(smooth).mean()
    pct_d = pct_k.rolling(d).mean()
    return pct_k, pct_d
