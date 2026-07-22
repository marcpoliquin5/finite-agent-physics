from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.contracts import (
    BackendProfile,
    Effect,
    EffectClass,
    RunEnvelope,
    TaskContract,
)
from agent_physics.effects import EffectState, SQLiteEffectBroker
from agent_physics.executor import (
    AdmissionRefused,
    AsyncGraphExecutor,
    DeadlineExceeded,
    DurableOutputInvalid,
    EffectExecutionRefused,
    ExecutionCancelled,
    OutputValidationError,
    RetryableWorkerError,
    RetryPolicy,
    RetryReservationRefused,
    RunState,
    RunAlreadyTerminal,
    SimulatedExecutorCrash,
    TaskExecutionContext,
    UsageReservationExceeded,
    WorkerResult,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import (
    RunDefinitionConflict,
    RunNotFound,
    SQLiteRunStore,
    Usage,
)


def _profile(provider: str = "fixture-a", name: str = "deterministic") -> BackendProfile:
    return BackendProfile(
        name=name,
        provider=provider,
        duration_ms_p50=5,
        duration_ms_p95=10,
        input_tokens=100,
        output_tokens=50,
        cost_microusd=25,
        context_bytes=1_000,
        quality=1.0,
    )


def _task(
    task_id: str,
    *,
    provider: str = "fixture-a",
    dependencies: tuple[str, ...] = (),
    deadline_ms: int | None = None,
    effect: Effect | None = None,
) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        profiles=(_profile(provider, f"{provider}-worker"),),
        dependencies=dependencies,
        deadline_ms=deadline_ms,
        effect=effect or Effect(),
    )


def _envelope(
    *,
    deadline_ms: int = 2_000,
    max_parallelism: int = 2,
    provider_limits: tuple[tuple[str, int], ...] = (),
    max_tokens: int = 100_000,
    max_cost_microusd: int = 100_000,
    max_context_bytes: int = 1_000_000,
) -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=deadline_ms,
        max_tokens=max_tokens,
        max_cost_microusd=max_cost_microusd,
        max_context_bytes=max_context_bytes,
        max_parallelism=max_parallelism,
        provider_limits=provider_limits,
    )


def test_global_and_provider_semaphores_bound_real_async_calls(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks(
        (
            _task("a1", provider="fixture-a"),
            _task("a2", provider="fixture-a"),
            _task("b1", provider="fixture-b"),
            _task("b2", provider="fixture-b"),
        )
    )
    active_global = 0
    max_global = 0
    active_provider: defaultdict[str, int] = defaultdict(int)
    max_provider: defaultdict[str, int] = defaultdict(int)

    async def worker(context: TaskExecutionContext) -> WorkerResult:
        nonlocal active_global, max_global
        provider = context.profile.provider
        active_global += 1
        active_provider[provider] += 1
        max_global = max(max_global, active_global)
        max_provider[provider] = max(max_provider[provider], active_provider[provider])
        try:
            await asyncio.sleep(0.025)
            return WorkerResult(
                {"task": context.task.task_id},
                Usage(tokens=80, cost_microusd=20, context_bytes=800),
            )
        finally:
            active_provider[provider] -= 1
            active_global -= 1

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(
        store,
        workers={task.task_id: worker for task in graph.tasks},
    )
    result = asyncio.run(
        executor.execute(
            graph,
            _envelope(
                max_parallelism=2,
                provider_limits=(("fixture-a", 1), ("fixture-b", 2)),
            ),
            run_id="parallel",
        )
    )

    assert set(result.outputs) == {"a1", "a2", "b1", "b2"}
    assert max_global == 2
    assert max_provider["fixture-a"] == 1
    assert max_provider["fixture-b"] <= 2
    completion = next(event for event in result.events if event.event_id == "parallel:a1:completed")
    assert completion.usage.estimated.tokens == 150
    assert completion.usage.reserved.tokens == 150
    assert completion.usage.actual.tokens == 80


def test_adaptive_admission_refuses_infeasible_run_before_any_call(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("required"),))
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"unexpected": True})

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(store, workers={"required": worker})
    with pytest.raises(AdmissionRefused):
        asyncio.run(
            executor.execute(
                graph,
                _envelope(max_tokens=149),
                run_id="infeasible",
            )
        )
    assert calls == 0
    with pytest.raises(RunNotFound):
        store.get_run("infeasible")


