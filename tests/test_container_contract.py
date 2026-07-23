from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def test_production_image_is_digest_pinned_non_root_and_fail_closed() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    first_line = dockerfile.splitlines()[0]
    assert first_line.startswith("ARG PYTHON_IMAGE=python:3.12-alpine3.24@sha256:")
    assert SHA256.search(first_line)
    assert "USER 10001:10001" in dockerfile
    assert 'CMD ["finite-api", "--host", "0.0.0.0", "--port", "8080"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/readyz" in dockerfile
    assert "pip uninstall -y" in dockerfile
    assert "pip setuptools wheel jaraco.context" in dockerfile
    assert "FINITE_CONTROL_BEARER_TOKEN=" not in dockerfile
    assert "COPY ." not in dockerfile


def test_build_context_excludes_everything_except_declared_inputs() -> None:
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert patterns[0] == "*"
    assert set(patterns[1:]) == {
        "!LICENSE",
        "!README.md",
        "!pyproject.toml",
        "!requirements/",
        "!requirements/container.txt",
        "!src/",
        "!src/**",
        "**/__pycache__/",
        "**/*.egg-info/",
        "**/*.py[cod]",
    }


def test_container_constraints_are_exact_and_cover_runtime_dependencies() -> None:
    lines = [
        line
        for line in (ROOT / "requirements" / "container.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    ]

    assert lines == sorted(lines, key=str.lower)
    assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[^=\s]+", line) for line in lines)
    assert {line.split("==", 1)[0].lower() for line in lines} == {
        "click",
        "defusedxml",
        "h11",
        "pip",
        "pyyaml",
        "uvicorn",
    }


def test_compose_applies_runtime_hardening_and_requires_operator_secret() -> None:
    compose_text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    document = yaml.safe_load(compose_text)
    service = document["services"]["finite-api"]

    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cpus"] == "2.0"
    assert service["mem_limit"] == "1g"
    assert service["mem_reservation"] == "256m"
    assert service["memswap_limit"] == "1g"
    assert service["pids_limit"] == 256
    assert service["ports"] == ["127.0.0.1:${FINITE_CONTROL_PORT:-8080}:8080"]
    assert service["environment"]["FINITE_CONTROL_BEARER_TOKEN"] == (
        "${FINITE_CONTROL_BEARER_TOKEN:?set FINITE_CONTROL_BEARER_TOKEN}"
    )
    assert service["volumes"] == [
        {
            "type": "volume",
            "source": "finite-state",
            "target": "/var/lib/finite",
            "read_only": False,
        }
    ]
    assert "FINITE_CONTROL_BEARER_TOKEN=" not in compose_text


def test_source_distribution_manifest_contains_the_deployment_contract() -> None:
    lines = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()

    assert lines == [
        "include .dockerignore",
        "include Dockerfile",
        "include compose.yaml",
        "include requirements/container.txt",
    ]
