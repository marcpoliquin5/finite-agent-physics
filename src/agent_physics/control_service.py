"""Configured ASGI service factory for the bounded StormShift runtime."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .adapter_capabilities import AdapterCapabilityError, validate_adapter_bindings
from .adaptive_runtime import (
    ADAPTIVE_RUNTIME_LIMITATIONS,
    ADAPTIVE_RUNTIME_SCOPE,
    AdaptiveDecision,
    AdaptiveInvariantError,
    AdaptiveRuntime,
    AdaptiveStatus,
    AdaptiveTaskContext,
    AdaptiveWorker,
    AdaptiveWorkerResult,
    SimulatedAdaptiveCrash,
    plan_adaptive_admission,
    replay_adaptive_records,
)
from .bob_lifecycle import default_state_directory
from .control_api import ControlPlane
from .contracts import BackendProfile, RunEnvelope, TaskContract
from .effects import EffectState, SQLiteEffectBroker, scoped_effect_idempotency_key
from .examples import miami_eoc_graph
from .executor import (
    CancellationSignal,
    ExecutionError,
    ExecutionResult,
    FixtureWorker,
    OutputValidator,
    RunState,
    TaskExecutionContext,
)
from .graph import ExecutionGraph
from .physical_resources import PhysicalAdmissionStatus, analyze_physical_resources
from .run_store import SQLiteRunStore, Usage
from .stormshift_runtime import StormShiftRuntime, stormshift_envelope
from .workflow_ir import compile_contracts, compile_python


class AdaptiveControlServiceError(RuntimeError):
    """A bounded adaptive control request failed with a safe public reason."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.control_status = status
        self.control_code = code
        self.control_message = message


@dataclass(slots=True)
class _AdaptiveSession:
    runtime: AdaptiveRuntime
    graph: ExecutionGraph
    envelope: RunEnvelope
    cancellation_event: asyncio.Event
    lock: asyncio.Lock
    changed: asyncio.Event
    start_gate: asyncio.Event


def _selected_profile(task: TaskContract) -> BackendProfile:
    """Return the deterministic low-resource profile used by legacy diagnostics."""

    qualified = [profile for profile in task.profiles if profile.quality >= task.min_quality]
    if not qualified:
        raise AdaptiveInvariantError(f"task {task.task_id!r} has no quality-qualified profile")
    return min(
        qualified,
        key=lambda profile: (
            profile.total_tokens,
            profile.cost_microusd,
            profile.context_bytes,
            profile.duration_ms_p95,
            profile.provider,
            profile.name,
        ),
    )


