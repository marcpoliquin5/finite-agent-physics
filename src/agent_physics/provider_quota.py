"""Deterministic, local quota physics for provider-call admission.

This module models declared limits.  It does not discover, measure, or certify a
remote provider's real quota state.  All time is supplied by an integer clock;
there is no sleeping, network access, or hidden retry loop.

The legacy ``global_*`` names mean "aggregate within one
``ProviderQuotaPhysics`` instance."  Instances do not share quota state, so
this module makes no process-global or distributed-coordination claim.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .serialization import content_digest


MODEL_SCOPE = "local_declared_quota_model_not_provider_measurement"
GLOBAL_GUARD_SCOPE = "per_instance_only_not_process_global_or_distributed"
SETTLEMENT_REFUND_POLICY = "unused_tokens_refunded_only_within_original_tpm_window"

__all__ = [
    "MODEL_SCOPE",
    "GLOBAL_GUARD_SCOPE",
    "SETTLEMENT_REFUND_POLICY",
    "AdmissionDecision",
    "BurstCorpusResult",
    "CallRequest",
    "FakeIntegerClock",
    "ProviderQuotaPhysics",
    "QuotaConfigurationError",
    "QuotaEvent",
    "QuotaLease",
    "QuotaLimits",
    "QuotaReplayError",
    "QuotaSettlementError",
    "QuotaSnapshot",
    "RefusalReason",
    "ReplayReport",
    "RetryDirective",
    "RetryPolicy",
    "replay_quota_events",
    "run_seeded_burst_corpus",
]


class QuotaConfigurationError(ValueError):
    """A quota contract is internally invalid."""


class QuotaSettlementError(RuntimeError):
    """A lease cannot be settled without violating its declared token cap."""


class QuotaReplayError(ValueError):
    """An event stream is not a valid replay of the declared quota model."""


class FakeIntegerClock:
    """Monotonic integer-millisecond clock used by simulations and tests."""

    __slots__ = ("_now_ms",)

    def __init__(self, now_ms: int = 0) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        self._now_ms = now_ms

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, delta_ms: int) -> int:
        if isinstance(delta_ms, bool) or not isinstance(delta_ms, int) or delta_ms < 0:
            raise ValueError("delta_ms must be a non-negative integer")
        self._now_ms += delta_ms
        return self._now_ms

    def advance_to(self, target_ms: int) -> int:
        if isinstance(target_ms, bool) or not isinstance(target_ms, int):
            raise ValueError("target_ms must be an integer")
        if target_ms < self._now_ms:
            raise ValueError("fake clock cannot move backwards")
        self._now_ms = target_ms
        return self._now_ms


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Fixed-time RPM, TPM, and concurrency limits for one modeled scope."""

    rpm: int
    tpm: int
    concurrency: int
    window_ms: int = 60_000

    def __post_init__(self) -> None:
        for name in ("rpm", "tpm", "concurrency", "window_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise QuotaConfigurationError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Deterministic exponential backoff with a strict total-attempt bound."""

    max_attempts: int = 3
    base_backoff_ms: int = 100
    max_backoff_ms: int = 10_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts <= 0
        ):
            raise QuotaConfigurationError("max_attempts must be a positive integer")
        for name in ("base_backoff_ms", "max_backoff_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise QuotaConfigurationError(f"{name} must be a non-negative integer")
        if self.max_backoff_ms < self.base_backoff_ms:
            raise QuotaConfigurationError("max_backoff_ms must be at least base_backoff_ms")

    def backoff_for(self, retry_attempt: int) -> int:
        """Return backoff before attempt 1, 2, ... without floating point."""

        if (
            isinstance(retry_attempt, bool)
            or not isinstance(retry_attempt, int)
            or retry_attempt <= 0
        ):
            raise ValueError("retry_attempt must be a positive integer")
        if self.base_backoff_ms == 0:
            return 0
        shift = min(retry_attempt - 1, 62)
        return min(self.base_backoff_ms * (1 << shift), self.max_backoff_ms)


@dataclass(frozen=True, slots=True)
class CallRequest:
    """One proposed provider call; attempt zero is the initial call."""

    logical_id: str
    attempt: int
    estimated_tokens: int
    previous_attempt_id: str | None = None
    deadline_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.logical_id, str) or not self.logical_id.strip():
            raise ValueError("logical_id must be non-empty")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        if (
            isinstance(self.estimated_tokens, bool)
            or not isinstance(self.estimated_tokens, int)
            or self.estimated_tokens <= 0
        ):
            raise ValueError("estimated_tokens must be a positive integer")
        if self.deadline_ms is not None and (
            isinstance(self.deadline_ms, bool)
            or not isinstance(self.deadline_ms, int)
            or self.deadline_ms < 0
        ):
            raise ValueError("deadline_ms must be a non-negative integer or None")
        if self.previous_attempt_id is not None and (
            not isinstance(self.previous_attempt_id, str) or not self.previous_attempt_id.strip()
        ):
            raise ValueError("previous_attempt_id must be a non-empty string or None")
        if self.attempt == 0 and self.previous_attempt_id is not None:
            raise ValueError("initial calls cannot name a previous attempt")
        if self.attempt > 0 and not self.previous_attempt_id:
            raise ValueError("retry calls must name the previous attempt")


class RefusalReason(str, Enum):
    PROVIDER_RESET = "provider_reset"
    RETRY_BACKOFF = "retry_backoff"
    RETRY_EXHAUSTED = "retry_exhausted"
    DEADLINE = "deadline"
    IDENTITY = "identity"
    TERMINAL = "terminal"
    PROVIDER_RPM = "provider_rpm"
    PROVIDER_TPM = "provider_tpm"
    PROVIDER_CONCURRENCY = "provider_concurrency"
    GLOBAL_RPM = "global_rpm"
    GLOBAL_TPM = "global_tpm"
    GLOBAL_CONCURRENCY = "global_concurrency"


@dataclass(frozen=True, slots=True)
class QuotaLease:
    provider_id: str
    logical_id: str
    attempt: int
    attempt_id: str
    lease_id: str
    token_cap: int
    admitted_at_ms: int
    deadline_ms: int | None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admitted: bool
    attempt_id: str
    lease: QuotaLease | None = None
    reasons: tuple[RefusalReason, ...] = ()
    retry_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RetryDirective:
    attempt_id: str
    retry_at_ms: int | None
    reset_at_ms: int | None
    remaining_attempts: int


Scalar = str | int | bool | None


def _detail_pairs_error(details: object) -> str | None:
    if type(details) is not tuple:
        return "event details must be an exact tuple"
    keys: list[str] = []
    for pair in details:
        if type(pair) is not tuple or len(pair) != 2:
            return "event details must contain exact key-value tuples"
        key, value = pair
        if type(key) is not str or not key:
            return "event detail keys must be exact non-empty strings"
        if value is not None and type(value) not in (str, int, bool):
            return "event detail values must be exact scalar types"
        keys.append(key)
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        return "event detail keys must be sorted and unique"
    return None


