# scripts/connect_binance.py
import os

from binance.client import Client  # <— to jest właściwy import
from dotenv import load_dotenv

load_dotenv()  # wczyta .env z katalogu głównego, jeśli uruchamiasz z root projektu

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET_KEY")

if not API_KEY or not API_SECRET:
    raise RuntimeError("Brak kluczy w .env (BINANCE_API_KEY / BINANCE_SECRET_KEY).")

client = Client(
    API_KEY, API_SECRET, testnet=True
)  # Ustaw testnet na True, jeśli chcesz używać testnetu
info = client.get_account()

if __name__ == "__main__":
    print(client.get_symbol_ticker(symbol="BTCUSDT"))
    print(f"Info about binance testnet account: {info}")
