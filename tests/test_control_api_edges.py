from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import agent_physics.control_api as control_api
from agent_physics.control_api import ControlAPIError, ControlPlane
from agent_physics.effects import SQLiteEffectBroker
from agent_physics.executor import AsyncGraphExecutor, ExecutionError, WorkerResult
from agent_physics.run_store import SQLiteRunStore


def _app(tmp_path: Path, **kwargs: object) -> ControlPlane:
    runtime = AsyncGraphExecutor(SQLiteRunStore(tmp_path / "runs.db"), workers={})
    return ControlPlane(runtime, **kwargs)  # type: ignore[arg-type]


def _workflow() -> dict[str, object]:
    return {
        "schema_version": 1,
        "envelope": {
            "deadline_ms": 1_000,
            "max_tokens": 100,
            "max_cost_microusd": 100,
            "max_context_bytes": 1_000,
            "max_parallelism": 1,
        },
        "tasks": [
            {
                "task_id": "work",
                "profiles": [
                    {
                        "name": "fixture",
                        "provider": "local",
                        "duration_ms_p50": 1,
                        "duration_ms_p95": 2,
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cost_microusd": 1,
                        "context_bytes": 1,
                    }
                ],
                "effect": {"kind": "pure"},
            }
        ],
    }


async def _asgi(
    app: ControlPlane,
    *,
    method: str = "GET",
    path: object = "/missing",
    headers: list[tuple[object, object]] | None = None,
    query: object = b"",
    messages: list[dict[str, object]] | None = None,
    scope_type: str = "http",
) -> tuple[int, dict[str, object], list[dict[str, object]]]:
    inbound = list(messages or [{"type": "http.request", "body": b"", "more_body": False}])

    async def receive() -> dict[str, object]:
        if inbound:
            return inbound.pop(0)
        return {"type": "http.disconnect"}

    outbound: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        outbound.append(message)

    scope: dict[str, object] = {
        "type": scope_type,
        "http_version": "1.1",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }
    await app(scope, receive, send)
    start = outbound[0]
    body = b"".join(
        message.get("body", b"")  # type: ignore[arg-type]
        for message in outbound[1:]
        if message.get("type") == "http.response.body"
    )
    payload: dict[str, object] = json.loads(body) if body else {}
    return int(start["status"]), payload, outbound


def _error(payload: dict[str, object]) -> str:
    return str(payload["error"]["code"])  # type: ignore[index]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_body_bytes": 0}, "max_body_bytes"),
        ({"event_poll_seconds": 0}, "SSE timing"),
        ({"sse_heartbeat_seconds": 0}, "SSE timing"),
        ({"allow_anonymous_status_stream": 1}, "boolean"),
        ({"max_active_runs": 0}, "max_active_runs"),
        ({"max_active_runs": True}, "max_active_runs"),
        ({"max_control_events_per_run": 0}, "max_control_events_per_run"),
        ({"max_control_events_per_run": 1_000_001}, "max_control_events_per_run"),
        ({"allowed_origins": ["https://a.example"]}, "tuple"),
        ({"allowed_origins": (7,)}, "strings"),
        (
            {"allowed_origins": ("https://b.example", "https://a.example")},
            "sorted and unique",
        ),
        (
            {"allowed_origins": ("https://a.example", "https://a.example")},
            "sorted and unique",
        ),
        ({"bearer_token": 7}, "bearer_token"),
        ({"bearer_token": "x" * 1025}, "bearer_token"),
        ({"bearer_token": "é" * 32}, "visible ASCII"),
        ({"bearer_token": "\x7f" * 32}, "visible ASCII"),
    ],
)
def test_constructor_rejects_ambiguous_security_configuration(
    tmp_path: Path,
    kwargs: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _app(tmp_path, **kwargs)


def test_concurrent_control_requests_atomically_reserve_the_durable_limit(
    tmp_path: Path,
) -> None:
    class BlockingRuntime:
        def __init__(self) -> None:
            self.store = SQLiteRunStore(tmp_path / "atomic-control-limit.db")
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def apply_adaptive_control(
            self,
            run_id: str,
            *,
            kind: str,
            expected_revision: int,
            occurred_at_ms: int,
            details: dict[str, object],
        ) -> dict[str, object]:
            del run_id, kind, occurred_at_ms, details
            self.entered.set()
            await self.release.wait()
            return {"state": {"revision": expected_revision + 1}}

    async def scenario() -> None:
        runtime = BlockingRuntime()
        runtime.store.get_or_create_run(
            run_id="bounded-control",
            graph_digest="graph",
            envelope={},
            deadline_at_ms=1_000,
        )
        app = ControlPlane(runtime, max_control_events_per_run=1)  # type: ignore[arg-type]
        first = asyncio.create_task(
            app.adaptive_control(
                "bounded-control",
                kind="provider.capacity",
                expected_revision=0,
                occurred_at_ms=0,
                details={"provider": "local", "capacity": 1},
            )
        )
        await runtime.entered.wait()
        with pytest.raises(ControlAPIError) as limited:
            await app.adaptive_control(
                "bounded-control",
                kind="budget.cut",
                expected_revision=1,
                occurred_at_ms=0,
                details={"tokens": 1, "cost_microusd": 1, "context_bytes": 1},
            )
        assert limited.value.status == 429
        assert limited.value.code == "control_event_limit"
        runtime.release.set()
        assert (await first)["state"] == {"revision": 1}
        accepted = [
            event
            for event in runtime.store.events("bounded-control")
            if event.event_type == "control.adaptive_event_accepted"
        ]
        assert len(accepted) == 1
        assert app._pending_control_events == {}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://a.example",
        "https://",
        "https://user@a.example",
        "https://a.example/path",
        "https://a.example?query=1",
        "https://a.example#fragment",
        "HTTPS://a.example",
    ],
)
def test_constructor_requires_exact_http_origins(tmp_path: Path, origin: str) -> None:
    with pytest.raises(ValueError, match="exact HTTP"):
        _app(tmp_path, allowed_origins=(origin,))