@dataclass(frozen=True, slots=True)
class QuotaEvent:
    sequence: int
    at_ms: int
    kind: str
    details: tuple[tuple[str, Scalar], ...]

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("event sequence must be a non-negative integer")
        if type(self.at_ms) is not int or self.at_ms < 0:
            raise ValueError("event time must be a non-negative integer")
        if type(self.kind) is not str or not self.kind:
            raise ValueError("event kind must be a non-empty string")
        error = _detail_pairs_error(self.details)
        if error is not None:
            raise ValueError(error)

    def as_dict(self) -> dict[str, Any]:
        if type(self) is not QuotaEvent:
            raise ValueError("quota event must be the exact QuotaEvent type")
        QuotaEvent.__post_init__(self)
        return {
            "sequence": self.sequence,
            "at_ms": self.at_ms,
            "kind": self.kind,
            "details": dict(self.details),
        }


def _snapshot_quota_event(event: object) -> QuotaEvent:
    """Copy one event into an exact immutable representation before use."""

    if type(event) is not QuotaEvent:
        raise ValueError("quota event must be the exact QuotaEvent type")
    sequence = event.sequence
    at_ms = event.at_ms
    kind = event.kind
    details = event.details
    return QuotaEvent(sequence=sequence, at_ms=at_ms, kind=kind, details=details)


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    at_ms: int
    provider_rpm_remaining: int
    provider_tpm_remaining: int
    provider_active: int
    global_rpm_remaining: int
    global_tpm_remaining: int
    global_active: int
    provider_reset_until_ms: int


@dataclass(frozen=True, slots=True)
class ReplayReport:
    valid: bool
    event_count: int
    admitted_calls: int
    refused_admissions: int
    settled_calls: int
    reset_suppressed_retries: int
    maximum_provider_active: int
    maximum_global_active: int
    actual_tokens_settled: int
    open_leases: int
    digest: str


@dataclass(frozen=True, slots=True)
class BurstCorpusResult:
    seed: int
    logical_calls: int
    admission_requests: int
    admitted_calls: int
    refused_admissions: int
    settled_calls: int
    reset_suppressed_retries: int
    maximum_provider_active: int
    maximum_global_active: int
    actual_tokens_settled: int
    event_count: int
    digest: str


@dataclass(slots=True)
class _FixedTimeTokenBucket:
    capacity: int
    refill_every_ms: int
    epoch_ms: int
    remaining: int = field(init=False)
    last_refill_ms: int = field(init=False)

    def __post_init__(self) -> None:
        self.remaining = self.capacity
        self.last_refill_ms = self.epoch_ms

    def refresh(self, now_ms: int) -> None:
        periods = (now_ms - self.last_refill_ms) // self.refill_every_ms
        if periods > 0:
            self.remaining = min(self.capacity, self.remaining + periods * self.capacity)
            self.last_refill_ms += periods * self.refill_every_ms

    def consume(self, units: int) -> None:
        if units > self.remaining:
            raise AssertionError("token bucket consumption was not admitted atomically")
        self.remaining -= units

    def refund_if_current(self, units: int, *, reserved_window_start_ms: int) -> int:
        """Credit unused units only while their reservation window is current."""

        if self.last_refill_ms != reserved_window_start_ms:
            return 0
        credited = min(units, self.capacity - self.remaining)
        self.remaining += credited
        return credited


@dataclass(slots=True)
class _LogicalState:
    last_attempt: int
    last_attempt_id: str
    deadline_ms: int | None
    retryable: bool = False
    next_retry_at_ms: int | None = None
    terminal: bool = False


@dataclass(slots=True)
class _ActiveLease:
    lease: QuotaLease
    reserved_tokens: int
    provider_tpm_window_start_ms: int
    global_tpm_window_start_ms: int


@dataclass(frozen=True, slots=True)
class _Settlement:
    active: _ActiveLease
    provider_refunded_tokens: int
    global_refunded_tokens: int


def _attempt_identity(provider_id: str, logical_id: str, attempt: int) -> str:
    material = f"finite-quota-v1\0{provider_id}\0{logical_id}\0{attempt}".encode()
    return hashlib.sha256(material).hexdigest()[:24]


def _lease_identity(attempt_id: str) -> str:
    return hashlib.sha256(f"finite-lease-v1\0{attempt_id}".encode()).hexdigest()[:24]


def _event_digest_from_snapshot(events: tuple[QuotaEvent, ...]) -> str:
    # Encode ordered pairs losslessly.  Even a malformed duplicate-key tuple
    # cannot alias a valid event by collapsing through ``dict(details)``.
    return content_digest(
        [
            {
                "sequence": event.sequence,
                "at_ms": event.at_ms,
                "kind": event.kind,
                "details": [[key, value] for key, value in event.details],
            }
            for event in events
        ]
    )


def _event_digest(events: Iterable[QuotaEvent]) -> str:
    snapshot = tuple(_snapshot_quota_event(event) for event in events)
    return _event_digest_from_snapshot(snapshot)


