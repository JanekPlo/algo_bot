"""Frozen P9 MMS Beta mini-benchmark runner and artifact contract.

This module is deliberately safe to import: it does not load CSV data, execute a
strategy, inspect a metric, or write an artifact at import time.  The production
entry point writes and verifies the preregistered development-only manifest before
it starts any of the twelve runs.  A separate explicit fixture mode exists only for
small synthetic engine tests; it can never be mistaken for the frozen benchmark.

The temporal holdout is represented only by its boundary.  Input data is supplied
by :mod:`algo_bot.engine.mms_beta_data`, whose streaming readers stop before the
first holdout row.  This runner never opens either source CSV itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pandas as pd
import talib
from nautilus_trader import __version__ as nautilus_version
from nautilus_trader.core import nautilus_pyo3 as nt

from algo_bot.engine.backtest_result import (
    BACKTEST_RESULT_SCHEMA_VERSION,
    BacktestResult,
    CostComponent,
    CostModel,
    CostProvenance,
    FillMethod,
    JsonValue,
    MarginMethod,
    ResultClass,
    SourceTreeState,
    assess_eligibility,
    canonical_json,
    capture_source_tree_state,
    json_hash,
    normalize_json,
)
from algo_bot.engine.mms_beta_data import (
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
    MmsBetaDataMetadata,
    MmsBetaDevelopmentData,
    load_mms_beta_development_data,
)
from algo_bot.engine.nautilus_mastermind import (
    PYO3_SMOKE_EXECUTION_PROFILE,
    PYO3_SMOKE_POSITION_MODEL,
    Pyo3SmokeRun,
    run_pyo3_mastermind_smoke,
)
from algo_bot.engine.nautilus_oms_poc import SELECTED_OMS_MODEL
from algo_bot.strategies.mastermind.model import (
    SCHEMA_VERSION as SNAPSHOT_SCHEMA_VERSION,
    STRATEGY_VERSION,
    AccountEquityUpdated,
    AddonTriggerPolicy,
    BarClosed,
    CloseReason,
    CloseRequested,
    DomainEvent,
    FundingApplied,
    MastermindConfig,
    OrderFilled,
    OrderLifecycle,
    OrderPartiallyFilled,
    OrderRole,
    OrderSubmitted,
    PositionBuild,
    PositionClosed,
    RiskMode,
)
from algo_bot.strategies.mastermind.state_machine import MastermindStateMachine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREREGISTRATION_PATH = PROJECT_ROOT / "docs/experiments/mms-v2-beta-preregistration.md"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"

BENCHMARK_MANIFEST_SCHEMA_VERSION = "mms_beta_benchmark_manifest/1"
BENCHMARK_RUNNER_VERSION = "mms_beta_benchmark/1"
BENCHMARK_COUNTERS_SCHEMA_VERSION = "mms_beta_counters/1"
BENCHMARK_RESULTS_INDEX_SCHEMA_VERSION = "mms_beta_results_index/1"
ABLATION_SCHEMA_VERSION = "mms_beta_ablation/1"
METRIC_PROFILE_ID = "MMS_BETA_DESCRIPTIVE_H1_V1"

RANDOM_SEED = 20_260_713
STARTING_BALANCE = Decimal("10000")
STRATEGY_ID = "MMS-P9-BETA-001"
TIMESTAMP_MAP_PROFILE = "BINANCE_OPEN_TO_INCLUSIVE_CLOSE_V1"
ONE_TICK_FILL_MODEL = "NAUTILUS_DEFAULT_FILL_MODEL_ONE_TICK_PROBABILITY_1_V1"
NATIVE_COST_PROFILE = "NAUTILUS_NATIVE_FEE_FUNDING_ONE_TICK_V1"
PRICE_TICK = Decimal("0.1")
QUANTITY_STEP = Decimal("0.001")
FIXED_FEE_RATE = Decimal("0.0004")
HOLDOUT_END = datetime(2026, 1, 1, tzinfo=UTC)

EXPECTED_PYTHON_VERSION = "3.12.13"
EXPECTED_UV_VERSION = "0.11.28"
EXPECTED_NAUTILUS_VERSION = "1.230.0"
EXPECTED_TALIB_VERSION = "0.7.0"

FROZEN_PREREGISTRATION_SHA256 = "3aa53985e6093521223bdac80747837506ce55aeefc90934c6a82cc498f70c26"
FROZEN_UV_LOCK_SHA256 = "6020bd7ed209fe8f50ef844e110900605de45aefbda5fe54b1ddd01212bba4eb"
FROZEN_DATA_HASHES: Mapping[str, str] = {
    "ohlcv_hash": "5acd3750ba0e63cff67e1c06bbbc995b30780f5fc754d5814d46c5f763d31e68",
    "funding_hash": "55bf51bfb8adacfa787bf3b0dd506c7b7e47a1669a144e83fdbeaa320fb25c63",
    "features_hash": "5da38d5e3d97e205dc52b4b6a6aaac28bff2c836772a667dae75df441ec945b4",
    "data_hash": "3f7f1aa135e9aeb3fc95e1eabe9a1379093335e4db132173b90466adeffbf67e",
    "config_hash": "bd39136efbd364d07e2debe6c3208c96e40a5031ed1d88b3fbee35eab396525c",
}

UNCONDITIONAL_INELIGIBILITY_REASONS = (
    "NO_MARK_PRICE_HISTORY",
    "H1_INTRABAR_HEURISTIC",
    "NO_ORDER_BOOK_OR_TRADES",
    "APPROX_ONE_TICK_SLIPPAGE",
    "FIXED_FEE_SCHEDULE",
    "BACKTEST_CLOSEALL_NOT_BINANCE_PARITY",
    "H1_WICK_PAIR_PROXY",
    "NO_H4_D1_CONTEXT",
)

COUNTER_NAMES = (
    "base_reaction_facts",
    "base_entry_intents",
    "base_submissions",
    "setups_started",
    "addon_trigger_facts",
    "addon_intents",
    "addon_submissions",
    "addon_first_fills",
    "addon_fill_deltas",
    "addon_rejections",
    "addon_sl_count",
    "full_base_sl_count",
    "full_to_scout_transitions",
    "scout_setups",
    "scout_to_full_rearms",
    "funding_settlements",
    "invariant_violation_count",
)

ABLATION_METRICS = (
    *COUNTER_NAMES,
    "scout_episode_mean_bars",
    "max_committed_target_quote",
    "max_gross_realized_exposure_quote",
    "max_committed_exposure_multiplier",
    "max_actual_gross_exposure_multiplier",
    "gross_price_pnl",
    "commissions",
    "funding_paid",
    "funding_received",
    "funding_net",
    "slippage_cost",
    "setup_net_pnl",
    "final_equity",
    "return_pct",
    "sharpe_h1_descriptive",
    "max_drawdown_pct",
    "turnover",
)

METRIC_DEFINITIONS: Mapping[str, str] = {
    "equity": "native marked account equity published before each wrapper bar callback",
    "return_pct": "100 * (final_equity / first_equity - 1)",
    "sharpe_h1_descriptive": (
        "sqrt(8760) * mean(hourly equity pct change) / sample std(ddof=1); "
        "null for fewer than two returns or zero variance"
    ),
    "max_drawdown_pct": "100 * min(equity / running_max_equity - 1); non-positive",
    "turnover": "sum(abs(native domain fill price * fill delta quantity)) / first_equity",
    "scout_episode_bars": (
        "number of delivered final H1 BarClosed events observed in SCOUT per episode; "
        "the final partial episode is included and separately right-censored"
    ),
    "setup_net_pnl": "gross price PnL - commissions + funding net - slippage cost",
    "funding_paid": "sum(abs(negative native FundingApplied amounts))",
    "funding_received": "sum(positive native FundingApplied amounts)",
}

_TERMINAL_NATIVE_ORDER_STATUSES = {
    "CANCELED",
    "DENIED",
    "EXPIRED",
    "FILLED",
    "REJECTED",
    "RELEASED",
}
_DOMAIN_FILL_TYPES = (OrderPartiallyFilled, OrderFilled)


class MmsBetaBenchmarkError(RuntimeError):
    """The frozen benchmark contract cannot be satisfied safely."""


class BenchmarkManifestError(MmsBetaBenchmarkError):
    """A pre-run manifest is incomplete, changed, or not frozen."""


class BenchmarkInvariantError(MmsBetaBenchmarkError):
    """A run ended with a failed technical invariant."""


@dataclass(frozen=True, slots=True)
class RuntimeVersions:
    """Pinned runtime versions recorded in the pre-run manifest."""

    python: str
    uv: str
    nautilus_trader: str
    talib: str

    def validate(self) -> None:
        """Fail if the runtime differs from the Beta hard gate."""

        expected = {
            "python": EXPECTED_PYTHON_VERSION,
            "uv": EXPECTED_UV_VERSION,
            "nautilus_trader": EXPECTED_NAUTILUS_VERSION,
            "talib": EXPECTED_TALIB_VERSION,
        }
        actual = {
            "python": self.python,
            "uv": self.uv,
            "nautilus_trader": self.nautilus_trader,
            "talib": self.talib,
        }
        if actual != expected:
            raise BenchmarkManifestError(
                f"runtime version drift: expected={expected}, got={actual}"
            )

    def as_dict(self) -> dict[str, str]:
        """Return stable JSON values."""

        return {
            "python": self.python,
            "uv": self.uv,
            "nautilus_trader": self.nautilus_trader,
            "talib": self.talib,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRunConfig:
    """One and only one preregistered parameter/ablation combination."""

    ordinal: int
    parameter_set_id: str
    variant_id: str
    machine_config: MastermindConfig
    dormant_addon_policy: bool
    seed: int = RANDOM_SEED

    @property
    def run_id(self) -> str:
        """Return the stable result key used on disk and in summaries."""

        return f"{self.parameter_set_id}__{self.variant_id}"

    @property
    def config_hash(self) -> str:
        """Return the P6 machine config hash used by ``BacktestResult``."""

        return self.machine_config.config_hash

    @property
    def run_spec_hash(self) -> str:
        """Hash the matrix identity around the underlying machine config."""

        return json_hash(self._payload())

    def _payload(self) -> dict[str, object]:
        config = self.machine_config
        return {
            "ordinal": self.ordinal,
            "run_id": self.run_id,
            "parameter_set_id": self.parameter_set_id,
            "variant_id": self.variant_id,
            "seed": self.seed,
            "dormant_addon_policy": self.dormant_addon_policy,
            "machine_config_hash": config.config_hash,
            "machine_config": {
                "strategy_id": config.strategy_id,
                "instrument_id": config.instrument_id,
                "strategy_version": config.strategy_version,
                "timeframe": config.timeframe,
                "bb_window": config.bb_window,
                "bb_num_std": config.bb_num_std,
                "arm_expiry_bars": config.arm_expiry_bars,
                "require_reclaim": config.require_reclaim,
                "base_exposure_full": config.base_exposure_full,
                "base_exposure_scout": config.base_exposure_scout,
                "base_sl_pct": config.base_sl_pct,
                "addon_max_sl_pct": config.addon_max_sl_pct,
                "stoch_oversold": config.stoch_oversold,
                "stoch_overbought": config.stoch_overbought,
                "quantity_step": config.quantity_step,
                "min_quantity": config.min_quantity,
                "min_notional": config.min_notional,
                "sequential_enabled": config.sequential_enabled,
                "addon_enabled": config.addon_enabled,
                "addon_trigger_policy": config.addon_trigger_policy,
            },
        }

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the canonical manifest record, including both hashes."""

        payload = _normalized_mapping(self._payload())
        payload["run_spec_hash"] = self.run_spec_hash
        return payload


