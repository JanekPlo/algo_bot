"""
bollinger_band_breakout_short.py – przykładowa strategia RBI oparta na przerwaniach Bollingera (krótko)

Rola pliku w szkielecie:
- Implementuje klasę strategii zgodnie z backtesting.py (dziedziczy po Strategy).
- Pokazuje, jak wykorzystać abstrahowaną logikę sygnałów w praktyce.
- Umożliwia natychmiastowe odpalenie backtestu w `executor.py` lub REPL.

Parametry domyślne można nadpisać przy optymalizacji.
"""

import talib
from backtesting import Strategy


class BollingerBandBreakoutShort(Strategy):
    # Parametry strategii (można optymalizować)
    window = 21  # okno do obliczeń Bollinger Bands
    num_std = 2.0  # liczba odchyleń standardowych
    take_profit = 0.05  # zysk docelowy 5%
    stop_loss = 0.03  # stop loss 3%

    def init(self):
        """
        Obliczenie pasm Bollingera przy inicjalizacji.
        self.I() rejestruje wskaźnik i aktualizuje przy każdym wywołaniu next().
        """
        self.upper, self.middle, self.lower = self.I(
            talib.BBANDS, self.data.Close, self.window, self.num_std, self.num_std
        )

    def next(self):
        """
        Logika przerwania poniżej dolnego pasma:
        - jeśli nie ma otwartej pozycji i cena < lower band, shortujemy.
        - zlecenie zawiera SL i TP.
        """
        price = self.data.Close[-1]
        if not self.position and price < self.lower[-1]:
            self.sell(sl=price * (1 + self.stop_loss), tp=price * (1 - self.take_profit))
