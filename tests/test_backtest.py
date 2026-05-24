"""
tests/test_backtest.py

Smoke testy dla algo_bot.engine.backtester.run_backtest. Strategia minimalizmu:
jeden deterministyczny test syntetyczny (zawsze działa, bez wymogu danych w
bot_data/processed/) + jeden opcjonalny integration test który skipuje się gdy
brak pliku CSV (gotowy do uruchomienia gdy dane są zsynkronizowane lokalnie).

Sygnatura ``run_backtest`` zaktualizowana w sesji Decyzji E (2026-05-24) — patrz
docs/adr/008-risk-limits-module.md sekcja "Backtester data injection".
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from algo_bot.engine.backtester import RAW_DIR, run_backtest


def make_synthetic_ohlcv(
    n_bars: int = 200, start: str = "2024-01-01", freq: str = "1h", seed: int = 42
) -> pd.DataFrame:
    """
    Buduje deterministyczny OHLCV DataFrame z dwufazowym ruchem ceny:
    pierwsza połowa rosnąca, druga spadająca. Crossy średnich (10/30)
    będą wymuszone dwukrotnie — wystarczy żeby simple_momentum wygenerował
    co najmniej jedno enter+exit.

    Args:
        n_bars: liczba świec.
        start: timestamp początkowy (UTC).
        freq: pandas frequency string (np. "1h", "5min", "4h").
        seed: ziarno dla małego szumu na High/Low (deterministyczne między
            uruchomieniami; szum jest tylko żeby high >= max(open, close) i
            low <= min(open, close) z marginesem).

    Returns:
        DataFrame z kolumnami Open/High/Low/Close/Volume i DatetimeIndex w UTC.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq=freq, tz="UTC")

    # Dwufazowa cena bazowa: rośnie do połowy, potem spada
    half = n_bars // 2
    trend_up = np.linspace(50_000.0, 55_000.0, half)
    trend_down = np.linspace(55_000.0, 50_000.0, n_bars - half)
    close = np.concatenate([trend_up, trend_down])

    # Open = poprzedni close (klasyczne OHLCV continuity)
    open_ = np.empty_like(close)
    open_[0] = close[0]
    open_[1:] = close[:-1]

    # High/Low z marginesem 0.5% + mały szum żeby high > max(o,c) zawsze
    noise_high = rng.uniform(low=0.001, high=0.005, size=n_bars)
    noise_low = rng.uniform(low=0.001, high=0.005, size=n_bars)
    high = np.maximum(open_, close) * (1.0 + noise_high)
    low = np.minimum(open_, close) * (1.0 - noise_low)

    volume = np.full(n_bars, 1000.0)

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def test_run_backtest_with_injected_data_returns_stats_equity_trades():
    """
    Smoke test: run_backtest przyjmuje wstrzyknięty DataFrame i zwraca
    krotkę (stats, equity, trades) o oczekiwanym kontrakcie.

    Strategia: simple_momentum (tylko Close required, minimum zależności).
    Dane: syntetyczne 200 barów hourly z dwufazowym trendem — wymusza
    przynajmniej jedno crossing 10/30 SMA.

    Asserts kontrakt z sygnatury run_backtest po flatten 2026-05-14:
    - stats: dict z kluczowymi polami metrics
    - equity: pd.DataFrame z kolumną Equity
    - trades: pd.DataFrame (może być pusty gdy brak wejść, ale typ musi się zgadzać)
    """
    df = make_synthetic_ohlcv(n_bars=200, freq="1h")

    # cash dobierany żeby był >= max(High) — backtesting.py warning'uje przy
    # cenach > initial cash (brak fractional trading w klasycznym Backtest).
    stats, equity, trades = run_backtest(
        symbol="SYNTH/USDT",
        timeframe="1h",
        strategy="simple_momentum",
        params={"short": 10, "long": 30, "side": "long"},
        cash=1_000_000.0,
        commission=0.0004,
        trade_on_close=True,
        data=df,
    )

    # Kontrakt: stats jest dict-em z kluczowymi polami (które backtesting.py
    # zwraca zawsze, niezależnie czy były trade'y).
    assert isinstance(stats, dict)
    assert "Equity Final [$]" in stats
    assert "Return [%]" in stats

    # Equity curve — DataFrame z kolumną Equity i równa liczbie barów wejściowych
    assert isinstance(equity, pd.DataFrame)
    assert "Equity" in equity.columns
    assert len(equity) == len(df)

    # Trades — DataFrame (może być pusty, ale typ stabilny). Z naszym syntetycznym
    # trendem dwufazowym i SMA 10/30 — sygnał long enter na początku spada-do-rośnie
    # zwykle powstaje. Asercja na typ, nie na obecność (niezawodność > ostrość).
    assert isinstance(trades, pd.DataFrame)


def test_run_backtest_rejects_dataframe_without_ohlcv_columns():
    """
    Walidacja kontraktu wstrzykiwania danych: brak wymaganych kolumn OHLCV
    podnosi ValueError (zanim trafi do backtesting.py, gdzie błąd byłby
    bardziej kryptyczny).
    """
    bad_df = pd.DataFrame(
        {"price": [1.0, 2.0, 3.0]},
        index=pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC"),
    )
    with pytest.raises(ValueError, match="wymaganych kolumn"):
        run_backtest(
            symbol="SYNTH/USDT",
            timeframe="1h",
            strategy="simple_momentum",
            params={},
            data=bad_df,
        )


def test_run_backtest_rejects_dataframe_without_datetime_index():
    """
    Walidacja: DataFrame musi mieć DatetimeIndex (wymóg backtesting.py
    + naszych downstream consumerów typu metrics.infer_periods_per_year).
    """
    bad_df = pd.DataFrame(
        {
            "Open": [1.0, 2.0],
            "High": [1.1, 2.1],
            "Low": [0.9, 1.9],
            "Close": [1.05, 2.05],
            "Volume": [100.0, 200.0],
        },
        index=pd.RangeIndex(2),
    )
    with pytest.raises(ValueError, match="DatetimeIndex"):
        run_backtest(
            symbol="SYNTH/USDT",
            timeframe="1h",
            strategy="simple_momentum",
            params={},
            data=bad_df,
        )


@pytest.mark.integration
def test_run_backtest_with_csv_data_when_available():
    """
    Integration test — uruchamia run_backtest na realnym pliku CSV z
    bot_data/processed/. Skipuje się gracefully gdy plik nie istnieje
    (typowo lokalny dev bez zsynkronizowanych danych).

    Sprawdza: end-to-end ścieżkę z load_ohlcv_csv bez wstrzykiwania.
    """
    expected_path = os.path.join(RAW_DIR, "binance_BTCUSDT_4h.csv")
    if not os.path.exists(expected_path):
        pytest.skip(f"Brak pliku danych: {expected_path} — test integration skip")

    stats, equity, trades = run_backtest(
        symbol="BTC/USDT",
        timeframe="4h",
        strategy="simple_momentum",
        params={"short": 10, "long": 30, "side": "long"},
        cash=10_000.0,
    )

    assert isinstance(stats, dict)
    assert "Equity Final [$]" in stats
    assert isinstance(equity, pd.DataFrame)
    assert isinstance(trades, pd.DataFrame)
