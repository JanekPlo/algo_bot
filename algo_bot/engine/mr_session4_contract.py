"""Zamrożony kontrakt badawczy in-sample sweepu MR-Session 4."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from algo_bot.engine.backtest_result import JsonValue, json_hash
from algo_bot.engine.mr_session4_data import SYMBOL_SPECS
from algo_bot.strategies.mastermind.model import AddonTriggerPolicy, MastermindConfig

CONTRACT_SCHEMA_VERSION = "mr_session4_contract/1"
RUN_SPEC_SCHEMA_VERSION = "mr_session4_run_spec/1"
PARAMETER_DESIGN_ID = "CYCLIC_7X3_PLUS_CENTRAL_ANCHOR_V1"
PERFORMANCE_PROFILE_ID = "ADR013_INCLUSIVE_PLUS_ZERO_LIQUIDATIONS_V1"
RANDOM_SEED = 20_260_715
EXPECTED_RUNS = 528
SYMBOLS = ("BTCUSDT", "ETHUSDT")
MARKING_TIMEFRAMES = ("5m", "10m")


class Session4ContractError(ValueError):
    """Zamrożony kontrakt Session 4 jest niespójny albo został zmieniony."""


@dataclass(frozen=True, slots=True)
class ParameterSet:
    """Jeden jawny punkt deterministycznego projektu parametrów."""

    parameter_set_id: str
    bb_window: int
    bb_num_std: Decimal
    arm_expiry_bars: int
    anchor: bool = False

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "parameter_set_id": self.parameter_set_id,
            "bb_window": self.bb_window,
            "bb_num_std": str(self.bb_num_std),
            "arm_expiry_bars": self.arm_expiry_bars,
            "anchor": self.anchor,
        }


@dataclass(frozen=True, slots=True)
class VariantSpec:
    """Jeden prerejestrowany wariant stosu MMS."""

    variant_id: str
    sequential_enabled: bool
    addon_enabled: bool
    addon_policy: AddonTriggerPolicy

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "variant_id": self.variant_id,
            "sequential_enabled": self.sequential_enabled,
            "addon_enabled": self.addon_enabled,
            "addon_policy": self.addon_policy.value,
        }


@dataclass(frozen=True, slots=True)
class Session4RunSpec:
    """Pełna, jednoznaczna specyfikacja jednego z 528 runów."""

    ordinal: int
    symbol: str
    marking_timeframe: str
    parameter_set: ParameterSet
    variant: VariantSpec
    seed: int = RANDOM_SEED

    @property
    def run_id(self) -> str:
        marking = {"5m": "M5", "10m": "M10"}.get(self.marking_timeframe)
        if marking is None:
            raise Session4ContractError(f"unsupported marking timeframe {self.marking_timeframe!r}")
        return (
            f"{self.symbol}__{marking}__{self.parameter_set.parameter_set_id}__"
            f"{self.variant.variant_id}"
        )

    @property
    def machine_config(self) -> MastermindConfig:
        try:
            symbol_spec = SYMBOL_SPECS[self.symbol]
        except KeyError as exc:  # pragma: no cover - guarded by matrix validation
            raise Session4ContractError(f"unsupported symbol {self.symbol!r}") from exc
        return MastermindConfig(
            strategy_id=f"MMS-MR-S4-{self.symbol}",
            instrument_id=symbol_spec.instrument_id,
            addon_trigger_policy=self.variant.addon_policy,
            addon_enabled=self.variant.addon_enabled,
            sequential_enabled=self.variant.sequential_enabled,
            marking_timeframe=self.marking_timeframe,
            bb_window=self.parameter_set.bb_window,
            bb_num_std=self.parameter_set.bb_num_std,
            arm_expiry_bars=self.parameter_set.arm_expiry_bars,
            quantity_step=symbol_spec.size_increment,
            min_quantity=symbol_spec.min_quantity,
            min_notional=symbol_spec.min_notional,
        )

    @property
    def config_hash(self) -> str:
        return self.machine_config.config_hash

    @property
    def run_spec_hash(self) -> str:
        return json_hash(self.as_dict())

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": RUN_SPEC_SCHEMA_VERSION,
            "ordinal": self.ordinal,
            "run_id": self.run_id,
            "symbol": self.symbol,
            "marking_timeframe": self.marking_timeframe,
            "parameter_set": self.parameter_set.as_dict(),
            "variant": self.variant.as_dict(),
            "seed": self.seed,
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True, slots=True)
class PerformanceThresholds:
    """Bramki przed WF; obsunięcie jest ułamkiem, nie punktami procentowymi."""

    sharpe: float = 1.0
    profit_factor: float = 1.3
    n_trades: int = 100
    max_drawdown_fraction: float = -0.20
    max_liquidation_events: int = 0
    comparison: str = "inclusive_greater_or_equal"

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "profile_id": PERFORMANCE_PROFILE_ID,
            "sharpe": self.sharpe,
            "profit_factor": self.profit_factor,
            "n_trades": self.n_trades,
            "max_drawdown_fraction": self.max_drawdown_fraction,
            "max_liquidation_events": self.max_liquidation_events,
            "comparison": self.comparison,
        }


@dataclass(frozen=True, slots=True)
class PerformanceAssessment:
    """Ekonomiczna bramka oddzielona od metodologicznej evidence gate."""

    considered: bool
    passed: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "considered": self.considered,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "profile_id": PERFORMANCE_PROFILE_ID,
        }


# Literalna tabela jest częścią prerejestracji. Nie wolno zastąpić jej losowaniem
# ani generować punktów w runtime zależnie od wyników wcześniejszych runów.
PARAMETER_SETS = (
    ParameterSet("P01_W15_D18_E1", 15, Decimal("1.8"), 1),
    ParameterSet("P02_W17_D19_E1", 17, Decimal("1.9"), 1),
    ParameterSet("P03_W19_D20_E1", 19, Decimal("2.0"), 1),
    ParameterSet("P04_W20_D21_E1", 20, Decimal("2.1"), 1),
    ParameterSet("P05_W21_D22_E1", 21, Decimal("2.2"), 1),
    ParameterSet("P06_W23_D23_E1", 23, Decimal("2.3"), 1),
    ParameterSet("P07_W25_D24_E1", 25, Decimal("2.4"), 1),
    ParameterSet("P08_W15_D20_E2", 15, Decimal("2.0"), 2),
    ParameterSet("P09_W17_D21_E2", 17, Decimal("2.1"), 2),
    ParameterSet("P10_W19_D22_E2", 19, Decimal("2.2"), 2),
    ParameterSet("P11_W20_D23_E2", 20, Decimal("2.3"), 2),
    ParameterSet("P12_W21_D24_E2", 21, Decimal("2.4"), 2),
    ParameterSet("P13_W23_D18_E2", 23, Decimal("1.8"), 2),
    ParameterSet("P14_W25_D19_E2", 25, Decimal("1.9"), 2),
    ParameterSet("P15_W15_D22_E3", 15, Decimal("2.2"), 3),
    ParameterSet("P16_W17_D23_E3", 17, Decimal("2.3"), 3),
    ParameterSet("P17_W19_D24_E3", 19, Decimal("2.4"), 3),
    ParameterSet("P18_W20_D18_E3", 20, Decimal("1.8"), 3),
    ParameterSet("P19_W21_D19_E3", 21, Decimal("1.9"), 3),
    ParameterSet("P20_W23_D20_E3", 23, Decimal("2.0"), 3),
    ParameterSet("P21_W25_D21_E3", 25, Decimal("2.1"), 3),
    ParameterSet("P22_W20_D20_E2_ANCHOR", 20, Decimal("2.0"), 2, anchor=True),
)

VARIANTS = (
    VariantSpec("V1_BASE_ONLY", False, False, AddonTriggerPolicy.CONFIRMING_CANDLE),
    VariantSpec("V2_BASE_SEQ", True, False, AddonTriggerPolicy.CONFIRMING_CANDLE),
    VariantSpec("V3_BASE_CC", False, True, AddonTriggerPolicy.CONFIRMING_CANDLE),
    VariantSpec("V4_BASE_STOCH", False, True, AddonTriggerPolicy.STOCH_CROSS),
    VariantSpec("V5_BASE_SEQ_CC", True, True, AddonTriggerPolicy.CONFIRMING_CANDLE),
    VariantSpec("V6_BASE_SEQ_STOCH", True, True, AddonTriggerPolicy.STOCH_CROSS),
)


def build_run_matrix() -> tuple[Session4RunSpec, ...]:
    """Buduje cztery stabilne strata: BTC/M5, BTC/M10, ETH/M5, ETH/M10."""

    matrix: list[Session4RunSpec] = []
    ordinal = 1
    for symbol in SYMBOLS:
        for marking_timeframe in MARKING_TIMEFRAMES:
            for parameter_set in PARAMETER_SETS:
                for variant in VARIANTS:
                    matrix.append(
                        Session4RunSpec(
                            ordinal=ordinal,
                            symbol=symbol,
                            marking_timeframe=marking_timeframe,
                            parameter_set=parameter_set,
                            variant=variant,
                        )
                    )
                    ordinal += 1
    _validate_run_matrix(matrix)
    return tuple(matrix)


def contract_core() -> dict[str, JsonValue]:
    """Zwraca deterministyczny rdzeń używany przez prerejestrację i manifest."""

    matrix = build_run_matrix()
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "parameter_design_id": PARAMETER_DESIGN_ID,
        "random_seed": RANDOM_SEED,
        "symbols": list(SYMBOLS),
        "marking_timeframes": list(MARKING_TIMEFRAMES),
        "parameter_sets": [item.as_dict() for item in PARAMETER_SETS],
        "variants": [item.as_dict() for item in VARIANTS],
        "run_count": len(matrix),
        "run_specs": [item.as_dict() for item in matrix],
        "performance_thresholds": PerformanceThresholds().as_dict(),
        "fixed_parameters": {
            "stochastic": "14/3/3",
            "base_sl_fraction": "0.02",
            "base_exposure_full": "1",
            "base_exposure_scout": "0.1",
        },
    }


def contract_hash() -> str:
    """Hash dokładnie tego samego rdzenia, który runner weryfikuje offline."""

    return json_hash(contract_core())


def assess_performance(
    stats: Mapping[str, object],
    *,
    evidence_gate_passed: bool,
    liquidation_event_count: int,
    thresholds: PerformanceThresholds = PerformanceThresholds(),
) -> PerformanceAssessment:
    """Stosuje metryki dopiero po twardej bramce fills/margin/evidence."""

    if not evidence_gate_passed:
        return PerformanceAssessment(False, False, ("EVIDENCE_GATE_FAILED",))
    if liquidation_event_count < 0:
        raise Session4ContractError("liquidation_event_count must be non-negative")
    if liquidation_event_count > thresholds.max_liquidation_events:
        return PerformanceAssessment(True, False, ("LIQUIDATION_EVENT_PRESENT",))

    reasons: list[str] = []
    values: dict[str, float] = {}
    for name in ("sharpe", "profit_factor", "n_trades", "max_drawdown_fraction"):
        value = _finite_metric_or_none(stats, name)
        if value is None:
            reasons.append(f"{name.upper()}_MISSING_OR_NONFINITE")
        else:
            values[name] = value
    if reasons:
        return PerformanceAssessment(True, False, tuple(reasons))
    if values["sharpe"] < thresholds.sharpe:
        reasons.append("SHARPE_BELOW_THRESHOLD")
    if values["profit_factor"] < thresholds.profit_factor:
        reasons.append("PROFIT_FACTOR_BELOW_THRESHOLD")
    if values["n_trades"] < thresholds.n_trades:
        reasons.append("N_TRADES_BELOW_THRESHOLD")
    drawdown = values["max_drawdown_fraction"]
    if not -1.0 <= drawdown <= 0.0:
        reasons.append("MAX_DRAWDOWN_FRACTION_OUT_OF_RANGE")
    elif drawdown < thresholds.max_drawdown_fraction:
        reasons.append("MAX_DRAWDOWN_BELOW_THRESHOLD")
    return PerformanceAssessment(True, not reasons, tuple(reasons))


def _finite_metric_or_none(stats: Mapping[str, object], name: str) -> float | None:
    try:
        value = float(str(stats[name]))
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _validate_run_matrix(matrix: list[Session4RunSpec]) -> None:
    if len(PARAMETER_SETS) != 22 or len(VARIANTS) != 6 or len(matrix) != EXPECTED_RUNS:
        raise Session4ContractError("frozen 22 x 6 x 2 x 2 matrix changed")
    if [item.ordinal for item in matrix] != list(range(1, EXPECTED_RUNS + 1)):
        raise Session4ContractError("run ordinals must be contiguous")
    run_ids = [item.run_id for item in matrix]
    spec_hashes = [item.run_spec_hash for item in matrix]
    if len(set(run_ids)) != EXPECTED_RUNS or len(set(spec_hashes)) != EXPECTED_RUNS:
        raise Session4ContractError("run IDs or run specs are not unique")
    if Counter(item.parameter_set.parameter_set_id for item in matrix) != Counter(
        {item.parameter_set_id: 24 for item in PARAMETER_SETS}
    ):
        raise Session4ContractError("parameter multiplicity drift")
    if Counter(item.variant.variant_id for item in matrix) != Counter(
        {item.variant_id: 88 for item in VARIANTS}
    ):
        raise Session4ContractError("variant multiplicity drift")
    if Counter(item.symbol for item in matrix) != Counter(dict.fromkeys(SYMBOLS, 264)):
        raise Session4ContractError("symbol multiplicity drift")
    if Counter(item.marking_timeframe for item in matrix) != Counter(
        dict.fromkeys(MARKING_TIMEFRAMES, 264)
    ):
        raise Session4ContractError("marking-timeframe multiplicity drift")
