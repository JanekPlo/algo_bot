"""
tests/test_indicators_bbands_stochastic.py

Standalone testy wskaźników bbands() i stochastic() (MR-Session Beta).

Strategia testowania: NIEZALEŻNA WYROCZNIA — oczekiwane wartości liczone
zwykłymi pętlami z definicji matematycznej (SMA, populacyjne odchylenie
standardowe, surowy %K + wygładzenia SMA), bez pandas i bez wywoływania
testowanego kodu. To nie mock — to druga, prostsza implementacja jako punkt
odniesienia (mindset reguła #3: bez mocków, deterministyczne fixtures).

Konwencje zgodne z algo_bot/indicators/core.py:
- bbands: mid = SMA(window); sd = odchylenie POPULACYJNE (ddof=0, jak talib.BBANDS,
  nie pandas-default ddof=1); upper/lower = mid ± num_std·sd; NaN w oknie
  rozgrzewkowym (pierwsze window-1 barów).
- stochastic "slow": %K_raw = 100·(Close-LL_k)/(HH_k-LL_k+1e-12);
  %K = SMA_smooth(%K_raw); %D = SMA_d(%K). NaN propaguje przez okna
  (rolling(w).mean() default min_periods=w → NaN gdy w oknie jest NaN).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from algo_bot.indicators import bbands, stochastic

NAN = float("nan")


# =====================================================================
# Wyrocznia — implementacja z definicji (plain Python, bez pandas)
# =====================================================================
def _sma_oracle(vals: list[float], w: int) -> list[float]:
    """SMA jak pandas rolling(w).mean() (min_periods=w): NaN gdy okno
    niekompletne albo zawiera NaN (bo wtedy < w niepustych obserwacji)."""
    out: list[float] = []
    for t in range(len(vals)):
        if t < w - 1:
            out.append(NAN)
            continue
        window = vals[t - w + 1 : t + 1]
        if any(math.isnan(v) for v in window):
            out.append(NAN)
        else:
            out.append(sum(window) / w)
    return out


def _pop_std_oracle(vals: list[float], w: int) -> list[float]:
    """Populacyjne odchylenie standardowe (ddof=0): sqrt(mean((x-mean)^2))."""
    out: list[float] = []
    for t in range(len(vals)):
        if t < w - 1:
            out.append(NAN)
            continue
        window = vals[t - w + 1 : t + 1]
        m = sum(window) / w
        var = sum((x - m) ** 2 for x in window) / w
        out.append(math.sqrt(var))
    return out


def _bbands_oracle(
    closes: list[float], window: int, num_std: float
) -> tuple[list[float], list[float], list[float]]:
    mid = _sma_oracle(closes, window)
    sd = _pop_std_oracle(closes, window)
    upper = [m + num_std * s if not math.isnan(m) else NAN for m, s in zip(mid, sd, strict=True)]
    lower = [m - num_std * s if not math.isnan(m) else NAN for m, s in zip(mid, sd, strict=True)]
    return upper, mid, lower


def _stoch_oracle(
    highs: list[float], lows: list[float], closes: list[float], k: int, d: int, smooth: int
) -> tuple[list[float], list[float]]:
    n = len(closes)
    kraw: list[float] = []
    for t in range(n):
        if t < k - 1:
            kraw.append(NAN)
            continue
        ll = min(lows[t - k + 1 : t + 1])
        hh = max(highs[t - k + 1 : t + 1])
        kraw.append(100.0 * (closes[t] - ll) / (hh - ll + 1e-12))
    pct_k = _sma_oracle(kraw, smooth)
    pct_d = _sma_oracle(pct_k, d)
    return pct_k, pct_d


# =====================================================================
# Fixtures — deterministyczny OHLC z zachowanym inwariantem baru
# =====================================================================
# Deterministyczny OHLC generowany numpy (bez długich literałów): Close z
# gładkim wahaniem (niezerowe sd, stoch odwiedza oba końce range), Open =
# poprzedni Close, High/Low = envelope ±1.5 (inwariant baru zachowany).
def _synthetic_ohlc(n: int = 18, seed: int = 3) -> tuple[list, list, list, list]:
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    close = 100.0 + 4.0 * np.sin(t / 1.7) + 1.5 * np.cos(t / 0.8) + rng.normal(0.0, 0.1, n)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + 1.5
    low = np.minimum(open_, close) - 1.5
    return list(open_), list(high), list(low), list(close)


_OPEN, _HIGH, _LOW, _CLOSE = _synthetic_ohlc()


def _make_df(opens, highs, lows, closes) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1.0] * len(closes),
        },
        index=idx,
    )


def _df() -> pd.DataFrame:
    return _make_df(_OPEN, _HIGH, _LOW, _CLOSE)


# =====================================================================
# bbands
# =====================================================================
class TestBBands:
    def test_matches_first_principles(self):
        """upper/mid/lower vs wyrocznia SMA + populacyjne sd (ddof=0)."""
        close = _df()["Close"]
        upper, mid, lower = bbands(close, window=5, num_std=2.0)
        o_up, o_mid, o_lo = _bbands_oracle(list(_CLOSE), 5, 2.0)

        np.testing.assert_allclose(mid.to_numpy(), o_mid, rtol=1e-9, atol=1e-9, equal_nan=True)
        np.testing.assert_allclose(upper.to_numpy(), o_up, rtol=1e-9, atol=1e-9, equal_nan=True)
        np.testing.assert_allclose(lower.to_numpy(), o_lo, rtol=1e-9, atol=1e-9, equal_nan=True)

    def test_population_std_not_sample(self):
        """Regresja na decyzję ddof=0: wstęgi muszą być WĘŻSZE niż z ddof=1.

        sd_pop = sd_sample · sqrt((n-1)/n); dla n=5 współczynnik = sqrt(4/5).
        Half-width = num_std·sd, więc szerokość z ddof=0 = szerokość z ddof=1
        przemnożona przez sqrt(4/5) ≈ 0.894. Gdyby ktoś zmienił na ddof=1,
        ten test padnie.
        """
        close = _df()["Close"]
        upper, _mid, lower = bbands(close, window=5, num_std=2.0)
        width_pop = (upper - lower).dropna()

        sd_sample = close.rolling(5).std(ddof=1)  # pandas default
        width_sample = (2.0 * 2.0 * sd_sample).dropna()

        ratio = (width_pop / width_sample).to_numpy()
        np.testing.assert_allclose(ratio, math.sqrt(4.0 / 5.0), rtol=1e-9)

    def test_constant_price_literal(self):
        """Stała cena → sd=0 → upper=mid=lower=const (po rozgrzewce)."""
        close = _make_df([42_000.0] * 8, [42_000.0] * 8, [42_000.0] * 8, [42_000.0] * 8)["Close"]
        upper, mid, lower = bbands(close, window=5, num_std=2.0)
        # bary 0..3 NaN (okno), 4..7 = 42000 dokładnie
        for s in (upper, mid, lower):
            np.testing.assert_allclose(s.to_numpy()[4:], [42_000.0] * 4, rtol=0, atol=1e-9)
            assert s.iloc[:4].isna().all()

    def test_ordering_and_warmup(self):
        """upper ≥ mid ≥ lower wszędzie poza NaN; pierwsze window-1 = NaN."""
        close = _df()["Close"]
        upper, mid, lower = bbands(close, window=5, num_std=2.0)
        assert upper.iloc[:4].isna().all() and mid.iloc[:4].isna().all()
        valid = mid.notna()
        assert (upper[valid] >= mid[valid]).all()
        assert (mid[valid] >= lower[valid]).all()

    def test_prefix_invariance(self):
        """Kauzalność: bbands(full).iloc[:m] == bbands(prefix). Warunek
        bezpiecznego cache'owania w precompute."""
        close = _df()["Close"]
        up_f, mid_f, lo_f = bbands(close, window=5, num_std=2.0)
        for m in (6, 10, 16):
            up_p, mid_p, lo_p = bbands(close.iloc[:m], window=5, num_std=2.0)
            pd.testing.assert_series_equal(up_f.iloc[:m], up_p, rtol=1e-12)
            pd.testing.assert_series_equal(mid_f.iloc[:m], mid_p, rtol=1e-12)
            pd.testing.assert_series_equal(lo_f.iloc[:m], lo_p, rtol=1e-12)


