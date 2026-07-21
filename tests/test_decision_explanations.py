from __future__ import annotations

import json
from dataclasses import fields, replace

import pytest

from agent_physics.contracts import BackendProfile, Effect, TaskContract
from agent_physics.decision_explanations import (
    DERIVATION_SCOPE,
    Comparison,
    DecisionExplanationBundle,
    DecisionExplanationError,
    DecisionExplanationRecord,
    ExplanationAction,
    NumericFact,
    explain_schedule,
)
from agent_physics.events import Event, EventType
from agent_physics.examples import miami_eoc_envelope, miami_eoc_graph
from agent_physics.graph import ExecutionGraph
from agent_physics.scheduler import ScheduleEntry, ScheduleResult, Scheduler


def _fact(record: object, metric_id: str) -> NumericFact:
    facts = record.numeric_facts  # type: ignore[attr-defined]
    return next(item for item in facts if item.metric_id == metric_id)


def _miami_result() -> tuple[object, object, ScheduleResult]:
    graph = miami_eoc_graph()
    envelope = miami_eoc_envelope()
    return graph, envelope, Scheduler().schedule(graph, envelope)


def test_feasible_miami_trace_has_one_digest_bound_record_per_event() -> None:
    graph, envelope, result = _miami_result()
    bundle = explain_schedule(graph, envelope, result)  # type: ignore[arg-type]

    assert result.success
    assert bundle.verify()
    assert bundle.verify_against(graph, envelope, result)  # type: ignore[arg-type]
    assert bundle == explain_schedule(graph, envelope, result)  # type: ignore[arg-type]
    assert bundle.bundle_id.startswith("sha256:")
    assert len(bundle.bundle_id) == 71
    assert len(bundle.records) == len(result.events)
    assert [record.source_event_sequence for record in bundle.records] == list(
        range(1, len(result.events) + 1)
    )
    assert [record.source_event_type for record in bundle.records] == [
        event.event_type.value for event in result.events
    ]
    assert all(
        record.verify() and record.record_id.startswith("sha256:") for record in bundle.records
    )
    assert all(record.derivation_scope == DERIVATION_SCOPE for record in bundle.records)
    assert all(record.reasoning_access is False for record in bundle.records)

    profile_events = sum(event.event_type is EventType.PROFILE_SELECTED for event in result.events)
    dispatch_events = sum(event.event_type is EventType.TASK_STARTED for event in result.events)
    completion_events = sum(event.event_type is EventType.TASK_COMPLETED for event in result.events)
    assert (
        sum(
            record.action
            in {
                ExplanationAction.PROFILE_SELECTION,
                ExplanationAction.DEGRADED_PROFILE_SELECTION,
            }
            for record in bundle.records
        )
        == profile_events
    )
    assert (
        sum(record.action is ExplanationAction.DISPATCH_ADMITTED for record in bundle.records)
        == dispatch_events
    )
    assert (
        sum(record.action is ExplanationAction.TASK_COMPLETION for record in bundle.records)
        == completion_events
    )
    assert bundle.records[-1].action is ExplanationAction.RUN_COMPLETION

    selected = next(
        record
        for record in bundle.records
        if record.source_event_type == EventType.PROFILE_SELECTED.value
    )
    assert selected.selected_backend
    assert selected.selected_provider
    assert selected.rule_ids
    assert _fact(selected, "profile_quality").comparison is Comparison.GREATER_THAN_OR_EQUAL
    assert _fact(selected, "cumulative_tokens").limit == envelope.max_tokens  # type: ignore[union-attr]
    assert _fact(selected, "dependencies_completed_before").limit == len(selected.dependency_ids)

    payload = bundle.as_dict()
    assert payload["reasoning_access"] is False
    assert json.loads(json.dumps(payload, allow_nan=False))["bundle_id"] == bundle.bundle_id


