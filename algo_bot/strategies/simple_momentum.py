# algo_bot/strategies/simple_momentum.py
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo_bot.strategy_base import Signal, StrategyBase

name = "simple_momentum"


@dataclass
class SimpleMomentumParams:
    short: int = 10
    long: int = 30
    side: str = "short"  # 'long' | 'short' | 'both'
    trade_on_close: bool = True
    tp_pct: float | None = None
    sl_pct: float | None = None


class Strategy(StrategyBase):
    ParamSchema = SimpleMomentumParams

    @staticmethod
    def required_features() -> set[str]:
        return {"Close"}

    def on_bar(self, df: pd.DataFrame) -> Signal:
        if len(df) < max(self.p.short, self.p.long) + 2:
            return Signal()  # hold

        short_ma = df["Close"].rolling(self.p.short).mean()
        long_ma = df["Close"].rolling(self.p.long).mean()
        s_prev, s_now = short_ma.iloc[-2], short_ma.iloc[-1]
        l_prev, l_now = long_ma.iloc[-2], long_ma.iloc[-1]

        # crossy
        enter_long = (s_prev <= l_prev) and (s_now > l_now)
        exit_long = (s_prev >= l_prev) and (s_now < l_now)
        enter_short = (s_prev >= l_prev) and (s_now < l_now)
        exit_short = (s_prev <= l_prev) and (s_now > l_now)

        # priorytet: enter przed exit (lub odwrotnie — zgodnie z Twoją polityką)
        if self.p.side in ("long", "both") and enter_long:
            return Signal("enter", "long", tp_pct=self.p.tp_pct, sl_pct=self.p.sl_pct)
        if self.p.side in ("short", "both") and enter_short:
            return Signal("enter", "short", tp_pct=self.p.tp_pct, sl_pct=self.p.sl_pct)

        if self.p.side in ("long", "both") and exit_long:
            return Signal("exit", "long")
        if self.p.side in ("short", "both") and exit_short:
            return Signal("exit", "short")

        return Signal()  # hold


# --- IGNORE ---
