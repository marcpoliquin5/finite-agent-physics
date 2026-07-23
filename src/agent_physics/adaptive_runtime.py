"""Bounded active execution with durable, replayable adaptive control.

This module closes the gap between a modeled residual plan and a small active
runtime.  It dispatches at most one local worker at a time, persists every
controller transition through :class:`SQLiteRunStore`, and uses the same pure
reducer for live control and call-free replay.

The controller is intentionally narrow.  Provider 429/reset/capacity inputs are
caller-supplied control facts, not live telemetry; workers are local callables;
there are no hidden retries, and declared writes stop at a durable effect intent.
A crash-ambiguous in-flight reservation is charged at its full declared bound.
Optional work can then be shed, but protected mandatory work is never silently
marked complete.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .contracts import BackendProfile, EffectClass, RunEnvelope, TaskContract
from .effects import SQLiteEffectBroker, scoped_effect_idempotency_key
from .graph import ExecutionGraph
from .run_store import SQLiteRunStore, Usage
from .scheduler import SchedulePolicy, Scheduler
from .serialization import canonical_json, content_digest, normalize


ADAPTIVE_RUNTIME_SCHEMA_VERSION: Final[str] = "finite-adaptive-controller/v1"
ADAPTIVE_STATE_SCHEMA_VERSION: Final[str] = "finite-adaptive-state/v1"
ADAPTIVE_EVENT_SCHEMA_VERSION: Final[str] = "finite-adaptive-event/v1"
ADAPTIVE_DECISION_SCHEMA_VERSION: Final[str] = "finite-adaptive-decision/v1"

ADAPTIVE_RUNTIME_SCOPE: Final[tuple[str, ...]] = (
    "bounded single-dispatch local worker orchestration",
    "append-only SQLite controller evidence and deterministic call-free replay",
    "componentwise token, cost-microusd, and context-byte protection",
    "caller-supplied provider reset/capacity and budget control facts",
    "full-reservation accounting for crash-ambiguous in-flight work",
    "durable proposal-only handling for declared write effects",
)

ADAPTIVE_RUNTIME_LIMITATIONS: Final[tuple[str, ...]] = (
    "provider control events are declared inputs, not authenticated live telemetry",
    "workers are local deterministic fixtures; this module makes no live-provider claim",
    "dispatch is deliberately single-flight and has no automatic retry policy",
    "SQLite is a single-database durability boundary, not distributed consensus",
    "controller transition and task-attempt appends are ordered but not one SQL transaction",
    "digest binding detects mutation but is not a producer signature",
    "declared writes are proposed to the effect broker and never externally committed",
)

_SHA256_CHARS = frozenset("0123456789abcdef")
_USAGE_FIELDS = frozenset({"tokens", "cost_microusd", "context_bytes"})
_EVENT_FIELDS = frozenset(
    {"schema_version", "event_id", "kind", "occurred_at_ms", "details", "event_digest"}
)
_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "graph_digest",
        "revision",
        "prior_state_digest",
        "deadline_ms",
        "caps",
        "settled_usage",
        "unknown_usage",
        "completed_task_ids",
        "shed_task_ids",
        "unknown_task_ids",
        "inflight",
        "provider_resets",
        "provider_capacities",
        "now_ms",
        "status",
        "state_digest",
    }
)
_INFLIGHT_FIELDS = frozenset(
    {"task_id", "attempt", "provider", "backend", "reservation", "dispatch_event_digest"}
)
_PROVIDER_RESET_FIELDS = frozenset({"provider", "reset_at_ms"})
_PROVIDER_CAPACITY_FIELDS = frozenset({"provider", "capacity"})
_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "revision",
        "event_id",
        "event_digest",
        "action",
        "task_id",
        "eligible_task_ids",
        "protected_task_ids",
        "newly_shed_task_ids",
        "prior_state_digest",
        "next_state_digest",
        "status",
        "reason_code",
        "decision_digest",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "revision",
        "prior_state_digest",
        "event",
        "next_state",
        "decision",
        "record_digest",
    }
)


class AdaptiveRuntimeError(RuntimeError):
    """Base class for active-controller failures."""


class AdaptiveInvariantError(AdaptiveRuntimeError):
    """A control input or worker result violated a hard invariant."""


class AdaptiveReplayError(AdaptiveRuntimeError):
    """Durable controller evidence failed strict replay."""


class SimulatedAdaptiveCrash(BaseException):
    """Deterministic crash injected after dispatch is durably recorded."""


class AdaptiveEventKind(str, Enum):
    RUNTIME_STARTED = "runtime.started"
    PROVIDER_429 = "provider.429"
    PROVIDER_RESET = "provider.reset"
    PROVIDER_CAPACITY = "provider.capacity"
    BUDGET_CUT = "budget.cut"
    TASK_DISPATCHED = "task.dispatched"
    USAGE_SETTLED = "usage.settled"
    CANCELLATION = "runtime.cancelled"
    UNKNOWN_INFLIGHT = "recovery.unknown_inflight"


class AdaptiveStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    REFUSED = "refused"
    CANCELLED = "cancelled"


class AdaptiveAction(str, Enum):
    INITIALIZE = "initialize"
    REPLAN = "replan"
    DISPATCH = "dispatch"
    SETTLE = "settle"
    RECOVER = "recover"
    CANCEL = "cancel"


_EVENT_DETAIL_FIELDS: Final[dict[AdaptiveEventKind, frozenset[str]]] = {
    AdaptiveEventKind.RUNTIME_STARTED: frozenset(),
    AdaptiveEventKind.PROVIDER_429: frozenset({"provider", "reset_at_ms"}),
    AdaptiveEventKind.PROVIDER_RESET: frozenset({"provider"}),
    AdaptiveEventKind.PROVIDER_CAPACITY: frozenset({"provider", "capacity"}),
    AdaptiveEventKind.BUDGET_CUT: _USAGE_FIELDS,
    AdaptiveEventKind.TASK_DISPATCHED: frozenset(
        {"task_id", "attempt", "provider", "backend", "reservation"}
    ),
    AdaptiveEventKind.USAGE_SETTLED: frozenset(
        {"task_id", "attempt", "actual_usage", "output_digest"}
    ),
    AdaptiveEventKind.CANCELLATION: frozenset({"reason"}),
    AdaptiveEventKind.UNKNOWN_INFLIGHT: frozenset({"task_id", "attempt", "reservation"}),
}


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _SHA256_CHARS for character in value)
    )


def _exact_mapping(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise AdaptiveReplayError(f"{label} must be an object with string keys")
    keys = set(value)
    if keys != fields:
        raise AdaptiveReplayError(
            f"{label} fields differ: unknown={sorted(keys - fields)}, "
            f"missing={sorted(fields - keys)}"
        )
    return value


def _strict_string(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise AdaptiveReplayError(f"{label} must be a non-empty string")
    return value


def _optional_digest(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not _is_digest(value):
        raise AdaptiveReplayError(f"{label} must be null or a lowercase SHA-256")
    return str(value)


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AdaptiveReplayError(f"{label} must be an integer >= {minimum}")
    return value


def _strict_list(value: object, label: str) -> list[object]:
    if type(value) is not list:
        raise AdaptiveReplayError(f"{label} must be an array")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    result = tuple(
        _strict_string(item, f"{label}[{index}]")
        for index, item in enumerate(_strict_list(value, label))
    )
    if result != tuple(sorted(set(result))):
        raise AdaptiveReplayError(f"{label} must be sorted and unique")
    return result


def _usage_from_mapping(value: object, label: str) -> Usage:
    mapping = _exact_mapping(value, _USAGE_FIELDS, label)
    return Usage(
        tokens=_strict_int(mapping["tokens"], f"{label}.tokens"),
        cost_microusd=_strict_int(mapping["cost_microusd"], f"{label}.cost_microusd"),
        context_bytes=_strict_int(mapping["context_bytes"], f"{label}.context_bytes"),
    )


def _usage_dict(value: Usage) -> dict[str, int]:
    return {
        "tokens": value.tokens,
        "cost_microusd": value.cost_microusd,
        "context_bytes": value.context_bytes,
    }


def _usage_add(*values: Usage) -> Usage:
    result = Usage()
    for value in values:
        result = result + value
    return result


def _usage_fits(value: Usage, cap: Usage) -> bool:
    return (
        value.tokens <= cap.tokens
        and value.cost_microusd <= cap.cost_microusd
        and value.context_bytes <= cap.context_bytes
    )


def _usage_subtract(cap: Usage, used: Usage) -> Usage:
    if not _usage_fits(used, cap):
        raise AdaptiveInvariantError("resource subtraction would become negative")
    return Usage(
        tokens=cap.tokens - used.tokens,
        cost_microusd=cap.cost_microusd - used.cost_microusd,
        context_bytes=cap.context_bytes - used.context_bytes,
    )


def _profile_usage(profile: BackendProfile) -> Usage:
    return Usage(
        tokens=profile.total_tokens,
        cost_microusd=profile.cost_microusd,
        context_bytes=profile.context_bytes,
    )


def _strict_canonical_json(value: object) -> str:
    try:
        return json.dumps(
            normalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AdaptiveInvariantError("value must be finite canonical JSON") from exc


def _canonical_profile(task: TaskContract) -> BackendProfile:
    qualified = [profile for profile in task.profiles if profile.quality >= task.min_quality]
    if not qualified:
        raise AdaptiveInvariantError(f"task {task.task_id!r} has no quality-qualified profile")
    return min(
        qualified,
        key=lambda profile: (
            profile.total_tokens,
            profile.cost_microusd,
            profile.context_bytes,
            profile.duration_ms_p95,
            profile.provider,
            profile.name,
        ),
    )


def plan_adaptive_admission(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
) -> tuple[dict[str, BackendProfile], tuple[str, ...]]:
    """Return the exact scheduler-admitted profile set for the live controller.

    The adaptive controller is deliberately single-flight.  In addition to the
    scheduler's parallel admission proof, verify that its own deterministic
    dispatch order meets every declared task and run deadline at p95.  Live
    control may temporarily block an admitted provider, but it may never switch
    to an unproved fallback profile.
    """

    admission = Scheduler().schedule(graph, envelope, SchedulePolicy.ADAPTIVE)
    if not admission.success:
        raise AdaptiveInvariantError(
            admission.failure_reason or "adaptive admission refused run"
        )
    by_id = graph.by_id
    profiles: dict[str, BackendProfile] = {}
    for entry in admission.entries:
        matches = tuple(
            profile
            for profile in by_id[entry.task_id].profiles
            if profile.name == entry.backend and profile.provider == entry.provider
        )
        if len(matches) != 1:
            raise AdaptiveInvariantError(
                f"admission selected unknown profile for task {entry.task_id!r}"
            )
        profiles[entry.task_id] = matches[0]
    skipped = tuple(sorted(admission.skipped))
    if set(profiles) != set(by_id) - set(skipped):
        raise AdaptiveInvariantError("adaptive admission produced an incomplete execution plan")

    pending = set(profiles)
    completed: set[str] = set()
    now_ms = 0
    while pending:
        ready = [
            by_id[task_id]
            for task_id in pending
            if set(by_id[task_id].dependencies) <= completed
        ]
        if not ready:
            raise AdaptiveInvariantError("adaptive single-flight plan has no ready task")
        task = min(ready, key=lambda item: (-item.value, item.task_id))
        now_ms += profiles[task.task_id].duration_ms_p95
        deadline_ms = min(task.deadline_ms or envelope.deadline_ms, envelope.deadline_ms)
        if now_ms > deadline_ms:
            raise AdaptiveInvariantError(
                f"adaptive single-flight admission misses deadline for task {task.task_id!r}"
            )
        pending.remove(task.task_id)
        completed.add(task.task_id)
    return profiles, skipped


@dataclass(frozen=True, slots=True)
class InflightReservation:
    task_id: str
    attempt: int
    provider: str
    backend: str
    reservation: Usage
    dispatch_event_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "attempt": self.attempt,
            "provider": self.provider,
            "backend": self.backend,
            "reservation": _usage_dict(self.reservation),
            "dispatch_event_digest": self.dispatch_event_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> InflightReservation:
        mapping = _exact_mapping(value, _INFLIGHT_FIELDS, "inflight reservation")
        digest = mapping["dispatch_event_digest"]
        if not _is_digest(digest):
            raise AdaptiveReplayError("inflight dispatch_event_digest is invalid")
        return cls(
            task_id=_strict_string(mapping["task_id"], "inflight.task_id"),
            attempt=_strict_int(mapping["attempt"], "inflight.attempt", minimum=1),
            provider=_strict_string(mapping["provider"], "inflight.provider"),
            backend=_strict_string(mapping["backend"], "inflight.backend"),
            reservation=_usage_from_mapping(mapping["reservation"], "inflight.reservation"),
            dispatch_event_digest=str(digest),
        )


@dataclass(frozen=True, slots=True)
class AdaptiveControlEvent:
    event_id: str
    kind: AdaptiveEventKind
    occurred_at_ms: int
    details: tuple[tuple[str, object], ...]
    event_digest: str

    @property
    def detail_map(self) -> dict[str, object]:
        return dict(self.details)

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": ADAPTIVE_EVENT_SCHEMA_VERSION,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "occurred_at_ms": self.occurred_at_ms,
            "details": normalize(self.detail_map),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "event_digest": self.event_digest}

    @classmethod
    def create(
        cls,
        event_id: str,
        kind: AdaptiveEventKind,
        occurred_at_ms: int,
        details: Mapping[str, object],
    ) -> AdaptiveControlEvent:
        if type(event_id) is not str or not event_id:
            raise AdaptiveInvariantError("control event_id is required")
        if type(kind) is not AdaptiveEventKind:
            raise AdaptiveInvariantError("control event kind must use the exact enum")
        if type(occurred_at_ms) is not int or occurred_at_ms < 0:
            raise AdaptiveInvariantError("control event time must be a non-negative integer")
        copied = json.loads(canonical_json(dict(details)))
        _validate_event_details(kind, copied, AdaptiveInvariantError)
        ordered = tuple(sorted(copied.items()))
        unsigned = {
            "schema_version": ADAPTIVE_EVENT_SCHEMA_VERSION,
            "event_id": event_id,
            "kind": kind.value,
            "occurred_at_ms": occurred_at_ms,
            "details": copied,
        }
        return cls(event_id, kind, occurred_at_ms, ordered, content_digest(unsigned))

    @classmethod
    def from_dict(cls, value: object) -> AdaptiveControlEvent:
        mapping = _exact_mapping(value, _EVENT_FIELDS, "control event")
        if mapping["schema_version"] != ADAPTIVE_EVENT_SCHEMA_VERSION:
            raise AdaptiveReplayError("unsupported adaptive event schema")
        try:
            kind = AdaptiveEventKind(_strict_string(mapping["kind"], "event.kind"))
        except ValueError as exc:
            raise AdaptiveReplayError("unknown adaptive event kind") from exc
        details = mapping["details"]
        if not isinstance(details, Mapping):
            raise AdaptiveReplayError("event.details must be an object")
        _validate_event_details(kind, details, AdaptiveReplayError)
        event = cls(
            event_id=_strict_string(mapping["event_id"], "event.event_id"),
            kind=kind,
            occurred_at_ms=_strict_int(mapping["occurred_at_ms"], "event.occurred_at_ms"),
            details=tuple(sorted(details.items())),
            event_digest=_strict_string(mapping["event_digest"], "event.event_digest"),
        )
        if not _is_digest(event.event_digest) or event.event_digest != content_digest(
            event.unsigned_payload()
        ):
            raise AdaptiveReplayError("control event digest verification failed")
        return event


def _validate_event_details(
    kind: AdaptiveEventKind,
    details: Mapping[str, object],
    error_type: type[Exception],
) -> None:
    expected = _EVENT_DETAIL_FIELDS[kind]
    if any(type(key) is not str for key in details) or set(details) != expected:
        raise error_type(
            f"{kind.value} details differ: unknown={sorted(set(details) - expected)}, "
            f"missing={sorted(expected - set(details))}"
        )

    def string(field: str) -> str:
        value = details[field]
        if type(value) is not str or not value:
            raise error_type(f"{kind.value}.{field} must be a non-empty string")
        return value

    def integer(field: str, *, minimum: int = 0) -> int:
        value = details[field]
        if type(value) is not int or value < minimum:
            raise error_type(f"{kind.value}.{field} must be an integer >= {minimum}")
        return value

    if kind in {
        AdaptiveEventKind.PROVIDER_429,
        AdaptiveEventKind.PROVIDER_RESET,
        AdaptiveEventKind.PROVIDER_CAPACITY,
    }:
        string("provider")
    if kind is AdaptiveEventKind.PROVIDER_429:
        integer("reset_at_ms", minimum=1)
    elif kind is AdaptiveEventKind.PROVIDER_CAPACITY:
        integer("capacity")
    elif kind is AdaptiveEventKind.BUDGET_CUT:
        try:
            _usage_from_mapping(details, "budget.cut")
        except AdaptiveReplayError as exc:
            raise error_type(str(exc)) from exc
    elif kind is AdaptiveEventKind.TASK_DISPATCHED:
        for field in ("task_id", "provider", "backend"):
            string(field)
        integer("attempt", minimum=1)
        try:
            _usage_from_mapping(details["reservation"], "dispatch.reservation")
        except AdaptiveReplayError as exc:
            raise error_type(str(exc)) from exc
    elif kind is AdaptiveEventKind.USAGE_SETTLED:
        string("task_id")
        integer("attempt", minimum=1)
        if not _is_digest(details["output_digest"]):
            raise error_type("usage.settled.output_digest must be a lowercase SHA-256")
        try:
            _usage_from_mapping(details["actual_usage"], "settlement.actual_usage")
        except AdaptiveReplayError as exc:
            raise error_type(str(exc)) from exc
    elif kind is AdaptiveEventKind.CANCELLATION:
        string("reason")
    elif kind is AdaptiveEventKind.UNKNOWN_INFLIGHT:
        string("task_id")
        integer("attempt", minimum=1)
        try:
            _usage_from_mapping(details["reservation"], "recovery.reservation")
        except AdaptiveReplayError as exc:
            raise error_type(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class AdaptiveState:
    run_id: str
    graph_digest: str
    revision: int
    prior_state_digest: str | None
    deadline_ms: int
    caps: Usage
    settled_usage: Usage
    unknown_usage: Usage
    completed_task_ids: tuple[str, ...]
    shed_task_ids: tuple[str, ...]
    unknown_task_ids: tuple[str, ...]
    inflight: tuple[InflightReservation, ...]
    provider_resets: tuple[tuple[str, int], ...]
    provider_capacities: tuple[tuple[str, int], ...]
    now_ms: int
    status: AdaptiveStatus
    state_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": ADAPTIVE_STATE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "graph_digest": self.graph_digest,
            "revision": self.revision,
            "prior_state_digest": self.prior_state_digest,
            "deadline_ms": self.deadline_ms,
            "caps": _usage_dict(self.caps),
            "settled_usage": _usage_dict(self.settled_usage),
            "unknown_usage": _usage_dict(self.unknown_usage),
            "completed_task_ids": list(self.completed_task_ids),
            "shed_task_ids": list(self.shed_task_ids),
            "unknown_task_ids": list(self.unknown_task_ids),
            "inflight": [item.as_dict() for item in self.inflight],
            "provider_resets": [
                {"provider": provider, "reset_at_ms": reset_at_ms}
                for provider, reset_at_ms in self.provider_resets
            ],
            "provider_capacities": [
                {"provider": provider, "capacity": capacity}
                for provider, capacity in self.provider_capacities
            ],
            "now_ms": self.now_ms,
            "status": self.status.value,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "state_digest": self.state_digest}

    def verify_digest(self) -> bool:
        return _is_digest(self.state_digest) and self.state_digest == content_digest(
            self.unsigned_payload()
        )

    @classmethod
    def from_dict(cls, value: object) -> AdaptiveState:
        mapping = _exact_mapping(value, _STATE_FIELDS, "adaptive state")
        if mapping["schema_version"] != ADAPTIVE_STATE_SCHEMA_VERSION:
            raise AdaptiveReplayError("unsupported adaptive state schema")
        graph_digest = mapping["graph_digest"]
        if not _is_digest(graph_digest):
            raise AdaptiveReplayError("state graph_digest is invalid")
        prior = _optional_digest(mapping["prior_state_digest"], "state.prior_state_digest")
        inflight = tuple(
            InflightReservation.from_dict(item)
            for item in _strict_list(mapping["inflight"], "state.inflight")
        )
        if len(inflight) > 1:
            raise AdaptiveReplayError("bounded adaptive state permits at most one in-flight task")
        resets: list[tuple[str, int]] = []
        for item in _strict_list(mapping["provider_resets"], "state.provider_resets"):
            record = _exact_mapping(item, _PROVIDER_RESET_FIELDS, "provider reset")
            resets.append(
                (
                    _strict_string(record["provider"], "provider reset name"),
                    _strict_int(record["reset_at_ms"], "provider reset time", minimum=1),
                )
            )
        capacities: list[tuple[str, int]] = []
        for item in _strict_list(mapping["provider_capacities"], "state.provider_capacities"):
            record = _exact_mapping(item, _PROVIDER_CAPACITY_FIELDS, "provider capacity")
            capacities.append(
                (
                    _strict_string(record["provider"], "provider capacity name"),
                    _strict_int(record["capacity"], "provider capacity"),
                )
            )
        if (
            resets != sorted(resets)
            or capacities != sorted(capacities)
            or len({provider for provider, _ in resets}) != len(resets)
            or len({provider for provider, _ in capacities}) != len(capacities)
        ):
            raise AdaptiveReplayError("provider state must be sorted and unique")
        try:
            status = AdaptiveStatus(_strict_string(mapping["status"], "state.status"))
        except ValueError as exc:
            raise AdaptiveReplayError("unknown adaptive state status") from exc
        state = cls(
            run_id=_strict_string(mapping["run_id"], "state.run_id"),
            graph_digest=str(graph_digest),
            revision=_strict_int(mapping["revision"], "state.revision"),
            prior_state_digest=prior,
            deadline_ms=_strict_int(mapping["deadline_ms"], "state.deadline_ms", minimum=1),
            caps=_usage_from_mapping(mapping["caps"], "state.caps"),
            settled_usage=_usage_from_mapping(mapping["settled_usage"], "state.settled_usage"),
            unknown_usage=_usage_from_mapping(mapping["unknown_usage"], "state.unknown_usage"),
            completed_task_ids=_string_tuple(
                mapping["completed_task_ids"], "state.completed_task_ids"
            ),
            shed_task_ids=_string_tuple(mapping["shed_task_ids"], "state.shed_task_ids"),
            unknown_task_ids=_string_tuple(mapping["unknown_task_ids"], "state.unknown_task_ids"),
            inflight=inflight,
            provider_resets=tuple(resets),
            provider_capacities=tuple(capacities),
            now_ms=_strict_int(mapping["now_ms"], "state.now_ms"),
            status=status,
            state_digest=_strict_string(mapping["state_digest"], "state.state_digest"),
        )
        if not state.verify_digest():
            raise AdaptiveReplayError("adaptive state digest verification failed")
        if set(state.completed_task_ids) & set(state.shed_task_ids):
            raise AdaptiveReplayError("completed and shed task identities overlap")
        if state.status in {AdaptiveStatus.RUNNING, AdaptiveStatus.COMPLETED} and not set(
            state.unknown_task_ids
        ) <= set(state.shed_task_ids):
            raise AdaptiveReplayError(
                "a nonterminal unknown task must be optional and durably shed"
            )
        return state


@dataclass(frozen=True, slots=True)
class AdaptiveDecision:
    revision: int
    event_id: str
    event_digest: str
    action: AdaptiveAction
    task_id: str | None
    eligible_task_ids: tuple[str, ...]
    protected_task_ids: tuple[str, ...]
    newly_shed_task_ids: tuple[str, ...]
    prior_state_digest: str
    next_state_digest: str
    status: AdaptiveStatus
    reason_code: str
    decision_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": ADAPTIVE_DECISION_SCHEMA_VERSION,
            "revision": self.revision,
            "event_id": self.event_id,
            "event_digest": self.event_digest,
            "action": self.action.value,
            "task_id": self.task_id,
            "eligible_task_ids": list(self.eligible_task_ids),
            "protected_task_ids": list(self.protected_task_ids),
            "newly_shed_task_ids": list(self.newly_shed_task_ids),
            "prior_state_digest": self.prior_state_digest,
            "next_state_digest": self.next_state_digest,
            "status": self.status.value,
            "reason_code": self.reason_code,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "decision_digest": self.decision_digest}

    def verify_digest(self) -> bool:
        return _is_digest(self.decision_digest) and self.decision_digest == content_digest(
            self.unsigned_payload()
        )

    @classmethod
    def from_dict(cls, value: object) -> AdaptiveDecision:
        mapping = _exact_mapping(value, _DECISION_FIELDS, "adaptive decision")
        if mapping["schema_version"] != ADAPTIVE_DECISION_SCHEMA_VERSION:
            raise AdaptiveReplayError("unsupported adaptive decision schema")
        digests = (
            mapping["event_digest"],
            mapping["prior_state_digest"],
            mapping["next_state_digest"],
            mapping["decision_digest"],
        )
        if not all(_is_digest(item) for item in digests):
            raise AdaptiveReplayError("decision contains an invalid digest")
        try:
            action = AdaptiveAction(_strict_string(mapping["action"], "decision.action"))
            status = AdaptiveStatus(_strict_string(mapping["status"], "decision.status"))
        except ValueError as exc:
            raise AdaptiveReplayError("decision enum value is invalid") from exc
        task_id = mapping["task_id"]
        if task_id is not None:
            task_id = _strict_string(task_id, "decision.task_id")
        decision = cls(
            revision=_strict_int(mapping["revision"], "decision.revision", minimum=1),
            event_id=_strict_string(mapping["event_id"], "decision.event_id"),
            event_digest=str(mapping["event_digest"]),
            action=action,
            task_id=task_id,
            eligible_task_ids=_string_tuple(
                mapping["eligible_task_ids"], "decision.eligible_task_ids"
            ),
            protected_task_ids=_string_tuple(
                mapping["protected_task_ids"], "decision.protected_task_ids"
            ),
            newly_shed_task_ids=_string_tuple(
                mapping["newly_shed_task_ids"], "decision.newly_shed_task_ids"
            ),
            prior_state_digest=str(mapping["prior_state_digest"]),
            next_state_digest=str(mapping["next_state_digest"]),
            status=status,
            reason_code=_strict_string(mapping["reason_code"], "decision.reason_code"),
            decision_digest=str(mapping["decision_digest"]),
        )
        if not decision.verify_digest():
            raise AdaptiveReplayError("adaptive decision digest verification failed")
        return decision


@dataclass(frozen=True, slots=True)
class AdaptiveControllerRecord:
    revision: int
    prior_state_digest: str
    event: AdaptiveControlEvent
    next_state: AdaptiveState
    decision: AdaptiveDecision
    record_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": ADAPTIVE_RUNTIME_SCHEMA_VERSION,
            "revision": self.revision,
            "prior_state_digest": self.prior_state_digest,
            "event": self.event.as_dict(),
            "next_state": self.next_state.as_dict(),
            "decision": self.decision.as_dict(),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "record_digest": self.record_digest}

    @classmethod
    def create(
        cls,
        prior_state: AdaptiveState,
        event: AdaptiveControlEvent,
        next_state: AdaptiveState,
        decision: AdaptiveDecision,
    ) -> AdaptiveControllerRecord:
        unsigned = {
            "schema_version": ADAPTIVE_RUNTIME_SCHEMA_VERSION,
            "revision": next_state.revision,
            "prior_state_digest": prior_state.state_digest,
            "event": event.as_dict(),
            "next_state": next_state.as_dict(),
            "decision": decision.as_dict(),
        }
        return cls(
            revision=next_state.revision,
            prior_state_digest=prior_state.state_digest,
            event=event,
            next_state=next_state,
            decision=decision,
            record_digest=content_digest(unsigned),
        )

    @classmethod
    def from_dict(cls, value: object) -> AdaptiveControllerRecord:
        mapping = _exact_mapping(value, _RECORD_FIELDS, "adaptive controller record")
        if mapping["schema_version"] != ADAPTIVE_RUNTIME_SCHEMA_VERSION:
            raise AdaptiveReplayError("unsupported adaptive controller schema")
        prior = mapping["prior_state_digest"]
        record_digest = mapping["record_digest"]
        if not _is_digest(prior) or not _is_digest(record_digest):
            raise AdaptiveReplayError("controller record digest field is invalid")
        record = cls(
            revision=_strict_int(mapping["revision"], "record.revision", minimum=1),
            prior_state_digest=str(prior),
            event=AdaptiveControlEvent.from_dict(mapping["event"]),
            next_state=AdaptiveState.from_dict(mapping["next_state"]),
            decision=AdaptiveDecision.from_dict(mapping["decision"]),
            record_digest=str(record_digest),
        )
        if record.record_digest != content_digest(record.unsigned_payload()):
            raise AdaptiveReplayError("controller record digest verification failed")
        return record


@dataclass(frozen=True, slots=True)
class AdaptiveTaskContext:
    run_id: str
    task_id: str
    attempt: int
    provider: str
    backend: str
    dependency_outputs: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AdaptiveWorkerResult:
    output: object
    actual_usage: Usage
    duration_ms: int = 1

    def __post_init__(self) -> None:
        if type(self.actual_usage) is not Usage:
            raise AdaptiveInvariantError("worker actual_usage must use the exact Usage contract")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise AdaptiveInvariantError("worker duration_ms must be a non-negative integer")
        _strict_canonical_json(self.output)


AdaptiveWorker = Callable[[AdaptiveTaskContext], AdaptiveWorkerResult]


@dataclass(frozen=True, slots=True)
class AdaptiveReplayViolation:
    index: int | None
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class AdaptiveReplayReport:
    passed: bool
    final_state: AdaptiveState | None
    control_digest: str
    record_count: int
    violations: tuple[AdaptiveReplayViolation, ...]


@dataclass(frozen=True, slots=True)
class AdaptiveRunResult:
    state: AdaptiveState
    outputs: Mapping[str, object]
    resumed_task_ids: tuple[str, ...]
    control_digest: str
    controller_records: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class AdaptiveRecoveryDrillResult:
    final_status: AdaptiveStatus
    control_digest: str
    replay_control_digest: str
    replay_passed: bool
    first_process_worker_calls: tuple[str, ...]
    restart_worker_calls: tuple[str, ...]
    resumed_task_ids: tuple[str, ...]
    unknown_task_ids: tuple[str, ...]
    shed_task_ids: tuple[str, ...]
    completed_task_ids: tuple[str, ...]
    provider_reset_honored: bool
    external_provider_calls: int
    controller_record_count: int


def _make_state(
    *,
    run_id: str,
    graph_digest: str,
    revision: int,
    prior_state_digest: str | None,
    deadline_ms: int,
    caps: Usage,
    settled_usage: Usage,
    unknown_usage: Usage,
    completed_task_ids: Iterable[str],
    shed_task_ids: Iterable[str],
    unknown_task_ids: Iterable[str],
    inflight: Iterable[InflightReservation],
    provider_resets: Mapping[str, int],
    provider_capacities: Mapping[str, int],
    now_ms: int,
    status: AdaptiveStatus,
) -> AdaptiveState:
    completed = tuple(sorted(set(completed_task_ids)))
    shed = tuple(sorted(set(shed_task_ids)))
    unknown = tuple(sorted(set(unknown_task_ids)))
    inflight_tuple = tuple(sorted(inflight, key=lambda item: item.task_id))
    resets = tuple(sorted(provider_resets.items()))
    capacities = tuple(sorted(provider_capacities.items()))
    unsigned = {
        "schema_version": ADAPTIVE_STATE_SCHEMA_VERSION,
        "run_id": run_id,
        "graph_digest": graph_digest,
        "revision": revision,
        "prior_state_digest": prior_state_digest,
        "deadline_ms": deadline_ms,
        "caps": _usage_dict(caps),
        "settled_usage": _usage_dict(settled_usage),
        "unknown_usage": _usage_dict(unknown_usage),
        "completed_task_ids": list(completed),
        "shed_task_ids": list(shed),
        "unknown_task_ids": list(unknown),
        "inflight": [item.as_dict() for item in inflight_tuple],
        "provider_resets": [
            {"provider": provider, "reset_at_ms": reset_at_ms} for provider, reset_at_ms in resets
        ],
        "provider_capacities": [
            {"provider": provider, "capacity": capacity} for provider, capacity in capacities
        ],
        "now_ms": now_ms,
        "status": status.value,
    }
    return AdaptiveState(
        run_id=run_id,
        graph_digest=graph_digest,
        revision=revision,
        prior_state_digest=prior_state_digest,
        deadline_ms=deadline_ms,
        caps=caps,
        settled_usage=settled_usage,
        unknown_usage=unknown_usage,
        completed_task_ids=completed,
        shed_task_ids=shed,
        unknown_task_ids=unknown,
        inflight=inflight_tuple,
        provider_resets=resets,
        provider_capacities=capacities,
        now_ms=now_ms,
        status=status,
        state_digest=content_digest(unsigned),
    )


class _AdaptiveControllerModel:
    def __init__(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        run_id: str,
        *,
        allow_write_effects: bool = False,
    ) -> None:
        _validate_runtime_inputs(
            graph,
            envelope,
            run_id,
            allow_write_effects=allow_write_effects,
        )
        self.graph = graph
        self.envelope = envelope
        self.run_id = run_id
        self.graph_digest = content_digest(graph)
        self.by_id = graph.by_id
        admitted, skipped = plan_adaptive_admission(graph, envelope)
        self.admitted_profiles = admitted
        self.admission_skipped = frozenset(skipped)
        self.providers = {profile.provider for profile in admitted.values()}
        self.protected = self._protected_task_ids()

    def _protected_task_ids(self) -> frozenset[str]:
        protected = {task.task_id for task in self.graph.tasks if not task.optional}
        stack = list(protected)
        while stack:
            for dependency in self.by_id[stack.pop()].dependencies:
                if dependency not in protected:
                    protected.add(dependency)
                    stack.append(dependency)
        return frozenset(protected)

    def initial_state(self) -> AdaptiveState:
        values: dict[str, object] = {
            "caps": Usage(
                tokens=self.envelope.max_tokens,
                cost_microusd=self.envelope.max_cost_microusd,
                context_bytes=self.envelope.max_context_bytes,
            ),
            "settled": Usage(),
            "unknown_usage": Usage(),
            "completed": set(),
            "shed": set(self.admission_skipped),
            "unknown_tasks": set(),
            "inflight": [],
            "resets": {},
            "capacities": {},
            "now_ms": 0,
            "status": AdaptiveStatus.RUNNING,
        }
        self._reconcile(values)
        return _make_state(
            run_id=self.run_id,
            graph_digest=self.graph_digest,
            revision=0,
            prior_state_digest=None,
            deadline_ms=self.envelope.deadline_ms,
            caps=values["caps"],  # type: ignore[arg-type]
            settled_usage=values["settled"],  # type: ignore[arg-type]
            unknown_usage=values["unknown_usage"],  # type: ignore[arg-type]
            completed_task_ids=values["completed"],  # type: ignore[arg-type]
            shed_task_ids=values["shed"],  # type: ignore[arg-type]
            unknown_task_ids=values["unknown_tasks"],  # type: ignore[arg-type]
            inflight=values["inflight"],  # type: ignore[arg-type]
            provider_resets=values["resets"],  # type: ignore[arg-type]
            provider_capacities=values["capacities"],  # type: ignore[arg-type]
            now_ms=0,
            status=values["status"],  # type: ignore[arg-type]
        )

    def _values(self, state: AdaptiveState) -> dict[str, object]:
        return {
            "caps": state.caps,
            "settled": state.settled_usage,
            "unknown_usage": state.unknown_usage,
            "completed": set(state.completed_task_ids),
            "shed": set(state.shed_task_ids),
            "unknown_tasks": set(state.unknown_task_ids),
            "inflight": list(state.inflight),
            "resets": dict(state.provider_resets),
            "capacities": dict(state.provider_capacities),
            "now_ms": state.now_ms,
            "status": state.status,
        }

    def _pending_mandatory_usage(
        self,
        completed: set[str],
        inflight: list[InflightReservation],
    ) -> Usage:
        inflight_ids = {item.task_id for item in inflight}
        return _usage_add(
            *(
                _profile_usage(self.admitted_profiles[task_id])
                for task_id in sorted(self.protected - completed - inflight_ids)
            )
        )

    @staticmethod
    def _inflight_usage(inflight: Iterable[InflightReservation]) -> Usage:
        return _usage_add(*(item.reservation for item in inflight))

    def _reconcile(self, values: dict[str, object]) -> None:
        status = values["status"]
        if status is AdaptiveStatus.CANCELLED:
            return
        completed = values["completed"]
        shed = values["shed"]
        inflight = values["inflight"]
        assert isinstance(completed, set)
        assert isinstance(shed, set)
        assert isinstance(inflight, list)
        caps = values["caps"]
        settled = values["settled"]
        unknown_usage = values["unknown_usage"]
        assert isinstance(caps, Usage)
        assert isinstance(settled, Usage)
        assert isinstance(unknown_usage, Usage)

        if set(self.protected) & shed:
            values["status"] = AdaptiveStatus.REFUSED
            return
        if int(values["now_ms"]) > self.envelope.deadline_ms and not self.protected <= completed:
            values["status"] = AdaptiveStatus.REFUSED
            return

        inflight_ids = {item.task_id for item in inflight}
        deadline_completed = completed | inflight_ids
        deadline_now_ms = int(values["now_ms"])
        for reservation in inflight:
            profile = self.admitted_profiles[reservation.task_id]
            deadline_now_ms += profile.duration_ms_p95
            task = self.by_id[reservation.task_id]
            task_deadline_ms = min(
                task.deadline_ms or self.envelope.deadline_ms,
                self.envelope.deadline_ms,
            )
            if deadline_now_ms > task_deadline_ms:
                values["status"] = AdaptiveStatus.REFUSED
                return
        pending_protected = self.protected - deadline_completed
        if not self._serial_deadlines_fit(
            completed=deadline_completed,
            included=pending_protected,
            now_ms=deadline_now_ms,
        ):
            shed.update(
                task_id
                for task_id in self.admitted_profiles
                if task_id not in self.protected and task_id not in completed
            )
            values["status"] = AdaptiveStatus.REFUSED
            return

        committed = _usage_add(settled, unknown_usage, self._inflight_usage(inflight))
        mandatory = self._pending_mandatory_usage(completed, inflight)
        if not _usage_fits(_usage_add(committed, mandatory), caps):
            shed.update(
                task.task_id
                for task in self.graph.tasks
                if task.task_id not in self.protected
                and task.task_id not in completed
                and task.task_id not in {item.task_id for item in inflight}
            )
            values["status"] = AdaptiveStatus.REFUSED
            return

        headroom = _usage_subtract(caps, _usage_add(committed, mandatory))
        optional_candidates = [
            task
            for task in self.graph.tasks
            if task.task_id in self.admitted_profiles
            and task.task_id not in self.protected
            and task.task_id not in completed
            and task.task_id not in shed
            and task.task_id not in {item.task_id for item in inflight}
        ]
        selected_usage = Usage()
        selected: set[str] = set()
        for task in sorted(optional_candidates, key=lambda item: (-item.value, item.task_id)):
            usage = _profile_usage(self.admitted_profiles[task.task_id])
            candidate = _usage_add(selected_usage, usage)
            if _usage_fits(candidate, headroom) and self._serial_deadlines_fit(
                completed=deadline_completed,
                included=pending_protected | selected | {task.task_id},
                now_ms=deadline_now_ms,
            ):
                selected.add(task.task_id)
                selected_usage = candidate
            else:
                shed.add(task.task_id)

        changed = True
        while changed:
            changed = False
            for task_id in tuple(selected):
                if set(self.by_id[task_id].dependencies) & shed:
                    selected.remove(task_id)
                    shed.add(task_id)
                    changed = True

        terminal = completed | shed
        if not inflight and set(self.by_id) <= terminal and self.protected <= completed:
            values["status"] = AdaptiveStatus.COMPLETED
        elif values["status"] is not AdaptiveStatus.REFUSED:
            values["status"] = AdaptiveStatus.RUNNING

    def _serial_deadlines_fit(
        self,
        *,
        completed: set[str],
        included: set[str] | frozenset[str],
        now_ms: int,
    ) -> bool:
        pending = set(included) - completed
        simulated_completed = set(completed)
        while pending:
            ready = [
                self.by_id[task_id]
                for task_id in pending
                if set(self.by_id[task_id].dependencies) <= simulated_completed
            ]
            if not ready:
                return False
            task = min(ready, key=lambda item: (-item.value, item.task_id))
            now_ms += self.admitted_profiles[task.task_id].duration_ms_p95
            deadline_ms = min(
                task.deadline_ms or self.envelope.deadline_ms,
                self.envelope.deadline_ms,
            )
            if now_ms > deadline_ms:
                return False
            pending.remove(task.task_id)
            simulated_completed.add(task.task_id)
        return True

    def _available_profile(self, task: TaskContract, state: AdaptiveState) -> BackendProfile | None:
        resets = dict(state.provider_resets)
        capacities = dict(state.provider_capacities)
        profile = self.admitted_profiles.get(task.task_id)
        if (
            profile is None
            or profile.provider in resets
            or capacities.get(
                profile.provider,
                self.envelope.provider_limit(profile.provider),
            )
            <= 0
        ):
            return None
        return profile

    def _dispatch_choices(
        self,
        state: AdaptiveState,
        *,
        occurred_at_ms: int | None = None,
    ) -> list[tuple[TaskContract, BackendProfile]]:
        if state.status is not AdaptiveStatus.RUNNING or state.inflight:
            return []
        dispatch_at_ms = state.now_ms if occurred_at_ms is None else occurred_at_ms
        if type(dispatch_at_ms) is not int or dispatch_at_ms < state.now_ms:
            return []
        completed = set(state.completed_task_ids)
        excluded = completed | set(state.shed_task_ids)
        committed = _usage_add(state.settled_usage, state.unknown_usage)
        choices: list[tuple[TaskContract, BackendProfile]] = []
        for task in self.graph.tasks:
            if task.task_id in excluded or not set(task.dependencies) <= completed:
                continue
            profile = self._available_profile(task, state)
            if profile is None:
                continue
            candidate_committed = _usage_add(committed, _profile_usage(profile))
            pending_protected = self.protected - completed - {task.task_id}
            protected_reserve = _usage_add(
                *(
                    _profile_usage(self.admitted_profiles[task_id])
                    for task_id in sorted(pending_protected)
                )
            )
            if not _usage_fits(_usage_add(candidate_committed, protected_reserve), state.caps):
                continue
            completed_at_ms = dispatch_at_ms + profile.duration_ms_p95
            task_deadline_ms = min(
                task.deadline_ms or self.envelope.deadline_ms,
                self.envelope.deadline_ms,
            )
            if completed_at_ms > task_deadline_ms:
                continue
            remaining = set(self.admitted_profiles) - excluded - {task.task_id}
            if self._serial_deadlines_fit(
                completed=completed | {task.task_id},
                included=remaining,
                now_ms=completed_at_ms,
            ):
                choices.append((task, profile))
        return choices

    def next_dispatch(
        self,
        state: AdaptiveState,
        *,
        occurred_at_ms: int | None = None,
    ) -> tuple[str, BackendProfile] | None:
        choices = self._dispatch_choices(state, occurred_at_ms=occurred_at_ms)
        if not choices:
            return None
        task, profile = min(choices, key=lambda item: (-item[0].value, item[0].task_id))
        return task.task_id, profile

    def eligible_task_ids(self, state: AdaptiveState) -> tuple[str, ...]:
        return tuple(sorted(task.task_id for task, _ in self._dispatch_choices(state)))

    def apply(
        self, prior: AdaptiveState, event: AdaptiveControlEvent
    ) -> tuple[AdaptiveState, AdaptiveDecision]:
        if not prior.verify_digest():
            raise AdaptiveInvariantError("prior adaptive state digest failed verification")
        if not _is_digest(event.event_digest) or event.event_digest != content_digest(
            event.unsigned_payload()
        ):
            raise AdaptiveInvariantError("control event digest failed verification")
        if prior.run_id != self.run_id or prior.graph_digest != self.graph_digest:
            raise AdaptiveInvariantError("adaptive state belongs to another run or graph")
        if event.occurred_at_ms < prior.now_ms:
            raise AdaptiveInvariantError("control event time cannot move backwards")
        if prior.status is not AdaptiveStatus.RUNNING and event.kind not in {
            AdaptiveEventKind.RUNTIME_STARTED
        }:
            raise AdaptiveInvariantError("terminal adaptive state rejects new control events")

        values = self._values(prior)
        values["now_ms"] = event.occurred_at_ms
        details = event.detail_map
        task_id: str | None = None
        action = AdaptiveAction.REPLAN
        reason = "control_applied"

        if event.kind is AdaptiveEventKind.RUNTIME_STARTED:
            if prior.revision != 0 or event.occurred_at_ms != 0:
                raise AdaptiveInvariantError("runtime.started is valid only at revision zero")
            action = AdaptiveAction.INITIALIZE
            reason = "controller_started"
        elif event.kind is AdaptiveEventKind.PROVIDER_429:
            provider = str(details["provider"])
            reset_at_ms = int(details["reset_at_ms"])
            self._require_provider(provider)
            if reset_at_ms <= event.occurred_at_ms:
                raise AdaptiveInvariantError("provider 429 reset must be later than the event")
            resets = values["resets"]
            assert isinstance(resets, dict)
            resets[provider] = max(int(resets.get(provider, 0)), reset_at_ms)
            reason = "provider_reset_window_applied"
        elif event.kind is AdaptiveEventKind.PROVIDER_RESET:
            provider = str(details["provider"])
            self._require_provider(provider)
            resets = values["resets"]
            assert isinstance(resets, dict)
            reset_at = resets.get(provider)
            if reset_at is None or event.occurred_at_ms < int(reset_at):
                raise AdaptiveInvariantError("provider reset arrived before its declared window")
            del resets[provider]
            reason = "provider_reset_window_cleared"
        elif event.kind is AdaptiveEventKind.PROVIDER_CAPACITY:
            provider = str(details["provider"])
            self._require_provider(provider)
            capacities = values["capacities"]
            assert isinstance(capacities, dict)
            capacities[provider] = int(details["capacity"])
            reason = "provider_capacity_changed"
        elif event.kind is AdaptiveEventKind.BUDGET_CUT:
            next_caps = _usage_from_mapping(details, "budget cut")
            current_caps = values["caps"]
            assert isinstance(current_caps, Usage)
            if not _usage_fits(next_caps, current_caps):
                raise AdaptiveInvariantError("a budget cut cannot increase any resource cap")
            values["caps"] = next_caps
            reason = "budget_cut_applied"
        elif event.kind is AdaptiveEventKind.TASK_DISPATCHED:
            action = AdaptiveAction.DISPATCH
            expected = self.next_dispatch(prior, occurred_at_ms=event.occurred_at_ms)
            if expected is None:
                raise AdaptiveInvariantError("no task is currently eligible for dispatch")
            expected_task_id, profile = expected
            task_id = str(details["task_id"])
            reservation = _usage_from_mapping(details["reservation"], "dispatch reservation")
            if (
                task_id != expected_task_id
                or details["provider"] != profile.provider
                or details["backend"] != profile.name
                or int(details["attempt"]) != 1
                or reservation != _profile_usage(profile)
            ):
                raise AdaptiveInvariantError("dispatch event differs from deterministic choice")
            values["inflight"] = [
                InflightReservation(
                    task_id=task_id,
                    attempt=1,
                    provider=profile.provider,
                    backend=profile.name,
                    reservation=reservation,
                    dispatch_event_digest=event.event_digest,
                )
            ]
            reason = "task_dispatched_with_protected_budget"
        elif event.kind is AdaptiveEventKind.USAGE_SETTLED:
            action = AdaptiveAction.SETTLE
            task_id = str(details["task_id"])
            inflight = values["inflight"]
            assert isinstance(inflight, list)
            if len(inflight) != 1:
                raise AdaptiveInvariantError("settlement requires exactly one in-flight task")
            reservation = inflight[0]
            actual = _usage_from_mapping(details["actual_usage"], "settled usage")
            if (
                reservation.task_id != task_id
                or reservation.attempt != int(details["attempt"])
                or not _usage_fits(actual, reservation.reservation)
            ):
                raise AdaptiveInvariantError("settlement exceeds or mismatches its reservation")
            completed = values["completed"]
            assert isinstance(completed, set)
            completed.add(task_id)
            settled = values["settled"]
            assert isinstance(settled, Usage)
            values["settled"] = _usage_add(settled, actual)
            values["inflight"] = []
            reason = "usage_settled_and_task_completed"
        elif event.kind is AdaptiveEventKind.UNKNOWN_INFLIGHT:
            action = AdaptiveAction.RECOVER
            task_id = str(details["task_id"])
            inflight = values["inflight"]
            assert isinstance(inflight, list)
            if len(inflight) != 1:
                raise AdaptiveInvariantError("unknown recovery requires one in-flight task")
            reservation = inflight[0]
            supplied = _usage_from_mapping(details["reservation"], "unknown reservation")
            if (
                reservation.task_id != task_id
                or reservation.attempt != int(details["attempt"])
                or supplied != reservation.reservation
            ):
                raise AdaptiveInvariantError("unknown recovery does not bind the in-flight lease")
            unknown_usage = values["unknown_usage"]
            assert isinstance(unknown_usage, Usage)
            values["unknown_usage"] = _usage_add(unknown_usage, reservation.reservation)
            values["inflight"] = []
            unknown_tasks = values["unknown_tasks"]
            shed = values["shed"]
            assert isinstance(unknown_tasks, set)
            assert isinstance(shed, set)
            unknown_tasks.add(task_id)
            if task_id in self.protected:
                values["status"] = AdaptiveStatus.REFUSED
            else:
                shed.add(task_id)
            reason = "unknown_inflight_charged_at_full_reservation"
        elif event.kind is AdaptiveEventKind.CANCELLATION:
            action = AdaptiveAction.CANCEL
            inflight = values["inflight"]
            assert isinstance(inflight, list)
            unknown_usage = values["unknown_usage"]
            assert isinstance(unknown_usage, Usage)
            values["unknown_usage"] = _usage_add(unknown_usage, self._inflight_usage(inflight))
            unknown_tasks = values["unknown_tasks"]
            shed = values["shed"]
            assert isinstance(unknown_tasks, set)
            assert isinstance(shed, set)
            for reservation in inflight:
                unknown_tasks.add(reservation.task_id)
                if reservation.task_id not in self.protected:
                    shed.add(reservation.task_id)
            values["inflight"] = []
            values["status"] = AdaptiveStatus.CANCELLED
            reason = "cancellation_stopped_future_dispatch"
        else:  # pragma: no cover - exhaustive enum guard
            raise AdaptiveInvariantError("unsupported adaptive control event")

        prior_shed = set(prior.shed_task_ids)
        self._reconcile(values)
        next_state = _make_state(
            run_id=self.run_id,
            graph_digest=self.graph_digest,
            revision=prior.revision + 1,
            prior_state_digest=prior.state_digest,
            deadline_ms=self.envelope.deadline_ms,
            caps=values["caps"],  # type: ignore[arg-type]
            settled_usage=values["settled"],  # type: ignore[arg-type]
            unknown_usage=values["unknown_usage"],  # type: ignore[arg-type]
            completed_task_ids=values["completed"],  # type: ignore[arg-type]
            shed_task_ids=values["shed"],  # type: ignore[arg-type]
            unknown_task_ids=values["unknown_tasks"],  # type: ignore[arg-type]
            inflight=values["inflight"],  # type: ignore[arg-type]
            provider_resets=values["resets"],  # type: ignore[arg-type]
            provider_capacities=values["capacities"],  # type: ignore[arg-type]
            now_ms=int(values["now_ms"]),
            status=values["status"],  # type: ignore[arg-type]
        )
        newly_shed = tuple(sorted(set(next_state.shed_task_ids) - prior_shed))
        if next_state.status is AdaptiveStatus.REFUSED:
            reason = "mandatory_protection_failed"
        elif next_state.status is AdaptiveStatus.COMPLETED:
            reason = "all_admitted_work_reached_a_terminal_fact"
        unsigned_decision = {
            "schema_version": ADAPTIVE_DECISION_SCHEMA_VERSION,
            "revision": next_state.revision,
            "event_id": event.event_id,
            "event_digest": event.event_digest,
            "action": action.value,
            "task_id": task_id,
            "eligible_task_ids": list(self.eligible_task_ids(next_state)),
            "protected_task_ids": sorted(self.protected),
            "newly_shed_task_ids": list(newly_shed),
            "prior_state_digest": prior.state_digest,
            "next_state_digest": next_state.state_digest,
            "status": next_state.status.value,
            "reason_code": reason,
        }
        decision = AdaptiveDecision(
            revision=next_state.revision,
            event_id=event.event_id,
            event_digest=event.event_digest,
            action=action,
            task_id=task_id,
            eligible_task_ids=self.eligible_task_ids(next_state),
            protected_task_ids=tuple(sorted(self.protected)),
            newly_shed_task_ids=newly_shed,
            prior_state_digest=prior.state_digest,
            next_state_digest=next_state.state_digest,
            status=next_state.status,
            reason_code=reason,
            decision_digest=content_digest(unsigned_decision),
        )
        return next_state, decision

    def _require_provider(self, provider: str) -> None:
        if provider not in self.providers:
            raise AdaptiveInvariantError(f"control event names unknown provider {provider!r}")


def _validate_runtime_inputs(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    run_id: str,
    *,
    allow_write_effects: bool = False,
) -> None:
    if type(graph) is not ExecutionGraph or type(graph.tasks) is not tuple:
        raise AdaptiveInvariantError("graph must use the exact immutable ExecutionGraph contract")
    if type(envelope) is not RunEnvelope:
        raise AdaptiveInvariantError("envelope must use the exact RunEnvelope contract")
    if type(run_id) is not str or not run_id:
        raise AdaptiveInvariantError("run_id is required")
    graph.validate()
    errors = envelope.validate()
    if errors:
        raise AdaptiveInvariantError("invalid run envelope: " + "; ".join(errors))
    envelope_values = (
        envelope.deadline_ms,
        envelope.max_tokens,
        envelope.max_cost_microusd,
        envelope.max_context_bytes,
        envelope.max_parallelism,
    )
    if any(type(value) is not int for value in envelope_values):
        raise AdaptiveInvariantError("envelope integer fields reject booleans and floats")
    for task in graph.tasks:
        if type(task) is not TaskContract or type(task.profiles) is not tuple:
            raise AdaptiveInvariantError("runtime tasks and profiles must use exact contracts")
        if not math.isfinite(task.value):
            raise AdaptiveInvariantError("task value must be finite")
        if task.effect.kind not in {EffectClass.PURE, EffectClass.READ} and not allow_write_effects:
            raise AdaptiveInvariantError(
                "adaptive fixture runtime refuses write effects; use the durable effect kernel"
            )
        for profile in task.profiles:
            if type(profile) is not BackendProfile:
                raise AdaptiveInvariantError("runtime profiles must use exact contracts")
        _canonical_profile(task)


def replay_adaptive_records(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    *,
    run_id: str,
    records: Iterable[Mapping[str, object]],
) -> AdaptiveReplayReport:
    """Replay controller records without invoking a worker or provider."""

    # Replay never executes an effect. It can therefore verify controller
    # records for effect-bearing graphs without needing a broker or adapter.
    model = _AdaptiveControllerModel(graph, envelope, run_id, allow_write_effects=True)
    state = model.initial_state()
    seen_event_ids: set[str] = set()
    record_digests: list[str] = []
    count = 0
    try:
        for count, supplied in enumerate(records, start=1):
            snapshot = json.loads(canonical_json(dict(supplied)))
            record = AdaptiveControllerRecord.from_dict(snapshot)
            if record.revision != count:
                raise AdaptiveReplayError(f"controller revision is non-monotonic: expected {count}")
            if record.event.event_id in seen_event_ids:
                raise AdaptiveReplayError("controller event IDs must be unique")
            seen_event_ids.add(record.event.event_id)
            if record.prior_state_digest != state.state_digest:
                raise AdaptiveReplayError("controller prior-state chain is broken")
            expected_state, expected_decision = model.apply(state, record.event)
            if record.next_state.as_dict() != expected_state.as_dict():
                raise AdaptiveReplayError("recorded next state differs from pure replay")
            if record.decision.as_dict() != expected_decision.as_dict():
                raise AdaptiveReplayError("recorded decision differs from pure replay")
            if record.decision.next_state_digest != record.next_state.state_digest:
                raise AdaptiveReplayError("decision is not bound to its next state")
            state = record.next_state
            record_digests.append(record.record_digest)
    except (AdaptiveReplayError, TypeError, ValueError) as exc:
        return AdaptiveReplayReport(
            passed=False,
            final_state=None,
            control_digest=content_digest(record_digests),
            record_count=count,
            violations=(AdaptiveReplayViolation(count or None, "replay_refused", str(exc)),),
        )
    return AdaptiveReplayReport(
        passed=bool(record_digests),
        final_state=state,
        control_digest=content_digest(record_digests),
        record_count=count,
        violations=(),
    )


class AdaptiveRuntime:
    """Small active controller whose complete decision history is replayable."""

    def __init__(
        self,
        store: SQLiteRunStore,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        workers: Mapping[str, AdaptiveWorker],
        effect_broker: SQLiteEffectBroker | None = None,
        crash_after_dispatch_task_ids: Iterable[str] = (),
    ) -> None:
        self.store = store
        self.graph = graph
        self.envelope = envelope
        self.run_id = run_id
        self._workers = dict(workers)
        self._effect_broker = effect_broker
        self._crash_after_dispatch = set(crash_after_dispatch_task_ids)
        self._model = _AdaptiveControllerModel(
            graph,
            envelope,
            run_id,
            allow_write_effects=effect_broker is not None,
        )
        graph_digest = self._model.graph_digest
        envelope_record = {
            "runtime_schema": ADAPTIVE_RUNTIME_SCHEMA_VERSION,
            "deadline_ms": envelope.deadline_ms,
            "max_tokens": envelope.max_tokens,
            "max_cost_microusd": envelope.max_cost_microusd,
            "max_context_bytes": envelope.max_context_bytes,
            "max_parallelism": envelope.max_parallelism,
            "provider_limits": [list(item) for item in sorted(envelope.provider_limits)],
        }
        self.store.get_or_create_run(
            run_id=run_id,
            graph_digest=graph_digest,
            envelope=envelope_record,
            deadline_at_ms=self.store.now_ms + envelope.deadline_ms,
            manifest_digest=content_digest(
                {
                    "runtime_schema": ADAPTIVE_RUNTIME_SCHEMA_VERSION,
                    "graph_digest": graph_digest,
                    "scope": ADAPTIVE_RUNTIME_SCOPE,
                    "limitations": ADAPTIVE_RUNTIME_LIMITATIONS,
                }
            ),
            manifest_revision=1,
        )
        existing = self._load_record_payloads()
        self.resumed_task_ids = tuple(sorted(self.store.completed_tasks(run_id)))
        if existing:
            replay = replay_adaptive_records(graph, envelope, run_id=run_id, records=existing)
            if not replay.passed or replay.final_state is None:
                detail = replay.violations[0].detail if replay.violations else "unknown"
                raise AdaptiveReplayError(f"stored controller history failed replay: {detail}")
            self._records = list(existing)
            self.state = replay.final_state
            durable_ids = set(self.store.completed_tasks(run_id))
            state_completed = set(self.state.completed_task_ids)
            inflight_ids = {item.task_id for item in self.state.inflight}
            if not state_completed <= durable_ids or not durable_ids <= (
                state_completed | inflight_ids
            ):
                raise AdaptiveReplayError(
                    "controller completion facts disagree with durable task outputs"
                )
        else:
            self._records: list[dict[str, object]] = []
            self.state = self._model.initial_state()
            started = AdaptiveControlEvent.create(
                f"{run_id}:runtime-started",
                AdaptiveEventKind.RUNTIME_STARTED,
                0,
                {},
            )
            self._append_transition(started)

    def _load_record_payloads(self) -> tuple[dict[str, object], ...]:
        return tuple(
            event.payload
            for event in self.store.events(self.run_id)
            if event.event_type == "adaptive.controller_transition"
        )

    def _append_transition(self, event: AdaptiveControlEvent) -> AdaptiveDecision:
        if event.event_id in {str(record["event"]["event_id"]) for record in self._records}:
            raise AdaptiveInvariantError(f"control event {event.event_id!r} already exists")
        next_state, decision = self._model.apply(self.state, event)
        record = AdaptiveControllerRecord.create(self.state, event, next_state, decision)
        self.store.append_event(
            run_id=self.run_id,
            event_id=f"{self.run_id}:controller:{record.revision:06d}",
            event_type="adaptive.controller_transition",
            payload=record.as_dict(),
        )
        self._records.append(record.as_dict())
        self.state = next_state
        return decision

    def _event_id(self, kind: AdaptiveEventKind) -> str:
        return f"{self.run_id}:{kind.value}:{self.state.revision + 1:06d}"

    def provider_429(
        self, provider: str, *, occurred_at_ms: int, reset_at_ms: int
    ) -> AdaptiveDecision:
        return self._append_transition(
            AdaptiveControlEvent.create(
                self._event_id(AdaptiveEventKind.PROVIDER_429),
                AdaptiveEventKind.PROVIDER_429,
                occurred_at_ms,
                {"provider": provider, "reset_at_ms": reset_at_ms},
            )
        )

    def provider_reset(self, provider: str, *, occurred_at_ms: int) -> AdaptiveDecision:
        return self._append_transition(
            AdaptiveControlEvent.create(
                self._event_id(AdaptiveEventKind.PROVIDER_RESET),
                AdaptiveEventKind.PROVIDER_RESET,
                occurred_at_ms,
                {"provider": provider},
            )
        )

    def provider_capacity(
        self, provider: str, capacity: int, *, occurred_at_ms: int
    ) -> AdaptiveDecision:
        return self._append_transition(
            AdaptiveControlEvent.create(
                self._event_id(AdaptiveEventKind.PROVIDER_CAPACITY),
                AdaptiveEventKind.PROVIDER_CAPACITY,
                occurred_at_ms,
                {"provider": provider, "capacity": capacity},
            )
        )

    def cut_budget(self, caps: Usage, *, occurred_at_ms: int) -> AdaptiveDecision:
        if type(caps) is not Usage:
            raise AdaptiveInvariantError("budget caps must use the exact Usage contract")
        return self._append_transition(
            AdaptiveControlEvent.create(
                self._event_id(AdaptiveEventKind.BUDGET_CUT),
                AdaptiveEventKind.BUDGET_CUT,
                occurred_at_ms,
                _usage_dict(caps),
            )
        )

    def cancel(self, reason: str, *, occurred_at_ms: int) -> AdaptiveDecision:
        return self._append_transition(
            AdaptiveControlEvent.create(
                self._event_id(AdaptiveEventKind.CANCELLATION),
                AdaptiveEventKind.CANCELLATION,
                occurred_at_ms,
                {"reason": reason},
            )
        )

    def dispatch_next(self, *, occurred_at_ms: int) -> str | None:
        choice = self._model.next_dispatch(self.state, occurred_at_ms=occurred_at_ms)
        if choice is None:
            return None
        task_id, profile = choice
        reservation = _profile_usage(profile)
        event = AdaptiveControlEvent.create(
            self._event_id(AdaptiveEventKind.TASK_DISPATCHED),
            AdaptiveEventKind.TASK_DISPATCHED,
            occurred_at_ms,
            {
                "task_id": task_id,
                "attempt": 1,
                "provider": profile.provider,
                "backend": profile.name,
                "reservation": _usage_dict(reservation),
            },
        )
        self._append_transition(event)
        started = self.store.start_attempt(
            run_id=self.run_id,
            task_id=task_id,
            provider=profile.provider,
            backend=profile.name,
            estimated=reservation,
            reserved=reservation,
        )
        if started.attempt != 1:
            raise AdaptiveInvariantError("bounded runtime refuses an implicit retry attempt")
        if task_id in self._crash_after_dispatch:
            self._crash_after_dispatch.remove(task_id)
            raise SimulatedAdaptiveCrash(
                f"simulated coordinator crash after dispatching {task_id!r}"
            )
        completed_outputs = {
            key: value.output for key, value in self.store.completed_tasks(self.run_id).items()
        }
        task = self.graph.by_id[task_id]
        if task.effect.kind.writes:
            broker = self._effect_broker
            if broker is None:  # pragma: no cover - rejected during model construction
                self.recover_unknown_inflight(occurred_at_ms=occurred_at_ms)
                raise AdaptiveInvariantError("write effect has no durable broker")
            try:
                intent = broker.propose(
                    run_id=self.run_id,
                    action=task_id,
                    resource=task.effect.resource,
                    effect_class=task.effect.kind,
                    idempotency_key=scoped_effect_idempotency_key(
                        run_id=self.run_id,
                        task_id=task.task_id,
                        attempt=started.attempt,
                        declared_key=task.effect.idempotency_key,
                    ),
                    payload={
                        "task_id": task_id,
                        "declared_idempotency_key": task.effect.idempotency_key,
                        "dependency_outputs": {
                            key: completed_outputs[key] for key in task.dependencies
                        },
                        "fixture_only": True,
                    },
                    compensation_action=task.effect.compensation,
                )
                result = AdaptiveWorkerResult(
                    output={
                        "effect_intent_id": intent.intent_id,
                        "effect_state": intent.state.value,
                        "declared_idempotency_key": task.effect.idempotency_key,
                        "executed_externally": False,
                    },
                    actual_usage=Usage(),
                    duration_ms=0,
                )
            except Exception:
                self.recover_unknown_inflight(occurred_at_ms=occurred_at_ms)
                raise
            output_kind = "effect_intent"
        else:
            worker = self._workers.get(task_id)
            if worker is None:
                self.recover_unknown_inflight(occurred_at_ms=occurred_at_ms)
                raise AdaptiveInvariantError(f"task {task_id!r} has no local worker")
            context = AdaptiveTaskContext(
                run_id=self.run_id,
                task_id=task_id,
                attempt=1,
                provider=profile.provider,
                backend=profile.name,
                dependency_outputs={key: completed_outputs[key] for key in task.dependencies},
            )
            try:
                result = worker(context)
            except Exception:
                self.recover_unknown_inflight(occurred_at_ms=occurred_at_ms)
                raise
            output_kind = "adaptive_fixture_output"
        if type(result) is not AdaptiveWorkerResult:
            self.recover_unknown_inflight(occurred_at_ms=occurred_at_ms)
            raise AdaptiveInvariantError("worker must return AdaptiveWorkerResult")
        if not _usage_fits(result.actual_usage, reservation):
            self.recover_unknown_inflight(occurred_at_ms=occurred_at_ms + result.duration_ms)
            raise AdaptiveInvariantError("worker actual usage exceeds its dispatch reservation")
        settled_at_ms = occurred_at_ms + result.duration_ms
        task_deadline_ms = min(
            task.deadline_ms or self.envelope.deadline_ms,
            self.envelope.deadline_ms,
        )
        if settled_at_ms > task_deadline_ms:
            self.recover_unknown_inflight(occurred_at_ms=settled_at_ms)
            raise AdaptiveInvariantError(
                f"task {task_id!r} completed after its declared deadline"
            )
        self.store.complete_attempt(
            run_id=self.run_id,
            task_id=task_id,
            attempt=1,
            output=result.output,
            estimated=reservation,
            reserved=reservation,
            actual=result.actual_usage,
            output_kind=output_kind,
        )
        settlement = AdaptiveControlEvent.create(
            self._event_id(AdaptiveEventKind.USAGE_SETTLED),
            AdaptiveEventKind.USAGE_SETTLED,
            settled_at_ms,
            {
                "task_id": task_id,
                "attempt": 1,
                "actual_usage": _usage_dict(result.actual_usage),
                "output_digest": content_digest(result.output),
            },
        )
        self._append_transition(settlement)
        return task_id

    def recover_unknown_inflight(self, *, occurred_at_ms: int) -> AdaptiveDecision | None:
        if not self.state.inflight:
            return None
        reservation = self.state.inflight[0]
        durable = self.store.completed_tasks(self.run_id).get(reservation.task_id)
        if durable is not None:
            settlement = AdaptiveControlEvent.create(
                self._event_id(AdaptiveEventKind.USAGE_SETTLED),
                AdaptiveEventKind.USAGE_SETTLED,
                occurred_at_ms,
                {
                    "task_id": reservation.task_id,
                    "attempt": reservation.attempt,
                    "actual_usage": _usage_dict(durable.event.usage.actual),
                    "output_digest": content_digest(durable.output),
                },
            )
            return self._append_transition(settlement)
        recovery = AdaptiveControlEvent.create(
            self._event_id(AdaptiveEventKind.UNKNOWN_INFLIGHT),
            AdaptiveEventKind.UNKNOWN_INFLIGHT,
            occurred_at_ms,
            {
                "task_id": reservation.task_id,
                "attempt": reservation.attempt,
                "reservation": _usage_dict(reservation.reservation),
            },
        )
        return self._append_transition(recovery)

    def run_until_blocked(
        self,
        *,
        start_at_ms: int | None = None,
        max_dispatches: int = 100,
    ) -> AdaptiveRunResult:
        if type(max_dispatches) is not int or max_dispatches <= 0:
            raise AdaptiveInvariantError("max_dispatches must be a positive integer")
        next_time = self.state.now_ms if start_at_ms is None else start_at_ms
        if type(next_time) is not int or next_time < self.state.now_ms:
            raise AdaptiveInvariantError("run loop time cannot move backwards")
        self.recover_unknown_inflight(occurred_at_ms=next_time)
        dispatch_count = 0
        while self.state.status is AdaptiveStatus.RUNNING and dispatch_count < max_dispatches:
            task_id = self.dispatch_next(occurred_at_ms=max(next_time, self.state.now_ms))
            if task_id is None:
                break
            dispatch_count += 1
            next_time = self.state.now_ms + 1
        if dispatch_count == max_dispatches and self.state.status is AdaptiveStatus.RUNNING:
            raise AdaptiveInvariantError("bounded dispatch limit reached before terminal state")
        return self.result()

    @property
    def control_digest(self) -> str:
        return content_digest([record["record_digest"] for record in self._records])

    @property
    def controller_records(self) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(canonical_json(record)) for record in self._records)

    def result(self) -> AdaptiveRunResult:
        outputs = {
            task_id: completed.output
            for task_id, completed in sorted(self.store.completed_tasks(self.run_id).items())
        }
        return AdaptiveRunResult(
            state=self.state,
            outputs=outputs,
            resumed_task_ids=self.resumed_task_ids,
            control_digest=self.control_digest,
            controller_records=self.controller_records,
        )


def _drill_profile(
    name: str,
    provider: str,
    *,
    tokens: int,
    cost: int,
    context: int,
) -> BackendProfile:
    return BackendProfile(
        name=name,
        provider=provider,
        duration_ms_p50=1,
        duration_ms_p95=2,
        input_tokens=tokens,
        output_tokens=0,
        cost_microusd=cost,
        context_bytes=context,
        quality=1.0,
    )


def adaptive_recovery_drill_graph() -> ExecutionGraph:
    """Return the deterministic local-only graph used by the recovery proof."""

    core = _drill_profile("core-fixture", "core", tokens=10, cost=10, context=10)
    enrich = _drill_profile("enrich-fixture", "core", tokens=20, cost=20, context=20)
    burst = _drill_profile("burst-fixture", "burst", tokens=30, cost=30, context=30)
    return ExecutionGraph.from_tasks(
        (
            TaskContract("intake", (core,), value=5.0),
            TaskContract("assessment", (core,), ("intake",), value=5.0),
            TaskContract("mandatory_alert", (core,), ("assessment",), value=1.0),
            TaskContract(
                "optional_enrichment",
                (enrich,),
                ("assessment",),
                optional=True,
                value=10.0,
            ),
            TaskContract(
                "optional_social",
                (burst,),
                ("assessment",),
                optional=True,
                value=2.0,
            ),
        )
    )


def adaptive_recovery_drill_envelope() -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=100,
        max_tokens=100,
        max_cost_microusd=100,
        max_context_bytes=100,
        max_parallelism=1,
        provider_limits=(("burst", 1), ("core", 1)),
    )


def run_adaptive_recovery_drill(database_path: str | Path) -> AdaptiveRecoveryDrillResult:
    """Execute the deterministic crash/restart/replay proof without network calls."""

    graph = adaptive_recovery_drill_graph()
    envelope = adaptive_recovery_drill_envelope()
    store = SQLiteRunStore(database_path, clock_ms=lambda: 1_000)
    first_calls: list[str] = []
    restart_calls: list[str] = []

    def worker_log(target: list[str]) -> dict[str, AdaptiveWorker]:
        def make(task_id: str) -> AdaptiveWorker:
            def worker(context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
                target.append(context.task_id)
                profile = _canonical_profile(graph.by_id[task_id])
                return AdaptiveWorkerResult(
                    output={"task_id": task_id, "source": "deterministic-local-fixture"},
                    actual_usage=_profile_usage(profile),
                    duration_ms=1,
                )

            return worker

        return {task.task_id: make(task.task_id) for task in graph.tasks}

    runtime = AdaptiveRuntime(
        store,
        graph,
        envelope,
        run_id="adaptive-recovery-drill-v1",
        workers=worker_log(first_calls),
        crash_after_dispatch_task_ids=("optional_enrichment",),
    )
    assert runtime.dispatch_next(occurred_at_ms=1) == "intake"
    assert runtime.dispatch_next(occurred_at_ms=3) == "assessment"
    blocked = runtime.provider_429("burst", occurred_at_ms=5, reset_at_ms=8)
    if "optional_social" in blocked.eligible_task_ids:
        raise RuntimeError("429 failed to suppress the burst-provider optional task")
    cut = runtime.cut_budget(Usage(tokens=55, cost_microusd=55, context_bytes=55), occurred_at_ms=6)
    if cut.newly_shed_task_ids != ("optional_social",):
        raise RuntimeError("budget cut did not shed only the unaffordable optional task")
    runtime.provider_capacity("burst", 0, occurred_at_ms=7)
    runtime.provider_reset("burst", occurred_at_ms=8)
    runtime.provider_capacity("burst", 1, occurred_at_ms=8)
    try:
        runtime.dispatch_next(occurred_at_ms=9)
    except SimulatedAdaptiveCrash:
        pass
    else:  # pragma: no cover - internal proof guard
        raise RuntimeError("adaptive recovery drill did not inject its crash")

    restarted = AdaptiveRuntime(
        SQLiteRunStore(database_path, clock_ms=lambda: 1_000),
        graph,
        envelope,
        run_id="adaptive-recovery-drill-v1",
        workers=worker_log(restart_calls),
    )
    result = restarted.run_until_blocked(start_at_ms=10, max_dispatches=10)
    replay = replay_adaptive_records(
        graph,
        envelope,
        run_id="adaptive-recovery-drill-v1",
        records=result.controller_records,
    )
    return AdaptiveRecoveryDrillResult(
        final_status=result.state.status,
        control_digest=result.control_digest,
        replay_control_digest=replay.control_digest,
        replay_passed=replay.passed,
        first_process_worker_calls=tuple(first_calls),
        restart_worker_calls=tuple(restart_calls),
        resumed_task_ids=result.resumed_task_ids,
        unknown_task_ids=result.state.unknown_task_ids,
        shed_task_ids=result.state.shed_task_ids,
        completed_task_ids=result.state.completed_task_ids,
        provider_reset_honored="burst" not in dict(result.state.provider_resets),
        external_provider_calls=0,
        controller_record_count=len(result.controller_records),
    )


__all__ = [
    "ADAPTIVE_RUNTIME_LIMITATIONS",
    "ADAPTIVE_RUNTIME_SCHEMA_VERSION",
    "ADAPTIVE_RUNTIME_SCOPE",
    "AdaptiveAction",
    "AdaptiveControlEvent",
    "AdaptiveControllerRecord",
    "AdaptiveDecision",
    "AdaptiveEventKind",
    "AdaptiveInvariantError",
    "AdaptiveRecoveryDrillResult",
    "AdaptiveReplayError",
    "AdaptiveReplayReport",
    "AdaptiveReplayViolation",
    "AdaptiveRunResult",
    "AdaptiveRuntime",
    "AdaptiveRuntimeError",
    "AdaptiveState",
    "AdaptiveStatus",
    "AdaptiveTaskContext",
    "AdaptiveWorkerResult",
    "InflightReservation",
    "SimulatedAdaptiveCrash",
    "adaptive_recovery_drill_envelope",
    "adaptive_recovery_drill_graph",
    "replay_adaptive_records",
    "run_adaptive_recovery_drill",
]
