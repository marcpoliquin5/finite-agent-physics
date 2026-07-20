"""Fail-closed verification for deterministic simulation traces."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isclose, prod

from .contracts import RunEnvelope
from .events import EventType
from .graph import ExecutionGraph
from .scheduler import ScheduleEntry, ScheduleResult
from .serialization import content_digest


@dataclass(frozen=True, slots=True)
class InvariantCheck:
    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class ConservationReport:
    passed: bool
    trace_digest: str
    checks: tuple[InvariantCheck, ...]

    @property
    def violations(self) -> tuple[InvariantCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


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


def _peak_concurrency(entries: list[ScheduleEntry], provider: str | None = None) -> int:
    points: list[tuple[int, int]] = []
    for entry in entries:
        if (provider is None or entry.provider == provider) and entry.start_ms < entry.end_ms:
            points.append((entry.start_ms, 1))
            points.append((entry.end_ms, -1))
    active = 0
    peak = 0
    for _, delta in sorted(points, key=lambda point: (point[0], point[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _model_bound(
    graph: ExecutionGraph,
    entries: list[ScheduleEntry],
    envelope: RunEnvelope,
) -> int:
    if not entries:
        return 0
    durations = {entry.task_id: entry.end_ms - entry.start_ms for entry in entries}
    providers = {entry.task_id: entry.provider for entry in entries}
    successors = graph.successors
    ranks: dict[str, int] = {}
    for task_id in reversed(graph.topological_order()):
        if task_id not in durations:
            continue
        downstream = max(
            (ranks[child] for child in successors[task_id] if child in durations),
            default=0,
        )
        ranks[task_id] = durations[task_id] + downstream

    work = sum(durations.values())
    global_bound = (work + envelope.max_parallelism - 1) // envelope.max_parallelism
    provider_work: dict[str, int] = {}
    for task_id, duration in durations.items():
        provider = providers[task_id]
        provider_work[provider] = provider_work.get(provider, 0) + duration
    provider_bound = max(
        (
            (duration + min(envelope.provider_limit(provider), envelope.max_parallelism) - 1)
            // min(envelope.provider_limit(provider), envelope.max_parallelism)
            for provider, duration in provider_work.items()
        ),
        default=0,
    )
    return max(max(ranks.values(), default=0), global_bound, provider_bound)


def verify_conservation(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    result: ScheduleResult,
) -> ConservationReport:
    """Reconstruct a trace without trusting its aggregates or lifecycle sets."""

    task_by_id = graph.by_id
    known_ids = set(task_by_id)
    protected_ids = _protected_task_ids(graph)
    optional_ids = known_ids - protected_ids
    checks: list[InvariantCheck] = []

    def add(name: str, passed: bool, evidence: str) -> None:
        checks.append(InvariantCheck(name, passed, evidence))

    entry_ids = [entry.task_id for entry in result.entries]
    entry_counts = Counter(entry_ids)
    unknown_entries = sorted(set(entry_ids) - known_ids)
    duplicate_entries = sorted(task_id for task_id, count in entry_counts.items() if count != 1)
    entry_identity_ok = not unknown_entries and not duplicate_entries
    add(
        "entry-identity",
        entry_identity_ok,
        f"unknown={unknown_entries}, duplicates={duplicate_entries}",
    )
    valid_entries = [entry for entry in result.entries if entry.task_id in known_ids]
    entry_by_task = {
        entry.task_id: entry for entry in valid_entries if entry_counts[entry.task_id] == 1
    }

    entry_shape_violations: list[str] = []
    profile_violations: list[str] = []
    deadline_violations: list[str] = []
    for entry in valid_entries:
        task = task_by_id[entry.task_id]
        if (
            entry.start_ms < 0
            or entry.end_ms < entry.start_ms
            or entry.end_ms > result.makespan_ms
            or entry.outcome not in {"completed", "cancelled"}
            or entry.optional != task.optional
        ):
            entry_shape_violations.append(entry.task_id)
        matches = [
            profile
            for profile in task.profiles
            if profile.name == entry.backend and profile.provider == entry.provider
        ]
        if len(matches) != 1:
            profile_violations.append(f"{entry.task_id}:backend")
            continue
        selected = matches[0]
        expected_duration = selected.duration_ms_p95
        duration_ok = (
            entry.end_ms - entry.start_ms == expected_duration
            if entry.outcome == "completed"
            else entry.end_ms - entry.start_ms <= expected_duration
        )
        if (
            selected.quality < task.min_quality
            or selected.total_tokens != entry.tokens
            or selected.cost_microusd != entry.cost_microusd
            or selected.context_bytes != entry.context_bytes
            or not isclose(
                1.0 - selected.failure_probability,
                entry.success_probability,
                abs_tol=1e-12,
            )
            or not duration_ok
        ):
            profile_violations.append(f"{entry.task_id}:profile")
        effective_deadline = min(task.deadline_ms or envelope.deadline_ms, envelope.deadline_ms)
        if entry.outcome == "completed" and entry.end_ms > effective_deadline:
            deadline_violations.append(entry.task_id)

    add(
        "entry-shape",
        not entry_shape_violations,
        f"invalid={sorted(entry_shape_violations)}",
    )
    add(
        "profile-integrity",
        not profile_violations,
        f"invalid={sorted(profile_violations)}",
    )
    add(
        "task-deadlines",
        not deadline_violations,
        f"late={sorted(deadline_violations)}",
    )

    sequences = [event.sequence for event in result.events]
    event_times = [event.time_ms for event in result.events]
    add(
        "event-order",
        sequences == list(range(1, len(sequences) + 1))
        and event_times == sorted(event_times)
        and all(time >= 0 for time in event_times),
        f"sequences={sequences}, times={event_times}",
    )
    typed_events = all(isinstance(event.event_type, EventType) for event in result.events)
    task_event_types = {
        EventType.PROFILE_SELECTED,
        EventType.TASK_STARTED,
        EventType.TASK_COMPLETED,
        EventType.TASK_CANCELLED,
        EventType.TASK_SKIPPED,
    }
    unknown_event_ids = sorted(
        {
            str(event.task_id)
            for event in result.events
            if event.event_type in task_event_types and event.task_id not in known_ids
        }
    )
    malformed_run_ids = [
        event.sequence
        for event in result.events
        if event.event_type
        in {EventType.RUN_STARTED, EventType.RUN_COMPLETED, EventType.RUN_FAILED}
        and event.task_id is not None
    ]
    add(
        "event-identity",
        typed_events and not unknown_event_ids and not malformed_run_ids,
        f"typed={typed_events}, unknown={unknown_event_ids}, malformed_run={malformed_run_ids}",
    )

    run_starts = [event for event in result.events if event.event_type is EventType.RUN_STARTED]
    run_completed = [event for event in result.events if event.event_type is EventType.RUN_COMPLETED]
    run_failed = [event for event in result.events if event.event_type is EventType.RUN_FAILED]
    expected_terminal = run_completed if result.success else run_failed
    unexpected_terminal = run_failed if result.success else run_completed
    terminal_ok = (
        len(run_starts) == 1
        and bool(result.events)
        and result.events[0] is run_starts[0]
        and run_starts[0].time_ms == 0
        and len(expected_terminal) == 1
        and not unexpected_terminal
        and result.events[-1] is expected_terminal[0]
        and expected_terminal[0].time_ms == result.makespan_ms
    )
    add(
        "run-lifecycle",
        terminal_ok,
        (
            f"starts={len(run_starts)}, completed={len(run_completed)}, "
            f"failed={len(run_failed)}, makespan={result.makespan_ms}"
        ),
    )

    event_counts: Counter[tuple[str, EventType]] = Counter()
    event_by_key: dict[tuple[str, EventType], object] = {}
    for event in result.events:
        if event.task_id in known_ids and event.event_type in task_event_types:
            key = (event.task_id, event.event_type)
            event_counts[key] += 1
            event_by_key[key] = event

    lifecycle_violations: list[str] = []
    selected_ids: set[str] = set()
    started_ids: set[str] = set()
    completed_ids: set[str] = set()
    cancelled_ids: set[str] = set()
    skipped_event_ids: set[str] = set()
    for task_id in known_ids:
        selected_count = event_counts[(task_id, EventType.PROFILE_SELECTED)]
        started_count = event_counts[(task_id, EventType.TASK_STARTED)]
        completed_count = event_counts[(task_id, EventType.TASK_COMPLETED)]
        cancelled_count = event_counts[(task_id, EventType.TASK_CANCELLED)]
        skipped_count = event_counts[(task_id, EventType.TASK_SKIPPED)]
        if selected_count:
            selected_ids.add(task_id)
        if started_count:
            started_ids.add(task_id)
        if completed_count:
            completed_ids.add(task_id)
        if cancelled_count:
            cancelled_ids.add(task_id)
        if skipped_count:
            skipped_event_ids.add(task_id)

        if task_id in entry_by_task:
            entry = entry_by_task[task_id]
            terminal_type = (
                EventType.TASK_COMPLETED
                if entry.outcome == "completed"
                else EventType.TASK_CANCELLED
            )
            selected_event = event_by_key.get((task_id, EventType.PROFILE_SELECTED))
            started_event = event_by_key.get((task_id, EventType.TASK_STARTED))
            terminal_event = event_by_key.get((task_id, terminal_type))
            aligned = (
                selected_count == 1
                and started_count == 1
                and completed_count + cancelled_count == 1
                and skipped_count == 0
                and selected_event is not None
                and started_event is not None
                and terminal_event is not None
                and selected_event.sequence < started_event.sequence < terminal_event.sequence
                and started_event.time_ms == entry.start_ms
                and terminal_event.time_ms == entry.end_ms
            )
            if not aligned:
                lifecycle_violations.append(task_id)
        elif selected_count or started_count or completed_count or cancelled_count:
            lifecycle_violations.append(task_id)
        elif skipped_count not in {0, 1}:
            lifecycle_violations.append(task_id)

    scheduled_ids = set(entry_by_task)
    lifecycle_ok = (
        not lifecycle_violations
        and scheduled_ids == selected_ids == started_ids == (completed_ids | cancelled_ids)
        and not (completed_ids & cancelled_ids)
    )
    add(
        "task-lifecycle",
        lifecycle_ok,
        (
            f"invalid={sorted(lifecycle_violations)}, scheduled={sorted(scheduled_ids)}, "
            f"started={sorted(started_ids)}, terminals={sorted(completed_ids | cancelled_ids)}"
        ),
    )

    skipped_result_ids = set(result.skipped)
    skips_ok = (
        len(result.skipped) == len(skipped_result_ids)
        and skipped_result_ids == skipped_event_ids
        and skipped_result_ids <= optional_ids
        and not (skipped_result_ids & scheduled_ids)
    )
    add(
        "skip-integrity",
        skips_ok,
        (
            f"result={sorted(skipped_result_ids)}, events={sorted(skipped_event_ids)}, "
            f"skippable={sorted(optional_ids)}"
        ),
    )

    dependency_violations: list[str] = []
    for task_id, entry in entry_by_task.items():
        for dependency in task_by_id[task_id].dependencies:
            parent = entry_by_task.get(dependency)
            if parent is None or parent.outcome != "completed" or parent.end_ms > entry.start_ms:
                dependency_violations.append(f"{task_id}<-{dependency}")
    add(
        "dependency-order",
        not dependency_violations,
        f"invalid={sorted(dependency_violations)}",
    )

    completed_successfully = {
        task_id for task_id, entry in entry_by_task.items() if entry.outcome == "completed"
    }
    success_shape_ok = (
        not result.success
        or (
            protected_ids <= completed_successfully
            and not cancelled_ids
            and completed_successfully | skipped_result_ids == known_ids
        )
    )
    add(
        "successful-run-completeness",
        success_shape_ok,
        (
            f"protected={sorted(protected_ids)}, completed={sorted(completed_successfully)}, "
            f"cancelled={sorted(cancelled_ids)}"
        ),
    )

    summed_tokens = sum(entry.tokens for entry in result.entries)
    summed_cost = sum(entry.cost_microusd for entry in result.entries)
    summed_context = sum(entry.context_bytes for entry in result.entries)
    summed_success = prod(entry.success_probability for entry in result.entries)
    add(
        "resource-accounting",
        summed_tokens == result.total_tokens
        and summed_cost == result.total_cost_microusd
        and summed_context == result.total_context_bytes,
        (
            f"entries=({summed_tokens},{summed_cost},{summed_context}), "
            f"aggregates=({result.total_tokens},{result.total_cost_microusd},"
            f"{result.total_context_bytes})"
        ),
    )
    add(
        "resource-caps",
        summed_tokens <= envelope.max_tokens
        and summed_cost <= envelope.max_cost_microusd
        and summed_context <= envelope.max_context_bytes,
        (
            f"used=({summed_tokens},{summed_cost},{summed_context}), "
            f"caps=({envelope.max_tokens},{envelope.max_cost_microusd},"
            f"{envelope.max_context_bytes})"
        ),
    )
    add(
        "modeled-reliability",
        isclose(summed_success, result.modeled_success_probability, abs_tol=1e-12)
        and (
            not result.success
            or summed_success >= envelope.min_modeled_success_probability
        ),
        (
            f"entries={summed_success}, aggregate={result.modeled_success_probability}, "
            f"floor={envelope.min_modeled_success_probability}"
        ),
    )

    global_peak = _peak_concurrency(valid_entries)
    providers = sorted({entry.provider for entry in valid_entries})
    provider_peaks = {
        provider: _peak_concurrency(valid_entries, provider) for provider in providers
    }
    add(
        "concurrency-caps",
        global_peak <= envelope.max_parallelism
        and all(
            peak <= min(envelope.provider_limit(provider), envelope.max_parallelism)
            for provider, peak in provider_peaks.items()
        ),
        (
            f"global={global_peak}/{envelope.max_parallelism}, "
            f"providers={provider_peaks}"
        ),
    )

    conflict_violations: list[str] = []
    effect_entries = [
        entry for entry in valid_entries if task_by_id[entry.task_id].effect.resource
    ]
    for index, left in enumerate(effect_entries):
        left_effect = task_by_id[left.task_id].effect
        for right in effect_entries[index + 1 :]:
            right_effect = task_by_id[right.task_id].effect
            overlaps = left.start_ms < right.end_ms and right.start_ms < left.end_ms
            incompatible = left_effect.kind.writes or right_effect.kind.writes
            if left_effect.resource == right_effect.resource and overlaps and incompatible:
                conflict_violations.append(f"{left.task_id}<->{right.task_id}")
    add(
        "effect-serialization",
        not conflict_violations,
        f"conflicts={sorted(conflict_violations)}",
    )

    recomputed_bound = _model_bound(graph, valid_entries, envelope) if entry_identity_ok else -1
    add(
        "model-bound",
        not result.success
        or (
            recomputed_bound == result.model_bound_ms
            and result.model_bound_ms <= result.makespan_ms
        ),
        (
            f"recomputed={recomputed_bound}, reported={result.model_bound_ms}, "
            f"makespan={result.makespan_ms}"
        ),
    )
    add(
        "run-deadline",
        not result.success or result.makespan_ms <= envelope.deadline_ms,
        f"makespan={result.makespan_ms}, deadline={envelope.deadline_ms}",
    )

    return ConservationReport(
        passed=all(check.passed for check in checks),
        trace_digest=content_digest(result.as_dict()),
        checks=tuple(checks),
    )
