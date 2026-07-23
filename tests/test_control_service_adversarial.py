from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.adaptive_runtime import (
    AdaptiveInvariantError,
    AdaptiveRuntime,
    AdaptiveTaskContext,
    AdaptiveWorkerResult,
)
from agent_physics.contracts import (
    AdapterRequirements,
    BackendProfile,
    CancellationSemantics,
    CheckpointSemantics,
    Effect,
    EffectClass,
    RunEnvelope,
    TaskContract,
    UsageSemantics,
)
from agent_physics.control_api import ControlAPIError, ControlPlane
from agent_physics.control_service import (
    AdaptiveControlRuntime,
    AdaptiveControlServiceError,
    _selected_profile,
)
from agent_physics.effects import SQLiteEffectBroker, SimulatedEffectAdapter
from agent_physics.executor import ExecutionError, RunState, WorkerResult
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import RunNotFound, SQLiteRunStore, Usage
from agent_physics.workflow_ir import compile_contracts


def _profile(*, peak_memory_bytes: int = 0, quality: float = 1.0) -> BackendProfile:
    return BackendProfile(
        name="fixture",
        provider="local",
        duration_ms_p50=1,
        duration_ms_p95=2,
        input_tokens=10,
        output_tokens=0,
        cost_microusd=10,
        context_bytes=10,
        quality=quality,
        peak_memory_bytes=peak_memory_bytes,
    )


def _graph(*, effect: Effect = Effect(), peak_memory_bytes: int = 0) -> ExecutionGraph:
    return ExecutionGraph.from_tasks(
        (
            TaskContract(
                "work",
                (_profile(peak_memory_bytes=peak_memory_bytes),),
                effect=effect,
            ),
        )
    )


def _envelope(**changes: object) -> RunEnvelope:
    base = RunEnvelope(
        deadline_ms=100,
        max_tokens=100,
        max_cost_microusd=100,
        max_context_bytes=100,
        max_parallelism=1,
        provider_limits=(("local", 1),),
        max_peak_memory_bytes=100,
    )
    return replace(base, **changes)


async def _worker(_context: object) -> WorkerResult:
    return WorkerResult(
        {"fixture": True},
        Usage(tokens=1, cost_microusd=1, context_bytes=1),
    )


async def _exploding_worker(_context: object) -> WorkerResult:
    raise RuntimeError("injected deterministic worker failure")


async def _accept(_task: TaskContract, _output: object) -> bool:
    return True


async def _reject(_task: TaskContract, _output: object) -> bool:
    return False


def _service(
    tmp_path: Path,
    name: str,
    *,
    workers: dict[str, object] | None = None,
    validator: object = _accept,
) -> AdaptiveControlRuntime:
    store = SQLiteRunStore(tmp_path / f"{name}-runs.sqlite3")
    broker = SQLiteEffectBroker(
        tmp_path / f"{name}-effects.sqlite3",
        broker_id=f"{name}-broker",
    )
    return AdaptiveControlRuntime(
        store,
        broker,
        workers=workers if workers is not None else {"work": _worker},  # type: ignore[arg-type]
        output_validator=validator,  # type: ignore[arg-type]
    )


def _session(
    service: AdaptiveControlRuntime,
    graph: ExecutionGraph,
    *,
    run_id: str,
    paused: bool = True,
):
    return service._new_session(
        graph,
        _envelope(),
        run_id=run_id,
        cancellation_event=asyncio.Event(),
        paused=paused,
    )


def _append_completion(
    service: AdaptiveControlRuntime,
    *,
    run_id: str,
    task_id: str,
    output: object,
) -> None:
    started = service.store.start_attempt(
        run_id=run_id,
        task_id=task_id,
        provider="local",
        backend="fixture",
        estimated=Usage(),
        reserved=Usage(),
    )
    assert started.attempt == 1
    service.store.complete_attempt(
        run_id=run_id,
        task_id=task_id,
        attempt=1,
        output=output,
        estimated=Usage(),
        reserved=Usage(),
        actual=Usage(),
        output_kind="adversarial_corruption",
    )


