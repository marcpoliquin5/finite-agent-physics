from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_physics.control_api import ControlAPIError, ControlPlane
from agent_physics.contracts import EffectClass
from agent_physics.effects import ApprovalAuthority, EffectState, SQLiteEffectBroker
from agent_physics.executor import AsyncGraphExecutor, TaskExecutionContext, WorkerResult
from agent_physics.run_store import SQLiteRunStore, Usage


def _workflow(*, effect: Mapping[str, object] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "envelope": {
            "deadline_ms": 5_000,
            "max_tokens": 10_000,
            "max_cost_microusd": 10_000,
            "max_context_bytes": 100_000,
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
                        "duration_ms_p95": 5,
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cost_microusd": 2,
                        "context_bytes": 100,
                    }
                ],
                "effect": dict(effect or {"kind": "pure"}),
            }
        ],
    }


async def _request(
    app: ControlPlane,
    method: str,
    path: str,
    *,
    value: object | None = None,
    raw_body: bytes | None = None,
    headers: list[tuple[bytes, bytes]] | None = None,
    query: bytes = b"",
) -> tuple[int, dict[bytes, bytes], bytes]:
    body = raw_body if raw_body is not None else json.dumps(value).encode("utf-8")
    request_headers = list(headers or [])
    if value is not None or raw_body is not None:
        if not any(name.lower() == b"content-type" for name, _ in request_headers):
            request_headers.append((b"content-type", b"application/json"))
        request_headers.append((b"content-length", str(len(body)).encode("ascii")))
    inbound = [
        {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }
    ]

    async def receive() -> dict[str, object]:
        if inbound:
            return inbound.pop(0)
        return {"type": "http.disconnect"}

    outbound: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        outbound.append(message)

    await app(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": request_headers,
        },
        receive,
        send,
    )
    start = outbound[0]
    response_headers = {
        name: value
        for name, value in start.get("headers", ())  # type: ignore[union-attr]
    }
    response_body = b"".join(
        message.get("body", b"")  # type: ignore[arg-type]
        for message in outbound[1:]
        if message.get("type") == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body


async def _wait_for_state(app: ControlPlane, run_id: str, state: str) -> None:
    for _ in range(200):
        if app.status(run_id)["state"] == state:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"run {run_id!r} did not reach {state!r}")


def _json(body: bytes) -> dict[str, Any]:
    value = json.loads(body)
    assert isinstance(value, dict)
    return value


def test_cors_is_exact_origin_allowlisted_and_preflight_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteRunStore(tmp_path / "runs.db")
        app = ControlPlane(
            AsyncGraphExecutor(store, workers={}),
            allowed_origins=("https://console.example",),
        )
        status, headers, body = await _request(
            app,
            "OPTIONS",
            "/v1/runs",
            headers=[
                (b"origin", b"https://console.example"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"authorization, content-type"),
            ],
        )
        assert status == 204
        assert body == b""
        assert headers[b"access-control-allow-origin"] == b"https://console.example"
        assert b"Authorization" in headers[b"access-control-allow-headers"]

        status, headers, body = await _request(
            app,
            "GET",
            "/v1/runs/missing/status",
            headers=[(b"origin", b"https://console.example")],
        )
        assert status == 404
        assert headers[b"access-control-allow-origin"] == b"https://console.example"
        assert _json(body)["error"]["code"] == "run_not_found"

        status, headers, body = await _request(
            app,
            "GET",
            "/v1/runs/missing/status",
            headers=[(b"origin", b"https://evil.example")],
        )
        assert status == 403
        assert b"access-control-allow-origin" not in headers
        assert _json(body)["error"]["code"] == "origin_not_allowed"

    asyncio.run(scenario())


