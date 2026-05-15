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
