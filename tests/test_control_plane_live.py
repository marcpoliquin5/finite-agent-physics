from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn

from agent_physics.control_api import ControlPlane
from agent_physics.control_service import AdaptiveControlRuntime, build_control_service
from agent_physics.effects import ApprovalAuthority, SQLiteEffectBroker
from agent_physics.examples import miami_eoc_graph
from agent_physics.run_store import SQLiteRunStore
from agent_physics.stormshift_runtime import StormShiftRuntime, stormshift_envelope
from agent_physics.workflow_ir import compile_contracts


TOKEN = "finite-live-socket-e2e-token-material-0001"
ORIGIN = "http://127.0.0.1:4173"
APPROVAL_SECRET = b"finite-live-e2e-approval-secret-material-0001"


@contextmanager
def _serve(app: ControlPlane) -> Iterator[int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="on",
            access_log=False,
            log_level="critical",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name=f"finite-live-e2e-{port}",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise AssertionError("uvicorn did not start on the supplied ephemeral socket")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "uvicorn did not stop cleanly"


def _request(
    port: int,
    method: str,
    path: str,
    *,
    value: object | None = None,
    raw_body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    body = raw_body
    request_headers = dict(headers or {})
    if value is not None:
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            payload,
        )
    finally:
        connection.close()


def _json(body: bytes) -> dict[str, Any]:
    value = json.loads(body)
    assert isinstance(value, dict)
    return value


def _javascript_json_round_trip(value: object) -> object:
    """Emulate the browser's binary64 JSON.parse/stringify wire boundary."""

    parsed = json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        parse_int=float,
    )

    def stringify(item: object) -> object:
        if isinstance(item, dict):
            return {key: stringify(child) for key, child in item.items()}
        if isinstance(item, list):
            return [stringify(child) for child in item]
        if type(item) is float and item.is_integer():
            return int(item)
        return item

    return json.loads(
        json.dumps(stringify(parsed), sort_keys=True, separators=(",", ":"))
    )


def _authorized(*, origin: bool = True) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if origin:
        headers["Origin"] = ORIGIN
    return headers


def _sse(
    port: int,
    run_id: str,
    *,
    last_event_id: int | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    headers = {
        **_authorized(),
        "Accept": "text/event-stream",
    }
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", f"/v1/runs/{run_id}/events", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        response_headers = {
            key.lower(): value for key, value in response.getheaders()
        }
        body = response.read().decode("utf-8").replace("\r\n", "\n")
    finally:
        connection.close()
    events: list[dict[str, Any]] = []
    for frame in body.split("\n\n"):
        data = next(
            (line.removeprefix("data: ") for line in frame.splitlines() if line.startswith("data: ")),
            None,
        )
        if data is not None:
            event = json.loads(data)
            assert isinstance(event, dict)
            events.append(event)
    return response_headers, events


def _wait_state(port: int, run_id: str, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status, _, body = _request(
            port,
            "GET",
            f"/v1/runs/{run_id}/status",
            headers=_authorized(),
        )
        assert status == 200
        payload = _json(body)
        if payload["state"] == expected:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"run {run_id!r} did not reach {expected!r}")


def _control(
    port: int,
    run_id: str,
    *,
    kind: str,
    revision: int,
    occurred_at_ms: int,
    details: Mapping[str, object],
) -> tuple[int, dict[str, Any]]:
    status, _, body = _request(
        port,
        "POST",
        f"/v1/runs/{run_id}/control-events",
        value={
            "kind": kind,
            "expected_revision": revision,
            "occurred_at_ms": occurred_at_ms,
            "details": dict(details),
        },
        headers=_authorized(),
    )
    return status, _json(body)


