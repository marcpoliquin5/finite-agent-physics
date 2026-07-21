import copy
import hashlib
import json
from pathlib import Path

import pytest

from agent_physics.judge_bundle import (
    ENVELOPE_SCHEMA_VERSION,
    build_judge_evidence,
    resolve_source_revision,
    verify_console_artifact,
    verify_judge_envelope,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def judge_bundle():  # type: ignore[no-untyped-def]
    return build_judge_evidence(revision="judge-test-revision", project_root=ROOT)


def test_bundle_combines_complete_labeled_fail_closed_evidence(judge_bundle) -> None:  # type: ignore[no-untyped-def]
    assert judge_bundle.verify()
    assert len(judge_bundle.content_digest) == 64
    envelope = judge_bundle.envelope
    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    content = envelope["content"]
    labels = content["labels"]
    assert labels["overall_claim_status"] == "descriptive-only"
    assert labels["external_systems_called"] is False
    assert labels["live_provider_or_model_calls"] is False
    assert labels["superiority_claimed"] is False

    workflow_ir = content["workflow_ir_equivalence"]
    assert workflow_ir["input_forms"] == [
        "python-mapping",
        "strict-json",
        "safe-yaml",
    ]
    assert workflow_ir["equivalent_digest_count"] == 1
    assert workflow_ir["unknown_fields_fail_closed"] is True
    assert workflow_ir["workflow_digest"] == workflow_ir["canonical_json_sha256"]

    resource_ledger = content["resource_ledger_stress"]
    assert resource_ledger["seed"] == 20_260_731
    assert resource_ledger["transition_count"] == 10_000
    assert resource_ledger["independent_replay_passed"] is True
    assert resource_ledger["trace_digest"] == (
        "5811acacd3df896505265362b7491606094b8b96d7dc25e3c474a92fc38a200d"
    )

    quota = content["provider_quota_stress"]
    assert quota["logical_calls"] == 1_200
    assert quota["admitted_calls"] == quota["settled_calls"] == 384
    assert quota["reset_suppressed_retries"] == 18
    assert quota["independent_replay_passed"] is True
    assert quota["model_scope"] == "local_declared_quota_model_not_provider_measurement"
    assert quota["aggregate_guard_scope"] == (
        "per_instance_only_not_process_global_or_distributed"
    )

    replanning = content["event_driven_replanning"]
    assert replanning["first_transition"]["disposition"] == "scheduled"
    assert replanning["first_transition"]["shed_task_ids"] == ["social_signal_scan"]
    assert replanning["second_transition"]["disposition"] == "refused"
    assert replanning["final_revision"] == 2
    assert replanning["state_chain_verified"] is True
    assert replanning["completed_work_replayed"] is False

    explanations = content["decision_explanations"]
    assert explanations["case_count"] == 3
    assert explanations["record_count"] == 79
    assert explanations["reasoning_access"] is False
    assert all(case["one_record_per_event"] for case in explanations["cases"])
    assert all(case["verified"] for case in explanations["cases"])

    preflight = content["preflight_and_conservation"]
    assert preflight["feasible"]["certificate"]["status"] == "feasible"
    assert preflight["impossible"]["certificate"]["status"] == "refused"
    assert preflight["feasible"]["conservation"]["passed"] is True
    assert preflight["impossible"]["conservation"]["passed"] is True
    assert len(preflight["feasible"]["conservation"]["trace_digest"]) == 64

    stormshift = content["stormshift_structural_validation"]
    assert stormshift["nominal"]["passed"] is True
    assert stormshift["adversarial_case_count"] == 5
    assert all(not case["passed"] for case in stormshift["adversarial"])
    assert all(case["failed_check_names"] for case in stormshift["adversarial"])

    executor = content["durable_executor_drill"]
    assert executor["resumed_task_count"] == executor["task_count"] == 11
    assert executor["effect"] == {
        "effect_state": "proposed",
        "executed_externally": False,
        "nondeterministic_local_intent_id_omitted": True,
    }

    experiment = content["fault_experiments"]
    assert experiment["raw_record_count"] == 450
    assert experiment["complete_design_validated"] is True
    assert experiment["claim_status"] == "descriptive-only"
    assert experiment["revision_provenance"] == "caller-supplied-unverified"
    assert experiment["summary"]["design"]["paired_seed_count"] == 30
    assert len(experiment["summary"]["groups"]) == 15

    console = content["console_artifact_verification"]
    assert console["sha256_verified"] is True
    assert console["canonical_payload_verified"] is True
    assert console["decision_count"] == 1_080


def test_bundle_and_optional_jsonl_are_byte_repeatable_and_digest_bound(
    judge_bundle, tmp_path: Path  # type: ignore[no-untyped-def]
) -> None:
    first = tmp_path / "a" / "judge.json"
    second = tmp_path / "b" / "judge.json"
    first_raw = tmp_path / "a" / "records.jsonl"
    second_raw = tmp_path / "b" / "records.jsonl"
    judge_bundle.write(first, raw_experiments_path=first_raw)
    judge_bundle.write(second, raw_experiments_path=second_raw)

    assert first.read_bytes() == second.read_bytes()
    assert first_raw.read_bytes() == second_raw.read_bytes()
    assert len(first_raw.read_text(encoding="utf-8").splitlines()) == 450
    loaded = json.loads(first.read_text(encoding="utf-8"))
    assert verify_judge_envelope(loaded)
    assert loaded["canonical_content"] == json.dumps(
        loaded["content"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert hashlib.sha256(loaded["canonical_content"].encode("utf-8")).hexdigest() == (
        loaded["content_digest"]
    )
    assert hashlib.sha256(first_raw.read_bytes()).hexdigest() == (
        loaded["content"]["fault_experiments"]["raw_jsonl_sha256"]
    )

    tampered = copy.deepcopy(loaded)
    tampered["content"]["labels"]["superiority_claimed"] = True
    assert not verify_judge_envelope(tampered)


def test_revision_and_console_integrity_fail_closed(tmp_path: Path) -> None:
    supplied = resolve_source_revision(" user-label ", project_root=ROOT)
    assert supplied["revision"] == "user-label"
    assert supplied["revision_provenance"] == "caller-supplied-unverified"
    assert supplied["local_git_object_resolved"] is False
    generated = ROOT / "artifacts" / "generated-judge.json"
    local = resolve_source_revision(
        None,
        project_root=ROOT,
        excluded_status_paths=(generated,),
    )
    assert local["revision_provenance"] == "local-git-head-read"
    assert local["local_git_object_resolved"] is True
    assert len(local["revision"]) in {40, 64}
    assert isinstance(local["worktree_dirty"], bool)
    assert local["status_excluded_derived_paths"] == [
        "artifacts/generated-judge.json"
    ]
    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_source_revision("  ", project_root=ROOT)

    console_path = ROOT / "apps" / "physics-console" / "app" / "demo-artifact.json"
    verified = verify_console_artifact(console_path)
    assert verified["sha256_verified"] is True
    artifact = json.loads(console_path.read_text(encoding="utf-8"))
    artifact["canonical_payload"] += " "
    tampered_path = tmp_path / "tampered-console.json"
    tampered_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="verification failed"):
        verify_console_artifact(tampered_path)
