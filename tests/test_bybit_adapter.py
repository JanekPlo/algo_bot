"""tests/test_bybit_adapter.py

Testy dla algo_bot.engine.exchanges.bybit_adapter (ADR-015).

Dwie warstwy, zgodnie z preferencją "bez mocków w integration value":
1. Pure unit (zawsze w make check): mapowanie symbolu unified → linear
   settle-suffix. Deterministyczne, bez sieci/kluczy.
2. Live smoke (opt-in): realne połączenie z Bybit TESTNET — load_markets,
   fetch_ticker, min amount. Skipuje się gdy brak kluczy testnet w .env lub
   gdy ALGO_BOT_RUN_LIVE_TESTS != 1 (CI / make check nigdy nie zależą od
   sieci ani kredencjałów). Zero mocków.
"""

from __future__ import annotations

import os

import pytest

from algo_bot.engine.exchanges.bybit_adapter import to_market_symbol


# --- Warstwa 1: pure unit (zawsze) ---
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BTC/USDT", "BTC/USDT:USDT"),
        ("ETH/USDT", "ETH/USDT:USDT"),
        ("BTC/USDT:USDT", "BTC/USDT:USDT"),  # idempotentne
        ("BTCUSDT", "BTC/USDT:USDT"),  # bez slasha
    ],
)
def test_to_market_symbol(raw: str, expected: str) -> None:
    assert to_market_symbol(raw) == expected


# --- Warstwa 2: live smoke na Bybit TESTNET (opt-in, bez mocków) ---
@pytest.mark.live
@pytest.mark.integration
def test_bybit_testnet_smoke_when_enabled() -> None:
    """Realny sanity connect do Bybit testnet. Skip gdy brak kluczy / flagi."""
    if os.getenv("ALGO_BOT_RUN_LIVE_TESTS") != "1":
        pytest.skip("set ALGO_BOT_RUN_LIVE_TESTS=1 to run live Bybit testnet smoke test")

    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("BYBIT_API_KEY_TESTNET", "").strip()
    sec = os.getenv("BYBIT_API_SECRET_TESTNET", "").strip()
    if not key or not sec:
        pytest.skip("brak BYBIT_API_KEY_TESTNET / BYBIT_API_SECRET_TESTNET w .env")

    from algo_bot.engine.exchanges.bybit_adapter import BybitFuturesAdapter

    adapter = BybitFuturesAdapter(key, sec, testnet=True)
    assert adapter.exchange.markets, "load_markets zwrócił pusty zbiór"

    last = adapter.fetch_ticker_last("BTC/USDT")
    assert last > 0.0, "ticker BTC/USDT:USDT powinien być dodatni"

    limits = adapter.market_limits("BTC/USDT")
    min_amt = (limits.get("amount") or {}).get("min")
    assert min_amt is not None and float(min_amt) > 0.0