def test_constrained_miami_records_degradation_and_optional_shedding() -> None:
    graph = miami_eoc_graph()
    envelope = replace(miami_eoc_envelope(), max_context_bytes=30_000)
    result = Scheduler().schedule(graph, envelope)
    bundle = explain_schedule(graph, envelope, result)

    assert result.success
    assert result.skipped == ("social_signal_scan",)
    shed = [record for record in bundle.records if record.action is ExplanationAction.OPTIONAL_SHED]
    assert len(shed) == 1
    assert shed[0].task_id == "social_signal_scan"
    assert shed[0].reason_code == "protected_envelope_preserved"
    assert shed[0].source_recorded_reason == "protected resource or deadline envelope"
    assert shed[0].selected_backend is None
    assert _fact(shed[0], "optional_flag").observed == 1
    assert _fact(shed[0], "remaining_context_bytes").observed == 16_700

    degraded = [
        record
        for record in bundle.records
        if record.action is ExplanationAction.DEGRADED_PROFILE_SELECTION
    ]
    assert degraded
    assert all(
        _fact(record, "profile_quality_delta_from_declared_max").observed > 0 for record in degraded
    )
    assert all(
        _fact(record, "profile_quality").observed >= _fact(record, "profile_quality").limit
        for record in degraded
    )
    terminal = bundle.records[-1]
    assert terminal.action is ExplanationAction.RUN_COMPLETION
    assert _fact(terminal, "skipped_task_count").observed == 1
    assert _fact(terminal, "pending_task_count").observed == 0


def test_refused_miami_trace_explains_cancellation_and_terminal_refusal() -> None:
    graph = miami_eoc_graph()
    envelope = replace(
        miami_eoc_envelope(),
        deadline_ms=6_200,
        max_parallelism=2,
        provider_limits=(("simulated-watsonx", 1), ("local-fixture", 4)),
    )
    result = Scheduler().schedule(graph, envelope)
    bundle = explain_schedule(graph, envelope, result)

    assert not result.success
    assert any(entry.outcome == "cancelled" for entry in result.entries)
    cancellations = [
        record for record in bundle.records if record.action is ExplanationAction.TASK_CANCELLATION
    ]
    assert [record.task_id for record in cancellations] == ["flood_zones"]
    assert cancellations[0].reason_code == "run_refused"
    assert cancellations[0].source_recorded_reason == "run refused"
    assert _fact(cancellations[0], "cancelled_task_count").observed == 1
    assert _fact(cancellations[0], "planned_remaining_ms").observed > 0

    refusal = bundle.records[-1]
    assert refusal.action is ExplanationAction.RUN_REFUSAL
    assert refusal.reason_code == "protected_task_no_admissible_profile"
    assert refusal.source_recorded_reason == result.failure_reason
    assert _fact(refusal, "completed_task_count").observed == 2
    assert _fact(refusal, "cancelled_task_count").observed == 1
    assert _fact(refusal, "pending_task_count").observed == 8
    assert _fact(refusal, "run_makespan_ms").limit == envelope.deadline_ms
    assert bundle.verify_against(graph, envelope, result)


@pytest.mark.parametrize("tamper", ["alter_event", "remove_control_event", "unknown_task"])
def test_event_tampering_fails_closed(tamper: str) -> None:
    graph, envelope, result = _miami_result()
    events = list(result.events)

    if tamper == "alter_event":
        index = next(
            index
            for index, event in enumerate(events)
            if event.event_type is EventType.PROFILE_SELECTED
        )
        event = events[index]
        details = dict(event.details)
        details["backend"] = "tampered-backend"
        events[index] = replace(event, details=tuple(details.items()))
    elif tamper == "remove_control_event":
        index = next(
            index
            for index, event in enumerate(events)
            if event.event_type is EventType.TASK_STARTED
        )
        del events[index]
    else:
        index = next(
            index
            for index, event in enumerate(events)
            if event.event_type is EventType.TASK_STARTED
        )
        events[index] = replace(events[index], task_id="unknown-task")

    tampered = replace(result, events=tuple(events))
    with pytest.raises(DecisionExplanationError):
        explain_schedule(graph, envelope, tampered)  # type: ignore[arg-type]


