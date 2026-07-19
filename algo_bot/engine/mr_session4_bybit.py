"""Zamrażanie i weryfikacja publicznych kontraktów Bybit dla Session 4."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from algo_bot.engine.backtest_result import JsonValue, canonical_json, json_hash, normalize_json
from algo_bot.engine.mr_session4_data import FUNDING_INTERVAL, SYMBOL_SPECS
from algo_bot.microstructure import (
    BYBIT_V5_MAINTENANCE_MARGIN_NORMALIZATION_PROFILE,
    BYBIT_V5_MAINTENANCE_MARGIN_UNIT,
    MaintenanceMarginTier,
    maintenance_margin_tiers_from_bybit,
)

BYBIT_CONTRACT_SCHEMA_VERSION = "mr_session4_bybit_contracts/3"
BYBIT_MAINNET_PUBLIC_BASE_URL = "https://api.bybit.com"
INSTRUMENTS_ENDPOINT = "/v5/market/instruments-info"
RISK_LIMIT_ENDPOINT = "/v5/market/risk-limit"
RISK_LIMIT_NORMALIZATION_PROFILE = BYBIT_V5_MAINTENANCE_MARGIN_NORMALIZATION_PROFILE
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_PAGES = 100

type JsonObject = dict[str, object]
type PageFetcher = Callable[[str, Mapping[str, str]], Mapping[str, object]]


class BybitContractError(ValueError):
    """Publiczny kontrakt Bybit jest niepełny, zmieniony albo uszkodzony."""


@dataclass(frozen=True, slots=True)
class BybitInstrumentContract:
    """Minimalne parametry liniowego perpetual USDT wymagane przez runner."""

    symbol: str
    base_coin: str
    quote_coin: str
    settle_coin: str
    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal
    min_notional_value: Decimal
    status: str
    contract_type: str
    funding_interval_minutes: int

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "symbol": self.symbol,
            "base_coin": self.base_coin,
            "quote_coin": self.quote_coin,
            "settle_coin": self.settle_coin,
            "tick_size": str(self.tick_size),
            "qty_step": str(self.qty_step),
            "min_order_qty": str(self.min_order_qty),
            "min_notional_value": str(self.min_notional_value),
            "status": self.status,
            "contract_type": self.contract_type,
            "funding_interval_minutes": self.funding_interval_minutes,
        }


@dataclass(frozen=True, slots=True)
class BybitSymbolContract:
    """Zweryfikowany instrument wraz z pełną drabiną maintenance margin."""

    instrument: BybitInstrumentContract
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...]


@dataclass(frozen=True, slots=True)
class FrozenBybitContracts:
    """Widok offline artefaktu zamrożonego przed prerejestracją."""

    captured_at_utc: str
    contract_hash: str
    symbols: Mapping[str, BybitSymbolContract]
    raw_document: Mapping[str, object]


def freeze_bybit_contracts(
    symbols: Sequence[str],
    output_path: Path,
    *,
    captured_at: datetime | None = None,
    fetch_page: PageFetcher | None = None,
) -> FrozenBybitContracts:
    """Pobiera publiczny mainnet, zachowuje raw pages i atomowo zamraża dokument."""

    normalized_symbols = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
    if normalized_symbols != tuple(SYMBOL_SPECS):
        raise BybitContractError(
            f"Session 4 requires symbols in frozen order {tuple(SYMBOL_SPECS)!r}"
        )
    if output_path.exists():
        raise BybitContractError(f"refusing to overwrite frozen contracts {output_path}")
    capture_time = captured_at or datetime.now(UTC)
    _require_utc(capture_time, "captured_at")
    fetcher = fetch_page or _public_get

    symbol_documents: dict[str, JsonValue] = {}
    for symbol in normalized_symbols:
        instrument_pages = _fetch_all_pages(
            INSTRUMENTS_ENDPOINT,
            {"category": "linear", "symbol": symbol, "limit": "1000"},
            fetcher,
        )
        risk_pages = _fetch_all_pages(
            RISK_LIMIT_ENDPOINT,
            {"category": "linear", "symbol": symbol},
            fetcher,
        )
        instrument_rows = _rows_from_pages(instrument_pages, symbol=symbol)
        risk_rows = _rows_from_pages(risk_pages, symbol=symbol)
        if len(instrument_rows) != 1:
            raise BybitContractError(
                f"{symbol} instruments-info returned {len(instrument_rows)} exact rows"
            )
        instrument = _normalize_instrument(instrument_rows[0])
        tiers = maintenance_margin_tiers_from_bybit(risk_rows)
        _validate_symbol_contract(instrument, tiers)
        symbol_documents[symbol] = {
            "instrument_requests": [normalize_json(page["request"]) for page in instrument_pages],
            "instrument_pages": [normalize_json(page["response"]) for page in instrument_pages],
            "risk_limit_requests": [normalize_json(page["request"]) for page in risk_pages],
            "risk_limit_pages": [normalize_json(page["response"]) for page in risk_pages],
            "normalized_instrument": instrument.as_dict(),
            "normalized_maintenance_margin_tiers": [_tier_as_dict(tier) for tier in tiers],
            "terminal_instrument_cursor": _terminal_cursor(instrument_pages),
            "terminal_risk_limit_cursor": _terminal_cursor(risk_pages),
        }

    core: dict[str, JsonValue] = {
        "schema_version": BYBIT_CONTRACT_SCHEMA_VERSION,
        "captured_at_utc": capture_time.isoformat().replace("+00:00", "Z"),
        "environment": "mainnet_public",
        "base_url": BYBIT_MAINNET_PUBLIC_BASE_URL,
        "category": "linear",
        "endpoints": {
            "instruments_info": INSTRUMENTS_ENDPOINT,
            "risk_limit": RISK_LIMIT_ENDPOINT,
        },
        "risk_limit_normalization": {
            "profile": RISK_LIMIT_NORMALIZATION_PROFILE,
            "maintenanceMargin_input_unit": BYBIT_V5_MAINTENANCE_MARGIN_UNIT,
            "maintenance_margin_rate_output_unit": "fraction",
            "percentage_points_divisor": "100",
        },
        "symbols": symbol_documents,
    }
    document = {**core, "contract_hash": json_hash(core)}
    _atomic_write_json(output_path, document)
    return load_frozen_bybit_contracts(output_path)


def load_frozen_bybit_contracts(path: Path) -> FrozenBybitContracts:
    """Ładuje dokument całkowicie offline i odtwarza normalized z raw responses."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BybitContractError(f"cannot read frozen Bybit contracts: {exc}") from exc
    if not isinstance(raw, dict):
        raise BybitContractError("frozen Bybit contracts must be a JSON object")
    document = cast(dict[str, object], raw)
    expected_hash = _required_str(document, "contract_hash")
    core = {key: value for key, value in document.items() if key != "contract_hash"}
    if json_hash(core) != expected_hash:
        raise BybitContractError("frozen Bybit contract hash mismatch")
    expected_header = {
        "schema_version": BYBIT_CONTRACT_SCHEMA_VERSION,
        "environment": "mainnet_public",
        "base_url": BYBIT_MAINNET_PUBLIC_BASE_URL,
        "category": "linear",
    }
    for field, expected in expected_header.items():
        if document.get(field) != expected:
            raise BybitContractError(f"Bybit contract {field} drift")
    _parse_utc(_required_str(document, "captured_at_utc"), "captured_at_utc")
    endpoints = _required_mapping(document, "endpoints")
    if endpoints != {
        "instruments_info": INSTRUMENTS_ENDPOINT,
        "risk_limit": RISK_LIMIT_ENDPOINT,
    }:
        raise BybitContractError("Bybit endpoint provenance drift")
    normalization = _required_mapping(document, "risk_limit_normalization")
    if normalization != {
        "profile": RISK_LIMIT_NORMALIZATION_PROFILE,
        "maintenanceMargin_input_unit": BYBIT_V5_MAINTENANCE_MARGIN_UNIT,
        "maintenance_margin_rate_output_unit": "fraction",
        "percentage_points_divisor": "100",
    }:
        raise BybitContractError("Bybit risk-limit normalization provenance drift")
    symbols_raw = _required_mapping(document, "symbols")
    if tuple(symbols_raw) != tuple(SYMBOL_SPECS):
        raise BybitContractError("Bybit contract symbol set/order drift")

    restored: dict[str, BybitSymbolContract] = {}
    for symbol in SYMBOL_SPECS:
        symbol_raw = _mapping_value(symbols_raw, symbol)
        instrument_pages = _restore_page_pairs(
            symbol_raw,
            requests_field="instrument_requests",
            responses_field="instrument_pages",
            endpoint=INSTRUMENTS_ENDPOINT,
            symbol=symbol,
        )
        risk_pages = _restore_page_pairs(
            symbol_raw,
            requests_field="risk_limit_requests",
            responses_field="risk_limit_pages",
            endpoint=RISK_LIMIT_ENDPOINT,
            symbol=symbol,
        )
        if _terminal_cursor(instrument_pages) != "" or _terminal_cursor(risk_pages) != "":
            raise BybitContractError(f"{symbol} pagination did not terminate")
        if (
            symbol_raw.get("terminal_instrument_cursor") != ""
            or symbol_raw.get("terminal_risk_limit_cursor") != ""
        ):
            raise BybitContractError(f"{symbol} stored terminal cursor is not empty")
        instrument_rows = _rows_from_pages(instrument_pages, symbol=symbol)
        risk_rows = _rows_from_pages(risk_pages, symbol=symbol)
        if len(instrument_rows) != 1:
            raise BybitContractError(f"{symbol} must have one instrument row")
        instrument = _normalize_instrument(instrument_rows[0])
        tiers = maintenance_margin_tiers_from_bybit(risk_rows)
        _validate_symbol_contract(instrument, tiers)
        if normalize_json(symbol_raw.get("normalized_instrument")) != instrument.as_dict():
            raise BybitContractError(f"{symbol} normalized instrument differs from raw")
        expected_tiers = normalize_json([_tier_as_dict(tier) for tier in tiers])
        if normalize_json(symbol_raw.get("normalized_maintenance_margin_tiers")) != expected_tiers:
            raise BybitContractError(f"{symbol} normalized tiers differ from raw")
        restored[symbol] = BybitSymbolContract(instrument, tiers)

    return FrozenBybitContracts(
        captured_at_utc=_required_str(document, "captured_at_utc"),
        contract_hash=expected_hash,
        symbols=restored,
        raw_document=document,
    )