def test_constructor_rejects_bad_reference_id_and_workflow(tmp_path: Path) -> None:
    with pytest.raises(ControlAPIError, match="reference workflow ID"):
        _app(tmp_path, reference_workflows={"bad/id": {}})
    with pytest.raises(ValueError, match="reference workflow"):
        _app(tmp_path, reference_workflows={"bad": {}})


def test_health_and_readiness_are_minimal_public_fail_closed_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        broker = SQLiteEffectBroker(tmp_path / "effects.db", broker_id="probe-broker")
        app = ControlPlane(
            AsyncGraphExecutor(SQLiteRunStore(tmp_path / "runs.db"), workers={}),
            effect_broker=broker,
            bearer_token="bounded-control-token-material-1234",
            allowed_origins=("https://console.example",),
        )

        for path, expected_status in (("/healthz", "ok"), ("/readyz", "ready")):
            status, payload, outbound = await _asgi(
                app,
                path=path,
                headers=[(b"origin", b"https://console.example")],
            )
            assert status == 200
            assert payload["status"] == expected_status
            assert "token" not in str(payload).lower()
            response_headers = dict(outbound[0]["headers"])  # type: ignore[arg-type]
            assert response_headers[b"cache-control"] == b"no-store"
            assert response_headers[b"access-control-allow-origin"] == b"https://console.example"

        status, payload, _ = await _asgi(app, method="POST", path="/healthz")
        assert status == 401
        assert _error(payload) == "unauthorized"

        status, payload, _ = await _asgi(app, path="/healthz", query=b"verbose=true")
        assert status == 400
        assert _error(payload) == "unknown_field"

        monkeypatch.setattr(app.store, "schema_versions", lambda: ())
        status, payload, _ = await _asgi(app, path="/readyz")
        assert status == 503
        assert payload == {
            "error": {
                "code": "not_ready",
                "message": "the control plane is not ready",
            }
        }

    asyncio.run(scenario())


def test_readiness_fails_closed_when_effect_broker_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        broker = SQLiteEffectBroker(tmp_path / "effects.db", broker_id="probe-broker")
        app = ControlPlane(
            AsyncGraphExecutor(SQLiteRunStore(tmp_path / "runs.db"), workers={}),
            effect_broker=broker,
        )

        def fail(*, limit: int = 100) -> tuple[object, ...]:
            del limit
            raise OSError("secret backend detail")

        monkeypatch.setattr(broker, "pending_outbox", fail)
        status, payload, _ = await _asgi(app, path="/readyz")
        assert status == 503
        assert _error(payload) == "not_ready"
        assert "secret backend detail" not in str(payload)

    asyncio.run(scenario())


