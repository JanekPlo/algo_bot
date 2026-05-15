# algo_bot/strategy/ema_cross.py
import pandas as pd

class EMACross:
    def __init__(self, fast=9, slow=21):
        self.fast = fast
        self.slow = slow
        self.frames = {}  # symbol -> DataFrame z kolumnami: open, high, low, close, volume, close_time

    def seed(self, symbol, df):  # podaj historyczne świece
        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=self.fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow, adjust=False).mean()
        self.frames[symbol] = df

    def on_new_kline(self, symbol, k):  # k = dict z danymi kline z WS
        df = self.frames[symbol]
        # zamknęła się świeca?
        if k["x"]:
            row = {
                "open": float(k["o"]), "high": float(k["h"]),
                "low": float(k["l"]), "close": float(k["c"]),
                "volume": float(k["v"]), "close_time": int(k["T"])
            }
            df.loc[len(df)] = row
            df["ema_fast"] = df["close"].ewm(span=self.fast, adjust=False).mean()
            df["ema_slow"] = df["close"].ewm(span=self.slow, adjust=False).mean()

            # sygnał na zamknięciu świecy
            if len(df) < self.slow + 5:
                return None
            f_prev, s_prev = df["ema_fast"].iloc[-2], df["ema_slow"].iloc[-2]
            f_now,  s_now  = df["ema_fast"].iloc[-1], df["ema_slow"].iloc[-1]
            if f_prev <= s_prev and f_now > s_now:
                return "BUY"
            if f_prev >= s_prev and f_now < s_now:
                return "SELL"
        return None
