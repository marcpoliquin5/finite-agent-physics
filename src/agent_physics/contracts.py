"""Typed contracts for finite agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


MAX_RESOURCE_UNITS = (1 << 63) - 1


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


class CancellationSemantics(str, Enum):
    NONE = "none"
    COOPERATIVE = "cooperative"
    HARD = "hard"


class CheckpointSemantics(str, Enum):
    NONE = "none"
    RECEIPT = "receipt"
    RESUMABLE = "resumable"


class UsageSemantics(str, Enum):
    NONE = "none"
    ESTIMATED = "estimated"
    PROVIDER_REPORTED = "provider_reported"


@dataclass(frozen=True, slots=True)
class AdapterRequirements:
    """Minimum worker semantics that admission must verify before dispatch."""

    cancellation: CancellationSemantics = CancellationSemantics.NONE
    checkpoint: CheckpointSemantics = CheckpointSemantics.NONE
    streaming: bool = False
    usage: UsageSemantics = UsageSemantics.NONE
    effect_fencing: bool = False
    max_hidden_retries: int = 0

    def validate(self, task_id: str) -> list[str]:
        errors: list[str] = []
        if type(self.cancellation) is not CancellationSemantics:
            errors.append(f"task {task_id!r}: invalid cancellation requirement")
        if type(self.checkpoint) is not CheckpointSemantics:
            errors.append(f"task {task_id!r}: invalid checkpoint requirement")
        if type(self.streaming) is not bool:
            errors.append(f"task {task_id!r}: streaming requirement must be boolean")
        if type(self.usage) is not UsageSemantics:
            errors.append(f"task {task_id!r}: invalid usage requirement")
        if type(self.effect_fencing) is not bool:
            errors.append(f"task {task_id!r}: effect_fencing requirement must be boolean")
        if type(self.max_hidden_retries) is not int or self.max_hidden_retries < 0:
            errors.append(f"task {task_id!r}: max_hidden_retries must be a non-negative integer")
        return errors


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Versioned adapter ABI inspected by the executor before any worker call."""

    adapter_id: str
    adapter_version: str
    provider: str
    cancellation: CancellationSemantics
    checkpoint: CheckpointSemantics
    streaming: bool
    usage: UsageSemantics
    supported_effects: tuple[EffectClass, ...]
    effect_fencing: bool
    hidden_retries_max: int
    schema_version: str = "finite-adapter-capabilities/v1"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.schema_version != "finite-adapter-capabilities/v1":
            errors.append("unsupported adapter capability schema")
        if not self.adapter_id or not self.adapter_version or not self.provider:
            errors.append("adapter ID, version, and provider are required")
        if type(self.cancellation) is not CancellationSemantics:
            errors.append("invalid adapter cancellation semantics")
        if type(self.checkpoint) is not CheckpointSemantics:
            errors.append("invalid adapter checkpoint semantics")
        if type(self.streaming) is not bool:
            errors.append("adapter streaming capability must be boolean")
        if type(self.usage) is not UsageSemantics:
            errors.append("invalid adapter usage semantics")
        if type(self.effect_fencing) is not bool:
            errors.append("adapter effect_fencing capability must be boolean")
        if type(self.hidden_retries_max) is not int or self.hidden_retries_max < 0:
            errors.append("adapter hidden_retries_max must be a non-negative integer")
        if any(type(effect) is not EffectClass for effect in self.supported_effects):
            errors.append("adapter supported_effects contains an invalid effect class")
        if len(self.supported_effects) != len(set(self.supported_effects)):
            errors.append("adapter supported_effects must be unique")
        return errors

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "provider": self.provider,
            "cancellation": self.cancellation.value,
            "checkpoint": self.checkpoint.value,
            "streaming": self.streaming,
            "usage": self.usage.value,
            "supported_effects": [effect.value for effect in self.supported_effects],
            "effect_fencing": self.effect_fencing,
            "hidden_retries_max": self.hidden_retries_max,
        }


@dataclass(frozen=True, slots=True)
class OutputPort:
    """A versioned artifact type produced by one task."""

    name: str
    schema: str
    schema_version: str
    media_type: str

    def validate(self, task_id: str) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append(f"task {task_id!r}: output port names cannot be empty")
        if not self.schema or not self.schema_version or not self.media_type:
            errors.append(
                f"task {task_id!r}: output port {self.name!r} requires schema, "
                "schema_version, and media_type"
            )
        return errors


