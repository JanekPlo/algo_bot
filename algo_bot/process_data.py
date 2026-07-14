#!/usr/bin/env python3
"""
algo_bot/process_data.py - standaryzuje RAW -> PROCESSED.

- RAW: bot_data/raw/BTC_USDT-5m.csv (Binance legacy) lub bot_data/raw/bybit_BTC_USDT-5m.csv
  (kolumny: ts(ms) + datetime(UTC) + OHLCV; legacy: 'timestamp' w s)
- PROCESSED: bot_data/processed/<exchange>_<SYMBOL>_<TF>.csv  (ADR-015)
  - index = UTC datetime
  - kolumny: Open,High,Low,Close,Volume (+ opcjonalne featury)
- Agregacja offline (M5 → M10) przez --resample-to (Bybit/Binance nie mają natywnego 10m)

Walidacja:
- sprawdzamy siatke czasu wg TF; jesli brakow > 0.5% -> blad
- wypelnienie brakow (<=0.5%): Close->Open, High/Low = max/min z O/C, Volume=0

Uzycie:
  # jeden plik RAW
  python3 -m algo_bot.process_data bot_data/raw/BTC_USDT-5m.csv

  # batch na wszystkie RAW-5m
  for f in bot_data/raw/*-5m.csv; do python3 -m algo_bot.process_data "$f"; done

Public API:
- main() - CLI entry (po dodaniu do [project.scripts] zostanie algo-process)
- process_file(path) - przetwarza pojedynczy plik raw -> processed
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from algo_bot.data_integrity import check_mark_price_integrity
from algo_bot.log import get_logger, setup_logging

logger = get_logger(__name__)

# --- opcjonalne featury (TA-Lib) ---
try:
    import talib

    _HAS_TALIB = True
except Exception:
    _HAS_TALIB = False

# --- konfiguracje ---
# 10m NIE jest natywnym interwałem giełd — powstaje przez agregację z 5m
# (patrz aggregate_processed / --resample-to). TF_MS obejmuje go dla ścieżki
# przetwarzania/walidacji.
TF_MS = {"5m": 300_000, "10m": 600_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
DEFAULT_MAX_MISSING_RATIO = 0.005  # 0.5%

# Prefiksy giełd rozpoznawane w nazwach plików (ADR-015).
KNOWN_EXCHANGES = {"binance", "bybit"}

# Mapa pandas dla resamplingu (agregacja M5→M10 itp.).
TF_PANDAS = {"5m": "5min", "10m": "10min", "15m": "15min", "1h": "1h", "4h": "4h"}

RAW_DIR = Path("bot_data/raw")
PROC_DIR = Path("bot_data/processed")


def parse_raw_name(raw_path: Path) -> tuple[str, str, str]:
    """Parsuje nazwę pliku RAW → ``(exchange, symbol_bez_slasha, timeframe)``.

    Obsługuje oba formaty (ADR-015):
        * legacy Binance bez prefiksu: ``BTC_USDT-5m.csv`` → ``('binance','BTCUSDT','5m')``
        * z prefiksem giełdy:          ``bybit_BTC_USDT-5m.csv`` → ``('bybit','BTCUSDT','5m')``
    """
    stem = raw_path.stem  # np. 'bybit_BTC_USDT-5m' albo 'BTC_USDT-5m'
    name, tf = stem.rsplit("-", 1)
    if name.endswith("-mark"):
        name = name.removesuffix("-mark")
    parts = name.split("_")
    if parts and parts[0].lower() in KNOWN_EXCHANGES:
        exchange = parts[0].lower()
        base, quote = parts[1], parts[2]
    else:
        exchange = "binance"
        base, quote = parts[0], parts[1]
    symbol = f"{base}{quote}".upper()
    return exchange, symbol, tf


def parse_legacy_name(raw_path: Path) -> tuple[str, str]:
    """[compat] BTC_USDT-5m.csv -> ('BTCUSDT','5m'). Patrz parse_raw_name."""
    _exchange, symbol, tf = parse_raw_name(raw_path)
    return symbol, tf


def is_mark_price_raw(raw_path: Path) -> bool:
    """Rozpoznaje jawny suffix ``-mark-<TF>.csv`` w nazwie RAW."""

    return "-mark-" in raw_path.stem


def _ensure_ts_datetime(df: pd.DataFrame) -> pd.DataFrame:
    # ts (ms)
    if "ts" not in df.columns:
        if "timestamp" in df.columns:
            t = df["timestamp"].astype(int)
            df["ts"] = t.where(t > 3_000_000_000, t * 1000)
        else:
            raise ValueError("RAW must contain 'ts' (ms) or legacy 'timestamp' (s)")
    # datetime (UTC)
    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

    # Koercja i sanity: usuń wiersze z brakującym ts, rzutuj ts na int
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts"]).copy()
    df["ts"] = df["ts"].astype("int64")
    return df


def validate_and_fill(
    df: pd.DataFrame, timeframe: str, max_missing_ratio=DEFAULT_MAX_MISSING_RATIO
) -> pd.DataFrame:
    step = TF_MS[timeframe]
    df = df.sort_values("ts").drop_duplicates(subset=["ts"])

    ts_start, ts_end = int(df["ts"].iloc[0]), int(df["ts"].iloc[-1])
    full_index = range(ts_start, ts_end + step, step)

    have = set(df["ts"].tolist())
    missing = [t for t in full_index if t not in have]
    miss_ratio = len(missing) / max(1, len(list(full_index)))

    if miss_ratio > max_missing_ratio:
        raise ValueError(
            f"Too many missing bars ({miss_ratio:.3%}) — aborting. Missing count={len(missing)}"
        )

    if missing:
        filler = pd.DataFrame({"ts": missing})
        merged = pd.concat([df, filler], ignore_index=True).sort_values("ts")
        merged["datetime"] = pd.to_datetime(merged["ts"], unit="ms", utc=True)

        # wypełnianie wg spec:
        merged["Close"] = merged["Close"].ffill()
        merged["Open"] = merged["Open"].fillna(merged["Close"].shift(1))
        merged["High"] = merged["High"].fillna(merged[["Open", "Close"]].max(axis=1))
        merged["Low"] = merged["Low"].fillna(merged[["Open", "Close"]].min(axis=1))
        merged["Volume"] = merged["Volume"].fillna(0.0)

        df = merged

    return df.sort_values("ts").reset_index(drop=True)


def processed_filename(
    symbol_no_slash: str,
    timeframe: str,
    exchange: str = "binance",
    *,
    mark_price: bool = False,
) -> Path:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    marker = "_mark" if mark_price else ""
    return PROC_DIR / f"{exchange.lower()}_{symbol_no_slash}{marker}_{timeframe}.csv"


def compute_features(df: pd.DataFrame, feature_cfg: list[dict[str, Any]] | None) -> pd.DataFrame:
    """
    Opcjonalne featury. Jeśli TA-Lib brak – pomiń grzecznie.
    feature_cfg (przykład):
      - {'type':'BBANDS','params':{'timeperiod':21,'nbdevup':2.0,'nbdevdn':2.0}}
      - {'type':'RSI','params':{'timeperiod':14}}
    """
    if not feature_cfg:
        return df
    if not _HAS_TALIB:
        logger.warning("TA-Lib not available — skipping features")
        return df

    for feat in feature_cfg:
        ftype = str(feat.get("type", "")).upper()
        params = feat.get("params", {}) or {}
        if ftype == "BBANDS":
            # TA-Lib stuby oczekuja ndarray, pandas.Series tez dziala runtime
            upper, mid, lower = talib.BBANDS(df["Close"], **params)  # type: ignore[arg-type]
            df["BB_upper"] = upper
            df["BB_middle"] = mid
            df["BB_lower"] = lower
        elif ftype == "RSI":
            df["RSI"] = talib.RSI(df["Close"], **params)  # type: ignore[arg-type]
        else:
            logger.warning("Unknown feature type — skipping", extra={"feature_type": ftype})
    return df


def process_file(
    raw_path: Path,
    feature_cfg: list[dict[str, Any]] | None = None,
    max_missing_ratio: float = DEFAULT_MAX_MISSING_RATIO,
    exchange: str | None = None,
) -> Path:
    """
    Przetwarza pojedynczy RAW → PROCESSED (z walidacją i ewentualnym fill braków).
    Zwraca ścieżkę wyjściową.

    exchange: gdy None — wnioskowany z nazwy pliku (prefiks giełdy lub 'binance'
        dla legacy). Podanie jawnie nadpisuje wnioskowanie.
    """
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)

    inferred_exchange, symbol, timeframe = parse_raw_name(raw_path)
    exchange = (exchange or inferred_exchange).lower()
    mark_price = is_mark_price_raw(raw_path)
    if timeframe not in TF_MS:
        raise ValueError(f"Unsupported timeframe in filename: {timeframe}")
    if mark_price and exchange != "bybit":
        raise ValueError("Mark-price pipeline jest obecnie zamrożony dla Bybit")

    df = pd.read_csv(raw_path)
    df = _ensure_ts_datetime(df)

    # sanity OHLCV / mark-price OHLC
    needed = ["Open", "High", "Low", "Close"]
    if not mark_price:
        needed.append("Volume")
    for c in needed:
        if c not in df.columns:
            raise ValueError(f"Missing column '{c}' in RAW {raw_path}")

    if mark_price:
        # Brakującego mark-price nie wolno syntetyzować: High/Low luki mogło
        # przekroczyć liquidation price. Integrity jest zatem twarde i no-fill.
        mark_columns = ["ts", "datetime", "Open", "High", "Low", "Close"]
        df = df.loc[:, mark_columns].sort_values("ts").drop_duplicates(subset=["ts"])
        mark_indexed = df.set_index(pd.DatetimeIndex(df["datetime"])).drop(
            columns=["ts", "datetime"]
        )
        report = check_mark_price_integrity(
            mark_indexed,
            timeframe,
            symbol=symbol,
            exchange=exchange,
        )
        if not report.ok:
            raise ValueError(f"Mark-price integrity failed for {raw_path}")
    else:
        # Standard OHLCV zachowuje historyczną politykę fillowania małych luk.
        df = validate_and_fill(
            df[["ts", "datetime", "Open", "High", "Low", "Close", "Volume"]],
            timeframe,
            max_missing_ratio=max_missing_ratio,
        )

    # Trzymaj kolumnę 'datetime' (UTC) w PROCESSED, żeby loader backtestera mógł ją sparsować
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime")

    # opcjonalne featury
    if not mark_price:
        df = compute_features(df, feature_cfg)

    out = processed_filename(symbol, timeframe, exchange, mark_price=mark_price)
    cols_order = [
        "datetime",
        "Open",
        "High",
        "Low",
        "Close",
    ]
    if not mark_price:
        cols_order.append("Volume")
    cols_order += [
        c for c in df.columns if c not in ["datetime", "Open", "High", "Low", "Close", "Volume"]
    ]
    df[cols_order].to_csv(out, index=False)
    logger.info(
        "Wrote PROCESSED file",
        extra={
            "out_path": str(out),
            "rows": len(df),
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": exchange,
        },
    )
    return out


def aggregate_processed(
    symbol_no_slash: str,
    src_tf: str,
    dst_tf: str,
    exchange: str = "binance",
) -> Path:
    """Agreguje istniejący plik PROCESSED do grubszego TF (np. M5 → M10).

    Bybit/Binance nie oferują natywnie interwału 10m — powstaje on offline z 5m
    (2 świece → 1). Czyta ``<exchange>_<SYMBOL>_<src_tf>.csv``, resampluje OHLCV
    (Open=first, High=max, Low=min, Close=last, Volume=sum) i zapisuje
    ``<exchange>_<SYMBOL>_<dst_tf>.csv``.

    Raises:
        FileNotFoundError: gdy źródłowy plik PROCESSED nie istnieje.
        ValueError: gdy ``dst_tf`` nie jest wielokrotnością ``src_tf`` lub TF nieznany.
    """
    for tf in (src_tf, dst_tf):
        if tf not in TF_PANDAS:
            raise ValueError(f"Nieznany timeframe do resamplingu: {tf}")
    if TF_MS[dst_tf] % TF_MS[src_tf] != 0 or TF_MS[dst_tf] <= TF_MS[src_tf]:
        raise ValueError(f"dst_tf ({dst_tf}) musi być wielokrotnością src_tf ({src_tf})")

    src_path = processed_filename(symbol_no_slash, src_tf, exchange)
    if not src_path.exists():
        raise FileNotFoundError(f"Brak źródła do agregacji: {src_path}")

    df = pd.read_csv(src_path, parse_dates=["datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()

    how = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    agg = df.resample(TF_PANDAS[dst_tf]).agg(how).dropna(subset=["Open", "Close"])  # type: ignore[arg-type]

    out = processed_filename(symbol_no_slash, dst_tf, exchange)
    agg.reset_index().to_csv(out, index=False)
    logger.info(
        "Aggregated PROCESSED file",
        extra={
            "out_path": str(out),
            "rows": len(agg),
            "symbol": symbol_no_slash,
            "src_tf": src_tf,
            "dst_tf": dst_tf,
            "exchange": exchange,
        },
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_path", nargs="?", help="np. bot_data/raw/BTC_USDT-5m.csv")
    ap.add_argument(
        "--max-missing-ratio",
        type=float,
        default=DEFAULT_MAX_MISSING_RATIO,
        help="Dopuszczalny udział braków (domyślnie 0.005 = 0.5%%)",
    )
    # Prosto: na razie bez YAML – featury można dopchnąć później
    ap.add_argument("--features-json", help="Opcjonalny JSON z listą featurów", default=None)
    ap.add_argument(
        "--exchange",
        default=None,
        choices=sorted(KNOWN_EXCHANGES),
        help="Nadpisuje giełdę (domyślnie wnioskowana z nazwy pliku). ADR-015.",
    )
    # Tryb agregacji offline (np. M5 → M10) zamiast RAW → PROCESSED.
    ap.add_argument(
        "--resample-to",
        default=None,
        help="Zamiast RAW→PROCESSED: agreguj istniejący PROCESSED do tego TF (np. 10m).",
    )
    ap.add_argument("--src-tf", default="5m", help="Źródłowy TF dla --resample-to (domyślnie 5m).")
    ap.add_argument("--symbol", default=None, help="Symbol dla --resample-to (np. BTCUSDT).")
    ap.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Poziom logowania dla process CLI.",
    )
    args = ap.parse_args()

    # ADR-006: setup_logging na entry point CLI (idempotentne).
    setup_logging(level=getattr(logging, args.log_level))

    # Tryb agregacji (M5 → M10 itp.) — nie dotyka RAW.
    if args.resample_to:
        if not args.symbol:
            raise SystemExit("--resample-to wymaga --symbol (np. BTCUSDT)")
        exchange = (args.exchange or "binance").lower()
        aggregate_processed(
            args.symbol.replace("/", "").upper(), args.src_tf, args.resample_to, exchange
        )
        return

    feature_cfg = None
    if args.features_json:
        import json

        feature_cfg = json.loads(args.features_json)

    if args.raw_path:
        process_file(
            Path(args.raw_path),
            feature_cfg=feature_cfg,
            max_missing_ratio=args.max_missing_ratio,
            exchange=args.exchange,
        )
    else:
        # batch: wszystkie RAW
        files = sorted(RAW_DIR.glob("*.csv"))
        if not files:
            logger.warning("Brak plików RAW w katalogu wejściowym", extra={"raw_dir": str(RAW_DIR)})
            return
        for p in files:
            try:
                process_file(
                    p,
                    feature_cfg=feature_cfg,
                    max_missing_ratio=args.max_missing_ratio,
                    exchange=args.exchange,
                )
            except Exception:
                # exc_info=True dorzuca traceback do JSON file handlera (ADR-006)
                logger.exception("Błąd przetwarzania pliku RAW", extra={"raw_path": str(p)})


if __name__ == "__main__":
    main()
