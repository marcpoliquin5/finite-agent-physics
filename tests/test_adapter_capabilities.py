from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.adapter_capabilities import (
    AdapterCapabilityError,
    validate_adapter_bindings,
)
from agent_physics.contracts import (
    AdapterCapabilities,
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
from agent_physics.executor import (
    AdmissionRefused,
    AsyncGraphExecutor,
    TaskExecutionContext,
    WorkerResult,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import RunNotFound, SQLiteRunStore, Usage
from agent_physics.workflow_ir import WorkflowIRValidationError, compile_python


def _profile(provider: str = "local") -> BackendProfile:
    return BackendProfile(
        "bounded",
        provider,
        duration_ms_p50=1,
        duration_ms_p95=5,
        input_tokens=5,
        output_tokens=5,
        cost_microusd=1,
        context_bytes=100,
    )


def _requirements() -> AdapterRequirements:
    return AdapterRequirements(
        cancellation=CancellationSemantics.COOPERATIVE,
        checkpoint=CheckpointSemantics.RECEIPT,
        streaming=False,
        usage=UsageSemantics.PROVIDER_REPORTED,
        effect_fencing=False,
        max_hidden_retries=0,
    )


def _graph() -> ExecutionGraph:
    return ExecutionGraph.from_tasks(
        (TaskContract("work", (_profile(),), adapter_requirements=_requirements()),)
    )


def _envelope() -> RunEnvelope:
    return RunEnvelope(1_000, 100, 100, 1_000, 1)


class CapableWorker:
    adapter_capabilities = AdapterCapabilities(
        adapter_id="test.capable",
        adapter_version="1.2.3",
        provider="local",
        cancellation=CancellationSemantics.COOPERATIVE,
        checkpoint=CheckpointSemantics.RESUMABLE,
        streaming=False,
        usage=UsageSemantics.PROVIDER_REPORTED,
        supported_effects=(EffectClass.PURE,),
        effect_fencing=False,
        hidden_retries_max=0,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _context: TaskExecutionContext) -> WorkerResult:
        self.calls += 1
        return WorkerResult({"ok": True}, Usage(tokens=10, cost_microusd=1, context_bytes=50))


def test_executor_refuses_missing_capability_manifest_before_worker_call(tmp_path: Path) -> None:
    calls = 0

    async def undeclared(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"unexpected": True})

    store = SQLiteRunStore(tmp_path / "missing.db")
    executor = AsyncGraphExecutor(store, workers={"work": undeclared})
    with pytest.raises(AdmissionRefused, match="no finite adapter capability manifest"):
        asyncio.run(executor.execute(_graph(), _envelope(), run_id="missing-capability"))

    assert calls == 0
    with pytest.raises(RunNotFound):
        store.get_run("missing-capability")


def test_capable_worker_is_admitted_and_manifest_binds_both_sides(tmp_path: Path) -> None:
    worker = CapableWorker()
    store = SQLiteRunStore(tmp_path / "capable.db")
    result = asyncio.run(
        AsyncGraphExecutor(store, workers={"work": worker}).execute(
            _graph(), _envelope(), run_id="capable"
        )
    )

    assert result.run_state.value == "completed"
    assert worker.calls == 1
    started = next(event for event in store.events("capable") if event.event_type == "run.started")
    binding = started.payload["manifest"]["adapter_bindings"][0]
    assert binding["task_id"] == "work"
    assert binding["requirements"]["usage"] == "provider_reported"
    assert binding["capabilities"]["adapter_version"] == "1.2.3"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"provider": "other"}, "does not match selected provider"),
        ({"cancellation": CancellationSemantics.NONE}, "cancellation semantics"),
        ({"checkpoint": CheckpointSemantics.NONE}, "checkpoint semantics"),
        ({"streaming": False}, "required streaming"),
        ({"usage": UsageSemantics.ESTIMATED}, "usage semantics"),
        ({"effect_fencing": False}, "required effect fencing"),
        ({"hidden_retries_max": 1}, "hidden retry bound"),
        ({"supported_effects": (EffectClass.READ,)}, "does not declare 'pure' support"),
    ],
)
def test_capability_mismatches_fail_before_dispatch(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    worker = CapableWorker()
    base = worker.adapter_capabilities.as_dict()
    values = {
        "adapter_id": base["adapter_id"],
        "adapter_version": base["adapter_version"],
        "provider": base["provider"],
        "cancellation": worker.adapter_capabilities.cancellation,
        "checkpoint": worker.adapter_capabilities.checkpoint,
        "streaming": base["streaming"],
        "usage": worker.adapter_capabilities.usage,
        "supported_effects": worker.adapter_capabilities.supported_effects,
        "effect_fencing": base["effect_fencing"],
        "hidden_retries_max": base["hidden_retries_max"],
    }
    values.update(mutation)
    if "streaming" in mutation:
        graph = ExecutionGraph.from_tasks(
            (
                TaskContract(
                    "work",
                    (_profile(),),
                    adapter_requirements=replace(_requirements(), streaming=True),
                ),
            )
        )
    elif "effect_fencing" in mutation:
        graph = ExecutionGraph.from_tasks(
            (
                TaskContract(
                    "work",
                    (_profile(),),
                    adapter_requirements=replace(_requirements(), effect_fencing=True),
                ),
            )
        )
    else:
        graph = _graph()
    worker.adapter_capabilities = AdapterCapabilities(**values)  # type: ignore[arg-type]
    executor = AsyncGraphExecutor(
        SQLiteRunStore(tmp_path / f"{message[:4]}.db"), workers={"work": worker}
    )

    with pytest.raises(AdmissionRefused, match=message):
        asyncio.run(executor.execute(graph, _envelope(), run_id="mismatch"))
    assert worker.calls == 0


def test_workflow_v2_compiles_strict_adapter_requirements() -> None:
    document = {
        "schema_version": 2,
        "envelope": {
            "deadline_ms": 1_000,
            "max_tokens": 100,
            "max_cost_microusd": 100,
            "max_context_bytes": 1_000,
            "max_parallelism": 1,
        },
        "tasks": [
            {
                "task_id": "work",
                "profiles": [
                    {
                        "name": "bounded",
                        "provider": "local",
                        "duration_ms_p50": 1,
                        "duration_ms_p95": 5,
                    }
                ],
                "adapter_requirements": {
                    "cancellation": "cooperative",
                    "checkpoint": "receipt",
                    "streaming": False,
                    "usage": "provider_reported",
                    "effect_fencing": False,
                    "max_hidden_retries": 0,
                },
            }
        ],
    }
    compiled = compile_python(document)
    assert compiled.graph.tasks[0].adapter_requirements == _requirements()
    assert compiled.to_python()["tasks"][0]["adapter_requirements"]["checkpoint"] == "receipt"

    document["tasks"][0]["adapter_requirements"]["usage"] = "invented"
    with pytest.raises(WorkflowIRValidationError, match="expected one of"):
        compile_python(document)


def test_binding_negotiation_skips_unrequired_and_write_tasks_but_rejects_invalid_manifest() -> (
    None
):
    profile = _profile()
    unrequired = TaskContract("unrequired", (profile,))
    write = TaskContract(
        "write",
        (profile,),
        effect=Effect(
            kind=EffectClass.IDEMPOTENT_WRITE,
            resource="simulation://write",
            idempotency_key="write-once",
        ),
        adapter_requirements=_requirements(),
    )
    assert (
        validate_adapter_bindings(
            {"unrequired": unrequired, "write": write},
            {"unrequired": profile, "write": profile},
            {},
        )
        == {}
    )

    worker = CapableWorker()
    worker.adapter_capabilities = replace(
        worker.adapter_capabilities,
        schema_version="finite-adapter-capabilities/v999",
    )
    required = TaskContract("required", (profile,), adapter_requirements=_requirements())
    with pytest.raises(AdapterCapabilityError, match="unsupported adapter capability schema"):
        validate_adapter_bindings(
            {"required": required},
            {"required": profile},
            {"required": worker},
        )
