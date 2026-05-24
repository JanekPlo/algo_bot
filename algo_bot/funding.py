"""
algo_bot/funding.py — ad-hoc scraper funding rates z Binance Futures.

Legacy script — pobiera funding history dla pojedynczego symbolu w hardcoded
zakresie dat i zapisuje do bot_data/aux/. Przepisanie na pełen module/CLI
jest follow-up'em w Fazie 2 (gdy walk-forward zacznie używać funding cost
w stress tests).

Uruchomienie:
    python -m algo_bot.funding
"""

from __future__ import annotations

import time

import ccxt
import pandas as pd

from algo_bot.log import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> None:
    """Pobiera funding rates dla BTCUSDT od 2024-01-01 do 2025-01-01."""
    setup_logging()

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
    out_path = "bot_data/aux/binance_BTCUSDT_funding.csv"
    df.to_csv(out_path, index=False)
    logger.info("Funding rates saved", extra={"out_path": out_path, "rows": len(df), "symbol": sym})


if __name__ == "__main__":
    main()
