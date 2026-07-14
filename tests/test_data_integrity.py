"""tests/test_data_integrity.py

Testy ``algo_bot.data_integrity`` (Faza 2 Sesja 2, Decyzja 7).

Dwa tory:
- **Unit** — deterministyczne ramki budowane ręcznie (regularna siatka, wstrzyknięte
  naruszenia). Reference values policzone na piechotę. Zawsze działają.
- **Integration** (gated) — czyta realne pliki ``bot_data/processed/binance_*.csv``
  przez ``data_loader.load_processed`` i sprawdza twardą integralność. Skip gracefully
  gdy plik nie istnieje (dane nie zostały jeszcze pobrane). Bez mocków (mindset rule #3) —
  test ma wartość integracyjną tylko na realnych danych.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from algo_bot.data_integrity import (
    TF_MS,
    check_integrity,
    check_mark_price_integrity,
    check_monotonic,
    check_ohlcv_invariants,
    detect_gaps,
)
from algo_bot.data_loader import get_processed_path, load_processed
from algo_bot.fetch_data import raw_filename
from algo_bot.process_data import is_mark_price_raw, parse_raw_name, processed_filename

# Pełen set Fazy 2 (Decyzja 1/3: Binance Futures USDT-M).
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["15m", "1h", "4h"]


def test_mark_price_pipeline_names_are_unambiguous() -> None:
    raw = raw_filename("BTC/USDT", "1h", "bybit", "mark")
    assert raw.as_posix().endswith("bot_data/raw/bybit_BTC_USDT-mark-1h.csv")
    assert is_mark_price_raw(raw)
    assert parse_raw_name(raw) == ("bybit", "BTCUSDT", "1h")
    assert processed_filename("BTCUSDT", "1h", "bybit", mark_price=True).name == (
        "bybit_BTCUSDT_mark_1h.csv"
    )


# ============================================================================
# Helpers / fixtures (bez mocków)
# ============================================================================
def _make_clean_ohlcv(n: int = 50, timeframe: str = "15m") -> pd.DataFrame:
    """Czysta, regularna siatka OHLCV spełniająca wszystkie niezmienniki.

    High = base + 1, Low = base - 1, Open = base, Close = base + 0.5,
    więc High >= max(Open, Close) i Low <= min(Open, Close) zawsze.
    """
    step = pd.Timedelta(milliseconds=TF_MS[timeframe])
    idx = pd.date_range(start="2020-01-01", periods=n, freq=step, tz="UTC")
    base = 100.0 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "Open": base,
            "High": base + 1.0,
            "Low": base - 1.0,
            "Close": base + 0.5,
            "Volume": np.arange(n, dtype=float),
        },
        index=idx,
    )


# ============================================================================
# check_monotonic
# ============================================================================
def test_monotonic_clean_grid_ok() -> None:
    df = _make_clean_ohlcv()
    res = check_monotonic(df)
    assert res.ok
    assert res.n_duplicates == 0
    assert res.n_out_of_order == 0
    assert res.is_monotonic_increasing


def test_monotonic_detects_duplicates() -> None:
    df = _make_clean_ohlcv(n=5)
    # Zduplikuj trzeci timestamp.
    dup_idx = df.index.tolist()
    dup_idx[2] = dup_idx[1]
    df.index = pd.DatetimeIndex(dup_idx)
    res = check_monotonic(df)
    assert res.n_duplicates == 1
    assert not res.ok  # duplikat zawala monotoniczność


def test_monotonic_detects_out_of_order() -> None:
    df = _make_clean_ohlcv(n=5)
    # Zamień miejscami dwa timestampy -> jedna inwersja.
    swapped = df.index.tolist()
    swapped[1], swapped[2] = swapped[2], swapped[1]
    df.index = pd.DatetimeIndex(swapped)
    res = check_monotonic(df)
    assert not res.is_monotonic_increasing
    assert res.n_out_of_order >= 1
    assert not res.ok


# ============================================================================
# check_ohlcv_invariants
# ============================================================================
def test_invariants_clean_grid_ok() -> None:
    res = check_ohlcv_invariants(_make_clean_ohlcv())
    assert res.ok
    assert res.n_rows == 50
    assert res.n_high_violations == 0
    assert res.n_low_violations == 0
    assert res.n_high_low_violations == 0
    assert res.n_negative_volume == 0
    assert res.n_nan_rows == 0


def test_invariants_detects_high_violation() -> None:
    df = _make_clean_ohlcv(n=10)
    # High poniżej Close w jednym wierszu (Close = base + 0.5).
    df.iloc[3, df.columns.get_loc("High")] = df.iloc[3]["Close"] - 0.1
    res = check_ohlcv_invariants(df)
    assert res.n_high_violations == 1
    assert not res.ok


def test_invariants_detects_low_violation() -> None:
    df = _make_clean_ohlcv(n=10)
    # Low powyżej Open w jednym wierszu.
    df.iloc[4, df.columns.get_loc("Low")] = df.iloc[4]["Open"] + 0.1
    res = check_ohlcv_invariants(df)
    assert res.n_low_violations == 1
    assert not res.ok


def test_invariants_detects_negative_volume() -> None:
    df = _make_clean_ohlcv(n=10)
    df.iloc[5, df.columns.get_loc("Volume")] = -1.0
    res = check_ohlcv_invariants(df)
    assert res.n_negative_volume == 1
    assert not res.ok


def test_invariants_detects_nan() -> None:
    df = _make_clean_ohlcv(n=10)
    df.iloc[6, df.columns.get_loc("Close")] = np.nan
    res = check_ohlcv_invariants(df)
    assert res.n_nan_rows == 1
    assert not res.ok


def test_invariants_missing_column_raises() -> None:
    df = _make_clean_ohlcv(n=5).drop(columns=["Volume"])
    with pytest.raises(ValueError, match="Volume"):
        check_ohlcv_invariants(df)


def test_mark_price_integrity_is_strict_about_single_gap_and_non_positive_price() -> None:
    frame = _make_clean_ohlcv(n=6, timeframe="1h").drop(columns=["Volume"])
    clean = check_mark_price_integrity(frame, "1h", symbol="BTCUSDT")
    assert clean.ok

    broken = frame.drop(index=frame.index[2]).copy()
    broken.iloc[0, broken.columns.get_loc("Low")] = 0.0
    report = check_mark_price_integrity(broken, "1h", symbol="BTCUSDT")
    assert not report.ok
    assert report.n_non_positive == 1
    assert len(report.gaps) == 1
    assert report.gaps[0].missing_bars == 1


def test_mark_price_integrity_rejects_not_yet_completed_bar() -> None:
    frame = _make_clean_ohlcv(n=2, timeframe="1h").drop(columns=["Volume"])
    as_of = frame.index[-1] + pd.Timedelta(minutes=30)
    report = check_mark_price_integrity(frame, "1h", as_of=as_of)
    assert not report.ok
    assert report.n_future_bars == 1


# ============================================================================
# detect_gaps
# ============================================================================
def test_detect_gaps_none_on_regular_grid() -> None:
    assert detect_gaps(_make_clean_ohlcv(timeframe="15m"), "15m") == ()


def test_detect_gaps_finds_single_gap() -> None:
    # Siatka 15m z dziurą: po 00:30 skok do 03:00 (2.5h > 3 × 15m = 45m).
    times = pd.to_datetime(
        [
            "2020-01-01 00:00",
            "2020-01-01 00:15",
            "2020-01-01 00:30",
            "2020-01-01 03:00",
            "2020-01-01 03:15",
        ],
        utc=True,
    )
    base = 100.0 + np.arange(len(times), dtype=float)
    df = pd.DataFrame(
        {
            "Open": base,
            "High": base + 1.0,
            "Low": base - 1.0,
            "Close": base + 0.5,
            "Volume": np.ones(len(times)),
        },
        index=times,
    )
    gaps = detect_gaps(df, "15m")
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.gap_ms == 150 * 60 * 1000  # 2h30m w ms
    # 150 min / 15 min = 10 slotów -> 9 brakujących świec.
    assert gap.missing_bars == 9
    assert str(gap.prev_ts) == "2020-01-01 00:30:00+00:00"
    assert str(gap.next_ts) == "2020-01-01 03:00:00+00:00"


def test_detect_gaps_unsupported_timeframe_raises() -> None:
    with pytest.raises(ValueError, match="timeframe"):
        detect_gaps(_make_clean_ohlcv(), "7m")


# ============================================================================
# check_integrity (orkiestrator)
# ============================================================================
def test_check_integrity_clean_ok() -> None:
    df = _make_clean_ohlcv(timeframe="1h")
    report = check_integrity(df, "1h", symbol="TEST/USDT")
    assert report.ok
    assert report.n_gaps == 0
    assert report.n_rows == 50
    assert report.symbol == "TEST/USDT"
    assert report.monotonic.ok
    assert report.invariants.ok


def test_check_integrity_flags_broken_data() -> None:
    df = _make_clean_ohlcv(n=10, timeframe="1h")
    df.iloc[2, df.columns.get_loc("High")] = df.iloc[2]["Close"] - 5.0
    report = check_integrity(df, "1h")
    assert not report.ok
    assert report.invariants.n_high_violations == 1


def test_check_integrity_detects_unsorted_input() -> None:
    # Regresja: check_integrity nie może sortować przed checkiem monotoniczności,
    # bo posortowana seria zawsze byłaby "monotoniczna" (duplikaty/inwersje znikają).
    df = _make_clean_ohlcv(n=6, timeframe="1h")
    shuffled = df.index.tolist()
    shuffled[2], shuffled[4] = shuffled[4], shuffled[2]
    df.index = pd.DatetimeIndex(shuffled)
    report = check_integrity(df, "1h")
    assert not report.monotonic.ok
    assert report.monotonic.n_out_of_order >= 1
    assert not report.ok


def test_check_integrity_warns_on_gap_but_stays_ok(caplog: pytest.LogCaptureFixture) -> None:
    # Gap jest miękki — loguje WARNING, ale ok pozostaje True (Decyzja 6).
    times = pd.to_datetime(["2020-01-01 00:00", "2020-01-01 00:15", "2020-01-01 02:00"], utc=True)
    base = np.array([100.0, 101.0, 102.0])
    df = pd.DataFrame(
        {
            "Open": base,
            "High": base + 1.0,
            "Low": base - 1.0,
            "Close": base + 0.5,
            "Volume": np.ones(3),
        },
        index=times,
    )
    with caplog.at_level("WARNING"):
        report = check_integrity(df, "15m")
    assert report.ok  # gap nie zawala twardej integralności
    assert report.n_gaps == 1
    assert any("Gap detected" in rec.message for rec in caplog.records)


# ============================================================================
# Integration (gated na obecność realnych danych — Faza 2 Sesja 2)
# ============================================================================
@pytest.mark.integration
@pytest.mark.parametrize("timeframe", TIMEFRAMES)
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_processed_file_integrity(symbol: str, timeframe: str) -> None:
    """Twarda integralność realnych plików PROCESSED.

    Skip gdy plik nie istnieje (dane nie pobrane) — patrz docs/guides/data-fetching.md.
    """
    path = get_processed_path(symbol, timeframe)
    if not path.exists():
        pytest.skip(
            f"Brak {path.name} — pobierz dane (algo-fetch + algo-process), "
            "patrz docs/guides/data-fetching.md"
        )

    df = load_processed(symbol, timeframe)
    report = check_integrity(df, timeframe, symbol=symbol)

    assert report.n_rows > 0, f"{path.name} jest pusty"
    assert report.monotonic.ok, (
        f"{path.name}: monotonic fail "
        f"(dups={report.monotonic.n_duplicates}, ooo={report.monotonic.n_out_of_order})"
    )
    assert report.invariants.ok, (
        f"{path.name}: OHLCV invariant violations — "
        f"high={report.invariants.n_high_violations}, "
        f"low={report.invariants.n_low_violations}, "
        f"high_low={report.invariants.n_high_low_violations}, "
        f"neg_vol={report.invariants.n_negative_volume}, "
        f"nan={report.invariants.n_nan_rows}"
    )