def test_physical_admission_refuses_before_run_creation_or_worker_call(tmp_path: Path) -> None:
    profile = replace(_profile(), cpu_time_ms=10)
    graph = ExecutionGraph.from_tasks((TaskContract("physical", (profile,)),))
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"unexpected": True})

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(store, workers={"physical": worker})
    with pytest.raises(AdmissionRefused, match="physical-resource admission"):
        asyncio.run(
            executor.execute(
                graph,
                replace(_envelope(), max_cpu_time_ms=9),
                run_id="physical-refusal",
            )
        )

    assert calls == 0
    with pytest.raises(RunNotFound):
        store.get_run("physical-refusal")


def test_retry_worst_case_must_fit_before_any_call(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("retry-reserve"),))
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"unexpected": True})

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(
        store,
        workers={"retry-reserve": worker},
        retry_policy=RetryPolicy(max_attempts=2),
    )
    with pytest.raises(RetryReservationRefused):
        asyncio.run(
            executor.execute(
                graph,
                _envelope(max_tokens=200),
                run_id="retry-reserve",
            )
        )
    assert calls == 0


def test_adaptive_skips_are_executed_as_skips(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks(
        (
            _task("required"),
            TaskContract(
                task_id="optional",
                profiles=(_profile(),),
                optional=True,
                value=0.1,
            ),
        )
    )
    calls: list[str] = []

    async def worker(context: TaskExecutionContext) -> WorkerResult:
        calls.append(context.task.task_id)
        return WorkerResult({"task": context.task.task_id}, Usage(tokens=100))

    result = asyncio.run(
        AsyncGraphExecutor(
            SQLiteRunStore(tmp_path / "runs.db"),
            workers={"required": worker, "optional": worker},
        ).execute(graph, _envelope(max_tokens=150), run_id="adaptive-skip")
    )
    assert result.skipped_task_ids == ("optional",)
    assert result.run_state is RunState.COMPLETED
    assert calls == ["required"]
    assert set(result.outputs) == {"required"}


def test_worker_receives_the_profile_selected_by_adaptive_admission(tmp_path: Path) -> None:
    costly = _profile("fixture-a", "costly-fast")
    economical = BackendProfile(
        name="economical",
        provider="fixture-b",
        duration_ms_p50=10,
        duration_ms_p95=20,
        input_tokens=150,
        output_tokens=50,
        cost_microusd=10,
        context_bytes=500,
        quality=1.0,
    )
    graph = ExecutionGraph.from_tasks(
        (TaskContract(task_id="routed", profiles=(costly, economical)),)
    )
    observed: list[tuple[str, str]] = []

    async def worker(context: TaskExecutionContext) -> WorkerResult:
        observed.append((context.profile.provider, context.profile.name))
        return WorkerResult({"profile": context.profile.name}, Usage(tokens=100))

    result = asyncio.run(
        AsyncGraphExecutor(
            SQLiteRunStore(tmp_path / "runs.db"),
            workers={"routed": worker},
        ).execute(graph, _envelope(), run_id="selected-profile")
    )
    assert observed == [("fixture-b", "economical")]
    started = next(event for event in result.events if event.event_type == "task.attempt_started")
    assert started.payload == {"backend": "economical", "provider": "fixture-b"}


def test_actual_usage_cannot_exceed_per_attempt_reservation(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("overrun"),))

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        return WorkerResult({"answer": 42}, Usage(tokens=151))

    store = SQLiteRunStore(tmp_path / "runs.db")
    external_cancellation = asyncio.Event()
    with pytest.raises(UsageReservationExceeded):
        asyncio.run(
            AsyncGraphExecutor(store, workers={"overrun": worker}).execute(
                graph,
                _envelope(),
                run_id="overrun",
                cancellation_event=external_cancellation,
            )
        )
    assert not external_cancellation.is_set()
    failed = next(
        event for event in store.events("overrun") if event.event_type == "task.attempt_failed"
    )
    assert failed.payload["phase"] == "worker_result"
    assert failed.usage.actual.tokens == 151
    assert not store.completed_tasks("overrun")


