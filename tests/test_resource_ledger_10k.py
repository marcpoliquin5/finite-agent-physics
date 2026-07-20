from dataclasses import replace

import pytest

from agent_physics.resource_ledger import (
    DEFAULT_STRESS_SEED,
    DEFAULT_STRESS_TRANSITIONS,
    EVENT_ID_PREFIX,
    ResourceBudgetLedger,
    ResourceVector,
    event_log_digest,
    generate_stress_corpus,
    replay_and_verify,
)


def test_exactly_10000_seeded_transitions_replay_without_conservation_failure() -> None:
    corpus = generate_stress_corpus()
    report = corpus.verify()

    assert corpus.seed == DEFAULT_STRESS_SEED == 20_260_731
    assert corpus.transition_count == DEFAULT_STRESS_TRANSITIONS == 10_000
    assert len(corpus.events) == report.event_count == 10_000
    assert corpus.peak_active_attempts >= 32
    assert set(corpus.operation_counts) == {"reserve", "settle", "cancel"}
    assert corpus.refusal_counts["actual_over_reservation"] > 0
    assert corpus.refusal_counts["capacity_exceeded"] > 0
    assert corpus.refusal_counts["identity_conflict"] > 0

    assert report.passed, report.failure_corpus().as_dict()
    assert not report.failures
    assert report.replayed_snapshot == corpus.final_snapshot
    assert report.trace_digest == corpus.trace_digest == event_log_digest(corpus.events)
    assert corpus.trace_digest == "5811acacd3df896505265362b7491606094b8b96d7dc25e3c474a92fc38a200d"
    assert len(report.trace_digest) == len(report.failure_digest) == 64
    assert [event.sequence for event in corpus.events] == list(range(1, 10_001))
    assert [event.event_id for event in corpus.events] == [
        f"{EVENT_ID_PREFIX}-{sequence:012d}" for sequence in range(1, 10_001)
    ]
    assert corpus.events[-1].event_id == f"{EVENT_ID_PREFIX}-000000010000"

    committed = corpus.final_snapshot.spent.add(corpus.final_snapshot.held)
    assert committed.fits_within(corpus.capacity)
    assert committed.add(corpus.final_snapshot.available) == corpus.capacity

    independently_summed_spend = ResourceVector.zero()
    for event in corpus.events:
        if event.operation.value == "settle" and event.applied:
            assert event.actual is not None
            assert event.reservation is not None
            assert event.actual.fits_within(event.reservation)
            independently_summed_spend = independently_summed_spend.add(event.actual)
    assert independently_summed_spend == corpus.final_snapshot.spent


def test_reservation_settlement_cancellation_and_identity_are_fail_closed() -> None:
    capacity = ResourceVector(100, 200, 300)
    ledger = ResourceBudgetLedger(capacity)

    reserved = ResourceVector(80, 150, 240)
    assert ledger.reserve("a", reserved).applied

    refused_capacity = ledger.reserve("b", ResourceVector(21, 1, 1))
    assert not refused_capacity.applied
    assert refused_capacity.reason == "capacity_exceeded"

    refused_actual = ledger.settle("a", ResourceVector(81, 150, 240))
    assert not refused_actual.applied
    assert refused_actual.reason == "actual_over_reservation"
    assert ledger.snapshot().held == reserved
    assert ledger.snapshot().spent == ResourceVector.zero()

    cancelled = ledger.cancel("a")
    assert cancelled.applied
    assert ledger.snapshot().available == capacity

    reused = ledger.reserve("a", ResourceVector(1, 1, 1))
    assert not reused.applied
    assert reused.reason == "identity_conflict"

    assert ledger.reserve("c", ResourceVector(60, 100, 120)).applied
    assert ledger.settle("c", ResourceVector(40, 70, 90)).applied
    assert ledger.snapshot().spent == ResourceVector(40, 70, 90)
    assert ledger.snapshot().available == ResourceVector(60, 130, 210)

    report = replay_and_verify(
        capacity,
        ledger.events,
        claimed_snapshot=ledger.snapshot(),
        seed=7,
    )
    assert report.passed
    assert report.failure_corpus().seed == 7


def test_independent_replay_detects_event_identity_and_hidden_state_tampering() -> None:
    capacity = ResourceVector(10, 10, 10)
    ledger = ResourceBudgetLedger(capacity)
    ledger.reserve("a", ResourceVector(5, 5, 5))
    ledger.settle("a", ResourceVector(3, 3, 3))
    events = list(ledger.events)

    events[1] = replace(events[1], sequence=1, event_id=events[0].event_id)
    report = replay_and_verify(capacity, events, claimed_snapshot=ledger.snapshot(), seed=11)
    codes = {failure.code for failure in report.failures}
    assert not report.passed
    assert {"non_monotonic_sequence", "invalid_event_id", "duplicate_event_id"} <= codes
    assert report.failure_corpus().digest == report.failure_digest

    forged_snapshot = replace(
        ledger.snapshot(),
        spent=ResourceVector(4, 3, 3),
        available=ResourceVector(6, 7, 7),
    )
    hidden = replay_and_verify(capacity, ledger.events, claimed_snapshot=forged_snapshot)
    assert not hidden.passed
    assert any(failure.code == "claimed_snapshot_mismatch" for failure in hidden.failures)


def test_independent_replay_rejects_an_applied_over_reservation_settlement() -> None:
    capacity = ResourceVector(10, 10, 10)
    ledger = ResourceBudgetLedger(capacity)
    ledger.reserve("a", ResourceVector(5, 5, 5))
    ledger.settle("a", ResourceVector(3, 3, 3))
    events = list(ledger.events)
    events[1] = replace(events[1], actual=ResourceVector(6, 3, 3))

    report = replay_and_verify(capacity, events)
    codes = {failure.code for failure in report.failures}
    assert not report.passed
    assert "decision_mismatch" in codes
    assert "post_snapshot_mismatch" in codes


@pytest.mark.parametrize(
    "units",
    [(-1, 0, 0), (0, -1, 0), (0, 0, -1)],
)
def test_resource_vectors_reject_negative_units(units: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        ResourceVector(*units)


def test_invalid_scalar_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        ResourceVector(True, 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive integer"):
        generate_stress_corpus(transitions=0)
    with pytest.raises(ValueError, match="seed must be an integer"):
        generate_stress_corpus(seed=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty string"):
        ResourceBudgetLedger(ResourceVector(1, 1, 1)).reserve(" ", ResourceVector.zero())
