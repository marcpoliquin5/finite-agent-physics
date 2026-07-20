"""Strict, versioned workflow interchange for framework-neutral execution.

Version 1 deliberately describes only a finite task DAG, backend profiles, an
effect contract, and a run envelope.  It does not yet represent alternative
branches, speculative execution, or typed artifact input/output ports.  Those
features fail closed as unknown fields instead of being silently discarded.

All accepted source forms (Python mappings, JSON, and YAML) pass through the
same validator and are emitted as a single canonical JSON representation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import yaml

from .contracts import BackendProfile, Effect, EffectClass, RunEnvelope, TaskContract
from .graph import ExecutionGraph, GraphValidationError
from .serialization import canonical_json


WORKFLOW_SCHEMA_VERSION = 1
"""The only workflow IR schema version understood by this release."""

UNSUPPORTED_SCHEMA_FEATURES = (
    "alternative branches",
    "speculative execution",
    "typed artifact ports",
)
"""Semantics intentionally absent from schema version 1."""


class WorkflowIRValidationError(ValueError):
    """Raised when workflow source cannot be compiled without guessing."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping level."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """Validated contracts plus their immutable canonical interchange form."""

    schema_version: int
    graph: ExecutionGraph
    envelope: RunEnvelope
    canonical_json: str
    digest: str

    def to_python(self) -> dict[str, Any]:
        """Return a detached JSON-compatible copy of the normalized document."""

        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):  # pragma: no cover - construction invariant
            raise RuntimeError("canonical workflow document is not an object")
        return value


def compile_workflow(
    source: Mapping[str, Any] | str | bytes,
    *,
    source_format: Literal["python", "json", "yaml"] | None = None,
) -> CompiledWorkflow:
    """Compile a Python mapping, JSON document, or safely loaded YAML document.

    Text defaults to JSON when its first non-whitespace character is ``{`` and
    YAML otherwise.  Callers handling an externally declared media type should
    pass ``source_format`` explicitly.
    """

    if source_format not in (None, "python", "json", "yaml"):
        raise WorkflowIRValidationError(f"unsupported source format {source_format!r}")

    if isinstance(source, Mapping):
        if source_format not in (None, "python"):
            raise WorkflowIRValidationError(
                f"{source_format} source must be text or UTF-8 bytes"
            )
        document: Any = source
    elif isinstance(source, (str, bytes)):
        if source_format == "python":
            raise WorkflowIRValidationError("python source must be a mapping")
        text = _decode_text(source)
        selected_format = source_format or (
            "json" if text.lstrip().startswith("{") else "yaml"
        )
        document = _load_json(text) if selected_format == "json" else _load_yaml(text)
    else:
        raise WorkflowIRValidationError(
            "workflow source must be a mapping, text, or UTF-8 bytes"
        )

    return _compile_document(document)


def compile_python(source: Mapping[str, Any]) -> CompiledWorkflow:
    """Compile a Python mapping using the versioned interchange schema."""

    return compile_workflow(source, source_format="python")


def compile_json(source: str | bytes) -> CompiledWorkflow:
    """Compile a strict JSON document (including duplicate-key rejection)."""

    return compile_workflow(source, source_format="json")


def compile_yaml(source: str | bytes) -> CompiledWorkflow:
    """Compile YAML through a duplicate-rejecting ``yaml.SafeLoader``."""

    return compile_workflow(source, source_format="yaml")


def _decode_text(source: str | bytes) -> str:
    if isinstance(source, str):
        return source
    try:
        return source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowIRValidationError("workflow bytes must be valid UTF-8") from exc


def _reject_json_constant(value: str) -> None:
    raise WorkflowIRValidationError(f"JSON constant {value!r} is not finite")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowIRValidationError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _load_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except WorkflowIRValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise WorkflowIRValidationError(f"invalid JSON: {exc}") from exc


