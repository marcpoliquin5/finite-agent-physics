import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_physics.contracts import BackendProfile, Effect, EffectClass, RunEnvelope, TaskContract
from agent_physics.executor import (
    AsyncGraphExecutor,
    CancellationSignal,
    TaskExecutionContext,
    TaskExecutionFailed,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import SQLiteRunStore
from agent_physics.watsonx import WatsonxConfig, WatsonxGraniteAdapter, WatsonxInferenceReceipt
from agent_physics.watsonx_worker import (
    WatsonxTaskSpec,
    WatsonxTaskWorker,
    WatsonxWorkerError,
    validate_watsonx_task_output,
    watsonx_workers,
)


class FakeInference:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


def config() -> WatsonxConfig:
    return WatsonxConfig(
        url="https://example.ml.cloud.ibm.com",
        api_key="never-serialize-this-secret",
        project_id="project",
        model_id="ibm/granite-test",
    )


def graph(*, provider: str = "watsonx.ai", context_bytes: int = 2_000) -> ExecutionGraph:
    return ExecutionGraph(
        tasks=(
            TaskContract(
                task_id="synthesize",
                profiles=(
                    BackendProfile(
                        name="granite-test",
                        provider=provider,
                        duration_ms_p50=10,
                        duration_ms_p95=100,
                        input_tokens=80,
                        output_tokens=32,
                        cost_microusd=500,
                        context_bytes=context_bytes,
                    ),
                ),
            ),
        )
    )


def envelope(*, provider: str = "watsonx.ai") -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=5_000,
        max_tokens=200,
        max_cost_microusd=1_000,
        max_context_bytes=3_000,
        max_parallelism=1,
        provider_limits=((provider, 1),),
    )


def adapter(fake: FakeInference) -> WatsonxGraniteAdapter:
    return WatsonxGraniteAdapter(config(), lambda **_: fake)


def test_watsonx_worker_executes_inside_durable_executor_and_settles_usage(
    tmp_path: Path,
) -> None:
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "bounded answer",
                    "input_token_count": 12,
                    "generated_token_count": 4,
                    "stop_reason": "eos_token",
                }
            ]
        }
    )
    workers = watsonx_workers(
        adapter(fake),
        (WatsonxTaskSpec("synthesize", "Return a bounded answer.", max_new_tokens=32),),
    )
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    executor = AsyncGraphExecutor(
        store,
        workers=workers,
        output_validator=validate_watsonx_task_output,
        validator_revision="watsonx-output/v1",
    )

    result = asyncio.run(executor.execute(graph(), envelope(), run_id="live-bridge"))

    assert len(fake.calls) == 1
    assert result.actual_usage.tokens == 16
    assert result.actual_usage.cost_microusd == 500
    assert 0 < result.actual_usage.context_bytes <= 2_000
    output = result.outputs["synthesize"]
    assert isinstance(output, dict)
    assert output["measurement_kind"] == "injected-test-double"
    assert output["model_id"] == "ibm/granite-test"
    assert output["usage_semantics"]["cost_microusd"] == (
        "admitted-profile-upper-bound-not-provider-billing"
    )
    assert "never-serialize-this-secret" not in str(output)


def test_watsonx_worker_resume_never_recalls_a_completed_model_attempt(tmp_path: Path) -> None:
    first_fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "durable answer",
                    "input_token_count": 10,
                    "generated_token_count": 2,
                }
            ]
        }
    )
    spec = WatsonxTaskSpec("synthesize", "Return a durable answer.", max_new_tokens=32)
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    first = AsyncGraphExecutor(
        store,
        workers={"synthesize": WatsonxTaskWorker(adapter(first_fake), spec)},
        output_validator=validate_watsonx_task_output,
        validator_revision="watsonx-output/v1",
    )
    initial = asyncio.run(first.execute(graph(), envelope(), run_id="resume-live"))

    second_fake = FakeInference(RuntimeError("must never be called"))
    resumed_executor = AsyncGraphExecutor(
        store,
        workers={"synthesize": WatsonxTaskWorker(adapter(second_fake), spec)},
        output_validator=validate_watsonx_task_output,
        validator_revision="watsonx-output/v1",
    )
    resumed = asyncio.run(resumed_executor.execute(graph(), envelope(), run_id="resume-live"))

    assert initial.outputs == resumed.outputs
    assert resumed.resumed_task_ids == ("synthesize",)
    assert len(first_fake.calls) == 1
    assert second_fake.calls == []