def test_contract_preflight_refuses_invalid_or_physically_impossible_inputs(
    tmp_path: Path,
) -> None:
    invalid_task = TaskContract(
        "unqualified",
        (_profile(quality=0.1),),
        min_quality=0.9,
    )
    with pytest.raises(AdaptiveInvariantError, match="no quality-qualified profile"):
        _selected_profile(invalid_task)

    service = _service(tmp_path, "preflight")
    with pytest.raises(ValueError, match="boolean"):
        service.configure_start("run", paused=1)  # type: ignore[arg-type]

    with pytest.raises(AdaptiveInvariantError, match="invalid run envelope"):
        service._preflight(_graph(), _envelope(max_tokens=-1))

    missing = _service(tmp_path, "missing-worker", workers={})
    with pytest.raises(AdaptiveInvariantError, match="no worker"):
        missing._preflight(_graph(), _envelope())

    physical = _graph(peak_memory_bytes=1)
    with pytest.raises(AdaptiveInvariantError, match="peak_memory"):
        service._preflight(physical, _envelope(max_peak_memory_bytes=0))


def test_live_preflight_uses_full_scheduler_reliability_and_adapter_admission(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "full-admission")
    too_slow = _graph()
    too_slow = ExecutionGraph.from_tasks(
        (
            replace(
                too_slow.by_id["work"],
                profiles=(
                    replace(
                        _profile(),
                        duration_ms_p50=100,
                        duration_ms_p95=200,
                    ),
                ),
            ),
        )
    )
    with pytest.raises(AdaptiveInvariantError, match="no admissible backend plan"):
        service._preflight(too_slow, _envelope(deadline_ms=100))

    unreliable = ExecutionGraph.from_tasks(
        (TaskContract("work", (replace(_profile(), failure_probability=0.9),)),)
    )
    with pytest.raises(AdaptiveInvariantError, match="no admissible backend plan"):
        service._preflight(
            unreliable,
            _envelope(min_modeled_success_probability=0.99),
        )

    strict_adapter = ExecutionGraph.from_tasks(
        (
            TaskContract(
                "work",
                (_profile(),),
                adapter_requirements=AdapterRequirements(
                    cancellation=CancellationSemantics.HARD,
                    checkpoint=CheckpointSemantics.RESUMABLE,
                    streaming=True,
                    usage=UsageSemantics.PROVIDER_REPORTED,
                    effect_fencing=True,
                    max_hidden_retries=0,
                ),
            ),
        )
    )
    with pytest.raises(AdaptiveInvariantError, match="capability manifest"):
        service._preflight(strict_adapter, _envelope())


def test_admitted_profile_is_locked_and_provider_block_never_uses_unsafe_fallback(
    tmp_path: Path,
) -> None:
    safe = replace(
        _profile(),
        name="safe",
        provider="a-safe",
        peak_memory_bytes=1,
    )
    unsafe = replace(
        _profile(),
        name="unsafe",
        provider="z-unsafe",
        peak_memory_bytes=100,
    )
    graph = ExecutionGraph.from_tasks((TaskContract("work", (safe, unsafe)),))
    envelope = _envelope(
        provider_limits=(("a-safe", 1), ("z-unsafe", 1)),
        max_peak_memory_bytes=10,
    )
    calls: list[tuple[str, str]] = []

    def worker(context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
        calls.append((context.provider, context.backend))
        return AdaptiveWorkerResult(
            {"fixture": True},
            Usage(tokens=1, cost_microusd=1, context_bytes=1),
            duration_ms=1,
        )

    runtime = AdaptiveRuntime(
        SQLiteRunStore(tmp_path / "locked-profile.sqlite3"),
        graph,
        envelope,
        run_id="locked-profile",
        workers={"work": worker},
    )
    assert runtime._model.admitted_profiles["work"] == safe
    runtime.provider_429("a-safe", occurred_at_ms=0, reset_at_ms=10)
    assert runtime.dispatch_next(occurred_at_ms=0) is None
    with pytest.raises(AdaptiveInvariantError, match="unknown provider"):
        runtime.provider_capacity("z-unsafe", 1, occurred_at_ms=0)
    runtime.provider_reset("a-safe", occurred_at_ms=10)
    assert runtime.dispatch_next(occurred_at_ms=10) == "work"
    assert calls == [("a-safe", "safe")]


def test_admission_refusal_is_safe_http_422_before_durable_run_creation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, "http-admission", workers={})
        plane = ControlPlane(service)
        workflow = compile_contracts(_graph(), _envelope()).to_python()
        with pytest.raises(ControlAPIError) as refused:
            await plane.submit(workflow, run_id="http-admission", start_paused=True)
        assert refused.value.status == 422
        assert refused.value.code == "admission_refused"
        with pytest.raises(RunNotFound):
            service.store.get_run("http-admission")
        assert service._start_paused == {}

    asyncio.run(scenario())