# =====================================================================
# stochastic
# =====================================================================
class TestStochastic:
    def test_matches_first_principles(self):
        """%K/%D vs wyrocznia (surowy %K + dwa wygładzenia SMA)."""
        df = _df()
        pct_k, pct_d = stochastic(df, k=5, d=3, smooth=3)
        o_k, o_d = _stoch_oracle(list(_HIGH), list(_LOW), list(_CLOSE), 5, 3, 3)

        np.testing.assert_allclose(pct_k.to_numpy(), o_k, rtol=1e-9, atol=1e-9, equal_nan=True)
        np.testing.assert_allclose(pct_d.to_numpy(), o_d, rtol=1e-9, atol=1e-9, equal_nan=True)

    def test_bounded_0_100(self):
        """%K i %D w [0, 100]: Close jest zawsze między Low[t] a High[t],
        które wchodzą do LL/HH okna, więc surowy %K ∈ [0,100], a SMA tego
        nie wyprowadza poza zakres."""
        df = _df()
        pct_k, pct_d = stochastic(df, k=5, d=3, smooth=3)
        for s in (pct_k, pct_d):
            v = s.dropna().to_numpy()
            assert (v >= -1e-9).all() and (v <= 100.0 + 1e-9).all()

    def test_flat_window_literal(self):
        """Płaskie High=Low=Close → HH==LL → guard 1e-12 → %K_raw=0 → %K=%D=0."""
        c = [42_000.0] * 12
        df = _make_df(c, c, c, c)
        pct_k, pct_d = stochastic(df, k=5, d=3, smooth=3)
        # Rozgrzewka: %K_raw od t≥k-1=4; %K=SMA_3(%K_raw) od t≥6;
        # %D=SMA_3(%K) od t≥8.
        np.testing.assert_allclose(pct_k.to_numpy()[6:], [0.0] * (12 - 6), atol=1e-6)
        np.testing.assert_allclose(pct_d.to_numpy()[8:], [0.0] * (12 - 8), atol=1e-6)

    def test_close_at_high_gives_100(self):
        """Close == najwyższy High w oknie (i to jest HH) → surowy %K = 100.

        Budujemy rosnące High/Close (każdy bar wyżej), Low płaskie niskie.
        Wtedy dla t≥k-1: HH=High[t]=Close[t], LL=min Low=stałe → %K_raw≈100.
        Test na surowym %K przez smooth=1 (bez wygładzenia).
        """
        n = 8
        closes = [100.0 + i for i in range(n)]
        highs = list(closes)  # Close == High
        lows = [90.0] * n
        opens = [90.0] * n
        df = _make_df(opens, highs, lows, closes)
        pct_k, _ = stochastic(df, k=3, d=1, smooth=1)
        # surowy %K (smooth=1) od bara k-1=2: (Close-90)/(High-90) z High=Close
        # = (Close-90)/(Close-90) ≈ 100 (guard 1e-12 pomijalny)
        np.testing.assert_allclose(pct_k.to_numpy()[2:], [100.0] * (n - 2), rtol=1e-6)

    def test_prefix_invariance(self):
        """Kauzalność: stochastic(full).iloc[:m] == stochastic(prefix)."""
        df = _df()
        k_f, d_f = stochastic(df, k=5, d=3, smooth=3)
        for m in (8, 12, 16):
            k_p, d_p = stochastic(df.iloc[:m], k=5, d=3, smooth=3)
            pd.testing.assert_series_equal(k_f.iloc[:m], k_p, rtol=1e-12)
            pd.testing.assert_series_equal(d_f.iloc[:m], d_p, rtol=1e-12)


# =====================================================================
# Kontrakt API
# =====================================================================
class TestApiContract:
    def test_bbands_tuple_shape_index(self):
        close = _df()["Close"]
        result = bbands(close, window=5, num_std=2.0)
        assert len(result) == 3
        for s in result:
            assert isinstance(s, pd.Series)
            assert len(s) == len(close)
            assert s.index.equals(close.index)
            assert s.dtype == np.float64

    def test_stochastic_tuple_shape_index(self):
        df = _df()
        result = stochastic(df, k=5, d=3, smooth=3)
        assert len(result) == 2
        for s in result:
            assert isinstance(s, pd.Series)
            assert len(s) == len(df)
            assert s.index.equals(df.index)
            assert s.dtype == np.float64
