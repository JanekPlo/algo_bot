"""Wznawialna, outcome-blind orkiestracja sweepu MR-Session 4."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast

from algo_bot.engine.backtest_result import (
    BacktestResult,
    FillMethod,
    JsonValue,
    MarginMethod,
    SourceTreeState,
    canonical_json,
    capture_source_tree_state,
    json_hash,
    normalize_json,
)
from algo_bot.engine.mr_session4_bybit import (
    FrozenBybitContracts,
    freeze_bybit_contracts,
    load_frozen_bybit_contracts,
)
from algo_bot.engine.mr_session4_contract import (
    EXPECTED_RUNS,
    SYMBOLS,
    assess_performance,
    build_run_matrix,
    contract_core,
    contract_hash,
)
from algo_bot.engine.mr_session4_data import (
    DEVELOPMENT_START,
    HOLDOUT_START,
    PROCESSED_DATA_ROOT,
    WARMUP_START,
    Session4DataError,
    load_session4_data,
    runtime_versions,
)
from algo_bot.engine.mr_session4_execution import (
    COST_PROFILE_ID,
    DEFAULT_NATIVE_LEVERAGE,
    EXECUTION_PROFILE_ID,
    MARGIN_PROFILE_ID,
    METRIC_PROFILE_ID,
    SESSION4_INVARIANT_CODES,
    STARTING_BALANCE,
    Session4ExecutionError,
    Session4RunArtifact,
    run_session4_spec,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACTS_PATH = PROJECT_ROOT / "config/experiments/mr-session-4-bybit-contracts.json"
DEFAULT_PREREGISTRATION_PATH = PROJECT_ROOT / "docs/experiments/mr-session-4-preregistration.md"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "results/experiments/mr-session-4-manifest.json"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"

MANIFEST_SCHEMA_VERSION = "mr_session4_manifest/1"
PROGRESS_SCHEMA_VERSION = "mr_session4_progress/1"
COMPLETION_SCHEMA_VERSION = "mr_session4_run_complete/1"
FAILURE_SCHEMA_VERSION = "mr_session4_attempt_failure/1"
ATTEMPT_STARTED_SCHEMA_VERSION = "mr_session4_attempt_started/1"
RESULTS_INDEX_SCHEMA_VERSION = "mr_session4_results_index/1"
SUITE_COMPLETE_SCHEMA_VERSION = "mr_session4_suite_complete/1"
RUNNER_VERSION = "mr_session4_runner/1"
MIN_FREE_GIB = 60.0
DEFAULT_WORKERS = 2
DEFAULT_MAX_ATTEMPTS = 2
RETRY_PROFILE_ID = "SAME_RUN_CONFIG_SEED_OSERROR_TIMEOUT_ONLY_V1"

_PREREGISTRATION_CORE_MARKER = re.compile(
    r"^<!-- mr-session-4-manifest-core-sha256: (PENDING|[0-9a-f]{64}) -->$",
    flags=re.MULTILINE,
)
_PREREGISTRATION_TAG = re.compile(
    r"^mr-session-4-preregistration-(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})$"
)

_OUTCOME_FIELDS = {
    "pnl",
    "profit",
    "return",
    "sharpe",
    "sortino",
    "calmar",
    "drawdown",
    "win_rate",
    "liquidation",
    "eligibility",
    "performance",
}

_SESSION4_METRIC_KEYS = (
    "total_return_fraction",
    "cagr_fraction",
    "sharpe",
    "sortino",
    "calmar",
    "mar",
    "max_drawdown_fraction",
    "max_drawdown_display_pct",
    "max_drawdown_duration_days",
    "recovery_time_days",
    "profit_factor",
    "win_rate_fraction",
    "n_trades",
    "periods_per_year",
)
_LIQUIDATION_NULL_METRIC_KEYS = tuple(
    key for key in _SESSION4_METRIC_KEYS if key != "periods_per_year"
)
_LIQUIDATION_NULL_ECONOMIC_FIELDS = (
    "gross_price_pnl",
    "commissions",
    "funding_paid",
    "funding_received",
    "funding_net",
    "slippage_cost",
    "setup_net_pnl",
    "final_equity",
    "turnover",
    "closed_setup_rows",
)


class Session4RunnerError(RuntimeError):
    """Manifest, artefakt albo orkiestracja narusza kontrakt Session 4."""


class Session4ManifestError(Session4RunnerError):
    """Pre-run manifest nie zgadza się z zamrożonym środowiskiem."""


class Session4ArtifactError(Session4RunnerError):
    """Ukończony katalog runu jest niepełny lub zmieniony."""


@dataclass(frozen=True, slots=True)
class VerifiedRun:
    """Minimalne, pozbawione wyniku potwierdzenie kompletnego artefaktu."""

    run_id: str
    ordinal: int
    artifact_hash: str
    completion_hash: str
    summary: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class VerifiedFailure:
    """Zweryfikowany, hash-chroniony zapis jednej nieudanej próby."""

    run_id: str
    attempt: int
    retryable: bool
    failure_class: str
    failure_hash: str
    failed_at_utc: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class VerifiedStart:
    """Zweryfikowany zapis rozpoczęcia próby, trwały przed obliczeniami."""

    run_id: str
    attempt: int
    started_at_utc: str
    started_hash: str


def build_experiment_core(
    contracts: FrozenBybitContracts,
    *,
    contracts_path: Path = DEFAULT_CONTRACTS_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    data_root: Path = PROCESSED_DATA_ROOT,
    repo_root: Path = PROJECT_ROOT,
) -> dict[str, JsonValue]:
    """Buduje samoniezależny rdzeń, którego hash można wpisać do prerejestracji."""

    data_metadata: dict[str, JsonValue] = {}
    for symbol in SYMBOLS:
        symbol_contract = contracts.symbols[symbol]
        bundle = load_session4_data(
            symbol,
            maintenance_margin_tiers=symbol_contract.maintenance_margin_tiers,
            risk_tiers_hash=contracts.contract_hash,
            data_root=data_root,
        )
        data_metadata[symbol] = bundle.metadata.as_dict()
    return {
        "runner_version": RUNNER_VERSION,
        "contract_hash": contract_hash(),
        "contract": contract_core(),
        "run_count": EXPECTED_RUNS,
        "windows": {
            "warmup_start_utc": _iso(WARMUP_START),
            "development_start_utc": _iso(DEVELOPMENT_START),
            "holdout_start_utc": _iso(HOLDOUT_START),
            "strategy_holdout_rows_read": 0,
            "holdout_policy": "NO_STRATEGY_LOAD_HASH_RUN_OR_REPORT_DURING_SESSION_4",
        },
        "profiles": _scientific_profiles(),
        "runtime_versions": _runtime_manifest_versions(),
        "implementation": {
            "uv_lock_path": _relative_path(uv_lock_path, repo_root),
            "uv_lock_sha256": _file_sha256(uv_lock_path),
            "runner_sources": _runner_source_hashes(),
        },
        "bybit_contracts": {
            "path": _relative_path(contracts_path, repo_root),
            "contract_hash": contracts.contract_hash,
            "captured_at_utc": contracts.captured_at_utc,
            "document_sha256": _file_sha256(contracts_path),
        },
        "data": data_metadata,
        "operator_integrity_access_disclosure": (
            "Full-file hashes/integrity were inspected during runner audit; "
            "no strategy holdout rows or holdout metrics were loaded."
        ),
    }


def build_experiment_manifest(
    contracts: FrozenBybitContracts,
    *,
    contracts_path: Path = DEFAULT_CONTRACTS_PATH,
    preregistration_path: Path = DEFAULT_PREREGISTRATION_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    data_root: Path = PROCESSED_DATA_ROOT,
    repo_root: Path = PROJECT_ROOT,
    created_at: datetime | None = None,
    source_tree: SourceTreeState | None = None,
) -> dict[str, JsonValue]:
    """Dodaje do rdzenia audytowalne provenance z czystego, zamrożonego commita."""

    tree = source_tree or capture_source_tree_state(repo_root)
    if tree.is_dirty:
        raise Session4ManifestError("source tree must be clean before manifest freeze")
    preregistration_tag = _preregistration_tag_for_commit(repo_root, tree.git_commit)
    timestamp = created_at or datetime.now(UTC)
    _require_utc(timestamp, "created_at")
    core = build_experiment_core(
        contracts,
        contracts_path=contracts_path,
        uv_lock_path=uv_lock_path,
        data_root=data_root,
        repo_root=repo_root,
    )
    core_hash = json_hash(core)
    _verify_preregistration_core_marker(preregistration_path, core_hash)
    provenance: dict[str, JsonValue] = {
        "tree": tree.to_dict(),
        "preregistration_tag": preregistration_tag,
        "preregistration_path": _relative_path(preregistration_path, repo_root),
        "preregistration_sha256": _file_sha256(preregistration_path),
    }
    document: dict[str, JsonValue] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at_utc": _iso(timestamp),
        "manifest_core": core,
        "manifest_core_hash": core_hash,
        "provenance": provenance,
        "provenance_hash": json_hash(provenance),
    }
    verify_experiment_manifest(
        document,
        contracts_path=contracts_path,
        preregistration_path=preregistration_path,
        uv_lock_path=uv_lock_path,
        repo_root=repo_root,
        expected_source_tree=tree,
        verify_data=False,
        data_root=data_root,
    )
    return document


def verify_experiment_manifest(
    document: Mapping[str, object],
    *,
    contracts_path: Path = DEFAULT_CONTRACTS_PATH,
    preregistration_path: Path = DEFAULT_PREREGISTRATION_PATH,
    uv_lock_path: Path = UV_LOCK_PATH,
    repo_root: Path = PROJECT_ROOT,
    expected_source_tree: SourceTreeState | None = None,
    verify_data: bool = False,
    verify_runtime: bool = True,
    data_root: Path = PROCESSED_DATA_ROOT,
) -> FrozenBybitContracts:
    """Failuje przed wykonaniem przy dowolnym drift contract/code/data/runtime."""

    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise Session4ManifestError("unsupported Session 4 manifest schema")
    _parse_utc(_required_str(document, "created_at_utc"), "created_at_utc")
    core = _required_mapping(document, "manifest_core")
    manifest_core_hash = _required_str(document, "manifest_core_hash")
    if json_hash(core) != manifest_core_hash:
        raise Session4ManifestError("manifest core hash mismatch")
    _verify_preregistration_core_marker(preregistration_path, manifest_core_hash)
    provenance = _required_mapping(document, "provenance")
    if json_hash(provenance) != _required_str(document, "provenance_hash"):
        raise Session4ManifestError("manifest provenance hash mismatch")
    if core.get("contract_hash") != contract_hash() or normalize_json(
        core.get("contract")
    ) != normalize_json(contract_core()):
        raise Session4ManifestError("frozen 528-run contract drift")
    if core.get("run_count") != EXPECTED_RUNS:
        raise Session4ManifestError("manifest must contain exactly 528 runs")
    windows = _required_mapping(core, "windows")
    if (
        windows.get("holdout_start_utc") != _iso(HOLDOUT_START)
        or windows.get("strategy_holdout_rows_read") != 0
    ):
        raise Session4ManifestError("holdout boundary/policy drift")
    profiles = _required_mapping(core, "profiles")
    _verify_scientific_profiles(profiles)

    contracts = load_frozen_bybit_contracts(contracts_path)
    bybit = _required_mapping(core, "bybit_contracts")
    if (
        bybit.get("path") != _relative_path(contracts_path, repo_root)
        or bybit.get("contract_hash") != contracts.contract_hash
        or bybit.get("document_sha256") != _file_sha256(contracts_path)
    ):
        raise Session4ManifestError("frozen Bybit contracts drift")
    if provenance.get("preregistration_sha256") != _file_sha256(preregistration_path):
        raise Session4ManifestError("preregistration changed after manifest freeze")
    frozen_tree = SourceTreeState.from_dict(_required_mapping(provenance, "tree"))
    preregistration_tag = _preregistration_tag_for_commit(repo_root, frozen_tree.git_commit)
    if provenance.get("preregistration_tag") != preregistration_tag:
        raise Session4ManifestError("preregistration tag differs from frozen manifest")
    implementation = _required_mapping(core, "implementation")
    if implementation.get("uv_lock_sha256") != _file_sha256(uv_lock_path):
        raise Session4ManifestError("uv.lock changed after manifest freeze")
    if normalize_json(implementation.get("runner_sources")) != normalize_json(
        _runner_source_hashes()
    ):
        raise Session4ManifestError("runner source changed after manifest freeze")
    tree = expected_source_tree or capture_source_tree_state(repo_root)
    if normalize_json(provenance.get("tree")) != normalize_json(tree.to_dict()):
        raise Session4ManifestError("source tree differs from frozen manifest")
    runtime = _required_mapping(core, "runtime_versions")
    if verify_runtime and normalize_json(runtime) != normalize_json(_runtime_manifest_versions()):
        raise Session4ManifestError("runtime versions differ from frozen manifest")

    if verify_data:
        stored_data = _required_mapping(core, "data")
        for symbol in SYMBOLS:
            bundle = load_session4_data(
                symbol,
                maintenance_margin_tiers=contracts.symbols[symbol].maintenance_margin_tiers,
                risk_tiers_hash=contracts.contract_hash,
                data_root=data_root,
            )
            if normalize_json(stored_data.get(symbol)) != normalize_json(bundle.metadata.as_dict()):
                raise Session4ManifestError(f"{symbol} data differs from frozen manifest")
    return contracts


def load_and_verify_manifest(
    path: Path,
    **kwargs: Any,
) -> tuple[dict[str, JsonValue], FrozenBybitContracts]:
    """Ładuje JSON i zwraca go dopiero po pełnej weryfikacji."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Session4ManifestError(f"cannot read experiment manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise Session4ManifestError("experiment manifest must be a JSON object")
    document = cast(dict[str, object], raw)
    contracts = verify_experiment_manifest(document, **kwargs)
    normalized = normalize_json(document)
    if not isinstance(normalized, dict):
        raise AssertionError("manifest must normalize to object")
    return normalized, contracts


