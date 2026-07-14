"""P8 tests for the versioned, fail-closed BacktestResult artifact."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from algo_bot.engine.backtest_result import (
    BACKTEST_RESULT_SCHEMA_VERSION,
    ArtifactIntegrityError,
    BacktestResult,
    BacktestResultError,
    CostComponent,
    CostModel,
    CostProvenance,
    EligibilityAssessment,
    EligibilityStatus,
    FillMethod,
    MarginMethod,
    ResearchEligibilityError,
    ResultClass,
    SourceTreeState,
    assess_eligibility,
    combined_data_hash,
    derive_legacy_ledgers,
    json_hash,
    legacy_adr011_cost_model,
)
from algo_bot.engine.backtester import run_backtest, run_backtest_result
from algo_bot.microstructure import LiquidationEvent

_EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _component(
    name: str,
    *,
    provenance: CostProvenance = CostProvenance.NATIVE,
    complete: bool = True,
    research_eligible: bool = True,
) -> CostComponent:
    return CostComponent(
        model_id=name,
        provenance=provenance,
        complete=complete,
        research_eligible=research_eligible,
    )


def _eligible_cost_model() -> CostModel:
    return CostModel(
        identifier="NATIVE_FIXTURE_V1",
        commission=_component("native-commission-v1"),
        funding=_component("native-funding-settlements-v1"),
        slippage=_component("observed-fill-slippage-v1"),
        execution=_component("causal-execution-profile-v1"),
    )


def _source_tree() -> SourceTreeState:
    return SourceTreeState(
        git_commit="e6b3c2a8d66767f8acca258bc1672d53509f7703",
        is_dirty=False,
        changes_hash=_EMPTY_HASH,
    )


def _frames() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    index = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    equity = pd.DataFrame({"Equity": [10_000.0, 10_010.5, 9_995.25]}, index=index)
    trades = pd.DataFrame(
        {
            "trade_id": ["trade-1"],
            "EntryTime": [index[0]],
            "ExitTime": [index[2]],
            "PnL": [-4.75],
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": ["order-1", "order-2"],
            "side": ["BUY", "SELL"],
            "status": ["FILLED", "FILLED"],
        }
    )
    fills = pd.DataFrame(
        {
            "fill_id": ["fill-1", "fill-2"],
            "order_id": ["order-1", "order-2"],
            "price": [50_000.0, 49_976.25],
            "commission": [2.0, 1.99905],
        }
    )
    positions = pd.DataFrame(
        {"position_id": ["position-1"], "status": ["CLOSED"], "realized_pnl": [-4.75]}
    )
    funding = pd.DataFrame(
        {
            "settlement_id": ["funding-1"],
            "event_time": [index[1]],
            "rate": [0.0001],
            "notional": [5_000.0],
            "amount": [-0.5],
            "currency": ["USDT"],
            "provenance": ["NATIVE_FIXTURE"],
        }
    )
    return equity, trades, orders, fills, positions, funding


def _result(
    *,
    cost_model: CostModel | None = None,
    eligibility: EligibilityAssessment | None = None,
    liquidation_events: tuple[LiquidationEvent, ...] = (),
) -> BacktestResult:
    equity, trades, orders, fills, positions, funding = _frames()
    resolved_cost_model = cost_model or _eligible_cost_model()
    resolved_eligibility = eligibility or assess_eligibility(resolved_cost_model)
    return BacktestResult(
        schema_version=BACKTEST_RESULT_SCHEMA_VERSION,
        engine="nautilus_trader",
        engine_version="1.230.0",
        strategy_version="fixture/1",
        source_tree=_source_tree(),
        stats={"return_pct": -0.0475, "undefined_metric": float("nan")},
        equity=equity,
        trades=trades,
        orders=orders,
        fills=fills,
        positions=positions,
        funding=funding,
        data_hash=combined_data_hash(equity.rename(columns={"Equity": "Close"})),
        config_hash=json_hash({"strategy": "fixture", "seed": 20260713}),
        random_seed=20260713,
        cost_model=resolved_cost_model,
        eligibility=resolved_eligibility,
        fill_method=FillMethod.NAUTILUS_NATIVE_BAR,
        margin_method=MarginMethod.MARK_PRICE_ISOLATED,
        mark_price_source="bybit:BTCUSDT:mark:1h:test-fixture",
        liquidation_events=liquidation_events,
    )


def _missing_funding_model() -> CostModel:
    return CostModel(
        identifier="NAUTILUS_NATIVE_PENDING_FUNDING_V1",
        commission=_component("native-commission-v1"),
        funding=_component(
            "native-funding-not-provided",
            provenance=CostProvenance.MISSING,
            complete=False,
            research_eligible=False,
        ),
        slippage=_component("observed-fill-slippage-v1"),
        execution=_component("causal-execution-profile-v1"),
    )


def test_missing_native_funding_fails_closed_but_artifact_remains_diagnostic() -> None:
    cost_model = _missing_funding_model()
    result = _result(cost_model=cost_model, eligibility=assess_eligibility(cost_model))

    assert result.eligibility.status is EligibilityStatus.NOT_ELIGIBLE
    assert result.eligibility.result_class is ResultClass.SMOKE_ONLY
    assert "FUNDING_MISSING" in result.eligibility.reasons
    with pytest.raises(ResearchEligibilityError, match="FUNDING_MISSING"):
        result.assert_research_eligible()


def test_liquidation_is_eligible_negative_outcome_and_round_trips(tmp_path: Path) -> None:
    event = LiquidationEvent(
        position_id="position-1",
        side="long",
        observed_at=pd.Timestamp("2024-01-01T02:00:00Z"),
        mark_price=36_000.0,
        liquidation_price=36_380.25,
        maintenance_margin_rate=0.005,
        maintenance_margin_deduction=0.0,
        source="bybit_BTCUSDT_mark_1h.csv",
    )
    result = _result(liquidation_events=(event,))
    assert result.eligibility.status is EligibilityStatus.ELIGIBLE
    result.save(tmp_path)
    restored = BacktestResult.load(tmp_path)
    assert restored.liquidation_events == (event,)
    assert restored.fill_method is FillMethod.NAUTILUS_NATIVE_BAR
    assert restored.margin_method is MarginMethod.MARK_PRICE_ISOLATED


def test_schema_v1_load_migrates_in_memory_without_rewriting_artifact(tmp_path: Path) -> None:
    result = _result()
    result.save(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    original["schema_version"] = "backtest_result/1"
    for key in ("fill_method", "margin_method", "mark_price_source", "liquidation_events"):
        original.pop(key)
    legacy_text = json.dumps(original, sort_keys=True, separators=(",", ":"))
    manifest_path.write_text(legacy_text, encoding="utf-8")

    restored = BacktestResult.load(tmp_path)
    assert restored.schema_version == BACKTEST_RESULT_SCHEMA_VERSION
    assert restored.fill_method is FillMethod.CLOSE_NAIVE
    assert restored.margin_method is MarginMethod.NONE
    assert restored.eligibility.status is EligibilityStatus.NOT_ELIGIBLE
    assert "FILL_METHOD_CLOSE_NAIVE" in restored.eligibility.reasons
    assert manifest_path.read_text(encoding="utf-8") == legacy_text


def test_native_does_not_automatically_mean_research_eligible() -> None:
    cost_model = CostModel(
        identifier="NATIVE_BUT_UNQUALIFIED_V1",
        commission=_component("commission"),
        funding=_component("funding", research_eligible=False),
        slippage=_component("slippage"),
        execution=_component("execution"),
    )

    assessment = assess_eligibility(cost_model)

    assert assessment.status is EligibilityStatus.NOT_ELIGIBLE
    assert assessment.reasons == ("FUNDING_NOT_RESEARCH_QUALIFIED",)


def test_approximate_costing_must_use_smoke_only_label() -> None:
    approximate = CostModel(
        identifier="APPROX_V1",
        commission=_component("commission"),
        funding=_component(
            "h1-close-funding-proxy",
            provenance=CostProvenance.APPROXIMATE,
            research_eligible=False,
        ),
        slippage=_component("slippage"),
        execution=_component("execution"),
    )

    assessment = assess_eligibility(approximate)
    assert assessment.result_class is ResultClass.SMOKE_ONLY
    assert "FUNDING_APPROXIMATE" in assessment.reasons
    with pytest.raises(BacktestResultError, match="Approximate costing"):
        assess_eligibility(approximate, noneligible_class=ResultClass.EQUIVALENCE_ONLY)


def test_result_rejects_false_eligible_claim_for_missing_cost() -> None:
    false_claim = EligibilityAssessment(
        status=EligibilityStatus.ELIGIBLE,
        result_class=ResultClass.RESEARCH,
        reasons=(),
    )

    with pytest.raises(BacktestResultError, match="claims ELIGIBLE"):
        _result(cost_model=_missing_funding_model(), eligibility=false_claim)


def test_round_trip_preserves_manifest_and_all_frames(tmp_path: Path) -> None:
    result = _result()
    artifact_dir = result.save(tmp_path / "artifact")

    restored = BacktestResult.load(artifact_dir)

    assert restored.manifest() == result.manifest()
    assert restored.artifact_hash() == result.artifact_hash()
    assert restored.stats["undefined_metric"] is None
    for name in ("equity", "trades", "orders", "fills", "positions", "funding"):
        pdt.assert_frame_equal(
            getattr(restored, name),
            getattr(result, name),
            check_freq=False,
        )


def test_serialization_is_byte_deterministic(tmp_path: Path) -> None:
    left = _result()
    right = _result()
    left_dir = left.save(tmp_path / "left")
    right_dir = right.save(tmp_path / "right")

    assert left.artifact_hash() == right.artifact_hash()
    for filename in (
        "manifest.json",
        "equity.json",
        "trades.json",
        "orders.json",
        "fills.json",
        "positions.json",
        "funding.json",
    ):
        assert (left_dir / filename).read_bytes() == (right_dir / filename).read_bytes()


def test_load_rejects_tampered_frame(tmp_path: Path) -> None:
    artifact_dir = _result().save(tmp_path / "artifact")
    equity_path = artifact_dir / "equity.json"
    equity_path.write_text(equity_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="Frame hash mismatch"):
        BacktestResult.load(artifact_dir)


def test_manifest_is_strict_json_without_nan_extensions(tmp_path: Path) -> None:
    manifest_path = _result().save(tmp_path / "artifact") / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "NaN" not in text
    assert "Infinity" not in text
    assert json.loads(text)["stats"]["undefined_metric"] is None


def test_legacy_tuple_facade_returns_stats_equity_trades() -> None:
    result = _result()

    stats, equity, trades = result

    assert stats == result.stats
    assert equity is result.equity
    assert trades is result.trades
    assert result.as_legacy_tuple() == (stats, equity, trades)


def test_legacy_ledger_is_stable_and_explicitly_derived() -> None:
    timestamp = pd.Timestamp("2024-01-01T00:00:00Z")
    trades = pd.DataFrame(
        {
            "Size": [0.2, -0.1],
            "EntryTime": [timestamp, timestamp + pd.Timedelta(hours=2)],
            "ExitTime": [timestamp + pd.Timedelta(hours=1), timestamp + pd.Timedelta(hours=3)],
            "EntryPrice": [50_000.0, 50_100.0],
            "ExitPrice": [50_050.0, 50_000.0],
            "PnL": [10.0, 10.0],
            "Commission": [8.0, 4.004],
        }
    )

    orders, fills, positions = derive_legacy_ledgers(trades)

    assert list(orders["order_id"]) == [
        "legacy-trade-000000-entry",
        "legacy-trade-000000-exit",
        "legacy-trade-000001-entry",
        "legacy-trade-000001-exit",
    ]
    assert list(orders["side"]) == ["BUY", "SELL", "SELL", "BUY"]
    assert len(fills) == 4
    assert fills["commission"].isna().all()
    assert list(positions["side"]) == ["LONG", "SHORT"]
    assert set(positions["provenance"]) == {"LEGACY_DERIVED"}


def test_legacy_adr011_contract_is_explicit_and_not_eligible() -> None:
    model = legacy_adr011_cost_model(
        commission_rate=0.0004,
        microstructure_enabled=True,
        slip_bps=1.0,
        funding_source="historical",
    )

    assert model.identifier == "LEGACY_ADR011_OVERLAY_V1"
    assert model.is_complete
    assert not model.is_research_eligible
    assert model.funding.provenance is CostProvenance.APPROXIMATE
    assert model.slippage.provenance is CostProvenance.APPROXIMATE


def _synthetic_ohlcv() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=80, freq="1h", tz="UTC")
    close = np.concatenate((np.linspace(100.0, 120.0, 40), np.linspace(120.0, 90.0, 40)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 1.0,
            "Low": np.minimum(open_, close) - 1.0,
            "Close": close,
            "Volume": np.full(len(index), 100.0),
        },
        index=index,
    )


def test_legacy_runner_remains_tuple_and_rich_factory_is_deterministic() -> None:
    data = _synthetic_ohlcv()
    kwargs = {
        "symbol": "SYNTH/USDT",
        "timeframe": "1h",
        "strategy": "simple_momentum",
        "params": {"short": 5, "long": 15, "side": "long"},
        "cash": 100_000.0,
        "commission": 0.0004,
        "trade_on_close": True,
        "data": data,
    }

    legacy = run_backtest(**kwargs)
    first = run_backtest_result(
        **kwargs,
        random_seed=20260713,
        strategy_version="simple_momentum/1",
        source_tree=_source_tree(),
    )
    second = run_backtest_result(
        **kwargs,
        random_seed=20260713,
        strategy_version="simple_momentum/1",
        source_tree=_source_tree(),
    )

    assert isinstance(legacy, tuple)
    assert len(legacy) == 3
    assert first.artifact_hash() == second.artifact_hash()
    assert first.engine == "backtesting.py"
    assert first.engine_version == "0.6.5"
    assert first.random_seed == 20260713
    assert first.eligibility.result_class is ResultClass.SMOKE_ONLY
    assert first.eligibility.status is EligibilityStatus.NOT_ELIGIBLE
    assert list(first.orders.columns)
    assert list(first.fills.columns)
    assert list(first.positions.columns)
    assert list(first.funding.columns)
    assert first.funding.empty


def test_data_and_config_hashes_change_only_with_their_inputs() -> None:
    base = _synthetic_ohlcv()
    changed = base.copy()
    changed.iloc[-1, changed.columns.get_loc("Close")] += 0.1

    assert combined_data_hash(base) == combined_data_hash(base.copy())
    assert combined_data_hash(base) != combined_data_hash(changed)
    assert json_hash({"seed": 1, "params": {"a": 2}}) == json_hash({"params": {"a": 2}, "seed": 1})
    assert json_hash({"seed": 1}) != json_hash({"seed": 2})