@dataclass(slots=True)
class DevelopmentCutoff:
    """Inject one manual close and suppress the last two domain bars."""

    strategy_id: str
    instrument_id: str
    cutoff_close_ns: int
    final_close_ns: int
    emitted_count: int = 0

    def __post_init__(self) -> None:
        if self.cutoff_close_ns >= self.final_close_ns:
            raise ValueError("cutoff close must precede the final executable close")

    def before_bar(self, bar: Any) -> tuple[DomainEvent, ...]:
        """Emit the deterministic ``CloseRequested(MANUAL)`` exactly once."""

        timestamp_ns = int(bar.ts_init)
        if timestamp_ns != self.cutoff_close_ns:
            return ()
        if self.emitted_count:
            raise BenchmarkInvariantError("manual development cutoff was requested twice")
        self.emitted_count += 1
        return (
            CloseRequested(
                event_id=f"p9-manual-cutoff:{self.cutoff_close_ns}",
                strategy_id=self.strategy_id,
                instrument_id=self.instrument_id,
                occurred_at_utc=_datetime_from_ns(self.cutoff_close_ns),
                source="mms_beta.development_cutoff",
                source_sequence=1,
                close_reason=CloseReason.MANUAL,
                reason="frozen development boundary",
            ),
        )

    def deliver_domain_bar(self, bar: Any) -> bool:
        """Block signal evaluation on the penultimate and final bars."""

        return int(bar.ts_init) < self.cutoff_close_ns


@dataclass(slots=True)
class BenchmarkRunArtifact:
    """P8 result plus the P9 counters, invariant ledger, and exact snapshot."""

    run_config: BenchmarkRunConfig
    result: BacktestResult
    counters: dict[str, int]
    final_snapshot: str
    invariant_ledger: tuple[dict[str, JsonValue], ...]

    @property
    def final_snapshot_hash(self) -> str:
        """Hash the exact recovery payload saved beside the P8 artifact."""

        return hashlib.sha256(self.final_snapshot.encode("utf-8")).hexdigest()

    def summary(self) -> dict[str, JsonValue]:
        """Build the checksummed machine-readable suite index entry."""

        return {
            "run_id": self.run_config.run_id,
            "parameter_set_id": self.run_config.parameter_set_id,
            "variant_id": self.run_config.variant_id,
            "config_hash": self.run_config.config_hash,
            "run_spec_hash": self.run_config.run_spec_hash,
            "artifact_hash": self.result.artifact_hash(),
            "final_snapshot_sha256": self.final_snapshot_hash,
            "eligibility": self.result.eligibility.to_dict(),
            "counters": dict(self.counters),
            "invariant_ledger": list(self.invariant_ledger),
        }

    def save(self, directory: Path) -> Path:
        """Persist and reload-verify every per-run artifact."""

        if directory.exists():
            raise MmsBetaBenchmarkError(f"refusing to overwrite run directory {directory}")
        directory.mkdir(parents=True)
        result_dir = directory / "backtest_result"
        self.result.save(result_dir)
        restored = BacktestResult.load(result_dir)
        if restored.artifact_hash() != self.result.artifact_hash():
            raise BenchmarkInvariantError("saved BacktestResult failed round-trip identity")
        _write_new_text(directory / "final_snapshot.json", self.final_snapshot + "\n")
        _write_new_json(
            directory / "counters.json",
            {
                "schema_version": BENCHMARK_COUNTERS_SCHEMA_VERSION,
                "run_id": self.run_config.run_id,
                "counters": self.counters,
            },
        )
        _write_new_json(directory / "run_summary.json", self.summary())
        return directory


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteArtifact:
    """Completed frozen suite; construction requires exactly twelve results."""

    manifest: dict[str, JsonValue]
    runs: tuple[BenchmarkRunArtifact, ...]
    ablation: pd.DataFrame
    output_directory: Path


@dataclass(frozen=True, slots=True)
class _VariantSpec:
    variant_id: str
    sequential_enabled: bool
    addon_enabled: bool
    addon_policy: AddonTriggerPolicy


_VARIANTS = (
    _VariantSpec("V1_BASE_ONLY", False, False, AddonTriggerPolicy.CONFIRMING_CANDLE),
    _VariantSpec("V2_BASE_SEQ", True, False, AddonTriggerPolicy.CONFIRMING_CANDLE),
    _VariantSpec("V3_BASE_CC", False, True, AddonTriggerPolicy.CONFIRMING_CANDLE),
    _VariantSpec("V4_BASE_STOCH", False, True, AddonTriggerPolicy.STOCH_CROSS),
    _VariantSpec("V5_BASE_SEQ_CC", True, True, AddonTriggerPolicy.CONFIRMING_CANDLE),
    _VariantSpec("V6_BASE_SEQ_STOCH", True, True, AddonTriggerPolicy.STOCH_CROSS),
)
_PARAMETER_SETS = (("P20_E2_R0", 2), ("P20_E1_R0", 1))


def build_run_matrix() -> tuple[BenchmarkRunConfig, ...]:
    """Construct the exact deterministic ``2 x 6`` preregistered matrix."""

    matrix: list[BenchmarkRunConfig] = []
    ordinal = 1
    for parameter_set_id, expiry in _PARAMETER_SETS:
        for variant in _VARIANTS:
            config = MastermindConfig(
                strategy_id=STRATEGY_ID,
                instrument_id=INSTRUMENT_ID,
                addon_trigger_policy=variant.addon_policy,
                addon_enabled=variant.addon_enabled,
                sequential_enabled=variant.sequential_enabled,
                bb_window=20,
                bb_num_std=Decimal("2"),
                arm_expiry_bars=expiry,
                require_reclaim=False,
                base_exposure_full=Decimal("1"),
                base_exposure_scout=Decimal("0.1"),
                base_sl_pct=Decimal("0.02"),
                addon_max_sl_pct=Decimal("0.01"),
                stoch_oversold=Decimal("20"),
                stoch_overbought=Decimal("80"),
                quantity_step=QUANTITY_STEP,
                min_quantity=QUANTITY_STEP,
                min_notional=Decimal("5"),
            )
            matrix.append(
                BenchmarkRunConfig(
                    ordinal=ordinal,
                    parameter_set_id=parameter_set_id,
                    variant_id=variant.variant_id,
                    machine_config=config,
                    dormant_addon_policy=not variant.addon_enabled,
                )
            )
            ordinal += 1
    _validate_matrix(matrix)
    return tuple(matrix)


