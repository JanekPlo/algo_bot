from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from algo_bot.engine.backtest_result import json_hash
from algo_bot.engine.mr_session4_bybit import (
    INSTRUMENTS_ENDPOINT,
    RISK_LIMIT_ENDPOINT,
    RISK_LIMIT_NORMALIZATION_PROFILE,
    BybitContractError,
    freeze_bybit_contracts,
    load_frozen_bybit_contracts,
)
from algo_bot.engine.mr_session4_data import SYMBOL_SPECS


def _response(rows: list[dict[str, object]], cursor: str = "") -> dict[str, object]:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {"category": "linear", "list": rows, "nextPageCursor": cursor},
        "retExtInfo": {},
        "time": 1_700_000_000_000,
    }


def _instrument_row(symbol: str) -> dict[str, object]:
    spec = SYMBOL_SPECS[symbol]
    return {
        "symbol": symbol,
        "baseCoin": spec.base_currency,
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "status": "Trading",
        "contractType": "LinearPerpetual",
        "fundingInterval": 480,
        "priceFilter": {"tickSize": str(spec.price_increment)},
        "lotSizeFilter": {
            "qtyStep": str(spec.size_increment),
            "minOrderQty": str(spec.min_quantity),
            "minNotionalValue": str(spec.min_notional),
        },
    }


def _fetcher(calls: list[tuple[str, dict[str, str]]]):
    def fetch(endpoint: str, params: Mapping[str, str]) -> Mapping[str, object]:
        request = dict(params)
        calls.append((endpoint, request))
        symbol = request["symbol"]
        if endpoint == INSTRUMENTS_ENDPOINT:
            return _response([_instrument_row(symbol)])
        assert endpoint == RISK_LIMIT_ENDPOINT
        if "cursor" not in request:
            return _response(
                [
                    {
                        "id": 1,
                        "symbol": symbol,
                        "riskLimitValue": "1000000",
                        "maintenanceMargin": "0.5",
                        "initialMargin": "1",
                        "maxLeverage": "100",
                        "mmDeduction": "0",
                    }
                ],
                cursor=f"next-{symbol}",
            )
        assert request["cursor"] == f"next-{symbol}"
        return _response(
            [
                {
                    "id": 2,
                    "symbol": symbol,
                    "riskLimitValue": "2000000",
                    "maintenanceMargin": "1",
                    "initialMargin": "2",
                    "maxLeverage": "50",
                    "mmDeduction": "5000",
                }
            ]
        )

    return fetch


def test_freeze_preserves_raw_pages_and_loader_rederives_normalized(tmp_path: Path) -> None:
    path = tmp_path / "contracts.json"
    calls: list[tuple[str, dict[str, str]]] = []
    frozen = freeze_bybit_contracts(
        ["BTCUSDT", "ETHUSDT"],
        path,
        captured_at=datetime(2026, 7, 15, 8, tzinfo=UTC),
        fetch_page=_fetcher(calls),
    )

    assert path.exists()
    assert len(frozen.contract_hash) == 64
    assert tuple(frozen.symbols) == ("BTCUSDT", "ETHUSDT")
    assert len(frozen.symbols["BTCUSDT"].maintenance_margin_tiers) == 2
    assert tuple(
        tier.maintenance_margin_rate for tier in frozen.symbols["BTCUSDT"].maintenance_margin_tiers
    ) == (0.005, 0.01)
    assert frozen.symbols["ETHUSDT"].instrument.tick_size == SYMBOL_SPECS["ETHUSDT"].price_increment
    assert frozen.symbols["BTCUSDT"].instrument.funding_interval_minutes == 480
    assert frozen.raw_document["risk_limit_normalization"] == {
        "profile": RISK_LIMIT_NORMALIZATION_PROFILE,
        "maintenanceMargin_input_unit": "percentage_points",
        "maintenance_margin_rate_output_unit": "fraction",
        "percentage_points_divisor": "100",
    }
    raw_on_disk = json.loads(path.read_text(encoding="utf-8"))
    btc_raw = raw_on_disk["symbols"]["BTCUSDT"]
    assert btc_raw["risk_limit_pages"][0]["result"]["list"][0]["maintenanceMargin"] == "0.5"
    assert btc_raw["normalized_maintenance_margin_tiers"][0]["maintenance_margin_rate"] == 0.005
    assert sum(endpoint == RISK_LIMIT_ENDPOINT for endpoint, _ in calls) == 4
    restored = load_frozen_bybit_contracts(path)
    assert restored.contract_hash == frozen.contract_hash
    assert restored.raw_document == frozen.raw_document
    assert restored.symbols["BTCUSDT"].maintenance_margin_tiers[0].maintenance_margin_rate == 0.005