def run_suite(
    *,
    manifest_path: Path,
    output_directory: Path,
    contracts_path: Path = DEFAULT_CONTRACTS_PATH,
    preregistration_path: Path = DEFAULT_PREREGISTRATION_PATH,
    data_root: Path = PROCESSED_DATA_ROOT,
    workers: int = DEFAULT_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    resume: bool = False,
    selected_run_ids: Sequence[str] = (),
    min_free_gib: float = MIN_FREE_GIB,
) -> None:
    """Uruchamia maksymalnie jeden długowieczny proces na symbol."""

    if workers < 1 or workers > len(SYMBOLS):
        raise Session4RunnerError(f"workers must be in [1, {len(SYMBOLS)}]")
    if max_attempts != DEFAULT_MAX_ATTEMPTS:
        raise Session4RunnerError(f"max_attempts is frozen at exactly {DEFAULT_MAX_ATTEMPTS}")
    if min_free_gib < MIN_FREE_GIB:
        raise Session4RunnerError(
            f"minimum free space cannot be lowered below frozen {MIN_FREE_GIB:.1f} GiB"
        )
    manifest, _contracts = load_and_verify_manifest(
        manifest_path,
        contracts_path=contracts_path,
        preregistration_path=preregistration_path,
        data_root=data_root,
        verify_data=True,
    )
    manifest_core_hash = _required_str(manifest, "manifest_core_hash")
    _require_free_space(output_directory, min_free_gib)
    _initialize_suite_directory(
        output_directory,
        manifest,
        resume=resume,
    )
    matrix = build_run_matrix()
    _reconcile_staged_runs(
        output_directory,
        matrix,
        expected_manifest_core_hash=manifest_core_hash,
    )
    _verify_previous_completed_progress(output_directory, matrix, manifest)
    selected = _select_run_specs(matrix, selected_run_ids)
    all_verified = _verified_runs(
        output_directory,
        matrix,
        expected_manifest_core_hash=manifest_core_hash,
    )
    verified = {
        spec.run_id: all_verified[spec.run_id] for spec in selected if spec.run_id in all_verified
    }
    pending = [spec for spec in selected if spec.run_id not in verified]
    for spec in pending:
        _assert_retry_allowed(output_directory, spec.run_id)
    progress = _new_progress(manifest, matrix, output_directory)
    _mark_verified_progress_complete(progress, output_directory, all_verified)
    _atomic_write_json(output_directory / "progress.json", progress)
    if not pending:
        return

    source_raw = _required_mapping(manifest, "provenance")
    manifest_core = _required_mapping(manifest, "manifest_core")
    expected_data = _required_mapping(manifest_core, "data")
    expected_runtime = _required_mapping(manifest_core, "runtime_versions")
    tree = SourceTreeState.from_dict(_required_mapping(source_raw, "tree"))
    progress_runs = progress.get("runs")
    if not isinstance(progress_runs, dict):
        raise Session4RunnerError("progress runs mapping is missing")
    partitions: dict[str, list[tuple[str, int]]] = {symbol: [] for symbol in SYMBOLS}
    for spec in pending:
        row = progress_runs.get(spec.run_id)
        if not isinstance(row, dict):
            raise Session4RunnerError(f"progress row missing for {spec.run_id}")
        prior_attempts = int(str(row.get("attempts", 0)))
        next_attempt = prior_attempts + 1
        if next_attempt > max_attempts:
            raise Session4RunnerError(
                f"run {spec.run_id} exhausted frozen retry budget ({prior_attempts}/{max_attempts})"
            )
        partitions[spec.symbol].append((spec.run_id, next_attempt))
    work = [(symbol, run_attempts) for symbol, run_attempts in partitions.items() if run_attempts]
    context = mp.get_context("spawn")
    messages: Any = context.Queue()
    active: list[Any] = []
    waiting = list(work)
    fatal_error: str | None = None
    try:
        while waiting or active:
            while waiting and len(active) < workers:
                symbol, run_attempts = waiting.pop(0)
                process = context.Process(
                    target=_symbol_worker,
                    args=(
                        symbol,
                        run_attempts,
                        str(output_directory),
                        str(contracts_path),
                        str(data_root),
                        tree.to_dict(),
                        _required_mapping(expected_data, symbol),
                        expected_runtime,
                        manifest_core_hash,
                        max_attempts,
                        messages,
                    ),
                    name=f"mr-s4-{symbol}",
                )
                process.start()
                active.append(process)
            try:
                message = cast(dict[str, object], messages.get(timeout=1.0))
            except queue.Empty:
                message = {}
            if message:
                fatal_error = _apply_worker_message(progress, message)
                _atomic_write_json(output_directory / "progress.json", progress)
            survivors: list[Any] = []
            for process in active:
                if process.is_alive():
                    survivors.append(process)
                    continue
                process.join()
                if process.exitcode not in (0, None) and fatal_error is None:
                    fatal_error = f"worker {process.name} exited with code {process.exitcode}"
            active = survivors
            if fatal_error is not None:
                for process in active:
                    process.terminate()
                for process in active:
                    process.join()
                raise Session4RunnerError(fatal_error)
    except KeyboardInterrupt:
        for process in active:
            process.terminate()
        for process in active:
            process.join()
        progress["interrupted_at_utc"] = _now_iso()
        _atomic_write_json(output_directory / "progress.json", progress)
        raise

    final_verified = _verified_runs(
        output_directory,
        selected,
        expected_manifest_core_hash=manifest_core_hash,
    )
    if len(final_verified) != len(selected):
        missing = sorted(spec.run_id for spec in selected if spec.run_id not in final_verified)
        raise Session4RunnerError(f"suite stopped with incomplete selected runs: {missing[:5]}")
    _mark_verified_progress_complete(progress, output_directory, final_verified)
    _atomic_write_json(output_directory / "progress.json", progress)


