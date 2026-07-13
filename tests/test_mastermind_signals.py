"""Causal signal-policy and H1 structural-stop fixtures for P6."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algo_bot.strategies.mastermind.model import (
    AddonTriggerPolicy,
    BarClosed,
    BarSnapshot,
    MastermindConfig,
    Side,
    SignalMemory,
    TriggerKind,
)
from algo_bot.strategies.mastermind.signals import (
    SignalContext,
    evaluate_bar,
    validate_structural_stop,
)

D = Decimal
START = datetime(2025, 1, 1, tzinfo=UTC)


def config(policy: AddonTriggerPolicy) -> MastermindConfig:
    return MastermindConfig(
        strategy_id="mms-v2",
        instrument_id="BTCUSDT-PERP.BINANCE",
        addon_trigger_policy=policy,
    )


def snapshot(
    index: int,
    *,
    open_: str = "99",
    high: str = "100.5",
    low: str = "99",
    close: str = "100",
    k: str | None = "10",
    d: str | None = "12",
) -> BarSnapshot:
    open_time = START + timedelta(hours=index)
    return BarSnapshot(
        bar_id=f"bar-{index}",
        open_time_utc=open_time,
        close_time_utc=open_time + timedelta(hours=1) - timedelta(milliseconds=1),
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D("1"),
        bb_upper=D("102"),
        bb_lower=D("98"),
        stoch_k=None if k is None else D(k),
        stoch_d=None if d is None else D(d),
    )


def event(bar: BarSnapshot) -> BarClosed:
    return BarClosed(
        event_id=f"event-{bar.bar_id}",
        strategy_id="mms-v2",
        instrument_id="BTCUSDT-PERP.BINANCE",
        occurred_at_utc=bar.close_time_utc,
        source="fixture",
        source_sequence=int(bar.bar_id.split("-")[1]),
        bar_id=bar.bar_id,
        open_time_utc=bar.open_time_utc,
        close_time_utc=bar.close_time_utc,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        bb_upper=bar.bb_upper,
        bb_lower=bar.bb_lower,
        stoch_k=bar.stoch_k,
        stoch_d=bar.stoch_d,
    )


def addon_context(reaction: BarSnapshot) -> SignalContext:
    return SignalContext(
        flat_entry_eligible=False,
        exposed=True,
        addon_observable=True,
        addon_opportunity_consumed=False,
        setup_side=Side.LONG,
        reaction_bar=reaction,
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (AddonTriggerPolicy.CONFIRMING_CANDLE, TriggerKind.CONFIRMING_CANDLE),
        (AddonTriggerPolicy.STOCH_CROSS, TriggerKind.STOCH_CROSS),
        (AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH, TriggerKind.CONFIRMING_CANDLE),
        (AddonTriggerPolicy.CANDLE_AND_STOCH, TriggerKind.CANDLE_AND_STOCH),
    ],
)
def test_each_addon_policy_and_simultaneous_tie_break(
    policy: AddonTriggerPolicy,
    expected: TriggerKind,
) -> None:
    reaction = snapshot(0, k="10", d="12")
    current = snapshot(1, open_="99.5", close="100", k="15", d="14")
    memory = SignalMemory(reaction_bar=reaction, recent_bars=[reaction])

    result = evaluate_bar(config(policy), memory, addon_context(reaction), event(current))

    assert result.addon_trigger is not None
    assert result.addon_trigger.trigger_kind is expected
    assert result.addon_trigger.preview_valid
    assert result.addon_trigger.structural_stop == D("99")


@pytest.mark.parametrize(
    ("policy", "candle", "stoch", "has_trigger"),
    [
        (AddonTriggerPolicy.CONFIRMING_CANDLE, False, True, False),
        (AddonTriggerPolicy.STOCH_CROSS, True, False, False),
        (AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH, False, True, True),
        (AddonTriggerPolicy.CANDLE_AND_STOCH, True, False, False),
    ],
)
def test_qualifying_and_nonqualifying_policy_combinations(
    policy: AddonTriggerPolicy,
    candle: bool,
    stoch: bool,
    has_trigger: bool,
) -> None:
    reaction = snapshot(0, k="10", d="12")
    current = snapshot(
        1,
        open_="99.5" if candle else "100.5",
        close="100",
        k="15" if stoch else "9",
        d="14" if stoch else "10",
    )

    result = evaluate_bar(
        config(policy),
        SignalMemory(reaction_bar=reaction, recent_bars=[reaction]),
        addon_context(reaction),
        event(current),
    )

    assert (result.addon_trigger is not None) is has_trigger


def test_confirming_candle_is_only_immediate_next_closed_h1() -> None:
    reaction = snapshot(0)
    first = snapshot(1, open_="100.5", close="100", k="50", d="50")
    memory = SignalMemory(reaction_bar=reaction, recent_bars=[reaction])
    first_result = evaluate_bar(
        config(AddonTriggerPolicy.CONFIRMING_CANDLE),
        memory,
        addon_context(reaction),
        event(first),
    )
    second = snapshot(2, open_="99.5", close="100", k="50", d="50")

    second_result = evaluate_bar(
        config(AddonTriggerPolicy.CONFIRMING_CANDLE),
        first_result.memory,
        addon_context(reaction),
        event(second),
    )

    assert first_result.addon_trigger is None
    assert first_result.memory.confirming_candle_checked
    assert second_result.addon_trigger is None


def test_stoch_thresholds_are_strict_and_require_a_cross() -> None:
    reaction = snapshot(0, k="10", d="12")
    memory = SignalMemory(reaction_bar=reaction, recent_bars=[reaction])
    at_threshold = snapshot(1, open_="100.5", close="100", k="20", d="19")

    result = evaluate_bar(
        config(AddonTriggerPolicy.STOCH_CROSS),
        memory,
        addon_context(reaction),
        event(at_threshold),
    )

    assert result.addon_trigger is None


@pytest.mark.parametrize(
    ("side", "stop", "fill", "valid", "distance"),
    [
        (Side.LONG, "99.00", "100.00", True, "0.01"),
        (Side.LONG, "98.99", "100.00", False, "0.0101"),
        (Side.LONG, "100.00", "100.00", False, "0"),
        (Side.LONG, "100.20", "100.00", False, "0.002"),
        (Side.SHORT, "101.00", "100.00", True, "0.01"),
        (Side.LONG, "49650", "50000", True, "0.007"),
    ],
)
def test_wick_stop_distance_fixtures_are_exact_and_never_clamped(
    side: Side,
    stop: str,
    fill: str,
    valid: bool,
    distance: str,
) -> None:
    accepted, actual_distance, _reason = validate_structural_stop(
        side=side,
        structural_stop=D(stop),
        fill_or_reference_price=D(fill),
        max_distance=D("0.01"),
    )

    assert accepted is valid
    assert actual_distance == D(distance)


def test_base_armed_reaction_does_not_use_stochastic_gate() -> None:
    cfg = config(AddonTriggerPolicy.CONFIRMING_CANDLE)
    touch = snapshot(
        0,
        open_="100",
        high="101",
        low="97",
        close="99",
        k=None,
        d=None,
    )
    context = SignalContext(
        flat_entry_eligible=True,
        exposed=False,
        addon_observable=False,
        addon_opportunity_consumed=False,
        setup_side=None,
        reaction_bar=None,
    )
    armed = evaluate_bar(cfg, SignalMemory(), context, event(touch))
    reaction = snapshot(1, open_="99", high="100.5", low="99", close="100", k=None, d=None)

    reacted = evaluate_bar(cfg, armed.memory, context, event(reaction))

    assert armed.memory.armed_side is Side.LONG
    assert reacted.base_reaction is not None
    assert reacted.base_reaction.side is Side.LONG


def test_touching_both_bands_does_not_arm() -> None:
    giant = snapshot(0, open_="100", high="103", low="97", close="100")
    context = SignalContext(True, False, False, False, None, None)

    result = evaluate_bar(
        config(AddonTriggerPolicy.CONFIRMING_CANDLE),
        SignalMemory(),
        context,
        event(giant),
    )

    assert result.memory.armed_side is None


def test_arming_expires_without_refresh_or_flip() -> None:
    cfg = config(AddonTriggerPolicy.CONFIRMING_CANDLE)
    context = SignalContext(True, False, False, False, None, None)
    armed = evaluate_bar(
        cfg,
        SignalMemory(),
        context,
        event(snapshot(0, open_="100", high="101", low="97", close="99")),
    )
    first_wait = evaluate_bar(
        cfg,
        armed.memory,
        context,
        event(snapshot(1, open_="100", high="103", low="99", close="100")),
    )
    expired = evaluate_bar(
        cfg,
        first_wait.memory,
        context,
        event(snapshot(2, open_="100", high="103", low="99", close="100")),
    )

    assert first_wait.memory.armed_side is Side.LONG
    assert expired.memory.armed_side is None


@pytest.mark.parametrize(
    ("side", "expected"),
    [(Side.LONG, D("102")), (Side.SHORT, D("98"))],
)
def test_live_target_uses_current_opposite_band(side: Side, expected: Decimal) -> None:
    bar = snapshot(0)
    context = SignalContext(False, True, False, False, side, bar)

    result = evaluate_bar(
        config(AddonTriggerPolicy.STOCH_CROSS),
        SignalMemory(),
        context,
        event(bar),
    )

    assert result.target is not None and result.target.trigger_price == expected