class AdaptiveControlRuntime:
    """Async service bridge over FINITE's replayable adaptive controller.

    The bridge keeps one bounded single-flight controller per active run. Every
    accepted external control fact is reduced and persisted by ``AdaptiveRuntime``;
    writes are only proposed to the durable effect broker. A coordinator recovery
    reconstructs the workflow and controller exclusively from the append-only run
    ledger before any future dispatch.
    """

    def __init__(
        self,
        store: SQLiteRunStore,
        effect_broker: SQLiteEffectBroker,
        *,
        workers: Mapping[str, FixtureWorker],
        output_validator: OutputValidator,
        crash_after_dispatch_task_ids: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.store = store
        self.effect_broker = effect_broker
        self._workers = dict(workers)
        self._output_validator = output_validator
        self._sessions: dict[str, _AdaptiveSession] = {}
        self._start_paused: dict[str, bool] = {}
        self._crash_after_dispatch = dict(crash_after_dispatch_task_ids or {})

    def configure_start(self, run_id: str, *, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("paused must be a boolean")
        self._start_paused[run_id] = paused

    @staticmethod
    def _providers(graph: ExecutionGraph) -> frozenset[str]:
        return frozenset(
            profile.provider for task in graph.tasks for profile in task.profiles
        )

    def _preflight(self, graph: ExecutionGraph, envelope: RunEnvelope) -> None:
        graph.validate()
        errors = envelope.validate()
        if errors:
            raise AdaptiveInvariantError("invalid run envelope: " + "; ".join(errors))
        selected, _skipped = plan_adaptive_admission(graph, envelope)
        missing_workers = sorted(
            task_id
            for task_id in selected
            if not graph.by_id[task_id].effect.kind.writes and task_id not in self._workers
        )
        if missing_workers:
            raise AdaptiveInvariantError(
                f"selected fixture tasks have no worker: {missing_workers}"
            )
        try:
            validate_adapter_bindings(graph.by_id, selected, self._workers)
        except AdapterCapabilityError as exc:
            raise AdaptiveInvariantError(str(exc)) from exc
        physical = analyze_physical_resources(graph, envelope, selected)
        if physical.status is PhysicalAdmissionStatus.REFUSED:
            dimensions = sorted(check.dimension for check in physical.violations)
            raise AdaptiveInvariantError(
                "physical-resource admission refused run: " + ", ".join(dimensions)
            )

    def _worker_adapters(
        self,
        graph: ExecutionGraph,
        cancellation_event: asyncio.Event,
    ) -> dict[str, AdaptiveWorker]:
        by_id = graph.by_id

        def adapt(task_id: str, worker: FixtureWorker) -> AdaptiveWorker:
            def execute(context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
                task = by_id[task_id]
                profile = next(
                    (
                        item
                        for item in task.profiles
                        if item.provider == context.provider and item.name == context.backend
                    ),
                    None,
                )
                if profile is None:
                    raise AdaptiveInvariantError("adaptive dispatch selected an unknown profile")
                durable_run = self.store.get_run(context.run_id)
                fixture_context = TaskExecutionContext(
                    run_id=context.run_id,
                    task=task,
                    profile=profile,
                    attempt=context.attempt,
                    dependency_outputs=context.dependency_outputs,
                    deadline_at_ms=durable_run.deadline_at_ms,
                    cancellation_event=CancellationSignal(cancellation_event),
                )
                result = asyncio.run(worker(fixture_context))
                valid = asyncio.run(self._output_validator(task, result.output))
                if valid is not True:
                    raise AdaptiveInvariantError(
                        f"task {task_id!r} failed its configured output validator"
                    )
                return AdaptiveWorkerResult(
                    output=result.output,
                    actual_usage=result.actual_usage,
                    # Logical time is deterministic and bounded by the selected
                    # profile rather than host scheduling noise.
                    duration_ms=profile.duration_ms_p50,
                )

            return execute

        return {
            task_id: adapt(task_id, worker)
            for task_id, worker in self._workers.items()
            if task_id in by_id and not by_id[task_id].effect.kind.writes
        }

    def _new_session(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        cancellation_event: asyncio.Event,
        paused: bool,
    ) -> _AdaptiveSession:
        runtime = AdaptiveRuntime(
            self.store,
            graph,
            envelope,
            run_id=run_id,
            workers=self._worker_adapters(graph, cancellation_event),
            effect_broker=self.effect_broker,
            crash_after_dispatch_task_ids=self._crash_after_dispatch.pop(run_id, ()),
        )
        gate = asyncio.Event()
        if not paused:
            gate.set()
        session = _AdaptiveSession(
            runtime=runtime,
            graph=graph,
            envelope=envelope,
            cancellation_event=cancellation_event,
            lock=asyncio.Lock(),
            changed=asyncio.Event(),
            start_gate=gate,
        )
        if runtime.state.status is AdaptiveStatus.RUNNING:
            self._sessions[run_id] = session
        return session

    def _retire_session(self, session: _AdaptiveSession) -> None:
        run_id = session.runtime.run_id
        if self._sessions.get(run_id) is session:
            del self._sessions[run_id]

    def _persist_run_start(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        start_paused: bool,
    ) -> None:
        workflow = compile_contracts(graph, envelope).to_python()
        self.store.append_event(
            run_id=run_id,
            event_id=f"{run_id}:run_started",
            event_type="run.started",
            payload={
                "execution_mode": "adaptive",
                "start_paused": start_paused,
                "workflow": workflow,
                "scope": list(ADAPTIVE_RUNTIME_SCOPE),
                "limitations": list(ADAPTIVE_RUNTIME_LIMITATIONS),
                "external_effect_boundary": "durable-proposal-only",
            },
        )

    def _compiled_run(self, run_id: str) -> tuple[ExecutionGraph, RunEnvelope]:
        self.store.get_run(run_id)
        started = next(
            (
                event
                for event in self.store.events(run_id)
                if event.event_type == "run.started"
            ),
            None,
        )
        workflow = started.payload.get("workflow") if started is not None else None
        if not isinstance(workflow, dict):
            raise AdaptiveControlServiceError(
                409,
                "adaptive_state_unavailable",
                "the run has no durable adaptive workflow",
            )
        try:
            compiled = compile_python(workflow)
        except Exception as exc:
            raise AdaptiveControlServiceError(
                409,
                "adaptive_state_invalid",
                "the durable adaptive workflow failed validation",
            ) from exc
        return compiled.graph, compiled.envelope

    async def _validate_durable_outputs(self, session: _AdaptiveSession) -> None:
        completed = self.store.completed_tasks(session.runtime.run_id)
        by_id = session.graph.by_id
        for task_id, record in sorted(completed.items()):
            task = by_id.get(task_id)
            if task is None:
                raise ExecutionError("durable output names an unknown adaptive task")
            if task.effect.kind.writes:
                output = record.output
                if not isinstance(output, dict):
                    raise ExecutionError("durable effect output is malformed")
                intent_id = output.get("effect_intent_id")
                if not isinstance(intent_id, str):
                    raise ExecutionError("durable effect output has no intent")
                intent = self.effect_broker.get(intent_id)
                expected_idempotency_key = scoped_effect_idempotency_key(
                    run_id=session.runtime.run_id,
                    task_id=task_id,
                    attempt=1,
                    declared_key=task.effect.idempotency_key,
                )
                if (
                    intent.run_id != session.runtime.run_id
                    or intent.action != task_id
                    or intent.resource != task.effect.resource
                    or intent.idempotency_key != expected_idempotency_key
                    or intent.payload.get("declared_idempotency_key")
                    != task.effect.idempotency_key
                    or output.get("declared_idempotency_key")
                    != task.effect.idempotency_key
                    or output.get("executed_externally") is not False
                ):
                    raise ExecutionError("durable effect output violated its exact scope")
                continue
            if await self._output_validator(task, record.output) is not True:
                raise ExecutionError("durable adaptive output failed revalidation")

    async def _wait_for(self, signal: asyncio.Event, cancellation: asyncio.Event) -> None:
        if signal.is_set() or cancellation.is_set():
            return
        signal_wait = asyncio.create_task(signal.wait())
        cancel_wait = asyncio.create_task(cancellation.wait())
        try:
            await asyncio.wait(
                {signal_wait, cancel_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (signal_wait, cancel_wait):
                if not task.done():
                    task.cancel()
            await asyncio.gather(signal_wait, cancel_wait, return_exceptions=True)

    def _terminal_result(self, session: _AdaptiveSession) -> ExecutionResult:
        runtime = session.runtime
        result = runtime.result()
        status = result.state.status
        if status is AdaptiveStatus.CANCELLED:
            self.store.append_event(
                run_id=runtime.run_id,
                event_id=f"{runtime.run_id}:run_cancelled",
                event_type="run.cancelled",
                payload={"reason": "adaptive cancellation stopped future dispatch"},
            )
            raise ExecutionError("adaptive run was cancelled")
        if status is AdaptiveStatus.REFUSED:
            self.store.append_event(
                run_id=runtime.run_id,
                event_id=f"{runtime.run_id}:run_failed",
                event_type="run.failed",
                payload={"reason": "adaptive controller refused the residual plan"},
            )
            raise ExecutionError("adaptive controller refused the residual plan")
        if status is not AdaptiveStatus.COMPLETED:
            raise ExecutionError("adaptive controller stopped without a terminal state")

        pending_effects: list[str] = []
        for task in session.graph.tasks:
            if not task.effect.kind.writes or task.task_id not in result.outputs:
                continue
            output = result.outputs[task.task_id]
            if not isinstance(output, dict) or not isinstance(
                output.get("effect_intent_id"), str
            ):
                raise ExecutionError("adaptive effect output is malformed")
            intent = self.effect_broker.get(str(output["effect_intent_id"]))
            if intent.state not in {EffectState.COMMITTED, EffectState.COMPENSATED}:
                pending_effects.append(task.task_id)

        if pending_effects:
            run_state = RunState.AWAITING_EFFECTS
            event_type = "run.awaiting_effects"
            event_id = f"{runtime.run_id}:run_awaiting_effects"
            payload: dict[str, object] = {
                "pending_effect_task_ids": sorted(pending_effects),
                "executed_externally": False,
            }
        else:
            run_state = RunState.COMPLETED
            event_type = "run.completed"
            event_id = f"{runtime.run_id}:run_completed"
            payload = {"skipped_task_ids": list(result.state.shed_task_ids)}
        self.store.append_event(
            run_id=runtime.run_id,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
        return ExecutionResult(
            run_id=runtime.run_id,
            outputs=result.outputs,
            actual_usage=result.state.settled_usage,
            events=self.store.events(runtime.run_id),
            resumed_task_ids=result.resumed_task_ids,
            run_state=run_state,
            skipped_task_ids=tuple(
                sorted(set(result.state.shed_task_ids) | set(result.state.unknown_task_ids))
            ),
        )

    async def _drive(self, session: _AdaptiveSession) -> ExecutionResult:
        try:
            await self._validate_durable_outputs(session)
            await self._wait_for(session.start_gate, session.cancellation_event)
            while session.runtime.state.status is AdaptiveStatus.RUNNING:
                if session.cancellation_event.is_set():
                    async with session.lock:
                        if session.runtime.state.status is AdaptiveStatus.RUNNING:
                            session.runtime.cancel(
                                "operator request",
                                occurred_at_ms=session.runtime.state.now_ms,
                            )
                    break
                async with session.lock:
                    task_id = await asyncio.to_thread(
                        session.runtime.dispatch_next,
                        occurred_at_ms=session.runtime.state.now_ms,
                    )
                if task_id is not None:
                    await asyncio.sleep(0)
                    continue
                session.changed.clear()
                await self._wait_for(session.changed, session.cancellation_event)
            return self._terminal_result(session)
        finally:
            if session.runtime.state.status is not AdaptiveStatus.RUNNING:
                self._retire_session(session)

    async def execute(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        cancellation_event: asyncio.Event | None = None,
    ) -> ExecutionResult:
        paused = self._start_paused.pop(run_id, False)
        try:
            self._preflight(graph, envelope)
        except AdaptiveInvariantError as exc:
            raise AdaptiveControlServiceError(
                422,
                "admission_refused",
                str(exc),
            ) from exc
        cancellation = cancellation_event or asyncio.Event()
        session = self._new_session(
            graph,
            envelope,
            run_id=run_id,
            cancellation_event=cancellation,
            paused=paused,
        )
        self._persist_run_start(
            graph,
            envelope,
            run_id=run_id,
            start_paused=paused,
        )
        try:
            return await self._drive(session)
        except SimulatedAdaptiveCrash:
            raise
        except ExecutionError:
            raise
        except Exception as exc:
            if session.runtime.state.status in {
                AdaptiveStatus.CANCELLED,
                AdaptiveStatus.REFUSED,
            }:
                return self._terminal_result(session)
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:run_failed",
                event_type="run.failed",
                payload={
                    "reason": "adaptive execution failed",
                    "error_type": type(exc).__name__,
                },
            )
            raise ExecutionError("adaptive execution failed") from exc

    async def resume_existing(
        self,
        run_id: str,
        *,
        cancellation_event: asyncio.Event,
    ) -> ExecutionResult:
        session = self._sessions.get(run_id)
        if session is None:
            graph, envelope = self._compiled_run(run_id)
            session = self._new_session(
                graph,
                envelope,
                run_id=run_id,
                cancellation_event=cancellation_event,
                paused=False,
            )
        else:
            session.cancellation_event = cancellation_event
            session.start_gate.set()
        return await self._drive(session)

    def adaptive_replay(self, run_id: str) -> dict[str, object]:
        graph, envelope = self._compiled_run(run_id)
        records = tuple(
            event.payload
            for event in self.store.events(run_id)
            if event.event_type == "adaptive.controller_transition"
        )
        report = replay_adaptive_records(
            graph,
            envelope,
            run_id=run_id,
            records=records,
        )
        return {
            "schema_version": "finite-adaptive-replay-response/v1",
            "run_id": run_id,
            "passed": report.passed,
            "record_count": report.record_count,
            "control_digest": report.control_digest,
            "worker_or_provider_calls": 0,
            "final_state": (
                report.final_state.as_dict() if report.final_state is not None else None
            ),
            "violations": [
                {"index": item.index, "code": item.code, "detail": item.detail}
                for item in report.violations
            ],
        }

    async def apply_adaptive_control(
        self,
        run_id: str,
        *,
        kind: str,
        expected_revision: int,
        occurred_at_ms: int,
        details: Mapping[str, object],
    ) -> dict[str, object]:
        if kind == "coordinator.recover":
            graph, envelope = self._compiled_run(run_id)
            session = self._new_session(
                graph,
                envelope,
                run_id=run_id,
                cancellation_event=asyncio.Event(),
                paused=False,
            )
        else:
            session = self._sessions.get(run_id)
            if session is None:
                graph, envelope = self._compiled_run(run_id)
                session = self._new_session(
                    graph,
                    envelope,
                    run_id=run_id,
                    cancellation_event=asyncio.Event(),
                    paused=True,
                )

        async with session.lock:
            runtime = session.runtime
            if expected_revision != runtime.state.revision:
                raise AdaptiveControlServiceError(
                    409,
                    "stale_adaptive_revision",
                    "expected_revision does not match the durable controller revision",
                )
            if (
                kind == "coordinator.recover"
                and runtime.state.status is not AdaptiveStatus.RUNNING
            ):
                raise AdaptiveControlServiceError(
                    409,
                    "run_terminal",
                    "a terminal adaptive run cannot be coordinator-recovered",
                )
            if not runtime.state.now_ms <= occurred_at_ms <= session.envelope.deadline_ms:
                raise AdaptiveControlServiceError(
                    422,
                    "invalid_control_time",
                    "occurred_at_ms must be monotonic and within the run deadline",
                )
            providers = self._providers(session.graph)
            provider = details.get("provider")
            if provider is not None and provider not in providers:
                raise AdaptiveControlServiceError(
                    422,
                    "unknown_provider",
                    "the control event names an undeclared provider",
                )

            decision: AdaptiveDecision | None
            try:
                if kind == "provider.429":
                    reset_at_ms = int(details["reset_at_ms"])
                    if not occurred_at_ms < reset_at_ms <= session.envelope.deadline_ms:
                        raise AdaptiveControlServiceError(
                            422,
                            "invalid_reset_window",
                            "reset_at_ms must be later than the event and within the deadline",
                        )
                    decision = runtime.provider_429(
                        str(provider),
                        occurred_at_ms=occurred_at_ms,
                        reset_at_ms=reset_at_ms,
                    )
                elif kind == "provider.reset":
                    decision = runtime.provider_reset(
                        str(provider),
                        occurred_at_ms=occurred_at_ms,
                    )
                elif kind == "provider.capacity":
                    capacity = int(details["capacity"])
                    declared_limit = session.envelope.provider_limit(str(provider))
                    if not 0 <= capacity <= declared_limit:
                        raise AdaptiveControlServiceError(
                            422,
                            "capacity_out_of_bounds",
                            "capacity must stay within the declared provider limit",
                        )
                    decision = runtime.provider_capacity(
                        str(provider),
                        capacity,
                        occurred_at_ms=occurred_at_ms,
                    )
                elif kind == "budget.cut":
                    decision = runtime.cut_budget(
                        Usage(
                            tokens=int(details["tokens"]),
                            cost_microusd=int(details["cost_microusd"]),
                            context_bytes=int(details["context_bytes"]),
                        ),
                        occurred_at_ms=occurred_at_ms,
                    )
                elif kind == "coordinator.recover":
                    decision = runtime.recover_unknown_inflight(
                        occurred_at_ms=occurred_at_ms
                    )
                elif kind == "runtime.resume":
                    if session.start_gate.is_set():
                        raise AdaptiveControlServiceError(
                            409,
                            "run_not_paused",
                            "the adaptive run is not paused",
                        )
                    self.store.append_event(
                        run_id=run_id,
                        event_id=f"{run_id}:control:runtime_resumed:{expected_revision}",
                        event_type="control.runtime_resumed",
                        payload={"controller_revision": expected_revision},
                    )
                    session.start_gate.set()
                    decision = None
                else:  # pragma: no cover - strict HTTP parser owns the allowlist
                    raise AdaptiveControlServiceError(
                        422,
                        "unsupported_control_event",
                        "the adaptive control event kind is unsupported",
                    )
            except AdaptiveControlServiceError:
                raise
            except (AdaptiveInvariantError, KeyError, TypeError, ValueError) as exc:
                message = str(exc)
                conflict = "terminal" in message or "backwards" in message
                raise AdaptiveControlServiceError(
                    409 if conflict else 422,
                    "adaptive_control_conflict" if conflict else "invalid_control_event",
                    "the adaptive controller rejected the bounded control event",
                ) from exc

            replay = self.adaptive_replay(run_id)
            if replay["passed"] is not True:
                if runtime.state.status is not AdaptiveStatus.RUNNING:
                    self._retire_session(session)
                raise AdaptiveControlServiceError(
                    409,
                    "adaptive_replay_failed",
                    "the persisted controller history failed call-free replay",
                )
            session.changed.set()
            response = {
                "schema_version": "finite-adaptive-control-response/v1",
                "run_id": run_id,
                "kind": kind,
                "decision": decision.as_dict() if decision is not None else None,
                "state": runtime.state.as_dict(),
                "replay": replay,
                "external_effects_committed": 0,
            }
            if runtime.state.status is not AdaptiveStatus.RUNNING:
                self._retire_session(session)
            return response


def _environment_boolean(values: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be one of: 0, 1, false, no, true, yes")


def _environment_positive_integer(
    values: Mapping[str, str],
    name: str,
    *,
    default: int,
) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError(f"{name} must be a positive base-10 integer")
    value = int(text)
    if not 1 <= value <= 1_000_000:
        raise ValueError(f"{name} must be from 1 through 1000000")
    return value


def build_control_service(
    state_directory: str | Path,
    *,
    bearer_token: str | None,
    allow_anonymous_status_stream: bool = False,
    allowed_origins: tuple[str, ...] = (),
    trusted_approval_keys: Mapping[str, bytes] | None = None,
    max_active_runs: int = 32,
    max_control_events_per_run: int = 128,
) -> ControlPlane:
    """Build one process-local control service over durable shared SQLite state."""

    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    store = SQLiteRunStore(state / "runs.sqlite3")
    broker = SQLiteEffectBroker(
        state / "effects.sqlite3",
        broker_id="finite-control-service",
        trusted_approval_keys=trusted_approval_keys,
    )
    stormshift = StormShiftRuntime(store, broker)
    runtime = AdaptiveControlRuntime(
        store,
        broker,
        workers=stormshift.fixture_workers.workers,
        output_validator=stormshift.fixture_workers.validate_output,
    )
    reference = compile_contracts(miami_eoc_graph(), stormshift_envelope())
    return ControlPlane(
        runtime,
        effect_broker=broker,
        bearer_token=bearer_token,
        allow_anonymous_status_stream=allow_anonymous_status_stream,
        allowed_origins=allowed_origins,
        reference_workflows={"stormshift": reference.to_python()},
        max_active_runs=max_active_runs,
        max_control_events_per_run=max_control_events_per_run,
    )


def build_control_service_from_environment(
    environment: Mapping[str, str] | None = None,
) -> ControlPlane:
    """Build from explicit environment without logging or serializing the token."""

    values = environment if environment is not None else os.environ
    token = values.get("FINITE_CONTROL_BEARER_TOKEN", "").strip() or None
    anonymous = _environment_boolean(
        values,
        "FINITE_ALLOW_ANONYMOUS_STATUS_STREAM",
        default=False,
    )
    raw_origins = values.get("FINITE_CONTROL_ALLOWED_ORIGINS", "")
    origins = tuple(sorted({item.strip() for item in raw_origins.split(",") if item.strip()}))
    max_active_runs = _environment_positive_integer(
        values,
        "FINITE_MAX_ACTIVE_RUNS",
        default=32,
    )
    max_control_events = _environment_positive_integer(
        values,
        "FINITE_MAX_CONTROL_EVENTS_PER_RUN",
        default=128,
    )
    return build_control_service(
        default_state_directory(values),
        bearer_token=token,
        allow_anonymous_status_stream=anonymous,
        allowed_origins=origins,
        max_active_runs=max_active_runs,
        max_control_events_per_run=max_control_events,
    )


def main() -> None:
    """Run the ASGI service with uvicorn; refuse an unauthenticated network bind."""

    parser = argparse.ArgumentParser(description="Run the FINITE REST/SSE control plane")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65_535:
        parser.error("--port must be from 1 through 65535")
    service = build_control_service_from_environment()
    loopback = arguments.host in {"127.0.0.1", "::1", "localhost"}
    if not service.authentication_enabled and not loopback:
        parser.error("FINITE_CONTROL_BEARER_TOKEN is required when binding beyond loopback")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on optional API extra
        raise RuntimeError('Install the API service with: pip install -e ".[api]"') from exc
    uvicorn.run(service, host=arguments.host, port=arguments.port, log_level="info")


__all__ = [
    "AdaptiveControlRuntime",
    "AdaptiveControlServiceError",
    "_environment_positive_integer",
    "build_control_service",
    "build_control_service_from_environment",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    main()
