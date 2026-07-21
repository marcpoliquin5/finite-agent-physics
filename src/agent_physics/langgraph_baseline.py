"""An honest, static LangGraph comparator for the StormShift fixture.

This module uses LangGraph's real ``StateGraph`` and SQLite checkpointer APIs,
but deliberately does not attribute FINITE capabilities to them.  The graph is
hand-authored, profiles are selected once by a documented static rule, and no
admission, replanning, resource reservation, retry, model, cache, or external
effect mechanism is present.  The committed StormShift fixture workers and
structural validator are reused so semantically comparable outputs stay equal.

LangGraph is an optional dependency.  Importing this module is safe without the
extra; calling :func:`run_langgraph_stormshift_baseline` then raises a focused
installation error.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import metadata, util
from pathlib import Path
from typing import Annotated, Any, TypedDict, cast

from .contracts import BackendProfile, RunEnvelope, TaskContract
from .examples import miami_eoc_graph
from .executor import CancellationSignal, TaskExecutionContext
from .graph import ExecutionGraph
from .serialization import content_digest, normalize
from .stormshift import PlanValidationReport, StormShiftValidator, stormshift_fixture
from .stormshift_runtime import (
    PUBLISH_TASK_ID,
    PURE_TASK_IDS,
    StormShiftFixtureWorkers,
    StormShiftRuntimeInvariantError,
    _response_plan_from_output,
    stormshift_envelope,
)


BASELINE_SCHEMA_VERSION = "langgraph-stormshift-static-baseline/v1"
COMPARATOR_KIND = "static_external_baseline"
FRAMEWORK_NAME = "langgraph"
LANGGRAPH_EXTRA = "agent-physics[langgraph]"
MAX_CONCURRENCY = 4


class LangGraphBaselineUnavailable(RuntimeError):
    """The optional pinned LangGraph comparator dependencies are unavailable."""


class LangGraphBaselineInvariantError(RuntimeError):
    """The static comparator violated its declared, locally checkable contract."""


def _merge_outputs(
    left: dict[str, object] | None,
    right: dict[str, object] | None,
) -> dict[str, object]:
    """Merge disjoint parallel node updates and reject duplicate execution."""

    left = left or {}
    right = right or {}
    overlap = set(left) & set(right)
    if overlap:
        raise LangGraphBaselineInvariantError(
            f"LangGraph emitted duplicate task outputs: {sorted(overlap)}"
        )
    return {**left, **right}


class _BaselineState(TypedDict):
    outputs: Annotated[dict[str, object], _merge_outputs]


@dataclass(frozen=True, slots=True)
class DependencyObservation:
    """The exact predecessor outputs presented to one node invocation."""

    task_id: str
    dependency_ids: tuple[str, ...]
    dependency_output_digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class StaticProfileSelection:
    """One fixed profile choice; it is metadata, not a live-provider claim."""

    task_id: str
    profile_name: str
    provider: str
    quality: float
    duration_ms_p95: int
    input_tokens: int
    output_tokens: int
    cost_microusd: int
    context_bytes: int
    failure_probability: float
    selection_rule: str = "highest_quality_qualified_stable_tiebreak"


@dataclass(frozen=True, slots=True)
class LangGraphBaselineRecord:
    """Deterministic evidence record for one actual LangGraph fixture run."""

    schema_version: str
    comparator_kind: str
    framework: str
    framework_version: str
    checkpoint_package: str
    checkpoint_package_version: str
    run_id: str
    graph_digest: str
    output_digest: str
    comparable_output_digest: str
    validation_digest: str
    effect_proposal_digest: str
    record_digest: str
    outputs: dict[str, object]
    validation: dict[str, object]
    task_call_counts: tuple[tuple[str, int], ...]
    dependency_observations: tuple[DependencyObservation, ...]
    static_profiles: tuple[StaticProfileSelection, ...]
    profile_snapshot_digest: str
    configured_max_concurrency: int
    configured_provider_limits: tuple[tuple[str, int], ...]
    observed_max_worker_concurrency: int
    observed_provider_maxima: tuple[tuple[str, int], ...]
    checkpoint_verified: bool
    cache_enabled: bool
    admission_performed: bool
    retries_configured: bool
    model_calls_made: bool
    external_calls_made: bool
    external_effects_executed: int
    effect_state: str

    def unsigned_payload(self) -> dict[str, object]:
        """Return the canonical record payload without its self-digest."""

        payload = cast(dict[str, object], normalize(self))
        payload.pop("record_digest")
        return payload

    def verify_digest(self) -> bool:
        """Recompute the self-digest over every other normalized field."""

        return self.record_digest == content_digest(self.unsigned_payload())

    def as_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible representation."""

        return cast(dict[str, object], normalize(self))