def test_loader_rejects_any_tampering(tmp_path: Path) -> None:
    path = tmp_path / "contracts.json"
    freeze_bybit_contracts(
        ["BTCUSDT", "ETHUSDT"],
        path,
        captured_at=datetime(2026, 7, 15, 8, tzinfo=UTC),
        fetch_page=_fetcher([]),
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["symbols"]["BTCUSDT"]["risk_limit_pages"][0]["time"] = 42
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(BybitContractError, match="hash mismatch"):
        load_frozen_bybit_contracts(path)


def test_loader_rejects_normalization_drift_even_after_rehash(tmp_path: Path) -> None:
    path = tmp_path / "contracts.json"
    freeze_bybit_contracts(
        ["BTCUSDT", "ETHUSDT"],
        path,
        captured_at=datetime(2026, 7, 15, 8, tzinfo=UTC),
        fetch_page=_fetcher([]),
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["risk_limit_normalization"]["percentage_points_divisor"] = "10000"
    core = {key: value for key, value in raw.items() if key != "contract_hash"}
    raw["contract_hash"] = json_hash(core)
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(BybitContractError, match="normalization provenance drift"):
        load_frozen_bybit_contracts(path)


def test_freeze_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "contracts.json"
    path.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(BybitContractError, match="refusing to overwrite"):
        freeze_bybit_contracts(
            ["BTCUSDT", "ETHUSDT"],
            path,
            fetch_page=_fetcher([]),
        )


def test_freeze_rejects_repeated_cursor(tmp_path: Path) -> None:
    def repeated(endpoint: str, params: Mapping[str, str]) -> Mapping[str, object]:
        symbol = params["symbol"]
        if endpoint == INSTRUMENTS_ENDPOINT:
            return _response([_instrument_row(symbol)])
        return _response(
            [
                {
                    "symbol": symbol,
                    "riskLimitValue": "1000000",
                    "maintenanceMargin": "0.5",
                    "mmDeduction": "0",
                }
            ],
            cursor="same",
        )

    with pytest.raises(BybitContractError, match="repeated pagination cursor"):
        freeze_bybit_contracts(
            ["BTCUSDT", "ETHUSDT"],
            tmp_path / "contracts.json",
            fetch_page=repeated,
        )


def test_freeze_rejects_contract_funding_interval_drift(tmp_path: Path) -> None:
    def wrong_interval(endpoint: str, params: Mapping[str, str]) -> Mapping[str, object]:
        symbol = params["symbol"]
        if endpoint == INSTRUMENTS_ENDPOINT:
            row = _instrument_row(symbol)
            row["fundingInterval"] = 240
            return _response([row])
        return _response(
            [
                {
                    "symbol": symbol,
                    "riskLimitValue": "1000000",
                    "maintenanceMargin": "0.5",
                    "mmDeduction": "0",
                }
            ]
        )

    with pytest.raises(BybitContractError, match="funding_interval_minutes differs"):
        freeze_bybit_contracts(
            ["BTCUSDT", "ETHUSDT"],
            tmp_path / "contracts.json",
            fetch_page=wrong_interval,
        )
