"""Discrete-event scheduling under finite execution envelopes."""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass, replace
from enum import Enum
from itertools import count
from typing import Iterable

from .contracts import BackendProfile, EffectClass, RunEnvelope, TaskContract
from .events import Event, EventType
from .graph import ExecutionGraph, GraphValidationError


class SchedulePolicy(str, Enum):
    ADAPTIVE = "adaptive"
    STATIC_PARALLEL = "static_parallel"
    SEQUENTIAL = "sequential"


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    task_id: str
    backend: str
    provider: str
    start_ms: int
    end_ms: int
    tokens: int
    cost_microusd: int
    context_bytes: int
    success_probability: float
    optional: bool
    outcome: str


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    policy: SchedulePolicy
    success: bool
    makespan_ms: int
    model_bound_ms: int
    total_tokens: int
    total_cost_microusd: int
    total_context_bytes: int
    modeled_success_probability: float
    entries: tuple[ScheduleEntry, ...]
    skipped: tuple[str, ...]
    events: tuple[Event, ...]
    failure_reason: str | None = None

    @property
    def model_bound_gap(self) -> float | None:
        if not self.success:
            return None
        if self.model_bound_ms == 0:
            return 1.0
        return self.makespan_ms / self.model_bound_ms

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.value,
            "success": self.success,
            "makespan_ms": self.makespan_ms,
            "model_bound_ms": self.model_bound_ms,
            "model_bound_gap": (
                round(self.model_bound_gap, 4) if self.model_bound_gap is not None else None
            ),
            "total_tokens": self.total_tokens,
            "total_cost_microusd": self.total_cost_microusd,
            "total_context_bytes": self.total_context_bytes,
            "modeled_success_probability": round(self.modeled_success_probability, 12),
            "skipped": list(self.skipped),
            "failure_reason": self.failure_reason,
            "entries": [asdict(entry) for entry in self.entries],
            "events": [event.as_dict() for event in self.events],
        }


