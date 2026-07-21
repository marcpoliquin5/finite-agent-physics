from __future__ import annotations

from dataclasses import replace

import pytest

from agent_physics.provider_quota import (
    GLOBAL_GUARD_SCOPE,
    MODEL_SCOPE,
    SETTLEMENT_REFUND_POLICY,
    CallRequest,
    FakeIntegerClock,
    ProviderQuotaPhysics,
    QuotaConfigurationError,
    QuotaEvent,
    QuotaLimits,
    QuotaReplayError,
    QuotaSettlementError,
    RefusalReason,
    RetryPolicy,
    replay_quota_events,
    run_seeded_burst_corpus,
)


def _physics(
    *,
    clock: FakeIntegerClock | None = None,
    provider: QuotaLimits | None = None,
    global_: QuotaLimits | None = None,
    retry: RetryPolicy | None = None,
) -> ProviderQuotaPhysics:
    return ProviderQuotaPhysics(
        provider_id="fixture",
        provider_limits=provider or QuotaLimits(rpm=10, tpm=500, concurrency=3, window_ms=100),
        global_limits=global_ or QuotaLimits(rpm=10, tpm=500, concurrency=3, window_ms=100),
        clock=clock or FakeIntegerClock(),
        retry_policy=retry or RetryPolicy(max_attempts=3, base_backoff_ms=10, max_backoff_ms=40),
    )


def _lease(decision):
    assert decision.admitted
    assert decision.lease is not None
    return decision.lease


def _change_detail(event: QuotaEvent, key: str, value: object) -> QuotaEvent:
    details = dict(event.details)
    details[key] = value
    return replace(event, details=tuple(sorted(details.items())))  # type: ignore[arg-type]


def test_provider_and_global_admission_is_atomic_until_fixed_refill() -> None:
    clock = FakeIntegerClock()
    physics = _physics(
        clock=clock,
        provider=QuotaLimits(rpm=3, tpm=100, concurrency=3, window_ms=100),
        global_=QuotaLimits(rpm=2, tpm=80, concurrency=3, window_ms=100),
    )
    first = _lease(physics.acquire(CallRequest("a", 0, 30)))
    second = _lease(physics.acquire(CallRequest("b", 0, 40)))
    before = physics.snapshot()

    refusal = physics.acquire(CallRequest("c", 0, 5))
    after = physics.snapshot()

    assert not refusal.admitted
    assert refusal.reasons == (RefusalReason.GLOBAL_RPM,)
    assert after == before
    physics.complete(first.lease_id, actual_tokens=20)
    physics.complete(second.lease_id, actual_tokens=25)
    still_bound = physics.acquire(CallRequest("c", 0, 5))
    assert still_bound.reasons == (RefusalReason.GLOBAL_RPM,)

    clock.advance(99)
    assert physics.acquire(CallRequest("c", 0, 5)).reasons == (RefusalReason.GLOBAL_RPM,)
    clock.advance(1)
    assert physics.acquire(CallRequest("c", 0, 5)).admitted


def test_concurrency_lease_and_token_settlement_refund_only_unused_tokens() -> None:
    physics = _physics(
        provider=QuotaLimits(rpm=10, tpm=100, concurrency=1, window_ms=1_000),
        global_=QuotaLimits(rpm=10, tpm=100, concurrency=2, window_ms=1_000),
    )
    first = _lease(physics.acquire(CallRequest("a", 0, 60)))
    held = physics.acquire(CallRequest("b", 0, 30))
    assert held.reasons == (RefusalReason.PROVIDER_CONCURRENCY,)
    assert physics.snapshot().provider_tpm_remaining == 40

    physics.complete(first.lease_id, actual_tokens=20)
    assert physics.snapshot().provider_tpm_remaining == 80
    second = _lease(physics.acquire(CallRequest("b", 0, 50)))
    assert second.token_cap == 50
    assert physics.snapshot().provider_tpm_remaining == 30
    physics.complete(second.lease_id, actual_tokens=50)

    report = replay_quota_events(physics.events)
    assert report.maximum_provider_active == 1
    assert report.actual_tokens_settled == 70
    assert report.open_leases == 0