def langgraph_baseline_available() -> bool:
    """Return whether both optional packages appear importable."""

    try:
        return (
            util.find_spec("langgraph.graph") is not None
            and util.find_spec("langgraph.checkpoint.sqlite.aio") is not None
        )
    except (ImportError, ModuleNotFoundError):
        return False


def _load_langgraph() -> tuple[Any, str, str, Any, Any, Any]:
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph import END, START, StateGraph
    except (ImportError, ModuleNotFoundError) as exc:
        raise LangGraphBaselineUnavailable(
            f"install the pinned optional comparator with `pip install {LANGGRAPH_EXTRA}` "
            "or `pip install -e .[langgraph]`"
        ) from exc
    return (
        StateGraph,
        START,
        END,
        AsyncSqliteSaver,
        metadata.version("langgraph"),
        metadata.version("langgraph-checkpoint-sqlite"),
    )

def _highest_quality_qualified_profile(task: TaskContract) -> BackendProfile:
    """Apply the comparator's one static, framework-independent profile rule."""

    qualified = [profile for profile in task.profiles if profile.quality >= task.min_quality]
    if not qualified:
        raise LangGraphBaselineInvariantError(
            f"task {task.task_id!r} has no profile meeting its quality floor"
        )
    return min(
        qualified,
        key=lambda profile: (
            -profile.quality,
            profile.duration_ms_p95,
            profile.cost_microusd,
            profile.total_tokens,
            profile.context_bytes,
            profile.failure_probability,
            profile.provider,
            profile.name,
        ),
    )


def _require_output_map(state: _BaselineState) -> dict[str, object]:
    outputs = state.get("outputs")
    if not isinstance(outputs, dict):
        raise LangGraphBaselineInvariantError("LangGraph state lacks its output map")
    return outputs


