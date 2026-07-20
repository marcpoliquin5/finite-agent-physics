"""Reproducible demonstration graphs."""

from __future__ import annotations

from .contracts import BackendProfile, Effect, EffectClass, RunEnvelope, TaskContract
from .graph import ExecutionGraph


def _profiles(task_scale: int = 1) -> tuple[BackendProfile, ...]:
    return (
        BackendProfile(
            name="simulated-granite-accurate",
            provider="simulated-watsonx",
            duration_ms_p50=700 * task_scale,
            duration_ms_p95=1_200 * task_scale,
            input_tokens=700 * task_scale,
            output_tokens=220 * task_scale,
            cost_microusd=900 * task_scale,
            context_bytes=3_500 * task_scale,
            quality=0.95,
            failure_probability=0.01,
        ),
        BackendProfile(
            name="simulated-granite-fast",
            provider="simulated-watsonx",
            duration_ms_p50=350 * task_scale,
            duration_ms_p95=600 * task_scale,
            input_tokens=420 * task_scale,
            output_tokens=140 * task_scale,
            cost_microusd=430 * task_scale,
            context_bytes=2_200 * task_scale,
            quality=0.87,
            failure_probability=0.02,
        ),
        BackendProfile(
            name="fixture-rule-engine",
            provider="local-fixture",
            duration_ms_p50=120 * task_scale,
            duration_ms_p95=180 * task_scale,
            input_tokens=0,
            output_tokens=0,
            cost_microusd=0,
            context_bytes=900 * task_scale,
            quality=0.82,
            failure_probability=0.0,
        ),
    )


def miami_eoc_graph() -> ExecutionGraph:
    tasks = [
        TaskContract(
            "incident_intake",
            _profiles(),
            min_quality=0.82,
            description="Normalize the simulated incident and operating envelope.",
        ),
        TaskContract("shelter_status", _profiles(2), ("incident_intake",), min_quality=0.82),
        TaskContract("transit_status", _profiles(), ("incident_intake",), min_quality=0.82),
        TaskContract("flood_zones", _profiles(2), ("incident_intake",), min_quality=0.87),
        TaskContract("hospital_capacity", _profiles(2), ("incident_intake",), min_quality=0.87),
        TaskContract("utility_outages", _profiles(), ("incident_intake",), min_quality=0.82),
        TaskContract(
            "social_signal_scan",
            _profiles(),
            ("incident_intake",),
            optional=True,
            value=0.35,
            min_quality=0.82,
        ),
        TaskContract(
            "response_plan",
            _profiles(2),
            (
                "shelter_status",
                "transit_status",
                "flood_zones",
                "hospital_capacity",
                "utility_outages",
            ),
            min_quality=0.95,
        ),
        TaskContract(
            "safety_review",
            _profiles(),
            ("response_plan",),
            min_quality=0.95,
        ),
        TaskContract(
            "multilingual_alert",
            _profiles(),
            ("response_plan",),
            min_quality=0.87,
        ),
        TaskContract(
            "publish_simulated_alert",
            _profiles(),
            ("safety_review", "multilingual_alert"),
            effect=Effect(
                kind=EffectClass.IRREVERSIBLE_WRITE,
                resource="public-alert-channel",
                requires_approval=True,
                idempotency_key="demo-incident-001-alert-v1",
            ),
            min_quality=0.95,
        ),
    ]
    return ExecutionGraph.from_tasks(tasks)


def miami_eoc_envelope() -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=12_000,
        max_tokens=16_000,
        max_cost_microusd=16_000,
        max_context_bytes=70_000,
        max_parallelism=4,
        min_modeled_success_probability=0.90,
        provider_limits=(("simulated-watsonx", 2), ("local-fixture", 4)),
    )
