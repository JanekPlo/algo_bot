"""algo_bot/engine/exchanges/bybit_adapter.py

CCXT wrapper dla Bybit v5 (linear USDT perpetuals). Analog ``binance_adapter``,
ale pod Bybit v5 API (ADR-015). Wstrzykuje API keys, ustawia sandbox mode
(testnet/mainnet), ładuje markets, wymusza One-Way position mode (Decyzja 6).

Różnice Bybit vs Binance istotne dla tego wrappera:
- Symbol linear perp w CCXT ma settle-suffix: ``BTC/USDT:USDT`` (nie ``BTC/USDT``).
  Wrapper przyjmuje unified ``BTC/USDT`` i normalizuje wewnętrznie.
- Position mode: system default = One-Way (``positionIdx=0``); ``reduce_only``
  działa (brak binance'owego ograniczenia Hedge Mode). Weryfikacja: Bybit v5
  ``/v5/position/switch-mode`` + ``/v5/account/fee-rate``.
- TP/SL zakładamy jako trading-stop na całej pozycji (Bybit v5
  ``/v5/position/trading-stop``) — odpowiednik binance'owego
  ``closePosition`` reduce-only stop/tp.

Public API:
- BybitFuturesAdapter(api_key, api_secret, testnet=True)
    Atrybuty:
    - self.exchange: ccxt.bybit — natywny klient CCXT
    Metody: patrz sekcje Public / Private / Orders niżej.

See also:
- docs/adr/015-exchange-migration-bybit.md (migracja Binance→Bybit)
- docs/adr/014-engine-migration-nautilus.md §9 (position model — Bybit note)
- docs/adr/004-hybrid-tp-sl-mode.md (tryby TP/SL w live)
- Bybit v5 API: https://bybit-exchange.github.io/docs/v5/intro
- ccxt docs: https://docs.ccxt.com/
"""

from __future__ import annotations

import contextlib
from typing import Any, cast

import ccxt

from algo_bot.log import get_logger

logger = get_logger(__name__)


def to_market_symbol(symbol: str) -> str:
    """'BTC/USDT' → 'BTC/USDT:USDT' (linear USDT perp w notacji CCXT).

    Idempotentne: symbol z suffixem zwracany bez zmian.
    """
    if ":" in symbol:
        return symbol
    base, quote = symbol.split("/") if "/" in symbol else (symbol[:-4], symbol[-4:])
    return f"{base}/{quote}:{quote}"