def test_reference_workflow_can_be_inspected_then_submitted_to_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def worker(context: TaskExecutionContext) -> WorkerResult:
            return WorkerResult({"task_id": context.task.task_id, "reference": True})

        store = SQLiteRunStore(tmp_path / "runs.db")
        app = ControlPlane(
            AsyncGraphExecutor(store, workers={"work": worker}),
            reference_workflows={"fixture": _workflow()},
        )

        status, _, body = await _request(app, "GET", "/v1/reference-workflows")
        assert status == 200
        listing = _json(body)
        assert [item["workflow_id"] for item in listing["workflows"]] == ["fixture"]
        assert "workflow" not in listing["workflows"][0]

        status, _, body = await _request(
            app,
            "GET",
            "/v1/reference-workflows/fixture",
        )
        assert status == 200
        reference = _json(body)
        assert reference["workflow_id"] == "fixture"
        assert reference["workflow_digest"]
        assert reference["workflow"]["tasks"][0]["task_id"] == "work"

        status, _, _ = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"run_id": "reference-run", "workflow": reference["workflow"]},
        )
        assert status == 202
        await _wait_for_state(app, "reference-run", "completed")
        assert app.inspect("reference-run")["outputs"]["work"]["reference"] is True

    asyncio.run(scenario())


def test_http_submit_status_inspect_and_resumable_sse_are_runtime_backed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async def worker(context: TaskExecutionContext) -> WorkerResult:
            await asyncio.sleep(0.01)
            return WorkerResult(
                {"task_id": context.task.task_id, "safe": True},
                Usage(tokens=12, cost_microusd=2, context_bytes=90),
            )

        store = SQLiteRunStore(tmp_path / "runs.db")
        runtime = AsyncGraphExecutor(store, workers={"work": worker})
        app = ControlPlane(runtime, event_poll_seconds=0.001)

        status, headers, body = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"run_id": "run-http", "workflow": _workflow()},
        )
        assert status == 202
        assert headers[b"location"] == b"/v1/runs/run-http"
        assert _json(body)["workflow_digest"]

        await _wait_for_state(app, "run-http", "completed")
        status, _, body = await _request(app, "GET", "/v1/runs/run-http/status")
        assert status == 200
        run_status = _json(body)
        assert run_status["state"] == "completed"
        assert run_status["last_event_id"] == str(run_status["event_count"])

        status, _, body = await _request(app, "GET", "/v1/runs/run-http/inspect")
        assert status == 200
        inspection = _json(body)
        assert inspection["outputs"] == {"work": {"safe": True, "task_id": "work"}}
        assert inspection["actual_usage"] == {
            "tokens": 12,
            "cost_microusd": 2,
            "context_bytes": 90,
        }
        assert inspection["definition"]["manifest_revision"] == 3

        status, sse_headers, stream = await _request(app, "GET", "/v1/runs/run-http/events")
        assert status == 200
        assert sse_headers[b"content-type"] == b"text/event-stream; charset=utf-8"
        ids = [
            int(line.removeprefix(b"id: "))
            for line in stream.splitlines()
            if line.startswith(b"id: ")
        ]
        assert ids == list(range(1, len(ids) + 1))
        assert b"event: run.completed" in stream

        resume_after = ids[-2]
        status, _, resumed = await _request(
            app,
            "GET",
            "/v1/runs/run-http/events",
            headers=[(b"last-event-id", str(resume_after).encode("ascii"))],
        )
        assert status == 200
        resumed_ids = [
            int(line.removeprefix(b"id: "))
            for line in resumed.splitlines()
            if line.startswith(b"id: ")
        ]
        assert resumed_ids == [ids[-1]]

        status, _, body = await _request(
            app,
            "GET",
            "/v1/runs/run-http/events",
            headers=[(b"last-event-id", b"999999")],
        )
        assert status == 409
        assert _json(body)["error"]["code"] == "cursor_ahead"

    asyncio.run(scenario())


