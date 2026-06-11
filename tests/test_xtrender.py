"""
tests/test_xtrender.py

Standalone testy wskaźnika xtrender (tail-end cleanup 2026-06-11).

Strategia testowania: NIEZALEŻNA WYROCZNIA — oczekiwane wartości liczone
zwykłymi pętlami z definicji matematycznej (rekurencja EMA, RSI Wildera,
T3 Tillsona), bez pandas i bez wywoływania testowanego kodu. Jeśli
implementacja na ``ewm()`` liczy to samo co goła definicja — test
przechodzi. To nie mock — to druga, prostsza implementacja jako punkt
odniesienia (mindset reguła #3: bez mocków, deterministyczne fixtures).

Konwencje zgodne z algo_bot/indicators/core.py:
- ema: alpha = 2/(span+1), e0 = x0 (pandas ewm(span, adjust=False))
- rsi: delta z diff(); NaN na pierwszym barze → up=down=0; wygładzanie
  ewm(alpha=1/length, adjust=False); guard 1e-12 w mianowniku
- t3: sześć kolejnych EMA + współczynniki c1..c4 Tillsona
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from algo_bot.indicators import xtrender_components

# =====================================================================
# Wyrocznia — implementacja z definicji (plain Python, bez pandas)
# =====================================================================


def _ema_oracle(values: list[float], span: int) -> list[float]:
    """EMA rekurencyjnie: e_t = alpha*x_t + (1-alpha)*e_{t-1}, e_0 = x_0."""
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out


def _ewm_alpha_oracle(values: list[float], alpha: float) -> list[float]:
    """EWM z jawnym alpha (dla RSI: alpha = 1/length)."""
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out


def _rsi_oracle(values: list[float], length: int) -> list[float]:
    """RSI z definicji jak w core.rsi (NaN delta pierwszego bara → 0/0)."""
    deltas = [float("nan")] + [values[i] - values[i - 1] for i in range(1, len(values))]
    # np.where(delta > 0, delta, 0.0): NaN > 0 jest False → 0.0
    ups = [d if (d == d and d > 0) else 0.0 for d in deltas]
    downs = [-d if (d == d and d < 0) else 0.0 for d in deltas]
    roll_up = _ewm_alpha_oracle(ups, 1.0 / length)
    roll_down = _ewm_alpha_oracle(downs, 1.0 / length)
    return [
        100.0 - 100.0 / (1.0 + (u / (d + 1e-12))) for u, d in zip(roll_up, roll_down, strict=True)
    ]


def _t3_oracle(values: list[float], length: int, b: float) -> list[float]:
    """T3 Tillsona: 6 kolejnych EMA + kombinacja c1*e6 + c2*e5 + c3*e4 + c4*e3."""
    chains = [list(values)]
    for _ in range(6):
        chains.append(_ema_oracle(chains[-1], length))
    e3, e4, e5, e6 = chains[3], chains[4], chains[5], chains[6]
    c1 = -(b**3)
    c2 = 3 * b**2 + 3 * b**3
    c3 = -6 * b**2 - 3 * b - 3 * b**3
    c4 = 1 + 3 * b + b**3 + 3 * b**2
    return [
        c1 * x6 + c2 * x5 + c3 * x4 + c4 * x3 for x3, x4, x5, x6 in zip(e3, e4, e5, e6, strict=True)
    ]


# =====================================================================
# Fixtures
# =====================================================================

# 12-barowa deterministyczna sekwencja z naprzemiennymi ruchami
# (żeby RSI miał i up, i down — bez saturacji)
_CLOSES = [100.0, 101.5, 99.8, 102.2, 103.0, 101.1, 104.5, 105.2, 103.9, 106.0, 107.3, 105.8]

# Małe długości — pełna aktywacja rekurencji na 12 barach
_P: dict[str, Any] = {
    "short_l1": 3,
    "short_l2": 5,
    "short_l3": 4,
    "long_l1": 4,
    "long_l2": 3,
    "t3_len": 3,
    "t3_b": 0.7,
}


def _make_close(values: list[float]) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx, name="Close")


# =====================================================================
# Testy
# =====================================================================


class TestShortTermLeg:
    def test_short_term_matches_first_principles(self):
        """short_term = rsi(ema(c,l1) - ema(c,l2), l3) - 50 vs wyrocznia z pętli."""
        close = _make_close(_CLOSES)
        short_term, _lt, _st3, _up, _down = xtrender_components(close, **_P)

        e_fast = _ema_oracle(_CLOSES, _P["short_l1"])
        e_slow = _ema_oracle(_CLOSES, _P["short_l2"])
        spread = [f - s for f, s in zip(e_fast, e_slow, strict=True)]
        expected = [r - 50.0 for r in _rsi_oracle(spread, _P["short_l3"])]

        np.testing.assert_allclose(short_term.to_numpy(), expected, atol=1e-9)

    def test_long_term_matches_first_principles(self):
        """long_term = rsi(ema(c, long_l1), long_l2) - 50 vs wyrocznia."""
        close = _make_close(_CLOSES)
        _st, long_term, _st3, _up, _down = xtrender_components(close, **_P)

        smoothed = _ema_oracle(_CLOSES, _P["long_l1"])
        expected = [r - 50.0 for r in _rsi_oracle(smoothed, _P["long_l2"])]

        np.testing.assert_allclose(long_term.to_numpy(), expected, atol=1e-9)


class TestT3Smoothing:
    def test_short_t3_matches_tillson_first_principles(self):
        """short_t3 = t3(short_term) — pełny łańcuch vs wyrocznia Tillsona."""
        close = _make_close(_CLOSES)
        _st, _lt, short_t3, _up, _down = xtrender_components(close, **_P)

        e_fast = _ema_oracle(_CLOSES, _P["short_l1"])
        e_slow = _ema_oracle(_CLOSES, _P["short_l2"])
        spread = [f - s for f, s in zip(e_fast, e_slow, strict=True)]
        short_term_oracle = [r - 50.0 for r in _rsi_oracle(spread, _P["short_l3"])]
        expected = _t3_oracle(short_term_oracle, _P["t3_len"], _P["t3_b"])

        np.testing.assert_allclose(short_t3.to_numpy(), expected, atol=1e-9)

    def test_constant_close_full_stack_literal(self):
        """Stała cena → handcomputed literal: short_term = long_term = short_t3 = -50.

        Wyprowadzenie: ema(const) = const od bara 0 (e0 = x0), więc spread = 0;
        delta zerowej/stałej serii = 0 → roll_up = roll_down = 0 →
        rs = 0/(0+1e-12) = 0 → rsi = 100 - 100/(1+0) = 0 → leg = 0 - 50 = -50.
        T3 stałej = stała, bo współczynniki Tillsona sumują się do 1:
        (-b³) + (3b²+3b³) + (-6b²-3b-3b³) + (1+3b+3b²+b³) = 1.
        """
        close = _make_close([42_000.0] * 15)
        short_term, long_term, short_t3, _up, _down = xtrender_components(close, **_P)

        np.testing.assert_allclose(short_term.to_numpy(), [-50.0] * 15, atol=1e-9)
        np.testing.assert_allclose(long_term.to_numpy(), [-50.0] * 15, atol=1e-9)
        np.testing.assert_allclose(short_t3.to_numpy(), [-50.0] * 15, atol=1e-9)


class TestDots:
    def test_up_dot_after_trough(self):
        """V-kształtna cena: 20 barów spadku, potem 25 wzrostu → up_dot
        pojawia się po dołku oscylatora (z lagiem T3), kropki nigdy nie są
        True jednocześnie.

        Krótkie parametry (_P) zamiast defaultów: RSI(15)+T3(len=5) mają
        zbyt duży lag na krótkiej fixturze — oscylator nie zdążyłby
        uformować dołka (lekcja z pierwszego make check 2026-06-11).
        """
        closes = [100.0 - i for i in range(20)] + [81.0 + 1.5 * j for j in range(1, 26)]
        close = _make_close(closes)
        _st, _lt, _st3, up_dot, down_dot = xtrender_components(close, **_P)

        # po zwrocie ceny (bar 20+) oscylator musi w którymś momencie
        # zawrócić w górę → przynajmniej jeden up_dot
        assert bool(up_dot.iloc[20:].any()), "brak up_dot po dołku V-kształtnej ceny"
        # kropki wzajemnie wykluczające się na każdym barze
        assert not bool((up_dot & down_dot).any())

    def test_dots_derived_from_short_t3(self):
        """Wiring: kropki liczone z short_t3 (nie z short_term/long_term) —
        rekonstrukcja formuły lokalnych ekstremów na zwróconej serii."""
        close = _make_close(_CLOSES)
        _st, _lt, st3, up_dot, down_dot = xtrender_components(close, **_P)

        expected_up = ((st3 > st3.shift(1)) & (st3.shift(1) < st3.shift(2))).fillna(False)
        expected_down = ((st3 < st3.shift(1)) & (st3.shift(1) > st3.shift(2))).fillna(False)

        pd.testing.assert_series_equal(up_dot, expected_up)
        pd.testing.assert_series_equal(down_dot, expected_down)


class TestShapeAndWarmup:
    def test_five_tuple_shapes_dtypes_index(self):
        """Kontrakt API: 5-tka, długości = wejście, legi float, kropki bool,
        indeks identyczny z wejściem."""
        close = _make_close(_CLOSES)
        result = xtrender_components(close, **_P)

        assert len(result) == 5
        short_term, long_term, short_t3, up_dot, down_dot = result
        for s in result:
            assert isinstance(s, pd.Series)
            assert len(s) == len(close)
            assert s.index.equals(close.index)
        for leg in (short_term, long_term, short_t3):
            assert leg.dtype == np.float64
        for dots in (up_dot, down_dot):
            assert dots.dtype == np.bool_

    def test_dots_first_bars_false_not_nan(self):
        """fillna(False): shift(1)/shift(2) dają NaN na pierwszych barach —
        kropki mają tam być False, nie NaN (strategia robi bool(dot.iloc[-1]))."""
        close = _make_close(_CLOSES)
        _st, _lt, _st3, up_dot, down_dot = xtrender_components(close, **_P)

        assert bool(up_dot.iloc[0]) is False and bool(up_dot.iloc[1]) is False
        assert bool(down_dot.iloc[0]) is False and bool(down_dot.iloc[1]) is False
        assert not up_dot.isna().any() and not down_dot.isna().any()