def test_late_settlement_never_refunds_an_old_reservation_into_a_new_window() -> None:
    clock = FakeIntegerClock()
    physics = _physics(
        clock=clock,
        provider=QuotaLimits(rpm=10, tpm=100, concurrency=3, window_ms=1_000),
        global_=QuotaLimits(rpm=10, tpm=100, concurrency=3, window_ms=1_000),
    )
    old_window = _lease(physics.acquire(CallRequest("old-window", 0, 100)))

    clock.advance_to(1_000)
    current_window = _lease(physics.acquire(CallRequest("current-window", 0, 100)))
    assert physics.snapshot().provider_tpm_remaining == 0

    physics.complete(old_window.lease_id, actual_tokens=0)
    after_late_settlement = physics.snapshot()
    assert after_late_settlement.provider_tpm_remaining == 0
    assert after_late_settlement.global_tpm_remaining == 0
    settlement = dict(physics.events[-1].details)
    assert settlement["unused_tokens"] == 100
    assert settlement["provider_refunded_tokens"] == 0
    assert settlement["global_refunded_tokens"] == 0

    extra = physics.acquire(CallRequest("must-not-fit", 0, 100))
    assert extra.reasons == (RefusalReason.PROVIDER_TPM, RefusalReason.GLOBAL_TPM)
    physics.complete(current_window.lease_id, actual_tokens=100)
    assert replay_quota_events(physics.events).valid


def test_provider_and_instance_aggregate_windows_refund_independently() -> None:
    clock = FakeIntegerClock()
    physics = _physics(
        clock=clock,
        provider=QuotaLimits(rpm=10, tpm=100, concurrency=2, window_ms=100),
        global_=QuotaLimits(rpm=10, tpm=100, concurrency=2, window_ms=200),
    )
    lease = _lease(physics.acquire(CallRequest("mixed-windows", 0, 60)))
    clock.advance_to(100)
    physics.complete(lease.lease_id, actual_tokens=20)
    snapshot = physics.snapshot()
    assert snapshot.provider_tpm_remaining == 100
    assert snapshot.global_tpm_remaining == 80
    settlement = dict(physics.events[-1].details)
    assert settlement["provider_refunded_tokens"] == 0
    assert settlement["global_refunded_tokens"] == 40
    assert replay_quota_events(physics.events).valid


def test_429_reset_suppresses_retries_without_consuming_quota() -> None:
    clock = FakeIntegerClock()
    physics = _physics(clock=clock)
    initial = _lease(physics.acquire(CallRequest("storm", 0, 100, deadline_ms=500)))
    directive = physics.provider_429(initial.lease_id, actual_tokens=0, reset_after_ms=100)
    assert directive.retry_at_ms == 100
    assert directive.reset_at_ms == 100
    after_429 = physics.snapshot()
    retry = CallRequest(
        "storm",
        1,
        100,
        previous_attempt_id=initial.attempt_id,
        deadline_ms=500,
    )

    immediate = physics.acquire(retry)
    clock.advance(99)
    almost = physics.acquire(retry)
    assert immediate.reasons == almost.reasons == (RefusalReason.PROVIDER_RESET,)
    assert immediate.retry_at_ms == almost.retry_at_ms == 100
    assert physics.snapshot().provider_rpm_remaining == after_429.provider_rpm_remaining
    assert physics.snapshot().provider_tpm_remaining == after_429.provider_tpm_remaining

    clock.advance(1)
    admitted_retry = _lease(physics.acquire(retry))
    physics.complete(admitted_retry.lease_id, actual_tokens=70)
    report = replay_quota_events(physics.events)
    assert report.admitted_calls == 2
    assert report.reset_suppressed_retries == 2
    assert report.settled_calls == 2

    retry_at_tamper = list(physics.events)
    refusal_index = next(
        index
        for index, event in enumerate(retry_at_tamper)
        if event.kind == "quota.admission_refused"
    )
    retry_at_tamper[refusal_index] = _change_detail(
        retry_at_tamper[refusal_index], "retry_at_ms", 101
    )
    with pytest.raises(QuotaReplayError, match="retry timestamp is not entailed"):
        replay_quota_events(retry_at_tamper)


