from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

import algo_bot.engine.mr_session4_runner as session4_runner
from algo_bot.engine.backtest_result import (
    BACKTEST_RESULT_SCHEMA_VERSION,
    BacktestResult,
    CostComponent,
    CostModel,
    CostProvenance,
    FillMethod,
    JsonValue,
    MarginMethod,
    SourceTreeState,
    assess_eligibility,
)
from algo_bot.engine.mr_session4_contract import PerformanceAssessment, build_run_matrix
from algo_bot.engine.mr_session4_execution import Session4RunArtifact
from algo_bot.engine.mr_session4_runner import (
    Session4ArtifactError,
    Session4ManifestError,
    Session4RunnerError,
    _assert_outcome_blind_progress,
    _atomic_write_json,
    _new_progress,
    _reconcile_staged_runs,
    _save_run_atomically,
    _verified_runs,
    verify_completed_run,
)

ZERO_HASH = "0" * 64


def _stub_uv_version(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    stderr: str = "",
) -> None:
    def fake_run(
        command: tuple[str, str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ("uv", "--version")
        assert check is True
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(session4_runner.subprocess, "run", fake_run)


@pytest.mark.parametrize(
    "stdout",
    (
        "uv 0.11.28\n",
        "uv 0.11.28 (x86_64-unknown-linux-gnu)\n",
    ),
)
def test_capture_uv_version_accepts_pinned_official_outputs(
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_uv_version(monkeypatch, stdout=stdout)

    assert session4_runner._capture_uv_version() == "0.11.28"


@pytest.mark.parametrize(
    "stdout",
    (
        "uv 0.11.27\n",
        "uv 0.11.28 extra\n",
        "uv 0.11.28\nuv 0.11.28\n",
        "uv 0.11.28 ()\n",
        "uv 0.11.28 ((x86_64-unknown-linux-gnu))\n",
        "uv 0.11.28 (x86_64-unknown-linux-gnu) spoof\n",
    ),
)
def test_capture_uv_version_rejects_wrong_or_spoofed_output(
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_uv_version(monkeypatch, stdout=stdout)

    with pytest.raises(Session4ManifestError):
        session4_runner._capture_uv_version()


def test_capture_uv_version_rejects_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_uv_version(
        monkeypatch,
        stdout="uv 0.11.28 (x86_64-unknown-linux-gnu)\n",
        stderr="unexpected diagnostic\n",
    )

    with pytest.raises(Session4ManifestError):
        session4_runner._capture_uv_version()


def test_capture_uv_version_wraps_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_run(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ("uv", "--version"))

    monkeypatch.setattr(session4_runner.subprocess, "run", fail_run)

    with pytest.raises(Session4ManifestError, match="cannot capture uv version"):
        session4_runner._capture_uv_version()


@pytest.mark.parametrize(
    ("python_version", "python_implementation"),
    (
        ("3.12.12", "CPython"),
        ("3.13.0", "CPython"),
        ("3.12.13", "PyPy"),
    ),
)
def test_runtime_manifest_rejects_unpinned_python_runtime(
    python_version: str,
    python_implementation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session4_runner.platform, "python_version", lambda: python_version)
    monkeypatch.setattr(
        session4_runner.platform,
        "python_implementation",
        lambda: python_implementation,
    )
    monkeypatch.setattr(session4_runner, "_capture_uv_version", lambda: "0.11.28")
    monkeypatch.setattr(session4_runner, "runtime_versions", dict)

    with pytest.raises(Session4ManifestError):
        session4_runner._runtime_manifest_versions()


def _eligible_cost_model() -> CostModel:
    def component(name: str) -> CostComponent:
        return CostComponent(
            model_id=name,
            provenance=CostProvenance.MODELLED,
            complete=True,
            research_eligible=True,
        )

    return CostModel(
        identifier="fixture",
        commission=component("commission"),
        funding=component("funding"),
        slippage=component("slippage"),
        execution=component("execution"),
    )


def _artifact() -> Session4RunArtifact:
    spec = build_run_matrix()[0]
    empty = pd.DataFrame()
    equity = pd.DataFrame(
        {"equity": [10_000.0, 10_001.0]},
        index=pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC"),
    )
    cost_model = _eligible_cost_model()
    performance = PerformanceAssessment(True, True, ())
    invariant_ledger: tuple[dict[str, JsonValue], ...] = tuple(
        {"code": code, "observed": 1, "expected": 1, "passed": True}
        for code in session4_runner.SESSION4_INVARIANT_CODES
    )
    metrics: dict[str, JsonValue] = {
        "total_return_fraction": 0.1,
        "cagr_fraction": 0.1,
        "sharpe": 1.0,
        "sortino": 1.0,
        "calmar": 1.0,
        "mar": 1.0,
        "max_drawdown_fraction": -0.20,
        "max_drawdown_display_pct": -20.0,
        "max_drawdown_duration_days": 1.0,
        "recovery_time_days": 1.0,
        "profit_factor": 1.3,
        "win_rate_fraction": 0.5,
        "n_trades": 100,
        "periods_per_year": 8_760.0,
    }
    result = BacktestResult(
        schema_version=BACKTEST_RESULT_SCHEMA_VERSION,
        engine="fixture-engine",
        engine_version="1",
        strategy_version="fixture-strategy",
        source_tree=SourceTreeState("commit", False, ZERO_HASH),
        stats={
            "run_id": spec.run_id,
            "ordinal": spec.ordinal,
            "run_spec_hash": spec.run_spec_hash,
            "config_hash": spec.config_hash,
            "symbol": spec.symbol,
            "marking_timeframe": spec.marking_timeframe,
            "parameter_set_id": spec.parameter_set.parameter_set_id,
            "variant_id": spec.variant.variant_id,
            "evidence_gate_passed": True,
            "performance_gate": performance.as_dict(),
            "economic_metrics_interpretable": True,
            "liquidation_event_count": 0,
            "metrics": metrics,
            "invariant_ledger": list(invariant_ledger),
            **metrics,
        },
        equity=equity,
        trades=empty,
        orders=empty,
        fills=empty,
        positions=empty,
        funding=empty,
        data_hash=ZERO_HASH,
        config_hash=spec.config_hash,
        random_seed=spec.seed,
        cost_model=cost_model,
        eligibility=assess_eligibility(cost_model),
        fill_method=FillMethod.NAUTILUS_NATIVE_BAR,
        margin_method=MarginMethod.MARK_PRICE_ISOLATED,
        mark_price_source="fixture-mark",
    )
    return Session4RunArtifact(
        run_spec=spec,
        result=result,
        counters={"fills": 0},
        invariant_ledger=invariant_ledger,
        final_snapshot="{}",
        performance=performance,
    )


def _move_completed_run_to_stage(
    output: Path,
    artifact: Session4RunArtifact,
    *,
    attempt: int,
    token: str,
) -> Path:
    _save_run_atomically(
        output,
        artifact,
        attempt=attempt,
        manifest_core_hash=ZERO_HASH,
    )
    final = output / "runs" / artifact.run_spec.run_id
    stage = output / "runs" / ".partial" / f"{artifact.run_spec.run_id}.a{attempt:03d}.test.{token}"
    os.rename(final, stage)
    return stage


def test_atomic_write_failure_preserves_previous_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "progress.json"
    path.write_text('{"old":true}\n', encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(Session4ArtifactError, match="atomically write"):
        _atomic_write_json(path, {"new": True})
    assert path.read_text(encoding="utf-8") == '{"old":true}\n'
    assert not list(tmp_path.glob(".progress.json.tmp.*"))


def test_progress_schema_is_outcome_blind_and_has_all_528_runs(tmp_path: Path) -> None:
    manifest = {"manifest_core_hash": ZERO_HASH}
    progress = _new_progress(manifest, build_run_matrix(), tmp_path)
    assert progress["counts"] == {"PENDING": 528, "TOTAL": 528}
    _assert_outcome_blind_progress(progress)
    with pytest.raises(Session4RunnerError, match="outcome field leaked"):
        _assert_outcome_blind_progress({"runs": {"x": {"sharpe": 1.0}}})


def test_resume_reconstructs_bounded_attempt_number_from_partial_and_failure(
    tmp_path: Path,
) -> None:
    run_id = build_run_matrix()[0].run_id
    failure = session4_runner._failure_record(
        run_id,
        1,
        OSError("retryable fixture"),
        retryable=True,
        elapsed_seconds=0.25,
    )
    session4_runner._write_attempt_failure(tmp_path, failure)
    partial_dir = tmp_path / "runs" / ".partial" / f"{run_id}.a002.123.deadbeef"
    partial_dir.mkdir(parents=True)

    progress = _new_progress(
        {"manifest_core_hash": ZERO_HASH},
        build_run_matrix(),
        tmp_path,
    )
    runs = progress["runs"]
    assert isinstance(runs, dict)
    row = runs[run_id]
    assert isinstance(row, dict)
    assert row["attempts"] == 2


def test_resume_promotes_deeply_valid_stage_before_attempt_exhaustion_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    stage = _move_completed_run_to_stage(
        tmp_path,
        artifact,
        attempt=2,
        token="crash",
    )
    session4_runner._write_attempt_failure(
        tmp_path,
        session4_runner._failure_record(
            artifact.run_spec.run_id,
            1,
            RuntimeError("old fatal marker"),
            retryable=False,
            elapsed_seconds=0.5,
        ),
    )
    manifest = {"manifest_core_hash": ZERO_HASH}
    monkeypatch.setattr(
        session4_runner,
        "load_and_verify_manifest",
        lambda *_args, **_kwargs: (manifest, object()),
    )
    monkeypatch.setattr(
        session4_runner,
        "_require_free_space",
        lambda *_args, **_kwargs: None,
    )

    session4_runner.run_suite(
        manifest_path=tmp_path / "manifest.json",
        output_directory=tmp_path,
        workers=1,
        max_attempts=2,
        resume=True,
        selected_run_ids=(artifact.run_spec.run_id,),
    )

    final = tmp_path / "runs" / artifact.run_spec.run_id
    assert not stage.exists()
    assert verify_completed_run(
        final,
        expected_spec=artifact.run_spec,
        expected_manifest_core_hash=ZERO_HASH,
    ).run_id == (artifact.run_spec.run_id)
    assert artifact.run_spec.run_id in _verified_runs(
        tmp_path,
        (artifact.run_spec,),
        expected_manifest_core_hash=ZERO_HASH,
    )
    progress = session4_runner._read_json_object(tmp_path / "progress.json")
    runs = progress["runs"]
    assert isinstance(runs, dict)
    row = runs[artifact.run_spec.run_id]
    assert isinstance(row, dict)
    assert row["attempts"] == 2
    assert row["status"] == "COMPLETE"


def test_resume_rejects_verified_fatal_failure_and_untrusted_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = build_run_matrix()[0]
    manifest = {"manifest_core_hash": ZERO_HASH}
    monkeypatch.setattr(
        session4_runner,
        "load_and_verify_manifest",
        lambda *_args, **_kwargs: (manifest, object()),
    )
    monkeypatch.setattr(
        session4_runner,
        "_require_free_space",
        lambda *_args, **_kwargs: None,
    )
    session4_runner._write_attempt_failure(
        tmp_path,
        session4_runner._failure_record(
            spec.run_id,
            1,
            RuntimeError("fatal invariant"),
            retryable=False,
            elapsed_seconds=0.75,
        ),
    )

    with pytest.raises(Session4RunnerError, match=r"terminal failure.*retry is forbidden"):
        session4_runner.run_suite(
            manifest_path=tmp_path / "manifest.json",
            output_directory=tmp_path,
            workers=1,
            resume=True,
            selected_run_ids=(spec.run_id,),
        )

    marker = tmp_path / "attempts" / spec.run_id / "a001.failure.json"
    marker.write_text("{}\n", encoding="utf-8")
    with pytest.raises(Session4ArtifactError, match="failure marker schema drift"):
        session4_runner.run_suite(
            manifest_path=tmp_path / "manifest.json",
            output_directory=tmp_path,
            workers=1,
            resume=True,
            selected_run_ids=(spec.run_id,),
        )


def test_started_marker_consumes_attempt_and_progress_timing_survives_resume(
    tmp_path: Path,
) -> None:
    spec = build_run_matrix()[0]
    first_started = "2026-07-15T06:00:00Z"
    second_started = "2026-07-15T06:05:00Z"
    session4_runner._write_attempt_started(
        tmp_path,
        session4_runner._started_record(spec.run_id, 1, first_started),
    )
    session4_runner._write_attempt_failure(
        tmp_path,
        session4_runner._failure_record(
            spec.run_id,
            1,
            OSError("retry"),
            retryable=True,
            elapsed_seconds=1.5,
        ),
    )
    session4_runner._write_attempt_started(
        tmp_path,
        session4_runner._started_record(spec.run_id, 2, second_started),
    )
    old = _new_progress(
        {"manifest_core_hash": ZERO_HASH},
        build_run_matrix(),
        tmp_path,
    )
    old_runs = old["runs"]
    assert isinstance(old_runs, dict)
    old_row = old_runs[spec.run_id]
    assert isinstance(old_row, dict)
    old_row["total_elapsed_seconds"] = 2.0
    old_row["completed_at_utc"] = "2026-07-15T06:10:00Z"
    _atomic_write_json(tmp_path / "progress.json", old)

    restored = _new_progress(
        {"manifest_core_hash": ZERO_HASH},
        build_run_matrix(),
        tmp_path,
    )
    restored_runs = restored["runs"]
    assert isinstance(restored_runs, dict)
    row = restored_runs[spec.run_id]
    assert isinstance(row, dict)
    assert row["attempts"] == 2
    assert row["last_started_at_utc"] == second_started
    assert row["last_elapsed_seconds"] == 1.5
    assert row["total_elapsed_seconds"] == 2.0
    assert row["completed_at_utc"] == "2026-07-15T06:10:00Z"


def test_resume_rejects_complete_progress_when_final_disappeared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = build_run_matrix()[0]
    manifest = {"manifest_core_hash": ZERO_HASH}
    progress = _new_progress(manifest, build_run_matrix(), tmp_path)
    runs = progress["runs"]
    assert isinstance(runs, dict)
    row = runs[spec.run_id]
    assert isinstance(row, dict)
    row["status"] = "COMPLETE"
    _atomic_write_json(tmp_path / "progress.json", progress)
    monkeypatch.setattr(
        session4_runner,
        "load_and_verify_manifest",
        lambda *_args, **_kwargs: (manifest, object()),
    )
    monkeypatch.setattr(
        session4_runner,
        "_require_free_space",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(Session4ArtifactError, match="COMPLETE but final artifact is missing"):
        session4_runner.run_suite(
            manifest_path=tmp_path / "manifest.json",
            output_directory=tmp_path,
            workers=1,
            resume=True,
            selected_run_ids=(spec.run_id,),
        )


def test_resume_quarantines_incomplete_and_corrupt_stages_and_counts_attempts(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    run_id = artifact.run_spec.run_id
    incomplete = tmp_path / "runs" / ".partial" / f"{run_id}.a001.test.incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "scratch.txt").write_text("partial\n", encoding="utf-8")
    corrupt = _move_completed_run_to_stage(
        tmp_path,
        artifact,
        attempt=2,
        token="corrupt",
    )
    (corrupt / "counters.json").write_text('{"fills":1}\n', encoding="utf-8")

    _reconcile_staged_runs(
        tmp_path,
        build_run_matrix(),
        expected_manifest_core_hash=ZERO_HASH,
    )

    quarantine = tmp_path / "runs" / ".quarantine"
    assert (quarantine / incomplete.name / "scratch.txt").read_text(encoding="utf-8") == (
        "partial\n"
    )
    assert (quarantine / corrupt.name / "complete.json").is_file()
    progress = _new_progress(
        {"manifest_core_hash": ZERO_HASH},
        build_run_matrix(),
        tmp_path,
    )
    runs = progress["runs"]
    assert isinstance(runs, dict)
    row = runs[run_id]
    assert isinstance(row, dict)
    assert row["attempts"] == 2


def test_resume_hard_fails_when_multiple_valid_stages_exist(tmp_path: Path) -> None:
    artifact = _artifact()
    first = _move_completed_run_to_stage(
        tmp_path,
        artifact,
        attempt=1,
        token="first",
    )
    second = _move_completed_run_to_stage(
        tmp_path,
        artifact,
        attempt=2,
        token="second",
    )

    with pytest.raises(Session4ArtifactError, match="multiple valid staged candidates"):
        _reconcile_staged_runs(
            tmp_path,
            build_run_matrix(),
            expected_manifest_core_hash=ZERO_HASH,
        )

    assert first.is_dir()
    assert second.is_dir()
    assert not (tmp_path / "runs" / artifact.run_spec.run_id).exists()


def test_resume_hard_fails_on_valid_stage_and_final_conflict_without_overwrite(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    _save_run_atomically(
        tmp_path,
        artifact,
        attempt=1,
        manifest_core_hash=ZERO_HASH,
    )
    final = tmp_path / "runs" / artifact.run_spec.run_id
    original_completion = (final / "complete.json").read_bytes()
    stage = tmp_path / "runs" / ".partial" / f"{artifact.run_spec.run_id}.a001.test.conflict"
    shutil.copytree(final, stage)

    with pytest.raises(Session4ArtifactError, match="conflicts with completed run"):
        _reconcile_staged_runs(
            tmp_path,
            build_run_matrix(),
            expected_manifest_core_hash=ZERO_HASH,
        )

    assert stage.is_dir()
    assert (final / "complete.json").read_bytes() == original_completion
    verify_completed_run(
        final,
        expected_spec=artifact.run_spec,
        expected_manifest_core_hash=ZERO_HASH,
    )


def test_retry_profile_attempt_budget_and_disk_floor_are_frozen(tmp_path: Path) -> None:
    profiles = session4_runner._scientific_profiles()
    assert profiles["retry_profile"] == session4_runner.RETRY_PROFILE_ID
    assert profiles["max_attempts"] == 2
    assert profiles["minimum_free_gib"] == 60.0
    drifted = {**profiles, "max_attempts": 3}
    with pytest.raises(session4_runner.Session4ManifestError, match="profile drift"):
        session4_runner._verify_scientific_profiles(drifted)

    with pytest.raises(Session4RunnerError, match="max_attempts is frozen"):
        session4_runner.run_suite(
            manifest_path=tmp_path / "manifest.json",
            output_directory=tmp_path / "max-attempts",
            max_attempts=3,
        )
    with pytest.raises(Session4RunnerError, match="cannot be lowered"):
        session4_runner.run_suite(
            manifest_path=tmp_path / "manifest.json",
            output_directory=tmp_path / "disk-floor",
            min_free_gib=59.9,
        )


def test_preregistration_core_marker_is_exact_unique_frozen_hash(tmp_path: Path) -> None:
    marker = tmp_path / "prereg.md"
    marker.write_text(
        f"<!-- mr-session-4-manifest-core-sha256: {ZERO_HASH} -->\n",
        encoding="utf-8",
    )
    session4_runner._verify_preregistration_core_marker(marker, ZERO_HASH)

    marker.write_text(
        "<!-- mr-session-4-manifest-core-sha256: PENDING -->\n",
        encoding="utf-8",
    )
    with pytest.raises(session4_runner.Session4ManifestError, match="PENDING"):
        session4_runner._verify_preregistration_core_marker(marker, ZERO_HASH)
    marker.write_text(
        "\n".join(
            [
                f"<!-- mr-session-4-manifest-core-sha256: {ZERO_HASH} -->",
                f"<!-- mr-session-4-manifest-core-sha256: {ZERO_HASH} -->",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(session4_runner.Session4ManifestError, match="exactly one"):
        session4_runner._verify_preregistration_core_marker(marker, ZERO_HASH)
    marker.write_text(
        f"<!-- mr-session-4-manifest-core-sha256: {'1' * 64} -->\n",
        encoding="utf-8",
    )
    with pytest.raises(session4_runner.Session4ManifestError, match="mismatch"):
        session4_runner._verify_preregistration_core_marker(marker, ZERO_HASH)


def test_preregistration_tag_is_unique_canonical_and_bound_to_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, ...]] = []
    output = "mr-session-4-preregistration-2026-07-15\n"

    def fake_run(command: tuple[str, ...], **_kwargs: object) -> object:
        observed.append(command)
        return type("Completed", (), {"stdout": output})()

    monkeypatch.setattr(session4_runner.subprocess, "run", fake_run)
    assert (
        session4_runner._preregistration_tag_for_commit(tmp_path, "frozen-commit")
        == "mr-session-4-preregistration-2026-07-15"
    )
    assert observed == [
        (
            "git",
            "-C",
            str(tmp_path),
            "tag",
            "--points-at",
            "frozen-commit",
            "--list",
            "mr-session-4-preregistration-*",
        )
    ]

    for invalid, error in (
        ("", "exactly one"),
        (
            "mr-session-4-preregistration-2026-07-15\nmr-session-4-preregistration-2026-07-16\n",
            "exactly one",
        ),
        ("mr-session-4-preregistration-final\n", "must match"),
        ("mr-session-4-preregistration-2026-02-30\n", "invalid date"),
    ):
        output = invalid
        with pytest.raises(session4_runner.Session4ManifestError, match=error):
            session4_runner._preregistration_tag_for_commit(tmp_path, "frozen-commit")


def test_manifest_provenance_records_the_verified_preregistration_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preregistration = tmp_path / "preregistration.md"
    preregistration.write_text("frozen preregistration\n", encoding="utf-8")
    tree = SourceTreeState("frozen-commit", False, ZERO_HASH)
    tag = "mr-session-4-preregistration-2026-07-15"
    observed: dict[str, object] = {}
    monkeypatch.setattr(session4_runner, "build_experiment_core", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        session4_runner,
        "_preregistration_tag_for_commit",
        lambda root, commit: observed.update({"root": root, "commit": commit}) or tag,
    )
    monkeypatch.setattr(
        session4_runner,
        "_verify_preregistration_core_marker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        session4_runner,
        "verify_experiment_manifest",
        lambda *_args, **_kwargs: None,
    )

    manifest = session4_runner.build_experiment_manifest(
        object(),  # type: ignore[arg-type]
        preregistration_path=preregistration,
        repo_root=tmp_path,
        source_tree=tree,
    )

    provenance = manifest["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["preregistration_tag"] == tag
    assert observed == {"root": tmp_path, "commit": "frozen-commit"}


def test_prepare_cli_uses_custom_contract_path_and_has_no_ignored_manifest_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = session4_runner._build_parser()
    subcommands = next(action for action in parser._actions if action.dest == "command")
    prepare_parser = subcommands.choices["prepare"]  # type: ignore[attr-defined]
    prepare_fields = {action.dest for action in prepare_parser._actions}
    assert "manifest" not in prepare_fields
    assert {"contracts", "preregistration", "output", "data_root"} <= prepare_fields

    contracts_path = tmp_path / "frozen-contracts.json"
    preregistration_path = tmp_path / "preregistration.md"
    output_path = tmp_path / "manifest.json"
    captured: dict[str, object] = {}
    frozen_contracts = object()

    def fake_load_contracts(path: Path) -> object:
        captured["loaded_contracts_path"] = path
        return frozen_contracts

    monkeypatch.setattr(session4_runner, "load_frozen_bybit_contracts", fake_load_contracts)

    def fake_build(contracts: object, **kwargs: object) -> dict[str, object]:
        captured["contracts"] = contracts
        captured.update(kwargs)
        return {"manifest_core_hash": ZERO_HASH}

    monkeypatch.setattr(session4_runner, "build_experiment_manifest", fake_build)
    monkeypatch.setattr(
        session4_runner,
        "_atomic_write_json",
        lambda path, payload, **kwargs: captured.update(
            {"output_path": path, "payload": payload, "write_options": kwargs}
        ),
    )

    assert (
        session4_runner.main(
            (
                "prepare",
                "--contracts",
                str(contracts_path),
                "--preregistration",
                str(preregistration_path),
                "--output",
                str(output_path),
            )
        )
        == 0
    )
    assert captured["loaded_contracts_path"] == contracts_path
    assert captured["contracts"] is frozen_contracts
    assert captured["contracts_path"] == contracts_path
    assert captured["preregistration_path"] == preregistration_path
    assert captured["output_path"] == output_path


def test_post_run_verify_skips_runtime_fingerprint_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = {"manifest_core_hash": ZERO_HASH}
    observed: dict[str, object] = {}

    def fake_load(*_args: object, **kwargs: object) -> tuple[dict[str, str], object]:
        observed.update(kwargs)
        return manifest, object()

    monkeypatch.setattr(session4_runner, "load_and_verify_manifest", fake_load)
    _atomic_write_json(tmp_path / "experiment_manifest.json", manifest)

    assert (
        session4_runner.verify_suite(
            tmp_path,
            manifest_path=tmp_path / "manifest.json",
            allow_incomplete=True,
        )
        == ()
    )
    assert observed["verify_runtime"] is False
    assert observed["verify_data"] is False


def test_save_rename_crash_recovers_valid_stage_without_reexecution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    final = tmp_path / "runs" / artifact.run_spec.run_id
    real_replace = os.replace

    def crash_on_final_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
    ) -> None:
        if Path(target) == final:
            raise OSError("simulated power loss before final rename")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", crash_on_final_replace)
    with pytest.raises(OSError, match="power loss"):
        _save_run_atomically(
            tmp_path,
            artifact,
            attempt=1,
            manifest_core_hash=ZERO_HASH,
        )
    monkeypatch.setattr(os, "replace", real_replace)

    assert (
        session4_runner._recover_staged_run(
            tmp_path,
            artifact.run_spec,
            expected_manifest_core_hash=ZERO_HASH,
        )
        is True
    )
    verify_completed_run(
        final,
        expected_spec=artifact.run_spec,
        expected_manifest_core_hash=ZERO_HASH,
    )
    assert not list((tmp_path / "runs" / ".partial").iterdir())


def test_final_verified_artifact_repairs_lost_complete_queue_message(tmp_path: Path) -> None:
    artifact = _artifact()
    _save_run_atomically(
        tmp_path,
        artifact,
        attempt=1,
        manifest_core_hash=ZERO_HASH,
    )
    progress = _new_progress(
        {"manifest_core_hash": ZERO_HASH},
        build_run_matrix(),
        tmp_path,
    )
    session4_runner._set_progress_state(
        progress,
        artifact.run_spec.run_id,
        "RUNNING",
        attempts=1,
    )
    verified = _verified_runs(
        tmp_path,
        (artifact.run_spec,),
        expected_manifest_core_hash=ZERO_HASH,
    )

    session4_runner._mark_verified_progress_complete(progress, tmp_path, verified)

    runs = progress["runs"]
    assert isinstance(runs, dict)
    row = runs[artifact.run_spec.run_id]
    assert isinstance(row, dict)
    assert row["status"] == "COMPLETE"
    assert row["attempts"] == 1
    assert "completed_at_utc" in row


def test_finalize_reuses_verified_index_after_crash_and_rejects_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    verified = session4_runner.VerifiedRun(
        run_id=artifact.run_spec.run_id,
        ordinal=artifact.run_spec.ordinal,
        artifact_hash=artifact.result.artifact_hash(),
        completion_hash=ZERO_HASH,
        summary=artifact.summary(),
    )
    monkeypatch.setattr(
        session4_runner,
        "verify_suite",
        lambda *_args, **_kwargs: (verified,),
    )
    _atomic_write_json(tmp_path / "experiment_manifest.json", {"fixture": True})
    expected_index = {
        "schema_version": session4_runner.RESULTS_INDEX_SCHEMA_VERSION,
        "run_count": 1,
        "runs": [session4_runner.normalize_json(verified.summary)],
    }
    _atomic_write_json(tmp_path / "results_index.json", expected_index)

    session4_runner.finalize_suite(
        tmp_path,
        manifest_path=tmp_path / "manifest.json",
    )
    assert (tmp_path / "suite_complete.json").is_file()
    session4_runner.finalize_suite(
        tmp_path,
        manifest_path=tmp_path / "manifest.json",
    )

    (tmp_path / "suite_complete.json").unlink()
    _atomic_write_json(tmp_path / "results_index.json", {"tampered": True})
    with pytest.raises(Session4ArtifactError, match="results index conflicts"):
        session4_runner.finalize_suite(
            tmp_path,
            manifest_path=tmp_path / "manifest.json",
        )


def test_deep_result_verifier_rejects_stats_identity_drift(tmp_path: Path) -> None:
    artifact = _artifact()
    artifact.result.stats["ordinal"] = 999
    with pytest.raises(Session4ArtifactError, match="stats identity ordinal mismatch"):
        session4_runner._verify_session4_result_contract(
            artifact.result,
            invariants={"checks": list(artifact.invariant_ledger)},
            expected_spec=artifact.run_spec,
            final_directory=tmp_path,
        )


def test_atomic_run_commit_roundtrips_and_refuses_tampered_resume(tmp_path: Path) -> None:
    artifact = _artifact()
    _save_run_atomically(
        tmp_path,
        artifact,
        attempt=1,
        manifest_core_hash=ZERO_HASH,
    )
    final = tmp_path / "runs" / artifact.run_spec.run_id
    verified = verify_completed_run(
        final,
        expected_spec=artifact.run_spec,
        expected_manifest_core_hash=ZERO_HASH,
    )
    assert verified.run_id == artifact.run_spec.run_id
    assert not any((tmp_path / "runs" / ".partial").iterdir())
    with pytest.raises(Session4ArtifactError, match="overwrite"):
        _save_run_atomically(
            tmp_path,
            artifact,
            attempt=2,
            manifest_core_hash=ZERO_HASH,
        )

    (final / "counters.json").write_text('{"fills":1}\n', encoding="utf-8")
    with pytest.raises(Session4ArtifactError, match="counters_sha256 mismatch"):
        verify_completed_run(
            final,
            expected_spec=artifact.run_spec,
            expected_manifest_core_hash=ZERO_HASH,
        )


def test_completed_run_from_another_manifest_core_is_rejected(tmp_path: Path) -> None:
    artifact = _artifact()
    _save_run_atomically(
        tmp_path,
        artifact,
        attempt=1,
        manifest_core_hash=ZERO_HASH,
    )

    with pytest.raises(Session4ArtifactError, match="manifest core mismatch"):
        _verified_runs(
            tmp_path,
            (artifact.run_spec,),
            expected_manifest_core_hash="1" * 64,
        )