def test_strict_scalar_helpers_reject_bool_ranges_and_shape() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        control_api._reject_json_constant("NaN")
    with pytest.raises(ControlAPIError) as not_object:
        control_api._object([], path="$", allowed=frozenset())
    assert not_object.value.code == "invalid_request"
    with pytest.raises(ControlAPIError) as missing:
        control_api._object({}, path="$", allowed=frozenset({"x"}), required=frozenset({"x"}))
    assert missing.value.code == "missing_field"
    with pytest.raises(ControlAPIError):
        control_api._string("", path="x")
    with pytest.raises(ControlAPIError):
        control_api._string("xx", path="x", maximum=1)
    with pytest.raises(ControlAPIError):
        control_api._integer(True, path="x")
    with pytest.raises(ControlAPIError):
        control_api._integer(-1, path="x", minimum=0)
    with pytest.raises(ControlAPIError):
        control_api._integer(2, path="x", maximum=1)


def test_programmatic_identifier_cursor_and_terminal_guards(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with pytest.raises(ControlAPIError) as identifier:
        app.status("bad/id")
    assert identifier.value.code == "invalid_identifier"
    with pytest.raises(ControlAPIError) as cursor:
        app.events("missing", after=True)
    assert cursor.value.code == "invalid_cursor"


def test_submit_reports_admitting_active_existing_and_pre_store_refusal(tmp_path: Path) -> None:
    class WaitingRuntime:
        def __init__(self, store: SQLiteRunStore) -> None:
            self.store = store
            self.release = asyncio.Event()

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            await self.release.wait()
            raise RuntimeError("test cleanup")

    class RefusingRuntime:
        def __init__(self, store: SQLiteRunStore) -> None:
            self.store = store

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise ExecutionError("refused before durable creation")

    async def scenario() -> None:
        waiting = WaitingRuntime(SQLiteRunStore(tmp_path / "waiting.db"))
        app = ControlPlane(waiting)  # type: ignore[arg-type]
        accepted = await app.submit(_workflow(), run_id="waiting-run")
        assert accepted["run"]["state"] == "admitting"  # type: ignore[index]
        with pytest.raises(ControlAPIError) as active:
            await app.submit(_workflow(), run_id="waiting-run")
        assert active.value.code == "run_active"
        task = app._active["waiting-run"].task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        existing_store = SQLiteRunStore(tmp_path / "existing.db")
        existing_store.get_or_create_run(
            run_id="existing-run",
            graph_digest="graph",
            envelope={},
            deadline_at_ms=1,
        )
        existing = ControlPlane(WaitingRuntime(existing_store))  # type: ignore[arg-type]
        with pytest.raises(ControlAPIError) as conflict:
            await existing.submit(_workflow(), run_id="existing-run")
        assert conflict.value.code == "run_exists"

        refusing = ControlPlane(RefusingRuntime(SQLiteRunStore(tmp_path / "refusing.db")))  # type: ignore[arg-type]
        with pytest.raises(ControlAPIError) as refused:
            await refusing.submit(_workflow(), run_id="refused-run")
        assert refused.value.code == "admission_refused"

    asyncio.run(scenario())


def test_process_local_active_run_cap_rejects_then_recovers_capacity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async def wait_for_completed(run_id: str) -> None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10.0
            while loop.time() < deadline:
                if app.status(run_id)["state"] == "completed":
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"{run_id} did not complete within 10 seconds")

        release = asyncio.Event()

        async def worker(_context: object) -> WorkerResult:
            await release.wait()
            return WorkerResult({"settled": True})

        workflow = _workflow()
        envelope = workflow["envelope"]
        assert isinstance(envelope, dict)
        envelope["deadline_ms"] = 10_000

        store = SQLiteRunStore(tmp_path / "active-cap.db")
        app = ControlPlane(
            AsyncGraphExecutor(store, workers={"work": worker}),  # type: ignore[dict-item]
            max_active_runs=1,
        )
        accepted = await app.submit(workflow, run_id="active-one")
        assert accepted["run"]["state"] == "running"  # type: ignore[index]

        with pytest.raises(ControlAPIError) as saturated:
            await app.submit(workflow, run_id="active-two")
        assert saturated.value.status == 429
        assert saturated.value.code == "active_run_limit"

        release.set()
        await wait_for_completed("active-one")

        second = await app.submit(workflow, run_id="active-two")
        assert second["run"]["run_id"] == "active-two"  # type: ignore[index]
        await wait_for_completed("active-two")

    asyncio.run(scenario())