def test_terminal_sessions_are_retired_while_durable_replay_remains_available(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, "session-retirement")
        for index in range(12):
            run_id = f"terminal-{index}"
            result = await service.execute(_graph(), _envelope(), run_id=run_id)
            assert result.run_state is RunState.COMPLETED
            assert run_id not in service._sessions
            assert service.adaptive_replay(run_id)["passed"] is True
        assert service._sessions == {}

        with pytest.raises(AdaptiveControlServiceError):
            await service.apply_adaptive_control(
                "terminal-0",
                kind="provider.capacity",
                expected_revision=3,
                occurred_at_ms=2,
                details={"provider": "local", "capacity": 1},
            )
        assert service._sessions == {}

    asyncio.run(scenario())


def test_worker_adapter_rejects_unknown_profiles_and_validator_failures(
    tmp_path: Path,
) -> None:
    graph = _graph()
    service = _service(tmp_path, "worker-profile")
    session = _session(service, graph, run_id="worker-profile")
    adapter = session.runtime._workers["work"]

    unknown = AdaptiveTaskContext(
        run_id="worker-profile",
        task_id="work",
        attempt=1,
        provider="undeclared",
        backend="fixture",
        dependency_outputs={},
    )
    with pytest.raises(AdaptiveInvariantError, match="unknown profile"):
        adapter(unknown)

    rejecting = _service(tmp_path, "worker-validator", validator=_reject)
    rejected_session = _session(rejecting, graph, run_id="worker-validator")
    declared = replace(unknown, run_id="worker-validator", provider="local")
    with pytest.raises(AdaptiveInvariantError, match="output validator"):
        rejected_session.runtime._workers["work"](declared)


