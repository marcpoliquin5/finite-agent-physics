"""Fail-closed adapter capability negotiation for FINITE worker admission."""

from __future__ import annotations

from collections.abc import Mapping

from .contracts import (
    AdapterCapabilities,
    BackendProfile,
    CancellationSemantics,
    CheckpointSemantics,
    TaskContract,
    UsageSemantics,
)


_CANCELLATION_RANK = {
    CancellationSemantics.NONE: 0,
    CancellationSemantics.COOPERATIVE: 1,
    CancellationSemantics.HARD: 2,
}
_CHECKPOINT_RANK = {
    CheckpointSemantics.NONE: 0,
    CheckpointSemantics.RECEIPT: 1,
    CheckpointSemantics.RESUMABLE: 2,
}
_USAGE_RANK = {
    UsageSemantics.NONE: 0,
    UsageSemantics.ESTIMATED: 1,
    UsageSemantics.PROVIDER_REPORTED: 2,
}


class AdapterCapabilityError(ValueError):
    """Raised before dispatch when a worker cannot meet declared semantics."""


def worker_capabilities(worker: object) -> AdapterCapabilities | None:
    """Return the exact public ABI manifest exposed by a worker, if any."""

    capabilities = getattr(worker, "adapter_capabilities", None)
    return capabilities if type(capabilities) is AdapterCapabilities else None


def validate_adapter_bindings(
    tasks: Mapping[str, TaskContract],
    profiles: Mapping[str, BackendProfile],
    workers: Mapping[str, object],
) -> dict[str, AdapterCapabilities]:
    """Validate all explicit task requirements before any worker is called."""

    accepted: dict[str, AdapterCapabilities] = {}
    errors: list[str] = []
    for task_id in sorted(profiles):
        task = tasks[task_id]
        requirement = task.adapter_requirements
        if requirement is None or task.effect.kind.writes:
            continue
        worker = workers.get(task_id)
        capabilities = worker_capabilities(worker) if worker is not None else None
        if capabilities is None:
            errors.append(f"task {task_id!r}: worker has no finite adapter capability manifest")
            continue
        capability_errors = capabilities.validate()
        if capability_errors:
            errors.extend(f"task {task_id!r}: {message}" for message in capability_errors)
            continue
        if capabilities.provider != profiles[task_id].provider:
            errors.append(
                f"task {task_id!r}: adapter provider {capabilities.provider!r} does not match "
                f"selected provider {profiles[task_id].provider!r}"
            )
        if (
            _CANCELLATION_RANK[capabilities.cancellation]
            < _CANCELLATION_RANK[requirement.cancellation]
        ):
            errors.append(f"task {task_id!r}: adapter cancellation semantics are insufficient")
        if _CHECKPOINT_RANK[capabilities.checkpoint] < _CHECKPOINT_RANK[requirement.checkpoint]:
            errors.append(f"task {task_id!r}: adapter checkpoint semantics are insufficient")
        if requirement.streaming and not capabilities.streaming:
            errors.append(f"task {task_id!r}: adapter does not support required streaming")
        if _USAGE_RANK[capabilities.usage] < _USAGE_RANK[requirement.usage]:
            errors.append(f"task {task_id!r}: adapter usage semantics are insufficient")
        if requirement.effect_fencing and not capabilities.effect_fencing:
            errors.append(f"task {task_id!r}: adapter does not support required effect fencing")
        if capabilities.hidden_retries_max > requirement.max_hidden_retries:
            errors.append(f"task {task_id!r}: adapter hidden retry bound exceeds task requirement")
        if task.effect.kind not in capabilities.supported_effects:
            errors.append(
                f"task {task_id!r}: adapter does not declare {task.effect.kind.value!r} support"
            )
        accepted[task_id] = capabilities
    if errors:
        raise AdapterCapabilityError("; ".join(errors))
    return accepted


__all__ = [
    "AdapterCapabilityError",
    "validate_adapter_bindings",
    "worker_capabilities",
]