def test_coordinator_recovery_reserves_active_capacity_across_await(
    tmp_path: Path,
) -> None:
    class RecoveryRuntime:
        def __init__(self, store: SQLiteRunStore) -> None:
            self.store = store
            self.control_entered = asyncio.Event()
            self.release_control = asyncio.Event()
            self.release_resume = asyncio.Event()
            self.control_calls: list[str] = []

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("a fresh execution must not bypass the reserved recovery slot")

        async def apply_adaptive_control(
            self,
            run_id: str,
            *,
            kind: str,
            expected_revision: int,
            occurred_at_ms: int,
            details: dict[str, object],
        ) -> dict[str, object]:
            assert kind == "coordinator.recover"
            assert occurred_at_ms == 0
            assert details == {}
            self.control_calls.append(run_id)
            self.control_entered.set()
            await self.release_control.wait()
            return {"state": {"revision": expected_revision + 1}}

        async def resume_existing(self, *_args: object, **_kwargs: object) -> object:
            await self.release_resume.wait()
            return object()

    async def scenario() -> None:
        store = SQLiteRunStore(tmp_path / "recovery-cap.db")
        for run_id in ("orphan-one", "orphan-two"):
            store.get_or_create_run(
                run_id=run_id,
                graph_digest="graph",
                envelope={},
                deadline_at_ms=1,
            )
        runtime = RecoveryRuntime(store)
        app = ControlPlane(runtime, max_active_runs=1)  # type: ignore[arg-type]

        first = asyncio.create_task(
            app.adaptive_control(
                "orphan-one",
                kind="coordinator.recover",
                expected_revision=0,
                occurred_at_ms=0,
                details={},
            )
        )
        await runtime.control_entered.wait()
        assert app._pending_recoveries == 1

        with pytest.raises(ControlAPIError) as second_recovery:
            await app.adaptive_control(
                "orphan-two",
                kind="coordinator.recover",
                expected_revision=0,
                occurred_at_ms=0,
                details={},
            )
        assert second_recovery.value.status == 429
        assert second_recovery.value.code == "active_run_limit"

        with pytest.raises(ControlAPIError) as fresh_submit:
            await app.submit(_workflow(), run_id="submit-during-recovery")
        assert fresh_submit.value.code == "active_run_limit"
        assert runtime.control_calls == ["orphan-one"]

        runtime.release_control.set()
        response = await first
        assert response["execution_resumed"] is True
        assert app._pending_recoveries == 0
        assert set(app._active) == {"orphan-one"}

        runtime.release_resume.set()
        await asyncio.sleep(0)
        recovered = await app.adaptive_control(
            "orphan-two",
            kind="coordinator.recover",
            expected_revision=0,
            occurred_at_ms=0,
            details={},
        )
        assert recovered["execution_resumed"] is True
        assert runtime.control_calls == ["orphan-one", "orphan-two"]

    asyncio.run(scenario())


def test_execution_callback_consumes_cancel_and_base_exception() -> None:
    class Cancelled:
        def cancelled(self) -> bool:
            return True

    class Broken:
        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            raise KeyboardInterrupt

    ControlPlane._consume_execution(Cancelled())  # type: ignore[arg-type]
    ControlPlane._consume_execution(Broken())  # type: ignore[arg-type]


