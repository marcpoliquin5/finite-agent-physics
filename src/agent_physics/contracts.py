"""Typed contracts for finite agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EffectClass(str, Enum):
    """The externally observable effect of a task."""

    PURE = "pure"
    READ = "read"
    IDEMPOTENT_WRITE = "idempotent_write"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"

    @property
    def writes(self) -> bool:
        return self in {
            EffectClass.IDEMPOTENT_WRITE,
            EffectClass.REVERSIBLE_WRITE,
            EffectClass.IRREVERSIBLE_WRITE,
        }


@dataclass(frozen=True, slots=True)
class Effect:
    """Effect and authority contract for one task."""

    kind: EffectClass = EffectClass.PURE
    resource: str = ""
    requires_approval: bool = False
    idempotency_key: str | None = None
    compensation: str | None = None

    def validate(self, task_id: str) -> list[str]:
        errors: list[str] = []
        if self.kind is not EffectClass.PURE and not self.resource:
            errors.append(f"task {task_id!r}: non-pure effects require a resource")
        if self.kind in {
            EffectClass.IDEMPOTENT_WRITE,
            EffectClass.IRREVERSIBLE_WRITE,
        } and not self.idempotency_key:
            errors.append(f"task {task_id!r}: {self.kind.value} requires an idempotency key")
        if self.kind is EffectClass.REVERSIBLE_WRITE and not self.compensation:
            errors.append(f"task {task_id!r}: reversible writes require a compensation handler")
        if self.kind is EffectClass.IRREVERSIBLE_WRITE and not self.requires_approval:
            errors.append(f"task {task_id!r}: irreversible writes must declare an approval gate")
        return errors


@dataclass(frozen=True, slots=True)
class BackendProfile:
    """Observed or estimated behavior for a task on one backend."""

    name: str
    provider: str
    duration_ms_p50: int
    duration_ms_p95: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_microusd: int = 0
    context_bytes: int = 0
    quality: float = 1.0
    failure_probability: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def validate(self, task_id: str) -> list[str]:
        errors: list[str] = []
        if not self.name or not self.provider:
            errors.append(f"task {task_id!r}: backend name and provider are required")
        if self.duration_ms_p50 < 0 or self.duration_ms_p95 < self.duration_ms_p50:
            errors.append(f"task {task_id!r}: invalid p50/p95 duration for {self.name!r}")
        if min(self.input_tokens, self.output_tokens, self.cost_microusd, self.context_bytes) < 0:
            errors.append(f"task {task_id!r}: backend resources cannot be negative")
        if not 0 <= self.quality <= 1:
            errors.append(f"task {task_id!r}: quality must be between 0 and 1")
        if not 0 <= self.failure_probability <= 1:
            errors.append(f"task {task_id!r}: failure probability must be between 0 and 1")
        return errors


@dataclass(frozen=True, slots=True)
class TaskContract:
    """A schedulable unit with explicit data, resource, and effect semantics."""

    task_id: str
    profiles: tuple[BackendProfile, ...]
    dependencies: tuple[str, ...] = ()
    effect: Effect = field(default_factory=Effect)
    optional: bool = False
    value: float = 1.0
    min_quality: float = 0.0
    deadline_ms: int | None = None
    description: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.task_id:
            errors.append("task IDs cannot be empty")
        if not self.profiles:
            errors.append(f"task {self.task_id!r}: at least one backend profile is required")
        if self.task_id in self.dependencies:
            errors.append(f"task {self.task_id!r}: a task cannot depend on itself")
        if self.value < 0:
            errors.append(f"task {self.task_id!r}: value cannot be negative")
        if not 0 <= self.min_quality <= 1:
            errors.append(f"task {self.task_id!r}: min_quality must be between 0 and 1")
        if self.deadline_ms is not None and self.deadline_ms <= 0:
            errors.append(f"task {self.task_id!r}: deadline must be positive")
        errors.extend(self.effect.validate(self.task_id))
        for profile in self.profiles:
            errors.extend(profile.validate(self.task_id))
        identities = [(profile.name, profile.provider) for profile in self.profiles]
        if len(identities) != len(set(identities)):
            errors.append(f"task {self.task_id!r}: backend identities must be unique")
        if self.profiles and not any(p.quality >= self.min_quality for p in self.profiles):
            errors.append(f"task {self.task_id!r}: no backend meets the quality floor")
        return errors


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    """Finite resources and service-level constraints for one graph run."""

    deadline_ms: int
    max_tokens: int
    max_cost_microusd: int
    max_context_bytes: int
    max_parallelism: int
    min_modeled_success_probability: float = 0.0
    provider_limits: tuple[tuple[str, int], ...] = ()

    def provider_limit(self, provider: str) -> int:
        limits = dict(self.provider_limits)
        return limits.get(provider, self.max_parallelism)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.deadline_ms <= 0:
            errors.append("run deadline must be positive")
        if min(self.max_tokens, self.max_cost_microusd, self.max_context_bytes) < 0:
            errors.append("run resource budgets cannot be negative")
        if self.max_parallelism <= 0:
            errors.append("max_parallelism must be positive")
        if not 0 <= self.min_modeled_success_probability <= 1:
            errors.append("min_modeled_success_probability must be between 0 and 1")
        for provider, limit in self.provider_limits:
            if not provider or limit <= 0:
                errors.append("provider limits require a name and positive capacity")
        provider_names = [provider for provider, _ in self.provider_limits]
        if len(provider_names) != len(set(provider_names)):
            errors.append("provider limits must contain unique provider names")
        return errors
