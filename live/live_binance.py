#!/usr/bin/env python3
"""
live/live_binance.py

Live trading runner dla Binance Futures (USDT-M perpetuals). Wczytuje strategię
przez load_strategy(), pętla: czekaj na zamknięcie świecy → on_bar(df) → executuj
Signal na Binance przez BinanceFuturesAdapter (CCXT).

Wsparcie dla 3 trybów TP/SL (--tpsl_mode, patrz ADR-004):
- 'server' — TP+SL na serwerze giełdy (proste, niezawodne, ale knoty)
- 'local'  — TP+SL lokalnie z mark price (omija knoty, ale brak fallback gdy bot padnie)
- 'hybrid' — TP server-side, SL lokalnie (kompromis, default)

Recovery:
- Bot crashuje → restart → wczytuje state z journala → kontynuuje
- Idempotency: client_order_id deterministycznie z run_id + bar_ts
- Reconciliation: porównanie equity z giełdy vs equity z journala (faza 4-5)

CLI:
- python live/live_binance.py --symbol BTC/USDT --timeframe 5m --strategy simple_momentum ...
- (przyszłość) algo-live --symbol ... (po przeniesieniu do algo_bot.live)

Required env vars (przez python-dotenv z .env):
- BINANCE_API_KEY_TESTNET / BINANCE_API_SECRET_TESTNET (dla --data_source=testnet)
- BINANCE_API_KEY / BINANCE_API_SECRET (dla --data_source=mainnet)

See also:
- docs/adr/004-hybrid-tp-sl-mode.md (rationale trybów TP/SL)
- docs/adr/003-strategybase-signal-api.md (Signal API którego ten loop konsumuje)
- docs/guides/live-trading-checklist.md (TBD — pre-flight checklist)
- algo_bot/telemetry/journal.py (CSV journal który tu używamy)
"""
import os, time, argparse, json
from pathlib import Path
from dotenv import load_dotenv

from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    from backports.zoneinfo import ZoneInfo  # pip install backports.zoneinfo tzdata

from algo_bot.strategy_loader import load_strategy
from algo_bot.engine.exchanges.binance_adapter import BinanceFuturesAdapter
from algo_bot.telemetry.journal import Journal


def ts() -> str:
    return datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")


def wait_for_next_close(exchange: BinanceFuturesAdapter, symbol: str, timeframe: str, last_ts: int) -> int:
    tf_sec = exchange.exchange.parse_timeframe(timeframe)
    next_ts = last_ts + tf_sec * 1000
    while True:
        now_ms = exchange.exchange.milliseconds()
        sleep_ms = max(0, next_ts - now_ms + 500)
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)
        try:
            ts_last = exchange.fetch_ohlcv(symbol, timeframe, limit=1)[-1][0]
            if ts_last != last_ts:
                return ts_last
        except Exception as e:
            print(f"[{ts()}] WARN kline (retry): {e}", flush=True)
            time.sleep(1)