def test_terminal_cancel_and_approval_state_broker_guards(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "state.db")
    store.get_or_create_run(
        run_id="terminal",
        graph_digest="graph",
        envelope={},
        deadline_at_ms=1,
    )
    store.append_event(
        run_id="terminal",
        event_id="terminal:complete",
        event_type="run.completed",
    )
    terminal = ControlPlane(AsyncGraphExecutor(store, workers={}))
    with pytest.raises(ControlAPIError) as approval_state:
        terminal.approve("terminal", "intent", scope={})
    assert approval_state.value.code == "run_not_awaiting_effects"

    async def cancel_terminal() -> None:
        with pytest.raises(ControlAPIError) as cancel_state:
            await terminal.cancel("terminal")
        assert cancel_state.value.code == "run_terminal"

    asyncio.run(cancel_terminal())

    awaiting_store = SQLiteRunStore(tmp_path / "awaiting.db")
    awaiting_store.get_or_create_run(
        run_id="awaiting",
        graph_digest="graph",
        envelope={},
        deadline_at_ms=1,
    )
    awaiting_store.append_event(
        run_id="awaiting",
        event_id="awaiting:effects",
        event_type="run.awaiting_effects",
    )
    no_broker = ControlPlane(AsyncGraphExecutor(awaiting_store, workers={}))
    with pytest.raises(ControlAPIError) as unavailable:
        no_broker.approve("awaiting", "intent", scope={})
    assert unavailable.value.code == "effect_broker_unavailable"

    broker = SQLiteEffectBroker(tmp_path / "effects.db", broker_id="edge-broker")
    missing = ControlPlane(
        AsyncGraphExecutor(awaiting_store, workers={}, effect_broker=broker),
        effect_broker=broker,
    )
    with pytest.raises(ControlAPIError) as absent:
        missing.approve("awaiting", "intent", scope={})
    assert absent.value.code == "effect_not_found"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([], "origin_not_allowed"),
        (
            [(b"origin", b"https://console.example"), (b"origin", b"https://console.example")],
            "invalid_origin",
        ),
        ([(b"origin", b"\xff")], "invalid_origin"),
        ([(b"origin", b"https://console.example")], "invalid_preflight"),
        (
            [
                (b"origin", b"https://console.example"),
                (b"access-control-request-method", b"\xff"),
            ],
            "invalid_preflight",
        ),
        (
            [
                (b"origin", b"https://console.example"),
                (b"access-control-request-method", b"DELETE"),
            ],
            "method_not_allowed",
        ),
        (
            [
                (b"origin", b"https://console.example"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"authorization"),
                (b"access-control-request-headers", b"content-type"),
            ],
            "invalid_preflight",
        ),
        (
            [
                (b"origin", b"https://console.example"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"\xff"),
            ],
            "invalid_preflight",
        ),
        (
            [
                (b"origin", b"https://console.example"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"x-admin"),
            ],
            "header_not_allowed",
        ),
    ],
)
def test_preflight_fails_closed(
    tmp_path: Path,
    headers: list[tuple[object, object]],
    expected: str,
) -> None:
    async def scenario() -> None:
        app = _app(tmp_path, allowed_origins=("https://console.example",))
        status, payload, _ = await _asgi(app, method="OPTIONS", path="/v1/runs", headers=headers)
        assert status >= 400
        assert _error(payload) == expected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("headers", "messages", "expected"),
    [
        ([], [{"type": "http.request", "body": b"{}"}], "unsupported_media_type"),
        (
            [(b"content-type", b"application/json"), (b"content-type", b"application/json")],
            [{"type": "http.request", "body": b"{}"}],
            "unsupported_media_type",
        ),
        (
            [(b"content-type", b"\xff")],
            [{"type": "http.request", "body": b"{}"}],
            "unsupported_media_type",
        ),
        (
            [(b"content-type", b"application/json; charset=latin1")],
            [{"type": "http.request", "body": b"{}"}],
            "unsupported_media_type",
        ),
        (
            [
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
                (b"content-length", b"2"),
            ],
            [{"type": "http.request", "body": b"{}"}],
            "invalid_request",
        ),
        (
            [(b"content-type", b"application/json"), (b"content-length", b"nope")],
            [{"type": "http.request", "body": b"{}"}],
            "invalid_request",
        ),
        (
            [(b"content-type", b"application/json"), (b"content-length", b"-1")],
            [{"type": "http.request", "body": b"{}"}],
            "request_too_large",
        ),
        (
            [(b"content-type", b"application/json"), (b"content-length", b"3")],
            [{"type": "http.request", "body": b"{}"}],
            "invalid_request",
        ),
        (
            [(b"content-type", b"application/json")],
            [{"type": "http.disconnect"}],
            "client_disconnected",
        ),
        (
            [(b"content-type", b"application/json")],
            [{"type": "websocket.receive"}],
            "invalid_request",
        ),
        (
            [(b"content-type", b"application/json")],
            [{"type": "http.request", "body": "not-bytes"}],
            "invalid_request",
        ),
        (
            [(b"content-type", b"application/json")],
            [{"type": "http.request", "body": b"\xff"}],
            "invalid_json",
        ),
        (
            [(b"content-type", b"application/json")],
            [{"type": "http.request", "body": b'{"workflow":NaN}'}],
            "invalid_json",
        ),
        (
            [(b"content-type", b"application/json")],
            [{"type": "http.request", "body": b"[]"}],
            "invalid_request",
        ),
    ],
)
def test_json_transport_parser_rejects_ambiguous_inputs(
    tmp_path: Path,
    headers: list[tuple[object, object]],
    messages: list[dict[str, object]],
    expected: str,
) -> None:
    async def scenario() -> None:
        app = _app(tmp_path, max_body_bytes=64)
        status, payload, _ = await _asgi(
            app,
            method="POST",
            path="/v1/runs",
            headers=headers,
            messages=messages,
        )
        assert status >= 400
        assert _error(payload) == expected

    asyncio.run(scenario())


