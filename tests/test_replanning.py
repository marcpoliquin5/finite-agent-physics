from __future__ import annotations

import json
import math
from dataclasses import fields, replace

import pytest

from agent_physics.contracts import (
    BackendProfile,
    Effect,
    EffectClass,
    RunEnvelope,
    TaskContract,
)
from agent_physics.examples import miami_eoc_envelope, miami_eoc_graph
from agent_physics.graph import ExecutionGraph
from agent_physics.replanning import (
    DurableRunState,
    EffectBoundary,
    EffectIntentSeal,
    EnvelopeChangeEvent,
    EventDrivenReplanner,
    ProviderCapacityEvent,
    ProviderSlowdownEvent,
    ReplanDisposition,
    ReplanDecision,
    ReplanInvariantError,
    ReplanReason,
    ReplanReasonCode,
    ReplanTamperError,
    ReplanTransition,
    RunProgressSnapshot,
    TaskFailureEvent,
)
from agent_physics.run_store import Usage
from agent_physics.scheduler import ScheduleResult
from agent_physics.serialization import content_digest


def _profile(
    name: str,
    provider: str,
    *,
    duration_ms: int = 1_000,
    tokens: int = 100,
    cost: int = 100,
    context: int = 100,
) -> BackendProfile:
    return BackendProfile(
        name=name,
        provider=provider,
        duration_ms_p50=duration_ms // 2,
        duration_ms_p95=duration_ms,
        input_tokens=tokens,
        cost_microusd=cost,
        context_bytes=context,
        quality=1.0,
        failure_probability=0.0,
    )


ALPHA = _profile("alpha", "provider-a")
BETA = _profile("beta", "provider-b", duration_ms=1_500, tokens=120)


def _graph(*, include_optional: bool = True) -> ExecutionGraph:
    tasks = [
        TaskContract("intake", (ALPHA, BETA)),
        TaskContract("mandatory", (ALPHA, BETA), ("intake",)),
    ]
    if include_optional:
        tasks.append(
            TaskContract(
                "nice_to_have",
                (ALPHA,),
                ("intake",),
                optional=True,
                value=0.1,
            )
        )
    tasks.append(TaskContract("finish", (ALPHA, BETA), ("mandatory",)))
    return ExecutionGraph.from_tasks(tasks)


def _envelope(**overrides: object) -> RunEnvelope:
    values: dict[str, object] = {
        "deadline_ms": 10_000,
        "max_tokens": 2_000,
        "max_cost_microusd": 2_000,
        "max_context_bytes": 2_000,
        "max_parallelism": 2,
        "min_modeled_success_probability": 0.0,
        "provider_limits": (("provider-a", 2), ("provider-b", 2)),
    }
    values.update(overrides)
    return RunEnvelope(**values)  # type: ignore[arg-type]


def _progress(
    state: DurableRunState,
    *,
    elapsed_ms: int,
    completed: tuple[str, ...] | None = None,
    skipped: tuple[str, ...] | None = None,
    usage: Usage | None = None,
    effect_boundary: EffectBoundary | None = None,
) -> RunProgressSnapshot:
    return RunProgressSnapshot.from_state(
        state,
        completed_task_ids=completed,
        skipped_task_ids=skipped,
        settled_usage=usage,
        elapsed_ms=elapsed_ms,
        effect_boundary=effect_boundary,
    )