def test_429_window_blocks_new_calls_and_can_only_extend() -> None:
    clock = FakeIntegerClock()
    physics = _physics(clock=clock)
    first = _lease(physics.acquire(CallRequest("a", 0, 10)))
    second = _lease(physics.acquire(CallRequest("b", 0, 10)))
    physics.provider_429(first.lease_id, reset_at_ms=100)
    clock.advance(10)
    physics.provider_429(second.lease_id, reset_after_ms=200)
    assert physics.snapshot().provider_reset_until_ms == 210
    refusal = physics.acquire(CallRequest("new", 0, 10))
    assert refusal.reasons == (RefusalReason.PROVIDER_RESET,)
    assert refusal.retry_at_ms == 210
    assert replay_quota_events(physics.events).valid


def test_retry_backoff_attempt_bound_and_original_deadline() -> None:
    clock = FakeIntegerClock()
    physics = _physics(
        clock=clock,
        retry=RetryPolicy(max_attempts=3, base_backoff_ms=10, max_backoff_ms=20),
    )
    first = _lease(physics.acquire(CallRequest("retry", 0, 20, deadline_ms=100)))
    one = physics.fail_retryable(first.lease_id, actual_tokens=5)
    request_one = CallRequest("retry", 1, 20, first.attempt_id, 100)
    too_early = physics.acquire(request_one)
    assert too_early.reasons == (RefusalReason.RETRY_BACKOFF,)
    assert too_early.retry_at_ms == one.retry_at_ms == 10

    clock.advance_to(10)
    second = _lease(physics.acquire(request_one))
    two = physics.fail_retryable(second.lease_id, actual_tokens=5)
    assert two.retry_at_ms == 30
    clock.advance_to(30)
    third = _lease(
        physics.acquire(CallRequest("retry", 2, 20, second.attempt_id, deadline_ms=100))
    )
    exhausted = physics.fail_retryable(third.lease_id, actual_tokens=5)
    assert exhausted.retry_at_ms is None
    over_bound = physics.acquire(CallRequest("retry", 3, 20, third.attempt_id, 100))
    assert over_bound.reasons == (RefusalReason.RETRY_EXHAUSTED,)

    deadline_guard = _physics(
        clock=FakeIntegerClock(),
        retry=RetryPolicy(max_attempts=3, base_backoff_ms=60, max_backoff_ms=60),
    )
    expiring = _lease(deadline_guard.acquire(CallRequest("deadline", 0, 10, deadline_ms=50)))
    no_retry = deadline_guard.fail_retryable(expiring.lease_id, actual_tokens=1)
    assert no_retry.retry_at_ms is None


def test_retry_identity_cannot_skip_or_extend_deadline() -> None:
    physics = _physics()
    initial = _lease(physics.acquire(CallRequest("same", 0, 20, deadline_ms=100)))
    physics.fail_retryable(initial.lease_id, actual_tokens=1)
    wrong_predecessor = physics.acquire(CallRequest("same", 1, 20, "wrong", 100))
    skipped = physics.acquire(CallRequest("same", 2, 20, initial.attempt_id, 100))
    extended = physics.acquire(CallRequest("same", 1, 20, initial.attempt_id, 101))
    assert wrong_predecessor.reasons == (RefusalReason.IDENTITY,)
    assert skipped.reasons == (RefusalReason.IDENTITY,)
    assert extended.reasons == (RefusalReason.IDENTITY,)