def verify_suite(
    output_directory: Path,
    *,
    manifest_path: Path,
    contracts_path: Path = DEFAULT_CONTRACTS_PATH,
    preregistration_path: Path = DEFAULT_PREREGISTRATION_PATH,
    allow_incomplete: bool = False,
) -> tuple[VerifiedRun, ...]:
    """Weryfikuje głęboko artefakty, ale niczego nie rankinguje ani nie drukuje."""

    manifest, _contracts = load_and_verify_manifest(
        manifest_path,
        contracts_path=contracts_path,
        preregistration_path=preregistration_path,
        verify_data=False,
        verify_runtime=False,
    )
    stored = _read_json_object(output_directory / "experiment_manifest.json")
    if normalize_json(stored) != normalize_json(manifest):
        raise Session4ArtifactError("suite manifest copy differs from source manifest")
    matrix = build_run_matrix()
    verified = _verified_runs(
        output_directory,
        matrix,
        expected_manifest_core_hash=_required_str(manifest, "manifest_core_hash"),
    )
    if not allow_incomplete and len(verified) != EXPECTED_RUNS:
        raise Session4ArtifactError(f"suite is incomplete: {len(verified)}/{EXPECTED_RUNS}")
    return tuple(verified[key] for key in sorted(verified, key=lambda item: verified[item].ordinal))


def finalize_suite(
    output_directory: Path,
    *,
    manifest_path: Path,
    contracts_path: Path = DEFAULT_CONTRACTS_PATH,
    preregistration_path: Path = DEFAULT_PREREGISTRATION_PATH,
) -> None:
    """Tworzy indeks dopiero po 528 głęboko zweryfikowanych completion markers."""

    verified = verify_suite(
        output_directory,
        manifest_path=manifest_path,
        contracts_path=contracts_path,
        preregistration_path=preregistration_path,
        allow_incomplete=False,
    )
    index: dict[str, JsonValue] = {
        "schema_version": RESULTS_INDEX_SCHEMA_VERSION,
        "run_count": len(verified),
        "runs": [normalize_json(item.summary) for item in verified],
    }
    index_path = output_directory / "results_index.json"
    if index_path.exists():
        if normalize_json(_read_json_object(index_path)) != normalize_json(index):
            raise Session4ArtifactError("existing results index conflicts with verified runs")
    else:
        _atomic_write_json(index_path, index, refuse_existing=True)
    manifest_sha256 = _file_sha256(output_directory / "experiment_manifest.json")
    index_sha256 = _file_sha256(index_path)
    completion_path = output_directory / "suite_complete.json"
    if completion_path.exists():
        stored_completion = _read_json_object(completion_path)
        _parse_utc(
            _required_str(stored_completion, "completed_at_utc"),
            "completed_at_utc",
        )
        expected_stable = {
            "schema_version": SUITE_COMPLETE_SCHEMA_VERSION,
            "run_count": len(verified),
            "results_index_sha256": index_sha256,
            "experiment_manifest_sha256": manifest_sha256,
        }
        observed_stable = {
            key: value for key, value in stored_completion.items() if key != "completed_at_utc"
        }
        if normalize_json(observed_stable) != normalize_json(expected_stable):
            raise Session4ArtifactError("existing suite completion marker conflicts")
        return
    completion: dict[str, JsonValue] = {
        "schema_version": SUITE_COMPLETE_SCHEMA_VERSION,
        "completed_at_utc": _now_iso(),
        "run_count": len(verified),
        "results_index_sha256": index_sha256,
        "experiment_manifest_sha256": manifest_sha256,
    }
    _atomic_write_json(completion_path, completion, refuse_existing=True)


def _symbol_worker(
    symbol: str,
    run_attempts: Sequence[tuple[str, int]],
    output_text: str,
    contracts_text: str,
    data_root_text: str,
    source_tree_raw: Mapping[str, object],
    expected_data_metadata: Mapping[str, object],
    expected_runtime: Mapping[str, object],
    manifest_core_hash: str,
    max_attempts: int,
    messages: Any,
) -> None:
    output = Path(output_text)
    try:
        if normalize_json(_runtime_manifest_versions()) != normalize_json(expected_runtime):
            raise Session4ManifestError("worker runtime differs from frozen manifest")
        contracts = load_frozen_bybit_contracts(Path(contracts_text))
        data = load_session4_data(
            symbol,
            maintenance_margin_tiers=contracts.symbols[symbol].maintenance_margin_tiers,
            risk_tiers_hash=contracts.contract_hash,
            data_root=Path(data_root_text),
        )
        if normalize_json(data.metadata.as_dict()) != normalize_json(expected_data_metadata):
            raise Session4ManifestError(f"{symbol} worker data differs from frozen manifest")
        source_tree = SourceTreeState.from_dict(source_tree_raw)
        by_id = {item.run_id: item for item in build_run_matrix()}
        for run_id, first_attempt in run_attempts:
            spec = by_id[run_id]
            for attempt in range(first_attempt, max_attempts + 1):
                started = time.monotonic()
                started_at_utc = _now_iso()
                _write_attempt_started(
                    output,
                    _started_record(run_id, attempt, started_at_utc),
                )
                messages.put(
                    {
                        "kind": "STARTED",
                        "run_id": run_id,
                        "attempt": attempt,
                        "started_at_utc": started_at_utc,
                    }
                )
                try:
                    artifact = run_session4_spec(spec, data, source_tree=source_tree)
                    _save_run_atomically(
                        output,
                        artifact,
                        attempt=attempt,
                        manifest_core_hash=manifest_core_hash,
                        started_at_utc=started_at_utc,
                        started_monotonic=started,
                    )
                except (OSError, TimeoutError) as exc:
                    elapsed = time.monotonic() - started
                    if _recover_staged_run(
                        output,
                        spec,
                        expected_manifest_core_hash=manifest_core_hash,
                    ):
                        messages.put(
                            {
                                "kind": "COMPLETE",
                                "run_id": run_id,
                                "attempt": attempt,
                                "elapsed_seconds": elapsed,
                                "completed_at_utc": _now_iso(),
                            }
                        )
                        break
                    failure = _failure_record(
                        run_id,
                        attempt,
                        exc,
                        retryable=True,
                        elapsed_seconds=elapsed,
                    )
                    _write_attempt_failure(output, failure)
                    messages.put(
                        {
                            "kind": "FAILED_RETRYABLE",
                            "run_id": run_id,
                            "attempt": attempt,
                            "failure_class": type(exc).__name__,
                            "elapsed_seconds": elapsed,
                        }
                    )
                    if attempt < max_attempts:
                        continue
                    raise
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    failure = _failure_record(
                        run_id,
                        attempt,
                        exc,
                        retryable=False,
                        elapsed_seconds=elapsed,
                    )
                    _write_attempt_failure(output, failure)
                    messages.put(
                        {
                            "kind": "FAILED_FATAL",
                            "run_id": run_id,
                            "attempt": attempt,
                            "failure_class": type(exc).__name__,
                            "message": str(exc),
                            "elapsed_seconds": elapsed,
                        }
                    )
                    return
                completed_at_utc = _now_iso()
                messages.put(
                    {
                        "kind": "COMPLETE",
                        "run_id": run_id,
                        "attempt": attempt,
                        "elapsed_seconds": time.monotonic() - started,
                        "completed_at_utc": completed_at_utc,
                    }
                )
                break
    except Exception as exc:
        messages.put(
            {
                "kind": "WORKER_FATAL",
                "run_id": None,
                "attempt": 0,
                "failure_class": type(exc).__name__,
                "message": str(exc),
            }
        )


def _save_run_atomically(
    output_directory: Path,
    artifact: Session4RunArtifact,
    *,
    attempt: int,
    manifest_core_hash: str,
    started_at_utc: str | None = None,
    started_monotonic: float | None = None,
) -> None:
    run_id = artifact.run_spec.run_id
    final = output_directory / "runs" / run_id
    if final.exists():
        raise Session4ArtifactError(f"refusing to overwrite completed run {run_id}")
    partial_root = output_directory / "runs" / ".partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    stage = partial_root / f"{run_id}.a{attempt:03d}.{os.getpid()}.{uuid.uuid4().hex}"
    if stage.exists():
        raise Session4ArtifactError(f"staging directory collision: {stage}")
    stage.mkdir()
    artifact.result.save(stage / "backtest_result")
    restored = BacktestResult.load(stage / "backtest_result")
    if restored.artifact_hash() != artifact.result.artifact_hash():
        raise Session4ArtifactError("BacktestResult round-trip identity mismatch")
    _write_new_json(stage / "run_spec.json", artifact.run_spec.as_dict())
    _write_new_json(stage / "counters.json", dict(artifact.counters))
    _write_new_json(stage / "invariant_ledger.json", {"checks": list(artifact.invariant_ledger)})
    _write_new_text(stage / "final_snapshot.json", artifact.final_snapshot + "\n")
    summary = artifact.summary()
    _write_new_json(stage / "run_summary.json", summary)
    completion_started_at = started_at_utc or _now_iso()
    _parse_utc(completion_started_at, "started_at_utc")
    completion_core: dict[str, JsonValue] = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "run_id": run_id,
        "ordinal": artifact.run_spec.ordinal,
        "attempt": attempt,
        "retry_profile": RETRY_PROFILE_ID,
        "manifest_core_hash": manifest_core_hash,
        "started_at_utc": completion_started_at,
        "completed_at_utc": _now_iso(),
        "elapsed_seconds": (
            0.0
            if started_monotonic is None
            else round(max(0.0, time.monotonic() - started_monotonic), 6)
        ),
        "run_spec_hash": artifact.run_spec.run_spec_hash,
        "artifact_hash": artifact.result.artifact_hash(),
        "run_spec_sha256": _file_sha256(stage / "run_spec.json"),
        "counters_sha256": _file_sha256(stage / "counters.json"),
        "invariant_ledger_sha256": _file_sha256(stage / "invariant_ledger.json"),
        "run_summary_sha256": _file_sha256(stage / "run_summary.json"),
        "final_snapshot_sha256": _file_sha256(stage / "final_snapshot.json"),
    }
    completion = {**completion_core, "completion_hash": json_hash(completion_core)}
    _write_new_json(stage / "complete.json", completion)
    _fsync_tree(stage)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stage, final)
    _fsync_directory(final.parent)
    verify_completed_run(
        final,
        expected_spec=artifact.run_spec,
        expected_manifest_core_hash=manifest_core_hash,
    )


