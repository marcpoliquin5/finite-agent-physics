import pytest

from agent_physics import (
    BackendProfile,
    Effect,
    EffectClass,
    ExecutionGraph,
    RunEnvelope,
    Scheduler,
    TaskContract,
)
from agent_physics.contracts import (
    MAX_RESOURCE_UNITS,
    AdapterCapabilities,
    AdapterRequirements,
    InputPort,
    OutputPort,
)
from agent_physics.graph import GraphValidationError


PROFILE = BackendProfile("test", "local", 10, 20, quality=1.0)


def test_cycle_is_rejected() -> None:
    with pytest.raises(GraphValidationError, match="cycle"):
        ExecutionGraph.from_tasks(
            [
                TaskContract("a", (PROFILE,), ("b",)),
                TaskContract("b", (PROFILE,), ("a",)),
            ]
        )


def test_irreversible_write_requires_approval_and_idempotency() -> None:
    with pytest.raises(GraphValidationError, match="idempotency key"):
        ExecutionGraph.from_tasks(
            [
                TaskContract(
                    "publish",
                    (PROFILE,),
                    effect=Effect(
                        kind=EffectClass.IRREVERSIBLE_WRITE,
                        resource="alerts",
                    ),
                )
            ]
        )


def test_reversible_write_requires_compensation() -> None:
    with pytest.raises(GraphValidationError, match="compensation"):
        ExecutionGraph.from_tasks(
            [
                TaskContract(
                    "update",
                    (PROFILE,),
                    effect=Effect(kind=EffectClass.REVERSIBLE_WRITE, resource="record"),
                )
            ]
        )


def test_read_effect_requires_resource_identity() -> None:
    with pytest.raises(GraphValidationError, match="non-pure effects require a resource"):
        ExecutionGraph.from_tasks(
            [TaskContract("read", (PROFILE,), effect=Effect(kind=EffectClass.READ))]
        )


def test_duplicate_backend_identity_is_rejected() -> None:
    with pytest.raises(GraphValidationError, match="backend identities must be unique"):
        ExecutionGraph.from_tasks([TaskContract("a", (PROFILE, PROFILE))])


def test_duplicate_provider_limit_is_rejected() -> None:
    graph = ExecutionGraph.from_tasks([TaskContract("a", (PROFILE,))])
    envelope = RunEnvelope(
        deadline_ms=100,
        max_tokens=100,
        max_cost_microusd=100,
        max_context_bytes=100,
        max_parallelism=2,
        provider_limits=(("local", 1), ("local", 2)),
    )
    with pytest.raises(GraphValidationError, match="unique provider"):
        Scheduler().schedule(graph, envelope)


def test_adapter_requirement_validation_rejects_every_hostile_scalar_shape() -> None:
    requirements = AdapterRequirements(
        cancellation="cooperative",  # type: ignore[arg-type]
        checkpoint="receipt",  # type: ignore[arg-type]
        streaming=1,  # type: ignore[arg-type]
        usage="estimated",  # type: ignore[arg-type]
        effect_fencing=1,  # type: ignore[arg-type]
        max_hidden_retries=True,
    )

    assert requirements.validate("hostile") == [
        "task 'hostile': invalid cancellation requirement",
        "task 'hostile': invalid checkpoint requirement",
        "task 'hostile': streaming requirement must be boolean",
        "task 'hostile': invalid usage requirement",
        "task 'hostile': effect_fencing requirement must be boolean",
        "task 'hostile': max_hidden_retries must be a non-negative integer",
    ]


def test_adapter_capability_manifest_rejects_all_invalid_abi_fields() -> None:
    capabilities = AdapterCapabilities(
        adapter_id="",
        adapter_version="",
        provider="",
        cancellation="hard",  # type: ignore[arg-type]
        checkpoint="resumable",  # type: ignore[arg-type]
        streaming=1,  # type: ignore[arg-type]
        usage="provider_reported",  # type: ignore[arg-type]
        supported_effects=(EffectClass.PURE, EffectClass.PURE, "pure"),  # type: ignore[arg-type]
        effect_fencing=1,  # type: ignore[arg-type]
        hidden_retries_max=True,
        schema_version="finite-adapter-capabilities/v999",
    )

    errors = capabilities.validate()
    assert set(errors) == {
        "unsupported adapter capability schema",
        "adapter ID, version, and provider are required",
        "invalid adapter cancellation semantics",
        "invalid adapter checkpoint semantics",
        "adapter streaming capability must be boolean",
        "invalid adapter usage semantics",
        "adapter effect_fencing capability must be boolean",
        "adapter hidden_retries_max must be a non-negative integer",
        "adapter supported_effects contains an invalid effect class",
        "adapter supported_effects must be unique",
    }


