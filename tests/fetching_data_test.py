import ccxt

exchange = ccxt.binance()
try:
    exchange.load_markets()
    print("Binance API is reachable and markets are loaded.")
except Exception as e:
    print(f"Error accessing Binance API: {e}")
