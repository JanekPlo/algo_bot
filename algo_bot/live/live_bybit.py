#!/usr/bin/env python3
"""algo_bot/live/live_bybit.py

Live trading runner dla Bybit v5 linear USDT perpetuals (ADR-015). Testnet-first:
domyślnie łączy się z Bybit testnet (zero-risk sanity), rozszerzenie mainnet w
Fazie 3 (Decyzja 4c). Wczytuje strategię przez ``load_strategy()``; pętla: czekaj
na zamknięcie świecy → ``on_bar(df) -> Signal`` → execute na Bybit przez
``BybitFuturesAdapter`` (CCXT).

Tryby TP/SL (--tpsl_mode, analog ADR-004, ale Bybit-native trading-stop):
- 'server' — TP+SL jako Bybit position trading-stop (whole-position, niezawodne)
- 'local'  — SL lokalnie z mark price (bot pilnuje), TP też lokalnie

Position model: One-Way (Decyzja 6) — jedna pozycja per symbol, ``reduce_only``
działa (brak binance'owego ograniczenia Hedge Mode). ``close_all_positions()``
implementuje Bybit Close-All parity: sekwencyjnie cancel-all-orders → market
close reduce-only pozycji, z logiem każdego kroku i best-effort retry.

Bezpieczeństwo (ADR-015): klucze z .env (BYBIT_*_TESTNET / BYBIT_*), NIGDY w git.

CLI:
- python -m algo_bot.live.live_bybit --symbol BTC/USDT --timeframe 15m \
      --strategy simple_momentum --data_source testnet

Required env vars (przez python-dotenv z .env):
- BYBIT_API_KEY_TESTNET / BYBIT_API_SECRET_TESTNET (dla --data_source=testnet)
- BYBIT_API_KEY / BYBIT_API_SECRET (dla --data_source=mainnet, Faza 3)

See also:
- docs/adr/015-exchange-migration-bybit.md
- algo_bot/engine/exchanges/bybit_adapter.py (CCXT wrapper)
- algo_bot/telemetry/journal.py (CSV journal)
- live/live_binance.py (legacy referencyjny loop Binance)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from algo_bot.engine.exchanges.bybit_adapter import BybitFuturesAdapter
from algo_bot.log import get_logger, setup_logging
from algo_bot.strategy_loader import load_strategy
from algo_bot.telemetry.journal import Journal

logger = get_logger(__name__)

_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def ohlcv_to_df(ohlcv: list[list[float]]) -> pd.DataFrame:
    """Lista CCXT OHLCV → DataFrame z UTC DatetimeIndex (kontrakt on_bar)."""
    df = pd.DataFrame(ohlcv, columns=["ts", *_OHLCV_COLS])
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("datetime")[_OHLCV_COLS]


def wait_for_next_close(
    adapter: BybitFuturesAdapter, symbol: str, timeframe: str, last_ts: int
) -> int:
    """Blokuje do zamknięcia kolejnej świecy; zwraca ts (ms) nowej świecy."""
    tf_sec = adapter.exchange.parse_timeframe(timeframe)
    next_ts = last_ts + tf_sec * 1000
    while True:
        now_ms = adapter.exchange.milliseconds()
        sleep_ms = max(0, next_ts - now_ms + 500)
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)
        try:
            ts_last = int(adapter.fetch_ohlcv(symbol, timeframe, limit=1)[-1][0])
            if ts_last != last_ts:
                return ts_last
        except Exception as e:
            logger.warning("Błąd kline (retry za 1s)", extra={"error": str(e)})
            time.sleep(1)


def default_run_id(strategy: str, symbol: str, timeframe: str) -> str:
    stamp = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_bybit_{strategy}_{symbol.replace('/', '')}_{timeframe}"


def read_position_info(
    adapter: BybitFuturesAdapter, symbol: str
) -> tuple[float, str | None, float]:
    """Zwraca (qty_abs, side, entry_price) lub (0.0, None, 0.0)."""
    with contextlib.suppress(Exception):
        market_symbol = adapter.sym(symbol)
        m_id = adapter.exchange.market(market_symbol)["id"]
        for p in adapter.exchange.fetch_positions([market_symbol]):
            info = p.get("info", {}) or {}
            if (p.get("symbol") not in (market_symbol, m_id)) and (
                info.get("symbol") not in (market_symbol, m_id)
            ):
                continue
            size = p.get("contracts")
            if size is None:
                size = info.get("size") or 0.0
            amt = float(size)
            if amt == 0.0:
                continue
            side_raw = (p.get("side") or info.get("side") or "").lower()
            side = "short" if side_raw in ("short", "sell") else "long"
            entry = p.get("entryPrice") or info.get("avgPrice") or info.get("entryPrice") or 0.0
            return abs(amt), side, float(entry or 0.0)
    return 0.0, None, 0.0


def close_all_positions(
    adapter: BybitFuturesAdapter,
    symbol: str,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Bybit Close-All parity: cancel-all-orders → market close reduce-only pozycji.

    Sekwencyjnie (Decyzja 7): najpierw anuluj wszystkie open orders (żeby nie
    zostały osierocone TP/SL), potem zamknij netto pozycję market reduce-only.
    Każdy krok logowany; transient errory retryowane best-effort (nie rzucamy —
    Close-All to safety path, ma dążyć do flat nawet przy częściowej awarii).

    Returns:
        dict z podsumowaniem: ``cancelled`` (bool), ``closed_qty`` (float),
        ``close_side`` (str | None), ``errors`` (list[str]).
    """
    errors: list[str] = []

    # Krok 1: cancel-all-orders (retry).
    cancelled = False
    for attempt in range(1, max_retries + 1):
        try:
            adapter.cancel_all_orders(symbol)
            cancelled = True
            logger.info("Close-All: cancel_all_orders OK", extra={"symbol": symbol})
            break
        except Exception as e:
            errors.append(f"cancel_all_orders#{attempt}: {e}")
            logger.warning(
                "Close-All: cancel_all_orders nieudane (retry)",
                extra={"symbol": symbol, "attempt": attempt, "error": str(e)},
            )
            time.sleep(min(2.0, attempt))

    # Krok 2: market close reduce-only netto pozycji (retry; re-fetch po każdej próbie).
    closed_qty = 0.0
    close_side: str | None = None
    for attempt in range(1, max_retries + 1):
        pos = adapter.fetch_positions(symbol)
        if abs(pos) == 0.0:
            logger.info("Close-All: brak pozycji do zamknięcia (flat)", extra={"symbol": symbol})
            break
        close_side = "buy" if pos < 0 else "sell"
        qty = abs(adapter.amount_to_precision(symbol, abs(pos)))
        try:
            adapter.create_market(symbol, close_side, qty, reduce_only=True)
            closed_qty = qty
            logger.info(
                "Close-All: pozycja zamknięta (reduceOnly market)",
                extra={"symbol": symbol, "side": close_side, "qty": qty},
            )
            break
        except Exception as e:
            errors.append(f"close#{attempt}: {e}")
            logger.warning(
                "Close-All: market close nieudane (retry)",
                extra={"symbol": symbol, "attempt": attempt, "error": str(e)},
            )
            time.sleep(min(2.0, attempt))

    return {
        "cancelled": cancelled,
        "closed_qty": closed_qty,
        "close_side": close_side,
        "errors": errors,
    }


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Live runner Bybit v5 (testnet-first, ADR-015)")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--strategy", default="simple_momentum")
    ap.add_argument("--params", default='{"short":10,"long":30,"side":"short"}')
    ap.add_argument("--size_usdt", type=float, default=150.0)
    ap.add_argument("--leverage", type=int, default=3)
    ap.add_argument("--tp_pct", type=float, default=0.03)
    ap.add_argument("--sl_pct", type=float, default=0.015)
    ap.add_argument("--data_source", choices=["testnet", "mainnet"], default="testnet")
    ap.add_argument("--tpsl_mode", choices=["server", "local"], default="server")
    ap.add_argument("--poll_limit", type=int, default=200, help="Ile świec pobierać na on_bar.")
    ap.add_argument("--run_id", default=None, help="Identyfikator sesji; domyślnie auto.")
    ap.add_argument(
        "--close_all",
        action="store_true",
        help="Zamiast pętli: wykonaj Close-All (cancel + close) i wyjdź.",
    )
    return ap.parse_args()