class BybitFuturesAdapter:
    """CCXT wrapper Bybit v5 linear USDT perpetuals (One-Way mode)."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True) -> None:
        self.exchange = ccxt.bybit(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},  # linear USDT perp
            }
        )
        self.exchange.set_sandbox_mode(testnet)
        self.exchange.load_markets()

    # --- Symbol helper ---
    def sym(self, symbol: str) -> str:
        """Unified symbol → market-specific (linear settle-suffix)."""
        return to_market_symbol(symbol)

    # --- Public (market data) ---
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> list[list[float]]:
        result = self.exchange.fetch_ohlcv(self.sym(symbol), timeframe=timeframe, limit=limit)
        return cast("list[list[float]]", result)

    def fetch_ticker_last(self, symbol: str) -> float:
        return float(self.exchange.fetch_ticker(self.sym(symbol))["last"])

    # --- Private (account / position config) ---
    def set_position_mode_oneway(self, symbol: str) -> None:
        """Ustawia One-Way (Merged Single) mode dla symbolu. Best-effort."""
        with contextlib.suppress(Exception):
            self.exchange.set_position_mode(hedged=False, symbol=self.sym(symbol))

    def set_leverage(self, symbol: str, lev: int) -> None:
        with contextlib.suppress(Exception):
            self.exchange.set_leverage(lev, self.sym(symbol))

    def fetch_positions(self, symbol: str) -> float:
        """Zwraca signed amount (long>0, short<0) w jednostkach bazowych.

        One-Way: pojedyncza pozycja per symbol; znak z pola ``side``.
        """
        market_symbol = self.sym(symbol)
        m = self.exchange.market(market_symbol)
        m_id = m["id"]
        total = 0.0
        for p in self.exchange.fetch_positions([market_symbol]):
            info = p.get("info", {}) or {}
            sym_ok = (p.get("symbol") in (market_symbol, m_id)) or (
                info.get("symbol") in (market_symbol, m_id)
            )
            if not sym_ok:
                continue
            contracts = p.get("contracts")
            amt = float(contracts) if contracts is not None else float(info.get("size") or 0.0)
            side = (p.get("side") or info.get("side") or "").lower()
            # Bybit zwraca dodatni size + osobne pole 'side'; nadajemy znak.
            if side in ("short", "sell"):
                amt = -abs(amt)
            elif side in ("long", "buy"):
                amt = abs(amt)
            total += amt
        return total

    # --- Orders ---
    def create_market(
        self, symbol: str, side: str, qty: float, reduce_only: bool = False
    ) -> dict[str, Any]:
        side = side.lower()
        params: dict[str, Any] = {"reduceOnly": reduce_only}
        if side == "buy":
            return cast(
                "dict[str, Any]",
                self.exchange.create_market_buy_order(self.sym(symbol), qty, params=params),
            )
        if side == "sell":
            return cast(
                "dict[str, Any]",
                self.exchange.create_market_sell_order(self.sym(symbol), qty, params=params),
            )
        raise ValueError("side must be buy/sell")

    def create_limit(
        self, symbol: str, side: str, qty: float, price: float, reduce_only: bool = False
    ) -> dict[str, Any]:
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy/sell")
        params: dict[str, Any] = {"reduceOnly": reduce_only}
        return cast(
            "dict[str, Any]",
            self.exchange.create_order(self.sym(symbol), "limit", side, qty, price, params=params),
        )

    def cancel_all_orders(self, symbol: str) -> Any:
        """Anuluje wszystkie open orders dla symbolu (Bybit v5 cancel-all)."""
        return self.exchange.cancel_all_orders(self.sym(symbol))

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.exchange.amount_to_precision(self.sym(symbol), amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(self.sym(symbol), price))

    def market_limits(self, symbol: str) -> dict[str, Any]:
        return cast("dict[str, Any]", self.exchange.market(self.sym(symbol)).get("limits", {}))

    # --- TP/SL ---
    def set_tpsl(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        tp_pct: float | None = None,
        sl_pct: float | None = None,
        trigger_by: str = "MarkPrice",
    ) -> dict[str, Any]:
        """Zakłada TP/SL na całej pozycji (Bybit v5 ``/v5/position/trading-stop``).

        Odpowiednik binance'owego ``closePosition`` reduce-only stop/tp: Bybit
        trzyma TP/SL na poziomie pozycji (``tpslMode=Full``, ``positionIdx=0`` w
        One-Way). ``tp_pct``/``sl_pct`` == None → pomija dany stop.

        Args:
            side: 'long' albo 'short' — kierunek pozycji (nie strony zlecenia).
            trigger_by: 'MarkPrice' (zalecane) albo 'LastPrice'.
        """
        market = self.exchange.market(self.sym(symbol))
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": market["id"],
            "tpslMode": "Full",
            "positionIdx": 0,  # One-Way
        }
        if tp_pct is not None:
            if side == "short":
                tp_price = entry_price * (1 - float(tp_pct))
            else:
                tp_price = entry_price * (1 + float(tp_pct))
            params["takeProfit"] = self.price_to_precision(symbol, tp_price)
            params["tpTriggerBy"] = trigger_by
        if sl_pct is not None:
            if side == "short":
                sl_price = entry_price * (1 + float(sl_pct))
            else:
                sl_price = entry_price * (1 - float(sl_pct))
            params["stopLoss"] = self.price_to_precision(symbol, sl_price)
            params["slTriggerBy"] = trigger_by

        try:
            result = self.exchange.private_post_v5_position_trading_stop(params)
            return cast("dict[str, Any]", result)
        except Exception:
            logger.warning(
                "Failed to set Bybit trading-stop TP/SL",
                extra={"symbol": symbol, "side": side, "tp_pct": tp_pct, "sl_pct": sl_pct},
                exc_info=True,
            )
            return {}