class _StaticStormShiftComparator:
    """Hand-authored LangGraph nodes plus bounded-call instrumentation."""

    def __init__(self, graph: ExecutionGraph, envelope: RunEnvelope, run_id: str) -> None:
        self.graph = graph
        self.envelope = envelope
        self.run_id = run_id
        self.scenario = stormshift_fixture()
        self.fixture_workers = StormShiftFixtureWorkers(self.scenario)
        self.profiles = {
            task.task_id: _highest_quality_qualified_profile(task)
            for task in graph.tasks
            if not task.effect.kind.writes
        }
        self.call_counts = {task.task_id: 0 for task in graph.tasks}
        self.dependency_observations: dict[str, DependencyObservation] = {}
        self._global_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        self._provider_semaphores = {
            provider: asyncio.Semaphore(limit)
            for provider, limit in envelope.provider_limits
        }
        self._active_workers = 0
        self._active_by_provider: dict[str, int] = {}
        self.max_active_workers = 0
        self.max_active_by_provider: dict[str, int] = {}

    def _dependencies(
        self,
        task: TaskContract,
        state: _BaselineState,
    ) -> dict[str, object]:
        outputs = _require_output_map(state)
        missing = sorted(set(task.dependencies) - set(outputs))
        if missing:
            raise LangGraphBaselineInvariantError(
                f"task {task.task_id!r} started before dependencies {missing}"
            )
        dependencies = {task_id: outputs[task_id] for task_id in task.dependencies}
        observation = DependencyObservation(
            task_id=task.task_id,
            dependency_ids=tuple(sorted(dependencies)),
            dependency_output_digests=tuple(
                (task_id, content_digest(dependencies[task_id]))
                for task_id in sorted(dependencies)
            ),
        )
        if task.task_id in self.dependency_observations:
            raise LangGraphBaselineInvariantError(
                f"task {task.task_id!r} was presented dependencies more than once"
            )
        self.dependency_observations[task.task_id] = observation
        return dependencies

    def _mark_call(self, task_id: str) -> None:
        if self.call_counts[task_id] != 0:
            raise LangGraphBaselineInvariantError(f"task {task_id!r} executed more than once")
        self.call_counts[task_id] = 1

    async def _execute_worker(
        self,
        task: TaskContract,
        dependencies: dict[str, object],
    ) -> object:
        profile = self.profiles[task.task_id]
        provider_semaphore = self._provider_semaphores.get(profile.provider)
        if provider_semaphore is None:
            provider_semaphore = asyncio.Semaphore(
                self.envelope.provider_limit(profile.provider)
            )
            self._provider_semaphores[profile.provider] = provider_semaphore

        async with self._global_semaphore, provider_semaphore:
            self._active_workers += 1
            self._active_by_provider[profile.provider] = (
                self._active_by_provider.get(profile.provider, 0) + 1
            )
            self.max_active_workers = max(self.max_active_workers, self._active_workers)
            self.max_active_by_provider[profile.provider] = max(
                self.max_active_by_provider.get(profile.provider, 0),
                self._active_by_provider[profile.provider],
            )
            try:
                # The yield makes real parallel fan-out observable without adding
                # timing measurements to the evidence record.
                await asyncio.sleep(0)
                result = await self.fixture_workers.execute_task(
                    TaskExecutionContext(
                        run_id=self.run_id,
                        task=task,
                        profile=profile,
                        attempt=1,
                        dependency_outputs=dependencies,
                        deadline_at_ms=self.envelope.deadline_ms,
                        cancellation_event=CancellationSignal(),
                    )
                )
                if not await self.fixture_workers.validate_output(task, result.output):
                    raise LangGraphBaselineInvariantError(
                        f"task {task.task_id!r} failed the committed fixture validator"
                    )
                # Fail immediately if a worker produced non-canonical evidence.
                content_digest(result.output)
                return result.output
            finally:
                self._active_workers -= 1
                self._active_by_provider[profile.provider] -= 1

    def worker_node(self, task: TaskContract) -> Any:
        async def node(state: _BaselineState) -> dict[str, dict[str, object]]:
            dependencies = self._dependencies(task, state)
            self._mark_call(task.task_id)
            output = await self._execute_worker(task, dependencies)
            return {"outputs": {task.task_id: output}}

        node.__name__ = f"stormshift_{task.task_id}"
        return node

    def effect_proposal_node(self, task: TaskContract) -> Any:
        async def node(state: _BaselineState) -> dict[str, dict[str, object]]:
            dependencies = self._dependencies(task, state)
            self._mark_call(task.task_id)
            safety = cast(dict[str, object], dependencies["safety_review"])
            alert = cast(dict[str, object], dependencies["multilingual_alert"])
            if safety.get("passed") is not True:
                raise LangGraphBaselineInvariantError(
                    "effect proposal was reached without a passing structural review"
                )
            if alert.get("external_publication_attempted") is not False:
                raise LangGraphBaselineInvariantError(
                    "effect proposal dependency attempted external publication"
                )
            proposal_body = {
                "schema_version": BASELINE_SCHEMA_VERSION,
                "run_id": self.run_id,
                "action": task.task_id,
                "resource": task.effect.resource,
                "effect_class": task.effect.kind.value,
                "idempotency_key": task.effect.idempotency_key,
                "requires_approval": task.effect.requires_approval,
                "dependency_output_digests": {
                    task_id: content_digest(value)
                    for task_id, value in sorted(dependencies.items())
                },
                "fixture_only": True,
            }
            proposal_digest = content_digest(proposal_body)
            output: dict[str, object] = {
                "effect_intent_id": f"langgraph-static:{proposal_digest}",
                "effect_state": "proposed",
                "executed_externally": False,
                "approval_grant_present": False,
                "proposal_digest": proposal_digest,
            }
            return {"outputs": {task.task_id: output}}

        node.__name__ = f"stormshift_{task.task_id}"
        return node