def default_run_id(strategy: str, symbol: str, timeframe: str) -> str:
    stamp = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{strategy}_{symbol.replace('/','')}_{timeframe}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='BTC/USDT')
    ap.add_argument('--timeframe', default='5m')
    ap.add_argument('--strategy', default='simple_momentum')
    ap.add_argument('--params', default='{"short":3,"long":6,"side":"short"}')
    ap.add_argument('--size_usdt', type=float, default=150.0)
    ap.add_argument('--leverage', type=int, default=3)
    ap.add_argument('--tp_pct', type=float, default=0.03)
    ap.add_argument('--sl_pct', type=float, default=0.015)
    ap.add_argument('--data_source', choices=['testnet','mainnet'], default='testnet')
    ap.add_argument('--run_id', default=None, help='Identyfikator sesji; domyślnie auto.')

    # nowe przełączniki pod testnetowe "knoty"
    ap.add_argument('--tpsl_mode', choices=['server','local','hybrid'], default='local',
                    help="server=TP/SL na giełdzie; local=TP/SL lokalnie; hybrid=TP serwerowo, SL lokalnie")
    ap.add_argument('--price_feed', choices=['mainnet_mark','mainnet_last','testnet_mark','testnet_last'],
                    default='mainnet_mark', help="Źródło ceny do lokalnych TP/SL")
    ap.add_argument('--poll_ms', type=int, default=1000, help='Jak często sprawdzać lokalne TP/SL (ms)')
    ap.add_argument('--cat_sl_pct', type=float, default=0.15, help='Awaryjny szeroki SL serwerowy przy tpsl_mode=local')

    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    key = os.getenv("BINANCE_FUTURES_API_KEY_TESTNET", "").strip()
    sec = os.getenv("BINANCE_FUTURES_API_SECRET_TESTNET", "").strip()
    if not key or not sec:
        raise RuntimeError("Brak kluczy testnet w .env (BINANCE_FUTURES_API_KEY_TESTNET / SECRET).")

    ex_trade = BinanceFuturesAdapter(key, sec, testnet=True)
    ex_trade.set_position_mode_oneway()
    ex_trade.set_leverage(args.symbol, args.leverage)

    # źródło danych do sygnałów
    if args.data_source == "testnet":
        ex_data = ex_trade
    else:
        ex_data = BinanceFuturesAdapter("", "", testnet=False)
        ex_data.exchange.apiKey = None
        ex_data.exchange.secret = None

    # osobny klient do feedu cen (żeby 'mainnet_mark' był z mainnetu)
    if args.price_feed.startswith('mainnet_'):
        price_client = BinanceFuturesAdapter("", "", testnet=False)  # public mainnet
        try:
            price_client.exchange.load_markets()
        except Exception:
            pass
    elif args.price_feed.startswith('testnet_'):
        price_client = ex_trade
    else:
        price_client = ex_data

    # strategia
    strat_params = json.loads(args.params)
    strat = load_strategy(args.strategy, strat_params)

    # run_id + dzienniczek
    run_id = args.run_id or default_run_id(args.strategy, args.symbol, args.timeframe)
    journal = Journal(run_id, base_dir="results/live")
    trade_seq = 0
    current = None  # aktualny trade: dict z entry

    print("== Live Strategy on Binance Futures TESTNET ==", flush=True)
    print(f"run_id={run_id} Symbol={args.symbol} TF={args.timeframe} "
          f"strat={args.strategy} params={args.params} size={args.size_usdt} lev={args.leverage}", flush=True)

    # bootstrap
    last_ts = ex_data.fetch_ohlcv(args.symbol, args.timeframe, limit=1)[-1][0]

    # --- helpers ---
    def _read_position_info():
        """Zwraca (qty_abs, side, entry_price) lub (0, None, 0)."""
        try:
            raw = ex_trade.exchange.fetch_positions([args.symbol])
            m = ex_trade.exchange.market(args.symbol)
            mid = m["id"]
            for p in raw:
                sym = p.get("symbol") or p.get("info", {}).get("symbol")
                if sym not in (args.symbol, mid):
                    continue
                amt = p.get("contracts")
                if amt is None:
                    amt = p.get("info", {}).get("positionAmt") or 0
                amt = float(amt)
                side = "long" if amt > 0 else ("short" if amt < 0 else None)
                ep = (
                    p.get("entryPrice")
                    or p.get("info", {}).get("entryPrice")
                    or p.get("info", {}).get("avgEntryPrice")
                    or p.get("info", {}).get("avgPrice")
                    or 0
                )
                return abs(amt), side, float(ep or 0)
        except Exception:
            pass
        return 0.0, None, 0.0

    def _get_price(feed: str):
        """Pobiera cenę wg feedu; ma fallbacki żeby zawsze coś zwrócić."""
        def _mark(client):
            try:
                mid = client.exchange.market(args.symbol)['id']
                d = client.exchange.fapiPublicGetPremiumIndex({'symbol': mid})
                return float(d['markPrice'])
            except Exception:
                return client.fetch_ticker_last(args.symbol)
        try:
            if feed.endswith('_mark'):
                return _mark(price_client)
            if feed.endswith('_last'):
                return price_client.fetch_ticker_last(args.symbol)
        except Exception:
            pass
        # ostateczny fallback
        try:
            return ex_data.fetch_ticker_last(args.symbol)
        except Exception:
            return ex_trade.fetch_ticker_last(args.symbol)

    # wyczyść stare zlecenia (TP/SL) na starcie
    try:
        ex_trade.exchange.cancel_all_orders(args.symbol)
    except Exception as e:
        print(f"[{ts()}] WARN cancel_all_orders (startup): {e}", flush=True)

    # resume: jeśli jest pozycja, odśwież TPSL wg trybu i zapisz ENTRY do journalu
    pos0 = ex_trade.fetch_positions(args.symbol)
    px0 = _get_price(args.price_feed)
    if abs(pos0) > 0:
        qty_abs, side0, entry0 = _read_position_info()
        if qty_abs > 0 and side0 in ('long', 'short'):
            entry_price = entry0 or px0
            trade_seq += 1
            trade_id = f"{run_id}_{trade_seq}_resume"
            current = {"trade_id": trade_id, "side": side0, "qty": qty_abs, "entry_price": entry_price}
            journal.log_entry(trade_id, args.symbol, args.timeframe,
                              args.strategy, strat_params, side0, qty_abs, entry_price)
            try:
                if args.tpsl_mode == 'server':
                    ex_trade.set_tpsl(args.symbol, side0, entry_price=entry_price,
                                      tp_pct=args.tp_pct, sl_pct=args.sl_pct)
                    msg = "TPSL(server)"
                elif args.tpsl_mode == 'hybrid':
                    ex_trade.set_tpsl(args.symbol, side0, entry_price=entry_price,
                                      tp_pct=args.tp_pct, sl_pct=None)
                    msg = "TP(server), SL(local)"
                else:
                    ex_trade.set_tpsl(args.symbol, side0, entry_price=entry_price,
                                      tp_pct=None, sl_pct=args.cat_sl_pct)
                    msg = f"Catastrophic SL(server) {args.cat_sl_pct*100:.1f}%"
                print(f"[{ts()}] Resume: position={pos0:.6f} ({side0}) — {msg} & journaled", flush=True)
            except Exception as e:
                print(f"[{ts()}] WARN TPSL (resume): {e}", flush=True)
        else:
            print(f"[{ts()}] Resume: brak realnej pozycji (pomijam journal resume)", flush=True)

    # pierwszy snapshot
    try:
        bal = ex_trade.exchange.fetch_balance()
        equity = (bal.get("info", {}) or {}).get("totalMarginBalance") or bal.get("total", {}).get("USDT")
        wallet = (bal.get("info", {}) or {}).get("totalWalletBalance")
        unreal = (bal.get("info", {}) or {}).get("totalUnrealizedProfit")
    except Exception:
        equity = wallet = unreal = None
    journal.snapshot_equity(args.symbol, args.timeframe, last_price=px0, position=pos0,
                            equity_usdt=equity, wallet_usdt=wallet, unrealized_usdt=unreal)

    prev_pos = pos0  # do watchdogów

    while True:
        ts_new = wait_for_next_close(ex_data, args.symbol, args.timeframe, last_ts)
        last_ts = ts_new

        # dane i decyzja
        ohlcv = ex_data.fetch_ohlcv(args.symbol, args.timeframe, limit=max(200, 50))
        sig = strat.on_bar(ohlcv)

        pos = ex_trade.fetch_positions(args.symbol)
        side_txt = "(short)" if pos < 0 else "(long)" if pos > 0 else "(flat)"
        print(f"[{ts()}] signal={sig}, position={pos:.6f} {side_txt}", flush=True)

        # snapshot co świecę
        try:
            last_px = _get_price(args.price_feed)
            bal = ex_trade.exchange.fetch_balance()
            equity = (bal.get("info", {}) or {}).get("totalMarginBalance") or bal.get("total", {}).get("USDT")
            wallet = (bal.get("info", {}) or {}).get("totalWalletBalance")
            unreal = (bal.get("info", {}) or {}).get("totalUnrealizedProfit")
        except Exception:
            last_px = None; equity = wallet = unreal = None
        journal.snapshot_equity(args.symbol, args.timeframe, last_price=last_px, position=pos,
                                equity_usdt=equity, wallet_usdt=wallet, unrealized_usdt=unreal)

        # WATCHDOG 1: pozycja zniknęła (TP/SL serwerowe lub manualnie) -> dopisz EXIT
        if current is not None and abs(prev_pos) > 0 and abs(pos) == 0 and args.tpsl_mode in ('server','hybrid'):
            px = last_px or _get_price(args.price_feed)
            e  = current["entry_price"]; side = current["side"]; qty = current["qty"]
            realized = (e - px) * qty if side == "short" else (px - e) * qty
            journal.log_exit(current["trade_id"], args.symbol, args.timeframe,
                             args.strategy, strat_params, side, qty, e, px, "tpsl_server_filled", realized)
            current = None
            try:
                ex_trade.exchange.cancel_all_orders(args.symbol)
                print(f"[{ts()}] cancel_all_orders done (tpsl_server_filled)", flush=True)
            except Exception as e:
                print(f"[{ts()}] WARN cancel_all_orders (server_filled): {e}", flush=True)
            prev_pos = pos
            continue  # uniknij podwójnej logiki w tej iteracji

        # WATCHDOG 2: lokalne TP/SL (mark price)
        if current is not None and args.tpsl_mode in ('local','hybrid'):
            px = last_px or _get_price(args.price_feed)
            e  = current['entry_price']; side = current['side']; qty = current['qty']
            tp_hit = sl_hit = False
            if side == 'short':
                if args.tpsl_mode == 'local':
                    tp_hit = px <= e * (1 - args.tp_pct)
                sl_hit = px >= e * (1 + args.sl_pct)
            else:
                if args.tpsl_mode == 'local':
                    tp_hit = px >= e * (1 + args.tp_pct)
                sl_hit = px <= e * (1 - args.sl_pct)

            if tp_hit or sl_hit:
                side_close = 'buy' if side == 'short' else 'sell'
                try:
                    order = ex_trade.create_market(args.symbol, side_close, qty, reduce_only=True)
                except Exception as ee:
                    print(f"[{ts()}] ERR CLOSE(local): {ee}", flush=True)
                else:
                    avg_exit = float(order.get('average') or order.get('info', {}).get('avgPrice') or px or 0.0)
                    reason   = 'tpsl_local_tp' if tp_hit else 'tpsl_local_sl'
                    realized = (e - avg_exit) * qty if side == 'short' else (avg_exit - e) * qty
                    journal.log_exit(current['trade_id'], args.symbol, args.timeframe,
                                     args.strategy, strat_params, side, qty, e, avg_exit, reason, realized)
                    current = None
                    try:
                        ex_trade.exchange.cancel_all_orders(args.symbol)
                        print(f"[{ts()}] cancel_all_orders done (local {reason})", flush=True)
                    except Exception as ce:
                        print(f"[{ts()}] WARN cancel_all_orders (local): {ce}", flush=True)
                prev_pos = pos
                continue

        # ENTER
        if sig == 'enter' and abs(pos) == 0:
            # safety: przed wejściem wyczyść ewentualne wiszące zlecenia
            try:
                ex_trade.exchange.cancel_all_orders(args.symbol)
            except Exception as e:
                print(f"[{ts()}] WARN cancel_all_orders (pre-entry): {e}", flush=True)

            px = last_px or _get_price(args.price_feed)
            price_ref = px * 0.995
            lim = ex_trade.market_limits(args.symbol)
            min_amt = float((lim.get("amount") or {}).get("min") or 0.001)
            qty_raw = max(min_amt, (args.size_usdt / price_ref) * 0.995)
            qty = ex_trade.amount_to_precision(args.symbol, qty_raw)

            side_exec = "sell" if getattr(strat, "side", "short") == "short" else "buy"
            try:
                order = ex_trade.create_market(args.symbol, side_exec, qty, reduce_only=False)
            except Exception as e:
                print(f"[{ts()}] ERR OPEN: {e}", flush=True)
                prev_pos = pos
                continue

            oid = (order.get("id") or order.get("info", {}).get("orderId"))
            avg = float(order.get("average") or order.get("info", {}).get("avgPrice") or px or 0.0)
            print(f"[{ts()}] OPENED {side_exec.upper()} qty={qty} {args.symbol} id={oid} avg={avg}", flush=True)

            pos_now = ex_trade.fetch_positions(args.symbol)
            print(f"[{ts()}] position_now={pos_now:.6f} "
                  f"{'(short)' if pos_now<0 else '(long)' if pos_now>0 else '(flat)'}", flush=True)

            # ENTRY -> journal
            trade_seq += 1
            trade_id = f"{run_id}_{trade_seq}"
            current = {"trade_id": trade_id, "side": getattr(strat, "side", "short"),
                       "qty": qty, "entry_price": avg}
            journal.log_entry(trade_id, args.symbol, args.timeframe,
                              args.strategy, strat_params, current["side"], qty, avg)

            # TP/SL wg trybu
            try:
                if args.tpsl_mode == 'server':
                    ex_trade.set_tpsl(args.symbol, current["side"], entry_price=avg,
                                      tp_pct=args.tp_pct, sl_pct=args.sl_pct)
                    print(f"[{ts()}] TPSL(server) tp={args.tp_pct*100:.1f}% sl={args.sl_pct*100:.1f}%", flush=True)
                elif args.tpsl_mode == 'hybrid':
                    ex_trade.set_tpsl(args.symbol, current["side"], entry_price=avg,
                                      tp_pct=args.tp_pct, sl_pct=None)
                    print(f"[{ts()}] TP(server) set; SL(local)", flush=True)
                else:
                    ex_trade.set_tpsl(args.symbol, current["side"], entry_price=avg,
                                      tp_pct=None, sl_pct=args.cat_sl_pct)
                    print(f"[{ts()}] Catastrophic SL(server) {args.cat_sl_pct*100:.1f}%", flush=True)
            except Exception as e:
                print(f"[{ts()}] WARN TPSL: {e}", flush=True)

        # EXIT (sygnał)
        elif sig == 'exit' and abs(pos) > 0:
            # zapasowe info o pozycji (gdyby current był pusty)
            b_qty, b_side, b_entry = _read_position_info()

            side_close = "buy" if pos < 0 else "sell"
            close_qty = abs(ex_trade.amount_to_precision(args.symbol, pos))
            try:
                order = ex_trade.create_market(args.symbol, side_close, close_qty, reduce_only=True)
            except Exception as e:
                print(f"[{ts()}] ERR CLOSE: {e}", flush=True)
                prev_pos = pos
                continue
            oid = (order.get("id") or order.get("info", {}).get("orderId"))
            avg_exit = float(order.get("average") or order.get("info", {}).get("avgPrice") or last_px or 0.0)
            print(f"[{ts()}] CLOSED {side_close.upper()} qty={close_qty} {args.symbol} id={oid} (reduceOnly) avg={avg_exit}", flush=True)

            if current is None:
                if b_qty > 0 and b_side in ('long', 'short'):
                    trade_seq += 1
                    trade_id = f"{run_id}_{trade_seq}_resume"
                    current = {"trade_id": trade_id, "side": b_side, "qty": b_qty, "entry_price": b_entry or (last_px or 0.0)}
                    journal.log_entry(trade_id, args.symbol, args.timeframe,
                                      args.strategy, strat_params, current["side"], current["qty"], current["entry_price"])
                else:
                    print(f"[{ts()}] EXIT: brak current i brak realnej pozycji — pomijam journal", flush=True)

            if current is not None:
                entry = current["entry_price"]; side = current["side"]; qty = current["qty"]
                realized = (entry - avg_exit) * qty if side == "short" else (avg_exit - entry) * qty
                journal.log_exit(current["trade_id"], args.symbol, args.timeframe,
                                 args.strategy, strat_params, side, qty, entry,
                                 avg_exit, "exit_signal", realized)
                current = None

            try:
                ex_trade.exchange.cancel_all_orders(args.symbol)
                print(f"[{ts()}] cancel_all_orders done (post-exit)", flush=True)
            except Exception as e:
                print(f"[{ts()}] WARN cancel_all_orders (post-exit): {e}", flush=True)

        prev_pos = pos  # na koniec iteracji


if __name__ == "__main__":
    import sys, signal

    def _graceful_exit(*_):
        print(f"[{ts()}] Stopped (signal).", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)   # Ctrl+C
    signal.signal(signal.SIGTERM, _graceful_exit)  # systemd stop

    try:
        main()
    except KeyboardInterrupt:
        _graceful_exit()
