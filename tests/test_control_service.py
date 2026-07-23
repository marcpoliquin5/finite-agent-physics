import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_physics.control_service as control_service
from agent_physics.control_service import (
    _environment_boolean,
    _environment_positive_integer,
    build_control_service,
    build_control_service_from_environment,
)


def test_service_factory_builds_durable_authenticated_runtime(tmp_path: Path) -> None:
    service = build_control_service(
        tmp_path / "state",
        bearer_token="bounded-control-token-material-1234",
        allow_anonymous_status_stream=True,
    )

    assert service.authentication_enabled is True
    assert service.allow_anonymous_status_stream is True
    assert Path(service.store.database_path) == tmp_path / "state" / "runs.sqlite3"
    assert (tmp_path / "state" / "effects.sqlite3").exists()


def test_environment_factory_defaults_closed_and_never_requires_token_serialization(
    tmp_path: Path,
) -> None:
    service = build_control_service_from_environment(
        {
            "FINITE_STATE_DIR": str(tmp_path / "state"),
            "FINITE_CONTROL_BEARER_TOKEN": "environment-control-token-material-123",
            "FINITE_CONTROL_ALLOWED_ORIGINS": (
                "https://judge.example,http://localhost:3001,https://judge.example"
            ),
        }
    )

    assert service.authentication_enabled is True
    assert service.allow_anonymous_status_stream is False
    assert service.allowed_origins == (
        "http://localhost:3001",
        "https://judge.example",
    )
    assert service.max_active_runs == 32
    assert service.max_control_events_per_run == 128
    assert "environment-control-token-material" not in repr(service.__dict__)


@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_environment_factory_rejects_ambiguous_boolean(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="FINITE_ALLOW_ANONYMOUS_STATUS_STREAM"):
        build_control_service_from_environment(
            {
                "FINITE_STATE_DIR": str(tmp_path / "state"),
                "FINITE_ALLOW_ANONYMOUS_STATUS_STREAM": value,
            }
        )


def test_service_rejects_non_origin_cors_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact HTTP"):
        build_control_service(
            tmp_path / "state",
            bearer_token=None,
            allowed_origins=("https://console.example/path",),
        )


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes "])
def test_environment_boolean_accepts_explicit_true(value: str) -> None:
    assert _environment_boolean({"FLAG": value}, "FLAG", default=False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no "])
def test_environment_boolean_accepts_explicit_false(value: str) -> None:
    assert _environment_boolean({"FLAG": value}, "FLAG", default=True) is False


def test_environment_boolean_uses_default_for_absent_or_blank() -> None:
    assert _environment_boolean({}, "FLAG", default=True) is True
    assert _environment_boolean({"FLAG": "  "}, "FLAG", default=False) is False


def test_environment_positive_integer_is_strict_and_bounded() -> None:
    assert _environment_positive_integer({}, "LIMIT", default=7) == 7
    assert _environment_positive_integer({"LIMIT": " 42 "}, "LIMIT", default=7) == 42
    for value in ("0", "-1", "+1", "1.0", "1000001", "１２"):
        with pytest.raises(ValueError, match="LIMIT"):
            _environment_positive_integer({"LIMIT": value}, "LIMIT", default=7)


def test_environment_factory_applies_process_and_durable_control_caps(tmp_path: Path) -> None:
    service = build_control_service_from_environment(
        {
            "FINITE_STATE_DIR": str(tmp_path / "state-caps"),
            "FINITE_MAX_ACTIVE_RUNS": "3",
            "FINITE_MAX_CONTROL_EVENTS_PER_RUN": "9",
        }
    )
    assert service.max_active_runs == 3
    assert service.max_control_events_per_run == 9


def test_main_rejects_invalid_port_before_building_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["finite-api", "--port", "0"])
    with pytest.raises(SystemExit) as error:
        control_service.main()
    assert error.value.code == 2


def test_main_requires_authentication_for_non_loopback_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["finite-api", "--host", "0.0.0.0"])
    monkeypatch.setattr(
        control_service,
        "build_control_service_from_environment",
        lambda: SimpleNamespace(authentication_enabled=False),
    )
    with pytest.raises(SystemExit) as error:
        control_service.main()
    assert error.value.code == 2


def test_main_runs_uvicorn_with_bounded_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SimpleNamespace(authentication_enabled=False)
    calls: list[tuple[object, str, int, str]] = []
    monkeypatch.setattr(sys, "argv", ["finite-api", "--host", "localhost", "--port", "9090"])
    monkeypatch.setattr(
        control_service,
        "build_control_service_from_environment",
        lambda: service,
    )
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(
            run=lambda app, *, host, port, log_level: calls.append(
                (app, host, port, log_level)
            )
        ),
    )
    control_service.main()
    assert calls == [(service, "localhost", 9090, "info")]