def verify_completed_run(
    final_directory: Path,
    *,
    expected_spec: Any,
    expected_manifest_core_hash: str,
) -> VerifiedRun:
    completion = _read_json_object(final_directory / "complete.json")
    if completion.get("schema_version") != COMPLETION_SCHEMA_VERSION:
        raise Session4ArtifactError(f"invalid completion schema in {final_directory}")
    completion_hash = _required_str(completion, "completion_hash")
    completion_core = {key: value for key, value in completion.items() if key != "completion_hash"}
    if json_hash(completion_core) != completion_hash:
        raise Session4ArtifactError(f"completion hash mismatch in {final_directory}")
    attempt = completion.get("attempt")
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
        or attempt > DEFAULT_MAX_ATTEMPTS
    ):
        raise Session4ArtifactError(f"invalid completion attempt in {final_directory}")
    if completion.get("retry_profile") != RETRY_PROFILE_ID:
        raise Session4ArtifactError(f"completion retry profile drift in {final_directory}")
    if completion.get("manifest_core_hash") != expected_manifest_core_hash:
        raise Session4ArtifactError(f"completion manifest core mismatch in {final_directory}")
    started_at = _parse_utc(_required_str(completion, "started_at_utc"), "started_at_utc")
    completed_at = _parse_utc(_required_str(completion, "completed_at_utc"), "completed_at_utc")
    elapsed = completion.get("elapsed_seconds")
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0.0
        or completed_at < started_at
    ):
        raise Session4ArtifactError(f"invalid completion timing in {final_directory}")
    spec_raw = _read_json_object(final_directory / "run_spec.json")
    if normalize_json(spec_raw) != normalize_json(expected_spec.as_dict()):
        raise Session4ArtifactError(f"run spec mismatch in {final_directory}")
    summary = _read_json_object(final_directory / "run_summary.json")
    counters = _read_json_object(final_directory / "counters.json")
    invariants = _read_json_object(final_directory / "invariant_ledger.json")
    result = BacktestResult.load(final_directory / "backtest_result")
    result.assert_research_eligible()
    if result.fill_method is not FillMethod.NAUTILUS_NATIVE_BAR or (
        result.margin_method is not MarginMethod.MARK_PRICE_ISOLATED
    ):
        raise Session4ArtifactError(f"evidence methods drift in {final_directory}")
    if (
        completion.get("run_id") != expected_spec.run_id
        or completion.get("ordinal") != expected_spec.ordinal
    ):
        raise Session4ArtifactError(f"completion identity mismatch in {final_directory}")
    if completion.get("run_spec_hash") != expected_spec.run_spec_hash:
        raise Session4ArtifactError(f"completion spec hash mismatch in {final_directory}")
    if result.config_hash != expected_spec.config_hash:
        raise Session4ArtifactError(f"strategy config hash mismatch in {final_directory}")
    _verify_session4_result_contract(
        result,
        invariants=invariants,
        expected_spec=expected_spec,
        final_directory=final_directory,
    )
    sidecar_hashes = {
        "run_spec_sha256": final_directory / "run_spec.json",
        "counters_sha256": final_directory / "counters.json",
        "invariant_ledger_sha256": final_directory / "invariant_ledger.json",
        "run_summary_sha256": final_directory / "run_summary.json",
        "final_snapshot_sha256": final_directory / "final_snapshot.json",
    }
    for field, path in sidecar_hashes.items():
        if completion.get(field) != _file_sha256(path):
            raise Session4ArtifactError(f"{field} mismatch in {final_directory}")
    if normalize_json(summary.get("counters")) != normalize_json(counters):
        raise Session4ArtifactError(f"counter sidecar mismatch in {final_directory}")
    if normalize_json(summary.get("invariant_ledger")) != normalize_json(invariants.get("checks")):
        raise Session4ArtifactError(f"invariant sidecar mismatch in {final_directory}")
    expected_summary_identity = {
        "run_id": expected_spec.run_id,
        "ordinal": expected_spec.ordinal,
        "run_spec_hash": expected_spec.run_spec_hash,
        "evidence_gate_passed": True,
    }
    for field, expected in expected_summary_identity.items():
        if summary.get(field) != expected:
            raise Session4ArtifactError(f"summary {field} mismatch in {final_directory}")
    if normalize_json(summary.get("performance_gate")) != normalize_json(
        result.stats.get("performance_gate")
    ):
        raise Session4ArtifactError(f"performance assessment mismatch in {final_directory}")
    artifact_hash = result.artifact_hash()
    if (
        completion.get("artifact_hash") != artifact_hash
        or summary.get("artifact_hash") != artifact_hash
    ):
        raise Session4ArtifactError(f"artifact hash mismatch in {final_directory}")
    return VerifiedRun(
        run_id=expected_spec.run_id,
        ordinal=expected_spec.ordinal,
        artifact_hash=artifact_hash,
        completion_hash=completion_hash,
        summary=summary,
    )


def _verify_session4_result_contract(
    result: BacktestResult,
    *,
    invariants: Mapping[str, object],
    expected_spec: Any,
    final_directory: Path,
) -> None:
    if result.random_seed != expected_spec.seed:
        raise Session4ArtifactError(f"random seed mismatch in {final_directory}")
    stats = result.stats
    expected_identity = {
        "run_id": expected_spec.run_id,
        "ordinal": expected_spec.ordinal,
        "run_spec_hash": expected_spec.run_spec_hash,
        "config_hash": expected_spec.config_hash,
        "symbol": expected_spec.symbol,
        "marking_timeframe": expected_spec.marking_timeframe,
        "parameter_set_id": expected_spec.parameter_set.parameter_set_id,
        "variant_id": expected_spec.variant.variant_id,
    }
    for field, expected in expected_identity.items():
        if stats.get(field) != expected:
            raise Session4ArtifactError(f"stats identity {field} mismatch in {final_directory}")

    checks = invariants.get("checks")
    if len(SESSION4_INVARIANT_CODES) != 30 or not isinstance(checks, list) or len(checks) != 30:
        raise Session4ArtifactError(f"invariant ledger size drift in {final_directory}")
    codes: list[str] = []
    for check in checks:
        if not isinstance(check, dict) or check.get("passed") is not True:
            raise Session4ArtifactError(f"failed invariant check in {final_directory}")
        code = check.get("code")
        if not isinstance(code, str):
            raise Session4ArtifactError(f"invalid invariant code in {final_directory}")
        codes.append(code)
    if tuple(codes) != SESSION4_INVARIANT_CODES or len(set(codes)) != len(codes):
        raise Session4ArtifactError(f"invariant ledger order/identity drift in {final_directory}")
    if normalize_json(stats.get("invariant_ledger")) != normalize_json(checks):
        raise Session4ArtifactError(f"result stats invariant ledger mismatch in {final_directory}")

    evidence = stats.get("evidence_gate_passed")
    if evidence is not True:
        raise Session4ArtifactError(f"evidence gate mismatch in {final_directory}")
    liquidation_count = stats.get("liquidation_event_count")
    if (
        not isinstance(liquidation_count, int)
        or isinstance(liquidation_count, bool)
        or liquidation_count < 0
        or liquidation_count != len(result.liquidation_events)
    ):
        raise Session4ArtifactError(f"liquidation count mismatch in {final_directory}")
    metrics = stats.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(_SESSION4_METRIC_KEYS):
        raise Session4ArtifactError(f"metric contract drift in {final_directory}")
    for field in _SESSION4_METRIC_KEYS:
        if normalize_json(stats.get(field)) != normalize_json(metrics.get(field)):
            raise Session4ArtifactError(f"top-level metric {field} mismatch in {final_directory}")
    interpretable = stats.get("economic_metrics_interpretable")
    if interpretable is not (liquidation_count == 0):
        raise Session4ArtifactError(
            f"economic metric interpretation flag mismatch in {final_directory}"
        )
    if liquidation_count:
        if any(metrics.get(field) is not None for field in _LIQUIDATION_NULL_METRIC_KEYS):
            raise Session4ArtifactError(
                f"liquidation economic metrics must be null in {final_directory}"
            )
        if metrics.get("periods_per_year") != 8_760.0:
            raise Session4ArtifactError(f"liquidation periods_per_year drift in {final_directory}")
        if any(stats.get(field) is not None for field in _LIQUIDATION_NULL_ECONOMIC_FIELDS):
            raise Session4ArtifactError(
                f"liquidation economic accounting must be null in {final_directory}"
            )
    performance = assess_performance(
        metrics,
        evidence_gate_passed=True,
        liquidation_event_count=liquidation_count,
    )
    if normalize_json(stats.get("performance_gate")) != normalize_json(performance.as_dict()):
        raise Session4ArtifactError(
            f"recomputed performance assessment mismatch in {final_directory}"
        )