def test_two_successive_replans_are_monotonic_and_do_not_reset_resources() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    initial = replanner.initial_state(graph, _envelope(), run_id="successive")

    first_progress = _progress(
        initial,
        elapsed_ms=500,
        completed=("intake",),
        usage=Usage(tokens=250, cost_microusd=120, context_bytes=80),
    )
    first_event = ProviderSlowdownEvent("event-1", 500, "provider-a", 1_500)
    first = replanner.replan(graph, initial, first_event, first_progress)

    assert first.state.revision == 1
    assert first.state.prior_state_digest == initial.state_digest
    assert first.decision.disposition is ReplanDisposition.SCHEDULED
    assert first.decision.remaining_envelope is not None
    assert first.decision.remaining_envelope.deadline_ms == 9_500
    assert first.decision.remaining_envelope.max_tokens == 1_750
    assert "intake" not in {task.task_id for task in first.decision.residual_graph.tasks}  # type: ignore[union-attr]
    slowed = {
        profile.provider: profile.duration_ms_p95
        for task in first.decision.residual_graph.tasks  # type: ignore[union-attr]
        for profile in task.profiles
        if task.task_id == "mandatory"
    }
    assert slowed == {"provider-a": 1_500, "provider-b": 1_500}

    second_progress = _progress(
        first.state,
        elapsed_ms=900,
        completed=("intake",),
        usage=Usage(tokens=400, cost_microusd=200, context_bytes=140),
    )
    second_event = ProviderCapacityEvent("event-2", 900, "provider-b", 0)
    second = replanner.replan(graph, first.state, second_event, second_progress)

    assert second.state.revision == 2
    assert tuple(event.revision for event in second.state.applied_events) == (1, 2)
    assert second.state.prior_state_digest == first.state.state_digest
    assert second.decision.remaining_envelope is not None
    assert second.decision.remaining_envelope.deadline_ms == 9_100
    assert second.decision.remaining_envelope.max_tokens == 1_600
    assert second.decision.remaining_envelope.max_cost_microusd == 1_800
    assert second.decision.remaining_envelope.max_context_bytes == 1_860
    assert {entry.provider for entry in second.decision.schedule.entries} == {"provider-a"}  # type: ignore[union-attr]
    assert second.decision.decision_digest != first.decision.decision_digest


def test_same_prior_event_and_progress_replay_to_identical_digests() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="determinism")
    progress = _progress(state, elapsed_ms=100)
    event = ProviderCapacityEvent("capacity", 100, "provider-a", 1)

    first = replanner.replan(graph, state, event, progress)
    replay = replanner.replan(graph, state, event, progress)

    assert replay == first
    assert replanner.verify_transition(graph, state, event, progress, first)
    with pytest.raises(ReplanInvariantError, match="already applied"):
        replanner.replan(graph, first.state, event, _progress(first.state, elapsed_ms=100))


def test_decision_and_state_tampering_are_detected() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="tamper")
    progress = _progress(state, elapsed_ms=100)
    event = ProviderCapacityEvent("capacity", 100, "provider-a", 1)
    transition = replanner.replan(graph, state, event, progress)

    altered_reason = replace(transition.decision.reason, summary="trust me")
    altered_decision = replace(transition.decision, reason=altered_reason)
    assert not altered_decision.verify_digest()
    assert not replanner.verify_transition(
        graph,
        state,
        event,
        progress,
        replace(transition, decision=altered_decision),
    )

    altered_state = replace(transition.state, elapsed_ms=101)
    assert not altered_state.verify_digest()
    altered_progress = _progress(transition.state, elapsed_ms=101)
    with pytest.raises(ReplanTamperError, match="prior durable state"):
        replanner.replan(
            graph,
            altered_state,
            ProviderCapacityEvent("next", 101, "provider-a", 1),
            altered_progress,
        )


def test_replan_rejects_event_and_state_subclasses_before_virtual_digest_calls() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="exact-inputs")
    progress = _progress(state, elapsed_ms=100)

    class ForgedCapacityEvent(ProviderCapacityEvent):
        @property
        def event_digest(self) -> str:
            return "0" * 64

    forged_event = ForgedCapacityEvent("capacity", 100, "provider-a", 1)
    with pytest.raises(ReplanInvariantError, match="exact supported contract"):
        replanner.replan(graph, state, forged_event, progress)

    class ForgedState(DurableRunState):
        def verify_digest(self) -> bool:
            return True

    state_values = {
        field.name: getattr(state, field.name) for field in fields(DurableRunState)
    }
    state_values["state_digest"] = "0" * 64
    forged_state = ForgedState(**state_values)
    with pytest.raises(ReplanInvariantError, match="exact DurableRunState"):
        replanner.replan(
            graph,
            forged_state,
            ProviderCapacityEvent("capacity", 100, "provider-a", 1),
            progress,
        )


