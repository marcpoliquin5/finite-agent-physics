"""Truthful, deterministic benchmark records for the simulation kernel.

Nothing in this module executes a live model or provider fault. Records are explicitly
marked as simulated so they cannot be mistaken for IBM Granite measurements.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from statistics import median
from typing import Iterable

from .contracts import BackendProfile, RunEnvelope, TaskContract
from .graph import ExecutionGraph
from .scheduler import SchedulePolicy, Scheduler
from .serialization import content_digest


@dataclass(frozen=True, slots=True)
class RegisteredFault:
    fault_id: str
    description: str
    execution_status: str = "registered-not-executed"


REGISTERED_FAULTS = (
    RegisteredFault("provider-429", "Provider throttles a bounded request burst."),
    RegisteredFault("tail-latency", "One provider profile develops a slow tail."),
    RegisteredFault("tool-timeout", "A deterministic tool exceeds its timeout."),
    RegisteredFault("worker-crash", "A worker loses its active lease."),
    RegisteredFault("duplicate-effect", "An effect delivery receipt is replayed."),
    RegisteredFault("budget-cut", "The remaining additive budget decreases mid-run."),
)


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    schema_version: str
    revision: str
    config_digest: str
    graph_digest: str
    scenario: str
    seed: int
    policy: str
    measurement_kind: str
    success: bool
    makespan_ms: int
    model_bound_ms: int
    tokens: int
    cost_microusd: int
    context_bytes: int
    modeled_success_probability: float
    selected_backends: tuple[tuple[str, str], ...]
    skipped: tuple[str, ...]
    failure_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdmissionOracleResult:
    feasible: bool
    selected_profiles: tuple[tuple[str, str], ...]
    total_tokens: int
    total_cost_microusd: int
    total_context_bytes: int
    modeled_success_probability: float


def _protected_task_ids(graph: ExecutionGraph) -> set[str]:
    by_id = graph.by_id
    protected = {task.task_id for task in graph.tasks if not task.optional}
    stack = list(protected)
    while stack:
        for dependency in by_id[stack.pop()].dependencies:
            if dependency not in protected:
                protected.add(dependency)
                stack.append(dependency)
    return protected


def exact_additive_admission_oracle(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
) -> AdmissionOracleResult:
    """Exhaustively solve small protected-work profile assignments.

    This oracle covers additive tokens, cost, context, profile quality, individual duration
    ceilings, and the independent profile-reliability floor. It deliberately does not claim
    to solve precedence/resource-constrained makespan scheduling.
    """

    graph.validate()
    protected_ids = _protected_task_ids(graph)
    tasks = [task for task in graph.tasks if task.task_id in protected_ids]
    choices: list[tuple[BackendProfile, ...]] = []
    for task in tasks:
        effective_deadline = min(task.deadline_ms or envelope.deadline_ms, envelope.deadline_ms)
        qualified = tuple(
            profile
            for profile in task.profiles
            if profile.quality >= task.min_quality
            and profile.duration_ms_p95 <= effective_deadline
        )
        if not qualified:
            return AdmissionOracleResult(False, (), 0, 0, 0, 0.0)
        choices.append(qualified)

    feasible: list[tuple[tuple[int, int, int, float, tuple[str, ...]], tuple[BackendProfile, ...]]]
    feasible = []
    for selected in product(*choices):
        tokens = sum(profile.total_tokens for profile in selected)
        cost = sum(profile.cost_microusd for profile in selected)
        context = sum(profile.context_bytes for profile in selected)
        success_probability = 1.0
        for profile in selected:
            success_probability *= 1.0 - profile.failure_probability
        if (
            tokens <= envelope.max_tokens
            and cost <= envelope.max_cost_microusd
            and context <= envelope.max_context_bytes
            and success_probability >= envelope.min_modeled_success_probability
        ):
            key = (
                cost,
                tokens,
                context,
                -success_probability,
                tuple(profile.name for profile in selected),
            )
            feasible.append((key, selected))
    if not feasible:
        return AdmissionOracleResult(False, (), 0, 0, 0, 0.0)

    _, winner = min(feasible, key=lambda item: item[0])
    winner_success = 1.0
    for profile in winner:
        winner_success *= 1.0 - profile.failure_probability
    return AdmissionOracleResult(
        True,
        tuple((task.task_id, profile.name) for task, profile in zip(tasks, winner, strict=True)),
        sum(profile.total_tokens for profile in winner),
        sum(profile.cost_microusd for profile in winner),
        sum(profile.context_bytes for profile in winner),
        winner_success,
    )


def generated_scenario(shape: str, seed: int) -> ExecutionGraph:
    """Create a reproducible synthetic graph with identical candidates for every policy."""

    randomizer = random.Random(seed)

    def task(task_id: str, dependencies: tuple[str, ...] = ()) -> TaskContract:
        scale = randomizer.randint(1, 4)
        profiles = (
            BackendProfile(
                "sim-fast",
                "sim-provider-a",
                25 * scale,
                40 * scale,
                input_tokens=20 * scale,
                output_tokens=5 * scale,
                cost_microusd=30 * scale,
                context_bytes=80 * scale,
                quality=0.9,
                failure_probability=0.01,
            ),
            BackendProfile(
                "sim-economy",
                "sim-provider-b",
                40 * scale,
                70 * scale,
                input_tokens=12 * scale,
                output_tokens=4 * scale,
                cost_microusd=12 * scale,
                context_bytes=50 * scale,
                quality=0.9,
                failure_probability=0.02,
            ),
        )
        return TaskContract(task_id, profiles, dependencies, min_quality=0.9)

    if shape == "chain":
        tasks = [task("n0")]
        tasks.extend(task(f"n{index}", (f"n{index - 1}",)) for index in range(1, 6))
    elif shape == "fanout":
        tasks = [task("root")]
        tasks.extend(task(f"leaf{index}", ("root",)) for index in range(6))
    elif shape == "diamond":
        tasks = [task("root"), task("left", ("root",)), task("right", ("root",))]
        tasks.append(task("join", ("left", "right")))
    elif shape == "mixed":
        tasks = [task("root")]
        tasks.extend(task(f"branch{index}", ("root",)) for index in range(4))
        tasks.extend(
            (
                task("join", tuple(f"branch{index}" for index in range(4))),
                task("tail", ("join",)),
            )
        )
    else:
        raise ValueError(f"unknown generated scenario shape {shape!r}")
    return ExecutionGraph.from_tasks(tasks)


def benchmark_envelope() -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=10_000,
        max_tokens=20_000,
        max_cost_microusd=20_000,
        max_context_bytes=100_000,
        max_parallelism=4,
        min_modeled_success_probability=0.80,
        provider_limits=(("sim-provider-a", 2), ("sim-provider-b", 2)),
    )


def run_simulated_benchmark(
    *,
    scenario: str,
    seeds: Iterable[int],
    revision: str,
    policies: tuple[SchedulePolicy, ...] = (
        SchedulePolicy.ADAPTIVE,
        SchedulePolicy.STATIC_PARALLEL,
        SchedulePolicy.SEQUENTIAL,
    ),
) -> tuple[BenchmarkRecord, ...]:
    if not revision.strip():
        raise ValueError("benchmark revision cannot be empty")
    scheduler = Scheduler()
    envelope = benchmark_envelope()
    config_digest = content_digest(
        {
            "scenario": scenario,
            "envelope": envelope,
            "policies": tuple(policy.value for policy in policies),
            "measurement_kind": "deterministic-simulation",
        }
    )
    records: list[BenchmarkRecord] = []
    for seed in seeds:
        graph = generated_scenario(scenario, seed)
        graph_digest = content_digest(graph)
        for policy in policies:
            result = scheduler.schedule(graph, envelope, policy)
            records.append(
                BenchmarkRecord(
                    schema_version="finite-benchmark/v1",
                    revision=revision,
                    config_digest=config_digest,
                    graph_digest=graph_digest,
                    scenario=scenario,
                    seed=seed,
                    policy=policy.value,
                    measurement_kind="deterministic-simulation",
                    success=result.success,
                    makespan_ms=result.makespan_ms,
                    model_bound_ms=result.model_bound_ms,
                    tokens=result.total_tokens,
                    cost_microusd=result.total_cost_microusd,
                    context_bytes=result.total_context_bytes,
                    modeled_success_probability=result.modeled_success_probability,
                    selected_backends=tuple(
                        (entry.task_id, entry.backend) for entry in result.entries
                    ),
                    skipped=result.skipped,
                    failure_reason=result.failure_reason,
                )
            )
    return tuple(records)


def summarize_simulated_records(records: Iterable[BenchmarkRecord]) -> dict[str, object]:
    grouped: dict[str, list[BenchmarkRecord]] = {}
    for record in records:
        if record.measurement_kind != "deterministic-simulation":
            raise ValueError("summary accepts deterministic simulation records only")
        grouped.setdefault(record.policy, []).append(record)
    return {
        "measurement_kind": "deterministic-simulation",
        "claim_status": "descriptive-only",
        "policies": {
            policy: {
                "runs": len(items),
                "successes": sum(item.success for item in items),
                "median_makespan_ms": median(item.makespan_ms for item in items),
                "median_tokens": median(item.tokens for item in items),
            }
            for policy, items in sorted(grouped.items())
        },
    }


def write_jsonl(path: Path, records: Iterable[BenchmarkRecord]) -> None:
    """Persist raw records; callers choose an artifact path and revision identifier."""

    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":")))
            stream.write("\n")