def test_missing_provider_usage_fails_instead_of_fabricating_settlement(
    tmp_path: Path,
) -> None:
    fake = FakeInference({"results": [{"generated_text": "usage omitted"}]})
    executor = AsyncGraphExecutor(
        SQLiteRunStore(tmp_path / "runs.sqlite3"),
        workers={
            "synthesize": WatsonxTaskWorker(
                adapter(fake),
                WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
            )
        },
        output_validator=validate_watsonx_task_output,
        validator_revision="watsonx-output/v1",
    )

    with pytest.raises(TaskExecutionFailed, match="omitted token usage"):
        asyncio.run(executor.execute(graph(), envelope(), run_id="missing-usage"))
    events = executor.store.events("missing-usage")
    failure = next(event for event in events if event.event_type == "task.attempt_failed")
    assert failure.usage.actual.tokens == 0
    assert failure.payload["retryable"] is False


@pytest.mark.parametrize(
    ("selected_graph", "selected_envelope", "message"),
    [
        (graph(provider="other"), envelope(provider="other"), "not a watsonx.ai"),
        (graph(context_bytes=1), envelope(), "context-byte reservation"),
    ],
)
def test_worker_refuses_profile_contract_mismatch_before_provider_call(
    tmp_path: Path,
    selected_graph: ExecutionGraph,
    selected_envelope: RunEnvelope,
    message: str,
) -> None:
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "must not happen",
                    "input_token_count": 1,
                    "generated_token_count": 1,
                }
            ]
        }
    )
    executor = AsyncGraphExecutor(
        SQLiteRunStore(tmp_path / "runs.sqlite3"),
        workers={
            "synthesize": WatsonxTaskWorker(
                adapter(fake),
                WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
            )
        },
        output_validator=validate_watsonx_task_output,
        validator_revision="watsonx-output/v1",
    )

    with pytest.raises(TaskExecutionFailed, match=message):
        asyncio.run(
            executor.execute(
                selected_graph,
                selected_envelope,
                run_id=f"refuse-{message}",
            )
        )
    assert fake.calls == []


