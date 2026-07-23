from dataclasses import replace

import pytest

from agent_physics.artifacts import (
    Artifact,
    Claim,
    ClaimAssessmentStatus,
    ClaimStatus,
    EvidenceSet,
    Sensitivity,
)
from agent_physics.context import (
    ContextBlock,
    ContextBudget,
    ContextManifest,
    ContextObligations,
    ContextPacker,
    ContextSourceKind,
    OptionalArtifact,
    PackedContext,
    PackingStatus,
)
from agent_physics.serialization import content_digest


NOW = 10_000
MISSING_ADDRESS = "sha256:" + "f" * 64


def artifact(
    payload: bytes = b"observation",
    *,
    created_at_ms: int = 1_000,
    fresh_until_ms: int | None = 20_000,
    parents: tuple[str, ...] = (),
) -> Artifact:
    return Artifact.create(
        payload,
        schema="test.observation",
        schema_version="1.0.0",
        media_type="application/octet-stream",
        producer="adversarial-test",
        parents=parents,
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=created_at_ms,
        fresh_until_ms=fresh_until_ms,
    )


def claim(
    claim_id: str,
    evidence_refs: tuple[str, ...] = (),
    *,
    status: ClaimStatus = ClaimStatus.SUPPORTED,
    created_at_ms: int = 2_000,
    contradicts: tuple[str, ...] = (),
) -> Claim:
    return Claim.create(
        claim_id,
        f"statement for {claim_id}",
        evidence_refs=evidence_refs,
        status=status,
        producer="adversarial-test",
        created_at_ms=created_at_ms,
        contradicts=contradicts,
    )


def reseal(manifest: ContextManifest, **changes: object) -> ContextManifest:
    changed = replace(manifest, **changes)
    return replace(changed, manifest_digest=content_digest(changed.unsigned_payload()))


