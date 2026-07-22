from __future__ import annotations

import base64
import copy

from agent_physics.artifacts import Artifact, Claim, ClaimStatus, Sensitivity
from agent_physics.whole_run_verifier import (
    GENESIS_EVENT_DIGEST,
    canonical_evidence_digest,
    seal_evidence_record,
    seal_whole_run_evidence,
    verify_whole_run_evidence,
)


ZERO = {"tokens": 0, "cost_microusd": 0, "context_bytes": 0}


def _resources(
    *,
    reserved: tuple[int, int, int] = (0, 0, 0),
    actual: tuple[int, int, int] = (0, 0, 0),
    released: tuple[int, int, int] = (0, 0, 0),
) -> dict[str, object]:
    def vector(values: tuple[int, int, int]) -> dict[str, int]:
        return dict(zip(("tokens", "cost_microusd", "context_bytes"), values, strict=True))

    return {
        "reserved": vector(reserved),
        "actual": vector(actual),
        "released": vector(released),
    }


def _artifact_record(artifact: Artifact, producer_event_digest: str) -> dict[str, object]:
    return seal_evidence_record(
        {
            "artifact_id": artifact.artifact_id,
            "schema": artifact.schema,
            "schema_version": artifact.schema_version,
            "media_type": artifact.media_type,
            "producer": artifact.producer,
            "parents": list(artifact.parents),
            "sensitivity": artifact.sensitivity.value,
            "created_at_ms": artifact.created_at_ms,
            "fresh_until_ms": artifact.fresh_until_ms,
            "payload_base64": base64.b64encode(artifact.payload).decode("ascii"),
            "payload_sha256": artifact.payload_sha256,
            "producer_event_digest": producer_event_digest,
        }
    )


def _claim_record(claim: Claim, produced_by_event_digest: str) -> dict[str, object]:
    return seal_evidence_record(
        {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "evidence_refs": list(claim.evidence_refs),
            "status": claim.status.value,
            "contradicts": list(claim.contradicts),
            "producer": claim.producer,
            "created_at_ms": claim.created_at_ms,
            "produced_by_event_digest": produced_by_event_digest,
            "claim_digest": claim.claim_digest,
        }
    )


