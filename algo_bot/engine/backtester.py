#!/usr/bin/env python3
"""
algo_bot/engine/backtester.py

Główny silnik backtestowy algo_bot. Wrapper na backtesting.py adaptujący
StrategyBase API (on_bar -> Signal) do BTStrategy z backtesting.py.

Public API:
- run_backtest(symbol, timeframe, strategy, params, ..., data=None) -> (stats, equity_df, trades_df)
    Pojedynczy backtest. Zwraca metryki + equity curve + log transakcji
    (z opcjonalnymi mikrostructure adjustments dla slippage/spread).
    Opcjonalny ``data`` pozwala wstrzyknąć pre-loaded OHLCV DataFrame (testy
    deterministyczne, walk-forward per fold) zamiast ładowania z CSV.
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

import contextlib
import hashlib
import importlib
import json
import math
import os
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy as BTStrategy

from algo_bot.log import get_logger, setup_logging
from algo_bot.metrics import MetricsSummary, summarize
from algo_bot.risk import (
    RiskLimitBreached,
    RiskLimits,
    check_all,
    init_state,
    update_state,
)
from algo_bot.strategy_base import StrategyBase

logger = get_logger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
RAW_DIR = os.path.join(PROJECT_ROOT, "bot_data", "processed")
OUT_DIR = os.path.join(PROJECT_ROOT, "results", "backtests")

# Defaults silnika backtestowego — single source of truth (cleanup 2026-06-11).
# CLI algo-backtest / algo-sweep / algo-walkforward importują te wartości zamiast
# trzymać własne kopie; config.yaml ich NIE definiuje (sekcja backtest: jest
# informacyjna — patrz docs/reference/config-reference.md).
DEFAULT_CASH = 1_000_000.0  # >> max(High) BTC — bez warningu fractional trading
DEFAULT_COMMISSION = 0.0004  # 4 bps = Binance USDT-M taker fee


# ------------------------------
# Utils
# ------------------------------
def now_utc_str() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def run_id(strategy: str, symbol: str, timeframe: str, params: dict[str, Any]) -> str:
    h = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    return f"{now_utc_str()}_{strategy}_{symbol.replace('/', '')}_{timeframe}_{h}"


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
        return module.Strategy

    class_name = "".join(part.capitalize() for part in name.split("_"))
    if hasattr(module, class_name):
        return getattr(module, class_name)

    raise AttributeError(
        f"Module algo_bot.strategies.{name} doesn't expose class Strategy or {class_name}"
    )


def coerce_params(StratClass, params_dict: dict[str, Any]) -> Any:
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
def apply_micro_price(
    base_price: float, side: str, spread_bps: float, slippage_bps: float
) -> float:
    half = (spread_bps or 0.0) / 2.0 / 1e4
    slip = (slippage_bps or 0.0) / 1e4
    if side == "buy":
        px = base_price * (1 + half)
        px = px * (1 + slip)
    else:  # sell
        px = base_price * (1 - half)
        px = px * (1 - slip)
    return float(px)


def adjust_trades_df(
    trades: pd.DataFrame,
    spread_bps: float | None,
    slippage_bps: float | None,
    symbol: str,
    timeframe: str,
) -> pd.DataFrame:
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

    qty = t["Size"].astype(float).abs() if "Size" in t.columns else 1.0

    t["PnL_adj"] = np.where(
        t["side"] == "long",
        (t["AdjExitPrice"] - t["AdjEntryPrice"]) * qty,
        (t["AdjEntryPrice"] - t["AdjExitPrice"]) * qty,
    )

    return t


# ------------------------------
# Wrapper do backtesting.py z egzekucją SL/TP/Trailing + cooldown
# ------------------------------
def make_bt_wrapper(StratClass, params_obj, risk_limits: RiskLimits | None = None):
    """
    Wrapper StrategyBase -> backtesting.py, z egzekucją SL/TP/traila na podstawie meta.
    Oczekuje, że strategia w meta przekaże: sl, tp, trail (opcjonalnie) oraz że na hold
    może aktualizować te poziomy. Konflikt TP&SL na tej samej świecy rozstrzygamy
    z priorytetem TP (lub wg meta['tp_has_priority'] jeśli strategia poda).

    Risk module hook (ADR-008): gdy ``risk_limits`` jest podany, wrapper na początku
    każdego ``next()`` aktualizuje ``RiskState`` i woła ``check_all``. Na breach
    zamyka otwartą pozycję po Close ostatniego bara (forced exit) i podnosi
    ``RiskLimitBreached`` — łapane przez ``run_backtest`` i serializowane jako
    ``stats["_risk_breach"]``. ``risk_limits=None`` (default) → brak hook'a,
    backward-compatible.
    """

    class Wrapped(BTStrategy):
        trade_on_close = getattr(params_obj, "trade_on_close", False)

        def init(self):
            self._algo = StratClass(params_obj)
            self._has_df = hasattr(self.data, "df")

            # stan egzekucyjny wrappera
            self._pos_side = None  # 'long'/'short'/None — lustrzane do strategii
            self._sl = None  # float
            self._tp = None  # float
            self._trail = None  # float
            self._tp_first = True  # domyślnie TP ma priorytet
            # jeśli strategia ma w ParamSchema tp_has_priority – przejmij:
            tp_pref = getattr(params_obj, "tp_has_priority", None)
            if tp_pref is not None:
                self._tp_first = bool(tp_pref)

            # Risk module state — lazy init przy pierwszym wywołaniu next()
            # (potrzebujemy timestamp pierwszego bara, niedostępny w init()).
            self._risk_limits = risk_limits
            self._risk_state = None  # type: ignore[assignment]

        def _current_df(self) -> pd.DataFrame:
            n = len(self.data.Close)
            if self._has_df:
                return self.data.df.iloc[:n].copy()
            # fallback: zbuduj df ręcznie
            idx = pd.RangeIndex(n)
            vol = self.data.Volume[:n] if hasattr(self.data, "Volume") else [0] * n
            return pd.DataFrame(
                {
                    "Open": self.data.Open[:n],
                    "High": self.data.High[:n],
                    "Low": self.data.Low[:n],
                    "Close": self.data.Close[:n],
                    "Volume": vol,
                },
                index=idx,
            )

        # pomocnicze: rozstrzygnięcie „TP i SL w jednej świecy”
        def _same_bar_hit(self, side: str, high: float, low: float) -> str | None:
            if self._sl is None or self._tp is None:
                return None
            if side == "long":
                hit_tp = high >= self._tp
                hit_sl = low <= self._sl
            else:  # short
                hit_tp = low <= self._tp
                hit_sl = high >= self._sl

            if hit_tp and hit_sl:
                return "tp" if self._tp_first else "sl"
            if hit_tp:
                return "tp"
            if hit_sl:
                return "sl"
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

            # === Risk module gate (ADR-008) ===
            # Sprawdzamy PRZED logiką strategii: jeśli equity przekroczyło DD/daily
            # loss/positions cap, zamykamy pozycję i podnosimy RiskLimitBreached.
            if self._risk_limits is not None:
                bar_ts = (
                    df.index[-1]
                    if isinstance(df.index, pd.DatetimeIndex)
                    else (pd.Timestamp.utcnow())
                )
                equity_now = float(self.equity)

                if self._risk_state is None:
                    # Pierwszy bar — inicjalizujemy state startowym equity
                    self._risk_state = init_state(
                        equity_start=equity_now, ts=bar_ts, limits=self._risk_limits
                    )
                else:
                    self._risk_state = update_state(
                        state=self._risk_state,
                        equity_now=equity_now,
                        ts=bar_ts,
                        open_positions=int(bool(self.position)),
                        limits=self._risk_limits,
                    )

                breach = check_all(
                    state=self._risk_state,
                    equity_now=equity_now,
                    ts=bar_ts,
                    limits=self._risk_limits,
                )
                if breach is not None:
                    logger.warning(
                        "Risk limit breached — halting backtest",
                        extra={
                            "kind": breach.kind,
                            "value": breach.value,
                            "threshold": breach.threshold,
                            "ts": str(breach.ts),
                        },
                    )
                    # Forced exit: zamknij otwartą pozycję po cenie Close bara,
                    # żeby trades.csv zawierał ekspozycję końcową.
                    if self.position:
                        close_px = float(df["Close"].iloc[-1])
                        self._close_at(close_px)
                        self._pos_side = None
                        self._sl = self._tp = self._trail = None
                    raise RiskLimitBreached(breach)

            sig = self._algo.on_bar(df)

            # --- aktualizacja/egzekucja gdy mamy pozycję ---
            if self.position:
                # jeżeli strategia wysłała meta na hold/exit — zaktualizuj SL/TP/trail
                if sig.meta:
                    if "sl" in sig.meta and sig.meta["sl"] is not None:
                        # zacieśniaj – nie oddalaj
                        if self._pos_side == "long":
                            self._sl = max(self._sl or -float("inf"), float(sig.meta["sl"]))
                        else:
                            self._sl = min(self._sl or float("inf"), float(sig.meta["sl"]))
                    if "tp" in sig.meta and sig.meta["tp"] is not None:
                        self._tp = float(sig.meta["tp"])
                    if "trail" in sig.meta and sig.meta["trail"] is not None:
                        # trail też traktujemy jako sugestię do SL
                        tr = float(sig.meta["trail"])
                        if self._pos_side == "long":
                            self._sl = max(self._sl or -float("inf"), tr)
                        else:
                            self._sl = min(self._sl or float("inf"), tr)
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
                    self._pos_side = None
                    self._sl = self._tp = self._trail = None
                    return

                # 2) intrabar check: TP/SL na tej świecy po High/Low
                h = float(df["High"].iloc[-1])
                lo = float(df["Low"].iloc[-1])
                which = self._same_bar_hit(self._pos_side, h, lo)
                if which == "tp":
                    self._close_at(self._tp)
                    self._pos_side = None
                    self._sl = self._tp = self._trail = None
                    return
                if which == "sl":
                    self._close_at(self._sl)
                    self._pos_side = None
                    self._sl = self._tp = self._trail = None
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
    params: dict[str, Any],
    start: str | None = None,
    end: str | None = None,
    cash: float = DEFAULT_CASH,
    commission: float = DEFAULT_COMMISSION,
    trade_on_close: bool = True,
    slippage_bps: float | None = None,
    spread_bps: float | None = None,
    unit_scale: float | None = None,
    data: pd.DataFrame | None = None,
    risk_limits: RiskLimits | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """
    Zwraca: (stats_raw, equity_df, trades_df_exec_adjusted)

    Args:
        data: Opcjonalny pre-loaded DataFrame z OHLCV (Open/High/Low/Close/Volume,
            DatetimeIndex). Gdy podany — pomija ``load_ohlcv_csv`` i używa
            wstrzykniętych danych. Używane przez (a) testy deterministyczne
            (bez wymogu pliku CSV w bot_data/processed/) i (b) walk-forward
            (Decyzja F) który wstrzykuje slice'y per fold. Argumenty ``symbol``
            i ``timeframe`` w tej ścieżce służą tylko do metadanych (run_id,
            mikrostructure adjustment, save_outputs).
        risk_limits: Opcjonalna konfiguracja limitów ryzyka (ADR-008). Gdy
            podana, wrapper sprawdza max DD / daily loss / max positions na
            każdym barze; breach kończy run i wpisuje detale do
            ``stats["_risk_breach"]``. ``None`` (default) → brak gate'ów,
            backward-compatible z poprzednim API.
    """
    # 1) Dane — albo wstrzyknięte z zewnątrz, albo ładowane z bot_data/processed/
    if data is not None:
        df = data.copy()
        # Wymagamy DatetimeIndex i kolumn OHLCV (kontrakt jak load_ohlcv_csv)
        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(df.columns):
            raise ValueError(f"Wstrzyknięty DataFrame nie ma wymaganych kolumn {need}")
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("Wstrzyknięty DataFrame musi mieć DatetimeIndex")
    else:
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
        BTStrat = make_bt_wrapper(StratClass, params_obj, risk_limits=risk_limits)
        run_kwargs = {}
    else:
        if risk_limits is not None:
            # Legacy strategies (nie-StrategyBase) nie mają wrappera — risk hook
            # niedostępny. Łatwiej dorobić niż udawać że działa.
            logger.warning(
                "risk_limits ignored — legacy strategy without StrategyBase wrapper",
                extra={"strategy": strategy},
            )
        BTStrat = StratClass
        run_kwargs = params_obj

    # 5) Backtest
    # 5a) Exclusive orders — dla DCA/pyramiding pozwól na wielokrotne pozycje w tym samym kierunku
    exclusive = True
    if issubclass(StratClass, StrategyBase):
        with contextlib.suppress(Exception):
            if getattr(params_obj, "allow_pyramiding", None) is True:
                exclusive = False

    bt = Backtest(
        df,
        BTStrat,
        cash=cash,
        commission=commission,
        trade_on_close=trade_on_close,
        exclusive_orders=exclusive,
    )

    risk_breach: dict[str, Any] | None = None
    try:
        stats = bt.run(**run_kwargs)
    except RiskLimitBreached as exc:
        # Risk module zatrzymał run — wyciągamy stats z częściowego stanu.
        # backtesting.py trzyma `stats` na strategii dopiero po zakończeniu pętli,
        # więc po przerwaniu wyjątkiem nie mamy pełnego Stats obiektu. Budujemy
        # minimalne stats ręcznie z bt-żyjącego strategy object (jeśli się da).
        logger.warning(
            "Backtest stopped by risk limit",
            extra={"kind": exc.breach.kind, "value": exc.breach.value},
        )
        risk_breach = {
            "kind": exc.breach.kind,
            "value": float(exc.breach.value),
            "threshold": float(exc.breach.threshold),
            "ts": str(exc.breach.ts),
            "message": exc.breach.message,
        }
        # Fallback stats — sięgamy bezpośrednio do internals backtesting.py.
        # `bt._results` zostaje ustawione przez `bt.run()` PRZED ewentualnym
        # wyjątkiem ze strategii nie zawsze; w trudnych przypadkach budujemy
        # placeholder żeby downstream nie crashował.
        stats = getattr(bt, "_results", None)
        if stats is None:
            stats = pd.Series(
                {
                    "Equity Final [$]": float(cash),
                    "Return [%]": 0.0,
                    "# Trades": 0,
                }
            )

    # 6) Equity i raw trades
    equity = (
        stats._equity_curve.copy()
        if hasattr(stats, "_equity_curve")
        else pd.DataFrame({"Equity": [float(cash)]}, index=[df.index[0]])
    )
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

    # Risk module breach (ADR-008) — gdy run został zatrzymany przez limit
    if risk_breach is not None:
        stats["_risk_breach"] = risk_breach

    # Konfiguracja risk limits jako metadana (zawsze, nawet bez breach)
    if risk_limits is not None:
        stats["_risk_limits"] = asdict(risk_limits)

    return stats, equity, trades_adj


def _metrics_summary_to_json_safe(summary: MetricsSummary) -> dict[str, Any]:
    """Konwertuje ``MetricsSummary`` do dict-a JSON-safe (NaN/inf → None).

    JSON spec nie pozwala na NaN/Infinity; ``json.dumps(nan)`` wypluwa "NaN"
    co większość parserów odrzuca. Mapujemy NaN i inf na ``None`` żeby
    ``summary.json`` był walidowalny przez ``json.loads`` po dowolnej stronie.
    """
    raw = asdict(summary)
    safe: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            safe[k] = None
        else:
            safe[k] = v
    return safe


def save_outputs(
    rid: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    params_in: dict[str, Any],
    stats: dict[str, Any],
    equity: pd.DataFrame,
    trades: pd.DataFrame,
) -> str:
    out_dir = os.path.join(OUT_DIR, rid)
    ensure_dir(out_dir)

    # MetricsSummary embed (post-ADR-007 follow-up, ADR-008 §12)
    # Liczymy summary z equity + trades.PnL gdy mamy dostępne kolumny;
    # NaN/inf → None dla JSON safety. Wszystko opakowane w try/except
    # żeby błąd metrics nie wywalił całego save_outputs.
    try:
        equity_series = equity["Equity"] if "Equity" in equity.columns else None
        trades_pnl = None
        if not trades.empty and "PnL" in trades.columns:
            trades_pnl = trades["PnL"]
        if equity_series is not None and len(equity_series) > 1:
            metrics_summary = summarize(equity=equity_series, trades_pnl=trades_pnl)
            stats["_metrics_summary"] = _metrics_summary_to_json_safe(metrics_summary)
    except Exception as e:
        logger.warning(
            "Failed to compute MetricsSummary for summary.json",
            extra={"error": str(e)},
        )

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

    logger.info("Wyniki backtestu zapisane", extra={"out_dir": out_dir, "run_id": rid})
    return out_dir


# ------------------------------
# CLI
# ------------------------------
def parse_args():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="np. BTC/USDT")
    ap.add_argument("--timeframe", required=True, help="np. 5m, 1h, 4h")
    ap.add_argument(
        "--strategy", required=True, help="nazwa modułu w strategies/, np. xtrender_pullback"
    )
    ap.add_argument(
        "--params",
        default="{}",
        help="JSON z parametrami strategii (dla StrategyBase: ParamSchema)",
    )
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--cash", type=float, default=DEFAULT_CASH)
    ap.add_argument("--commission", type=float, default=DEFAULT_COMMISSION)
    ap.add_argument("--trade_on_close", action="store_true")
    ap.add_argument("--slippage_bps", type=float, default=None)
    ap.add_argument("--spread_bps", type=float, default=None)
    ap.add_argument(
        "--unit_scale",
        type=float,
        default=None,
        help="Opcjonalny mnożnik cen (np. 0.001 → 1 jednostka = 0.001 instrumentu)",
    )
    # Risk module flags (ADR-008) — opcjonalne, brak = brak gate'ów (backward compat)
    ap.add_argument(
        "--max_dd_pct",
        type=float,
        default=None,
        help="Max drawdown stop jako fraction (np. 0.20 = 20%%). Brak = wyłączone.",
    )
    ap.add_argument(
        "--daily_loss_pct",
        type=float,
        default=None,
        help="Daily loss limit jako fraction (np. 0.05 = 5%%). Brak = wyłączone.",
    )
    ap.add_argument(
        "--risk_per_trade_pct",
        type=float,
        default=None,
        help=(
            "% equity per trade jako fraction (np. 0.01 = 1%%). Używane przez "
            "position_size helper — strategia musi go zawołać explicit."
        ),
    )
    ap.add_argument(
        "--daily_reset_tz",
        type=str,
        default="UTC",
        help="IANA timezone dla daily_loss reset (default UTC).",
    )
    return ap.parse_args()


def main():
    # Inicjalizacja loggera (ADR-006): konsola + rotating JSON file w logs/algo_bot.log.
    setup_logging()
    args = parse_args()
    try:
        params = json.loads(args.params)
    except Exception as e:
        raise SystemExit(f"Niepoprawny JSON w --params: {e}") from e

    rid = run_id(args.strategy, args.symbol, args.timeframe, params)

    # Risk limits z CLI (ADR-008) — None gdy żaden próg nie podany
    risk_limits = None
    if (
        args.max_dd_pct is not None
        or args.daily_loss_pct is not None
        or args.risk_per_trade_pct is not None
    ):
        risk_limits = RiskLimits(
            max_drawdown_pct=args.max_dd_pct,
            daily_loss_pct=args.daily_loss_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            daily_reset_tz=args.daily_reset_tz,
        )

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
        risk_limits=risk_limits,
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