class ProviderQuotaPhysics:
    """Atomic admission across provider and per-instance aggregate quotas.

    The ``global_*`` parameter, snapshot, and refusal names are retained for API
    compatibility.  They describe an aggregate guard owned by this object, not
    a process-global or distributed coordinator shared by other instances.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        provider_limits: QuotaLimits,
        global_limits: QuotaLimits,
        clock: FakeIntegerClock,
        retry_policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        if not provider_id or not provider_id.strip():
            raise QuotaConfigurationError("provider_id must be non-empty")
        self.provider_id = provider_id
        self.provider_limits = provider_limits
        self.global_limits = global_limits
        self.clock = clock
        self.retry_policy = retry_policy
        epoch = clock.now_ms
        self._provider_rpm = _FixedTimeTokenBucket(
            provider_limits.rpm, provider_limits.window_ms, epoch
        )
        self._provider_tpm = _FixedTimeTokenBucket(
            provider_limits.tpm, provider_limits.window_ms, epoch
        )
        self._global_rpm = _FixedTimeTokenBucket(
            global_limits.rpm, global_limits.window_ms, epoch
        )
        self._global_tpm = _FixedTimeTokenBucket(
            global_limits.tpm, global_limits.window_ms, epoch
        )
        self._provider_active = 0
        self._global_active = 0
        self._provider_reset_until_ms = epoch
        self._events: list[QuotaEvent] = []
        self._logical: dict[str, _LogicalState] = {}
        self._active: dict[str, _ActiveLease] = {}
        self._admitted_attempt_ids: set[str] = set()
        self._emit(
            "quota.configured",
            {
                "provider_id": provider_id,
                "epoch_ms": epoch,
                "provider_rpm": provider_limits.rpm,
                "provider_tpm": provider_limits.tpm,
                "provider_concurrency": provider_limits.concurrency,
                "provider_window_ms": provider_limits.window_ms,
                "global_rpm": global_limits.rpm,
                "global_tpm": global_limits.tpm,
                "global_concurrency": global_limits.concurrency,
                "global_window_ms": global_limits.window_ms,
                "max_attempts": retry_policy.max_attempts,
                "base_backoff_ms": retry_policy.base_backoff_ms,
                "max_backoff_ms": retry_policy.max_backoff_ms,
                "model_scope": MODEL_SCOPE,
                "global_guard_scope": GLOBAL_GUARD_SCOPE,
                "settlement_refund_policy": SETTLEMENT_REFUND_POLICY,
            },
        )

    @property
    def events(self) -> tuple[QuotaEvent, ...]:
        return tuple(self._events)

    @property
    def digest(self) -> str:
        return _event_digest(self._events)

    def _emit(self, kind: str, details: dict[str, Scalar]) -> None:
        self._events.append(
            QuotaEvent(
                sequence=len(self._events),
                at_ms=self.clock.now_ms,
                kind=kind,
                details=tuple(sorted(details.items())),
            )
        )

    def _refresh(self) -> None:
        now = self.clock.now_ms
        self._provider_rpm.refresh(now)
        self._provider_tpm.refresh(now)
        self._global_rpm.refresh(now)
        self._global_tpm.refresh(now)

    def snapshot(self) -> QuotaSnapshot:
        self._refresh()
        return QuotaSnapshot(
            at_ms=self.clock.now_ms,
            provider_rpm_remaining=self._provider_rpm.remaining,
            provider_tpm_remaining=self._provider_tpm.remaining,
            provider_active=self._provider_active,
            global_rpm_remaining=self._global_rpm.remaining,
            global_tpm_remaining=self._global_tpm.remaining,
            global_active=self._global_active,
            provider_reset_until_ms=self._provider_reset_until_ms,
        )

    def _snapshot_details(self) -> dict[str, int]:
        state = self.snapshot()
        return {
            "provider_rpm_remaining": state.provider_rpm_remaining,
            "provider_tpm_remaining": state.provider_tpm_remaining,
            "provider_active": state.provider_active,
            "global_rpm_remaining": state.global_rpm_remaining,
            "global_tpm_remaining": state.global_tpm_remaining,
            "global_active": state.global_active,
            "provider_reset_until_ms": state.provider_reset_until_ms,
        }

    def _identity_reasons(self, request: CallRequest) -> tuple[RefusalReason, ...]:
        state = self._logical.get(request.logical_id)
        if request.attempt == 0:
            if state is not None:
                return (RefusalReason.TERMINAL if state.terminal else RefusalReason.IDENTITY,)
            return ()
        if state is None:
            return (RefusalReason.IDENTITY,)
        if request.attempt >= self.retry_policy.max_attempts:
            return (RefusalReason.RETRY_EXHAUSTED,)
        if state.terminal:
            return (RefusalReason.TERMINAL,)
        if not state.retryable:
            return (RefusalReason.IDENTITY,)
        if request.attempt != state.last_attempt + 1:
            return (RefusalReason.IDENTITY,)
        if request.previous_attempt_id != state.last_attempt_id:
            return (RefusalReason.IDENTITY,)
        if request.deadline_ms is not None and request.deadline_ms != state.deadline_ms:
            return (RefusalReason.IDENTITY,)
        return ()

    def acquire(self, request: CallRequest) -> AdmissionDecision:
        """Atomically reserve one call, its token cap, and one concurrency lease."""

        self._refresh()
        now = self.clock.now_ms
        attempt_id = _attempt_identity(self.provider_id, request.logical_id, request.attempt)
        state = self._logical.get(request.logical_id)
        effective_deadline = state.deadline_ms if state is not None else request.deadline_ms

        reasons = list(self._identity_reasons(request))
        retry_at: int | None = None
        if not reasons and effective_deadline is not None and now > effective_deadline:
            reasons.append(RefusalReason.DEADLINE)
        if not reasons and now < self._provider_reset_until_ms:
            reasons.append(RefusalReason.PROVIDER_RESET)
            retry_at = self._provider_reset_until_ms
        if (
            not reasons
            and state is not None
            and request.attempt > 0
            and state.next_retry_at_ms is not None
            and now < state.next_retry_at_ms
        ):
            reasons.append(RefusalReason.RETRY_BACKOFF)
            retry_at = state.next_retry_at_ms
        if not reasons:
            if self._provider_rpm.remaining < 1:
                reasons.append(RefusalReason.PROVIDER_RPM)
            if self._provider_tpm.remaining < request.estimated_tokens:
                reasons.append(RefusalReason.PROVIDER_TPM)
            if self._provider_active >= self.provider_limits.concurrency:
                reasons.append(RefusalReason.PROVIDER_CONCURRENCY)
            if self._global_rpm.remaining < 1:
                reasons.append(RefusalReason.GLOBAL_RPM)
            if self._global_tpm.remaining < request.estimated_tokens:
                reasons.append(RefusalReason.GLOBAL_TPM)
            if self._global_active >= self.global_limits.concurrency:
                reasons.append(RefusalReason.GLOBAL_CONCURRENCY)

        if reasons:
            details: dict[str, Scalar] = {
                "logical_id": request.logical_id,
                "attempt": request.attempt,
                "attempt_id": attempt_id,
                "previous_attempt_id": request.previous_attempt_id,
                "estimated_tokens": request.estimated_tokens,
                "deadline_ms": effective_deadline,
                "requested_deadline_ms": request.deadline_ms,
                "reasons": "|".join(reason.value for reason in reasons),
                "retry_at_ms": retry_at,
            }
            details.update(self._snapshot_details())
            self._emit("quota.admission_refused", details)
            return AdmissionDecision(
                admitted=False,
                attempt_id=attempt_id,
                reasons=tuple(reasons),
                retry_at_ms=retry_at,
            )

        self._provider_rpm.consume(1)
        self._provider_tpm.consume(request.estimated_tokens)
        self._global_rpm.consume(1)
        self._global_tpm.consume(request.estimated_tokens)
        self._provider_active += 1
        self._global_active += 1
        lease_id = _lease_identity(attempt_id)
        if attempt_id in self._admitted_attempt_ids or lease_id in self._active:
            raise AssertionError("admitted attempt identity collision")
        lease = QuotaLease(
            provider_id=self.provider_id,
            logical_id=request.logical_id,
            attempt=request.attempt,
            attempt_id=attempt_id,
            lease_id=lease_id,
            token_cap=request.estimated_tokens,
            admitted_at_ms=now,
            deadline_ms=effective_deadline,
        )
        self._active[lease_id] = _ActiveLease(
            lease=lease,
            reserved_tokens=request.estimated_tokens,
            provider_tpm_window_start_ms=self._provider_tpm.last_refill_ms,
            global_tpm_window_start_ms=self._global_tpm.last_refill_ms,
        )
        self._admitted_attempt_ids.add(attempt_id)
        self._logical[request.logical_id] = _LogicalState(
            last_attempt=request.attempt,
            last_attempt_id=attempt_id,
            deadline_ms=effective_deadline,
        )
        details = {
            "logical_id": request.logical_id,
            "attempt": request.attempt,
            "attempt_id": attempt_id,
            "previous_attempt_id": request.previous_attempt_id,
            "lease_id": lease_id,
            "estimated_tokens": request.estimated_tokens,
            "deadline_ms": effective_deadline,
            "requested_deadline_ms": request.deadline_ms,
        }
        details.update(self._snapshot_details())
        self._emit("quota.call_admitted", details)
        return AdmissionDecision(admitted=True, attempt_id=attempt_id, lease=lease)

    def _settle_active(self, lease_id: str, actual_tokens: int) -> _Settlement:
        if isinstance(actual_tokens, bool) or not isinstance(actual_tokens, int) or actual_tokens < 0:
            raise ValueError("actual_tokens must be a non-negative integer")
        active = self._active.get(lease_id)
        if active is None:
            raise QuotaSettlementError("lease is not active")
        if actual_tokens > active.reserved_tokens:
            details: dict[str, Scalar] = {
                "lease_id": lease_id,
                "attempt_id": active.lease.attempt_id,
                "logical_id": active.lease.logical_id,
                "attempt": active.lease.attempt,
                "actual_tokens": actual_tokens,
                "token_cap": active.reserved_tokens,
                "reason": "actual_tokens_exceed_admitted_cap",
            }
            details.update(self._snapshot_details())
            self._emit("quota.settlement_refused", details)
            raise QuotaSettlementError("actual tokens exceed the lease's admitted token cap")
        self._refresh()
        unused_tokens = active.reserved_tokens - actual_tokens
        provider_refunded_tokens = self._provider_tpm.refund_if_current(
            unused_tokens,
            reserved_window_start_ms=active.provider_tpm_window_start_ms,
        )
        global_refunded_tokens = self._global_tpm.refund_if_current(
            unused_tokens,
            reserved_window_start_ms=active.global_tpm_window_start_ms,
        )
        self._provider_active -= 1
        self._global_active -= 1
        del self._active[lease_id]
        return _Settlement(
            active=active,
            provider_refunded_tokens=provider_refunded_tokens,
            global_refunded_tokens=global_refunded_tokens,
        )

    def complete(self, lease_id: str, *, actual_tokens: int) -> None:
        settlement = self._settle_active(lease_id, actual_tokens)
        active = settlement.active
        state = self._logical[active.lease.logical_id]
        state.terminal = True
        details: dict[str, Scalar] = {
            "lease_id": lease_id,
            "attempt_id": active.lease.attempt_id,
            "logical_id": active.lease.logical_id,
            "attempt": active.lease.attempt,
            "reserved_tokens": active.reserved_tokens,
            "actual_tokens": actual_tokens,
            "unused_tokens": active.reserved_tokens - actual_tokens,
            "provider_refunded_tokens": settlement.provider_refunded_tokens,
            "global_refunded_tokens": settlement.global_refunded_tokens,
            "outcome": "success",
        }
        details.update(self._snapshot_details())
        self._emit("quota.call_settled", details)

    def fail_retryable(self, lease_id: str, *, actual_tokens: int) -> RetryDirective:
        settlement = self._settle_active(lease_id, actual_tokens)
        active = settlement.active
        state = self._logical[active.lease.logical_id]
        next_attempt = active.lease.attempt + 1
        retry_at: int | None = None
        if next_attempt < self.retry_policy.max_attempts:
            candidate = self.clock.now_ms + self.retry_policy.backoff_for(next_attempt)
            if state.deadline_ms is None or candidate <= state.deadline_ms:
                retry_at = candidate
        state.retryable = retry_at is not None
        state.next_retry_at_ms = retry_at
        state.terminal = retry_at is None
        details: dict[str, Scalar] = {
            "lease_id": lease_id,
            "attempt_id": active.lease.attempt_id,
            "logical_id": active.lease.logical_id,
            "attempt": active.lease.attempt,
            "reserved_tokens": active.reserved_tokens,
            "actual_tokens": actual_tokens,
            "unused_tokens": active.reserved_tokens - actual_tokens,
            "provider_refunded_tokens": settlement.provider_refunded_tokens,
            "global_refunded_tokens": settlement.global_refunded_tokens,
            "next_retry_at_ms": retry_at,
            "retry_exhausted": retry_at is None,
            "outcome": "retryable_failure",
        }
        details.update(self._snapshot_details())
        self._emit("quota.call_settled", details)
        return RetryDirective(
            attempt_id=active.lease.attempt_id,
            retry_at_ms=retry_at,
            reset_at_ms=None,
            remaining_attempts=max(0, self.retry_policy.max_attempts - next_attempt),
        )

    def provider_429(
        self,
        lease_id: str,
        *,
        actual_tokens: int = 0,
        reset_after_ms: int | None = None,
        reset_at_ms: int | None = None,
    ) -> RetryDirective:
        """Settle one dispatched call and declare a provider-wide reset window."""

        if (reset_after_ms is None) == (reset_at_ms is None):
            raise ValueError("provide exactly one of reset_after_ms or reset_at_ms")
        if reset_after_ms is not None:
            if (
                isinstance(reset_after_ms, bool)
                or not isinstance(reset_after_ms, int)
                or reset_after_ms <= 0
            ):
                raise ValueError("reset_after_ms must be a positive integer")
            effective_reset = self.clock.now_ms + reset_after_ms
        else:
            if (
                isinstance(reset_at_ms, bool)
                or not isinstance(reset_at_ms, int)
                or reset_at_ms <= self.clock.now_ms
            ):
                raise ValueError("reset_at_ms must be an integer later than now")
            effective_reset = reset_at_ms
        settlement = self._settle_active(lease_id, actual_tokens)
        active = settlement.active
        self._provider_reset_until_ms = max(self._provider_reset_until_ms, effective_reset)
        state = self._logical[active.lease.logical_id]
        next_attempt = active.lease.attempt + 1
        retry_at: int | None = None
        if next_attempt < self.retry_policy.max_attempts:
            retry_at = max(
                self.clock.now_ms + self.retry_policy.backoff_for(next_attempt),
                self._provider_reset_until_ms,
            )
            if state.deadline_ms is not None and retry_at > state.deadline_ms:
                retry_at = None
        state.retryable = retry_at is not None
        state.next_retry_at_ms = retry_at
        state.terminal = retry_at is None
        details: dict[str, Scalar] = {
            "lease_id": lease_id,
            "attempt_id": active.lease.attempt_id,
            "logical_id": active.lease.logical_id,
            "attempt": active.lease.attempt,
            "reserved_tokens": active.reserved_tokens,
            "actual_tokens": actual_tokens,
            "unused_tokens": active.reserved_tokens - actual_tokens,
            "provider_refunded_tokens": settlement.provider_refunded_tokens,
            "global_refunded_tokens": settlement.global_refunded_tokens,
            "reset_at_ms": self._provider_reset_until_ms,
            "next_retry_at_ms": retry_at,
            "retry_exhausted": retry_at is None,
            "outcome": "provider_429",
        }
        details.update(self._snapshot_details())
        self._emit("quota.provider_429", details)
        return RetryDirective(
            attempt_id=active.lease.attempt_id,
            retry_at_ms=retry_at,
            reset_at_ms=self._provider_reset_until_ms,
            remaining_attempts=max(0, self.retry_policy.max_attempts - next_attempt),
        )


def replay_quota_events(events: Iterable[QuotaEvent]) -> ReplayReport:
    """Independently replay event evidence and reject quota or identity violations."""

    try:
        # Snapshot each yielded event before requesting the next one.  A hostile
        # input iterator cannot mutate an already-yielded source event and alter
        # the semantic replay or its final digest.
        stream = tuple(_snapshot_quota_event(event) for event in events)
    except ValueError as exc:
        raise QuotaReplayError(str(exc)) from exc
    if not stream:
        raise QuotaReplayError("quota event stream is empty")
    first = stream[0]
    if first.sequence != 0 or first.kind != "quota.configured":
        raise QuotaReplayError("first event must be quota.configured at sequence zero")
    config = dict(first.details)
    required = {
        "provider_id",
        "epoch_ms",
        "provider_rpm",
        "provider_tpm",
        "provider_concurrency",
        "provider_window_ms",
        "global_rpm",
        "global_tpm",
        "global_concurrency",
        "global_window_ms",
        "max_attempts",
        "base_backoff_ms",
        "max_backoff_ms",
        "model_scope",
        "global_guard_scope",
        "settlement_refund_policy",
    }
    if (
        set(config) != required
        or config["model_scope"] != MODEL_SCOPE
        or config["global_guard_scope"] != GLOBAL_GUARD_SCOPE
        or config["settlement_refund_policy"] != SETTLEMENT_REFUND_POLICY
    ):
        raise QuotaReplayError("configuration evidence is missing or out of scope")

    def integer(name: str, *, positive: bool = True) -> int:
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise QuotaReplayError(f"configuration {name} is not an integer")
        if (positive and value <= 0) or (not positive and value < 0):
            raise QuotaReplayError(f"configuration {name} is out of range")
        return value

    epoch = integer("epoch_ms", positive=False)
    provider_id = config["provider_id"]
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise QuotaReplayError("configuration provider_id is invalid")
    provider_rpm_capacity = integer("provider_rpm")
    provider_tpm_capacity = integer("provider_tpm")
    provider_concurrency = integer("provider_concurrency")
    provider_window = integer("provider_window_ms")
    global_rpm_capacity = integer("global_rpm")
    global_tpm_capacity = integer("global_tpm")
    global_concurrency = integer("global_concurrency")
    global_window = integer("global_window_ms")
    max_attempts = integer("max_attempts")
    base_backoff = integer("base_backoff_ms", positive=False)
    max_backoff = integer("max_backoff_ms", positive=False)
    if max_backoff < base_backoff:
        raise QuotaReplayError("configuration retry backoff is invalid")

    buckets = {
        "provider_rpm": [provider_rpm_capacity, provider_rpm_capacity, provider_window, epoch],
        "provider_tpm": [provider_tpm_capacity, provider_tpm_capacity, provider_window, epoch],
        "global_rpm": [global_rpm_capacity, global_rpm_capacity, global_window, epoch],
        "global_tpm": [global_tpm_capacity, global_tpm_capacity, global_window, epoch],
    }

    def refresh(now: int) -> None:
        for bucket in buckets.values():
            capacity, remaining, window, last = bucket
            periods = (now - last) // window
            if periods > 0:
                bucket[1] = min(capacity, remaining + periods * capacity)
                bucket[3] = last + periods * window

    def backoff(retry_attempt: int) -> int:
        if base_backoff == 0:
            return 0
        return min(base_backoff * (1 << min(retry_attempt - 1, 62)), max_backoff)

    active: dict[str, dict[str, Scalar]] = {}
    logical: dict[str, dict[str, Scalar]] = {}
    admitted_ids: set[str] = set()
    provider_active = 0
    global_active = 0
    reset_until = epoch
    previous_time = first.at_ms
    admitted = refused = settled = reset_suppressed = actual_total = 0
    max_provider_active = max_global_active = 0
    snapshot_keys = {
        "provider_rpm_remaining",
        "provider_tpm_remaining",
        "provider_active",
        "global_rpm_remaining",
        "global_tpm_remaining",
        "global_active",
        "provider_reset_until_ms",
    }

    def expect_snapshot(details: dict[str, Scalar]) -> None:
        expected = {
            "provider_rpm_remaining": buckets["provider_rpm"][1],
            "provider_tpm_remaining": buckets["provider_tpm"][1],
            "provider_active": provider_active,
            "global_rpm_remaining": buckets["global_rpm"][1],
            "global_tpm_remaining": buckets["global_tpm"][1],
            "global_active": global_active,
            "provider_reset_until_ms": reset_until,
        }
        if any(
            type(details.get(key)) is not int or details.get(key) != value
            for key, value in expected.items()
        ):
            raise QuotaReplayError("recorded quota snapshot does not match replay state")

    def optional_int_matches(recorded: Scalar, expected: int | None) -> bool:
        if expected is None:
            return recorded is None
        return type(recorded) is int and recorded == expected

    for expected_sequence, event in enumerate(stream):
        if event.sequence != expected_sequence:
            raise QuotaReplayError("event sequence is not contiguous")
        if event.at_ms < previous_time:
            raise QuotaReplayError("event time moved backwards")
        previous_time = event.at_ms
        if expected_sequence == 0:
            if event.at_ms != epoch:
                raise QuotaReplayError("configuration time does not match epoch")
            continue
        refresh(event.at_ms)
        details = dict(event.details)
        kind = event.kind
        if kind == "quota.call_admitted":
            admission_keys = {
                "logical_id",
                "attempt",
                "attempt_id",
                "previous_attempt_id",
                "lease_id",
                "estimated_tokens",
                "deadline_ms",
                "requested_deadline_ms",
            } | snapshot_keys
            if set(details) != admission_keys:
                raise QuotaReplayError("admission evidence fields are not canonical")
            logical_id = details.get("logical_id")
            attempt = details.get("attempt")
            attempt_id = details.get("attempt_id")
            lease_id = details.get("lease_id")
            tokens = details.get("estimated_tokens")
            previous_attempt_id = details.get("previous_attempt_id")
            deadline = details.get("deadline_ms")
            requested_deadline = details.get("requested_deadline_ms")
            if (
                not isinstance(logical_id, str)
                or not logical_id.strip()
                or isinstance(attempt, bool)
                or not isinstance(attempt, int)
                or attempt < 0
                or isinstance(tokens, bool)
                or not isinstance(tokens, int)
                or tokens <= 0
                or not isinstance(attempt_id, str)
                or not isinstance(lease_id, str)
                or isinstance(deadline, bool)
                or (deadline is not None and not isinstance(deadline, int))
                or (isinstance(deadline, int) and deadline < 0)
                or isinstance(requested_deadline, bool)
                or (requested_deadline is not None and not isinstance(requested_deadline, int))
                or (isinstance(requested_deadline, int) and requested_deadline < 0)
            ):
                raise QuotaReplayError("admission identity or token reservation is invalid")
            if attempt_id != _attempt_identity(provider_id, logical_id, attempt):
                raise QuotaReplayError("attempt identity does not match canonical identity")
            if lease_id != _lease_identity(attempt_id):
                raise QuotaReplayError("lease identity does not match canonical identity")
            if attempt_id in admitted_ids or lease_id in active:
                raise QuotaReplayError("attempt or lease identity was admitted twice")
            if event.at_ms < reset_until:
                raise QuotaReplayError("a call was admitted during a declared provider reset")
            prior = logical.get(logical_id)
            if attempt == 0:
                if (
                    prior is not None
                    or previous_attempt_id is not None
                    or deadline != requested_deadline
                ):
                    raise QuotaReplayError("initial call identity is not unique")
            else:
                if (
                    not isinstance(previous_attempt_id, str)
                    or not previous_attempt_id.strip()
                ):
                    raise QuotaReplayError("retry predecessor identity is invalid")
                if prior is None or prior.get("terminal") or not prior.get("retryable"):
                    raise QuotaReplayError("retry has no retryable predecessor")
                if attempt >= max_attempts or attempt != prior["attempt"] + 1:
                    raise QuotaReplayError("retry exceeds bound or skips an attempt")
                if previous_attempt_id != prior["attempt_id"]:
                    raise QuotaReplayError("retry predecessor identity does not match")
                if deadline != prior["deadline_ms"]:
                    raise QuotaReplayError("retry changed its original deadline")
                if requested_deadline is not None and requested_deadline != prior["deadline_ms"]:
                    raise QuotaReplayError("retry requested a changed deadline")
                retry_at = prior["next_retry_at_ms"]
                if not isinstance(retry_at, int) or event.at_ms < retry_at:
                    raise QuotaReplayError("retry was admitted before deterministic backoff")
            if deadline is not None and event.at_ms > deadline:
                raise QuotaReplayError("call was admitted after its deadline")
            if (
                buckets["provider_rpm"][1] < 1
                or buckets["provider_tpm"][1] < tokens
                or provider_active >= provider_concurrency
                or buckets["global_rpm"][1] < 1
                or buckets["global_tpm"][1] < tokens
                or global_active >= global_concurrency
            ):
                raise QuotaReplayError("call admission exceeds a declared quota")
            buckets["provider_rpm"][1] -= 1
            buckets["provider_tpm"][1] -= tokens
            buckets["global_rpm"][1] -= 1
            buckets["global_tpm"][1] -= tokens
            provider_active += 1
            global_active += 1
            max_provider_active = max(max_provider_active, provider_active)
            max_global_active = max(max_global_active, global_active)
            admitted_ids.add(attempt_id)
            active[lease_id] = {
                "logical_id": logical_id,
                "attempt": attempt,
                "attempt_id": attempt_id,
                "reserved_tokens": tokens,
                "deadline_ms": deadline,
                "provider_tpm_window_start_ms": buckets["provider_tpm"][3],
                "global_tpm_window_start_ms": buckets["global_tpm"][3],
            }
            logical[logical_id] = {
                "attempt": attempt,
                "attempt_id": attempt_id,
                "deadline_ms": deadline,
                "retryable": False,
                "next_retry_at_ms": None,
                "terminal": False,
            }
            admitted += 1
            expect_snapshot(details)
        elif kind in {"quota.call_settled", "quota.provider_429"}:
            settlement_base_keys = {
                "lease_id",
                "attempt_id",
                "logical_id",
                "attempt",
                "reserved_tokens",
                "actual_tokens",
                "unused_tokens",
                "provider_refunded_tokens",
                "global_refunded_tokens",
                "outcome",
            } | snapshot_keys
            outcome = details.get("outcome")
            if kind == "quota.provider_429":
                expected_settlement_keys = settlement_base_keys | {
                    "reset_at_ms",
                    "next_retry_at_ms",
                    "retry_exhausted",
                }
                if outcome != "provider_429":
                    raise QuotaReplayError("429 settlement outcome is not canonical")
            elif outcome == "retryable_failure":
                expected_settlement_keys = settlement_base_keys | {
                    "next_retry_at_ms",
                    "retry_exhausted",
                }
            elif outcome == "success":
                expected_settlement_keys = settlement_base_keys
            else:
                raise QuotaReplayError("settlement outcome is unknown")
            if set(details) != expected_settlement_keys:
                raise QuotaReplayError("settlement evidence fields are not canonical")
            lease_id = details.get("lease_id")
            lease = active.get(lease_id) if isinstance(lease_id, str) else None
            actual = details.get("actual_tokens")
            reserved = details.get("reserved_tokens")
            if lease is None or isinstance(actual, bool) or not isinstance(actual, int):
                raise QuotaReplayError("settlement does not name an active lease")
            if (
                details.get("attempt_id") != lease["attempt_id"]
                or details.get("logical_id") != lease["logical_id"]
                or type(details.get("attempt")) is not int
                or details.get("attempt") != lease["attempt"]
            ):
                raise QuotaReplayError("settlement identity does not match its active lease")
            if (
                isinstance(reserved, bool)
                or not isinstance(reserved, int)
                or reserved != lease["reserved_tokens"]
                or actual < 0
                or actual > reserved
            ):
                raise QuotaReplayError("token settlement exceeds or changes its reservation")
            unused_tokens = reserved - actual
            provider_refund = 0
            if lease["provider_tpm_window_start_ms"] == buckets["provider_tpm"][3]:
                provider_refund = min(
                    unused_tokens,
                    provider_tpm_capacity - buckets["provider_tpm"][1],
                )
            global_refund = 0
            if lease["global_tpm_window_start_ms"] == buckets["global_tpm"][3]:
                global_refund = min(
                    unused_tokens,
                    global_tpm_capacity - buckets["global_tpm"][1],
                )
            recorded_refund_values = (
                details.get("unused_tokens"),
                details.get("provider_refunded_tokens"),
                details.get("global_refunded_tokens"),
            )
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in recorded_refund_values
            ) or recorded_refund_values != (
                unused_tokens,
                provider_refund,
                global_refund,
            ):
                raise QuotaReplayError("settlement refund is not reservation-window scoped")
            buckets["provider_tpm"][1] += provider_refund
            buckets["global_tpm"][1] += global_refund
            provider_active -= 1
            global_active -= 1
            del active[lease_id]
            state = logical[str(lease["logical_id"])]
            state["retryable"] = False
            state["next_retry_at_ms"] = None
            if kind == "quota.provider_429":
                declared_reset = details.get("reset_at_ms")
                if (
                    isinstance(declared_reset, bool)
                    or not isinstance(declared_reset, int)
                    or declared_reset <= event.at_ms
                    or declared_reset < reset_until
                ):
                    raise QuotaReplayError("provider reset timestamp is invalid")
                reset_until = declared_reset
                next_attempt = int(lease["attempt"]) + 1
                expected_retry = None
                if next_attempt < max_attempts:
                    expected_retry = max(event.at_ms + backoff(next_attempt), reset_until)
                    deadline = lease["deadline_ms"]
                    if isinstance(deadline, int) and expected_retry > deadline:
                        expected_retry = None
                if not optional_int_matches(details.get("next_retry_at_ms"), expected_retry):
                    raise QuotaReplayError("429 retry timestamp is not reset/backoff aware")
            else:
                if outcome == "success":
                    expected_retry = None
                elif outcome == "retryable_failure":
                    next_attempt = int(lease["attempt"]) + 1
                    expected_retry = None
                    if next_attempt < max_attempts:
                        expected_retry = event.at_ms + backoff(next_attempt)
                        deadline = lease["deadline_ms"]
                        if isinstance(deadline, int) and expected_retry > deadline:
                            expected_retry = None
                    if not optional_int_matches(
                        details.get("next_retry_at_ms"), expected_retry
                    ):
                        raise QuotaReplayError("retry timestamp violates bounded backoff")
                else:
                    raise QuotaReplayError("settlement outcome is unknown")
            if kind != "quota.call_settled" or outcome == "retryable_failure":
                if details.get("retry_exhausted") is not (expected_retry is None):
                    raise QuotaReplayError("settlement retry exhaustion flag is inconsistent")
            state["retryable"] = expected_retry is not None
            state["next_retry_at_ms"] = expected_retry
            state["terminal"] = expected_retry is None
            actual_total += actual
            settled += 1
            expect_snapshot(details)
        elif kind == "quota.admission_refused":
            refusal_keys = {
                "logical_id",
                "attempt",
                "attempt_id",
                "previous_attempt_id",
                "estimated_tokens",
                "deadline_ms",
                "requested_deadline_ms",
                "reasons",
                "retry_at_ms",
            } | snapshot_keys
            if set(details) != refusal_keys:
                raise QuotaReplayError("refusal evidence fields are not canonical")
            logical_id = details.get("logical_id")
            attempt = details.get("attempt")
            attempt_id = details.get("attempt_id")
            previous_attempt_id = details.get("previous_attempt_id")
            tokens = details.get("estimated_tokens")
            requested_deadline = details.get("requested_deadline_ms")
            recorded_deadline = details.get("deadline_ms")
            if (
                not isinstance(logical_id, str)
                or not logical_id.strip()
                or isinstance(attempt, bool)
                or not isinstance(attempt, int)
                or attempt < 0
                or not isinstance(attempt_id, str)
                or isinstance(tokens, bool)
                or not isinstance(tokens, int)
                or tokens <= 0
                or isinstance(requested_deadline, bool)
                or (requested_deadline is not None and not isinstance(requested_deadline, int))
                or (isinstance(requested_deadline, int) and requested_deadline < 0)
                or isinstance(recorded_deadline, bool)
                or (recorded_deadline is not None and not isinstance(recorded_deadline, int))
            ):
                raise QuotaReplayError("refusal request evidence is invalid")
            if attempt == 0:
                if previous_attempt_id is not None:
                    raise QuotaReplayError("refused initial call names a predecessor")
            elif (
                not isinstance(previous_attempt_id, str)
                or not previous_attempt_id.strip()
            ):
                raise QuotaReplayError("refused retry predecessor identity is invalid")
            if attempt_id != _attempt_identity(provider_id, logical_id, attempt):
                raise QuotaReplayError("refusal attempt identity is not canonical")

            prior = logical.get(logical_id)
            effective_deadline = (
                prior["deadline_ms"] if prior is not None else requested_deadline
            )
            if recorded_deadline != effective_deadline:
                raise QuotaReplayError("refusal effective deadline is inconsistent")

            expected_reasons: list[RefusalReason] = []
            if attempt == 0:
                if prior is not None:
                    expected_reasons.append(
                        RefusalReason.TERMINAL
                        if prior["terminal"]
                        else RefusalReason.IDENTITY
                    )
            elif prior is None:
                expected_reasons.append(RefusalReason.IDENTITY)
            elif attempt >= max_attempts:
                expected_reasons.append(RefusalReason.RETRY_EXHAUSTED)
            elif prior["terminal"]:
                expected_reasons.append(RefusalReason.TERMINAL)
            elif not prior["retryable"]:
                expected_reasons.append(RefusalReason.IDENTITY)
            elif attempt != int(prior["attempt"]) + 1:
                expected_reasons.append(RefusalReason.IDENTITY)
            elif previous_attempt_id != prior["attempt_id"]:
                expected_reasons.append(RefusalReason.IDENTITY)
            elif (
                requested_deadline is not None
                and requested_deadline != prior["deadline_ms"]
            ):
                expected_reasons.append(RefusalReason.IDENTITY)

            expected_retry_at: int | None = None
            if (
                not expected_reasons
                and isinstance(effective_deadline, int)
                and event.at_ms > effective_deadline
            ):
                expected_reasons.append(RefusalReason.DEADLINE)
            if not expected_reasons and event.at_ms < reset_until:
                expected_reasons.append(RefusalReason.PROVIDER_RESET)
                expected_retry_at = reset_until
            if (
                not expected_reasons
                and prior is not None
                and attempt > 0
                and isinstance(prior["next_retry_at_ms"], int)
                and event.at_ms < prior["next_retry_at_ms"]
            ):
                expected_reasons.append(RefusalReason.RETRY_BACKOFF)
                expected_retry_at = int(prior["next_retry_at_ms"])
            if not expected_reasons:
                if buckets["provider_rpm"][1] < 1:
                    expected_reasons.append(RefusalReason.PROVIDER_RPM)
                if buckets["provider_tpm"][1] < tokens:
                    expected_reasons.append(RefusalReason.PROVIDER_TPM)
                if provider_active >= provider_concurrency:
                    expected_reasons.append(RefusalReason.PROVIDER_CONCURRENCY)
                if buckets["global_rpm"][1] < 1:
                    expected_reasons.append(RefusalReason.GLOBAL_RPM)
                if buckets["global_tpm"][1] < tokens:
                    expected_reasons.append(RefusalReason.GLOBAL_TPM)
                if global_active >= global_concurrency:
                    expected_reasons.append(RefusalReason.GLOBAL_CONCURRENCY)

            expected_reason_text = "|".join(reason.value for reason in expected_reasons)
            if not expected_reasons or details.get("reasons") != expected_reason_text:
                raise QuotaReplayError("refusal reasons are not entailed by replay state")
            if not optional_int_matches(details.get("retry_at_ms"), expected_retry_at):
                raise QuotaReplayError("refusal retry timestamp is not entailed by replay state")
            if RefusalReason.PROVIDER_RESET in expected_reasons and attempt > 0:
                reset_suppressed += 1
            expect_snapshot(details)
            refused += 1
        elif kind == "quota.settlement_refused":
            settlement_refusal_keys = {
                "lease_id",
                "attempt_id",
                "logical_id",
                "attempt",
                "actual_tokens",
                "token_cap",
                "reason",
            } | snapshot_keys
            if set(details) != settlement_refusal_keys:
                raise QuotaReplayError("settlement refusal evidence fields are not canonical")
            lease_id = details.get("lease_id")
            lease = active.get(lease_id) if isinstance(lease_id, str) else None
            actual = details.get("actual_tokens")
            if (
                lease is None
                or isinstance(actual, bool)
                or not isinstance(actual, int)
                or actual <= int(lease["reserved_tokens"])
                or details.get("attempt_id") != lease["attempt_id"]
                or details.get("logical_id") != lease["logical_id"]
                or type(details.get("attempt")) is not int
                or details.get("attempt") != lease["attempt"]
                or type(details.get("token_cap")) is not int
                or details.get("token_cap") != lease["reserved_tokens"]
                or details.get("reason") != "actual_tokens_exceed_admitted_cap"
            ):
                raise QuotaReplayError("settlement refusal is not entailed by its active lease")
            expect_snapshot(details)
        else:
            raise QuotaReplayError(f"unknown quota event kind: {kind}")

    return ReplayReport(
        valid=True,
        event_count=len(stream),
        admitted_calls=admitted,
        refused_admissions=refused,
        settled_calls=settled,
        reset_suppressed_retries=reset_suppressed,
        maximum_provider_active=max_provider_active,
        maximum_global_active=max_global_active,
        actual_tokens_settled=actual_total,
        open_leases=len(active),
        digest=_event_digest_from_snapshot(stream),
    )


def run_seeded_burst_corpus(*, seed: int = 13, cycles: int = 48) -> BurstCorpusResult:
    """Run a fast deterministic burst corpus against only the local quota model."""

    import random

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles <= 0:
        raise ValueError("cycles must be a positive integer")
    rng = random.Random(seed)
    clock = FakeIntegerClock()
    physics = ProviderQuotaPhysics(
        provider_id="fixture-provider",
        provider_limits=QuotaLimits(rpm=9, tpm=360, concurrency=3, window_ms=1_000),
        global_limits=QuotaLimits(rpm=8, tpm=420, concurrency=4, window_ms=1_000),
        clock=clock,
        retry_policy=RetryPolicy(max_attempts=3, base_backoff_ms=25, max_backoff_ms=100),
    )
    logical_calls = 0
    admission_requests = 0

    for cycle in range(cycles):
        clock.advance_to(cycle * 1_000)
        for wave in range(5):
            admitted_wave: list[QuotaLease] = []
            for slot in range(5):
                logical_id = f"s{seed}-c{cycle:03d}-w{wave}-n{slot}"
                tokens = rng.randint(24, 88)
                decision = physics.acquire(
                    CallRequest(
                        logical_id=logical_id,
                        attempt=0,
                        estimated_tokens=tokens,
                        deadline_ms=(cycle + 1) * 1_000 - 1,
                    )
                )
                logical_calls += 1
                admission_requests += 1
                if decision.admitted:
                    assert decision.lease is not None
                    admitted_wave.append(decision.lease)

            for index, lease in enumerate(admitted_wave):
                actual = rng.randint(0, lease.token_cap)
                if cycle % 8 == 0 and wave == 0 and index == 0:
                    directive = physics.provider_429(
                        lease.lease_id, actual_tokens=actual, reset_after_ms=125
                    )
                    retry = CallRequest(
                        logical_id=lease.logical_id,
                        attempt=1,
                        estimated_tokens=lease.token_cap,
                        previous_attempt_id=lease.attempt_id,
                        deadline_ms=lease.deadline_ms,
                    )
                    for _ in range(3):
                        physics.acquire(retry)
                        admission_requests += 1
                    if directive.retry_at_ms is not None:
                        clock.advance_to(directive.retry_at_ms)
                        retried = physics.acquire(retry)
                        admission_requests += 1
                        if retried.admitted:
                            assert retried.lease is not None
                            physics.complete(
                                retried.lease.lease_id,
                                actual_tokens=rng.randint(0, retried.lease.token_cap),
                            )
                elif (cycle + wave + index) % 11 == 0:
                    directive = physics.fail_retryable(lease.lease_id, actual_tokens=actual)
                    retry = CallRequest(
                        logical_id=lease.logical_id,
                        attempt=1,
                        estimated_tokens=lease.token_cap,
                        previous_attempt_id=lease.attempt_id,
                        deadline_ms=lease.deadline_ms,
                    )
                    physics.acquire(retry)
                    admission_requests += 1
                    if directive.retry_at_ms is not None:
                        clock.advance_to(directive.retry_at_ms)
                        retried = physics.acquire(retry)
                        admission_requests += 1
                        if retried.admitted:
                            assert retried.lease is not None
                            physics.complete(
                                retried.lease.lease_id,
                                actual_tokens=rng.randint(0, retried.lease.token_cap),
                            )
                else:
                    physics.complete(lease.lease_id, actual_tokens=actual)
            clock.advance(rng.randint(7, 29))

    report = replay_quota_events(physics.events)
    if report.open_leases:
        raise AssertionError("seeded corpus left a concurrency lease open")
    return BurstCorpusResult(
        seed=seed,
        logical_calls=logical_calls,
        admission_requests=admission_requests,
        admitted_calls=report.admitted_calls,
        refused_admissions=report.refused_admissions,
        settled_calls=report.settled_calls,
        reset_suppressed_retries=report.reset_suppressed_retries,
        maximum_provider_active=report.maximum_provider_active,
        maximum_global_active=report.maximum_global_active,
        actual_tokens_settled=report.actual_tokens_settled,
        event_count=report.event_count,
        digest=report.digest,
    )