def _valid_evidence() -> dict[str, object]:
    input_artifact = Artifact.create(
        b'{"incident":"storm"}',
        schema="stormshift.input",
        schema_version="1",
        media_type="application/json",
        producer="fixture",
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=0,
    )
    result_artifact = Artifact.create(
        b'{"route":"shelter-7"}',
        schema="stormshift.route",
        schema_version="1",
        media_type="application/json",
        producer="worker",
        parents=(input_artifact.artifact_id,),
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=3,
    )
    receipt_artifact = Artifact.create(
        b'{"delivery":"simulated"}',
        schema="stormshift.effect-receipt",
        schema_version="1",
        media_type="application/json",
        producer="effect-kernel",
        parents=(result_artifact.artifact_id,),
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=8,
    )

    run_id = "run-v5-001"
    events: list[dict[str, object]] = []

    def event(
        event_type: str,
        occurred_at_ms: int,
        *,
        task_id: str | None = None,
        attempt: int | None = None,
        causes: tuple[str, ...] = (),
        resources: dict[str, object] | None = None,
        outputs: tuple[str, ...] = (),
        evidence: tuple[str, ...] = (),
        approval_id: str | None = None,
        effect_id: str | None = None,
    ) -> dict[str, object]:
        sequence = len(events) + 1
        record = seal_evidence_record(
            {
                "run_id": run_id,
                "sequence": sequence,
                "event_id": f"{run_id}:event:{sequence}",
                "event_type": event_type,
                "task_id": task_id,
                "attempt": attempt,
                "occurred_at_ms": occurred_at_ms,
                "previous_event_digest": (
                    str(events[-1]["event_digest"]) if events else GENESIS_EVENT_DIGEST
                ),
                "causes": sorted(causes),
                "resources": resources or _resources(),
                "output_artifact_refs": sorted(outputs),
                "evidence_refs": sorted(evidence),
                "approval_id": approval_id,
                "effect_id": effect_id,
            },
            digest_field="event_digest",
        )
        events.append(record)
        return record

    started = event("run.started", 0, outputs=(input_artifact.artifact_id,))
    route_started = event(
        "task.attempt_started",
        1,
        task_id="route",
        attempt=1,
        causes=(str(started["event_digest"]),),
        resources=_resources(reserved=(10, 100, 200)),
        evidence=(input_artifact.artifact_id,),
    )
    context_validated = event(
        "context.validated",
        2,
        task_id="route",
        attempt=1,
        causes=(str(route_started["event_digest"]),),
        evidence=(input_artifact.artifact_id,),
    )
    route_completed = event(
        "task.completed",
        3,
        task_id="route",
        attempt=1,
        causes=(
            str(route_started["event_digest"]),
            str(context_validated["event_digest"]),
        ),
        resources=_resources(actual=(7, 80, 150), released=(3, 20, 50)),
        outputs=(result_artifact.artifact_id,),
        evidence=(input_artifact.artifact_id,),
    )
    alert_started = event(
        "task.attempt_started",
        4,
        task_id="alert",
        attempt=1,
        causes=(str(route_completed["event_digest"]),),
        resources=_resources(reserved=(2, 20, 10)),
        evidence=(result_artifact.artifact_id,),
    )
    proposed = event(
        "effect.proposed",
        5,
        task_id="alert",
        causes=(str(alert_started["event_digest"]),),
        evidence=(result_artifact.artifact_id,),
        effect_id="effect-alert-1",
    )
    granted = event(
        "approval.granted",
        6,
        task_id="alert",
        causes=(str(proposed["event_digest"]),),
        approval_id="approval-alert-1",
        effect_id="effect-alert-1",
    )
    committed = event(
        "effect.committed",
        7,
        task_id="alert",
        causes=(str(proposed["event_digest"]), str(granted["event_digest"])),
        approval_id="approval-alert-1",
        effect_id="effect-alert-1",
    )
    alert_completed = event(
        "task.completed",
        8,
        task_id="alert",
        attempt=1,
        causes=(str(alert_started["event_digest"]), str(committed["event_digest"])),
        resources=_resources(actual=(2, 20, 10)),
        outputs=(receipt_artifact.artifact_id,),
        evidence=(result_artifact.artifact_id,),
    )
    terminal = event(
        "run.completed",
        9,
        causes=(str(alert_completed["event_digest"]),),
    )

    artifact_records = sorted(
        (
            _artifact_record(input_artifact, str(started["event_digest"])),
            _artifact_record(result_artifact, str(route_completed["event_digest"])),
            _artifact_record(receipt_artifact, str(alert_completed["event_digest"])),
        ),
        key=lambda item: str(item["artifact_id"]),
    )
    claim = Claim.create(
        "alert-delivery",
        "The simulated emergency alert effect committed once.",
        evidence_refs=(receipt_artifact.artifact_id,),
        status=ClaimStatus.SUPPORTED,
        producer="effect-kernel",
        created_at_ms=8,
    )
    claim_records = [_claim_record(claim, str(alert_completed["event_digest"]))]
    context_records = [
        seal_evidence_record(
            {
                "obligation_id": "route-grounding",
                "run_id": run_id,
                "task_id": "route",
                "requirement_digest": canonical_evidence_digest("fresh incident input"),
                "evidence_refs": [input_artifact.artifact_id],
                "validation_event_digest": context_validated["event_digest"],
                "satisfied": True,
            }
        )
    ]
    effect_unsigned = {
        "effect_id": "effect-alert-1",
        "run_id": run_id,
        "task_id": "alert",
        "effect_class": "irreversible_write",
        "action": "publish_alert",
        "resource": "simulated://emergency-alerts",
        "idempotency_key": "alert:storm:001",
        "payload_artifact_ref": result_artifact.artifact_id,
        "proposed_event_digest": proposed["event_digest"],
        "approval_id": "approval-alert-1",
        "terminal_state": "committed",
        "commit_event_digest": committed["event_digest"],
        "terminal_event_digest": committed["event_digest"],
    }
    effect_record = seal_evidence_record(effect_unsigned)
    scope = {
        key: effect_unsigned[key]
        for key in (
            "run_id",
            "effect_id",
            "task_id",
            "effect_class",
            "action",
            "resource",
            "idempotency_key",
            "payload_artifact_ref",
        )
    }
    approval_records = [
        seal_evidence_record(
            {
                "approval_id": "approval-alert-1",
                "run_id": run_id,
                "effect_id": "effect-alert-1",
                "principal": "incident-commander@example.test",
                "scope_digest": canonical_evidence_digest(scope),
                "grant_event_digest": granted["event_digest"],
            }
        )
    ]
    effect_records = [effect_record]

    envelope_unsigned = {
        "run_id": run_id,
        "deadline_ms": 20,
        "resource_caps": {"tokens": 20, "cost_microusd": 200, "context_bytes": 500},
        "policy_digest": canonical_evidence_digest("policy-v5"),
    }
    envelope_digest = canonical_evidence_digest(envelope_unsigned)
    envelope = {**envelope_unsigned, "envelope_digest": envelope_digest}
    graph_digest = canonical_evidence_digest("stormshift-graph-v5")
    replay_unsigned = {
        "run_id": run_id,
        "graph_digest": graph_digest,
        "envelope_digest": envelope_digest,
        "terminal_event_digest": terminal["event_digest"],
        "event_count": len(events),
        "event_chain_digest": canonical_evidence_digest([item["event_digest"] for item in events]),
        "resource_totals": {"tokens": 9, "cost_microusd": 100, "context_bytes": 160},
        "output_set_digest": canonical_evidence_digest(
            sorted(
                (
                    input_artifact.artifact_id,
                    result_artifact.artifact_id,
                    receipt_artifact.artifact_id,
                )
            )
        ),
        "artifact_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in artifact_records]
        ),
        "claim_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in claim_records]
        ),
        "context_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in context_records]
        ),
        "approval_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in approval_records]
        ),
        "effect_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in effect_records]
        ),
    }
    replay = seal_evidence_record(replay_unsigned, digest_field="witness_digest")
    content = {
        "identity": {
            "run_id": run_id,
            "graph_digest": graph_digest,
            "manifest_digest": canonical_evidence_digest("manifest-v5"),
            "envelope_digest": envelope_digest,
        },
        "envelope": envelope,
        "events": events,
        "artifacts": artifact_records,
        "claims": claim_records,
        "context_obligations": context_records,
        "approvals": approval_records,
        "effects": effect_records,
        "replay_witness": replay,
    }
    return seal_whole_run_evidence(content)


