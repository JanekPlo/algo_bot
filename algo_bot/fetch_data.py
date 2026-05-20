#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from collections.abc import Iterable
from pathlib import Path

import ccxt
import pandas as pd

# === Konfiguracja kroków czasowych (ms) ===
TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


# === Utils ===
def to_ccxt_symbol(sym: str) -> str:
    """
    Normalizuje zapis symbolu do formatu CCXT, np.:
    - 'BTC_USDT'  -> 'BTC/USDT'
    - 'BTCUSDT'   -> 'BTC/USDT'
    - 'BTC/USDT'  -> 'BTC/USDT' (bez zmian)
    """
    s = sym.strip().upper()
    if "/" in s:
        return s
    if "_" in s:
        b, q = s.split("_", 1)
        return f"{b}/{q}"
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    # fallback – spróbuj domyślnie USDT
    return f"{s}/USDT"


def raw_filename(symbol_ccxt: str, timeframe: str) -> Path:
    """RAW zapisujemy w legacy formacie: bot_data/raw/BTC_USDT-5m.csv"""
    base, quote = symbol_ccxt.split("/")
    fn = f"{base}_{quote}-{timeframe}.csv"
    return Path("bot_data/raw") / fn


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def last_ts_from_file(path: Path) -> int | None:
    """
    Zwraca ostatni ts (ms) z istniejącego pliku RAW.
    Obsługuje też legacy kolumnę 'timestamp' (sekundy).
    """
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, usecols=["ts"])
        if "ts" in df.columns and len(df):
            return int(df["ts"].max())
    except Exception:
        # Legacy fallback
        try:
            df = pd.read_csv(path, usecols=["timestamp"])
            if len(df):
                t = int(df["timestamp"].max())
                return t if t > 3_000_000_000 else t * 1000
        except Exception:
            return None
    return None


def backoff_sleep(attempt: int) -> None:
    """Łagodny backoff (max 30s)."""
    time.sleep(min(30.0, 1.6**attempt))


def fetch_ohlcv_batches(
    ex: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int | None = None,
    limit: int = 1000,
) -> Iterable[list[list[float]]]:
    """
    Generator batchy OHLCV (z retry) od 'since_ms' do 'until_ms' (jeśli podane).
    """
    tf_ms = TF_MS[timeframe]
    cursor = since_ms
    while True:
        # przerwij, jeśli osiągnęliśmy limit czasu
        if until_ms is not None and cursor > until_ms:
            return

        # kilka prób z backoffem
        ohlcv = None
        for i in range(6):
            try:
                ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit)
                break
            except (ccxt.NetworkError, ccxt.DDoSProtection, ccxt.ExchangeNotAvailable) as e:
                print(f"[fetch] WARN {type(e).__name__}: {e} (retry {i + 1}/6)")
                backoff_sleep(i)
        if ohlcv is None:
            # ostatnia próba – jeśli nadal None, rzuć wyjątek
            raise RuntimeError("fetch_ohlcv failed after retries")

        if not ohlcv:
            return

        yield ohlcv

        # przesuwamy kursor na koniec batcha + jedna świeca
        last_ts = ohlcv[-1][0]
        cursor = last_ts + tf_ms

        # rate limit friendly
        time.sleep(ex.rateLimit / 1000.0)


def _save_append(path: Path, new_df: pd.DataFrame) -> None:
    """
    Dopisuje nowe wiersze do RAW, deduplikuje po 'ts', sortuje.
    Gwarantuje kolumny: ts, datetime, Open, High, Low, Close, Volume
    """
    cols = ["ts", "datetime", "Open", "High", "Low", "Close", "Volume"]

    if path.exists():
        old = pd.read_csv(path)
        df = pd.concat([old, new_df], ignore_index=True)
    else:
        df = new_df.copy()

    # Dedup + sort
    if "ts" not in df.columns and "timestamp" in df.columns:
        # legacy 'timestamp' → 'ts' (sekundy → ms)
        t = df["timestamp"].astype(int)
        df["ts"] = t.where(t > 3_000_000_000, t * 1000)

    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

    df = df.drop_duplicates(subset=["ts"]).sort_values("ts")

    for c in cols:
        if c not in df.columns:
            if c == "datetime":
                df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            else:
                df[c] = pd.NA

    df = df[cols]
    df.to_csv(path, index=False)


# === Main CLI ===
def main():
    ap = argparse.ArgumentParser(description="Pobiera OHLCV do RAW (z resume) - Binance Futures")
    ap.add_argument("symbol", help="np. BTC/USDT, BTC_USDT lub BTCUSDT")
    ap.add_argument("timeframe", choices=list(TF_MS.keys()))
    ap.add_argument("--start", required=True, help="Początek zakresu w UTC, np. 2024-01-01")
    ap.add_argument("--end", help="Koniec zakresu w UTC (opcjonalnie), np. 2024-06-30")
    ap.add_argument(
        "--limit", type=int, default=1000, help="Limit świec na jeden request (domyślnie 1000)"
    )
    ap.add_argument(
        "--exchange", default="binance", choices=["binance"], help="Na razie wspieramy binance"
    )
    ap.add_argument(
        "--market", default="future", choices=["future", "spot"], help="Typ rynku dla CCXT"
    )
    args = ap.parse_args()

    symbol = to_ccxt_symbol(args.symbol)
    tf = args.timeframe
    tf_ms = TF_MS[tf]

    # Daty → ms
    start_dt = pd.to_datetime(args.start, utc=True)
    since_ms = int(start_dt.timestamp() * 1000)
    until_ms = None
    if args.end:
        end_dt = pd.to_datetime(args.end, utc=True)
        until_ms = int(end_dt.timestamp() * 1000)

    # CCXT client
    if args.exchange == "binance":
        ex = ccxt.binance(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "future" if args.market == "future" else "spot"},
            }
        )
    else:
        raise NotImplementedError("Tylko binance na tę chwilę")

    # Plik RAW + resume
    out_path = raw_filename(symbol, tf)
    ensure_parent(out_path)

    last_in_file = last_ts_from_file(out_path)
    if last_in_file is not None and last_in_file >= since_ms:
        since_ms = last_in_file + tf_ms
        print(f"[fetch] Resume from {pd.to_datetime(since_ms, unit='ms', utc=True)}")

    # Główny loop
    buffers: list[pd.DataFrame] = []
    total_rows = 0
    try:
        for batch in fetch_ohlcv_batches(ex, symbol, tf, since_ms, until_ms, limit=args.limit):
            df = pd.DataFrame(batch, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
            df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            buffers.append(df)

            if len(buffers) >= 12:  # co kilka batchy flush – zmniejsza zużycie RAM
                flush = pd.concat(buffers, ignore_index=True)
                _save_append(out_path, flush)
                total_rows += len(flush)
                buffers = []
                print(f"[flush] {total_rows} rows written → {out_path}")

        if buffers:
            flush = pd.concat(buffers, ignore_index=True)
            _save_append(out_path, flush)
            total_rows += len(flush)
            print(f"[flush] {total_rows} rows written → {out_path}")

        print(f"[done] RAW saved: {out_path} (rows appended: {total_rows})")

    except Exception as e:
        print(f"[error] {e}")
        raise


if __name__ == "__main__":
    main()
