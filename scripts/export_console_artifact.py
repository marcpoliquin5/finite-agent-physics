"""Export the digest-bound deterministic artifact consumed by the Physics Console."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_physics.adaptive_runtime import (
    ADAPTIVE_RUNTIME_LIMITATIONS,
    ADAPTIVE_RUNTIME_SCHEMA_VERSION,
    ADAPTIVE_RUNTIME_SCOPE,
    run_adaptive_recovery_drill,
)
from agent_physics.artifact_store import (
    ArtifactProvenance,
    SQLiteArtifactStore,
    transformation_digest,
)
from agent_physics.artifacts import Artifact, Sensitivity
from agent_physics.contracts import BackendProfile, RunEnvelope, TaskContract
from agent_physics.examples import miami_eoc_envelope, miami_eoc_graph
from agent_physics.decision_explanations import explain_schedule
from agent_physics.experiments import run_registered_experiments, summarize_experiments
from agent_physics.feasibility import FeasibilityAnalyzer
from agent_physics.framework_conformance import (
    LANGGRAPH_TARGET,
    NEUTRAL_TARGET,
    PINNED_LANGGRAPH_CHECKPOINT_VERSION,
    PINNED_LANGGRAPH_VERSION,
    CachePolicy,
    WrapperRuntimePolicy,
    finite_to_wrapper,
    validate_wrapper_manifest,
    wrapper_to_finite,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.ledger import verify_conservation
from agent_physics.mcp_server import finite_capabilities, finite_effect_drill
from agent_physics.physical_resources import analyze_physical_resources
from agent_physics.provider_quota import (
    GLOBAL_GUARD_SCOPE,
    MODEL_SCOPE,
    run_seeded_burst_corpus,
)
from agent_physics.release_manifest import (
    CAPABILITY_IDS,
    INTEGRATED_PROOF_IDS,
    RELEASE_GATE_IDS,
    REQUIRED_EXTERNAL_KINDS,
    SCHEMA_VERSION as RELEASE_MANIFEST_SCHEMA_VERSION,
)
from agent_physics.replanning import (
    EventDrivenReplanner,
    ProviderCapacityEvent,
    RunProgressSnapshot,
)
from agent_physics.resource_ledger import generate_stress_corpus
from agent_physics.run_store import Usage
from agent_physics.scheduler import Scheduler
from agent_physics.semantic_safety import (
    SEMANTIC_SAFETY_SCHEMA_VERSION,
    StormShiftSemanticSafetyVerifier,
    adversarial_mutation_corpus,
    build_reference_semantic_bundle,
    corpus_digest,
)
from agent_physics.serialization import content_digest, normalize
from agent_physics.stormshift import (
    StormShiftValidator,
    build_reference_plan,
    stormshift_fixture,
)
from agent_physics.whole_run_verifier import (
    DIGEST_ALGORITHM as WHOLE_RUN_DIGEST_ALGORITHM,
    WHOLE_RUN_SCHEMA_VERSION,
)
from agent_physics.workflow_ir import compile_python


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps" / "physics-console" / "app" / "demo-artifact.json"

SCENARIOS = {
    "nominal": {
        "label": "Nominal envelope",
        "short_label": "Nominal",
        "binding_annotation": "Response-plan critical path",
        "decision_annotation": "Cheapest qualified profiles preserve every declared floor.",
        "max_parallelism": 4,
        "provider_limits": (("simulated-watsonx", 2), ("local-fixture", 4)),
    },
    "provider": {
        "label": "Modeled Watsonx lane capacity: 1",
        "short_label": "Provider loss",
        "binding_annotation": "Serialized modeled Granite lane",
        "decision_annotation": (
            "Hospital analysis waits; mandatory bilingual and safety tasks remain in-plan."
        ),
        "max_parallelism": 4,
        "provider_limits": (("simulated-watsonx", 1), ("local-fixture", 4)),
    },
    "workers": {
        "label": "Global workers: 2",
        "short_label": "Workers halved",
        "binding_annotation": "Global worker capacity",
        "decision_annotation": (
            "Critical modeled Granite work leads; fixture tasks queue behind it."
        ),
        "max_parallelism": 2,
        "provider_limits": (("simulated-watsonx", 2), ("local-fixture", 4)),
    },
}

TASK_LABELS = {
    "incident_intake": "Incident intake",
    "shelter_status": "Shelter status",
    "transit_status": "Transit status",
    "flood_zones": "Flood zones",
    "hospital_capacity": "Hospital capacity",
    "utility_outages": "Utility outages",
    "social_signal_scan": "Social signal scan",
    "response_plan": "Response plan",
    "safety_review": "Safety review",
    "multilingual_alert": "Bilingual alert",
    "publish_simulated_alert": "Simulated effect intent",
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _configured_envelope(config: dict[str, object]):  # type: ignore[no-untyped-def]
    return replace(
        miami_eoc_envelope(),
        max_parallelism=int(config["max_parallelism"]),
        provider_limits=tuple(config["provider_limits"]),  # type: ignore[arg-type]
    )


def _sealed_summary(fields: dict[str, object]) -> dict[str, object]:
    """Return a JSON-native summary whose boundary text is included in its digest."""

    normalized = normalize(fields)
    if not isinstance(normalized, dict):  # pragma: no cover - local construction invariant
        raise TypeError("summary fields must normalize to an object")
    return {**normalized, "summary_digest": content_digest(normalized)}


def _physical_admission_summary() -> dict[str, object]:
    intake = BackendProfile(
        name="physical-intake-fixture",
        provider="local-physical-fixture",
        duration_ms_p50=120,
        duration_ms_p95=180,
        input_tokens=80,
        output_tokens=20,
        cost_microusd=120,
        context_bytes=2_000,
        quality=1.0,
        cpu_time_ms=150,
        peak_memory_bytes=64 * 1024 * 1024,
        peak_vram_bytes=16 * 1024 * 1024,
        storage_read_bytes=120_000,
        storage_write_bytes=80_000,
        network_ingress_bytes=30_000,
        network_egress_bytes=20_000,
        min_bandwidth_bps=700_000,
        network_rtt_ms=20,
        egress_cost_microusd=50,
    )
    synthesis = BackendProfile(
        name="physical-synthesis-fixture",
        provider="local-physical-fixture",
        duration_ms_p50=240,
        duration_ms_p95=360,
        input_tokens=160,
        output_tokens=40,
        cost_microusd=240,
        context_bytes=4_000,
        quality=1.0,
        cpu_time_ms=250,
        peak_memory_bytes=96 * 1024 * 1024,
        peak_vram_bytes=24 * 1024 * 1024,
        storage_read_bytes=180_000,
        storage_write_bytes=120_000,
        network_ingress_bytes=50_000,
        network_egress_bytes=40_000,
        min_bandwidth_bps=900_000,
        network_rtt_ms=35,
        egress_cost_microusd=70,
    )
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("physical_intake", (intake,)),
            TaskContract("physical_synthesis", (synthesis,), ("physical_intake",)),
        )
    )
    envelope = RunEnvelope(
        deadline_ms=5_000,
        max_tokens=1_000,
        max_cost_microusd=2_000,
        max_context_bytes=16_000,
        max_parallelism=2,
        provider_limits=(("local-physical-fixture", 2),),
        max_cpu_time_ms=500,
        max_peak_memory_bytes=192 * 1024 * 1024,
        max_peak_vram_bytes=64 * 1024 * 1024,
        max_storage_read_bytes=400_000,
        max_storage_write_bytes=300_000,
        max_network_ingress_bytes=120_000,
        max_network_egress_bytes=100_000,
        available_bandwidth_bps=2_000_000,
        max_network_rtt_ms=40,
        max_egress_cost_microusd=200,
    )
    physical_caps = {
        "max_cpu_time_ms": envelope.max_cpu_time_ms,
        "max_peak_memory_bytes": envelope.max_peak_memory_bytes,
        "max_peak_vram_bytes": envelope.max_peak_vram_bytes,
        "max_storage_read_bytes": envelope.max_storage_read_bytes,
        "max_storage_write_bytes": envelope.max_storage_write_bytes,
        "max_network_ingress_bytes": envelope.max_network_ingress_bytes,
        "max_network_egress_bytes": envelope.max_network_egress_bytes,
        "available_bandwidth_bps": envelope.available_bandwidth_bps,
        "max_network_rtt_ms": envelope.max_network_rtt_ms,
        "max_egress_cost_microusd": envelope.max_egress_cost_microusd,
    }
    report = analyze_physical_resources(
        graph,
        envelope,
        {"physical_intake": intake, "physical_synthesis": synthesis},
    )
    report_payload = report.as_dict()
    coverage = report_payload["coverage_matrix"]
    if not isinstance(coverage, list):  # pragma: no cover - normalized report invariant
        raise RuntimeError("physical coverage matrix was not normalized")
    totals = report_payload["totals"]
    if not isinstance(totals, dict):  # pragma: no cover - normalized report invariant
        raise RuntimeError("physical totals were not normalized")
    energy = next(
        entry
        for entry in coverage
        if isinstance(entry, dict) and entry.get("dimension") == "energy"
    )
    if (
        report.status.value != "admitted"
        or not report.verify_digest()
        or not all(check.passed for check in report.checks)
        or energy.get("status") != "unsupported"
    ):
        raise RuntimeError("physical admission evidence failed its construction guards")
    return _sealed_summary(
        {
            "schema_version": report.schema_version,
            "measurement_kind": "deterministic-local-resource-estimate",
            "claim_status": "bounded-admission-only",
            "declared_envelope_caps": {
                "deadline_ms": envelope.deadline_ms,
                "max_tokens": envelope.max_tokens,
                "max_cost_microusd": envelope.max_cost_microusd,
                "max_context_bytes": envelope.max_context_bytes,
                "max_parallelism": envelope.max_parallelism,
            },
            "declared_physical_caps": physical_caps,
            "declared_physical_cap_count": len(physical_caps),
            "all_declared_physical_caps_nonzero": all(
                value > 0 for value in physical_caps.values()
            ),
            "all_observed_physical_totals_nonzero": all(
                isinstance(value, int) and value > 0 for value in totals.values()
            ),
            "report": report_payload,
            "coverage_dimension_count": len(coverage),
            "energy_boundary": energy,
            "external_systems_called": False,
        }
    )


def _adaptive_recovery_summary() -> dict[str, object]:
    with TemporaryDirectory(prefix="finite-console-adaptive-") as temporary_directory:
        result = run_adaptive_recovery_drill(
            Path(temporary_directory) / "adaptive-recovery.sqlite3"
        )
    if (
        not result.replay_passed
        or result.control_digest != result.replay_control_digest
        or result.external_provider_calls != 0
    ):
        raise RuntimeError("adaptive recovery drill failed its replay guards")
    return _sealed_summary(
        {
            "schema_version": ADAPTIVE_RUNTIME_SCHEMA_VERSION,
            "measurement_kind": "deterministic-local-crash-restart-drill",
            "final_status": result.final_status.value,
            "control_digest": result.control_digest,
            "replay_control_digest": result.replay_control_digest,
            "replay_passed": result.replay_passed,
            "call_free_replay": True,
            "worker_calls_during_replay": 0,
            "first_process_worker_calls": result.first_process_worker_calls,
            "restart_worker_calls": result.restart_worker_calls,
            "resumed_task_ids": result.resumed_task_ids,
            "unknown_task_ids": result.unknown_task_ids,
            "shed_task_ids": result.shed_task_ids,
            "completed_task_ids": result.completed_task_ids,
            "provider_reset_honored": result.provider_reset_honored,
            "external_provider_calls": result.external_provider_calls,
            "controller_record_count": result.controller_record_count,
            "scope": ADAPTIVE_RUNTIME_SCOPE,
            "limitations": ADAPTIVE_RUNTIME_LIMITATIONS,
        }
    )


def _semantic_safety_summary() -> dict[str, object]:
    verifier = StormShiftSemanticSafetyVerifier()
    baseline = verifier.verify(build_reference_semantic_bundle())
    corpus = adversarial_mutation_corpus()
    mutation_results: list[dict[str, object]] = []
    all_expected_failures_observed = True
    refused_count = 0
    for mutation in corpus:
        report = verifier.verify(mutation.bundle)
        observed_failed_checks = tuple(
            check.check_id for check in report.checks if not check.passed
        )
        expected_observed = all(
            check_id in observed_failed_checks for check_id in mutation.expected_failed_checks
        )
        all_expected_failures_observed &= expected_observed
        refused_count += int(not report.passed)
        mutation_results.append(
            {
                "mutation_id": mutation.mutation_id,
                "mutation_digest": mutation.mutation_digest,
                "expected_failed_checks": mutation.expected_failed_checks,
                "observed_failed_checks": observed_failed_checks,
                "expected_failures_observed": expected_observed,
                "report_digest": report.report_digest,
                "report_digest_verified": report.verify_digest(),
            }
        )
    if (
        not baseline.passed
        or not baseline.verify_digest()
        or refused_count != len(corpus)
        or not all_expected_failures_observed
        or not all(item["report_digest_verified"] for item in mutation_results)
    ):
        raise RuntimeError("bounded semantic-safety corpus failed its guards")
    return _sealed_summary(
        {
            "schema_version": SEMANTIC_SAFETY_SCHEMA_VERSION,
            "measurement_kind": "bounded-deterministic-structural-verification",
            "baseline_passed": baseline.passed,
            "baseline_bundle_digest": baseline.bundle_digest,
            "baseline_report_digest": baseline.report_digest,
            "baseline_report_digest_verified": baseline.verify_digest(),
            "bounded_check_ids": tuple(check.check_id for check in baseline.checks),
            "bounded_check_count": len(baseline.checks),
            "adversarial_corpus_digest": corpus_digest(corpus),
            "adversarial_mutation_count": len(corpus),
            "adversarial_refused_count": refused_count,
            "all_expected_failures_observed": all_expected_failures_observed,
            "mutation_results": mutation_results,
            "scope": baseline.scope,
            "limitations": baseline.limitations,
            "external_systems_called": False,
        }
    )


def _artifact_store_restart_summary() -> dict[str, object]:
    parent = Artifact.create(
        b'{"source":"finite-console-fixture"}',
        schema="finite.console.source",
        schema_version="1.0.0",
        media_type="application/json",
        producer="console-artifact-builder",
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=1_000,
        fresh_until_ms=10_000,
    )
    child = Artifact.create(
        b'{"derived":true,"source":"finite-console-fixture"}',
        schema="finite.console.derived",
        schema_version="1.0.0",
        media_type="application/json",
        producer="console-artifact-builder",
        parents=(parent.artifact_id,),
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=1_100,
        fresh_until_ms=10_000,
    )
    provenance = ArtifactProvenance.create(
        artifact_id=child.artifact_id,
        run_id="console-artifact-store-restart-v1",
        task_id="derive-console-evidence",
        attempt=1,
        producer_event_digest="a" * 64,
        transformation_digest=transformation_digest(
            revision="console-artifact-store/v1",
            parameters={"deterministic": True},
        ),
        input_artifact_ids=child.parents,
    )
    with TemporaryDirectory(prefix="finite-console-artifacts-") as temporary_directory:
        database_path = Path(temporary_directory) / "artifacts.sqlite3"
        first_process = SQLiteArtifactStore(database_path)
        parent_inserted = first_process.put(parent)
        child_inserted = first_process.put(child, provenance=provenance)
        duplicate_inserted = first_process.put(child, provenance=provenance)

        restarted = SQLiteArtifactStore(database_path)
        parent_round_trip = restarted.get(parent.artifact_id) == parent
        child_round_trip = restarted.get(child.artifact_id) == child
        provenance_round_trip = restarted.provenance(child.artifact_id) == provenance
        artifact_ids = restarted.artifact_ids()
        verification = restarted.verify_all()
    if not (
        parent_inserted
        and child_inserted
        and not duplicate_inserted
        and parent_round_trip
        and child_round_trip
        and provenance_round_trip
        and verification.passed
        and verification.verify_digest()
    ):
        raise RuntimeError("artifact-store restart drill failed its integrity guards")
    return _sealed_summary(
        {
            "schema_version": "finite-artifact-store-restart-summary/v1",
            "measurement_kind": "deterministic-local-sqlite-restart-drill",
            "parent_inserted": parent_inserted,
            "child_inserted": child_inserted,
            "duplicate_inserted": duplicate_inserted,
            "deduplication_preserved": not duplicate_inserted,
            "parent_round_trip_verified": parent_round_trip,
            "child_round_trip_verified": child_round_trip,
            "provenance_round_trip_verified": provenance_round_trip,
            "artifact_ids": artifact_ids,
            "artifact_count": verification.artifact_count,
            "provenance_count": verification.provenance_count,
            "verification_passed": verification.passed,
            "verification_failures": verification.failures,
            "verification_digest": verification.verification_digest,
            "verification_digest_verified": verification.verify_digest(),
            "external_systems_called": False,
            "limitations": (
                "SQLite is a local single-database durability boundary, not distributed consensus",
                "content addresses and record digests detect mutation but do not authenticate producers",
                "this drill does not claim remote replication, backup recovery, or access-control enforcement",
            ),
        }
    )


def _conformance_workflow_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "envelope": {
            "deadline_ms": 5_000,
            "max_tokens": 3_000,
            "max_cost_microusd": 9_000,
            "max_context_bytes": 32_000,
            "max_parallelism": 2,
            "min_modeled_success_probability": 0.9,
            "provider_limits": {"simulated-watsonx": 1, "local-fixture": 2},
        },
        "tasks": [
            {
                "task_id": "collect",
                "profiles": [
                    {
                        "name": "local-reader",
                        "provider": "local-fixture",
                        "duration_ms_p50": 10,
                        "duration_ms_p95": 20,
                        "quality": 0.9,
                    }
                ],
                "effect": {"kind": "read", "resource": "fictional-input"},
                "output_ports": [
                    {
                        "name": "facts",
                        "schema": "finite-fictional-facts",
                        "schema_version": "1",
                        "media_type": "application/json",
                    }
                ],
                "adapter_requirements": {
                    "cancellation": "cooperative",
                    "checkpoint": "receipt",
                    "streaming": False,
                    "usage": "provider_reported",
                    "effect_fencing": False,
                    "max_hidden_retries": 0,
                },
            },
            {
                "task_id": "propose_publish",
                "profiles": [
                    {
                        "name": "simulated-granite",
                        "provider": "simulated-watsonx",
                        "duration_ms_p50": 50,
                        "duration_ms_p95": 100,
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cost_microusd": 500,
                        "context_bytes": 2_000,
                        "quality": 0.98,
                    }
                ],
                "dependencies": ["collect"],
                "input_ports": [
                    {
                        "name": "facts",
                        "source_task_id": "collect",
                        "source_port": "facts",
                        "schema": "finite-fictional-facts",
                        "schema_version": "1",
                        "media_type": "application/json",
                    }
                ],
                "effect": {
                    "kind": "irreversible_write",
                    "resource": "simulation-preview",
                    "requires_approval": True,
                    "idempotency_key": "console-preview/${run_id}",
                },
                "min_quality": 0.9,
                "adapter_requirements": {
                    "cancellation": "cooperative",
                    "checkpoint": "receipt",
                    "streaming": False,
                    "usage": "provider_reported",
                    "effect_fencing": True,
                    "max_hidden_retries": 0,
                },
            },
        ],
    }


def _framework_conformance_summary() -> dict[str, object]:
    workflow = compile_python(_conformance_workflow_document())
    bindings = {
        "collect": ("validate-fictional-input/v1",),
        "propose_publish": (
            "validate-preview-intent/v1",
            "validate-no-external-send/v1",
        ),
    }
    cache_policy = CachePolicy(
        "content_addressed_readwrite",
        ("workflow_digest", "task_id", "dependency_output_digests"),
    )
    runtime_policy = WrapperRuntimePolicy(3, 1, "resumable", "fenced_commit")
    neutral = finite_to_wrapper(
        workflow,
        target=NEUTRAL_TARGET,
        validator_bindings=bindings,
        cache_policy=cache_policy,
        runtime_policy=runtime_policy,
    )
    langgraph = finite_to_wrapper(
        workflow,
        target=LANGGRAPH_TARGET,
        validator_bindings=bindings,
        cache_policy=cache_policy,
        runtime_policy=runtime_policy,
        loss_policy="record",
    )
    neutral_problems = validate_wrapper_manifest(neutral)
    langgraph_problems = validate_wrapper_manifest(langgraph)
    neutral_round_trip = wrapper_to_finite(neutral)
    langgraph_round_trip = wrapper_to_finite(langgraph)
    if (
        neutral_problems
        or langgraph_problems
        or neutral.semantic_losses
        or neutral_round_trip.workflow.digest != workflow.digest
        or langgraph_round_trip.workflow.digest != workflow.digest
        or not neutral.verify_digest()
        or not langgraph.verify_digest()
    ):
        raise RuntimeError("framework conformance manifests failed their guards")
    return _sealed_summary(
        {
            "schema_version": neutral.schema_version,
            "measurement_kind": "deterministic-wrapper-conversion-no-framework-execution",
            "source_workflow_digest": workflow.digest,
            "neutral": {
                "target": neutral.target,
                "manifest_digest": neutral.manifest_digest,
                "manifest_digest_verified": neutral.verify_digest(),
                "validation_problems": neutral_problems,
                "round_trip_workflow_digest": neutral_round_trip.workflow.digest,
                "round_trip_exact": neutral_round_trip.workflow.digest == workflow.digest,
                "semantic_loss_count": len(neutral.semantic_losses),
                "feature_accounting": neutral.feature_accounting,
                "claim_boundaries": neutral.claim_boundaries,
            },
            "langgraph": {
                "target": langgraph.target,
                "manifest_digest": langgraph.manifest_digest,
                "manifest_digest_verified": langgraph.verify_digest(),
                "validation_problems": langgraph_problems,
                "round_trip_workflow_digest": langgraph_round_trip.workflow.digest,
                "round_trip_exact": langgraph_round_trip.workflow.digest == workflow.digest,
                "semantic_loss_count": len(langgraph.semantic_losses),
                "semantic_losses": langgraph.semantic_losses,
                "feature_accounting": langgraph.feature_accounting,
                "claim_boundaries": langgraph.claim_boundaries,
                "pinned_langgraph_version": PINNED_LANGGRAPH_VERSION,
                "pinned_checkpoint_version": PINNED_LANGGRAPH_CHECKPOINT_VERSION,
                "actual_framework_execution_witness_present": False,
            },
            "optional_framework_execution": "not-executed",
            "optional_framework_required_for_artifact_build": False,
            "model_calls_made": 0,
            "external_calls_made": 0,
        }
    )


def _release_verifier_boundaries_summary() -> dict[str, object]:
    return _sealed_summary(
        {
            "schema_version": "finite-release-verifier-boundaries/v1",
            "measurement_kind": "capability-inventory-only",
            "release_manifest": {
                "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
                "capability_id_count": len(CAPABILITY_IDS),
                "capability_id_set_digest": content_digest(CAPABILITY_IDS),
                "integrated_proof_id_count": len(INTEGRATED_PROOF_IDS),
                "integrated_proof_id_set_digest": content_digest(INTEGRATED_PROOF_IDS),
                "release_gate_id_count": len(RELEASE_GATE_IDS),
                "release_gate_id_set_digest": content_digest(RELEASE_GATE_IDS),
                "required_external_kinds": tuple(sorted(REQUIRED_EXTERNAL_KINDS)),
                "capabilities": (
                    "strict exact-field and identifier-set validation",
                    "referenced local artifact byte-length and SHA-256 verification",
                    "freshness, source-commit, classification, and release-gate consistency checks",
                ),
                "boundaries": (
                    "the validator never fetches a URI",
                    "the validator does not verify third-party signatures",
                    "the validator does not decide whether Bob or provider attestation text is truthful",
                ),
            },
            "whole_run_verifier": {
                "schema_version": WHOLE_RUN_SCHEMA_VERSION,
                "digest_algorithm": WHOLE_RUN_DIGEST_ALGORITHM,
                "capabilities": (
                    "strict canonical envelope and record-digest validation",
                    "run, graph, manifest, policy, and envelope identity binding",
                    "monotonic event-chain and declared causal-link validation",
                    "token, cost-microusd, and context-byte conservation",
                    "artifact and claim causality plus freshness validation",
                    "context-obligation binding",
                    "approval and effect lifecycle uniqueness",
                    "call-free replay-witness binding",
                ),
                "independent_of_scheduler_executor_provider_and_planner": True,
                "executed_for_this_console_summary": False,
                "boundaries": (
                    "SHA-256 mutation detection is not producer authentication",
                    "a malicious evidence producer requires a separately trusted outer signature",
                    "this capability inventory is not itself a sealed whole-run evidence package",
                ),
            },
            "release_ready_claim": False,
            "external_attestations_present": False,
            "external_systems_called": False,
        }
    )


def _v5_evidence_boundaries() -> dict[str, object]:
    return _sealed_summary(
        {
            "schema_version": "finite-console-v5-boundaries/v1",
            "simulation_only": True,
            "fictional_fixture": True,
            "live_bob_session_present": False,
            "bob_capability_inventory_only": True,
            "live_watsonx_or_granite_calls": 0,
            "watsonx_profiles_are_simulated": True,
            "live_alibaba_pageagent_execution_present": False,
            "live_beeai_execution_present": False,
            "public_deployment_receipt_present": False,
            "external_attestations_present": False,
            "release_ready_claim": False,
            "limitations": (
                "local deterministic evidence does not substitute for a recorded live Bob session",
                "simulated-watsonx profile labels do not prove a watsonx or Granite provider call",
                "framework wrapper conversion does not prove target-runtime enforcement",
                "checked-in console evidence is not a public deployment or hackathon submission receipt",
            ),
        }
    )


def build_payload() -> dict[str, object]:
    graph = miami_eoc_graph()
    scheduler = Scheduler()
    analyzer = FeasibilityAnalyzer()
    witnesses: dict[str, object] = {}
    decisions: dict[str, object] = {}

    for scenario_id, config in SCENARIOS.items():
        base_envelope = _configured_envelope(config)
        result = scheduler.schedule(graph, base_envelope)
        certificate, _ = analyzer.analyze(graph, base_envelope)
        conservation = verify_conservation(graph, base_envelope, result)
        entries = []
        for entry in result.entries:
            task = graph.by_id[entry.task_id]
            lane = (
                "effect"
                if task.effect.kind.writes
                else "granite"
                if entry.provider == "simulated-watsonx"
                else "fixture"
            )
            entries.append(
                {
                    "id": entry.task_id,
                    "label": TASK_LABELS[entry.task_id],
                    "lane": lane,
                    "start": entry.start_ms,
                    "end": entry.end_ms,
                    "mandatory": not task.optional,
                }
            )
        witnesses[scenario_id] = {
            "label": config["label"],
            "short_label": config["short_label"],
            "binding_annotation": config["binding_annotation"],
            "decision_annotation": config["decision_annotation"],
            "provider_cap": base_envelope.provider_limit("simulated-watsonx"),
            "workers": base_envelope.max_parallelism,
            "result": {
                "success": result.success,
                "makespan_ms": result.makespan_ms,
                "model_bound_ms": result.model_bound_ms,
                "total_tokens": result.total_tokens,
                "total_cost_microusd": result.total_cost_microusd,
                "total_context_bytes": result.total_context_bytes,
                "modeled_success_probability": result.modeled_success_probability,
                "entries": entries,
            },
            "certificate_digest": certificate.certificate_digest,
            "trace_digest": conservation.trace_digest,
            "trace_verified": conservation.passed,
        }

        scenario_decisions: dict[str, object] = {}
        for deadline_ms in range(5_000, 12_001, 1_000):
            for cost_cap in range(5_000, 16_001, 250):
                envelope = replace(
                    base_envelope,
                    deadline_ms=deadline_ms,
                    max_cost_microusd=cost_cap,
                )
                decision, _ = analyzer.analyze(graph, envelope)
                scenario_decisions[f"{deadline_ms}:{cost_cap}"] = {
                    "status": decision.status.value,
                    "failure_reason": decision.failure_reason,
                    "certificate_digest": decision.certificate_digest,
                    "projected_makespan_ms": decision.projected_makespan_ms,
                    "model_bound_ms": decision.model_bound_ms,
                }
        decisions[scenario_id] = scenario_decisions

    stormshift = stormshift_fixture()
    stormshift_plan = build_reference_plan(stormshift)
    stormshift_report = StormShiftValidator().validate(stormshift, stormshift_plan)
    experiment_records = run_registered_experiments(revision="console-artifact-v5")
    experiment_summary = summarize_experiments(experiment_records)
    experiment_design = experiment_summary["design"]
    effect_drill = finite_effect_drill("hard")
    capabilities = finite_capabilities()
    resource_corpus = generate_stress_corpus()
    resource_replay = resource_corpus.verify()
    quota_corpus = run_seeded_burst_corpus()

    replanner = EventDrivenReplanner()
    replan_envelope = replace(miami_eoc_envelope(), max_context_bytes=29_500)
    initial_state = replanner.initial_state(
        graph,
        replan_envelope,
        run_id="console-stormshift-replanning",
    )
    first_progress = RunProgressSnapshot.from_state(
        initial_state,
        completed_task_ids=("incident_intake",),
        settled_usage=Usage(context_bytes=900),
        elapsed_ms=2_000,
    )
    first_event = ProviderCapacityEvent(
        "watsonx-capacity-drop",
        2_000,
        "simulated-watsonx",
        1,
    )
    first_replan = replanner.replan(
        graph,
        initial_state,
        first_event,
        first_progress,
    )
    second_progress = RunProgressSnapshot.from_state(
        first_replan.state,
        completed_task_ids=("incident_intake",),
        settled_usage=Usage(tokens=150, cost_microusd=200, context_bytes=1_200),
        elapsed_ms=2_500,
    )
    second_event = ProviderCapacityEvent(
        "fixture-capacity-drop",
        2_500,
        "local-fixture",
        1,
    )
    second_replan = replanner.replan(
        graph,
        first_replan.state,
        second_event,
        second_progress,
    )
    replan_verified = replanner.verify_transition(
        graph,
        initial_state,
        first_event,
        first_progress,
        first_replan,
    ) and replanner.verify_transition(
        graph,
        first_replan.state,
        second_event,
        second_progress,
        second_replan,
    )

    explanation_cases = (
        miami_eoc_envelope(),
        replace(miami_eoc_envelope(), max_context_bytes=30_000),
        replace(
            miami_eoc_envelope(),
            deadline_ms=6_200,
            max_parallelism=2,
            provider_limits=(("simulated-watsonx", 1), ("local-fixture", 4)),
        ),
    )
    explanation_bundles = tuple(
        explain_schedule(graph, envelope, Scheduler().schedule(graph, envelope))
        for envelope in explanation_cases
    )
    physical_admission = _physical_admission_summary()
    adaptive_recovery = _adaptive_recovery_summary()
    semantic_safety = _semantic_safety_summary()
    artifact_store_restart = _artifact_store_restart_summary()
    framework_conformance = _framework_conformance_summary()
    release_verifier_boundaries = _release_verifier_boundaries_summary()
    evidence_boundaries = _v5_evidence_boundaries()
    return {
        "schema_version": "finite-console-payload/v2",
        "release_generation": "v5",
        "measurement_kind": "deterministic-simulation",
        "claim_status": "descriptive-only",
        "fictional_fixture": True,
        "external_systems_called": False,
        "bob_mcp_tool_count": capabilities["tool_count"],
        "witnesses": witnesses,
        "decisions": decisions,
        "protected_minima": {
            "tokens": witnesses["nominal"]["result"]["total_tokens"],  # type: ignore[index]
            "cost_microusd": witnesses["nominal"]["result"]["total_cost_microusd"],  # type: ignore[index]
            "context_bytes": witnesses["nominal"]["result"]["total_context_bytes"],  # type: ignore[index]
        },
        "mandatory_task_count": sum(not task.optional for task in graph.tasks),
        "total_task_count": len(graph.tasks),
        "stormshift_structural_validation": {
            "passed": stormshift_report.passed,
            "report_digest": stormshift_report.report_digest,
            "digest_verified": stormshift_report.verify_digest(),
            "scope": (
                "typed fixture identity, capacity, routes, modeled closures, declared "
                "accessibility fields, bilingual numeric parity, citation IDs/freshness, "
                "and declared no-publication state"
            ),
            "limitations": (
                "does not prove semantic translation equivalence, rendered accessibility, "
                "claim entailment, or an external delivery-system state"
            ),
        },
        "registered_fault_experiment": {
            "measurement_kind": experiment_summary["measurement_kind"],
            "claim_status": experiment_summary["claim_status"],
            "revision_provenance": experiment_summary["revision_provenance"],
            "raw_record_count": len(experiment_records),
            "paired_seed_count": experiment_design["paired_seed_count"],
            "condition_count": len(experiment_design["condition_ids"]),
            "policy_count": len(experiment_design["policies"]),
            "experiment_config_digest": experiment_summary["experiment_config_digest"],
            "comparison_scope": (
                "adaptive baseline with static_parallel and sequential development references"
            ),
        },
        "independent_effect_drill": {
            "measurement_kind": effect_drill["measurement_kind"],
            "injected_fault": effect_drill["injected_fault"],
            "final_state": effect_drill["final_state"],
            "physical_apply_count": effect_drill["physical_apply_count"],
            "external_effects_possible": effect_drill["external_effects_possible"],
        },
        "resource_ledger_stress": {
            "transition_count": resource_corpus.transition_count,
            "independent_replay_passed": resource_replay.passed,
            "trace_digest": resource_corpus.trace_digest,
            "scope": "single-process deterministic integer accounting",
        },
        "provider_quota_stress": {
            "model_scope": MODEL_SCOPE,
            "aggregate_guard_scope": GLOBAL_GUARD_SCOPE,
            "logical_calls": quota_corpus.logical_calls,
            "admission_requests": quota_corpus.admission_requests,
            "settled_calls": quota_corpus.settled_calls,
            "refused_admissions": quota_corpus.refused_admissions,
            "reset_suppressed_retries": quota_corpus.reset_suppressed_retries,
            "event_count": quota_corpus.event_count,
            "event_digest": quota_corpus.digest,
        },
        "replanning_witness": {
            "event_count": 2,
            "final_revision": second_replan.state.revision,
            "first_disposition": first_replan.decision.disposition.value,
            "first_reason_code": first_replan.decision.reason.code.value,
            "shed_task_ids": first_replan.decision.shed_task_ids,
            "second_disposition": second_replan.decision.disposition.value,
            "second_reason_code": second_replan.decision.reason.code.value,
            "state_chain_verified": replan_verified,
            "first_decision_digest": first_replan.decision.decision_digest,
            "second_decision_digest": second_replan.decision.decision_digest,
            "scope": "modeled residual-graph replanning, not live executor mutation",
        },
        "decision_explanation_evidence": {
            "case_count": len(explanation_bundles),
            "record_count": sum(len(bundle.records) for bundle in explanation_bundles),
            "one_record_per_event": True,
            "reasoning_access": False,
            "bundle_ids": tuple(bundle.bundle_id for bundle in explanation_bundles),
            "scope": "post-hoc public numeric facts, not chain-of-thought",
        },
        "physical_resource_admission": physical_admission,
        "adaptive_crash_restart_recovery": adaptive_recovery,
        "bounded_semantic_safety": semantic_safety,
        "artifact_store_restart_integrity": artifact_store_restart,
        "framework_conformance_loss_accounting": framework_conformance,
        "release_and_whole_run_verifier_boundaries": release_verifier_boundaries,
        "v5_evidence_boundaries": evidence_boundaries,
    }


def main() -> None:
    payload = build_payload()
    canonical_payload = _canonical_json(payload)
    envelope = {
        "schema_version": "finite-console-artifact/v1",
        "digest_algorithm": "sha256",
        "sha256": hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        "canonical_payload": canonical_payload,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
