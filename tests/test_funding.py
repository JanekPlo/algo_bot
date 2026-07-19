from __future__ import annotations

from typing import Any

from algo_bot.funding import _fetch_funding_bybit


class _BybitFundingFixture:
    def __init__(self) -> None:
        self.rateLimit = 0
        self.calls: list[tuple[str, int | None, int | None, dict[str, int]]] = []

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        frozen_params = dict(params or {})
        self.calls.append((symbol, since, limit, frozen_params))
        return [
            {"timestamp": 100, "fundingRate": "0.0001"},
            {"timestamp": 200, "fundingRate": "-0.0002"},
        ]


def test_bybit_funding_fetch_pushes_reserved_boundary_to_server() -> None:
    exchange = _BybitFundingFixture()

    rows = _fetch_funding_bybit(exchange, "ETH/USDT", since=100, end_ms=200)  # type: ignore[arg-type]

    assert [row["datetime"].value // 1_000_000 for row in rows] == [100, 200]
    assert exchange.calls == [
        ("ETH/USDT:USDT", 100, 200, {"endTime": 200}),
    ]
