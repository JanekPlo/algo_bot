from __future__ import annotations

from typing import Any

import pytest

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


class _DescendingBybitFundingFixture:
    """Emuluje V5: endpoint wybiera najnowsze rekordy i zwraca je malejąco."""

    def __init__(self, timestamps: list[int]) -> None:
        self.rateLimit = 0
        self.timestamps = timestamps
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
        lower = since if since is not None else min(self.timestamps)
        upper = frozen_params["endTime"]
        page = sorted(
            (timestamp for timestamp in self.timestamps if lower <= timestamp <= upper),
            reverse=True,
        )[:limit]
        return [
            {"timestamp": timestamp, "fundingRate": f"{timestamp / 1_000_000:.8f}"}
            for timestamp in page
        ]


def _timestamps_ms(rows: list[dict[str, Any]]) -> list[int]:
    return [int(row["datetime"].value // 1_000_000) for row in rows]


def test_bybit_funding_paginates_backward_across_more_than_200_descending_rows() -> None:
    exchange = _DescendingBybitFundingFixture(list(range(100, 550)))

    rows = _fetch_funding_bybit(exchange, "ETH/USDT", since=100, end_ms=549)  # type: ignore[arg-type]

    assert _timestamps_ms(rows) == list(range(100, 550))
    assert exchange.calls == [
        ("ETH/USDT:USDT", 100, 200, {"endTime": 549}),
        ("ETH/USDT:USDT", 100, 200, {"endTime": 349}),
        ("ETH/USDT:USDT", 100, 200, {"endTime": 149}),
    ]


@pytest.mark.parametrize(
    ("since", "end_ms", "expected"),
    [
        (100, 300, list(range(100, 301))),
        (200, 200, [200]),
    ],
)
def test_bybit_funding_enforces_inclusive_start_and_end_bounds(
    since: int,
    end_ms: int,
    expected: list[int],
) -> None:
    exchange = _DescendingBybitFundingFixture(list(range(401)))

    rows = _fetch_funding_bybit(exchange, "ETH/USDT", since=since, end_ms=end_ms)  # type: ignore[arg-type]

    assert _timestamps_ms(rows) == expected


class _DuplicateBybitFundingFixture:
    rateLimit = 0

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        del symbol, since, limit, params
        return [
            {"timestamp": 200, "fundingRate": "0.0001"},
            {"timestamp": 200, "fundingRate": "0.0002"},
            {"timestamp": 100, "fundingRate": "0.0003"},
        ]


def test_bybit_funding_rejects_duplicate_settlement_instead_of_silently_deduplicating() -> None:
    exchange = _DuplicateBybitFundingFixture()

    with pytest.raises(RuntimeError, match=r"duplicate|Duplicate"):
        _fetch_funding_bybit(exchange, "ETH/USDT", since=100, end_ms=200)  # type: ignore[arg-type]


class _StalledBybitFundingFixture:
    rateLimit = 0

    def __init__(self) -> None:
        self.calls = 0

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        del symbol, since, params
        self.calls += 1
        return [
            {"timestamp": timestamp, "fundingRate": "0.0001"}
            for timestamp in range(500, 500 - int(limit or 200), -1)
        ]


def test_bybit_funding_rejects_page_which_ignores_decreasing_end_time() -> None:
    exchange = _StalledBybitFundingFixture()

    with pytest.raises(RuntimeError, match=r"boundary|progress|stalled|endTime"):
        _fetch_funding_bybit(exchange, "ETH/USDT", since=100, end_ms=500)  # type: ignore[arg-type]

    assert exchange.calls <= 2


class _IncompleteBybitFundingFixture:
    rateLimit = 0

    def __init__(self) -> None:
        self.calls = 0

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        del symbol, since, params
        self.calls += 1
        if self.calls > 1:
            return []
        return [
            {"timestamp": timestamp, "fundingRate": "0.0001"}
            for timestamp in range(500, 500 - int(limit or 200), -1)
        ]


def test_bybit_funding_rejects_incomplete_history_before_requested_start() -> None:
    exchange = _IncompleteBybitFundingFixture()

    with pytest.raises(RuntimeError, match=r"coverage|incomplete|requested start"):
        _fetch_funding_bybit(exchange, "ETH/USDT", since=100, end_ms=500)  # type: ignore[arg-type]