def _verified_runs(
    output: Path,
    specs: Sequence[Any],
    *,
    expected_manifest_core_hash: str,
) -> dict[str, VerifiedRun]:
    result: dict[str, VerifiedRun] = {}
    for spec in specs:
        directory = output / "runs" / spec.run_id
        if not directory.exists():
            continue
        result[spec.run_id] = verify_completed_run(
            directory,
            expected_spec=spec,
            expected_manifest_core_hash=expected_manifest_core_hash,
        )
    return result


def _mark_verified_progress_complete(
    progress: dict[str, JsonValue],
    output: Path,
    verified: Mapping[str, VerifiedRun],
) -> None:
    runs = progress.get("runs")
    if not isinstance(runs, dict):
        raise Session4RunnerError("progress runs mapping is missing")
    for run_id in verified:
        row = runs.get(run_id)
        if not isinstance(row, dict):
            raise Session4RunnerError(f"progress row missing for {run_id}")
        _restore_marker_timing(output, run_id, row)
        _set_progress_state(
            progress,
            run_id,
            "COMPLETE",
            attempts=_observed_attempt_count(output, run_id),
        )


def _verify_previous_completed_progress(
    output: Path,
    specs: Sequence[Any],
    manifest: Mapping[str, object],
) -> None:
    progress_path = output / "progress.json"
    if not progress_path.exists():
        return
    progress = _read_json_object(progress_path)
    if progress.get("schema_version") != PROGRESS_SCHEMA_VERSION:
        raise Session4ArtifactError("stored progress schema drift")
    if progress.get("manifest_core_hash") != _required_str(manifest, "manifest_core_hash"):
        raise Session4ArtifactError("stored progress belongs to another manifest")
    runs = progress.get("runs")
    if not isinstance(runs, dict):
        raise Session4ArtifactError("stored progress runs mapping is missing")
    by_id = {spec.run_id: spec for spec in specs}
    for run_id, row in runs.items():
        if not isinstance(row, dict) or row.get("status") != "COMPLETE":
            continue
        spec = by_id.get(run_id)
        if spec is None:
            raise Session4ArtifactError(f"stored progress contains unknown run {run_id}")
        final = output / "runs" / run_id
        if not final.exists():
            raise Session4ArtifactError(
                f"progress marks {run_id} COMPLETE but final artifact is missing"
            )
        verify_completed_run(
            final,
            expected_spec=spec,
            expected_manifest_core_hash=_required_str(manifest, "manifest_core_hash"),
        )


def _assert_retry_allowed(output: Path, run_id: str) -> None:
    fatal = [marker for marker in _verified_failure_markers(output, run_id) if not marker.retryable]
    if fatal:
        latest = max(fatal, key=lambda marker: marker.attempt)
        raise Session4RunnerError(
            f"run {run_id} has terminal failure at attempt {latest.attempt} "
            f"({latest.failure_class}); retry is forbidden"
        )


def _reconcile_staged_runs(
    output: Path,
    specs: Sequence[Any],
    *,
    expected_manifest_core_hash: str,
) -> None:
    """Promuje kompletne stagingi, a niedokończone zachowuje w kwarantannie."""

    by_id = {spec.run_id: spec for spec in specs}
    runs_root = output / "runs"
    quarantine_root = runs_root / ".quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    candidates: dict[str, list[tuple[Path, int, VerifiedRun]]] = {}

    for staging_root in (runs_root / ".partial", runs_root / ".stage"):
        if not staging_root.exists():
            continue
        for candidate in sorted(staging_root.iterdir(), key=lambda item: item.name):
            parsed = _parse_staged_candidate(candidate, by_id)
            if parsed is None:
                quarantined = _quarantine_staged_candidate(candidate, quarantine_root)
                raise Session4ArtifactError(
                    f"unrecognized staged run was quarantined at {quarantined}"
                )
            run_id, attempt = parsed
            if not candidate.is_dir() or not (candidate / "complete.json").is_file():
                _quarantine_staged_candidate(candidate, quarantine_root)
                continue
            try:
                verified = verify_completed_run(
                    candidate,
                    expected_spec=by_id[run_id],
                    expected_manifest_core_hash=expected_manifest_core_hash,
                )
                completion = _read_json_object(candidate / "complete.json")
                if completion.get("attempt") != attempt:
                    raise Session4ArtifactError(f"staged attempt mismatch in {candidate}")
            # Każdy błąd głębokiej weryfikacji oznacza uszkodzony staging.
            except Exception:
                _quarantine_staged_candidate(candidate, quarantine_root)
                continue
            candidates.setdefault(run_id, []).append((candidate, attempt, verified))

    for run_id, valid in candidates.items():
        if len(valid) > 1:
            locations = sorted(str(item[0]) for item in valid)
            raise Session4ArtifactError(
                f"multiple valid staged candidates for {run_id}: {locations}"
            )
        final = runs_root / run_id
        if final.exists():
            verify_completed_run(
                final,
                expected_spec=by_id[run_id],
                expected_manifest_core_hash=expected_manifest_core_hash,
            )
            raise Session4ArtifactError(
                f"valid staged candidate conflicts with completed run {run_id}"
            )

    for run_id, valid in candidates.items():
        candidate, _attempt, _verified = valid[0]
        final = runs_root / run_id
        if final.exists():
            raise Session4ArtifactError(f"refusing to overwrite completed run {run_id}")
        try:
            _fsync_tree(candidate)
            os.rename(candidate, final)
            _fsync_directory(candidate.parent)
            _fsync_directory(final.parent)
        except OSError as exc:
            raise Session4ArtifactError(
                f"cannot atomically promote staged run {run_id}: {exc}"
            ) from exc
        verify_completed_run(
            final,
            expected_spec=by_id[run_id],
            expected_manifest_core_hash=expected_manifest_core_hash,
        )


def _recover_staged_run(
    output: Path,
    spec: Any,
    *,
    expected_manifest_core_hash: str,
) -> bool:
    """Odzyskuje wyłącznie staging bieżącego runu po błędzie zapisu."""

    runs_root = output / "runs"
    final = runs_root / spec.run_id
    if final.exists():
        verify_completed_run(
            final,
            expected_spec=spec,
            expected_manifest_core_hash=expected_manifest_core_hash,
        )
        return True
    quarantine_root = runs_root / ".quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    valid: list[Path] = []
    by_id = {spec.run_id: spec}
    for staging_root in (runs_root / ".partial", runs_root / ".stage"):
        if not staging_root.exists():
            continue
        for candidate in sorted(staging_root.glob(f"{spec.run_id}.a*")):
            parsed = _parse_staged_candidate(candidate, by_id)
            if parsed is None:
                _quarantine_staged_candidate(candidate, quarantine_root)
                continue
            _run_id, attempt = parsed
            if not candidate.is_dir() or not (candidate / "complete.json").is_file():
                _quarantine_staged_candidate(candidate, quarantine_root)
                continue
            try:
                verify_completed_run(
                    candidate,
                    expected_spec=spec,
                    expected_manifest_core_hash=expected_manifest_core_hash,
                )
                completion = _read_json_object(candidate / "complete.json")
                if completion.get("attempt") != attempt:
                    raise Session4ArtifactError(f"staged attempt mismatch in {candidate}")
            except Exception:
                _quarantine_staged_candidate(candidate, quarantine_root)
                continue
            valid.append(candidate)
    if len(valid) > 1:
        raise Session4ArtifactError(
            f"multiple valid staged candidates for {spec.run_id}: {[str(path) for path in valid]}"
        )
    if not valid:
        return False
    candidate = valid[0]
    if final.exists():
        verify_completed_run(
            final,
            expected_spec=spec,
            expected_manifest_core_hash=expected_manifest_core_hash,
        )
        raise Session4ArtifactError(
            f"valid staged candidate conflicts with completed run {spec.run_id}"
        )
    try:
        _fsync_tree(candidate)
        os.rename(candidate, final)
        _fsync_directory(candidate.parent)
        _fsync_directory(final.parent)
    except OSError as exc:
        raise Session4ArtifactError(
            f"cannot atomically recover staged run {spec.run_id}: {exc}"
        ) from exc
    verify_completed_run(
        final,
        expected_spec=spec,
        expected_manifest_core_hash=expected_manifest_core_hash,
    )
    return True


def _parse_staged_candidate(
    candidate: Path,
    by_id: Mapping[str, Any],
) -> tuple[str, int] | None:
    name = candidate.name
    if ".a" not in name:
        return None
    run_id, suffix = name.rsplit(".a", maxsplit=1)
    attempt_text = suffix.split(".", maxsplit=1)[0]
    if run_id not in by_id or not attempt_text.isdigit():
        return None
    attempt = int(attempt_text)
    if attempt < 1:
        return None
    return run_id, attempt


def _quarantine_staged_candidate(candidate: Path, quarantine_root: Path) -> Path:
    target = quarantine_root / candidate.name
    if target.exists():
        raise Session4ArtifactError(f"staging quarantine collision: {target}")
    try:
        os.rename(candidate, target)
        _fsync_directory(candidate.parent)
        _fsync_directory(quarantine_root)
    except OSError as exc:
        raise Session4ArtifactError(f"cannot quarantine staged run {candidate}: {exc}") from exc
    return target