def test_over_cap_settlement_is_refused_without_releasing_lease() -> None:
    physics = _physics()
    lease = _lease(physics.acquire(CallRequest("cap", 0, 40)))
    before = physics.snapshot()
    with pytest.raises(QuotaSettlementError, match="token cap"):
        physics.complete(lease.lease_id, actual_tokens=41)
    assert physics.snapshot() == before
    assert physics.events[-1].kind == "quota.settlement_refused"
    refused_events = physics.events
    assert replay_quota_events(refused_events).open_leases == 1
    reason_tamper = list(refused_events)
    reason_tamper[-1] = _change_detail(reason_tamper[-1], "reason", "looks_plausible")
    with pytest.raises(QuotaReplayError, match="not entailed"):
        replay_quota_events(reason_tamper)
    physics.complete(lease.lease_id, actual_tokens=39)
    report = replay_quota_events(physics.events)
    assert report.actual_tokens_settled == 39
    assert report.open_leases == 0


def test_replay_rejects_identity_and_token_settlement_tampering() -> None:
    physics = _physics()
    lease = _lease(physics.acquire(CallRequest("evidence", 0, 50)))
    physics.complete(lease.lease_id, actual_tokens=30)
    original = physics.events
    assert replay_quota_events(original).digest == physics.digest

    identity_tamper = list(original)
    identity_tamper[1] = _change_detail(identity_tamper[1], "attempt_id", "forged")
    with pytest.raises(QuotaReplayError, match="canonical identity"):
        replay_quota_events(identity_tamper)

    token_tamper = list(original)
    token_tamper[2] = _change_detail(token_tamper[2], "actual_tokens", 51)
    with pytest.raises(QuotaReplayError, match="token settlement"):
        replay_quota_events(token_tamper)

    snapshot_tamper = list(original)
    snapshot_tamper[1] = _change_detail(snapshot_tamper[1], "provider_rpm_remaining", 99)
    with pytest.raises(QuotaReplayError, match="snapshot"):
        replay_quota_events(snapshot_tamper)

    refund_tamper = list(original)
    refund_tamper[2] = _change_detail(refund_tamper[2], "provider_refunded_tokens", 21)
    with pytest.raises(QuotaReplayError, match="reservation-window scoped"):
        replay_quota_events(refund_tamper)


def test_replay_requires_refusal_reasons_entailed_by_reconstructed_state() -> None:
    physics = _physics(
        provider=QuotaLimits(rpm=10, tpm=500, concurrency=3, window_ms=100),
        global_=QuotaLimits(rpm=1, tpm=500, concurrency=3, window_ms=100),
    )
    first = _lease(physics.acquire(CallRequest("first", 0, 10)))
    refusal = physics.acquire(CallRequest("second", 0, 10))
    assert refusal.reasons == (RefusalReason.GLOBAL_RPM,)
    original = physics.events
    assert replay_quota_events(original).valid

    false_reason = list(original)
    false_reason[-1] = _change_detail(false_reason[-1], "reasons", "provider_tpm")
    with pytest.raises(QuotaReplayError, match="not entailed"):
        replay_quota_events(false_reason)

    extra_reason = list(original)
    extra_reason[-1] = _change_detail(
        extra_reason[-1],
        "reasons",
        "global_rpm|provider_concurrency",
    )
    with pytest.raises(QuotaReplayError, match="not entailed"):
        replay_quota_events(extra_reason)

    extra_field = list(original)
    details = dict(extra_field[-1].details)
    details["judge_claim"] = "false"
    extra_field[-1] = replace(
        extra_field[-1],
        details=tuple(sorted(details.items())),  # type: ignore[arg-type]
    )
    with pytest.raises(QuotaReplayError, match="fields are not canonical"):
        replay_quota_events(extra_field)
    physics.complete(first.lease_id, actual_tokens=10)


