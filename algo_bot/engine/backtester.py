#!/usr/bin/env python3
"""
algo_bot/engine/backtester.py

Główny silnik backtestowy algo_bot. Wrapper na backtesting.py adaptujący
StrategyBase API (on_bar -> Signal) do BTStrategy z backtesting.py.

Public API:
- run_backtest(symbol, timeframe, strategy, params, ...) -> (stats, equity_df, trades_df)
    Pojedynczy backtest. Zwraca metryki + equity curve + log transakcji
    (z opcjonalnymi mikrostructure adjustments dla slippage/spread).
- save_outputs(rid, symbol, timeframe, strategy, params_in, stats, equity, trades) -> str
    Zapisuje wyniki do results/backtests/<run_id>/{summary.json, params.json, equity.csv, trades.csv}.
- run_id(strategy, symbol, timeframe, params) -> str
    Deterministyczny ID runu (timestamp + strategy + symbol + tf + hash params).

Internal:
- make_bt_wrapper(StratClass, params_obj) -> BTStrategy subclass
    Adapter parsujący Signal na backtesting.py API (buy/sell/position.close).
    Obsługuje TP/SL/trail, cooldown, same-bar TP-vs-SL priority.
- adjust_trades_df(trades, spread_bps, slippage_bps, ...) -> DataFrame
    Post-run adjustment dla mikrostructure (spread, slippage).

CLI:
- python -m algo_bot.engine.backtester --help
- algo-backtest --help (po pip install -e .)

See also:
- docs/adr/003-strategybase-signal-api.md (Signal API)
- docs/adr/005-backtesting-py-mvp-engine.md (rationale silnika)
- docs/reference/modules/engine-backtester.md (TBD)
"""
from __future__ import annotations

import os
import json
import hashlib
import importlib
from dataclasses import is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple, Optional

import numpy as np
import pandas as pd
from backtesting import Backtest
from backtesting import Strategy as BTStrategy

from algo_bot.strategy_base import StrategyBase

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
RAW_DIR = os.path.join(PROJECT_ROOT, "bot_data", "processed")
OUT_DIR = os.path.join(PROJECT_ROOT, "results", "backtests")