def test_watsonx_worker_capabilities_keep_hard_cancellation_and_billing_visible() -> None:
    capabilities = WatsonxTaskWorker.capabilities.as_dict()
    assert capabilities["bounded_hidden_retries"] is True
    assert capabilities["reports_provider_tokens"] is True
    assert capabilities["reports_provider_billing_cost"] is False
    assert capabilities["cooperative_cancellation"] is False
    assert capabilities["hard_cancellation"] is False
    assert capabilities["external_effects"] is False


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        (("", "instruction"), "task_id"),
        (("task", "   "), "instruction"),
        (("task", "instruction", 0), "max_new_tokens"),
    ],
)
def test_task_spec_rejects_missing_or_unbounded_contract(
    arguments: tuple[object, ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        WatsonxTaskSpec(*arguments)  # type: ignore[arg-type]


def _context(
    *,
    task: TaskContract | None = None,
    profile: BackendProfile | None = None,
    dependencies: dict[str, object] | None = None,
    cancellation: CancellationSignal | None = None,
) -> TaskExecutionContext:
    selected_task = task or graph().by_id["synthesize"]
    return TaskExecutionContext(
        run_id="edge-run",
        task=selected_task,
        profile=profile or selected_task.profiles[0],
        attempt=1,
        dependency_outputs=dependencies or {},
        deadline_at_ms=10_000,
        cancellation_event=cancellation or CancellationSignal(),
    )


def test_worker_rejects_invalid_context_task_effect_tokens_cancellation_and_dependencies() -> None:
    fake = FakeInference({"results": []})
    worker = WatsonxTaskWorker(
        adapter(fake),
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    with pytest.raises(WatsonxWorkerError, match="exact TaskExecutionContext"):
        asyncio.run(worker(object()))  # type: ignore[arg-type]

    wrong_task = replace(graph().by_id["synthesize"], task_id="other")
    with pytest.raises(WatsonxWorkerError, match="task ID"):
        asyncio.run(worker(_context(task=wrong_task)))

    write_task = replace(
        graph().by_id["synthesize"],
        effect=Effect(
            kind=EffectClass.IDEMPOTENT_WRITE,
            resource="sandbox/output",
            idempotency_key="watsonx-edge-write",
        ),
    )
    with pytest.raises(WatsonxWorkerError, match="external writes"):
        asyncio.run(worker(_context(task=write_task)))

    small_output = replace(graph().by_id["synthesize"].profiles[0], output_tokens=31)
    with pytest.raises(WatsonxWorkerError, match="generation bound"):
        asyncio.run(worker(_context(profile=small_output)))

    cancelled = CancellationSignal()
    cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker(_context(cancellation=cancelled)))

    with pytest.raises(WatsonxWorkerError, match="canonical JSON"):
        asyncio.run(worker(_context(dependencies={"bad": float("nan")})))
    assert fake.calls == []


def test_worker_rejects_unsupported_receipt_and_post_call_cancellation() -> None:
    unsupported = WatsonxTaskWorker(
        SimpleNamespace(generate=lambda *_args, **_kwargs: object()),  # type: ignore[arg-type]
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    with pytest.raises(WatsonxWorkerError, match="unsupported receipt"):
        asyncio.run(unsupported(_context()))

    signal = CancellationSignal()
    receipt = WatsonxInferenceReceipt(
        schema_version="finite-watsonx-receipt/v1",
        measurement_kind="injected-test-double",
        provider="watsonx.ai",
        model_id="ibm/granite-test",
        request_digest="a" * 64,
        response_digest="b" * 64,
        latency_ms=1,
        input_tokens=1,
        output_tokens=1,
        stop_reason=None,
        generated_text="answer",
    )

    def cancel_after_call(*_args: object, **_kwargs: object) -> WatsonxInferenceReceipt:
        signal.set()
        return receipt

    post_cancel = WatsonxTaskWorker(
        SimpleNamespace(generate=cancel_after_call),  # type: ignore[arg-type]
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(post_cancel(_context(cancellation=signal)))


def test_duplicate_worker_specs_are_rejected() -> None:
    selected_adapter = adapter(FakeInference({"results": []}))
    spec = WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32)
    with pytest.raises(ValueError, match="duplicate"):
        watsonx_workers(selected_adapter, (spec, spec))


def test_durable_output_validator_rejects_tampered_public_receipt_fields() -> None:
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "validated",
                    "input_token_count": 2,
                    "generated_token_count": 1,
                }
            ]
        }
    )
    worker = WatsonxTaskWorker(
        adapter(fake),
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    task = graph().by_id["synthesize"]

    context = TaskExecutionContext(
        run_id="validator",
        task=task,
        profile=task.profiles[0],
        attempt=1,
        dependency_outputs={},
        deadline_at_ms=10_000,
        cancellation_event=CancellationSignal(),
    )
    output = asyncio.run(worker(context)).output
    assert asyncio.run(validate_watsonx_task_output(task, output)) is True
    assert isinstance(output, dict)
    tampered = dict(output)
    tampered["generated_text"] = "changed"
    assert asyncio.run(validate_watsonx_task_output(task, tampered)) is False
    tampered = dict(output)
    tampered["usage_semantics"] = dict(output["usage_semantics"])
    tampered["usage_semantics"]["cost_microusd"] = "provider-billing"
    assert asyncio.run(validate_watsonx_task_output(task, tampered)) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "future"),
        ("provider", "other"),
        ("task_id", "other"),
        ("measurement_kind", "pretend-live"),
        ("usage_complete", False),
        ("attempt", 0),
        ("attempt", True),
        ("latency_ms", -1),
        ("input_tokens", "1"),
        ("request_digest", "short"),
        ("generated_text", 7),
        ("usage_semantics", []),
    ],
)
def test_output_validator_rejects_each_public_field_class(field: str, value: object) -> None:
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "validated",
                    "input_token_count": 2,
                    "generated_token_count": 1,
                }
            ]
        }
    )
    task = graph().by_id["synthesize"]
    worker = WatsonxTaskWorker(
        adapter(fake),
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    output = asyncio.run(worker(_context())).output
    assert isinstance(output, dict)
    changed = dict(output)
    changed[field] = value
    assert asyncio.run(validate_watsonx_task_output(task, changed)) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tokens", "estimated"),
        ("context_bytes", "tokens"),
        ("cost_microusd", "provider-billing"),
        ("prompt_bytes", -1),
        ("prompt_bytes", True),
    ],
)
def test_output_validator_rejects_usage_semantic_mutation(field: str, value: object) -> None:
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "validated",
                    "input_token_count": 2,
                    "generated_token_count": 1,
                }
            ]
        }
    )
    task = graph().by_id["synthesize"]
    worker = WatsonxTaskWorker(
        adapter(fake),
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    output = asyncio.run(worker(_context())).output
    assert isinstance(output, dict)
    changed = dict(output)
    changed["usage_semantics"] = dict(output["usage_semantics"])
    changed["usage_semantics"][field] = value
    assert asyncio.run(validate_watsonx_task_output(task, changed)) is False


def test_output_validator_rejects_shape_and_semantics_key_set() -> None:
    task = graph().by_id["synthesize"]
    assert asyncio.run(validate_watsonx_task_output(task, [])) is False
    fake = FakeInference(
        {
            "results": [
                {
                    "generated_text": "validated",
                    "input_token_count": 2,
                    "generated_token_count": 1,
                }
            ]
        }
    )
    worker = WatsonxTaskWorker(
        adapter(fake),
        WatsonxTaskSpec("synthesize", "Answer.", max_new_tokens=32),
    )
    output = asyncio.run(worker(_context())).output
    assert isinstance(output, dict)
    extra = dict(output)
    extra["unexpected"] = True
    assert asyncio.run(validate_watsonx_task_output(task, extra)) is False
    semantics = dict(output)
    semantics["usage_semantics"] = dict(output["usage_semantics"])
    semantics["usage_semantics"]["unexpected"] = True
    assert asyncio.run(validate_watsonx_task_output(task, semantics)) is False
