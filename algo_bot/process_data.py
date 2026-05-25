#!/usr/bin/env python3
"""
algo_bot/process_data.py - standaryzuje RAW -> PROCESSED.

- RAW: bot_data/raw/BTC_USDT-5m.csv (kolumny: ts(ms) + datetime(UTC) + OHLCV; legacy: 'timestamp' w s)
- PROCESSED: bot_data/processed/binance_<SYMBOL>_<TF>.csv
  - index = UTC datetime
  - kolumny: Open,High,Low,Close,Volume (+ opcjonalne featury)

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

from algo_bot.log import get_logger, setup_logging

logger = get_logger(__name__)

# --- opcjonalne featury (TA-Lib) ---
try:
    import talib

    _HAS_TALIB = True
except Exception:
    _HAS_TALIB = False

# --- konfiguracje ---
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
DEFAULT_MAX_MISSING_RATIO = 0.005  # 0.5%

RAW_DIR = Path("bot_data/raw")
PROC_DIR = Path("bot_data/processed")


def parse_legacy_name(raw_path: Path) -> tuple[str, str]:
    """
    BTC_USDT-5m.csv -> ('BTCUSDT','5m')
    """
    stem = raw_path.stem  # BTC_USDT-5m
    base, tf = stem.split("-", 1)
    b, q = base.split("_", 1)
    symbol = f"{b}{q}".upper()
    return symbol, tf


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


def processed_filename(symbol_no_slash: str, timeframe: str) -> Path:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    return PROC_DIR / f"binance_{symbol_no_slash}_{timeframe}.csv"


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
) -> Path:
    """
    Przetwarza pojedynczy RAW → PROCESSED (z walidacją i ewentualnym fill braków).
    Zwraca ścieżkę wyjściową.
    """
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)

    symbol, timeframe = parse_legacy_name(raw_path)
    if timeframe not in TF_MS:
        raise ValueError(f"Unsupported timeframe in filename: {timeframe}")

    df = pd.read_csv(raw_path)
    df = _ensure_ts_datetime(df)

    # sanity OHLCV
    needed = ["Open", "High", "Low", "Close", "Volume"]
    for c in needed:
        if c not in df.columns:
            raise ValueError(f"Missing column '{c}' in RAW {raw_path}")

    # walidacja + fill braków
    df = validate_and_fill(
        df[["ts", "datetime", "Open", "High", "Low", "Close", "Volume"]],
        timeframe,
        max_missing_ratio=max_missing_ratio,
    )

    # Trzymaj kolumnę 'datetime' (UTC) w PROCESSED, żeby loader backtestera mógł ją sparsować
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime")

    # opcjonalne featury
    df = compute_features(df, feature_cfg)

    out = processed_filename(symbol, timeframe)
    cols_order = [
        "datetime",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ] + [c for c in df.columns if c not in ["datetime", "Open", "High", "Low", "Close", "Volume"]]
    df[cols_order].to_csv(out, index=False)
    logger.info(
        "Wrote PROCESSED file",
        extra={"out_path": str(out), "rows": len(df), "symbol": symbol, "timeframe": timeframe},
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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Poziom logowania dla process CLI.",
    )
    args = ap.parse_args()

    # ADR-006: setup_logging na entry point CLI (idempotentne).
    setup_logging(level=getattr(logging, args.log_level))

    feature_cfg = None
    if args.features_json:
        import json

        feature_cfg = json.loads(args.features_json)

    if args.raw_path:
        process_file(
            Path(args.raw_path), feature_cfg=feature_cfg, max_missing_ratio=args.max_missing_ratio
        )
    else:
        # batch: wszystkie RAW
        files = sorted(RAW_DIR.glob("*.csv"))
        if not files:
            logger.warning("Brak plików RAW w katalogu wejściowym", extra={"raw_dir": str(RAW_DIR)})
            return
        for p in files:
            try:
                process_file(p, feature_cfg=feature_cfg, max_missing_ratio=args.max_missing_ratio)
            except Exception:
                # exc_info=True dorzuca traceback do JSON file handlera (ADR-006)
                logger.exception("Błąd przetwarzania pliku RAW", extra={"raw_path": str(p)})


if __name__ == "__main__":
    main()