@pytest.mark.parametrize(
    "kind",
    ["transition", "state", "decision", "reason", "schedule"],
)
def test_verify_transition_rejects_nested_integrity_subclasses(kind: str) -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id=f"nested-{kind}")
    progress = _progress(state, elapsed_ms=100)
    event = ProviderCapacityEvent("capacity", 100, "provider-a", 1)
    transition = replanner.replan(graph, state, event, progress)

    if kind == "transition":
        class ForgedTransition(ReplanTransition):
            pass

        forged = ForgedTransition(transition.state, transition.decision)
    elif kind == "state":
        class ForgedState(DurableRunState):
            def verify_digest(self) -> bool:
                return True

        forged_state = ForgedState(
            **{
                field.name: getattr(transition.state, field.name)
                for field in fields(DurableRunState)
            }
        )
        forged = replace(transition, state=forged_state)
    elif kind == "decision":
        class ForgedDecision(ReplanDecision):
            def verify_digest(self) -> bool:
                return True

        forged_decision = ForgedDecision(
            **{
                field.name: getattr(transition.decision, field.name)
                for field in fields(ReplanDecision)
            }
        )
        forged = replace(transition, decision=forged_decision)
    elif kind == "reason":
        class ForgedReason(ReplanReason):
            def verify(self) -> bool:
                return True

        forged_reason = ForgedReason(
            **{
                field.name: getattr(transition.decision.reason, field.name)
                for field in fields(ReplanReason)
            }
        )
        forged = replace(
            transition,
            decision=replace(transition.decision, reason=forged_reason),
        )
    else:
        class ForgedSchedule(ScheduleResult):
            pass

        assert transition.decision.schedule is not None
        forged_schedule = ForgedSchedule(
            **{
                field.name: getattr(transition.decision.schedule, field.name)
                for field in fields(ScheduleResult)
            }
        )
        forged = replace(
            transition,
            decision=replace(transition.decision, schedule=forged_schedule),
        )

    assert not replanner.verify_transition(graph, state, event, progress, forged)


def test_reason_and_schedule_digest_material_has_no_lossy_aliases() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="lossless-digest")
    progress = _progress(state, elapsed_ms=100)
    event = ProviderCapacityEvent("capacity", 100, "provider-a", 1)
    transition = replanner.replan(graph, state, event, progress)

    original_reason = transition.decision.reason
    assert original_reason.facts
    duplicate_key, original_value = original_reason.facts[0]
    shadowed_reason = replace(
        original_reason,
        facts=((duplicate_key, "shadow"), *original_reason.facts),
    )
    assert shadowed_reason.as_dict()["facts"] == original_reason.as_dict()["facts"]
    altered_reason_decision = replace(transition.decision, reason=shadowed_reason)
    assert not shadowed_reason.verify()
    assert not altered_reason_decision.verify_digest()
    assert content_digest(altered_reason_decision.unsigned_payload()) != content_digest(
        transition.decision.unsigned_payload()
    )
    assert original_value != "shadow"

    schedule = transition.decision.schedule
    assert schedule is not None
    detail_index = next(index for index, item in enumerate(schedule.events) if item.details)
    source_event = schedule.events[detail_index]
    detail_key, _ = source_event.details[0]
    shadowed_event = replace(
        source_event,
        details=((detail_key, "shadow"), *source_event.details),
    )
    assert shadowed_event.as_dict() == source_event.as_dict()
    shadowed_schedule = replace(
        schedule,
        events=(
            *schedule.events[:detail_index],
            shadowed_event,
            *schedule.events[detail_index + 1 :],
        ),
    )
    shadowed_decision = replace(transition.decision, schedule=shadowed_schedule)
    assert not shadowed_decision.verify_digest()
    assert not replanner.verify_transition(
        graph,
        state,
        event,
        progress,
        replace(transition, decision=shadowed_decision),
    )

    near_probability = math.nextafter(schedule.modeled_success_probability, 0.0)
    assert near_probability != schedule.modeled_success_probability
    near_schedule = replace(schedule, modeled_success_probability=near_probability)
    assert near_schedule.as_dict() == schedule.as_dict()
    near_decision = replace(transition.decision, schedule=near_schedule)
    assert not near_decision.verify_digest()


def test_durable_state_json_round_trip_and_payload_tamper() -> None:
    graph = _graph()
    state = EventDrivenReplanner().initial_state(
        graph,
        _envelope(),
        run_id="roundtrip",
        settled_usage=Usage(tokens=7, cost_microusd=8, context_bytes=9),
    )
    assert DurableRunState.from_json(state.to_json()) == state

    payload = json.loads(state.to_json())
    payload["settled_usage"]["tokens"] = 8
    with pytest.raises(ReplanTamperError, match="digest verification"):
        DurableRunState.from_json(json.dumps(payload))
    with pytest.raises(ReplanTamperError, match="not valid JSON"):
        DurableRunState.from_json("{")
    del payload["run_id"]
    with pytest.raises(ReplanTamperError, match="invalid schema"):
        DurableRunState.from_dict(payload)


