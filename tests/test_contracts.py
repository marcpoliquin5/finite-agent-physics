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
