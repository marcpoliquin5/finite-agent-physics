from agent_physics import (
    BackendProfile,
    Effect,
    EffectClass,
    ExecutionGraph,
    RunEnvelope,
    SchedulePolicy,
    Scheduler,
    TaskContract,
)


def profile(
    name: str,
    duration: int,
    *,
    provider: str = "local",
    tokens: int = 10,
    cost: int = 10,
    quality: float = 1.0,
    failure_probability: float = 0.0,
) -> BackendProfile:
    return BackendProfile(
        name,
        provider,
        duration,
        duration,
        input_tokens=tokens,
        cost_microusd=cost,
        context_bytes=10,
        quality=quality,
        failure_probability=failure_probability,
    )


def envelope(**overrides: int) -> RunEnvelope:
    values = {
        "deadline_ms": 10_000,
        "max_tokens": 10_000,
        "max_cost_microusd": 10_000,
        "max_context_bytes": 10_000,
        "max_parallelism": 4,
    }
    values.update(overrides)
    return RunEnvelope(**values)


def test_parallel_policy_beats_sequential_for_independent_work() -> None:
    graph = ExecutionGraph.from_tasks(
        [TaskContract(task_id, (profile(task_id, 100),)) for task_id in ("a", "b", "c")]
    )
    scheduler = Scheduler()
    adaptive = scheduler.schedule(graph, envelope(), SchedulePolicy.ADAPTIVE)
    sequential = scheduler.schedule(graph, envelope(), SchedulePolicy.SEQUENTIAL)
    assert adaptive.success
    assert adaptive.makespan_ms == 100
    assert sequential.makespan_ms == 300


def test_adaptive_chooses_cheapest_profile_that_meets_deadline() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract(
                "a",
                (
                    profile("accurate-expensive", 500, tokens=500, cost=500, quality=0.99),
                    profile("fast-cheap", 100, tokens=100, cost=100, quality=0.9),
                ),
                min_quality=0.9,
            )
        ]
    )
    result = Scheduler().schedule(graph, envelope(deadline_ms=300))
    assert result.success
    assert result.entries[0].backend == "fast-cheap"


def test_optional_work_is_shed_before_required_work() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("required", (profile("required", 100, tokens=80),)),
            TaskContract("optional", (profile("optional", 100, tokens=80),), optional=True),
        ]
    )
    result = Scheduler().schedule(graph, envelope(max_tokens=80))
    assert result.success
    assert result.skipped == ("optional",)


def test_conflicting_writes_are_serialized() -> None:
    write = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="crm-record-1",
        idempotency_key="test-key",
    )
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("a", (profile("a", 100),), effect=write),
            TaskContract("b", (profile("b", 100),), effect=write),
        ]
    )
    result = Scheduler().schedule(graph, envelope())
    assert result.success
    assert result.makespan_ms == 200


def test_read_and_write_on_same_resource_are_serialized() -> None:
    read = Effect(kind=EffectClass.READ, resource="record")
    write = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="record",
        idempotency_key="write-record",
    )
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("read", (profile("read", 100),), effect=read),
            TaskContract("write", (profile("write", 100),), effect=write),
        ]
    )
    result = Scheduler().schedule(graph, envelope())
    assert result.success
    assert result.makespan_ms == 200


def test_optional_ancestor_of_required_work_is_protected() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("source", (profile("source", 100, tokens=80),), optional=True),
            TaskContract("required", (profile("required", 100, tokens=10),), ("source",)),
        ]
    )
    result = Scheduler().schedule(graph, envelope(max_tokens=10))
    assert not result.success
    assert result.skipped == ()
    assert "protected task" in (result.failure_reason or "")


def test_profile_choice_preserves_joint_future_budget() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract(
                "a",
                (
                    profile("cheap-token-heavy", 10, tokens=90, cost=1),
                    profile("balanced", 10, tokens=10, cost=90),
                ),
            ),
            TaskContract("b", (profile("b", 10, tokens=90, cost=10),), ("a",)),
        ]
    )
    result = Scheduler().schedule(
        graph,
        envelope(max_tokens=100, max_cost_microusd=100),
    )
    assert result.success
    assert result.entries[0].backend == "balanced"
    assert result.total_tokens == 100
    assert result.total_cost_microusd == 100


def test_optional_work_cannot_spend_protected_cost_to_go() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("root", (profile("root", 100, tokens=20),)),
            TaskContract("required_tail", (profile("tail", 100, tokens=80),), ("root",)),
            TaskContract("optional", (profile("optional", 10, tokens=30),), optional=True),
        ]
    )
    result = Scheduler().schedule(graph, envelope(max_tokens=100, max_parallelism=2))
    assert result.success
    assert result.skipped == ("optional",)
    assert {entry.task_id for entry in result.entries} == {"root", "required_tail"}


