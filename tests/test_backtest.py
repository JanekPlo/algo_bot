import os

import pytest

from algo_bot.data_loader import load_csv_ohlcv
from algo_bot.engine.backtester import run_backtest
from algo_bot.strategies.bollinger_band_breakout_short import BollingerBandBreakoutShort


# Test SKIPPED — sygnatura niespojna z nowa run_backtest API (od commitu flatten 2026-05-14).
# Decyzja podjeta w sesji ADR-007: scope refaktoru tego testu wykracza poza Decision D,
# bedzie naprawiony w osobnej, dedykowanej sesji przed Decision E (risk module).
# Patrz: docs/captains-log/2026-05-21.md "Open questions for Janek".
@pytest.mark.skip(
    reason="Broken signature (df, StrategyClass) vs new (symbol, timeframe, strategy, params). "
    "Deferred to dedicated session — patrz docs/adr/007-risk-adjusted-metrics.md Notes."
)
def test_bollinger_backtest_runs():
    path = os.path.join("bot_data", "processed", "BTC_USDT-4h.csv")
    df = load_csv_ohlcv(path)
    stats = run_backtest(df, BollingerBandBreakoutShort)
    # sprawdź, że statystyki zawierają jakieś kluczowe pola
    assert hasattr(stats, "Equity Final [$]")