def test_http_parser_fails_closed_on_duplicate_unknown_and_malformed_fields(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async def worker(_context: TaskExecutionContext) -> WorkerResult:
            return WorkerResult({"safe": True})

        app = ControlPlane(
            AsyncGraphExecutor(
                SQLiteRunStore(tmp_path / "runs.db"),
                workers={"work": worker},
            )
        )

        status, _, body = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"workflow": _workflow(), "surprise": True},
        )
        assert status == 400
        assert _json(body)["error"]["code"] == "unknown_field"

        status, _, body = await _request(
            app,
            "POST",
            "/v1/runs",
            raw_body=b'{"workflow":{},"workflow":{}}',
        )
        assert status == 400
        assert _json(body)["error"]["code"] == "invalid_json"

        malformed = _workflow()
        malformed["tasks"][0]["profiles"][0]["undeclared"] = 7
        status, _, body = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"workflow": malformed},
        )
        assert status == 422
        assert _json(body)["error"]["code"] == "invalid_workflow"

        status, _, body = await _request(
            app,
            "POST",
            "/v1/runs",
            raw_body=b"{}",
            headers=[(b"content-type", b"text/plain")],
        )
        assert status == 415
        assert _json(body)["error"]["code"] == "unsupported_media_type"

    asyncio.run(scenario())


def test_cancel_is_durable_then_cooperative_and_cannot_be_faked_after_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def worker(_context: TaskExecutionContext) -> WorkerResult:
            started.set()
            await asyncio.sleep(10)
            return WorkerResult({"unexpected": True})

        store = SQLiteRunStore(tmp_path / "runs.db")
        runtime = AsyncGraphExecutor(store, workers={"work": worker})
        app = ControlPlane(runtime)
        status, _, _ = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"run_id": "cancel-me", "workflow": _workflow()},
        )
        assert status == 202
        await asyncio.wait_for(started.wait(), timeout=1)

        detached = ControlPlane(runtime)
        with pytest.raises(ControlAPIError, match="not controlled") as unavailable:
            await detached.cancel("cancel-me")
        assert unavailable.value.code == "executor_unavailable"
        assert not any(
            event.event_type == "control.cancel_requested" for event in store.events("cancel-me")
        )

        status, _, body = await _request(
            app,
            "POST",
            "/v1/runs/cancel-me/cancel",
            value={"reason": "operator stopped this run"},
        )
        assert status == 202
        assert _json(body)["state"] == "cancelling"
        await _wait_for_state(app, "cancel-me", "cancelled")

        events = store.events("cancel-me")
        kinds = [event.event_type for event in events]
        assert kinds.index("control.cancel_requested") < kinds.index("run.cancelled")
        assert "run.completed" not in kinds

    asyncio.run(scenario())


