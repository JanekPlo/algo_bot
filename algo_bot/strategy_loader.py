# src/strategy_loader.py
from __future__ import annotations

import importlib
from typing import Any

from .strategy_base import StrategyBase


def load_strategy(name: str, params: dict[str, Any]) -> StrategyBase:
    """
    Ładuje strategies/<name>.py i zwraca zainicjalizowaną instancję klasy Strategy.
    - Klasa Strategy MUSI dziedziczyć po StrategyBase.
    - Jeśli ma ParamSchema (dataclass), params zostaną do niej zmapowane.
    """
    mod = importlib.import_module(f"strategies.{name}")
    if not hasattr(mod, "Strategy"):
        raise ImportError(f"strategies.{name} nie eksportuje klasy Strategy")

    Strat = getattr(mod, "Strategy")
    if not issubclass(Strat, StrategyBase):
        raise TypeError(f"strategies.{name}.Strategy nie dziedziczy po StrategyBase")

    return Strat(params)  # StrategyBase sam ogarnie ParamSchema/dict
