import os

from algo_bot.data_loader import load_csv_ohlcv
from algo_bot.engine.backtester import run_backtest
from algo_bot.strategies.bollinger_band_breakout_short import BollingerBandBreakoutShort


# TODO (faza 1): ten test jest niespojny z nowa sygnatura run_backtest
# (przyjmuje symbol/timeframe/strategy/params, nie df+StrategyClass).
# Do refaktoru w decyzji D (metrics module + test fixtures) lub przy dyskusji
# o test infrastructure. Na razie zostawiamy 1:1 (rename only).
def test_bollinger_backtest_runs():
    path = os.path.join("bot_data", "processed", "BTC_USDT-4h.csv")
    df = load_csv_ohlcv(path)
    stats = run_backtest(df, BollingerBandBreakoutShort)
    # sprawdź, że statystyki zawierają jakieś kluczowe pola
    assert hasattr(stats, "Equity Final [$]")
