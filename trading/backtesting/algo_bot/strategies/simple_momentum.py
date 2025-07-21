#!/usr/bin/env python3
"""
executor.py – uniwersalny skrypt do uruchamiania backtestów i optymalizacji strategii w frameworku RBI.

Opis:
- Wczytuje konfigurację z config/config.yaml
- Pobiera przetworzone dane z bot_data/processed
- Dynamicznie ładuje wybraną strategię z katalogu strategies
- Uruchamia backtest (lub optymalizację, gdy użyto flagi)
- Zapisuje statystyki do katalogu results jako JSON, equity curve i log transakcji

Użycie example:
    python3 -m src.executor --symbol BTC_USDT --timeframe 4h --strategy bollinger_band_breakout_short
    python3 -m src.executor -s ETH_USDT -t 1h -st simple_momentum -o
"""
import os
import sys
import argparse
import importlib
import yaml
from datetime import datetime

from src.data_loader import load_csv_ohlcv
from src.backtester import run_backtest, optimize_backtest


def parse_args():
    parser = argparse.ArgumentParser(
        description='Uruchom backtest lub optymalizację strategii w algo_bot'
    )
    parser.add_argument(
        '--symbol', '-s', required=True,
        help="Symbol formatu SYMBOL_BASE, np. BTC_USDT"
    )
    parser.add_argument(
        '--timeframe', '-t', required=True,
        help="Interwał świec, np. 1h, 4h, 1d"
    )
    parser.add_argument(
        '--strategy', '-st', required=True,
        help="Nazwa pliku strategii (bez .py), np. bollinger_band_breakout_short"
    )
    parser.add_argument(
        '--optimize', '-o', action='store_true',
        help="Flaga: uruchom optymalizację parametrów"
    )
    parser.add_argument(
        '--config', '-c', default='config/config.yaml',
        help="Ścieżka do pliku konfiguracyjnego"
    )
    return parser.parse_args()


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        print(f"Brak pliku konfiguracyjnego: {path}")
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    args = parse_args()
    cfg = load_config(args.config)

    # Ścieżki do danych
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    processed_dir = os.path.join(project_root, cfg['data']['processed_dir'])
    csv_name = f"{args.symbol}-{args.timeframe}.csv"
    data_path = os.path.join(processed_dir, csv_name)
    if not os.path.exists(data_path):
        print(f"Brak pliku przetworzonego: {data_path}")
        sys.exit(1)

    # Wczytaj dane do DataFrame
    df = load_csv_ohlcv(data_path)

    # Dynamiczne ładowanie strategii
    strategy_module = importlib.import_module(f"strategies.{args.strategy}")
    class_name = ''.join([w.title() for w in args.strategy.split('_')])
    StrategyClass = getattr(strategy_module, class_name)

    # Parametry z config.yaml
    backtest_cfg = cfg.get('backtest', {})
    strat_cfg = cfg.get('strategies', {}).get(args.strategy, {})

    # Uruchom backtest lub optymalizację
    if args.optimize:
        optimize_kwargs = strat_cfg.get('optimize', {})
        result = optimize_backtest(
            data=df,
            strategy_cls=StrategyClass,
            optimize_kwargs=optimize_kwargs,
            cash=backtest_cfg.get('cash', 100000),
            commission=backtest_cfg.get('commission', 0.002),
            maximize=optimize_kwargs.get('maximize')
        )
    else:
        run_kwargs = strat_cfg.get('run', {})
        result = run_backtest(
            data=df,
            strategy_cls=StrategyClass,
            strategy_kwargs=run_kwargs,
            cash=backtest_cfg.get('cash', 100000),
            commission=backtest_cfg.get('commission', 0.002)
        )

    # Wyświetl statystyki
    print(result)

    # Przygotuj katalog results
    results_dir = os.path.join(project_root, 'results')
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_fname = f"{args.symbol}_{args.strategy}_{timestamp}"

    # Zapis wyników JSON
    json_path = os.path.join(results_dir, f"{base_fname}.json")
    with open(json_path, 'w') as f:
        f.write(result.to_json())
    print(f"Wyniki zapisane do JSON: {json_path}")

    # Zapis equity curve do CSV
    eq_path = os.path.join(results_dir, f"{base_fname}_equity.csv")
    try:
        result._equity_curve.to_csv(eq_path)
        print(f"Krzywa kapitału zapisana do: {eq_path}")
    except Exception:
        print("Nie udało się zapisać krzywej kapitału.")

    # Zapis logu transakcji do CSV
    trades_path = os.path.join(results_dir, f"{base_fname}_trades.csv")
    try:
        result._trades.to_csv(trades_path)
        print(f"Log transakcji zapisany do: {trades_path}")
    except Exception:
        print("Nie udało się zapisać logu transakcji.")

if __name__ == '__main__':
    main()