def _make_adapter(data_source: str) -> BybitFuturesAdapter:
    """Buduje adapter z kluczy .env (testnet lub mainnet)."""
    import os

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if data_source == "testnet":
        key = os.getenv("BYBIT_API_KEY_TESTNET", "").strip()
        sec = os.getenv("BYBIT_API_SECRET_TESTNET", "").strip()
        if not key or not sec:
            raise RuntimeError(
                "Brak kluczy testnet w .env (BYBIT_API_KEY_TESTNET / BYBIT_API_SECRET_TESTNET)."
            )
        return BybitFuturesAdapter(key, sec, testnet=True)

    # mainnet (Faza 3) — realny kapitał; wymaga jawnych kluczy mainnet.
    key = os.getenv("BYBIT_API_KEY", "").strip()
    sec = os.getenv("BYBIT_API_SECRET", "").strip()
    if not key or not sec:
        raise RuntimeError("Brak kluczy mainnet w .env (BYBIT_API_KEY / BYBIT_API_SECRET).")
    return BybitFuturesAdapter(key, sec, testnet=False)


def main() -> None:
    args = _parse_args()

    run_id = args.run_id or default_run_id(args.strategy, args.symbol, args.timeframe)
    setup_logging(log_dir=Path("results/live") / run_id)

    adapter = _make_adapter(args.data_source)
    adapter.set_position_mode_oneway(args.symbol)
    adapter.set_leverage(args.symbol, args.leverage)

    # Tryb Close-All (safety / ręczne domknięcie) — wykonaj i wyjdź.
    if args.close_all:
        result = close_all_positions(adapter, args.symbol)
        logger.info("Close-All zakończone", extra={"symbol": args.symbol, **result})
        return

    strat_params: dict[str, Any] = json.loads(args.params)
    strat = load_strategy(args.strategy, strat_params)

    journal = Journal(run_id, base_dir="results/live")
    trade_seq = 0
    current: dict[str, Any] | None = None

    logger.info(
        f"== Live Strategy on Bybit v5 ({args.data_source.upper()}) ==",
        extra={
            "run_id": run_id,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "strategy": args.strategy,
            "params": args.params,
            "size_usdt": args.size_usdt,
            "leverage": args.leverage,
            "tpsl_mode": args.tpsl_mode,
        },
    )

    # Startowy sanity: wyczyść wiszące zlecenia.
    with contextlib.suppress(Exception):
        adapter.cancel_all_orders(args.symbol)

    last_ts = int(adapter.fetch_ohlcv(args.symbol, args.timeframe, limit=1)[-1][0])

    while True:
        last_ts = wait_for_next_close(adapter, args.symbol, args.timeframe, last_ts)

        ohlcv = adapter.fetch_ohlcv(args.symbol, args.timeframe, limit=args.poll_limit)
        df = ohlcv_to_df(ohlcv)
        sig = strat.on_bar(df)

        pos = adapter.fetch_positions(args.symbol)
        last_px = adapter.fetch_ticker_last(args.symbol)
        side_txt = "short" if pos < 0 else "long" if pos > 0 else "flat"
        logger.info(
            "Tick: sygnał + pozycja",
            extra={
                "action": sig.action,
                "side": sig.side,
                "position": pos,
                "pos_side": side_txt,
                "last_price": last_px,
            },
        )
        journal.snapshot_equity(args.symbol, args.timeframe, last_price=last_px, position=pos)

        # WATCHDOG lokalnego TP/SL (mark/last price) — gdy tpsl_mode=local.
        if current is not None and args.tpsl_mode == "local":
            entry = float(current["entry_price"])
            c_side = str(current["side"])
            qty = float(current["qty"])
            if c_side == "short":
                tp_hit = last_px <= entry * (1 - args.tp_pct)
                sl_hit = last_px >= entry * (1 + args.sl_pct)
            else:
                tp_hit = last_px >= entry * (1 + args.tp_pct)
                sl_hit = last_px <= entry * (1 - args.sl_pct)
            if tp_hit or sl_hit:
                close_side = "buy" if c_side == "short" else "sell"
                with contextlib.suppress(Exception):
                    order = adapter.create_market(args.symbol, close_side, qty, reduce_only=True)
                    avg_exit = float(order.get("average") or last_px)
                    reason = "tpsl_local_tp" if tp_hit else "tpsl_local_sl"
                    realized = (
                        (entry - avg_exit) * qty if c_side == "short" else (avg_exit - entry) * qty
                    )
                    journal.log_exit(
                        str(current["trade_id"]),
                        args.symbol,
                        args.timeframe,
                        args.strategy,
                        strat_params,
                        c_side,
                        qty,
                        entry,
                        avg_exit,
                        reason,
                        realized,
                    )
                    current = None
                    adapter.cancel_all_orders(args.symbol)
                continue

        # ENTER
        if sig.action == "enter" and abs(pos) == 0.0:
            with contextlib.suppress(Exception):
                adapter.cancel_all_orders(args.symbol)
            price_ref = last_px * 0.995
            lim = adapter.market_limits(args.symbol)
            min_amt = float((lim.get("amount") or {}).get("min") or 0.001)
            qty_raw = max(min_amt, (args.size_usdt / price_ref) * 0.995)
            qty = adapter.amount_to_precision(args.symbol, qty_raw)
            side_str: str = sig.side or "short"
            side_exec = "buy" if side_str == "long" else "sell"
            try:
                order = adapter.create_market(args.symbol, side_exec, qty, reduce_only=False)
            except Exception as e:
                logger.error("OPEN nieudany", extra={"error": str(e), "side": side_exec})
                continue

            avg = float(order.get("average") or last_px)
            trade_seq += 1
            trade_id = f"{run_id}_{trade_seq}"
            current = {"trade_id": trade_id, "side": side_str, "qty": qty, "entry_price": avg}
            journal.log_entry(
                trade_id,
                args.symbol,
                args.timeframe,
                args.strategy,
                strat_params,
                side_str,
                qty,
                avg,
            )
            logger.info(
                "Pozycja otwarta",
                extra={"side": side_exec, "qty": qty, "avg_price": avg, "trade_id": trade_id},
            )

            if args.tpsl_mode == "server":
                adapter.set_tpsl(
                    args.symbol, side_str, entry_price=avg, tp_pct=args.tp_pct, sl_pct=args.sl_pct
                )
                logger.info("TPSL(server) ustawione", extra={"tp": args.tp_pct, "sl": args.sl_pct})

        # EXIT (sygnał)
        elif sig.action == "exit" and abs(pos) != 0.0:
            b_qty, b_side, b_entry = read_position_info(adapter, args.symbol)
            close_side = "buy" if pos < 0 else "sell"
            close_qty = abs(adapter.amount_to_precision(args.symbol, abs(pos)))
            try:
                order = adapter.create_market(args.symbol, close_side, close_qty, reduce_only=True)
            except Exception as e:
                logger.error("CLOSE nieudany", extra={"error": str(e), "side": close_side})
                continue
            avg_exit = float(order.get("average") or last_px)
            entry = float(current["entry_price"]) if current is not None else (b_entry or last_px)
            e_side = str(current["side"]) if current is not None else (b_side or side_txt)
            qty = float(current["qty"]) if current is not None else b_qty
            realized = (entry - avg_exit) * qty if e_side == "short" else (avg_exit - entry) * qty
            trade_id = (
                str(current["trade_id"]) if current is not None else f"{run_id}_{trade_seq}_x"
            )
            journal.log_exit(
                trade_id,
                args.symbol,
                args.timeframe,
                args.strategy,
                strat_params,
                e_side,
                qty,
                entry,
                avg_exit,
                "exit_signal",
                realized,
            )
            current = None
            with contextlib.suppress(Exception):
                adapter.cancel_all_orders(args.symbol)
            logger.info("Pozycja zamknięta (exit signal)", extra={"avg_price": avg_exit})


if __name__ == "__main__":
    import signal
    import sys

    def _graceful_exit(*_: Any) -> None:
        stamp = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] Stopped (signal).", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    try:
        main()
    except KeyboardInterrupt:
        _graceful_exit()