class Scheduler:
    """Plan deterministic execution while preserving resource and effect invariants."""

    def schedule(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        policy: SchedulePolicy = SchedulePolicy.ADAPTIVE,
    ) -> ScheduleResult:
        graph.validate()
        envelope_errors = envelope.validate()
        if envelope_errors:
            raise GraphValidationError("; ".join(envelope_errors))

        by_id = graph.by_id
        protected_ids = self._protected_task_ids(graph)
        reference_profiles = {
            task.task_id: self._fastest_qualified(task) for task in graph.tasks
        }
        ranks = graph.upward_ranks(reference_profiles)
        protected_ranks = self._upward_ranks_for_tasks(
            graph,
            reference_profiles,
            protected_ids,
        )
        model_bound = self._profile_lower_bound(
            graph,
            reference_profiles,
            envelope,
            protected_ids,
        )

        time_ms = 0
        sequence = 0
        events: list[Event] = []
        entries: list[ScheduleEntry] = []
        skipped: list[str] = []
        completed: set[str] = set()
        started: set[str] = set()
        running: list[tuple[int, int, str, BackendProfile]] = []
        provider_running: dict[str, int] = {}
        active_effects: dict[str, list[EffectClass]] = {}
        used_tokens = 0
        used_cost = 0
        used_context = 0
        modeled_success_probability = 1.0
        tie_breaker = count()

        def emit(
            event_type: EventType,
            task_id: str | None = None,
            details: Iterable[tuple[str, object]] = (),
        ) -> None:
            nonlocal sequence
            sequence += 1
            events.append(Event(sequence, time_ms, event_type, task_id, tuple(details)))

        def set_outcome(task_id: str, outcome: str, end_ms: int) -> None:
            for index, entry in enumerate(entries):
                if entry.task_id == task_id:
                    entries[index] = replace(entry, outcome=outcome, end_ms=end_ms)
                    return
            raise RuntimeError(f"missing schedule entry for started task {task_id!r}")

        def fail(reason: str) -> ScheduleResult:
            nonlocal running
            for _, _, task_id, _ in sorted(running):
                set_outcome(task_id, "cancelled", time_ms)
                emit(EventType.TASK_CANCELLED, task_id, (("reason", "run refused"),))
            running = []
            return self._failure(
                policy,
                time_ms,
                model_bound,
                used_tokens,
                used_cost,
                used_context,
                modeled_success_probability,
                entries,
                skipped,
                events,
                reason,
                emit,
            )

        emit(EventType.RUN_STARTED, details=(("policy", policy.value),))

        while len(completed) + len(skipped) < len(by_id):
            blocked_by_skipped = [
                task
                for task in graph.tasks
                if task.task_id not in started
                and task.task_id not in skipped
                and any(dependency in skipped for dependency in task.dependencies)
            ]
            if blocked_by_skipped:
                for task in sorted(blocked_by_skipped, key=lambda item: item.task_id):
                    missing = sorted(set(task.dependencies) & set(skipped))
                    if not task.optional:
                        return fail(
                            f"required task {task.task_id!r} depends on skipped work {missing}"
                        )
                    skipped.append(task.task_id)
                    emit(
                        EventType.TASK_SKIPPED,
                        task.task_id,
                        (("reason", "dependency skipped"), ("dependencies", missing)),
                    )
                continue

            ready = [
                task
                for task in graph.tasks
                if task.task_id not in started
                and task.task_id not in skipped
                and all(dep in completed for dep in task.dependencies)
            ]
            ready = self._sort_ready(
                ready,
                ranks,
                protected_ranks,
                reference_profiles,
                protected_ids,
                envelope,
                time_ms,
                policy,
            )
            dispatched = False

            for task in ready:
                capacity = 1 if policy is SchedulePolicy.SEQUENTIAL else envelope.max_parallelism
                if len(running) >= capacity:
                    break
                profile = self._choose_profile(
                    task=task,
                    time_ms=time_ms,
                    downstream_ms=max(
                        0,
                        (protected_ranks if task.task_id in protected_ids else ranks)[
                            task.task_id
                        ]
                        - reference_profiles[task.task_id].duration_ms_p95,
                    ),
                    envelope=envelope,
                    used_tokens=used_tokens,
                    used_cost=used_cost,
                    used_context=used_context,
                    used_success_probability=modeled_success_probability,
                    remaining_protected=tuple(
                        candidate
                        for candidate in graph.tasks
                        if candidate.task_id in protected_ids
                        and candidate.task_id not in started
                        and candidate.task_id != task.task_id
                    ),
                )
                if profile is None:
                    if task.task_id not in protected_ids:
                        skipped.append(task.task_id)
                        emit(
                            EventType.TASK_SKIPPED,
                            task.task_id,
                            (("reason", "protected resource or deadline envelope"),),
                        )
                        dispatched = True
                        continue
                    return fail(
                        f"protected task {task.task_id!r} has no admissible backend plan"
                    )
                if provider_running.get(profile.provider, 0) >= envelope.provider_limit(profile.provider):
                    continue
                if self._effect_conflicts(task, active_effects):
                    continue

                started.add(task.task_id)
                used_tokens += profile.total_tokens
                used_cost += profile.cost_microusd
                used_context += profile.context_bytes
                modeled_success_probability *= 1.0 - profile.failure_probability
                provider_running[profile.provider] = provider_running.get(profile.provider, 0) + 1
                if task.effect.resource:
                    active_effects.setdefault(task.effect.resource, []).append(task.effect.kind)
                end_ms = time_ms + profile.duration_ms_p95
                heapq.heappush(
                    running,
                    (end_ms, next(tie_breaker), task.task_id, profile),
                )
                entries.append(
                    ScheduleEntry(
                        task_id=task.task_id,
                        backend=profile.name,
                        provider=profile.provider,
                        start_ms=time_ms,
                        end_ms=end_ms,
                        tokens=profile.total_tokens,
                        cost_microusd=profile.cost_microusd,
                        context_bytes=profile.context_bytes,
                        success_probability=1.0 - profile.failure_probability,
                        optional=task.optional,
                        outcome="running",
                    )
                )
                emit(
                    EventType.PROFILE_SELECTED,
                    task.task_id,
                    (("backend", profile.name), ("provider", profile.provider)),
                )
                emit(EventType.TASK_STARTED, task.task_id, (("end_ms", end_ms),))
                dispatched = True

            if running:
                next_end = running[0][0]
                time_ms = next_end
                while running and running[0][0] == next_end:
                    _, _, task_id, profile = heapq.heappop(running)
                    task = by_id[task_id]
                    completed.add(task_id)
                    set_outcome(task_id, "completed", time_ms)
                    provider_running[profile.provider] -= 1
                    if task.effect.resource:
                        active_effects[task.effect.resource].remove(task.effect.kind)
                        if not active_effects[task.effect.resource]:
                            del active_effects[task.effect.resource]
                    emit(EventType.TASK_COMPLETED, task_id, (("backend", profile.name),))
            elif not dispatched:
                return fail("scheduler deadlock: ready tasks could not be dispatched")

            if time_ms > envelope.deadline_ms:
                return fail("run deadline exceeded")

        final_entries = tuple(sorted(entries, key=lambda item: (item.start_ms, item.task_id)))
        model_bound = self._entry_lower_bound(graph, final_entries, envelope)
        if model_bound > time_ms:
            raise RuntimeError("planning-model bound exceeded simulated makespan")
        emit(EventType.RUN_COMPLETED, details=(("makespan_ms", time_ms),))
        return ScheduleResult(
            policy=policy,
            success=True,
            makespan_ms=time_ms,
            model_bound_ms=model_bound,
            total_tokens=used_tokens,
            total_cost_microusd=used_cost,
            total_context_bytes=used_context,
            modeled_success_probability=modeled_success_probability,
            entries=final_entries,
            skipped=tuple(sorted(skipped)),
            events=tuple(events),
        )

    @staticmethod
    def _fastest_qualified(task: TaskContract) -> BackendProfile:
        qualified = [profile for profile in task.profiles if profile.quality >= task.min_quality]
        return min(qualified, key=lambda profile: (profile.duration_ms_p95, profile.cost_microusd))

    @staticmethod
    def _sort_ready(
        ready: list[TaskContract],
        ranks: dict[str, int],
        protected_ranks: dict[str, int],
        reference_profiles: dict[str, BackendProfile],
        protected_ids: set[str],
        envelope: RunEnvelope,
        time_ms: int,
        policy: SchedulePolicy,
    ) -> list[TaskContract]:
        if policy is SchedulePolicy.ADAPTIVE:
            def urgency(task: TaskContract) -> int:
                own_deadline = min(task.deadline_ms or envelope.deadline_ms, envelope.deadline_ms)
                task_slack = (
                    own_deadline
                    - time_ms
                    - reference_profiles[task.task_id].duration_ms_p95
                )
                remaining_rank = (protected_ranks if task.task_id in protected_ids else ranks)[
                    task.task_id
                ]
                run_slack = envelope.deadline_ms - time_ms - remaining_rank
                return min(task_slack, run_slack)

            return sorted(
                ready,
                key=lambda task: (
                    task.task_id not in protected_ids,
                    urgency(task),
                    -(protected_ranks if task.task_id in protected_ids else ranks)[task.task_id],
                    -task.value,
                    task.task_id,
                ),
            )
        return sorted(ready, key=lambda task: task.task_id)

    def _choose_profile(
        self,
        *,
        task: TaskContract,
        time_ms: int,
        downstream_ms: int,
        envelope: RunEnvelope,
        used_tokens: int,
        used_cost: int,
        used_context: int,
        used_success_probability: float,
        remaining_protected: tuple[TaskContract, ...],
    ) -> BackendProfile | None:
        qualified = [profile for profile in task.profiles if profile.quality >= task.min_quality]
        task_deadline = min(task.deadline_ms or envelope.deadline_ms, envelope.deadline_ms)
        feasible = [
            profile
            for profile in qualified
            if used_tokens + profile.total_tokens <= envelope.max_tokens
            and used_cost + profile.cost_microusd <= envelope.max_cost_microusd
            and used_context + profile.context_bytes <= envelope.max_context_bytes
            and time_ms + profile.duration_ms_p95 <= task_deadline
            and self._future_plan_fits(
                remaining_protected,
                envelope.max_tokens - used_tokens - profile.total_tokens,
                envelope.max_cost_microusd - used_cost - profile.cost_microusd,
                envelope.max_context_bytes - used_context - profile.context_bytes,
                time_ms,
                envelope.deadline_ms,
                (
                    envelope.min_modeled_success_probability
                    / (used_success_probability * (1.0 - profile.failure_probability))
                    if used_success_probability * (1.0 - profile.failure_probability) > 0
                    else (0.0 if envelope.min_modeled_success_probability == 0 else 2.0)
                ),
            )
        ]
        if not feasible:
            return None

        deadline_slack = envelope.deadline_ms - time_ms - downstream_ms
        on_time = [profile for profile in feasible if profile.duration_ms_p95 <= deadline_slack]
        if not on_time:
            return None
        return min(
            on_time,
            key=lambda profile: (
                profile.cost_microusd,
                profile.total_tokens,
                profile.context_bytes,
                profile.failure_probability,
                -profile.quality,
                profile.duration_ms_p95,
                profile.provider,
                profile.name,
            ),
        )

    @staticmethod
    def _protected_task_ids(graph: ExecutionGraph) -> set[str]:
        """Return required work plus every transitive dependency it needs."""

        by_id = graph.by_id
        protected = {task.task_id for task in graph.tasks if not task.optional}
        stack = list(protected)
        while stack:
            task_id = stack.pop()
            for dependency in by_id[task_id].dependencies:
                if dependency not in protected:
                    protected.add(dependency)
                    stack.append(dependency)
        return protected

    @classmethod
    def _future_plan_fits(
        cls,
        tasks: tuple[TaskContract, ...],
        remaining_tokens: int,
        remaining_cost: int,
        remaining_context: int,
        time_ms: int,
        run_deadline_ms: int,
        required_success_probability: float,
    ) -> bool:
        """Find a joint resource-feasible profile plan for protected future work.

        The frontier is exact for the declared additive resource vectors. Deadline
        filtering is deliberately optimistic: every remaining task is tested as if
        it could start now. The live scheduler still has to produce a causal witness.
        """

        if min(remaining_tokens, remaining_cost, remaining_context) < 0:
            return False
        if required_success_probability > 1:
            return False
        frontier: set[tuple[int, int, int, float]] = {(0, 0, 0, 1.0)}
        for task in sorted(tasks, key=lambda item: item.task_id):
            task_deadline = min(task.deadline_ms or run_deadline_ms, run_deadline_ms)
            profiles = [
                profile
                for profile in task.profiles
                if profile.quality >= task.min_quality
                and time_ms + profile.duration_ms_p95 <= task_deadline
            ]
            if not profiles:
                return False
            candidates: set[tuple[int, int, int, float]] = set()
            for tokens, cost, context, success_probability in frontier:
                for profile in profiles:
                    state = (
                        tokens + profile.total_tokens,
                        cost + profile.cost_microusd,
                        context + profile.context_bytes,
                        success_probability * (1.0 - profile.failure_probability),
                    )
                    if (
                        state[0] <= remaining_tokens
                        and state[1] <= remaining_cost
                        and state[2] <= remaining_context
                    ):
                        candidates.add(state)
            if not candidates:
                return False
            frontier = cls._pareto_minimal(candidates)
        return any(state[3] >= required_success_probability for state in frontier)

    @staticmethod
    def _pareto_minimal(
        states: set[tuple[int, int, int, float]],
    ) -> set[tuple[int, int, int, float]]:
        """Discard resource vectors dominated in every additive dimension."""

        ordered = sorted(states)
        frontier: set[tuple[int, int, int, float]] = set()
        for candidate in ordered:
            if any(
                incumbent[0] <= candidate[0]
                and incumbent[1] <= candidate[1]
                and incumbent[2] <= candidate[2]
                and incumbent[3] >= candidate[3]
                for incumbent in frontier
            ):
                continue
            frontier = {
                incumbent
                for incumbent in frontier
                if not (
                    candidate[0] <= incumbent[0]
                    and candidate[1] <= incumbent[1]
                    and candidate[2] <= incumbent[2]
                    and candidate[3] >= incumbent[3]
                )
            }
            frontier.add(candidate)
        return frontier

    @staticmethod
    def _effect_conflicts(
        task: TaskContract,
        active_effects: dict[str, list[EffectClass]],
    ) -> bool:
        resource = task.effect.resource
        if not resource or task.effect.kind is EffectClass.PURE:
            return False
        active = active_effects.get(resource, [])
        if task.effect.kind is EffectClass.READ:
            return any(effect.writes for effect in active)
        return bool(active)

    @staticmethod
    def _upward_ranks_for_tasks(
        graph: ExecutionGraph,
        profiles: dict[str, BackendProfile],
        included: set[str],
    ) -> dict[str, int]:
        successors = graph.successors
        ranks: dict[str, int] = {}
        for task_id in reversed(graph.topological_order()):
            if task_id not in included:
                continue
            downstream = max(
                (ranks[child] for child in successors[task_id] if child in included),
                default=0,
            )
            ranks[task_id] = profiles[task_id].duration_ms_p95 + downstream
        return ranks

    @classmethod
    def _profile_lower_bound(
        cls,
        graph: ExecutionGraph,
        profiles: dict[str, BackendProfile],
        envelope: RunEnvelope,
        included: set[str],
    ) -> int:
        durations = {
            task_id: profile.duration_ms_p95
            for task_id, profile in profiles.items()
            if task_id in included
        }
        providers = {
            task_id: profile.provider
            for task_id, profile in profiles.items()
            if task_id in included
        }
        return cls._duration_lower_bound(graph, durations, providers, envelope)

    @classmethod
    def _entry_lower_bound(
        cls,
        graph: ExecutionGraph,
        entries: tuple[ScheduleEntry, ...],
        envelope: RunEnvelope,
    ) -> int:
        durations = {entry.task_id: entry.end_ms - entry.start_ms for entry in entries}
        providers = {entry.task_id: entry.provider for entry in entries}
        return cls._duration_lower_bound(graph, durations, providers, envelope)

    @staticmethod
    def _duration_lower_bound(
        graph: ExecutionGraph,
        durations: dict[str, int],
        providers: dict[str, str],
        envelope: RunEnvelope,
    ) -> int:
        if not durations:
            return 0
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

    @staticmethod
    def _failure(
        policy: SchedulePolicy,
        time_ms: int,
        model_bound: int,
        tokens: int,
        cost: int,
        context: int,
        modeled_success_probability: float,
        entries: list[ScheduleEntry],
        skipped: list[str],
        events: list[Event],
        reason: str,
        emit: object,
    ) -> ScheduleResult:
        emit(EventType.RUN_FAILED, details=(("reason", reason),))  # type: ignore[misc]
        return ScheduleResult(
            policy=policy,
            success=False,
            makespan_ms=time_ms,
            model_bound_ms=model_bound,
            total_tokens=tokens,
            total_cost_microusd=cost,
            total_context_bytes=context,
            modeled_success_probability=modeled_success_probability,
            entries=tuple(sorted(entries, key=lambda item: (item.start_ms, item.task_id))),
            skipped=tuple(sorted(skipped)),
            events=tuple(events),
            failure_reason=reason,
        )