def _new_progress(
    manifest: Mapping[str, object],
    matrix: Sequence[Any],
    output: Path,
) -> dict[str, JsonValue]:
    existing_path = output / "progress.json"
    old: Mapping[str, object] = {}
    if existing_path.exists():
        old = _read_json_object(existing_path)
    old_runs_raw = old.get("runs", {})
    old_runs = old_runs_raw if isinstance(old_runs_raw, dict) else {}
    runs: dict[str, JsonValue] = {}
    for spec in matrix:
        prior = old_runs.get(spec.run_id)
        attempts = 0
        if isinstance(prior, dict) and isinstance(prior.get("attempts"), int):
            attempts = cast(int, prior["attempts"])
        attempts = max(attempts, _observed_attempt_count(output, spec.run_id))
        row: dict[str, JsonValue] = {
            "ordinal": spec.ordinal,
            "status": "PENDING",
            "attempts": attempts,
        }
        if isinstance(prior, dict):
            _copy_progress_timing(prior, row)
        _restore_marker_timing(output, spec.run_id, row)
        runs[spec.run_id] = row
    progress: dict[str, JsonValue] = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "manifest_core_hash": _required_str(manifest, "manifest_core_hash"),
        "updated_at_utc": _now_iso(),
        "runs": runs,
        "counts": _progress_counts(runs),
    }
    _assert_outcome_blind_progress(progress)
    return progress


def _copy_progress_timing(
    prior: Mapping[str, object],
    row: dict[str, JsonValue],
) -> None:
    for field in ("last_started_at_utc", "completed_at_utc"):
        value = prior.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise Session4ArtifactError(f"stored progress {field} is invalid")
        _parse_utc(value, field)
        row[field] = value
    for field in ("last_elapsed_seconds", "total_elapsed_seconds"):
        value = prior.get(field)
        if value is None:
            continue
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise Session4ArtifactError(f"stored progress {field} is invalid")
        row[field] = round(float(value), 6)


def _restore_marker_timing(
    output: Path,
    run_id: str,
    row: dict[str, JsonValue],
) -> None:
    starts = _verified_started_markers(output, run_id)
    failures = _verified_failure_markers(output, run_id)
    if starts:
        latest_start = max(starts, key=lambda marker: marker.attempt)
        row["last_started_at_utc"] = latest_start.started_at_utc
    marker_total = sum(marker.elapsed_seconds for marker in failures)
    latest_attempt = -1
    if failures:
        latest_failure = max(failures, key=lambda marker: marker.attempt)
        row["last_elapsed_seconds"] = round(latest_failure.elapsed_seconds, 6)
        latest_attempt = latest_failure.attempt

    completion_path = output / "runs" / run_id / "complete.json"
    if completion_path.exists():
        completion = _read_json_object(completion_path)
        if completion.get("schema_version") != COMPLETION_SCHEMA_VERSION:
            raise Session4ArtifactError(f"completion schema drift in {completion_path}")
        completion_hash = _required_str(completion, "completion_hash")
        core = {key: value for key, value in completion.items() if key != "completion_hash"}
        if json_hash(core) != completion_hash:
            raise Session4ArtifactError(f"completion hash mismatch in {completion_path}")
        if completion.get("retry_profile") != RETRY_PROFILE_ID:
            raise Session4ArtifactError(f"completion retry profile drift in {completion_path}")
        attempt = _required_attempt(completion, "attempt")
        started_at_utc = _required_str(completion, "started_at_utc")
        _parse_utc(started_at_utc, "started_at_utc")
        completed_at_utc = _required_str(completion, "completed_at_utc")
        _parse_utc(completed_at_utc, "completed_at_utc")
        elapsed = _required_elapsed(completion, "elapsed_seconds", completion_path)
        marker_total += elapsed
        row["last_started_at_utc"] = started_at_utc
        row["completed_at_utc"] = completed_at_utc
        if attempt >= latest_attempt:
            row["last_elapsed_seconds"] = round(elapsed, 6)

    prior_total_raw = row.get("total_elapsed_seconds", 0.0)
    prior_total = float(str(prior_total_raw))
    if marker_total > 0.0 or prior_total > 0.0:
        row["total_elapsed_seconds"] = round(max(prior_total, marker_total), 6)


def _set_progress_state(
    progress: dict[str, JsonValue],
    run_id: str,
    status: str,
    *,
    attempts: int | None,
    failure_class: str | None = None,
    elapsed_seconds: float | None = None,
    started_at_utc: str | None = None,
    completed_at_utc: str | None = None,
) -> None:
    runs = progress.get("runs")
    if not isinstance(runs, dict) or run_id not in runs or not isinstance(runs[run_id], dict):
        raise Session4RunnerError(f"progress does not contain run {run_id}")
    row = cast(dict[str, JsonValue], runs[run_id])
    row["status"] = status
    if attempts is not None:
        row["attempts"] = attempts
    if failure_class is not None:
        row["failure_class"] = failure_class
    else:
        row.pop("failure_class", None)
    if started_at_utc is not None:
        _parse_utc(started_at_utc, "started_at_utc")
        row["last_started_at_utc"] = started_at_utc
    if completed_at_utc is not None:
        _parse_utc(completed_at_utc, "completed_at_utc")
        row["completed_at_utc"] = completed_at_utc
    if elapsed_seconds is not None:
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0.0:
            raise Session4RunnerError("worker elapsed_seconds must be finite and non-negative")
        rounded = round(elapsed_seconds, 6)
        row["last_elapsed_seconds"] = rounded
        previous_total = row.get("total_elapsed_seconds", 0.0)
        row["total_elapsed_seconds"] = round(float(str(previous_total)) + rounded, 6)
    progress["updated_at_utc"] = _now_iso()
    progress["counts"] = _progress_counts(runs)
    _assert_outcome_blind_progress(progress)


def _apply_worker_message(
    progress: dict[str, JsonValue],
    message: Mapping[str, object],
) -> str | None:
    kind = str(message.get("kind", ""))
    run_id_raw = message.get("run_id")
    run_id = None if run_id_raw is None else str(run_id_raw)
    attempt = int(str(message.get("attempt", 0)))
    elapsed_raw = message.get("elapsed_seconds")
    elapsed = None if elapsed_raw is None else float(str(elapsed_raw))
    started_at_raw = message.get("started_at_utc")
    started_at = None if started_at_raw is None else str(started_at_raw)
    completed_at_raw = message.get("completed_at_utc")
    completed_at = None if completed_at_raw is None else str(completed_at_raw)
    if kind == "STARTED" and run_id is not None:
        _set_progress_state(
            progress,
            run_id,
            "RUNNING",
            attempts=attempt,
            started_at_utc=started_at,
        )
        return None
    if kind == "COMPLETE" and run_id is not None:
        _set_progress_state(
            progress,
            run_id,
            "COMPLETE",
            attempts=attempt,
            elapsed_seconds=elapsed,
            completed_at_utc=completed_at,
        )
        return None
    if kind == "FAILED_RETRYABLE" and run_id is not None:
        _set_progress_state(
            progress,
            run_id,
            "RETRY_PENDING",
            attempts=attempt,
            failure_class=str(message.get("failure_class", "OperationalError")),
            elapsed_seconds=elapsed,
        )
        return None
    if kind == "FAILED_FATAL" and run_id is not None:
        _set_progress_state(
            progress,
            run_id,
            "FAILED_FATAL",
            attempts=attempt,
            failure_class=str(message.get("failure_class", "FatalError")),
            elapsed_seconds=elapsed,
        )
        return f"run {run_id} failed fatally: {message.get('message', '')}"
    if kind == "WORKER_FATAL":
        return (
            f"worker preflight failed: {message.get('failure_class', '')}: "
            f"{message.get('message', '')}"
        )
    return f"unknown worker message: {message!r}"