def _reseal_outer(evidence: dict[str, object]) -> None:
    evidence["content_digest"] = canonical_evidence_digest(evidence["content"])


def _content(evidence: dict[str, object]) -> dict[str, object]:
    value = evidence["content"]
    assert isinstance(value, dict)
    return value


def test_valid_sealed_whole_run_passes_independent_verification() -> None:
    evidence = _valid_evidence()

    report = verify_whole_run_evidence(evidence)

    assert report.passed
    assert report.run_id == "run-v5-001"
    assert report.evidence_digest == evidence["content_digest"]
    assert report.violations == ()


def test_mutated_resource_fails_outer_seal_and_conservation() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    resources = events[3]["resources"]
    resources["actual"]["tokens"] = 8
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert not report.passed
    assert "event_digest_mismatch" in report.violation_codes
    assert "resource_conservation_violation" in report.violation_codes


def test_any_unresealed_content_mutation_breaks_outer_digest() -> None:
    evidence = _valid_evidence()
    identity = _content(evidence)["identity"]
    assert isinstance(identity, dict)
    identity["run_id"] = "mutated-run"

    report = verify_whole_run_evidence(evidence)

    assert "content_digest_mismatch" in report.violation_codes


def test_unknown_field_fails_even_when_outer_envelope_is_resealed() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    events[0]["scheduler_opinion"] = "trust me"
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert not report.passed
    assert report.violation_codes == ("unknown_fields",)


