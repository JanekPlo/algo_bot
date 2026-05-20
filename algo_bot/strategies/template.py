"""
algo_bot/strategies/template.py

Skeleton dla nowej strategii w algo_bot. Skopiuj jako starter:
    cp algo_bot/strategies/template.py algo_bot/strategies/my_new_strategy.py
i zaimplementuj swoją logikę w `on_bar()`.

Struktura każdej strategii (konwencja od ADR-003):
1. `name` — string identyfikator (musi pasować do nazwy pliku bez .py)
2. `@dataclass` z parametrami (sieci się na ParamSchema)
3. `class Strategy(StrategyBase)` z ParamSchema = ten dataclass
4. `required_features()` — zwraca set kolumn potrzebnych w df
5. `init(state)` (opcjonalne) — jednorazowy precompute
6. `on_bar(df)` — główna logika, zwraca Signal

Sygnały do zwrócenia:
- Signal() — hold
- Signal("enter", "long", tp_pct=0.05, sl_pct=0.02)
- Signal("exit", "long")
- Signal(meta={"sl": new_sl_price}) — update SL podczas hold

See also:
- docs/adr/003-strategybase-signal-api.md (interface)
- docs/guides/adding-a-strategy.md (TBD — pełen walkthrough)
- algo_bot/strategies/bghtrend_pullback.py (najbardziej rozbudowany przykład)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo_bot.strategy_base import Signal, StrategyBase

name = "template"


@dataclass
class TemplateParams:
    lookback: int = 50
    side: str = "both"  # 'long'|'short'|'both'
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
            return Signal(action="enter", side="long", tp_pct=self.p.tp_pct, sl_pct=self.p.sl_pct)

        if self.p.side in ("short", "both") and ret < 0:
            return Signal(action="enter", side="short", tp_pct=self.p.tp_pct, sl_pct=self.p.sl_pct)

        # przykładowy exit: odwrócenie momentum
        if self.p.side in ("long", "both") and ret < 0:
            return Signal(action="exit", side="long")
        if self.p.side in ("short", "both") and ret > 0:
            return Signal(action="exit", side="short")

        return Signal()  # hold