def _assert_outcome_blind_progress(progress: Mapping[str, object]) -> None:
    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                lowered = str(key).lower()
                if any(field in lowered for field in _OUTCOME_FIELDS):
                    raise Session4RunnerError(f"outcome field leaked into progress: {key}")
                walk(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                walk(item)

    walk(progress)


def _progress_counts(runs: Mapping[str, object]) -> dict[str, JsonValue]:
    counts: dict[str, JsonValue] = {}
    for value in runs.values():
        if isinstance(value, Mapping):
            status = str(value.get("status", "UNKNOWN"))
            previous = counts.get(status, 0)
            counts[status] = int(str(previous)) + 1
    counts["TOTAL"] = len(runs)
    return counts


def _initialize_suite_directory(
    output: Path,
    manifest: Mapping[str, object],
    *,
    resume: bool,
) -> None:
    if output.exists() and not resume:
        raise Session4RunnerError(f"output exists; pass --resume to reuse {output}")
    output.mkdir(parents=True, exist_ok=True)
    stored_path = output / "experiment_manifest.json"
    if stored_path.exists():
        if normalize_json(_read_json_object(stored_path)) != normalize_json(manifest):
            raise Session4ArtifactError("output belongs to another manifest")
    else:
        _write_new_json(stored_path, manifest)
    (output / "runs" / ".partial").mkdir(parents=True, exist_ok=True)
    (output / "runs" / ".quarantine").mkdir(parents=True, exist_ok=True)
    (output / "attempts").mkdir(parents=True, exist_ok=True)


def _select_run_specs(matrix: Sequence[Any], selected_ids: Sequence[str]) -> list[Any]:
    if not selected_ids:
        return list(matrix)
    by_id = {item.run_id: item for item in matrix}
    unknown = sorted(set(selected_ids) - set(by_id))
    if unknown:
        raise Session4RunnerError(f"unknown run IDs: {unknown}")
    selected_set = set(selected_ids)
    return [item for item in matrix if item.run_id in selected_set]


def _observed_attempt_count(output: Path, run_id: str) -> int:
    observed = 0
    for started_marker in _verified_started_markers(output, run_id):
        observed = max(observed, started_marker.attempt)
    for failure_marker in _verified_failure_markers(output, run_id):
        observed = max(observed, failure_marker.attempt)
    for partials in (
        output / "runs" / ".partial",
        output / "runs" / ".stage",
        output / "runs" / ".quarantine",
    ):
        if not partials.exists():
            continue
        stage_marker = f"{run_id}.a"
        for path in partials.glob(f"{run_id}.a*"):
            suffix = path.name.removeprefix(stage_marker).split(".", maxsplit=1)[0]
            if suffix.isdigit():
                observed = max(observed, int(suffix))
    completion_path = output / "runs" / run_id / "complete.json"
    if completion_path.exists():
        completion = _read_json_object(completion_path)
        attempt = completion.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise Session4ArtifactError(f"invalid completion attempt in {completion_path}")
        observed = max(observed, attempt)
    return observed


def _failure_record(
    run_id: str,
    attempt: int,
    error: BaseException,
    *,
    retryable: bool,
    elapsed_seconds: float,
) -> dict[str, JsonValue]:
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0.0:
        raise Session4ArtifactError("failure elapsed_seconds must be finite and non-negative")
    core: dict[str, JsonValue] = {
        "schema_version": FAILURE_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt": attempt,
        "failed_at_utc": _now_iso(),
        "failure_class": type(error).__name__,
        "message": str(error),
        "retryable": retryable,
        "retry_profile": RETRY_PROFILE_ID,
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    return {**core, "failure_hash": json_hash(core)}


def _started_record(run_id: str, attempt: int, started_at_utc: str) -> dict[str, JsonValue]:
    core: dict[str, JsonValue] = {
        "schema_version": ATTEMPT_STARTED_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt": attempt,
        "started_at_utc": started_at_utc,
        "retry_profile": RETRY_PROFILE_ID,
    }
    return {**core, "started_hash": json_hash(core)}


def _write_attempt_started(output: Path, started: Mapping[str, object]) -> None:
    run_id = _required_str(started, "run_id")
    attempt = _required_attempt(started, "attempt")
    directory = _ensure_attempt_directory(output, run_id)
    path = directory / f"a{attempt:03d}.started.json"
    _atomic_write_json(path, started, refuse_existing=True)
    _verify_started_marker(path, expected_run_id=run_id, expected_attempt=attempt)


def _write_attempt_failure(output: Path, failure: Mapping[str, object]) -> None:
    run_id = _required_str(failure, "run_id")
    attempt = _required_attempt(failure, "attempt")
    directory = _ensure_attempt_directory(output, run_id)
    path = directory / f"a{attempt:03d}.failure.json"
    _atomic_write_json(path, failure, refuse_existing=True)
    _verify_failure_marker(path, expected_run_id=run_id, expected_attempt=attempt)


def _ensure_attempt_directory(output: Path, run_id: str) -> Path:
    directory = output / "attempts" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    _fsync_directory(directory)
    _fsync_directory(directory.parent)
    _fsync_directory(output)
    return directory


def _verified_started_markers(output: Path, run_id: str) -> tuple[VerifiedStart, ...]:
    directory = output / "attempts" / run_id
    if not directory.exists():
        return ()
    result: list[VerifiedStart] = []
    for path in sorted(directory.glob("a*.started.json")):
        attempt = _attempt_from_marker_name(path, suffix=".started.json")
        result.append(
            _verify_started_marker(
                path,
                expected_run_id=run_id,
                expected_attempt=attempt,
            )
        )
    return tuple(result)


def _verified_failure_markers(output: Path, run_id: str) -> tuple[VerifiedFailure, ...]:
    directory = output / "attempts" / run_id
    if not directory.exists():
        return ()
    result: list[VerifiedFailure] = []
    for path in sorted(directory.glob("a*.failure.json")):
        attempt = _attempt_from_marker_name(path, suffix=".failure.json")
        result.append(
            _verify_failure_marker(
                path,
                expected_run_id=run_id,
                expected_attempt=attempt,
            )
        )
    return tuple(result)


def _verify_started_marker(
    path: Path,
    *,
    expected_run_id: str,
    expected_attempt: int,
) -> VerifiedStart:
    raw = _read_json_object(path)
    if raw.get("schema_version") != ATTEMPT_STARTED_SCHEMA_VERSION:
        raise Session4ArtifactError(f"started marker schema drift in {path}")
    started_hash = _required_str(raw, "started_hash")
    core = {key: value for key, value in raw.items() if key != "started_hash"}
    if json_hash(core) != started_hash:
        raise Session4ArtifactError(f"started marker hash mismatch in {path}")
    run_id = _required_str(raw, "run_id")
    attempt = _required_attempt(raw, "attempt")
    if run_id != expected_run_id or attempt != expected_attempt:
        raise Session4ArtifactError(f"started marker identity mismatch in {path}")
    if raw.get("retry_profile") != RETRY_PROFILE_ID:
        raise Session4ArtifactError(f"started marker retry profile drift in {path}")
    started_at_utc = _required_str(raw, "started_at_utc")
    _parse_utc(started_at_utc, "started_at_utc")
    return VerifiedStart(run_id, attempt, started_at_utc, started_hash)


def _verify_failure_marker(
    path: Path,
    *,
    expected_run_id: str,
    expected_attempt: int,
) -> VerifiedFailure:
    raw = _read_json_object(path)
    if raw.get("schema_version") != FAILURE_SCHEMA_VERSION:
        raise Session4ArtifactError(f"failure marker schema drift in {path}")
    failure_hash = _required_str(raw, "failure_hash")
    core = {key: value for key, value in raw.items() if key != "failure_hash"}
    if json_hash(core) != failure_hash:
        raise Session4ArtifactError(f"failure marker hash mismatch in {path}")
    run_id = _required_str(raw, "run_id")
    attempt = _required_attempt(raw, "attempt")
    if run_id != expected_run_id or attempt != expected_attempt:
        raise Session4ArtifactError(f"failure marker identity mismatch in {path}")
    if raw.get("retry_profile") != RETRY_PROFILE_ID:
        raise Session4ArtifactError(f"failure marker retry profile drift in {path}")
    failed_at_utc = _required_str(raw, "failed_at_utc")
    _parse_utc(failed_at_utc, "failed_at_utc")
    failure_class = _required_str(raw, "failure_class")
    if not isinstance(raw.get("message"), str):
        raise Session4ArtifactError(f"failure marker message is invalid in {path}")
    retryable = raw.get("retryable")
    if not isinstance(retryable, bool):
        raise Session4ArtifactError(f"failure marker retryable flag is invalid in {path}")
    elapsed_seconds = _required_elapsed(raw, "elapsed_seconds", path)
    return VerifiedFailure(
        run_id,
        attempt,
        retryable,
        failure_class,
        failure_hash,
        failed_at_utc,
        elapsed_seconds,
    )


def _attempt_from_marker_name(path: Path, *, suffix: str) -> int:
    name = path.name
    attempt_text = name.removeprefix("a").removesuffix(suffix)
    if not name.startswith("a") or not name.endswith(suffix) or not attempt_text.isdigit():
        raise Session4ArtifactError(f"invalid attempt marker filename: {path}")
    attempt = int(attempt_text)
    if name != f"a{attempt:03d}{suffix}":
        raise Session4ArtifactError(f"non-canonical attempt marker filename: {path}")
    if attempt < 1 or attempt > DEFAULT_MAX_ATTEMPTS:
        raise Session4ArtifactError(f"attempt marker outside frozen budget: {path}")
    return attempt


def _required_attempt(raw: Mapping[str, object], field: str) -> int:
    value = raw.get(field)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > DEFAULT_MAX_ATTEMPTS
    ):
        raise Session4ArtifactError(f"{field} must be in [1, {DEFAULT_MAX_ATTEMPTS}]")
    return value


def _required_elapsed(raw: Mapping[str, object], field: str, path: Path) -> float:
    value = raw.get(field)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise Session4ArtifactError(f"invalid {field} in {path}")
    return float(value)


def _require_free_space(path: Path, minimum_gib: float) -> None:
    if minimum_gib < 0:
        raise Session4RunnerError("minimum free space cannot be negative")
    probe = path if path.exists() else path.parent
    probe.mkdir(parents=True, exist_ok=True)
    free_gib = shutil.disk_usage(probe).free / (1024**3)
    if free_gib < minimum_gib:
        raise Session4RunnerError(
            f"insufficient free space: {free_gib:.1f} GiB < required {minimum_gib:.1f} GiB"
        )


def _runner_source_hashes() -> dict[str, JsonValue]:
    names = (
        "mr_session4_bybit.py",
        "mr_session4_contract.py",
        "mr_session4_data.py",
        "mr_session4_execution.py",
        "mr_session4_runner.py",
        "nautilus_mastermind.py",
    )
    directory = Path(__file__).parent
    return {name: _file_sha256(directory / name) for name in names}


def _scientific_profiles() -> dict[str, JsonValue]:
    return {
        "execution": EXECUTION_PROFILE_ID,
        "margin": MARGIN_PROFILE_ID,
        "metrics": METRIC_PROFILE_ID,
        "costs": COST_PROFILE_ID,
        "native_default_leverage": str(DEFAULT_NATIVE_LEVERAGE),
        "starting_balance": {"amount": str(STARTING_BALANCE), "currency": "USDT"},
        "parallelism": "SPAWN_ONE_LONG_LIVED_WORKER_PER_SYMBOL_V1",
        "artifact_commit": "STAGE_VERIFY_COMPLETE_MARKER_ATOMIC_RENAME_V1",
        "retry_profile": RETRY_PROFILE_ID,
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "minimum_free_gib": MIN_FREE_GIB,
    }


def _verify_scientific_profiles(profiles: Mapping[str, object]) -> None:
    if normalize_json(profiles) != normalize_json(_scientific_profiles()):
        raise Session4ManifestError("execution/retry profile drift")


def _verify_preregistration_core_marker(path: Path, expected_hash: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Session4ManifestError(f"cannot read preregistration marker: {exc}") from exc
    matches = _PREREGISTRATION_CORE_MARKER.findall(text)
    if len(matches) != 1:
        raise Session4ManifestError("preregistration must contain exactly one manifest-core marker")
    observed = matches[0]
    if observed == "PENDING":
        raise Session4ManifestError("preregistration manifest-core marker is PENDING")
    if observed != expected_hash:
        raise Session4ManifestError("preregistration manifest-core marker mismatch")


def _preregistration_tag_for_commit(repo_root: Path, git_commit: str) -> str:
    """Wymaga jednego kanonicznego taga prerejestracji na zamrożonym commicie."""

    try:
        completed = subprocess.run(
            (
                "git",
                "-C",
                str(repo_root),
                "tag",
                "--points-at",
                git_commit,
                "--list",
                "mr-session-4-preregistration-*",
            ),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Session4ManifestError(f"cannot verify preregistration tag: {exc}") from exc
    tags = tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
    if len(tags) != 1:
        raise Session4ManifestError(
            "frozen commit must have exactly one mr-session-4-preregistration-YYYY-MM-DD tag"
        )
    tag = tags[0]
    match = _PREREGISTRATION_TAG.fullmatch(tag)
    if match is None:
        raise Session4ManifestError(
            "preregistration tag must match mr-session-4-preregistration-YYYY-MM-DD"
        )
    try:
        datetime.strptime(match.group("date"), "%Y-%m-%d")
    except ValueError as exc:
        raise Session4ManifestError(f"preregistration tag has an invalid date: {tag}") from exc
    return tag


def _runtime_manifest_versions() -> dict[str, JsonValue]:
    """Zamraża aktywny interpreter, platformę i biblioteki wpływające na wynik."""

    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_compiler": platform.python_compiler(),
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
        "uv": _capture_uv_version(),
        **runtime_versions(),
    }


def _capture_uv_version() -> str:
    try:
        completed = subprocess.run(
            ("uv", "--version"),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Session4ManifestError(f"cannot capture uv version: {exc}") from exc
    parts = completed.stdout.strip().split()
    if len(parts) != 2 or parts[0] != "uv":
        raise Session4ManifestError(f"unexpected uv --version output: {completed.stdout!r}")
    return parts[1]


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, object],
    *,
    refuse_existing: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if refuse_existing and path.exists():
        raise Session4ArtifactError(f"refusing to overwrite {path}")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise Session4ArtifactError(f"cannot atomically write {path}: {exc}") from exc


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    _write_new_text(path, canonical_json(payload) + "\n")


def _write_new_text(path: Path, text: str) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise Session4ArtifactError(f"cannot write new file {path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Session4ArtifactError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise Session4ArtifactError(f"{path} must contain a JSON object")
    return cast(dict[str, object], raw)


def _file_sha256(path: Path) -> str:
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise Session4ManifestError(f"cannot hash {path}: {exc}") from exc
    digest = hashlib.sha256()
    with handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise Session4ManifestError(f"{path} must be inside {root}") from exc


def _required_mapping(raw: Mapping[str, object], field: str) -> dict[str, object]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise Session4ManifestError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _required_str(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise Session4ManifestError(f"{field} must be a non-empty string")
    return value


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise Session4ManifestError(f"{field} must be timezone-aware UTC")


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Session4ManifestError(f"invalid {field}: {value!r}") from exc
    _require_utc(parsed, field)
    return parsed


def _iso(value: datetime) -> str:
    _require_utc(value, "datetime")
    return value.isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return _iso(datetime.now(UTC))


def _status_payload(output: Path) -> dict[str, JsonValue]:
    progress = _read_json_object(output / "progress.json")
    return {
        "schema_version": normalize_json(progress.get("schema_version")),
        "updated_at_utc": normalize_json(progress.get("updated_at_utc")),
        "counts": normalize_json(progress.get("counts", {})),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    freeze = subcommands.add_parser("freeze-bybit-contracts")
    freeze.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    freeze.add_argument("--output", type=Path, default=DEFAULT_CONTRACTS_PATH)
    freeze.add_argument("--mainnet-public", action="store_true", required=True)

    prepare = subcommands.add_parser("prepare")
    _add_contract_and_preregistration_inputs(prepare)
    prepare.add_argument("--output", type=Path, default=DEFAULT_MANIFEST_PATH)
    prepare.add_argument("--data-root", type=Path, default=PROCESSED_DATA_ROOT)

    lock_core = subcommands.add_parser("lock-core")
    lock_core.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS_PATH)
    lock_core.add_argument("--data-root", type=Path, default=PROCESSED_DATA_ROOT)

    plan = subcommands.add_parser("plan")
    _add_manifest_inputs(plan)

    run = subcommands.add_parser("run")
    _add_manifest_inputs(run)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--data-root", type=Path, default=PROCESSED_DATA_ROOT)
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run.add_argument(
        "--max-attempts",
        type=int,
        choices=(DEFAULT_MAX_ATTEMPTS,),
        default=DEFAULT_MAX_ATTEMPTS,
    )
    run.add_argument("--resume", action="store_true")
    run.add_argument("--run-id", action="append", default=[])
    run.add_argument("--min-free-gib", type=float, default=MIN_FREE_GIB)

    status = subcommands.add_parser("status")
    status.add_argument("--output", type=Path, required=True)

    verify = subcommands.add_parser("verify")
    _add_manifest_inputs(verify)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--allow-incomplete", action="store_true")
    verify.add_argument("--no-metrics", action="store_true", default=True)

    finalize = subcommands.add_parser("finalize")
    _add_manifest_inputs(finalize)
    finalize.add_argument("--output", type=Path, required=True)
    return parser


def _add_manifest_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    _add_contract_and_preregistration_inputs(parser)


def _add_contract_and_preregistration_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS_PATH)
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=DEFAULT_PREREGISTRATION_PATH,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia CLI; nigdy nie uruchamia pełnego sweepu bez jawnego ``run``."""

    args = _build_parser().parse_args(argv)
    try:
        if args.command == "freeze-bybit-contracts":
            freeze_bybit_contracts(args.symbols, args.output)
            print(canonical_json({"status": "FROZEN", "output": str(args.output)}))
        elif args.command == "prepare":
            contracts = load_frozen_bybit_contracts(args.contracts)
            manifest = build_experiment_manifest(
                contracts,
                contracts_path=args.contracts,
                preregistration_path=args.preregistration,
                data_root=args.data_root,
            )
            _atomic_write_json(args.output, manifest, refuse_existing=True)
            print(
                canonical_json(
                    {
                        "status": "PREPARED",
                        "manifest": str(args.output),
                        "manifest_core_hash": manifest["manifest_core_hash"],
                        "run_count": EXPECTED_RUNS,
                    }
                )
            )
        elif args.command == "lock-core":
            contracts = load_frozen_bybit_contracts(args.contracts)
            core = build_experiment_core(
                contracts,
                contracts_path=args.contracts,
                data_root=args.data_root,
            )
            print(
                canonical_json(
                    {
                        "status": "CORE_READY",
                        "manifest_core_hash": json_hash(core),
                        "run_count": EXPECTED_RUNS,
                    }
                )
            )
        elif args.command == "plan":
            manifest, _contracts = load_and_verify_manifest(
                args.manifest,
                contracts_path=args.contracts,
                preregistration_path=args.preregistration,
                verify_data=False,
            )
            print(
                canonical_json(
                    {
                        "status": "VERIFIED",
                        "manifest_core_hash": manifest["manifest_core_hash"],
                        "run_count": EXPECTED_RUNS,
                        "strata": [
                            {"symbol": symbol, "marking_timeframe": marking, "runs": 132}
                            for symbol in SYMBOLS
                            for marking in ("5m", "10m")
                        ],
                    }
                )
            )
        elif args.command == "run":
            run_suite(
                manifest_path=args.manifest,
                output_directory=args.output,
                contracts_path=args.contracts,
                preregistration_path=args.preregistration,
                data_root=args.data_root,
                workers=args.workers,
                max_attempts=args.max_attempts,
                resume=args.resume,
                selected_run_ids=args.run_id,
                min_free_gib=args.min_free_gib,
            )
            print(canonical_json({"status": "SELECTED_RUNS_COMPLETE"}))
        elif args.command == "status":
            print(canonical_json(_status_payload(args.output)))
        elif args.command == "verify":
            verified = verify_suite(
                args.output,
                manifest_path=args.manifest,
                contracts_path=args.contracts,
                preregistration_path=args.preregistration,
                allow_incomplete=args.allow_incomplete,
            )
            print(canonical_json({"status": "VERIFIED", "verified_runs": len(verified)}))
        elif args.command == "finalize":
            finalize_suite(
                args.output,
                manifest_path=args.manifest,
                contracts_path=args.contracts,
                preregistration_path=args.preregistration,
            )
            print(canonical_json({"status": "FINALIZED", "run_count": EXPECTED_RUNS}))
        else:  # pragma: no cover - argparse enforces a command
            raise AssertionError(f"unknown command {args.command!r}")
    except KeyboardInterrupt:
        return 130
    except (Session4RunnerError, Session4ExecutionError, Session4DataError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def _die(message: str) -> NoReturn:  # pragma: no cover - reserved CLI helper
    raise Session4RunnerError(message)


if __name__ == "__main__":
    raise SystemExit(main())