def test_quota_event_rejects_duplicate_keys_and_digest_is_lossless() -> None:
    physics = _physics()
    original = physics.events[0]
    duplicate_details = tuple(
        sorted(original.details + (("provider_id", "shadow-provider"),), key=lambda pair: pair[0])
    )
    with pytest.raises(ValueError, match="sorted and unique"):
        QuotaEvent(
            sequence=original.sequence,
            at_ms=original.at_ms,
            kind=original.kind,
            details=duplicate_details,
        )

    # Simulate hostile deserialization that bypassed the frozen dataclass constructor.
    malformed = object.__new__(QuotaEvent)
    object.__setattr__(malformed, "sequence", original.sequence)
    object.__setattr__(malformed, "at_ms", original.at_ms)
    object.__setattr__(malformed, "kind", original.kind)
    object.__setattr__(malformed, "details", duplicate_details)
    physics._events[0] = malformed  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="sorted and unique"):
        _ = physics.digest
    with pytest.raises(QuotaReplayError, match="sorted and unique"):
        replay_quota_events(physics.events)


def test_replay_rejects_event_and_nested_container_subclasses_without_iteration() -> None:
    physics = _physics()
    original = physics.events[0]

    class DerivedQuotaEvent(QuotaEvent):
        pass

    derived = DerivedQuotaEvent(
        original.sequence,
        original.at_ms,
        original.kind,
        original.details,
    )
    with pytest.raises(QuotaReplayError, match="exact QuotaEvent type"):
        replay_quota_events((derived,))
    with pytest.raises(ValueError, match="exact QuotaEvent type"):
        derived.as_dict()

    class StatefulTuple(tuple):
        iterations = 0

        def __iter__(self):
            type(self).iterations += 1
            return tuple.__iter__(self)

    stateful_details = StatefulTuple(original.details)
    with pytest.raises(ValueError, match="exact tuple"):
        QuotaEvent(original.sequence, original.at_ms, original.kind, stateful_details)
    assert StatefulTuple.iterations == 0

    hostile_details_event = object.__new__(QuotaEvent)
    object.__setattr__(hostile_details_event, "sequence", original.sequence)
    object.__setattr__(hostile_details_event, "at_ms", original.at_ms)
    object.__setattr__(hostile_details_event, "kind", original.kind)
    object.__setattr__(hostile_details_event, "details", stateful_details)
    with pytest.raises(QuotaReplayError, match="exact tuple"):
        replay_quota_events((hostile_details_event,))
    assert StatefulTuple.iterations == 0

    StatefulTuple.iterations = 0
    stateful_pair = StatefulTuple(original.details[0])
    pair_subclass_details = (stateful_pair,) + original.details[1:]
    with pytest.raises(ValueError, match="exact key-value tuples"):
        QuotaEvent(original.sequence, original.at_ms, original.kind, pair_subclass_details)
    assert StatefulTuple.iterations == 0


def test_event_boundary_rejects_scalar_subclasses() -> None:
    original = _physics().events[0]

    class DerivedString(str):
        pass

    class DerivedInteger(int):
        pass

    bad_key = ((DerivedString("base_backoff_ms"), 100),) + original.details[1:]
    with pytest.raises(ValueError, match="exact non-empty strings"):
        QuotaEvent(original.sequence, original.at_ms, original.kind, bad_key)

    bad_value = (("base_backoff_ms", DerivedInteger(100)),) + original.details[1:]
    with pytest.raises(ValueError, match="exact scalar types"):
        QuotaEvent(original.sequence, original.at_ms, original.kind, bad_value)
    with pytest.raises(ValueError, match="sequence"):
        QuotaEvent(DerivedInteger(0), original.at_ms, original.kind, original.details)


