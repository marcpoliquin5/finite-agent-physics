"""Content-addressed, numeric explanations for recorded scheduler decisions.

The records in this module are post-hoc facts derived from public contracts,
envelopes, schedule entries, and events.  They do not claim access to model
reasoning, intent, or a causal account of why a model produced an output.

Inputs are accepted only when exact concrete FINITE contract types reproduce under
the deterministic ``Scheduler`` with canonically equal public fields.  That replay
check deliberately fails closed on subclasses, missing, unknown, or altered control
events before any explanation is emitted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .contracts import (
    BackendProfile,
    Effect,
    EffectClass,
    RunEnvelope,
    TaskContract,
)
from .events import Event, EventType
from .graph import ExecutionGraph, GraphValidationError
from .scheduler import ScheduleEntry, SchedulePolicy, ScheduleResult, Scheduler
from .serialization import content_digest, normalize


EXPLANATION_SCHEMA_VERSION = "finite-decision-explanation/v1"
EXPLANATION_BUNDLE_SCHEMA_VERSION = "finite-decision-explanation-bundle/v1"
DERIVATION_SCOPE = "post_hoc_recorded_numeric_facts"


class DecisionExplanationError(ValueError):
    """Raised when source decisions cannot be explained without guessing."""


class Comparison(str, Enum):
    """Machine-readable relation between an observation and an optional limit."""

    NONE = "none"
    EQUAL = "equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"


class ExplanationAction(str, Enum):
    """Recorded action represented by one source event."""

    RUN_START = "run_start"
    PROFILE_SELECTION = "profile_selection"
    DEGRADED_PROFILE_SELECTION = "degraded_profile_selection"
    DISPATCH_ADMITTED = "dispatch_admitted"
    TASK_COMPLETION = "task_completion"
    OPTIONAL_SHED = "optional_shed"
    TASK_CANCELLATION = "task_cancellation"
    RUN_COMPLETION = "run_completion"
    RUN_REFUSAL = "run_refusal"


@dataclass(frozen=True, slots=True)
class NumericFact:
    """One public measurement, optionally compared with a declared limit."""

    metric_id: str
    observed: int | float
    unit: str
    comparison: Comparison = Comparison.NONE
    limit: int | float | None = None

    def verify(self) -> bool:
        if type(self) is not NumericFact:
            return False
        if type(self.metric_id) is not str or type(self.unit) is not str:
            return False
        if not self.metric_id or not self.unit:
            return False
        if not isinstance(self.comparison, Comparison):
            return False
        if isinstance(self.observed, bool) or not isinstance(self.observed, (int, float)):
            return False
        if not math.isfinite(float(self.observed)):
            return False
        if self.limit is None:
            return self.comparison is Comparison.NONE
        if isinstance(self.limit, bool) or not isinstance(self.limit, (int, float)):
            return False
        if not math.isfinite(float(self.limit)):
            return False
        if self.comparison is Comparison.EQUAL:
            return self.observed == self.limit
        if self.comparison is Comparison.LESS_THAN_OR_EQUAL:
            return self.observed <= self.limit
        if self.comparison is Comparison.GREATER_THAN_OR_EQUAL:
            return self.observed >= self.limit
        return False

    def as_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "observed": self.observed,
            "unit": self.unit,
            "comparison": self.comparison.value,
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class DecisionExplanationRecord:
    """Digest-protected explanation of exactly one recorded event."""

    record_id: str
    schema_version: str
    derivation_scope: str
    reasoning_access: bool
    source_graph_digest: str
    source_envelope_digest: str
    source_schedule_digest: str
    source_event_digest: str
    source_event_sequence: int
    source_event_type: str
    source_time_ms: int
    policy: str
    action: ExplanationAction
    task_id: str | None
    dependency_ids: tuple[str, ...]
    selected_backend: str | None
    selected_provider: str | None
    reason_code: str | None
    source_recorded_reason: str | None
    rule_ids: tuple[str, ...]
    numeric_facts: tuple[NumericFact, ...]

    @classmethod
    def create(
        cls,
        *,
        source_graph_digest: str,
        source_envelope_digest: str,
        source_schedule_digest: str,
        event: Event,
        policy: SchedulePolicy,
        action: ExplanationAction,
        task_id: str | None,
        dependency_ids: Iterable[str] = (),
        selected_backend: str | None = None,
        selected_provider: str | None = None,
        reason_code: str | None = None,
        source_recorded_reason: str | None = None,
        rule_ids: Iterable[str],
        numeric_facts: Iterable[NumericFact],
    ) -> DecisionExplanationRecord:
        if cls is not DecisionExplanationRecord:
            raise DecisionExplanationError("explanation record subclasses are not accepted")
        dependencies = tuple(sorted(set(dependency_ids)))
        rules = tuple(sorted(set(rule_ids)))
        facts = tuple(sorted(numeric_facts, key=lambda item: item.metric_id))
        event_digest = _address(content_digest(event))
        material = cls._material(
            source_graph_digest=source_graph_digest,
            source_envelope_digest=source_envelope_digest,
            source_schedule_digest=source_schedule_digest,
            source_event_digest=event_digest,
            source_event_sequence=event.sequence,
            source_event_type=event.event_type.value,
            source_time_ms=event.time_ms,
            policy=policy.value,
            action=action,
            task_id=task_id,
            dependency_ids=dependencies,
            selected_backend=selected_backend,
            selected_provider=selected_provider,
            reason_code=reason_code,
            source_recorded_reason=source_recorded_reason,
            rule_ids=rules,
            numeric_facts=facts,
        )
        record = cls(
            record_id=_address(content_digest(material)),
            schema_version=EXPLANATION_SCHEMA_VERSION,
            derivation_scope=DERIVATION_SCOPE,
            reasoning_access=False,
            source_graph_digest=source_graph_digest,
            source_envelope_digest=source_envelope_digest,
            source_schedule_digest=source_schedule_digest,
            source_event_digest=event_digest,
            source_event_sequence=event.sequence,
            source_event_type=event.event_type.value,
            source_time_ms=event.time_ms,
            policy=policy.value,
            action=action,
            task_id=task_id,
            dependency_ids=dependencies,
            selected_backend=selected_backend,
            selected_provider=selected_provider,
            reason_code=reason_code,
            source_recorded_reason=source_recorded_reason,
            rule_ids=rules,
            numeric_facts=facts,
        )
        if not record.verify():
            raise DecisionExplanationError("explanation record is structurally invalid")
        return record

    @staticmethod
    def _material(
        *,
        source_graph_digest: str,
        source_envelope_digest: str,
        source_schedule_digest: str,
        source_event_digest: str,
        source_event_sequence: int,
        source_event_type: str,
        source_time_ms: int,
        policy: str,
        action: ExplanationAction,
        task_id: str | None,
        dependency_ids: tuple[str, ...],
        selected_backend: str | None,
        selected_provider: str | None,
        reason_code: str | None,
        source_recorded_reason: str | None,
        rule_ids: tuple[str, ...],
        numeric_facts: tuple[NumericFact, ...],
    ) -> dict[str, object]:
        return {
            "schema_version": EXPLANATION_SCHEMA_VERSION,
            "derivation_scope": DERIVATION_SCOPE,
            "reasoning_access": False,
            "source_graph_digest": source_graph_digest,
            "source_envelope_digest": source_envelope_digest,
            "source_schedule_digest": source_schedule_digest,
            "source_event_digest": source_event_digest,
            "source_event_sequence": source_event_sequence,
            "source_event_type": source_event_type,
            "source_time_ms": source_time_ms,
            "policy": policy,
            "action": action,
            "task_id": task_id,
            "dependency_ids": dependency_ids,
            "selected_backend": selected_backend,
            "selected_provider": selected_provider,
            "reason_code": reason_code,
            "source_recorded_reason": source_recorded_reason,
            "rule_ids": rule_ids,
            "numeric_facts": numeric_facts,
        }

    def verify(self) -> bool:
        if type(self) is not DecisionExplanationRecord:
            return False
        if type(self.record_id) is not str or not _is_address(self.record_id):
            return False
        if self.schema_version != EXPLANATION_SCHEMA_VERSION:
            return False
        if self.derivation_scope != DERIVATION_SCOPE or self.reasoning_access:
            return False
        addresses = (
            self.source_graph_digest,
            self.source_envelope_digest,
            self.source_schedule_digest,
            self.source_event_digest,
        )
        if any(not _is_address(value) for value in addresses):
            return False
        if self.source_event_sequence <= 0 or self.source_time_ms < 0:
            return False
        if self.source_event_type not in {item.value for item in EventType}:
            return False
        if self.policy not in {item.value for item in SchedulePolicy}:
            return False
        if not isinstance(self.action, ExplanationAction):
            return False
        if self.task_id is not None and (not isinstance(self.task_id, str) or not self.task_id):
            return False
        if any(not isinstance(item, str) or not item for item in self.dependency_ids):
            return False
        if tuple(sorted(set(self.dependency_ids))) != self.dependency_ids:
            return False
        if any(not isinstance(item, str) or not item for item in self.rule_ids):
            return False
        if tuple(sorted(set(self.rule_ids))) != self.rule_ids or not self.rule_ids:
            return False
        if (self.selected_backend is None) != (self.selected_provider is None):
            return False
        if self.selected_backend is not None and (
            not isinstance(self.selected_backend, str)
            or not isinstance(self.selected_provider, str)
            or not self.selected_backend
            or not self.selected_provider
        ):
            return False
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code
        ):
            return False
        if self.source_recorded_reason is not None and not isinstance(
            self.source_recorded_reason, str
        ):
            return False
        if type(self.dependency_ids) is not tuple or type(self.rule_ids) is not tuple:
            return False
        if type(self.numeric_facts) is not tuple or any(
            type(fact) is not NumericFact for fact in self.numeric_facts
        ):
            return False
        metric_ids = tuple(fact.metric_id for fact in self.numeric_facts)
        if tuple(sorted(metric_ids)) != metric_ids or len(metric_ids) != len(set(metric_ids)):
            return False
        if not self.numeric_facts or not all(
            NumericFact.verify(fact) for fact in self.numeric_facts
        ):
            return False
        material = self._material(
            source_graph_digest=self.source_graph_digest,
            source_envelope_digest=self.source_envelope_digest,
            source_schedule_digest=self.source_schedule_digest,
            source_event_digest=self.source_event_digest,
            source_event_sequence=self.source_event_sequence,
            source_event_type=self.source_event_type,
            source_time_ms=self.source_time_ms,
            policy=self.policy,
            action=self.action,
            task_id=self.task_id,
            dependency_ids=self.dependency_ids,
            selected_backend=self.selected_backend,
            selected_provider=self.selected_provider,
            reason_code=self.reason_code,
            source_recorded_reason=self.source_recorded_reason,
            rule_ids=self.rule_ids,
            numeric_facts=self.numeric_facts,
        )
        return self.record_id == _address(content_digest(material))

    def as_dict(self) -> dict[str, object]:
        payload = self._material(
            source_graph_digest=self.source_graph_digest,
            source_envelope_digest=self.source_envelope_digest,
            source_schedule_digest=self.source_schedule_digest,
            source_event_digest=self.source_event_digest,
            source_event_sequence=self.source_event_sequence,
            source_event_type=self.source_event_type,
            source_time_ms=self.source_time_ms,
            policy=self.policy,
            action=self.action,
            task_id=self.task_id,
            dependency_ids=self.dependency_ids,
            selected_backend=self.selected_backend,
            selected_provider=self.selected_provider,
            reason_code=self.reason_code,
            source_recorded_reason=self.source_recorded_reason,
            rule_ids=self.rule_ids,
            numeric_facts=self.numeric_facts,
        )
        return {"record_id": self.record_id, **normalize(payload)}


@dataclass(frozen=True, slots=True)
class DecisionExplanationBundle:
    """A complete, ordered explanation set for one deterministic schedule."""

    bundle_id: str
    schema_version: str
    source_graph_digest: str
    source_envelope_digest: str
    source_schedule_digest: str
    source_event_digests: tuple[str, ...]
    records: tuple[DecisionExplanationRecord, ...]

    @classmethod
    def create(
        cls,
        *,
        source_graph_digest: str,
        source_envelope_digest: str,
        source_schedule_digest: str,
        source_event_digests: Iterable[str],
        records: Iterable[DecisionExplanationRecord],
    ) -> DecisionExplanationBundle:
        if cls is not DecisionExplanationBundle:
            raise DecisionExplanationError("explanation bundle subclasses are not accepted")
        event_digests = tuple(source_event_digests)
        ordered_records = tuple(records)
        material = cls._material(
            source_graph_digest,
            source_envelope_digest,
            source_schedule_digest,
            event_digests,
            tuple(record.record_id for record in ordered_records),
        )
        bundle = cls(
            bundle_id=_address(content_digest(material)),
            schema_version=EXPLANATION_BUNDLE_SCHEMA_VERSION,
            source_graph_digest=source_graph_digest,
            source_envelope_digest=source_envelope_digest,
            source_schedule_digest=source_schedule_digest,
            source_event_digests=event_digests,
            records=ordered_records,
        )
        if not bundle.verify():
            raise DecisionExplanationError(
                "explanation generation did not cover every source event exactly once"
            )
        return bundle

    @staticmethod
    def _material(
        source_graph_digest: str,
        source_envelope_digest: str,
        source_schedule_digest: str,
        source_event_digests: tuple[str, ...],
        record_ids: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "schema_version": EXPLANATION_BUNDLE_SCHEMA_VERSION,
            "derivation_scope": DERIVATION_SCOPE,
            "reasoning_access": False,
            "source_graph_digest": source_graph_digest,
            "source_envelope_digest": source_envelope_digest,
            "source_schedule_digest": source_schedule_digest,
            "source_event_digests": source_event_digests,
            "record_ids": record_ids,
        }

    def verify(self) -> bool:
        if type(self) is not DecisionExplanationBundle:
            return False
        if type(self.bundle_id) is not str or not _is_address(self.bundle_id):
            return False
        if self.schema_version != EXPLANATION_BUNDLE_SCHEMA_VERSION:
            return False
        if any(
            not _is_address(value)
            for value in (
                self.source_graph_digest,
                self.source_envelope_digest,
                self.source_schedule_digest,
            )
        ):
            return False
        if type(self.source_event_digests) is not tuple or type(self.records) is not tuple:
            return False
        if any(not _is_address(digest) for digest in self.source_event_digests):
            return False
        if any(type(record) is not DecisionExplanationRecord for record in self.records):
            return False
        if len(self.source_event_digests) != len(self.records):
            return False
        if not self.records:
            return False
        expected_sequences = tuple(range(1, len(self.records) + 1))
        if tuple(record.source_event_sequence for record in self.records) != expected_sequences:
            return False
        if (
            tuple(record.source_event_digest for record in self.records)
            != self.source_event_digests
        ):
            return False
        for record in self.records:
            if not DecisionExplanationRecord.verify(record):
                return False
            if (
                record.source_graph_digest != self.source_graph_digest
                or record.source_envelope_digest != self.source_envelope_digest
                or record.source_schedule_digest != self.source_schedule_digest
            ):
                return False
        material = self._material(
            self.source_graph_digest,
            self.source_envelope_digest,
            self.source_schedule_digest,
            self.source_event_digests,
            tuple(record.record_id for record in self.records),
        )
        return self.bundle_id == _address(content_digest(material))

    def verify_against(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        result: ScheduleResult,
    ) -> bool:
        if type(self) is not DecisionExplanationBundle or not DecisionExplanationBundle.verify(
            self
        ):
            return False
        try:
            regenerated = explain_schedule(graph, envelope, result)
            return DecisionExplanationBundle.verify(regenerated) and content_digest(
                self
            ) == content_digest(regenerated)
        except (
            DecisionExplanationError,
            GraphValidationError,
            TypeError,
            ValueError,
            OverflowError,
        ):
            return False

    def as_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "schema_version": self.schema_version,
            "derivation_scope": DERIVATION_SCOPE,
            "reasoning_access": False,
            "source_graph_digest": self.source_graph_digest,
            "source_envelope_digest": self.source_envelope_digest,
            "source_schedule_digest": self.source_schedule_digest,
            "source_event_digests": list(self.source_event_digests),
            "records": [record.as_dict() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class _EventSnapshot:
    completed_before: tuple[str, ...]
    skipped_before: tuple[str, ...]
    cancelled_before: tuple[str, ...]
    running_before: tuple[str, ...]
    completed_after: tuple[str, ...]
    skipped_after: tuple[str, ...]
    cancelled_after: tuple[str, ...]
    running_after: tuple[str, ...]
    tokens_before: int
    cost_before: int
    context_before: int
    success_before: float
    tokens_after: int
    cost_after: int
    context_after: int
    success_after: float


@dataclass(frozen=True, slots=True)
class _ValidatedSchedule:
    entries: dict[str, ScheduleEntry]
    profiles: dict[str, BackendProfile]
    planned_ends: dict[str, int]
    snapshots: dict[int, _EventSnapshot]


def explain_schedule(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    result: ScheduleResult,
) -> DecisionExplanationBundle:
    """Emit a deterministic explanation for every event or fail without output.

    This API explains an existing ``ScheduleResult``.  It does not expose or
    reconstruct chain-of-thought.  Exact deterministic replay is an integrity
    precondition, while the emitted facts come only from the supplied public data.
    """

    validated = _validate_and_index(graph, envelope, result)
    graph_digest = _address(content_digest(graph))
    envelope_digest = _address(content_digest(envelope))
    schedule_digest = _address(content_digest(result))
    records = tuple(
        _record_for_event(
            graph,
            envelope,
            result,
            event,
            validated,
            graph_digest,
            envelope_digest,
            schedule_digest,
        )
        for event in result.events
    )
    event_digests = tuple(_address(content_digest(event)) for event in result.events)
    return DecisionExplanationBundle.create(
        source_graph_digest=graph_digest,
        source_envelope_digest=envelope_digest,
        source_schedule_digest=schedule_digest,
        source_event_digests=event_digests,
        records=records,
    )


def _validate_and_index(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    result: ScheduleResult,
) -> _ValidatedSchedule:
    if type(graph) is not ExecutionGraph or type(envelope) is not RunEnvelope:
        raise TypeError("graph and envelope must use FINITE contract types")
    if type(result) is not ScheduleResult:
        raise TypeError("result must be a ScheduleResult")
    _validate_exact_contract_tree(graph, envelope)
    graph.validate()
    envelope_errors = envelope.validate()
    if envelope_errors:
        raise GraphValidationError("; ".join(envelope_errors))
    if type(result.policy) is not SchedulePolicy:
        raise DecisionExplanationError("schedule policy is unknown or malformed")
    if not isinstance(result.entries, tuple) or not all(
        type(entry) is ScheduleEntry for entry in result.entries
    ):
        raise DecisionExplanationError("schedule entries are malformed")
    if type(result.skipped) is not tuple or any(
        type(task_id) is not str for task_id in result.skipped
    ):
        raise DecisionExplanationError("schedule skipped-task identities are malformed")
    if not isinstance(result.events, tuple) or not result.events:
        raise DecisionExplanationError("schedule has no complete event stream")
    for expected_sequence, event in enumerate(result.events, start=1):
        if type(event) is not Event or type(event.event_type) is not EventType:
            raise DecisionExplanationError("event stream contains an unknown event type")
        if (
            type(event.sequence) is not int
            or type(event.time_ms) is not int
            or event.sequence != expected_sequence
            or event.time_ms < 0
        ):
            raise DecisionExplanationError("event sequence or timestamp is malformed")
        if event.task_id is not None and type(event.task_id) is not str:
            raise DecisionExplanationError("event task identity is malformed")
        if type(event.details) is not tuple:
            raise DecisionExplanationError("event details must be an immutable tuple")
        keys: list[str] = []
        for detail in event.details:
            if type(detail) is not tuple or len(detail) != 2 or type(detail[0]) is not str:
                raise DecisionExplanationError("event details contain a malformed fact")
            keys.append(detail[0])
        if len(keys) != len(set(keys)):
            raise DecisionExplanationError("event details contain duplicate fact IDs")

    replay = Scheduler().schedule(graph, envelope, result.policy)
    if content_digest(result) != content_digest(replay):
        raise DecisionExplanationError(
            "source schedule public fields do not match deterministic replay; explanation refused"
        )

    entries = {entry.task_id: entry for entry in result.entries}
    profiles: dict[str, BackendProfile] = {}
    for task_id, entry in entries.items():
        task = graph.by_id[task_id]
        matching = [
            profile
            for profile in task.profiles
            if profile.name == entry.backend and profile.provider == entry.provider
        ]
        if len(matching) != 1:
            raise DecisionExplanationError(
                f"entry {task_id!r} references an unknown or ambiguous backend"
            )
        profiles[task_id] = matching[0]

    completed: set[str] = set()
    skipped: set[str] = set()
    cancelled: set[str] = set()
    running: set[str] = set()
    planned_ends: dict[str, int] = {}
    selected: set[str] = set()
    tokens = 0
    cost = 0
    context = 0
    success_probability = 1.0
    snapshots: dict[int, _EventSnapshot] = {}

    for event in result.events:
        before = (
            tuple(sorted(completed)),
            tuple(sorted(skipped)),
            tuple(sorted(cancelled)),
            tuple(sorted(running)),
            tokens,
            cost,
            context,
            success_probability,
        )
        if event.event_type is EventType.PROFILE_SELECTED:
            if event.task_id is None or event.task_id in selected:
                raise DecisionExplanationError("profile event does not identify one new task")
            profile = profiles[event.task_id]
            selected.add(event.task_id)
            tokens += profile.total_tokens
            cost += profile.cost_microusd
            context += profile.context_bytes
            success_probability *= 1.0 - profile.failure_probability
        elif event.event_type is EventType.TASK_STARTED:
            if event.task_id is None or event.task_id not in selected:
                raise DecisionExplanationError("dispatch has no preceding profile selection")
            running.add(event.task_id)
            planned_ends[event.task_id] = int(dict(event.details)["end_ms"])
        elif event.event_type is EventType.TASK_COMPLETED:
            if event.task_id is None or event.task_id not in running:
                raise DecisionExplanationError("completion has no admitted dispatch")
            running.remove(event.task_id)
            completed.add(event.task_id)
        elif event.event_type is EventType.TASK_CANCELLED:
            if event.task_id is None or event.task_id not in running:
                raise DecisionExplanationError("cancellation has no admitted dispatch")
            running.remove(event.task_id)
            cancelled.add(event.task_id)
        elif event.event_type is EventType.TASK_SKIPPED:
            if event.task_id is None or event.task_id in selected:
                raise DecisionExplanationError("skip conflicts with an admitted dispatch")
            skipped.add(event.task_id)

        snapshots[event.sequence] = _EventSnapshot(
            completed_before=before[0],
            skipped_before=before[1],
            cancelled_before=before[2],
            running_before=before[3],
            completed_after=tuple(sorted(completed)),
            skipped_after=tuple(sorted(skipped)),
            cancelled_after=tuple(sorted(cancelled)),
            running_after=tuple(sorted(running)),
            tokens_before=before[4],
            cost_before=before[5],
            context_before=before[6],
            success_before=before[7],
            tokens_after=tokens,
            cost_after=cost,
            context_after=context,
            success_after=success_probability,
        )

    return _ValidatedSchedule(entries, profiles, planned_ends, snapshots)


def _validate_exact_contract_tree(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
) -> None:
    """Reject behavior-bearing subclasses and mutable contract containers."""

    if type(graph.tasks) is not tuple:
        raise TypeError("graph tasks must use an exact immutable tuple")
    for task in graph.tasks:
        if type(task) is not TaskContract:
            raise TypeError("graph tasks must use exact TaskContract instances")
        if type(task.profiles) is not tuple:
            raise TypeError("task profiles must use exact immutable tuples")
        if type(task.dependencies) is not tuple:
            raise TypeError("task dependencies must use exact immutable tuples")
        if any(type(dependency) is not str for dependency in task.dependencies):
            raise TypeError("task dependencies must contain exact strings")
        if type(task.effect) is not Effect or type(task.effect.kind) is not EffectClass:
            raise TypeError("task effects must use exact Effect contracts")
        if any(type(profile) is not BackendProfile for profile in task.profiles):
            raise TypeError("task profiles must use exact BackendProfile instances")

    if type(envelope.provider_limits) is not tuple:
        raise TypeError("provider limits must use an exact immutable tuple")
    if any(
        type(pair) is not tuple
        or len(pair) != 2
        or type(pair[0]) is not str
        or type(pair[1]) is not int
        for pair in envelope.provider_limits
    ):
        raise TypeError("provider limits must contain exact (str, int) tuples")


def _record_for_event(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    result: ScheduleResult,
    event: Event,
    validated: _ValidatedSchedule,
    graph_digest: str,
    envelope_digest: str,
    schedule_digest: str,
) -> DecisionExplanationRecord:
    snapshot = validated.snapshots[event.sequence]
    task = graph.by_id.get(event.task_id) if event.task_id is not None else None
    entry = validated.entries.get(event.task_id) if event.task_id is not None else None
    profile = validated.profiles.get(event.task_id) if event.task_id is not None else None
    action = _action_for_event(event, task, profile)
    reason = _recorded_reason(event)
    reason_code = _reason_code(event, reason)
    rules = _rule_ids(event, action, reason_code)
    facts = _numeric_facts(
        graph,
        envelope,
        result,
        event,
        snapshot,
        task,
        entry,
        profile,
        validated.planned_ends,
    )
    return DecisionExplanationRecord.create(
        source_graph_digest=graph_digest,
        source_envelope_digest=envelope_digest,
        source_schedule_digest=schedule_digest,
        event=event,
        policy=result.policy,
        action=action,
        task_id=event.task_id,
        dependency_ids=task.dependencies if task is not None else (),
        selected_backend=entry.backend if entry is not None else None,
        selected_provider=entry.provider if entry is not None else None,
        reason_code=reason_code,
        source_recorded_reason=reason,
        rule_ids=rules,
        numeric_facts=facts,
    )


def _action_for_event(
    event: Event,
    task: TaskContract | None,
    profile: BackendProfile | None,
) -> ExplanationAction:
    if event.event_type is EventType.RUN_STARTED:
        return ExplanationAction.RUN_START
    if event.event_type is EventType.PROFILE_SELECTED:
        if task is None or profile is None:
            raise DecisionExplanationError("profile event references unknown task data")
        qualified = [item for item in task.profiles if item.quality >= task.min_quality]
        if profile.quality < max(item.quality for item in qualified):
            return ExplanationAction.DEGRADED_PROFILE_SELECTION
        return ExplanationAction.PROFILE_SELECTION
    mapping = {
        EventType.TASK_STARTED: ExplanationAction.DISPATCH_ADMITTED,
        EventType.TASK_COMPLETED: ExplanationAction.TASK_COMPLETION,
        EventType.TASK_SKIPPED: ExplanationAction.OPTIONAL_SHED,
        EventType.TASK_CANCELLED: ExplanationAction.TASK_CANCELLATION,
        EventType.RUN_COMPLETED: ExplanationAction.RUN_COMPLETION,
        EventType.RUN_FAILED: ExplanationAction.RUN_REFUSAL,
    }
    try:
        return mapping[event.event_type]
    except KeyError as exc:
        raise DecisionExplanationError("source event has no explanation mapping") from exc


def _recorded_reason(event: Event) -> str | None:
    reason = dict(event.details).get("reason")
    return reason if isinstance(reason, str) else None


def _reason_code(event: Event, reason: str | None) -> str | None:
    if event.event_type is EventType.TASK_SKIPPED:
        return {
            "dependency skipped": "dependency_skipped",
            "protected resource or deadline envelope": "protected_envelope_preserved",
        }.get(reason, "recorded_skip")
    if event.event_type is EventType.TASK_CANCELLED:
        return "run_refused"
    if event.event_type is not EventType.RUN_FAILED:
        return None
    if reason is None:
        return "recorded_failure"
    if reason.startswith("protected task ") and reason.endswith(" has no admissible backend plan"):
        return "protected_task_no_admissible_profile"
    if reason.startswith("required task ") and " depends on skipped work " in reason:
        return "required_dependency_skipped"
    if reason == "run deadline exceeded":
        return "run_deadline_exceeded"
    if reason.startswith("scheduler deadlock"):
        return "scheduler_deadlock"
    return "recorded_failure"


def _rule_ids(
    event: Event,
    action: ExplanationAction,
    reason_code: str | None,
) -> tuple[str, ...]:
    prefix = "FINITE.M33"
    rules: dict[ExplanationAction, tuple[str, ...]] = {
        ExplanationAction.RUN_START: (
            f"{prefix}.ENVELOPE_DECLARED",
            f"{prefix}.GRAPH_VALIDATED",
            f"{prefix}.POLICY_RECORDED",
        ),
        ExplanationAction.PROFILE_SELECTION: (
            f"{prefix}.PROFILE_ENTRY_MATCH",
            f"{prefix}.QUALITY_FLOOR_MET",
            f"{prefix}.RESOURCE_LIMITS_RECORDED",
        ),
        ExplanationAction.DEGRADED_PROFILE_SELECTION: (
            f"{prefix}.PROFILE_ENTRY_MATCH",
            f"{prefix}.QUALITY_FLOOR_MET",
            f"{prefix}.QUALITY_DELTA_RECORDED",
            f"{prefix}.RESOURCE_LIMITS_RECORDED",
        ),
        ExplanationAction.DISPATCH_ADMITTED: (
            f"{prefix}.DEPENDENCIES_COMPLETED",
            f"{prefix}.DISPATCH_ENTRY_MATCH",
            f"{prefix}.PROFILE_PRECEDES_DISPATCH",
        ),
        ExplanationAction.TASK_COMPLETION: (
            f"{prefix}.COMPLETION_TIME_MATCH",
            f"{prefix}.DISPATCH_TERMINAL_PAIR",
        ),
        ExplanationAction.OPTIONAL_SHED: (
            f"{prefix}.NO_DISPATCH_FOR_SKIPPED_TASK",
            f"{prefix}.OPTIONAL_WORK_SHED",
        ),
        ExplanationAction.TASK_CANCELLATION: (
            f"{prefix}.CANCELLATION_TIME_MATCH",
            f"{prefix}.DISPATCH_TERMINAL_PAIR",
            f"{prefix}.RUN_REFUSAL_CANCELLATION",
        ),
        ExplanationAction.RUN_COMPLETION: (
            f"{prefix}.REQUIRED_WORK_COMPLETED",
            f"{prefix}.RUN_LIMITS_RECORDED",
            f"{prefix}.RUN_TOTALS_MATCH_ENTRIES",
        ),
        ExplanationAction.RUN_REFUSAL: (
            f"{prefix}.RUN_REFUSAL_RECORDED",
            f"{prefix}.RUN_TERMINAL_EVENT_MATCH",
            f"{prefix}.RUN_TOTALS_MATCH_ENTRIES",
        ),
    }
    selected = list(rules[action])
    if reason_code:
        selected.append(f"{prefix}.REASON.{reason_code.upper()}")
    if event.event_type is EventType.TASK_SKIPPED and reason_code == "dependency_skipped":
        selected.append(f"{prefix}.SKIPPED_DEPENDENCY_PROPAGATION")
    return tuple(selected)


def _numeric_facts(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    result: ScheduleResult,
    event: Event,
    snapshot: _EventSnapshot,
    task: TaskContract | None,
    entry: ScheduleEntry | None,
    profile: BackendProfile | None,
    planned_ends: dict[str, int],
) -> tuple[NumericFact, ...]:
    common = [
        _fact("event_sequence", event.sequence, "count"),
        _fact("event_time_ms", event.time_ms, "ms"),
    ]
    if event.event_type is EventType.RUN_STARTED:
        required = sum(not item.optional for item in graph.tasks)
        optional = len(graph.tasks) - required
        return tuple(
            common
            + [
                _fact("graph_optional_task_count", optional, "count"),
                _fact("graph_required_task_count", required, "count"),
                _fact("graph_task_count", len(graph.tasks), "count"),
                _fact("limit_context_bytes", envelope.max_context_bytes, "bytes"),
                _fact("limit_cost_microusd", envelope.max_cost_microusd, "microusd"),
                _fact("limit_deadline_ms", envelope.deadline_ms, "ms"),
                _fact("limit_global_parallelism", envelope.max_parallelism, "count"),
                _fact(
                    "limit_modeled_success_probability",
                    envelope.min_modeled_success_probability,
                    "probability",
                ),
                _fact("limit_tokens", envelope.max_tokens, "tokens"),
            ]
        )

    if task is not None and entry is not None and profile is not None:
        common.extend(
            _profile_facts(
                envelope,
                result,
                event,
                snapshot,
                task,
                entry,
                profile,
                planned_ends,
            )
        )
        if event.event_type is EventType.TASK_COMPLETED:
            common.extend(
                [
                    _fact("active_tasks_after", len(snapshot.running_after), "count"),
                    _fact("active_tasks_before", len(snapshot.running_before), "count"),
                    _fact("completed_task_count", len(snapshot.completed_after), "count"),
                ]
            )
        elif event.event_type is EventType.TASK_CANCELLED:
            planned_end = planned_ends[task.task_id]
            common.extend(
                [
                    _fact("active_tasks_after", len(snapshot.running_after), "count"),
                    _fact("active_tasks_before", len(snapshot.running_before), "count"),
                    _fact("cancelled_task_count", len(snapshot.cancelled_after), "count"),
                    _fact("elapsed_before_cancellation_ms", event.time_ms - entry.start_ms, "ms"),
                    _fact("planned_remaining_ms", planned_end - event.time_ms, "ms"),
                ]
            )
        return tuple(common)

    if event.event_type is EventType.TASK_SKIPPED and task is not None:
        qualified = [item for item in task.profiles if item.quality >= task.min_quality]
        details = dict(event.details)
        dependency_ids = details.get("dependencies", [])
        skipped_dependencies = len(dependency_ids) if isinstance(dependency_ids, list) else 0
        common.extend(
            [
                _fact("cumulative_context_bytes", snapshot.context_after, "bytes"),
                _fact("cumulative_cost_microusd", snapshot.cost_after, "microusd"),
                _fact("cumulative_tokens", snapshot.tokens_after, "tokens"),
                _fact("declared_task_value", task.value, "value"),
                _fact("dependency_count", len(task.dependencies), "count"),
                _fact("optional_flag", 1, "boolean"),
                _fact("qualified_profile_count", len(qualified), "count"),
                _fact(
                    "qualified_profile_max_quality",
                    max(item.quality for item in qualified),
                    "probability",
                ),
                _fact(
                    "qualified_profile_min_context_bytes",
                    min(item.context_bytes for item in qualified),
                    "bytes",
                ),
                _fact(
                    "qualified_profile_min_cost_microusd",
                    min(item.cost_microusd for item in qualified),
                    "microusd",
                ),
                _fact(
                    "qualified_profile_min_duration_p95_ms",
                    min(item.duration_ms_p95 for item in qualified),
                    "ms",
                ),
                _fact(
                    "qualified_profile_min_tokens",
                    min(item.total_tokens for item in qualified),
                    "tokens",
                ),
                _fact(
                    "remaining_context_bytes",
                    envelope.max_context_bytes - snapshot.context_after,
                    "bytes",
                ),
                _fact(
                    "remaining_cost_microusd",
                    envelope.max_cost_microusd - snapshot.cost_after,
                    "microusd",
                ),
                _fact("remaining_deadline_ms", envelope.deadline_ms - event.time_ms, "ms"),
                _fact("remaining_tokens", envelope.max_tokens - snapshot.tokens_after, "tokens"),
                _fact("skipped_dependency_count", skipped_dependencies, "count"),
                _fact("task_min_quality", task.min_quality, "probability"),
            ]
        )
        return tuple(common)

    if event.event_type in {EventType.RUN_COMPLETED, EventType.RUN_FAILED}:
        required_ids = {item.task_id for item in graph.tasks if not item.optional}
        completed_required = len(required_ids & set(snapshot.completed_after))
        terminal_count = (
            len(snapshot.completed_after)
            + len(snapshot.cancelled_after)
            + len(snapshot.skipped_after)
        )
        common.extend(
            [
                _fact("admitted_task_count", len(result.entries), "count"),
                _fact("cancelled_task_count", len(snapshot.cancelled_after), "count"),
                _fact("completed_required_task_count", completed_required, "count"),
                _fact("completed_task_count", len(snapshot.completed_after), "count"),
                _fact(
                    "deadline_slack_ms",
                    envelope.deadline_ms - result.makespan_ms,
                    "ms",
                ),
                _fact(
                    "modeled_success_probability",
                    result.modeled_success_probability,
                    "probability",
                    Comparison.GREATER_THAN_OR_EQUAL,
                    envelope.min_modeled_success_probability,
                ),
                _fact("model_bound_ms", result.model_bound_ms, "ms"),
                _fact("pending_task_count", len(graph.tasks) - terminal_count, "count"),
                _fact("required_task_count", len(required_ids), "count"),
                _fact(
                    "run_makespan_ms",
                    result.makespan_ms,
                    "ms",
                    Comparison.LESS_THAN_OR_EQUAL,
                    envelope.deadline_ms,
                ),
                _fact("skipped_task_count", len(snapshot.skipped_after), "count"),
                _fact(
                    "total_context_bytes",
                    result.total_context_bytes,
                    "bytes",
                    Comparison.LESS_THAN_OR_EQUAL,
                    envelope.max_context_bytes,
                ),
                _fact(
                    "total_cost_microusd",
                    result.total_cost_microusd,
                    "microusd",
                    Comparison.LESS_THAN_OR_EQUAL,
                    envelope.max_cost_microusd,
                ),
                _fact(
                    "total_tokens",
                    result.total_tokens,
                    "tokens",
                    Comparison.LESS_THAN_OR_EQUAL,
                    envelope.max_tokens,
                ),
            ]
        )
        return tuple(common)

    raise DecisionExplanationError("source event lacks numeric explanation facts")


def _profile_facts(
    envelope: RunEnvelope,
    result: ScheduleResult,
    event: Event,
    snapshot: _EventSnapshot,
    task: TaskContract,
    entry: ScheduleEntry,
    profile: BackendProfile,
    planned_ends: dict[str, int],
) -> list[NumericFact]:
    planned_end = planned_ends.get(task.task_id, entry.end_ms)
    task_deadline = min(task.deadline_ms or envelope.deadline_ms, envelope.deadline_ms)
    qualified_max_quality = max(
        item.quality for item in task.profiles if item.quality >= task.min_quality
    )
    # ``ScheduleResult`` intentionally has no secondary index.  Count from entries
    # here so every number still comes from the recorded public witness.
    entry_index = {item.task_id: item for item in result.entries}
    provider_running_before = sum(
        entry_index[task_id].provider == profile.provider for task_id in snapshot.running_before
    )
    observed_or_predicted_global_running = len(snapshot.running_before) + (
        1 if event.event_type in {EventType.PROFILE_SELECTED, EventType.TASK_STARTED} else 0
    )
    observed_or_predicted_provider_running = provider_running_before + (
        1 if event.event_type in {EventType.PROFILE_SELECTED, EventType.TASK_STARTED} else 0
    )
    return [
        _fact(
            "cumulative_context_bytes",
            snapshot.context_after,
            "bytes",
            Comparison.LESS_THAN_OR_EQUAL,
            envelope.max_context_bytes,
        ),
        _fact(
            "cumulative_cost_microusd",
            snapshot.cost_after,
            "microusd",
            Comparison.LESS_THAN_OR_EQUAL,
            envelope.max_cost_microusd,
        ),
        _fact(
            "cumulative_tokens",
            snapshot.tokens_after,
            "tokens",
            Comparison.LESS_THAN_OR_EQUAL,
            envelope.max_tokens,
        ),
        _fact("dependency_count", len(task.dependencies), "count"),
        _fact(
            "dependencies_completed_before",
            len(set(task.dependencies) & set(snapshot.completed_before)),
            "count",
            Comparison.EQUAL,
            len(task.dependencies),
        ),
        _fact(
            "global_running_count",
            observed_or_predicted_global_running,
            "count",
            Comparison.LESS_THAN_OR_EQUAL,
            1 if result.policy is SchedulePolicy.SEQUENTIAL else envelope.max_parallelism,
        ),
        _fact(
            "modeled_run_success_probability",
            result.modeled_success_probability,
            "probability",
            Comparison.GREATER_THAN_OR_EQUAL,
            envelope.min_modeled_success_probability,
        ),
        _fact("optional_flag", int(task.optional), "boolean"),
        _fact("planned_end_ms", planned_end, "ms", Comparison.LESS_THAN_OR_EQUAL, task_deadline),
        _fact("profile_context_bytes", profile.context_bytes, "bytes"),
        _fact("profile_cost_microusd", profile.cost_microusd, "microusd"),
        _fact("profile_duration_p50_ms", profile.duration_ms_p50, "ms"),
        _fact("profile_duration_p95_ms", profile.duration_ms_p95, "ms"),
        _fact("profile_failure_probability", profile.failure_probability, "probability"),
        _fact(
            "profile_quality",
            profile.quality,
            "probability",
            Comparison.GREATER_THAN_OR_EQUAL,
            task.min_quality,
        ),
        _fact(
            "profile_quality_delta_from_declared_max",
            qualified_max_quality - profile.quality,
            "probability",
        ),
        _fact("profile_success_probability", 1.0 - profile.failure_probability, "probability"),
        _fact("profile_tokens", profile.total_tokens, "tokens"),
        _fact(
            "provider_running_count",
            observed_or_predicted_provider_running,
            "count",
            Comparison.LESS_THAN_OR_EQUAL,
            envelope.provider_limit(profile.provider),
        ),
        _fact(
            "remaining_context_bytes", envelope.max_context_bytes - snapshot.context_after, "bytes"
        ),
        _fact(
            "remaining_cost_microusd", envelope.max_cost_microusd - snapshot.cost_after, "microusd"
        ),
        _fact("remaining_tokens", envelope.max_tokens - snapshot.tokens_after, "tokens"),
        _fact("run_deadline_slack_at_planned_end_ms", envelope.deadline_ms - planned_end, "ms"),
        _fact("task_deadline_slack_ms", task_deadline - planned_end, "ms"),
        _fact("task_min_quality", task.min_quality, "probability"),
    ]


def _fact(
    metric_id: str,
    observed: int | float,
    unit: str,
    comparison: Comparison = Comparison.NONE,
    limit: int | float | None = None,
) -> NumericFact:
    fact = NumericFact(metric_id, observed, unit, comparison, limit)
    if not fact.verify():
        raise DecisionExplanationError(f"invalid numeric explanation fact {metric_id!r}")
    return fact


def _address(digest: str) -> str:
    return f"sha256:{digest}"


def _is_address(value: str) -> bool:
    if type(value) is not str or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
