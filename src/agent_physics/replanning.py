"""Digest-bound, deterministic replanning over modeled execution state.

This module is deliberately a planning vertical slice.  It does not mutate a live
``AsyncGraphExecutor``, cancel remote calls, change provider quotas, or commit an
effect.  Instead it consumes an explicit cumulative progress snapshot and one
explicit disturbance event, constructs the causally correct residual graph, and
asks FINITE's existing :class:`~agent_physics.scheduler.Scheduler` for a new plan.

The boundary matters: elapsed time and *actual settled* usage are never refunded,
and completed work is never put back into the graph.  An effect-intent seal has
strictly narrower meaning than completion: it prevents duplicate dispatch of the
write, but it does not satisfy downstream dependencies.  A sealed write becomes
dependency-satisfying only when the cumulative progress snapshot also names it in
``completed_task_ids``; that pair is this module's typed committed fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, TypeAlias, cast

from .contracts import BackendProfile, Effect, EffectClass, RunEnvelope, TaskContract
from .events import Event, EventType
from .graph import ExecutionGraph
from .run_store import Usage
from .scheduler import ScheduleEntry, SchedulePolicy, ScheduleResult, Scheduler
from .serialization import canonical_json, content_digest, normalize


REPLANNING_MODEL_SCOPE = (
    "deterministic residual-graph construction from caller-supplied durable state",
    "modeled p95 duration, provider-capacity, deadline, token, cost, and context caps",
    "append-only event identity, monotonic revisions, and SHA-256 decision binding",
    "append-only sealed effect-intent exclusion; this module never executes an external effect",
)

REPLANNING_MODEL_LIMITATIONS = (
    "does not mutate, pause, cancel, or lease work in a live executor",
    "does not verify that caller-reported completions, elapsed time, or usage came from a provider",
    "provider slowdown and capacity are deterministic planning transforms, not live telemetry",
    "failure removes one declared task/provider choice; retry attempts are not executed here",
    "remaining schedule usage is an estimate while prior settled usage is caller-reported actual usage",
    "an intent seal prevents duplicate dispatch but requires an explicit completed-task fact before it satisfies dependencies",
    "events do not carry run_id or prior-state identity; callers must route each event with its matching durable state",
)


class ReplanError(RuntimeError):
    """Base class for modeled replanning failures."""


class ReplanInvariantError(ReplanError):
    """Raised when an event or progress snapshot violates causal invariants."""


class ReplanTamperError(ReplanError):
    """Raised when a durable digest no longer matches its content."""


class ReplanEventKind(str, Enum):
    PROVIDER_SLOWDOWN = "provider.slowdown"
    TASK_FAILURE = "task.failure"
    PROVIDER_CAPACITY = "provider.capacity"
    ENVELOPE_CHANGE = "envelope.change"


class ReplanDisposition(str, Enum):
    SCHEDULED = "scheduled"
    COMPLETE = "complete"
    REFUSED = "refused"


class ReplanReasonCode(str, Enum):
    EVENT_APPLIED = "event_applied"
    OPTIONAL_WORK_SHED = "optional_work_shed"
    NO_RESIDUAL_WORK = "no_residual_work"
    MANDATORY_PROMISE_BROKEN = "mandatory_promise_broken"
    NO_ADMISSIBLE_PROFILE = "no_admissible_profile"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    EFFECT_COMMIT_UNCONFIRMED = "effect_commit_unconfirmed"
    SCHEDULER_REFUSED = "scheduler_refused"


@dataclass(frozen=True, slots=True)
class EffectIntentSeal:
    """Content identity for one already-created external-effect intent."""

    task_id: str
    intent_id: str
    intent_digest: str


@dataclass(frozen=True, slots=True)
class EffectBoundary:
    """Immutable set of write intents that a residual plan must never recreate."""

    intents: tuple[EffectIntentSeal, ...]
    boundary_digest: str

    @classmethod
    def create(cls, intents: tuple[EffectIntentSeal, ...] = ()) -> EffectBoundary:
        ordered = tuple(sorted(intents, key=lambda item: (item.task_id, item.intent_id)))
        task_ids = [item.task_id for item in ordered]
        intent_ids = [item.intent_id for item in ordered]
        if len(task_ids) != len(set(task_ids)):
            raise ReplanInvariantError("an effect boundary may seal at most one intent per task")
        if len(intent_ids) != len(set(intent_ids)):
            raise ReplanInvariantError("effect intent IDs must be unique")
        for item in ordered:
            if not isinstance(item.task_id, str) or not isinstance(item.intent_id, str):
                raise ReplanInvariantError("effect seal identifiers must be strings")
            if not item.task_id or not item.intent_id:
                raise ReplanInvariantError("effect seals require task and intent IDs")
            if not isinstance(item.intent_digest, str) or not _is_sha256(item.intent_digest):
                raise ReplanInvariantError("effect intent digests must be lowercase SHA-256")
        payload = {"intents": normalize(ordered)}
        return cls(ordered, content_digest(payload))

    @classmethod
    def empty(cls) -> EffectBoundary:
        return cls.create()

    def unsigned_payload(self) -> dict[str, object]:
        return {"intents": normalize(self.intents)}

    def verify_digest(self) -> bool:
        if (
            type(self) is not EffectBoundary
            or type(self.intents) is not tuple
            or type(self.boundary_digest) is not str
            or not _is_sha256(self.boundary_digest)
            or any(type(seal) is not EffectIntentSeal for seal in self.intents)
        ):
            return False
        task_ids: list[str] = []
        intent_ids: list[str] = []
        for seal in self.intents:
            if (
                type(seal.task_id) is not str
                or not seal.task_id
                or type(seal.intent_id) is not str
                or not seal.intent_id
                or type(seal.intent_digest) is not str
                or not _is_sha256(seal.intent_digest)
            ):
                return False
            task_ids.append(seal.task_id)
            intent_ids.append(seal.intent_id)
        if (
            len(task_ids) != len(set(task_ids))
            or len(intent_ids) != len(set(intent_ids))
            or self.intents
            != tuple(sorted(self.intents, key=lambda item: (item.task_id, item.intent_id)))
        ):
            return False
        return self.boundary_digest == content_digest(
            EffectBoundary.unsigned_payload(self)
        )

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "boundary_digest": self.boundary_digest}


_DURABLE_STATE_FIELDS = frozenset(
    {
        "run_id",
        "graph_digest",
        "revision",
        "prior_state_digest",
        "current_envelope",
        "completed_task_ids",
        "skipped_task_ids",
        "settled_usage",
        "elapsed_ms",
        "effect_boundary",
        "provider_capacities",
        "provider_slowdowns_permille",
        "failed_task_providers",
        "applied_events",
        "state_digest",
    }
)
_EFFECT_BOUNDARY_FIELDS = frozenset({"intents", "boundary_digest"})
_EFFECT_INTENT_FIELDS = frozenset({"task_id", "intent_id", "intent_digest"})
_ENVELOPE_FIELDS = frozenset(
    {
        "deadline_ms",
        "max_tokens",
        "max_cost_microusd",
        "max_context_bytes",
        "max_parallelism",
        "min_modeled_success_probability",
        "provider_limits",
    }
)
_USAGE_FIELDS = frozenset({"tokens", "cost_microusd", "context_bytes"})
_APPLIED_EVENT_FIELDS = frozenset({"revision", "event_id", "event_digest"})


@dataclass(frozen=True, slots=True)
class AppliedReplanEvent:
    revision: int
    event_id: str
    event_digest: str


@dataclass(frozen=True, slots=True)
class DurableRunState:
    """Canonical state that can be serialized and checked after a restart."""

    run_id: str
    graph_digest: str
    revision: int
    prior_state_digest: str | None
    current_envelope: RunEnvelope
    completed_task_ids: tuple[str, ...]
    skipped_task_ids: tuple[str, ...]
    settled_usage: Usage
    elapsed_ms: int
    effect_boundary: EffectBoundary
    provider_capacities: tuple[tuple[str, int], ...]
    provider_slowdowns_permille: tuple[tuple[str, int], ...]
    failed_task_providers: tuple[tuple[str, str], ...]
    applied_events: tuple[AppliedReplanEvent, ...]
    state_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "graph_digest": self.graph_digest,
            "revision": self.revision,
            "prior_state_digest": self.prior_state_digest,
            "current_envelope": normalize(self.current_envelope),
            "completed_task_ids": list(self.completed_task_ids),
            "skipped_task_ids": list(self.skipped_task_ids),
            "settled_usage": normalize(self.settled_usage),
            "elapsed_ms": self.elapsed_ms,
            "effect_boundary": self.effect_boundary.as_dict(),
            "provider_capacities": normalize(self.provider_capacities),
            "provider_slowdowns_permille": normalize(
                self.provider_slowdowns_permille
            ),
            "failed_task_providers": normalize(self.failed_task_providers),
            "applied_events": normalize(self.applied_events),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "state_digest": self.state_digest}

    def to_json(self) -> str:
        return canonical_json(self.as_dict())

    def verify_digest(self) -> bool:
        if type(self) is not DurableRunState or type(self.effect_boundary) is not EffectBoundary:
            return False
        try:
            _validate_state_shape(self)
        except (ReplanError, TypeError, ValueError, AttributeError):
            return False
        return EffectBoundary.verify_digest(
            self.effect_boundary
        ) and self.state_digest == content_digest(DurableRunState.unsigned_payload(self))

    @classmethod
    def from_json(cls, value: str) -> DurableRunState:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ReplanTamperError("durable state is not valid JSON") from exc
        return cls.from_dict(_mapping(payload, "state"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> DurableRunState:
        """Restore one canonical snapshot and reject content/digest disagreement."""

        try:
            _require_exact_fields(payload, _DURABLE_STATE_FIELDS, "state")
            boundary_payload = _mapping(payload["effect_boundary"], "effect_boundary")
            _require_exact_fields(
                boundary_payload, _EFFECT_BOUNDARY_FIELDS, "effect_boundary"
            )
            parsed_intents: list[EffectIntentSeal] = []
            for value in _sequence(boundary_payload["intents"], "intents"):
                item = _mapping(value, "effect intent")
                _require_exact_fields(item, _EFFECT_INTENT_FIELDS, "effect intent")
                parsed_intents.append(
                    EffectIntentSeal(
                        task_id=_string(item["task_id"], "task_id"),
                        intent_id=_string(item["intent_id"], "intent_id"),
                        intent_digest=_string(item["intent_digest"], "intent_digest"),
                    )
                )
            intents = tuple(parsed_intents)
            boundary = EffectBoundary(
                intents=intents,
                boundary_digest=_string(
                    boundary_payload["boundary_digest"], "boundary_digest"
                ),
            )
            envelope = _envelope_from_mapping(
                _mapping(payload["current_envelope"], "current_envelope")
            )
            usage_payload = _mapping(payload["settled_usage"], "settled_usage")
            _require_exact_fields(usage_payload, _USAGE_FIELDS, "settled_usage")
            parsed_events: list[AppliedReplanEvent] = []
            for value in _sequence(payload["applied_events"], "applied_events"):
                item = _mapping(value, "applied event")
                _require_exact_fields(item, _APPLIED_EVENT_FIELDS, "applied event")
                parsed_events.append(
                    AppliedReplanEvent(
                        revision=_integer(item["revision"], "event revision"),
                        event_id=_string(item["event_id"], "event_id"),
                        event_digest=_string(item["event_digest"], "event_digest"),
                    )
                )
            applied_events = tuple(parsed_events)
            state = cls(
                run_id=_string(payload["run_id"], "run_id"),
                graph_digest=_string(payload["graph_digest"], "graph_digest"),
                revision=_integer(payload["revision"], "revision"),
                prior_state_digest=_optional_string(
                    payload["prior_state_digest"], "prior_state_digest"
                ),
                current_envelope=envelope,
                completed_task_ids=_string_tuple(
                    payload["completed_task_ids"], "completed_task_ids"
                ),
                skipped_task_ids=_string_tuple(
                    payload["skipped_task_ids"], "skipped_task_ids"
                ),
                settled_usage=Usage(
                    tokens=_integer(usage_payload["tokens"], "tokens"),
                    cost_microusd=_integer(
                        usage_payload["cost_microusd"], "cost_microusd"
                    ),
                    context_bytes=_integer(
                        usage_payload["context_bytes"], "context_bytes"
                    ),
                ),
                elapsed_ms=_integer(payload["elapsed_ms"], "elapsed_ms"),
                effect_boundary=boundary,
                provider_capacities=_pair_tuple(
                    payload["provider_capacities"], "provider_capacities"
                ),
                provider_slowdowns_permille=_pair_tuple(
                    payload["provider_slowdowns_permille"],
                    "provider_slowdowns_permille",
                ),
                failed_task_providers=_string_pair_tuple(
                    payload["failed_task_providers"], "failed_task_providers"
                ),
                applied_events=applied_events,
                state_digest=_string(payload["state_digest"], "state_digest"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReplanTamperError("durable state has an invalid schema") from exc
        if not DurableRunState.verify_digest(state):
            raise ReplanTamperError("durable state digest verification failed")
        _validate_state_shape(state)
        return state


@dataclass(frozen=True, slots=True)
class RunProgressSnapshot:
    """Cumulative, not delta, execution facts observed at an event boundary.

    ``effect_boundary`` carries the canonical material for the digest and may
    append newly created intent seals.  Prior seals cannot be removed or changed.
    ``effect_boundary_digest`` remains explicit so a transport cannot substitute
    boundary material without also satisfying the content-address check.
    """

    completed_task_ids: tuple[str, ...]
    skipped_task_ids: tuple[str, ...]
    settled_usage: Usage
    elapsed_ms: int
    effect_boundary_digest: str
    effect_boundary: EffectBoundary | None = None

    @classmethod
    def from_state(
        cls,
        state: DurableRunState,
        *,
        completed_task_ids: tuple[str, ...] | None = None,
        skipped_task_ids: tuple[str, ...] | None = None,
        settled_usage: Usage | None = None,
        elapsed_ms: int | None = None,
        effect_boundary: EffectBoundary | None = None,
    ) -> RunProgressSnapshot:
        if type(state) is not DurableRunState or not DurableRunState.verify_digest(state):
            raise ReplanInvariantError("progress requires an exact verified durable state")
        boundary = state.effect_boundary if effect_boundary is None else effect_boundary
        return cls(
            completed_task_ids=(
                state.completed_task_ids
                if completed_task_ids is None
                else completed_task_ids
            ),
            skipped_task_ids=(
                state.skipped_task_ids if skipped_task_ids is None else skipped_task_ids
            ),
            settled_usage=state.settled_usage if settled_usage is None else settled_usage,
            elapsed_ms=state.elapsed_ms if elapsed_ms is None else elapsed_ms,
            effect_boundary_digest=boundary.boundary_digest,
            effect_boundary=boundary,
        )


@dataclass(frozen=True, slots=True)
class ProviderSlowdownEvent:
    event_id: str
    occurred_at_ms: int
    provider: str
    multiplier_permille: int

    @property
    def kind(self) -> ReplanEventKind:
        return ReplanEventKind.PROVIDER_SLOWDOWN

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "occurred_at_ms": self.occurred_at_ms,
            "provider": self.provider,
            "multiplier_permille": self.multiplier_permille,
        }

    @property
    def event_digest(self) -> str:
        return content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class TaskFailureEvent:
    event_id: str
    occurred_at_ms: int
    task_id: str
    provider: str

    @property
    def kind(self) -> ReplanEventKind:
        return ReplanEventKind.TASK_FAILURE

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "occurred_at_ms": self.occurred_at_ms,
            "task_id": self.task_id,
            "provider": self.provider,
        }

    @property
    def event_digest(self) -> str:
        return content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class ProviderCapacityEvent:
    event_id: str
    occurred_at_ms: int
    provider: str
    capacity: int

    @property
    def kind(self) -> ReplanEventKind:
        return ReplanEventKind.PROVIDER_CAPACITY

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "occurred_at_ms": self.occurred_at_ms,
            "provider": self.provider,
            "capacity": self.capacity,
        }

    @property
    def event_digest(self) -> str:
        return content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class EnvelopeChangeEvent:
    event_id: str
    occurred_at_ms: int
    envelope: RunEnvelope

    @property
    def kind(self) -> ReplanEventKind:
        return ReplanEventKind.ENVELOPE_CHANGE

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "occurred_at_ms": self.occurred_at_ms,
            "envelope": normalize(self.envelope),
        }

    @property
    def event_digest(self) -> str:
        return content_digest(self.unsigned_payload())


ReplanEvent: TypeAlias = (
    ProviderSlowdownEvent
    | TaskFailureEvent
    | ProviderCapacityEvent
    | EnvelopeChangeEvent
)


@dataclass(frozen=True, slots=True)
class ReplanReason:
    code: ReplanReasonCode
    summary: str
    facts: tuple[tuple[str, object], ...] = ()

    def verify(self) -> bool:
        if type(self) is not ReplanReason:
            return False
        if type(self.code) is not ReplanReasonCode or type(self.summary) is not str or not self.summary:
            return False
        if type(self.facts) is not tuple:
            return False
        keys: list[str] = []
        for fact in self.facts:
            if type(fact) is not tuple or len(fact) != 2 or type(fact[0]) is not str:
                return False
            keys.append(fact[0])
            try:
                normalize(fact[1])
            except (TypeError, ValueError):
                return False
        return (
            all(keys)
            and len(keys) == len(set(keys))
            and self.facts == tuple(sorted(self.facts, key=lambda item: item[0]))
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "summary": self.summary,
            "facts": {key: normalize(value) for key, value in self.facts},
            # Preserve the existing convenient mapping while binding the digest
            # to a lossless representation that cannot alias duplicate keys.
            "fact_pairs": normalize(self.facts),
        }


@dataclass(frozen=True, slots=True)
class ReplanDecision:
    """One revisioned decision and its complete deterministic witness."""

    revision: int
    event_id: str
    event_kind: ReplanEventKind
    prior_state_digest: str
    next_state_digest: str
    disposition: ReplanDisposition
    reason: ReplanReason
    remaining_envelope: RunEnvelope | None
    residual_graph: ExecutionGraph | None
    schedule: ScheduleResult | None
    shed_task_ids: tuple[str, ...]
    scope: tuple[str, ...]
    limitations: tuple[str, ...]
    decision_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "event_id": self.event_id,
            "event_kind": self.event_kind.value,
            "prior_state_digest": self.prior_state_digest,
            "next_state_digest": self.next_state_digest,
            "disposition": self.disposition.value,
            "reason": self.reason.as_dict(),
            "remaining_envelope": normalize(self.remaining_envelope),
            "residual_graph": normalize(self.residual_graph),
            # ScheduleResult.as_dict() intentionally rounds floats and Event.as_dict()
            # collapses detail pairs into a mapping.  Neither is safe digest material.
            "schedule": normalize(self.schedule),
            "shed_task_ids": list(self.shed_task_ids),
            "scope": list(self.scope),
            "limitations": list(self.limitations),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_payload(), "decision_digest": self.decision_digest}

    def verify_digest(self) -> bool:
        if type(self) is not ReplanDecision or type(self.reason) is not ReplanReason:
            return False
        if (
            type(self.revision) is not int
            or type(self.event_id) is not str
            or type(self.event_kind) is not ReplanEventKind
            or type(self.prior_state_digest) is not str
            or type(self.next_state_digest) is not str
            or type(self.disposition) is not ReplanDisposition
            or type(self.shed_task_ids) is not tuple
            or any(type(task_id) is not str for task_id in self.shed_task_ids)
            or type(self.scope) is not tuple
            or type(self.limitations) is not tuple
            or any(type(item) is not str for item in (*self.scope, *self.limitations))
            or not _is_sha256(self.decision_digest)
        ):
            return False
        if not ReplanReason.verify(self.reason):
            return False
        if self.remaining_envelope is not None and type(self.remaining_envelope) is not RunEnvelope:
            return False
        if self.residual_graph is not None and type(self.residual_graph) is not ExecutionGraph:
            return False
        if self.schedule is not None and not _is_exact_schedule(self.schedule):
            return False
        try:
            if self.remaining_envelope is not None:
                _validate_exact_envelope(self.remaining_envelope, "remaining envelope")
            if self.residual_graph is not None:
                _validate_exact_replan_graph(self.residual_graph)
            return self.decision_digest == content_digest(
                ReplanDecision.unsigned_payload(self)
            )
        except (ReplanError, TypeError, ValueError, OverflowError):
            return False


@dataclass(frozen=True, slots=True)
class ReplanTransition:
    state: DurableRunState
    decision: ReplanDecision


@dataclass(frozen=True, slots=True)
class _ResidualBuild:
    graph: ExecutionGraph | None
    auto_shed: tuple[str, ...]
    refusal: ReplanReason | None


class EventDrivenReplanner:
    """Pure controller for deterministic modeled schedule revisions."""

    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self._scheduler = scheduler or Scheduler()

    def initial_state(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        completed_task_ids: tuple[str, ...] = (),
        skipped_task_ids: tuple[str, ...] = (),
        settled_usage: Usage = Usage(),
        elapsed_ms: int = 0,
        effect_boundary: EffectBoundary | None = None,
    ) -> DurableRunState:
        """Bind a durable revision-zero snapshot to a graph and total envelope."""

        _validate_exact_replan_graph(graph)
        _validate_exact_envelope(envelope, "initial envelope")
        graph.validate()
        if not run_id:
            raise ReplanInvariantError("run_id is required")
        if envelope.validate():
            raise ReplanInvariantError("initial envelope is invalid: " + "; ".join(envelope.validate()))
        _validate_usage(settled_usage, "initial settled usage")
        boundary = effect_boundary or EffectBoundary.empty()
        self._validate_boundary(graph, boundary)
        self._validate_terminal_sets(graph, completed_task_ids, skipped_task_ids, boundary)
        completed = _ordered_ids(completed_task_ids)
        skipped = _ordered_ids(skipped_task_ids)
        self._validate_completion_closure(graph, completed, boundary)
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            raise ReplanInvariantError("elapsed_ms must be a non-negative integer")
        return self._make_state(
            run_id=run_id,
            graph=graph,
            revision=0,
            prior_state_digest=None,
            current_envelope=envelope,
            completed_task_ids=completed,
            skipped_task_ids=skipped,
            settled_usage=settled_usage,
            elapsed_ms=elapsed_ms,
            effect_boundary=boundary,
            provider_capacities=(),
            provider_slowdowns_permille=(),
            failed_task_providers=(),
            applied_events=(),
        )

    def replan(
        self,
        graph: ExecutionGraph,
        state: DurableRunState,
        event: ReplanEvent,
        progress: RunProgressSnapshot,
    ) -> ReplanTransition:
        """Apply one event to a cumulative snapshot and reschedule the remainder."""

        effect_boundary = self._validate_inputs(graph, state, event, progress)
        current_envelope, capacities, slowdowns, failures = self._apply_event(
            graph, state, event, progress, effect_boundary
        )
        completed = _ordered_ids(progress.completed_task_ids)
        reported_skipped = _ordered_ids(progress.skipped_task_ids)
        residual = self._build_residual(
            graph,
            completed=completed,
            skipped=reported_skipped,
            elapsed_ms=progress.elapsed_ms,
            effect_boundary=effect_boundary,
            provider_capacities=capacities,
            provider_slowdowns_permille=slowdowns,
            failed_task_providers=failures,
        )

        remaining_envelope: RunEnvelope | None = None
        schedule: ScheduleResult | None = None
        shed: tuple[str, ...] = ()
        if residual.refusal is not None:
            disposition = ReplanDisposition.REFUSED
            reason = residual.refusal
        elif residual.graph is None:
            raise RuntimeError("residual builder produced no graph and no refusal")
        elif not residual.graph.tasks:
            terminal_refusal = self._consumed_envelope_refusal(
                current_envelope,
                progress.settled_usage,
                progress.elapsed_ms,
            )
            if terminal_refusal is not None:
                disposition = ReplanDisposition.REFUSED
                reason = terminal_refusal
            else:
                disposition = ReplanDisposition.COMPLETE
                shed = residual.auto_shed
                reason = _reason(
                    ReplanReasonCode.NO_RESIDUAL_WORK,
                    "all work has an explicit completed or skipped terminal fact",
                    terminal_task_count=len(completed) + len(reported_skipped),
                )
        else:
            remaining_envelope, envelope_refusal = self._remaining_envelope(
                current_envelope,
                progress.settled_usage,
                progress.elapsed_ms,
                capacities,
            )
            if envelope_refusal is not None:
                disposition = ReplanDisposition.REFUSED
                reason = envelope_refusal
            else:
                assert remaining_envelope is not None
                schedule = self._scheduler.schedule(
                    residual.graph,
                    remaining_envelope,
                    SchedulePolicy.ADAPTIVE,
                )
                if not schedule.success:
                    disposition = ReplanDisposition.REFUSED
                    reason = _reason(
                        ReplanReasonCode.SCHEDULER_REFUSED,
                        "the residual mandatory promises have no admissible schedule",
                        scheduler_reason=schedule.failure_reason or "unspecified",
                        residual_task_count=len(residual.graph.tasks),
                    )
                else:
                    disposition = ReplanDisposition.SCHEDULED
                    shed = _ordered_ids((*residual.auto_shed, *schedule.skipped))
                    if shed:
                        reason = _reason(
                            ReplanReasonCode.OPTIONAL_WORK_SHED,
                            "mandatory promises remain scheduled after optional work is shed",
                            event_kind=event.kind.value,
                            shed_task_ids=shed,
                        )
                    else:
                        reason = _reason(
                            ReplanReasonCode.EVENT_APPLIED,
                            "the disturbance was applied and the residual work was rescheduled",
                            event_kind=event.kind.value,
                            residual_task_count=len(residual.graph.tasks),
                        )

        next_skipped = reported_skipped
        if disposition is not ReplanDisposition.REFUSED:
            next_skipped = _ordered_ids((*reported_skipped, *shed))
        applied_event = AppliedReplanEvent(
            revision=state.revision + 1,
            event_id=event.event_id,
            event_digest=_event_digest(event),
        )
        next_state = self._make_state(
            run_id=state.run_id,
            graph=graph,
            revision=state.revision + 1,
            prior_state_digest=state.state_digest,
            current_envelope=current_envelope,
            completed_task_ids=completed,
            skipped_task_ids=next_skipped,
            settled_usage=progress.settled_usage,
            elapsed_ms=progress.elapsed_ms,
            effect_boundary=effect_boundary,
            provider_capacities=tuple(sorted(capacities.items())),
            provider_slowdowns_permille=tuple(sorted(slowdowns.items())),
            failed_task_providers=tuple(sorted(failures)),
            applied_events=(*state.applied_events, applied_event),
        )
        unsigned_decision = {
            "revision": next_state.revision,
            "event_id": event.event_id,
            "event_kind": event.kind.value,
            "prior_state_digest": state.state_digest,
            "next_state_digest": next_state.state_digest,
            "disposition": disposition.value,
            "reason": reason.as_dict(),
            "remaining_envelope": normalize(remaining_envelope),
            "residual_graph": normalize(residual.graph),
            "schedule": normalize(schedule),
            "shed_task_ids": list(shed),
            "scope": list(REPLANNING_MODEL_SCOPE),
            "limitations": list(REPLANNING_MODEL_LIMITATIONS),
        }
        decision = ReplanDecision(
            revision=next_state.revision,
            event_id=event.event_id,
            event_kind=event.kind,
            prior_state_digest=state.state_digest,
            next_state_digest=next_state.state_digest,
            disposition=disposition,
            reason=reason,
            remaining_envelope=remaining_envelope,
            residual_graph=residual.graph,
            schedule=schedule,
            shed_task_ids=shed,
            scope=REPLANNING_MODEL_SCOPE,
            limitations=REPLANNING_MODEL_LIMITATIONS,
            decision_digest=content_digest(unsigned_decision),
        )
        if not ReplanDecision.verify_digest(decision):
            raise RuntimeError("internal decision digest construction disagreed")
        return ReplanTransition(next_state, decision)

    def verify_transition(
        self,
        graph: ExecutionGraph,
        prior_state: DurableRunState,
        event: ReplanEvent,
        progress: RunProgressSnapshot,
        transition: ReplanTransition,
    ) -> bool:
        """Replay a transition from its prior state and compare both digests."""

        if (
            type(prior_state) is not DurableRunState
            or type(progress) is not RunProgressSnapshot
            or type(transition) is not ReplanTransition
            or type(transition.state) is not DurableRunState
            or type(transition.decision) is not ReplanDecision
        ):
            return False
        try:
            _validate_state_shape(transition.state)
            if not DurableRunState.verify_digest(
                transition.state
            ) or not ReplanDecision.verify_digest(transition.decision):
                return False
            replayed = self.replan(graph, prior_state, event, progress)
        except (ReplanError, TypeError, ValueError, AttributeError):
            return False
        return (
            replayed.state.state_digest == transition.state.state_digest
            and replayed.decision.decision_digest
            == transition.decision.decision_digest
        )

    def _validate_inputs(
        self,
        graph: ExecutionGraph,
        state: DurableRunState,
        event: ReplanEvent,
        progress: RunProgressSnapshot,
    ) -> EffectBoundary:
        _validate_exact_replan_graph(graph)
        if type(progress) is not RunProgressSnapshot:
            raise ReplanInvariantError("progress must use the RunProgressSnapshot contract")
        if type(state) is not DurableRunState:
            raise ReplanInvariantError("state must use the exact DurableRunState contract")
        if type(progress.completed_task_ids) is not tuple or any(
            type(task_id) is not str for task_id in progress.completed_task_ids
        ):
            raise ReplanInvariantError("progress completions must use an exact string tuple")
        if type(progress.skipped_task_ids) is not tuple or any(
            type(task_id) is not str for task_id in progress.skipped_task_ids
        ):
            raise ReplanInvariantError("progress skips must use an exact string tuple")
        if not _is_sha256(progress.effect_boundary_digest):
            raise ReplanInvariantError("progress effect boundary digest is malformed")
        graph.validate()
        _validate_usage(progress.settled_usage, "progress settled usage")
        _validate_state_shape(state)
        if not DurableRunState.verify_digest(state):
            raise ReplanTamperError("prior durable state digest verification failed")
        if state.graph_digest != content_digest(graph):
            raise ReplanInvariantError("durable state is bound to a different graph")
        effect_boundary = self._progress_effect_boundary(graph, state, progress)
        self._validate_event(graph, event)
        if event.event_id in {item.event_id for item in state.applied_events}:
            raise ReplanInvariantError(f"event {event.event_id!r} was already applied")
        if type(progress.elapsed_ms) is not int or progress.elapsed_ms < state.elapsed_ms:
            raise ReplanInvariantError("elapsed time cannot move backwards")
        if event.occurred_at_ms != progress.elapsed_ms:
            raise ReplanInvariantError("event time must equal the cumulative progress time")
        if not _usage_at_least(progress.settled_usage, state.settled_usage):
            raise ReplanInvariantError("actual settled usage cannot decrease")

        completed = set(progress.completed_task_ids)
        skipped = set(progress.skipped_task_ids)
        if not set(state.completed_task_ids).issubset(completed):
            raise ReplanInvariantError("completed tasks cannot be removed")
        if not set(state.skipped_task_ids).issubset(skipped):
            raise ReplanInvariantError("skipped tasks cannot be reintroduced")
        self._validate_terminal_sets(
            graph,
            progress.completed_task_ids,
            progress.skipped_task_ids,
            effect_boundary,
        )
        self._validate_completion_closure(
            graph,
            _ordered_ids(progress.completed_task_ids),
            effect_boundary,
        )
        return effect_boundary

    def _progress_effect_boundary(
        self,
        graph: ExecutionGraph,
        state: DurableRunState,
        progress: RunProgressSnapshot,
    ) -> EffectBoundary:
        boundary = progress.effect_boundary
        if boundary is None:
            if progress.effect_boundary_digest != state.effect_boundary.boundary_digest:
                raise ReplanInvariantError(
                    "effect boundary material is required when its digest changes"
                )
            boundary = state.effect_boundary
        elif progress.effect_boundary_digest != boundary.boundary_digest:
            raise ReplanInvariantError("effect boundary material and digest disagree")

        self._validate_boundary(graph, boundary)
        prior_by_task = {seal.task_id: seal for seal in state.effect_boundary.intents}
        next_by_task = {seal.task_id: seal for seal in boundary.intents}
        if any(next_by_task.get(task_id) != seal for task_id, seal in prior_by_task.items()):
            raise ReplanInvariantError(
                "effect boundary is append-only; existing intent seals cannot be removed or mutated"
            )
        return boundary

    @staticmethod
    def _validate_event(graph: ExecutionGraph, event: ReplanEvent) -> None:
        event_type = type(event)
        if event_type not in {
            ProviderSlowdownEvent,
            TaskFailureEvent,
            ProviderCapacityEvent,
            EnvelopeChangeEvent,
        }:
            raise ReplanInvariantError("events must use an exact supported contract")
        if type(event.event_id) is not str or not event.event_id:
            raise ReplanInvariantError("event_id is required")
        if type(event.occurred_at_ms) is not int or event.occurred_at_ms < 0:
            raise ReplanInvariantError("event time must be a non-negative integer")
        known_providers = {
            profile.provider for task in graph.tasks for profile in task.profiles
        }
        if event_type is ProviderSlowdownEvent:
            if type(event.provider) is not str:
                raise ReplanInvariantError("slowdown provider must be an exact string")
            if event.provider not in known_providers:
                raise ReplanInvariantError("slowdown targets an unknown provider")
            if type(event.multiplier_permille) is not int or event.multiplier_permille <= 1_000:
                raise ReplanInvariantError("a slowdown multiplier must be an integer above 1000")
        elif event_type is TaskFailureEvent:
            if type(event.task_id) is not str or type(event.provider) is not str:
                raise ReplanInvariantError("failure task and provider must be exact strings")
            task = graph.by_id.get(event.task_id)
            if task is None:
                raise ReplanInvariantError("failure targets an unknown task")
            if event.provider not in {profile.provider for profile in task.profiles}:
                raise ReplanInvariantError("failure targets an undeclared task provider")
        elif event_type is ProviderCapacityEvent:
            if type(event.provider) is not str:
                raise ReplanInvariantError("capacity provider must be an exact string")
            if event.provider not in known_providers:
                raise ReplanInvariantError("capacity event targets an unknown provider")
            if type(event.capacity) is not int or event.capacity < 0:
                raise ReplanInvariantError("provider capacity must be a non-negative integer")
        elif event_type is EnvelopeChangeEvent:
            _validate_exact_envelope(event.envelope, "changed envelope")
            errors = event.envelope.validate()
            if errors:
                raise ReplanInvariantError("changed envelope is invalid: " + "; ".join(errors))

    @staticmethod
    def _validate_boundary(graph: ExecutionGraph, boundary: EffectBoundary) -> None:
        if type(boundary) is not EffectBoundary or type(boundary.intents) is not tuple:
            raise ReplanInvariantError("effect boundary must use the exact immutable contract")
        if any(type(seal) is not EffectIntentSeal for seal in boundary.intents):
            raise ReplanInvariantError("effect boundary contains a malformed intent seal")
        if not EffectBoundary.verify_digest(boundary):
            raise ReplanTamperError("effect boundary digest verification failed")
        by_id = graph.by_id
        for seal in boundary.intents:
            task = by_id.get(seal.task_id)
            if task is None:
                raise ReplanInvariantError("effect boundary names an unknown task")
            if not task.effect.kind.writes:
                raise ReplanInvariantError("only write tasks can be sealed as effect intents")

    @staticmethod
    def _validate_terminal_sets(
        graph: ExecutionGraph,
        completed: tuple[str, ...],
        skipped: tuple[str, ...],
        boundary: EffectBoundary,
    ) -> None:
        known = set(graph.by_id)
        if len(completed) != len(set(completed)) or len(skipped) != len(set(skipped)):
            raise ReplanInvariantError("terminal task IDs must be unique")
        if not set(completed).issubset(known) or not set(skipped).issubset(known):
            raise ReplanInvariantError("terminal state contains an unknown task")
        if set(completed) & set(skipped):
            raise ReplanInvariantError("a task cannot be both completed and skipped")
        effect_tasks = {item.task_id for item in boundary.intents}
        if set(skipped) & effect_tasks:
            raise ReplanInvariantError("a sealed effect intent cannot be marked skipped")
        for task_id in completed:
            if graph.by_id[task_id].effect.kind.writes and task_id not in effect_tasks:
                raise ReplanInvariantError(
                    f"completed write task {task_id!r} lacks an immutable effect seal"
                )

    @staticmethod
    def _validate_completion_closure(
        graph: ExecutionGraph,
        completed: tuple[str, ...],
        boundary: EffectBoundary,
    ) -> None:
        completed_set = set(completed)
        for task_id in completed:
            missing = set(graph.by_id[task_id].dependencies) - completed_set
            if missing:
                raise ReplanInvariantError(
                    f"terminal task {task_id!r} lacks completed dependencies {sorted(missing)}"
                )
        for seal in boundary.intents:
            missing = set(graph.by_id[seal.task_id].dependencies) - completed_set
            if missing:
                raise ReplanInvariantError(
                    f"sealed effect task {seal.task_id!r} lacks completed dependencies "
                    f"{sorted(missing)}"
                )

    @staticmethod
    def _apply_event(
        graph: ExecutionGraph,
        state: DurableRunState,
        event: ReplanEvent,
        progress: RunProgressSnapshot,
        effect_boundary: EffectBoundary,
    ) -> tuple[RunEnvelope, dict[str, int], dict[str, int], set[tuple[str, str]]]:
        envelope = state.current_envelope
        capacities = dict(state.provider_capacities)
        slowdowns = dict(state.provider_slowdowns_permille)
        failures = set(state.failed_task_providers)
        terminal = (
            set(progress.completed_task_ids)
            | set(progress.skipped_task_ids)
            | {item.task_id for item in effect_boundary.intents}
        )
        if type(event) is ProviderSlowdownEvent:
            slowdowns[event.provider] = event.multiplier_permille
        elif type(event) is TaskFailureEvent:
            if event.task_id in terminal:
                raise ReplanInvariantError(
                    "a completed, skipped, or dispatch-sealed task cannot report a new failure"
                )
            failures.add((event.task_id, event.provider))
        elif type(event) is ProviderCapacityEvent:
            capacities[event.provider] = event.capacity
        elif type(event) is EnvelopeChangeEvent:
            envelope = event.envelope
        return envelope, capacities, slowdowns, failures

    def _build_residual(
        self,
        graph: ExecutionGraph,
        *,
        completed: tuple[str, ...],
        skipped: tuple[str, ...],
        elapsed_ms: int,
        effect_boundary: EffectBoundary,
        provider_capacities: Mapping[str, int],
        provider_slowdowns_permille: Mapping[str, int],
        failed_task_providers: set[tuple[str, str]],
    ) -> _ResidualBuild:
        protected = self._protected_task_ids(graph)
        skipped_set = set(skipped)
        violated = sorted(skipped_set & protected)
        if violated:
            return _ResidualBuild(
                graph=None,
                auto_shed=(),
                refusal=_reason(
                    ReplanReasonCode.MANDATORY_PROMISE_BROKEN,
                    "a mandatory task or one of its required dependencies was skipped",
                    task_ids=violated,
                ),
            )
        satisfied = set(completed)
        dispatch_sealed = {
            item.task_id for item in effect_boundary.intents if item.task_id not in satisfied
        }
        auto_shed: set[str] = set()

        def transformed_profiles(task: TaskContract) -> tuple[BackendProfile, ...]:
            profiles: list[BackendProfile] = []
            for profile in task.profiles:
                if provider_capacities.get(profile.provider, 1) == 0:
                    continue
                if (task.task_id, profile.provider) in failed_task_providers:
                    continue
                multiplier = provider_slowdowns_permille.get(profile.provider, 1_000)
                profiles.append(
                    replace(
                        profile,
                        duration_ms_p50=_scaled_duration(
                            profile.duration_ms_p50, multiplier
                        ),
                        duration_ms_p95=_scaled_duration(
                            profile.duration_ms_p95, multiplier
                        ),
                    )
                )
            return tuple(profiles)

        while True:
            changed = False
            effective_skips = skipped_set | auto_shed
            for task in graph.tasks:
                if (
                    task.task_id in satisfied
                    or task.task_id in dispatch_sealed
                    or task.task_id in effective_skips
                ):
                    continue
                blocked = sorted(set(task.dependencies) & effective_skips)
                if blocked:
                    if task.task_id in protected:
                        return _ResidualBuild(
                            graph=None,
                            auto_shed=tuple(sorted(auto_shed)),
                            refusal=_reason(
                                ReplanReasonCode.MANDATORY_PROMISE_BROKEN,
                                "mandatory work depends on skipped work",
                                task_id=task.task_id,
                                dependencies=blocked,
                            ),
                        )
                    auto_shed.add(task.task_id)
                    changed = True
                    continue
                pending_effects = sorted(set(task.dependencies) & dispatch_sealed)
                if pending_effects:
                    if task.task_id in protected:
                        return _ResidualBuild(
                            graph=None,
                            auto_shed=tuple(sorted(auto_shed)),
                            refusal=_reason(
                                ReplanReasonCode.EFFECT_COMMIT_UNCONFIRMED,
                                "mandatory work depends on a sealed effect intent without a committed completion fact",
                                task_id=task.task_id,
                                pending_effect_task_ids=pending_effects,
                            ),
                        )
                    auto_shed.add(task.task_id)
                    changed = True
                    continue
                profiles = transformed_profiles(task)
                qualified = tuple(
                    profile for profile in profiles if profile.quality >= task.min_quality
                )
                deadline_left = (
                    None if task.deadline_ms is None else task.deadline_ms - elapsed_ms
                )
                if not qualified or (deadline_left is not None and deadline_left <= 0):
                    if task.task_id in protected:
                        code = (
                            ReplanReasonCode.DEADLINE_EXHAUSTED
                            if deadline_left is not None and deadline_left <= 0
                            else ReplanReasonCode.NO_ADMISSIBLE_PROFILE
                        )
                        return _ResidualBuild(
                            graph=None,
                            auto_shed=tuple(sorted(auto_shed)),
                            refusal=_reason(
                                code,
                                "a mandatory residual task has no admissible modeled execution",
                                task_id=task.task_id,
                            ),
                        )
                    auto_shed.add(task.task_id)
                    changed = True
            if not changed:
                break

        final_skips = skipped_set | auto_shed
        residual_tasks: list[TaskContract] = []
        for task in graph.tasks:
            if (
                task.task_id in satisfied
                or task.task_id in dispatch_sealed
                or task.task_id in final_skips
            ):
                continue
            profiles = transformed_profiles(task)
            deadline = (
                None if task.deadline_ms is None else task.deadline_ms - elapsed_ms
            )
            dependencies = tuple(
                dependency
                for dependency in task.dependencies
                if dependency not in satisfied
            )
            residual_tasks.append(
                replace(
                    task,
                    profiles=profiles,
                    dependencies=dependencies,
                    deadline_ms=deadline,
                )
            )
        if not residual_tasks and dispatch_sealed:
            return _ResidualBuild(
                graph=ExecutionGraph.from_tasks(()),
                auto_shed=tuple(sorted(auto_shed)),
                refusal=_reason(
                    ReplanReasonCode.EFFECT_COMMIT_UNCONFIRMED,
                    "sealed effect intents remain dispatch-protected but lack committed completion facts",
                    pending_effect_task_ids=tuple(sorted(dispatch_sealed)),
                ),
            )
        return _ResidualBuild(
            graph=ExecutionGraph.from_tasks(residual_tasks),
            auto_shed=tuple(sorted(auto_shed)),
            refusal=None,
        )

    @staticmethod
    def _consumed_envelope_refusal(
        envelope: RunEnvelope,
        settled_usage: Usage,
        elapsed_ms: int,
    ) -> ReplanReason | None:
        remaining_tokens = envelope.max_tokens - settled_usage.tokens
        remaining_cost = envelope.max_cost_microusd - settled_usage.cost_microusd
        remaining_context = envelope.max_context_bytes - settled_usage.context_bytes
        if min(remaining_tokens, remaining_cost, remaining_context) < 0:
            return _reason(
                ReplanReasonCode.RESOURCE_EXHAUSTED,
                "actual settled usage exceeds the current total envelope",
                settled_tokens=settled_usage.tokens,
                settled_cost_microusd=settled_usage.cost_microusd,
                settled_context_bytes=settled_usage.context_bytes,
            )
        if elapsed_ms > envelope.deadline_ms:
            return _reason(
                ReplanReasonCode.DEADLINE_EXHAUSTED,
                "cumulative elapsed time exceeds the current total deadline",
                elapsed_ms=elapsed_ms,
                total_deadline_ms=envelope.deadline_ms,
            )
        return None

    @staticmethod
    def _remaining_envelope(
        envelope: RunEnvelope,
        settled_usage: Usage,
        elapsed_ms: int,
        capacities: Mapping[str, int],
    ) -> tuple[RunEnvelope | None, ReplanReason | None]:
        remaining_tokens = envelope.max_tokens - settled_usage.tokens
        remaining_cost = envelope.max_cost_microusd - settled_usage.cost_microusd
        remaining_context = envelope.max_context_bytes - settled_usage.context_bytes
        if min(remaining_tokens, remaining_cost, remaining_context) < 0:
            return None, _reason(
                ReplanReasonCode.RESOURCE_EXHAUSTED,
                "actual settled usage already exceeds the current total envelope",
                settled_tokens=settled_usage.tokens,
                settled_cost_microusd=settled_usage.cost_microusd,
                settled_context_bytes=settled_usage.context_bytes,
            )
        deadline_left = envelope.deadline_ms - elapsed_ms
        if deadline_left <= 0:
            return None, _reason(
                ReplanReasonCode.DEADLINE_EXHAUSTED,
                "no modeled run time remains after subtracting elapsed time",
                elapsed_ms=elapsed_ms,
                total_deadline_ms=envelope.deadline_ms,
            )
        provider_limits = dict(envelope.provider_limits)
        provider_limits.update(capacities)
        remaining = RunEnvelope(
            deadline_ms=deadline_left,
            max_tokens=remaining_tokens,
            max_cost_microusd=remaining_cost,
            max_context_bytes=remaining_context,
            max_parallelism=envelope.max_parallelism,
            min_modeled_success_probability=envelope.min_modeled_success_probability,
            provider_limits=tuple(
                sorted(
                    (provider, capacity)
                    for provider, capacity in provider_limits.items()
                    if capacity > 0
                )
            ),
        )
        return remaining, None

    @staticmethod
    def _protected_task_ids(graph: ExecutionGraph) -> set[str]:
        by_id = graph.by_id
        protected = {task.task_id for task in graph.tasks if not task.optional}
        stack = list(protected)
        while stack:
            task_id = stack.pop()
            for dependency in by_id[task_id].dependencies:
                if dependency not in protected:
                    protected.add(dependency)
                    stack.append(dependency)
        return protected

    @staticmethod
    def _make_state(
        *,
        run_id: str,
        graph: ExecutionGraph,
        revision: int,
        prior_state_digest: str | None,
        current_envelope: RunEnvelope,
        completed_task_ids: tuple[str, ...],
        skipped_task_ids: tuple[str, ...],
        settled_usage: Usage,
        elapsed_ms: int,
        effect_boundary: EffectBoundary,
        provider_capacities: tuple[tuple[str, int], ...],
        provider_slowdowns_permille: tuple[tuple[str, int], ...],
        failed_task_providers: tuple[tuple[str, str], ...],
        applied_events: tuple[AppliedReplanEvent, ...],
    ) -> DurableRunState:
        _validate_usage(settled_usage, "durable settled usage")
        values: dict[str, object] = {
            "run_id": run_id,
            "graph_digest": content_digest(graph),
            "revision": revision,
            "prior_state_digest": prior_state_digest,
            "current_envelope": normalize(current_envelope),
            "completed_task_ids": list(completed_task_ids),
            "skipped_task_ids": list(skipped_task_ids),
            "settled_usage": normalize(settled_usage),
            "elapsed_ms": elapsed_ms,
            "effect_boundary": effect_boundary.as_dict(),
            "provider_capacities": normalize(provider_capacities),
            "provider_slowdowns_permille": normalize(
                provider_slowdowns_permille
            ),
            "failed_task_providers": normalize(failed_task_providers),
            "applied_events": normalize(applied_events),
        }
        state = DurableRunState(
            run_id=run_id,
            graph_digest=cast(str, values["graph_digest"]),
            revision=revision,
            prior_state_digest=prior_state_digest,
            current_envelope=current_envelope,
            completed_task_ids=completed_task_ids,
            skipped_task_ids=skipped_task_ids,
            settled_usage=settled_usage,
            elapsed_ms=elapsed_ms,
            effect_boundary=effect_boundary,
            provider_capacities=provider_capacities,
            provider_slowdowns_permille=provider_slowdowns_permille,
            failed_task_providers=failed_task_providers,
            applied_events=applied_events,
            state_digest=content_digest(values),
        )
        _validate_state_shape(state)
        return state


def _reason(
    code: ReplanReasonCode,
    summary: str,
    **facts: object,
) -> ReplanReason:
    return ReplanReason(code, summary, tuple(sorted(facts.items())))


def _scaled_duration(duration_ms: int, multiplier_permille: int) -> int:
    return (duration_ms * multiplier_permille + 999) // 1_000


def _usage_at_least(candidate: Usage, prior: Usage) -> bool:
    return (
        candidate.tokens >= prior.tokens
        and candidate.cost_microusd >= prior.cost_microusd
        and candidate.context_bytes >= prior.context_bytes
    )


def _validate_usage(usage: Usage, label: str) -> None:
    if type(usage) is not Usage:
        raise ReplanInvariantError(f"{label} must use the exact Usage contract")
    values = (usage.tokens, usage.cost_microusd, usage.context_bytes)
    if any(type(value) is not int or value < 0 for value in values):
        raise ReplanInvariantError(
            f"{label} values must be non-negative integers (booleans and floats are invalid)"
        )


def _ordered_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _is_sha256(value: str) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_exact_replan_graph(graph: ExecutionGraph) -> None:
    if type(graph) is not ExecutionGraph or type(graph.tasks) is not tuple:
        raise ReplanInvariantError("graph must use the exact immutable ExecutionGraph contract")
    for task in graph.tasks:
        if type(task) is not TaskContract:
            raise ReplanInvariantError("graph tasks must use exact TaskContract instances")
        if type(task.profiles) is not tuple or any(
            type(profile) is not BackendProfile for profile in task.profiles
        ):
            raise ReplanInvariantError("task profiles must use exact immutable contracts")
        if type(task.dependencies) is not tuple or any(
            type(dependency) is not str for dependency in task.dependencies
        ):
            raise ReplanInvariantError("task dependencies must use exact immutable strings")
        if type(task.effect) is not Effect or type(task.effect.kind) is not EffectClass:
            raise ReplanInvariantError("task effects must use exact Effect contracts")


def _validate_exact_envelope(envelope: RunEnvelope, label: str) -> None:
    if type(envelope) is not RunEnvelope or type(envelope.provider_limits) is not tuple:
        raise ReplanInvariantError(f"{label} must use the exact RunEnvelope contract")
    if any(
        type(pair) is not tuple
        or len(pair) != 2
        or type(pair[0]) is not str
        or type(pair[1]) is not int
        for pair in envelope.provider_limits
    ):
        raise ReplanInvariantError(f"{label} provider limits must use exact tuples")


def _is_exact_schedule(schedule: ScheduleResult) -> bool:
    return (
        type(schedule) is ScheduleResult
        and type(schedule.policy) is SchedulePolicy
        and type(schedule.entries) is tuple
        and all(type(entry) is ScheduleEntry for entry in schedule.entries)
        and type(schedule.skipped) is tuple
        and all(type(task_id) is str for task_id in schedule.skipped)
        and type(schedule.events) is tuple
        and all(
            type(event) is Event
            and type(event.event_type) is EventType
            and type(event.details) is tuple
            and all(
                type(detail) is tuple
                and len(detail) == 2
                and type(detail[0]) is str
                for detail in event.details
            )
            for event in schedule.events
        )
    )


def _event_payload(event: ReplanEvent) -> dict[str, object]:
    event_type = type(event)
    if event_type is ProviderSlowdownEvent:
        return {
            "event_id": event.event_id,
            "kind": ReplanEventKind.PROVIDER_SLOWDOWN.value,
            "occurred_at_ms": event.occurred_at_ms,
            "provider": event.provider,
            "multiplier_permille": event.multiplier_permille,
        }
    if event_type is TaskFailureEvent:
        return {
            "event_id": event.event_id,
            "kind": ReplanEventKind.TASK_FAILURE.value,
            "occurred_at_ms": event.occurred_at_ms,
            "task_id": event.task_id,
            "provider": event.provider,
        }
    if event_type is ProviderCapacityEvent:
        return {
            "event_id": event.event_id,
            "kind": ReplanEventKind.PROVIDER_CAPACITY.value,
            "occurred_at_ms": event.occurred_at_ms,
            "provider": event.provider,
            "capacity": event.capacity,
        }
    if event_type is EnvelopeChangeEvent:
        return {
            "event_id": event.event_id,
            "kind": ReplanEventKind.ENVELOPE_CHANGE.value,
            "occurred_at_ms": event.occurred_at_ms,
            "envelope": normalize(event.envelope),
        }
    raise ReplanInvariantError("events must use an exact supported contract")


def _event_digest(event: ReplanEvent) -> str:
    return content_digest(_event_payload(event))


def _validate_state_shape(state: DurableRunState) -> None:
    if type(state) is not DurableRunState:
        raise ReplanInvariantError("state must use the exact DurableRunState contract")
    if (
        type(state.run_id) is not str
        or not state.run_id
        or not _is_sha256(state.graph_digest)
        or not _is_sha256(state.state_digest)
    ):
        raise ReplanInvariantError("durable state identity is malformed")
    if (
        type(state.revision) is not int
        or type(state.elapsed_ms) is not int
        or state.revision < 0
        or state.elapsed_ms < 0
    ):
        raise ReplanInvariantError("durable revision and elapsed time cannot be negative")
    if type(state.applied_events) is not tuple or any(
        type(item) is not AppliedReplanEvent for item in state.applied_events
    ):
        raise ReplanInvariantError("applied events must use exact immutable contracts")
    if state.revision != len(state.applied_events):
        raise ReplanInvariantError("revision must equal the append-only event count")
    if state.revision == 0 and state.prior_state_digest is not None:
        raise ReplanInvariantError("revision zero cannot name a prior state")
    if state.revision > 0 and not (
        state.prior_state_digest and _is_sha256(state.prior_state_digest)
    ):
        raise ReplanInvariantError("a revised state requires a prior SHA-256 digest")
    revisions = tuple(item.revision for item in state.applied_events)
    if revisions != tuple(range(1, state.revision + 1)):
        raise ReplanInvariantError("applied event revisions must be contiguous")
    event_ids = tuple(item.event_id for item in state.applied_events)
    if any(type(item) is not str or not item for item in event_ids) or len(
        event_ids
    ) != len(set(event_ids)):
        raise ReplanInvariantError("applied event IDs must be nonempty and unique")
    if any(not _is_sha256(item.event_digest) for item in state.applied_events):
        raise ReplanInvariantError("applied event digests must be SHA-256")
    _validate_exact_envelope(state.current_envelope, "durable envelope")
    if state.current_envelope.validate():
        raise ReplanInvariantError("durable state contains an invalid envelope")
    _validate_usage(state.settled_usage, "durable settled usage")
    if type(state.effect_boundary) is not EffectBoundary or not EffectBoundary.verify_digest(
        state.effect_boundary
    ):
        raise ReplanInvariantError("durable effect boundary is malformed")
    if type(state.completed_task_ids) is not tuple or not all(
        type(task_id) is str and task_id for task_id in state.completed_task_ids
    ):
        raise ReplanInvariantError("completed task IDs must be nonempty strings")
    if type(state.skipped_task_ids) is not tuple or not all(
        type(task_id) is str and task_id for task_id in state.skipped_task_ids
    ):
        raise ReplanInvariantError("skipped task IDs must be nonempty strings")
    if tuple(sorted(state.completed_task_ids)) != state.completed_task_ids:
        raise ReplanInvariantError("completed task IDs must use canonical ordering")
    if tuple(sorted(state.skipped_task_ids)) != state.skipped_task_ids:
        raise ReplanInvariantError("skipped task IDs must use canonical ordering")
    if len(set(state.completed_task_ids)) != len(state.completed_task_ids):
        raise ReplanInvariantError("completed task IDs must be unique")
    if len(set(state.skipped_task_ids)) != len(state.skipped_task_ids):
        raise ReplanInvariantError("skipped task IDs must be unique")
    for pairs, label in (
        (state.provider_capacities, "provider capacities"),
        (state.provider_slowdowns_permille, "provider slowdowns"),
    ):
        if type(pairs) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2 for pair in pairs
        ):
            raise ReplanInvariantError(f"{label} must use exact immutable pairs")
        if tuple(sorted(pairs)) != pairs or len(dict(pairs)) != len(pairs):
            raise ReplanInvariantError(f"{label} must be canonical and unique")
    if any(
        type(provider) is not str
        or not provider
        or type(capacity) is not int
        or capacity < 0
        for provider, capacity in state.provider_capacities
    ):
        raise ReplanInvariantError("provider capacities contain an invalid value")
    if any(
        type(provider) is not str
        or not provider
        or type(multiplier) is not int
        or multiplier <= 1_000
        for provider, multiplier in state.provider_slowdowns_permille
    ):
        raise ReplanInvariantError("provider slowdowns contain an invalid value")
    if (
        type(state.failed_task_providers) is not tuple
        or any(
            type(pair) is not tuple or len(pair) != 2
            for pair in state.failed_task_providers
        )
        or
        tuple(sorted(state.failed_task_providers)) != state.failed_task_providers
        or len(set(state.failed_task_providers)) != len(state.failed_task_providers)
        or any(
            type(task_id) is not str
            or not task_id
            or type(provider) is not str
            or not provider
            for task_id, provider in state.failed_task_providers
        )
    ):
        raise ReplanInvariantError("failed task providers must be canonical and unique")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _require_exact_fields(
    payload: Mapping[str, object], expected: frozenset[str], label: str
) -> None:
    actual = frozenset(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise TypeError(f"{label} fields are invalid: {', '.join(details)}")


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return cast(list[object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    return cast(int, value)


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _sequence(value, label))


def _pair_tuple(value: object, label: str) -> tuple[tuple[str, int], ...]:
    result: list[tuple[str, int]] = []
    for raw_pair in _sequence(value, label):
        pair = _sequence(raw_pair, label)
        if len(pair) != 2:
            raise TypeError(f"{label} entries must have two fields")
        result.append((_string(pair[0], label), _integer(pair[1], label)))
    return tuple(result)


def _string_pair_tuple(value: object, label: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for raw_pair in _sequence(value, label):
        pair = _sequence(raw_pair, label)
        if len(pair) != 2:
            raise TypeError(f"{label} entries must have two fields")
        result.append((_string(pair[0], label), _string(pair[1], label)))
    return tuple(result)


def _envelope_from_mapping(payload: Mapping[str, object]) -> RunEnvelope:
    _require_exact_fields(payload, _ENVELOPE_FIELDS, "current_envelope")
    provider_limits = _pair_tuple(payload["provider_limits"], "provider_limits")
    probability = payload["min_modeled_success_probability"]
    if type(probability) not in (int, float):
        raise TypeError("min_modeled_success_probability must be numeric")
    return RunEnvelope(
        deadline_ms=_integer(payload["deadline_ms"], "deadline_ms"),
        max_tokens=_integer(payload["max_tokens"], "max_tokens"),
        max_cost_microusd=_integer(
            payload["max_cost_microusd"], "max_cost_microusd"
        ),
        max_context_bytes=_integer(
            payload["max_context_bytes"], "max_context_bytes"
        ),
        max_parallelism=_integer(payload["max_parallelism"], "max_parallelism"),
        min_modeled_success_probability=float(probability),
        provider_limits=provider_limits,
    )
