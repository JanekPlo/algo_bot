#!/usr/bin/env python3
"""
process_data.py – przetwarza surowe dane OHLCV z bot_data/raw,
                     wzbogaca je o wybrane cechy i zapisuje do bot_data/processed.

Rola pliku w szkielecie:
- Standaryzacja i weryfikacja kolumn OHLCV.
- Obliczanie wskaźników technicznych (np. Bollinger Bands, RSI).
- Doklejanie dodatkowych źródeł (np. Fear&Greed, Open Interest) – tu można rozszerzyć.
- Zapis gotowego DataFrame do CSV w katalogu processed.

Użycie:
    python3 src/process_data.py
"""
import os
import glob
import yaml
import pandas as pd
import talib
from typing import Dict, Any

# Ścieżki do katalogów projektu
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
RAW_DIR = os.path.join(PROJECT_ROOT, 'bot_data', 'raw')
PROCESSED_DIR = os.path.join(PROJECT_ROOT, 'bot_data', 'processed')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config', 'config.yaml')


def load_config(path: str) -> Dict[str, Any]:
    """
    Wczytuje plik YAML z konfiguracją projektu.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Brak pliku konfiguracyjnego: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def compute_features(df: pd.DataFrame, feature_cfg: Dict[str, Any]) -> pd.DataFrame:
    """
    Dla DataFrame df oblicza wskaźniki na podstawie konfiguracji feature_cfg.

    feature_cfg przykładowo:
      - type: BBANDS
        params: {timeperiod: 21, nbdevup: 2.0, nbdevdn: 2.0}
      - type: RSI
        params: {timeperiod: 14}
    """
    for feat in feature_cfg:
        ftype = feat.get('type', '').upper()
        params = feat.get('params', {})
        if ftype == 'BBANDS':
            upper, mid, lower = talib.BBANDS(df['Close'], **params)
            df['BB_upper'] = upper
            df['BB_middle'] = mid
            df['BB_lower'] = lower
        elif ftype == 'RSI':
            df['RSI'] = talib.RSI(df['Close'], **params)
        else:
            # Miejsce na dodatkowe źródła np. Fear&Greed
            print(f"Nieznany typ cechy: {ftype}, pomijam.")
    return df


def process_file(path: str, feature_cfg: Dict[str, Any]) -> None:
    """
    Przetwarza pojedynczy surowy plik CSV oraz oblicza cechy.
    """
    df = pd.read_csv(path, parse_dates=['datetime'])
    df = df.rename(columns={'datetime': 'datetime'})
    df = df.set_index('datetime').sort_index()

    # Weryfikacja podstawowych kolumn OHLCV
    expected = ['Open', 'High', 'Low', 'Close', 'Volume']
    missing = set(expected) - set(df.columns)
    if missing:
        raise ValueError(f"Brakuje kolumn OHLCV: {missing} w {path}")
    df = df[expected]

    # Oblicz wskaźniki techniczne
    df = compute_features(df, feature_cfg)

    # Zapis do katalogu processed
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    fname = os.path.basename(path)
    out_path = os.path.join(PROCESSED_DIR, fname)
    df.to_csv(out_path)
    print(f"Przetworzono i zapisano: {out_path}")


def main():
    # Wczytaj konfigurację projektu i listę cech
    cfg = load_config(CONFIG_PATH)
    feature_cfg = cfg.get('defaults', {}).get('features', [])

    # Przetwórz każdy plik w RAW_DIR
    pattern = os.path.join(RAW_DIR, '*.csv')
    files = glob.glob(pattern)
    if not files:
        print(f"Brak plików raw w {RAW_DIR}")
        return
    for path in files:
        try:
            process_file(path, feature_cfg)
        except Exception as e:
            print(f"Błąd podczas przetwarzania {path}: {e}")


if __name__ == '__main__':
    main()