# ------------------------------
# Utils
# ------------------------------
def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_id(strategy: str, symbol: str, timeframe: str, params: Dict[str, Any]) -> str:
    h = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    return f"{now_utc_str()}_{strategy}_{symbol.replace('/','')}_{timeframe}_{h}"


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def load_ohlcv_csv(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Oczekuje pliku: bot_data/processed/binance_<SYMBOL_BEZ_SLASH>_<TF>.csv
    np. binance_BTCUSDT_5m.csv
    """
    safe_symbol = symbol.replace("/", "")
    filename = f"binance_{safe_symbol}_{timeframe}.csv"
    path = os.path.join(RAW_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Brak pliku danych: {path}")
    df = pd.read_csv(path, parse_dates=["datetime"])
    need = {"Open", "High", "Low", "Close", "Volume"}
    if not need.issubset(df.columns):
        raise ValueError(f"Plik {path} nie ma wymaganych kolumn {need}")
    df = df.sort_values("datetime").set_index("datetime")
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df


# ------------------------------
# Strategy loading & adapters
# ------------------------------
def resolve_strategy_class(name: str):
    """
    Ładuje strategię z algo_bot.strategies.<name>
    1) preferuje class Strategy (API oparte o StrategyBase)
    2) wsteczna zgodność: CamelCase (np. simple_momentum -> SimpleMomentum)
    """
    module = importlib.import_module(f"algo_bot.strategies.{name}")

    if hasattr(module, "Strategy"):
        return getattr(module, "Strategy")

    class_name = "".join(part.capitalize() for part in name.split("_"))
    if hasattr(module, class_name):
        return getattr(module, class_name)

    raise AttributeError(f"Module algo_bot.strategies.{name} doesn't expose class Strategy or {class_name}")


def coerce_params(StratClass, params_dict: Dict[str, Any]) -> Any:
    """
    Jeśli strategia ma ParamSchema (dataclass), zwróć instancję tej dataclass.
    W przeciwnym razie zwróć zwykły dict (legacy).
    """
    schema = getattr(StratClass, "ParamSchema", None)
    if schema and is_dataclass(schema):
        allowed = {f.name for f in schema.__dataclass_fields__.values()}
        clean = {k: v for k, v in (params_dict or {}).items() if k in allowed}
        return schema(**clean)
    return params_dict or {}


# ------------------------------
# Microstructure helpers (post-run adjustment, zostawiamy hook)
# ------------------------------
def apply_micro_price(base_price: float, side: str, spread_bps: float, slippage_bps: float) -> float:
    half = (spread_bps or 0.0) / 2.0 / 1e4
    slip = (slippage_bps or 0.0) / 1e4
    if side == "buy":
        px = base_price * (1 + half)
        px = px * (1 + slip)
    else:  # sell
        px = base_price * (1 - half)
        px = px * (1 - slip)
    return float(px)


def adjust_trades_df(trades: pd.DataFrame,
                     spread_bps: Optional[float],
                     slippage_bps: Optional[float],
                     symbol: str,
                     timeframe: str) -> pd.DataFrame:
    if trades is None or trades.empty:
        return trades

    t = trades.copy()
    required = {"EntryPrice", "ExitPrice", "EntryTime", "ExitTime"}
    missing = required - set(t.columns)
    if missing:
        # różne wersje backtesting.py – gdy brak kolumn, oddaj surowe
        return t

    # side z Size (standard w backtesting.py: Size > 0 long, < 0 short)
    if "Size" in t.columns:
        side = np.where(t["Size"].astype(float) >= 0, "long", "short")
    else:
        side = np.array(["long"] * len(t))
    t["side"] = side

    if not spread_bps and not slippage_bps:
        return t

    sbps = float(spread_bps or 0.0)
    slbps = float(slippage_bps or 0.0)

    def _adj(px, s_buy_sell):
        return apply_micro_price(float(px), s_buy_sell, sbps, slbps)

    # Entry: long -> buy; short -> sell
    t["AdjEntryPrice"] = np.where(
        t["side"] == "long",
        [_adj(p, "buy") for p in t["EntryPrice"]],
        [_adj(p, "sell") for p in t["EntryPrice"]],
    )
    # Exit: long -> sell; short -> buy
    t["AdjExitPrice"] = np.where(
        t["side"] == "long",
        [_adj(p, "sell") for p in t["ExitPrice"]],
        [_adj(p, "buy") for p in t["ExitPrice"]],
    )

    if "Size" in t.columns:
        qty = t["Size"].astype(float).abs()
    else:
        qty = 1.0

    t["PnL_adj"] = np.where(
        t["side"] == "long",
        (t["AdjExitPrice"] - t["AdjEntryPrice"]) * qty,
        (t["AdjEntryPrice"] - t["AdjExitPrice"]) * qty,
    )

    return t


# ------------------------------
# Wrapper do backtesting.py z egzekucją SL/TP/Trailing + cooldown
# ------------------------------
def make_bt_wrapper(StratClass, params_obj):
    """
    Wrapper StrategyBase -> backtesting.py, z egzekucją SL/TP/traila na podstawie meta.
    Oczekuje, że strategia w meta przekaże: sl, tp, trail (opcjonalnie) oraz że na hold
    może aktualizować te poziomy. Konflikt TP&SL na tej samej świecy rozstrzygamy
    z priorytetem TP (lub wg meta['tp_has_priority'] jeśli strategia poda).
    """
    class Wrapped(BTStrategy):
        trade_on_close = getattr(params_obj, "trade_on_close", False)

        def init(self):
            self._algo = StratClass(params_obj)
            self._has_df = hasattr(self.data, "df")

            # stan egzekucyjny wrappera
            self._pos_side = None           # 'long'/'short'/None — lustrzane do strategii
            self._sl = None                 # float
            self._tp = None                 # float
            self._trail = None              # float
            self._tp_first = True           # domyślnie TP ma priorytet
            # jeśli strategia ma w ParamSchema tp_has_priority – przejmij:
            tp_pref = getattr(params_obj, "tp_has_priority", None)
            if tp_pref is not None:
                self._tp_first = bool(tp_pref)

        def _current_df(self) -> pd.DataFrame:
            n = len(self.data.Close)
            if self._has_df:
                return self.data.df.iloc[:n].copy()
            # fallback: zbuduj df ręcznie
            idx = pd.RangeIndex(n)
            vol = self.data.Volume[:n] if hasattr(self.data, "Volume") else [0]*n
            return pd.DataFrame(
                {"Open":  self.data.Open[:n],
                 "High":  self.data.High[:n],
                 "Low":   self.data.Low[:n],
                 "Close": self.data.Close[:n],
                 "Volume": vol},
                index=idx,
            )

        # pomocnicze: rozstrzygnięcie „TP i SL w jednej świecy”
        def _same_bar_hit(self, side: str, high: float, low: float) -> str | None:
            if self._sl is None or self._tp is None:
                return None
            if side == "long":
                hit_tp = high >= self._tp
                hit_sl = low  <= self._sl
            else:  # short
                hit_tp = low  <= self._tp
                hit_sl = high >= self._sl

            if hit_tp and hit_sl:
                return "tp" if self._tp_first else "sl"
            if hit_tp: return "tp"
            if hit_sl: return "sl"
            return None

        # zamknięcie po konkretnej cenie (żeby PnL był „czysty”)
        def _close_at(self, price: float):
            try:
                self.position.close(price=price)
            except TypeError:
                # starsze backtesting.py może nie przyjmować price — zamykamy „rynkowo”
                self.position.close()

        def next(self):
            df = self._current_df()
            sig = self._algo.on_bar(df)

            # --- aktualizacja/egzekucja gdy mamy pozycję ---
            if self.position:
                # jeżeli strategia wysłała meta na hold/exit — zaktualizuj SL/TP/trail
                if sig.meta:
                    if "sl" in sig.meta and sig.meta["sl"] is not None:
                        # zacieśniaj – nie oddalaj
                        if self._pos_side == "long":
                            self._sl = max(self._sl or -float('inf'), float(sig.meta["sl"]))
                        else:
                            self._sl = min(self._sl or float('inf'), float(sig.meta["sl"]))
                    if "tp" in sig.meta and sig.meta["tp"] is not None:
                        self._tp = float(sig.meta["tp"])
                    if "trail" in sig.meta and sig.meta["trail"] is not None:
                        # trail też traktujemy jako sugestię do SL
                        tr = float(sig.meta["trail"])
                        if self._pos_side == "long":
                            self._sl = max(self._sl or -float('inf'), tr)
                        else:
                            self._sl = min(self._sl or float('inf'), tr)
                    if "tp_has_priority" in sig.meta:
                        self._tp_first = bool(sig.meta["tp_has_priority"])

                # pozwól strategii dołożyć do istniejącej pozycji (pyramiding)
                if sig.action == "enter":
                    side = sig.side or self._pos_side
                    if side == self._pos_side and side in ("long", "short"):
                        try:
                            if side == "long":
                                self.buy(size=sig.size)
                            else:
                                self.sell(size=sig.size)
                        except TypeError:
                            # starsze backtesting.py może nie przyjmować size
                            if side == "long":
                                self.buy()
                            else:
                                self.sell()
                        return

                # 1) jeśli strategia mówi EXIT — zamknij po cenie zamknięcia tej świecy (gdy trade_on_close)
                if sig.action == "exit":
                    px = float(df["Close"].iloc[-1]) if self.trade_on_close else None
                    self._close_at(px) if px is not None else self.position.close()
                    self._pos_side = None; self._sl = self._tp = self._trail = None
                    return

                # 2) intrabar check: TP/SL na tej świecy po High/Low
                h = float(df["High"].iloc[-1]); l = float(df["Low"].iloc[-1])
                which = self._same_bar_hit(self._pos_side, h, l)
                if which == "tp":
                    self._close_at(self._tp)
                    self._pos_side = None; self._sl = self._tp = self._trail = None
                    return
                if which == "sl":
                    self._close_at(self._sl)
                    self._pos_side = None; self._sl = self._tp = self._trail = None
                    return

                # 3) inaczej: trwaj, nic nie rób (poziomy już zaktualizowane)
                return

            # --- nie mamy pozycji: obsłuż ENTER/EXIT ---
            if sig.action == "enter" and not self.position:
                # kierunek i ewentualne poziomy z meta
                side = sig.side or getattr(self._algo, "side", None)
                if side not in ("long", "short"):
                    return  # brak kierunku -> nic

                self._pos_side = side

                # poziomy
                self._sl = float(sig.meta.get("sl")) if sig.meta and "sl" in sig.meta else None
                self._tp = float(sig.meta.get("tp")) if sig.meta and "tp" in sig.meta else None
                if sig.meta and "tp_has_priority" in sig.meta:
                    self._tp_first = bool(sig.meta["tp_has_priority"])

                # otwarcie
                try:
                    if side == "long":
                        self.buy(size=sig.size)
                    else:
                        self.sell(size=sig.size)
                except TypeError:
                    # starsze backtesting.py może nie przyjmować size
                    if side == "long":
                        self.buy()
                    else:
                        self.sell()
                return
            # sygnał EXIT bez pozycji — ignorujemy
            # HOLD bez pozycji — ignorujemy

    return Wrapped



# ------------------------------
# Backtest runner
# ------------------------------
def run_backtest(
    symbol: str,
    timeframe: str,
    strategy: str,
    params: Dict[str, Any],
    start: str | None = None,
    end: str | None = None,
    cash: float = 100_000.0,
    commission: float = 0.0004,
    trade_on_close: bool = True,
    slippage_bps: float | None = None,
    spread_bps: float | None = None,
    unit_scale: float | None = None,
) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """
    Zwraca: (stats_raw, equity_df, trades_df_exec_adjusted)
    """
    # 1) Dane
    df = load_ohlcv_csv(symbol, timeframe)
    if start:
        df = df[df.index >= pd.to_datetime(start, utc=True)]
    if end:
        df = df[df.index <= pd.to_datetime(end, utc=True)]
    if df.empty:
        raise ValueError("Zakres dat zwrócił pusty zbiór danych.")

    # 2a) Opcjonalna skala jednostki (np. 0.001 → 1 'udział' = 0.001 BTC)
    if unit_scale is not None and float(unit_scale) != 1.0:
        s = float(unit_scale)
        for c in ("Open", "High", "Low", "Close"):
            df[c] = df[c] * s

    # 2) Strategia
    StratClass = resolve_strategy_class(strategy)

    # 3) Paramy
    params_obj = coerce_params(StratClass, params)

    # 4) Adapter (StrategyBase vs legacy)
    if issubclass(StratClass, StrategyBase):
        BTStrat = make_bt_wrapper(StratClass, params_obj)
        run_kwargs = {}
    else:
        BTStrat = StratClass
        run_kwargs = params_obj

    # 5) Backtest
    # 5a) Exclusive orders — dla DCA/pyramiding pozwól na wielokrotne pozycje w tym samym kierunku
    exclusive = True
    if issubclass(StratClass, StrategyBase):
        try:
            allow_pyr = getattr(params_obj, "allow_pyramiding", None)
            if allow_pyr is True:
                exclusive = False
        except Exception:
            pass

    bt = Backtest(
        df,
        BTStrat,
        cash=cash,
        commission=commission,
        trade_on_close=trade_on_close,
        exclusive_orders=exclusive,
    )

    stats = bt.run(**run_kwargs)

    # 6) Equity i raw trades
    equity = stats._equity_curve.copy()
    trades_raw = stats._trades.copy() if hasattr(stats, "_trades") else pd.DataFrame()

    # 7) Post-run microstructure adjustment (opcjonalnie)
    trades_adj = adjust_trades_df(trades_raw, spread_bps, slippage_bps, symbol, timeframe)

    # 8) Dorzuć meta do stats
    stats = dict(stats)
    stats["_microstructure"] = {
        "spread_bps": spread_bps,
        "slippage_bps": slippage_bps,
        "note": "trades.csv zawiera ewentualne korekty AdjEntry/AdjExit; equity pozostaje z silnika.",
    }
    if unit_scale is not None and float(unit_scale) != 1.0:
        stats["_scaling"] = {"unit_scale": float(unit_scale)}

    return stats, equity, trades_adj


def save_outputs(
    rid: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    params_in: Dict[str, Any],
    stats: Dict[str, Any],
    equity: pd.DataFrame,
    trades: pd.DataFrame,
) -> str:
    out_dir = os.path.join(OUT_DIR, rid)
    ensure_dir(out_dir)

    # summary
    summary = {}
    for k, v in stats.items():
        try:
            json.dumps(v)
            summary[k] = v
        except Exception:
            summary[k] = str(v)

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    with open(os.path.join(out_dir, "params.json"), "w") as f:
        json.dump(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "strategy": strategy,
                "params": params_in,
                "run_id": rid,
                "created_at_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            },
            f,
            indent=2,
        )

    equity.to_csv(os.path.join(out_dir, "equity.csv"), index=True)
    trades.to_csv(os.path.join(out_dir, "trades.csv"), index=False)

    print(f"[OK] Wyniki zapisane w: {out_dir}")
    return out_dir


# ------------------------------
# CLI
# ------------------------------
def parse_args():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="np. BTC/USDT")
    ap.add_argument("--timeframe", required=True, help="np. 5m, 1h, 4h")
    ap.add_argument("--strategy", required=True, help="nazwa modułu w strategies/, np. xtrender_pullback")
    ap.add_argument("--params", default="{}", help="JSON z parametrami strategii (dla StrategyBase: ParamSchema)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--commission", type=float, default=0.0004)
    ap.add_argument("--trade_on_close", action="store_true")
    ap.add_argument("--slippage_bps", type=float, default=None)
    ap.add_argument("--spread_bps", type=float, default=None)
    ap.add_argument("--unit_scale", type=float, default=None,
                    help="Opcjonalny mnożnik cen (np. 0.001 → 1 jednostka = 0.001 instrumentu)")
    return ap.parse_args()


def main():
    args = parse_args()
    try:
        params = json.loads(args.params)
    except Exception as e:
        raise SystemExit(f"Niepoprawny JSON w --params: {e}")

    rid = run_id(args.strategy, args.symbol, args.timeframe, params)

    stats, equity, trades = run_backtest(
        symbol=args.symbol,
        timeframe=args.timeframe,
        strategy=args.strategy,
        params=params,
        start=args.start,
        end=args.end,
        cash=args.cash,
        commission=args.commission,
        trade_on_close=args.trade_on_close,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
        unit_scale=args.unit_scale,
    )

    save_outputs(
        rid=rid,
        symbol=args.symbol,
        timeframe=args.timeframe,
        strategy=args.strategy,
        params_in=params,
        stats=stats,
        equity=equity,
        trades=trades,
    )


if __name__ == "__main__":
    main()