@pytest.mark.parametrize(
    "path",
    [
        ("unknown_top_level",),
        ("effect_boundary", "unknown_nested"),
        ("current_envelope", "unknown_nested"),
        ("settled_usage", "unknown_nested"),
        ("applied_events", 0, "unknown_nested"),
    ],
)
def test_durable_state_rejects_unknown_top_level_and_nested_fields(
    path: tuple[str | int, ...],
) -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    initial = replanner.initial_state(graph, _envelope(), run_id="strict-schema")
    revised = replanner.replan(
        graph,
        initial,
        ProviderCapacityEvent("capacity", 1, "provider-a", 1),
        _progress(initial, elapsed_ms=1),
    ).state
    payload = json.loads(revised.to_json())
    target: object = payload
    for component in path[:-1]:
        target = target[component]  # type: ignore[index]
    target[path[-1]] = "shadow"  # type: ignore[index]

    with pytest.raises(ReplanTamperError, match="invalid schema"):
        DurableRunState.from_dict(payload)


def test_durable_state_rejects_unknown_effect_intent_fields() -> None:
    write = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="fixture",
        idempotency_key="strict-intent",
    )
    graph = ExecutionGraph.from_tasks((TaskContract("write", (ALPHA,), effect=write),))
    boundary = EffectBoundary.create(
        (EffectIntentSeal("write", "intent", content_digest("intent")),)
    )
    state = EventDrivenReplanner().initial_state(
        graph,
        _envelope(),
        run_id="strict-intent",
        completed_task_ids=("write",),
        effect_boundary=boundary,
    )
    payload = json.loads(state.to_json())
    payload["effect_boundary"]["intents"][0]["unknown_nested"] = "shadow"
    with pytest.raises(ReplanTamperError, match="invalid schema"):
        DurableRunState.from_dict(payload)


@pytest.mark.parametrize(
    "usage",
    [
        Usage(tokens=True),
        Usage(tokens=1.5),
        Usage(cost_microusd=False),
        Usage(context_bytes=2.5),
    ],
)
def test_replanner_rejects_boolean_and_float_usage_before_state_hashing(
    usage: Usage,
) -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    with pytest.raises(ReplanInvariantError, match="booleans and floats"):
        replanner.initial_state(
            graph,
            _envelope(),
            run_id="invalid-initial-usage",
            settled_usage=usage,
        )

    state = replanner.initial_state(graph, _envelope(), run_id="invalid-progress-usage")
    with pytest.raises(ReplanInvariantError, match="booleans and floats"):
        replanner.replan(
            graph,
            state,
            ProviderCapacityEvent("capacity", 1, "provider-a", 1),
            _progress(state, elapsed_ms=1, usage=usage),
        )


def test_sealed_but_uncommitted_effect_blocks_downstream_without_redispatch() -> None:
    write = Effect(
        kind=EffectClass.IRREVERSIBLE_WRITE,
        resource="fictional-alert-channel",
        requires_approval=True,
        idempotency_key="intent-boundary-test",
    )
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("prepare", (ALPHA,)),
            TaskContract("publish", (ALPHA,), ("prepare",), effect=write),
            TaskContract("audit_intent", (ALPHA,), ("publish",)),
        )
    )
    seal = EffectIntentSeal(
        "publish",
        "intent-001",
        content_digest({"intent_id": "intent-001", "preview": "fictional"}),
    )
    boundary = EffectBoundary.create((seal,))
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(),
        run_id="effect-boundary",
        completed_task_ids=("prepare",),
        effect_boundary=boundary,
    )
    progress = _progress(state, elapsed_ms=100)
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("capacity", 100, "provider-a", 1),
        progress,
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert (
        transition.decision.reason.code
        is ReplanReasonCode.EFFECT_COMMIT_UNCONFIRMED
    )
    assert transition.decision.schedule is None
    assert transition.decision.residual_graph is None
    assert transition.state.effect_boundary == boundary

    committed = replanner.replan(
        graph,
        transition.state,
        ProviderCapacityEvent("commit-observed", 200, "provider-a", 1),
        _progress(
            transition.state,
            elapsed_ms=200,
            completed=("prepare", "publish"),
        ),
    )
    residual = committed.decision.residual_graph
    assert committed.decision.disposition is ReplanDisposition.SCHEDULED
    assert residual is not None
    assert residual.topological_order() == ("audit_intent",)
    assert residual.by_id["audit_intent"].dependencies == ()
    assert {entry.task_id for entry in committed.decision.schedule.entries} == {  # type: ignore[union-attr]
        "audit_intent"
    }

    changed_boundary_progress = replace(progress, effect_boundary_digest="0" * 64)
    with pytest.raises(ReplanInvariantError, match="material and digest disagree"):
        replanner.replan(
            graph,
            state,
            ProviderCapacityEvent("capacity-2", 100, "provider-a", 1),
            changed_boundary_progress,
        )


