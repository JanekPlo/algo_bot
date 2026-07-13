"""Synthetic-only P9 runner tests; the twelve development runs are never executed."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.backtest_result import (
    BACKTEST_RESULT_SCHEMA_VERSION,
    BacktestResult,
    EligibilityStatus,
    ResultClass,
    SourceTreeState,
)
from algo_bot.engine.mms_beta_benchmark import (
    ABLATION_METRICS,
    ABLATION_SCHEMA_VERSION,
    BENCHMARK_MANIFEST_SCHEMA_VERSION,
    FROZEN_DATA_HASHES,
    FROZEN_PREREGISTRATION_SHA256,
    FROZEN_UV_LOCK_SHA256,
    PREREGISTRATION_PATH,
    QUANTITY_STEP,
    RANDOM_SEED,
    STRATEGY_ID,
    UNCONDITIONAL_INELIGIBILITY_REASONS,
    UV_LOCK_PATH,
    BenchmarkInvariantError,
    BenchmarkManifestError,
    RuntimeVersions,
    _descriptive_h1_sharpe,
    _max_drawdown_pct,
    _snapshot_exposure_maxima,
    build_ablation_summary,
    build_benchmark_manifest,
    build_run_matrix,
    run_synthetic_benchmark_dry_run,
    validate_execution_boundaries,
    verify_benchmark_manifest,
)
from algo_bot.engine.mms_beta_data import (
    BAR_TYPE,
    DATA_SCHEMA_VERSION,
    DEVELOPMENT_BARS,
    DEVELOPMENT_FUNDING_ROWS,
    DEVELOPMENT_START,
    FEATURE_MODEL_ID,
    FUNDING_PROFILE_ID,
    HOLDOUT_START,
    HOUR_NS,
    INSTRUMENT_ID,
    MILLISECOND_NS,
    TIMESTAMP_PROFILE_ID,
    WARMUP_BARS,
    WARMUP_START,
    CloseNsBarFeatureSource,
    MmsBetaDataMetadata,
    MmsBetaDevelopmentData,
    build_btcusdt_perpetual,
)
from algo_bot.engine.nautilus_mastermind import BarFeatures
from algo_bot.strategies.mastermind.model import (
    SCHEMA_VERSION as SNAPSHOT_SCHEMA_VERSION,
    STRATEGY_VERSION,
    AddonTriggerPolicy,
)

_EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_FAKE_DATA_HASH = "a" * 64


def _source_tree() -> SourceTreeState:
    return SourceTreeState(
        git_commit="e6b3c2a8d66767f8acca258bc1672d53509f7703",
        is_dirty=False,
        changes_hash=_EMPTY_HASH,
    )


def _runtime() -> RuntimeVersions:
    return RuntimeVersions(
        python="3.12.13",
        uv="0.11.28",
        nautilus_trader="1.230.0",
        talib="0.7.0",
    )


def _frozen_metadata() -> MmsBetaDataMetadata:
    return MmsBetaDataMetadata(
        schema_version=DATA_SCHEMA_VERSION,
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        warmup_start_utc="2023-12-23T16:00:00Z",
        development_start_utc="2024-01-01T00:00:00Z",
        holdout_start_utc="2025-07-01T00:00:00Z",
        warmup_bars=WARMUP_BARS,
        development_bars=DEVELOPMENT_BARS,
        funding_updates=DEVELOPMENT_FUNDING_ROWS,
        timestamp_profile=TIMESTAMP_PROFILE_ID,
        feature_model=FEATURE_MODEL_ID,
        funding_profile=FUNDING_PROFILE_ID,
        ohlcv_hash=FROZEN_DATA_HASHES["ohlcv_hash"],
        funding_hash=FROZEN_DATA_HASHES["funding_hash"],
        features_hash=FROZEN_DATA_HASHES["features_hash"],
        data_hash=FROZEN_DATA_HASHES["data_hash"],
        config_hash=FROZEN_DATA_HASHES["config_hash"],
        nautilus_version="1.230.0",
        talib_version="0.7.0",
        holdout_rows_read=0,
    )


def _synthetic_bundle() -> MmsBetaDevelopmentData:
    instrument, bar_type = build_btcusdt_perpetual()
    opens = pd.date_range(DEVELOPMENT_START, periods=5, freq="1h", tz="UTC")
    prices = (
        (Decimal("100"), Decimal("101"), Decimal("97"), Decimal("99")),
        (Decimal("99"), Decimal("100"), Decimal("99"), Decimal("100")),
        (Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
        (Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
        (Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
    )
    bars: list[Any] = []
    features: dict[int, BarFeatures] = {}
    rows: list[dict[str, float]] = []
    for open_time, (open_, high, low, close) in zip(opens, prices, strict=True):
        close_ns = int(open_time.value) + HOUR_NS - MILLISECOND_NS
        bars.append(
            nt.Bar(
                bar_type=bar_type,
                open=instrument.make_price(open_),
                high=instrument.make_price(high),
                low=instrument.make_price(low),
                close=instrument.make_price(close),
                volume=instrument.make_qty(Decimal("10")),
                ts_event=close_ns,
                ts_init=close_ns,
            )
        )
        features[close_ns] = BarFeatures(
            bb_upper=Decimal("110"),
            bb_lower=Decimal("98"),
            stoch_k=Decimal("50"),
            stoch_d=Decimal("50"),
            previous_stoch_k=Decimal("50"),
            previous_stoch_d=Decimal("50"),
        )
        rows.append(
            {
                "Open": float(open_),
                "High": float(high),
                "Low": float(low),
                "Close": float(close),
                "Volume": 10.0,
                "ts": int(open_time.timestamp() * 1_000),
            }
        )
    settlement_ns = int(pd.Timestamp("2024-01-01T04:00:00Z").value)
    funding_update = nt.FundingRateUpdate(
        instrument.id,
        Decimal("0.0001"),
        settlement_ns - 1,
        settlement_ns - 1,
        interval=28_800,
        next_funding_ns=settlement_ns,
    )
    development = pd.DataFrame(rows, index=opens)
    funding_rates = pd.DataFrame(
        {"funding_rate": [0.0001]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01T04:00:00Z")]),
    )
    native_data = tuple(sorted((*bars, funding_update), key=lambda item: int(item.ts_init)))
    metadata = replace(
        _frozen_metadata(),
        warmup_bars=0,
        development_bars=len(bars),
        funding_updates=1,
        ohlcv_hash=_FAKE_DATA_HASH,
        funding_hash="b" * 64,
        features_hash="c" * 64,
        data_hash="d" * 64,
        config_hash="e" * 64,
    )
    return MmsBetaDevelopmentData(
        ohlcv_with_warmup=development.copy(),
        development_ohlcv=development,
        funding_rates=funding_rates,
        feature_source=CloseNsBarFeatureSource(features),
        instrument=instrument,
        bar_type=bar_type,
        bars=tuple(bars),
        funding_updates=(funding_update,),
        native_data=native_data,
        metadata=metadata,
    )


def test_matrix_is_exactly_two_parameter_sets_by_six_frozen_variants() -> None:
    matrix = build_run_matrix()

    assert len(matrix) == 12
    assert [item.ordinal for item in matrix] == list(range(1, 13))
    assert {item.seed for item in matrix} == {RANDOM_SEED}
    assert {item.machine_config.strategy_id for item in matrix} == {STRATEGY_ID}
    assert len({item.run_id for item in matrix}) == 12
    assert len({item.config_hash for item in matrix}) == 12
    assert [item.parameter_set_id for item in matrix[:6]] == ["P20_E2_R0"] * 6
    assert [item.parameter_set_id for item in matrix[6:]] == ["P20_E1_R0"] * 6
    assert [item.variant_id for item in matrix[:6]] == [
        "V1_BASE_ONLY",
        "V2_BASE_SEQ",
        "V3_BASE_CC",
        "V4_BASE_STOCH",
        "V5_BASE_SEQ_CC",
        "V6_BASE_SEQ_STOCH",
    ]
    assert [item.machine_config.arm_expiry_bars for item in matrix] == [2] * 6 + [1] * 6
    assert all(item.machine_config.quantity_step == QUANTITY_STEP for item in matrix)
    assert matrix[0].dormant_addon_policy and matrix[1].dormant_addon_policy
    assert matrix[0].machine_config.addon_trigger_policy is AddonTriggerPolicy.CONFIRMING_CANDLE
    assert not matrix[2].dormant_addon_policy
    assert {
        item.machine_config.addon_trigger_policy
        for item in matrix
        if item.machine_config.addon_enabled
    } == {AddonTriggerPolicy.CONFIRMING_CANDLE, AddonTriggerPolicy.STOCH_CROSS}


def test_manifest_core_is_deterministic_and_verifies_frozen_inputs() -> None:
    first = build_benchmark_manifest(
        _frozen_metadata(),
        source_tree=_source_tree(),
        runtime_versions=_runtime(),
        created_at_utc=datetime(2026, 7, 13, 12, tzinfo=UTC),
    )
    second = build_benchmark_manifest(
        _frozen_metadata(),
        source_tree=_source_tree(),
        runtime_versions=_runtime(),
        created_at_utc=datetime(2026, 7, 13, 13, tzinfo=UTC),
    )

    assert first["schema_version"] == BENCHMARK_MANIFEST_SCHEMA_VERSION
    assert first["manifest_core"] == second["manifest_core"]
    assert first["manifest_core_hash"] == second["manifest_core_hash"]
    assert first["created_at_utc"] != second["created_at_utc"]
    core = first["manifest_core"]
    assert isinstance(core, dict)
    assert core["run_count"] == 12
    assert len(core["run_matrix"]) == 12
    assert core["schemas"]["backtest_result"] == BACKTEST_RESULT_SCHEMA_VERSION
    assert core["schemas"]["strategy"] == STRATEGY_VERSION
    assert core["schemas"]["snapshot"] == SNAPSHOT_SCHEMA_VERSION
    assert core["windows"]["holdout"]["rows_read"] == 0
    assert core["source"]["preregistration_sha256"] == FROZEN_PREREGISTRATION_SHA256
    assert core["source"]["uv_lock_sha256"] == FROZEN_UV_LOCK_SHA256
    assert core["unconditional_ineligibility_reasons"] == list(UNCONDITIONAL_INELIGIBILITY_REASONS)
    verify_benchmark_manifest(first, expected_source_tree=_source_tree())

    tampered = copy.deepcopy(first)
    tampered_core = tampered["manifest_core"]
    assert isinstance(tampered_core, dict)
    tampered_core["run_count"] = 13
    with pytest.raises(BenchmarkManifestError, match="core hash"):
        verify_benchmark_manifest(tampered)


def test_repo_preregistration_and_lock_are_the_frozen_bytes() -> None:
    import hashlib

    assert hashlib.sha256(PREREGISTRATION_PATH.read_bytes()).hexdigest() == (
        FROZEN_PREREGISTRATION_SHA256
    )
    assert hashlib.sha256(UV_LOCK_PATH.read_bytes()).hexdigest() == FROZEN_UV_LOCK_SHA256


def test_boundaries_block_last_two_domain_bars_and_reject_holdout_data() -> None:
    bundle = _synthetic_bundle()
    cutoff = validate_execution_boundaries(bundle, require_frozen_counts=False)

    assert cutoff.cutoff_close_ns == int(bundle.bars[-2].ts_init)
    assert cutoff.final_close_ns == int(bundle.bars[-1].ts_init)
    assert cutoff.deliver_domain_bar(bundle.bars[-3])
    assert not cutoff.deliver_domain_bar(bundle.bars[-2])
    assert not cutoff.deliver_domain_bar(bundle.bars[-1])
    assert cutoff.before_bar(bundle.bars[-3]) == ()
    event = cutoff.before_bar(bundle.bars[-2])[0]
    assert isinstance(event, object)
    assert event.__class__.__name__ == "CloseRequested"
    assert event.close_reason.value == "MANUAL"
    assert cutoff.before_bar(bundle.bars[-1]) == ()
    with pytest.raises(BenchmarkInvariantError, match="twice"):
        cutoff.before_bar(bundle.bars[-2])

    class HoldoutItem:
        ts_init = int(pd.Timestamp(HOLDOUT_START).value)

    contaminated = replace(
        bundle,
        native_data=(*bundle.native_data, HoldoutItem()),
    )
    with pytest.raises(Exception, match="holdout"):
        validate_execution_boundaries(contaminated, require_frozen_counts=False)


def test_ablation_has_all_variant_deltas_and_frozen_interactions() -> None:
    stats_by_run: dict[str, dict[str, float]] = {}
    values = {
        "V1_BASE_ONLY": 10.0,
        "V2_BASE_SEQ": 13.0,
        "V3_BASE_CC": 12.0,
        "V4_BASE_STOCH": 15.0,
        "V5_BASE_SEQ_CC": 20.0,
        "V6_BASE_SEQ_STOCH": 25.0,
    }
    for config in build_run_matrix():
        stats_by_run[config.run_id] = dict.fromkeys(ABLATION_METRICS, values[config.variant_id])

    summary = build_ablation_summary(stats_by_run)

    assert len(summary) == 22
    assert set(summary["schema_version"]) == {ABLATION_SCHEMA_VERSION}
    e2 = summary.loc[summary["parameter_set_id"] == "P20_E2_R0"]
    base_delta = e2.loc[
        e2["comparison_id"] == "V1_BASE_ONLY_MINUS_V1_BASE_ONLY",
        "final_equity",
    ].item()
    seq_cc = e2.loc[e2["comparison_id"] == "SEQ_X_CC", "final_equity"].item()
    seq_stoch = e2.loc[e2["comparison_id"] == "SEQ_X_STOCH", "final_equity"].item()
    assert base_delta == 0.0
    assert seq_cc == 5.0
    assert seq_stoch == 7.0


def test_committed_exposure_is_x1_base_only_and_x2_with_addon_reserved() -> None:
    base_only = {
        "setup": {
            "setup_start_equity": "10000",
            "base_target_notional": "10000",
            "addon_target_notional": "0",
            "actual_entry_notional": "9999",
        }
    }
    addon_enabled = {
        "setup": {
            "setup_start_equity": "10000",
            "base_target_notional": "10000",
            "addon_target_notional": "10000",
            "actual_entry_notional": "19998",
        }
    }

    base_maxima = _snapshot_exposure_maxima([base_only])
    addon_maxima = _snapshot_exposure_maxima([addon_enabled])

    assert base_maxima["max_committed_target_quote"] == 10_000.0
    assert base_maxima["max_committed_exposure_multiplier"] == 1.0
    assert addon_maxima["max_committed_target_quote"] == 20_000.0
    assert addon_maxima["max_committed_exposure_multiplier"] == 2.0


def test_metric_profile_uses_sample_h1_sharpe_and_nonpositive_drawdown() -> None:
    equity = pd.Series([100.0, 110.0, 104.5])
    returns = pd.Series([0.1, -0.05])
    expected_sharpe = (8_760.0**0.5) * returns.mean() / returns.std(ddof=1)

    assert _descriptive_h1_sharpe(equity) == pytest.approx(expected_sharpe)
    assert _max_drawdown_pct(equity) == pytest.approx(-5.0)
    assert _descriptive_h1_sharpe(pd.Series([100.0, 100.0, 100.0])) is None


def test_small_real_pyo3_engine_dry_run_is_flat_and_smoke_only(tmp_path: Path) -> None:
    bundle = _synthetic_bundle()
    run_config = build_run_matrix()[0]

    artifact = run_synthetic_benchmark_dry_run(
        run_config,
        bundle,
        source_tree=_source_tree(),
    )

    assert artifact.result.schema_version == BACKTEST_RESULT_SCHEMA_VERSION
    assert artifact.result.eligibility.status is EligibilityStatus.NOT_ELIGIBLE
    assert artifact.result.eligibility.result_class is ResultClass.SMOKE_ONLY
    assert set(UNCONDITIONAL_INELIGIBILITY_REASONS).issubset(artifact.result.eligibility.reasons)
    assert "SYNTHETIC_FIXTURE" in artifact.result.eligibility.reasons
    assert artifact.counters["base_entry_intents"] == 1
    assert artifact.counters["setups_started"] == 1
    assert artifact.counters["invariant_violation_count"] == 0
    assert artifact.result.stats["final_state"]["position_build"] == "FLAT"
    assert artifact.result.stats["final_state"]["active_domain_orders"] == 0
    assert artifact.result.stats["synthetic_fixture"] is True
    assert all(item["passed"] is True for item in artifact.invariant_ledger)
    assert not artifact.result.orders.empty
    assert not artifact.result.fills.empty
    assert artifact.final_snapshot_hash == artifact.result.stats["final_snapshot_sha256"]

    output = artifact.save(tmp_path / "dry-run")
    restored = BacktestResult.load(output / "backtest_result")
    assert restored.artifact_hash() == artifact.result.artifact_hash()
    assert (output / "final_snapshot.json").read_text(encoding="utf-8").strip() == (
        artifact.final_snapshot
    )


def test_strict_boundary_requires_full_frozen_counts() -> None:
    with pytest.raises(Exception, match=str(DEVELOPMENT_BARS)):
        validate_execution_boundaries(_synthetic_bundle(), require_frozen_counts=True)


def test_window_constants_remain_right_open_utc() -> None:
    assert datetime(2023, 12, 23, 16, tzinfo=UTC) == WARMUP_START
    assert datetime(2024, 1, 1, tzinfo=UTC) == DEVELOPMENT_START
    assert datetime(2025, 7, 1, tzinfo=UTC) == HOLDOUT_START
    assert timedelta(days=547) == HOLDOUT_START - DEVELOPMENT_START
