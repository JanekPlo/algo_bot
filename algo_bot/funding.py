"""algo_bot/funding.py — fetcher historycznych funding rates perp USDT (Binance/Bybit).

CLI ``algo-fetch-funding`` pobiera funding rate history przez ccxt i zapisuje do
``bot_data/processed/<exchange>_<SYMBOL>_funding.csv`` w schemacie ``datetime``
(UTC) + ``funding_rate``. Konsumowane przez ``data_loader.load_funding`` i
warstwę microstructure (ADR-011).

Per-giełda źródło (ADR-015):
    * Binance — implicit endpoint ``/fapi/v1/fundingRate`` (``fapiPublicGetFundingRate``),
      zwraca ``fundingTime`` + ``fundingRate``.
    * Bybit — unified ``fetch_funding_rate_history`` (v5 ``/v5/market/funding/history``,
      linear USDT perp, symbol ``BTC/USDT:USDT``), zwraca ``timestamp`` + ``fundingRate``.

Funding rate jest publiczny (brak API key). Settlement co 8h (00/08/16 UTC) —
endpointy zwracają rzeczywisty timestamp per settlement, więc obsługują też
ewentualne off-cycle settlementy.

Uruchomienie (w WSL, z zablokowanego środowiska ``uv``):
    uv run --locked algo-fetch-funding --symbol BTC/USDT --start 2019-09-08
    uv run --locked algo-fetch-funding --exchange bybit --symbol BTC/USDT --start 2020-03-25

See also:
    docs/adr/011-microstructure-adjustments.md (Decyzja 6/7 — źródło i storage)
    docs/adr/015-exchange-migration-bybit.md (migracja Binance→Bybit)
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

# Max liczba rekordów per request. Binance fapi = 1000; Bybit v5 funding = 200.
_LIMIT_BINANCE = 1000
_LIMIT_BYBIT = 200

EXCHANGE_CHOICES = ["binance", "bybit"]


def _to_iso(value: str) -> str:
    """Normalizuje ``YYYY-MM-DD`` → ``YYYY-MM-DDT00:00:00Z`` dla ``ccxt.parse8601``."""
    if "T" in value:
        return value if value.endswith("Z") else value + "Z"
    return value + "T00:00:00Z"


def _bybit_market_symbol(symbol: str) -> str:
    """'BTC/USDT' → 'BTC/USDT:USDT' (linear USDT perp w notacji CCXT)."""
    base, quote = symbol.split("/") if "/" in symbol else (symbol[:-4], symbol[-4:])
    return f"{base}/{quote}:{quote}"


def _fetch_funding_binance(ex: ccxt.Exchange, symbol: str, since: int, end_ms: int) -> list[dict]:
    """Loop po ``fapiPublicGetFundingRate`` (Binance implicit endpoint)."""
    market = symbol_noslash(symbol)  # 'BTC/USDT' → 'BTCUSDT'
    rows: list[dict] = []
    while since < end_ms:
        data = ex.fapiPublicGetFundingRate(
            {"symbol": market, "startTime": since, "limit": _LIMIT_BINANCE}
        )
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
    return rows


def _fetch_funding_bybit(ex: ccxt.Exchange, symbol: str, since: int, end_ms: int) -> list[dict]:
    """Pobiera malejące strony Bybit v5 w zakresie ograniczonym przez ``endTime``."""
    market = _bybit_market_symbol(symbol)  # 'BTC/USDT' → 'BTC/USDT:USDT'
    rows: list[dict] = []
    seen_timestamps: set[int] = set()
    cursor_end = end_ms

    # Bybit zwraca najwyżej 200 najnowszych rekordów do endTime. Dlatego
    # paginujemy wstecz po najstarszym timestampie strony; przesuwanie `since`
    # do przodu zachowałoby wyłącznie ostatnią stronę wieloletniego zakresu.
    while since <= cursor_end:
        data = ex.fetch_funding_rate_history(
            market,
            since=since,
            limit=_LIMIT_BYBIT,
            params={"endTime": cursor_end},
        )
        if not data:
            if rows:
                raise RuntimeError(
                    "Bybit funding history is incomplete before the requested start boundary"
                )
            break

        page_timestamps = [int(item["timestamp"]) for item in data]
        if any(ts < since or ts > cursor_end for ts in page_timestamps):
            raise RuntimeError("Bybit funding page escaped the requested server-side time boundary")
        if len(set(page_timestamps)) != len(page_timestamps):
            raise RuntimeError("Bybit funding page contains duplicate settlement timestamps")
        overlap = seen_timestamps.intersection(page_timestamps)
        if overlap:
            raise RuntimeError("Bybit funding pagination returned an overlapping settlement page")

        for d in data:
            ts = int(d["timestamp"])
            rows.append(
                {
                    "datetime": pd.to_datetime(ts, unit="ms", utc=True),
                    "funding_rate": float(d["fundingRate"]),
                }
            )
        seen_timestamps.update(page_timestamps)

        oldest = min(page_timestamps)
        if oldest <= since:
            break
        next_cursor_end = oldest - 1
        if next_cursor_end >= cursor_end:
            raise RuntimeError("Bybit funding pagination cursor did not move backwards")
        cursor_end = next_cursor_end
        time.sleep(ex.rateLimit / 1000)

    return sorted(rows, key=lambda row: row["datetime"])


def fetch_funding(
    symbol: str, start: str, end: str | None = None, exchange: str = "binance"
) -> pd.DataFrame:
    """Pobiera funding history dla symbolu w zakresie ``[start, end]``.

    Args:
        symbol: np. ``"BTC/USDT"``.
        start: ISO date/datetime UTC, np. ``"2019-09-08"`` (Binance) / ``"2020-03-25"`` (Bybit).
        end: ISO date/datetime UTC; ``None`` → bieżący moment.
        exchange: ``"binance"`` (implicit fapi endpoint) lub ``"bybit"``
            (unified ``fetch_funding_rate_history``).

    Returns:
        DataFrame z kolumnami ``datetime`` (UTC) i ``funding_rate``, posortowany,
        bez duplikatów. Pusty DataFrame gdy brak danych.
    """
    if exchange == "binance":
        ex = ccxt.binance({"options": {"defaultType": "future"}})
    elif exchange == "bybit":
        ex = ccxt.bybit({"options": {"defaultType": "swap"}})
    else:
        raise NotImplementedError(f"Nieobsługiwana giełda funding: {exchange}")

    since = ex.parse8601(_to_iso(start))
    end_ms = ex.parse8601(_to_iso(end)) if end else ex.milliseconds()

    if exchange == "binance":
        rows = _fetch_funding_binance(ex, symbol, since, end_ms)
    else:
        rows = _fetch_funding_bybit(ex, symbol, since, end_ms)

    if not rows:
        logger.warning(
            "No funding rows fetched",
            extra={"symbol": symbol, "start": start, "exchange": exchange},
        )
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
            "exchange": exchange,
            "rows": len(df),
            "first": str(df["datetime"].iloc[0]),
            "last": str(df["datetime"].iloc[-1]),
        },
    )
    return df


def save_funding(df: pd.DataFrame, symbol: str, exchange: str = "binance") -> Path:
    """Zapisuje funding DataFrame do bot_data/processed/<exchange>_<SYMBOL>_funding.csv."""
    path = get_funding_path(symbol, exchange)
    df.to_csv(path, index=False)
    logger.info(
        "Funding saved",
        extra={"out_path": str(path), "rows": len(df), "symbol": symbol, "exchange": exchange},
    )
    return path


def main() -> None:
    """Entry point dla ``algo-fetch-funding``."""
    setup_logging()
    ap = argparse.ArgumentParser(
        description="algo-fetch-funding — USDT perp funding history Binance/Bybit (ADR-011/015)"
    )
    ap.add_argument("--symbol", required=True, help="np. BTC/USDT")
    ap.add_argument("--start", required=True, help="ISO date/datetime (UTC)")
    ap.add_argument("--end", default=None, help="ISO date/datetime (UTC); brak → teraz")
    ap.add_argument(
        "--exchange",
        default="binance",
        choices=EXCHANGE_CHOICES,
        help="Giełda źródłowa funding (binance | bybit). ADR-015.",
    )
    args = ap.parse_args()

    df = fetch_funding(args.symbol, args.start, args.end, exchange=args.exchange)
    if df.empty:
        raise SystemExit(f"Brak danych funding dla {args.symbol} od {args.start} ({args.exchange})")
    save_funding(df, args.symbol, exchange=args.exchange)


if __name__ == "__main__":
    main()
