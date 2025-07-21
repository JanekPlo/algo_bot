import os
import pytest
from src.data_loader import load_csv_ohlcv
from src.backtester import run_backtest
from strategies.bollinger_band_breakout_short import BollingerBandBreakoutShort

def test_bollinger_backtest_runs():
    path = os.path.join('bot_data','processed','BTC_USDT-4h.csv')
    df = load_csv_ohlcv(path)
    stats = run_backtest(df, BollingerBandBreakoutShort)
    # sprawdź, że statystyki zawierają jakieś kluczowe pola
    assert hasattr(stats, 'Equity Final [$]')