def test_durable_workflow_recovery_fails_closed_on_missing_or_invalid_ir(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "durable-workflow")
    for run_id in ("missing-workflow", "invalid-workflow"):
        service.store.get_or_create_run(
            run_id=run_id,
            graph_digest="graph",
            envelope={},
            deadline_at_ms=1,
        )

    with pytest.raises(AdaptiveControlServiceError) as unavailable:
        service._compiled_run("missing-workflow")
    assert unavailable.value.control_code == "adaptive_state_unavailable"

    service.store.append_event(
        run_id="invalid-workflow",
        event_id="invalid-workflow:started",
        event_type="run.started",
        payload={"workflow": {"schema_version": "not-an-integer"}},
    )
    with pytest.raises(AdaptiveControlServiceError) as invalid:
        service._compiled_run("invalid-workflow")
    assert invalid.value.control_code == "adaptive_state_invalid"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unknown_task", "unknown adaptive task"),
        ("malformed_effect", "effect output is malformed"),
        ("missing_intent", "effect output has no intent"),
        ("wrong_scope", "violated its exact scope"),
        ("invalid_pure", "failed revalidation"),
    ],
)
def test_durable_output_corruption_is_detected_before_resume(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    effect = Effect(
        EffectClass.IDEMPOTENT_WRITE,
        resource="finite://effect/work",
        idempotency_key=f"effect-{case}",
    )
    graph = (
        _graph(effect=effect)
        if case in {"malformed_effect", "missing_intent", "wrong_scope"}
        else _graph()
    )
    validator = _reject if case == "invalid_pure" else _accept
    service = _service(tmp_path, f"durable-{case}", validator=validator)
    session = _session(service, graph, run_id=f"durable-{case}")

    task_id = "unknown" if case == "unknown_task" else "work"
    if case == "malformed_effect":
        output: object = "not-an-effect-object"
    elif case == "missing_intent":
        output = {"executed_externally": False}
    elif case == "wrong_scope":
        intent = service.effect_broker.propose(
            run_id=f"durable-{case}",
            action="wrong-action",
            resource="finite://effect/work",
            effect_class=EffectClass.IDEMPOTENT_WRITE,
            idempotency_key=f"forged-{case}",
            payload={"fixture": True},
        )
        output = {
            "effect_intent_id": intent.intent_id,
            "executed_externally": False,
        }
    else:
        output = {"fixture": True}
    _append_completion(
        service,
        run_id=f"durable-{case}",
        task_id=task_id,
        output=output,
    )

    with pytest.raises(ExecutionError, match=message):
        asyncio.run(service._validate_durable_outputs(session))


def test_terminal_projection_covers_refusal_running_and_committed_effect(
    tmp_path: Path,
) -> None:
    pure = _graph()
    running_service = _service(tmp_path, "terminal-running")
    running = _session(running_service, pure, run_id="terminal-running")
    with pytest.raises(ExecutionError, match="without a terminal state"):
        running_service._terminal_result(running)

    failing = _service(
        tmp_path,
        "terminal-refused",
        workers={"work": _exploding_worker},
    )
    with pytest.raises(ExecutionError, match="refused the residual plan"):
        asyncio.run(failing.execute(pure, _envelope(), run_id="terminal-refused"))
    assert failing.store.events("terminal-refused")[-1].event_type == "run.failed"

    completed = _service(tmp_path, "terminal-completed")
    result = asyncio.run(completed.execute(pure, _envelope(), run_id="terminal-completed"))
    assert result.run_state is RunState.COMPLETED

    effect_graph = _graph(
        effect=Effect(
            EffectClass.IDEMPOTENT_WRITE,
            resource="finite://effect/work",
            idempotency_key="terminal-effect",
        )
    )
    committed = _service(tmp_path, "terminal-committed")
    committed_session = _session(
        committed,
        effect_graph,
        run_id="terminal-committed",
        paused=False,
    )
    assert committed_session.runtime.dispatch_next(occurred_at_ms=1) == "work"
    effect_output = committed_session.runtime.result().outputs["work"]
    assert isinstance(effect_output, dict)
    intent_id = effect_output["effect_intent_id"]
    assert isinstance(intent_id, str)
    prepared = committed.effect_broker.prepare(intent_id)
    committed.effect_broker.approve(intent_id, prepared.fencing_token)
    committed.effect_broker.commit(
        intent_id,
        prepared.fencing_token,
        SimulatedEffectAdapter(),
    )
    projected = committed._terminal_result(committed_session)
    assert projected.run_state is RunState.COMPLETED


def test_paused_run_blocks_then_wakes_and_rejects_invalid_controls(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, "control-boundaries")
        run_id = "control-boundaries"
        service.configure_start(run_id, paused=True)
        execution = asyncio.create_task(service.execute(_graph(), _envelope(), run_id=run_id))
        for _ in range(100):
            if run_id in service._sessions:
                break
            await asyncio.sleep(0)
        session = service._sessions[run_id]
        revision = session.runtime.state.revision

        zero = await service.apply_adaptive_control(
            run_id,
            kind="provider.capacity",
            expected_revision=revision,
            occurred_at_ms=0,
            details={"provider": "local", "capacity": 0},
        )
        revision = zero["state"]["revision"]  # type: ignore[index]
        assert isinstance(revision, int)

        invalid_cases = (
            (
                "provider.capacity",
                -1,
                {"provider": "local", "capacity": 0},
                "invalid_control_time",
            ),
            (
                "provider.429",
                0,
                {"provider": "local", "reset_at_ms": 0},
                "invalid_reset_window",
            ),
            (
                "provider.capacity",
                0,
                {"provider": "local", "capacity": 2},
                "capacity_out_of_bounds",
            ),
            (
                "budget.cut",
                0,
                {"tokens": 101, "cost_microusd": 101, "context_bytes": 101},
                "invalid_control_event",
            ),
            (
                "budget.cut",
                0,
                {"tokens": -1, "cost_microusd": 1, "context_bytes": 1},
                "invalid_control_event",
            ),
        )
        for kind, occurred_at_ms, details, code in invalid_cases:
            with pytest.raises(AdaptiveControlServiceError) as rejected:
                await service.apply_adaptive_control(
                    run_id,
                    kind=kind,
                    expected_revision=revision,
                    occurred_at_ms=occurred_at_ms,
                    details=details,
                )
            assert rejected.value.control_code == code

        resumed = await service.apply_adaptive_control(
            run_id,
            kind="runtime.resume",
            expected_revision=revision,
            occurred_at_ms=0,
            details={},
        )
        assert resumed["decision"] is None
        with pytest.raises(AdaptiveControlServiceError) as not_paused:
            await service.apply_adaptive_control(
                run_id,
                kind="runtime.resume",
                expected_revision=revision,
                occurred_at_ms=0,
                details={},
            )
        assert not_paused.value.control_code == "run_not_paused"

        await asyncio.sleep(0.01)
        assert not execution.done()
        restored = await service.apply_adaptive_control(
            run_id,
            kind="provider.capacity",
            expected_revision=revision,
            occurred_at_ms=0,
            details={"provider": "local", "capacity": 1},
        )
        assert restored["replay"]["passed"] is True  # type: ignore[index]
        assert (await execution).run_state is RunState.COMPLETED

    asyncio.run(scenario())


def test_cold_restart_reconstructs_session_and_replay_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        graph = _graph()
        envelope = _envelope()
        initial = _service(tmp_path, "cold-restart")
        session = initial._new_session(
            graph,
            envelope,
            run_id="cold-restart",
            cancellation_event=asyncio.Event(),
            paused=True,
        )
        initial._persist_run_start(
            graph,
            envelope,
            run_id="cold-restart",
            start_paused=True,
        )

        restarted = AdaptiveControlRuntime(
            initial.store,
            initial.effect_broker,
            workers={"work": _worker},
            output_validator=_accept,
        )
        zero = await restarted.apply_adaptive_control(
            "cold-restart",
            kind="provider.capacity",
            expected_revision=session.runtime.state.revision,
            occurred_at_ms=0,
            details={"provider": "local", "capacity": 0},
        )
        assert "cold-restart" in restarted._sessions

        second_restart = AdaptiveControlRuntime(
            initial.store,
            initial.effect_broker,
            workers={"work": _worker},
            output_validator=_accept,
        )
        restored = await second_restart.apply_adaptive_control(
            "cold-restart",
            kind="provider.capacity",
            expected_revision=zero["state"]["revision"],  # type: ignore[index]
            occurred_at_ms=0,
            details={"provider": "local", "capacity": 1},
        )
        assert restored["replay"]["passed"] is True  # type: ignore[index]

        final_restart = AdaptiveControlRuntime(
            initial.store,
            initial.effect_broker,
            workers={"work": _worker},
            output_validator=_accept,
        )
        result = await final_restart.resume_existing(
            "cold-restart",
            cancellation_event=asyncio.Event(),
        )
        assert result.run_state is RunState.COMPLETED

        tampered = _service(tmp_path, "replay-tampered")
        tampered_session = tampered._new_session(
            graph,
            envelope,
            run_id="replay-tampered",
            cancellation_event=asyncio.Event(),
            paused=True,
        )
        tampered._persist_run_start(
            graph,
            envelope,
            run_id="replay-tampered",
            start_paused=True,
        )
        tampered.store.append_event(
            run_id="replay-tampered",
            event_id="replay-tampered:forged-transition",
            event_type="adaptive.controller_transition",
            payload={"forged": True},
        )
        with pytest.raises(AdaptiveControlServiceError) as replay_failed:
            await tampered.apply_adaptive_control(
                "replay-tampered",
                kind="provider.capacity",
                expected_revision=tampered_session.runtime.state.revision,
                occurred_at_ms=0,
                details={"provider": "local", "capacity": 0},
            )
        assert replay_failed.value.control_code == "adaptive_replay_failed"

    asyncio.run(scenario())