def _build_state_graph(
    StateGraph: Any,
    START: str,
    END: str,
    comparator: _StaticStormShiftComparator,
) -> Any:
    builder = StateGraph(_BaselineState)
    successors = comparator.graph.successors
    for task in comparator.graph.tasks:
        node = (
            comparator.effect_proposal_node(task)
            if task.effect.kind.writes
            else comparator.worker_node(task)
        )
        builder.add_node(task.task_id, node)
    for task in comparator.graph.tasks:
        if not task.dependencies:
            builder.add_edge(START, task.task_id)
        elif len(task.dependencies) == 1:
            builder.add_edge(task.dependencies[0], task.task_id)
        else:
            # A list edge is LangGraph's exact all-predecessor join API.
            builder.add_edge(list(task.dependencies), task.task_id)
        if not successors[task.task_id]:
            builder.add_edge(task.task_id, END)
    return builder


def _normalized_validation(report: PlanValidationReport) -> dict[str, object]:
    if not report.passed or not report.verify_digest():
        raise LangGraphBaselineInvariantError(
            "final output failed StormShift's deterministic structural validator"
        )
    return cast(dict[str, object], normalize(report))


def _make_record(
    *,
    comparator: _StaticStormShiftComparator,
    outputs: dict[str, object],
    validation: PlanValidationReport,
    framework_version: str,
    checkpoint_version: str,
    checkpoint_verified: bool,
) -> LangGraphBaselineRecord:
    graph_digest = content_digest(comparator.graph)
    comparable_outputs = {
        task_id: outputs[task_id]
        for task_id in sorted(PURE_TASK_IDS)
    }
    validation_payload = _normalized_validation(validation)
    proposal = cast(dict[str, object], outputs[PUBLISH_TASK_ID])
    proposal_digest = proposal.get("proposal_digest")
    if not isinstance(proposal_digest, str):
        raise LangGraphBaselineInvariantError("effect proposal lacks its content digest")

    static_profiles = tuple(
        StaticProfileSelection(
            task_id=task_id,
            profile_name=profile.name,
            provider=profile.provider,
            quality=profile.quality,
            duration_ms_p95=profile.duration_ms_p95,
            input_tokens=profile.input_tokens,
            output_tokens=profile.output_tokens,
            cost_microusd=profile.cost_microusd,
            context_bytes=profile.context_bytes,
            failure_probability=profile.failure_probability,
        )
        for task_id, profile in sorted(comparator.profiles.items())
    )
    fields: dict[str, object] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "comparator_kind": COMPARATOR_KIND,
        "framework": FRAMEWORK_NAME,
        "framework_version": framework_version,
        "checkpoint_package": "langgraph-checkpoint-sqlite",
        "checkpoint_package_version": checkpoint_version,
        "run_id": comparator.run_id,
        "graph_digest": graph_digest,
        "output_digest": content_digest(outputs),
        "comparable_output_digest": content_digest(comparable_outputs),
        "validation_digest": validation.report_digest,
        "effect_proposal_digest": proposal_digest,
        "outputs": outputs,
        "validation": validation_payload,
        "task_call_counts": tuple(sorted(comparator.call_counts.items())),
        "dependency_observations": tuple(
            comparator.dependency_observations[task_id]
            for task_id in sorted(comparator.dependency_observations)
        ),
        "static_profiles": static_profiles,
        "profile_snapshot_digest": content_digest(static_profiles),
        "configured_max_concurrency": MAX_CONCURRENCY,
        "configured_provider_limits": tuple(sorted(comparator.envelope.provider_limits)),
        "observed_max_worker_concurrency": comparator.max_active_workers,
        "observed_provider_maxima": tuple(sorted(comparator.max_active_by_provider.items())),
        "checkpoint_verified": checkpoint_verified,
        "cache_enabled": False,
        "admission_performed": False,
        "retries_configured": False,
        "model_calls_made": False,
        "external_calls_made": False,
        "external_effects_executed": 0,
        "effect_state": "proposed",
    }
    fields["record_digest"] = content_digest(fields)
    return LangGraphBaselineRecord(**fields)  # type: ignore[arg-type]


