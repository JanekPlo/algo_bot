# strategies/bghtrend_pullback.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple

import numpy as np
import pandas as pd

from src.strategy_base import StrategyBase, Signal

from indicators import ema, rsi, t3, atr, xtrender_components

# =========================
# Paramy
# =========================
@dataclass
class XtrenderPullbackParams:
    # Trend / EMA
    ema_fast: int = 21
    ema_mid:  int = 89
    ema_slow: int = 200

    # Slope filter (znormalizowany)
    slope_mode: str = "pct"          # 'pct' | 'zscore'
    slope_lookback: int = 34
    slope_thr_mid: float = 5e-5      # progi dla 'pct' (per bar)
    slope_thr_slow: float = 3e-5
    zscore_window: int = 100         # dla 'zscore', rolling okno

    # Pullback
    pullback_lookback: int = 15
    pullback_atr_len: int = 14
    pullback_atr_mult: float = 0.15  # ile ATR to „blisko EMA89”
    entry_max_atr_mult: float = 0.75
    require_rebound: bool = True     # świeca odbicia (Close vs Close[-1], powrót nad/pod EMA21)

    # Xtrender
    short_l1: int = 5
    short_l2: int = 20
    short_l3: int = 15
    long_l1:  int = 20
    long_l2:  int = 15
    t3_len:   int = 5
    t3_b:     float = 0.7
    deadzone: float = 3.0            # martwa strefa wokół 0

    # SL/TP/Trail
    rr_target: float = 1.5
    sl_atr_mult: float = 0.5         # SL = EMA89 ± 0.5*ATR
    trail_atr_mult: float = 2.0      # ATR-trail
    tp_has_priority: bool = True     # jeśli w tej samej świecy TP i SL

    # Zarządzanie pozycją
    stale_max_bars: int = 40         # timeout (~10h na 15m), używany gdy brak momentum (xtr deadzone)
    cooldown_bars: int = 10           # przerwa po SL
    side: str = "both"               # 'long'|'short'|'both'

    # kosmetyka API
    trade_on_close: bool = True
    tp_pct: float | None = None
    sl_pct: float | None = None