def capture_runtime_versions() -> RuntimeVersions:
    """Capture the pinned local runtime without network access."""

    try:
        completed = subprocess.run(
            ("uv", "--version"),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BenchmarkManifestError(f"cannot capture uv version: {exc}") from exc
    parts = completed.stdout.strip().split()
    if len(parts) < 2 or parts[0] != "uv":
        raise BenchmarkManifestError(f"unexpected uv --version output: {completed.stdout!r}")
    versions = RuntimeVersions(
        python=platform.python_version(),
        uv=parts[1],
        nautilus_trader=nautilus_version,
        talib=talib.__version__,
    )
    versions.validate()
    return versions


def build_benchmark_manifest(
    data_metadata: MmsBetaDataMetadata,
    *,
    source_tree: SourceTreeState | None = None,
    runtime_versions: RuntimeVersions | None = None,
    created_at_utc: datetime | None = None,
    preregistration_path: Path = PREREGISTRATION_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    repo_root: Path = PROJECT_ROOT,
) -> dict[str, JsonValue]:
    """Build, but do not write, the immutable pre-run manifest document."""

    _validate_frozen_data_metadata(data_metadata)
    preregistration_hash = _file_sha256(preregistration_path)
    uv_lock_hash = _file_sha256(uv_lock_path)
    if preregistration_hash != FROZEN_PREREGISTRATION_SHA256:
        raise BenchmarkManifestError("frozen preregistration hash changed")
    if uv_lock_hash != FROZEN_UV_LOCK_SHA256:
        raise BenchmarkManifestError("frozen uv.lock hash changed")
    versions = runtime_versions or capture_runtime_versions()
    versions.validate()
    tree = source_tree or capture_source_tree_state(repo_root)
    created = created_at_utc or datetime.now(UTC)
    _require_utc(created, "created_at_utc")
    matrix = build_run_matrix()

    core = _normalized_mapping(
        {
            "runner_version": BENCHMARK_RUNNER_VERSION,
            "seed": RANDOM_SEED,
            "instrument_id": INSTRUMENT_ID,
            "timeframe": "1h",
            "starting_balance": {"amount": STARTING_BALANCE, "currency": "USDT"},
            "windows": {
                "warmup": {"start": _iso(WARMUP_START), "end": _iso(DEVELOPMENT_START)},
                "development": {
                    "start": _iso(DEVELOPMENT_START),
                    "end": _iso(HOLDOUT_START),
                },
                "holdout": {
                    "start": _iso(HOLDOUT_START),
                    "end": _iso(HOLDOUT_END),
                    "rows_read": 0,
                    "policy": "DO_NOT_LOAD_HASH_RUN_OR_REPORT",
                },
            },
            "schemas": {
                "manifest": BENCHMARK_MANIFEST_SCHEMA_VERSION,
                "data": DATA_SCHEMA_VERSION,
                "strategy": STRATEGY_VERSION,
                "snapshot": SNAPSHOT_SCHEMA_VERSION,
                "backtest_result": BACKTEST_RESULT_SCHEMA_VERSION,
                "counters": BENCHMARK_COUNTERS_SCHEMA_VERSION,
                "results_index": BENCHMARK_RESULTS_INDEX_SCHEMA_VERSION,
                "ablation": ABLATION_SCHEMA_VERSION,
            },
            "profiles": {
                "timestamp_map": TIMESTAMP_MAP_PROFILE,
                "data_timestamp_implementation": TIMESTAMP_PROFILE_ID,
                "feature_model": FEATURE_MODEL_ID,
                "funding_updates": FUNDING_PROFILE_ID,
                "wrapper_execution": PYO3_SMOKE_EXECUTION_PROFILE,
                "oms": SELECTED_OMS_MODEL,
                "backtest_close_all": PYO3_SMOKE_POSITION_MODEL,
                "fill_model": ONE_TICK_FILL_MODEL,
                "cost_model": NATIVE_COST_PROFILE,
                "latency_ns": 0,
                "bar_adaptive_high_low_ordering": True,
                "deterministic_full_fills": True,
                "maker_fee": FIXED_FEE_RATE,
                "taker_fee": FIXED_FEE_RATE,
                "price_tick": PRICE_TICK,
                "quantity_step": QUANTITY_STEP,
            },
            "cutoff": {
                "request_on_penultimate_close": _iso(
                    HOLDOUT_START.replace(microsecond=0)
                    - pd.Timedelta(hours=1, milliseconds=1).to_pytimedelta()
                ),
                "fill_on_final_close": _iso(
                    HOLDOUT_START.replace(microsecond=0)
                    - pd.Timedelta(milliseconds=1).to_pytimedelta()
                ),
                "reason": CloseReason.MANUAL.value,
                "domain_bars_blocked": 2,
            },
            "runtime_versions": versions.as_dict(),
            "source": {
                "preregistration_path": preregistration_path.relative_to(repo_root).as_posix(),
                "preregistration_sha256": preregistration_hash,
                "uv_lock_path": uv_lock_path.relative_to(repo_root).as_posix(),
                "uv_lock_sha256": uv_lock_hash,
                "runner_source_sha256": _file_sha256(Path(__file__)),
                "tree": tree.to_dict(),
            },
            "data": data_metadata.as_dict(),
            "metric_profile_id": METRIC_PROFILE_ID,
            "metric_definitions": dict(METRIC_DEFINITIONS),
            "ablation_metrics": list(ABLATION_METRICS),
            "unconditional_ineligibility_reasons": list(UNCONDITIONAL_INELIGIBILITY_REASONS),
            "run_count": 12,
            "run_matrix": [item.as_dict() for item in matrix],
            "config_hashes": {item.run_id: item.config_hash for item in matrix},
            "run_spec_hashes": {item.run_id: item.run_spec_hash for item in matrix},
            "matrix_hash": json_hash([item.as_dict() for item in matrix]),
        }
    )
    document: dict[str, JsonValue] = {
        "schema_version": BENCHMARK_MANIFEST_SCHEMA_VERSION,
        "created_at_utc": _iso(created),
        "manifest_core": core,
        "manifest_core_hash": json_hash(core),
    }
    verify_benchmark_manifest(
        document,
        preregistration_path=preregistration_path,
        uv_lock_path=uv_lock_path,
        expected_source_tree=tree,
    )
    return document


def verify_benchmark_manifest(
    document: Mapping[str, object],
    *,
    preregistration_path: Path = PREREGISTRATION_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    expected_source_tree: SourceTreeState | None = None,
) -> None:
    """Fail closed on manifest hash, matrix, file, data, or source drift."""

    if document.get("schema_version") != BENCHMARK_MANIFEST_SCHEMA_VERSION:
        raise BenchmarkManifestError("unsupported benchmark manifest schema")
    created = _required_string(document, "created_at_utc")
    _parse_utc(created, "created_at_utc")
    core = _required_mapping(document, "manifest_core")
    expected_core_hash = _required_string(document, "manifest_core_hash")
    if json_hash(core) != expected_core_hash:
        raise BenchmarkManifestError("benchmark manifest core hash mismatch")
    if core.get("run_count") != 12:
        raise BenchmarkManifestError("benchmark manifest must contain exactly 12 runs")

    expected_matrix = [item.as_dict() for item in build_run_matrix()]
    if normalize_json(core.get("run_matrix")) != normalize_json(expected_matrix):
        raise BenchmarkManifestError("run matrix differs from the frozen 2 x 6 matrix")
    expected_configs = {item.run_id: item.config_hash for item in build_run_matrix()}
    if normalize_json(core.get("config_hashes")) != normalize_json(expected_configs):
        raise BenchmarkManifestError("machine config hashes differ from the frozen matrix")
    expected_specs = {item.run_id: item.run_spec_hash for item in build_run_matrix()}
    if normalize_json(core.get("run_spec_hashes")) != normalize_json(expected_specs):
        raise BenchmarkManifestError("run spec hashes differ from the frozen matrix")
    if core.get("matrix_hash") != json_hash(expected_matrix):
        raise BenchmarkManifestError("matrix hash mismatch")

    source = _required_mapping(core, "source")
    if source.get("preregistration_sha256") != _file_sha256(preregistration_path):
        raise BenchmarkManifestError("preregistration file changed after manifest freeze")
    if source.get("uv_lock_sha256") != _file_sha256(uv_lock_path):
        raise BenchmarkManifestError("uv.lock changed after manifest freeze")
    if source.get("preregistration_sha256") != FROZEN_PREREGISTRATION_SHA256:
        raise BenchmarkManifestError("manifest does not use the frozen preregistration")
    if source.get("uv_lock_sha256") != FROZEN_UV_LOCK_SHA256:
        raise BenchmarkManifestError("manifest does not use the frozen uv.lock")
    if source.get("runner_source_sha256") != _file_sha256(Path(__file__)):
        raise BenchmarkManifestError("runner source changed after manifest freeze")
    if expected_source_tree is not None and normalize_json(source.get("tree")) != normalize_json(
        expected_source_tree.to_dict()
    ):
        raise BenchmarkManifestError("source-tree state differs from the frozen manifest")

    windows = _required_mapping(core, "windows")
    holdout = _required_mapping(windows, "holdout")
    if holdout.get("rows_read") != 0 or holdout.get("start") != _iso(HOLDOUT_START):
        raise BenchmarkManifestError("holdout boundary was not preserved")
    data = _required_mapping(core, "data")
    _validate_frozen_data_mapping(data)
    if core.get("unconditional_ineligibility_reasons") != list(UNCONDITIONAL_INELIGIBILITY_REASONS):
        raise BenchmarkManifestError("mandatory smoke-only reason codes changed")
    runtime = RuntimeVersions(
        python=_required_string(_required_mapping(core, "runtime_versions"), "python"),
        uv=_required_string(_required_mapping(core, "runtime_versions"), "uv"),
        nautilus_trader=_required_string(
            _required_mapping(core, "runtime_versions"), "nautilus_trader"
        ),
        talib=_required_string(_required_mapping(core, "runtime_versions"), "talib"),
    )
    runtime.validate()


def load_and_verify_benchmark_manifest(
    path: Path,
    *,
    preregistration_path: Path = PREREGISTRATION_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    expected_source_tree: SourceTreeState | None = None,
) -> dict[str, JsonValue]:
    """Load a JSON manifest and verify it before strategy execution."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkManifestError(f"cannot read benchmark manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise BenchmarkManifestError("benchmark manifest root must be an object")
    document = cast(dict[str, object], raw)
    verify_benchmark_manifest(
        document,
        preregistration_path=preregistration_path,
        uv_lock_path=uv_lock_path,
        expected_source_tree=expected_source_tree,
    )
    return _normalized_mapping(document)


def validate_execution_boundaries(
    data: MmsBetaDevelopmentData,
    *,
    require_frozen_counts: bool,
) -> DevelopmentCutoff:
    """Validate causal native inputs and return the two-bar cutoff controller."""

    if len(data.bars) < 2:
        raise MmsBetaBenchmarkError("benchmark execution requires at least two native bars")
    bar_timestamps = tuple(int(bar.ts_init) for bar in data.bars)
    if bar_timestamps != tuple(sorted(bar_timestamps)) or len(set(bar_timestamps)) != len(
        bar_timestamps
    ):
        raise MmsBetaBenchmarkError("native bar timestamps must be unique and sorted")
    if any(int(bar.ts_event) != int(bar.ts_init) for bar in data.bars):
        raise MmsBetaBenchmarkError("native bars must use identical close ts_event and ts_init")
    development_start_ns = _to_ns(DEVELOPMENT_START)
    holdout_start_ns = _to_ns(HOLDOUT_START)
    if bar_timestamps[0] < development_start_ns:
        raise MmsBetaBenchmarkError("native execution contains a warmup bar")
    if bar_timestamps[-1] >= holdout_start_ns:
        raise MmsBetaBenchmarkError("native execution reaches the temporal holdout")

    native_timestamps = tuple(int(item.ts_init) for item in data.native_data)
    if native_timestamps != tuple(sorted(native_timestamps)):
        raise MmsBetaBenchmarkError("combined native data must be sorted by ts_init")
    if any(timestamp >= holdout_start_ns for timestamp in native_timestamps):
        raise MmsBetaBenchmarkError("combined native data reaches the temporal holdout")
    expected_native_data = tuple(
        sorted((*data.bars, *data.funding_updates), key=lambda item: int(item.ts_init))
    )
    if len(expected_native_data) != len(data.native_data) or any(
        expected is not observed
        for expected, observed in zip(expected_native_data, data.native_data, strict=True)
    ):
        raise MmsBetaBenchmarkError(
            "combined native data must contain exactly the supplied bars and funding updates"
        )
    for update in data.funding_updates:
        settlement_ns = int(update.next_funding_ns)
        if not (development_start_ns <= settlement_ns < holdout_start_ns):
            raise MmsBetaBenchmarkError("funding settlement reaches outside development")
        if int(update.ts_init) >= settlement_ns or int(update.interval) != 28_800:
            raise MmsBetaBenchmarkError("native funding update timing contract changed")
    if tuple(data.feature_source.close_timestamps_ns) != bar_timestamps:
        raise MmsBetaBenchmarkError(
            "causal feature keys must exactly match native close timestamps"
        )
    _validate_instrument_contract(data)

    cutoff = DevelopmentCutoff(
        strategy_id=STRATEGY_ID,
        instrument_id=INSTRUMENT_ID,
        cutoff_close_ns=bar_timestamps[-2],
        final_close_ns=bar_timestamps[-1],
    )
    if require_frozen_counts:
        if len(data.bars) != DEVELOPMENT_BARS:
            raise MmsBetaBenchmarkError(
                f"frozen run requires {DEVELOPMENT_BARS} bars, got {len(data.bars)}"
            )
        if len(data.funding_updates) != DEVELOPMENT_FUNDING_ROWS:
            raise MmsBetaBenchmarkError(
                "frozen run requires "
                f"{DEVELOPMENT_FUNDING_ROWS} funding updates, got {len(data.funding_updates)}"
            )
        _validate_frozen_data_metadata(data.metadata)
        _validate_frozen_frames(data)
        expected_first = development_start_ns + HOUR_NS - MILLISECOND_NS
        expected_cutoff = holdout_start_ns - HOUR_NS - MILLISECOND_NS
        expected_final = holdout_start_ns - MILLISECOND_NS
        if (bar_timestamps[0], cutoff.cutoff_close_ns, cutoff.final_close_ns) != (
            expected_first,
            expected_cutoff,
            expected_final,
        ):
            raise MmsBetaBenchmarkError("frozen development bar boundaries changed")
    return cutoff


def run_development_benchmark_config(
    run_config: BenchmarkRunConfig,
    data: MmsBetaDevelopmentData,
    *,
    source_tree: SourceTreeState,
    verified_manifest: Mapping[str, object],
) -> BenchmarkRunArtifact:
    """Execute one strict frozen-window configuration after manifest verification."""

    verify_benchmark_manifest(verified_manifest, expected_source_tree=source_tree)
    core = _required_mapping(verified_manifest, "manifest_core")
    if normalize_json(core.get("data")) != normalize_json(data.metadata.as_dict()):
        raise BenchmarkManifestError("execution data differs from the verified manifest")
    config_hashes = _required_mapping(core, "config_hashes")
    if config_hashes.get(run_config.run_id) != run_config.config_hash:
        raise BenchmarkManifestError("execution config differs from the verified manifest")
    return _execute_benchmark_config(
        run_config,
        data,
        source_tree=source_tree,
        require_frozen_counts=True,
        synthetic_fixture=False,
    )


def run_synthetic_benchmark_dry_run(
    run_config: BenchmarkRunConfig,
    data: MmsBetaDevelopmentData,
    *,
    source_tree: SourceTreeState,
) -> BenchmarkRunArtifact:
    """Run a short explicit fixture which is never valid as P9 benchmark output."""

    return _execute_benchmark_config(
        run_config,
        data,
        source_tree=source_tree,
        require_frozen_counts=False,
        synthetic_fixture=True,
    )


def _execute_benchmark_config(
    run_config: BenchmarkRunConfig,
    data: MmsBetaDevelopmentData,
    *,
    source_tree: SourceTreeState,
    require_frozen_counts: bool,
    synthetic_fixture: bool,
) -> BenchmarkRunArtifact:
    matrix_by_id = {item.run_id: item for item in build_run_matrix()}
    expected = matrix_by_id.get(run_config.run_id)
    if expected is None or expected.as_dict() != run_config.as_dict():
        raise MmsBetaBenchmarkError("run config is outside the frozen 2 x 6 matrix")
    cutoff = validate_execution_boundaries(data, require_frozen_counts=require_frozen_counts)
    machine = MastermindStateMachine(run_config.machine_config)
    snapshot_documents: list[Mapping[str, object]] = []

    def observe_transition(_event: DomainEvent) -> None:
        snapshot_documents.append(_compact_machine_document(machine))

    native_namespace = cast(Any, nt)
    fill_model = native_namespace.DefaultFillModel(
        prob_fill_on_limit=1.0,
        prob_slippage=1.0,
        random_seed=run_config.seed,
    )
    smoke = run_pyo3_mastermind_smoke(
        machine=machine,
        strategy_id=run_config.machine_config.strategy_id,
        instrument=data.instrument,
        bar_type=data.bar_type,
        data=data.native_data,
        feature_source=data.feature_source.as_p7_source(),
        starting_balance=STARTING_BALANCE,
        fill_model=fill_model,
        before_bar_domain_events=cutoff.before_bar,
        deliver_domain_bar=cutoff.deliver_domain_bar,
        slippage_per_unit=PRICE_TICK,
        serialize_transition_snapshots=False,
        transition_observer=observe_transition,
    )
    final_snapshot = machine.snapshot_json()
    invariant_ledger = _build_invariant_ledger(
        machine=machine,
        smoke=smoke,
        cutoff=cutoff,
        transition_observation_count=len(snapshot_documents),
        final_snapshot=final_snapshot,
        data=data,
    )
    failures = [str(item["code"]) for item in invariant_ledger if item.get("passed") is not True]
    if failures:
        raise BenchmarkInvariantError(f"run {run_config.run_id} failed invariants: {failures}")

    frames = _build_result_frames(smoke.domain_events, smoke)
    counters = _derive_counters(machine, smoke.domain_events)
    stats = _build_stats(
        run_config=run_config,
        machine=machine,
        events=smoke.domain_events,
        equity=frames["equity"],
        snapshots=tuple(snapshot_documents),
        counters=counters,
        final_snapshot=final_snapshot,
        invariant_ledger=invariant_ledger,
        cutoff=cutoff,
        synthetic_fixture=synthetic_fixture,
    )
    cost_model = _native_smoke_cost_model()
    extra_reasons: tuple[str, ...] = (
        *UNCONDITIONAL_INELIGIBILITY_REASONS,
        "FILL_METHOD_CLOSE_NAIVE",
        "MARK_PRICE_MARGIN_NOT_MODELLED",
        *(("SYNTHETIC_FIXTURE",) if synthetic_fixture else ()),
    )
    eligibility = assess_eligibility(
        cost_model,
        extra_reasons=extra_reasons,
        noneligible_class=ResultClass.SMOKE_ONLY,
    )
    result = BacktestResult(
        schema_version=BACKTEST_RESULT_SCHEMA_VERSION,
        engine="nautilus_trader.core.nautilus_pyo3.BacktestEngine",
        engine_version=nautilus_version,
        strategy_version=STRATEGY_VERSION,
        source_tree=source_tree,
        stats=stats,
        equity=frames["equity"],
        trades=frames["trades"],
        orders=frames["orders"],
        fills=frames["fills"],
        positions=frames["positions"],
        funding=frames["funding"],
        data_hash=data.metadata.data_hash,
        config_hash=run_config.config_hash,
        random_seed=run_config.seed,
        cost_model=cost_model,
        eligibility=eligibility,
        fill_method=FillMethod.CLOSE_NAIVE,
        margin_method=MarginMethod.NONE,
    )
    if result.eligibility.status.value != "NOT_ELIGIBLE" or (
        result.eligibility.result_class is not ResultClass.SMOKE_ONLY
    ):
        raise BenchmarkInvariantError("P9 result escaped SMOKE_ONLY / NOT_ELIGIBLE")
    missing_reasons = set(UNCONDITIONAL_INELIGIBILITY_REASONS) - set(result.eligibility.reasons)
    if missing_reasons:
        raise BenchmarkInvariantError(f"result omitted reason codes: {sorted(missing_reasons)}")
    return BenchmarkRunArtifact(
        run_config=run_config,
        result=result,
        counters=counters,
        final_snapshot=final_snapshot,
        invariant_ledger=invariant_ledger,
    )


def run_frozen_benchmark(
    output_directory: Path,
    *,
    data: MmsBetaDevelopmentData | None = None,
    preregistration_path: Path = PREREGISTRATION_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    repo_root: Path = PROJECT_ROOT,
) -> BenchmarkSuiteArtifact:
    """Write/verify the manifest, then execute exactly the frozen twelve runs.

    This function is intentionally not called by the test suite.  The operator must
    authorize the actual metric-producing P9 execution separately.
    """

    if output_directory.exists():
        raise MmsBetaBenchmarkError(f"refusing to reuse output directory {output_directory}")
    bundle = data or load_mms_beta_development_data()
    _validate_frozen_data_metadata(bundle.metadata)
    source_tree = capture_source_tree_state(repo_root)
    manifest = build_benchmark_manifest(
        bundle.metadata,
        source_tree=source_tree,
        preregistration_path=preregistration_path,
        uv_lock_path=uv_lock_path,
        repo_root=repo_root,
    )
    if capture_source_tree_state(repo_root) != source_tree:
        raise BenchmarkManifestError("source tree changed while constructing pre-run manifest")

    output_directory.mkdir(parents=True)
    manifest_path = output_directory / "experiment_manifest.json"
    _write_new_json(manifest_path, manifest)
    verified_manifest = load_and_verify_benchmark_manifest(
        manifest_path,
        preregistration_path=preregistration_path,
        uv_lock_path=uv_lock_path,
        expected_source_tree=source_tree,
    )

    artifacts: list[BenchmarkRunArtifact] = []
    for run_config in build_run_matrix():
        artifact = run_development_benchmark_config(
            run_config,
            bundle,
            source_tree=source_tree,
            verified_manifest=verified_manifest,
        )
        artifact.save(output_directory / "runs" / run_config.run_id)
        artifacts.append(artifact)
    if len(artifacts) != 12:
        raise BenchmarkInvariantError("frozen suite did not produce exactly twelve results")

    stats_by_run = {artifact.run_config.run_id: artifact.result.stats for artifact in artifacts}
    ablation = build_ablation_summary(stats_by_run)
    ablation_path = output_directory / "ablation.csv"
    ablation.to_csv(ablation_path, index=False, lineterminator="\n")
    ablation_hash = _file_sha256(ablation_path)
    results_index = {
        "schema_version": BENCHMARK_RESULTS_INDEX_SCHEMA_VERSION,
        "manifest_core_hash": verified_manifest["manifest_core_hash"],
        "result_count": len(artifacts),
        "results": [artifact.summary() for artifact in artifacts],
        "ablation": {
            "schema_version": ABLATION_SCHEMA_VERSION,
            "path": "ablation.csv",
            "sha256": ablation_hash,
            "rows": len(ablation),
        },
        "interpretation": "DESCRIPTIVE_MECHANICS_ONLY_NO_RANKING",
    }
    _write_new_json(output_directory / "results_index.json", results_index)
    return BenchmarkSuiteArtifact(
        manifest=verified_manifest,
        runs=tuple(artifacts),
        ablation=ablation,
        output_directory=output_directory,
    )


def build_ablation_summary(
    stats_by_run: Mapping[str, Mapping[str, object]],
) -> pd.DataFrame:
    """Compute frozen deltas and interactions without ranking or inference."""

    matrix = build_run_matrix()
    expected_ids = {item.run_id for item in matrix}
    if set(stats_by_run) != expected_ids:
        missing = sorted(expected_ids - set(stats_by_run))
        extra = sorted(set(stats_by_run) - expected_ids)
        raise MmsBetaBenchmarkError(
            f"ablation needs exact matrix; missing={missing}, extra={extra}"
        )
    rows: list[dict[str, object]] = []
    variants = [variant.variant_id for variant in _VARIANTS]
    contrasts: tuple[tuple[str, str, Mapping[str, int]], ...] = (
        ("V2_MINUS_V1", "V2 - V1", {"V2_BASE_SEQ": 1, "V1_BASE_ONLY": -1}),
        ("V3_MINUS_V1", "V3 - V1", {"V3_BASE_CC": 1, "V1_BASE_ONLY": -1}),
        ("V4_MINUS_V1", "V4 - V1", {"V4_BASE_STOCH": 1, "V1_BASE_ONLY": -1}),
        (
            "SEQ_X_CC",
            "V5 - V2 - V3 + V1",
            {
                "V5_BASE_SEQ_CC": 1,
                "V2_BASE_SEQ": -1,
                "V3_BASE_CC": -1,
                "V1_BASE_ONLY": 1,
            },
        ),
        (
            "SEQ_X_STOCH",
            "V6 - V2 - V4 + V1",
            {
                "V6_BASE_SEQ_STOCH": 1,
                "V2_BASE_SEQ": -1,
                "V4_BASE_STOCH": -1,
                "V1_BASE_ONLY": 1,
            },
        ),
    )
    for parameter_set_id, _expiry in _PARAMETER_SETS:
        by_variant = {
            variant: stats_by_run[f"{parameter_set_id}__{variant}"] for variant in variants
        }
        base = by_variant["V1_BASE_ONLY"]
        for variant in variants:
            row: dict[str, object] = {
                "schema_version": ABLATION_SCHEMA_VERSION,
                "parameter_set_id": parameter_set_id,
                "comparison_kind": "VARIANT_MINUS_BASE",
                "comparison_id": f"{variant}_MINUS_V1_BASE_ONLY",
                "formula": f"{variant} - V1_BASE_ONLY",
            }
            for metric in ABLATION_METRICS:
                row[metric] = _difference(
                    _numeric_stat(by_variant[variant], metric),
                    _numeric_stat(base, metric),
                )
            rows.append(row)
        for comparison_id, formula, weights in contrasts:
            row = {
                "schema_version": ABLATION_SCHEMA_VERSION,
                "parameter_set_id": parameter_set_id,
                "comparison_kind": "FROZEN_CONTRAST",
                "comparison_id": comparison_id,
                "formula": formula,
            }
            for metric in ABLATION_METRICS:
                values = [
                    (_numeric_stat(by_variant[variant], metric), weight)
                    for variant, weight in weights.items()
                ]
                row[metric] = _weighted_contrast(values)
            rows.append(row)
    columns = (
        "schema_version",
        "parameter_set_id",
        "comparison_kind",
        "comparison_id",
        "formula",
        *ABLATION_METRICS,
    )
    return pd.DataFrame.from_records(rows, columns=columns)


def _build_result_frames(
    events: Sequence[DomainEvent],
    smoke: Pyo3SmokeRun,
) -> dict[str, pd.DataFrame]:
    equity_events = [event for event in events if isinstance(event, AccountEquityUpdated)]
    if not equity_events:
        raise BenchmarkInvariantError("native run published no equity history")
    equity = pd.DataFrame(
        {"equity": [float(event.equity) for event in equity_events]},
        index=pd.DatetimeIndex(
            [event.occurred_at_utc for event in equity_events],
            name="timestamp",
        ),
    )
    closed = [event for event in events if isinstance(event, PositionClosed)]
    trades = pd.DataFrame.from_records(
        [
            {
                "event_id": event.event_id,
                "setup_id": event.setup_id,
                "exit_time": event.occurred_at_utc,
                "close_reason": event.close_reason.value,
                "gross_price_pnl": float(event.realized_price_pnl),
                "commissions": float(event.commissions),
                "funding_net": float(event.funding),
                "slippage_cost": float(event.realized_slippage_cost),
                "setup_net_pnl": float(
                    event.realized_price_pnl
                    - event.commissions
                    + event.funding
                    - event.realized_slippage_cost
                ),
            }
            for event in closed
        ]
    )
    funding_events = [event for event in events if isinstance(event, FundingApplied)]
    funding = pd.DataFrame.from_records(
        [
            {
                "settlement_id": event.settlement_id,
                "event_time": event.occurred_at_utc,
                "setup_id": event.setup_id,
                "amount": float(event.amount),
                "currency": "USDT",
                "provenance": "NAUTILUS_NATIVE_FUNDING_ADJUSTMENT",
            }
            for event in funding_events
        ]
    )
    return {
        "equity": _stable_frame(equity),
        "trades": _stable_frame(trades),
        "orders": _stable_frame(smoke.reports.orders),
        "fills": _stable_frame(smoke.reports.fills),
        "positions": _stable_frame(smoke.reports.positions),
        "funding": _stable_frame(funding),
    }


def _derive_counters(
    machine: MastermindStateMachine,
    events: Sequence[DomainEvent],
) -> dict[str, int]:
    state_counters = machine.state.counters
    domain_fills = _unique_domain_fills(events)
    base_fills = [event for event in domain_fills if event.role is OrderRole.BASE_ENTRY]
    addon_fills = [event for event in domain_fills if event.role is OrderRole.ADDON_ENTRY]
    base_submissions = sum(
        1
        for event in events
        if isinstance(event, OrderSubmitted) and event.role is OrderRole.BASE_ENTRY
    )
    addon_submissions = sum(
        1
        for event in events
        if isinstance(event, OrderSubmitted) and event.role is OrderRole.ADDON_ENTRY
    )
    reaction_without_intent = sum(
        diagnostic == "BASE_REACTION_WITHOUT_CONFIRMED_EQUITY"
        for diagnostic in machine.state.diagnostics
    )
    base_local_rejections = state_counters.get("base_local_rejections", 0)
    base_entry_intents = state_counters.get("base_entries", 0)
    counters = {
        "base_reaction_facts": (
            base_entry_intents + base_local_rejections + reaction_without_intent
        ),
        "base_entry_intents": base_entry_intents,
        "base_submissions": base_submissions,
        "setups_started": len({event.setup_id for event in base_fills}),
        "addon_trigger_facts": state_counters.get("addon_trigger_facts", 0),
        "addon_intents": state_counters.get("addon_intents", 0),
        "addon_submissions": addon_submissions,
        "addon_first_fills": len({event.setup_id for event in addon_fills}),
        "addon_fill_deltas": len(addon_fills),
        "addon_rejections": state_counters.get("addon_rejections", 0),
        "addon_sl_count": state_counters.get("addon_stop_count", 0),
        "full_base_sl_count": state_counters.get("full_base_sl_count", 0),
        "full_to_scout_transitions": state_counters.get("full_to_scout_transitions", 0),
        "scout_setups": state_counters.get("scout_setups", 0),
        "scout_to_full_rearms": state_counters.get("scout_to_full_rearms", 0),
        "funding_settlements": state_counters.get("funding_settlements", 0),
        "invariant_violation_count": machine.state.invariant_violation_count,
    }
    if tuple(counters) != COUNTER_NAMES:
        raise AssertionError("counter schema drift")
    return counters


def _unique_domain_fills(
    events: Sequence[DomainEvent],
) -> tuple[OrderPartiallyFilled | OrderFilled, ...]:
    unique: list[OrderPartiallyFilled | OrderFilled] = []
    payloads: dict[str, tuple[object, ...]] = {}
    for event in events:
        if not isinstance(event, _DOMAIN_FILL_TYPES):
            continue
        payload = (
            event.role,
            event.last_quantity,
            event.cumulative_quantity,
            event.price,
            event.commission,
            event.benchmark_price,
            event.setup_id,
            event.client_order_id,
        )
        previous = payloads.get(event.execution_id)
        if previous is None:
            payloads[event.execution_id] = payload
            unique.append(event)
        elif previous != payload:
            raise BenchmarkInvariantError(
                f"execution ID {event.execution_id} has conflicting fill payloads"
            )
    return tuple(unique)


def _build_stats(
    *,
    run_config: BenchmarkRunConfig,
    machine: MastermindStateMachine,
    events: Sequence[DomainEvent],
    equity: pd.DataFrame,
    snapshots: Sequence[Mapping[str, object]],
    counters: Mapping[str, int],
    final_snapshot: str,
    invariant_ledger: Sequence[Mapping[str, JsonValue]],
    cutoff: DevelopmentCutoff,
    synthetic_fixture: bool,
) -> dict[str, JsonValue]:
    closed = [event for event in events if isinstance(event, PositionClosed)]
    funding_events = [event for event in events if isinstance(event, FundingApplied)]
    domain_fills = _unique_domain_fills(events)
    gross_price_pnl = sum((event.realized_price_pnl for event in closed), start=Decimal(0))
    commissions = sum((event.commissions for event in closed), start=Decimal(0))
    funding_net = sum((event.amount for event in funding_events), start=Decimal(0))
    funding_paid = sum(
        (-event.amount for event in funding_events if event.amount < 0), start=Decimal(0)
    )
    funding_received = sum(
        (event.amount for event in funding_events if event.amount > 0), start=Decimal(0)
    )
    slippage = sum((event.realized_slippage_cost for event in closed), start=Decimal(0))
    setup_net = gross_price_pnl - commissions + funding_net - slippage
    fill_notional = sum(
        (event.price * event.last_quantity for event in domain_fills), start=Decimal(0)
    )
    first_equity = float(equity["equity"].iloc[0])
    final_equity = float(equity["equity"].iloc[-1])
    if first_equity <= 0:
        raise BenchmarkInvariantError("equity history starts non-positive")
    return_pct = 100.0 * (final_equity / first_equity - 1.0)
    sharpe = _descriptive_h1_sharpe(equity["equity"])
    max_drawdown_pct = _max_drawdown_pct(equity["equity"])
    turnover = float(fill_notional) / first_equity
    exposure = _snapshot_exposure_maxima(snapshots)
    scout_episodes, scout_right_censored = _scout_episodes(events, snapshots)
    scout_mean = sum(scout_episodes) / len(scout_episodes) if scout_episodes else 0.0
    final_document = _parse_json_mapping(final_snapshot, "final snapshot")
    stats = _normalized_mapping(
        {
            "runner_version": BENCHMARK_RUNNER_VERSION,
            "metric_profile_id": METRIC_PROFILE_ID,
            "run_id": run_config.run_id,
            "parameter_set_id": run_config.parameter_set_id,
            "variant_id": run_config.variant_id,
            "synthetic_fixture": synthetic_fixture,
            "counters": dict(counters),
            **dict(counters),
            "scout_episode_bars": scout_episodes,
            "scout_episode_mean_bars": scout_mean,
            "scout_right_censored": scout_right_censored,
            **exposure,
            "gross_price_pnl": gross_price_pnl,
            "commissions": commissions,
            "funding_paid": funding_paid,
            "funding_received": funding_received,
            "funding_net": funding_net,
            "slippage_cost": slippage,
            "setup_net_pnl": setup_net,
            "final_equity": final_equity,
            "return_pct": return_pct,
            "sharpe_h1_descriptive": sharpe,
            "max_drawdown_pct": max_drawdown_pct,
            "turnover": turnover,
            "cutoff_close_ns": cutoff.cutoff_close_ns,
            "final_close_ns": cutoff.final_close_ns,
            "final_state": {
                "risk_mode": machine.state.risk_mode.value,
                "position_build": machine.state.position_build.value,
                "order_lifecycle": machine.state.order_lifecycle.value,
                "real_open_quantity": machine.state.real_open_quantity,
                "active_domain_orders": sum(
                    order.status.active for order in machine.state.orders.values()
                ),
                "outbox_size": len(machine.state.outbox),
            },
            "final_snapshot_schema_version": final_document.get("schema_version"),
            "final_snapshot_sha256": hashlib.sha256(final_snapshot.encode("utf-8")).hexdigest(),
            "invariant_ledger": list(invariant_ledger),
        }
    )
    return stats


def _build_invariant_ledger(
    *,
    machine: MastermindStateMachine,
    smoke: Pyo3SmokeRun,
    cutoff: DevelopmentCutoff,
    transition_observation_count: int,
    final_snapshot: str,
    data: MmsBetaDevelopmentData,
) -> tuple[dict[str, JsonValue], ...]:
    machine.assert_invariants()
    state = machine.state
    funding_events = [event for event in smoke.domain_events if isinstance(event, FundingApplied)]
    domain_fills = _unique_domain_fills(smoke.domain_events)
    position_closed = [event for event in smoke.domain_events if isinstance(event, PositionClosed)]
    settlement_ids = [event.settlement_id for event in funding_events]
    fill_commissions = sum((event.commission for event in domain_fills), start=Decimal(0))
    closed_commissions = sum((event.commissions for event in position_closed), start=Decimal(0))
    native_funding_net = sum((event.amount for event in funding_events), start=Decimal(0))
    closed_funding_net = sum((event.funding for event in position_closed), start=Decimal(0))
    expected_funding_settlements = _expected_funding_settlements(
        data,
        smoke.domain_events,
    )
    observed_funding_settlements = sorted(_to_ns(event.occurred_at_utc) for event in funding_events)
    expected_slippage = sum(
        (event.last_quantity * PRICE_TICK for event in domain_fills), start=Decimal(0)
    )
    closed_slippage = sum(
        (event.realized_slippage_cost for event in position_closed), start=Decimal(0)
    )
    observation_count_matches = transition_observation_count == len(smoke.domain_events)
    try:
        restored = MastermindStateMachine.from_snapshot(machine.config, final_snapshot)
        snapshot_roundtrip = restored.snapshot_json() == final_snapshot
    except (TypeError, ValueError):
        snapshot_roundtrip = False
    delivered_bars = [event for event in smoke.domain_events if isinstance(event, BarClosed)]
    active_domain_orders = [
        order.client_order_id for order in state.orders.values() if order.status.active
    ]
    pending_outbox = [
        {"intent_id": intent.intent_id, "kind": intent.kind.value} for intent in state.outbox
    ]
    native_open_order_ids = _native_open_order_ids(smoke.reports.orders)
    unallocated_funding = [
        diagnostic
        for diagnostic in state.diagnostics
        if diagnostic.startswith("UNALLOCATED_FUNDING:")
    ]
    checks: tuple[tuple[str, object, object, bool], ...] = (
        (
            "INVARIANT_VIOLATION_COUNT_ZERO",
            state.invariant_violation_count,
            0,
            state.invariant_violation_count == 0,
        ),
        (
            "FINAL_DOMAIN_POSITION_FLAT",
            state.position_build.value,
            PositionBuild.FLAT.value,
            state.position_build is PositionBuild.FLAT,
        ),
        (
            "FINAL_DOMAIN_QUANTITY_ZERO",
            state.real_open_quantity,
            Decimal(0),
            state.real_open_quantity == 0,
        ),
        (
            "FINAL_NATIVE_QUANTITY_ZERO",
            smoke.final_net_quantity,
            Decimal(0),
            smoke.final_net_quantity == 0,
        ),
        (
            "FINAL_ORDER_LIFECYCLE_NONE",
            state.order_lifecycle.value,
            OrderLifecycle.NONE.value,
            state.order_lifecycle is OrderLifecycle.NONE,
        ),
        ("FINAL_SETUP_NONE", state.setup is None, True, state.setup is None),
        (
            "NO_ACTIVE_DOMAIN_ORDERS",
            active_domain_orders,
            [],
            not active_domain_orders,
        ),
        (
            "NO_ACTIVE_NATIVE_ORDERS",
            native_open_order_ids,
            [],
            not native_open_order_ids,
        ),
        (
            "FINAL_OUTBOX_EMPTY",
            pending_outbox,
            [],
            not pending_outbox,
        ),
        ("MANUAL_CUTOFF_EMITTED_ONCE", cutoff.emitted_count, 1, cutoff.emitted_count == 1),
        (
            "NO_DOMAIN_BAR_AT_OR_AFTER_CUTOFF",
            max(
                (int(_to_ns(event.close_time_utc)) for event in delivered_bars),
                default=-1,
            ),
            f"<{cutoff.cutoff_close_ns}",
            all(_to_ns(event.close_time_utc) < cutoff.cutoff_close_ns for event in delivered_bars),
        ),
        (
            "FUNDING_SETTLEMENT_IDS_UNIQUE",
            len(settlement_ids),
            len(settlement_ids),
            len(settlement_ids) == len(set(settlement_ids)),
        ),
        (
            "FUNDING_LEDGER_RECONCILED",
            sorted(state.pnl.funding_settlement_ids),
            sorted(set(settlement_ids)),
            state.pnl.funding_settlement_ids == set(settlement_ids),
        ),
        (
            "NO_UNALLOCATED_FUNDING",
            unallocated_funding,
            [],
            not unallocated_funding,
        ),
        (
            "NATIVE_COMMISSION_EVIDENCE_PRESENT",
            [event.commission for event in domain_fills],
            "positive commission per fill or no fills",
            not domain_fills or all(event.commission > 0 for event in domain_fills),
        ),
        (
            "COMMISSION_LEDGER_RECONCILED",
            closed_commissions,
            fill_commissions,
            closed_commissions == fill_commissions,
        ),
        (
            "FUNDING_AMOUNT_LEDGER_RECONCILED",
            closed_funding_net,
            native_funding_net,
            closed_funding_net == native_funding_net,
        ),
        (
            "NATIVE_FUNDING_SETTLEMENTS_COMPLETE",
            observed_funding_settlements,
            expected_funding_settlements,
            observed_funding_settlements == expected_funding_settlements,
        ),
        (
            "ONE_TICK_SLIPPAGE_LEDGER_RECONCILED",
            closed_slippage,
            expected_slippage,
            closed_slippage == expected_slippage,
        ),
        (
            "TRANSITION_OBSERVER_COUNT_MATCHES_DOMAIN_EVENTS",
            transition_observation_count,
            len(smoke.domain_events),
            observation_count_matches,
        ),
        (
            "FINAL_SNAPSHOT_ROUNDTRIP",
            snapshot_roundtrip,
            True,
            snapshot_roundtrip,
        ),
        (
            "NO_HOLDOUT_NATIVE_DATA",
            max((int(item.ts_init) for item in data.native_data), default=-1),
            f"<{_to_ns(HOLDOUT_START)}",
            all(int(item.ts_init) < _to_ns(HOLDOUT_START) for item in data.native_data),
        ),
    )
    return tuple(
        _normalized_mapping(
            {
                "code": code,
                "observed": observed,
                "expected": expected,
                "passed": passed,
            }
        )
        for code, observed, expected, passed in checks
    )


def _expected_funding_settlements(
    data: MmsBetaDevelopmentData,
    events: Sequence[DomainEvent],
) -> list[int]:
    first_entries: dict[str, int] = {}
    closes: dict[str, int] = {}
    for event in events:
        setup_id = event.setup_id
        if setup_id is None:
            continue
        if isinstance(event, _DOMAIN_FILL_TYPES) and event.role is OrderRole.BASE_ENTRY:
            first_entries[setup_id] = min(
                first_entries.get(setup_id, _to_ns(event.occurred_at_utc)),
                _to_ns(event.occurred_at_utc),
            )
        elif isinstance(event, PositionClosed):
            closes[setup_id] = _to_ns(event.occurred_at_utc)
    if set(first_entries) != set(closes):
        raise BenchmarkInvariantError("cannot derive closed setup intervals for funding audit")
    intervals = [(first_entries[key], closes[key]) for key in sorted(first_entries)]
    return sorted(
        int(update.next_funding_ns)
        for update in data.funding_updates
        if any(start <= int(update.next_funding_ns) < end for start, end in intervals)
    )


def _native_smoke_cost_model() -> CostModel:
    return CostModel(
        identifier=NATIVE_COST_PROFILE,
        commission=CostComponent(
            model_id="nautilus-native-crypto-perpetual-fixed-taker-0.0004-v1",
            provenance=CostProvenance.NATIVE,
            complete=True,
            research_eligible=False,
            notes=("engine-applied", "fixed-historical-tier-proxy"),
        ),
        funding=CostComponent(
            model_id="nautilus-native-historical-funding-settlements-v1",
            provenance=CostProvenance.HISTORICAL,
            complete=True,
            research_eligible=False,
            notes=("native-settlement", "no-historical-mark-price"),
        ),
        slippage=CostComponent(
            model_id=ONE_TICK_FILL_MODEL,
            provenance=CostProvenance.APPROXIMATE,
            complete=True,
            research_eligible=False,
            notes=("native-fill-model", "one-adverse-price-tick"),
        ),
        execution=CostComponent(
            model_id=PYO3_SMOKE_EXECUTION_PROFILE,
            provenance=CostProvenance.APPROXIMATE,
            complete=True,
            research_eligible=False,
            notes=("h1-ohlc-heuristic", "decomposed-close-all-not-binance-parity"),
        ),
    )


def _snapshot_exposure_maxima(
    snapshots: Sequence[Mapping[str, object]],
) -> dict[str, float]:
    max_committed_quote = Decimal(0)
    max_actual_quote = Decimal(0)
    max_committed_multiplier = Decimal(0)
    max_actual_multiplier = Decimal(0)
    for document in snapshots:
        setup_raw = document.get("setup")
        if not isinstance(setup_raw, Mapping):
            continue
        setup = cast(Mapping[str, object], setup_raw)
        equity = _decimal_from_object(setup.get("setup_start_equity"), "setup_start_equity")
        base_target = _decimal_from_object(
            setup.get("base_target_notional"), "base_target_notional"
        )
        addon_target = _decimal_from_object(
            setup.get("addon_target_notional"), "addon_target_notional"
        )
        actual = _decimal_from_object(setup.get("actual_entry_notional"), "actual_entry_notional")
        committed = base_target + addon_target
        max_committed_quote = max(max_committed_quote, committed)
        max_actual_quote = max(max_actual_quote, actual)
        if equity > 0:
            max_committed_multiplier = max(max_committed_multiplier, committed / equity)
            max_actual_multiplier = max(max_actual_multiplier, actual / equity)
    return {
        "max_committed_target_quote": float(max_committed_quote),
        "max_gross_realized_exposure_quote": float(max_actual_quote),
        "max_committed_exposure_multiplier": float(max_committed_multiplier),
        "max_actual_gross_exposure_multiplier": float(max_actual_multiplier),
    }


def _scout_episodes(
    events: Sequence[DomainEvent],
    snapshots: Sequence[Mapping[str, object]],
) -> tuple[list[int], bool]:
    if len(events) != len(snapshots):
        raise BenchmarkInvariantError("domain event and snapshot counts differ")
    episodes: list[int] = []
    current: int | None = None
    previous_mode = RiskMode.FULL.value
    for event, snapshot in zip(events, snapshots, strict=True):
        mode = snapshot.get("risk_mode")
        if mode not in {RiskMode.FULL.value, RiskMode.SCOUT.value}:
            raise BenchmarkInvariantError("snapshot contains an unknown risk mode")
        if previous_mode == RiskMode.FULL.value and mode == RiskMode.SCOUT.value:
            current = 0
        if isinstance(event, BarClosed) and mode == RiskMode.SCOUT.value:
            if current is None:
                current = 0
            current += 1
        if previous_mode == RiskMode.SCOUT.value and mode == RiskMode.FULL.value:
            episodes.append(0 if current is None else current)
            current = None
        previous_mode = mode
    right_censored = previous_mode == RiskMode.SCOUT.value
    if right_censored:
        episodes.append(0 if current is None else current)
    return episodes, right_censored


def _descriptive_h1_sharpe(equity: pd.Series) -> float | None:
    returns = equity.astype(float).pct_change(fill_method=None).dropna()
    if len(returns) < 2:
        return None
    standard_deviation = float(returns.std(ddof=1))
    if not math.isfinite(standard_deviation) or standard_deviation <= 0:
        return None
    value = math.sqrt(8_760.0) * float(returns.mean()) / standard_deviation
    return value if math.isfinite(value) else None


def _max_drawdown_pct(equity: pd.Series) -> float:
    values = equity.astype(float)
    if values.empty or bool((values <= 0).any()):
        raise BenchmarkInvariantError("drawdown requires positive non-empty equity")
    drawdown = values / values.cummax() - 1.0
    result = 100.0 * float(drawdown.min())
    return result if math.isfinite(result) else 0.0


def _validate_matrix(matrix: Sequence[BenchmarkRunConfig]) -> None:
    if len(matrix) != 12:
        raise AssertionError("P9 matrix must contain exactly twelve runs")
    if [item.ordinal for item in matrix] != list(range(1, 13)):
        raise AssertionError("P9 matrix ordinals must be contiguous")
    run_ids = [item.run_id for item in matrix]
    if len(set(run_ids)) != len(run_ids):
        raise AssertionError("P9 matrix run IDs must be unique")
    config_hashes = [item.config_hash for item in matrix]
    if len(set(config_hashes)) != len(config_hashes):
        raise AssertionError("P9 machine config hashes must be unique")
    forbidden = {
        AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH,
        AddonTriggerPolicy.CANDLE_AND_STOCH,
    }
    if any(item.machine_config.addon_trigger_policy in forbidden for item in matrix):
        raise AssertionError("non-preregistered add-on policy entered P9 matrix")


def _validate_frozen_data_metadata(metadata: MmsBetaDataMetadata) -> None:
    _validate_frozen_data_mapping(metadata.as_dict())


def _validate_frozen_data_mapping(metadata: Mapping[str, object]) -> None:
    expected_scalars: Mapping[str, object] = {
        "schema_version": DATA_SCHEMA_VERSION,
        "instrument_id": INSTRUMENT_ID,
        "bar_type": f"{INSTRUMENT_ID}-1-HOUR-LAST-EXTERNAL",
        "warmup_start_utc": _iso(WARMUP_START),
        "development_start_utc": _iso(DEVELOPMENT_START),
        "holdout_start_utc": _iso(HOLDOUT_START),
        "warmup_bars": WARMUP_BARS,
        "development_bars": DEVELOPMENT_BARS,
        "funding_updates": DEVELOPMENT_FUNDING_ROWS,
        "timestamp_profile": TIMESTAMP_PROFILE_ID,
        "feature_model": FEATURE_MODEL_ID,
        "funding_profile": FUNDING_PROFILE_ID,
        "holdout_rows_read": 0,
        "nautilus_version": EXPECTED_NAUTILUS_VERSION,
        "talib_version": EXPECTED_TALIB_VERSION,
    }
    for name, expected in expected_scalars.items():
        if metadata.get(name) != expected:
            raise BenchmarkManifestError(
                f"frozen data metadata drift for {name}: {metadata.get(name)!r} != {expected!r}"
            )
    for name, expected_hash in FROZEN_DATA_HASHES.items():
        if metadata.get(name) != expected_hash:
            raise BenchmarkManifestError(f"frozen data hash drift for {name}")


def _validate_frozen_frames(data: MmsBetaDevelopmentData) -> None:
    warmup_development = data.ohlcv_with_warmup
    development = data.development_ohlcv
    funding = data.funding_rates
    if len(warmup_development) != WARMUP_BARS + DEVELOPMENT_BARS:
        raise MmsBetaBenchmarkError("warmup+development OHLCV row count changed")
    if len(development) != DEVELOPMENT_BARS:
        raise MmsBetaBenchmarkError("development OHLCV row count changed")
    if len(funding) != DEVELOPMENT_FUNDING_ROWS:
        raise MmsBetaBenchmarkError("development funding row count changed")
    _validate_utc_frame_index(warmup_development, "warmup+development OHLCV")
    _validate_utc_frame_index(development, "development OHLCV")
    _validate_utc_frame_index(funding, "development funding")
    expected_last_open = pd.Timestamp(HOLDOUT_START) - pd.Timedelta(hours=1)
    if warmup_development.index[0] != pd.Timestamp(WARMUP_START) or (
        warmup_development.index[-1] != expected_last_open
    ):
        raise MmsBetaBenchmarkError("warmup+development OHLCV boundaries changed")
    if development.index[0] != pd.Timestamp(DEVELOPMENT_START) or (
        development.index[-1] != expected_last_open
    ):
        raise MmsBetaBenchmarkError("development OHLCV boundaries changed")
    if funding.index[0] < pd.Timestamp(DEVELOPMENT_START) or funding.index[-1] >= pd.Timestamp(
        HOLDOUT_START
    ):
        raise MmsBetaBenchmarkError("development funding reaches outside the frozen window")


def _validate_utc_frame_index(frame: pd.DataFrame, label: str) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise MmsBetaBenchmarkError(f"{label} index must be DatetimeIndex")
    if frame.index.tz is None or str(frame.index.tz) not in {"UTC", "UTC+00:00"}:
        raise MmsBetaBenchmarkError(f"{label} index must be timezone-aware UTC")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise MmsBetaBenchmarkError(f"{label} index must be unique and sorted")


def _validate_instrument_contract(data: MmsBetaDevelopmentData) -> None:
    instrument = data.instrument
    checks = {
        "instrument_id": (str(instrument.id), INSTRUMENT_ID),
        "price_increment": (str(instrument.price_increment), str(PRICE_TICK)),
        "size_increment": (str(instrument.size_increment), str(QUANTITY_STEP)),
        "maker_fee": (Decimal(str(instrument.maker_fee)), FIXED_FEE_RATE),
        "taker_fee": (Decimal(str(instrument.taker_fee)), FIXED_FEE_RATE),
    }
    failures = [name for name, (actual, expected) in checks.items() if actual != expected]
    if failures:
        raise MmsBetaBenchmarkError(f"native instrument cost/precision drift: {failures}")


def _native_open_order_ids(orders: pd.DataFrame) -> list[str]:
    if orders.empty:
        return []
    if "status" not in orders.columns:
        raise BenchmarkInvariantError("native order report has no status column")
    id_column = "client_order_id" if "client_order_id" in orders.columns else None
    open_ids: list[str] = []
    for index, row in orders.iterrows():
        status = str(row["status"]).upper()
        if status not in _TERMINAL_NATIVE_ORDER_STATUSES:
            identifier = row[id_column] if id_column is not None else index
            open_ids.append(str(identifier))
    return sorted(open_ids)


def _stable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    if result.empty:
        return result.reset_index(drop=True) if isinstance(result.index, pd.RangeIndex) else result
    if isinstance(result.index, pd.DatetimeIndex):
        return result.sort_index(kind="stable")
    preferred = [
        name
        for name in (
            "ts_event",
            "ts_init",
            "event_time",
            "exit_time",
            "client_order_id",
            "event_id",
            "settlement_id",
        )
        if name in result.columns
    ]
    if preferred:
        result = result.sort_values(preferred, kind="stable")
    return result.reset_index(drop=True)


def _compact_machine_document(machine: MastermindStateMachine) -> Mapping[str, object]:
    setup = machine.state.setup
    compact: dict[str, object] = {"risk_mode": machine.state.risk_mode.value}
    if setup is not None:
        compact["setup"] = {
            "setup_start_equity": setup.setup_start_equity,
            "base_target_notional": setup.base_target_notional,
            "addon_target_notional": setup.addon_target_notional,
            "actual_entry_notional": setup.actual_entry_notional,
        }
    else:
        compact["setup"] = None
    return compact


def _parse_json_mapping(raw: str, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BenchmarkInvariantError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BenchmarkInvariantError(f"{label} root must be an object")
    return cast(dict[str, object], value)


def _numeric_stat(stats: Mapping[str, object], name: str) -> float | None:
    value = stats.get(name)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _weighted_contrast(values: Iterable[tuple[float | None, int]]) -> float | None:
    total = 0.0
    for value, weight in values:
        if value is None:
            return None
        total += value * weight
    return total


def _decimal_from_object(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise BenchmarkInvariantError(f"snapshot {field} is not decimal-compatible")
    try:
        decimal = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise BenchmarkInvariantError(f"snapshot {field} is not decimal-compatible") from exc
    if not decimal.is_finite():
        raise BenchmarkInvariantError(f"snapshot {field} is non-finite")
    return decimal


def _normalized_mapping(value: object) -> dict[str, JsonValue]:
    normalized = normalize_json(value)
    if not isinstance(normalized, dict):
        raise MmsBetaBenchmarkError("expected a JSON object after normalization")
    return normalized


def _required_mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise BenchmarkManifestError(f"manifest {key} must be an object")
    return cast(Mapping[str, object], value)


def _required_string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise BenchmarkManifestError(f"manifest {key} must be a non-empty string")
    return value


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkManifestError(f"manifest {field} is not ISO UTC") from exc
    _require_utc(parsed, field)
    return parsed


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise BenchmarkManifestError(f"{field} must be timezone-aware UTC")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BenchmarkManifestError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _write_new_json(path: Path, value: object) -> None:
    _write_new_text(path, canonical_json(value) + "\n")


def _write_new_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise MmsBetaBenchmarkError(f"refusing to overwrite artifact {path}") from exc
    except OSError as exc:
        raise MmsBetaBenchmarkError(f"cannot write artifact {path}: {exc}") from exc


def _datetime_from_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanoseconds // 1_000)


def _to_ns(value: datetime) -> int:
    _require_utc(value, "timestamp")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (
        delta.days * 86_400_000_000_000 + delta.seconds * 1_000_000_000 + delta.microseconds * 1_000
    )


def _iso(value: datetime) -> str:
    _require_utc(value, "timestamp")
    return value.isoformat().replace("+00:00", "Z")