def test_event_chain_break_is_detected() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    events[4]["previous_event_digest"] = GENESIS_EVENT_DIGEST
    unsigned = {key: value for key, value in events[4].items() if key != "event_digest"}
    events[4]["event_digest"] = canonical_evidence_digest(unsigned)
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "event_chain_break" in report.violation_codes


def test_artifact_payload_mutation_breaks_both_record_and_content_address() -> None:
    evidence = _valid_evidence()
    artifacts = _content(evidence)["artifacts"]
    assert isinstance(artifacts, list)
    artifacts[0]["payload_base64"] = base64.b64encode(b"tampered").decode("ascii")
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "artifact_record_digest_mismatch" in report.violation_codes
    assert "artifact_integrity_failure" in report.violation_codes


def test_missing_event_evidence_link_fails_closed() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    events[1]["evidence_refs"] = ["sha256:" + "f" * 64]
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "missing_evidence_artifact" in report.violation_codes


def test_duplicate_approval_identity_is_rejected() -> None:
    evidence = _valid_evidence()
    approvals = _content(evidence)["approvals"]
    assert isinstance(approvals, list)
    approvals.append(copy.deepcopy(approvals[0]))
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "noncanonical_approval_set" in report.violation_codes


def test_duplicate_effect_idempotency_key_is_rejected() -> None:
    evidence = _valid_evidence()
    effects = _content(evidence)["effects"]
    assert isinstance(effects, list)
    duplicate = copy.deepcopy(effects[0])
    duplicate["effect_id"] = "effect-alert-2"
    unsigned = {key: value for key, value in duplicate.items() if key != "record_digest"}
    duplicate["record_digest"] = canonical_evidence_digest(unsigned)
    effects.append(duplicate)
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "duplicate_effect_idempotency_key" in report.violation_codes


def test_irreversible_commit_without_approval_is_rejected() -> None:
    evidence = _valid_evidence()
    content = _content(evidence)
    effects = content["effects"]
    assert isinstance(effects, list)
    effects[0]["approval_id"] = None
    content["approvals"] = []
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "irreversible_effect_without_approval" in report.violation_codes


def test_replay_witness_must_bind_recomputed_resource_totals() -> None:
    evidence = _valid_evidence()
    replay = _content(evidence)["replay_witness"]
    assert isinstance(replay, dict)
    replay["resource_totals"]["tokens"] = 10
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "replay_witness_binding_mismatch" in report.violation_codes
    assert "replay_witness_digest_mismatch" in report.violation_codes


def test_successful_run_cannot_hide_unsatisfied_context_obligation() -> None:
    evidence = _valid_evidence()
    obligations = _content(evidence)["context_obligations"]
    assert isinstance(obligations, list)
    obligations[0]["satisfied"] = False
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "context_obligation_unsatisfied" in report.violation_codes
    assert "context_completion_causality" in report.violation_codes


def test_run_identity_substitution_is_rejected() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    events[2]["run_id"] = "another-run"
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "event_run_identity_mismatch" in report.violation_codes


def test_noncanonical_cause_order_is_rejected() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    assert len(events[7]["causes"]) == 2
    events[7]["causes"].reverse()
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert "noncanonical_reference_order" in report.violation_codes


def test_boolean_cannot_smuggle_itself_as_an_integer() -> None:
    evidence = _valid_evidence()
    events = _content(evidence)["events"]
    assert isinstance(events, list)
    events[0]["sequence"] = True
    _reseal_outer(evidence)

    report = verify_whole_run_evidence(evidence)

    assert report.violation_codes == ("invalid_type",)
