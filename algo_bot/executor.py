#!/usr/bin/env python3
"""
executor.py – uniwersalny skrypt do uruchamiania backtestów i optymalizacji strategii w frameworku RBI.

Opis:
- Wczytuje konfigurację z config/config.yaml
- Pobiera przetworzone dane z bot_data/processed
- Dynamicznie ładuje wybraną strategię z katalogu strategies
- Uruchamia backtest (lub optymalizację, gdy użyto flagi)
- Zapisuje statystyki i dane pomocnicze do katalogu results

Użycie example:
    python3 -m algo_bot.executor --symbol BTC_USDT --timeframe 4h --strategy bollinger_band_breakout_short
    python3 -m algo_bot.executor -s ETH_USDT -t 1h -st simple_momentum -o

UWAGA — TODO dyskusja w fazie 1:
  Import 'from algo_bot.backtester' pozostaje broken (1:1 jak bylo przed flatten —
  poprzednio 'from src.backtester' tez nie istnialo, prawdziwy backtester to engine/backtester).
  Funkcja 'optimize_backtest' tez nie istnieje (logika opt. zyje w algo_bot.engine.sweep).
  Decyzja: czy ten plik deprecation i polegamy na algo_bot.engine.backtester:main(),
  czy migrujemy executor zeby uzywal engine/backtester + sweep zgodnie z nowa architektura.
"""
import os
import sys
import argparse
import importlib
import yaml
from datetime import datetime

# PROJECT_ROOT uzywany tylko do liczenia sciezek do data/results.
# sys.path hack usuniety: po `pip install -e .` algo_bot jest importowalne globalnie.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

from algo_bot.data_loader import load_csv_ohlcv
from algo_bot.backtester import run_backtest, optimize_backtest  # FIXME (decyzja faza 1): broken — patrz docstring


def parse_args():
    parser = argparse.ArgumentParser(
        description='Uruchom backtest lub optymalizację strategii w algo_bot'
    )
    parser.add_argument('--symbol', '-s', required=True, help="Symbol formatu BTC_USDT")
    parser.add_argument('--timeframe', '-t', required=True, help="Interwał np. 4h")
    parser.add_argument('--strategy', '-st', required=True, help="Nazwa pliku strategii bez .py")
    parser.add_argument('--optimize', '-o', action='store_true', help="Optymalizacja parametrów")
    parser.add_argument('--config', '-c', default='config/config.yaml', help="Ścieżka do config.yaml")
    return parser.parse_args()


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        print(f"Brak pliku konfiguracyjnego: {path}")
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    # Debug prints
    print(">>> Executor startuje <<<<")
    args = parse_args()
    print(f">>> Parsed args: symbol={args.symbol}, timeframe={args.timeframe}, strategy={args.strategy}, optimize={args.optimize}")
    cfg = load_config(args.config)

    # Debug strategies folder
    strategies_folder = os.path.join(PROJECT_ROOT, 'strategies')
    print(f">>> Strategies folder contents: {os.listdir(strategies_folder)}")

    # Ścieżki do danych
    processed_dir = os.path.join(PROJECT_ROOT, cfg['data']['processed_dir'])
    csv_name = f"{args.symbol}-{args.timeframe}.csv"
    data_path = os.path.join(processed_dir, csv_name)
    print(f">>> Looking for data at: {data_path}")
    if not os.path.exists(data_path):
        print(f"Brak przetworzonych danych: {data_path}")
        sys.exit(1)

    # Wczytaj dane
    df = load_csv_ohlcv(data_path)

    # Dynamiczny import strategii
    print(f">>> Importing strategy module: algo_bot.strategies.{args.strategy}")
    try:
        strategy_module = importlib.import_module(f"algo_bot.strategies.{args.strategy}")
    except Exception as e:
        print(f"!!! Błąd importu strategii: {e}")
        sys.exit(1)
    class_name = ''.join([w.title() for w in args.strategy.split('_')])
    print(f">>> Resolving class name: {class_name}")
    try:
        StrategyClass = getattr(strategy_module, class_name)
    except Exception as e:
        print(f"!!! Błąd pobierania klasy strategii: {e}")
        sys.exit(1)

    # Konfiguracja globalna i strategii
    backtest_cfg = cfg.get('backtest', {})
    strat_cfg = cfg.get('strategies', {}).get(args.strategy, {})

    # Uruchom test lub optymalizację
    if args.optimize:
        print(">>> Running optimize_backtest...")
        optimize_kwargs = strat_cfg.get('optimize', {})
        stats, all_results = optimize_backtest(
            data=df,
            strategy_cls=StrategyClass,
            optimize_kwargs=optimize_kwargs,
            cash=backtest_cfg.get('cash', 100000),
            commission=backtest_cfg.get('commission', 0.002)
        )
    else:
        print(">>> Running run_backtest...")
        run_kwargs = strat_cfg.get('run', {})
        stats = run_backtest(
            data=df,
            strategy_cls=StrategyClass,
            strategy_kwargs=run_kwargs,
            cash=backtest_cfg.get('cash', 100000),
            commission=backtest_cfg.get('commission', 0.002)
        )

    # Wyświetl statystyki
    print(stats)

    # Przygotuj katalog results
    results_dir = os.path.join(PROJECT_ROOT, 'results')
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base = f"{args.symbol}_{args.strategy}_{timestamp}"

    # Zapis JSON
    json_path = os.path.join(results_dir, f"{base}.json")
    with open(json_path, 'w') as f:
        f.write(stats.to_json())
    print(f"Statystyki JSON zapisane: {json_path}")

    # Zapis equity curve CSV
    try:
        eq_path = os.path.join(results_dir, f"{base}_equity.csv")
        stats._equity_curve.to_csv(eq_path)
        print(f"Equity curve zapisane: {eq_path}")
    except Exception:
        print("Błąd zapisu equity curve.")

    # Zapis logu transakcji CSV
    try:
        trades_path = os.path.join(results_dir, f"{base}_trades.csv")
        stats._trades.to_csv(trades_path)
        print(f"Log transakcji zapisany: {trades_path}")
    except Exception:
        print("Błąd zapisu logu transakcji.")

    # Zapis pełnej macierzy optymalizacji, jeśli była
    if args.optimize:
        opt_csv = os.path.join(results_dir, f"{base}_opt_results.csv")
        all_results.to_csv(opt_csv, index=False)
        print(f"Pełne wyniki optymalizacji zapisane: {opt_csv}")

if __name__ == '__main__':
    main()
