"""
algo_bot/strategy_loader.py

Dynamic loading strategii po nazwie. Używane w CLI (--strategy bghtrend_pullback)
i w live runnerze (load_strategy("bghtrend_pullback", params_dict)).

Public API:
- load_strategy(name: str, params: dict) -> StrategyBase
    Ładuje algo_bot.strategies.<name>, waliduje że eksportuje Strategy klasę
    dziedziczącą po StrategyBase, instancjuje z params.

Walidacje:
- Moduł musi eksportować klasę o nazwie 'Strategy' (konwencja od ADR-003)
- Strategy musi dziedziczyć po StrategyBase (TypeError jeśli nie)
- params filtrowane do pól ParamSchema (przypadkowe klucze ignorowane)

See also:
- docs/adr/003-strategybase-signal-api.md
- algo_bot/strategy_base.py (StrategyBase + Signal)
"""

from __future__ import annotations

import importlib
from typing import Any

from .strategy_base import StrategyBase


def load_strategy(name: str, params: dict[str, Any]) -> StrategyBase:
    """
    Ładuje algo_bot/strategies/<name>.py i zwraca zainicjalizowaną instancję klasy Strategy.
    - Klasa Strategy MUSI dziedziczyć po StrategyBase.
    - Jeśli ma ParamSchema (dataclass), params zostaną do niej zmapowane.
    """
    mod = importlib.import_module(f"algo_bot.strategies.{name}")
    if not hasattr(mod, "Strategy"):
        raise ImportError(f"algo_bot.strategies.{name} nie eksportuje klasy Strategy")

    Strat = mod.Strategy
    if not issubclass(Strat, StrategyBase):
        raise TypeError(f"algo_bot.strategies.{name}.Strategy nie dziedziczy po StrategyBase")

    return Strat(params)  # StrategyBase sam ogarnie ParamSchema/dict
