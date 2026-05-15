Algo Bot — RBI Framework

Repozytorium: https://github.com/JanekPlo/algo_bot

Lekki framework do badania, testowania i uruchamiania kryptowalutowych strategii tradingowych według modelu RBI (Research → Backtest → Implement).

📋 Spis treści

Opis projektu

Wymagania

Instalacja

Pobieranie danych

Przetwarzanie danych

Strategie

Backtester i Executor

Analiza wyników

Testy

Contributing

Opis projektu

Ten projekt zapewnia spójny szkielet do:

Pobierania historycznych danych OHLCV z giełd (Binance, Bybit) przez CCXT (src/fetch_data.py, src/data_loader.py).

Przetwarzania surowych danych, liczenia wskaźników technicznych i łączenia źródeł w jednorodne pliki CSV (src/process_data.py).

Backtestowania strategii (Backtesting.py) z możliwością optymalizacji (src/backtester.py, src/executor.py).

Tworzenia i przechowywania strategii w katalogu strategies/. Każdy plik to osobna klasa dziedzicząca po Strategy.

Analizy wyników w Jupyter Notebookach (notebooks/).

Wymagania

Python 3.8+

Zainstalowane dependencies z requirements.txt:

pip install -r requirements.txt

Instalacja

git clone git@github.com:JanekPlo/algo_bot.git
cd algo_bot
pip install -r requirements.txt

Pobieranie danych

Skrypt CLI do pobierania surowych danych BTC/USDT z Binance:

python3 src/fetch_data.py BTC/USDT 4h --start 2020-01-01

symbol: para rynkowa (BTC/USDT)

timeframe: interwał świecy (1h, 4h, 1d)

start: data początkowa (YYYY-MM-DD)

Surowe pliki zapisują się w bot_data/raw/.

Przetwarzanie danych

Standaryzacja, liczenie wskaźników (BBANDS, RSI) i zapis gotowych CSV do bot_data/processed/:

python3 src/process_data.py

Konfiguracja wskaźników w config/config.yaml (sekcja defaults.features).

Strategie

Katalog strategies/ zawiera pliki-strategie:

bollinger_band_breakout_short.py: przerwanie poniżej dolnego pasma Bollingera → short z TP/SL.

simple_momentum.py: klasyczne przecięcie dwóch średnich.

Każdy plik definiuje klasę dziedziczącą z backtesting.Strategy

Backtester i Executor

Backtester (src/backtester.py):

run_backtest(data, StrategyClass, ...) → pojedynczy test

optimize_backtest(data, StrategyClass, optimize_kwargs, ...) → optymalizacja parametrów

Executor (src/executor.py): uniwersalny CLI, łączy wszystkie kroki:

python3 -m src.executor \
  --symbol BTC_USDT \
  --timeframe 4h \
  --strategy bollinger_band_breakout_short

Flaga -o do optymalizacji.

Tworzy JSON (results/*.json), equity curve (results/*_equity.csv), log transakcji (results/*_trades.csv).

Analiza wyników

Notebooki w notebooks/:

01_data_exploration.ipynb

02_bollinger_analysis.ipynb

Uruchom Jupyter:

jupyter lab notebooks/02_bollinger_analysis.ipynb

Testy

Proste testy unit i integration w tests/:

pytest -q

Contributing

Fork repozytorium

Stwórz feature branch (git checkout -b feature/xyz)

Wprowadź zmiany, napisz testy

git push origin feature/xyz

Otwórz Pull Request na GitHubie

Autor: JanekPlo
Licencja: MIT

