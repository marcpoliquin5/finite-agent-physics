import asyncio

from agent_physics.mcp_server import (
    finite_capabilities,
    finite_context_drill,
    finite_decision_explanation_drill,
    finite_effect_drill,
    finite_executor_drill,
    finite_fault_experiment,
    finite_preflight,
    finite_quota_corpus,
    finite_registered_faults,
    finite_replanning_drill,
    finite_simulate,
    finite_stormshift_validate,
    finite_verify,
)


def test_mcp_capability_statement_is_explicitly_simulated() -> None:
    payload = finite_capabilities()
    assert payload["stage"] == "deterministic-simulation"
    assert payload["tool_count"] == 13
    assert len(payload["tools"]) == payload["tool_count"]
    assert len(set(payload["tools"])) == payload["tool_count"]
    assert payload["boundaries"] == {
        "external_effects_possible": False,
        "live_provider_calls": False,
        "reasoning_access": False,
        "safety": "All current scenario backends and effects are simulated.",
    }
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


def test_quota_corpus_replays_declared_limits_without_a_provider_call() -> None:
    first = finite_quota_corpus(seed=13, cycles=8)
    second = finite_quota_corpus(seed=13, cycles=8)
    assert first == second
    assert first["measurement_kind"] == "deterministic-local-quota-model"
    assert first["live_provider_calls"] is False
    assert first["provider_quota_measurement"] is False
    assert first["aggregate_guard_scope"] == (
        "per_instance_only_not_process_global_or_distributed"
    )
    assert first["replay_valid"] is True
    assert first["logical_calls"] == 200
    assert first["admission_requests"] > first["logical_calls"]
    assert first["admitted_calls"] == first["settled_calls"]
    assert first["refused_admissions"] > 0
    assert first["reset_suppressed_retries"] > 0
    assert first["maximum_provider_active"] <= 3
    assert first["event_digest"]


def test_quota_corpus_rejects_an_unbounded_mcp_workload() -> None:
    try:
        finite_quota_corpus(cycles=1_001)
    except ValueError as error:
        assert "at most 1000" in str(error)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("unbounded quota corpus was accepted")


def test_stormshift_replanning_drill_preserves_mandatory_work_and_sheds_optional() -> None:
    result = finite_replanning_drill()
    assert result["measurement_kind"] == "deterministic-local-replanning-model"
    assert result["live_provider_calls"] is False
    assert result["live_executor_mutated"] is False
    assert result["replay_verified"] is True
    assert result["revision"] == 1
    assert result["disposition"] == "scheduled"
    assert result["reason"]["code"] == "optional_work_shed"
    assert result["shed_task_ids"] == ("social_signal_scan",)
    assert result["mandatory_tasks_preserved"] is True
    assert "incident_intake" not in result["scheduled_task_ids"]
    assert "social_signal_scan" not in result["scheduled_task_ids"]
    assert result["remaining_envelope"]["deadline_ms"] == 10_000
    assert result["remaining_envelope"]["max_context_bytes"] == 28_600
    assert result["remaining_envelope"]["simulated_watsonx_capacity"] == 1


def test_decision_explanation_drill_covers_nominal_degraded_and_refused_modes() -> None:
    nominal = finite_decision_explanation_drill()
    degraded = finite_decision_explanation_drill("degraded")
    refused = finite_decision_explanation_drill("refused", include_records=True)

    for result in (nominal, degraded, refused):
        assert result["reasoning_access"] is False
        assert result["live_provider_calls"] is False
        assert result["bundle_verified"] is True
        assert result["source_replay_verified"] is True
        assert result["record_count"] == result["event_count"]
        assert len(result["record_ids"]) == result["record_count"]

    assert nominal["schedule_success"] is True
    assert nominal["terminal_action"] == "run_completion"
    assert nominal["records_included"] is False
    assert "records" not in nominal
    assert degraded["schedule_success"] is True
    assert degraded["skipped_task_ids"] == ("social_signal_scan",)
    assert degraded["action_counts"]["optional_shed"] == 1
    assert refused["schedule_success"] is False
    assert refused["terminal_action"] == "run_refusal"
    assert refused["records_included"] is True
    assert len(refused["records"]) == refused["record_count"]
    assert all(record["reasoning_access"] is False for record in refused["records"])
