#!/usr/bin/env python3
"""
fetch_data.py – pobiera historyczne dane OHLCV z giełdy (Binance, Bybit itp.)
                       i zapisuje surowe CSV do katalogu bot_data/raw w katalogu projektu.
"""
import os
import ccxt
import pandas as pd
import argparse
import time
from datetime import datetime

# Funkcja zwraca katalog projektu (rodzic katalogu tego skryptu)
def get_project_root() -> str:
    """
    Zwraca absolutną ścieżkę do katalogu głównego projektu (jeden poziom wyżej niż src/).
    """
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir)
    )

# Funkcja zwraca katalog raw względem root projektu
def get_raw_dir() -> str:
    """
    Zwraca ścieżkę do bot_data/raw w katalogu projektu.
    Tworzy katalog, jeśli nie istnieje.
    """
    root = get_project_root()
    raw_dir = os.path.join(root, 'bot_data', 'raw')
    os.makedirs(raw_dir, exist_ok=True)
    return raw_dir


def init_exchange(name: str, api_key: str = None, secret: str = None, **kwargs) -> ccxt.Exchange:
    if not hasattr(ccxt, name):
        raise ValueError(f"Nieznana giełda CCXT: {name}")
    exch_cls = getattr(ccxt, name)
    return exch_cls({ 'apiKey': api_key, 'secret': secret, **kwargs })


def fetch_ohlcv(exchange, symbol: str, timeframe: str, since: int, limit: int) -> pd.DataFrame:
    """Pobiera pełny zestaw danych przez paginację i zwraca DataFrame."""
    all_bars = []
    while True:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not bars:
            break
        all_bars.extend(bars)
        since = bars[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    df = pd.DataFrame(all_bars, columns=['timestamp','Open','High','Low','Close','Volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df.set_index('datetime').drop(columns=['timestamp'])


def save_csv(df: pd.DataFrame, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path)
    print(f"Zapisano: {path}")


def parse_args():
    p = argparse.ArgumentParser(description='Pobierz OHLCV i zapisz do bot_data/raw')
    p.add_argument('symbol', help="Para, np. 'BTC/USDT'.")
    p.add_argument('timeframe', help="Interwał, np. '1h', '4h', '1d'.")
    p.add_argument('--start', help="Data startu YYYY-MM-DD (domyślnie 2020-01-01).", default='2020-01-01')
    p.add_argument('--exchange', help="Nazwa CCXT (domyślnie 'binance').", default='binance')
    p.add_argument('--limit', type=int, help="Liczba świec na zapytanie (domyślnie 1000).", default=1000)
    return p.parse_args()


def main():
    args = parse_args()
    # Parsowanie daty startowej
    try:
        dt = datetime.strptime(args.start, '%Y-%m-%d')
        since = int(dt.timestamp() * 1000)
    except ValueError:
        print("Nieprawidłowy format daty. Użyj YYYY-MM-DD.")
        return

    exchange = init_exchange(args.exchange, enableRateLimit=True)
    print(f"Ładowanie {args.timeframe} danych dla {args.symbol} od {exchange.iso8601(since)}...")
    df = fetch_ohlcv(exchange, args.symbol, args.timeframe, since, args.limit)

    # Zapis do katalogu bot_data/raw w root projektu
    raw_dir = get_raw_dir()
    safe_symbol = args.symbol.replace('/', '_')
    filename = f"{safe_symbol}-{args.timeframe}.csv"
    out_path = os.path.join(raw_dir, filename)
    save_csv(df, out_path)

if __name__ == '__main__':
    main()

# This code is part of a cryptocurrency trading bot project.
# BGH approved