"""Async, fixture-only execution over an :class:`ExecutionGraph`.

Workers are injected deterministic fixtures. The executor has no HTTP client,
model SDK, shell runner, or generic tool adapter. Declared writes are never sent
to workers: they become durable ``PROPOSED`` effect intents when an effect broker
is supplied, or execution is refused.

This vertical slice assumes one active executor per run ID. SQLite makes event
append and sequence allocation safe, but a production deployment still needs a
distributed run lease and target-side resource accounting. Injected Python
callables are trusted test fixtures, not a security sandbox; production workers
need process isolation and capability controls so a falsely declared ``PURE``
callable cannot escape the effect-intent boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar, cast

from .contracts import BackendProfile, RunEnvelope, TaskContract
from .effects import EffectState, SQLiteEffectBroker
from .graph import ExecutionGraph, GraphValidationError
from .run_store import RunDefinition, RunEvent, SQLiteRunStore, Usage, UsageRecord
from .scheduler import SchedulePolicy, Scheduler


class ExecutionError(RuntimeError):
    """Base class for fixture executor failures."""


class TaskExecutionFailed(ExecutionError):
    """A task exhausted its admissible calls or failed permanently."""


class DeadlineExceeded(ExecutionError):
    """An absolute run or task deadline elapsed."""


class ExecutionCancelled(ExecutionError):
    """Cooperative cancellation was requested."""


class EffectExecutionRefused(ExecutionError):
    """A write was declared but no durable effect-intent sink was configured."""


class OutputValidationError(ExecutionError):
    """A fixture worker returned an output that failed validation."""


class RunAlreadyTerminal(ExecutionError):
    """A failed or cancelled run requires a new run ID, not silent replay."""


class UncooperativeWorker(ExecutionError):
    """A fixture ignored cancellation beyond the bounded cooperative grace."""


class AdmissionRefused(ExecutionError):
    """Adaptive admission proved that no executable plan fits the run envelope."""


class RetryReservationRefused(AdmissionRefused):
    """The declared envelope cannot reserve the retry policy's worst case."""


class UsageReservationExceeded(ExecutionError):
    """A fixture reported actual usage above its per-call reservation."""


class DurableOutputInvalid(ExecutionError):
    """A persisted output failed validation before resume."""


class SimulatedExecutorCrash(BaseException):
    """Crash injection that intentionally leaves an open attempt in the ledger."""


