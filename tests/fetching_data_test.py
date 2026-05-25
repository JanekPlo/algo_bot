from __future__ import annotations

import os

import pytest


@pytest.mark.live
def test_binance_api_reachable_when_live_tests_enabled() -> None:
    """
    Optional live smoke test. Disabled by default so CI and local `make check`
    never depend on Binance availability, network access, or API credentials.
    """
    if os.getenv("ALGO_BOT_RUN_LIVE_TESTS") != "1":
        pytest.skip("set ALGO_BOT_RUN_LIVE_TESTS=1 to run live Binance API smoke test")

    import ccxt

    exchange = ccxt.binance()
    exchange.load_markets()

    assert exchange.markets
