"""
algo_bot/indicators/xtrender.py

Xtrender oscillator — custom momentum indicator (Bryan G. Howell variant).
Łączy short-term i long-term momentum z dodatkowym wygładzeniem T3.

Public API:
- xtrender_components(close, short_l1, short_l2, short_l3, long_l1, long_l2, t3_len, t3_b)
    -> tuple(short_term, long_term, short_t3, up_dot, down_dot)
    5-tka pd.Series (indeks jak wejście):
    [0] short_term — surowy short-term oscylator (RSI spreadu EMA, recentered na 0)
    [1] long_term  — wolny, reżimowy momentum (RSI wygładzonej ceny, recentered na 0)
    [2] short_t3   — short_term wygładzony T3 (Tillson)
    [3] up_dot     — bool, lokalny dołek short_t3 (oscylator zawrócił w górę)
    [4] down_dot   — bool, lokalny szczyt short_t3 (oscylator zawrócił w dół)
    Kropki mają .fillna(False) na pierwszych barach; legi float niosą warmup
    EWM bez maskowania (konwencja core — caller decyduje o dropna/fillna).

Formuła:
- short_term = rsi(ema(close, short_l1) - ema(close, short_l2), short_l3) - 50
- long_term  = rsi(ema(close, long_l1), long_l2) - 50
- short_t3   = t3(short_term, t3_len, t3_b)
- up_dot     = (st > st[-1]) & (st[-1] < st[-2]), gdzie st = short_t3
- down_dot   = (st < st[-1]) & (st[-1] > st[-2])

Interpretacja (poziom wskaźnika):
- short_t3 > 0 i rosnący → bull momentum (short-term)
- short_t3 < 0 i opadający → bear momentum (short-term)
- long_term > 0 → reżim byczy w skali long_l1/long_l2, < 0 → niedźwiedzi
- |wartość| < deadzone (~1.5-5, parametr konsumenta) → brak czytelnego momentum
  (deadzone żyje po stronie strategii, nie wskaźnika)

Używany w:
- algo_bot/strategies/bghtrend_pullback.py — jako filter momentum przy entry

See also:
- algo_bot/indicators/core.py — bazowe wskaźniki (ema, rsi, t3)
- docs/concepts/glossary.md (xtrender)
"""

from __future__ import annotations

import pandas as pd

from .core import ema, rsi, t3


def xtrender_components(
    close: pd.Series,
    short_l1=5,
    short_l2=20,
    short_l3=15,
    long_l1=20,
    long_l2=15,
    t3_len=5,
    t3_b=0.7,
):
    short_term = rsi(ema(close, short_l1) - ema(close, short_l2), short_l3) - 50.0
    long_term = rsi(ema(close, long_l1), long_l2) - 50.0
    short_t3 = t3(short_term, t3_len, t3_b)

    st = short_t3
    up_dot = (st > st.shift(1)) & (st.shift(1) < st.shift(2))  # lokalny dołek
    down_dot = (st < st.shift(1)) & (st.shift(1) > st.shift(2))  # lokalny szczyt
    return short_term, long_term, short_t3, up_dot.fillna(False), down_dot.fillna(False)