def test_body_limit_applies_to_declared_and_streamed_bytes(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path, max_body_bytes=3)
        headers = [(b"content-type", b"application/json")]
        status, payload, _ = await _asgi(
            app,
            method="POST",
            path="/v1/runs",
            headers=[*headers, (b"content-length", b"4")],
            messages=[{"type": "http.request", "body": b"{}"}],
        )
        assert status == 413 and _error(payload) == "request_too_large"
        status, payload, _ = await _asgi(
            app,
            method="POST",
            path="/v1/runs",
            headers=headers,
            messages=[
                {"type": "http.request", "body": b"{}", "more_body": True},
                {"type": "http.request", "body": b"{}", "more_body": False},
            ],
        )
        assert status == 413 and _error(payload) == "request_too_large"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("query", "headers", "expected"),
    [
        (b"unknown=1", [], "unknown_field"),
        (b"after=1&after=1", [], "invalid_query"),
        (b"broken", [], "invalid_query"),
        (b"\xff", [], "invalid_query"),
        (b"after=1", [(b"last-event-id", b"2")], "invalid_cursor"),
        (b"", [(b"last-event-id", b"1"), (b"last-event-id", b"1")], "invalid_cursor"),
        (b"", [(b"last-event-id", b"\xff")], "invalid_cursor"),
        (b"after=-1", [], "invalid_cursor"),
        (b"after=00", [], "invalid_cursor"),
        (b"after=99999999999999999999", [], "invalid_cursor"),
    ],
)
def test_event_cursor_is_unambiguous(
    tmp_path: Path,
    query: bytes,
    headers: list[tuple[object, object]],
    expected: str,
) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        status, payload, _ = await _asgi(
            app,
            path="/v1/runs/missing/events",
            headers=headers,
            query=query,
        )
        assert status >= 400
        assert _error(payload) == expected

    asyncio.run(scenario())


def test_query_requires_byte_transport(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with pytest.raises(ControlAPIError) as error:
        app._query({"query_string": "after=1"}, allowed=frozenset({"after"}))
    assert error.value.code == "invalid_query"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/reference-workflows"),
        ("POST", "/v1/reference-workflows/missing"),
        ("GET", "/v1/runs"),
        ("POST", "/v1/runs/missing"),
        ("POST", "/v1/runs/missing/status"),
        ("POST", "/v1/runs/missing/inspect"),
        ("GET", "/v1/runs/missing/cancel"),
        ("GET", "/v1/runs/missing/effects/intent/approve"),
        ("POST", "/not-a-route"),
    ],
)
def test_route_methods_and_unknown_routes_fail_closed(
    tmp_path: Path,
    method: str,
    path: str,
) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        status, payload, _ = await _asgi(app, method=method, path=path)
        assert status in {404, 405}
        assert _error(payload) in {"method_not_allowed", "route_not_found"}

    asyncio.run(scenario())


