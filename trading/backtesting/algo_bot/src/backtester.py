"""
backtester.py – moduł do uruchamiania backtestów i optymalizacji strategii z użyciem backtesting.py

Rola pliku w szkielecie:
- Zapewnia jednolity interfejs do uruchamiania pojedynczych backtestów (`run_backtest`).
- Umożliwia optymalizację parametrów strategii (`optimize_backtest`).
- Centralizuje konfigurację takich parametrów jak początkowy kapitał i prowizje.

Do rozwinięcia w przyszłości:
- **Batch backtests**: uruchamianie wielu strategii/parametrów w pętli, zapisywanie wyników.
- **Zapis statystyk**: automatyczny eksport `stats` do JSON/CSV w katalogu `results/`.
- **Integracja z RiskManager**: dynamiczne sizing pozycji.
- **Własne metryki**: obliczanie Sharpe, Sortino itp., poza wbudowanymi.
- **Równoległość**: multiprocess/async do przyspieszenia wielu backtestów.
- **Logging**: szczegółowe logi dla debugowania i audytu.
"""
import pandas as pd
from backtesting import Backtest
from typing import Type, Dict, Any, Optional


def run_backtest(
    data: pd.DataFrame,
    strategy_cls: Type,
    strategy_kwargs: Optional[Dict[str, Any]] = None,
    cash: float = 100_000,
    commission: float = 0.002
) -> Any:
    """
    Uruchamia pojedynczy backtest.

    Args:
        data (pd.DataFrame): OHLCV DataFrame z kolumnami ['Open','High','Low','Close','Volume'].
        strategy_cls: Klasa strategii dziedzicząca z backtesting.Strategy.
        strategy_kwargs (dict): Argumenty przekazywane do bt.run(), np. {'trade_on_close': True}.
        cash (float): Początkowy kapitał.
        commission (float): Procentowa prowizja od każdej transakcji.

    Returns:
        backtesting.lib.backtest.Result: Obiekt ze statystykami backtestu.
    """
    bt = Backtest(
        data,
        strategy_cls,
        cash=cash,
        commission=commission
    )
    # Uruchom z ewentualnymi dodatkowymi argumentami
    stats = bt.run(**(strategy_kwargs or {}))
    return stats


def optimize_backtest(
    data: pd.DataFrame,
    strategy_cls: Type,
    optimize_kwargs: Dict[str, Any],
    cash: float = 100_000,
    commission: float = 0.002,
    maximize: Optional[str] = None
) -> Any:
    """
    Przeprowadza optymalizację parametrów strategii.

    Args:
        data (pd.DataFrame): OHLCV DataFrame.
        strategy_cls: Klasa strategii.
        optimize_kwargs (dict): Parametry do przekazania do `.optimize()`, np. {
            'window': range(10, 50, 5),
            'stop_loss': [0.01, 0.02]
        }.
        cash (float): Początkowy kapitał.
        commission (float): Procentowa prowizja.
        maximize (str): Nazwa metryki do maksymalizacji, np. 'Equity Final [$]'.

    Returns:
        backtesting.lib.backtest.Result: Wyniki optymalizacji z najlepszymi parametrami.
    """
    bt = Backtest(
        data,
        strategy_cls,
        cash=cash,
        commission=commission
    )
    # Przekaż nazwę metryki do maximize, jeżeli podano
    if maximize:
        result = bt.optimize(
            maximize=maximize,
            **optimize_kwargs
        )
    else:
        result = bt.optimize(
            **optimize_kwargs
        )
    return result
