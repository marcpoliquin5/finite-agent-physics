"""Deterministic, offline judge evidence for the FINITE vertical slice.

The bundle intentionally separates measurements from claims.  It combines only local
fixture execution and pinned deterministic models; creating it cannot call a provider,
publish an alert, or commit an external effect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, replace
from importlib import metadata
from pathlib import Path
from typing import Iterable, Mapping

from .contracts import RunEnvelope
from .decision_explanations import DERIVATION_SCOPE, explain_schedule
from .examples import miami_eoc_envelope, miami_eoc_graph
from .experiments import (
    CLAIM_STATUS as EXPERIMENT_CLAIM_STATUS,
    MEASUREMENT_KIND as EXPERIMENT_MEASUREMENT_KIND,
    REVISION_PROVENANCE as EXPERIMENT_REVISION_PROVENANCE,
    ExperimentRecord,
    run_registered_experiments,
    summarize_experiments,
    validate_complete_design,
    write_experiment_jsonl,
)
from .feasibility import FeasibilityAnalyzer, FeasibilityStatus
from .ledger import verify_conservation
from .provider_quota import GLOBAL_GUARD_SCOPE, MODEL_SCOPE, run_seeded_burst_corpus
from .replanning import (
    EventDrivenReplanner,
    ProviderCapacityEvent,
    ReplanDisposition,
    ReplanReasonCode,
    RunProgressSnapshot,
)
from .resource_ledger import generate_stress_corpus
from .run_store import Usage
from .scheduler import Scheduler
from .serialization import canonical_json, content_digest, normalize
from .stormshift import (
    BilingualAlert,
    PublicationDisposition,
    ResponsePlan,
    StormShiftScenario,
    StormShiftValidator,
    build_reference_plan,
    fault_capacity_loss,
    fault_contradiction,
    fault_stale_artifact,
    stormshift_fixture,
)
from .workflow_ir import (
    UNSUPPORTED_SCHEMA_FEATURES,
    WorkflowIRValidationError,
    compile_json,
    compile_python,
    compile_yaml,
)

BUNDLE_SCHEMA_VERSION = "finite-judge-evidence/v2"
ENVELOPE_SCHEMA_VERSION = "finite-judge-evidence-envelope/v1"
DEFAULT_CONSOLE_ARTIFACT = Path("apps/physics-console/app/demo-artifact.json")
_GIT_OBJECT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _sealed(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = normalize(dict(payload))
    assert isinstance(normalized, dict)
    return {**normalized, "component_digest": content_digest(normalized)}


def resolve_source_revision(
    revision: str | None,
    *,
    project_root: Path,
    excluded_status_paths: Iterable[Path] = (),
) -> dict[str, object]:
    """Resolve an honest source label without treating arbitrary input as verified.

    A caller-supplied value is always marked unverified.  With no supplied value, the
    local Git ``HEAD`` is read directly; that proves only which local object was resolved,
    not that it exists on a remote or that a dirty worktree matches it exactly.
    """

    if revision is not None:
        value = revision.strip()
        if not value:
            raise ValueError("caller-supplied revision cannot be empty")
        return {
            "revision": value,
            "revision_provenance": "caller-supplied-unverified",
            "local_git_object_resolved": False,
            "worktree_dirty": None,
            "verification_scope": "not checked against local or remote Git history",
        }

    root = project_root.resolve()
    exclusions: list[str] = []
    for path in excluded_status_paths:
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        exclusions.append(relative)
    status_command = [
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        ".",
        *(f":(exclude,literal){path}" for path in sorted(set(exclusions))),
    ]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        ).stdout.strip().lower()
        status = subprocess.run(
            status_command,
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "no caller revision was supplied and local Git HEAD could not be read"
        ) from exc
    if _GIT_OBJECT_PATTERN.fullmatch(head) is None:
        raise ValueError("local Git returned an invalid object ID")
    return {
        "revision": head,
        "revision_provenance": "local-git-head-read",
        "local_git_object_resolved": True,
        "worktree_dirty": bool(status.strip()),
        "status_excluded_derived_paths": sorted(set(exclusions)),
        "verification_scope": (
            "git rev-parse resolved local HEAD; no remote attestation; dirty state is "
            "reported after excluding only the declared generated-output paths"
        ),
    }


def _environment_provenance(source_revision: Mapping[str, object]) -> dict[str, object]:
    try:
        package_version = metadata.version("agent-physics")
    except metadata.PackageNotFoundError:
        package_version = "uninstalled-source-tree"
    return _sealed(
        {
            "source_revision": source_revision,
            "python": {
                "implementation": platform.python_implementation(),
                "version": ".".join(str(part) for part in sys.version_info[:3]),
            },
            "runtime": {
                "os_name": os.name,
                "sys_platform": sys.platform,
                "machine": platform.machine() or "unknown",
            },
            "package": {"name": "agent-physics", "version": package_version},
            "network_mode": "no-network-calls-by-bundle",
        }
    )


def _preflight_evidence() -> dict[str, object]:
    graph = miami_eoc_graph()
    nominal_envelope = miami_eoc_envelope()
    impossible_envelope = replace(nominal_envelope, max_tokens=1)
    analyzer = FeasibilityAnalyzer()

    def build_case(
        case_id: str,
        envelope: RunEnvelope,
        expected_status: FeasibilityStatus,
        interpretation: str,
    ) -> dict[str, object]:
        certificate, result = analyzer.analyze(graph, envelope)
        conservation = verify_conservation(graph, envelope, result)
        if certificate.status is not expected_status:
            raise RuntimeError(
                f"preflight case {case_id!r} returned {certificate.status.value}, "
                f"expected {expected_status.value}"
            )
        if not certificate.verify_digest() or not conservation.passed:
            raise RuntimeError(f"preflight case {case_id!r} failed its evidence checks")
        return _sealed(
            {
                "case_id": case_id,
                "measurement_kind": "deterministic-simulation",
                "claim_status": "descriptive-only",
                "interpretation": interpretation,
                "certificate": certificate.as_dict(),
                "schedule_result": result.as_dict(),
                "conservation": {
                    "passed": conservation.passed,
                    "trace_digest": conservation.trace_digest,
                    "report_digest": content_digest(conservation),
                    "checks": conservation.checks,
                },
                "external_systems_called": False,
            }
        )

    feasible = build_case(
        "feasible-pinned-envelope",
        nominal_envelope,
        FeasibilityStatus.FEASIBLE,
        "A witness exists under the pinned profile model and declared envelope.",
    )
    impossible = build_case(
        "impossible-required-token-cap",
        impossible_envelope,
        FeasibilityStatus.REFUSED,
        (
            "The required graph cannot be admitted with a one-token cap under the pinned "
            "profiles; this conservative refusal is not a general mathematical proof."
        ),
    )
    return _sealed(
        {
            "measurement_kind": "deterministic-simulation",
            "claim_status": "descriptive-only",
            "feasible": feasible,
            "impossible": impossible,
        }
    )


def _workflow_ir_evidence() -> dict[str, object]:
    document = {
        "schema_version": 1,
        "envelope": {
            "deadline_ms": 1_000,
            "max_tokens": 1_000,
            "max_cost_microusd": 2_000,
            "max_context_bytes": 4_000,
            "max_parallelism": 1,
            "provider_limits": {"local-fixture": 1},
        },
        "tasks": [
            {
                "task_id": "inspect",
                "profiles": [
                    {
                        "name": "fixture",
                        "provider": "local-fixture",
                        "duration_ms_p50": 10,
                        "duration_ms_p95": 20,
                    }
                ],
                "effect": {"kind": "read", "resource": "fictional-input"},
            }
        ],
    }
    json_source = json.dumps(document, sort_keys=False)
    yaml_source = """\
