"""Digest-bound real-socket load proof for the adaptive FINITE control plane.

This benchmark is deliberately local and deterministic.  It exercises the
actual ASGI service over TCP, but it makes no model, provider, network-egress,
or external-effect commit calls.  Its receipts prove bounded control-plane
concurrency, durable replay, and proposal-only effect isolation; they are not
claims about live Granite latency or production infrastructure capacity.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, cast

from .control_api import ControlPlane
from .control_service import build_control_service
from .examples import miami_eoc_graph
from .serialization import canonical_json, content_digest
from .stormshift_runtime import stormshift_envelope
from .workflow_ir import compile_contracts


CONTRACT_SCHEMA_VERSION = "finite-live-load-contract/v1"
ENVIRONMENT_SCHEMA_VERSION = "finite-live-load-environment/v1"
RECORD_SCHEMA_VERSION = "finite-live-load-record/v1"
REPORT_SCHEMA_VERSION = "finite-live-load-report/v1"
EVIDENCE_SCHEMA_VERSION = "finite-live-load-evidence/v1"
MANIFEST_SCHEMA_VERSION = "finite-live-load-files/v1"
DEFAULT_CONCURRENCY = 32
DEFAULT_ROUNDS = 2
CONTROL_EVENT_LIMIT = 4
MIN_CONCURRENCY = 32
MAX_CONCURRENCY = 128
MAX_TOTAL_RUNS = 4_096
_PACKAGE_DIRECTORY = Path(__file__).resolve().parent
_EVIDENCE_FILES = (
    "contract.json",
    "environment.json",
    "raw-records.jsonl",
    "report.json",
    "evidence.json",
)


class LiveLoadInvariantError(ValueError):
    """The load contract, execution, or evidence bundle is invalid."""


class _HTTPResult:
    """One measured request plus its parsed response body."""

    __slots__ = ("body", "duration_ns", "error_code", "operation", "status")

    def __init__(
        self,
        *,
        operation: str,
        status: int,
        duration_ns: int,
        body: Mapping[str, Any],
    ) -> None:
        self.operation = operation
        self.status = status
        self.duration_ns = duration_ns
        self.body = dict(body)
        error = self.body.get("error")
        self.error_code = error.get("code") if isinstance(error, dict) else None

    def receipt(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "status": self.status,
            "duration_ns": self.duration_ns,
            "error_code": self.error_code,
            "response_digest": content_digest(self.body),
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_digests() -> list[dict[str, str]]:
    components = (
        "adaptive_runtime.py",
        "control_api.py",
        "control_service.py",
        "effects.py",
        "run_store.py",
        "serialization.py",
        "stormshift_runtime.py",
        "workflow_ir.py",
        "live_load.py",
    )
    return [
        {
            "component": f"agent_physics/{name}",
            "sha256": _sha256_bytes((_PACKAGE_DIRECTORY / name).read_bytes()),
        }
        for name in components
    ]


def _unsigned(value: Mapping[str, Any], digest_field: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(digest_field, None)
    return result


def _require_integer(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise LiveLoadInvariantError(
            f"{name} must be an integer from {minimum} through {maximum}"
        )
    return value


def build_live_load_contract(
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    rounds: int = DEFAULT_ROUNDS,
    request_timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Freeze the bounded load design before observing any timings."""

    concurrency = _require_integer(
        concurrency,
        name="concurrency",
        minimum=MIN_CONCURRENCY,
        maximum=MAX_CONCURRENCY,
    )
    rounds = _require_integer(rounds, name="rounds", minimum=1, maximum=128)
    if concurrency * rounds > MAX_TOTAL_RUNS:
        raise LiveLoadInvariantError(f"the load proof is capped at {MAX_TOTAL_RUNS} runs")
    if (
        isinstance(request_timeout_seconds, bool)
        or not isinstance(request_timeout_seconds, (int, float))
        or not math.isfinite(float(request_timeout_seconds))
        or not 1.0 <= float(request_timeout_seconds) <= 300.0
    ):
        raise LiveLoadInvariantError(
            "request_timeout_seconds must be a finite number from 1 through 300"
        )

    workflow = compile_contracts(miami_eoc_graph(), stormshift_envelope()).to_python()
    fields: dict[str, object] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "benchmark_id": "finite-live-adaptive-load-v1",
        "transport": "real-loopback-tcp-http",
        "server": "uvicorn-asgi",
        "timer": "time.perf_counter_ns",
        "concurrency": concurrency,
        "rounds": rounds,
        "total_accepted_runs": concurrency * rounds,
        "max_active_runs": concurrency,
        "max_control_events_per_run": CONTROL_EVENT_LIMIT,
        "expected_active_limit_rejections": rounds,
        "expected_control_limit_rejections": concurrency * rounds,
        "request_timeout_seconds": float(request_timeout_seconds),
        "workflow_id": "stormshift",
        "workflow_digest": content_digest(workflow),
        "start_mode": "paused-before-first-dispatch",
        "control_sequence": [
            {
                "kind": "budget.cut",
                "relative_occurred_at_ms": 0,
                "details": {
                    "tokens": 7_000,
                    "cost_microusd": 6_000,
                    "context_bytes": 29_500,
                },
            },
            {
                "kind": "provider.429",
                "relative_occurred_at_ms": 1,
                "details": {
                    "provider": "simulated-watsonx",
                    "relative_reset_at_ms": 2,
                },
            },
            {
                "kind": "provider.reset",
                "relative_occurred_at_ms": 2,
                "details": {"provider": "simulated-watsonx"},
            },
            {
                "kind": "runtime.resume",
                "relative_occurred_at_ms": 2,
                "details": {},
            },
        ],
        "required_terminal_state": "awaiting_effects",
        "required_effect_state": "proposed",
        "source_digests": _source_digests(),
        "claim_boundaries": [
            "local deterministic fixture workers only",
            "no model, Granite, watsonx, or public-network call is measured",
            "effect writes stop at durable proposal and are never externally committed",
            "throughput is descriptive for the disclosed local machine and candidate source",
        ],
        "excluded_identifiers": [
            "bearer credential",
            "hostname",
            "username",
            "state-directory path",
            "ephemeral TCP port",
        ],
    }
    fields["contract_digest"] = content_digest(fields)
    return fields