def test_model_bound_uses_only_admitted_work() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("required", (profile("required", 100, tokens=10),)),
            TaskContract(
                "optional",
                (profile("optional", 1_000, tokens=100),),
                optional=True,
            ),
        ]
    )
    result = Scheduler().schedule(graph, envelope(max_tokens=10))
    assert result.success
    assert result.makespan_ms == 100
    assert result.model_bound_ms == 100
    assert result.model_bound_gap == 1.0


def test_least_slack_priority_preserves_task_deadline() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("a_relaxed", (profile("relaxed", 90),), deadline_ms=1_000),
            TaskContract("z_urgent", (profile("urgent", 20),), deadline_ms=20),
        ]
    )
    result = Scheduler().schedule(graph, envelope(max_parallelism=1))
    assert result.success
    assert [entry.task_id for entry in result.entries] == ["z_urgent", "a_relaxed"]


def test_task_deadline_never_loosens_run_deadline() -> None:
    graph = ExecutionGraph.from_tasks(
        [TaskContract("a", (profile("a", 150),), deadline_ms=200)]
    )
    result = Scheduler().schedule(graph, envelope(deadline_ms=100))
    assert not result.success
    assert not result.entries


def test_refusal_cancels_every_started_task_before_terminal_event() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("a_long", (profile("long", 200),)),
            TaskContract("b_gate", (profile("gate", 50),)),
            TaskContract("c_urgent", (profile("urgent", 50),), ("b_gate",), deadline_ms=80),
        ]
    )
    result = Scheduler().schedule(graph, envelope(deadline_ms=300, max_parallelism=2))
    assert not result.success
    starts = [event.task_id for event in result.events if event.event_type.value == "task.started"]
    terminals = [
        event.task_id
        for event in result.events
        if event.event_type.value in {"task.completed", "task.cancelled"}
    ]
    assert sorted(starts) == sorted(terminals)
    assert result.events[-1].event_type.value == "run.failed"
    assert all(entry.end_ms <= result.makespan_ms for entry in result.entries)


def test_reliability_floor_rejects_guaranteed_failure_profile() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract(
                "a",
                (
                    profile("risky", 10, cost=1, failure_probability=1.0),
                    profile("reliable", 10, cost=10, failure_probability=0.0),
                ),
            )
        ]
    )
    constrained = envelope()
    constrained = RunEnvelope(
        deadline_ms=constrained.deadline_ms,
        max_tokens=constrained.max_tokens,
        max_cost_microusd=constrained.max_cost_microusd,
        max_context_bytes=constrained.max_context_bytes,
        max_parallelism=constrained.max_parallelism,
        min_modeled_success_probability=0.9,
    )
    result = Scheduler().schedule(graph, constrained)
    assert result.success
    assert result.entries[0].backend == "reliable"


def test_adaptive_profile_selection_is_tuple_order_invariant() -> None:
    choices = (
        profile("z", 10, cost=10),
        profile("a", 10, cost=10),
    )
    first = ExecutionGraph.from_tasks([TaskContract("task", choices)])
    second = ExecutionGraph.from_tasks([TaskContract("task", tuple(reversed(choices)))])
    scheduler = Scheduler()
    first_result = scheduler.schedule(first, envelope())
    second_result = scheduler.schedule(second, envelope())
    assert first_result.entries[0].backend == second_result.entries[0].backend == "a"


def test_provider_limit_is_enforced() -> None:
    graph = ExecutionGraph.from_tasks(
        [
            TaskContract("a", (profile("a", 100, provider="watsonx"),)),
            TaskContract("b", (profile("b", 100, provider="watsonx"),)),
        ]
    )
    constrained = RunEnvelope(
        deadline_ms=10_000,
        max_tokens=10_000,
        max_cost_microusd=10_000,
        max_context_bytes=10_000,
        max_parallelism=4,
        provider_limits=(("watsonx", 1),),
    )
    result = Scheduler().schedule(graph, constrained)
    assert result.success
    assert result.makespan_ms == 200


def test_result_is_json_ready() -> None:
    graph = ExecutionGraph.from_tasks([TaskContract("a", (profile("a", 100),))])
    result = Scheduler().schedule(graph, envelope())
    payload = result.as_dict()
    assert payload["entries"][0]["task_id"] == "a"
    assert payload["events"][0]["event_type"] == "run.started"