def test_real_socket_auth_cors_adaptive_controls_sse_approval_and_restart(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    app = build_control_service(
        state,
        bearer_token=TOKEN,
        allowed_origins=(ORIGIN,),
        trusted_approval_keys={"live-e2e-approver": APPROVAL_SECRET},
        max_control_events_per_run=6,
    )
    with _serve(app) as port:
        for path, expected in (("/healthz", "ok"), ("/readyz", "ready")):
            status, headers, body = _request(port, "GET", path)
            assert status == 200
            assert _json(body)["status"] == expected
            assert headers["cache-control"] == "no-store"

        status, headers, body = _request(port, "GET", "/v1/reference-workflows")
        assert status == 401
        assert headers["www-authenticate"] == 'Bearer realm="finite-control"'
        assert _json(body)["error"]["code"] == "unauthorized"

        status, _, body = _request(
            port,
            "POST",
            "/v1/runs",
            raw_body=b'{"workflow":NaN}',
            headers={"Origin": ORIGIN},
        )
        assert status == 401
        assert _json(body)["error"]["code"] == "unauthorized"

        status, _, body = _request(
            port,
            "GET",
            "/v1/reference-workflows",
            headers={**_authorized(origin=False), "Origin": "https://evil.example"},
        )
        assert status == 403
        assert _json(body)["error"]["code"] == "origin_not_allowed"

        status, headers, body = _request(
            port,
            "OPTIONS",
            "/v1/runs",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert status == 204
        assert body == b""
        assert headers["access-control-allow-origin"] == ORIGIN

        status, headers, body = _request(
            port,
            "GET",
            "/v1/reference-workflows/stormshift",
            headers=_authorized(),
        )
        assert status == 200
        assert headers["access-control-allow-origin"] == ORIGIN
        workflow = _javascript_json_round_trip(_json(body)["workflow"])
        assert isinstance(workflow, dict)

        status, headers, body = _request(
            port,
            "POST",
            "/v1/runs",
            value={
                "run_id": "socket-adaptive",
                "workflow": workflow,
                "start_paused": True,
            },
            headers=_authorized(),
        )
        assert status == 202
        assert headers["location"] == "/v1/runs/socket-adaptive"
        assert _json(body)["run"]["state"] == "running"

        status, _, body = _request(
            port,
            "GET",
            "/v1/runs/socket-adaptive/adaptive-replay",
            headers=_authorized(),
        )
        assert status == 200
        replay = _json(body)
        assert replay["passed"] is True
        assert replay["worker_or_provider_calls"] == 0
        revision = replay["final_state"]["revision"]

        status, payload = _control(
            port,
            "socket-adaptive",
            kind="arbitrary.method",
            revision=revision,
            occurred_at_ms=0,
            details={},
        )
        assert status == 422
        assert payload["error"]["code"] == "unsupported_control_event"

        status, payload = _control(
            port,
            "socket-adaptive",
            kind="provider.capacity",
            revision=revision,
            occurred_at_ms=0,
            details={"provider": "undeclared-provider", "capacity": 0},
        )
        assert status == 422
        assert payload["error"]["code"] == "unknown_provider"

        controls = (
            (
                "budget.cut",
                0,
                {"tokens": 7_000, "cost_microusd": 6_000, "context_bytes": 29_500},
            ),
            (
                "provider.429",
                1,
                {"provider": "simulated-watsonx", "reset_at_ms": 2},
            ),
            ("provider.reset", 2, {"provider": "simulated-watsonx"}),
            (
                "provider.capacity",
                2,
                {"provider": "simulated-watsonx", "capacity": 0},
            ),
            (
                "provider.capacity",
                2,
                {"provider": "simulated-watsonx", "capacity": 2},
            ),
        )
        stale_revision = revision
        for kind, occurred_at_ms, details in controls:
            status, payload = _control(
                port,
                "socket-adaptive",
                kind=kind,
                revision=revision,
                occurred_at_ms=occurred_at_ms,
                details=details,
            )
            assert status == 202
            assert payload["replay"]["passed"] is True
            assert payload["external_effects_committed"] == 0
            revision = payload["state"]["revision"]

        status, payload = _control(
            port,
            "socket-adaptive",
            kind="provider.capacity",
            revision=stale_revision,
            occurred_at_ms=2,
            details={"provider": "simulated-watsonx", "capacity": 1},
        )
        assert status == 409
        assert payload["error"]["code"] == "stale_adaptive_revision"

        status, payload = _control(
            port,
            "socket-adaptive",
            kind="runtime.resume",
            revision=revision,
            occurred_at_ms=2,
            details={},
        )
        assert status == 202
        assert payload["decision"] is None
        settled = _wait_state(port, "socket-adaptive", "awaiting_effects")
        assert settled["event_count"] >= 50

        sse_headers, events = _sse(port, "socket-adaptive")
        assert sse_headers["content-type"].startswith("text/event-stream")
        assert [event["sequence"] for event in events] == list(
            range(1, len(events) + 1)
        )
        assert len({event["event_id"] for event in events}) == len(events)
        assert events[-1]["type"] == "run.awaiting_effects"
        control_kinds = {
            event["payload"]["event"]["kind"]
            for event in events
            if event["type"] == "adaptive.controller_transition"
        }
        assert {
            "budget.cut",
            "provider.429",
            "provider.reset",
            "provider.capacity",
        } <= control_kinds

        status, _, body = _request(
            port,
            "GET",
            "/v1/runs/socket-adaptive/inspect",
            headers=_authorized(),
        )
        assert status == 200
        inspection = _json(body)
        assert len(inspection["outputs"]) == 10
        assert inspection["adaptive_replay"]["passed"] is True
        assert inspection["adaptive_replay"]["final_state"]["shed_task_ids"] == [
            "social_signal_scan"
        ]
        assert len(inspection["effects"]) == 1
        effect = inspection["effects"][0]
        assert effect["state"] == "proposed"

        exact_scope = {
            key: effect[key]
            for key in ("intent_id", "run_id", "effect_digest", "resource", "action")
        }
        wrong_scope = {**exact_scope, "resource": "arbitrary/resource"}
        status, _, body = _request(
            port,
            "POST",
            (
                f"/v1/runs/socket-adaptive/effects/{effect['intent_id']}"
                "/approve"
            ),
            value={"scope": wrong_scope},
            headers=_authorized(),
        )
        assert status == 403
        assert _json(body)["error"]["code"] == "approval_scope_mismatch"

        last_before_approval = events[-1]["sequence"]
        intent = app.effect_broker.get(effect["intent_id"])
        grant = ApprovalAuthority(
            "live-e2e-approver",
            APPROVAL_SECRET,
        ).issue(
            intent,
            principal="live-e2e-judge",
            now_ms=int(time.time() * 1_000),
            ttl_ms=60_000,
        )
        status, _, body = _request(
            port,
            "POST",
            (
                f"/v1/runs/socket-adaptive/effects/{effect['intent_id']}"
                "/approve"
            ),
            value={
                "scope": exact_scope,
                "grant": {**grant.claims(), "signature": grant.signature},
            },
            headers=_authorized(),
        )
        assert status == 200
        approved = _json(body)
        assert approved["effect"]["state"] == "approved"
        assert approved["executed_externally"] is False

        _, resumed_events = _sse(
            port,
            "socket-adaptive",
            last_event_id=last_before_approval,
        )
        assert [event["type"] for event in resumed_events] == [
            "control.effect_approved"
        ]

    restarted = build_control_service(
        state,
        bearer_token=TOKEN,
        allowed_origins=(ORIGIN,),
        max_control_events_per_run=6,
    )
    bound_worker = next(iter(restarted.runtime._workers.values()))  # type: ignore[attr-defined]
    fixture_workers = bound_worker.__self__
    assert sum(fixture_workers.call_counts.values()) == 0
    with _serve(restarted) as port:
        status, _, body = _request(
            port,
            "GET",
            "/v1/runs/socket-adaptive/adaptive-replay",
            headers=_authorized(),
        )
        assert status == 200
        replay = _json(body)
        assert replay["passed"] is True
        assert replay["worker_or_provider_calls"] == 0
        assert sum(fixture_workers.call_counts.values()) == 0
        assert _wait_state(port, "socket-adaptive", "awaiting_effects")

        status, payload = _control(
            port,
            "socket-adaptive",
            kind="provider.capacity",
            revision=replay["final_state"]["revision"],
            occurred_at_ms=replay["final_state"]["now_ms"],
            details={"provider": "simulated-watsonx", "capacity": 1},
        )
        assert status == 429
        assert payload["error"]["code"] == "control_event_limit"

        status, _, body = _request(
            port,
            "POST",
            "/v1/runs",
            value={"run_id": "socket-adaptive", "workflow": workflow},
            headers=_authorized(),
        )
        assert status == 409
        assert _json(body)["error"]["code"] == "run_exists"

        status, _, body = _request(
            port,
            "POST",
            "/v1/runs/socket-adaptive/cancel",
            value={"reason": "too late"},
            headers=_authorized(),
        )
        assert status == 409
        assert _json(body)["error"]["code"] == "run_terminal"


def test_real_socket_cancel_and_crash_ambiguous_coordinator_recovery(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    store = SQLiteRunStore(state / "runs.sqlite3")
    broker = SQLiteEffectBroker(
        state / "effects.sqlite3",
        broker_id="finite-control-service",
    )
    stormshift = StormShiftRuntime(store, broker)
    runtime = AdaptiveControlRuntime(
        store,
        broker,
        workers=stormshift.fixture_workers.workers,
        output_validator=stormshift.fixture_workers.validate_output,
        crash_after_dispatch_task_ids={"crash-recovery": ("social_signal_scan",)},
    )
    workflow = compile_contracts(miami_eoc_graph(), stormshift_envelope()).to_python()
    app = ControlPlane(
        runtime,
        effect_broker=broker,
        bearer_token=TOKEN,
        allowed_origins=(ORIGIN,),
        reference_workflows={"stormshift": workflow},
    )

    with _serve(app) as port:
        status, _, body = _request(
            port,
            "POST",
            "/v1/runs",
            value={
                "run_id": "cancel-paused",
                "workflow": workflow,
                "start_paused": True,
            },
            headers=_authorized(),
        )
        assert status == 202
        assert _json(body)["run"]["state"] == "running"
        status, _, body = _request(
            port,
            "POST",
            "/v1/runs/cancel-paused/cancel",
            value={"reason": "socket operator cancellation"},
            headers=_authorized(),
        )
        assert status == 202
        assert _json(body)["cancellation_requested"] is True
        _wait_state(port, "cancel-paused", "cancelled")
        _, cancelled_events = _sse(port, "cancel-paused")
        assert cancelled_events[-1]["type"] == "run.cancelled"
        assert any(
            event["type"] == "control.cancel_requested"
            for event in cancelled_events
        )

        status, _, _ = _request(
            port,
            "POST",
            "/v1/runs",
            value={"run_id": "crash-recovery", "workflow": workflow},
            headers=_authorized(),
        )
        assert status == 202
        deadline = time.monotonic() + 10
        while "crash-recovery" in app._active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "crash-recovery" not in app._active
        assert _wait_state(port, "crash-recovery", "running")

        status, _, body = _request(
            port,
            "GET",
            "/v1/runs/crash-recovery/adaptive-replay",
            headers=_authorized(),
        )
        assert status == 200
        before = _json(body)
        state_before = before["final_state"]
        assert state_before["inflight"][0]["task_id"] == "social_signal_scan"
        completed_before = set(state_before["completed_task_ids"])
        call_counts_before = stormshift.fixture_workers.call_counts

        status, payload = _control(
            port,
            "crash-recovery",
            kind="coordinator.recover",
            revision=state_before["revision"],
            occurred_at_ms=state_before["now_ms"],
            details={},
        )
        assert status == 202
        assert payload["execution_resumed"] is True
        assert payload["replay"]["passed"] is True
        assert payload["state"]["unknown_task_ids"] == ["social_signal_scan"]
        assert completed_before == set(payload["state"]["completed_task_ids"])
        _wait_state(port, "crash-recovery", "awaiting_effects")
        assert stormshift.fixture_workers.call_counts == call_counts_before

        status, payload = _control(
            port,
            "crash-recovery",
            kind="coordinator.recover",
            revision=payload["state"]["revision"],
            occurred_at_ms=payload["state"]["now_ms"],
            details={},
        )
        assert status == 409
        assert payload["error"]["code"] in {
            "run_terminal",
            "adaptive_control_conflict",
        }


def test_shared_effect_broker_isolates_sequential_concurrent_and_restarted_runs(
    tmp_path: Path,
) -> None:
    state = tmp_path / "shared-effect-state"
    app = build_control_service(
        state,
        bearer_token=TOKEN,
        allowed_origins=(ORIGIN,),
    )
    run_ids = (
        "effect-isolation-sequential",
        "effect-isolation-concurrent-a",
        "effect-isolation-concurrent-b",
    )

    with _serve(app) as port:
        status, _, body = _request(
            port,
            "GET",
            "/v1/reference-workflows/stormshift",
            headers=_authorized(),
        )
        assert status == 200
        workflow = _javascript_json_round_trip(_json(body)["workflow"])

        status, _, _ = _request(
            port,
            "POST",
            "/v1/runs",
            value={"run_id": run_ids[0], "workflow": workflow},
            headers=_authorized(),
        )
        assert status == 202
        _wait_state(port, run_ids[0], "awaiting_effects")

        def submit(run_id: str) -> tuple[int, dict[str, Any]]:
            submit_status, _, submit_body = _request(
                port,
                "POST",
                "/v1/runs",
                value={"run_id": run_id, "workflow": workflow},
                headers=_authorized(),
            )
            return submit_status, _json(submit_body)

        with ThreadPoolExecutor(max_workers=2) as pool:
            submissions = list(pool.map(submit, run_ids[1:]))
        assert [item[0] for item in submissions] == [202, 202]

        inspections: dict[str, dict[str, Any]] = {}
        for run_id in run_ids:
            _wait_state(port, run_id, "awaiting_effects")
            status, _, body = _request(
                port,
                "GET",
                f"/v1/runs/{run_id}/inspect",
                headers=_authorized(),
            )
            assert status == 200
            inspections[run_id] = _json(body)

        effects = [inspections[run_id]["effects"][0] for run_id in run_ids]
        assert {effect["run_id"] for effect in effects} == set(run_ids)
        assert len({effect["intent_id"] for effect in effects}) == len(run_ids)
        assert len({effect["idempotency_key"] for effect in effects}) == len(run_ids)
        assert all(effect["state"] == "proposed" for effect in effects)

    restarted = build_control_service(
        state,
        bearer_token=TOKEN,
        allowed_origins=(ORIGIN,),
    )
    bound_worker = next(iter(restarted.runtime._workers.values()))  # type: ignore[attr-defined]
    fixture_workers = bound_worker.__self__
    assert sum(fixture_workers.call_counts.values()) == 0
    with _serve(restarted) as port:
        for run_id in run_ids:
            status, _, body = _request(
                port,
                "GET",
                f"/v1/runs/{run_id}/inspect",
                headers=_authorized(),
            )
            assert status == 200
            inspection = _json(body)
            assert inspection["effects"][0]["run_id"] == run_id
            assert inspection["adaptive_replay"]["passed"] is True
            assert inspection["adaptive_replay"]["worker_or_provider_calls"] == 0
        assert sum(fixture_workers.call_counts.values()) == 0