def test_completed_write_requires_effect_seal_and_boundary_rejects_pure_task() -> None:
    write = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="fixture",
        idempotency_key="write-once",
    )
    graph = ExecutionGraph.from_tasks(
        (TaskContract("pure", (ALPHA,)), TaskContract("write", (ALPHA,), effect=write))
    )
    replanner = EventDrivenReplanner()
    with pytest.raises(ReplanInvariantError, match="lacks an immutable effect seal"):
        replanner.initial_state(
            graph,
            _envelope(),
            run_id="unsafe-completion",
            completed_task_ids=("write",),
        )
    pure_seal = EffectBoundary.create(
        (EffectIntentSeal("pure", "intent", content_digest("pure-intent")),)
    )
    with pytest.raises(ReplanInvariantError, match="only write tasks"):
        replanner.initial_state(
            graph,
            _envelope(),
            run_id="unsafe-boundary",
            effect_boundary=pure_seal,
        )

    dependent_graph = ExecutionGraph.from_tasks(
        (
            TaskContract("prepare", (ALPHA,)),
            TaskContract("write", (ALPHA,), ("prepare",), effect=write),
        )
    )
    write_seal = EffectBoundary.create(
        (EffectIntentSeal("write", "intent", content_digest("write-intent")),)
    )
    with pytest.raises(ReplanInvariantError, match="lacks completed dependencies"):
        replanner.initial_state(
            dependent_graph,
            _envelope(),
            run_id="causally-impossible-boundary",
            effect_boundary=write_seal,
        )


def test_effect_boundary_can_only_append_and_new_seal_can_commit_same_revision() -> None:
    write = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="fixture",
        idempotency_key="append-only-write",
    )
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("prepare", (ALPHA,)),
            TaskContract("write", (ALPHA,), ("prepare",), effect=write),
        )
    )
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(),
        run_id="append-effect",
        completed_task_ids=("prepare",),
    )
    seal = EffectIntentSeal("write", "intent-1", content_digest("intent-1"))
    appended = EffectBoundary.create((seal,))
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("commit", 100, "provider-a", 1),
        _progress(
            state,
            elapsed_ms=100,
            completed=("prepare", "write"),
            effect_boundary=appended,
        ),
    )

    assert transition.decision.disposition is ReplanDisposition.COMPLETE
    assert transition.state.effect_boundary == appended
    assert transition.state.completed_task_ids == ("prepare", "write")

    removed = EffectBoundary.empty()
    with pytest.raises(ReplanInvariantError, match="append-only"):
        replanner.replan(
            graph,
            transition.state,
            ProviderCapacityEvent("remove", 200, "provider-a", 1),
            _progress(
                transition.state,
                elapsed_ms=200,
                effect_boundary=removed,
            ),
        )

    mutated = EffectBoundary.create(
        (EffectIntentSeal("write", "intent-2", content_digest("intent-2")),)
    )
    with pytest.raises(ReplanInvariantError, match="append-only"):
        replanner.replan(
            graph,
            transition.state,
            ProviderCapacityEvent("mutate", 200, "provider-a", 1),
            _progress(
                transition.state,
                elapsed_ms=200,
                effect_boundary=mutated,
            ),
        )


