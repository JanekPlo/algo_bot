import contextlib
import os

import ccxt

ex = ccxt.bybit(
    {
        "apiKey": os.getenv("BYBIT_API_KEY_TESTNET"),
        "secret": os.getenv("BYBIT_API_SECRET_TESTNET"),
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},  # USDT perpetual
    }
)
ex.set_sandbox_mode(True)  # TESTNET

markets = ex.load_markets()
print("Symbols:", len(markets))
symbol = "BTC/USDT:USDT"  # ważny format na Bybit
print("Ticker:", ex.fetch_ticker(symbol)["last"])

# (opcjonalnie) dzwignia i tryb pozycji:
with contextlib.suppress(Exception):
    ex.set_position_mode(hedged=False)  # One-Way
with contextlib.suppress(Exception):
    ex.set_leverage(3, symbol)

# test minimalnej ilości
min_amt = ex.market(symbol)["limits"]["amount"]["min"]
print("Min amount:", min_amt)

# test małego market ordera (zmień ilość pod min_amt jeśli trzeba)
# ex.create_order(symbol, 'market', 'sell', max(min_amt, 0.001))