def test_reference_missing_and_invalid_path_shape_are_safe(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        status, payload, _ = await _asgi(app, path="/v1/reference-workflows/missing")
        assert status == 404 and _error(payload) == "reference_workflow_not_found"
        status, payload, _ = await _asgi(app, path=None)
        assert status == 400 and _error(payload) == "invalid_request"

    asyncio.run(scenario())


def test_route_payload_types_and_event_method_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        json_headers = [(b"content-type", b"application/json")]
        for path, body, expected in (
            ("/v1/runs", {"workflow": []}, "invalid_request"),
            (
                "/v1/runs/missing/effects/intent/approve",
                {"scope": []},
                "invalid_request",
            ),
            (
                "/v1/runs/missing/effects/intent/approve",
                {"scope": {}, "grant": []},
                "invalid_request",
            ),
        ):
            encoded = json.dumps(body).encode()
            status, payload, _ = await _asgi(
                app,
                method="POST",
                path=path,
                headers=[*json_headers, (b"content-length", str(len(encoded)).encode())],
                messages=[{"type": "http.request", "body": encoded}],
            )
            assert status == 400 and _error(payload) == expected

        status, payload, _ = await _asgi(
            app,
            method="POST",
            path="/v1/runs/missing/events",
        )
        assert status == 405 and _error(payload) == "method_not_allowed"

        scope = {
            "method": "GET",
            "path": "/v1/runs/missing/events",
            "query_string": b"",
            "headers": [],
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        with pytest.raises(RuntimeError, match="non-streaming"):
            await app._dispatch(scope, receive)

    asyncio.run(scenario())


def test_sse_emits_heartbeat_then_stops_on_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        app = _app(tmp_path, event_poll_seconds=0.001, sse_heartbeat_seconds=0.000001)
        app.store.get_or_create_run(
            run_id="streaming",
            graph_digest="graph",
            envelope={},
            deadline_at_ms=1,
        )
        outbound: list[dict[str, object]] = []
        clock = iter((0.0, 1.0))

        class FakeTime:
            @staticmethod
            def monotonic() -> float:
                return next(clock)

        monkeypatch.setattr(control_api, "time", FakeTime)

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            outbound.append(message)

        await app._stream_events(receive, send, run_id="streaming", cursor=0)
        assert outbound[0]["status"] == 200
        assert any(message.get("body") == b": keep-alive\n\n" for message in outbound)

    asyncio.run(scenario())


def test_sse_validation_internal_error_is_redacted(tmp_path: Path) -> None:
    class BrokenEvents(ControlPlane):
        def events(self, run_id: str, *, after: int = 0) -> tuple[dict[str, object], ...]:
            raise RuntimeError(f"secret failure for {run_id} after {after}")

    async def scenario() -> None:
        runtime = AsyncGraphExecutor(SQLiteRunStore(tmp_path / "stream-broken.db"), workers={})
        app = BrokenEvents(runtime)
        status, payload, outbound = await _asgi(app, path="/v1/runs/run/events")
        assert status == 500 and _error(payload) == "internal_error"
        assert b"secret failure" not in repr(outbound).encode()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "authorization",
    [
        b"\xff",
        b"Basic abc",
        b"Bearer",
        b"Bearer ",
        b"Bearer wrong",
        b"Bearer wrong token",
    ],
)
def test_bearer_shape_is_strict_and_redacted(
    tmp_path: Path,
    authorization: bytes,
) -> None:
    async def scenario() -> None:
        app = _app(tmp_path, bearer_token="correct-bearer-token-material-12345")
        status, payload, outbound = await _asgi(
            app,
            path="/v1/reference-workflows",
            headers=[(b"authorization", authorization)],
        )
        assert status == 401 and _error(payload) == "unauthorized"
        credential = authorization.partition(b" ")[2]
        if credential:
            assert credential not in repr(outbound).encode("utf-8", errors="ignore")

    asyncio.run(scenario())


def test_non_http_scope_is_rejected_and_lifespan_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async def receive_http() -> dict[str, object]:
            return {"type": "websocket.receive"}

        async def send_http(_message: dict[str, object]) -> None:
            raise AssertionError("unexpected response")

        with pytest.raises(RuntimeError, match="ASGI HTTP"):
            await app({"type": "websocket"}, receive_http, send_http)

        inbound = [
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        ]
        outbound: list[dict[str, object]] = []

        async def receive_lifespan() -> dict[str, object]:
            return inbound.pop(0)

        async def send_lifespan(message: dict[str, object]) -> None:
            outbound.append(message)

        await app({"type": "lifespan"}, receive_lifespan, send_lifespan)
        assert outbound == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]

    asyncio.run(scenario())


def test_internal_error_response_does_not_leak_exception(tmp_path: Path) -> None:
    class BrokenControlPlane(ControlPlane):
        async def _dispatch(
            self,
            scope: control_api.ASGIScope,
            receive: control_api.Receive,
        ) -> control_api._Response:
            raise RuntimeError("secret internal detail")

    async def scenario() -> None:
        runtime = AsyncGraphExecutor(SQLiteRunStore(tmp_path / "broken.db"), workers={})
        app = BrokenControlPlane(runtime)
        status, payload, outbound = await _asgi(app)
        assert status == 500 and _error(payload) == "internal_error"
        assert b"secret internal detail" not in repr(outbound).encode()

    asyncio.run(scenario())
