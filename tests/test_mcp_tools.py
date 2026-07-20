import asyncio

from agent_physics.mcp_server import (
    finite_capabilities,
    finite_context_drill,
    finite_effect_drill,
    finite_executor_drill,
    finite_fault_experiment,
    finite_preflight,
    finite_registered_faults,
    finite_simulate,
    finite_stormshift_validate,
    finite_verify,
)


def test_mcp_capability_statement_is_explicitly_simulated() -> None:
    payload = finite_capabilities()
    assert payload["stage"] == "deterministic-simulation"
    assert "live IBM Granite or watsonx execution" in payload["not_implemented"]


def test_mcp_preflight_can_refuse_without_calling_external_systems() -> None:
    payload = finite_preflight(max_tokens=1)
    assert payload["status"] == "refused"
    assert payload["measurement_kind"] == "deterministic-simulation"


def test_mcp_simulation_and_verification_are_machine_readable() -> None:
    simulation = finite_simulate(include_events=False)
    verification = finite_verify()
    assert simulation["success"] is True
    assert "events" not in simulation
    assert verification["passed"] is True
    assert simulation["measurement_kind"] == "deterministic-simulation"


def test_fault_registry_does_not_claim_execution() -> None:
    faults = finite_registered_faults()["faults"]
    assert faults
    assert {fault["execution_status"] for fault in faults} == {"registered-not-executed"}


def test_context_drill_packs_or_refuses_without_exposing_raw_hostile_text() -> None:
    packed = finite_context_drill()
    refused = finite_context_drill(max_bytes=1, max_tokens=1)
    assert packed["status"] == "packed"
    assert packed["verified"] is True
    assert packed["raw_attack_visible_in_wire"] is False
    assert refused["status"] == "refused"
    assert refused["verified"] is True


def test_effect_drill_is_single_apply_after_hard_and_soft_faults() -> None:
    for crash_mode in ("none", "soft", "hard"):
        result = finite_effect_drill(crash_mode)
        assert result["external_effects_possible"] is False
        assert result["final_state"] == "committed"
        assert result["physical_apply_count"] == 1


def test_stormshift_validation_passes_nominal_and_fails_closed_under_faults() -> None:
    nominal = finite_stormshift_validate()
    stale = finite_stormshift_validate("stale-artifact")
    unsafe = finite_stormshift_validate("external-publication")
    assert nominal["passed"] is True
    assert nominal["digest_verified"] is True
    assert nominal["external_effects_possible"] is False
    assert stale["passed"] is False
    assert unsafe["passed"] is False


def test_fault_experiment_is_complete_paired_and_descriptive_only() -> None:
    summary = finite_fault_experiment("mcp-test-v1")
    assert summary["measurement_kind"] == "deterministic-simulation"
    assert summary["claim_status"] == "descriptive-only"
    assert summary["raw_record_count"] == 450
    assert summary["design"]["paired_seed_count"] == 30
    assert summary["external_systems_called"] is False


def test_executor_drill_resumes_all_completed_work_and_only_proposes_effect() -> None:
    result = asyncio.run(finite_executor_drill())
    assert result["external_effects_possible"] is False
    assert result["external_calls_made"] is False
    assert result["model_calls_made"] is False
    assert result["validator_kind"] == "deterministic_structural_only"
    assert result["task_count"] == 11
    assert result["resumed_task_count"] == 11
    assert result["first_run_state"] == "awaiting_effects"
    assert result["resumed_run_state"] == "awaiting_effects"
    assert result["first_worker_call_count"] == 10
    assert result["restart_worker_call_count"] == 0
    assert result["validation_digest_verified"] is True
    assert result["response_plan_digest"]
    assert result["validation_report_digest"]
    assert result["effect_output"]["effect_state"] == "proposed"
    assert result["effect_output"]["executed_externally"] is False
