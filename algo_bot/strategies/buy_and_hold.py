"""
algo_bot/strategies/buy_and_hold.py

Baseline strategia "kup-i-trzymaj" — deterministyczne wejście na drugim barze
runu (pierwszy bar to warm-up), brak exit'u (trzymanie do końca okresu albo do
breach z risk module). Używana jako:

1. Baseline benchmark dla porównań strategy-vs-HODL w research workflow.
2. Deterministyczny test fixture dla modułów które potrzebują "strategy która
   otworzyła pozycję" (np. risk module integration test) — bez zależności od
   crossingów wskaźników na syntetycznych danych.

Konfiguracja minimalna: tylko ``side`` ("long"/"short"). Brak SL/TP — strategia
nie zarządza wyjściem. Risk module może wyłapać breach na drawdown.

See also:
    docs/adr/003-strategybase-signal-api.md (Signal + StrategyBase kontrakt)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo_bot.strategy_base import Signal, StrategyBase

name = "buy_and_hold"


@dataclass
class BuyAndHoldParams:
    """Parametry strategii buy-and-hold.

    Attributes:
        side: Kierunek pozycji: "long" (default) lub "short".
        trade_on_close: Czy wejście wykonane po cenie Close bara (default True,
            zgodnie z konwencją projektu).
    """

    side: str = "long"
    trade_on_close: bool = True


class Strategy(StrategyBase):
    """Buy-and-hold: enter na drugim barze, hold do końca / breach."""

    ParamSchema = BuyAndHoldParams

    def __init__(self, params: object | None = None) -> None:
        super().__init__(params)
        self._entered: bool = False

    @staticmethod
    def required_features() -> set[str]:
        return {"Close"}

    def on_bar(self, df: pd.DataFrame) -> Signal:
        """Zwraca enter na drugim barze, hold w pozostałych.

        Pierwszy bar jest warm-up'em (backtesting.py potrzebuje co najmniej
        dwóch barów żeby zarządzić order execution). Po wejściu strategia
        nie wysyła żadnego exit — pozycja trwa do końca okresu albo do
        forced exit z risk module.
        """
        if self._entered:
            return Signal()  # hold

        if len(df) < 2:
            return Signal()  # nie wystarczająco barów — czekamy

        self._entered = True
        return Signal(action="enter", side=self.p.side)
