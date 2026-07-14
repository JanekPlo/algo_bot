"""Versioned, engine-neutral backtest result artifacts.

The legacy runner historically returned ``(stats, equity, trades)``.  That
tuple is useful to callers, but it is not enough to reproduce or audit a run.
This module defines the richer P8 source result and keeps the tuple as an
explicit compatibility view.

The cost contract intentionally fails closed.  A result with missing,
disabled, approximate, or otherwise unqualified commission, funding,
slippage, or execution evidence can still be saved as a diagnostic artifact,
but it cannot be represented as research eligible.  A future Nautilus wrapper
must provide its native funding-settlement evidence through the same
``CostComponent`` contract; this module does not fabricate those settlements.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_timedelta64_dtype

from algo_bot.microstructure import LiquidationEvent

BACKTEST_RESULT_SCHEMA_VERSION = "backtest_result/2"
LEGACY_BACKTEST_RESULT_SCHEMA_VERSION = "backtest_result/1"
_HASH_LENGTH = 64
_FRAME_NAMES = ("equity", "trades", "orders", "fills", "positions", "funding")

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type LegacyResultTuple = tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]


class BacktestResultError(ValueError):
    """Base class for malformed or ineligible result artifacts."""


class ArtifactIntegrityError(BacktestResultError):
    """Raised when an on-disk artifact does not match its manifest hash."""


class ResearchEligibilityError(BacktestResultError):
    """Raised when a non-eligible result is requested as research evidence."""


class CostProvenance(StrEnum):
    """How a cost/execution component entered the result ledger."""

    NATIVE = "NATIVE"
    HISTORICAL = "HISTORICAL"
    MODELLED = "MODELLED"
    APPROXIMATE = "APPROXIMATE"
    DISABLED = "DISABLED"
    MISSING = "MISSING"


class ResultClass(StrEnum):
    """Permitted interpretation of a result."""

    RESEARCH = "RESEARCH"
    SMOKE_ONLY = "SMOKE_ONLY"
    EQUIVALENCE_ONLY = "EQUIVALENCE_ONLY"


class EligibilityStatus(StrEnum):
    """Whether a result may be used as research evidence."""

    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class FillMethod(StrEnum):
    """Jak engine wyznaczył ceny zapisane w ledgerze fills."""

    CLOSE_NAIVE = "close_naive"
    CLOSE_PLUS_SLIPPAGE = "close_plus_slippage"
    NAUTILUS_NATIVE_BAR = "nautilus_native_bar"


class MarginMethod(StrEnum):
    """Czy margin safety używa przyczynowej historii mark-price."""

    NONE = "none"
    MARK_PRICE_ISOLATED = "mark_price_isolated"


@dataclass(frozen=True, slots=True)
class CostComponent:
    """Auditable evidence for one required cost/execution component.

    ``complete`` means that the run explicitly configured and recorded the
    component.  It does not imply realism.  ``research_eligible`` is a separate
    assertion because native engine output is not automatically a realistic
    cost model.
    """

    model_id: str
    provenance: CostProvenance
    complete: bool
    research_eligible: bool
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise BacktestResultError("Cost component model_id must be non-empty")
        if self.provenance is CostProvenance.MISSING and self.complete:
            raise BacktestResultError("A MISSING cost component cannot be complete")
        if not self.complete and self.research_eligible:
            raise BacktestResultError("An incomplete cost component cannot be eligible")
        if (
            self.provenance
            in {
                CostProvenance.APPROXIMATE,
                CostProvenance.DISABLED,
                CostProvenance.MISSING,
            }
            and self.research_eligible
        ):
            raise BacktestResultError(
                f"{self.provenance.value} cost component cannot be research eligible"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a stable JSON representation."""

        return {
            "model_id": self.model_id,
            "provenance": self.provenance.value,
            "complete": self.complete,
            "research_eligible": self.research_eligible,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> CostComponent:
        """Restore a component from a validated manifest mapping."""

        return cls(
            model_id=_required_str(raw, "model_id"),
            provenance=CostProvenance(_required_str(raw, "provenance")),
            complete=_required_bool(raw, "complete"),
            research_eligible=_required_bool(raw, "research_eligible"),
            notes=_string_tuple(raw.get("notes", []), "notes"),
        )


@dataclass(frozen=True, slots=True)
class CostModel:
    """Complete cost/execution contract required by P8."""

    identifier: str
    commission: CostComponent
    funding: CostComponent
    slippage: CostComponent
    execution: CostComponent

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise BacktestResultError("Cost model identifier must be non-empty")

    def components(self) -> tuple[tuple[str, CostComponent], ...]:
        """Return required components in schema order."""

        return (
            ("commission", self.commission),
            ("funding", self.funding),
            ("slippage", self.slippage),
            ("execution", self.execution),
        )

    def ineligibility_reasons(self) -> tuple[str, ...]:
        """Explain why this model cannot support research eligibility."""

        reasons: list[str] = []
        for name, component in self.components():
            prefix = name.upper()
            if not component.complete or component.provenance is CostProvenance.MISSING:
                reasons.append(f"{prefix}_MISSING")
            elif component.provenance is CostProvenance.DISABLED:
                reasons.append(f"{prefix}_DISABLED")
            elif component.provenance is CostProvenance.APPROXIMATE:
                reasons.append(f"{prefix}_APPROXIMATE")
            elif not component.research_eligible:
                reasons.append(f"{prefix}_NOT_RESEARCH_QUALIFIED")
        return tuple(reasons)

    @property
    def is_complete(self) -> bool:
        """Return whether every required component is explicitly present."""

        return all(component.complete for _, component in self.components())

    @property
    def is_research_eligible(self) -> bool:
        """Return whether every required component is qualified."""

        return not self.ineligibility_reasons()

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a stable JSON representation."""

        return {
            "identifier": self.identifier,
            "commission": self.commission.to_dict(),
            "funding": self.funding.to_dict(),
            "slippage": self.slippage.to_dict(),
            "execution": self.execution.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> CostModel:
        """Restore a cost model from a manifest mapping."""

        return cls(
            identifier=_required_str(raw, "identifier"),
            commission=CostComponent.from_dict(_required_mapping(raw, "commission")),
            funding=CostComponent.from_dict(_required_mapping(raw, "funding")),
            slippage=CostComponent.from_dict(_required_mapping(raw, "slippage")),
            execution=CostComponent.from_dict(_required_mapping(raw, "execution")),
        )


@dataclass(frozen=True, slots=True)
class EligibilityAssessment:
    """Fail-closed interpretation attached to a result."""

    status: EligibilityStatus
    result_class: ResultClass
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status is EligibilityStatus.ELIGIBLE:
            if self.result_class is not ResultClass.RESEARCH or self.reasons:
                raise BacktestResultError(
                    "ELIGIBLE requires RESEARCH result class and no rejection reasons"
                )
        elif not self.reasons:
            raise BacktestResultError("NOT_ELIGIBLE requires at least one reason")
        if (
            self.result_class is ResultClass.RESEARCH
            and self.status is not EligibilityStatus.ELIGIBLE
        ):
            raise BacktestResultError("RESEARCH result class must be ELIGIBLE")

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a stable JSON representation."""

        return {
            "status": self.status.value,
            "result_class": self.result_class.value,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EligibilityAssessment:
        """Restore an eligibility assessment."""

        return cls(
            status=EligibilityStatus(_required_str(raw, "status")),
            result_class=ResultClass(_required_str(raw, "result_class")),
            reasons=_string_tuple(raw.get("reasons", []), "reasons"),
        )


def assess_eligibility(
    cost_model: CostModel,
    *,
    extra_reasons: Sequence[str] = (),
    noneligible_class: ResultClass = ResultClass.SMOKE_ONLY,
) -> EligibilityAssessment:
    """Derive eligibility without trusting a caller-provided boolean."""

    reasons = tuple(dict.fromkeys((*cost_model.ineligibility_reasons(), *extra_reasons)))
    if not reasons:
        return EligibilityAssessment(
            status=EligibilityStatus.ELIGIBLE,
            result_class=ResultClass.RESEARCH,
            reasons=(),
        )
    if noneligible_class is ResultClass.RESEARCH:
        raise BacktestResultError("A non-eligible result cannot use RESEARCH result class")
    if (
        any(
            component.provenance is CostProvenance.APPROXIMATE
            for _, component in cost_model.components()
        )
        and noneligible_class is not ResultClass.SMOKE_ONLY
    ):
        raise BacktestResultError("Approximate costing must be labeled SMOKE_ONLY")
    return EligibilityAssessment(
        status=EligibilityStatus.NOT_ELIGIBLE,
        result_class=noneligible_class,
        reasons=reasons,
    )


@dataclass(frozen=True, slots=True)
class SourceTreeState:
    """Git commit plus a content hash of changes relative to it."""

    git_commit: str
    is_dirty: bool
    changes_hash: str

    def __post_init__(self) -> None:
        if not self.git_commit.strip():
            raise BacktestResultError("git_commit must be non-empty")
        _validate_sha256(self.changes_hash, "changes_hash")

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a stable JSON representation."""

        return {
            "git_commit": self.git_commit,
            "is_dirty": self.is_dirty,
            "changes_hash": self.changes_hash,
            "state": "DIRTY" if self.is_dirty else "CLEAN",
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> SourceTreeState:
        """Restore source-tree metadata."""

        return cls(
            git_commit=_required_str(raw, "git_commit"),
            is_dirty=_required_bool(raw, "is_dirty"),
            changes_hash=_required_str(raw, "changes_hash"),
        )


def capture_source_tree_state(repo_root: Path) -> SourceTreeState:
    """Capture commit and all tracked/untracked changes without mutating Git."""

    root = repo_root.resolve()
    commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = _git(root, "diff", "--binary", "--no-ext-diff", "HEAD", "--", ".")
    untracked_raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z")

    digest = hashlib.sha256()
    digest.update(diff)
    for raw_name in sorted(name for name in untracked_raw.split(b"\0") if name):
        relative_name = raw_name.decode("utf-8", errors="surrogateescape")
        path = root / relative_name
        digest.update(b"\0UNTRACKED\0")
        digest.update(raw_name)
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        elif path.is_symlink():
            digest.update(path.readlink().as_posix().encode("utf-8"))
    return SourceTreeState(
        git_commit=commit,
        is_dirty=bool(status.strip()),
        changes_hash=digest.hexdigest(),
    )


def _git(repo_root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_root), *args),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BacktestResultError(f"Cannot capture Git source-tree state: {exc}") from exc
    return completed.stdout


@dataclass(slots=True)
class BacktestResult:
    """Versioned source result with a legacy tuple compatibility facade."""

    schema_version: str
    engine: str
    engine_version: str
    strategy_version: str
    source_tree: SourceTreeState
    stats: dict[str, JsonValue]
    equity: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    positions: pd.DataFrame
    funding: pd.DataFrame
    data_hash: str
    config_hash: str
    random_seed: int
    cost_model: CostModel
    eligibility: EligibilityAssessment
    fill_method: FillMethod = FillMethod.CLOSE_NAIVE
    margin_method: MarginMethod = MarginMethod.NONE
    mark_price_source: str | None = None
    liquidation_events: tuple[LiquidationEvent, ...] = ()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate schema, hashes, ledgers, and fail-closed eligibility."""

        if self.schema_version != BACKTEST_RESULT_SCHEMA_VERSION:
            raise BacktestResultError(f"Unsupported BacktestResult schema: {self.schema_version!r}")
        for field_name, value in (
            ("engine", self.engine),
            ("engine_version", self.engine_version),
            ("strategy_version", self.strategy_version),
        ):
            if not value.strip():
                raise BacktestResultError(f"{field_name} must be non-empty")
        _validate_sha256(self.data_hash, "data_hash")
        _validate_sha256(self.config_hash, "config_hash")
        if self.random_seed < 0:
            raise BacktestResultError("random_seed must be non-negative")
        if not isinstance(self.fill_method, FillMethod):
            raise BacktestResultError("fill_method must be FillMethod")
        if not isinstance(self.margin_method, MarginMethod):
            raise BacktestResultError("margin_method must be MarginMethod")
        if self.margin_method is MarginMethod.MARK_PRICE_ISOLATED:
            if self.mark_price_source is None or not self.mark_price_source.strip():
                raise BacktestResultError("mark_price_isolated requires mark_price_source")
        elif self.mark_price_source is not None:
            raise BacktestResultError("mark_price_source requires a mark-price margin method")
        if not isinstance(self.liquidation_events, tuple) or not all(
            isinstance(event, LiquidationEvent) for event in self.liquidation_events
        ):
            raise BacktestResultError("liquidation_events must be tuple[LiquidationEvent, ...]")
        for frame_name in _FRAME_NAMES:
            frame = getattr(self, frame_name)
            if not isinstance(frame, pd.DataFrame):
                raise BacktestResultError(f"{frame_name} must be a pandas DataFrame")
            setattr(self, frame_name, _canonicalize_frame(frame))

        normalized_stats = normalize_json(self.stats)
        if not isinstance(normalized_stats, dict):
            raise BacktestResultError("stats must normalize to a JSON object")
        self.stats = normalized_stats

        required_reasons = set(self.cost_model.ineligibility_reasons())
        required_reasons.update(self.evidence_ineligibility_reasons())
        actual_reasons = set(self.eligibility.reasons)
        if self.eligibility.status is EligibilityStatus.ELIGIBLE:
            if required_reasons or not self.cost_model.is_research_eligible:
                raise BacktestResultError(
                    "Cost model is incomplete/unqualified but result claims ELIGIBLE"
                )
        elif not required_reasons.issubset(actual_reasons):
            missing = sorted(required_reasons - actual_reasons)
            raise BacktestResultError(f"Eligibility assessment omits cost-model reasons: {missing}")
        if (
            any(
                component.provenance is CostProvenance.APPROXIMATE
                for _, component in self.cost_model.components()
            )
            and self.eligibility.result_class is not ResultClass.SMOKE_ONLY
        ):
            raise BacktestResultError("Approximate costing requires SMOKE_ONLY result class")

    def evidence_ineligibility_reasons(self) -> tuple[str, ...]:
        """Zwraca braki metodologii fills/margin, niezależnie od wyniku PnL."""

        reasons: list[str] = []
        if self.fill_method is FillMethod.CLOSE_NAIVE:
            reasons.append("FILL_METHOD_CLOSE_NAIVE")
        elif self.fill_method is FillMethod.CLOSE_PLUS_SLIPPAGE:
            reasons.append("FILL_METHOD_CLOSE_PLUS_SLIPPAGE")
        if self.margin_method is MarginMethod.NONE:
            reasons.append("MARK_PRICE_MARGIN_NOT_MODELLED")
        return tuple(reasons)

    def as_legacy_tuple(self) -> LegacyResultTuple:
        """Return the historical ``(stats, equity, trades)`` facade."""

        return cast(dict[str, Any], self.stats), self.equity, self.trades

    def __iter__(self) -> Iterator[object]:
        """Allow explicit rich results to be unpacked like the legacy tuple."""

        yield from self.as_legacy_tuple()

    def assert_research_eligible(self) -> None:
        """Fail closed before a caller treats this result as research evidence."""

        if self.eligibility.status is not EligibilityStatus.ELIGIBLE:
            reasons = ", ".join(self.eligibility.reasons)
            raise ResearchEligibilityError(f"Backtest result is NOT_ELIGIBLE: {reasons}")

    def manifest(self) -> dict[str, JsonValue]:
        """Build the canonical manifest, including every frame digest."""

        frames: dict[str, JsonValue] = {}
        for name in _FRAME_NAMES:
            frame = cast(pd.DataFrame, getattr(self, name))
            frames[name] = {
                "path": f"{name}.json",
                "sha256": dataframe_hash(frame),
                "rows": len(frame),
                "columns": [str(column) for column in frame.columns],
            }
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "strategy_version": self.strategy_version,
            "source_tree": self.source_tree.to_dict(),
            "stats": self.stats,
            "data_hash": self.data_hash,
            "config_hash": self.config_hash,
            "random_seed": self.random_seed,
            "cost_model": self.cost_model.to_dict(),
            "eligibility": self.eligibility.to_dict(),
            "fill_method": self.fill_method.value,
            "margin_method": self.margin_method.value,
            "mark_price_source": self.mark_price_source,
            "liquidation_events": normalize_json(
                [event.to_dict() for event in self.liquidation_events]
            ),
            "frames": frames,
        }

    def artifact_hash(self) -> str:
        """Return a deterministic identity for metadata and all ledgers."""

        return json_hash(self.manifest())

    def save(self, directory: Path) -> Path:
        """Persist a strict JSON manifest and checksummed table artifacts."""

        self.validate()
        directory.mkdir(parents=True, exist_ok=True)
        for name in _FRAME_NAMES:
            frame = cast(pd.DataFrame, getattr(self, name))
            (directory / f"{name}.json").write_text(_dataframe_json(frame), encoding="utf-8")
        manifest_text = canonical_json(self.manifest())
        (directory / "manifest.json").write_text(manifest_text + "\n", encoding="utf-8")
        return directory

    @classmethod
    def load(cls, directory: Path) -> BacktestResult:
        """Load an artifact and reject schema or frame-integrity drift."""

        manifest_path = directory / "manifest.json"
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(f"Cannot read BacktestResult manifest: {exc}") from exc
        if not isinstance(raw_manifest, dict):
            raise ArtifactIntegrityError("BacktestResult manifest must be a JSON object")
        manifest = cast(dict[str, object], raw_manifest)
        stored_schema = manifest.get("schema_version")
        if stored_schema not in {
            BACKTEST_RESULT_SCHEMA_VERSION,
            LEGACY_BACKTEST_RESULT_SCHEMA_VERSION,
        }:
            raise ArtifactIntegrityError(
                f"Unsupported BacktestResult schema: {manifest.get('schema_version')!r}"
            )

        frame_specs = _required_mapping(manifest, "frames")
        loaded_frames: dict[str, pd.DataFrame] = {}
        for name in _FRAME_NAMES:
            spec = _required_mapping(frame_specs, name)
            relative_path = _required_str(spec, "path")
            if relative_path != f"{name}.json":
                raise ArtifactIntegrityError(f"Unexpected path for {name}: {relative_path!r}")
            path = directory / relative_path
            try:
                frame_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ArtifactIntegrityError(f"Cannot read {name} frame: {exc}") from exc
            observed_hash = hashlib.sha256(frame_text.encode("utf-8")).hexdigest()
            expected_hash = _required_str(spec, "sha256")
            if observed_hash != expected_hash:
                raise ArtifactIntegrityError(
                    f"Frame hash mismatch for {name}: expected {expected_hash}, got {observed_hash}"
                )
            try:
                loaded_frames[name] = pd.read_json(io.StringIO(frame_text), orient="table")
            except (ValueError, TypeError) as exc:
                raise ArtifactIntegrityError(f"Cannot decode {name} frame: {exc}") from exc

        stats_raw = _required_mapping(manifest, "stats")
        legacy = stored_schema == LEGACY_BACKTEST_RESULT_SCHEMA_VERSION
        raw_liquidations = manifest.get("liquidation_events", [])
        if not isinstance(raw_liquidations, list):
            raise ArtifactIntegrityError("liquidation_events must be a list")
        liquidation_events: list[LiquidationEvent] = []
        for raw_event in raw_liquidations:
            if not isinstance(raw_event, dict):
                raise ArtifactIntegrityError("liquidation event must be an object")
            try:
                liquidation_events.append(LiquidationEvent.from_dict(raw_event))
            except (KeyError, TypeError, ValueError) as exc:
                raise ArtifactIntegrityError(f"Invalid liquidation event: {exc}") from exc

        restored_cost_model = CostModel.from_dict(_required_mapping(manifest, "cost_model"))
        restored_eligibility = EligibilityAssessment.from_dict(
            _required_mapping(manifest, "eligibility")
        )
        if legacy:
            restored_eligibility = assess_eligibility(
                restored_cost_model,
                extra_reasons=(
                    *restored_eligibility.reasons,
                    "FILL_METHOD_CLOSE_NAIVE",
                    "MARK_PRICE_MARGIN_NOT_MODELLED",
                ),
                noneligible_class=(
                    ResultClass.SMOKE_ONLY
                    if restored_eligibility.result_class is ResultClass.RESEARCH
                    else restored_eligibility.result_class
                ),
            )

        result = cls(
            schema_version=BACKTEST_RESULT_SCHEMA_VERSION,
            engine=_required_str(manifest, "engine"),
            engine_version=_required_str(manifest, "engine_version"),
            strategy_version=_required_str(manifest, "strategy_version"),
            source_tree=SourceTreeState.from_dict(_required_mapping(manifest, "source_tree")),
            stats=cast(dict[str, JsonValue], normalize_json(stats_raw)),
            equity=loaded_frames["equity"],
            trades=loaded_frames["trades"],
            orders=loaded_frames["orders"],
            fills=loaded_frames["fills"],
            positions=loaded_frames["positions"],
            funding=loaded_frames["funding"],
            data_hash=_required_str(manifest, "data_hash"),
            config_hash=_required_str(manifest, "config_hash"),
            random_seed=_required_int(manifest, "random_seed"),
            cost_model=restored_cost_model,
            eligibility=restored_eligibility,
            fill_method=(
                FillMethod.CLOSE_NAIVE
                if legacy
                else FillMethod(_required_str(manifest, "fill_method"))
            ),
            margin_method=(
                MarginMethod.NONE
                if legacy
                else MarginMethod(_required_str(manifest, "margin_method"))
            ),
            mark_price_source=(
                None
                if legacy or manifest.get("mark_price_source") is None
                else str(manifest["mark_price_source"])
            ),
            liquidation_events=tuple(liquidation_events),
        )
        if not legacy and result.manifest() != normalize_json(raw_manifest):
            raise ArtifactIntegrityError("Manifest metadata does not match restored result")
        return result


def normalize_json(value: object) -> JsonValue:
    """Convert supported scientific Python values to strict canonical JSON."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, (pd.Timedelta, timedelta)):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return normalize_json(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return normalize_json(asdict(value))
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BacktestResultError(f"JSON mapping key must be str, got {type(key).__name__}")
            normalized[key] = normalize_json(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize_json(item) for item in value]
    raise BacktestResultError(
        f"Value is not supported by strict JSON schema: {type(value).__name__}"
    )


def canonical_json(value: object) -> str:
    """Return strict deterministic JSON without NaN/Infinity extensions."""

    return json.dumps(
        normalize_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def json_hash(value: object) -> str:
    """SHA-256 a canonical JSON value."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def dataframe_hash(frame: pd.DataFrame) -> str:
    """SHA-256 the exact table representation used by ``save``."""

    return hashlib.sha256(_dataframe_json(frame).encode("utf-8")).hexdigest()


def combined_data_hash(ohlcv: pd.DataFrame, funding: pd.Series | None = None) -> str:
    """Hash the exact OHLCV input and optional funding event series."""

    inputs: dict[str, JsonValue] = {"ohlcv": dataframe_hash(ohlcv)}
    if funding is not None:
        funding_frame = funding.rename("funding_rate").to_frame()
        inputs["funding"] = dataframe_hash(funding_frame)
    else:
        inputs["funding"] = None
    return json_hash(inputs)


def public_stats(stats: Mapping[str, object]) -> dict[str, JsonValue]:
    """Remove embedded engine objects/tables and normalize scalar statistics."""

    excluded = {"_strategy", "_equity_curve", "_trades"}
    selected = {str(key): value for key, value in stats.items() if str(key) not in excluded}
    normalized = normalize_json(selected)
    if not isinstance(normalized, dict):  # defensive; selected is always a dict
        raise BacktestResultError("Normalized stats must be a JSON object")
    return normalized


def legacy_adr011_cost_model(
    *,
    commission_rate: float,
    microstructure_enabled: bool,
    slip_bps: float,
    funding_source: str,
) -> CostModel:
    """Describe the existing legacy engine and ADR-011 overlay honestly.

    The commission is applied by ``backtesting.py``, but the configured rate is
    still a model rather than account-specific fee evidence.  ADR-011 funding
    uses an H1 Close mark proxy (and can fall back to a synthetic rate), while
    slippage is a constant-bps debit.  Consequently the legacy path is always
    diagnostic/smoke evidence even though all chosen components are explicit.
    """

    commission = CostComponent(
        model_id=f"backtesting-relative-commission:{commission_rate:.12g}",
        provenance=CostProvenance.MODELLED,
        complete=True,
        research_eligible=False,
        notes=("engine-applied", "not-account-specific"),
    )
    if microstructure_enabled and funding_source != "none":
        funding = CostComponent(
            model_id=f"adr011-funding:{funding_source}",
            provenance=CostProvenance.APPROXIMATE,
            complete=True,
            research_eligible=False,
            notes=("historical-or-synthetic-rate", "h1-close-mark-proxy"),
        )
    else:
        funding = CostComponent(
            model_id="adr011-funding:disabled",
            provenance=CostProvenance.DISABLED,
            complete=True,
            research_eligible=False,
        )
    if microstructure_enabled:
        slippage = CostComponent(
            model_id=f"adr011-constant-slippage-bps:{slip_bps:.12g}",
            provenance=CostProvenance.APPROXIMATE,
            complete=True,
            research_eligible=False,
            notes=("post-hoc-cash-debit",),
        )
    else:
        slippage = CostComponent(
            model_id="adr011-slippage:disabled",
            provenance=CostProvenance.DISABLED,
            complete=True,
            research_eligible=False,
        )
    execution = CostComponent(
        model_id="backtesting-0.6.5-ohlc-execution",
        provenance=CostProvenance.APPROXIMATE,
        complete=True,
        research_eligible=False,
        notes=("legacy-ohlc-model",),
    )
    identifier = (
        "LEGACY_ADR011_OVERLAY_V1" if microstructure_enabled else "LEGACY_BACKTESTING_NO_OVERLAY_V1"
    )
    return CostModel(
        identifier=identifier,
        commission=commission,
        funding=funding,
        slippage=slippage,
        execution=execution,
    )


_ORDER_COLUMNS = (
    "order_id",
    "trade_id",
    "event_time",
    "role",
    "side",
    "quantity",
    "status",
    "provenance",
)
_FILL_COLUMNS = (
    "fill_id",
    "order_id",
    "trade_id",
    "event_time",
    "side",
    "quantity",
    "price",
    "commission",
    "provenance",
)
_POSITION_COLUMNS = (
    "position_id",
    "entry_time",
    "exit_time",
    "side",
    "quantity",
    "entry_price",
    "exit_price",
    "realized_pnl",
    "commission",
    "status",
    "provenance",
)
_FUNDING_COLUMNS = (
    "settlement_id",
    "event_time",
    "rate",
    "notional",
    "amount",
    "currency",
    "provenance",
)


def empty_funding_ledger() -> pd.DataFrame:
    """Return the typed placeholder a caller must replace with settlement events."""

    return pd.DataFrame(columns=_FUNDING_COLUMNS)


def derive_legacy_ledgers(
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Derive an explicit, honestly labeled ledger from closed legacy trades.

    ``backtesting.py`` does not expose the native order/fill event stream used by
    Nautilus.  The derived rows therefore carry ``LEGACY_DERIVED`` provenance,
    and per-fill commission remains null because the legacy table reports only
    aggregate trade commission.  This is useful for compatibility/audit, but is
    deliberately insufficient for research eligibility.
    """

    order_rows: list[dict[str, object]] = []
    fill_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    for ordinal, (_, trade) in enumerate(trades.reset_index(drop=True).iterrows()):
        trade_id = f"legacy-trade-{ordinal:06d}"
        entry_order_id = f"{trade_id}-entry"
        exit_order_id = f"{trade_id}-exit"
        size = float(trade.get("Size", 0.0))
        quantity = abs(size)
        entry_side = "BUY" if size >= 0 else "SELL"
        exit_side = "SELL" if size >= 0 else "BUY"
        side = "LONG" if size >= 0 else "SHORT"
        entry_time = trade.get("EntryTime")
        exit_time = trade.get("ExitTime")
        entry_price = trade.get("EntryPrice")
        exit_price = trade.get("ExitPrice")

        for order_id, event_time, role, order_side in (
            (entry_order_id, entry_time, "ENTRY", entry_side),
            (exit_order_id, exit_time, "EXIT", exit_side),
        ):
            order_rows.append(
                {
                    "order_id": order_id,
                    "trade_id": trade_id,
                    "event_time": event_time,
                    "role": role,
                    "side": order_side,
                    "quantity": quantity,
                    "status": "FILLED",
                    "provenance": "LEGACY_DERIVED",
                }
            )
        for fill_id, order_id, event_time, fill_side, price in (
            (f"{entry_order_id}-fill", entry_order_id, entry_time, entry_side, entry_price),
            (f"{exit_order_id}-fill", exit_order_id, exit_time, exit_side, exit_price),
        ):
            fill_rows.append(
                {
                    "fill_id": fill_id,
                    "order_id": order_id,
                    "trade_id": trade_id,
                    "event_time": event_time,
                    "side": fill_side,
                    "quantity": quantity,
                    "price": price,
                    "commission": None,
                    "provenance": "LEGACY_DERIVED",
                }
            )
        position_rows.append(
            {
                "position_id": trade_id,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "realized_pnl": trade.get("PnL"),
                "commission": trade.get("Commission"),
                "status": "CLOSED",
                "provenance": "LEGACY_DERIVED",
            }
        )
    return (
        pd.DataFrame(order_rows, columns=_ORDER_COLUMNS),
        pd.DataFrame(fill_rows, columns=_FILL_COLUMNS),
        pd.DataFrame(position_rows, columns=_POSITION_COLUMNS),
    )


def _dataframe_json(frame: pd.DataFrame) -> str:
    return frame.to_json(
        orient="table",
        date_format="iso",
        date_unit="ns",
        double_precision=15,
        index=True,
    )


def _canonicalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize temporal resolution so JSON round-trips are state-equal."""

    normalized = frame.copy(deep=True)
    if isinstance(normalized.index, (pd.DatetimeIndex, pd.TimedeltaIndex)):
        normalized.index = normalized.index.as_unit("ns")
    for column in normalized.columns:
        series = normalized[column]
        if is_datetime64_any_dtype(series.dtype) or is_timedelta64_dtype(series.dtype):
            normalized[column] = series.dt.as_unit("ns")
    return normalized


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise BacktestResultError(f"{field_name} must be a lowercase SHA-256 digest")


def _required_mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ArtifactIntegrityError(f"Manifest field {key!r} must be an object")
    return cast(Mapping[str, object], value)


def _required_str(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ArtifactIntegrityError(f"Manifest field {key!r} must be a string")
    return value


def _required_bool(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ArtifactIntegrityError(f"Manifest field {key!r} must be a boolean")
    return value


def _required_int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactIntegrityError(f"Manifest field {key!r} must be an integer")
    return value


def _string_tuple(raw: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ArtifactIntegrityError(f"Manifest field {field_name!r} must be a string list")
    return tuple(cast(list[str], raw))
