from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from algo_bot.engine.mr_session4_contract import (
    EXPECTED_RUNS,
    PARAMETER_SETS,
    VARIANTS,
    assess_performance,
    build_run_matrix,
    contract_hash,
)


def test_literal_parameter_design_matches_independent_oracle() -> None:
    observed = [
        (
            item.parameter_set_id,
            item.bb_window,
            str(item.bb_num_std),
            item.arm_expiry_bars,
            item.anchor,
        )
        for item in PARAMETER_SETS
    ]
    assert observed == [
        ("P01_W15_D18_E1", 15, "1.8", 1, False),
        ("P02_W17_D19_E1", 17, "1.9", 1, False),
        ("P03_W19_D20_E1", 19, "2.0", 1, False),
        ("P04_W20_D21_E1", 20, "2.1", 1, False),
        ("P05_W21_D22_E1", 21, "2.2", 1, False),
        ("P06_W23_D23_E1", 23, "2.3", 1, False),
        ("P07_W25_D24_E1", 25, "2.4", 1, False),
        ("P08_W15_D20_E2", 15, "2.0", 2, False),
        ("P09_W17_D21_E2", 17, "2.1", 2, False),
        ("P10_W19_D22_E2", 19, "2.2", 2, False),
        ("P11_W20_D23_E2", 20, "2.3", 2, False),
        ("P12_W21_D24_E2", 21, "2.4", 2, False),
        ("P13_W23_D18_E2", 23, "1.8", 2, False),
        ("P14_W25_D19_E2", 25, "1.9", 2, False),
        ("P15_W15_D22_E3", 15, "2.2", 3, False),
        ("P16_W17_D23_E3", 17, "2.3", 3, False),
        ("P17_W19_D24_E3", 19, "2.4", 3, False),
        ("P18_W20_D18_E3", 20, "1.8", 3, False),
        ("P19_W21_D19_E3", 21, "1.9", 3, False),
        ("P20_W23_D20_E3", 23, "2.0", 3, False),
        ("P21_W25_D21_E3", 25, "2.1", 3, False),
        ("P22_W20_D20_E2_ANCHOR", 20, "2.0", 2, True),
    ]


def test_matrix_is_exact_unique_22_x_6_x_2_x_2() -> None:
    matrix = build_run_matrix()
    assert len(matrix) == EXPECTED_RUNS == 528
    assert [item.ordinal for item in matrix] == list(range(1, 529))
    assert len({item.run_id for item in matrix}) == 528
    assert len({item.run_spec_hash for item in matrix}) == 528
    assert Counter(item.parameter_set.parameter_set_id for item in matrix) == Counter(
        {item.parameter_set_id: 24 for item in PARAMETER_SETS}
    )
    assert Counter(item.variant.variant_id for item in matrix) == Counter(
        {item.variant_id: 88 for item in VARIANTS}
    )
    assert Counter(item.symbol for item in matrix) == Counter({"BTCUSDT": 264, "ETHUSDT": 264})
    assert Counter(item.marking_timeframe for item in matrix) == Counter({"5m": 264, "10m": 264})
    assert all("H1" not in item.run_id for item in matrix)
    assert matrix[0].run_id.startswith("BTCUSDT__M5__P01")
    assert matrix[131].run_id.startswith("BTCUSDT__M5__P22")
    assert matrix[132].run_id.startswith("BTCUSDT__M10__P01")
    assert matrix[264].run_id.startswith("ETHUSDT__M5__P01")
    assert matrix[396].run_id.startswith("ETHUSDT__M10__P01")


def test_contract_hash_is_deterministic() -> None:
    assert contract_hash() == contract_hash()
    assert len(contract_hash()) == 64


class _PoisonMetrics(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise AssertionError(f"metric {key} was read before evidence gate")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("metrics were iterated before evidence gate")

    def __len__(self) -> int:
        raise AssertionError("metrics length was read before evidence gate")


def test_evidence_failure_short_circuits_metric_access() -> None:
    result = assess_performance(
        _PoisonMetrics(),
        evidence_gate_passed=False,
        liquidation_event_count=0,
    )
    assert result.considered is False
    assert result.passed is False
    assert result.reasons == ("EVIDENCE_GATE_FAILED",)


def test_liquidation_short_circuits_economic_metric_access() -> None:
    result = assess_performance(
        _PoisonMetrics(),
        evidence_gate_passed=True,
        liquidation_event_count=1,
    )
    assert result.considered is True
    assert result.passed is False
    assert result.reasons == ("LIQUIDATION_EVENT_PRESENT",)


@pytest.mark.parametrize(
    ("changes", "liquidations", "passed", "reason"),
    [
        ({}, 0, True, None),
        ({"sharpe": 0.999999}, 0, False, "SHARPE_BELOW_THRESHOLD"),
        ({"profit_factor": 1.299999}, 0, False, "PROFIT_FACTOR_BELOW_THRESHOLD"),
        ({"n_trades": 99}, 0, False, "N_TRADES_BELOW_THRESHOLD"),
        (
            {"max_drawdown_fraction": -0.200001},
            0,
            False,
            "MAX_DRAWDOWN_BELOW_THRESHOLD",
        ),
        ({}, 1, False, "LIQUIDATION_EVENT_PRESENT"),
        ({"sharpe": float("nan")}, 0, False, "SHARPE_MISSING_OR_NONFINITE"),
        ({"profit_factor": float("inf")}, 0, False, "PROFIT_FACTOR_MISSING_OR_NONFINITE"),
    ],
)
def test_performance_gate_has_inclusive_boundaries_and_fails_closed(
    changes: Mapping[str, Any],
    liquidations: int,
    passed: bool,
    reason: str | None,
) -> None:
    metrics: dict[str, object] = {
        "sharpe": 1.0,
        "profit_factor": 1.3,
        "n_trades": 100,
        "max_drawdown_fraction": -0.20,
    }
    metrics.update(changes)
    result = assess_performance(
        metrics,
        evidence_gate_passed=True,
        liquidation_event_count=liquidations,
    )
    assert result.considered is True
    assert result.passed is passed
    if reason is not None:
        assert reason in result.reasons
