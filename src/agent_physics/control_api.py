"""Dependency-light ASGI control plane for durable FINITE executions.

The API delegates execution and effect transitions to the existing runtime
objects.  It never calls fixture workers directly, commits an effect, or writes
terminal run records on the executor's behalf. It can enforce a digest-only
bearer credential before request parsing; TLS, admission rate limiting, identity
federation, and distributed execution leases remain deployment responsibilities.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast
from urllib.parse import parse_qsl, urlsplit

from .contracts import EffectClass, RunEnvelope
from .effects import (
    ApprovalGrant,
    ApprovalRequired,
    EffectIntent,
    EffectKernelError,
    EffectState,
    IntentNotFound,
    InvalidApproval,
    InvalidTransition,
    SQLiteEffectBroker,
    StaleFence,
)
from .executor import ExecutionError, ExecutionResult
from .graph import ExecutionGraph, GraphValidationError
from .run_store import (
    SCHEMA_VERSION,
    EventConflict,
    RunDefinition,
    RunEvent,
    RunNotFound,
    SQLiteRunStore,
)
from .workflow_ir import CompiledWorkflow, WorkflowIRValidationError, compile_python


ASGIMessage = dict[str, Any]
ASGIScope = Mapping[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]

_IDENTIFIER: Final[str] = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_RUN_ROUTE = re.compile(rf"/v1/runs/(?P<run_id>{_IDENTIFIER})\Z")
_STATUS_ROUTE = re.compile(rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/status\Z")
_INSPECT_ROUTE = re.compile(rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/inspect\Z")
_EVENTS_ROUTE = re.compile(rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/events\Z")
_CANCEL_ROUTE = re.compile(rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/cancel\Z")
_CONTROL_EVENTS_ROUTE = re.compile(
    rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/control-events\Z"
)
_ADAPTIVE_REPLAY_ROUTE = re.compile(
    rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/adaptive-replay\Z"
)
_APPROVE_ROUTE = re.compile(
    rf"/v1/runs/(?P<run_id>{_IDENTIFIER})/effects/"
    rf"(?P<intent_id>{_IDENTIFIER})/approve\Z"
)
_REFERENCE_WORKFLOWS_ROUTE: Final[str] = "/v1/reference-workflows"
_REFERENCE_WORKFLOW_ROUTE = re.compile(rf"/v1/reference-workflows/(?P<workflow_id>{_IDENTIFIER})\Z")
_LIVENESS_ROUTE: Final[str] = "/healthz"
_READINESS_ROUTE: Final[str] = "/readyz"
_TERMINAL_EVENT_STATES: Final[dict[str, str]] = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.awaiting_effects": "awaiting_effects",
}
_SSE_TERMINAL_STATES: Final[frozenset[str]] = frozenset(_TERMINAL_EVENT_STATES.values())
_MAX_SQLITE_INTEGER: Final[int] = 9_223_372_036_854_775_807
_ADAPTIVE_CONTROL_DETAIL_FIELDS: Final[dict[str, frozenset[str]]] = {
    "provider.429": frozenset({"provider", "reset_at_ms"}),
    "provider.reset": frozenset({"provider"}),
    "provider.capacity": frozenset({"provider", "capacity"}),
    "budget.cut": frozenset({"tokens", "cost_microusd", "context_bytes"}),
    "coordinator.recover": frozenset(),
    "runtime.resume": frozenset(),
}


class ExecutionRuntime(Protocol):
    """The narrow executor contract consumed by :class:`ControlPlane`."""

    store: SQLiteRunStore

    async def execute(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        *,
        run_id: str,
        cancellation_event: asyncio.Event | None = None,
    ) -> ExecutionResult: ...


class ControlAPIError(RuntimeError):
    """A safe, typed failure that can cross the HTTP boundary."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    body: Mapping[str, object]
    headers: tuple[tuple[bytes, bytes], ...] = ()