def test_record_and_bundle_tampering_cannot_reuse_content_addresses() -> None:
    graph, envelope, result = _miami_result()
    bundle = explain_schedule(graph, envelope, result)  # type: ignore[arg-type]
    record = bundle.records[1]
    original_fact = record.numeric_facts[0]
    altered_fact = replace(original_fact, observed=original_fact.observed + 1)
    altered_record = replace(
        record,
        numeric_facts=(altered_fact, *record.numeric_facts[1:]),
    )
    altered_bundle = replace(
        bundle,
        records=(bundle.records[0], altered_record, *bundle.records[2:]),
    )

    assert not altered_record.verify()
    assert not altered_bundle.verify()
    assert not bundle.verify_against(
        graph,
        replace(envelope, max_tokens=envelope.max_tokens - 1),  # type: ignore[union-attr]
        result,
    )
    with pytest.raises(DecisionExplanationError):
        DecisionExplanationBundle.create(
            source_graph_digest=bundle.source_graph_digest,
            source_envelope_digest=bundle.source_envelope_digest,
            source_schedule_digest=bundle.source_schedule_digest,
            source_event_digests=bundle.source_event_digests,
            records=bundle.records[:-1],
        )


def test_verify_against_rejects_bundle_subclass_with_overloaded_equality() -> None:
    graph, envelope, result = _miami_result()
    bundle = explain_schedule(graph, envelope, result)  # type: ignore[arg-type]

    class AlwaysEqualBundle(DecisionExplanationBundle):
        def __eq__(self, other: object) -> bool:
            return True

    values = {
        field.name: getattr(bundle, field.name)
        for field in fields(DecisionExplanationBundle)
    }
    values["bundle_id"] = "sha256:" + "0" * 64
    forged = AlwaysEqualBundle(**values)

    assert forged == bundle
    assert not forged.verify()
    assert not forged.verify_against(graph, envelope, result)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["record", "numeric_fact"])
def test_bundle_rejects_nested_explanation_subclasses_with_reused_ids(kind: str) -> None:
    graph, envelope, result = _miami_result()
    bundle = explain_schedule(graph, envelope, result)  # type: ignore[arg-type]
    source_record = bundle.records[0]
    source_fact = source_record.numeric_facts[0]
    altered_fact = replace(source_fact, observed=source_fact.observed + 999)

    if kind == "record":
        class EvilRecord(DecisionExplanationRecord):
            def verify(self) -> bool:
                return True

        record_values = {
            field.name: getattr(source_record, field.name)
            for field in fields(DecisionExplanationRecord)
        }
        record_values["numeric_facts"] = (
            altered_fact,
            *source_record.numeric_facts[1:],
        )
        forged_record = EvilRecord(**record_values)
    else:
        class EvilNumericFact(NumericFact):
            def verify(self) -> bool:
                return True

        forged_fact = EvilNumericFact(
            **{
                field.name: getattr(altered_fact, field.name)
                for field in fields(NumericFact)
            }
        )
        forged_record = replace(
            source_record,
            numeric_facts=(forged_fact, *source_record.numeric_facts[1:]),
        )

    forged_bundle = replace(
        bundle,
        records=(forged_record, *bundle.records[1:]),
    )
    assert forged_record.record_id == source_record.record_id
    assert not forged_bundle.verify()
    assert not forged_bundle.verify_against(  # type: ignore[arg-type]
        graph,
        envelope,
        result,
    )


def test_numeric_facts_reject_non_numeric_non_finite_and_unbounded_relations() -> None:
    assert NumericFact("tokens", 1, "tokens").verify()
    assert NumericFact("tokens", 1, "tokens", Comparison.LESS_THAN_OR_EQUAL, 2).verify()
    assert NumericFact("tokens", 2, "tokens", Comparison.EQUAL, 2).verify()
    assert NumericFact("quality", 0.9, "probability", Comparison.GREATER_THAN_OR_EQUAL, 0.8).verify()
    assert not NumericFact("", 1, "tokens").verify()
    assert not NumericFact("tokens", True, "tokens").verify()
    assert not NumericFact("tokens", float("nan"), "tokens").verify()
    assert not NumericFact("tokens", 1, "tokens", Comparison.LESS_THAN_OR_EQUAL).verify()
    assert not NumericFact("tokens", 1, "tokens", limit=float("inf")).verify()
    assert not NumericFact("tokens", 3, "tokens", Comparison.LESS_THAN_OR_EQUAL, 2).verify()
    assert not NumericFact("quality", 0.1, "probability", Comparison.GREATER_THAN_OR_EQUAL, 0.9).verify()
    assert not NumericFact("tokens", 1, "tokens", Comparison.EQUAL, 2).verify()


