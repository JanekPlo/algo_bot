"""
algo_bot/indicators/core.py

Podstawowe wskaźniki techniczne implementowane na pandas.Series. Cienka warstwa
nad pandas ewm() / rolling() / diff() bez zależności od TA-Lib (te są dostępne
przez `import talib` w strategiach).

Public API (wszystkie funkcje przyjmują pd.Series i zwracają pd.Series):
- ema(series, length) — Exponential Moving Average
- rsi(series, length) — Relative Strength Index (0-100)
- atr(df, length) — Average True Range (wymaga df z High/Low/Close)
- t3(series, length, b) — T3 moving average (Tillson)

Konwencja:
- Brak handlingu NaN — caller decyduje czy dropna/fillna
- length: int > 0 (sanity check przez pandas, nie my)
- Wartości zwracane indeksowane jak input series

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