def test_artifact_construction_and_verification_reject_every_malformed_boundary() -> None:
    valid_parent = "sha256:" + "0" * 64
    later_parent = "sha256:" + "1" * 64

    with pytest.raises(TypeError, match="payload must be bytes"):
        Artifact.create(
            bytearray(b"not immutable"),
            schema="test",
            schema_version="1",
            media_type="application/octet-stream",
            producer="test",
            created_at_ms=0,
        )
    with pytest.raises(ValueError, match="are required"):
        Artifact.create(
            b"data",
            schema="",
            schema_version="1",
            media_type="application/octet-stream",
            producer="test",
            created_at_ms=0,
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        artifact(created_at_ms=-1)
    with pytest.raises(ValueError, match="cannot precede"):
        artifact(created_at_ms=2, fresh_until_ms=1)
    for malformed in ("sha256:not-a-digest", "sha256:" + "A" * 64, 7):
        with pytest.raises(ValueError, match="sha256 addresses"):
            Artifact.create(
                b"data",
                schema="test",
                schema_version="1",
                media_type="application/octet-stream",
                producer="test",
                parents=(malformed,),
                created_at_ms=0,
            )

    valid = artifact(parents=(later_parent, valid_parent, later_parent))
    assert valid.parents == (valid_parent, later_parent)
    mutations = (
        replace(valid, created_at_ms=-1),
        replace(valid, fresh_until_ms=valid.created_at_ms - 1),
        replace(valid, schema=""),
        replace(valid, parents=(valid_parent, valid_parent)),
        replace(valid, parents=("sha256:truncated",)),
        replace(valid, payload=b"tampered"),
        replace(valid, payload_sha256="0" * 64),
        replace(valid, artifact_id=MISSING_ADDRESS),
    )
    assert all(not mutation.verify() for mutation in mutations)
    assert not valid.is_fresh(valid.created_at_ms - 1)
    assert valid.is_fresh(valid.created_at_ms)
    assert valid.is_fresh(valid.fresh_until_ms or 0)
    assert not valid.is_fresh((valid.fresh_until_ms or 0) + 1)
    assert artifact(fresh_until_ms=None).is_fresh(10**12)


def test_claim_construction_and_verification_reject_every_malformed_boundary() -> None:
    evidence = artifact()
    address = evidence.artifact_id

    with pytest.raises(ValueError, match="are required"):
        Claim.create(
            "",
            "statement",
            evidence_refs=(address,),
            status=ClaimStatus.SUPPORTED,
            producer="test",
            created_at_ms=0,
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        claim("negative-time", (address,), created_at_ms=-1)
    with pytest.raises(ValueError, match="contradict itself"):
        claim("self-conflict", (address,), contradicts=("self-conflict",))
    for malformed in ("sha256:not-a-digest", "sha256:" + "A" * 64, 7):
        with pytest.raises(ValueError, match="sha256 addresses"):
            Claim.create(
                "malformed-evidence",
                "statement",
                evidence_refs=(malformed,),
                status=ClaimStatus.SUPPORTED,
                producer="test",
                created_at_ms=0,
            )

    valid = claim("valid", (address,))
    mutations = (
        replace(valid, statement=""),
        replace(valid, created_at_ms=-1),
        replace(valid, evidence_refs=(address, address)),
        replace(valid, contradicts=("z", "a")),
        replace(valid, contradicts=(valid.claim_id,)),
        replace(valid, evidence_refs=("sha256:truncated",)),
        replace(valid, claim_digest="0" * 64),
    )
    assert all(not mutation.verify() for mutation in mutations)


def test_evidence_set_recalculates_all_claim_states_and_conflict_paths() -> None:
    fresh = artifact(b"fresh")
    stale = artifact(b"stale", fresh_until_ms=NOW - 1)
    tampered = replace(artifact(b"authentic"), payload=b"forged")

    with pytest.raises(ValueError, match="duplicate artifact"):
        EvidenceSet.from_records((fresh, fresh))
    duplicate_claim = claim("duplicate", (fresh.artifact_id,))
    with pytest.raises(ValueError, match="duplicate claim"):
        EvidenceSet.from_records((fresh,), (duplicate_claim, duplicate_claim))

    cases = (
        (
            EvidenceSet.from_records((fresh,), (replace(claim("invalid"), statement=""),)),
            "invalid",
            ClaimAssessmentStatus.INVALID,
        ),
        (
            EvidenceSet.from_records(
                (fresh,),
                (claim("future", (fresh.artifact_id,), created_at_ms=NOW + 1),),
            ),
            "future",
            ClaimAssessmentStatus.INVALID,
        ),
        (
            EvidenceSet.from_records((), (claim("no-evidence"),)),
            "no-evidence",
            ClaimAssessmentStatus.UNVERIFIED,
        ),
        (
            EvidenceSet.from_records((), (claim("missing-evidence", (MISSING_ADDRESS,)),)),
            "missing-evidence",
            ClaimAssessmentStatus.UNVERIFIED,
        ),
        (
            EvidenceSet.from_records(
                (tampered,),
                (claim("invalid-evidence", (tampered.artifact_id,)),),
            ),
            "invalid-evidence",
            ClaimAssessmentStatus.INVALID,
        ),
        (
            EvidenceSet.from_records((stale,), (claim("stale-evidence", (stale.artifact_id,)),)),
            "stale-evidence",
            ClaimAssessmentStatus.STALE,
        ),
        (
            EvidenceSet.from_records((fresh,), (claim("supported", (fresh.artifact_id,)),)),
            "supported",
            ClaimAssessmentStatus.SUPPORTED,
        ),
    )
    for evidence, claim_id, expected in cases:
        assert evidence.assess_claim(claim_id, NOW).status is expected

    declared = {
        ClaimStatus.UNVERIFIED: ClaimAssessmentStatus.UNVERIFIED,
        ClaimStatus.STALE: ClaimAssessmentStatus.STALE,
        ClaimStatus.CONTRADICTED: ClaimAssessmentStatus.CONTRADICTED,
        ClaimStatus.RETRACTED: ClaimAssessmentStatus.RETRACTED,
    }
    for declared_status, assessed_status in declared.items():
        declared_claim = claim(f"declared-{declared_status.value}", status=declared_status)
        evidence = EvidenceSet.from_records(claims=(declared_claim,))
        assert evidence.assess_claim(declared_claim.claim_id, NOW).status is assessed_status

    primary = claim("primary", (fresh.artifact_id,), contradicts=("withdrawn",))
    unrelated = claim("unrelated", (fresh.artifact_id,))
    withdrawn = claim("withdrawn", status=ClaimStatus.RETRACTED)
    evidence = EvidenceSet.from_records((fresh,), (primary, unrelated, withdrawn))
    assert evidence.assess_claim("primary", NOW).status is ClaimAssessmentStatus.SUPPORTED
    assert evidence.assess_claim("absent", NOW).status is ClaimAssessmentStatus.MISSING


def test_packed_context_verification_rejects_resealed_semantic_forgeries() -> None:
    required = artifact()
    packed = ContextPacker().pack(
        EvidenceSet.from_records((required,)),
        ContextObligations.create(required_artifacts=(required.artifact_id,)),
        ContextBudget(100_000, 100_000),
        as_of_ms=NOW,
    )
    entry = packed.manifest.included[0]
    assert packed.verify()

    refused = ContextPacker().pack(
        EvidenceSet.from_records(),
        ContextObligations.create(required_artifacts=(MISSING_ADDRESS,)),
        ContextBudget(100_000, 100_000),
        as_of_ms=NOW,
    )
    assert refused.verify()
    assert not replace(refused, blocks=packed.blocks).verify()
    refused_with_included = reseal(refused.manifest, included=(entry,))
    assert not replace(refused, manifest=refused_with_included).verify()

    packed_with_refusal = reseal(packed.manifest, refusal_reasons=("forged refusal",))
    assert not replace(packed, manifest=packed_with_refusal).verify()
    assert not replace(packed, blocks=()).verify()
    altered_entry = replace(entry, included=False)
    altered_entries = reseal(packed.manifest, included=(altered_entry,))
    assert not replace(packed, manifest=altered_entries).verify()

    accounting_forgeries = (
        reseal(packed.manifest, used_bytes=packed.manifest.used_bytes + 1),
        reseal(packed.manifest, used_tokens=packed.manifest.used_tokens + 1),
        reseal(packed.manifest, byte_cap=packed.manifest.used_bytes - 1),
        reseal(packed.manifest, token_cap=packed.manifest.used_tokens - 1),
    )
    assert all(not replace(packed, manifest=forgery).verify() for forgery in accounting_forgeries)

    different_block = ContextBlock(ContextSourceKind.ARTIFACT, required.artifact_id, b"tampered")
    assert not PackedContext((different_block,), packed.manifest).verify()


def test_context_packer_refuses_invalid_required_inputs_without_partial_context() -> None:
    packer = ContextPacker()
    empty = EvidenceSet.from_records()
    no_obligations = ContextObligations.create()

    with pytest.raises(ValueError, match="caps cannot be negative"):
        packer.pack(empty, no_obligations, ContextBudget(-1, 0), as_of_ms=NOW)
    with pytest.raises(ValueError, match="caps cannot be negative"):
        packer.pack(empty, no_obligations, ContextBudget(0, -1), as_of_ms=NOW)
    with pytest.raises(ValueError, match="as_of_ms cannot be negative"):
        packer.pack(empty, no_obligations, ContextBudget(0, 0), as_of_ms=-1)
    with pytest.raises(ValueError, match="artifact_id is required"):
        packer.pack(
            empty,
            no_obligations,
            ContextBudget(0, 0),
            as_of_ms=NOW,
            optional_artifacts=(OptionalArtifact("", 1),),
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        packer.pack(
            empty,
            no_obligations,
            ContextBudget(0, 0),
            as_of_ms=NOW,
            optional_artifacts=(OptionalArtifact(MISSING_ADDRESS, -1),),
        )

    valid = artifact(b"valid")
    invalid = replace(artifact(b"invalid"), payload=b"tampered")
    stale = artifact(b"stale", fresh_until_ms=NOW - 1)
    required_cases = (
        (empty, MISSING_ADDRESS, "missing"),
        (EvidenceSet.from_records((invalid,)), invalid.artifact_id, "integrity"),
        (EvidenceSet.from_records((stale,)), stale.artifact_id, "stale"),
    )
    for evidence, required_ref, expected_reason in required_cases:
        result = packer.pack(
            evidence,
            ContextObligations.create(required_artifacts=(required_ref,)),
            ContextBudget(100_000, 100_000),
            as_of_ms=NOW,
        )
        assert result.manifest.status is PackingStatus.REFUSED
        assert not result.blocks
        assert expected_reason in result.manifest.refusal_reasons[0]
        assert result.verify()

    optional = artifact(b"optional")
    mixed = packer.pack(
        EvidenceSet.from_records((valid, optional)),
        ContextObligations.create(
            required_artifacts=(valid.artifact_id, MISSING_ADDRESS),
        ),
        ContextBudget(100_000, 100_000),
        as_of_ms=NOW,
        optional_artifacts=(OptionalArtifact(optional.artifact_id, 5),),
    )
    reasons = {entry.reason for entry in mixed.manifest.excluded}
    assert mixed.manifest.status is PackingStatus.REFUSED
    assert "context refused because another mandatory obligation failed" in reasons
    assert "optional selection not evaluated after refusal" in reasons


def test_context_packer_tracks_claim_evidence_and_every_optional_exclusion() -> None:
    required = artifact(b"required")
    supported = claim("supported", (required.artifact_id,))
    evidence = EvidenceSet.from_records((required,), (supported,))
    packed_claim = ContextPacker().pack(
        evidence,
        ContextObligations.create(required_claims=(supported.claim_id,)),
        ContextBudget(100_000, 100_000),
        as_of_ms=NOW,
    )
    assert packed_claim.verify()
    assert {block.source_kind for block in packed_claim.blocks} == {
        ContextSourceKind.ARTIFACT,
        ContextSourceKind.CLAIM,
    }

    missing_claim = ContextPacker().pack(
        evidence,
        ContextObligations.create(required_claims=("absent-claim",)),
        ContextBudget(100_000, 100_000),
        as_of_ms=NOW,
    )
    assert missing_claim.manifest.status is PackingStatus.REFUSED
    assert "required claim is missing" in missing_claim.manifest.refusal_reasons[0]

    invalid = replace(artifact(b"invalid"), payload=b"tampered")
    stale = artifact(b"stale", fresh_until_ms=NOW - 1)
    selected = artifact(b"selected")
    optional_evidence = EvidenceSet.from_records((required, invalid, stale, selected))
    options = (
        OptionalArtifact(required.artifact_id, 1),
        OptionalArtifact(required.artifact_id, 9),
        OptionalArtifact(MISSING_ADDRESS, 2),
        OptionalArtifact(invalid.artifact_id, 3),
        OptionalArtifact(stale.artifact_id, 4),
        OptionalArtifact(selected.artifact_id, 5),
    )
    result = ContextPacker().pack(
        optional_evidence,
        ContextObligations.create(required_artifacts=(required.artifact_id,)),
        ContextBudget(100_000, 100_000),
        as_of_ms=NOW,
        optional_artifacts=options,
    )
    exclusion_reasons = {entry.source_ref: entry.reason for entry in result.manifest.excluded}
    assert result.verify()
    assert result.manifest.loss_report.total_optional_value_units == 23
    assert result.manifest.loss_report.included_optional_value_units == 5
    assert "already included as mandatory" in exclusion_reasons[required.artifact_id]
    assert "missing" in exclusion_reasons[MISSING_ADDRESS]
    assert "integrity" in exclusion_reasons[invalid.artifact_id]
    assert "stale" in exclusion_reasons[stale.artifact_id]