def test_irreversible_approval_requires_exact_scope_and_authenticated_grant(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        secret = b"control-plane-test-secret-material!"
        now_ms = 10_000
        store = SQLiteRunStore(tmp_path / "runs.db")
        broker = SQLiteEffectBroker(
            tmp_path / "effects.db",
            broker_id="control-broker",
            trusted_approval_keys={"approver-v1": secret},
            clock_ms=lambda: now_ms,
        )
        runtime = AsyncGraphExecutor(store, workers={}, effect_broker=broker)
        app = ControlPlane(runtime, effect_broker=broker)
        workflow = _workflow(
            effect={
                "kind": "irreversible_write",
                "resource": "miami-dade/public-alerts",
                "requires_approval": True,
                "idempotency_key": "control-api-alert-1",
            }
        )
        await app.submit(workflow, run_id="approval-run")
        await _wait_for_state(app, "approval-run", "awaiting_effects")
        intent_view = app.inspect("approval-run")["effects"][0]  # type: ignore[index]
        intent_id = intent_view["intent_id"]
        scope = {
            field: intent_view[field]
            for field in ("intent_id", "run_id", "effect_digest", "resource", "action")
        }

        rogue = broker.propose(
            run_id="approval-run",
            action="rogue",
            resource="miami-dade/public-alerts",
            effect_class=EffectClass.IDEMPOTENT_WRITE,
            idempotency_key="rogue-control-intent",
            payload={"not": "from the executor"},
        )
        rogue_scope = {
            "intent_id": rogue.intent_id,
            "run_id": rogue.run_id,
            "effect_digest": rogue.effect_digest,
            "resource": rogue.resource,
            "action": rogue.action,
        }
        with pytest.raises(ControlAPIError) as unbound:
            app.approve("approval-run", rogue.intent_id, scope=rogue_scope)
        assert unbound.value.code == "effect_not_found"
        assert broker.get(rogue.intent_id).state is EffectState.PROPOSED

        wrong_scope = dict(scope)
        wrong_scope["resource"] = "somewhere/else"
        with pytest.raises(ControlAPIError) as mismatch:
            app.approve("approval-run", intent_id, scope=wrong_scope)  # type: ignore[arg-type]
        assert mismatch.value.code == "approval_scope_mismatch"
        assert broker.get(intent_id).state is EffectState.PROPOSED

        status, _, body = await _request(
            app,
            "POST",
            f"/v1/runs/approval-run/effects/{intent_id}/approve",
            value={"scope": {**scope, "undeclared": True}},
        )
        assert status == 400
        assert _json(body)["error"]["code"] == "unknown_field"
        assert broker.get(intent_id).state is EffectState.PROPOSED

        intent = broker.get(intent_id)
        grant = ApprovalAuthority("approver-v1", secret).issue(
            intent,
            principal="judge@example.test",
            now_ms=now_ms,
            ttl_ms=1_000,
        )
        status, _, body = await _request(
            app,
            "POST",
            f"/v1/runs/approval-run/effects/{intent_id}/approve",
            value={"scope": scope, "grant": {**grant.claims(), "signature": grant.signature}},
        )
        assert status == 200
        approved = _json(body)
        assert approved["effect"]["state"] == "approved"
        assert approved["executed_externally"] is False
        assert broker.get(intent_id).state is EffectState.APPROVED
        assert any(
            event.event_type == "control.effect_approved" for event in store.events("approval-run")
        )

    asyncio.run(scenario())


def test_bearer_auth_protects_mutations_and_sensitive_inspection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        token = "finite-control-test-token-material-32"

        async def worker(_context: TaskExecutionContext) -> WorkerResult:
            return WorkerResult({"safe": True})

        app = ControlPlane(
            AsyncGraphExecutor(
                SQLiteRunStore(tmp_path / "runs-auth.db"),
                workers={"work": worker},
            ),
            bearer_token=token,
            allow_anonymous_status_stream=True,
        )
        assert app.authentication_enabled is True

        status, headers, body = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"run_id": "protected-run", "workflow": _workflow()},
        )
        assert status == 401
        assert headers[b"www-authenticate"].startswith(b"Bearer")
        assert _json(body)["error"]["code"] == "unauthorized"

        authorized = [(b"authorization", f"Bearer {token}".encode("ascii"))]
        status, _, _ = await _request(
            app,
            "POST",
            "/v1/runs",
            value={"run_id": "protected-run", "workflow": _workflow()},
            headers=authorized,
        )
        assert status == 202
        await _wait_for_state(app, "protected-run", "completed")

        status, _, _ = await _request(app, "GET", "/v1/runs/protected-run/status")
        assert status == 200
        status, _, body = await _request(app, "GET", "/v1/runs/protected-run/inspect")
        assert status == 401
        assert _json(body)["error"]["code"] == "unauthorized"
        status, _, _ = await _request(
            app,
            "GET",
            "/v1/runs/protected-run/inspect",
            headers=authorized,
        )
        assert status == 200

        duplicate = [*authorized, *authorized]
        status, _, body = await _request(
            app,
            "GET",
            "/v1/runs/protected-run/inspect",
            headers=duplicate,
        )
        assert status == 401
        assert token.encode("ascii") not in body

    asyncio.run(scenario())


def test_bearer_configuration_rejects_weak_or_ambiguous_tokens(tmp_path: Path) -> None:
    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        return WorkerResult({"safe": True})

    runtime = AsyncGraphExecutor(
        SQLiteRunStore(tmp_path / "runs-weak.db"),
        workers={"work": worker},
    )
    with pytest.raises(ValueError, match="bearer_token"):
        ControlPlane(runtime, bearer_token="too-short")
    with pytest.raises(ValueError, match="bearer_token"):
        ControlPlane(runtime, bearer_token="x" * 31 + " ")