def test_ports_and_backend_profiles_reject_ambiguous_or_out_of_range_contracts() -> None:
    output_errors = OutputPort("", "", "", "").validate("producer")
    assert len(output_errors) == 2
    assert "names cannot be empty" in output_errors[0]
    assert "requires schema" in output_errors[1]

    input_errors = InputPort("", "", "", "", "", "").validate("consumer")
    assert len(input_errors) == 3
    assert "names cannot be empty" in input_errors[0]
    assert "requires a producer" in input_errors[1]
    assert "requires schema" in input_errors[2]

    invalid_profile = BackendProfile(
        "",
        "",
        duration_ms_p50=-1,
        duration_ms_p95=-2,
        input_tokens=-1,
        quality=2.0,
        failure_probability=-0.1,
        profile_snapshot_digest="NOT-A-DIGEST",
        cpu_time_ms=True,
    )
    profile_errors = invalid_profile.validate("work")
    assert len(profile_errors) == 7
    assert any("name and provider" in item for item in profile_errors)
    assert any("p50/p95" in item for item in profile_errors)
    assert any("resources cannot be negative" in item for item in profile_errors)
    assert any("quality" in item for item in profile_errors)
    assert any("failure probability" in item for item in profile_errors)
    assert any("lowercase SHA-256" in item for item in profile_errors)
    assert any("non-negative int64" in item for item in profile_errors)

    overflow_profile = BackendProfile("x", "p", 0, 0, peak_vram_bytes=MAX_RESOURCE_UNITS + 1)
    assert any("non-negative int64" in item for item in overflow_profile.validate("work"))


def test_task_contract_validation_accumulates_independent_fail_closed_errors() -> None:
    structurally_invalid = TaskContract(
        "",
        (),
        dependencies=("",),
        value=-1,
        min_quality=2,
        deadline_ms=0,
        adapter_requirements=object(),  # type: ignore[arg-type]
    )
    errors = structurally_invalid.validate()
    assert "task IDs cannot be empty" in errors
    assert any("at least one backend" in item for item in errors)
    assert any("cannot depend on itself" in item for item in errors)
    assert any("value cannot be negative" in item for item in errors)
    assert any("min_quality" in item for item in errors)
    assert any("deadline must be positive" in item for item in errors)
    assert any("invalid adapter requirements" in item for item in errors)

    weak = BackendProfile("weak", "local", 1, 1, quality=0.1)
    input_port = InputPort("same", "source", "out", "schema", "1", "application/json")
    output_port = OutputPort("same", "schema", "1", "application/json")
    duplicate_errors = TaskContract(
        "work",
        (weak, weak),
        min_quality=0.9,
        input_ports=(input_port, input_port),
        output_ports=(output_port, output_port),
    ).validate()
    assert any("input port names must be unique" in item for item in duplicate_errors)
    assert any("output port names must be unique" in item for item in duplicate_errors)
    assert any("backend identities must be unique" in item for item in duplicate_errors)
    assert any("no backend meets the quality floor" in item for item in duplicate_errors)


def test_run_envelope_rejects_each_invalid_resource_and_concurrency_shape() -> None:
    envelope = RunEnvelope(
        deadline_ms=0,
        max_tokens=-1,
        max_cost_microusd=0,
        max_context_bytes=0,
        max_parallelism=0,
        min_modeled_success_probability=2.0,
        provider_limits=(("", 0), ("", 0)),
        max_cpu_time_ms=True,
    )

    errors = envelope.validate()
    assert set(errors) == {
        "run deadline must be positive",
        "run resource budgets cannot be negative",
        "max_parallelism must be positive",
        "min_modeled_success_probability must be between 0 and 1",
        "provider limits require a name and positive capacity",
        "provider limits must contain unique provider names",
        "physical resource caps must be non-negative int64 units",
    }


def test_graph_validation_rejects_duplicate_unknown_and_topological_cycle_paths() -> None:
    duplicate_unknown = ExecutionGraph(
        (
            TaskContract("same", (PROFILE,), dependencies=("missing",)),
            TaskContract("same", (PROFILE,)),
        )
    )
    with pytest.raises(GraphValidationError) as exc_info:
        duplicate_unknown.validate()
    message = str(exc_info.value)
    assert "task IDs must be unique" in message
    assert "unknown dependency 'missing'" in message

    cyclic = ExecutionGraph(
        (
            TaskContract("a", (PROFILE,), dependencies=("b",)),
            TaskContract("b", (PROFILE,), dependencies=("a",)),
        )
    )
    with pytest.raises(GraphValidationError, match="graph contains a cycle"):
        cyclic.topological_order()
