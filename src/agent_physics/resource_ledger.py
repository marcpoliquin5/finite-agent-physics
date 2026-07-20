"""Deterministic, fail-closed accounting for local run resource budgets.

The ledger models integer reservations for tokens, cost in micro-USD, and
context bytes.  It is deliberately a local accounting primitive: it cannot
stop a remote provider that ignores a request limit or reports usage late.
Provider adapters must enforce their own limits and reconcile trusted usage
back into this ledger.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable


DEFAULT_STRESS_SEED = 20_260_731
DEFAULT_STRESS_TRANSITIONS = 10_000
EVENT_ID_PREFIX = "resource-event"


@dataclass(frozen=True, slots=True)
class ResourceVector:
    """A non-negative integer vector in the ledger's three resource units."""

    tokens: int
    cost_microusd: int
    context_bytes: int

    def __post_init__(self) -> None:
        for name, value in (
            ("tokens", self.tokens),
            ("cost_microusd", self.cost_microusd),
            ("context_bytes", self.context_bytes),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @classmethod
    def zero(cls) -> ResourceVector:
        return cls(0, 0, 0)

    def add(self, other: ResourceVector) -> ResourceVector:
        return ResourceVector(
            self.tokens + other.tokens,
            self.cost_microusd + other.cost_microusd,
            self.context_bytes + other.context_bytes,
        )

    def subtract(self, other: ResourceVector) -> ResourceVector:
        if not other.fits_within(self):
            raise ValueError("resource subtraction would produce a negative balance")
        return ResourceVector(
            self.tokens - other.tokens,
            self.cost_microusd - other.cost_microusd,
            self.context_bytes - other.context_bytes,
        )

    def fits_within(self, limit: ResourceVector) -> bool:
        return (
            self.tokens <= limit.tokens
            and self.cost_microusd <= limit.cost_microusd
            and self.context_bytes <= limit.context_bytes
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "tokens": self.tokens,
            "cost_microusd": self.cost_microusd,
            "context_bytes": self.context_bytes,
        }


def _sum_resources(resources: Iterable[ResourceVector]) -> ResourceVector:
    tokens = 0
    cost_microusd = 0
    context_bytes = 0
    for resource in resources:
        tokens += resource.tokens
        cost_microusd += resource.cost_microusd
        context_bytes += resource.context_bytes
    return ResourceVector(tokens, cost_microusd, context_bytes)


class LedgerOperation(str, Enum):
    RESERVE = "reserve"
    SETTLE = "settle"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    capacity: ResourceVector
    spent: ResourceVector
    held: ResourceVector
    available: ResourceVector
    active_attempts: int
    terminal_attempts: int
    last_sequence: int

    def as_dict(self) -> dict[str, object]:
        return {
            "capacity": self.capacity.as_dict(),
            "spent": self.spent.as_dict(),
            "held": self.held.as_dict(),
            "available": self.available.as_dict(),
            "active_attempts": self.active_attempts,
            "terminal_attempts": self.terminal_attempts,
            "last_sequence": self.last_sequence,
        }


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One append-only policy decision with its post-decision accounting state."""

    sequence: int
    event_id: str
    operation: LedgerOperation
    attempt_id: str
    reservation: ResourceVector | None
    actual: ResourceVector | None
    applied: bool
    reason: str | None
    post_snapshot: BudgetSnapshot

    def as_dict(self) -> dict[str, object]:
        operation = (
            self.operation.value
            if isinstance(self.operation, LedgerOperation)
            else str(self.operation)
        )
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "operation": operation,
            "attempt_id": self.attempt_id,
            "reservation": self.reservation.as_dict() if self.reservation else None,
            "actual": self.actual.as_dict() if self.actual else None,
            "applied": self.applied,
            "reason": self.reason,
            "post_snapshot": self.post_snapshot.as_dict(),
        }


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def event_log_digest(events: Iterable[LedgerEvent]) -> str:
    """Return a stable SHA-256 digest over the canonical event representation."""

    return _canonical_digest([event.as_dict() for event in events])


class ResourceBudgetLedger:
    """Reserve, settle, or refund a finite local resource envelope.

    Settlements can never exceed the attempt's reservation.  Such a request is
    recorded as refused and leaves the full reservation held, so callers must
    explicitly cancel or submit a valid settlement before the budget is freed.
    """

    def __init__(self, capacity: ResourceVector) -> None:
        self._capacity = capacity
        self._spent = ResourceVector.zero()
        self._active: dict[str, ResourceVector] = {}
        self._terminal: dict[str, str] = {}
        self._events: list[LedgerEvent] = []

    @property
    def capacity(self) -> ResourceVector:
        return self._capacity

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    @property
    def active_attempt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    @property
    def known_attempt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._active) | set(self._terminal)))

    def reservation_for(self, attempt_id: str) -> ResourceVector | None:
        return self._active.get(attempt_id)

    def snapshot(self) -> BudgetSnapshot:
        return self._snapshot(len(self._events))

    def reserve(self, attempt_id: str, requested: ResourceVector) -> LedgerEvent:
        self._validate_attempt_id(attempt_id)
        if attempt_id in self._active or attempt_id in self._terminal:
            return self._record(
                LedgerOperation.RESERVE,
                attempt_id,
                requested,
                None,
                applied=False,
                reason="identity_conflict",
            )

        if not requested.fits_within(self.snapshot().available):
            self._terminal[attempt_id] = "reservation_refused"
            return self._record(
                LedgerOperation.RESERVE,
                attempt_id,
                requested,
                None,
                applied=False,
                reason="capacity_exceeded",
            )

        self._active[attempt_id] = requested
        return self._record(
            LedgerOperation.RESERVE,
            attempt_id,
            requested,
            None,
            applied=True,
            reason=None,
        )

    def settle(self, attempt_id: str, actual: ResourceVector) -> LedgerEvent:
        self._validate_attempt_id(attempt_id)
        reservation = self._active.get(attempt_id)
        if reservation is None:
            reason = "terminal_attempt" if attempt_id in self._terminal else "unknown_attempt"
            return self._record(
                LedgerOperation.SETTLE,
                attempt_id,
                None,
                actual,
                applied=False,
                reason=reason,
            )

        if not actual.fits_within(reservation):
            return self._record(
                LedgerOperation.SETTLE,
                attempt_id,
                reservation,
                actual,
                applied=False,
                reason="actual_over_reservation",
            )

        self._spent = self._spent.add(actual)
        del self._active[attempt_id]
        self._terminal[attempt_id] = "settled"
        return self._record(
            LedgerOperation.SETTLE,
            attempt_id,
            reservation,
            actual,
            applied=True,
            reason=None,
        )

    def cancel(self, attempt_id: str) -> LedgerEvent:
        self._validate_attempt_id(attempt_id)
        reservation = self._active.get(attempt_id)
        if reservation is None:
            reason = "terminal_attempt" if attempt_id in self._terminal else "unknown_attempt"
            return self._record(
                LedgerOperation.CANCEL,
                attempt_id,
                None,
                None,
                applied=False,
                reason=reason,
            )

        del self._active[attempt_id]
        self._terminal[attempt_id] = "cancelled"
        return self._record(
            LedgerOperation.CANCEL,
            attempt_id,
            reservation,
            None,
            applied=True,
            reason=None,
        )

    @staticmethod
    def _validate_attempt_id(attempt_id: str) -> None:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty string")

    def _snapshot(self, last_sequence: int) -> BudgetSnapshot:
        held = _sum_resources(self._active.values())
        committed = self._spent.add(held)
        if not committed.fits_within(self._capacity):
            raise AssertionError("internal ledger conservation failure")
        return BudgetSnapshot(
            capacity=self._capacity,
            spent=self._spent,
            held=held,
            available=self._capacity.subtract(committed),
            active_attempts=len(self._active),
            terminal_attempts=len(self._terminal),
            last_sequence=last_sequence,
        )

    def _record(
        self,
        operation: LedgerOperation,
        attempt_id: str,
        reservation: ResourceVector | None,
        actual: ResourceVector | None,
        *,
        applied: bool,
        reason: str | None,
    ) -> LedgerEvent:
        sequence = len(self._events) + 1
        event = LedgerEvent(
            sequence=sequence,
            event_id=f"{EVENT_ID_PREFIX}-{sequence:012d}",
            operation=operation,
            attempt_id=attempt_id,
            reservation=reservation,
            actual=actual,
            applied=applied,
            reason=reason,
            post_snapshot=self._snapshot(sequence),
        )
        self._events.append(event)
        return event


@dataclass(frozen=True, slots=True)
class VerificationFailure:
    sequence: int | None
    code: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"sequence": self.sequence, "code": self.code, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class FailureCorpus:
    """Portable deterministic evidence for reproducing verification failures."""

    seed: int | None
    trace_digest: str
    failures: tuple[VerificationFailure, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "finite-resource-ledger-failures/v1",
            "seed": self.seed,
            "trace_digest": self.trace_digest,
            "failures": [failure.as_dict() for failure in self.failures],
        }

    @property
    def digest(self) -> str:
        return _canonical_digest(self.as_dict())


@dataclass(frozen=True, slots=True)
class ReplayReport:
    passed: bool
    seed: int | None
    event_count: int
    trace_digest: str
    replayed_snapshot: BudgetSnapshot
    failures: tuple[VerificationFailure, ...]

    def failure_corpus(self) -> FailureCorpus:
        return FailureCorpus(self.seed, self.trace_digest, self.failures)

    @property
    def failure_digest(self) -> str:
        return self.failure_corpus().digest


def _snapshot_from_replay(
    capacity: ResourceVector,
    spent: ResourceVector,
    active: dict[str, ResourceVector],
    terminal: dict[str, str],
    last_sequence: int,
) -> BudgetSnapshot:
    held = _sum_resources(active.values())
    committed = spent.add(held)
    if not committed.fits_within(capacity):
        # The verifier records this separately; clamp only to keep the report serializable.
        available = ResourceVector.zero()
    else:
        available = capacity.subtract(committed)
    return BudgetSnapshot(
        capacity=capacity,
        spent=spent,
        held=held,
        available=available,
        active_attempts=len(active),
        terminal_attempts=len(terminal),
        last_sequence=last_sequence,
    )


def replay_and_verify(
    capacity: ResourceVector,
    events: Iterable[LedgerEvent],
    *,
    claimed_snapshot: BudgetSnapshot | None = None,
    seed: int | None = None,
) -> ReplayReport:
    """Independently replay events and verify identity, policy, and conservation.

    The replay derives outcomes from operation inputs instead of trusting each
    event's ``applied`` flag, refusal reason, or post-state snapshot.
    """

    event_tuple = tuple(events)
    failures: list[VerificationFailure] = []
    spent = ResourceVector.zero()
    active: dict[str, ResourceVector] = {}
    terminal: dict[str, str] = {}
    seen_event_ids: set[str] = set()

    def fail(sequence: int | None, code: str, detail: str) -> None:
        failures.append(VerificationFailure(sequence, code, detail))

    for position, event in enumerate(event_tuple, start=1):
        if event.sequence != position:
            fail(event.sequence, "non_monotonic_sequence", f"expected={position}")
        expected_event_id = f"{EVENT_ID_PREFIX}-{position:012d}"
        if event.event_id != expected_event_id:
            fail(event.sequence, "invalid_event_id", f"expected={expected_event_id}")
        if event.event_id in seen_event_ids:
            fail(event.sequence, "duplicate_event_id", event.event_id)
        seen_event_ids.add(event.event_id)

        reservation_before = active.get(event.attempt_id)
        expected_applied = False
        expected_reason: str | None = None

        if event.operation is LedgerOperation.RESERVE:
            if event.actual is not None or event.reservation is None:
                expected_reason = "malformed_input"
            elif event.attempt_id in active or event.attempt_id in terminal:
                expected_reason = "identity_conflict"
            else:
                snapshot_before = _snapshot_from_replay(
                    capacity, spent, active, terminal, position - 1
                )
                if not event.reservation.fits_within(snapshot_before.available):
                    expected_reason = "capacity_exceeded"
                    terminal[event.attempt_id] = "reservation_refused"
                else:
                    expected_applied = True
                    active[event.attempt_id] = event.reservation
        elif event.operation is LedgerOperation.SETTLE:
            if event.actual is None:
                expected_reason = "malformed_input"
            elif reservation_before is None:
                expected_reason = (
                    "terminal_attempt" if event.attempt_id in terminal else "unknown_attempt"
                )
            elif not event.actual.fits_within(reservation_before):
                expected_reason = "actual_over_reservation"
            else:
                expected_applied = True
                spent = spent.add(event.actual)
                del active[event.attempt_id]
                terminal[event.attempt_id] = "settled"
        elif event.operation is LedgerOperation.CANCEL:
            if event.actual is not None:
                expected_reason = "malformed_input"
            elif reservation_before is None:
                expected_reason = (
                    "terminal_attempt" if event.attempt_id in terminal else "unknown_attempt"
                )
            else:
                expected_applied = True
                del active[event.attempt_id]
                terminal[event.attempt_id] = "cancelled"
        else:
            expected_reason = "unknown_operation"

        expected_reservation = (
            event.reservation
            if event.operation is LedgerOperation.RESERVE
            else reservation_before
        )
        if event.reservation != expected_reservation:
            fail(
                event.sequence,
                "reservation_mismatch",
                f"expected={expected_reservation!r}",
            )
        if event.applied != expected_applied or event.reason != expected_reason:
            fail(
                event.sequence,
                "decision_mismatch",
                f"expected_applied={expected_applied}, expected_reason={expected_reason}",
            )

        replayed = _snapshot_from_replay(capacity, spent, active, terminal, position)
        if event.post_snapshot != replayed:
            fail(event.sequence, "post_snapshot_mismatch", "event post-state is not replayable")

        committed = replayed.spent.add(replayed.held)
        if not committed.fits_within(capacity):
            fail(event.sequence, "cap_breach", f"committed={committed.as_dict()}")
        expected_available = capacity.subtract(committed)
        if replayed.available != expected_available:
            fail(event.sequence, "negative_or_hidden_balance", "available balance mismatch")

    final_snapshot = _snapshot_from_replay(
        capacity,
        spent,
        active,
        terminal,
        len(event_tuple),
    )
    if claimed_snapshot is not None and claimed_snapshot != final_snapshot:
        fail(None, "claimed_snapshot_mismatch", "claimed final state differs from replay")

    digest = event_log_digest(event_tuple)
    return ReplayReport(
        passed=not failures,
        seed=seed,
        event_count=len(event_tuple),
        trace_digest=digest,
        replayed_snapshot=final_snapshot,
        failures=tuple(failures),
    )


@dataclass(frozen=True, slots=True)
class DeterministicStressCorpus:
    seed: int
    transition_count: int
    capacity: ResourceVector
    events: tuple[LedgerEvent, ...]
    final_snapshot: BudgetSnapshot
    peak_active_attempts: int
    trace_digest: str

    @property
    def operation_counts(self) -> dict[str, int]:
        return dict(Counter(event.operation.value for event in self.events))

    @property
    def refusal_counts(self) -> dict[str, int]:
        return dict(Counter(event.reason for event in self.events if event.reason is not None))

    def verify(self) -> ReplayReport:
        return replay_and_verify(
            self.capacity,
            self.events,
            claimed_snapshot=self.final_snapshot,
            seed=self.seed,
        )


def generate_stress_corpus(
    *,
    seed: int = DEFAULT_STRESS_SEED,
    transitions: int = DEFAULT_STRESS_TRANSITIONS,
) -> DeterministicStressCorpus:
    """Generate an exact-length seeded workload with logically concurrent attempts."""

    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if type(transitions) is not int or transitions <= 0:
        raise ValueError("transitions must be a positive integer")

    rng = random.Random(seed)
    capacity = ResourceVector(20_000_000, 40_000_000, 80_000_000)
    ledger = ResourceBudgetLedger(capacity)
    attempt_counter = 0
    peak_active = 0

    def next_attempt_id() -> str:
        nonlocal attempt_counter
        attempt_counter += 1
        return f"attempt-{attempt_counter:08d}"

    def random_reservation() -> ResourceVector:
        return ResourceVector(
            rng.randint(1, 4_000),
            rng.randint(1, 8_000),
            rng.randint(1, 16_000),
        )

    for transition in range(1, transitions + 1):
        active_ids = ledger.active_attempt_ids

        if transition % 113 == 0 and active_ids:
            attempt_id = rng.choice(active_ids)
            reservation = ledger.reservation_for(attempt_id)
            assert reservation is not None
            ledger.settle(attempt_id, replace(reservation, tokens=reservation.tokens + 1))
        elif transition % 127 == 0:
            available = ledger.snapshot().available
            overflow = ResourceVector(available.tokens + 1, 0, 0)
            ledger.reserve(next_attempt_id(), overflow)
        elif transition % 211 == 0 and ledger.known_attempt_ids:
            conflicting_id = rng.choice(ledger.known_attempt_ids)
            ledger.reserve(conflicting_id, random_reservation())
        elif len(active_ids) < 32:
            ledger.reserve(next_attempt_id(), random_reservation())
        elif len(active_ids) > 96:
            attempt_id = rng.choice(active_ids)
            if rng.random() < 0.65:
                reservation = ledger.reservation_for(attempt_id)
                assert reservation is not None
                ledger.settle(
                    attempt_id,
                    ResourceVector(
                        rng.randint(0, reservation.tokens),
                        rng.randint(0, reservation.cost_microusd),
                        rng.randint(0, reservation.context_bytes),
                    ),
                )
            else:
                ledger.cancel(attempt_id)
        elif rng.random() < 0.51:
            ledger.reserve(next_attempt_id(), random_reservation())
        else:
            attempt_id = rng.choice(active_ids)
            if rng.random() < 0.68:
                reservation = ledger.reservation_for(attempt_id)
                assert reservation is not None
                ledger.settle(
                    attempt_id,
                    ResourceVector(
                        rng.randint(0, reservation.tokens),
                        rng.randint(0, reservation.cost_microusd),
                        rng.randint(0, reservation.context_bytes),
                    ),
                )
            else:
                ledger.cancel(attempt_id)

        peak_active = max(peak_active, ledger.snapshot().active_attempts)

    events = ledger.events
    return DeterministicStressCorpus(
        seed=seed,
        transition_count=transitions,
        capacity=capacity,
        events=events,
        final_snapshot=ledger.snapshot(),
        peak_active_attempts=peak_active,
        trace_digest=event_log_digest(events),
    )