async def run_langgraph_stormshift_baseline(
    *,
    run_id: str = "stormshift-langgraph-static-v1",
    checkpoint_path: str | Path = ":memory:",
) -> LangGraphBaselineRecord:
    """Execute one actual LangGraph run and return normalized local evidence.

    The comparator is intentionally static.  It does not call FINITE's scheduler
    or executor and performs no feasibility/admission decision.  ``run_id`` must
    be unique within a persistent checkpoint database; this function rejects
    duplicate node execution rather than presenting a second invocation as a
    restart benchmark.
    """

    if not run_id:
        raise ValueError("run_id is required")
    StateGraph, START, END, AsyncSqliteSaver, framework_version, checkpoint_version = (
        _load_langgraph()
    )
    graph = miami_eoc_graph()
    envelope = stormshift_envelope()
    if envelope.max_parallelism != MAX_CONCURRENCY:
        raise LangGraphBaselineInvariantError(
            f"committed envelope max_parallelism changed from {MAX_CONCURRENCY}"
        )
    comparator = _StaticStormShiftComparator(graph, envelope, run_id)
    builder = _build_state_graph(StateGraph, START, END, comparator)
    checkpoint_location = str(checkpoint_path)

    async with AsyncSqliteSaver.from_conn_string(checkpoint_location) as saver:
        await saver.setup()
        compiled = builder.compile(
            checkpointer=saver,
            cache=None,
            name="stormshift-static-langgraph-baseline",
        )
        config = {
            "configurable": {"thread_id": run_id},
            "max_concurrency": MAX_CONCURRENCY,
        }
        state = await compiled.ainvoke(
            {"outputs": {}},
            config=config,
            durability="sync",
        )
        snapshot = await compiled.aget_state(config)

    outputs = cast(dict[str, object], state.get("outputs", {}))
    expected_ids = set(graph.by_id)
    if set(outputs) != expected_ids:
        raise LangGraphBaselineInvariantError(
            f"completed output IDs differ: {sorted(set(outputs) ^ expected_ids)}"
        )
    if comparator.call_counts != {task_id: 1 for task_id in expected_ids}:
        raise LangGraphBaselineInvariantError("every committed task must execute exactly once")
    checkpoint_outputs = cast(dict[str, object], snapshot.values.get("outputs", {}))
    checkpoint_verified = checkpoint_outputs == outputs
    if not checkpoint_verified:
        raise LangGraphBaselineInvariantError("SQLite checkpoint differs from final outputs")

    response_plan = _response_plan_from_output(outputs["response_plan"])
    validation = StormShiftValidator().validate(comparator.scenario, response_plan)
    safety_output = cast(dict[str, object], outputs["safety_review"])
    if safety_output.get("report_digest") != validation.report_digest:
        raise StormShiftRuntimeInvariantError(
            "LangGraph safety output is not bound to final structural validation"
        )
    effect_output = cast(dict[str, object], outputs[PUBLISH_TASK_ID])
    if (
        effect_output.get("effect_state") != "proposed"
        or effect_output.get("executed_externally") is not False
        or effect_output.get("approval_grant_present") is not False
    ):
        raise LangGraphBaselineInvariantError(
            "the comparator crossed its proposal-only effect boundary"
        )

    record = _make_record(
        comparator=comparator,
        outputs=outputs,
        validation=validation,
        framework_version=framework_version,
        checkpoint_version=checkpoint_version,
        checkpoint_verified=checkpoint_verified,
    )
    if not record.verify_digest():
        raise LangGraphBaselineInvariantError("normalized baseline record digest failed")
    return record


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "COMPARATOR_KIND",
    "DependencyObservation",
    "LangGraphBaselineInvariantError",
    "LangGraphBaselineRecord",
    "LangGraphBaselineUnavailable",
    "StaticProfileSelection",
    "langgraph_baseline_available",
    "run_langgraph_stormshift_baseline",
]
