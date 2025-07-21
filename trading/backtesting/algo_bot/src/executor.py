#!/usr/bin/env python3
"""
executor.py – uniwersalny skrypt do uruchamiania backtestów i optymalizacji strategii w frameworku RBI.

Opis:
- Wczytuje konfigurację z config/config.yaml
- Pobiera przetworzone dane z bot_data/processed
- Dynamicznie ładuje wybraną strategię z katalogu strategies
- Uruchamia backtest (lub optymalizację, gdy użyto flagi)
- Zapisuje statystyki do katalogu results jako JSON i CSV

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

# Dodaj project root do sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, PROJECT_ROOT)

from src.data_loader import load_csv_ohlcv
from src.backtester import run_backtest, optimize_backtest


def parse_args():
    parser = argparse.ArgumentParser(
        description='Uruchom backtest lub optymalizację strategii w algo_bot'
    )
    parser.add_argument('--symbol','-s', required=True, help="Symbol formatu BTC_USDT")
    parser.add_argument('--timeframe','-t', required=True, help="Interwał np. 4h")
    parser.add_argument('--strategy','-st', required=True, help="Nazwa pliku strategii bez .py")
    parser.add_argument('--optimize','-o', action='store_true', help="Optymalizacja parametrów")
    parser.add_argument('--config','-c', default='config/config.yaml', help="Ścieżka do config.yaml")
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
    processed_dir = os.path.join(PROJECT_ROOT, cfg['data']['processed_dir'])
    csv_name = f"{args.symbol}-{args.timeframe}.csv"
    data_path = os.path.join(processed_dir, csv_name)
    if not os.path.exists(data_path):
        print(f"Brak przetworzonych danych: {data_path}")
        sys.exit(1)

    # Wczytaj dane
    df = load_csv_ohlcv(data_path)

    # Dynamiczny import strategii
    strategy_module = importlib.import_module(f"strategies.{args.strategy}")
    class_name = ''.join([w.title() for w in args.strategy.split('_')])
    StrategyClass = getattr(strategy_module, class_name)

    # Konfiguracja backtestu i strategii
    backtest_cfg = cfg.get('backtest', {})
    strat_cfg = cfg.get('strategies', {}).get(args.strategy, {})

    # Wykonaj backtest lub optymalizację
    if args.optimize:
        opt_kwargs = strat_cfg.get('optimize', {})
        result = optimize_backtest(
            data=df,
            strategy_cls=StrategyClass,
            optimize_kwargs=opt_kwargs,
            cash=backtest_cfg.get('cash', 100000),
            commission=backtest_cfg.get('commission', 0.002),
            maximize=opt_kwargs.get('maximize')
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
    results_dir = os.path.join(PROJECT_ROOT, 'results')
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base = f"{args.symbol}_{args.strategy}_{timestamp}"

    # Zapis JSON
    json_path = os.path.join(results_dir, f"{base}.json")
    with open(json_path, 'w') as f:
        f.write(result.to_json())
    print(f"Statystyki JSON zapisane: {json_path}")

    # Zapis equity curve CSV
    try:
        eq_path = os.path.join(results_dir, f"{base}_equity.csv")
        result._equity_curve.to_csv(eq_path)
        print(f"Equity curve zapisane: {eq_path}")
    except Exception:
        print("Błąd zapisu equity curve.")

    # Zapis logu transakcji CSV
    try:
        trades_path = os.path.join(results_dir, f"{base}_trades.csv")
        result._trades.to_csv(trades_path)
        print(f"Log transakcji zapisany: {trades_path}")
    except Exception:
        print("Błąd zapisu logu transakcji.")

if __name__ == '__main__':
    main()
