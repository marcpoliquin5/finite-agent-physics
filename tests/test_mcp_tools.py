import asyncio

import pytest

from agent_physics.mcp_server import (
    finite_adaptive_recovery_drill,
    finite_artifact_integrity_drill,
    finite_capabilities,
    finite_context_drill,
    finite_decision_explanation_drill,
    finite_effect_drill,
    finite_executor_drill,
    finite_explain_run,
    finite_fault_experiment,
    finite_framework_conformance_drill,
    finite_physical_admission_drill,
    finite_preflight,
    finite_run,
    finite_status,
    finite_quota_corpus,
    finite_registered_faults,
    finite_replanning_drill,
    finite_simulate,
    finite_stormshift_validate,
    finite_verify,
    finite_verify_run,
)


def test_mcp_capability_statement_is_explicit_about_live_boundary() -> None:
    payload = finite_capabilities()
    assert payload["stage"] == "durable-local-and-live-ready"
    assert payload["tool_count"] == 22
    assert len(payload["tools"]) == payload["tool_count"]
    assert len(set(payload["tools"])) == payload["tool_count"]
    assert payload["boundaries"] == {
        "external_effects_possible": False,
        "default_live_provider_calls": False,
        "explicit_live_provider_mode_available": True,
        "live_provider_evidence_captured": False,
        "reasoning_access": False,
        "physical_resource_evidence": "declared-estimates-not-runtime-measurement",
        "langgraph_witness": "conditional-on-reviewed-pinned-dependencies",
        "alibaba_pageagent_integration": False,
        "beeai_support": False,
        "safety": (
            "Fixture backends and all effects are simulated. Granite mode is an explicit "
            "provider call and still cannot commit an external effect."
        ),
    }
    assert (
        "entrant-owned genuine Bob and live-watsonx evidence capture" in payload["not_implemented"]
    )
    assert "physical-runtime measurement" in payload["not_implemented"]
    assert "Alibaba PageAgent integration or BeeAI adapter support" in payload["not_implemented"]


def test_mcp_durable_fixture_lifecycle_uses_one_persistent_run(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINITE_STATE_DIR", str(tmp_path / "finite-state"))

    started = asyncio.run(finite_run("mcp-lifecycle-1"))
    status = finite_status("mcp-lifecycle-1")
    explanation = finite_explain_run("mcp-lifecycle-1")
    verification = finite_verify_run("mcp-lifecycle-1")

    assert started["state"] == "awaiting_effects"
    assert status["event_digest"] == explanation["event_digest"]
    assert status["mode"] == "fixture"
    assert verification["passed"] is True


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
    assert result["validator_kind"] == "deterministic_structural_plus_bounded_semantic"
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
    assert first["aggregate_guard_scope"] == ("per_instance_only_not_process_global_or_distributed")
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


def test_physical_admission_drill_proves_exact_boundary_and_one_unit_refusal() -> None:
    result = finite_physical_admission_drill()
    admitted = result["exact_cap_witness"]
    refused = result["one_cpu_ms_tighter_witness"]

    assert result["measurement_kind"] == "declared-nonzero-integer-estimates"
    assert result["runtime_measurement_performed"] is False
    assert result["energy_measurement_supported"] is False
    assert result["live_provider_calls"] is False
    assert result["all_declared_estimates_nonzero"] is True
    assert result["boundary_proof_passed"] is True
    assert admitted["status"] == "admitted"
    assert refused["status"] == "refused"
    assert admitted["report_digest"]
    assert refused["report_digest"]
    assert all(value > 0 for value in admitted["totals"].values())
    violations = [check for check in refused["checks"] if not check["passed"]]
    assert [check["dimension"] for check in violations] == ["cpu_time"]
    assert violations[0]["observed"] == 41
    assert violations[0]["limit"] == 40


def test_adaptive_recovery_drill_executes_crash_restart_and_call_free_replay() -> None:
    result = finite_adaptive_recovery_drill()

    assert result["measurement_kind"] == "deterministic-local-durable-crash-recovery"
    assert result["proof_passed"] is True
    assert result["final_status"] == "completed"
    assert result["control_digest"] == result["replay_control_digest"]
    assert result["replay_passed"] is True
    assert result["first_process_worker_calls"] == ("intake", "assessment")
    assert result["restart_worker_calls"] == ("mandatory_alert",)
    assert result["unknown_task_ids"] == ("optional_enrichment",)
    assert result["shed_task_ids"] == ("optional_enrichment", "optional_social")
    assert result["provider_reset_honored"] is True
    assert result["controller_record_count"] == 14
    assert result["external_provider_calls"] == 0
    assert result["external_effects_possible"] is False


def test_framework_conformance_drill_is_actual_pinned_evidence_or_honest_unavailability() -> None:
    result = asyncio.run(finite_framework_conformance_drill())

    assert result["live_provider_calls"] is False
    assert result["external_calls_made"] is False
    assert result["external_effects_possible"] is False
    assert result["alibaba_pageagent_exercised"] is False
    assert result["beeai_exercised"] is False
    if result["status"] == "unavailable":
        assert result["verified"] is False
        assert result["actual_framework_execution"] is False
        assert "install" in result["reason"]
    else:
        assert result["status"] == "passed"
        assert result["verified"] is True
        assert result["actual_framework_execution"] is True
        witness = result["witness"]
        assert witness["pinned_versions_match"] is True
        assert witness["all_tasks_executed_once"] is True
        assert witness["dependencies_preserved"] is True
        assert witness["checkpoint_receipt_verified"] is True
        assert witness["effects_proposal_only"] is True
        assert witness["model_calls_made"] is False
        assert witness["external_calls_made"] is False
        assert witness["external_effects_executed"] == 0
        assert "loss:langgraph:run-budgets" in witness["semantic_loss_ids"]


def test_artifact_integrity_drill_restarts_deduplicates_and_detects_tampering() -> None:
    result = finite_artifact_integrity_drill()

    assert result["measurement_kind"] == "temporary-local-sqlite-restart-and-tamper-proof"
    assert result["external_storage_called"] is False
    assert result["live_provider_calls"] is False
    assert result["external_effects_possible"] is False
    assert result["proof_passed"] is True
    assert result["parent_inserted"] is True
    assert result["child_inserted"] is True
    assert result["restart_payload_verified"] is True
    assert result["restart_provenance_verified"] is True
    assert result["restart_duplicate_inserted"] is False
    assert result["pre_tamper"] == {
        **result["pre_tamper"],
        "passed": True,
        "artifact_count": 2,
        "provenance_count": 1,
        "digest_verified": True,
    }
    assert result["post_tamper"]["passed"] is False
    assert result["post_tamper"]["failure_count"] >= 1
    assert result["post_tamper"]["digest_verified"] is True
    assert result["post_tamper"]["direct_read_rejected"] is True
