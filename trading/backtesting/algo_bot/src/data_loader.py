"""
src/data_loader.py – moduł do pobierania i wczytywania danych OHLCV.

Funkcjonalność:
- init_exchange: inicjalizacja giełdy CCXT
- fetch_ohlcv: pobieranie świec OHLCV jako DataFrame
- fetch_and_save_ohlcv: pobranie + zapis CSV do bot_data/raw
- batch_fetch_symbols: wsadowe pobieranie wielu symboli
- list_csv_files: lista plików CSV w katalogu
- load_csv_ohlcv: wczytywanie CSV jako DataFrame OHLCV
- load_all_csv_ohlcv: wsadowe wczytywanie CSV
- resample_ohlcv: zmiana interwału danych
"""
import os
import ccxt
import pandas as pd
import time
from typing import List, Dict, Optional, Any


def init_exchange(
    exchange_name: str,
    api_key: Optional[str] = None,
    secret: Optional[str] = None,
    **kwargs: Any
) -> ccxt.Exchange:
    """
    Inicjalizuje obiekt giełdy CCXT.

    Args:
        exchange_name: nazwa giełdy, np. 'binance', 'bybit'.
        api_key: klucz API.
        secret: sekret API.
        kwargs: dodatkowe parametry (timeout, rateLimit, itp.).

    Returns:
        Obiekt ccxt.Exchange.
    """
    if not hasattr(ccxt, exchange_name):
        raise ValueError(f"Nieznana giełda CCXT: {exchange_name}")
    exch_cls = getattr(ccxt, exchange_name)
    return exch_cls({ 'apiKey': api_key, 'secret': secret, **kwargs })


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str = '1h',
    since: Optional[int] = None,
    limit: int = 1000,
    params: Optional[Dict[str, Any]] = None
) -> pd.DataFrame:
    """
    Pobiera dane OHLCV z giełdy przez paginację.

    Args:
        exchange: instancja ccxt.Exchange.
        symbol: para, np. 'BTC/USDT'.
        timeframe: interwał, np. '1h', '4h'.
        since: timestamp w ms.
        limit: liczba świec na zapytanie.
        params: dodatkowe parametry.

    Returns:
        DataFrame z indeksem datetime i kolumnami Open, High, Low, Close, Volume.
    """
    all_bars = []
    while True:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit, params=params or {})
        if not bars:
            break
        all_bars.extend(bars)
        since = bars[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    df = pd.DataFrame(all_bars, columns=['timestamp','Open','High','Low','Close','Volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df.set_index('datetime').drop(columns=['timestamp'])


def fetch_and_save_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: Optional[int],
    limit: int,
    output_dir: str,
    filename: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None
) -> str:
    """
    Pobiera OHLCV i zapisuje surowe dane do CSV.

    Args:
        exchange: instancja CCXT.
        symbol: para.
        timeframe: interwał.
        since: timestamp startowy.
        limit: liczba świec per fetch.
        output_dir: katalog zapisu.
        filename: nazwa pliku (opcjonalnie).
        params: dodatkowe parametry.

    Returns:
        Scieżka do zapisanego pliku.
    """
    df = fetch_ohlcv(exchange, symbol, timeframe, since, limit, params)
    os.makedirs(output_dir, exist_ok=True)
    safe_symbol = symbol.replace('/', '_')
    fname = filename or f"{safe_symbol}-{timeframe}.csv"
    path = os.path.join(output_dir, fname)
    df.to_csv(path)
    return path


def batch_fetch_symbols(
    exchange: ccxt.Exchange,
    symbols: List[str],
    timeframe: str,
    since: Optional[int],
    limit: int,
    output_dir: str,
    params: Optional[Dict[str, Any]] = None
) -> Dict[str, str]:
    """
    Pobiera i zapisuje CSV dla listy symboli.

    Returns:
        Dict mapping symbol -> file path.
    """
    results: Dict[str, str] = {}
    for sym in symbols:
        try:
            path = fetch_and_save_ohlcv(exchange, sym, timeframe, since, limit, output_dir, None, params)
            results[sym] = path
        except Exception as e:
            print(f"Błąd pobierania {sym}: {e}")
    return results


def list_csv_files(directory: str) -> List[str]:
    """
    Zwraca listę ścieżek do plików CSV w katalogu.
    """
    return [
        os.path.join(directory, f) for f in os.listdir(directory)
        if f.lower().endswith('.csv')
    ]


def load_csv_ohlcv(
    path: str,
    datetime_col: str = 'datetime',
    cols_mapping: Optional[Dict[str, str]] = None
) -> pd.DataFrame:
    """
    Wczytuje CSV OHLCV jako DataFrame.

    Args:
        path: ścieżka do pliku.
        datetime_col: nazwa kolumny z datą.
        cols_mapping: mapowanie kolumn niestandardowych.

    Returns:
        DataFrame z kolumnami Open, High, Low, Close, Volume.
    """
    df = pd.read_csv(path, parse_dates=[datetime_col])
    df = df.rename(columns={datetime_col: 'datetime'})
    if cols_mapping:
        df = df.rename(columns=cols_mapping)
    df = df.set_index('datetime').sort_index()
    expected = ['Open','High','Low','Close','Volume']
    missing = set(expected) - set(df.columns)
    if missing:
        raise ValueError(f"Brakujące kolumny OHLCV {missing} w {path}")
    return df[expected]


def load_all_csv_ohlcv(
    directory: str,
    datetime_col: str = 'datetime',
    cols_mapping: Optional[Dict[str, str]] = None
) -> Dict[str, pd.DataFrame]:
    """
    Batchowo wczytuje wszystkie CSV w katalogu.

    Returns:
        Dict symbol -> DataFrame.
    """
    data: Dict[str, pd.DataFrame] = {}
    for path in list_csv_files(directory):
        symbol = os.path.splitext(os.path.basename(path))[0]
        data[symbol] = load_csv_ohlcv(path, datetime_col, cols_mapping)
    return data


def resample_ohlcv(
    df: pd.DataFrame,
    timeframe: str,
    how: Optional[Dict[str, str]] = None
) -> pd.DataFrame:
    """
    Resampluje dane OHLCV na inny interwał.

    Args:
        df: DataFrame index datetime.
        timeframe: np. '1H', '15T'.
        how: dict mapowania kolumn na agregacje.

    Returns:
        Zresamplowany DataFrame.
    """
    if how is None:
        how = {
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }
    df_r = df.resample(timeframe).agg(how)
    return df_r.dropna()
