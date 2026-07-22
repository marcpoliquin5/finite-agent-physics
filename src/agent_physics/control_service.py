"""Configured ASGI service factory for the bounded StormShift runtime."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path

from .bob_lifecycle import default_state_directory
from .control_api import ControlPlane
from .effects import SQLiteEffectBroker
from .examples import miami_eoc_graph
from .run_store import SQLiteRunStore
from .stormshift_runtime import StormShiftRuntime, stormshift_envelope
from .workflow_ir import compile_contracts


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


def build_control_service(
    state_directory: str | Path,
    *,
    bearer_token: str | None,
    allow_anonymous_status_stream: bool = False,
    allowed_origins: tuple[str, ...] = (),
) -> ControlPlane:
    """Build one process-local control service over durable shared SQLite state."""

    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    store = SQLiteRunStore(state / "runs.sqlite3")
    broker = SQLiteEffectBroker(
        state / "effects.sqlite3",
        broker_id="finite-control-service",
    )
    runtime = StormShiftRuntime(store, broker)
    reference = compile_contracts(miami_eoc_graph(), stormshift_envelope())
    return ControlPlane(
        runtime.executor,
        effect_broker=broker,
        bearer_token=bearer_token,
        allow_anonymous_status_stream=allow_anonymous_status_stream,
        allowed_origins=allowed_origins,
        reference_workflows={"stormshift": reference.to_python()},
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
    return build_control_service(
        default_state_directory(values),
        bearer_token=token,
        allow_anonymous_status_stream=anonymous,
        allowed_origins=origins,
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


__all__ = ["build_control_service", "build_control_service_from_environment", "main"]


if __name__ == "__main__":  # pragma: no cover
    main()