def _git_metadata() -> tuple[str | None, bool | None]:
    repository = _PACKAGE_DIRECTORY.parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def capture_live_load_environment(contract: Mapping[str, Any]) -> dict[str, object]:
    """Capture non-identifying execution metadata and bind it to the contract."""

    commit, dirty = _git_metadata()
    try:
        uvicorn_version = metadata.version("uvicorn")
    except metadata.PackageNotFoundError:
        uvicorn_version = None
    timer = time.get_clock_info("perf_counter")
    fields: dict[str, object] = {
        "schema_version": ENVIRONMENT_SCHEMA_VERSION,
        "contract_digest": contract["contract_digest"],
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "logical_cpu_count": __import__("os").cpu_count(),
        "uvicorn_version": uvicorn_version,
        "repository_commit": commit,
        "repository_dirty": dirty,
        "perf_counter_resolution_seconds": timer.resolution,
        "perf_counter_monotonic": timer.monotonic,
        "perf_counter_adjustable": timer.adjustable,
    }
    fields["environment_digest"] = content_digest(fields)
    return fields


@contextmanager
def _serve(app: ControlPlane, *, startup_timeout_seconds: float) -> Iterator[int]:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on optional API extra
        raise RuntimeError('Install the load proof with: pip install -e ".[api]"') from exc

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(256)
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
        name="finite-live-load-server",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + startup_timeout_seconds
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise LiveLoadInvariantError("the live load server did not start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=startup_timeout_seconds)
        listener.close()
        if thread.is_alive():
            raise LiveLoadInvariantError("the live load server did not stop cleanly")


def _request(
    port: int,
    token: str,
    operation: str,
    method: str,
    path: str,
    *,
    timeout_seconds: float,
    body: object | None = None,
) -> _HTTPResult:
    encoded: bytes | None = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        encoded = canonical_json(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    started = time.perf_counter_ns()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_seconds)
    try:
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        duration = time.perf_counter_ns() - started
    finally:
        connection.close()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveLoadInvariantError(f"{operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise LiveLoadInvariantError(f"{operation} returned a non-object response")
    return _HTTPResult(
        operation=operation,
        status=response.status,
        duration_ns=duration,
        body=payload,
    )


def _expect(result: _HTTPResult, *, status: int, error_code: str | None = None) -> None:
    if result.status != status or result.error_code != error_code:
        raise LiveLoadInvariantError(
            f"{result.operation} returned HTTP {result.status}/{result.error_code!r}; "
            f"expected {status}/{error_code!r}"
        )


def _state(value: Mapping[str, Any], *, operation: str) -> dict[str, Any]:
    state = value.get("final_state") if "final_state" in value else value.get("state")
    if not isinstance(state, dict):
        raise LiveLoadInvariantError(f"{operation} omitted its adaptive state")
    return state


def _control(
    port: int,
    token: str,
    run_id: str,
    *,
    timeout_seconds: float,
    kind: str,
    revision: int,
    occurred_at_ms: int,
    details: Mapping[str, object],
) -> _HTTPResult:
    return _request(
        port,
        token,
        f"control:{kind}",
        "POST",
        f"/v1/runs/{run_id}/control-events",
        timeout_seconds=timeout_seconds,
        body={
            "kind": kind,
            "expected_revision": revision,
            "occurred_at_ms": occurred_at_ms,
            "details": dict(details),
        },
    )


def _drive_paused_run(
    port: int,
    token: str,
    run_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    initial = _request(
        port,
        token,
        "initial-replay",
        "GET",
        f"/v1/runs/{run_id}/adaptive-replay",
        timeout_seconds=timeout_seconds,
    )
    _expect(initial, status=200)
    if initial.body.get("passed") is not True or initial.body.get("worker_or_provider_calls") != 0:
        raise LiveLoadInvariantError("initial replay was not a zero-call pass")
    current = _state(initial.body, operation=initial.operation)
    revision = cast(int, current.get("revision"))
    base_time = cast(int, current.get("now_ms"))
    if type(revision) is not int or type(base_time) is not int:
        raise LiveLoadInvariantError("initial replay returned invalid revision or logical time")

    specifications: Sequence[tuple[str, int, Mapping[str, object]]] = (
        (
            "budget.cut",
            base_time,
            {"tokens": 7_000, "cost_microusd": 6_000, "context_bytes": 29_500},
        ),
        (
            "provider.429",
            base_time + 1,
            {"provider": "simulated-watsonx", "reset_at_ms": base_time + 2},
        ),
        ("provider.reset", base_time + 2, {"provider": "simulated-watsonx"}),
        ("runtime.resume", base_time + 2, {}),
    )
    controls: list[dict[str, object]] = []
    for kind, occurred_at_ms, details in specifications:
        result = _control(
            port,
            token,
            run_id,
            timeout_seconds=timeout_seconds,
            kind=kind,
            revision=revision,
            occurred_at_ms=occurred_at_ms,
            details=details,
        )
        _expect(result, status=202)
        if (
            result.body.get("external_effects_committed") != 0
            or not isinstance(result.body.get("replay"), dict)
            or result.body["replay"].get("passed") is not True
            or result.body["replay"].get("worker_or_provider_calls") != 0
        ):
            raise LiveLoadInvariantError(f"accepted {kind} control failed its replay boundary")
        revision_state = _state(result.body, operation=result.operation)
        next_revision = revision_state.get("revision")
        if type(next_revision) is not int or next_revision < revision:
            raise LiveLoadInvariantError(f"accepted {kind} control returned an invalid revision")
        revision = next_revision
        controls.append(result.receipt())

    settled_started = time.perf_counter_ns()
    poll_count = 0
    deadline = time.monotonic() + timeout_seconds
    terminal_state: str | None = None
    while time.monotonic() < deadline:
        poll = _request(
            port,
            token,
            "status-poll",
            "GET",
            f"/v1/runs/{run_id}/status",
            timeout_seconds=timeout_seconds,
        )
        _expect(poll, status=200)
        poll_count += 1
        state = poll.body.get("state")
        if state in {"awaiting_effects", "completed", "failed", "cancelled"}:
            terminal_state = cast(str, state)
            break
        time.sleep(0.01)
    settle_duration_ns = time.perf_counter_ns() - settled_started
    if terminal_state != "awaiting_effects":
        raise LiveLoadInvariantError(
            f"run {run_id!r} settled as {terminal_state!r}, not 'awaiting_effects'"
        )
    return {
        "initial_replay": initial.receipt(),
        "controls": controls,
        "settle_duration_ns": settle_duration_ns,
        "status_poll_count": poll_count,
        "terminal_state": terminal_state,
        "revision_after_resume": revision,
    }


def _fixture_call_counts(app: ControlPlane) -> dict[str, int]:
    workers = getattr(app.runtime, "_workers", None)
    if not isinstance(workers, dict) or not workers:
        raise LiveLoadInvariantError("fixture call counters are unavailable")
    owner = getattr(next(iter(workers.values())), "__self__", None)
    counts = getattr(owner, "call_counts", None)
    if not isinstance(counts, dict) or any(type(value) is not int for value in counts.values()):
        raise LiveLoadInvariantError("fixture call counters are malformed")
    return {str(key): int(value) for key, value in sorted(counts.items())}


def _inspect_and_limit(
    port: int,
    token: str,
    run_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    replay = _request(
        port,
        token,
        "final-replay",
        "GET",
        f"/v1/runs/{run_id}/adaptive-replay",
        timeout_seconds=timeout_seconds,
    )
    _expect(replay, status=200)
    replay_state = _state(replay.body, operation=replay.operation)
    if replay.body.get("passed") is not True or replay.body.get("worker_or_provider_calls") != 0:
        raise LiveLoadInvariantError(f"run {run_id!r} failed call-free final replay")

    inspection = _request(
        port,
        token,
        "inspect",
        "GET",
        f"/v1/runs/{run_id}/inspect",
        timeout_seconds=timeout_seconds,
    )
    _expect(inspection, status=200)
    run = inspection.body.get("run")
    effects = inspection.body.get("effects")
    outputs = inspection.body.get("outputs")
    if not isinstance(run, dict) or run.get("state") != "awaiting_effects":
        raise LiveLoadInvariantError(f"run {run_id!r} inspection is not awaiting effects")
    if not isinstance(effects, list) or len(effects) != 1 or not isinstance(effects[0], dict):
        raise LiveLoadInvariantError(f"run {run_id!r} did not expose exactly one effect intent")
    effect = effects[0]
    if effect.get("run_id") != run_id or effect.get("state") != "proposed":
        raise LiveLoadInvariantError(f"run {run_id!r} effect crossed its proposal boundary")
    if not isinstance(outputs, dict) or not all(isinstance(value, dict) for value in outputs.values()):
        raise LiveLoadInvariantError(f"run {run_id!r} exposed malformed outputs")
    external_flags = [
        value.get("executed_externally")
        for value in outputs.values()
        if "executed_externally" in value
    ]
    if external_flags != [False]:
        raise LiveLoadInvariantError(f"run {run_id!r} output did not prove proposal-only effect")

    final_revision = replay_state.get("revision")
    final_time = replay_state.get("now_ms")
    if type(final_revision) is not int or type(final_time) is not int:
        raise LiveLoadInvariantError("final replay returned invalid revision or logical time")
    limited = _control(
        port,
        token,
        run_id,
        timeout_seconds=timeout_seconds,
        kind="provider.capacity",
        revision=final_revision,
        occurred_at_ms=final_time,
        details={"provider": "simulated-watsonx", "capacity": 1},
    )
    _expect(limited, status=429, error_code="control_event_limit")
    return {
        "final_replay": replay.receipt(),
        "inspection": inspection.receipt(),
        "control_limit_rejection": limited.receipt(),
        "effect_intent_id": effect.get("intent_id"),
        "effect_idempotency_key": effect.get("idempotency_key"),
        "effect_digest": effect.get("effect_digest"),
        "effect_state": effect.get("state"),
        "external_effects_committed": 0,
        "replay_passed": True,
        "replay_worker_or_provider_calls": 0,
    }


def _percentile(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    if not ordered:
        raise LiveLoadInvariantError("latency statistics require observations")
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _latency_summary(values: Sequence[int]) -> dict[str, int]:
    if not values:
        raise LiveLoadInvariantError("latency statistics require observations")
    return {
        "count": len(values),
        "min_ns": min(values),
        "mean_ns": sum(values) // len(values),
        "p50_ns": _percentile(values, 0.50),
        "p95_ns": _percentile(values, 0.95),
        "max_ns": max(values),
    }


def _wait_round_inactive(
    app: ControlPlane,
    run_ids: Sequence[str],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not any(run_id in app._active for run_id in run_ids):  # noqa: SLF001
            return
        time.sleep(0.01)
    raise LiveLoadInvariantError("terminal runs did not leave the bounded active registry")


def _run_live_load(
    contract: Mapping[str, Any],
    environment: Mapping[str, Any],
) -> dict[str, object]:
    concurrency = cast(int, contract["concurrency"])
    rounds = cast(int, contract["rounds"])
    timeout_seconds = cast(float, contract["request_timeout_seconds"])
    token = secrets.token_urlsafe(48)
    records: list[dict[str, object]] = []
    active_limit_receipts: list[dict[str, object]] = []
    benchmark_started = time.perf_counter_ns()

    with tempfile.TemporaryDirectory(prefix="finite-live-load-") as state_directory:
        app = build_control_service(
            state_directory,
            bearer_token=token,
            max_active_runs=concurrency,
            max_control_events_per_run=CONTROL_EVENT_LIMIT,
        )
        with _serve(app, startup_timeout_seconds=timeout_seconds) as port:
            reference = _request(
                port,
                token,
                "reference-workflow",
                "GET",
                "/v1/reference-workflows/stormshift",
                timeout_seconds=timeout_seconds,
            )
            _expect(reference, status=200)
            workflow = reference.body.get("workflow")
            if not isinstance(workflow, dict) or content_digest(workflow) != contract["workflow_digest"]:
                raise LiveLoadInvariantError("served workflow differs from the frozen contract")

            for round_index in range(rounds):
                run_ids = [
                    f"load-r{round_index + 1:03d}-{index + 1:03d}"
                    for index in range(concurrency)
                ]
                barrier = threading.Barrier(concurrency)

                def submit(run_id: str) -> tuple[str, int, _HTTPResult]:
                    barrier.wait(timeout=timeout_seconds)
                    started = time.perf_counter_ns()
                    result = _request(
                        port,
                        token,
                        "submit-paused",
                        "POST",
                        "/v1/runs",
                        timeout_seconds=timeout_seconds,
                        body={"run_id": run_id, "workflow": workflow, "start_paused": True},
                    )
                    return run_id, started, result

                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    submissions = list(pool.map(submit, run_ids))
                started_by_run: dict[str, int] = {}
                submission_by_run: dict[str, dict[str, object]] = {}
                for run_id, started, result in submissions:
                    _expect(result, status=202)
                    started_by_run[run_id] = started
                    submission_by_run[run_id] = result.receipt()

                overflow = _request(
                    port,
                    token,
                    "active-limit-overflow",
                    "POST",
                    "/v1/runs",
                    timeout_seconds=timeout_seconds,
                    body={
                        "run_id": f"load-r{round_index + 1:03d}-overflow",
                        "workflow": workflow,
                        "start_paused": True,
                    },
                )
                _expect(overflow, status=429, error_code="active_run_limit")
                active_limit_receipts.append(overflow.receipt())

                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    driven = list(
                        pool.map(
                            lambda run_id: _drive_paused_run(
                                port,
                                token,
                                run_id,
                                timeout_seconds=timeout_seconds,
                            ),
                            run_ids,
                        )
                    )
                _wait_round_inactive(app, run_ids, timeout_seconds=timeout_seconds)

                before_replay = _fixture_call_counts(app)
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    inspected = list(
                        pool.map(
                            lambda run_id: _inspect_and_limit(
                                port,
                                token,
                                run_id,
                                timeout_seconds=timeout_seconds,
                            ),
                            run_ids,
                        )
                    )
                after_replay = _fixture_call_counts(app)
                if before_replay != after_replay:
                    raise LiveLoadInvariantError(
                        "inspection or adaptive replay caused an additional worker/provider call"
                    )

                for run_id, driven_result, inspected_result in zip(
                    run_ids, driven, inspected, strict=True
                ):
                    fields: dict[str, object] = {
                        "schema_version": RECORD_SCHEMA_VERSION,
                        "contract_digest": contract["contract_digest"],
                        "environment_digest": environment["environment_digest"],
                        "run_id": run_id,
                        "round": round_index + 1,
                        "submission": submission_by_run[run_id],
                        **driven_result,
                        **inspected_result,
                        "end_to_end_duration_ns": time.perf_counter_ns()
                        - started_by_run[run_id],
                    }
                    fields["record_digest"] = content_digest(fields)
                    records.append(fields)

        fixture_calls = _fixture_call_counts(app)

    wall_duration_ns = time.perf_counter_ns() - benchmark_started
    intent_ids = [cast(str, record["effect_intent_id"]) for record in records]
    idempotency_keys = [cast(str, record["effect_idempotency_key"]) for record in records]
    submit_latencies = [
        cast(dict[str, Any], record["submission"])["duration_ns"] for record in records
    ]
    control_latencies = [
        cast(dict[str, Any], receipt)["duration_ns"]
        for record in records
        for receipt in cast(list[dict[str, object]], record["controls"])
    ]
    replay_latencies = [
        cast(dict[str, Any], record["final_replay"])["duration_ns"] for record in records
    ]
    end_to_end_latencies = [cast(int, record["end_to_end_duration_ns"]) for record in records]
    request_statuses = Counter(
        cast(dict[str, Any], receipt)["status"]
        for record in records
        for receipt in (
            [record["submission"], record["initial_replay"]]
            + cast(list[dict[str, object]], record["controls"])
            + [
                record["final_replay"],
                record["inspection"],
                record["control_limit_rejection"],
            ]
        )
    )
    request_statuses.update(
        cast(dict[str, Any], receipt)["status"] for receipt in active_limit_receipts
    )
    expected_runs = concurrency * rounds
    violations: list[str] = []
    if len(records) != expected_runs:
        violations.append("accepted run receipt count mismatch")
    if len(set(intent_ids)) != expected_runs:
        violations.append("effect intent IDs are not unique and run-scoped")
    if len(set(idempotency_keys)) != expected_runs:
        violations.append("effect idempotency keys are not unique and run-scoped")
    if any(record["effect_state"] != "proposed" for record in records):
        violations.append("an effect crossed the durable proposal boundary")
    if any(record["replay_passed"] is not True for record in records):
        violations.append("an adaptive replay failed")
    if any(record["replay_worker_or_provider_calls"] != 0 for record in records):
        violations.append("an adaptive replay reported a worker/provider call")

    report_fields: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "contract_digest": contract["contract_digest"],
        "environment_digest": environment["environment_digest"],
        "record_count": len(records),
        "record_set_digest": content_digest(records),
        "wall_duration_ns": wall_duration_ns,
        "completed_run_throughput_per_second": round(
            len(records) * 1_000_000_000 / wall_duration_ns,
            6,
        ),
        "latency": {
            "submission": _latency_summary(submit_latencies),
            "accepted_control": _latency_summary(control_latencies),
            "final_call_free_replay": _latency_summary(replay_latencies),
            "end_to_end": _latency_summary(end_to_end_latencies),
        },
        "http_status_counts": {
            str(status): count for status, count in sorted(request_statuses.items())
        },
        "accepted_submission_count": len(records),
        "terminal_awaiting_effects_count": sum(
            record["terminal_state"] == "awaiting_effects" for record in records
        ),
        "active_run_limit": {
            "configured": concurrency,
            "expected_rejections": rounds,
            "observed_rejections": len(active_limit_receipts),
            "enforced": len(active_limit_receipts) == rounds,
        },
        "control_event_limit": {
            "configured_per_run": CONTROL_EVENT_LIMIT,
            "expected_rejections": expected_runs,
            "observed_rejections": sum(
                cast(dict[str, Any], record["control_limit_rejection"])["status"] == 429
                for record in records
            ),
            "enforced": all(
                cast(dict[str, Any], record["control_limit_rejection"])["error_code"]
                == "control_event_limit"
                for record in records
            ),
        },
        "effects": {
            "intent_count": len(intent_ids),
            "unique_intent_count": len(set(intent_ids)),
            "unique_idempotency_key_count": len(set(idempotency_keys)),
            "proposed_count": sum(record["effect_state"] == "proposed" for record in records),
            "externally_committed_count": 0,
        },
        "adaptive_replay": {
            "passed_count": sum(record["replay_passed"] is True for record in records),
            "zero_call_count": sum(
                record["replay_worker_or_provider_calls"] == 0 for record in records
            ),
            "fixture_call_counts_after_workload": fixture_calls,
            "post_terminal_replay_call_delta": 0,
        },
        "expected_rejection_count": rounds + expected_runs,
        "unexpected_error_count": len(violations),
        "violations": violations,
        "passed": not violations,
        "claim_status": "local-deterministic-load-proof-only",
    }
    report_fields["report_digest"] = content_digest(report_fields)
    evidence_fields: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "contract": dict(contract),
        "environment": dict(environment),
        "records": records,
        "active_limit_receipts": active_limit_receipts,
        "report": report_fields,
    }
    evidence_fields["evidence_digest"] = content_digest(evidence_fields)
    return evidence_fields


def _write_canonical(path: Path, value: object) -> None:
    if path.is_symlink():
        raise LiveLoadInvariantError(f"refusing to overwrite symlink {path.name!r}")
    path.write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def write_live_load_evidence(evidence: Mapping[str, Any], output: str | Path) -> dict[str, object]:
    """Write canonical evidence files and a byte-level SHA-256 manifest."""

    destination = Path(output)
    if destination.exists() and not destination.is_dir():
        raise LiveLoadInvariantError("the evidence destination must be a directory")
    destination.mkdir(parents=True, exist_ok=True)
    _write_canonical(destination / "contract.json", evidence["contract"])
    _write_canonical(destination / "environment.json", evidence["environment"])
    records = cast(list[dict[str, object]], evidence["records"])
    raw_path = destination / "raw-records.jsonl"
    if raw_path.is_symlink():
        raise LiveLoadInvariantError("refusing to overwrite symlink 'raw-records.jsonl'")
    raw_path.write_text(
        "".join(canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    _write_canonical(destination / "report.json", evidence["report"])
    _write_canonical(destination / "evidence.json", evidence)
    files = [
        {
            "name": name,
            "bytes": (destination / name).stat().st_size,
            "sha256": _sha256_bytes((destination / name).read_bytes()),
        }
        for name in _EVIDENCE_FILES
    ]
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "files": files,
    }
    manifest["manifest_digest"] = content_digest(manifest)
    _write_canonical(destination / "manifest.json", manifest)
    return manifest


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise LiveLoadInvariantError(f"{path.name} contains duplicate field {key!r}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise LiveLoadInvariantError(f"{path.name} contains non-finite value {value!r}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LiveLoadInvariantError(f"could not parse {path.name}") from exc
    if not isinstance(value, dict):
        raise LiveLoadInvariantError(f"{path.name} must contain one JSON object")
    return value


def verify_live_load_evidence(output: str | Path) -> dict[str, Any]:
    """Reject non-canonical, tampered, incomplete, or failed load evidence."""

    source = Path(output)
    manifest = _load_json(source / "manifest.json")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise LiveLoadInvariantError("unsupported live-load manifest schema")
    if manifest.get("manifest_digest") != content_digest(
        _unsigned(manifest, "manifest_digest")
    ):
        raise LiveLoadInvariantError("live-load manifest digest mismatch")
    listed = manifest.get("files")
    if (
        not isinstance(listed, list)
        or not all(isinstance(item, dict) for item in listed)
        or [item.get("name") for item in listed] != list(_EVIDENCE_FILES)
    ):
        raise LiveLoadInvariantError("live-load manifest file set or order changed")
    for item in listed:
        if not isinstance(item, dict):
            raise LiveLoadInvariantError("live-load manifest contains a malformed file entry")
        path = source / cast(str, item["name"])
        if path.is_symlink() or not path.is_file():
            raise LiveLoadInvariantError(f"evidence file {path.name!r} is missing or unsafe")
        raw = path.read_bytes()
        if item.get("bytes") != len(raw) or item.get("sha256") != _sha256_bytes(raw):
            raise LiveLoadInvariantError(f"evidence file {path.name!r} failed its byte digest")

    contract = _load_json(source / "contract.json")
    environment = _load_json(source / "environment.json")
    report = _load_json(source / "report.json")
    evidence = _load_json(source / "evidence.json")
    for name, value in (
        ("contract.json", contract),
        ("environment.json", environment),
        ("report.json", report),
        ("evidence.json", evidence),
    ):
        expected = canonical_json(value) + "\n"
        if (source / name).read_text(encoding="utf-8") != expected:
            raise LiveLoadInvariantError(f"{name} is not canonical JSON")

    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION or contract.get(
        "contract_digest"
    ) != content_digest(_unsigned(contract, "contract_digest")):
        raise LiveLoadInvariantError("live-load contract is unsupported or tampered")
    concurrency = _require_integer(
        contract.get("concurrency"),
        name="contract concurrency",
        minimum=MIN_CONCURRENCY,
        maximum=MAX_CONCURRENCY,
    )
    rounds = _require_integer(
        contract.get("rounds"), name="contract rounds", minimum=1, maximum=128
    )
    if contract.get("total_accepted_runs") != concurrency * rounds:
        raise LiveLoadInvariantError("live-load contract total is inconsistent")
    if contract.get("max_active_runs") != concurrency:
        raise LiveLoadInvariantError("live-load contract does not test its exact active-run cap")
    if contract.get("max_control_events_per_run") != CONTROL_EVENT_LIMIT:
        raise LiveLoadInvariantError("live-load control-event cap changed")
    expected_contract = build_live_load_contract(
        concurrency=concurrency,
        rounds=rounds,
        request_timeout_seconds=contract.get("request_timeout_seconds"),
    )
    if contract != expected_contract:
        raise LiveLoadInvariantError("live-load contract differs from the current frozen design")
    if environment.get("schema_version") != ENVIRONMENT_SCHEMA_VERSION or environment.get(
        "environment_digest"
    ) != content_digest(_unsigned(environment, "environment_digest")):
        raise LiveLoadInvariantError("live-load environment is unsupported or tampered")
    if environment.get("contract_digest") != contract["contract_digest"]:
        raise LiveLoadInvariantError("live-load environment is bound to another contract")

    raw_lines = (source / "raw-records.jsonl").read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LiveLoadInvariantError("raw-records.jsonl contains invalid JSON") from exc
        if not isinstance(record, dict) or canonical_json(record) != line:
            raise LiveLoadInvariantError("raw-records.jsonl contains a non-canonical record")
        if record.get("schema_version") != RECORD_SCHEMA_VERSION or record.get(
            "record_digest"
        ) != content_digest(_unsigned(record, "record_digest")):
            raise LiveLoadInvariantError("raw-records.jsonl contains a tampered record")
        records.append(record)
    if len(records) != concurrency * rounds or len({item.get("run_id") for item in records}) != len(
        records
    ):
        raise LiveLoadInvariantError("raw load receipts are incomplete or duplicated")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION or report.get(
        "report_digest"
    ) != content_digest(_unsigned(report, "report_digest")):
        raise LiveLoadInvariantError("live-load report is unsupported or tampered")
    if (
        report.get("contract_digest") != contract["contract_digest"]
        or report.get("environment_digest") != environment["environment_digest"]
        or any(
            record.get("contract_digest") != contract["contract_digest"]
            or record.get("environment_digest") != environment["environment_digest"]
            for record in records
        )
    ):
        raise LiveLoadInvariantError("live-load receipts are cross-bound to different inputs")
    if report.get("record_set_digest") != content_digest(records):
        raise LiveLoadInvariantError("live-load record-set digest mismatch")
    if report.get("passed") is not True or report.get("unexpected_error_count") != 0:
        raise LiveLoadInvariantError("live-load report did not pass all registered gates")
    if report.get("accepted_submission_count") != concurrency * rounds:
        raise LiveLoadInvariantError("not every registered concurrent submission was accepted")
    active_limit = report.get("active_run_limit")
    control_limit = report.get("control_event_limit")
    effects = report.get("effects")
    replay = report.get("adaptive_replay")
    if not isinstance(active_limit, dict) or active_limit.get("enforced") is not True:
        raise LiveLoadInvariantError("active-run admission cap was not enforced")
    if not isinstance(control_limit, dict) or control_limit.get("enforced") is not True:
        raise LiveLoadInvariantError("per-run control-event cap was not enforced")
    if not isinstance(effects, dict) or (
        effects.get("intent_count") != concurrency * rounds
        or effects.get("unique_intent_count") != concurrency * rounds
        or effects.get("unique_idempotency_key_count") != concurrency * rounds
        or effects.get("proposed_count") != concurrency * rounds
        or effects.get("externally_committed_count") != 0
    ):
        raise LiveLoadInvariantError("effect isolation or proposal-only proof failed")
    if not isinstance(replay, dict) or (
        replay.get("passed_count") != concurrency * rounds
        or replay.get("zero_call_count") != concurrency * rounds
        or replay.get("post_terminal_replay_call_delta") != 0
    ):
        raise LiveLoadInvariantError("call-free adaptive replay proof failed")
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION or evidence.get(
        "evidence_digest"
    ) != content_digest(_unsigned(evidence, "evidence_digest")):
        raise LiveLoadInvariantError("live-load evidence object is unsupported or tampered")
    if (
        evidence.get("contract") != contract
        or evidence.get("environment") != environment
        or evidence.get("records") != records
        or evidence.get("report") != report
    ):
        raise LiveLoadInvariantError("live-load evidence files disagree")
    return report


def run_live_load(
    output: str | Path,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    rounds: int = DEFAULT_ROUNDS,
    request_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Execute, write, then independently re-read and verify the live load proof."""

    contract = build_live_load_contract(
        concurrency=concurrency,
        rounds=rounds,
        request_timeout_seconds=request_timeout_seconds,
    )
    environment = capture_live_load_environment(contract)
    evidence = _run_live_load(contract, environment)
    write_live_load_evidence(evidence, output)
    return verify_live_load_evidence(output)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-load"))
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--verify-only", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.verify_only is not None:
            report = verify_live_load_evidence(arguments.verify_only)
        else:
            report = run_live_load(
                arguments.output,
                concurrency=arguments.concurrency,
                rounds=arguments.rounds,
                request_timeout_seconds=arguments.request_timeout_seconds,
            )
    except (LiveLoadInvariantError, OSError, RuntimeError) as exc:
        print(f"live-load proof failed: {exc}", file=sys.stderr)
        return 1
    print(
        canonical_json(
            {
                "passed": report["passed"],
                "record_count": report["record_count"],
                "throughput_per_second": report["completed_run_throughput_per_second"],
                "latency": report["latency"],
                "unexpected_error_count": report["unexpected_error_count"],
                "report_digest": report["report_digest"],
            }
        )
    )
    return 0


__all__ = [
    "LiveLoadInvariantError",
    "build_live_load_contract",
    "capture_live_load_environment",
    "main",
    "run_live_load",
    "verify_live_load_evidence",
    "write_live_load_evidence",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
