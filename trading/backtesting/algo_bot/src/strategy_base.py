"""
strategy_base.py – definicja abstrakcyjnej klasy strategii zgodnej z koncepcją RBI.

Rola pliku w szkielecie:
- Narzuca jednolity interfejs dla wszystkich strategii (Research → Backtest → Implement).
- Każda strategia dziedziczy po tej klasie, implementuje własną logikę sygnałów.
- Ułatwia integrację z modułem backtester i executor.

Kluczowe metody:
- __init__: przyjmuje dane OHLCV oraz słownik parametrów.
- generate_signals: abstrakcyjna metoda, wypełnia wektor sygnałów (1 = long, -1 = short, 0 = brak pozycji).
- get_signals: zwraca sygnały, wywołuje generate_signals przy pierwszym dostępie.
- optionally: helpery do ewaluacji, wizualizacji lub metryk (można rozbudować).
"""
from abc import ABC, abstractmethod
from typing import Dict, Any
import pandas as pd

class StrategyBase(ABC):
    """
    Abstrakcyjna klasa bazowa dla strategii tradingowych.
    Każda strategia powinna implementować metodę generate_signals().
    """
    def __init__(self, data: pd.DataFrame, params: Dict[str, Any]):
        """
        Inicjalizacja strategii.

        Args:
            data (pd.DataFrame): OHLCV DataFrame z kolumnami ['Open','High','Low','Close','Volume'].
            params (dict): Parametry strategii, np. {'window':21, 'threshold':0.05}.
        """
        # Surowe dane cenowe (DataFrame index = datetime)
        self.data = data.copy()
        # Parametry strategii przekazane przez użytkownika
        self.params = params
        # Miejsce na sygnały: 1=wejście long, -1=wejście short, 0=flat
        self.signals: pd.Series = pd.Series(0, index=self.data.index, dtype="int8")

    @abstractmethod
    def generate_signals(self) -> None:
        """
        Główna logika strategii - ustala sygnały wejścia/wyjścia.
        Po wywołaniu self.signals będzie wypełniona wartościami 1, -1 lub 0.
        """
        ...

    def get_signals(self) -> pd.Series:
        """
        Zwraca sygnały strategii. Jeśli nie wygenerowano jeszcze sygnałów,
        wywołuje generate_signals().

        Returns:
            pd.Series: sygnały dla każdego punktu czasowego.
        """
        # Generujemy sygnały tylko raz
        if (self.signals == 0).all():
            self.generate_signals()
        return self.signals

    def summary(self) -> pd.DataFrame:
        """
        Opcjonalna metoda do szybkiego podsumowania sygnałów:
        liczbą sygnałów long/short, procentowym udziałem pozycji.
        Można nadpisać lub rozbudować.
        """
        summary = {
            'total_bars': len(self.signals),
            'long_signals': int((self.signals == 1).sum()),
            'short_signals': int((self.signals == -1).sum()),
            'flat_bars': int((self.signals == 0).sum()),
        }
        return pd.DataFrame.from_dict(summary, orient='index', columns=['value'])