def test_retryable_failure_usage_overrun_is_not_retried(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("retry-overrun"),))
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        raise RetryableWorkerError("oversized 429", actual_usage=Usage(tokens=151))

    store = SQLiteRunStore(tmp_path / "runs.db")
    with pytest.raises(UsageReservationExceeded):
        asyncio.run(
            AsyncGraphExecutor(
                store,
                workers={"retry-overrun": worker},
                retry_policy=RetryPolicy(max_attempts=2),
            ).execute(graph, _envelope(), run_id="retry-overrun")
        )
    assert calls == 1
    failed = next(
        event
        for event in store.events("retry-overrun")
        if event.event_type == "task.attempt_failed"
    )
    assert failed.payload["phase"] == "retryable_failure"
    assert failed.payload["retryable"] is False


def test_bounded_retries_happen_between_nonoverlapping_calls(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("flaky", deadline_ms=1_000),))
    calls = 0
    active_calls = 0
    deadlines: list[int] = []

    async def flaky(context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls, active_calls
        calls += 1
        active_calls += 1
        assert active_calls == 1
        deadlines.append(context.deadline_at_ms)
        await asyncio.sleep(0)
        active_calls -= 1
        if calls < 3:
            raise RetryableWorkerError(
                "fixture 429",
                actual_usage=Usage(tokens=5, cost_microusd=1, context_bytes=10),
            )
        return WorkerResult(
            {"answer": 42},
            Usage(tokens=7, cost_microusd=2, context_bytes=12),
        )

    store = SQLiteRunStore(tmp_path / "runs.db")
    result = asyncio.run(
        AsyncGraphExecutor(
            store,
            workers={"flaky": flaky},
            retry_policy=RetryPolicy(max_attempts=3, backoff_ms=1),
        ).execute(graph, _envelope(), run_id="retry")
    )

    assert result.outputs["flaky"] == {"answer": 42}
    assert calls == 3
    assert len(set(deadlines)) == 1
    assert result.actual_usage.tokens == 17
    attempts = [
        event.attempt
        for event in result.events
        if event.task_id == "flaky" and event.event_type == "task.attempt_started"
    ]
    assert attempts == [1, 2, 3]
    assert len([event for event in result.events if event.event_type == "task.attempt_failed"]) == 2
    succeeded = next(
        event for event in result.events if event.event_type == "task.attempt_succeeded"
    )
    completed = next(event for event in result.events if event.event_type == "task.completed")
    assert completed.sequence == succeeded.sequence + 1


def test_absolute_task_deadline_cancels_call_without_retry(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("slow", deadline_ms=30),))
    calls = 0

    async def slow(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return WorkerResult({"late": True})

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(
        store,
        workers={"slow": slow},
        retry_policy=RetryPolicy(max_attempts=3),
    )
    external_cancellation = asyncio.Event()
    with pytest.raises(DeadlineExceeded):
        asyncio.run(
            executor.execute(
                graph,
                _envelope(),
                run_id="deadline",
                cancellation_event=external_cancellation,
            )
        )

    assert calls == 1
    assert not external_cancellation.is_set()
    events = store.events("deadline")
    assert len([event for event in events if event.event_type == "task.attempt_started"]) == 1
    failed = next(event for event in events if event.event_type == "task.attempt_failed")
    assert failed.payload["error_type"] == "DeadlineExceeded"


def test_cooperative_cancellation_is_visible_inside_worker(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("waiting"),))
    entered = asyncio.Event()
    observed = False

    async def waiting(context: TaskExecutionContext) -> WorkerResult:
        nonlocal observed
        entered.set()
        try:
            while not context.cancellation_requested:
                await asyncio.sleep(0)
            observed = True
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            observed = context.cancellation_requested
            raise
        return WorkerResult({"unexpected": True})

    async def scenario() -> None:
        cancellation = asyncio.Event()
        executor = AsyncGraphExecutor(
            SQLiteRunStore(tmp_path / "runs.db"),
            workers={"waiting": waiting},
        )
        execution = asyncio.create_task(
            executor.execute(
                graph,
                _envelope(),
                run_id="cancelled",
                cancellation_event=cancellation,
            )
        )
        await entered.wait()
        cancellation.set()
        with pytest.raises(ExecutionCancelled):
            await execution

    asyncio.run(scenario())
    assert observed


