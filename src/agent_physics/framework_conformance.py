"""Strict framework-wrapper conformance and explicit semantic-loss accounting.

The neutral manifest in this module is a round-trip representation, not a claim
that another runtime enforces FINITE semantics.  Target-specific accounting
distinguishes native behavior, retained metadata, narrowed proposal-only
behavior, and absent features.  A LangGraph claim is emitted only by executing
the repository's pinned, instrumented LangGraph baseline.

The page-action contract is intentionally described as "PageAgent-style".  It
does not import, call, or claim integration with Alibaba PageAgent or BeeAI.
Browser observations are always tainted data; page mutations remain unexecuted
effect intents until an independent authority/effect layer acts on them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from .contracts import (
    AdapterRequirements,
    BackendProfile,
    Effect,
    EffectClass,
    InputPort,
    OutputPort,
    RunEnvelope,
)
from .langgraph_baseline import (
    LangGraphBaselineUnavailable,
    langgraph_baseline_available,
    run_langgraph_stormshift_baseline,
)
from .serialization import content_digest
from .workflow_ir import (
    SUPPORTED_WORKFLOW_SCHEMA_VERSIONS,
    CompiledWorkflow,
    WorkflowIRValidationError,
    compile_json,
    compile_python,
)


WRAPPER_SCHEMA_VERSION = "finite-framework-wrapper/v1"
NEUTRAL_TARGET = "neutral-wrapper-contract/v1"
LANGGRAPH_TARGET = "langgraph-stategraph-static/v1"
PINNED_LANGGRAPH_VERSION = "1.2.9"
PINNED_LANGGRAPH_CHECKPOINT_VERSION = "3.1.0"
LANGGRAPH_WITNESS_SCHEMA_VERSION = "finite-langgraph-conformance-witness/v1"

PAGE_ACTION_SCHEMA_VERSION = "finite-page-action-wrapper/v1"
PAGE_ACTION_WRAPPER_KIND = "governed-page-action-contract"
TAINTED_BROWSER_OBSERVATION = "untrusted-browser-observation-data"

_TARGETS = frozenset({NEUTRAL_TARGET, LANGGRAPH_TARGET})
_LOSS_POLICIES = frozenset({"record", "reject"})
_CHECKPOINT_MODES = frozenset({"none", "receipt", "resumable"})
_EFFECT_MODES = frozenset({"proposal_only", "fenced_commit"})
_CACHE_MODES = frozenset({"disabled", "content_addressed_readonly", "content_addressed_readwrite"})
_ACCOUNTING_FEATURES = (
    "dag",
    "dependencies",
    "effect-declarations",
    "profile-selection",
    "validators",
    "cache-policy",
    "typed-ports",
    "approvals",
    "run-budgets",
    "adapter-requirements",
    "retries",
    "checkpoint-semantics",
    "effect-execution-semantics",
)

_NEUTRAL_CLAIM_BOUNDARIES = (
    "representation and deterministic round-trip only",
    "no external framework was imported or executed by this manifest conversion",
    "represented contracts are not evidence that a target runtime enforces them",
)
_LANGGRAPH_CLAIM_BOUNDARIES = (
    "manifest conversion alone is not a LangGraph execution witness",
    "only run_pinned_langgraph_conformance_witness records actual pinned LangGraph execution",
    "profile choices in the fixture witness are static metadata and make no provider-model call",
    "write effects are proposal-only; no external effect is committed",
    "no Alibaba PageAgent or BeeAI support is claimed",
)
_PAGE_CLAIM_BOUNDARIES = (
    "local PageAgent-style contract only; Alibaba PageAgent is not imported or exercised",
    "BeeAI is not imported or exercised",
    "browser observations are untrusted data and cannot authorize tools or effects",
    "page mutations are durable-intent-shaped declarations, not executed browser actions",
)


class FrameworkConformanceError(ValueError):
    """A wrapper conversion or manifest violates its strict contract."""


class FrameworkUnavailableError(RuntimeError):
    """An optional framework needed for an executable witness is unavailable."""


class PinnedFrameworkVersionError(RuntimeError):
    """Installed framework versions differ from the reviewed witness versions."""


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    task_id: str
    profile_name: str
    provider: str
    selection_rule: str
    profile_digest: str


@dataclass(frozen=True, slots=True)
class CachePolicy:
    mode: str
    key_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WrapperRuntimePolicy:
    max_attempts: int = 1
    max_hidden_retries: int = 0
    checkpoint_mode: str = "receipt"
    effect_mode: str = "proposal_only"


@dataclass(frozen=True, slots=True)
class NeutralTaskWrapper:
    task_id: str
    dependencies: tuple[str, ...]
    profiles: tuple[BackendProfile, ...]
    selected_profile: ProfileSelection
    effect: Effect
    optional: bool
    value: float
    min_quality: float
    deadline_ms: int | None
    description: str
    input_ports: tuple[InputPort, ...]
    output_ports: tuple[OutputPort, ...]
    adapter_requirements: AdapterRequirements | None
    validator_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WrapperEdge:
    source_task_id: str
    target_task_id: str


@dataclass(frozen=True, slots=True)
class FeatureAccounting:
    feature: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class SemanticLoss:
    loss_id: str
    feature: str
    source_paths: tuple[str, ...]
    source_semantics: str
    target_semantics: str
    disposition: str = "metadata_only"


@dataclass(frozen=True, slots=True)
class FrameworkWrapperManifest:
    schema_version: str
    target: str
    source_workflow_schema_version: int
    source_workflow_digest: str
    envelope: RunEnvelope
    nodes: tuple[NeutralTaskWrapper, ...]
    edges: tuple[WrapperEdge, ...]
    cache_policy: CachePolicy
    runtime_policy: WrapperRuntimePolicy
    feature_accounting: tuple[FeatureAccounting, ...]
    semantic_losses: tuple[SemanticLoss, ...]
    claim_boundaries: tuple[str, ...]
    manifest_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "target": self.target,
            "source_workflow_schema_version": self.source_workflow_schema_version,
            "source_workflow_digest": self.source_workflow_digest,
            "envelope": self.envelope,
            "nodes": self.nodes,
            "edges": self.edges,
            "cache_policy": self.cache_policy,
            "runtime_policy": self.runtime_policy,
            "feature_accounting": self.feature_accounting,
            "semantic_losses": self.semantic_losses,
            "claim_boundaries": self.claim_boundaries,
        }

    def verify_digest(self) -> bool:
        return self.manifest_digest == content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class FiniteWrapperProjection:
    workflow: CompiledWorkflow
    selected_profiles: tuple[ProfileSelection, ...]
    validator_bindings: tuple[tuple[str, tuple[str, ...]], ...]
    cache_policy: CachePolicy
    runtime_policy: WrapperRuntimePolicy
    semantic_losses: tuple[SemanticLoss, ...]


@dataclass(frozen=True, slots=True)
class LangGraphConformanceWitness:
    schema_version: str
    framework: str
    framework_version: str
    checkpoint_package: str
    checkpoint_package_version: str
    pinned_versions_match: bool
    actual_framework_execution: bool
    run_id: str
    wrapper_manifest_digest: str
    baseline_record_digest: str
    graph_digest: str
    dependency_witness_digest: str
    all_tasks_executed_once: bool
    dependencies_preserved: bool
    static_profile_selection_preserved: bool
    validator_executed: bool
    cache_disabled: bool
    checkpoint_receipt_verified: bool
    effects_proposal_only: bool
    model_calls_made: bool
    external_calls_made: bool
    external_effects_executed: int
    semantic_loss_ids: tuple[str, ...]
    claim_boundaries: tuple[str, ...]
    witness_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
            if field != "witness_digest"
        }

    def verify_digest(self) -> bool:
        return self.witness_digest == content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    observation_id: str
    page_url: str
    captured_at_ms: int
    payload_digest: str
    taint_label: str = TAINTED_BROWSER_OBSERVATION


@dataclass(frozen=True, slots=True)
class PageActionIntent:
    intent_id: str
    action: str
    target: str
    mutates_page_or_external_state: bool
    effect_class: EffectClass
    authority_grant_id: str
    observation_ids: tuple[str, ...]
    idempotency_key: str
    requires_approval: bool
    compensation_action: str | None
    state: str = "proposed"
    executed_externally: bool = False


@dataclass(frozen=True, slots=True)
class GovernedPageActionContract:
    schema_version: str
    wrapper_kind: str
    contract_id: str
    allowed_hosts: tuple[str, ...]
    observations: tuple[BrowserObservation, ...]
    action_intents: tuple[PageActionIntent, ...]
    alibaba_pageagent_exercised: bool
    beeai_exercised: bool
    integration_evidence_digests: tuple[str, ...]
    claim_boundaries: tuple[str, ...]
    contract_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
            if field != "contract_digest"
        }

    def verify_digest(self) -> bool:
        return self.contract_digest == content_digest(self.unsigned_payload())


def _profile_sort_key(profile: BackendProfile) -> tuple[object, ...]:
    return (
        -profile.quality,
        profile.duration_ms_p95,
        profile.cost_microusd,
        profile.total_tokens,
        profile.context_bytes,
        profile.failure_probability,
        profile.provider,
        profile.name,
    )


def _default_profile(task: NeutralTaskWrapper | object) -> BackendProfile:
    profiles = cast(tuple[BackendProfile, ...], getattr(task, "profiles"))
    min_quality = cast(float, getattr(task, "min_quality"))
    qualified = [profile for profile in profiles if profile.quality >= min_quality]
    if not qualified:
        raise FrameworkConformanceError("task has no profile meeting its quality floor")
    return min(qualified, key=_profile_sort_key)


def _selection_for(
    task: object,
    requested: tuple[str, str] | None,
) -> ProfileSelection:
    task_id = cast(str, getattr(task, "task_id"))
    profiles = cast(tuple[BackendProfile, ...], getattr(task, "profiles"))
    if requested is None:
        selected = _default_profile(task)
        rule = "highest_quality_qualified_stable_tiebreak"
    else:
        provider, name = requested
        matches = tuple(
            profile for profile in profiles if profile.provider == provider and profile.name == name
        )
        if len(matches) != 1:
            raise FrameworkConformanceError(
                f"task {task_id!r}: selected profile {(provider, name)!r} is not unique"
            )
        selected = matches[0]
        if selected.quality < cast(float, getattr(task, "min_quality")):
            raise FrameworkConformanceError(
                f"task {task_id!r}: selected profile is below the quality floor"
            )
        rule = "caller-explicit"
    return ProfileSelection(
        task_id=task_id,
        profile_name=selected.name,
        provider=selected.provider,
        selection_rule=rule,
        profile_digest=content_digest(selected),
    )


def _edges(nodes: tuple[NeutralTaskWrapper, ...]) -> tuple[WrapperEdge, ...]:
    return tuple(
        sorted(
            (
                WrapperEdge(dependency, node.task_id)
                for node in nodes
                for dependency in node.dependencies
            ),
            key=lambda edge: (edge.source_task_id, edge.target_task_id),
        )
    )


def _loss(
    loss_id: str,
    feature: str,
    paths: tuple[str, ...],
    source: str,
    target: str,
    disposition: str = "metadata_only",
) -> SemanticLoss:
    return SemanticLoss(
        loss_id,
        feature,
        tuple(sorted(paths)),
        source,
        target,
        disposition,
    )


def _target_accounting(
    target: str,
    nodes: tuple[NeutralTaskWrapper, ...],
    envelope: RunEnvelope,
    cache_policy: CachePolicy,
    runtime_policy: WrapperRuntimePolicy,
) -> tuple[tuple[FeatureAccounting, ...], tuple[SemanticLoss, ...]]:
    if target == NEUTRAL_TARGET:
        accounting = tuple(
            FeatureAccounting(
                feature,
                "represented",
                "retained in the neutral manifest for deterministic reverse conversion",
            )
            for feature in _ACCOUNTING_FEATURES
        )
        return accounting, ()
    if target != LANGGRAPH_TARGET:
        raise FrameworkConformanceError(f"unsupported wrapper target {target!r}")

    losses: list[SemanticLoss] = []
    statuses: dict[str, tuple[str, str]] = {
        "dag": ("native", "StateGraph nodes preserve the finite task set"),
        "dependencies": ("native", "StateGraph edges and all-predecessor joins preserve the DAG"),
        "effect-declarations": (
            "metadata-only",
            "effect classes and resources remain manifest metadata",
        ),
        "profile-selection": (
            "static-wrapper",
            "one selected profile identity is retained per task; no provider call is implied",
        ),
        "validators": (
            "metadata-only",
            "validator IDs require an explicitly bound callable in a concrete wrapper",
        ),
        "cache-policy": (
            "native-disabled" if cache_policy.mode == "disabled" else "metadata-only",
            "the pinned witness compiles with cache=None"
            if cache_policy.mode == "disabled"
            else "the pinned witness does not configure cache reads or writes",
        ),
        "typed-ports": ("not-present", "the source workflow has no typed ports"),
        "approvals": ("not-present", "the source workflow has no approval gate"),
        "run-budgets": (
            "partially-native",
            "concurrency is configured; deadline, token, cost, context, and success admission are metadata",
        ),
        "adapter-requirements": (
            "not-present",
            "the source workflow has no adapter requirement contract",
        ),
        "retries": (
            "native-disabled"
            if runtime_policy.max_attempts == 1 and runtime_policy.max_hidden_retries == 0
            else "metadata-only",
            "the pinned witness configures no retry policy"
            if runtime_policy.max_attempts == 1 and runtime_policy.max_hidden_retries == 0
            else "the pinned witness does not implement the requested retry contract",
        ),
        "checkpoint-semantics": (
            "native-receipt"
            if runtime_policy.checkpoint_mode == "receipt"
            else (
                "native-disabled" if runtime_policy.checkpoint_mode == "none" else "metadata-only"
            ),
            "SQLite final-state receipt equality is exercised"
            if runtime_policy.checkpoint_mode == "receipt"
            else (
                "checkpoint behavior is explicitly disabled"
                if runtime_policy.checkpoint_mode == "none"
                else "restart/resume equivalence is not exercised by the pinned witness"
            ),
        ),
        "effect-execution-semantics": (
            "narrowed-proposal-only",
            "writes stop at a deterministic, non-executed proposal",
        ),
    }

    losses.append(
        _loss(
            "loss:langgraph:effect-declarations",
            "effect-declarations",
            tuple(f"$.tasks.{node.task_id}.effect" for node in nodes),
            "typed effect class, resource, approval, idempotency, and compensation contract",
            "retained as wrapper metadata; StateGraph does not enforce it",
        )
    )
    losses.append(
        _loss(
            "loss:langgraph:validators",
            "validators",
            tuple(f"$.tasks.{node.task_id}.validators" for node in nodes),
            "named validator bindings",
            "IDs are retained; generic callable binding is outside the manifest",
        )
    )
    losses.append(
        _loss(
            "loss:langgraph:run-budgets",
            "run-budgets",
            ("$.envelope",),
            (
                "deadline, token, cost, context, parallelism, provider, and modeled-success "
                "constraints"
            ),
            (
                "global concurrency is configured; remaining resources are not admitted or "
                "ledger-enforced"
            ),
        )
    )

    typed_paths = tuple(
        f"$.tasks.{node.task_id}.typed_ports"
        for node in nodes
        if node.input_ports or node.output_ports
    )
    if typed_paths:
        statuses["typed-ports"] = (
            "metadata-only",
            "StateGraph state does not enforce FINITE artifact schema/version/media contracts",
        )
        losses.append(
            _loss(
                "loss:langgraph:typed-ports",
                "typed-ports",
                typed_paths,
                "versioned producer/consumer artifact contracts",
                "retained as metadata without runtime schema enforcement",
            )
        )

    approval_paths = tuple(
        f"$.tasks.{node.task_id}.effect.requires_approval"
        for node in nodes
        if node.effect.requires_approval
    )
    if approval_paths:
        statuses["approvals"] = (
            "metadata-only",
            "the pinned wrapper does not authenticate or consume approval grants",
        )
        losses.append(
            _loss(
                "loss:langgraph:approvals",
                "approvals",
                approval_paths,
                "independent approval gate before irreversible commit",
                "approval bit retained; execution is narrowed to an unapproved proposal",
            )
        )

    adapter_paths = tuple(
        f"$.tasks.{node.task_id}.adapter_requirements"
        for node in nodes
        if node.adapter_requirements is not None
    )
    if adapter_paths:
        statuses["adapter-requirements"] = (
            "metadata-only",
            "StateGraph does not negotiate FINITE adapter capabilities before dispatch",
        )
        losses.append(
            _loss(
                "loss:langgraph:adapter-requirements",
                "adapter-requirements",
                adapter_paths,
                "cancellation, checkpoint, streaming, usage, fencing, and retry requirements",
                "retained as metadata without capability admission",
            )
        )

    if cache_policy.mode != "disabled":
        losses.append(
            _loss(
                "loss:langgraph:cache-policy",
                "cache-policy",
                ("$.cache_policy",),
                f"{cache_policy.mode} with explicit key fields",
                "pinned witness disables cache",
            )
        )
    if runtime_policy.max_attempts != 1 or runtime_policy.max_hidden_retries != 0:
        losses.append(
            _loss(
                "loss:langgraph:retries",
                "retries",
                ("$.runtime_policy.max_attempts", "$.runtime_policy.max_hidden_retries"),
                "bounded visible and hidden retry contract",
                "pinned witness configures no retry policy",
            )
        )
    if runtime_policy.checkpoint_mode == "resumable":
        losses.append(
            _loss(
                "loss:langgraph:checkpoint-resume",
                "checkpoint-semantics",
                ("$.runtime_policy.checkpoint_mode",),
                "resumable execution semantics",
                "only final-state SQLite receipt equality is exercised",
            )
        )

    write_paths = tuple(
        f"$.tasks.{node.task_id}.effect" for node in nodes if node.effect.kind.writes
    )
    if write_paths:
        losses.append(
            _loss(
                "loss:langgraph:effect-commit",
                "effect-execution-semantics",
                write_paths,
                "FINITE broker lifecycle, fencing, approval, reconciliation, and compensation",
                "deterministic proposal only; no external commit",
                "narrowed_proposal_only",
            )
        )
    else:
        statuses["effect-execution-semantics"] = (
            "not-present",
            "the source workflow has no write effect",
        )

    accounting = tuple(
        FeatureAccounting(feature, *statuses[feature]) for feature in _ACCOUNTING_FEATURES
    )
    return accounting, tuple(sorted(losses, key=lambda item: item.loss_id))


def _validate_cache_policy(policy: CachePolicy) -> tuple[str, ...]:
    problems: list[str] = []
    if policy.mode not in _CACHE_MODES:
        problems.append("cache policy mode is unsupported")
    if not isinstance(policy.key_fields, tuple) or any(
        not isinstance(item, str) or not item for item in policy.key_fields
    ):
        problems.append("cache key fields must be a tuple of nonempty strings")
    elif len(policy.key_fields) != len(set(policy.key_fields)):
        problems.append("cache key fields must be unique")
    if policy.mode == "disabled" and policy.key_fields:
        problems.append("disabled cache policy cannot declare key fields")
    if policy.mode != "disabled" and not policy.key_fields:
        problems.append("enabled cache policy requires explicit key fields")
    return tuple(problems)


def _validate_runtime_policy(policy: WrapperRuntimePolicy) -> tuple[str, ...]:
    problems: list[str] = []
    if type(policy.max_attempts) is not int or policy.max_attempts <= 0:
        problems.append("max_attempts must be a positive integer")
    if type(policy.max_hidden_retries) is not int or policy.max_hidden_retries < 0:
        problems.append("max_hidden_retries must be a nonnegative integer")
    if policy.checkpoint_mode not in _CHECKPOINT_MODES:
        problems.append("checkpoint mode is unsupported")
    if policy.effect_mode not in _EFFECT_MODES:
        problems.append("effect mode is unsupported")
    return tuple(problems)


def finite_to_wrapper(
    workflow: CompiledWorkflow,
    *,
    target: str = NEUTRAL_TARGET,
    selected_profiles: Mapping[str, tuple[str, str]] | None = None,
    validator_bindings: Mapping[str, tuple[str, ...]],
    cache_policy: CachePolicy,
    runtime_policy: WrapperRuntimePolicy | None = None,
    loss_policy: Literal["record", "reject"] = "record",
) -> FrameworkWrapperManifest:
    """Convert FINITE IR into a strict wrapper manifest without silent loss."""

    if not isinstance(workflow, CompiledWorkflow):
        raise FrameworkConformanceError("workflow must be a CompiledWorkflow")
    if compile_json(workflow.canonical_json).digest != workflow.digest:
        raise FrameworkConformanceError("compiled workflow canonical digest is inconsistent")
    if target not in _TARGETS:
        raise FrameworkConformanceError(f"unsupported wrapper target {target!r}")
    if loss_policy not in _LOSS_POLICIES:
        raise FrameworkConformanceError(f"unsupported loss policy {loss_policy!r}")
    runtime_policy = runtime_policy or WrapperRuntimePolicy()
    policy_problems = _validate_cache_policy(cache_policy) + _validate_runtime_policy(
        runtime_policy
    )
    if policy_problems:
        raise FrameworkConformanceError("; ".join(policy_problems))

    task_ids = set(workflow.graph.by_id)
    if set(validator_bindings) != task_ids:
        raise FrameworkConformanceError(
            "validator bindings must contain exactly every workflow task ID"
        )
    for task_id, validator_ids in validator_bindings.items():
        if not isinstance(validator_ids, tuple) or not validator_ids:
            raise FrameworkConformanceError(
                f"task {task_id!r}: at least one validator ID is required"
            )
        if any(not isinstance(item, str) or not item for item in validator_ids):
            raise FrameworkConformanceError(
                f"task {task_id!r}: validator IDs must be nonempty strings"
            )
        if len(validator_ids) != len(set(validator_ids)):
            raise FrameworkConformanceError(f"task {task_id!r}: validator IDs must be unique")
    requested = dict(selected_profiles or {})
    if requested and set(requested) != task_ids:
        raise FrameworkConformanceError(
            "explicit profile selections must contain exactly every workflow task ID"
        )

    nodes = tuple(
        NeutralTaskWrapper(
            task_id=task.task_id,
            dependencies=task.dependencies,
            profiles=task.profiles,
            selected_profile=_selection_for(task, requested.get(task.task_id)),
            effect=task.effect,
            optional=task.optional,
            value=task.value,
            min_quality=task.min_quality,
            deadline_ms=task.deadline_ms,
            description=task.description,
            input_ports=task.input_ports,
            output_ports=task.output_ports,
            adapter_requirements=task.adapter_requirements,
            validator_ids=validator_bindings[task.task_id],
        )
        for task in workflow.graph.tasks
    )
    accounting, losses = _target_accounting(
        target,
        nodes,
        workflow.envelope,
        cache_policy,
        runtime_policy,
    )
    if losses and loss_policy == "reject":
        raise FrameworkConformanceError(
            "target conversion has semantic loss: " + ", ".join(item.loss_id for item in losses)
        )
    unsigned: dict[str, object] = {
        "schema_version": WRAPPER_SCHEMA_VERSION,
        "target": target,
        "source_workflow_schema_version": workflow.schema_version,
        "source_workflow_digest": workflow.digest,
        "envelope": workflow.envelope,
        "nodes": nodes,
        "edges": _edges(nodes),
        "cache_policy": cache_policy,
        "runtime_policy": runtime_policy,
        "feature_accounting": accounting,
        "semantic_losses": losses,
        "claim_boundaries": (
            _NEUTRAL_CLAIM_BOUNDARIES if target == NEUTRAL_TARGET else _LANGGRAPH_CLAIM_BOUNDARIES
        ),
    }
    return FrameworkWrapperManifest(
        **unsigned,
        manifest_digest=content_digest(unsigned),
    )


def _task_document(node: NeutralTaskWrapper, schema_version: int) -> dict[str, object]:
    task: dict[str, object] = {
        "task_id": node.task_id,
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
            for profile in node.profiles
        ],
        "dependencies": list(node.dependencies),
        "effect": {
            "kind": node.effect.kind.value,
            "resource": node.effect.resource,
            "requires_approval": node.effect.requires_approval,
            "idempotency_key": node.effect.idempotency_key,
            "compensation": node.effect.compensation,
        },
        "optional": node.optional,
        "value": node.value,
        "min_quality": node.min_quality,
        "deadline_ms": node.deadline_ms,
        "description": node.description,
    }
    if schema_version >= 2:
        task["input_ports"] = [
            {
                "name": port.name,
                "source_task_id": port.source_task_id,
                "source_port": port.source_port,
                "schema": port.schema,
                "schema_version": port.schema_version,
                "media_type": port.media_type,
            }
            for port in node.input_ports
        ]
        task["output_ports"] = [
            {
                "name": port.name,
                "schema": port.schema,
                "schema_version": port.schema_version,
                "media_type": port.media_type,
            }
            for port in node.output_ports
        ]
        if node.adapter_requirements is not None:
            requirements = node.adapter_requirements
            task["adapter_requirements"] = {
                "cancellation": requirements.cancellation.value,
                "checkpoint": requirements.checkpoint.value,
                "streaming": requirements.streaming,
                "usage": requirements.usage.value,
                "effect_fencing": requirements.effect_fencing,
                "max_hidden_retries": requirements.max_hidden_retries,
            }
    return task


def _workflow_document(manifest: FrameworkWrapperManifest) -> dict[str, object]:
    envelope = manifest.envelope
    return {
        "schema_version": manifest.source_workflow_schema_version,
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
            _task_document(node, manifest.source_workflow_schema_version) for node in manifest.nodes
        ],
    }


def validate_wrapper_manifest(manifest: FrameworkWrapperManifest) -> tuple[str, ...]:
    """Return deterministic manifest problems; an empty tuple means valid."""

    problems: list[str] = []
    if not isinstance(manifest, FrameworkWrapperManifest):
        return ("value is not a FrameworkWrapperManifest",)
    if manifest.schema_version != WRAPPER_SCHEMA_VERSION:
        problems.append("wrapper schema version is unsupported")
    if manifest.target not in _TARGETS:
        problems.append("wrapper target is unsupported")
    if manifest.source_workflow_schema_version not in SUPPORTED_WORKFLOW_SCHEMA_VERSIONS:
        problems.append("source workflow schema version is unsupported")
    if not isinstance(manifest.nodes, tuple) or not manifest.nodes:
        problems.append("wrapper nodes must be a nonempty tuple")
    elif any(not isinstance(node, NeutralTaskWrapper) for node in manifest.nodes):
        problems.append("wrapper contains a malformed node")
    if not isinstance(manifest.edges, tuple) or any(
        not isinstance(edge, WrapperEdge) for edge in manifest.edges
    ):
        problems.append("wrapper edges are malformed")
    if not isinstance(manifest.cache_policy, CachePolicy):
        problems.append("cache policy is malformed")
    else:
        problems.extend(_validate_cache_policy(manifest.cache_policy))
    if not isinstance(manifest.runtime_policy, WrapperRuntimePolicy):
        problems.append("runtime policy is malformed")
    else:
        problems.extend(_validate_runtime_policy(manifest.runtime_policy))

    if not problems:
        node_ids = tuple(node.task_id for node in manifest.nodes)
        if node_ids != tuple(sorted(node_ids)) or len(node_ids) != len(set(node_ids)):
            problems.append("wrapper node IDs must be unique and sorted")
        expected_edges = _edges(manifest.nodes)
        if manifest.edges != expected_edges:
            problems.append("wrapper edges do not exactly equal node dependencies")
        for node in manifest.nodes:
            selection = node.selected_profile
            if selection.task_id != node.task_id:
                problems.append(f"task {node.task_id!r}: selected profile task ID differs")
                continue
            profiles = {(profile.provider, profile.name): profile for profile in node.profiles}
            selected = profiles.get((selection.provider, selection.profile_name))
            if selected is None:
                problems.append(f"task {node.task_id!r}: selected profile does not exist")
            elif selection.profile_digest != content_digest(selected):
                problems.append(f"task {node.task_id!r}: selected profile digest differs")
            elif selected.quality < node.min_quality:
                problems.append(f"task {node.task_id!r}: selected profile is below quality floor")
            if selection.selection_rule not in {
                "highest_quality_qualified_stable_tiebreak",
                "caller-explicit",
            }:
                problems.append(f"task {node.task_id!r}: profile selection rule is unknown")
            elif selection.selection_rule == "highest_quality_qualified_stable_tiebreak":
                deterministic = _default_profile(node)
                if (selection.provider, selection.profile_name) != (
                    deterministic.provider,
                    deterministic.name,
                ):
                    problems.append(
                        f"task {node.task_id!r}: selected profile violates its deterministic rule"
                    )
            if not isinstance(node.validator_ids, tuple) or not node.validator_ids:
                problems.append(f"task {node.task_id!r}: validator bindings are empty")
            elif any(not isinstance(value, str) or not value for value in node.validator_ids):
                problems.append(f"task {node.task_id!r}: validator binding is malformed")
            elif len(node.validator_ids) != len(set(node.validator_ids)):
                problems.append(f"task {node.task_id!r}: validator bindings are duplicated")
        try:
            reconstructed = compile_python(_workflow_document(manifest))
        except (WorkflowIRValidationError, AttributeError, TypeError, ValueError) as exc:
            problems.append(f"wrapper cannot reconstruct valid FINITE IR: {type(exc).__name__}")
        else:
            if reconstructed.digest != manifest.source_workflow_digest:
                problems.append("reconstructed FINITE workflow digest differs from source")
        expected_accounting, expected_losses = _target_accounting(
            manifest.target,
            manifest.nodes,
            manifest.envelope,
            manifest.cache_policy,
            manifest.runtime_policy,
        )
        if manifest.feature_accounting != expected_accounting:
            problems.append("feature accounting is incomplete or inconsistent")
        if manifest.semantic_losses != expected_losses:
            problems.append("semantic-loss ledger is incomplete or inconsistent")
        expected_boundaries = (
            _NEUTRAL_CLAIM_BOUNDARIES
            if manifest.target == NEUTRAL_TARGET
            else _LANGGRAPH_CLAIM_BOUNDARIES
        )
        if manifest.claim_boundaries != expected_boundaries:
            problems.append("claim boundaries were weakened or changed")
    if not manifest.verify_digest():
        problems.append("manifest digest is invalid")
    return tuple(sorted(set(problems)))


def wrapper_to_finite(manifest: FrameworkWrapperManifest) -> FiniteWrapperProjection:
    """Reconstruct exact FINITE IR plus wrapper-only policy metadata."""

    problems = validate_wrapper_manifest(manifest)
    if problems:
        raise FrameworkConformanceError("; ".join(problems))
    workflow = compile_python(_workflow_document(manifest))
    return FiniteWrapperProjection(
        workflow=workflow,
        selected_profiles=tuple(node.selected_profile for node in manifest.nodes),
        validator_bindings=tuple((node.task_id, node.validator_ids) for node in manifest.nodes),
        cache_policy=manifest.cache_policy,
        runtime_policy=manifest.runtime_policy,
        semantic_losses=manifest.semantic_losses,
    )


def _compiled_from_graph(graph: object, envelope: RunEnvelope) -> CompiledWorkflow:
    tasks = cast(tuple[object, ...], getattr(graph, "tasks"))
    # Build through the same strict compiler used by public workflow IR. This is
    # deliberately not a privileged graph-to-manifest shortcut.
    placeholder_nodes = tuple(
        NeutralTaskWrapper(
            task_id=cast(str, getattr(task, "task_id")),
            dependencies=cast(tuple[str, ...], getattr(task, "dependencies")),
            profiles=cast(tuple[BackendProfile, ...], getattr(task, "profiles")),
            selected_profile=_selection_for(task, None),
            effect=cast(Effect, getattr(task, "effect")),
            optional=cast(bool, getattr(task, "optional")),
            value=cast(float, getattr(task, "value")),
            min_quality=cast(float, getattr(task, "min_quality")),
            deadline_ms=cast(int | None, getattr(task, "deadline_ms")),
            description=cast(str, getattr(task, "description")),
            input_ports=cast(tuple[InputPort, ...], getattr(task, "input_ports")),
            output_ports=cast(tuple[OutputPort, ...], getattr(task, "output_ports")),
            adapter_requirements=cast(
                AdapterRequirements | None,
                getattr(task, "adapter_requirements"),
            ),
            validator_ids=("placeholder-validator",),
        )
        for task in tasks
    )
    temporary = FrameworkWrapperManifest(
        WRAPPER_SCHEMA_VERSION,
        NEUTRAL_TARGET,
        2,
        "pending",
        envelope,
        placeholder_nodes,
        _edges(placeholder_nodes),
        CachePolicy("disabled"),
        WrapperRuntimePolicy(),
        (),
        (),
        _NEUTRAL_CLAIM_BOUNDARIES,
        "pending",
    )
    return compile_python(_workflow_document(temporary))


def langgraph_conformance_available() -> bool:
    """Return whether the optional pinned witness packages are importable."""

    return langgraph_baseline_available()


async def run_pinned_langgraph_conformance_witness(
    *,
    run_id: str,
    checkpoint_path: str | Path,
) -> LangGraphConformanceWitness:
    """Execute the real pinned LangGraph comparator and bind checked evidence."""

    if not langgraph_conformance_available():
        raise FrameworkUnavailableError(
            "install the pinned witness with `pip install -e .[langgraph]`"
        )
    framework_version = metadata.version("langgraph")
    checkpoint_version = metadata.version("langgraph-checkpoint-sqlite")
    if (
        framework_version != PINNED_LANGGRAPH_VERSION
        or checkpoint_version != PINNED_LANGGRAPH_CHECKPOINT_VERSION
    ):
        raise PinnedFrameworkVersionError(
            "reviewed witness requires langgraph=="
            f"{PINNED_LANGGRAPH_VERSION} and langgraph-checkpoint-sqlite=="
            f"{PINNED_LANGGRAPH_CHECKPOINT_VERSION}; found "
            f"{framework_version} and {checkpoint_version}"
        )

    from .examples import miami_eoc_graph
    from .stormshift_runtime import PUBLISH_TASK_ID, stormshift_envelope

    graph = miami_eoc_graph()
    compiled = _compiled_from_graph(graph, stormshift_envelope())
    validators = {
        task.task_id: (
            ("effect-proposal-boundary/v1",)
            if task.task_id == PUBLISH_TASK_ID
            else ("stormshift-fixture-output-validator/v1",)
        )
        for task in graph.tasks
    }
    manifest = finite_to_wrapper(
        compiled,
        target=LANGGRAPH_TARGET,
        validator_bindings=validators,
        cache_policy=CachePolicy("disabled"),
        runtime_policy=WrapperRuntimePolicy(
            max_attempts=1,
            max_hidden_retries=0,
            checkpoint_mode="receipt",
            effect_mode="proposal_only",
        ),
    )
    try:
        record = await run_langgraph_stormshift_baseline(
            run_id=run_id,
            checkpoint_path=checkpoint_path,
        )
    except LangGraphBaselineUnavailable as exc:  # race with environment changes
        raise FrameworkUnavailableError(str(exc)) from exc

    expected_ids = set(graph.by_id)
    all_once = dict(record.task_call_counts) == {task_id: 1 for task_id in expected_ids}
    observations = {item.task_id: item for item in record.dependency_observations}
    dependencies_preserved = set(observations) == expected_ids and all(
        observations[task.task_id].dependency_ids == tuple(sorted(task.dependencies))
        for task in graph.tasks
    )
    selections = {node.task_id: node.selected_profile for node in manifest.nodes}
    static_profiles_preserved = all(
        item.task_id in selections
        and item.profile_name == selections[item.task_id].profile_name
        and item.provider == selections[item.task_id].provider
        for item in record.static_profiles
    ) and {item.task_id for item in record.static_profiles} == (expected_ids - {PUBLISH_TASK_ID})
    validator_executed = (
        record.validation.get("passed") is True
        and record.validation.get("report_digest") == record.validation_digest
    )
    proposal = record.outputs.get(PUBLISH_TASK_ID)
    effects_proposal_only = (
        isinstance(proposal, dict)
        and proposal.get("effect_state") == "proposed"
        and proposal.get("executed_externally") is False
        and record.external_effects_executed == 0
    )
    invariants = (
        all_once,
        dependencies_preserved,
        static_profiles_preserved,
        validator_executed,
        record.cache_enabled is False,
        record.checkpoint_verified,
        effects_proposal_only,
        record.model_calls_made is False,
        record.external_calls_made is False,
    )
    if not all(invariants):
        raise FrameworkConformanceError(
            "actual LangGraph witness violated one or more declared invariants"
        )

    fields: dict[str, object] = {
        "schema_version": LANGGRAPH_WITNESS_SCHEMA_VERSION,
        "framework": "langgraph",
        "framework_version": record.framework_version,
        "checkpoint_package": record.checkpoint_package,
        "checkpoint_package_version": record.checkpoint_package_version,
        "pinned_versions_match": True,
        "actual_framework_execution": True,
        "run_id": run_id,
        "wrapper_manifest_digest": manifest.manifest_digest,
        "baseline_record_digest": record.record_digest,
        "graph_digest": record.graph_digest,
        "dependency_witness_digest": content_digest(record.dependency_observations),
        "all_tasks_executed_once": all_once,
        "dependencies_preserved": dependencies_preserved,
        "static_profile_selection_preserved": static_profiles_preserved,
        "validator_executed": validator_executed,
        "cache_disabled": record.cache_enabled is False,
        "checkpoint_receipt_verified": record.checkpoint_verified,
        "effects_proposal_only": effects_proposal_only,
        "model_calls_made": record.model_calls_made,
        "external_calls_made": record.external_calls_made,
        "external_effects_executed": record.external_effects_executed,
        "semantic_loss_ids": tuple(item.loss_id for item in manifest.semantic_losses),
        "claim_boundaries": _LANGGRAPH_CLAIM_BOUNDARIES,
    }
    witness = LangGraphConformanceWitness(
        **fields,
        witness_digest=content_digest(fields),
    )
    if not witness.verify_digest():  # pragma: no cover - construction invariant
        raise FrameworkConformanceError("LangGraph witness self-digest failed")
    return witness


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _page_url_problem(url: object, allowed_hosts: tuple[str, ...]) -> str | None:
    if not isinstance(url, str) or not url or url != url.strip() or "\\" in url:
        return "page URL is malformed"
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return "page URL cannot be parsed"
    if parsed.scheme.lower() != "https":
        return "page URL must use HTTPS"
    if parsed.hostname is None or parsed.hostname.lower() not in allowed_hosts:
        return "page URL host is not exactly allowlisted"
    if parsed.username is not None or parsed.password is not None:
        return "page URL cannot contain credentials"
    if port not in {None, 443}:
        return "page URL port is not allowed"
    return None


def validate_page_action_contract(
    contract: GovernedPageActionContract,
) -> tuple[str, ...]:
    """Validate a non-executing, taint-aware page-action contract."""

    if not isinstance(contract, GovernedPageActionContract):
        return ("value is not a GovernedPageActionContract",)
    problems: list[str] = []
    if contract.schema_version != PAGE_ACTION_SCHEMA_VERSION:
        problems.append("page-action schema version is unsupported")
    if contract.wrapper_kind != PAGE_ACTION_WRAPPER_KIND:
        problems.append("page-action wrapper kind is unsupported")
    if not contract.contract_id:
        problems.append("page-action contract ID is required")
    hosts_are_valid = (
        isinstance(contract.allowed_hosts, tuple)
        and bool(contract.allowed_hosts)
        and all(
            isinstance(host, str)
            and bool(host)
            and host == host.lower()
            and "://" not in host
            and "/" not in host
            for host in contract.allowed_hosts
        )
        and len(contract.allowed_hosts) == len(set(contract.allowed_hosts))
    )
    if not hosts_are_valid:
        problems.append("page-action allowed hosts must be unique canonical hostnames")
    allowed_hosts = contract.allowed_hosts if hosts_are_valid else ()

    if not isinstance(contract.observations, tuple) or any(
        not isinstance(item, BrowserObservation) for item in contract.observations
    ):
        problems.append("browser observations are malformed")
        observations: tuple[BrowserObservation, ...] = ()
    else:
        observations = contract.observations
    observation_ids = [item.observation_id for item in observations]
    canonical_observation_ids = all(
        isinstance(value, str) and bool(value) for value in observation_ids
    )
    if not canonical_observation_ids or len(observation_ids) != len(set(observation_ids)):
        problems.append("browser observation IDs must be nonempty and unique")
    for observation in observations:
        if observation.taint_label != TAINTED_BROWSER_OBSERVATION:
            problems.append(f"observation {observation.observation_id!r} is not explicitly tainted")
        if type(observation.captured_at_ms) is not int or observation.captured_at_ms < 0:
            problems.append(f"observation {observation.observation_id!r} timestamp is malformed")
        if not _valid_digest(observation.payload_digest):
            problems.append(
                f"observation {observation.observation_id!r} payload digest is malformed"
            )
        url_problem = _page_url_problem(observation.page_url, allowed_hosts)
        if url_problem:
            problems.append(f"observation {observation.observation_id!r}: {url_problem}")

    if not isinstance(contract.action_intents, tuple) or any(
        not isinstance(item, PageActionIntent) for item in contract.action_intents
    ):
        problems.append("page action intents are malformed")
        intents: tuple[PageActionIntent, ...] = ()
    else:
        intents = contract.action_intents
    intent_ids = [item.intent_id for item in intents]
    canonical_intent_ids = all(isinstance(value, str) and bool(value) for value in intent_ids)
    if not canonical_intent_ids or len(intent_ids) != len(set(intent_ids)):
        problems.append("page action intent IDs must be nonempty and unique")
    known_observations = set(observation_ids) if canonical_observation_ids else set()
    mutation_actions = {"click", "execute_script", "select", "submit", "type", "upload"}
    read_actions = {"inspect"}
    for intent in intents:
        prefix = f"intent {intent.intent_id!r}"
        if intent.action not in mutation_actions | read_actions:
            problems.append(f"{prefix} action is outside the governed vocabulary")
        if not intent.target:
            problems.append(f"{prefix} target is required")
        if type(intent.mutates_page_or_external_state) is not bool:
            problems.append(f"{prefix} mutation flag must be boolean")
        if type(intent.requires_approval) is not bool:
            problems.append(f"{prefix} approval flag must be boolean")
        if (
            intent.state != "proposed"
            or type(intent.executed_externally) is not bool
            or intent.executed_externally
        ):
            problems.append(f"{prefix} must remain an unexecuted proposal")
        if not intent.authority_grant_id:
            problems.append(f"{prefix} requires an independent authority grant reference")
        if (
            isinstance(intent.authority_grant_id, str)
            and intent.authority_grant_id in known_observations
        ):
            problems.append(f"{prefix} attempts to use a browser observation as authority")
        if (
            not isinstance(intent.observation_ids, tuple)
            or any(not isinstance(value, str) for value in intent.observation_ids)
            or any(value not in known_observations for value in intent.observation_ids)
        ):
            problems.append(f"{prefix} contains an unknown observation reference")
        if type(intent.effect_class) is not EffectClass:
            problems.append(f"{prefix} effect class is malformed")
            continue
        if intent.mutates_page_or_external_state is True:
            if intent.action not in mutation_actions:
                problems.append(f"{prefix} mutation flag conflicts with its action")
            if not intent.effect_class.writes:
                problems.append(f"{prefix} page write is not classified as a write effect intent")
            if not intent.idempotency_key:
                problems.append(f"{prefix} page write lacks an idempotency key")
            if (
                intent.effect_class is EffectClass.REVERSIBLE_WRITE
                and not intent.compensation_action
            ):
                problems.append(f"{prefix} reversible page write lacks compensation")
            if (
                intent.effect_class is EffectClass.IRREVERSIBLE_WRITE
                and not intent.requires_approval
            ):
                problems.append(f"{prefix} irreversible page write lacks approval")
        else:
            if intent.action not in read_actions or intent.effect_class is not EffectClass.READ:
                problems.append(f"{prefix} read declaration has inconsistent effect semantics")

    if contract.alibaba_pageagent_exercised is not False:
        problems.append("Alibaba PageAgent execution cannot be claimed by this local contract")
    if contract.beeai_exercised is not False:
        problems.append("BeeAI execution cannot be claimed by this local contract")
    if contract.integration_evidence_digests:
        problems.append("external integration evidence is outside this local contract schema")
    if contract.claim_boundaries != _PAGE_CLAIM_BOUNDARIES:
        problems.append("page-action claim boundaries were weakened or changed")
    if not contract.verify_digest():
        problems.append("page-action contract digest is invalid")
    return tuple(sorted(set(problems)))


def build_reference_page_action_contract() -> GovernedPageActionContract:
    """Build a safe local contract fixture without invoking any browser framework."""

    observation = BrowserObservation(
        observation_id="observation:page:v1",
        page_url="https://stormshift.invalid/console",
        captured_at_ms=100_000,
        payload_digest=content_digest({"visible_text": "fictional simulation"}),
    )
    intent = PageActionIntent(
        intent_id="intent:type-simulation:v1",
        action="type",
        target="form[name=simulation] textarea[name=message]",
        mutates_page_or_external_state=True,
        effect_class=EffectClass.REVERSIBLE_WRITE,
        authority_grant_id="grant:page-preview:v1",
        observation_ids=(observation.observation_id,),
        idempotency_key="page-preview/${run_id}/message",
        requires_approval=False,
        compensation_action="clear-simulation-message",
    )
    fields: dict[str, object] = {
        "schema_version": PAGE_ACTION_SCHEMA_VERSION,
        "wrapper_kind": PAGE_ACTION_WRAPPER_KIND,
        "contract_id": "page-action:stormshift-preview:v1",
        "allowed_hosts": ("stormshift.invalid",),
        "observations": (observation,),
        "action_intents": (intent,),
        "alibaba_pageagent_exercised": False,
        "beeai_exercised": False,
        "integration_evidence_digests": (),
        "claim_boundaries": _PAGE_CLAIM_BOUNDARIES,
    }
    contract = GovernedPageActionContract(
        **fields,
        contract_digest=content_digest(fields),
    )
    problems = validate_page_action_contract(contract)
    if problems:  # pragma: no cover - construction invariant
        raise FrameworkConformanceError("; ".join(problems))
    return contract


__all__ = [
    "BrowserObservation",
    "CachePolicy",
    "FeatureAccounting",
    "FiniteWrapperProjection",
    "FrameworkConformanceError",
    "FrameworkUnavailableError",
    "FrameworkWrapperManifest",
    "GovernedPageActionContract",
    "LANGGRAPH_TARGET",
    "LangGraphConformanceWitness",
    "NEUTRAL_TARGET",
    "NeutralTaskWrapper",
    "PAGE_ACTION_SCHEMA_VERSION",
    "PINNED_LANGGRAPH_CHECKPOINT_VERSION",
    "PINNED_LANGGRAPH_VERSION",
    "PageActionIntent",
    "PinnedFrameworkVersionError",
    "ProfileSelection",
    "SemanticLoss",
    "TAINTED_BROWSER_OBSERVATION",
    "WrapperEdge",
    "WrapperRuntimePolicy",
    "build_reference_page_action_contract",
    "finite_to_wrapper",
    "langgraph_conformance_available",
    "run_pinned_langgraph_conformance_witness",
    "validate_page_action_contract",
    "validate_wrapper_manifest",
    "wrapper_to_finite",
]
