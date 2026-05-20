import time

import ccxt
import pandas as pd

ex = ccxt.binance()
sym = "BTCUSDT"
since = ex.parse8601("2024-01-01T00:00:00Z")
rows = []
while True:
    # surowe: ostatnie fundingi; limit max 1000
    data = ex.fapiPublicGetFundingRate({"symbol": sym, "startTime": since, "limit": 1000})
    if not data:
        break
    for d in data:
        ts = int(d["fundingTime"])
        rows.append(
            {
                "datetime": pd.to_datetime(ts, unit="ms", utc=True),
                "funding_rate": float(d["fundingRate"]),
            }
        )
    since = int(data[-1]["fundingTime"]) + 1
    # stop, gdy dolecimy do końca 2024:
    if since >= ex.parse8601("2025-01-01T00:00:00Z"):
        break
    time.sleep(ex.rateLimit / 1000)

df = pd.DataFrame(rows).drop_duplicates(subset=["datetime"]).sort_values("datetime")
df.to_csv("bot_data/aux/binance_BTCUSDT_funding.csv", index=False)
print("Zapisano:", "bot_data/aux/binance_BTCUSDT_funding.csv")
