from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_physics.mcp_server import finite_capabilities
from agent_physics.serialization import content_digest
from scripts.export_console_artifact import build_payload


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "apps" / "physics-console" / "app" / "demo-artifact.json"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _assert_summary_digest(summary: dict[str, object]) -> None:
    unsigned = dict(summary)
    declared = unsigned.pop("summary_digest")
    assert declared == content_digest(unsigned)


def test_console_artifact_is_current_digest_bound_kernel_output() -> None:
    envelope = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    canonical_payload = envelope["canonical_payload"]
    payload = json.loads(canonical_payload)

    assert envelope["schema_version"] == "finite-console-artifact/v1"
    assert envelope["digest_algorithm"] == "sha256"
    assert envelope["sha256"] == hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    assert canonical_payload == _canonical_json(build_payload())
    assert payload["schema_version"] == "finite-console-payload/v2"
    assert payload["release_generation"] == "v5"
    assert payload["bob_mcp_tool_count"] == finite_capabilities()["tool_count"]
    assert sum(len(states) for states in payload["decisions"].values()) == 1_080
    experiment = payload["registered_fault_experiment"]
    assert experiment["raw_record_count"] == 450
    assert experiment["paired_seed_count"] == 30
    assert experiment["condition_count"] == 5
    assert experiment["policy_count"] == 3
    assert experiment["revision_provenance"] == "caller-supplied-unverified"
    assert payload["resource_ledger_stress"]["transition_count"] == 10_000
    assert payload["resource_ledger_stress"]["independent_replay_passed"] is True
    assert payload["provider_quota_stress"]["logical_calls"] == 1_200
    assert payload["provider_quota_stress"]["settled_calls"] == 384
    assert payload["provider_quota_stress"]["aggregate_guard_scope"] == (
        "per_instance_only_not_process_global_or_distributed"
    )
    assert payload["replanning_witness"]["final_revision"] == 2
    assert payload["replanning_witness"]["shed_task_ids"] == ["social_signal_scan"]
    assert payload["replanning_witness"]["second_disposition"] == "refused"
    assert payload["decision_explanation_evidence"]["record_count"] == 79
    assert payload["decision_explanation_evidence"]["reasoning_access"] is False


def test_console_artifact_seals_v5_resource_recovery_semantic_and_store_proofs() -> None:
    payload = json.loads(json.loads(ARTIFACT.read_text(encoding="utf-8"))["canonical_payload"])

    physical = payload["physical_resource_admission"]
    _assert_summary_digest(physical)
    assert physical["report"]["status"] == "admitted"
    assert physical["report"]["report_digest"] == content_digest(
        {key: value for key, value in physical["report"].items() if key != "report_digest"}
    )
    assert physical["declared_physical_cap_count"] == 10
    assert physical["all_declared_physical_caps_nonzero"] is True
    assert physical["all_observed_physical_totals_nonzero"] is True
    assert set(physical["declared_physical_caps"]) == {
        "available_bandwidth_bps",
        "max_cpu_time_ms",
        "max_egress_cost_microusd",
        "max_network_egress_bytes",
        "max_network_ingress_bytes",
        "max_network_rtt_ms",
        "max_peak_memory_bytes",
        "max_peak_vram_bytes",
        "max_storage_read_bytes",
        "max_storage_write_bytes",
    }
    assert len(physical["report"]["checks"]) == 12
    assert all(check["passed"] for check in physical["report"]["checks"])
    assert physical["coverage_dimension_count"] == 12
    assert physical["energy_boundary"] == next(
        entry for entry in physical["report"]["coverage_matrix"] if entry["dimension"] == "energy"
    )
    assert physical["energy_boundary"]["status"] == "unsupported"
    assert "no energy claim" in physical["energy_boundary"]["limitation"]

    recovery = payload["adaptive_crash_restart_recovery"]
    _assert_summary_digest(recovery)
    assert recovery["final_status"] == "completed"
    assert recovery["replay_passed"] is True
    assert recovery["control_digest"] == recovery["replay_control_digest"]
    assert recovery["call_free_replay"] is True
    assert recovery["worker_calls_during_replay"] == 0
    assert recovery["external_provider_calls"] == 0
    assert recovery["first_process_worker_calls"] == ["intake", "assessment"]
    assert recovery["restart_worker_calls"] == ["mandatory_alert"]
    assert recovery["unknown_task_ids"] == ["optional_enrichment"]
    assert recovery["provider_reset_honored"] is True

    semantic = payload["bounded_semantic_safety"]
    _assert_summary_digest(semantic)
    assert semantic["baseline_passed"] is True
    assert semantic["baseline_report_digest_verified"] is True
    assert semantic["adversarial_mutation_count"] >= 15
    assert semantic["adversarial_refused_count"] == semantic["adversarial_mutation_count"]
    assert semantic["all_expected_failures_observed"] is True
    assert all(item["expected_failures_observed"] for item in semantic["mutation_results"])
    assert all(item["report_digest_verified"] for item in semantic["mutation_results"])
    assert any("no general natural-language" in item for item in semantic["limitations"])
    assert any("declarations only" in item for item in semantic["limitations"])

    store = payload["artifact_store_restart_integrity"]
    _assert_summary_digest(store)
    assert store["parent_inserted"] is True
    assert store["child_inserted"] is True
    assert store["duplicate_inserted"] is False
    assert store["deduplication_preserved"] is True
    assert store["parent_round_trip_verified"] is True
    assert store["child_round_trip_verified"] is True
    assert store["provenance_round_trip_verified"] is True
    assert store["artifact_count"] == 2
    assert store["provenance_count"] == 1
    assert store["verification_passed"] is True
    assert store["verification_digest"] == content_digest(
        {
            "artifact_count": store["artifact_count"],
            "provenance_count": store["provenance_count"],
            "passed": store["verification_passed"],
            "failures": store["verification_failures"],
        }
    )


