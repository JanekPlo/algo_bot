"""
algo_bot/strategy_base.py

Bazowa klasa wszystkich strategii w algo_bot + dataclass Signal jako format
zwracanej decyzji. Strategia implementuje pojedynczą metodę:

    def on_bar(self, df: pd.DataFrame) -> Signal

Silniki (algo_bot/engine/backtester.py i live/live_binance.py) wywołują tę metodę
na każdej zamkniętej świecy. Strategia jest agnostyczna — nie wie czy uruchamiana
w backteście czy live.

Public API:
- Signal — dataclass z polami: action, side, size, tp_pct, sl_pct, meta
- StrategyBase — abstract base class z ParamSchema (dataclass), required_features(),
  init(), on_bar() (abstract)

Konwencja parametrów:
- Strategia deklaruje ParamSchema = SomeDataclass
- StrategyBase.__init__ filtruje przekazane params do pól ParamSchema
- Self.p to instancja ParamSchema (lub SimpleNamespace gdy brak schematu)

See also:
- docs/adr/003-strategybase-signal-api.md (rationale + design)
- docs/reference/modules/strategy-base.md (TBD — deep reference)
- docs/concepts/glossary.md (Signal, hold/enter/exit semantics)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields, is_dataclass
from types import SimpleNamespace
from typing import Any

import pandas as pd


@dataclass
class Signal:
    """
    Minimalny sygnał, który runner potrafi zrozumieć.
    - action: 'enter' | 'exit' | None
    - side:   'long'  | 'short' | None
    - size:   jeśli None, runner użyje domyślnego sizingu (np. --size_usdt)
    - tp/sl:  jeśli None, runner użyje globalnych ustawień (np. --tp_pct/--sl_pct)
    - meta:   dowolne dodatki do logów / journala (np. wartości wskaźników)
    """

    action: str | None = None
    side: str | None = None
    size: float | None = None
    tp_pct: float | None = None
    sl_pct: float | None = None
    meta: dict[str, Any] | None = None


class StrategyBase(ABC):
    """
    Jednolity interfejs strategii (backtest + live).
    Każda strategia:
      - definiuje ParamSchema (opcjonalnie; dataclass),
      - implementuje required_features() oraz on_bar(df) -> Signal.
    """

    ParamSchema: type | None = None  # np. dataclass z parametrami

    def __init__(self, params: dict[str, Any] | Any = None) -> None:
        """
        params: dict z parametrami lub instancja ParamSchema (jeśli ją masz).
        """
        if params is None:
            params = {}

        if self.ParamSchema is not None:
            if is_dataclass(self.ParamSchema) and not isinstance(params, self.ParamSchema):
                # waliduj przez dataclass
                allowed = {f.name for f in fields(self.ParamSchema)}
                filtered = {k: v for k, v in dict(params).items() if k in allowed}
                self.p = self.ParamSchema(**filtered)  # type: ignore
            else:
                # już zainstancjonowane
                self.p = params
        else:
            # brak schematu – przyjmij wszystko
            self.p = SimpleNamespace(**dict(params))

    # ----- interfejs, który runner/backtester może wołać -----

    @staticmethod
    def required_features() -> set[str]:
        """
        Jakich kolumn/feature'ów wymaga strategia w df?
        Domyślnie Close, ale można rozszerzyć (np. {'Close','ATR','EMA_fast','EMA_slow'}).
        """
        return {"Close"}

    def init(self, state: Any) -> None:
        """
        Jednorazowa inicjalizacja (opcjonalna). Backtester/live może przekazać swój 'state'.
        """
        return None

    @abstractmethod
    def on_bar(self, df: pd.DataFrame) -> Signal:
        """
        Główna logika – df zawiera ostatnie N świec (zamknięte!). Użyj df.iloc[-1].
        Zwróć Signal(action, side, ...).
        """
        raise NotImplementedError

    # ----- małe udogodnienia -----

    @property
    def side(self) -> str | None:
        """Jeśli w parametrach jest 'side', można do niego wygodnie sięgnąć jak dotąd w live."""
        return getattr(self.p, "side", None)