# =========================
# Strategia
# =========================
class Strategy(StrategyBase):
    name = "bghtrend_pullback"
    ParamSchema = XtrenderPullbackParams

    # state
    _pos_side: str | None = None
    _entry_price: float | None = None
    _sl: float | None = None
    _tp: float | None = None
    _trail: float | None = None
    _bars_in_trade: int = 0
    _cooldown_left: int = 0

    @staticmethod
    def required_features() -> set[str]:
        return {"Open", "High", "Low", "Close"}

    # ---------- slope normalization ----------
    @staticmethod
    def _slope_pct(series: pd.Series, lookback: int) -> float:
        """% per bar ~ (Δ / (price_{-L} * L))"""
        prev = series.shift(lookback)
        base = (prev.abs() * max(1, lookback)).replace(0, np.nan)
        val = (series.iloc[-1] - series.iloc[-1 - lookback]) / base.iloc[-1]
        return float(np.nan_to_num(val))

    @staticmethod
    def _slope_zscore(series: pd.Series, lookback: int, zwin: int) -> float:
        raw = (series - series.shift(lookback)) / max(1, lookback)
        mu = raw.rolling(zwin, min_periods=10).mean()
        sd = raw.rolling(zwin, min_periods=10).std(ddof=0).replace(0, np.nan)
        z = (raw - mu) / sd
        return float(np.nan_to_num(z.iloc[-1]))

    # ---------- checks ----------
    def _trend_ok(self, ef: pd.Series, em: pd.Series, es: pd.Series, side: str) -> bool:
        # hierarchia EMA (jedno sprawdzenie, nie duplikujemy)
        if side == "long":
            if not (ef.iloc[-1] > em.iloc[-1] > es.iloc[-1]):
                return False
        else:
            if not (es.iloc[-1] > em.iloc[-1] > ef.iloc[-1]):
                return False

        # slope filter na EMA89/EMA200 (znormalizowany)
        L = self.p.slope_lookback
        if len(em) < L + 2 or len(es) < L + 2:
            return False

        if self.p.slope_mode == "zscore":
            s89  = self._slope_zscore(em, L, self.p.zscore_window)
            s200 = self._slope_zscore(es, L, self.p.zscore_window)
            thr89 = self.p.slope_thr_mid
            thr200 = self.p.slope_thr_slow
        else:  # 'pct'
            s89  = self._slope_pct(em, L)
            s200 = self._slope_pct(es, L)
            thr89 = self.p.slope_thr_mid
            thr200 = self.p.slope_thr_slow

        if side == "long":
            return (s89 >= thr89) and (s200 >= thr200)
        else:
            return (s89 <= -thr89) and (s200 <= -thr200)

    def _pullback_seen(self, df: pd.DataFrame, ema89: pd.Series, atr_s: pd.Series, side: str) -> bool:
        look = self.p.pullback_lookback
        win = df.iloc[-look:]
        e89 = ema89.iloc[-look:]
        thr = atr_s.iloc[-look:] * self.p.pullback_atr_mult

        if side == "long":
            # blisko EMA89 po Low (long)
            near = (e89 - win["Low"]).abs() <= thr
        else:
            # blisko EMA89 po High (short)
            near = (win["High"] - e89).abs() <= thr
        return bool(near.any())

    def _rebound_ok(self, close: pd.Series, ema21: pd.Series, side: str) -> bool:
        if not self.p.require_rebound:
            return True
        c_now, c_prev = float(close.iloc[-1]), float(close.iloc[-2])
        e_now = float(ema21.iloc[-1])
        if side == "long":
            return (c_now >= e_now) and (c_now > c_prev)
        else:
            return (c_now <= e_now) and (c_now < c_prev)

    def _entry_distance_ok(self, close: float, ema89_now: float, atr_now: float) -> bool:
        """Sprawdza, czy odległość wejścia od EMA89 nie jest zbyt duża (w ATR)."""
        k = float(self.p.entry_max_atr_mult)
        return abs(close - ema89_now) <= k * max(1e-9,atr_now)


    def _xtr_ok(self, x_long: pd.Series, side: str) -> bool:
        val = float(x_long.iloc[-1]); dz = self.p.deadzone
        return (val > dz) if side == "long" else (val < -dz)

    def _in_profit(self, last_close: float) -> bool:
        if self._entry_price is None or self._pos_side is None:
            return False
        return (last_close > self._entry_price) if self._pos_side == "long" else (last_close < self._entry_price)

    # ---------- targets ----------
    def _compute_sl_tp(self, entry: float, ema89_now: float, atr_now: float, side: str) -> Tuple[float, float]:
        pad = self.p.sl_atr_mult * atr_now
        if side == "long":
            sl = ema89_now - pad
            risk = max(1e-9, entry - sl)
            tp = entry + self.p.rr_target * risk
        else:
            sl = ema89_now + pad
            risk = max(1e-9, sl - entry)
            tp = entry - self.p.rr_target * risk
        return float(sl), float(tp)

    def _update_trailing(self, last_close: float, atr_now: float):
        if self._pos_side is None:
            return
        step = self.p.trail_atr_mult * atr_now
        if self._pos_side == "long":
            new_trail = last_close - step
            self._trail = max(self._trail or -np.inf, new_trail)
            self._sl = max(self._sl, self._trail)      # zacieśniaj, nie poszerzaj
        else:
            new_trail = last_close + step
            self._trail = min(self._trail or np.inf, new_trail)
            self._sl = min(self._sl, self._trail)

    def _same_bar_hit(self, high: float, low: float, side: str) -> str | None:
        if self._tp is None or self._sl is None:
            return None
        if side == "long":
            hit_tp = high >= self._tp
            hit_sl = low  <= self._sl
        else:
            hit_tp = low  <= self._tp
            hit_sl = high >= self._sl

        if hit_tp and hit_sl:
            return "tp" if self.p.tp_has_priority else "sl"
        if hit_tp: return "tp"
        if hit_sl: return "sl"
        return None

    # ---------- state ----------
    def _set_pos(self, side: str, entry: float, sl: float, tp: float, atr_now: float):
        self._pos_side = side
        self._entry_price = entry
        self._sl, self._tp = sl, tp
        # inicjalny trail po ATR (od razu lekko „pod”/„nad” wejściem)
        if side == "long":
            self._trail = entry - self.p.trail_atr_mult * atr_now
            self._sl = max(self._sl, self._trail)
        else:
            self._trail = entry + self.p.trail_atr_mult * atr_now
            self._sl = min(self._sl, self._trail)
        self._bars_in_trade = 0

    def _reset_pos(self):
        self._pos_side = None
        self._entry_price = None
        self._sl = None
        self._tp = None
        self._trail = None
        self._bars_in_trade = 0

    # ---------- main ----------
    def on_bar(self, df: pd.DataFrame) -> Signal:
        need = max(self.p.ema_slow, self.p.long_l1 if hasattr(self.p, "long_l1") else 20,
                   self.p.short_l2 if hasattr(self.p, "short_l2") else 20, 60)
        if len(df) < need + 5:
            return Signal()

        o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]

        ema21  = ema(c, self.p.ema_fast)
        ema89  = ema(c, self.p.ema_mid)
        ema200 = ema(c, self.p.ema_slow)
        atr_s  = atr(df, self.p.pullback_atr_len)

        x_short, x_long, x_t3, up_dot, down_dot = xtrender_components(
            c, self.p.short_l1, self.p.short_l2, self.p.short_l3,
            self.p.long_l1, self.p.long_l2, self.p.t3_len, self.p.t3_b
        )

        # cooldown po SL
        if self._cooldown_left > 0:
            self._cooldown_left -= 1
            return Signal()

        # ===== EXIT (pierwszeństwo) =====
        if self._pos_side is not None:
            c_now, h_now, l_now = float(c.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1])

            # trail po Average True Range
            self._update_trailing(c_now, float(atr_s.iloc[-1]))

            # same-bar TP/SL (po aktualizacji trail/SL)
            hit = self._same_bar_hit(h_now, l_now, self._pos_side)
            if hit == "tp":
                side = self._pos_side; self._reset_pos()
                return Signal("exit", side, meta={"reason": "tp_hit"})
            if hit == "sl":
                side = self._pos_side; self._reset_pos()
                self._cooldown_left = max(self._cooldown_left, self.p.cooldown_bars)
                return Signal("exit", side, meta={"reason": "sl_hit"})

            # „kropki” T3 jako wyjście tylko na zysku
            if self._in_profit(c_now):
                if self._pos_side == "long" and bool(down_dot.iloc[-1]):
                    side = self._pos_side; self._reset_pos()
                    return Signal("exit", side, meta={"reason": "xtrender_peak"})
                if self._pos_side == "short" and bool(up_dot.iloc[-1]):
                    side = self._pos_side; self._reset_pos()
                    return Signal("exit", side, meta={"reason": "xtrender_trough"})

            # timeout w chopie (stale-exit): brak momentum + limit barów
            self._bars_in_trade += 1
            if self._bars_in_trade >= self.p.stale_max_bars and abs(float(x_long.iloc[-1])) <= self.p.deadzone:
                side = self._pos_side; self._reset_pos()
                return Signal("exit", side, meta={"reason": "time_limit"})

            # brak wyjścia – raportuj meta (dla runnera/backtestera pod egzekucję)
            return Signal("hold", self._pos_side, meta={"sl": self._sl, "tp": self._tp, "trail": self._trail})

        # ===== ENTRY =====
        if self._cooldown_left > 0:
            return Signal()  # pauza po SL

        # LONG
        if self.p.side in ("long", "both"):
            if self._trend_ok(ema21, ema89, ema200, "long") \
               and self._pullback_seen(df, ema89, atr_s, "long") \
               and self._rebound_ok(c, ema21, "long") \
               and self._xtr_ok(x_long, "long"):

                entry = float(c.iloc[-1]); e89 = float(ema89.iloc[-1]); a = float(atr_s.iloc[-1])
                # nowy filtr: odległość wejścia od EMA89 nie może być zbyt duża (w ATR)
                if not self._entry_distance_ok(entry, e89, a):
                    return Signal()
                sl, tp = self._compute_sl_tp(entry, e89, a, "long")
                self._set_pos("long", entry, sl, tp, a)
                return Signal("enter", "long", meta={"sl": sl, "tp": tp, "trail_atr_mult": self.p.trail_atr_mult})

        # SHORT
        if self.p.side in ("short", "both"):
            if self._trend_ok(ema21, ema89, ema200, "short") \
               and self._pullback_seen(df, ema89, atr_s, "short") \
               and self._rebound_ok(c, ema21, "short") \
               and self._xtr_ok(x_long, "short"):

                entry = float(c.iloc[-1]); e89 = float(ema89.iloc[-1]); a = float(atr_s.iloc[-1])
                # nowy filtr: odległość wejścia od EMA89 nie może być zbyt duża (w ATR)
                if not self._entry_distance_ok(entry, e89, a):
                    return Signal()
                sl, tp = self._compute_sl_tp(entry, e89, a, "short")
                self._set_pos("short", entry, sl, tp, a)
                return Signal("enter", "short", meta={"sl": sl, "tp": tp, "trail_atr_mult": self.p.trail_atr_mult})

        return Signal()  # hold





