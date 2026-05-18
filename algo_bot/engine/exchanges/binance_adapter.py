"""
algo_bot/engine/exchanges/binance_adapter.py

CCXT wrapper dla Binance Futures (USDT-M perpetuals). Wstrzykuje API keys,
ustawia sandbox mode (testnet/mainnet), ładuje markets. Używany przez live runner
i fetch_data.

Public API:
- BinanceFuturesAdapter(api_key, api_secret, testnet=True)
    Klasa wrapper. Atrybuty:
    - self.exchange: ccxt.binance — natywny CCXT klient
    Metody: typowo używane przez `self.exchange.<method>` (CCXT API).

CCXT methods often used:
- fetch_ohlcv(symbol, timeframe, since, limit)
- create_market_order(symbol, side, amount, params={})
- create_order(symbol, type, side, amount, price, params={'stopPrice': ..., 'reduceOnly': True})
- cancel_order(order_id, symbol)
- fetch_position(symbol)

See also:
- docs/adr/004-hybrid-tp-sl-mode.md (jak używamy w live)
- ccxt docs: https://docs.ccxt.com/
- Binance Futures API: https://binance-docs.github.io/apidocs/futures/en/
"""
import os, ccxt
from typing import Any, Dict, List, Optional

class BinanceFuturesAdapter:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self.exchange.set_sandbox_mode(testnet)
        self.exchange.load_markets()

    # --- Public ---
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200):
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_ticker_last(self, symbol: str) -> float:
        return self.exchange.fetch_ticker(symbol)["last"]

    # --- Private ---
    def set_position_mode_oneway(self):
        try:
            self.exchange.set_position_mode(hedged=False)
        except Exception:
            pass

    def set_leverage(self, symbol: str, lev: int):
        try:
            self.exchange.set_leverage(lev, symbol)
        except Exception:
            pass

    def fetch_positions(self, symbol: str) -> float:
        """
        Zwraca signed amount (long>0, short<0) w jednostkach bazowych.
        """
        m = self.exchange.market(symbol)
        m_id = m["id"]
        total = 0.0
        for p in self.exchange.fetch_positions([symbol]):
            sym_ok = (p.get("symbol") in (symbol, m_id)) or (p.get("info", {}).get("symbol") in (symbol, m_id))
            if not sym_ok:
                continue
            raw_amt = p.get("info", {}).get("positionAmt")
            amt = float(raw_amt) if raw_amt is not None else float(p.get("contracts") or 0.0)
            # na Binance positionAmt ma znak (short<0)
            total += amt
        return total

    def create_market(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> Dict[str, Any]:
        side = side.lower()
        t = {"reduceOnly": reduce_only, "workingType": "MARK_PRICE", "newOrderRespType": "RESULT"}
        if side == "buy":
            return self.exchange.create_market_buy_order(symbol, qty, params=t)
        elif side == "sell":
            return self.exchange.create_market_sell_order(symbol, qty, params=t)
        raise ValueError("side must be buy/sell")

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.exchange.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(symbol, price))

    def market_limits(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.market(symbol).get("limits", {})

    def set_tpsl(self, symbol, side, entry_price, tp_pct=None, sl_pct=None, price_type='MARK_PRICE'):
        """
        Zakłada TP/SL jako closePosition reduceOnly. Jeśli tp_pct/sl_pct == None → pomija dany stop.
        price_type: 'MARK_PRICE' (zalecane na testnecie) albo 'CONTRACT_PRICE'
        """
        working_type = 'MARK_PRICE' if (price_type or '').upper() == 'MARK_PRICE' else 'CONTRACT_PRICE'
        params_base = {
        'closePosition': True,   # nie podajemy amount, zamyka całość
        'workingType': working_type,
        'priceProtect': True,
        }

        results = []

        # TAKE-PROFIT
        if tp_pct is not None:
            if side == 'short':
                tp_price = entry_price * (1 - float(tp_pct))
                tp_side  = 'buy'
            else:
                tp_price = entry_price * (1 + float(tp_pct))
                tp_side  = 'sell'
            try:
                results.append(self.exchange.create_order(
                    symbol, 'TAKE_PROFIT_MARKET', tp_side, None,
                    params={**params_base, 'stopPrice': self.price_to_precision(symbol, tp_price)}))
            except Exception as e:
                print(f"[set_tpsl] WARN TP: {e}", flush=True)

        # STOP-LOSS
        if sl_pct is not None:
            if side == 'short':
                sl_price = entry_price * (1 + float(sl_pct))
                sl_side  = 'buy'
            else:
                sl_price = entry_price * (1 - float(sl_pct))
                sl_side  = 'sell'
            try:
                results.append(self.exchange.create_order(
                    symbol, 'STOP_MARKET', sl_side, None,
                    params={**params_base, 'stopPrice': self.price_to_precision(symbol, sl_price)}
            ))
            except Exception as e:
                print(f"[set_tpsl] WARN SL: {e}", flush=True)

        return results

