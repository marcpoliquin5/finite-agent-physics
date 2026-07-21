"""Export the digest-bound deterministic artifact consumed by the Physics Console."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from agent_physics.examples import miami_eoc_envelope, miami_eoc_graph
from agent_physics.decision_explanations import explain_schedule
from agent_physics.experiments import run_registered_experiments, summarize_experiments
from agent_physics.feasibility import FeasibilityAnalyzer
from agent_physics.ledger import verify_conservation
from agent_physics.mcp_server import finite_capabilities, finite_effect_drill
from agent_physics.provider_quota import (
    GLOBAL_GUARD_SCOPE,
    MODEL_SCOPE,
    run_seeded_burst_corpus,
)
from agent_physics.replanning import (
    EventDrivenReplanner,
    ProviderCapacityEvent,
    RunProgressSnapshot,
)
from agent_physics.resource_ledger import generate_stress_corpus
from agent_physics.run_store import Usage
from agent_physics.scheduler import Scheduler
from agent_physics.stormshift import (
    StormShiftValidator,
    build_reference_plan,
    stormshift_fixture,
)


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
    experiment_records = run_registered_experiments(revision="console-artifact-v1")
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
    return {
        "schema_version": "finite-console-payload/v1",
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
