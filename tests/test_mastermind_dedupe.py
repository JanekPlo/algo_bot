"""Bounded event-dedupe and snapshot-size regression fixtures for P6."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from algo_bot.strategies.mastermind.model import (
    RECENT_EVENT_ID_LIMIT,
    AccountEquityUpdated,
    AddonTriggerPolicy,
    FundingApplied,
    MastermindConfig,
)
from algo_bot.strategies.mastermind.state_machine import MastermindStateMachine

START = datetime(2025, 1, 1, tzinfo=UTC)


def config() -> MastermindConfig:
    return MastermindConfig(
        strategy_id="mms-v2",
        instrument_id="BTCUSDT-PERP.BINANCE",
        addon_trigger_policy=AddonTriggerPolicy.STOCH_CROSS,
    )


def equity_event(
    machine: MastermindStateMachine,
    sequence: int,
    *,
    event_id: str | None = None,
    source: str = "bounded-fixture",
) -> AccountEquityUpdated:
    return AccountEquityUpdated(
        event_id=event_id or f"flat-{sequence:06d}",
        strategy_id=machine.config.strategy_id,
        instrument_id=machine.config.instrument_id,
        occurred_at_utc=START + timedelta(microseconds=sequence),
        source=source,
        source_sequence=sequence,
        equity=Decimal("10000"),
    )


def test_recent_event_window_evicts_and_source_highwater_rejects_stale_replay() -> None:
    machine = MastermindStateMachine(config())
    first = equity_event(machine, 0)
    machine.apply(first)
    for sequence in range(1, RECENT_EVENT_ID_LIMIT + 5):
        machine.apply(equity_event(machine, sequence))

    assert len(machine.state.processed_event_ids) == RECENT_EVENT_ID_LIMIT
    assert first.event_id not in machine.state.processed_event_ids
    before = machine.snapshot_json()

    replay = machine.apply(first)
    unseen_stale = machine.apply(
        equity_event(
            machine,
            1,
            event_id="never-seen-but-stale",
        )
    )

    assert replay.duplicate and replay.intents == ()
    assert unseen_stale.duplicate and unseen_stale.intents == ()
    assert machine.snapshot_json() == before

    fresh_source = machine.apply(
        equity_event(machine, 0, event_id="fresh-source-zero", source="another-source")
    )
    assert not fresh_source.duplicate


def test_bounded_window_round_trip_preserves_order_and_global_exact_ids() -> None:
    machine = MastermindStateMachine(config())
    machine.state.processed_execution_ids.add("execution-forever")
    machine.state.pnl.funding_settlement_ids.add("funding-forever")
    for sequence in range(RECENT_EVENT_ID_LIMIT + 17):
        machine.apply(equity_event(machine, sequence))
    raw = machine.snapshot_json()

    restored = MastermindStateMachine.from_snapshot(machine.config, raw)

    assert restored.snapshot_json() == raw
    assert list(restored.state.processed_event_ids) == list(machine.state.processed_event_ids)
    assert len(restored.state.processed_event_ids) == RECENT_EVENT_ID_LIMIT
    assert restored.state.processed_execution_ids == {"execution-forever"}
    assert restored.state.pnl.funding_settlement_ids == {"funding-forever"}
    stale = restored.apply(equity_event(restored, 0, event_id="evicted-after-restore"))
    assert stale.duplicate


def test_five_thousand_flat_events_keep_final_snapshot_size_bounded() -> None:
    """Structural microbenchmark: bounded state avoids quadratic snapshot growth.

    This intentionally asserts bytes, not wall-clock timing, so the regression is
    deterministic across CI hosts.  With fixed-width event IDs, snapshots at 2,500
    and 5,000 events should be effectively the same size.
    """

    machine = MastermindStateMachine(config())
    middle_size = 0
    for sequence in range(5_000):
        result = machine.apply(equity_event(machine, sequence))
        if sequence == 2_499:
            middle_size = len(result.snapshot_json.encode("utf-8"))
    final = machine.snapshot_json().encode("utf-8")

    assert len(machine.state.processed_event_ids) == RECENT_EVENT_ID_LIMIT
    assert len(final) < 100_000
    assert len(final) <= middle_size + 64


def test_unallocated_funding_settlement_remains_globally_deduplicated() -> None:
    machine = MastermindStateMachine(config())
    first = FundingApplied(
        event_id="unallocated-funding-first",
        strategy_id=machine.config.strategy_id,
        instrument_id=machine.config.instrument_id,
        occurred_at_utc=START,
        source="funding-source",
        source_sequence=1,
        settlement_id="settlement-global",
        amount=Decimal("-2"),
    )
    machine.apply(first)
    duplicate_transport = FundingApplied(
        event_id="unallocated-funding-second-transport",
        strategy_id=machine.config.strategy_id,
        instrument_id=machine.config.instrument_id,
        occurred_at_utc=START + timedelta(hours=1),
        source="funding-source",
        source_sequence=2,
        settlement_id="settlement-global",
        amount=Decimal("-2"),
    )

    duplicate = machine.apply(duplicate_transport)

    assert duplicate.duplicate and duplicate.intents == ()
    assert machine.state.pnl.funding == Decimal(0)
    assert machine.state.pnl.funding_settlement_ids == {"settlement-global"}


def test_fresh_machine_snapshot_is_bitwise_deterministic_before_first_event() -> None:
    first = MastermindStateMachine(config())
    second = MastermindStateMachine(config())

    assert first.snapshot_json() == second.snapshot_json()
