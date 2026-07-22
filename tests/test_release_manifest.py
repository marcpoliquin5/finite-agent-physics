from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest

from agent_physics.release_manifest import (
    CAPABILITY_IDS,
    INTEGRATED_PROOF_IDS,
    RELEASE_GATE_IDS,
    REQUIRED_EXTERNAL_KINDS,
    SCHEMA_VERSION,
    ReleaseManifestError,
    compute_evidence_digest,
    seal_release_manifest,
    validate_release_manifest,
)


COMMIT = "1" * 40
GENERATED_AT = "2026-07-31T12:00:00Z"
OBSERVED_AT = "2026-07-30T12:00:00Z"
CORE_ARTIFACT = "artifact:core"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _force_seal(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute only the outer seal so inner-integrity mutations reach their verifier."""

    result = copy.deepcopy(document)
    result.pop("seal", None)
    encoded = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    result["seal"] = {
        "algorithm": "sha256",
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    return result


def _artifact_maps(document: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    digests = {item["id"]: item["sha256"] for item in document["artifacts"]}
    classifications = {item["id"]: item["classification"] for item in document["artifacts"]}
    return digests, classifications


def _refresh_record_digests(document: dict[str, Any]) -> None:
    digests, classifications = _artifact_maps(document)
    for section in ("capabilities", "integrated_proofs", "release_gates"):
        for record in document[section]:
            record["evidence_digest"] = compute_evidence_digest(
                record_id=record["id"],
                status=record["status"],
                scope=record["scope"],
                source_commit=record["source_commit"],
                evidence_refs=record["evidence_refs"],
                artifact_digests=digests,
                artifact_classifications=classifications,
            )


def _unsealed_ready_manifest() -> tuple[dict[str, Any], dict[str, bytes]]:
    classifications = {
        "bob": "genuine_external",
        "deployment": "genuine_external",
        "eligibility": "human_attested_private",
        "github": "genuine_external",
        "skillsbuild": "human_attested_private",
        "submission": "genuine_external",
        "video": "genuine_external",
        "watsonx": "genuine_external",
    }
    payloads: dict[str, bytes] = {CORE_ARTIFACT: b"core release evidence"}
    for kind in sorted(REQUIRED_EXTERNAL_KINDS):
        payloads[f"artifact:external-{kind}"] = f"redacted {kind} evidence".encode()

    artifact_records = []
    for artifact_id in sorted(payloads):
        kind = artifact_id.removeprefix("artifact:external-")
        classification = "local" if artifact_id == CORE_ARTIFACT else classifications[kind]
        payload = payloads[artifact_id]
        artifact_records.append(
            {
                "id": artifact_id,
                "path_or_uri": f"artifacts/{artifact_id.removeprefix('artifact:')}.json",
                "media_type": "application/json",
                "sha256": _sha(payload),
                "bytes": len(payload),
                "produced_by": "release evidence fixture",
                "exit_code": 0,
                "observed_at": OBSERVED_AT,
                "source_commit": COMMIT,
                "classification": classification,
                "contains_secrets": False,
            }
        )

    artifact_digests = {item["id"]: item["sha256"] for item in artifact_records}
    artifact_classifications = {item["id"]: item["classification"] for item in artifact_records}

    def digest(
        record_id: str,
        status: str,
        scope: str,
        evidence_refs: list[str],
    ) -> str:
        return compute_evidence_digest(
            record_id=record_id,
            status=status,
            scope=scope,
            source_commit=COMMIT,
            evidence_refs=evidence_refs,
            artifact_digests=artifact_digests,
            artifact_classifications=artifact_classifications,
        )

    capabilities = []
    for capability_id in CAPABILITY_IDS:
        evidence_refs = [CORE_ARTIFACT]
        capabilities.append(
            {
                "id": capability_id,
                "gate_text_sha256": _sha(f"gate {capability_id}".encode()),
                "status": "pass",
                "scope": "local",
                "source_commit": COMMIT,
                "evidence_refs": evidence_refs,
                "evidence_digest": digest(capability_id, "pass", "local", evidence_refs),
                "test_refs": ["tests/test_release_manifest.py"],
                "claim_ids": [],
                "limitations": [],
            }
        )

    proofs = []
    for proof_id in INTEGRATED_PROOF_IDS:
        evidence_refs = [CORE_ARTIFACT]
        if proof_id == "R01":
            evidence_refs.extend(["artifact:external-bob", "artifact:external-watsonx"])
        if proof_id == "R08":
            evidence_refs.append("artifact:external-deployment")
        evidence_refs.sort()
        scope = "live" if proof_id in {"R01", "R08"} else "local"
        proofs.append(
            {
                "id": proof_id,
                "gate_text_sha256": _sha(f"gate {proof_id}".encode()),
                "status": "pass",
                "scope": scope,
                "source_commit": COMMIT,
                "evidence_refs": evidence_refs,
                "evidence_digest": digest(proof_id, "pass", scope, evidence_refs),
                "run_ids": ["run:release-1"],
                "limitations": [],
            }
        )

    release_gates = []
    for gate_id in RELEASE_GATE_IDS:
        evidence_refs = [CORE_ARTIFACT]
        if gate_id == "V5-02":
            evidence_refs.append("artifact:external-github")
        if gate_id == "V5-09":
            evidence_refs.extend(
                [
                    "artifact:external-eligibility",
                    "artifact:external-skillsbuild",
                    "artifact:external-submission",
                    "artifact:external-video",
                ]
            )
        evidence_refs.sort()
        scope = "live" if gate_id in {"V5-02", "V5-09"} else "local"
        release_gates.append(
            {
                "id": gate_id,
                "status": "pass",
                "scope": scope,
                "source_commit": COMMIT,
                "evidence_refs": evidence_refs,
                "evidence_digest": digest(gate_id, "pass", scope, evidence_refs),
                "validator": f"python -m finite_release_verify --gate {gate_id}",
                "limitations": [],
            }
        )

    attestations = []
    for kind in sorted(REQUIRED_EXTERNAL_KINDS):
        attestations.append(
            {
                "id": f"attestation:{kind}",
                "kind": kind,
                "classification": classifications[kind],
                "owner": "entrant" if classifications[kind].startswith("human") else "provider",
                "observed_at": OBSERVED_AT,
                "redacted_artifact_ref": f"artifact:external-{kind}",
                "original_sha256": _sha(f"private original {kind}".encode()),
                "reviewed_by": "release-reviewer",
                "availability": (
                    "private-attested"
                    if classifications[kind] == "human_attested_private"
                    else (
                        "public"
                        if kind in {"github", "deployment", "video", "submission"}
                        else "available-to-judges"
                    )
                ),
                "source_commit": COMMIT,
            }
        )

    document = {
        "schema_version": SCHEMA_VERSION,
        "release": {
            "name": "FINITE",
            "version": "5.0.0",
            "decision": "pass",
            "ready": True,
            "generated_at": GENERATED_AT,
            "evidence_max_age_seconds": 7 * 24 * 60 * 60,
        },
        "source": {
            "repository": "https://github.com/example/finite",
            "commit": COMMIT,
            "tree_sha256": "2" * 64,
            "tag": "v5.0.0",
            "dirty": False,
            "program_sha256": "3" * 64,
            "release_contract_sha256": "4" * 64,
        },
        "capabilities": capabilities,
        "integrated_proofs": proofs,
        "release_gates": release_gates,
        "artifacts": artifact_records,
        "external_attestations": attestations,
    }
    return document, payloads


def _sealed_ready_manifest() -> tuple[dict[str, Any], dict[str, bytes]]:
    document, payloads = _unsealed_ready_manifest()
    return seal_release_manifest(document), payloads


def test_complete_release_manifest_is_canonical_sealed_and_payload_verified() -> None:
    sealed, payloads = _sealed_ready_manifest()
    result = validate_release_manifest(sealed, artifact_payloads=payloads)

    assert result.release_ready is True
    assert result.decision == "pass"
    assert result.source_commit == COMMIT
    assert result.artifact_payloads_verified is True
    assert result.manifest_sha256 == sealed["seal"]["manifest_sha256"]
    assert result.to_python() == sealed
    assert result.canonical_json == json.dumps(
        sealed, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )

    from_text = validate_release_manifest(json.dumps(sealed, indent=2), artifact_payloads=payloads)
    from_bytes = validate_release_manifest(
        result.canonical_json.encode(), artifact_payloads=payloads
    )
    assert from_text == from_bytes == result


def test_seal_is_mapping_order_independent_and_does_not_mutate_input() -> None:
    document, _ = _unsealed_ready_manifest()
    original = copy.deepcopy(document)
    reversed_root = dict(reversed(list(document.items())))

    first = seal_release_manifest(document)
    second = seal_release_manifest(reversed_root)

    assert document == original
    assert first["seal"] == second["seal"]
    assert first == second


@pytest.mark.parametrize("section", ["capabilities", "integrated_proofs", "release_gates"])
def test_exact_program_id_sets_reject_missing_duplicate_and_reordered_records(
    section: str,
) -> None:
    document, _ = _unsealed_ready_manifest()
    missing = copy.deepcopy(document)
    missing[section].pop()
    with pytest.raises(ReleaseManifestError, match="exact ID set/order|required"):
        seal_release_manifest(missing)

    duplicate = copy.deepcopy(document)
    duplicate[section].insert(1, copy.deepcopy(duplicate[section][0]))
    with pytest.raises(ReleaseManifestError, match="duplicate ID"):
        seal_release_manifest(duplicate)

    reordered = copy.deepcopy(document)
    reordered[section][0], reordered[section][1] = (
        reordered[section][1],
        reordered[section][0],
    )
    with pytest.raises(ReleaseManifestError, match="canonical ID order"):
        seal_release_manifest(reordered)


def test_unknown_program_id_is_not_accepted_as_a_replacement() -> None:
    document, _ = _unsealed_ready_manifest()
    document["capabilities"][-1]["id"] = "S26"
    _refresh_record_digests(document)
    with pytest.raises(ReleaseManifestError, match="missing=.*S25.*unknown=.*S26"):
        seal_release_manifest(document)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("future", "ignored"),
        lambda value: value["release"].__setitem__("channel", "stable"),
        lambda value: value["source"].__setitem__("branch", "main"),
        lambda value: value["capabilities"][0].__setitem__("notes", []),
        lambda value: value["artifacts"][0].__setitem__("trusted", True),
        lambda value: value["external_attestations"][0].__setitem__("url", "https://x"),
    ],
)
def test_unknown_fields_fail_closed_at_every_manifest_level(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    document, _ = _unsealed_ready_manifest()
    mutation(document)
    with pytest.raises(ReleaseManifestError, match="unknown fields"):
        seal_release_manifest(document)

    sealed, payloads = _sealed_ready_manifest()
    sealed["seal"]["future"] = "ignored"
    with pytest.raises(ReleaseManifestError, match="unknown fields"):
        validate_release_manifest(sealed, artifact_payloads=payloads)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["release"].__setitem__("evidence_max_age_seconds", True),
        lambda value: value["release"].__setitem__("ready", 1),
        lambda value: value["artifacts"][0].__setitem__("bytes", False),
        lambda value: value["artifacts"][0].__setitem__("exit_code", True),
    ],
)
def test_boolean_integer_aliases_are_rejected(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    document, _ = _unsealed_ready_manifest()
    mutation(document)
    with pytest.raises(ReleaseManifestError, match="expected an integer|expected a boolean"):
        seal_release_manifest(document)


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("root", "schema_version"),
        ("release", "generated_at"),
        ("source", "tree_sha256"),
        ("capability", "scope"),
        ("artifact", "observed_at"),
        ("attestation", "reviewed_by"),
    ],
)
def test_missing_required_fields_are_rejected(location: str, field: str) -> None:
    document, _ = _unsealed_ready_manifest()
    target: dict[str, Any]
    if location == "root":
        target = document
    elif location == "release":
        target = document["release"]
    elif location == "source":
        target = document["source"]
    elif location == "capability":
        target = document["capabilities"][0]
    elif location == "artifact":
        target = document["artifacts"][0]
    else:
        target = document["external_attestations"][0]
    target.pop(field)

    with pytest.raises(ReleaseManifestError, match="missing required fields"):
        seal_release_manifest(document)


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("source", "tree_sha256"),
        ("capability", "gate_text_sha256"),
        ("artifact", "sha256"),
        ("attestation", "original_sha256"),
    ],
)
def test_malformed_sha256_values_are_rejected(target: str, field: str) -> None:
    document, _ = _unsealed_ready_manifest()
    if target == "source":
        record = document["source"]
    elif target == "capability":
        record = document["capabilities"][0]
    elif target == "artifact":
        record = document["artifacts"][0]
    else:
        record = document["external_attestations"][0]
    record[field] = "A" * 64

    with pytest.raises(ReleaseManifestError, match="lowercase hexadecimal SHA-256"):
        seal_release_manifest(document)


@pytest.mark.parametrize("location", ["artifact", "attestation"])
def test_stale_and_future_dated_evidence_is_rejected(location: str) -> None:
    document, _ = _unsealed_ready_manifest()
    target = (
        document["artifacts"][0] if location == "artifact" else document["external_attestations"][0]
    )
    target["observed_at"] = "2026-06-01T00:00:00Z"
    with pytest.raises(ReleaseManifestError, match="stale evidence"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    target = (
        document["artifacts"][0] if location == "artifact" else document["external_attestations"][0]
    )
    target["observed_at"] = "2026-08-01T00:00:00Z"
    with pytest.raises(ReleaseManifestError, match="dated after"):
        seal_release_manifest(document)


@pytest.mark.parametrize("section", ["capabilities", "integrated_proofs", "release_gates"])
def test_pass_records_require_evidence(section: str) -> None:
    document, _ = _unsealed_ready_manifest()
    record = document[section][0]
    record["evidence_refs"] = []
    _refresh_record_digests(document)
    with pytest.raises(ReleaseManifestError, match="at least one entry is required"):
        seal_release_manifest(document)


def test_pass_capability_requires_test_and_pass_proof_requires_run_identity() -> None:
    document, _ = _unsealed_ready_manifest()
    document["capabilities"][0]["test_refs"] = []
    with pytest.raises(ReleaseManifestError, match="test_refs.*at least one"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["integrated_proofs"][0]["run_ids"] = []
    with pytest.raises(ReleaseManifestError, match="run_ids.*at least one"):
        seal_release_manifest(document)


def test_references_must_resolve_and_evidence_digest_binds_status_scope_and_artifacts() -> None:
    document, _ = _unsealed_ready_manifest()
    document["capabilities"][0]["evidence_refs"] = ["artifact:missing"]
    document["capabilities"][0]["evidence_digest"] = "0" * 64
    with pytest.raises(ReleaseManifestError, match="missing artifact"):
        seal_release_manifest(document)

    sealed, payloads = _sealed_ready_manifest()
    sealed["capabilities"][0]["evidence_digest"] = "0" * 64
    sealed = _force_seal(sealed)
    with pytest.raises(ReleaseManifestError, match="evidence digest mismatch"):
        validate_release_manifest(sealed, artifact_payloads=payloads)


@pytest.mark.parametrize("location", ["capability", "artifact", "attestation"])
def test_every_evidence_record_is_bound_to_the_source_commit(location: str) -> None:
    document, _ = _unsealed_ready_manifest()
    if location == "capability":
        target = document["capabilities"][0]
    elif location == "artifact":
        target = document["artifacts"][0]
    else:
        target = document["external_attestations"][0]
    target["source_commit"] = "9" * 40
    if location == "capability":
        _refresh_record_digests(document)
    with pytest.raises(ReleaseManifestError, match="does not match source commit"):
        seal_release_manifest(document)


def test_nonready_manifest_can_report_partial_work_without_claiming_release() -> None:
    document, _ = _unsealed_ready_manifest()
    document["release"].update({"version": "5.0.0-rc.1", "decision": "blocked", "ready": False})
    document["source"].update({"tag": "v5.0.0-rc.1", "dirty": True})
    document["capabilities"][0]["status"] = "partial"
    _refresh_record_digests(document)
    sealed = seal_release_manifest(document)

    result = validate_release_manifest(sealed)
    assert result.release_ready is False
    assert result.decision == "blocked"
    assert result.artifact_payloads_verified is False


def test_release_ready_rejects_any_nonpassing_program_or_gate_status() -> None:
    for section, status in (
        ("capabilities", "partial"),
        ("integrated_proofs", "blocked"),
        ("release_gates", "fail"),
    ):
        document, _ = _unsealed_ready_manifest()
        document[section][0]["status"] = status
        _refresh_record_digests(document)
        with pytest.raises(ReleaseManifestError, match="every capability, proof, and release gate"):
            seal_release_manifest(document)


def test_release_decision_ready_tag_and_clean_tree_are_consistent() -> None:
    document, _ = _unsealed_ready_manifest()
    document["release"]["ready"] = False
    with pytest.raises(ReleaseManifestError, match="decision 'pass' requires ready=true"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["source"]["tag"] = "v5.0.0-rc.1"
    with pytest.raises(ReleaseManifestError, match="source tag 'v5.0.0'"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["source"]["dirty"] = True
    with pytest.raises(ReleaseManifestError, match="source must be clean"):
        seal_release_manifest(document)


def test_release_ready_requires_each_genuine_external_evidence_kind() -> None:
    document, _ = _unsealed_ready_manifest()
    document["external_attestations"] = [
        item for item in document["external_attestations"] if item["kind"] != "bob"
    ]
    with pytest.raises(ReleaseManifestError, match="genuine evidence for every external gate"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    bob_artifact = next(
        item for item in document["artifacts"] if item["id"] == "artifact:external-bob"
    )
    bob_attestation = next(
        item for item in document["external_attestations"] if item["kind"] == "bob"
    )
    bob_artifact["classification"] = "simulated"
    bob_attestation["classification"] = "simulated"
    _refresh_record_digests(document)
    with pytest.raises(ReleaseManifestError, match="genuine evidence for every external gate"):
        seal_release_manifest(document)


def test_external_attestation_must_be_bound_to_its_required_proof_or_release_gate() -> None:
    document, _ = _unsealed_ready_manifest()
    r01 = next(item for item in document["integrated_proofs"] if item["id"] == "R01")
    r01["evidence_refs"].remove("artifact:external-bob")
    _refresh_record_digests(document)
    with pytest.raises(ReleaseManifestError, match="genuine evidence for every external gate"):
        seal_release_manifest(document)


def test_public_external_gate_evidence_must_actually_be_public() -> None:
    document, _ = _unsealed_ready_manifest()
    github = next(item for item in document["external_attestations"] if item["kind"] == "github")
    github["availability"] = "private-attested"
    with pytest.raises(ReleaseManifestError, match="genuine evidence for every external gate"):
        seal_release_manifest(document)


def test_release_ready_requires_exact_verified_artifact_payload_set() -> None:
    sealed, payloads = _sealed_ready_manifest()
    with pytest.raises(ReleaseManifestError, match="requires every artifact payload"):
        validate_release_manifest(sealed)

    missing = dict(payloads)
    missing.pop(CORE_ARTIFACT)
    with pytest.raises(ReleaseManifestError, match="missing=.*artifact:core"):
        validate_release_manifest(sealed, artifact_payloads=missing)

    extra = dict(payloads)
    extra["artifact:extra"] = b"extra"
    with pytest.raises(ReleaseManifestError, match="unknown=.*artifact:extra"):
        validate_release_manifest(sealed, artifact_payloads=extra)


def test_artifact_payload_byte_length_and_sha256_are_verified() -> None:
    sealed, payloads = _sealed_ready_manifest()
    wrong_length = dict(payloads)
    wrong_length[CORE_ARTIFACT] += b"!"
    with pytest.raises(ReleaseManifestError, match="byte length mismatch"):
        validate_release_manifest(sealed, artifact_payloads=wrong_length)

    wrong_hash = dict(payloads)
    wrong_hash[CORE_ARTIFACT] = b"X" * len(payloads[CORE_ARTIFACT])
    with pytest.raises(ReleaseManifestError, match="SHA-256 mismatch"):
        validate_release_manifest(sealed, artifact_payloads=wrong_hash)


def test_outer_seal_detects_any_post_seal_mutation() -> None:
    sealed, payloads = _sealed_ready_manifest()
    sealed["artifacts"][0]["produced_by"] = "mutated after sealing"
    with pytest.raises(ReleaseManifestError, match="manifest seal mismatch"):
        validate_release_manifest(sealed, artifact_payloads=payloads)

    sealed, payloads = _sealed_ready_manifest()
    sealed["seal"]["manifest_sha256"] = "f" * 64
    with pytest.raises(ReleaseManifestError, match="manifest seal mismatch"):
        validate_release_manifest(sealed, artifact_payloads=payloads)


def test_json_duplicate_keys_nonfinite_constants_and_nonobject_sources_fail() -> None:
    with pytest.raises(ReleaseManifestError, match="duplicate key"):
        validate_release_manifest('{"schema_version":"x","schema_version":"y"}')
    with pytest.raises(ReleaseManifestError, match="not supported"):
        validate_release_manifest('{"schema_version":NaN}')
    with pytest.raises(ReleaseManifestError, match="expected an object"):
        validate_release_manifest("[]")
    with pytest.raises(ReleaseManifestError, match="valid UTF-8"):
        validate_release_manifest(b"\xff")


def test_secret_bearing_or_path_traversing_artifacts_fail_closed() -> None:
    document, _ = _unsealed_ready_manifest()
    document["artifacts"][0]["contains_secrets"] = True
    with pytest.raises(ReleaseManifestError, match="secrets are forbidden"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["artifacts"][0]["path_or_uri"] = "../private/evidence.json"
    with pytest.raises(ReleaseManifestError, match="normalized relative path"):
        seal_release_manifest(document)


def test_malformed_scope_status_commit_and_timestamp_are_rejected() -> None:
    document, _ = _unsealed_ready_manifest()
    document["capabilities"][0]["scope"] = "production"
    with pytest.raises(ReleaseManifestError, match="unsupported value"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["capabilities"][0]["status"] = "implemented"
    with pytest.raises(ReleaseManifestError, match="unsupported value"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["source"]["commit"] = "1" * 64
    with pytest.raises(ReleaseManifestError, match="40-character lowercase commit"):
        seal_release_manifest(document)

    document, _ = _unsealed_ready_manifest()
    document["release"]["generated_at"] = "2026-07-31T12:00:00-04:00"
    with pytest.raises(ReleaseManifestError, match="RFC 3339 UTC seconds"):
        seal_release_manifest(document)