def _fetch_all_pages(
    endpoint: str,
    base_params: Mapping[str, str],
    fetch_page: PageFetcher,
) -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for _ in range(MAX_PAGES):
        request = dict(base_params)
        if cursor:
            request["cursor"] = cursor
        response = dict(fetch_page(endpoint, request))
        _validate_response(response, endpoint)
        pages.append({"request": request, "response": response})
        next_cursor = _response_cursor(response)
        if not next_cursor:
            return pages
        if next_cursor in seen_cursors:
            raise BybitContractError(f"{endpoint} repeated pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise BybitContractError(f"{endpoint} exceeded {MAX_PAGES} pages")


def _public_get(endpoint: str, params: Mapping[str, str]) -> Mapping[str, object]:
    query = urllib.parse.urlencode(sorted(params.items()))
    request = urllib.request.Request(
        f"{BYBIT_MAINNET_PUBLIC_BASE_URL}{endpoint}?{query}",
        headers={"Accept": "application/json", "User-Agent": "algo-bot-mr-session4/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise BybitContractError(f"Bybit public request failed for {endpoint}: {exc}") from exc
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BybitContractError(f"Bybit returned invalid JSON for {endpoint}") from exc
    if not isinstance(decoded, dict):
        raise BybitContractError(f"Bybit returned non-object JSON for {endpoint}")
    return cast(dict[str, object], decoded)


def _restore_page_pairs(
    raw: Mapping[str, object],
    *,
    requests_field: str,
    responses_field: str,
    endpoint: str,
    symbol: str,
) -> list[dict[str, object]]:
    requests = _required_list(raw, requests_field)
    responses = _required_list(raw, responses_field)
    if not requests or len(requests) != len(responses):
        raise BybitContractError(f"{symbol} {endpoint} page/request count mismatch")
    result: list[dict[str, object]] = []
    expected_cursor = ""
    for request_raw, response_raw in zip(requests, responses, strict=True):
        if not isinstance(request_raw, dict) or not isinstance(response_raw, dict):
            raise BybitContractError(f"{symbol} {endpoint} pages must be objects")
        request = cast(dict[str, object], request_raw)
        response = cast(dict[str, object], response_raw)
        expected_request: dict[str, object] = {"category": "linear", "symbol": symbol}
        if endpoint == INSTRUMENTS_ENDPOINT:
            expected_request["limit"] = "1000"
        if expected_cursor:
            expected_request["cursor"] = expected_cursor
        if request != expected_request:
            raise BybitContractError(f"{symbol} {endpoint} request provenance drift")
        _validate_response(response, endpoint)
        result.append({"request": request, "response": response})
        expected_cursor = _response_cursor(response)
    return result


def _rows_from_pages(
    pages: Sequence[Mapping[str, object]],
    *,
    symbol: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for page in pages:
        response = _mapping_value(page, "response")
        result = _required_mapping(response, "result")
        category = result.get("category")
        if category != "linear":
            raise BybitContractError(f"{symbol} response category drift")
        for row in _required_list(result, "list"):
            if not isinstance(row, dict):
                raise BybitContractError(f"{symbol} response row must be an object")
            typed = cast(dict[str, object], row)
            if typed.get("symbol") != symbol:
                raise BybitContractError(f"{symbol} response included another symbol")
            rows.append(typed)
    if not rows:
        raise BybitContractError(f"{symbol} response contains no rows")
    return rows


def _normalize_instrument(raw: Mapping[str, object]) -> BybitInstrumentContract:
    price_filter = _required_mapping(raw, "priceFilter")
    lot_filter = _required_mapping(raw, "lotSizeFilter")
    return BybitInstrumentContract(
        symbol=_required_str(raw, "symbol"),
        base_coin=_required_str(raw, "baseCoin"),
        quote_coin=_required_str(raw, "quoteCoin"),
        settle_coin=_required_str(raw, "settleCoin"),
        tick_size=_positive_decimal(price_filter, "tickSize"),
        qty_step=_positive_decimal(lot_filter, "qtyStep"),
        min_order_qty=_positive_decimal(lot_filter, "minOrderQty"),
        min_notional_value=_positive_decimal(lot_filter, "minNotionalValue"),
        status=_required_str(raw, "status"),
        contract_type=_required_str(raw, "contractType"),
        funding_interval_minutes=_positive_int(raw, "fundingInterval"),
    )


def _validate_symbol_contract(
    instrument: BybitInstrumentContract,
    tiers: tuple[MaintenanceMarginTier, ...],
) -> None:
    try:
        local = SYMBOL_SPECS[instrument.symbol]
    except KeyError as exc:
        raise BybitContractError(f"unexpected symbol {instrument.symbol!r}") from exc
    expected = {
        "base_coin": local.base_currency,
        "quote_coin": "USDT",
        "settle_coin": "USDT",
        "tick_size": local.price_increment,
        "qty_step": local.size_increment,
        "min_order_qty": local.min_quantity,
        "min_notional_value": local.min_notional,
        "status": "Trading",
        "contract_type": "LinearPerpetual",
        "funding_interval_minutes": int(FUNDING_INTERVAL.total_seconds() // 60),
    }
    for field, expected_value in expected.items():
        if getattr(instrument, field) != expected_value:
            raise BybitContractError(
                f"{instrument.symbol} {field} differs from local frozen spec: "
                f"{getattr(instrument, field)!r} != {expected_value!r}"
            )
    if not tiers:
        raise BybitContractError(f"{instrument.symbol} has no maintenance-margin tiers")
    previous_limit = 0.0
    for tier in tiers:
        limit = tier.max_position_value
        if limit is not None:
            if not math.isfinite(limit) or limit <= previous_limit:
                raise BybitContractError(f"{instrument.symbol} risk tiers are not increasing")
            previous_limit = limit


def _tier_as_dict(tier: MaintenanceMarginTier) -> dict[str, JsonValue]:
    return {
        "max_position_value": tier.max_position_value,
        "maintenance_margin_rate": tier.maintenance_margin_rate,
        "maintenance_margin_deduction": tier.maintenance_margin_deduction,
    }


def _validate_response(response: Mapping[str, object], endpoint: str) -> None:
    if response.get("retCode") != 0 or response.get("retMsg") != "OK":
        raise BybitContractError(
            f"{endpoint} returned retCode={response.get('retCode')!r}, "
            f"retMsg={response.get('retMsg')!r}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise BybitContractError(f"{endpoint} response has no result object")
    if not isinstance(result.get("list"), list):
        raise BybitContractError(f"{endpoint} response result has no list")
    if not isinstance(result.get("nextPageCursor", ""), str):
        raise BybitContractError(f"{endpoint} nextPageCursor must be a string")


def _response_cursor(response: Mapping[str, object]) -> str:
    return str(_required_mapping(response, "result").get("nextPageCursor", ""))


def _terminal_cursor(pages: Sequence[Mapping[str, object]]) -> str:
    if not pages:
        raise BybitContractError("pagination must contain at least one page")
    return _response_cursor(_mapping_value(pages[-1], "response"))


def _positive_decimal(raw: Mapping[str, object], field: str) -> Decimal:
    try:
        value = Decimal(_required_str(raw, field))
    except InvalidOperation as exc:
        raise BybitContractError(f"{field} must be a decimal") from exc
    if not value.is_finite() or value <= 0:
        raise BybitContractError(f"{field} must be finite and positive")
    return value


def _positive_int(raw: Mapping[str, object], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool):
        raise BybitContractError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise BybitContractError(f"{field} must be a positive integer") from exc
    if parsed <= 0 or str(parsed) != str(value):
        raise BybitContractError(f"{field} must be a positive integer")
    return parsed


def _required_mapping(raw: Mapping[str, object], field: str) -> dict[str, object]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise BybitContractError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _mapping_value(raw: Mapping[str, object], field: str) -> dict[str, object]:
    return _required_mapping(raw, field)


def _required_list(raw: Mapping[str, object], field: str) -> list[object]:
    value = raw.get(field)
    if not isinstance(value, list):
        raise BybitContractError(f"{field} must be a list")
    return value


def _required_str(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise BybitContractError(f"{field} must be a non-empty string")
    return value


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise BybitContractError(f"{field} must be timezone-aware UTC")


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    text = canonical_json(payload) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise BybitContractError(f"cannot atomically save {path}: {exc}") from exc


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BybitContractError(f"invalid {field}: {value!r}") from exc
    _require_utc(parsed, field)
    return parsed