def _load_yaml(text: str) -> Any:
    try:
        return yaml.load(text, Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise WorkflowIRValidationError(f"invalid YAML: {exc}") from exc


def _compile_document(document: Any) -> CompiledWorkflow:
    root = _object(
        document,
        "$",
        allowed={"schema_version", "envelope", "tasks"},
        required={"schema_version", "envelope", "tasks"},
    )
    schema_version = _integer(root["schema_version"], "$.schema_version")
    if schema_version != WORKFLOW_SCHEMA_VERSION:
        raise WorkflowIRValidationError(
            f"$.schema_version: unsupported version {schema_version!r}; "
            f"expected {WORKFLOW_SCHEMA_VERSION}"
        )

    envelope = _compile_envelope(root["envelope"])
    task_values = _array(root["tasks"], "$.tasks")
    tasks = tuple(_compile_task(value, index) for index, value in enumerate(task_values))
    tasks = tuple(sorted(tasks, key=lambda task: task.task_id))

    try:
        graph = ExecutionGraph.from_tasks(tasks)
    except GraphValidationError as exc:
        raise WorkflowIRValidationError(f"$.tasks: {exc}") from exc
    envelope_errors = envelope.validate()
    if envelope_errors:
        raise WorkflowIRValidationError(f"$.envelope: {'; '.join(envelope_errors)}")

    normalized = _normalized_document(schema_version, graph, envelope)
    normalized_json = canonical_json(normalized)
    digest = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
    return CompiledWorkflow(
        schema_version=schema_version,
        graph=graph,
        envelope=envelope,
        canonical_json=normalized_json,
        digest=digest,
    )


def _compile_envelope(value: Any) -> RunEnvelope:
    path = "$.envelope"
    obj = _object(
        value,
        path,
        allowed={
            "deadline_ms",
            "max_tokens",
            "max_cost_microusd",
            "max_context_bytes",
            "max_parallelism",
            "min_modeled_success_probability",
            "provider_limits",
        },
        required={
            "deadline_ms",
            "max_tokens",
            "max_cost_microusd",
            "max_context_bytes",
            "max_parallelism",
        },
    )
    provider_limits_value = obj.get("provider_limits", {})
    provider_limits_obj = _object(
        provider_limits_value,
        f"{path}.provider_limits",
        allowed=None,
        required=set(),
    )
    provider_limits = tuple(
        (
            provider,
            _integer(limit, f"{path}.provider_limits.{provider}"),
        )
        for provider, limit in sorted(provider_limits_obj.items())
    )
    return RunEnvelope(
        deadline_ms=_integer(obj["deadline_ms"], f"{path}.deadline_ms"),
        max_tokens=_integer(obj["max_tokens"], f"{path}.max_tokens"),
        max_cost_microusd=_integer(
            obj["max_cost_microusd"], f"{path}.max_cost_microusd"
        ),
        max_context_bytes=_integer(
            obj["max_context_bytes"], f"{path}.max_context_bytes"
        ),
        max_parallelism=_integer(obj["max_parallelism"], f"{path}.max_parallelism"),
        min_modeled_success_probability=_finite_number(
            obj.get("min_modeled_success_probability", 0.0),
            f"{path}.min_modeled_success_probability",
        ),
        provider_limits=provider_limits,
    )


def _compile_task(value: Any, index: int) -> TaskContract:
    path = f"$.tasks[{index}]"
    obj = _object(
        value,
        path,
        allowed={
            "task_id",
            "profiles",
            "dependencies",
            "effect",
            "optional",
            "value",
            "min_quality",
            "deadline_ms",
            "description",
        },
        required={"task_id", "profiles"},
    )
    dependencies_values = _array(obj.get("dependencies", []), f"{path}.dependencies")
    dependencies = tuple(
        _string(item, f"{path}.dependencies[{dependency_index}]")
        for dependency_index, item in enumerate(dependencies_values)
    )
    if len(dependencies) != len(set(dependencies)):
        raise WorkflowIRValidationError(f"{path}.dependencies: entries must be unique")

    profile_values = _array(obj["profiles"], f"{path}.profiles")
    profiles = tuple(
        _compile_profile(profile, f"{path}.profiles[{profile_index}]")
        for profile_index, profile in enumerate(profile_values)
    )
    profiles = tuple(sorted(profiles, key=lambda profile: (profile.name, profile.provider)))

    deadline_value = obj.get("deadline_ms")
    deadline = (
        None
        if deadline_value is None
        else _integer(deadline_value, f"{path}.deadline_ms")
    )
    return TaskContract(
        task_id=_string(obj["task_id"], f"{path}.task_id"),
        profiles=profiles,
        dependencies=tuple(sorted(dependencies)),
        effect=_compile_effect(obj.get("effect", {}), f"{path}.effect"),
        optional=_boolean(obj.get("optional", False), f"{path}.optional"),
        value=_finite_number(obj.get("value", 1.0), f"{path}.value"),
        min_quality=_finite_number(
            obj.get("min_quality", 0.0), f"{path}.min_quality"
        ),
        deadline_ms=deadline,
        description=_string(obj.get("description", ""), f"{path}.description"),
    )


def _compile_profile(value: Any, path: str) -> BackendProfile:
    obj = _object(
        value,
        path,
        allowed={
            "name",
            "provider",
            "duration_ms_p50",
            "duration_ms_p95",
            "input_tokens",
            "output_tokens",
            "cost_microusd",
            "context_bytes",
            "quality",
            "failure_probability",
        },
        required={"name", "provider", "duration_ms_p50", "duration_ms_p95"},
    )
    return BackendProfile(
        name=_string(obj["name"], f"{path}.name"),
        provider=_string(obj["provider"], f"{path}.provider"),
        duration_ms_p50=_integer(obj["duration_ms_p50"], f"{path}.duration_ms_p50"),
        duration_ms_p95=_integer(obj["duration_ms_p95"], f"{path}.duration_ms_p95"),
        input_tokens=_integer(obj.get("input_tokens", 0), f"{path}.input_tokens"),
        output_tokens=_integer(obj.get("output_tokens", 0), f"{path}.output_tokens"),
        cost_microusd=_integer(obj.get("cost_microusd", 0), f"{path}.cost_microusd"),
        context_bytes=_integer(obj.get("context_bytes", 0), f"{path}.context_bytes"),
        quality=_finite_number(obj.get("quality", 1.0), f"{path}.quality"),
        failure_probability=_finite_number(
            obj.get("failure_probability", 0.0), f"{path}.failure_probability"
        ),
    )


def _compile_effect(value: Any, path: str) -> Effect:
    obj = _object(
        value,
        path,
        allowed={
            "kind",
            "resource",
            "requires_approval",
            "idempotency_key",
            "compensation",
        },
        required=set(),
    )
    kind_value = _string(obj.get("kind", EffectClass.PURE.value), f"{path}.kind")
    try:
        kind = EffectClass(kind_value)
    except ValueError as exc:
        allowed = ", ".join(repr(member.value) for member in EffectClass)
        raise WorkflowIRValidationError(
            f"{path}.kind: expected one of {allowed}; got {kind_value!r}"
        ) from exc
    return Effect(
        kind=kind,
        resource=_string(obj.get("resource", ""), f"{path}.resource"),
        requires_approval=_boolean(
            obj.get("requires_approval", False), f"{path}.requires_approval"
        ),
        idempotency_key=_optional_string(
            obj.get("idempotency_key"), f"{path}.idempotency_key"
        ),
        compensation=_optional_string(
            obj.get("compensation"), f"{path}.compensation"
        ),
    )


def _object(
    value: Any,
    path: str,
    *,
    allowed: set[str] | None,
    required: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowIRValidationError(f"{path}: expected an object")
    non_string_keys = [key for key in value if not isinstance(key, str)]
    if non_string_keys:
        raise WorkflowIRValidationError(
            f"{path}: object keys must be strings; got {non_string_keys[0]!r}"
        )
    keys = set(value)
    if allowed is not None:
        unknown = sorted(keys - allowed)
        if unknown:
            raise WorkflowIRValidationError(
                f"{path}: unknown field{'s' if len(unknown) != 1 else ''} "
                + ", ".join(repr(field) for field in unknown)
            )
    missing = sorted(required - keys)
    if missing:
        raise WorkflowIRValidationError(
            f"{path}: missing required field{'s' if len(missing) != 1 else ''} "
            + ", ".join(repr(field) for field in missing)
        )
    return value


def _array(value: Any, path: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise WorkflowIRValidationError(f"{path}: expected an array")
    return value


def _integer(value: Any, path: str) -> int:
    if type(value) is not int:
        raise WorkflowIRValidationError(f"{path}: expected an integer")
    return value


def _finite_number(value: Any, path: str) -> float:
    if type(value) not in (int, float):
        raise WorkflowIRValidationError(f"{path}: expected a finite number")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise WorkflowIRValidationError(f"{path}: expected a finite number") from exc
    if not math.isfinite(normalized):
        raise WorkflowIRValidationError(f"{path}: expected a finite number")
    return 0.0 if normalized == 0 else normalized


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise WorkflowIRValidationError(f"{path}: expected a boolean")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise WorkflowIRValidationError(f"{path}: expected a string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _normalized_document(
    schema_version: int,
    graph: ExecutionGraph,
    envelope: RunEnvelope,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "envelope": {
            "deadline_ms": envelope.deadline_ms,
            "max_tokens": envelope.max_tokens,
            "max_cost_microusd": envelope.max_cost_microusd,
            "max_context_bytes": envelope.max_context_bytes,
            "max_parallelism": envelope.max_parallelism,
            "min_modeled_success_probability": envelope.min_modeled_success_probability,
            "provider_limits": dict(envelope.provider_limits),
        },
        "tasks": [
            {
                "task_id": task.task_id,
                "profiles": [
                    {
                        "name": profile.name,
                        "provider": profile.provider,
                        "duration_ms_p50": profile.duration_ms_p50,
                        "duration_ms_p95": profile.duration_ms_p95,
                        "input_tokens": profile.input_tokens,
                        "output_tokens": profile.output_tokens,
                        "cost_microusd": profile.cost_microusd,
                        "context_bytes": profile.context_bytes,
                        "quality": profile.quality,
                        "failure_probability": profile.failure_probability,
                    }
                    for profile in task.profiles
                ],
                "dependencies": list(task.dependencies),
                "effect": {
                    "kind": task.effect.kind.value,
                    "resource": task.effect.resource,
                    "requires_approval": task.effect.requires_approval,
                    "idempotency_key": task.effect.idempotency_key,
                    "compensation": task.effect.compensation,
                },
                "optional": task.optional,
                "value": task.value,
                "min_quality": task.min_quality,
                "deadline_ms": task.deadline_ms,
                "description": task.description,
            }
            for task in graph.tasks
        ],
    }