def test_replay_snapshots_each_event_before_advancing_the_input_iterator() -> None:
    original = _physics().events[0]
    source = QuotaEvent(
        original.sequence,
        original.at_ms,
        original.kind,
        original.details,
    )
    baseline = replay_quota_events((source,))

    def mutate_after_yield():
        yield source
        changed = tuple(
            (key, "changed-after-yield") if key == "provider_id" else (key, value)
            for key, value in source.details
        )
        object.__setattr__(source, "details", changed)

    replayed = replay_quota_events(mutate_after_yield())
    assert replayed.digest == baseline.digest
    assert dict(source.details)["provider_id"] == "changed-after-yield"


def test_legacy_global_names_are_explicitly_scoped_to_one_instance() -> None:
    limits = QuotaLimits(rpm=10, tpm=100, concurrency=1, window_ms=100)
    first = _physics(global_=limits)
    second = _physics(global_=limits)
    first_lease = _lease(first.acquire(CallRequest("one", 0, 10)))
    second_lease = _lease(second.acquire(CallRequest("two", 0, 10)))

    assert dict(first.events[0].details)["global_guard_scope"] == GLOBAL_GUARD_SCOPE
    assert GLOBAL_GUARD_SCOPE == "per_instance_only_not_process_global_or_distributed"
    assert dict(first.events[0].details)["settlement_refund_policy"] == (
        SETTLEMENT_REFUND_POLICY
    )
    first.complete(first_lease.lease_id, actual_tokens=10)
    second.complete(second_lease.lease_id, actual_tokens=10)


def test_seeded_burst_corpus_is_stable_replayable_and_fully_settled() -> None:
    first = run_seeded_burst_corpus(seed=13, cycles=48)
    second = run_seeded_burst_corpus(seed=13, cycles=48)
    assert first == second
    assert first.logical_calls == 1_200
    assert first.admission_requests == 1_272
    assert first.admitted_calls == first.settled_calls == 384
    assert first.refused_admissions == 888
    assert first.reset_suppressed_retries == 18
    assert first.maximum_provider_active == first.maximum_global_active == 3
    assert first.actual_tokens_settled == 10_406
    assert first.event_count == 1_657
    assert first.digest == "140131324897e81945426218ebf4568afb35c22717adc0b7ca1ea2cffc015260"
    assert run_seeded_burst_corpus(seed=14, cycles=48).digest != first.digest


def test_configuration_clock_and_scope_are_explicit() -> None:
    with pytest.raises(QuotaConfigurationError):
        QuotaLimits(rpm=0, tpm=1, concurrency=1)
    with pytest.raises(QuotaConfigurationError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(QuotaConfigurationError):
        RetryPolicy(base_backoff_ms=2, max_backoff_ms=1)
    clock = FakeIntegerClock(5)
    with pytest.raises(ValueError, match="backwards"):
        clock.advance_to(4)
    physics = _physics(clock=clock)
    assert dict(physics.events[0].details)["model_scope"] == MODEL_SCOPE
    assert MODEL_SCOPE.endswith("not_provider_measurement")


def test_request_and_429_contract_validation() -> None:
    with pytest.raises(ValueError):
        CallRequest("", 0, 1)
    with pytest.raises(ValueError):
        CallRequest("x", 1, 1)
    with pytest.raises(ValueError):
        CallRequest("x", 0, 1, previous_attempt_id="impossible")
    physics = _physics()
    lease = _lease(physics.acquire(CallRequest("x", 0, 10)))
    with pytest.raises(ValueError, match="exactly one"):
        physics.provider_429(lease.lease_id)
    with pytest.raises(ValueError, match="positive"):
        physics.provider_429(lease.lease_id, reset_after_ms=0)
    with pytest.raises(ValueError, match="later"):
        physics.provider_429(lease.lease_id, reset_at_ms=0)
    physics.complete(lease.lease_id, actual_tokens=0)
    with pytest.raises(QuotaSettlementError, match="not active"):
        physics.complete(lease.lease_id, actual_tokens=0)
