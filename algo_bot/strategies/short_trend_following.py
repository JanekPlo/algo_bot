"""
short_trend_following.py – Strategia Momentum Short Trend Following na bazie Death Cross + MACD + ATR.

Logika:
- Death Cross: 50- i 200-okresowe średnie kroczące (SMA).
- Potwierdzenie spadków: linia MACD poniżej linii sygnałowej.
- Wejście short: death cross i momentum spadkowe.
- Wyjście short: złoty krzyż lub trailing stop na bazie ATR.

Parametry:
- fast_window: okres krótkiej SMA (domyślnie 50)
- slow_window: okres długiej SMA (domyślnie 200)
- macd_fast: szybki okres MACD (12)
- macd_slow: wolny okres MACD (26)
- macd_signal: okres linii sygnału MACD (9)
- atr_window: okres ATR (14)
- atr_multiplier: mnożnik ATR dla trailing stop (2.0)
- trade_on_close: wykonanie zleceń na zamknięciu świecy
"""

import talib
from backtesting import Strategy
from backtesting.lib import crossover


class ShortTrendFollowing(Strategy):
    fast_window = 50
    slow_window = 200
    macd_fast = 12
    macd_slow = 26
    macd_signal = 9
    atr_window = 14
    atr_multiplier = 2.0
    trade_on_close = True

    def init(self):
        # Obliczenia wskaźników
        self.sma_fast = self.I(talib.SMA, self.data.Close, self.fast_window)
        self.sma_slow = self.I(talib.SMA, self.data.Close, self.slow_window)

        macd_line, macd_signal, _ = talib.MACD(
            self.data.Close,
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal,
        )
        # Ta-lambda to hack, by I() przyjęło wektor
        self.macd_line = self.I(lambda x: macd_line, self.data.Close)
        self.macd_signal = self.I(lambda x: macd_signal, self.data.Close)

        self.atr = self.I(
            talib.ATR, self.data.High, self.data.Low, self.data.Close, self.atr_window
        )

    def next(self):
        price = self.data.Close[-1]
        # Wejście short: death cross + MACD
        if not self.position and crossover(self.sma_slow, self.sma_fast):
            if self.macd_line[-1] < self.macd_signal[-1]:
                self.sell()
                self.entry_price = price

        # Wyjście short
        elif self.position:
            # Złoty krzyż odwrotny
            if crossover(self.sma_fast, self.sma_slow):
                self.position.close()
                return
            # Trailing stop na bazie ATR
            trail_stop = self.entry_price + self.atr_multiplier * self.atr[-1]
            if price > trail_stop:
                self.position.close()
