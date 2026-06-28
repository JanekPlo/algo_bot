"""algo_bot/funding.py — fetcher historycznych funding rates Binance USDT-M perp.

CLI ``algo-fetch-funding`` pobiera funding rate history przez ccxt
(``/fapi/v1/fundingRate``) i zapisuje do
``bot_data/processed/binance_<SYMBOL>_funding.csv`` w schemacie ``datetime``
(UTC) + ``funding_rate``. Konsumowane przez ``data_loader.load_funding`` i
warstwę microstructure (ADR-011).

Funding rate jest publiczny (brak API key). Settlement co 8h (00/08/16 UTC) —
endpoint zwraca rzeczywisty ``fundingTime`` per settlement, więc obsługuje też
ewentualne off-cycle settlementy Binance.

Uruchomienie (w WSL, conda env algo_bot):
    algo-fetch-funding --symbol BTC/USDT --start 2019-09-08
    python -m algo_bot.funding --symbol ETH/USDT --start 2019-11-01

See also:
    docs/adr/011-microstructure-adjustments.md (Decyzja 6/7 — źródło i storage)
    algo_bot/data_loader.py (load_funding — konsument tego pliku)
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import ccxt
import pandas as pd

from algo_bot.data_loader import get_funding_path, symbol_noslash
from algo_bot.log import get_logger, setup_logging

logger = get_logger(__name__)

# Max liczba rekordów per request (limit Binance fapi).
_LIMIT = 1000


def _to_iso(value: str) -> str:
    """Normalizuje ``YYYY-MM-DD`` → ``YYYY-MM-DDT00:00:00Z`` dla ``ccxt.parse8601``."""
    if "T" in value:
        return value if value.endswith("Z") else value + "Z"
    return value + "T00:00:00Z"


def fetch_funding(symbol: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Pobiera funding history dla symbolu w zakresie ``[start, end]``.

    Args:
        symbol: np. ``"BTC/USDT"``.
        start: ISO date/datetime UTC, np. ``"2019-09-08"``.
        end: ISO date/datetime UTC; ``None`` → bieżący moment.

    Returns:
        DataFrame z kolumnami ``datetime`` (UTC) i ``funding_rate``, posortowany,
        bez duplikatów. Pusty DataFrame gdy brak danych.
    """
    ex = ccxt.binance()
    market = symbol_noslash(symbol)  # 'BTC/USDT' → 'BTCUSDT'
    since = ex.parse8601(_to_iso(start))
    end_ms = ex.parse8601(_to_iso(end)) if end else ex.milliseconds()

    rows: list[dict] = []
    while since < end_ms:
        data = ex.fapiPublicGetFundingRate({"symbol": market, "startTime": since, "limit": _LIMIT})
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
        last = int(data[-1]["fundingTime"])
        if last <= since:
            break
        since = last + 1
        time.sleep(ex.rateLimit / 1000)

    if not rows:
        logger.warning("No funding rows fetched", extra={"symbol": symbol, "start": start})
        return pd.DataFrame(columns=["datetime", "funding_rate"])

    df = (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["datetime"])
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    if end:
        df = df[df["datetime"] <= pd.to_datetime(end, utc=True)].reset_index(drop=True)

    logger.info(
        "Funding history fetched",
        extra={
            "symbol": symbol,
            "rows": len(df),
            "first": str(df["datetime"].iloc[0]),
            "last": str(df["datetime"].iloc[-1]),
        },
    )
    return df


def save_funding(df: pd.DataFrame, symbol: str, exchange: str = "binance") -> Path:
    """Zapisuje funding DataFrame do bot_data/processed/binance_<SYMBOL>_funding.csv."""
    path = get_funding_path(symbol, exchange)
    df.to_csv(path, index=False)
    logger.info("Funding saved", extra={"out_path": str(path), "rows": len(df), "symbol": symbol})
    return path


def main() -> None:
    """Entry point dla ``algo-fetch-funding``."""
    setup_logging()
    ap = argparse.ArgumentParser(
        description="algo-fetch-funding — Binance USDT-M funding history (ADR-011)"
    )
    ap.add_argument("--symbol", required=True, help="np. BTC/USDT")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (UTC); brak → teraz")
    args = ap.parse_args()

    df = fetch_funding(args.symbol, args.start, args.end)
    if df.empty:
        raise SystemExit(f"Brak danych funding dla {args.symbol} od {args.start}")
    save_funding(df, args.symbol)


if __name__ == "__main__":
    main()