def test_pending_seal_alone_is_not_misreported_as_complete() -> None:
    write = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="fixture",
        idempotency_key="pending-only",
    )
    graph = ExecutionGraph.from_tasks((TaskContract("write", (ALPHA,), effect=write),))
    boundary = EffectBoundary.create(
        (EffectIntentSeal("write", "pending", content_digest("pending")),)
    )
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(),
        run_id="pending-only",
        effect_boundary=boundary,
    )
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("observe", 1, "provider-a", 1),
        _progress(state, elapsed_ms=1),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert (
        transition.decision.reason.code
        is ReplanReasonCode.EFFECT_COMMIT_UNCONFIRMED
    )
    assert transition.decision.schedule is None


def test_terminal_sets_and_effect_seals_reject_duplicate_or_malformed_identity() -> None:
    graph = _graph(include_optional=False)
    replanner = EventDrivenReplanner()
    with pytest.raises(ReplanInvariantError, match="terminal task IDs must be unique"):
        replanner.initial_state(
            graph,
            _envelope(),
            run_id="duplicates",
            completed_task_ids=("intake", "intake"),
        )
    digest = content_digest("intent")
    with pytest.raises(ReplanInvariantError, match="at most one intent per task"):
        EffectBoundary.create(
            (
                EffectIntentSeal("same", "one", digest),
                EffectIntentSeal("same", "two", digest),
            )
        )
    with pytest.raises(ReplanInvariantError, match="lowercase SHA-256"):
        EffectBoundary.create((EffectIntentSeal("write", "intent", "not-a-digest"),))


def test_task_failure_removes_failed_provider_without_erasing_other_choice() -> None:
    graph = _graph(include_optional=False)
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="failure")
    progress = _progress(
        state,
        elapsed_ms=200,
        completed=("intake",),
        usage=Usage(tokens=100, cost_microusd=100, context_bytes=100),
    )
    transition = replanner.replan(
        graph,
        state,
        TaskFailureEvent("failed-a", 200, "mandatory", "provider-a"),
        progress,
    )

    assert transition.decision.disposition is ReplanDisposition.SCHEDULED
    mandatory = transition.decision.residual_graph.by_id["mandatory"]  # type: ignore[union-attr]
    assert tuple(profile.provider for profile in mandatory.profiles) == ("provider-b",)
    assert ("mandatory", "provider-a") in transition.state.failed_task_providers


def test_task_failure_refuses_when_mandatory_task_has_no_profile() -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("only", (ALPHA,)),))
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="failure-refusal")
    transition = replanner.replan(
        graph,
        state,
        TaskFailureEvent("failed", 10, "only", "provider-a"),
        _progress(state, elapsed_ms=10),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.NO_ADMISSIBLE_PROFILE
    assert transition.decision.schedule is None


def test_capacity_zero_refuses_instead_of_using_provider_default_capacity() -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("only", (ALPHA,)),))
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="capacity-zero")
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("offline", 10, "provider-a", 0),
        _progress(state, elapsed_ms=10),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.NO_ADMISSIBLE_PROFILE
    assert dict(transition.state.provider_capacities) == {"provider-a": 0}


def test_envelope_cut_below_actual_usage_is_a_visible_refusal() -> None:
    graph = _graph(include_optional=False)
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(),
        run_id="budget-cut",
        settled_usage=Usage(tokens=300, cost_microusd=200, context_bytes=100),
    )
    cut = _envelope(max_tokens=250)
    transition = replanner.replan(
        graph,
        state,
        EnvelopeChangeEvent("budget-cut", 100, cut),
        _progress(state, elapsed_ms=100),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.RESOURCE_EXHAUSTED
    assert transition.decision.remaining_envelope is None
    assert transition.state.current_envelope.max_tokens == 250
    assert transition.state.settled_usage.tokens == 300


def test_elapsed_deadline_is_subtracted_and_never_rebased() -> None:
    graph = _graph(include_optional=False)
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(deadline_ms=1_000), run_id="deadline")
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("at-deadline", 1_000, "provider-a", 1),
        _progress(state, elapsed_ms=1_000),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.DEADLINE_EXHAUSTED


