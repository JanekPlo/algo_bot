"""
tests/test_bghtrend_precompute_equivalence.py

Test ekwiwalencji dwóch ścieżek liczenia wskaźników w bghtrend_pullback
(perf fix Sesji 4 Fazy 2, 2026-07-04):

1. ścieżka live/fallback — wskaźniki liczone od zera na prefiksie df per bar
   (stan sprzed fixu, nadal używana w live),
2. ścieżka backtest — wskaźniki policzone RAZ na pełnej serii w ``precompute``,
   w ``on_bar`` czytane jako prefiks ``.iloc[:m]``.

Własność matematyczna: wszystkie używane wskaźniki (EMA, ATR, RSI, T3,
shift wstecz) są kauzalne — wartość w barze ``t`` zależy tylko od danych
``<= t`` — więc obie ścieżki muszą dawać identyczne wartości, a w
konsekwencji identyczną sekwencję ``Signal``. Złamanie tej równości
oznaczałoby look-ahead bias w ścieżce precompute.

Bez mocków (konwencja repo): niezależna wyrocznia = dwie instancje tej samej
strategii na tych samych danych syntetycznych, porównanie bar po barze.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from algo_bot.indicators import atr, ema, xtrender_components
from algo_bot.strategies.bghtrend_pullback import Strategy

# Parametry celowo permisywne (niskie progi, płytkie EMA) — fixture ma
# wygenerować realne entry/exit, żeby test nie był pusty. Inwariant
# ema_fast < ema_mid < ema_slow zachowany.
_PARAMS: dict[str, Any] = {
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "slope_mode": "pct",
    "slope_lookback": 13,
    "slope_thr_mid": 0.0,
    "slope_thr_slow": 0.0,
    "pullback_lookback": 10,
    "pullback_atr_len": 14,
    "pullback_atr_mult": 0.5,
    "entry_max_atr_mult": 2.0,
    "require_rebound": False,
    "deadzone": 0.5,
    "rr_target": 1.5,
    "sl_atr_mult": 0.5,
    "trail_atr_mult": 2.0,
    "stale_max_bars": 20,
    "cooldown_bars": 3,
    "side": "both",
}


def _synthetic_ohlcv(n: int = 700, seed: int = 42) -> pd.DataFrame:
    """Deterministyczny random walk z trendem i cyklem — wymusza pullbacki."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    drift = 0.0004 * t
    cycle = 0.03 * np.sin(2.0 * np.pi * t / 120.0)
    noise = rng.normal(0.0, 0.004, n).cumsum()
    close = np.exp(np.log(30_000.0) + drift + cycle + noise)

    spread = close * rng.uniform(0.0005, 0.004, n)
    open_ = close * (1.0 + rng.normal(0.0, 0.001, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = rng.uniform(10.0, 1000.0, n)

    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def _assert_meta_equal(ma: dict[str, Any] | None, mb: dict[str, Any] | None, bar: int) -> None:
    assert (ma is None) == (mb is None), f"bar {bar}: meta None mismatch ({ma} vs {mb})"
    if ma is None or mb is None:
        return
    assert set(ma) == set(mb), f"bar {bar}: meta keys {set(ma)} != {set(mb)}"
    for k in ma:
        va, vb = ma[k], mb[k]
        if isinstance(va, float) and isinstance(vb, float):
            assert np.isclose(va, vb, rtol=1e-12, atol=0.0, equal_nan=True), (
                f"bar {bar}: meta[{k!r}] {va!r} != {vb!r}"
            )
        else:
            assert va == vb, f"bar {bar}: meta[{k!r}] {va!r} != {vb!r}"


def test_indicators_prefix_invariant() -> None:
    """Prefiks pełnej serii == wskaźnik policzony na prefiksie (kauzalność)."""
    df = _synthetic_ohlcv(n=400, seed=7)
    c = df["Close"]

    ema_full = ema(c, 21)
    atr_full = atr(df, 14)
    x_long_full = xtrender_components(c, 5, 20, 15, 20, 15, 5, 0.7)[1]

    for m in (60, 133, 250, 400):
        pd.testing.assert_series_equal(ema_full.iloc[:m], ema(c.iloc[:m], 21), rtol=1e-12, atol=0.0)
        pd.testing.assert_series_equal(
            atr_full.iloc[:m], atr(df.iloc[:m], 14), rtol=1e-12, atol=0.0
        )
        pd.testing.assert_series_equal(
            x_long_full.iloc[:m],
            xtrender_components(c.iloc[:m], 5, 20, 15, 20, 15, 5, 0.7)[1],
            rtol=1e-12,
            atol=0.0,
        )


def test_signals_identical_prefix_vs_precompute() -> None:
    """Sekwencja Signal: ścieżka per-prefix == ścieżka precompute, bar po barze."""
    df = _synthetic_ohlcv()

    strat_live = Strategy(dict(_PARAMS))  # bez precompute → liczy per prefiks
    strat_pre = Strategy(dict(_PARAMS))
    strat_pre.precompute(df)
    assert strat_pre._pre is not None  # hook faktycznie zbudował cache

    n_enter = 0
    n_exit = 0
    for m in range(1, len(df) + 1):
        prefix = df.iloc[:m]
        sig_a = strat_live.on_bar(prefix)
        sig_b = strat_pre.on_bar(prefix)

        assert sig_a.action == sig_b.action, f"bar {m}: {sig_a.action} != {sig_b.action}"
        assert sig_a.side == sig_b.side, f"bar {m}: {sig_a.side} != {sig_b.side}"
        _assert_meta_equal(sig_a.meta, sig_b.meta, m)

        if sig_a.action == "enter":
            n_enter += 1
        elif sig_a.action == "exit":
            n_exit += 1

    # Guard przeciw testowi pustemu: fixture MUSI wygenerować transakcje.
    assert n_enter > 0, "fixture nie wygenerowała żadnego entry — popraw dane/parametry"
    assert n_exit > 0, "fixture nie wygenerowała żadnego exit — popraw dane/parametry"
