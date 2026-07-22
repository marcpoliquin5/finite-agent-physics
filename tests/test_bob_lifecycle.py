import asyncio
from pathlib import Path

import pytest

from agent_physics.bob_lifecycle import BobRunService
from agent_physics.run_store import RunNotFound
from agent_physics.watsonx import WatsonxConfigurationError


class FakeInference:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return {
            "results": [
                {
                    "generated_text": "Granite receipt bound to the run.",
                    "input_token_count": 20,
                    "generated_token_count": 8,
                    "stop_reason": "eos_token",
                }
            ]
        }


def live_environment() -> dict[str, str]:
    return {
        "WATSONX_URL": "https://example.ml.cloud.ibm.com",
        "WATSONX_API_KEY": "secret-value",
        "WATSONX_PROJECT_ID": "project",
        "WATSONX_MODEL_ID": "ibm/granite-test",
    }


def test_fixture_lifecycle_is_durable_explainable_and_control_verified(
    tmp_path: Path,
) -> None:
    service = BobRunService(tmp_path / "state")
    first = asyncio.run(
        service.run_fixture(run_id="bob-fixture-1", bob_session_ref="screenshot://bob-1")
    )
    resumed = asyncio.run(service.run_fixture(run_id="bob-fixture-1"))
    summary = service.summary("bob-fixture-1", mode="fixture").as_dict()
    explanation = service.explain("bob-fixture-1")
    verification = service.verify("bob-fixture-1")

    assert first["state"] == "awaiting_effects"
    assert first["effect_state"] == "proposed"
    assert first["external_effects_possible"] is False
    assert first["bob_session_ref_status"] == "caller-supplied-unverified"
    assert resumed["completed_task_ids"] == first["completed_task_ids"]
    assert len(resumed["completed_task_ids"]) == 11
    assert summary["event_count"] == len(explanation["events"])
    assert explanation["event_type_counts"]["run.started"] == 1
    assert explanation["event_type_counts"]["provenance.bob_caller_assertion"] == 1
    assert explanation["reasoning_access"] is False
    assert all("payload_digest" in event for event in explanation["events"])
    assert verification["passed"] is True
    assert verification["measurement_scope"].startswith("control-ledger-only")


def test_granite_preflight_never_calls_provider_and_missing_config_fails_closed(
    tmp_path: Path,
) -> None:
    fake = FakeInference()
    configured = BobRunService(
        tmp_path / "configured",
        environment=live_environment(),
        inference_factory=lambda **_: fake,
    )
    preflight = configured.granite_preflight(max_new_tokens=64)
    assert preflight["status"] == "feasible"
    assert preflight["live_provider_calls"] is False
    assert preflight["model_id"] == "ibm/granite-test"
    assert fake.calls == []

    missing = BobRunService(tmp_path / "missing", environment={})
    with pytest.raises(WatsonxConfigurationError):
        missing.granite_preflight()


def test_granite_probe_runs_once_resumes_and_preserves_redacted_receipt(
    tmp_path: Path,
) -> None:
    fake = FakeInference()
    service = BobRunService(
        tmp_path / "state",
        environment=live_environment(),
        inference_factory=lambda **_: fake,
    )
    first = asyncio.run(
        service.run_granite_probe(
            run_id="bob-granite-1",
            instruction="Return one bounded readiness sentence.",
            max_new_tokens=64,
            bob_session_ref="bob://session/real-evidence-added-later",
        )
    )
    resumed = asyncio.run(
        service.run_granite_probe(
            run_id="bob-granite-1",
            instruction="Return one bounded readiness sentence.",
            max_new_tokens=64,
        )
    )

    assert len(fake.calls) == 1
    assert first["state"] == "completed"
    assert first["measurement_kind"] == "injected-test-double"
    assert first["live_provider_calls"] is False
    assert first["receipt"]["measurement_kind"] == "injected-test-double"
    assert first["receipt"]["usage_complete"] is True
    assert "secret-value" not in str(first)
    assert resumed["resumed_task_ids"] == ("granite_probe",)
    assert service.verify("bob-granite-1")["passed"] is True


def test_unknown_run_and_malformed_identifiers_fail_closed(tmp_path: Path) -> None:
    service = BobRunService(tmp_path / "state")
    with pytest.raises(RunNotFound):
        service.summary("not-created")
    with pytest.raises(ValueError, match="run_id"):
        service.summary("../escape")
    with pytest.raises(ValueError, match="instruction"):
        asyncio.run(
            service.run_granite_probe(
                run_id="valid",
                instruction=" ",
            )
        )


def test_invalid_bob_reference_is_rejected_before_any_provider_call(tmp_path: Path) -> None:
    fake = FakeInference()
    service = BobRunService(
        tmp_path / "state",
        environment=live_environment(),
        inference_factory=lambda **_: fake,
    )

    with pytest.raises(ValueError, match="bob_session_ref"):
        asyncio.run(
            service.run_granite_probe(
                run_id="bob-invalid-provenance",
                instruction="This must never reach the provider.",
                bob_session_ref=" ",
            )
        )

    assert fake.calls == []
    with pytest.raises(RunNotFound):
        service.summary("bob-invalid-provenance")