def test_console_artifact_accounts_for_framework_loss_and_release_boundaries() -> None:
    payload = json.loads(json.loads(ARTIFACT.read_text(encoding="utf-8"))["canonical_payload"])

    conformance = payload["framework_conformance_loss_accounting"]
    _assert_summary_digest(conformance)
    neutral = conformance["neutral"]
    langgraph = conformance["langgraph"]
    assert neutral["manifest_digest_verified"] is True
    assert neutral["validation_problems"] == []
    assert neutral["round_trip_exact"] is True
    assert neutral["semantic_loss_count"] == 0
    assert langgraph["manifest_digest_verified"] is True
    assert langgraph["validation_problems"] == []
    assert langgraph["round_trip_exact"] is True
    assert langgraph["semantic_loss_count"] == len(langgraph["semantic_losses"])
    assert {
        "loss:langgraph:adapter-requirements",
        "loss:langgraph:approvals",
        "loss:langgraph:cache-policy",
        "loss:langgraph:checkpoint-resume",
        "loss:langgraph:effect-commit",
        "loss:langgraph:effect-declarations",
        "loss:langgraph:retries",
        "loss:langgraph:run-budgets",
        "loss:langgraph:typed-ports",
        "loss:langgraph:validators",
    } == {item["loss_id"] for item in langgraph["semantic_losses"]}
    assert langgraph["actual_framework_execution_witness_present"] is False
    assert conformance["optional_framework_execution"] == "not-executed"
    assert conformance["optional_framework_required_for_artifact_build"] is False
    assert conformance["model_calls_made"] == 0
    assert conformance["external_calls_made"] == 0

    verifier_boundaries = payload["release_and_whole_run_verifier_boundaries"]
    _assert_summary_digest(verifier_boundaries)
    release = verifier_boundaries["release_manifest"]
    whole_run = verifier_boundaries["whole_run_verifier"]
    assert release["capability_id_count"] == 62
    assert release["integrated_proof_id_count"] == 8
    assert release["release_gate_id_count"] == 9
    assert set(release["required_external_kinds"]) == {
        "bob",
        "deployment",
        "eligibility",
        "github",
        "skillsbuild",
        "submission",
        "video",
        "watsonx",
    }
    assert len(whole_run["capabilities"]) == 8
    assert whole_run["independent_of_scheduler_executor_provider_and_planner"] is True
    assert whole_run["executed_for_this_console_summary"] is False
    assert verifier_boundaries["release_ready_claim"] is False
    assert verifier_boundaries["external_attestations_present"] is False

    boundaries = payload["v5_evidence_boundaries"]
    _assert_summary_digest(boundaries)
    assert boundaries["simulation_only"] is True
    assert boundaries["live_bob_session_present"] is False
    assert boundaries["bob_capability_inventory_only"] is True
    assert boundaries["live_watsonx_or_granite_calls"] == 0
    assert boundaries["watsonx_profiles_are_simulated"] is True
    assert boundaries["live_alibaba_pageagent_execution_present"] is False
    assert boundaries["public_deployment_receipt_present"] is False
    assert boundaries["release_ready_claim"] is False


def test_console_artifact_keeps_simulation_and_refusal_boundaries_explicit() -> None:
    payload = json.loads(json.loads(ARTIFACT.read_text(encoding="utf-8"))["canonical_payload"])

    assert payload["measurement_kind"] == "deterministic-simulation"
    assert payload["claim_status"] == "descriptive-only"
    assert payload["fictional_fixture"] is True
    assert payload["external_systems_called"] is False
    for decisions in payload["decisions"].values():
        refused = decisions["5000:5000"]
        assert refused["status"] == "refused"
        assert refused["failure_reason"]

    effect = payload["independent_effect_drill"]
    assert effect["measurement_kind"] == "simulated-effect-target"
    assert effect["physical_apply_count"] == 1
    assert effect["external_effects_possible"] is False