@dataclass(frozen=True, slots=True)
class InputPort:
    """A versioned artifact input bound to one direct producer port."""

    name: str
    source_task_id: str
    source_port: str
    schema: str
    schema_version: str
    media_type: str

    def validate(self, task_id: str) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append(f"task {task_id!r}: input port names cannot be empty")
        if not self.source_task_id or not self.source_port:
            errors.append(
                f"task {task_id!r}: input port {self.name!r} requires a producer task and port"
            )
        if not self.schema or not self.schema_version or not self.media_type:
            errors.append(
                f"task {task_id!r}: input port {self.name!r} requires schema, "
                "schema_version, and media_type"
            )
        return errors


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
        if (
            self.kind
            in {
                EffectClass.IDEMPOTENT_WRITE,
                EffectClass.IRREVERSIBLE_WRITE,
            }
            and not self.idempotency_key
        ):
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
    profile_snapshot_digest: str | None = None
    cpu_time_ms: int = 0
    peak_memory_bytes: int = 0
    peak_vram_bytes: int = 0
    storage_read_bytes: int = 0
    storage_write_bytes: int = 0
    network_ingress_bytes: int = 0
    network_egress_bytes: int = 0
    min_bandwidth_bps: int = 0
    network_rtt_ms: int = 0
    egress_cost_microusd: int = 0

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
        if self.profile_snapshot_digest is not None and (
            len(self.profile_snapshot_digest) != 64
            or any(
                character not in "0123456789abcdef" for character in self.profile_snapshot_digest
            )
        ):
            errors.append(f"task {task_id!r}: profile snapshot digest must be lowercase SHA-256")
        physical = (
            self.cpu_time_ms,
            self.peak_memory_bytes,
            self.peak_vram_bytes,
            self.storage_read_bytes,
            self.storage_write_bytes,
            self.network_ingress_bytes,
            self.network_egress_bytes,
            self.min_bandwidth_bps,
            self.network_rtt_ms,
            self.egress_cost_microusd,
        )
        if any(
            type(value) is not int or not 0 <= value <= MAX_RESOURCE_UNITS for value in physical
        ):
            errors.append(
                f"task {task_id!r}: physical resource values must be non-negative int64 units"
            )
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
    input_ports: tuple[InputPort, ...] = ()
    output_ports: tuple[OutputPort, ...] = ()
    adapter_requirements: AdapterRequirements | None = None

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
        for port in self.input_ports:
            errors.extend(port.validate(self.task_id))
        for port in self.output_ports:
            errors.extend(port.validate(self.task_id))
        if self.adapter_requirements is not None:
            if type(self.adapter_requirements) is not AdapterRequirements:
                errors.append(f"task {self.task_id!r}: invalid adapter requirements")
            else:
                errors.extend(self.adapter_requirements.validate(self.task_id))
        input_names = [port.name for port in self.input_ports]
        if len(input_names) != len(set(input_names)):
            errors.append(f"task {self.task_id!r}: input port names must be unique")
        output_names = [port.name for port in self.output_ports]
        if len(output_names) != len(set(output_names)):
            errors.append(f"task {self.task_id!r}: output port names must be unique")
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
    max_cpu_time_ms: int = MAX_RESOURCE_UNITS
    max_peak_memory_bytes: int = MAX_RESOURCE_UNITS
    max_peak_vram_bytes: int = MAX_RESOURCE_UNITS
    max_storage_read_bytes: int = MAX_RESOURCE_UNITS
    max_storage_write_bytes: int = MAX_RESOURCE_UNITS
    max_network_ingress_bytes: int = MAX_RESOURCE_UNITS
    max_network_egress_bytes: int = MAX_RESOURCE_UNITS
    available_bandwidth_bps: int = MAX_RESOURCE_UNITS
    max_network_rtt_ms: int = MAX_RESOURCE_UNITS
    max_egress_cost_microusd: int = MAX_RESOURCE_UNITS

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
        physical_caps = (
            self.max_cpu_time_ms,
            self.max_peak_memory_bytes,
            self.max_peak_vram_bytes,
            self.max_storage_read_bytes,
            self.max_storage_write_bytes,
            self.max_network_ingress_bytes,
            self.max_network_egress_bytes,
            self.available_bandwidth_bps,
            self.max_network_rtt_ms,
            self.max_egress_cost_microusd,
        )
        if any(
            type(value) is not int or not 0 <= value <= MAX_RESOURCE_UNITS
            for value in physical_caps
        ):
            errors.append("physical resource caps must be non-negative int64 units")
        return errors