@dataclass(slots=True)
class _ActiveExecution:
    cancellation: asyncio.Event
    task: asyncio.Task[ExecutionResult]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON field {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _object(
    value: object,
    *,
    path: str,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlAPIError(400, "invalid_request", f"{path} must be an object")
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ControlAPIError(
            400,
            "unknown_field",
            f"{path} contains unknown fields: {unknown}",
        )
    if missing:
        raise ControlAPIError(
            400,
            "missing_field",
            f"{path} is missing required fields: {missing}",
        )
    return cast(dict[str, Any], value)


def _string(value: object, *, path: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ControlAPIError(
            400,
            "invalid_request",
            f"{path} must be a non-empty string of at most {maximum} characters",
        )
    return value


def _integer(
    value: object,
    *,
    path: str,
    minimum: int | None = None,
    maximum: int = _MAX_SQLITE_INTEGER,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ControlAPIError(400, "invalid_request", f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ControlAPIError(
            400,
            "invalid_request",
            f"{path} must be at least {minimum}",
        )
    if value > maximum:
        raise ControlAPIError(
            400,
            "invalid_request",
            f"{path} must be at most {maximum}",
        )
    return value


def _usage(value: object) -> dict[str, int]:
    usage = cast(Any, value)
    return {
        "tokens": usage.tokens,
        "cost_microusd": usage.cost_microusd,
        "context_bytes": usage.context_bytes,
    }


def _event(event: RunEvent) -> dict[str, object]:
    return {
        "id": str(event.sequence),
        "sequence": event.sequence,
        "event_id": event.event_id,
        "type": event.event_type,
        "task_id": event.task_id,
        "attempt": event.attempt,
        "occurred_at_ms": event.occurred_at_ms,
        "payload": event.payload,
        "usage": {
            "estimated": _usage(event.usage.estimated),
            "reserved": _usage(event.usage.reserved),
            "actual": _usage(event.usage.actual),
        },
    }


def _effect(intent: EffectIntent) -> dict[str, object]:
    return {
        "intent_id": intent.intent_id,
        "run_id": intent.run_id,
        "action": intent.action,
        "resource": intent.resource,
        "effect_class": intent.effect_class.value,
        "idempotency_key": intent.idempotency_key,
        "payload": intent.payload,
        "compensation_action": intent.compensation_action,
        "state": intent.state.value,
        "effect_digest": intent.effect_digest,
        "fence_version": intent.fence_version,
        "fence_owner": intent.fence_owner,
        "approval_grant_id": intent.approval_grant_id,
        "last_error": intent.last_error,
        "created_at_ms": intent.created_at_ms,
        "updated_at_ms": intent.updated_at_ms,
    }


def _run_state(events: tuple[RunEvent, ...], active: _ActiveExecution | None) -> str:
    for event in reversed(events):
        state = _TERMINAL_EVENT_STATES.get(event.event_type)
        if state is not None:
            return state
    if active is not None and active.cancellation.is_set():
        return "cancelling"
    return "running"


class ControlPlane:
    """Typed programmatic API and dependency-free ASGI application.

    A single instance coordinates locally active tasks while all definitions,
    outputs, control requests, and events remain in the supplied durable store.
    Run execution itself is always performed by ``runtime.execute``.
    """

    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        effect_broker: SQLiteEffectBroker | None = None,
        max_body_bytes: int = 1_048_576,
        event_poll_seconds: float = 0.05,
        sse_heartbeat_seconds: float = 15.0,
        run_id_factory: Callable[[], str] | None = None,
        bearer_token: str | None = None,
        allow_anonymous_status_stream: bool = False,
        allowed_origins: tuple[str, ...] = (),
        reference_workflows: Mapping[str, Mapping[str, Any]] | None = None,
        max_active_runs: int = 32,
        max_control_events_per_run: int = 128,
    ) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        if event_poll_seconds <= 0 or sse_heartbeat_seconds <= 0:
            raise ValueError("SSE timing values must be positive")
        if not isinstance(allow_anonymous_status_stream, bool):
            raise ValueError("allow_anonymous_status_stream must be a boolean")
        if (
            type(max_active_runs) is not int
            or not 1 <= max_active_runs <= 1_000_000
        ):
            raise ValueError("max_active_runs must be an integer from 1 through 1000000")
        if (
            type(max_control_events_per_run) is not int
            or not 1 <= max_control_events_per_run <= 1_000_000
        ):
            raise ValueError(
                "max_control_events_per_run must be an integer from 1 through 1000000"
            )
        if type(allowed_origins) is not tuple:
            raise ValueError("allowed_origins must be a tuple")
        normalized_origins: list[str] = []
        for origin in allowed_origins:
            if type(origin) is not str:
                raise ValueError("allowed origins must be strings")
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or origin != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError("allowed origins must be exact HTTP(S) origins")
            normalized_origins.append(origin)
        if tuple(sorted(set(normalized_origins))) != allowed_origins:
            raise ValueError("allowed_origins must be sorted and unique")
        if bearer_token is not None:
            if (
                type(bearer_token) is not str
                or not 32 <= len(bearer_token) <= 1_024
                or any(not 33 <= ord(character) <= 126 for character in bearer_token)
            ):
                raise ValueError(
                    "bearer_token must contain 32-1024 visible ASCII characters"
                )
        self.runtime = runtime
        self.store = runtime.store
        self.effect_broker = effect_broker
        self.max_body_bytes = max_body_bytes
        self.event_poll_seconds = event_poll_seconds
        self.sse_heartbeat_seconds = sse_heartbeat_seconds
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._bearer_token_digest = (
            hashlib.sha256(bearer_token.encode("utf-8")).digest()
            if bearer_token is not None
            else None
        )
        self.allow_anonymous_status_stream = allow_anonymous_status_stream
        self.allowed_origins = allowed_origins
        self.max_active_runs = max_active_runs
        self.max_control_events_per_run = max_control_events_per_run
        # A recovery mutates durable controller state before it can create the
        # replacement execution task. Reserve that slot across the await so a
        # burst of recover/submit requests cannot bypass max_active_runs.
        self._pending_recoveries = 0
        # Reserve a per-run control slot before awaiting the runtime reducer.
        # Durable accepted markers alone are insufficient because concurrent
        # requests can queue behind the session lock after observing the same
        # count.
        self._pending_control_events: dict[str, int] = {}
        compiled_references: dict[str, dict[str, object]] = {}
        for workflow_id, document in sorted((reference_workflows or {}).items()):
            self._validate_identifier(workflow_id, path="reference workflow ID")
            try:
                compiled = compile_python(document)
            except WorkflowIRValidationError as exc:
                raise ValueError(f"reference workflow {workflow_id!r} is invalid: {exc}") from exc
            compiled_references[workflow_id] = {
                "workflow_id": workflow_id,
                "workflow_digest": compiled.digest,
                "schema_version": compiled.schema_version,
                "workflow": compiled.to_python(),
                "execution_boundary": (
                    "submitting starts the configured bounded runtime; declared writes stop "
                    "at durable approval and are never committed by this API"
                ),
            }
        self._reference_workflows = compiled_references
        self._active: dict[str, _ActiveExecution] = {}

    @property
    def authentication_enabled(self) -> bool:
        """Whether this instance enforces a configured bearer credential."""

        return self._bearer_token_digest is not None

    def health(self) -> dict[str, object]:
        """Return a deliberately minimal process-liveness response."""

        return {
            "schema_version": "finite-control-health/v1",
            "service": "finite-control-plane",
            "status": "ok",
        }

    def readiness(self) -> dict[str, object]:
        """Fail closed unless every configured durable dependency is readable."""

        try:
            observed_versions = self.store.schema_versions()
            expected_versions = tuple(range(1, SCHEMA_VERSION + 1))
            if observed_versions != expected_versions:
                raise RuntimeError("run-store schema is incomplete")
            effect_status = "not_configured"
            if self.effect_broker is not None:
                # A bounded outbox read exercises the broker connection and schema
                # without mutating durable state or revealing an intent count.
                self.effect_broker.pending_outbox(limit=1)
                effect_status = "ok"
        except Exception as exc:
            raise ControlAPIError(
                503,
                "not_ready",
                "the control plane is not ready",
            ) from exc
        return {
            "schema_version": "finite-control-readiness/v1",
            "service": "finite-control-plane",
            "status": "ready",
            "checks": {
                "run_store": "ok",
                "effect_broker": effect_status,
            },
        }

    @staticmethod
    def _validate_identifier(value: str, *, path: str) -> str:
        if re.fullmatch(_IDENTIFIER, value) is None:
            raise ControlAPIError(
                400,
                "invalid_identifier",
                f"{path} must match {_IDENTIFIER!r}",
            )
        return value

    def _definition(self, run_id: str) -> RunDefinition:
        try:
            return self.store.get_run(run_id)
        except RunNotFound as exc:
            raise ControlAPIError(404, "run_not_found", f"run {run_id!r} was not found") from exc

    def _events(self, run_id: str) -> tuple[RunEvent, ...]:
        self._definition(run_id)
        return self.store.events(run_id)

    @staticmethod
    def _consume_execution(task: asyncio.Task[ExecutionResult]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass

    def _execution_done(self, run_id: str, task: asyncio.Task[ExecutionResult]) -> None:
        active = self._active.get(run_id)
        if active is not None and active.task is task:
            del self._active[run_id]
        self._consume_execution(task)

    def _discard_finished_executions(self) -> None:
        for active_run_id, active in tuple(self._active.items()):
            if active.task.done():
                self._execution_done(active_run_id, active.task)

    async def submit(
        self,
        workflow: Mapping[str, Any],
        *,
        run_id: str | None = None,
        start_paused: bool = False,
    ) -> dict[str, object]:
        """Validate and start a new run without bypassing runtime admission."""

        selected_id = run_id or self._run_id_factory()
        self._validate_identifier(selected_id, path="run_id")
        if selected_id in self._active:
            raise ControlAPIError(409, "run_active", f"run {selected_id!r} is already active")
        self._discard_finished_executions()
        if len(self._active) + self._pending_recoveries >= self.max_active_runs:
            raise ControlAPIError(
                429,
                "active_run_limit",
                "the process-local active-run limit has been reached",
            )
        try:
            compiled: CompiledWorkflow = compile_python(workflow)
        except WorkflowIRValidationError as exc:
            raise ControlAPIError(422, "invalid_workflow", str(exc)) from exc
        try:
            self.store.get_run(selected_id)
        except RunNotFound:
            pass
        else:
            raise ControlAPIError(409, "run_exists", f"run {selected_id!r} already exists")

        if type(start_paused) is not bool:
            raise ControlAPIError(400, "invalid_request", "start_paused must be a boolean")
        configure_start = getattr(self.runtime, "configure_start", None)
        if start_paused and not callable(configure_start):
            raise ControlAPIError(
                422,
                "adaptive_runtime_unavailable",
                "start_paused requires the adaptive control runtime",
            )
        if callable(configure_start):
            configure_start(selected_id, paused=start_paused)

        cancellation = asyncio.Event()
        task = asyncio.create_task(
            self.runtime.execute(
                compiled.graph,
                compiled.envelope,
                run_id=selected_id,
                cancellation_event=cancellation,
            ),
            name=f"finite-control:{selected_id}",
        )
        self._active[selected_id] = _ActiveExecution(cancellation, task)
        task.add_done_callback(lambda completed: self._execution_done(selected_id, completed))

        # Runtime admission and durable run creation occur before its first
        # worker await. One scheduling turn lets synchronous admission failures
        # become a 422 instead of a phantom accepted run.
        await asyncio.sleep(0)
        if task.done():
            try:
                task.result()
            except (ExecutionError, GraphValidationError, ValueError) as exc:
                try:
                    self.store.get_run(selected_id)
                except RunNotFound:
                    raise ControlAPIError(422, "admission_refused", str(exc)) from exc
            except Exception as exc:
                safe_status = getattr(exc, "control_status", None)
                safe_code = getattr(exc, "control_code", None)
                safe_message = getattr(exc, "control_message", None)
                if (
                    type(safe_status) is int
                    and safe_status == 422
                    and isinstance(safe_code, str)
                    and isinstance(safe_message, str)
                ):
                    try:
                        self.store.get_run(selected_id)
                    except RunNotFound:
                        raise ControlAPIError(
                            safe_status,
                            safe_code,
                            safe_message,
                        ) from exc
                raise

        try:
            run_status = self.status(selected_id)
        except ControlAPIError as exc:
            if exc.code != "run_not_found":
                raise
            # An executor may yield before materializing the definition. The
            # active registry is explicit about this short-lived state.
            run_status = {
                "run_id": selected_id,
                "state": "admitting",
                "event_count": 0,
                "last_event_id": None,
            }
        return {
            "run": run_status,
            "workflow_digest": compiled.digest,
        }

    def status(self, run_id: str) -> dict[str, object]:
        """Return the state reconstructed from immutable run events."""

        self._validate_identifier(run_id, path="run_id")
        definition = self._definition(run_id)
        events = self.store.events(run_id)
        active = self._active.get(run_id)
        return {
            "run_id": run_id,
            "state": _run_state(events, active),
            "created_at_ms": definition.created_at_ms,
            "deadline_at_ms": definition.deadline_at_ms,
            "event_count": len(events),
            "last_event_id": str(events[-1].sequence) if events else None,
        }

    def inspect(self, run_id: str) -> dict[str, object]:
        """Return durable definition, outputs, usage totals, and effect scopes."""

        self._validate_identifier(run_id, path="run_id")
        definition = self._definition(run_id)
        events = self.store.events(run_id)
        completed = self.store.completed_tasks(run_id)
        actual = {"tokens": 0, "cost_microusd": 0, "context_bytes": 0}
        for event in events:
            if event.event_type in {
                "task.attempt_failed",
                "task.attempt_succeeded",
                "task.attempt_cancelled",
            }:
                actual["tokens"] += event.usage.actual.tokens
                actual["cost_microusd"] += event.usage.actual.cost_microusd
                actual["context_bytes"] += event.usage.actual.context_bytes

        intents: list[dict[str, object]] = []
        if self.effect_broker is not None:
            for task_id in sorted(completed):
                output = completed[task_id].output
                if not isinstance(output, dict):
                    continue
                intent_id = output.get("effect_intent_id")
                if not isinstance(intent_id, str):
                    continue
                try:
                    intent = self.effect_broker.get(intent_id)
                except IntentNotFound as exc:
                    raise ControlAPIError(
                        500,
                        "durable_invariant_failed",
                        f"effect intent {intent_id!r} is unavailable",
                    ) from exc
                if intent.run_id != run_id:
                    raise ControlAPIError(
                        500,
                        "durable_invariant_failed",
                        f"effect intent {intent_id!r} belongs to another run",
                    )
                if intent.action != task_id:
                    raise ControlAPIError(
                        500,
                        "durable_invariant_failed",
                        f"effect intent {intent_id!r} is bound to another task",
                    )
                intents.append(_effect(intent))

        adaptive_replay: dict[str, object] | None = None
        replay = getattr(self.runtime, "adaptive_replay", None)
        if callable(replay) and any(
            event.event_type == "adaptive.controller_transition" for event in events
        ):
            adaptive_replay = cast(dict[str, object], replay(run_id))

        return {
            "run": self.status(run_id),
            "definition": {
                "graph_digest": definition.graph_digest,
                "envelope": definition.envelope,
                "manifest_digest": definition.manifest_digest,
                "manifest_revision": definition.manifest_revision,
            },
            "outputs": {task_id: completed[task_id].output for task_id in sorted(completed)},
            "actual_usage": actual,
            "effects": intents,
            "adaptive_replay": adaptive_replay,
            "latest_event": _event(events[-1]) if events else None,
        }

    @staticmethod
    def _safe_adaptive_error(exc: Exception) -> ControlAPIError:
        status = getattr(exc, "control_status", None)
        code = getattr(exc, "control_code", None)
        message = getattr(exc, "control_message", None)
        if (
            type(status) is int
            and status in {404, 409, 422}
            and isinstance(code, str)
            and isinstance(message, str)
        ):
            return ControlAPIError(status, code, message)
        return ControlAPIError(
            409,
            "adaptive_control_rejected",
            "the adaptive runtime rejected the control operation",
        )

    async def adaptive_control(
        self,
        run_id: str,
        *,
        kind: str,
        expected_revision: int,
        occurred_at_ms: int,
        details: Mapping[str, object],
    ) -> dict[str, object]:
        """Apply one revision-fenced control fact through the durable reducer."""

        self._validate_identifier(run_id, path="run_id")
        self._definition(run_id)
        handler = getattr(self.runtime, "apply_adaptive_control", None)
        if not callable(handler):
            raise ControlAPIError(
                409,
                "adaptive_runtime_unavailable",
                "this run is not controlled by the adaptive runtime",
            )
        accepted_controls = sum(
            event.event_type == "control.adaptive_event_accepted"
            for event in self.store.events(run_id)
        )
        pending_controls = self._pending_control_events.get(run_id, 0)
        if accepted_controls + pending_controls >= self.max_control_events_per_run:
            raise ControlAPIError(
                429,
                "control_event_limit",
                "the durable per-run control-event limit has been reached",
            )
        self._pending_control_events[run_id] = pending_controls + 1

        recovery_reserved = False
        try:
            active = self._active.get(run_id)
            if kind == "coordinator.recover" and active is not None and not active.task.done():
                raise ControlAPIError(
                    409,
                    "coordinator_still_active",
                    "coordinator recovery requires an orphaned durable run",
                )
            if kind == "coordinator.recover":
                self._discard_finished_executions()
                if len(self._active) + self._pending_recoveries >= self.max_active_runs:
                    raise ControlAPIError(
                        429,
                        "active_run_limit",
                        "the process-local active-run limit has been reached",
                    )
                self._pending_recoveries += 1
                recovery_reserved = True
            try:
                result = await handler(
                    run_id,
                    kind=kind,
                    expected_revision=expected_revision,
                    occurred_at_ms=occurred_at_ms,
                    details=dict(details),
                )
            except Exception as exc:
                raise self._safe_adaptive_error(exc) from exc
            if not isinstance(result, dict):
                raise ControlAPIError(
                    409,
                    "adaptive_control_rejected",
                    "the adaptive runtime returned an invalid control response",
                )

            state = result.get("state")
            next_revision = state.get("revision") if isinstance(state, dict) else None
            marker_id = f"{run_id}:control:adaptive:{expected_revision}:{kind}"
            try:
                self.store.append_event(
                    run_id=run_id,
                    event_id=marker_id,
                    event_type="control.adaptive_event_accepted",
                    payload={
                        "kind": kind,
                        "expected_revision": expected_revision,
                        "next_revision": next_revision,
                        "control_payload_digest": hashlib.sha256(
                            _canonical_json(
                                {
                                    "kind": kind,
                                    "expected_revision": expected_revision,
                                    "occurred_at_ms": occurred_at_ms,
                                    "details": dict(details),
                                }
                            )
                        ).hexdigest(),
                    },
                )
            except EventConflict as exc:
                raise ControlAPIError(
                    409,
                    "duplicate_control_event",
                    "the control event identity was already consumed",
                ) from exc

            execution_resumed = False
            if kind == "coordinator.recover":
                resume = getattr(self.runtime, "resume_existing", None)
                if not callable(resume):
                    raise ControlAPIError(
                        409,
                        "adaptive_runtime_unavailable",
                        "the adaptive runtime cannot resume recovered execution",
                    )
                cancellation = asyncio.Event()
                task = asyncio.create_task(
                    resume(run_id, cancellation_event=cancellation),
                    name=f"finite-control:recovered:{run_id}",
                )
                self._active[run_id] = _ActiveExecution(cancellation, task)
                task.add_done_callback(lambda completed: self._execution_done(run_id, completed))
                execution_resumed = True
            return {**result, "execution_resumed": execution_resumed}
        finally:
            if recovery_reserved:
                self._pending_recoveries -= 1
            remaining_controls = self._pending_control_events[run_id] - 1
            if remaining_controls:
                self._pending_control_events[run_id] = remaining_controls
            else:
                del self._pending_control_events[run_id]

    def adaptive_replay(self, run_id: str) -> dict[str, object]:
        """Replay a controller history without calling a worker or provider."""

        self._validate_identifier(run_id, path="run_id")
        self._definition(run_id)
        handler = getattr(self.runtime, "adaptive_replay", None)
        if not callable(handler):
            raise ControlAPIError(
                409,
                "adaptive_runtime_unavailable",
                "this run has no adaptive controller history",
            )
        try:
            result = handler(run_id)
        except Exception as exc:
            raise self._safe_adaptive_error(exc) from exc
        if not isinstance(result, dict):
            raise ControlAPIError(
                409,
                "adaptive_replay_failed",
                "the adaptive runtime returned an invalid replay response",
            )
        return cast(dict[str, object], result)

    def events(self, run_id: str, *, after: int = 0) -> tuple[dict[str, object], ...]:
        """Read ordered durable events after a per-run sequence cursor."""

        self._validate_identifier(run_id, path="run_id")
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise ControlAPIError(400, "invalid_cursor", "event cursor must be non-negative")
        events = self._events(run_id)
        latest = events[-1].sequence if events else 0
        if after > latest:
            raise ControlAPIError(
                409,
                "cursor_ahead",
                f"event cursor {after} is ahead of latest event {latest}",
            )
        return tuple(_event(event) for event in events if event.sequence > after)

    async def cancel(self, run_id: str, *, reason: str = "operator request") -> dict[str, object]:
        """Durably request, then cooperatively signal, cancellation."""

        self._validate_identifier(run_id, path="run_id")
        reason = _string(reason, path="reason", maximum=512)
        events = self._events(run_id)
        state = _run_state(events, self._active.get(run_id))
        if state in _SSE_TERMINAL_STATES:
            raise ControlAPIError(409, "run_terminal", f"run is already {state}")
        active = self._active.get(run_id)
        if active is None:
            raise ControlAPIError(
                409,
                "executor_unavailable",
                "run is not controlled by this process; no cancellation signal was sent",
            )

        existing = next(
            (event for event in events if event.event_type == "control.cancel_requested"),
            None,
        )
        if existing is None:
            self.store.append_event(
                run_id=run_id,
                event_id=f"{run_id}:control:cancel_requested",
                event_type="control.cancel_requested",
                payload={"reason": reason},
            )
        active.cancellation.set()
        return {
            "run_id": run_id,
            "state": "cancelling",
            "cancellation_requested": True,
        }

    @staticmethod
    def _scope(value: object) -> dict[str, str]:
        scope = _object(
            value,
            path="scope",
            allowed=frozenset({"intent_id", "run_id", "effect_digest", "resource", "action"}),
            required=frozenset({"intent_id", "run_id", "effect_digest", "resource", "action"}),
        )
        return {
            field: _string(scope[field], path=f"scope.{field}", maximum=512)
            for field in ("intent_id", "run_id", "effect_digest", "resource", "action")
        }

    @staticmethod
    def _grant(value: object) -> ApprovalGrant:
        fields = frozenset(
            {
                "grant_id",
                "key_id",
                "principal",
                "intent_id",
                "run_id",
                "effect_digest",
                "resource",
                "action",
                "not_before_ms",
                "expires_at_ms",
                "signature",
            }
        )
        grant = _object(
            value,
            path="grant",
            allowed=fields,
            required=fields,
        )
        return ApprovalGrant(
            grant_id=_string(grant["grant_id"], path="grant.grant_id"),
            key_id=_string(grant["key_id"], path="grant.key_id"),
            principal=_string(grant["principal"], path="grant.principal"),
            intent_id=_string(grant["intent_id"], path="grant.intent_id"),
            run_id=_string(grant["run_id"], path="grant.run_id"),
            effect_digest=_string(grant["effect_digest"], path="grant.effect_digest"),
            resource=_string(grant["resource"], path="grant.resource"),
            action=_string(grant["action"], path="grant.action"),
            not_before_ms=_integer(grant["not_before_ms"], path="grant.not_before_ms", minimum=0),
            expires_at_ms=_integer(grant["expires_at_ms"], path="grant.expires_at_ms", minimum=1),
            signature=_string(grant["signature"], path="grant.signature", maximum=4096),
        )

    def approve(
        self,
        run_id: str,
        intent_id: str,
        *,
        scope: Mapping[str, object],
        grant: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Fence and approve one exact-scope intent; never commit it."""

        self._validate_identifier(run_id, path="run_id")
        self._validate_identifier(intent_id, path="intent_id")
        self._definition(run_id)
        run_events = self.store.events(run_id)
        state = _run_state(run_events, self._active.get(run_id))
        if state != "awaiting_effects":
            raise ControlAPIError(
                409,
                "run_not_awaiting_effects",
                f"run is {state}, not awaiting effect approval",
            )
        if self.effect_broker is None:
            raise ControlAPIError(503, "effect_broker_unavailable", "effect broker unavailable")
        try:
            intent = self.effect_broker.get(intent_id)
        except IntentNotFound as exc:
            raise ControlAPIError(
                404, "effect_not_found", f"effect intent {intent_id!r} was not found"
            ) from exc
        if intent.run_id != run_id:
            raise ControlAPIError(
                404, "effect_not_found", f"effect intent {intent_id!r} was not found in this run"
            )

        references: list[str] = []
        for task_id, completed in self.store.completed_tasks(run_id).items():
            output = completed.output
            if isinstance(output, dict) and output.get("effect_intent_id") == intent_id:
                references.append(task_id)
        if references != [intent.action]:
            raise ControlAPIError(
                404,
                "effect_not_found",
                f"effect intent {intent_id!r} is not bound to a durable run task",
            )

        supplied_scope = self._scope(dict(scope))
        exact_scope = {
            "intent_id": intent.intent_id,
            "run_id": intent.run_id,
            "effect_digest": intent.effect_digest,
            "resource": intent.resource,
            "action": intent.action,
        }
        if supplied_scope != exact_scope:
            raise ControlAPIError(
                403,
                "approval_scope_mismatch",
                "approval does not match the exact persisted effect scope",
            )

        parsed_grant = self._grant(dict(grant)) if grant is not None else None
        if parsed_grant is not None:
            grant_scope = {
                "intent_id": parsed_grant.intent_id,
                "run_id": parsed_grant.run_id,
                "effect_digest": parsed_grant.effect_digest,
                "resource": parsed_grant.resource,
                "action": parsed_grant.action,
            }
            if grant_scope != exact_scope:
                raise ControlAPIError(
                    403,
                    "approval_scope_mismatch",
                    "signed grant does not match the exact persisted effect scope",
                )
        if intent.effect_class is EffectClass.IRREVERSIBLE_WRITE and parsed_grant is None:
            raise ControlAPIError(
                403,
                "approval_grant_required",
                "irreversible effects require an authenticated exact-scope grant",
            )
        if intent.effect_class is not EffectClass.IRREVERSIBLE_WRITE and parsed_grant is not None:
            raise ControlAPIError(
                400,
                "unexpected_approval_grant",
                "non-irreversible effects use policy approval without a grant",
            )

        try:
            if intent.state is EffectState.PROPOSED:
                intent = self.effect_broker.prepare(intent_id)
            if intent.state is EffectState.PREPARED and (
                intent.fence_owner != self.effect_broker.broker_id
            ):
                raise ControlAPIError(
                    409,
                    "fence_owned_elsewhere",
                    "effect fence belongs to another broker",
                )
            approved = self.effect_broker.approve(
                intent_id,
                intent.fencing_token,
                parsed_grant,
            )
        except ControlAPIError:
            raise
        except (ApprovalRequired, InvalidApproval) as exc:
            raise ControlAPIError(403, "approval_rejected", str(exc)) from exc
        except (InvalidTransition, StaleFence) as exc:
            raise ControlAPIError(409, "effect_transition_conflict", str(exc)) from exc
        except EffectKernelError as exc:
            raise ControlAPIError(409, "effect_transition_conflict", str(exc)) from exc

        self.store.append_event(
            run_id=run_id,
            event_id=f"{run_id}:control:effect:{intent_id}:approved",
            event_type="control.effect_approved",
            payload={
                "intent_id": intent_id,
                "effect_digest": approved.effect_digest,
                "resource": approved.resource,
                "action": approved.action,
                "approval_grant_id": approved.approval_grant_id,
            },
        )
        return {"effect": _effect(approved), "executed_externally": False}

    @staticmethod
    def _headers(scope: ASGIScope) -> dict[bytes, list[bytes]]:
        result: dict[bytes, list[bytes]] = {}
        for raw_name, raw_value in scope.get("headers", []):
            name = bytes(raw_name).lower()
            result.setdefault(name, []).append(bytes(raw_value))
        return result

    def _cors_headers(self, scope: ASGIScope) -> tuple[tuple[bytes, bytes], ...]:
        origins = self._headers(scope).get(b"origin", [])
        if len(origins) > 1:
            raise ControlAPIError(400, "invalid_origin", "duplicate Origin headers")
        if not origins:
            return ()
        try:
            origin = origins[0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ControlAPIError(400, "invalid_origin", "Origin must be ASCII") from exc
        if self.allowed_origins and origin not in self.allowed_origins:
            raise ControlAPIError(403, "origin_not_allowed", "request origin is not allowed")
        if origin not in self.allowed_origins:
            return ()
        return (
            (b"access-control-allow-origin", origin.encode("ascii")),
            (b"access-control-expose-headers", b"Location"),
            (b"vary", b"Origin"),
        )

    async def _preflight(self, scope: ASGIScope, send: Send) -> None:
        cors = self._cors_headers(scope)
        if not cors:
            raise ControlAPIError(403, "origin_not_allowed", "CORS is not configured")
        headers = self._headers(scope)
        requested_methods = headers.get(b"access-control-request-method", [])
        if len(requested_methods) != 1:
            raise ControlAPIError(
                400,
                "invalid_preflight",
                "one Access-Control-Request-Method header is required",
            )
        try:
            requested_method = requested_methods[0].decode("ascii").upper()
        except UnicodeDecodeError as exc:
            raise ControlAPIError(400, "invalid_preflight", "requested method is invalid") from exc
        if requested_method not in {"GET", "POST"}:
            raise ControlAPIError(405, "method_not_allowed", "CORS permits GET and POST")
        requested_header_values = headers.get(b"access-control-request-headers", [])
        if len(requested_header_values) > 1:
            raise ControlAPIError(400, "invalid_preflight", "duplicate requested-header fields")
        requested_headers: set[str] = set()
        if requested_header_values:
            try:
                requested_headers = {
                    item.strip().lower()
                    for item in requested_header_values[0].decode("ascii").split(",")
                    if item.strip()
                }
            except UnicodeDecodeError as exc:
                raise ControlAPIError(
                    400, "invalid_preflight", "requested headers are invalid"
                ) from exc
        allowed_headers = {"authorization", "content-type", "last-event-id"}
        if not requested_headers <= allowed_headers:
            raise ControlAPIError(
                403,
                "header_not_allowed",
                "CORS requested a header outside the finite allowlist",
            )
        response_headers = (
            *cors,
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-allow-headers", b"Authorization, Content-Type, Last-Event-ID"),
            (b"access-control-max-age", b"600"),
            (b"content-length", b"0"),
        )
        await send({"type": "http.response.start", "status": 204, "headers": response_headers})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _authorize(self, scope: ASGIScope) -> None:
        """Authorize before parsing a body or exposing sensitive run material.

        When a token is configured, anonymous access can optionally be limited to
        status and event-stream reads. Submit, inspect, cancel, and approve always
        require the credential. Only a SHA-256 digest of the configured token is kept.
        """

        expected = self._bearer_token_digest
        method = str(scope.get("method", "")).upper()
        path = scope.get("path")
        if method == "GET" and path in {_LIVENESS_ROUTE, _READINESS_ROUTE}:
            return
        if expected is None:
            return
        public_status = isinstance(path, str) and (
            _RUN_ROUTE.fullmatch(path) is not None
            or _STATUS_ROUTE.fullmatch(path) is not None
            or _EVENTS_ROUTE.fullmatch(path) is not None
        )
        if self.allow_anonymous_status_stream and method == "GET" and public_status:
            return

        authorization = self._headers(scope).get(b"authorization", [])
        supplied_digest = b"\x00" * len(expected)
        valid_shape = False
        if len(authorization) == 1:
            try:
                value = authorization[0].decode("ascii")
            except UnicodeDecodeError:
                value = ""
            scheme, separator, credential = value.partition(" ")
            valid_shape = (
                separator == " "
                and scheme.lower() == "bearer"
                and bool(credential)
                and not any(character.isspace() for character in credential)
            )
            if valid_shape:
                supplied_digest = hashlib.sha256(credential.encode("ascii")).digest()
        if not (valid_shape and hmac.compare_digest(expected, supplied_digest)):
            raise ControlAPIError(401, "unauthorized", "a valid bearer credential is required")

    async def _read_json(self, scope: ASGIScope, receive: Receive) -> dict[str, Any]:
        headers = self._headers(scope)
        content_types = headers.get(b"content-type", [])
        if len(content_types) != 1:
            raise ControlAPIError(
                415, "unsupported_media_type", "exactly one Content-Type header is required"
            )
        try:
            content_type = content_types[0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ControlAPIError(
                415, "unsupported_media_type", "Content-Type must be ASCII"
            ) from exc
        parts = [part.strip().lower() for part in content_type.split(";")]
        if (
            not parts
            or parts[0] != "application/json"
            or any(part != "charset=utf-8" for part in parts[1:])
        ):
            raise ControlAPIError(
                415,
                "unsupported_media_type",
                "Content-Type must be application/json with optional charset=utf-8",
            )

        content_lengths = headers.get(b"content-length", [])
        declared_length: int | None = None
        if len(content_lengths) > 1:
            raise ControlAPIError(400, "invalid_request", "duplicate Content-Length headers")
        if content_lengths:
            try:
                declared_length = int(content_lengths[0].decode("ascii"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ControlAPIError(400, "invalid_request", "invalid Content-Length") from exc
            if declared_length < 0 or declared_length > self.max_body_bytes:
                raise ControlAPIError(413, "request_too_large", "request body is too large")

        body = bytearray()
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                raise ControlAPIError(400, "client_disconnected", "client disconnected")
            if message_type != "http.request":
                raise ControlAPIError(400, "invalid_request", "invalid ASGI request message")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise ControlAPIError(400, "invalid_request", "request body must be bytes")
            body.extend(chunk)
            if len(body) > self.max_body_bytes:
                raise ControlAPIError(413, "request_too_large", "request body is too large")
            if not message.get("more_body", False):
                break
        if declared_length is not None and declared_length != len(body):
            raise ControlAPIError(
                400,
                "invalid_request",
                "Content-Length does not match the request body",
            )
        try:
            text = bytes(body).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ControlAPIError(400, "invalid_json", "request body must be UTF-8") from exc
        try:
            decoded = json.loads(
                text,
                object_pairs_hook=_duplicate_rejecting_object,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ControlAPIError(400, "invalid_json", f"invalid JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ControlAPIError(400, "invalid_request", "request JSON must be an object")
        return cast(dict[str, Any], decoded)

    @staticmethod
    def _query(scope: ASGIScope, *, allowed: frozenset[str]) -> dict[str, str]:
        raw = scope.get("query_string", b"")
        if not isinstance(raw, bytes):
            raise ControlAPIError(400, "invalid_query", "query string must be bytes")
        try:
            pairs = (
                parse_qsl(
                    raw.decode("ascii"),
                    keep_blank_values=True,
                    strict_parsing=True,
                    encoding="utf-8",
                    errors="strict",
                )
                if raw
                else []
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise ControlAPIError(400, "invalid_query", "query string is malformed") from exc
        result: dict[str, str] = {}
        for key, value in pairs:
            if key not in allowed:
                raise ControlAPIError(400, "unknown_field", f"unknown query field {key!r}")
            if key in result:
                raise ControlAPIError(400, "invalid_query", f"duplicate query field {key!r}")
            result[key] = value
        return result

    def _event_cursor(self, scope: ASGIScope) -> int:
        query = self._query(scope, allowed=frozenset({"after"}))
        headers = self._headers(scope)
        header_values = headers.get(b"last-event-id", [])
        if len(header_values) > 1:
            raise ControlAPIError(400, "invalid_cursor", "duplicate Last-Event-ID headers")
        header_value: str | None = None
        if header_values:
            try:
                header_value = header_values[0].decode("ascii")
            except UnicodeDecodeError as exc:
                raise ControlAPIError(400, "invalid_cursor", "invalid Last-Event-ID") from exc
        query_value = query.get("after")
        if query_value is not None and header_value is not None and query_value != header_value:
            raise ControlAPIError(
                400,
                "invalid_cursor",
                "after and Last-Event-ID cursors disagree",
            )
        value = query_value if query_value is not None else header_value
        if value is None:
            return 0
        if (
            re.fullmatch(r"0|[1-9][0-9]*", value) is None
            or len(value) > 19
            or int(value) > _MAX_SQLITE_INTEGER
        ):
            raise ControlAPIError(400, "invalid_cursor", "event cursor must be non-negative")
        return int(value)

    async def _send_json(self, send: Send, response: _Response) -> None:
        body = _canonical_json(response.body)
        headers = (
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            *response.headers,
        )
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _stream_events(
        self,
        receive: Receive,
        send: Send,
        *,
        run_id: str,
        cursor: int,
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": (
                    (b"content-type", b"text/event-stream; charset=utf-8"),
                    (b"cache-control", b"no-cache, no-transform"),
                    (b"x-accel-buffering", b"no"),
                    (b"x-content-type-options", b"nosniff"),
                ),
            }
        )
        heartbeat_at = time.monotonic() + self.sse_heartbeat_seconds
        while True:
            emitted = self.events(run_id, after=cursor)
            for event in emitted:
                cursor = cast(int, event["sequence"])
                payload = _canonical_json(event)
                frame = (
                    f"id: {cursor}\nevent: {event['type']}\ndata: ".encode("utf-8")
                    + payload
                    + b"\n\n"
                )
                await send({"type": "http.response.body", "body": frame, "more_body": True})

            status = self.status(run_id)
            latest = int(cast(str, status["last_event_id"]) or "0")
            if status["state"] in _SSE_TERMINAL_STATES and cursor >= latest:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return

            now = time.monotonic()
            if now >= heartbeat_at:
                await send(
                    {
                        "type": "http.response.body",
                        "body": b": keep-alive\n\n",
                        "more_body": True,
                    }
                )
                heartbeat_at = now + self.sse_heartbeat_seconds
            try:
                message = await asyncio.wait_for(receive(), timeout=self.event_poll_seconds)
            except TimeoutError:
                continue
            if message.get("type") == "http.disconnect":
                return

    async def _dispatch(self, scope: ASGIScope, receive: Receive) -> _Response:
        method = str(scope.get("method", "")).upper()
        path = scope.get("path")
        if not isinstance(path, str):
            raise ControlAPIError(400, "invalid_request", "request path is unavailable")

        if path in {_LIVENESS_ROUTE, _READINESS_ROUTE}:
            if method != "GET":
                raise ControlAPIError(405, "method_not_allowed", "GET is required")
            self._query(scope, allowed=frozenset())
            payload = self.health() if path == _LIVENESS_ROUTE else self.readiness()
            return _Response(200, payload, ((b"cache-control", b"no-store"),))

        if path == _REFERENCE_WORKFLOWS_ROUTE:
            if method != "GET":
                raise ControlAPIError(405, "method_not_allowed", "GET is required")
            self._query(scope, allowed=frozenset())
            return _Response(
                200,
                {
                    "schema_version": "finite-reference-workflows/v1",
                    "workflows": [
                        {key: value for key, value in reference.items() if key != "workflow"}
                        for reference in self._reference_workflows.values()
                    ],
                },
            )

        reference_route = _REFERENCE_WORKFLOW_ROUTE.fullmatch(path)
        if reference_route is not None:
            if method != "GET":
                raise ControlAPIError(405, "method_not_allowed", "GET is required")
            self._query(scope, allowed=frozenset())
            workflow_id = reference_route.group("workflow_id")
            reference = self._reference_workflows.get(workflow_id)
            if reference is None:
                raise ControlAPIError(
                    404,
                    "reference_workflow_not_found",
                    f"reference workflow {workflow_id!r} was not found",
                )
            return _Response(200, reference)

        if path == "/v1/runs":
            if method != "POST":
                raise ControlAPIError(405, "method_not_allowed", "POST is required")
            self._query(scope, allowed=frozenset())
            body = _object(
                await self._read_json(scope, receive),
                path="$",
                allowed=frozenset({"run_id", "workflow", "start_paused"}),
                required=frozenset({"workflow"}),
            )
            run_id = body.get("run_id")
            if run_id is not None:
                run_id = _string(run_id, path="run_id", maximum=128)
            workflow = body["workflow"]
            if not isinstance(workflow, dict):
                raise ControlAPIError(400, "invalid_request", "workflow must be an object")
            start_paused = body.get("start_paused", False)
            if type(start_paused) is not bool:
                raise ControlAPIError(
                    400,
                    "invalid_request",
                    "start_paused must be a boolean",
                )
            result = await self.submit(
                cast(dict[str, Any], workflow),
                run_id=run_id,
                start_paused=start_paused,
            )
            created_id = cast(str, cast(dict[str, object], result["run"])["run_id"])
            return _Response(
                202,
                result,
                ((b"location", f"/v1/runs/{created_id}".encode("ascii")),),
            )

        route = _RUN_ROUTE.fullmatch(path)
        status_route = _STATUS_ROUTE.fullmatch(path)
        if route is None and status_route is not None:
            route = status_route
        if route is not None:
            if method != "GET":
                raise ControlAPIError(405, "method_not_allowed", "GET is required")
            self._query(scope, allowed=frozenset())
            return _Response(200, self.status(route.group("run_id")))

        route = _INSPECT_ROUTE.fullmatch(path)
        if route is not None:
            if method != "GET":
                raise ControlAPIError(405, "method_not_allowed", "GET is required")
            self._query(scope, allowed=frozenset())
            return _Response(200, self.inspect(route.group("run_id")))

        route = _CANCEL_ROUTE.fullmatch(path)
        if route is not None:
            if method != "POST":
                raise ControlAPIError(405, "method_not_allowed", "POST is required")
            self._query(scope, allowed=frozenset())
            body = _object(
                await self._read_json(scope, receive),
                path="$",
                allowed=frozenset({"reason"}),
            )
            reason = body.get("reason", "operator request")
            result = await self.cancel(
                route.group("run_id"),
                reason=_string(reason, path="reason", maximum=512),
            )
            return _Response(202, result)

        route = _CONTROL_EVENTS_ROUTE.fullmatch(path)
        if route is not None:
            if method != "POST":
                raise ControlAPIError(405, "method_not_allowed", "POST is required")
            self._query(scope, allowed=frozenset())
            body = _object(
                await self._read_json(scope, receive),
                path="$",
                allowed=frozenset(
                    {"kind", "expected_revision", "occurred_at_ms", "details"}
                ),
                required=frozenset(
                    {"kind", "expected_revision", "occurred_at_ms", "details"}
                ),
            )
            kind = _string(body["kind"], path="kind", maximum=64)
            allowed_details = _ADAPTIVE_CONTROL_DETAIL_FIELDS.get(kind)
            if allowed_details is None:
                raise ControlAPIError(
                    422,
                    "unsupported_control_event",
                    "kind is not a registered adaptive control event",
                )
            details = _object(
                body["details"],
                path="details",
                allowed=allowed_details,
                required=allowed_details,
            )
            normalized_details: dict[str, object] = {}
            for field, value in details.items():
                if field == "provider":
                    normalized_details[field] = _string(
                        value,
                        path="details.provider",
                        maximum=128,
                    )
                else:
                    normalized_details[field] = _integer(
                        value,
                        path=f"details.{field}",
                        minimum=0,
                    )
            result = await self.adaptive_control(
                route.group("run_id"),
                kind=kind,
                expected_revision=_integer(
                    body["expected_revision"],
                    path="expected_revision",
                    minimum=1,
                ),
                occurred_at_ms=_integer(
                    body["occurred_at_ms"],
                    path="occurred_at_ms",
                    minimum=0,
                ),
                details=normalized_details,
            )
            return _Response(202, result)

        route = _ADAPTIVE_REPLAY_ROUTE.fullmatch(path)
        if route is not None:
            if method != "GET":
                raise ControlAPIError(405, "method_not_allowed", "GET is required")
            self._query(scope, allowed=frozenset())
            return _Response(200, self.adaptive_replay(route.group("run_id")))

        route = _APPROVE_ROUTE.fullmatch(path)
        if route is not None:
            if method != "POST":
                raise ControlAPIError(405, "method_not_allowed", "POST is required")
            self._query(scope, allowed=frozenset())
            body = _object(
                await self._read_json(scope, receive),
                path="$",
                allowed=frozenset({"scope", "grant"}),
                required=frozenset({"scope"}),
            )
            scope_value = body["scope"]
            if not isinstance(scope_value, dict):
                raise ControlAPIError(400, "invalid_request", "scope must be an object")
            grant_value = body.get("grant")
            if grant_value is not None and not isinstance(grant_value, dict):
                raise ControlAPIError(400, "invalid_request", "grant must be an object")
            result = self.approve(
                route.group("run_id"),
                route.group("intent_id"),
                scope=cast(dict[str, object], scope_value),
                grant=cast(dict[str, object], grant_value) if grant_value is not None else None,
            )
            return _Response(200, result)

        if _EVENTS_ROUTE.fullmatch(path) is not None:
            # Streaming is dispatched by __call__ after validation.
            raise RuntimeError("event stream route reached non-streaming dispatcher")
        raise ControlAPIError(404, "route_not_found", "route was not found")

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message.get("type") == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message.get("type") == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Serve the ASGI HTTP or lifespan protocol."""

        if scope.get("type") == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope.get("type") != "http":
            raise RuntimeError("ControlPlane supports only ASGI HTTP and lifespan scopes")
        original_send = send
        try:
            cors_headers = self._cors_headers(scope)
        except ControlAPIError as exc:
            await self._send_json(
                original_send,
                _Response(exc.status, {"error": {"code": exc.code, "message": exc.message}}),
            )
            return
        if str(scope.get("method", "")).upper() == "OPTIONS":
            try:
                await self._preflight(scope, original_send)
            except ControlAPIError as exc:
                await self._send_json(
                    original_send,
                    _Response(
                        exc.status,
                        {"error": {"code": exc.code, "message": exc.message}},
                    ),
                )
            return

        async def send_with_cors(message: ASGIMessage) -> None:
            if message.get("type") == "http.response.start" and cors_headers:
                message = dict(message)
                message["headers"] = (*tuple(message.get("headers", ())), *cors_headers)
            await original_send(message)

        send = send_with_cors
        try:
            self._authorize(scope)
        except ControlAPIError as exc:
            await self._send_json(
                send,
                _Response(
                    exc.status,
                    {"error": {"code": exc.code, "message": exc.message}},
                    ((b"www-authenticate", b'Bearer realm="finite-control"'),),
                ),
            )
            return
        path = scope.get("path")
        route = _EVENTS_ROUTE.fullmatch(path) if isinstance(path, str) else None
        if route is not None:
            try:
                if str(scope.get("method", "")).upper() != "GET":
                    raise ControlAPIError(405, "method_not_allowed", "GET is required")
                cursor = self._event_cursor(scope)
                # Validation must finish before the SSE response starts. Once
                # started, transport/runtime failures propagate to the server;
                # emitting a second JSON response would violate ASGI.
                self.events(route.group("run_id"), after=cursor)
            except ControlAPIError as exc:
                await self._send_json(
                    send,
                    _Response(
                        exc.status,
                        {"error": {"code": exc.code, "message": exc.message}},
                    ),
                )
                return
            except Exception:
                await self._send_json(
                    send,
                    _Response(
                        500,
                        {
                            "error": {
                                "code": "internal_error",
                                "message": "the control plane could not complete the request",
                            }
                        },
                    ),
                )
                return
            await self._stream_events(
                receive,
                send,
                run_id=route.group("run_id"),
                cursor=cursor,
            )
            return
        try:
            response = await self._dispatch(scope, receive)
        except ControlAPIError as exc:
            response = _Response(
                exc.status,
                {"error": {"code": exc.code, "message": exc.message}},
            )
        except Exception:
            response = _Response(
                500,
                {
                    "error": {
                        "code": "internal_error",
                        "message": "the control plane could not complete the request",
                    }
                },
            )
        await self._send_json(send, response)


__all__ = ["ControlAPIError", "ControlPlane", "ExecutionRuntime"]
