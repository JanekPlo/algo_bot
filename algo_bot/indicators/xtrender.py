"""
algo_bot/indicators/xtrender.py

Xtrender oscillator — custom momentum indicator (Bryan G. Howell variant).
Łączy short-term i long-term momentum z dodatkowym wygładzeniem T3.

Public API:
- xtrender_components(close, short_l1, short_l2, short_l3, long_l1, long_l2, t3_len, t3_b)
    -> tuple(st, lt, st_t3)
    Zwraca: short-term, long-term, smoothed short-term komponenty.

Formuła:
- short_term = rsi(ema(close, short_l1) - ema(close, short_l2), short_l3) - 50
- long_term  = rsi(ema(close, long_l1), long_l2) - 50
- short_t3   = t3(short_term, t3_len, t3_b)

Interpretacja:
- short_t3 > 0 i rosnący → bull momentum
- short_t3 < 0 i opadający → bear momentum
- |short_t3| < deadzone (~3) → no clear momentum (filtruj trades)

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
    short_l1=5, short_l2=20, short_l3=15,
    long_l1=20, long_l2=15,
    t3_len=5, t3_b=0.7
):
    short_term = rsi(ema(close, short_l1) - ema(close, short_l2), short_l3) - 50.0
    long_term  = rsi(ema(close, long_l1), long_l2) - 50.0
    short_t3   = t3(short_term, t3_len, t3_b)

    st = short_t3
    up_dot   = (st > st.shift(1)) & (st.shift(1) < st.shift(2))   # lokalny dołek
    down_dot = (st < st.shift(1)) & (st.shift(1) > st.shift(2))   # lokalny szczyt
    return short_term, long_term, short_t3, up_dot.fillna(False), down_dot.fillna(False)