schema_version: 1
envelope:
  deadline_ms: 1000
  max_tokens: 1000
  max_cost_microusd: 2000
  max_context_bytes: 4000
  max_parallelism: 1
  provider_limits: {local-fixture: 1}
tasks:
  - task_id: inspect
    profiles:
      - name: fixture
        provider: local-fixture
        duration_ms_p50: 10
        duration_ms_p95: 20
    effect: {kind: read, resource: fictional-input}
"""
    compiled = (
        compile_python(document),
        compile_json(json_source),
        compile_yaml(yaml_source),
    )
    digests = {item.digest for item in compiled}
    canonical_documents = {item.canonical_json for item in compiled}
    if len(digests) != 1 or len(canonical_documents) != 1:
        raise RuntimeError("workflow IR forms did not compile to one canonical identity")

    unknown_field_refused = False
    try:
        compile_python({**document, "unrecognized_authority": True})
    except WorkflowIRValidationError:
        unknown_field_refused = True
    if not unknown_field_refused:
        raise RuntimeError("workflow IR accepted an unknown root field")

    result = compiled[0]
    return _sealed(
        {
            "measurement_kind": "deterministic-compiler-check",
            "claim_status": "local-schema-equivalence-only",
            "schema_version": result.schema_version,
            "input_forms": ["python-mapping", "strict-json", "safe-yaml"],
            "equivalent_digest_count": len(digests),
            "workflow_digest": result.digest,
            "canonical_json_sha256": hashlib.sha256(
                result.canonical_json.encode("utf-8")
            ).hexdigest(),
            "task_count": len(result.graph.tasks),
            "unknown_fields_fail_closed": unknown_field_refused,
            "unsupported_schema_v1_features": UNSUPPORTED_SCHEMA_FEATURES,
            "external_systems_called": False,
        }
    )


def _resource_ledger_evidence() -> dict[str, object]:
    corpus = generate_stress_corpus()
    report = corpus.verify()
    if (
        not report.passed
        or report.event_count != 10_000
        or report.trace_digest != corpus.trace_digest
        or report.replayed_snapshot != corpus.final_snapshot
    ):
        raise RuntimeError("10,000-transition resource corpus failed independent replay")
    return _sealed(
        {
            "measurement_kind": "deterministic-local-ledger-stress",
            "claim_status": "local-accounting-model-only",
            "seed": corpus.seed,
            "transition_count": corpus.transition_count,
            "trace_digest": corpus.trace_digest,
            "failure_corpus_digest": report.failure_digest,
            "operation_counts": corpus.operation_counts,
            "refusal_counts": corpus.refusal_counts,
            "peak_active_attempts": corpus.peak_active_attempts,
            "final_snapshot": corpus.final_snapshot,
            "independent_replay_passed": report.passed,
            "scope": (
                "integer single-process logical accounting; no remote-provider containment "
                "or distributed lock claim"
            ),
            "external_systems_called": False,
        }
    )


def _provider_quota_evidence() -> dict[str, object]:
    corpus = run_seeded_burst_corpus()
    if (
        corpus.logical_calls != 1_200
        or corpus.admitted_calls != corpus.settled_calls
        or len(corpus.digest) != 64
    ):
        raise RuntimeError("provider quota corpus lost replay or settlement completeness")
    return _sealed(
        {
            "measurement_kind": "deterministic-local-quota-stress",
            "claim_status": "local-declared-quota-model-only",
            "model_scope": MODEL_SCOPE,
            "aggregate_guard_scope": GLOBAL_GUARD_SCOPE,
            "seed": corpus.seed,
            "logical_calls": corpus.logical_calls,
            "admission_requests": corpus.admission_requests,
            "admitted_calls": corpus.admitted_calls,
            "refused_admissions": corpus.refused_admissions,
            "settled_calls": corpus.settled_calls,
            "reset_suppressed_retries": corpus.reset_suppressed_retries,
            "maximum_provider_active": corpus.maximum_provider_active,
            "maximum_global_active": corpus.maximum_global_active,
            "actual_tokens_settled": corpus.actual_tokens_settled,
            "event_count": corpus.event_count,
            "event_digest": corpus.digest,
            "independent_replay_passed": True,
            "scope": (
                "integer single-process declared RPM, TPM, concurrency, reset-window, "
                "retry, deadline, and settlement accounting"
            ),
            "external_systems_called": False,
        }
    )


def _replanning_evidence() -> dict[str, object]:
    graph = miami_eoc_graph()
    envelope = replace(miami_eoc_envelope(), max_context_bytes=29_500)
    replanner = EventDrivenReplanner()
    initial = replanner.initial_state(
        graph,
        envelope,
        run_id="judge-stormshift-replanning",
    )
    first_progress = RunProgressSnapshot.from_state(
        initial,
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
    first = replanner.replan(graph, initial, first_event, first_progress)

    second_progress = RunProgressSnapshot.from_state(
        first.state,
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
    second = replanner.replan(graph, first.state, second_event, second_progress)

    first_verified = replanner.verify_transition(
        graph,
        initial,
        first_event,
        first_progress,
        first,
    )
    second_verified = replanner.verify_transition(
        graph,
        first.state,
        second_event,
        second_progress,
        second,
    )
    if (
        not first_verified
        or not second_verified
        or first.decision.disposition is not ReplanDisposition.SCHEDULED
        or first.decision.reason.code is not ReplanReasonCode.OPTIONAL_WORK_SHED
        or first.decision.shed_task_ids != ("social_signal_scan",)
        or second.decision.disposition is not ReplanDisposition.REFUSED
        or second.decision.reason.code is not ReplanReasonCode.SCHEDULER_REFUSED
        or second.state.revision != 2
    ):
        raise RuntimeError("event-driven replanning witness violated its pinned expectations")
    first_residual = first.decision.residual_graph
    second_residual = second.decision.residual_graph
    assert first_residual is not None and second_residual is not None
    return _sealed(
        {
            "measurement_kind": "deterministic-modeled-replanning",
            "claim_status": "residual-graph-model-only",
            "event_count": 2,
            "completed_work_replayed": False,
            "sealed_effects_replayed": False,
            "first_transition": {
                "event_kind": first.decision.event_kind.value,
                "disposition": first.decision.disposition.value,
                "reason_code": first.decision.reason.code.value,
                "shed_task_ids": first.decision.shed_task_ids,
                "mandatory_residual_task_count": sum(
                    not task.optional for task in first_residual.tasks
                ),
                "decision_digest": first.decision.decision_digest,
                "state_digest": first.state.state_digest,
                "transition_verified": first_verified,
            },
            "second_transition": {
                "event_kind": second.decision.event_kind.value,
                "disposition": second.decision.disposition.value,
                "reason_code": second.decision.reason.code.value,
                "shed_task_ids": second.decision.shed_task_ids,
                "mandatory_residual_task_count": sum(
                    not task.optional for task in second_residual.tasks
                ),
                "decision_digest": second.decision.decision_digest,
                "state_digest": second.state.state_digest,
                "transition_verified": second_verified,
            },
            "final_revision": second.state.revision,
            "final_settled_usage": second.state.settled_usage,
            "final_elapsed_ms": second.state.elapsed_ms,
            "state_chain_verified": (
                first.state.prior_state_digest == initial.state_digest
                and second.state.prior_state_digest == first.state.state_digest
            ),
            "scope": first.decision.scope,
            "limitations": first.decision.limitations,
            "external_systems_called": False,
        }
    )


def _decision_explanation_evidence() -> dict[str, object]:
    graph = miami_eoc_graph()
    base = miami_eoc_envelope()
    cases = (
        ("feasible", base),
        ("optional-shed", replace(base, max_context_bytes=30_000)),
        (
            "refused-with-cancellation",
            replace(
                base,
                deadline_ms=6_200,
                max_parallelism=2,
                provider_limits=(("simulated-watsonx", 1), ("local-fixture", 4)),
            ),
        ),
    )
    evidence: list[dict[str, object]] = []
    record_total = 0
    for case_id, envelope in cases:
        result = Scheduler().schedule(graph, envelope)
        bundle = explain_schedule(graph, envelope, result)
        verified = bundle.verify() and bundle.verify_against(graph, envelope, result)
        if not verified or len(bundle.records) != len(result.events):
            raise RuntimeError(f"decision explanations failed coverage for {case_id!r}")
        if any(record.reasoning_access for record in bundle.records):
            raise RuntimeError("decision explanation claimed access to hidden reasoning")
        record_total += len(bundle.records)
        action_counts = Counter(record.action.value for record in bundle.records)
        evidence.append(
            {
                "case_id": case_id,
                "schedule_success": result.success,
                "source_event_count": len(result.events),
                "explanation_record_count": len(bundle.records),
                "one_record_per_event": True,
                "action_counts": dict(sorted(action_counts.items())),
                "bundle_id": bundle.bundle_id,
                "verified": verified,
                "terminal_record": bundle.records[-1].as_dict(),
            }
        )
    return _sealed(
        {
            "measurement_kind": "deterministic-post-hoc-public-fact-derivation",
            "claim_status": "structured-explanation-integrity-only",
            "derivation_scope": DERIVATION_SCOPE,
            "reasoning_access": False,
            "case_count": len(evidence),
            "record_count": record_total,
            "cases": evidence,
            "scope": (
                "public graph, envelope, schedule, and event fields with numeric rule facts"
            ),
            "limitations": (
                "does not expose chain-of-thought, intent, semantic causality, or model "
                "reasoning; hashes prove content integrity, not producer identity"
            ),
            "external_systems_called": False,
        }
    )


def _stormshift_report(
    *,
    case_id: str,
    scenario: StormShiftScenario,
    plan: ResponsePlan,
    transformation: str,
    fault_semantics: str,
) -> dict[str, object]:
    report = StormShiftValidator().validate(scenario, plan)
    if not report.verify_digest():
        raise RuntimeError(f"StormShift report digest failed for {case_id!r}")
    return _sealed(
        {
            "case_id": case_id,
            "measurement_kind": "deterministic-fictional-fixture",
            "claim_status": "structural-validation-only",
            "transformation": transformation,
            "fault_semantics": fault_semantics,
            "passed": report.passed,
            "digest_verified": True,
            "scenario_fault_markers": scenario.faults,
            "report": report,
            "failed_check_names": [
                check.name for check in report.checks if not check.passed
            ],
            "external_systems_called": False,
        }
    )


def _stormshift_evidence() -> dict[str, object]:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    cited = plan.citations[0]
    nominal = _stormshift_report(
        case_id="nominal-reference-plan",
        scenario=scenario,
        plan=plan,
        transformation="none-control",
        fault_semantics="not-injected-control",
    )
    adversarial = (
        _stormshift_report(
            case_id="stale-cited-artifact",
            scenario=fault_stale_artifact(scenario, cited),
            plan=plan,
            transformation=f"expire cited fixture record {cited}",
            fault_semantics="executed-fixture-transformation",
        ),
        _stormshift_report(
            case_id="fresh-evidence-contradiction",
            scenario=fault_contradiction(scenario, cited),
            plan=plan,
            transformation=f"add fresh contradictory fixture record for {cited}",
            fault_semantics="executed-fixture-transformation",
        ),
        _stormshift_report(
            case_id="shelter-capacity-loss",
            scenario=fault_capacity_loss(
                scenario,
                plan.allocations[0].shelter_id,
                1,
            ),
            plan=plan,
            transformation="remove one declared shelter space after plan construction",
            fault_semantics="executed-fixture-transformation",
        ),
        _stormshift_report(
            case_id="bilingual-numeric-drift",
            scenario=scenario,
            plan=replace(
                plan,
                alert=BilingualAlert(
                    plan.alert.english,
                    plan.alert.spanish.replace("180", "181"),
                ),
            ),
            transformation="change one Spanish numeric fact from 180 to 181",
            fault_semantics="executed-plan-transformation",
        ),
        _stormshift_report(
            case_id="external-publication-attempt",
            scenario=scenario,
            plan=replace(
                plan,
                publication_disposition=PublicationDisposition.EXTERNAL_PUBLICATION,
                external_publication_attempted=True,
                external_targets=("blocked-fictional-target",),
            ),
            transformation="mark a fictional external publication attempt",
            fault_semantics="executed-plan-transformation-no-adapter",
        ),
    )
    if not nominal["passed"] or any(case["passed"] for case in adversarial):
        raise RuntimeError("StormShift nominal/adversarial expectations were not met")
    return _sealed(
        {
            "measurement_kind": "deterministic-fictional-fixture",
            "claim_status": "structural-validation-only",
            "nominal": nominal,
            "adversarial": adversarial,
            "adversarial_case_count": len(adversarial),
        }
    )


def _canonical_experiment_jsonl(records: Iterable[ExperimentRecord]) -> bytes:
    ordered = sorted(records, key=lambda item: (item.seed, item.fault_id, item.policy))
    lines = [
        json.dumps(
            record.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        for record in ordered
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _experiment_evidence(
    revision: str,
) -> tuple[dict[str, object], tuple[ExperimentRecord, ...]]:
    records = run_registered_experiments(revision=revision)
    validate_complete_design(records)
    if len(records) != 450:
        raise RuntimeError(f"registered experiment emitted {len(records)} records, not 450")
    summary = summarize_experiments(records)
    raw_jsonl = _canonical_experiment_jsonl(records)
    payload = _sealed(
        {
            "measurement_kind": EXPERIMENT_MEASUREMENT_KIND,
            "claim_status": EXPERIMENT_CLAIM_STATUS,
            "revision": revision,
            "revision_provenance": EXPERIMENT_REVISION_PROVENANCE,
            "complete_design_validated": True,
            "raw_record_count": len(records),
            "raw_jsonl_sha256": hashlib.sha256(raw_jsonl).hexdigest(),
            "raw_records_content_digest": content_digest(
                [
                    record.as_dict()
                    for record in sorted(
                        records,
                        key=lambda item: (item.seed, item.fault_id, item.policy),
                    )
                ]
            ),
            "summary": summary,
            "external_systems_called": False,
        }
    )
    return payload, records


def _executor_evidence() -> dict[str, object]:
    # The import is deliberately lazy: the Bob/MCP module does not import this module,
    # and its optional MCP dependency is loaded only when a server is constructed.
    from .mcp_server import finite_executor_drill

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        raw = asyncio.run(finite_executor_drill())
    else:  # pragma: no cover - synchronous bundle API documents this boundary
        raise RuntimeError("build judge evidence from a synchronous context")

    effect = raw.get("effect_output")
    if not isinstance(effect, dict):
        raise RuntimeError("executor drill did not return an effect result")
    deterministic = {
        "schema_version": raw.get("schema_version"),
        "measurement_kind": raw.get("measurement_kind"),
        "claim_status": "local-fixture-execution-only",
        "external_effects_possible": raw.get("external_effects_possible"),
        "task_count": raw.get("task_count"),
        "resumed_task_count": raw.get("resumed_task_count"),
        "resumed_task_ids": raw.get("resumed_task_ids"),
        "first_run_state": raw.get("first_run_state"),
        "resumed_run_state": raw.get("resumed_run_state"),
        "effect": {
            "effect_state": effect.get("effect_state"),
            "executed_externally": effect.get("executed_externally"),
            "nondeterministic_local_intent_id_omitted": True,
        },
        "event_type_counts": raw.get("event_type_counts"),
        "actual_usage": raw.get("actual_usage"),
    }
    if (
        deterministic["external_effects_possible"] is not False
        or deterministic["first_run_state"] != "awaiting_effects"
        or deterministic["resumed_run_state"] != "awaiting_effects"
        or deterministic["resumed_task_count"] != deterministic["task_count"]
        or deterministic["effect"]["effect_state"] != "proposed"  # type: ignore[index]
        or deterministic["effect"]["executed_externally"] is not False  # type: ignore[index]
    ):
        raise RuntimeError("executor drill violated its local-only durable-resume contract")
    return _sealed(deterministic)


def _canonical_unicode_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def verify_console_artifact(path: Path) -> dict[str, object]:
    """Verify and summarize the checked-in console artifact without trusting its hash."""

    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        canonical_payload = artifact["canonical_payload"]
        declared_sha = artifact["sha256"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"invalid console artifact at {path}") from exc
    if not isinstance(canonical_payload, str) or not isinstance(declared_sha, str):
        raise ValueError("console artifact digest fields have invalid types")
    computed_sha = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    try:
        payload = json.loads(canonical_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("console canonical_payload is not valid JSON") from exc
    canonical_verified = _canonical_unicode_json(payload) == canonical_payload
    sha_verified = computed_sha == declared_sha
    if not sha_verified or not canonical_verified:
        raise ValueError("console artifact SHA or canonical-payload verification failed")
    if (
        payload.get("measurement_kind") != "deterministic-simulation"
        or payload.get("claim_status") != "descriptive-only"
        or payload.get("external_systems_called") is not False
    ):
        raise ValueError("console artifact lost its deterministic local-only labels")
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict) or not all(
        isinstance(values, dict) for values in decisions.values()
    ):
        raise ValueError("console artifact decisions are missing")
    decision_count = sum(len(values) for values in decisions.values())
    return _sealed(
        {
            "schema_version": artifact.get("schema_version"),
            "measurement_kind": payload["measurement_kind"],
            "claim_status": payload["claim_status"],
            "artifact_sha256": declared_sha,
            "sha256_verified": sha_verified,
            "canonical_payload_verified": canonical_verified,
            "payload_schema_version": payload.get("schema_version"),
            "decision_count": decision_count,
            "fictional_fixture": payload.get("fictional_fixture"),
            "external_systems_called": False,
        }
    )


def verify_judge_envelope(envelope: Mapping[str, object]) -> bool:
    """Return whether the outer content address matches the normalized content."""

    content = envelope.get("content")
    return (
        envelope.get("schema_version") == ENVELOPE_SCHEMA_VERSION
        and envelope.get("digest_algorithm") == "sha256-canonical-json"
        and isinstance(content, dict)
        and content.get("schema_version") == BUNDLE_SCHEMA_VERSION
        and envelope.get("canonical_content") == canonical_json(content)
        and envelope.get("content_digest") == content_digest(content)
    )


@dataclass(frozen=True, slots=True)
class JudgeEvidenceBundle:
    """A sealed judge envelope plus its optional complete raw experiment records."""

    envelope: dict[str, object]
    experiment_records: tuple[ExperimentRecord, ...]

    @property
    def content_digest(self) -> str:
        value = self.envelope["content_digest"]
        assert isinstance(value, str)
        return value

    def verify(self) -> bool:
        return verify_judge_envelope(self.envelope)

    def write(
        self,
        output_path: Path,
        *,
        raw_experiments_path: Path | None = None,
    ) -> None:
        """Write byte-repeatable JSON and, optionally, the complete canonical JSONL."""

        if not self.verify():
            raise ValueError("judge evidence envelope failed content-digest verification")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(normalize(self.envelope), indent=2, sort_keys=True, ensure_ascii=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if raw_experiments_path is None:
            return
        raw_experiments_path.parent.mkdir(parents=True, exist_ok=True)
        write_experiment_jsonl(raw_experiments_path, self.experiment_records)
        content = self.envelope["content"]
        assert isinstance(content, dict)
        experiment = content["fault_experiments"]
        assert isinstance(experiment, dict)
        expected = experiment["raw_jsonl_sha256"]
        observed = hashlib.sha256(raw_experiments_path.read_bytes()).hexdigest()
        if observed != expected:
            raise RuntimeError("written experiment JSONL did not match its bundle digest")


def build_judge_evidence(
    *,
    revision: str | None = None,
    project_root: Path | None = None,
    console_artifact_path: Path | None = None,
    provenance_excluded_paths: Iterable[Path] = (),
) -> JudgeEvidenceBundle:
    """Build the complete offline evidence bundle with no time- or network-derived fields."""

    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    source_revision = resolve_source_revision(
        revision,
        project_root=root,
        excluded_status_paths=provenance_excluded_paths,
    )
    revision_value = source_revision["revision"]
    assert isinstance(revision_value, str)
    experiments, records = _experiment_evidence(revision_value)
    console_path = console_artifact_path or root / DEFAULT_CONSOLE_ARTIFACT
    console = verify_console_artifact(console_path)

    content = normalize(
        {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "artifact_kind": "offline-judge-evidence",
            "labels": {
                "overall_claim_status": "descriptive-only",
                "measurement_kinds": [
                    "deterministic-compiler-check",
                    "deterministic-local-ledger-stress",
                    "deterministic-local-quota-stress",
                    "deterministic-modeled-replanning",
                    "deterministic-post-hoc-public-fact-derivation",
                    "deterministic-simulation",
                    "deterministic-fictional-fixture",
                    "deterministic-local-fixture-execution",
                    "content-integrity-verification",
                ],
                "external_systems_called": False,
                "live_provider_or_model_calls": False,
                "external_effects_possible": False,
                "superiority_claimed": False,
                "real_emergency_data": False,
            },
            "provenance": _environment_provenance(source_revision),
            "workflow_ir_equivalence": _workflow_ir_evidence(),
            "resource_ledger_stress": _resource_ledger_evidence(),
            "provider_quota_stress": _provider_quota_evidence(),
            "event_driven_replanning": _replanning_evidence(),
            "decision_explanations": _decision_explanation_evidence(),
            "preflight_and_conservation": _preflight_evidence(),
            "stormshift_structural_validation": _stormshift_evidence(),
            "durable_executor_drill": _executor_evidence(),
            "fault_experiments": experiments,
            "console_artifact_verification": console,
            "limitations": [
                "Every result is local deterministic simulation or fixture execution; no live IBM Granite, watsonx, emergency, or provider call is measured.",
                "Workflow schema v1 excludes alternatives, speculative branches, and typed artifact ports; those fields fail closed instead of being approximated.",
                "The 10,000-transition ledger proves a deterministic local integer-accounting model, not remote-provider containment or distributed locking.",
                "Provider quota evidence is a single-process declared RPM/TPM/concurrency/reset model; it is not live provider telemetry, a distributed lease, or an adapter-enforced cap.",
                "Event-driven replanning operates on caller-supplied durable progress and modeled residual graphs; it does not pause, cancel, or mutate a live executor or provider call.",
                "Decision explanations derive post-hoc numeric facts from public scheduler inputs and events; they expose no chain-of-thought, intent, or semantic causality.",
                "Preflight refusal is conservative under pinned profiles and is not a general proof of mathematical infeasibility.",
                "Executor workers are trusted in-process fixture callables; this is not a distributed lease or sandbox demonstration.",
                "The effect drill stops at a durable proposed intent and performs no external delivery.",
                "StormShift checks structural fields, arithmetic, citations, declared accessibility, bilingual numeric parity, and publication state; they do not prove semantics, rendering, entailment, or external state.",
                "Fault experiments are descriptive paired deterministic simulations; development-reference policies are not tuned third-party framework comparisons.",
                "A SHA-256 match proves artifact byte integrity, not the truth of the modeled assumptions.",
                "Caller-supplied revisions remain unverified; a locally read Git HEAD has no remote attestation and may have a separately reported dirty worktree.",
            ],
        }
    )
    assert isinstance(content, dict)
    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "digest_algorithm": "sha256-canonical-json",
        "content_digest": content_digest(content),
        "canonical_content": canonical_json(content),
        "content": content,
    }
    if not verify_judge_envelope(envelope):
        raise RuntimeError("new judge evidence envelope failed self-verification")
    return JudgeEvidenceBundle(envelope=envelope, experiment_records=records)


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "DEFAULT_CONSOLE_ARTIFACT",
    "ENVELOPE_SCHEMA_VERSION",
    "JudgeEvidenceBundle",
    "build_judge_evidence",
    "resolve_source_revision",
    "verify_console_artifact",
    "verify_judge_envelope",
]