def test_output_validator_is_a_nonretryable_completion_gate(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("validate"),))

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        return WorkerResult(
            {"required_field": False},
            Usage(tokens=9, cost_microusd=3, context_bytes=20),
        )

    store = SQLiteRunStore(tmp_path / "runs.db")

    async def validator(_task: TaskContract, output: object) -> bool:
        return output == {"required_field": True}

    executor = AsyncGraphExecutor(
        store,
        workers={"validate": worker},
        output_validator=validator,
        retry_policy=RetryPolicy(max_attempts=3),
    )
    with pytest.raises(OutputValidationError):
        asyncio.run(executor.execute(graph, _envelope(), run_id="validation"))

    events = store.events("validation")
    assert not store.completed_tasks("validation")
    assert len([event for event in events if event.event_type == "task.attempt_started"]) == 1
    failed = next(event for event in events if event.event_type == "task.attempt_failed")
    assert failed.usage.actual.tokens == 9
    assert failed.payload["retryable"] is False
    with pytest.raises(RunAlreadyTerminal):
        asyncio.run(executor.execute(graph, _envelope(), run_id="validation"))


def test_output_validation_cannot_commit_after_absolute_deadline(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("validate-slow", deadline_ms=30),))

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        return WorkerResult({"candidate": True}, Usage(tokens=4))

    async def slow_validator(_task: TaskContract, _output: object) -> bool:
        await asyncio.sleep(0.1)
        return True

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(
        store,
        workers={"validate-slow": worker},
        output_validator=slow_validator,
    )
    with pytest.raises(DeadlineExceeded):
        asyncio.run(executor.execute(graph, _envelope(), run_id="slow-validation"))

    assert not store.completed_tasks("slow-validation")
    failed = next(
        event
        for event in store.events("slow-validation")
        if event.event_type == "task.attempt_failed"
    )
    assert failed.payload["phase"] == "output_validation"


def test_caller_cancellation_during_validation_closes_attempt_ledger(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("validate-cancel"),))
    validation_started = asyncio.Event()

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        return WorkerResult({"candidate": True}, Usage(tokens=6))

    async def validator(_task: TaskContract, _output: object) -> bool:
        validation_started.set()
        await asyncio.sleep(1)
        return True

    store = SQLiteRunStore(tmp_path / "runs.db")

    async def scenario() -> None:
        execution = asyncio.create_task(
            AsyncGraphExecutor(
                store,
                workers={"validate-cancel": worker},
                output_validator=validator,
            ).execute(graph, _envelope(), run_id="cancel-validation")
        )
        await validation_started.wait()
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(scenario())
    cancelled = next(
        event
        for event in store.events("cancel-validation")
        if event.event_type == "task.attempt_cancelled"
    )
    assert cancelled.payload["phase"] == "output_validation"
    assert cancelled.usage.actual.tokens == 6


def test_resume_binds_manifest_and_revalidates_durable_outputs(tmp_path: Path) -> None:
    graph = ExecutionGraph.from_tasks((_task("durable"),))
    calls = 0
    output_is_valid = [True]

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"approved": True}, Usage(tokens=10))

    async def validator(_task: TaskContract, output: object) -> bool:
        return output_is_valid[0] and output == {"approved": True}

    store = SQLiteRunStore(tmp_path / "runs.db")
    executor = AsyncGraphExecutor(
        store,
        workers={"durable": worker},
        output_validator=validator,
        validator_revision="safety-v1",
    )
    first = asyncio.run(executor.execute(graph, _envelope(), run_id="manifest"))
    definition = store.get_run("manifest")
    assert definition.manifest_revision > 0
    assert len(definition.manifest_digest) == 64
    assert first.run_state is RunState.COMPLETED
    assert calls == 1

    output_is_valid[0] = False
    with pytest.raises(DurableOutputInvalid):
        asyncio.run(executor.execute(graph, _envelope(), run_id="manifest"))
    assert calls == 1

    output_is_valid[0] = True
    incompatible = AsyncGraphExecutor(
        store,
        workers={"durable": worker},
        output_validator=validator,
        validator_revision="safety-v2",
    )
    with pytest.raises(RunDefinitionConflict):
        asyncio.run(incompatible.execute(graph, _envelope(), run_id="manifest"))

    async def unused(_context: TaskExecutionContext) -> WorkerResult:
        return WorkerResult({"unused": True})

    different_worker_set = AsyncGraphExecutor(
        store,
        workers={"durable": worker, "extra-worker": unused},
        output_validator=validator,
        validator_revision="safety-v1",
    )
    with pytest.raises(RunDefinitionConflict):
        asyncio.run(different_worker_set.execute(graph, _envelope(), run_id="manifest"))


