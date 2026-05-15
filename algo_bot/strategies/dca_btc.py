from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import math

from algo_bot.strategy_base import StrategyBase, Signal


name = "dca_btc"


@dataclass
class DCAParams:
    # Co ile świec dokładać zakup (np. 6 na 4h ≈ raz dziennie)
    buy_every_n_bars: int = 6
    # Podstawowa kwota w USDT na zakup
    base_usdt: float = 100.0
    # offset startowy, pozwala przesunąć rytm zakupów
    start_offset: int = 0

    # Fear & Greed
    fng_mode: str = "off"          # 'off' | 'filter' | 'scale'
    fng_path: str = "bot_data/aux/crypto_fear_greed.csv"
    fng_buy_max: int = 50           # dla 'filter': kupuj tylko gdy index <= fng_buy_max

    # Uproszczony long-only spot
    side: str = "long"
    trade_on_close: bool = True
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None

    # Jeśli korzystasz z unit_scale w backtesterze (np. 0.001), włącz zaokrąglenie do całkowitych 'udziałów'.
    round_to_int_shares: bool = False
    # pozwól backtesterowi wyłączyć exclusive_orders, aby nie zamykał poprzednich wejść
    allow_pyramiding: bool = True


class Strategy(StrategyBase):
    ParamSchema = DCAParams

    # state
    _bar_i: int = 0
    _fng_series: Optional[pd.Series] = None

    @staticmethod
    def required_features() -> set[str]:
        return {"Close"}

    def _load_fng(self) -> Optional[pd.Series]:
        try:
            # ścieżka względem katalogu projektu
            proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
            path = self.p.fng_path
            if not os.path.isabs(path):
                path = os.path.join(proj_root, path)
            if not os.path.exists(path):
                return None
            df = pd.read_csv(path)
            # Oczekujemy kolumn: Date, FearGreedIndex
            if "Date" not in df.columns or "FearGreedIndex" not in df.columns:
                return None
            df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.normalize()
            s = df.sort_values("Date").set_index("Date")["FearGreedIndex"].astype(int)
            return s
        except Exception:
            return None

    def init(self, state) -> None:
        self._bar_i = 0
        self._fng_series = self._load_fng()

    def _fng_value_for(self, ts: pd.Timestamp) -> Optional[int]:
        if self._fng_series is None or self._fng_series.empty:
            return None
        d = pd.Timestamp(ts).tz_convert("UTC").normalize()
        idx = self._fng_series.index
        pos = idx.searchsorted(d, side="right") - 1
        if pos >= 0:
            try:
                return int(self._fng_series.iloc[pos])
            except Exception:
                return None
        return None

    @staticmethod
    def _scale_from_fng(val: int) -> float:
        # Prosty piecewise: im większy strach, tym większa alokacja
        if val <= 25:
            return 2.0
        if val <= 45:
            return 1.2
        if val <= 55:
            return 1.0
        if val <= 75:
            return 0.7
        return 0.4

    def on_bar(self, df: pd.DataFrame) -> Signal:
        # harmonogram: co N świec, z offsetem
        i = self._bar_i
        self._bar_i += 1
        n = max(1, int(self.p.buy_every_n_bars))
        if (i - int(self.p.start_offset)) % n != 0:
            return Signal()  # hold

        # cena i podstawowa alokacja
        close = float(df["Close"].iloc[-1])
        size_usdt = float(self.p.base_usdt)

        # Fear & Greed
        fng_val = self._fng_value_for(df.index[-1])
        mult = 1.0
        if self.p.fng_mode == "filter" and fng_val is not None:
            if int(fng_val) > int(self.p.fng_buy_max):
                return Signal()  # pomiń zakup
        elif self.p.fng_mode == "scale" and fng_val is not None:
            mult = self._scale_from_fng(int(fng_val))

        size_usdt *= mult
        if size_usdt <= 0:
            return Signal()  # nic nie kupuj

        # przelicz na jednostki (BTC)
        size_units = size_usdt / max(1e-9, close)

        # Jeśli ceny zostały przeskalowane (unit_scale) i chcesz unikać ułamkowych udziałów,
        # można wymusić zakup co najmniej 1 całkowitej jednostki.
        if self.p.round_to_int_shares:
            size_units = max(1, int(math.floor(size_units + 1e-9)))

        return Signal(
            action="enter",
            side="long",
            size=size_units,
            tp_pct=self.p.tp_pct,
            sl_pct=self.p.sl_pct,
            meta={
                "note": "dca_buy",
                "fng": fng_val,
                "mult": mult,
                "size_usdt": size_usdt,
                "size_units": size_units,
            },
        )
