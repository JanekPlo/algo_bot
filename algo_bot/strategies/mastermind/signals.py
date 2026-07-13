"""Pure H1 signal and structural-stop evaluation for Mastermind v2."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal

from algo_bot.strategies.mastermind.model import (
    ZERO,
    AddonTriggerPolicy,
    BarClosed,
    BarSnapshot,
    MastermindConfig,
    Side,
    SignalMemory,
    TriggerKind,
)


@dataclass(frozen=True, slots=True)
class SignalContext:
    """Lifecycle guards supplied by the reducer, not inferred from indicators."""

    flat_entry_eligible: bool
    exposed: bool
    addon_observable: bool
    addon_opportunity_consumed: bool
    setup_side: Side | None
    reaction_bar: BarSnapshot | None


@dataclass(frozen=True, slots=True)
class BaseReactionFact:
    side: Side
    reaction_bar: BarSnapshot


@dataclass(frozen=True, slots=True)
class AddonTriggerFact:
    trigger_id: str
    trigger_kind: TriggerKind
    trigger_bar: BarSnapshot
    structural_stop: Decimal
    reference_price: Decimal
    preview_distance: Decimal
    preview_valid: bool
    invalid_reason: str | None


@dataclass(frozen=True, slots=True)
class TargetFact:
    trigger_price: Decimal


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    memory: SignalMemory
    base_reaction: BaseReactionFact | None = None
    addon_trigger: AddonTriggerFact | None = None
    target: TargetFact | None = None


def evaluate_bar(
    config: MastermindConfig,
    memory: SignalMemory,
    context: SignalContext,
    event: BarClosed,
) -> SignalEvaluation:
    """Evaluate one final closed bar without touching lifecycle or orders."""

    next_memory = copy.deepcopy(memory)
    bar = event.snapshot()
    previous_bar = next_memory.recent_bars[-1] if next_memory.recent_bars else None
    base_reaction: BaseReactionFact | None = None
    addon_trigger: AddonTriggerFact | None = None
    target: TargetFact | None = None

    if context.exposed and context.setup_side is not None:
        level = bar.bb_upper if context.setup_side is Side.LONG else bar.bb_lower
        if level.is_finite() and level > ZERO:
            target = TargetFact(level)

    immediate_confirmation = False
    candle_fact = False
    if (
        context.reaction_bar is not None
        and bar.close_time_utc > context.reaction_bar.close_time_utc
        and not next_memory.confirming_candle_checked
    ):
        next_memory.confirming_candle_checked = True
        immediate_confirmation = True
        candle_fact = _directional_body(context.setup_side, bar)

    if context.addon_observable and not context.addon_opportunity_consumed:
        stoch_fact = _stoch_cross(config, context.setup_side, previous_bar, bar)
        chosen = _choose_trigger(
            config.addon_trigger_policy,
            candle_fact=candle_fact and immediate_confirmation,
            stoch_fact=stoch_fact,
        )
        if (
            chosen is not None
            and context.setup_side is not None
            and context.reaction_bar is not None
        ):
            trigger_id = _trigger_id(
                setup_side=context.setup_side,
                policy=config.addon_trigger_policy,
                bar_id=bar.bar_id,
                trigger_kind=chosen,
                reaction_bar_id=context.reaction_bar.bar_id,
            )
            if trigger_id not in next_memory.seen_trigger_ids:
                structural_stop = _structural_stop(
                    side=context.setup_side,
                    trigger_kind=chosen,
                    reaction_bar=context.reaction_bar,
                    previous_bar=previous_bar,
                    trigger_bar=bar,
                )
                valid, distance, reason = validate_structural_stop(
                    side=context.setup_side,
                    structural_stop=structural_stop,
                    fill_or_reference_price=bar.close,
                    max_distance=config.addon_max_sl_pct,
                )
                next_memory.seen_trigger_ids.add(trigger_id)
                addon_trigger = AddonTriggerFact(
                    trigger_id=trigger_id,
                    trigger_kind=chosen,
                    trigger_bar=bar,
                    structural_stop=structural_stop,
                    reference_price=bar.close,
                    preview_distance=distance,
                    preview_valid=valid,
                    invalid_reason=reason,
                )

    if context.flat_entry_eligible:
        if next_memory.armed_side is not None:
            armed_side = next_memory.armed_side
            if _reaction_ok(
                side=armed_side,
                bar=bar,
                require_reclaim=config.require_reclaim,
            ):
                base_reaction = BaseReactionFact(armed_side, bar)
                next_memory.armed_side = None
                next_memory.armed_bars_remaining = 0
                next_memory.touch_bar_id = None
                next_memory.reaction_bar = bar
                next_memory.confirming_candle_checked = False
            else:
                next_memory.armed_bars_remaining -= 1
                if next_memory.armed_bars_remaining <= 0:
                    next_memory.armed_side = None
                    next_memory.armed_bars_remaining = 0
                    next_memory.touch_bar_id = None
        else:
            touch_long = bar.low <= bar.bb_lower
            touch_short = bar.high >= bar.bb_upper
            if touch_long != touch_short:
                next_memory.armed_side = Side.LONG if touch_long else Side.SHORT
                next_memory.armed_bars_remaining = config.arm_expiry_bars
                next_memory.touch_bar_id = bar.bar_id

    next_memory.recent_bars.append(bar)
    next_memory.recent_bars = next_memory.recent_bars[-2:]
    return SignalEvaluation(
        memory=next_memory,
        base_reaction=base_reaction,
        addon_trigger=addon_trigger,
        target=target,
    )


def validate_structural_stop(
    *,
    side: Side,
    structural_stop: Decimal,
    fill_or_reference_price: Decimal,
    max_distance: Decimal,
) -> tuple[bool, Decimal, str | None]:
    """Validate, never clamp, the structural stop against a fill/reference."""

    if not structural_stop.is_finite() or not fill_or_reference_price.is_finite():
        return False, ZERO, "NON_FINITE"
    if fill_or_reference_price <= ZERO:
        return False, ZERO, "NON_POSITIVE_REFERENCE"
    correctly_sided = (
        structural_stop < fill_or_reference_price
        if side is Side.LONG
        else structural_stop > fill_or_reference_price
    )
    distance = abs(fill_or_reference_price - structural_stop) / fill_or_reference_price
    if not correctly_sided or distance == ZERO:
        return False, distance, "WRONG_SIDE_OR_ZERO_DISTANCE"
    if distance > max_distance:
        return False, distance, "DISTANCE_EXCEEDS_CAP"
    return True, distance, None


def _reaction_ok(*, side: Side, bar: BarSnapshot, require_reclaim: bool) -> bool:
    if side is Side.LONG:
        return bar.close > bar.open and (not require_reclaim or bar.close > bar.bb_lower)
    return bar.close < bar.open and (not require_reclaim or bar.close < bar.bb_upper)


def _directional_body(side: Side | None, bar: BarSnapshot) -> bool:
    if side is Side.LONG:
        return bar.close > bar.open
    if side is Side.SHORT:
        return bar.close < bar.open
    return False


def _stoch_cross(
    config: MastermindConfig,
    side: Side | None,
    previous: BarSnapshot | None,
    current: BarSnapshot,
) -> bool:
    if side is None or previous is None:
        return False
    values = (previous.stoch_k, previous.stoch_d, current.stoch_k, current.stoch_d)
    if any(value is None or not value.is_finite() for value in values):
        return False
    prev_k, prev_d, curr_k, curr_d = values
    assert prev_k is not None and prev_d is not None and curr_k is not None and curr_d is not None
    if side is Side.LONG:
        return (
            prev_k <= prev_d
            and curr_k > curr_d
            and curr_k < config.stoch_oversold
            and curr_d < config.stoch_oversold
        )
    return (
        prev_k >= prev_d
        and curr_k < curr_d
        and curr_k > config.stoch_overbought
        and curr_d > config.stoch_overbought
    )


def _choose_trigger(
    policy: AddonTriggerPolicy,
    *,
    candle_fact: bool,
    stoch_fact: bool,
) -> TriggerKind | None:
    if policy is AddonTriggerPolicy.CONFIRMING_CANDLE:
        return TriggerKind.CONFIRMING_CANDLE if candle_fact else None
    if policy is AddonTriggerPolicy.STOCH_CROSS:
        return TriggerKind.STOCH_CROSS if stoch_fact else None
    if policy is AddonTriggerPolicy.FIRST_OF_CANDLE_OR_STOCH:
        if candle_fact:
            return TriggerKind.CONFIRMING_CANDLE
        return TriggerKind.STOCH_CROSS if stoch_fact else None
    if candle_fact and stoch_fact:
        return TriggerKind.CANDLE_AND_STOCH
    return None


def _structural_stop(
    *,
    side: Side,
    trigger_kind: TriggerKind,
    reaction_bar: BarSnapshot,
    previous_bar: BarSnapshot | None,
    trigger_bar: BarSnapshot,
) -> Decimal:
    candle_level = _extreme(side, reaction_bar, trigger_bar)
    if previous_bar is None:
        return candle_level
    stoch_level = _extreme(side, previous_bar, trigger_bar)
    if trigger_kind is TriggerKind.CONFIRMING_CANDLE:
        return candle_level
    if trigger_kind is TriggerKind.STOCH_CROSS:
        return stoch_level
    # AND uses the structurally farther level across both candidate pairs.
    return (
        min(candle_level, stoch_level)
        if side is Side.LONG
        else max(
            candle_level,
            stoch_level,
        )
    )


def _extreme(side: Side, first: BarSnapshot, second: BarSnapshot) -> Decimal:
    if side is Side.LONG:
        return min(first.low, second.low)
    return max(first.high, second.high)


def _trigger_id(
    *,
    setup_side: Side,
    policy: AddonTriggerPolicy,
    bar_id: str,
    trigger_kind: TriggerKind,
    reaction_bar_id: str,
) -> str:
    return ":".join(
        (
            reaction_bar_id,
            setup_side.value,
            policy.value,
            bar_id,
            trigger_kind.value,
        )
    )
