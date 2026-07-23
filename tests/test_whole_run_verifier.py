from __future__ import annotations

import base64
import copy

import pytest

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


def _reseal_record(record: dict[str, object], digest_field: str = "record_digest") -> None:
    record.pop(digest_field, None)
    record[digest_field] = canonical_evidence_digest(record)


def _records(evidence: dict[str, object], field: str) -> list[dict[str, object]]:
    value = _content(evidence)[field]
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value  # type: ignore[return-value]


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


def test_public_sealing_helpers_reject_noncanonical_or_already_sealed_input() -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        canonical_evidence_digest({"not_finite": float("nan")})
    with pytest.raises(ValueError, match="already contains"):
        seal_evidence_record({"record_digest": "0" * 64})


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("not_object", "invalid_type"),
        ("non_string_key", "invalid_type"),
        ("unsupported_schema", "unsupported_schema"),
        ("unsupported_digest", "unsupported_digest_algorithm"),
        ("events_not_array", "invalid_type"),
        ("empty_events", "missing_events"),
        ("empty_run_id", "invalid_type"),
        ("unknown_event", "unknown_event_type"),
        ("bad_optional_string", "invalid_type"),
        ("bad_optional_integer", "invalid_type"),
        ("bad_address", "invalid_digest"),
        ("bad_sensitivity", "invalid_sensitivity"),
        ("bad_claim_status", "invalid_claim_status"),
        ("bad_boolean", "invalid_type"),
        ("bad_effect_class", "invalid_effect_class"),
        ("bad_effect_state", "invalid_effect_state"),
        ("bad_nullable_digest", "invalid_digest"),
    ),
)
def test_untrusted_whole_run_schema_is_rejected_without_execution(
    case: str,
    expected_code: str,
) -> None:
    if case == "not_object":
        sealed: object = []
    else:
        evidence = _valid_evidence()
        sealed = evidence
        content = _content(evidence)
        events = _records(evidence, "events")
        if case == "non_string_key":
            evidence[1] = "hostile"  # type: ignore[index]
        elif case == "unsupported_schema":
            evidence["schema_version"] = "finite-whole-run-evidence/v999"
        elif case == "unsupported_digest":
            evidence["digest_algorithm"] = "sha1"
        elif case == "events_not_array":
            content["events"] = {}
        elif case == "empty_events":
            content["events"] = []
        elif case == "empty_run_id":
            identity = content["identity"]
            assert isinstance(identity, dict)
            identity["run_id"] = ""
        elif case == "unknown_event":
            events[0]["event_type"] = "run.teleported"
        elif case == "bad_optional_string":
            events[0]["approval_id"] = 7
        elif case == "bad_optional_integer":
            events[1]["attempt"] = 0
        elif case == "bad_address":
            events[0]["output_artifact_refs"] = ["0" * 64]
        elif case == "bad_sensitivity":
            _records(evidence, "artifacts")[0]["sensitivity"] = "top-secret"
        elif case == "bad_claim_status":
            _records(evidence, "claims")[0]["status"] = "guessed"
        elif case == "bad_boolean":
            _records(evidence, "context_obligations")[0]["satisfied"] = 1
        elif case == "bad_effect_class":
            _records(evidence, "effects")[0]["effect_class"] = "pure"
        elif case == "bad_effect_state":
            _records(evidence, "effects")[0]["terminal_state"] = "lost"
        elif case == "bad_nullable_digest":
            _records(evidence, "effects")[0]["commit_event_digest"] = "not-a-digest"
        else:
            raise AssertionError(case)

    report = verify_whole_run_evidence(sealed)  # type: ignore[arg-type]
    assert not report.passed
    assert report.run_id is None
    assert report.violation_codes == (expected_code,)


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("deadline", "run_deadline_exceeded"),
        ("envelope_digest", "envelope_digest_mismatch"),
        ("duplicate_event_id", "duplicate_event_id"),
        ("duplicate_event_digest", "duplicate_event_digest"),
        ("event_sequence", "non_monotonic_event_sequence"),
        ("event_time", "non_monotonic_event_time"),
        ("genesis_causes", "invalid_genesis_causes"),
        ("missing_causes", "missing_causal_predecessor"),
        ("future_cause", "invalid_event_cause"),
        ("run_identity_shape", "invalid_event_identity_shape"),
        ("ingest_without_artifact", "missing_ingested_artifact"),
        ("ingest_identity_shape", "invalid_event_identity_shape"),
        ("task_identity_shape", "invalid_event_identity_shape"),
        ("terminal_task_identity_shape", "invalid_event_identity_shape"),
        ("approval_identity_shape", "invalid_event_identity_shape"),
        ("effect_identity_shape", "invalid_event_identity_shape"),
        ("illegal_output", "illegal_output_event"),
        ("noncanonical_output_refs", "noncanonical_reference_order"),
        ("duplicate_output", "duplicate_artifact_output"),
        ("duplicate_attempt", "duplicate_attempt_start"),
        ("task_causality", "invalid_task_causality"),
        ("missing_task_output", "missing_task_output"),
        ("duplicate_task_completion", "duplicate_task_completion"),
        ("invalid_lifecycle", "invalid_run_lifecycle"),
        ("resource_cap", "resource_cap_exceeded"),
        ("resource_leak", "resource_leak"),
        ("artifact_order", "noncanonical_artifact_set"),
        ("artifact_payload", "invalid_artifact_payload"),
        ("artifact_parents_order", "noncanonical_reference_order"),
        ("artifact_producer", "artifact_producer_mismatch"),
        ("artifact_time", "artifact_time_causality"),
        ("artifact_parent_missing", "missing_artifact_parent"),
        ("artifact_lineage", "artifact_lineage_causality"),
        ("output_artifact_missing", "missing_output_artifact"),
        ("event_evidence_missing", "missing_evidence_artifact"),
        ("event_evidence_future", "evidence_causality_violation"),
        ("event_evidence_stale", "stale_or_future_evidence"),
        ("claim_order", "noncanonical_claim_set"),
        ("claim_integrity", "claim_integrity_failure"),
        ("claim_record_seal", "claim_record_digest_mismatch"),
        ("claim_reference_order", "noncanonical_reference_order"),
        ("claim_unsupported", "unsupported_claim"),
        ("claim_producer_missing", "missing_claim_producer"),
        ("claim_time", "claim_time_causality"),
        ("claim_evidence_missing", "missing_claim_evidence"),
        ("claim_evidence_future", "claim_causality_violation"),
        ("claim_evidence_stale", "stale_or_future_claim_evidence"),
        ("claim_conflict_missing", "missing_conflicting_claim"),
        ("context_order", "noncanonical_context_set"),
        ("context_duplicate_validation", "duplicate_context_validation"),
        ("context_run", "context_run_identity_mismatch"),
        ("context_validation", "context_validation_mismatch"),
        ("context_evidence_empty", "context_evidence_missing"),
        ("context_reference_order", "noncanonical_reference_order"),
        ("context_completion", "context_completion_causality"),
        ("context_evidence_missing", "missing_context_evidence"),
        ("approval_unregistered", "unregistered_approval_event"),
        ("approval_run", "approval_run_identity_mismatch"),
        ("approval_record_seal", "approval_record_digest_mismatch"),
        ("approval_event", "approval_event_mismatch"),
        ("approval_uniqueness", "approval_event_uniqueness"),
        ("effect_unregistered", "unregistered_effect_event"),
        ("effect_order", "noncanonical_effect_set"),
        ("effect_payload", "missing_effect_payload"),
        ("effect_proposal", "effect_proposal_mismatch"),
        ("effect_payload_future", "effect_payload_causality"),
        ("effect_proposal_approval", "effect_proposal_mismatch"),
        ("effect_proposal_uniqueness", "effect_proposal_uniqueness"),
        ("effect_commit_uniqueness", "effect_commit_uniqueness"),
        ("effect_approval", "effect_approval_mismatch"),
        ("effect_scope", "approval_scope_mismatch"),
        ("effect_approval_causality", "approval_causality"),
        ("effect_proposed_state", "effect_state_mismatch"),
        ("effect_aborted_binding", "effect_commit_binding"),
        ("effect_compensated_binding", "effect_commit_binding"),
        ("effect_committed_terminal_binding", "effect_commit_binding"),
        ("effect_abort_causality", "effect_terminal_causality"),
        ("effect_compensation_causality", "effect_terminal_causality"),
        ("effect_commit_approval", "effect_commit_binding"),
        ("effect_commit_causality", "effect_commit_causality"),
        ("orphan_approval", "orphan_or_reused_approval"),
        ("replay_envelope", "replay_identity_mismatch"),
    ),
)
def test_resealed_semantic_mutations_fail_closed_at_the_independent_verifier(
    case: str,
    expected_code: str,
) -> None:
    evidence = _valid_evidence()
    content = _content(evidence)
    events = _records(evidence, "events")
    artifacts = _records(evidence, "artifacts")
    claims = _records(evidence, "claims")
    contexts = _records(evidence, "context_obligations")
    approvals = _records(evidence, "approvals")
    effects = _records(evidence, "effects")
    missing_digest = "0" * 64
    missing_address = f"sha256:{missing_digest}"

    def reseal_event(index: int) -> None:
        _reseal_record(events[index], "event_digest")

    def artifact_with_schema(schema: str) -> dict[str, object]:
        return next(record for record in artifacts if record["schema"] == schema)

    if case == "deadline":
        envelope = content["envelope"]
        assert isinstance(envelope, dict)
        envelope["deadline_ms"] = 8
        envelope["envelope_digest"] = canonical_evidence_digest(
            {key: value for key, value in envelope.items() if key != "envelope_digest"}
        )
    elif case == "envelope_digest":
        envelope = content["envelope"]
        assert isinstance(envelope, dict)
        envelope["deadline_ms"] = 19
    elif case == "duplicate_event_id":
        events[1]["event_id"] = events[0]["event_id"]
        reseal_event(1)
    elif case == "duplicate_event_digest":
        events[1]["event_digest"] = events[0]["event_digest"]
    elif case == "event_sequence":
        events[1]["sequence"] = 1
        reseal_event(1)
    elif case == "event_time":
        events[2]["occurred_at_ms"] = 0
        reseal_event(2)
    elif case == "genesis_causes":
        events[0]["causes"] = [events[1]["event_digest"]]
        reseal_event(0)
    elif case == "missing_causes":
        events[1]["causes"] = []
        reseal_event(1)
    elif case == "future_cause":
        events[1]["causes"] = [missing_digest]
        reseal_event(1)
    elif case == "run_identity_shape":
        events[0]["task_id"] = "route"
        reseal_event(0)
    elif case == "ingest_without_artifact":
        events[2].update(
            {
                "event_type": "artifact.ingested",
                "task_id": None,
                "attempt": None,
                "output_artifact_refs": [],
            }
        )
        reseal_event(2)
    elif case == "ingest_identity_shape":
        events[2]["event_type"] = "artifact.ingested"
        reseal_event(2)
    elif case == "task_identity_shape":
        events[1]["attempt"] = None
        reseal_event(1)
    elif case == "terminal_task_identity_shape":
        events[3]["attempt"] = None
        reseal_event(3)
    elif case == "approval_identity_shape":
        events[6]["approval_id"] = None
        reseal_event(6)
    elif case == "effect_identity_shape":
        events[5]["effect_id"] = None
        reseal_event(5)
    elif case == "illegal_output":
        events[5]["output_artifact_refs"] = [events[3]["output_artifact_refs"][0]]
        reseal_event(5)
    elif case == "noncanonical_output_refs":
        output_id = events[3]["output_artifact_refs"][0]
        events[3]["output_artifact_refs"] = [output_id, output_id]
        reseal_event(3)
    elif case == "duplicate_output":
        events[3]["output_artifact_refs"].append(events[0]["output_artifact_refs"][0])
        events[3]["output_artifact_refs"].sort()
        reseal_event(3)
    elif case == "duplicate_attempt":
        events[2]["event_type"] = "task.attempt_started"
        reseal_event(2)
    elif case == "task_causality":
        events[3]["causes"] = [events[2]["event_digest"]]
        reseal_event(3)
    elif case == "missing_task_output":
        events[3]["output_artifact_refs"] = []
        reseal_event(3)
    elif case == "duplicate_task_completion":
        events[8]["task_id"] = "route"
        reseal_event(8)
    elif case == "invalid_lifecycle":
        events[9]["event_type"] = "run.started"
        reseal_event(9)
    elif case == "resource_cap":
        events[1]["resources"]["reserved"]["tokens"] = 21
        reseal_event(1)
    elif case == "resource_leak":
        events[9]["resources"]["reserved"]["tokens"] = 1
        reseal_event(9)
    elif case == "artifact_order":
        artifacts.reverse()
    elif case == "artifact_payload":
        artifacts[0]["payload_base64"] = "!"
        _reseal_record(artifacts[0])
    elif case == "artifact_parents_order":
        child = next(record for record in artifacts if record["parents"])
        child["parents"] = [child["parents"][0], child["parents"][0]]
        _reseal_record(child)
    elif case == "artifact_producer":
        artifacts[0]["producer_event_digest"] = missing_digest
        _reseal_record(artifacts[0])
    elif case == "artifact_time":
        result = artifact_with_schema("stormshift.route")
        result["created_at_ms"] = 2
        _reseal_record(result)
    elif case == "artifact_parent_missing":
        result = artifact_with_schema("stormshift.route")
        result["parents"] = [missing_address]
        _reseal_record(result)
    elif case == "artifact_lineage":
        result = artifact_with_schema("stormshift.route")
        receipt = artifact_with_schema("stormshift.effect-receipt")
        result["parents"] = [receipt["artifact_id"]]
        _reseal_record(result)
    elif case == "output_artifact_missing":
        output_id = events[0]["output_artifact_refs"][0]
        artifacts[:] = [record for record in artifacts if record["artifact_id"] != output_id]
    elif case == "event_evidence_missing":
        events[1]["evidence_refs"] = [missing_address]
        reseal_event(1)
    elif case == "event_evidence_future":
        events[1]["evidence_refs"] = [events[3]["output_artifact_refs"][0]]
        reseal_event(1)
    elif case == "event_evidence_stale":
        source = artifact_with_schema("stormshift.input")
        source["fresh_until_ms"] = 0
        _reseal_record(source)
    elif case == "claim_order":
        claims.append(copy.deepcopy(claims[0]))
    elif case == "claim_integrity":
        claims[0]["statement"] = "Mutated after the claim digest was created."
        _reseal_record(claims[0])
    elif case == "claim_record_seal":
        claims[0]["producer"] = "mutated-without-resealing"
    elif case == "claim_reference_order":
        evidence_id = claims[0]["evidence_refs"][0]
        claims[0]["evidence_refs"] = [evidence_id, evidence_id]
        _reseal_record(claims[0])
    elif case == "claim_unsupported":
        claims[0]["evidence_refs"] = []
        _reseal_record(claims[0])
    elif case == "claim_producer_missing":
        claims[0]["produced_by_event_digest"] = missing_digest
        _reseal_record(claims[0])
    elif case == "claim_time":
        claims[0]["created_at_ms"] = 7
        _reseal_record(claims[0])
    elif case == "claim_evidence_missing":
        claims[0]["evidence_refs"] = [missing_address]
        _reseal_record(claims[0])
    elif case == "claim_evidence_future":
        claims[0]["produced_by_event_digest"] = events[3]["event_digest"]
        _reseal_record(claims[0])
    elif case == "claim_evidence_stale":
        claims[0]["created_at_ms"] = 2
        _reseal_record(claims[0])
    elif case == "claim_conflict_missing":
        claims[0]["contradicts"] = ["missing-claim"]
        _reseal_record(claims[0])
    elif case == "context_order":
        contexts.append(copy.deepcopy(contexts[0]))
    elif case == "context_duplicate_validation":
        duplicate = copy.deepcopy(contexts[0])
        duplicate["obligation_id"] = "route-grounding-2"
        _reseal_record(duplicate)
        contexts.append(duplicate)
        contexts.sort(key=lambda item: str(item["obligation_id"]))
    elif case == "context_run":
        contexts[0]["run_id"] = "different-run"
        _reseal_record(contexts[0])
    elif case == "context_validation":
        contexts[0]["validation_event_digest"] = missing_digest
        _reseal_record(contexts[0])
    elif case == "context_evidence_empty":
        contexts[0]["evidence_refs"] = []
        _reseal_record(contexts[0])
    elif case == "context_reference_order":
        evidence_id = contexts[0]["evidence_refs"][0]
        contexts[0]["evidence_refs"] = [evidence_id, evidence_id]
        _reseal_record(contexts[0])
    elif case == "context_completion":
        events[3]["causes"] = [events[1]["event_digest"]]
        reseal_event(3)
    elif case == "context_evidence_missing":
        contexts[0]["evidence_refs"] = [missing_address]
        _reseal_record(contexts[0])
    elif case == "approval_unregistered":
        content["approvals"] = []
    elif case == "approval_run":
        approvals[0]["run_id"] = "different-run"
        _reseal_record(approvals[0])
    elif case == "approval_record_seal":
        approvals[0]["principal"] = "mutated-without-resealing"
    elif case == "approval_event":
        approvals[0]["grant_event_digest"] = missing_digest
        _reseal_record(approvals[0])
    elif case == "approval_uniqueness":
        approvals[0]["approval_id"] = "approval-not-in-events"
        _reseal_record(approvals[0])
    elif case == "effect_unregistered":
        content["effects"] = []
    elif case == "effect_order":
        effects.append(copy.deepcopy(effects[0]))
    elif case == "effect_payload":
        effects[0]["payload_artifact_ref"] = missing_address
        _reseal_record(effects[0])
    elif case == "effect_proposal":
        effects[0]["proposed_event_digest"] = missing_digest
        _reseal_record(effects[0])
    elif case == "effect_payload_future":
        effects[0]["payload_artifact_ref"] = artifact_with_schema("stormshift.effect-receipt")[
            "artifact_id"
        ]
        _reseal_record(effects[0])
    elif case == "effect_proposal_approval":
        events[5]["approval_id"] = "approval-alert-1"
        reseal_event(5)
        effects[0]["proposed_event_digest"] = events[5]["event_digest"]
        _reseal_record(effects[0])
    elif case == "effect_proposal_uniqueness":
        events[5]["effect_id"] = "different-effect"
        reseal_event(5)
    elif case == "effect_commit_uniqueness":
        events[8].update(
            {
                "event_type": "effect.committed",
                "attempt": None,
                "output_artifact_refs": [],
                "approval_id": "approval-alert-1",
                "effect_id": "effect-alert-1",
            }
        )
        reseal_event(8)
    elif case == "effect_approval":
        effects[0]["approval_id"] = "missing-approval"
        _reseal_record(effects[0])
    elif case == "effect_scope":
        approvals[0]["scope_digest"] = missing_digest
        _reseal_record(approvals[0])
    elif case == "effect_approval_causality":
        events[6]["causes"] = []
        reseal_event(6)
    elif case == "effect_proposed_state":
        effects[0]["terminal_state"] = "proposed"
        _reseal_record(effects[0])
    elif case == "effect_aborted_binding":
        effects[0]["terminal_state"] = "aborted"
        _reseal_record(effects[0])
    elif case == "effect_compensated_binding":
        effects[0]["terminal_state"] = "compensated"
        effects[0]["commit_event_digest"] = None
        _reseal_record(effects[0])
    elif case == "effect_committed_terminal_binding":
        effects[0]["terminal_event_digest"] = effects[0]["proposed_event_digest"]
        _reseal_record(effects[0])
    elif case == "effect_abort_causality":
        events[7]["event_type"] = "effect.aborted"
        events[7]["causes"] = []
        events[7]["approval_id"] = None
        reseal_event(7)
        effects[0]["terminal_state"] = "aborted"
        effects[0]["commit_event_digest"] = None
        effects[0]["terminal_event_digest"] = events[7]["event_digest"]
        _reseal_record(effects[0])
    elif case == "effect_compensation_causality":
        events[8].update(
            {
                "event_type": "effect.compensated",
                "attempt": None,
                "causes": [events[5]["event_digest"]],
                "output_artifact_refs": [],
                "approval_id": "approval-alert-1",
                "effect_id": "effect-alert-1",
            }
        )
        reseal_event(8)
        effects[0]["terminal_state"] = "compensated"
        effects[0]["terminal_event_digest"] = events[8]["event_digest"]
        _reseal_record(effects[0])
    elif case == "effect_commit_approval":
        effects[0]["approval_id"] = None
        content["approvals"] = []
        _reseal_record(effects[0])
    elif case == "effect_commit_causality":
        events[7]["causes"] = []
        reseal_event(7)
        effects[0]["commit_event_digest"] = events[7]["event_digest"]
        effects[0]["terminal_event_digest"] = events[7]["event_digest"]
        _reseal_record(effects[0])
    elif case == "orphan_approval":
        content["effects"] = []
    elif case == "replay_envelope":
        replay = content["replay_witness"]
        assert isinstance(replay, dict)
        replay["envelope_digest"] = missing_digest
        _reseal_record(replay, "witness_digest")
    else:
        raise AssertionError(case)

    _reseal_outer(evidence)
    report = verify_whole_run_evidence(evidence)
    assert not report.passed
    assert expected_code in report.violation_codes