def test_crash_restart_skips_completed_task_and_resumes_open_attempt(tmp_path: Path) -> None:
    database = tmp_path / "runs.db"
    graph = ExecutionGraph.from_tasks(
        (
            _task("a"),
            _task("b", dependencies=("a",)),
        )
    )
    calls = {"a": 0, "b": 0}

    async def worker_a(_context: TaskExecutionContext) -> WorkerResult:
        calls["a"] += 1
        return WorkerResult({"value": 7}, Usage(tokens=10))

    async def worker_b(context: TaskExecutionContext) -> WorkerResult:
        calls["b"] += 1
        assert context.dependency_outputs == {"a": {"value": 7}}
        if calls["b"] == 1:
            raise SimulatedExecutorCrash("crash after attempt start")
        return WorkerResult({"doubled": 14}, Usage(tokens=11))

    first_store = SQLiteRunStore(database)
    first = AsyncGraphExecutor(
        first_store,
        workers={"a": worker_a, "b": worker_b},
        retry_policy=RetryPolicy(max_attempts=2),
    )
    with pytest.raises(SimulatedExecutorCrash):
        asyncio.run(first.execute(graph, _envelope(), run_id="restart"))

    first_events = first_store.events("restart")
    assert first_store.completed_tasks("restart")["a"].output == {"value": 7}
    assert not any(event.event_type == "run.failed" for event in first_events)
    assert (
        len(
            [
                event
                for event in first_events
                if event.task_id == "b" and event.event_type == "task.attempt_started"
            ]
        )
        == 1
    )

    incompatible = AsyncGraphExecutor(
        SQLiteRunStore(database),
        workers={"a": worker_a, "b": worker_b},
        retry_policy=RetryPolicy(max_attempts=3),
    )
    with pytest.raises(RunDefinitionConflict):
        asyncio.run(incompatible.execute(graph, _envelope(), run_id="restart"))

    restarted_store = SQLiteRunStore(database)
    restarted = AsyncGraphExecutor(
        restarted_store,
        workers={"a": worker_a, "b": worker_b},
        retry_policy=RetryPolicy(max_attempts=2),
    )
    result = asyncio.run(restarted.execute(graph, _envelope(), run_id="restart"))

    assert result.resumed_task_ids == ("a",)
    assert result.outputs == {"a": {"value": 7}, "b": {"doubled": 14}}
    assert calls == {"a": 1, "b": 2}
    events = restarted_store.events("restart")
    assert tuple(event.sequence for event in events) == tuple(range(1, len(events) + 1))
    b_attempts = [
        event.attempt
        for event in events
        if event.task_id == "b" and event.event_type == "task.attempt_started"
    ]
    assert b_attempts == [1, 2]


def test_writes_become_proposed_effect_intents_or_are_refused(tmp_path: Path) -> None:
    effect = Effect(
        kind=EffectClass.IDEMPOTENT_WRITE,
        resource="miami-eoc/notices",
        idempotency_key="notice-42",
    )
    graph = ExecutionGraph.from_tasks((_task("publish", effect=effect),))
    worker_called = False

    async def forbidden_worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal worker_called
        worker_called = True
        return WorkerResult({"should_not": "run"})

    refused_store = SQLiteRunStore(tmp_path / "refused-runs.db")
    refused = AsyncGraphExecutor(refused_store, workers={"publish": forbidden_worker})
    with pytest.raises(EffectExecutionRefused):
        asyncio.run(refused.execute(graph, _envelope(), run_id="refused"))
    assert not worker_called
    assert any(
        event.event_type == "task.effect_refused" for event in refused_store.events("refused")
    )

    accepted_store = SQLiteRunStore(tmp_path / "accepted-runs.db")
    effect_broker = SQLiteEffectBroker(tmp_path / "effects.db", broker_id="executor")
    accepted = AsyncGraphExecutor(
        accepted_store,
        workers={"publish": forbidden_worker},
        effect_broker=effect_broker,
    )
    result = asyncio.run(accepted.execute(graph, _envelope(), run_id="accepted"))
    output = result.outputs["publish"]
    assert isinstance(output, dict)
    assert output["executed_externally"] is False
    intent = effect_broker.get(output["effect_intent_id"])
    assert intent.state is EffectState.PROPOSED
    assert intent.idempotency_key == "notice-42"
    assert result.run_state is RunState.AWAITING_EFFECTS
    assert any(event.event_type == "run.awaiting_effects" for event in result.events)
    assert not any(event.event_type == "run.completed" for event in result.events)
    assert not worker_called