class RetryableWorkerError(RuntimeError):
    """A completed fixture call that may be retried between calls."""

    def __init__(self, message: str, *, actual_usage: Usage = Usage()) -> None:
        super().__init__(message)
        self.actual_usage = actual_usage


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """A total per-task call bound, including calls made before a restart."""

    max_attempts: int = 1
    backoff_ms: int = 0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.backoff_ms < 0:
            raise ValueError("backoff_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class WorkerResult:
    """JSON output and measured usage returned by a deterministic fixture call."""

    output: object
    actual_usage: Usage = Usage()


@dataclass(frozen=True, slots=True)
class TaskExecutionContext:
    """All deterministic inputs visible to one fixture worker call."""

    run_id: str
    task: TaskContract
    profile: BackendProfile
    attempt: int
    dependency_outputs: Mapping[str, object]
    deadline_at_ms: int
    cancellation_event: CancellationSignal

    @property
    def cancellation_requested(self) -> bool:
        return self.cancellation_event.is_set()


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    run_id: str
    outputs: Mapping[str, object]
    actual_usage: Usage
    events: tuple[RunEvent, ...]
    resumed_task_ids: tuple[str, ...]
    run_state: RunState
    skipped_task_ids: tuple[str, ...]


class RunState(str, Enum):
    COMPLETED = "completed"
    AWAITING_EFFECTS = "awaiting_effects"


class CancellationSignal:
    """Combined cancellation view that never mutates a caller-owned event."""

    def __init__(self, external: asyncio.Event | None = None) -> None:
        self._internal = asyncio.Event()
        self._external = external

    def set(self) -> None:
        self._internal.set()

    def is_set(self) -> bool:
        return self._internal.is_set() or (
            self._external is not None and self._external.is_set()
        )

    async def wait(self) -> None:
        if self.is_set():
            return
        internal_wait = asyncio.create_task(self._internal.wait())
        waits = {internal_wait}
        if self._external is not None:
            waits.add(asyncio.create_task(self._external.wait()))
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for future in waits:
                if not future.done():
                    future.cancel()
            await asyncio.gather(*waits, return_exceptions=True)


FixtureWorker = Callable[[TaskExecutionContext], Awaitable[WorkerResult]]
OutputValidator = Callable[[TaskContract, object], Awaitable[bool]]
T = TypeVar("T")
_CANCELLATION_GRACE_SECONDS = 0.05
EXECUTION_MANIFEST_REVISION = 1


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise OutputValidationError("worker outputs must be canonical JSON data") from exc


def _profile_usage(profile: BackendProfile) -> Usage:
    return Usage(
        tokens=profile.total_tokens,
        cost_microusd=profile.cost_microusd,
        context_bytes=profile.context_bytes,
    )


def _scale_usage(usage: Usage, multiplier: int) -> Usage:
    return Usage(
        tokens=usage.tokens * multiplier,
        cost_microusd=usage.cost_microusd * multiplier,
        context_bytes=usage.context_bytes * multiplier,
    )


def _usage_fits(actual: Usage, reservation: Usage) -> bool:
    return (
        actual.tokens <= reservation.tokens
        and actual.cost_microusd <= reservation.cost_microusd
        and actual.context_bytes <= reservation.context_bytes
    )


def _callable_identity(function: object) -> str:
    module = getattr(function, "__module__", type(function).__module__)
    qualname = getattr(function, "__qualname__", type(function).__qualname__)
    return f"{module}:{qualname}"


def _graph_digest(graph: ExecutionGraph) -> str:
    tasks: list[dict[str, object]] = []
    for task in sorted(graph.tasks, key=lambda item: item.task_id):
        profiles = [
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
            for profile in sorted(task.profiles, key=lambda item: (item.provider, item.name))
        ]
        tasks.append(
            {
                "task_id": task.task_id,
                "dependencies": sorted(task.dependencies),
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
                "profiles": profiles,
            }
        )
    encoded = json.dumps(tasks, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _envelope_dict(envelope: RunEnvelope) -> dict[str, object]:
    return {
        "deadline_ms": envelope.deadline_ms,
        "max_tokens": envelope.max_tokens,
        "max_cost_microusd": envelope.max_cost_microusd,
        "max_context_bytes": envelope.max_context_bytes,
        "max_parallelism": envelope.max_parallelism,
        "min_modeled_success_probability": envelope.min_modeled_success_probability,
        "provider_limits": [list(item) for item in sorted(envelope.provider_limits)],
    }


async def _default_output_validator(_task: TaskContract, output: object) -> bool:
    _canonical_json(output)
    return True


class AsyncGraphExecutor:
    """Execute pure/read fixture tasks and materialize writes as effect intents."""

    def __init__(
        self,
        store: SQLiteRunStore,
        *,
        workers: Mapping[str, FixtureWorker],
        output_validator: OutputValidator | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
        effect_broker: SQLiteEffectBroker | None = None,
        validator_revision: str = "1",
    ) -> None:
        if not validator_revision:
            raise ValueError("validator_revision is required")
        self.store = store
        self._workers = dict(workers)
        self._output_validator = output_validator or _default_output_validator
        self._retry_policy = retry_policy
        self._effect_broker = effect_broker
        self._validator_revision = validator_revision

    def _adaptive_admission(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
    ) -> tuple[dict[str, BackendProfile], tuple[str, ...], Usage]:
        admission = Scheduler().schedule(graph, envelope, SchedulePolicy.ADAPTIVE)
        if not admission.success:
            raise AdmissionRefused(admission.failure_reason or "adaptive admission refused run")
        profiles: dict[str, BackendProfile] = {}
        by_id = graph.by_id
        for entry in admission.entries:
            matches = [
                profile
                for profile in by_id[entry.task_id].profiles
                if profile.name == entry.backend and profile.provider == entry.provider
            ]
            if len(matches) != 1:
                raise AdmissionRefused(
                    f"admission selected unknown profile for task {entry.task_id!r}"
                )
            profiles[entry.task_id] = matches[0]
        skipped = tuple(sorted(admission.skipped))
        expected = set(by_id) - set(skipped)
        if set(profiles) != expected:
            raise AdmissionRefused("adaptive admission produced an incomplete execution plan")

        worst_case = Usage()
        for task_id, profile in profiles.items():
            task = by_id[task_id]
            multiplier = 1 if task.effect.kind.writes else self._retry_policy.max_attempts
            worst_case = worst_case + _scale_usage(_profile_usage(profile), multiplier)
        capacity = Usage(
            tokens=envelope.max_tokens,
            cost_microusd=envelope.max_cost_microusd,
            context_bytes=envelope.max_context_bytes,
        )
        if not _usage_fits(worst_case, capacity):
            raise RetryReservationRefused(
                "retry worst-case reservation exceeds token, cost, or context envelope"
            )
        return profiles, skipped, worst_case

    def _execution_manifest(
        self,
        graph: ExecutionGraph,
        profiles: Mapping[str, BackendProfile],
        skipped: tuple[str, ...],
        retry_reservation: Usage,
    ) -> tuple[str, dict[str, object]]:
        by_id = graph.by_id
        selected = []
        for task_id in sorted(profiles):
            profile = profiles[task_id]
            selected.append(
                {
                    "task_id": task_id,
                    "name": profile.name,
                    "provider": profile.provider,
                    "duration_ms_p50": profile.duration_ms_p50,
                    "duration_ms_p95": profile.duration_ms_p95,
                    "tokens": profile.total_tokens,
                    "cost_microusd": profile.cost_microusd,
                    "context_bytes": profile.context_bytes,
                    "quality": profile.quality,
                    "failure_probability": profile.failure_probability,
                }
            )
        effects = [
            {
                "task_id": task_id,
                "kind": by_id[task_id].effect.kind.value,
                "resource": by_id[task_id].effect.resource,
                "requires_approval": by_id[task_id].effect.requires_approval,
                "idempotency_key": by_id[task_id].effect.idempotency_key,
                "compensation": by_id[task_id].effect.compensation,
            }
            for task_id in sorted(by_id)
        ]
        manifest: dict[str, object] = {
            "revision": EXECUTION_MANIFEST_REVISION,
            "selected_profiles": selected,
            "skipped_task_ids": list(skipped),
            "retry_policy": {
                "max_attempts": self._retry_policy.max_attempts,
                "backoff_ms": self._retry_policy.backoff_ms,
            },
            "retry_reservation": {
                "tokens": retry_reservation.tokens,
                "cost_microusd": retry_reservation.cost_microusd,
                "context_bytes": retry_reservation.context_bytes,
            },
            "worker_task_ids": sorted(self._workers),
            "validator": {
                "identity": _callable_identity(self._output_validator),
                "revision": self._validator_revision,
            },
            "effect_broker_configured": self._effect_broker is not None,
            "effect_contracts": effects,
        }
        encoded = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), manifest

    @staticmethod
    def _absolute_task_deadline(run: RunDefinition, task: TaskContract) -> int:
        if task.deadline_ms is None:
            return run.deadline_at_ms
        return min(run.deadline_at_ms, run.created_at_ms + task.deadline_ms)

    @staticmethod
    def _consume_future(future: asyncio.Future[object]) -> None:
        if future.cancelled():
            return
        try:
            future.exception()
        except BaseException:
            pass

    async def _cancel_future_bounded(self, future: asyncio.Future[object]) -> bool:
        """Request cancellation without letting a hostile fixture block the executor."""

        if future.done():
            self._consume_future(future)
            return True
        future.cancel()
        done, _ = await asyncio.wait({future}, timeout=_CANCELLATION_GRACE_SECONDS)
        if future in done:
            self._consume_future(future)
            return True
        future.add_done_callback(self._consume_future)
        return False

    async def _await_controlled(
        self,
        awaitable: Awaitable[T],
        *,
        cancellation_event: CancellationSignal,
        deadline_at_ms: int,
    ) -> T:
        """Await one operation under the same absolute deadline and cancel signal."""

        if cancellation_event.is_set():
            if hasattr(awaitable, "close"):
                cast(object, awaitable).close()  # type: ignore[attr-defined]
            raise ExecutionCancelled("cooperative cancellation requested")
        remaining = (deadline_at_ms - self.store.now_ms) / 1_000
        if remaining <= 0:
            if hasattr(awaitable, "close"):
                cast(object, awaitable).close()  # type: ignore[attr-defined]
            raise DeadlineExceeded("absolute task or run deadline elapsed")

        operation = asyncio.ensure_future(awaitable)
        cancellation = asyncio.create_task(cancellation_event.wait())
        cleanup_attempted = False
        try:
            done, _ = await asyncio.wait(
                {operation, cancellation},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation in done:
                return await operation
            cancelled_by_request = cancellation in done and cancellation_event.is_set()
            if not cancelled_by_request:
                cancellation_event.set()
            cleanup_attempted = True
            stopped = await self._cancel_future_bounded(operation)
            if not stopped:
                raise UncooperativeWorker(
                    "fixture ignored cooperative cancellation; process isolation is required"
                )
            if cancelled_by_request:
                raise ExecutionCancelled("cooperative cancellation requested")
            raise DeadlineExceeded("absolute task or run deadline elapsed")
        finally:
            await self._cancel_future_bounded(cancellation)
            if not operation.done() and not cleanup_attempted:
                await self._cancel_future_bounded(operation)

    @asynccontextmanager
    async def _capacity(
        self,
        global_semaphore: asyncio.Semaphore,
        provider_semaphore: asyncio.Semaphore,
        *,
        cancellation_event: CancellationSignal,
        deadline_at_ms: int,
    ):
        global_acquired = False
        provider_acquired = False
        try:
            await self._await_controlled(
                provider_semaphore.acquire(),
                cancellation_event=cancellation_event,
                deadline_at_ms=deadline_at_ms,
            )
            provider_acquired = True
            await self._await_controlled(
                global_semaphore.acquire(),
                cancellation_event=cancellation_event,
                deadline_at_ms=deadline_at_ms,
            )
            global_acquired = True
            yield
        finally:
            if global_acquired:
                global_semaphore.release()
            if provider_acquired:
                provider_semaphore.release()

    def _append_attempt_outcome(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt: int,
        outcome: str,
        payload: Mapping[str, object],
        estimated: Usage,
        reserved: Usage,
        actual: Usage,
    ) -> RunEvent:
        return self.store.append_event(
            run_id=run_id,
            event_id=f"{run_id}:{task_id}:attempt:{attempt}:{outcome}",
            event_type=f"task.attempt_{outcome}",
            task_id=task_id,
            attempt=attempt,
            payload=payload,
            usage=UsageRecord(estimated=estimated, reserved=reserved, actual=actual),
        )

    def _reject_usage_overrun(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt: int,
        estimated: Usage,
        reserved: Usage,
        actual: Usage,
        phase: str,
    ) -> None:
        if _usage_fits(actual, reserved):
            return
        error = UsageReservationExceeded(
            f"task {task_id!r} attempt {attempt} exceeded its usage reservation"
        )
        self._append_attempt_outcome(
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            outcome="failed",
            payload={
                "error_type": type(error).__name__,
                "message": str(error),
                "retryable": False,
                "phase": phase,
            },
            estimated=estimated,
            reserved=reserved,
            actual=actual,
        )
        raise error

    async def _validate_output(self, task: TaskContract, output: object) -> None:
        try:
            valid = await self._output_validator(task, output)
            _canonical_json(output)
        except OutputValidationError:
            raise
        except Exception as exc:
            raise OutputValidationError(
                f"task {task.task_id!r} output validator raised: {exc}"
            ) from exc
        if not valid:
            raise OutputValidationError(f"task {task.task_id!r} output failed validation")

    async def _complete_fixture_attempt(
        self,
        *,
        run: RunDefinition,
        task: TaskContract,
        attempt: int,
        result: object,
        estimated: Usage,
        reserved: Usage,
        cancellation_event: CancellationSignal,
        deadline_at_ms: int,
    ) -> object:
        actual = result.actual_usage if isinstance(result, WorkerResult) else Usage()
        error: OutputValidationError | TaskExecutionFailed | None = None
        if not isinstance(result, WorkerResult):
            error = TaskExecutionFailed(
                f"task {task.task_id!r} worker must return WorkerResult"
            )
        else:
            self._reject_usage_overrun(
                run_id=run.run_id,
                task_id=task.task_id,
                attempt=attempt,
                estimated=estimated,
                reserved=reserved,
                actual=result.actual_usage,
                phase="worker_result",
            )
            try:
                await self._await_controlled(
                    self._validate_output(task, result.output),
                    cancellation_event=cancellation_event,
                    deadline_at_ms=deadline_at_ms,
                )
            except (DeadlineExceeded, ExecutionCancelled, UncooperativeWorker) as exc:
                outcome = "cancelled" if isinstance(exc, ExecutionCancelled) else "failed"
                self._append_attempt_outcome(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    outcome=outcome,
                    payload={
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "retryable": False,
                        "phase": "output_validation",
                    },
                    estimated=estimated,
                    reserved=reserved,
                    actual=actual,
                )
                raise
            except asyncio.CancelledError:
                self._append_attempt_outcome(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    outcome="cancelled",
                    payload={
                        "message": "caller cancelled during output validation",
                        "phase": "output_validation",
                    },
                    estimated=estimated,
                    reserved=reserved,
                    actual=actual,
                )
                raise
            except OutputValidationError as exc:
                error = exc
        if error is not None:
            self._append_attempt_outcome(
                run_id=run.run_id,
                task_id=task.task_id,
                attempt=attempt,
                outcome="failed",
                payload={
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "retryable": False,
                },
                estimated=estimated,
                reserved=reserved,
                actual=actual,
            )
            raise error

        result = cast(WorkerResult, result)
        if cancellation_event.is_set():
            error = ExecutionCancelled("cooperative cancellation requested before commit")
            self._append_attempt_outcome(
                run_id=run.run_id,
                task_id=task.task_id,
                attempt=attempt,
                outcome="cancelled",
                payload={"message": str(error), "phase": "completion"},
                estimated=estimated,
                reserved=reserved,
                actual=result.actual_usage,
            )
            raise error
        if self.store.now_ms >= deadline_at_ms:
            error = DeadlineExceeded("absolute deadline elapsed before completion commit")
            self._append_attempt_outcome(
                run_id=run.run_id,
                task_id=task.task_id,
                attempt=attempt,
                outcome="failed",
                payload={
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "retryable": False,
                    "phase": "completion",
                },
                estimated=estimated,
                reserved=reserved,
                actual=result.actual_usage,
            )
            raise error
        self.store.complete_attempt(
            run_id=run.run_id,
            task_id=task.task_id,
            attempt=attempt,
            output=result.output,
            estimated=estimated,
            reserved=reserved,
            actual=result.actual_usage,
        )
        return result.output

    async def _execute_fixture_task(
        self,
        *,
        run: RunDefinition,
        task: TaskContract,
        profile: BackendProfile,
        dependency_outputs: Mapping[str, object],
        global_semaphore: asyncio.Semaphore,
        provider_semaphore: asyncio.Semaphore,
        cancellation_event: CancellationSignal,
    ) -> object:
        worker = self._workers.get(task.task_id)
        if worker is None:
            raise TaskExecutionFailed(f"task {task.task_id!r} has no injected fixture worker")
        deadline_at_ms = self._absolute_task_deadline(run, task)
        estimated = _profile_usage(profile)
        reserved = estimated
        prior_attempts = sum(
            1
            for event in self.store.events(run.run_id)
            if event.task_id == task.task_id and event.event_type == "task.attempt_started"
        )
        calls_remaining = self._retry_policy.max_attempts - prior_attempts
        if calls_remaining <= 0:
            raise TaskExecutionFailed(
                f"task {task.task_id!r} exhausted {self._retry_policy.max_attempts} calls"
            )

        for call_index in range(calls_remaining):
            if cancellation_event.is_set():
                raise ExecutionCancelled("cooperative cancellation requested")
            if self.store.now_ms >= deadline_at_ms:
                raise DeadlineExceeded(f"task {task.task_id!r} absolute deadline elapsed")
            async with self._capacity(
                global_semaphore,
                provider_semaphore,
                cancellation_event=cancellation_event,
                deadline_at_ms=deadline_at_ms,
            ):
                started = self.store.start_attempt(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    provider=profile.provider,
                    backend=profile.name,
                    estimated=estimated,
                    reserved=reserved,
                )
                attempt = cast(int, started.attempt)
                context = TaskExecutionContext(
                    run_id=run.run_id,
                    task=task,
                    profile=profile,
                    attempt=attempt,
                    dependency_outputs=dict(dependency_outputs),
                    deadline_at_ms=deadline_at_ms,
                    cancellation_event=cancellation_event,
                )
                retry_requested = False
                try:
                    result = await self._await_controlled(
                        worker(context),
                        cancellation_event=cancellation_event,
                        deadline_at_ms=deadline_at_ms,
                    )
                except RetryableWorkerError as exc:
                    self._reject_usage_overrun(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        estimated=estimated,
                        reserved=reserved,
                        actual=exc.actual_usage,
                        phase="retryable_failure",
                    )
                    self._append_attempt_outcome(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        outcome="failed",
                        payload={
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "retryable": True,
                        },
                        estimated=estimated,
                        reserved=reserved,
                        actual=exc.actual_usage,
                    )
                    if call_index + 1 >= calls_remaining:
                        raise TaskExecutionFailed(
                            f"task {task.task_id!r} exhausted bounded retries"
                        ) from exc
                    retry_requested = True
                except DeadlineExceeded as exc:
                    self._append_attempt_outcome(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        outcome="failed",
                        payload={
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "retryable": False,
                        },
                        estimated=estimated,
                        reserved=reserved,
                        actual=Usage(),
                    )
                    raise
                except ExecutionCancelled as exc:
                    self._append_attempt_outcome(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        outcome="cancelled",
                        payload={"message": str(exc)},
                        estimated=estimated,
                        reserved=reserved,
                        actual=Usage(),
                    )
                    raise
                except asyncio.CancelledError:
                    self._append_attempt_outcome(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        outcome="cancelled",
                        payload={"message": "asyncio task cancelled"},
                        estimated=estimated,
                        reserved=reserved,
                        actual=Usage(),
                    )
                    raise
                except Exception as exc:
                    self._append_attempt_outcome(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        outcome="failed",
                        payload={
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "retryable": False,
                        },
                        estimated=estimated,
                        reserved=reserved,
                        actual=Usage(),
                    )
                    raise TaskExecutionFailed(
                        f"task {task.task_id!r} fixture call failed: {exc}"
                    ) from exc
                if not retry_requested:
                    return await self._complete_fixture_attempt(
                        run=run,
                        task=task,
                        attempt=attempt,
                        result=result,
                        estimated=estimated,
                        reserved=reserved,
                        cancellation_event=cancellation_event,
                        deadline_at_ms=deadline_at_ms,
                    )

            if self._retry_policy.backoff_ms:
                backoff_end = self.store.now_ms + self._retry_policy.backoff_ms
                if backoff_end >= deadline_at_ms:
                    raise DeadlineExceeded(
                        f"task {task.task_id!r} deadline does not admit retry backoff"
                    )
                await self._await_controlled(
                    asyncio.sleep(self._retry_policy.backoff_ms / 1_000),
                    cancellation_event=cancellation_event,
                    deadline_at_ms=deadline_at_ms,
                )
        raise TaskExecutionFailed(f"task {task.task_id!r} produced no result")

    def _materialize_effect_intent(
        self,
        *,
        run: RunDefinition,
        task: TaskContract,
        dependency_outputs: Mapping[str, object],
    ) -> object:
        if self._effect_broker is None:
            self.store.append_event(
                run_id=run.run_id,
                event_id=f"{run.run_id}:{task.task_id}:effect_refused",
                event_type="task.effect_refused",
                task_id=task.task_id,
                payload={"reason": "no durable effect broker configured"},
            )
            raise EffectExecutionRefused(
                f"task {task.task_id!r} declares a write; external execution is refused"
            )
        idempotency_key = task.effect.idempotency_key or (
            f"fixture-run:{run.run_id}:task:{task.task_id}"
        )
        intent = self._effect_broker.propose(
            run_id=run.run_id,
            action=task.task_id,
            resource=task.effect.resource,
            effect_class=task.effect.kind,
            idempotency_key=idempotency_key,
            payload={
                "task_id": task.task_id,
                "dependency_outputs": dict(dependency_outputs),
                "fixture_only": True,
            },
            compensation_action=task.effect.compensation,
        )
        output = {
            "effect_intent_id": intent.intent_id,
            "effect_state": intent.state.value,
            "executed_externally": False,
        }
        self.store.append_event(
            run_id=run.run_id,
            event_id=f"{run.run_id}:{task.task_id}:effect_intent",
            event_type="task.effect_intent_created",
            task_id=task.task_id,
            payload={"output": output},
        )
        self.store.append_event(
            run_id=run.run_id,
            event_id=f"{run.run_id}:{task.task_id}:completed",
            event_type="task.completed",
            task_id=task.task_id,
            payload={"output": output, "kind": "effect_intent"},
        )
        return output

    async def _execute_task(
        self,
        *,
        run: RunDefinition,
        task: TaskContract,
        profile: BackendProfile,
        dependency_outputs: Mapping[str, object],
        global_semaphore: asyncio.Semaphore,
        provider_semaphore: asyncio.Semaphore,
        cancellation_event: CancellationSignal,
    ) -> object:
        if self.store.now_ms >= self._absolute_task_deadline(run, task):
            raise DeadlineExceeded(f"task {task.task_id!r} absolute deadline elapsed")
        if task.effect.kind.writes:
            return self._materialize_effect_intent(
                run=run,
                task=task,
                dependency_outputs=dependency_outputs,
            )
        return await self._execute_fixture_task(
            run=run,
            task=task,
            profile=profile,
            dependency_outputs=dependency_outputs,
            global_semaphore=global_semaphore,
            provider_semaphore=provider_semaphore,
            cancellation_event=cancellation_event,
        )

    async def _cancel_running(
        self,
        running: Mapping[asyncio.Task[object], str],
        cancellation_event: CancellationSignal,
    ) -> None:
        cancellation_event.set()
        for future in running:
            if not future.done():
                future.cancel()
        if running:
            done, pending = await asyncio.wait(
                set(running), timeout=_CANCELLATION_GRACE_SECONDS
            )
            for future in done:
                self._consume_future(future)
            for future in pending:
                future.add_done_callback(self._consume_future)

    async def _revalidate_durable_outputs(
        self,
        *,
        run: RunDefinition,
        graph: ExecutionGraph,
        durable: Mapping[str, object],
        completion_events: Mapping[str, RunEvent],
        profiles: Mapping[str, BackendProfile],
        cancellation_event: CancellationSignal,
    ) -> None:
        by_id = graph.by_id
        for task_id in sorted(durable):
            task = by_id.get(task_id)
            event = completion_events.get(task_id)
            if task is None or event is None:
                raise DurableOutputInvalid(f"unknown durable task {task_id!r}")
            kind = event.payload.get("kind")
            output = durable[task_id]
            if kind == "fixture_output":
                profile = profiles.get(task_id)
                expected_usage = _profile_usage(profile) if profile is not None else None
                if (
                    expected_usage is None
                    or event.usage.estimated != expected_usage
                    or event.usage.reserved != expected_usage
                    or not _usage_fits(event.usage.actual, expected_usage)
                ):
                    raise DurableOutputInvalid(
                        f"durable usage for task {task_id!r} violates its selected profile"
                    )
                try:
                    await self._await_controlled(
                        self._validate_output(task, output),
                        cancellation_event=cancellation_event,
                        deadline_at_ms=self._absolute_task_deadline(run, task),
                    )
                except OutputValidationError as exc:
                    raise DurableOutputInvalid(
                        f"durable output for task {task_id!r} failed revalidation"
                    ) from exc
                continue
            if kind != "effect_intent" or not task.effect.kind.writes:
                raise DurableOutputInvalid(
                    f"durable task {task_id!r} has unsupported completion kind {kind!r}"
                )
            if self._effect_broker is None or not isinstance(output, dict):
                raise DurableOutputInvalid(
                    f"durable effect output for task {task_id!r} lacks its broker"
                )
            intent_id = output.get("effect_intent_id")
            if not isinstance(intent_id, str) or output.get("executed_externally") is not False:
                raise DurableOutputInvalid(f"durable effect output for {task_id!r} is malformed")
            try:
                intent = self._effect_broker.get(intent_id)
            except Exception as exc:
                raise DurableOutputInvalid(
                    f"durable effect intent for task {task_id!r} is unavailable"
                ) from exc
            if intent.action != task_id or intent.resource != task.effect.resource:
                raise DurableOutputInvalid(
                    f"durable effect intent for task {task_id!r} violates its contract"
                )

    def _pending_effect_task_ids(
        self,
        graph: ExecutionGraph,
        outputs: Mapping[str, object],
    ) -> tuple[str, ...]:
        pending: list[str] = []
        for task in graph.tasks:
            if not task.effect.kind.writes or task.task_id not in outputs:
                continue
            output = outputs[task.task_id]
            if self._effect_broker is None or not isinstance(output, dict):
                raise DurableOutputInvalid(f"effect output for {task.task_id!r} is unavailable")
            intent_id = output.get("effect_intent_id")
            if not isinstance(intent_id, str):
                raise DurableOutputInvalid(f"effect output for {task.task_id!r} is malformed")
            intent = self._effect_broker.get(intent_id)
            if intent.state not in {EffectState.COMMITTED, EffectState.COMPENSATED}:
                pending.append(task.task_id)
        return tuple(sorted(pending))

    async def execute(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        cancellation_event: asyncio.Event | None = None,
    ) -> ExecutionResult:
        """Execute or resume a run without repeating durably completed tasks."""

        graph.validate()
        envelope_errors = envelope.validate()
        if envelope_errors:
            raise GraphValidationError("; ".join(envelope_errors))
        if not run_id:
            raise ValueError("run_id is required")

        profiles, skipped_task_ids, retry_reservation = self._adaptive_admission(
            graph, envelope
        )
        by_id = graph.by_id
        missing_workers = sorted(
            task_id
            for task_id in profiles
            if not by_id[task_id].effect.kind.writes and task_id not in self._workers
        )
        if missing_workers:
            raise AdmissionRefused(
                f"selected fixture tasks have no worker: {missing_workers}"
            )
        manifest_digest, manifest = self._execution_manifest(
            graph,
            profiles,
            skipped_task_ids,
            retry_reservation,
        )
        persisted_envelope = _envelope_dict(envelope)
        persisted_envelope["retry_policy"] = {
            "max_attempts": self._retry_policy.max_attempts,
            "backoff_ms": self._retry_policy.backoff_ms,
        }
        run = self.store.get_or_create_run(
            run_id=run_id,
            graph_digest=_graph_digest(graph),
            envelope=persisted_envelope,
            deadline_at_ms=self.store.now_ms + envelope.deadline_ms,
            manifest_digest=manifest_digest,
            manifest_revision=EXECUTION_MANIFEST_REVISION,
        )
        self.store.append_event(
            run_id=run_id,
            event_id=f"{run_id}:run_started",
            event_type="run.started",
            payload={
                "deadline_at_ms": run.deadline_at_ms,
                "manifest_digest": manifest_digest,
                "manifest_revision": EXECUTION_MANIFEST_REVISION,
                "manifest": manifest,
                "selected_task_ids": sorted(profiles),
                "skipped_task_ids": list(skipped_task_ids),
                "retry_reservation": manifest["retry_reservation"],
            },
        )
        existing_events = self.store.events(run_id)
        terminal_failure = next(
            (
                event
                for event in reversed(existing_events)
                if event.event_type in {"run.failed", "run.cancelled"}
            ),
            None,
        )
        if terminal_failure is not None:
            raise RunAlreadyTerminal(
                f"run {run_id!r} is terminal after {terminal_failure.event_type}; use a new run ID"
            )
        cancel_signal = CancellationSignal(cancellation_event)
        durable_records = self.store.completed_tasks(run_id)
        if set(durable_records) & set(skipped_task_ids):
            raise DurableOutputInvalid("durable outputs include tasks skipped by admission")
        resumed_task_ids = tuple(sorted(durable_records))
        outputs = {
            task_id: completion.output for task_id, completion in durable_records.items()
        }
        await self._revalidate_durable_outputs(
            run=run,
            graph=graph,
            durable=outputs,
            completion_events={
                task_id: completion.event
                for task_id, completion in durable_records.items()
            },
            profiles=profiles,
            cancellation_event=cancel_signal,
        )
        global_semaphore = asyncio.Semaphore(envelope.max_parallelism)
        provider_semaphores = {
            provider: asyncio.Semaphore(envelope.provider_limit(provider))
            for provider in {profile.provider for profile in profiles.values()}
        }
        pending = set(profiles) - set(outputs)
        running: dict[asyncio.Task[object], str] = {}

        try:
            while pending or running:
                if cancel_signal.is_set():
                    raise ExecutionCancelled("cooperative cancellation requested")
                if self.store.now_ms >= run.deadline_at_ms:
                    raise DeadlineExceeded("absolute run deadline elapsed")
                running_ids = set(running.values())
                ready = sorted(
                    task_id
                    for task_id in pending
                    if task_id not in running_ids
                    and all(dependency in outputs for dependency in by_id[task_id].dependencies)
                )
                for task_id in ready:
                    task = by_id[task_id]
                    profile = profiles[task_id]
                    dependencies = {
                        dependency: outputs[dependency] for dependency in task.dependencies
                    }
                    future = asyncio.create_task(
                        self._execute_task(
                            run=run,
                            task=task,
                            profile=profile,
                            dependency_outputs=dependencies,
                            global_semaphore=global_semaphore,
                            provider_semaphore=provider_semaphores[profile.provider],
                            cancellation_event=cancel_signal,
                        ),
                        name=f"agent-physics:{run_id}:{task_id}",
                    )
                    running[future] = task_id
                if not running:
                    raise TaskExecutionFailed("executor deadlock: no dependency-ready tasks")

                done, _ = await asyncio.wait(
                    tuple(running), return_when=asyncio.FIRST_COMPLETED
                )
                for future in sorted(done, key=lambda item: running[item]):
                    task_id = running.pop(future)
                    output = future.result()
                    outputs[task_id] = output
                    pending.remove(task_id)
        except SimulatedExecutorCrash:
            await self._cancel_running(running, cancel_signal)
            raise
        except asyncio.CancelledError:
            await self._cancel_running(running, cancel_signal)
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:run_cancelled",
                event_type="run.cancelled",
                payload={"reason": "caller cancelled executor"},
            )
            raise
        except ExecutionCancelled as exc:
            await self._cancel_running(running, cancel_signal)
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:run_cancelled",
                event_type="run.cancelled",
                payload={"reason": str(exc)},
            )
            raise
        except Exception as exc:
            await self._cancel_running(running, cancel_signal)
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:run_failed",
                event_type="run.failed",
                payload={"error_type": type(exc).__name__, "reason": str(exc)},
            )
            raise
        except BaseException:
            await self._cancel_running(running, cancel_signal)
            raise

        pending_effects = self._pending_effect_task_ids(graph, outputs)
        if pending_effects:
            run_state = RunState.AWAITING_EFFECTS
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:run_awaiting_effects",
                event_type="run.awaiting_effects",
                payload={
                    "pending_effect_task_ids": list(pending_effects),
                    "skipped_task_ids": list(skipped_task_ids),
                    "task_count": len(outputs),
                },
            )
        else:
            run_state = RunState.COMPLETED
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:run_completed",
                event_type="run.completed",
                payload={
                    "skipped_task_ids": list(skipped_task_ids),
                    "task_count": len(outputs),
                },
            )
        events = self.store.events(run_id)
        actual_usage = Usage()
        for event in events:
            if event.event_type in {
                "task.attempt_failed",
                "task.attempt_succeeded",
                "task.attempt_cancelled",
            }:
                actual_usage = actual_usage + event.usage.actual
        return ExecutionResult(
            run_id=run_id,
            outputs={task_id: outputs[task_id] for task_id in sorted(outputs)},
            actual_usage=actual_usage,
            events=events,
            resumed_task_ids=resumed_task_ids,
            run_state=run_state,
            skipped_task_ids=skipped_task_ids,
        )