def test_scheduler_refusal_keeps_the_failed_schedule_as_a_witness() -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("slow", (ALPHA,)),))
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(deadline_ms=5_000),
        run_id="scheduler-refusal",
    )
    transition = replanner.replan(
        graph,
        state,
        ProviderSlowdownEvent("late-slowdown", 4_500, "provider-a", 2_000),
        _progress(state, elapsed_ms=4_500),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.SCHEDULER_REFUSED
    assert transition.decision.remaining_envelope is not None
    assert transition.decision.remaining_envelope.deadline_ms == 500
    assert transition.decision.schedule is not None
    assert not transition.decision.schedule.success


def test_all_terminal_work_still_refuses_when_observed_after_deadline() -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("done", (ALPHA,)),))
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(deadline_ms=100),
        run_id="already-complete",
        completed_task_ids=("done",),
        elapsed_ms=100,
    )
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("completion-observation", 150, "provider-a", 1),
        _progress(state, elapsed_ms=150),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.DEADLINE_EXHAUSTED
    assert transition.decision.residual_graph is not None
    assert transition.decision.residual_graph.tasks == ()
    assert transition.decision.schedule is None
    assert transition.decision.remaining_envelope is None


def test_terminal_snapshot_refuses_settled_overrun_but_allows_exact_deadline() -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("done", (ALPHA,)),))
    replanner = EventDrivenReplanner()
    overrun_state = replanner.initial_state(
        graph,
        _envelope(max_tokens=100),
        run_id="terminal-overrun",
        completed_task_ids=("done",),
    )
    overrun = replanner.replan(
        graph,
        overrun_state,
        ProviderCapacityEvent("usage-observed", 50, "provider-a", 1),
        _progress(overrun_state, elapsed_ms=50, usage=Usage(tokens=101)),
    )
    assert overrun.decision.disposition is ReplanDisposition.REFUSED
    assert overrun.decision.reason.code is ReplanReasonCode.RESOURCE_EXHAUSTED
    assert overrun.decision.residual_graph is not None
    assert overrun.decision.residual_graph.tasks == ()

    deadline_state = replanner.initial_state(
        graph,
        _envelope(deadline_ms=100),
        run_id="terminal-at-deadline",
        completed_task_ids=("done",),
        elapsed_ms=100,
    )
    on_time = replanner.replan(
        graph,
        deadline_state,
        ProviderCapacityEvent("deadline-observed", 100, "provider-a", 1),
        _progress(deadline_state, elapsed_ms=100),
    )
    assert on_time.decision.disposition is ReplanDisposition.COMPLETE
    assert on_time.decision.reason.code is ReplanReasonCode.NO_RESIDUAL_WORK


def test_provider_loss_auto_sheds_independent_optional_work() -> None:
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("mandatory", (BETA,)),
            TaskContract("optional-a", (ALPHA,), optional=True),
        )
    )
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="auto-shed")
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("a-offline", 10, "provider-a", 0),
        _progress(state, elapsed_ms=10),
    )

    assert transition.decision.disposition is ReplanDisposition.SCHEDULED
    assert transition.decision.shed_task_ids == ("optional-a",)
    assert transition.decision.residual_graph is not None
    assert transition.decision.residual_graph.topological_order() == ("mandatory",)
    assert transition.state.skipped_task_ids == ("optional-a",)


def test_mandatory_skip_is_refused_not_silently_treated_as_satisfied() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="mandatory-skip")
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("capacity", 10, "provider-a", 1),
        _progress(state, elapsed_ms=10, skipped=("mandatory",)),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.MANDATORY_PROMISE_BROKEN


def test_stormshift_capacity_drop_preserves_promises_by_shedding_optional_work() -> None:
    graph = miami_eoc_graph()
    envelope = replace(miami_eoc_envelope(), max_context_bytes=29_500)
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, envelope, run_id="stormshift-capacity")
    progress = _progress(
        state,
        elapsed_ms=2_000,
        completed=("incident_intake",),
        usage=Usage(context_bytes=900),
    )
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent(
            "watsonx-capacity-drop",
            2_000,
            "simulated-watsonx",
            1,
        ),
        progress,
    )

    assert transition.decision.disposition is ReplanDisposition.SCHEDULED
    assert transition.decision.reason.code is ReplanReasonCode.OPTIONAL_WORK_SHED
    assert transition.decision.shed_task_ids == ("social_signal_scan",)
    assert transition.decision.remaining_envelope is not None
    assert transition.decision.remaining_envelope.deadline_ms == 10_000
    assert transition.decision.remaining_envelope.max_context_bytes == 28_600
    assert transition.decision.remaining_envelope.provider_limit("simulated-watsonx") == 1
    scheduled = {entry.task_id for entry in transition.decision.schedule.entries}  # type: ignore[union-attr]
    mandatory_remaining = {
        task.task_id
        for task in graph.tasks
        if not task.optional and task.task_id != "incident_intake"
    }
    assert mandatory_remaining.issubset(scheduled)
    assert "social_signal_scan" not in scheduled
    assert transition.state.skipped_task_ids == ("social_signal_scan",)
    assert transition.decision.schedule.total_context_bytes == 28_600  # type: ignore[union-attr]


