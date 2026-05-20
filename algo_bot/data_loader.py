"""
algo_bot/data_loader.py – ujednolicony loader danych OHLCV.

Nowy tor pracy (dla PROCESSED):
- load_processed(symbol, timeframe, ...) -> DataFrame z indeksem UTC i kolumnami OHLCV (+ ew. featury)
- count_missing_bars(df, timeframe) -> ile świec brakuje względem idealnej siatki
- get_processed_path(...) -> ścieżka do pliku w bot_data/processed

Zachowane (legacy) – żeby nic nie pękło w istniejącym kodzie:
- init_exchange, fetch_ohlcv, fetch_and_save_ohlcv, batch_fetch_symbols,
  list_csv_files, load_csv_ohlcv, load_all_csv_ohlcv, resample_ohlcv
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd

# === ŚCIEŻKI ===
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "bot_data" / "raw"
PROC_DIR = PROJECT_ROOT / "bot_data" / "processed"


# === MAPY CZASU ===
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
TF_PANDAS = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "1d": "1D",
}


# === UTILSY SYMBOLI ===
def to_ccxt_symbol(sym: str) -> str:
    s = sym.strip().upper()
    if "/" in s:
        return s
    if "_" in s:
        b, q = s.split("_", 1)
        return f"{b}/{q}"
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return f"{s}/USDT"


def symbol_noslash(sym: str) -> str:
    """'BTC/USDT' -> 'BTCUSDT'; 'BTC_USDT' -> 'BTCUSDT'."""
    s = to_ccxt_symbol(sym)
    b, q = s.split("/")
    return f"{b}{q}"


# === NOWY TOR: PROCESSED ===
def get_processed_path(symbol: str, timeframe: str, exchange: str = "binance") -> Path:
    """
    Ścieżka do standaryzowanego pliku PROCESSED:
    bot_data/processed/binance_<SYMBOL>_<TF>.csv  (np. binance_BTCUSDT_5m.csv)
    """
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    return PROC_DIR / f"{exchange.lower()}_{symbol_noslash(symbol)}_{timeframe}.csv"


def _coerce_ohlcv_types(df: pd.DataFrame) -> pd.DataFrame:
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Akceptuje:
      - kolumnę 'datetime' (string/ts) lub
      - kolumnę 'ts' (ms) lub
      - indeks już będący datetime
    Zwraca: indeks = UTC datetime, posortowany, bez duplikatów.
    """
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
        if idx.tz is None:
            df.index = idx.tz_localize("UTC")
        else:
            df.index = idx.tz_convert("UTC")
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime")
    elif "ts" in df.columns:
        df["datetime"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        df = df.set_index("datetime")
        # opcjonalnie: df.drop(columns=["ts"], inplace=True)
    else:
        raise ValueError("Brak kolumny 'datetime' lub 'ts', a indeks nie jest datetime.")

    # sort + dedupe
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_processed(
    symbol: str,
    timeframe: str,
    *,
    exchange: str = "binance",
    features: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """
    Wczytuje PROCESSED (UTC index). Obsługuje oba warianty: z kolumną 'datetime'/'ts' albo z indeksem datetime.

    Args:
      symbol: np. 'BTC/USDT'
      timeframe: '5m', '15m', '1h', '4h'
      exchange: nazwa (prefiks w pliku), domyślnie 'binance'
      features: jeśli podasz listę, zwróci tylko OHLCV + te kolumny (jeśli istnieją)
      start/end: opcjonalny zakres czasu, parsowalny przez pandas (UTC)

    Returns:
      DataFrame z kolumnami: Open, High, Low, Close, Volume (+ ew. featury), indeks UTC.
    """
    path = get_processed_path(symbol, timeframe, exchange)
    if not path.exists():
        # fallbacki nazw (legacy): BTC_USDT-5m.csv, binance_BTC_USDT_5m.csv, itp.
        legacy1 = PROC_DIR / f"{symbol.replace('/', '_')}-{timeframe}.csv"
        legacy2 = PROC_DIR / f"{exchange}_{symbol.replace('/', '_')}_{timeframe}.csv"
        candidates = [p for p in [legacy1, legacy2] if p.exists()]
        if not candidates:
            raise FileNotFoundError(f"Processed file not found: {path}")
        path = candidates[0]

    df = pd.read_csv(path)
    df = _ensure_datetime_index(df)
    df = _coerce_ohlcv_types(df)

    # sanity OHLCV
    needed = ["Open", "High", "Low", "Close", "Volume"]
    for c in needed:
        if c not in df.columns:
            raise ValueError(f"Missing column '{c}' in {path}")

    # wybór kolumn (OHLCV + featury)
    cols = needed.copy()
    if features:
        cols += [c for c in features if c in df.columns]
    else:
        # zachowaj wszystkie dodatkowe kolumny, ale OHLCV trzymaj na przodzie
        extras = [c for c in df.columns if c not in needed]
        cols += extras

    df = df[cols]

    # filtr zakresu
    if start:
        df = df[df.index >= pd.to_datetime(start, utc=True)]
    if end:
        df = df[df.index <= pd.to_datetime(end, utc=True)]

    return df


def count_missing_bars(df: pd.DataFrame, timeframe: str) -> int:
    """
    Liczy brakujące świece względem idealnej siatki (od min do max indeksu).
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame index must be DatetimeIndex")

    df = df[~df.index.duplicated(keep="last")].sort_index()

    start = int(df.index[0].timestamp() * 1000)
    end = int(df.index[-1].timestamp() * 1000)
    step = TF_MS[timeframe]

    expected = 1 + (end - start) // step
    actual = df.index.size
    missing = int(expected - actual)
    return max(0, missing)


# === LEGACY: init/fetch/raw ===
def init_exchange(
    exchange_name: str, api_key: str | None = None, secret: str | None = None, **kwargs: Any
) -> ccxt.Exchange:
    """[LEGACY] Inicjalizacja giełdy CCXT (używaj fetch_data.py do pobierania wsadowego)."""
    if not hasattr(ccxt, exchange_name):
        raise ValueError(f"Nieznana giełda CCXT: {exchange_name}")
    exch_cls = getattr(ccxt, exchange_name)
    return exch_cls({"apiKey": api_key, "secret": secret, **kwargs})


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str = "1h",
    since: int | None = None,
    limit: int = 1000,
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """[LEGACY] Paginacja OHLCV – preferuj teraz src/fetch_data.py."""
    all_bars = []
    while True:
        bars = exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since, limit=limit, params=params or {}
        )
        if not bars:
            break
        all_bars.extend(bars)
        since = bars[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    df = pd.DataFrame(all_bars, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("datetime").drop(columns=["timestamp"]).sort_index()


def fetch_and_save_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: int | None,
    limit: int,
    output_dir: str,
    filename: str | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """[LEGACY] Pobiera OHLCV i zapisuje do CSV – rekomendowany fetch_data.py."""
    df = fetch_ohlcv(exchange, symbol, timeframe, since, limit, params)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "_")
    fname = filename or f"{safe_symbol}-{timeframe}.csv"
    path = out / fname
    df.to_csv(path)
    return str(path)


def batch_fetch_symbols(
    exchange: ccxt.Exchange,
    symbols: list[str],
    timeframe: str,
    since: int | None,
    limit: int,
    output_dir: str,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """[LEGACY] Batch pobieranie CSV – rekomendowany fetch_data.py."""
    results: dict[str, str] = {}
    for sym in symbols:
        try:
            path = fetch_and_save_ohlcv(
                exchange, sym, timeframe, since, limit, output_dir, None, params
            )
            results[sym] = path
        except Exception as e:
            print(f"Błąd pobierania {sym}: {e}")
    return results


def list_csv_files(directory: str) -> list[str]:
    """Lista plików CSV w katalogu."""
    d = Path(directory)
    return [str(d / f) for f in os.listdir(d) if f.lower().endswith(".csv")]


def load_csv_ohlcv(
    path: str, datetime_col: str = "datetime", cols_mapping: dict[str, str] | None = None
) -> pd.DataFrame:
    """
    [LEGACY] Wczytywanie dowolnego CSV OHLCV.
    Zwraca index=UTC datetime + kolumny OHLCV.
    """
    df = pd.read_csv(path, parse_dates=[datetime_col])
    df = df.rename(columns={datetime_col: "datetime"})
    if cols_mapping:
        df = df.rename(columns=cols_mapping)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    expected = ["Open", "High", "Low", "Close", "Volume"]
    missing = set(expected) - set(df.columns)
    if missing:
        raise ValueError(f"Brakujące kolumny OHLCV {missing} w {path}")
    df = _coerce_ohlcv_types(df)
    return df[expected]


def load_all_csv_ohlcv(
    directory: str, datetime_col: str = "datetime", cols_mapping: dict[str, str] | None = None
) -> dict[str, pd.DataFrame]:
    """[LEGACY] Batch load CSV."""
    data: dict[str, pd.DataFrame] = {}
    for path in list_csv_files(directory):
        symbol = os.path.splitext(os.path.basename(path))[0]
        data[symbol] = load_csv_ohlcv(path, datetime_col, cols_mapping)
    return data


def resample_ohlcv(
    df: pd.DataFrame, timeframe: str, how: dict[str, str] | None = None
) -> pd.DataFrame:
    """
    Resampling OHLCV na inny interwał (pandas).
    """
    if timeframe not in TF_PANDAS:
        raise ValueError(f"Unsupported target timeframe: {timeframe}")
    if how is None:
        how = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    df_r = df.resample(TF_PANDAS[timeframe]).agg(how)
    return df_r.dropna()
