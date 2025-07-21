# strategies/template.py
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from src.strategy_base import StrategyBase, Signal

name = "template"


@dataclass
class TemplateParams:
    lookback: int = 50
    side: str = "both"     # 'long'|'short'|'both'
    tp_pct: float | None = None
    sl_pct: float | None = None


class Strategy(StrategyBase):
    ParamSchema = TemplateParams

    @staticmethod
    def required_features() -> set[str]:
        # dodaj tu np. 'ATR', 'EMA_fast' jeśli strategia ich używa
        return {"Close"}

    def init(self, state) -> None:
        # opcjonalne: cache, precompute
        pass

    def on_bar(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.p.lookback + 2:
            return Signal()  # hold

        # proste momentum na zamknięciu:
        ret = df["Close"].pct_change(self.p.lookback).iloc[-1]

        if self.p.side in ("long", "both") and ret > 0:
            return Signal(action="enter", side="long",
                          tp_pct=self.p.tp_pct, sl_pct=self.p.sl_pct)

        if self.p.side in ("short", "both") and ret < 0:
            return Signal(action="enter", side="short",
                          tp_pct=self.p.tp_pct, sl_pct=self.p.sl_pct)

        # przykładowy exit: odwrócenie momentum
        if self.p.side in ("long", "both") and ret < 0:
            return Signal(action="exit", side="long")
        if self.p.side in ("short", "both") and ret > 0:
            return Signal(action="exit", side="short")

        return Signal()  # hold