def test_stormshift_capacity_loss_refuses_when_mandatory_provider_is_offline() -> None:
    graph = miami_eoc_graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, miami_eoc_envelope(), run_id="stormshift-offline")
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("watsonx-offline", 2_000, "simulated-watsonx", 0),
        _progress(
            state,
            elapsed_ms=2_000,
            completed=("incident_intake",),
            usage=Usage(context_bytes=900),
        ),
    )

    assert transition.decision.disposition is ReplanDisposition.REFUSED
    assert transition.decision.reason.code is ReplanReasonCode.NO_ADMISSIBLE_PROFILE
    assert transition.decision.schedule is None


def test_progress_cannot_rewind_completion_usage_time_or_dependency_order() -> None:
    graph = _graph(include_optional=False)
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(
        graph,
        _envelope(),
        run_id="monotonic",
        completed_task_ids=("intake",),
        settled_usage=Usage(tokens=10, cost_microusd=10, context_bytes=10),
        elapsed_ms=100,
    )
    event = ProviderCapacityEvent("next", 100, "provider-a", 1)
    with pytest.raises(ReplanInvariantError, match="completed tasks cannot be removed"):
        replanner.replan(
            graph,
            state,
            event,
            _progress(state, elapsed_ms=100, completed=()),
        )
    with pytest.raises(ReplanInvariantError, match="settled usage cannot decrease"):
        replanner.replan(
            graph,
            state,
            event,
            _progress(
                state,
                elapsed_ms=100,
                usage=Usage(tokens=9, cost_microusd=10, context_bytes=10),
            ),
        )
    with pytest.raises(ReplanInvariantError, match="elapsed time cannot move backwards"):
        replanner.replan(
            graph,
            state,
            ProviderCapacityEvent("early", 99, "provider-a", 1),
            _progress(state, elapsed_ms=99),
        )
    fresh = replanner.initial_state(graph, _envelope(), run_id="bad-order")
    with pytest.raises(ReplanInvariantError, match="lacks completed dependencies"):
        replanner.replan(
            graph,
            fresh,
            ProviderCapacityEvent("bad-order", 10, "provider-a", 1),
            _progress(fresh, elapsed_ms=10, completed=("mandatory",)),
        )


def test_invalid_events_are_rejected_as_malformed_observations() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="invalid-events")

    cases = (
        ProviderSlowdownEvent("slow", 10, "provider-a", 1_000),
        ProviderCapacityEvent("unknown", 10, "unknown-provider", 1),
        TaskFailureEvent("failure", 10, "mandatory", "unknown-provider"),
    )
    for event in cases:
        with pytest.raises(ReplanInvariantError):
            replanner.replan(graph, state, event, _progress(state, elapsed_ms=10))

    invalid_envelope = replace(_envelope(), max_parallelism=0)
    with pytest.raises(ReplanInvariantError, match="changed envelope is invalid"):
        replanner.replan(
            graph,
            state,
            EnvelopeChangeEvent("invalid-envelope", 10, invalid_envelope),
            _progress(state, elapsed_ms=10),
        )


def test_decision_explicitly_disclaims_live_executor_mutation() -> None:
    graph = _graph()
    replanner = EventDrivenReplanner()
    state = replanner.initial_state(graph, _envelope(), run_id="scope")
    transition = replanner.replan(
        graph,
        state,
        ProviderCapacityEvent("capacity", 10, "provider-a", 1),
        _progress(state, elapsed_ms=10),
    )

    limitations = " ".join(transition.decision.limitations)
    assert "does not mutate, pause, cancel, or lease work in a live executor" in limitations
    assert "caller-reported actual usage" in limitations
    assert "events do not carry run_id or prior-state identity" in limitations
    assert transition.decision.verify_digest()