def test_replay_rejects_schedule_result_subclass_even_when_equality_lies() -> None:
    graph, envelope, result = _miami_result()

    class AlwaysEqualScheduleResult(ScheduleResult):
        def __eq__(self, other: object) -> bool:
            return True

    forged = AlwaysEqualScheduleResult(
        **{field.name: getattr(result, field.name) for field in fields(ScheduleResult)}
    )
    with pytest.raises(TypeError, match="result must be a ScheduleResult"):
        explain_schedule(graph, envelope, forged)  # type: ignore[arg-type]


def test_replay_compares_canonical_public_fields_not_python_numeric_equality() -> None:
    graph, envelope, result = _miami_result()
    forged = replace(result, total_tokens=float(result.total_tokens))

    # Dataclass/Python equality aliases an integer and its equal-valued float;
    # canonical schedule material deliberately does not.
    assert forged == result
    with pytest.raises(DecisionExplanationError, match="public fields"):
        explain_schedule(graph, envelope, forged)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["task", "profile", "effect"])
def test_replay_rejects_recursive_contract_subclasses(kind: str) -> None:
    graph, envelope, _ = _miami_result()
    tasks = list(graph.tasks)
    source_task = tasks[0]

    if kind == "task":
        class ForgedTask(TaskContract):
            pass

        tasks[0] = ForgedTask(
            **{
                field.name: getattr(source_task, field.name)
                for field in fields(TaskContract)
            }
        )
        match = "exact TaskContract"
    elif kind == "profile":
        class ForgedProfile(BackendProfile):
            pass

        source_profile = source_task.profiles[0]
        profile = ForgedProfile(
            **{
                field.name: getattr(source_profile, field.name)
                for field in fields(BackendProfile)
            }
        )
        tasks[0] = replace(
            source_task,
            profiles=(profile, *source_task.profiles[1:]),
        )
        match = "exact BackendProfile"
    else:
        class ForgedEffect(Effect):
            pass

        source_effect = source_task.effect
        effect = ForgedEffect(
            **{
                field.name: getattr(source_effect, field.name)
                for field in fields(Effect)
            }
        )
        tasks[0] = replace(source_task, effect=effect)
        match = "exact Effect"

    forged_graph = ExecutionGraph(tuple(tasks))
    result = Scheduler().schedule(forged_graph, envelope)
    with pytest.raises(TypeError, match=match):
        explain_schedule(forged_graph, envelope, result)


@pytest.mark.parametrize("kind", ["graph_tasks", "task_profiles", "dependencies"])
def test_replay_rejects_mutable_contract_containers(kind: str) -> None:
    graph, envelope, _ = _miami_result()
    if kind == "graph_tasks":
        forged_graph = ExecutionGraph(list(graph.tasks))  # type: ignore[arg-type]
        match = "graph tasks"
    else:
        tasks = list(graph.tasks)
        source = tasks[0]
        if kind == "task_profiles":
            tasks[0] = replace(source, profiles=list(source.profiles))  # type: ignore[arg-type]
            match = "task profiles"
        else:
            tasks[0] = replace(source, dependencies=list(source.dependencies))  # type: ignore[arg-type]
            match = "task dependencies"
        forged_graph = ExecutionGraph(tuple(tasks))

    result = Scheduler().schedule(forged_graph, envelope)
    with pytest.raises(TypeError, match=match):
        explain_schedule(forged_graph, envelope, result)


@pytest.mark.parametrize("kind", ["event", "entry"])
def test_replay_rejects_nested_contract_subclasses(kind: str) -> None:
    graph, envelope, result = _miami_result()

    if kind == "event":
        class ForgedEvent(Event):
            pass

        source = result.events[0]
        forged_event = ForgedEvent(
            source.sequence,
            source.time_ms,
            source.event_type,
            source.task_id,
            source.details,
        )
        forged = replace(result, events=(forged_event, *result.events[1:]))
        match = "unknown event type"
    else:
        class ForgedScheduleEntry(ScheduleEntry):
            pass

        source_entry = result.entries[0]
        forged_entry = ForgedScheduleEntry(
            **{
                field.name: getattr(source_entry, field.name)
                for field in fields(ScheduleEntry)
            }
        )
        forged = replace(result, entries=(forged_entry, *result.entries[1:]))
        match = "entries are malformed"

    with pytest.raises(DecisionExplanationError, match=match):
        explain_schedule(graph, envelope, forged)  # type: ignore[arg-type]
