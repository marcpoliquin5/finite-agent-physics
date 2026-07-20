"""Immutable artifacts and evidence-backed claims.

Artifact and claim digests are integrity checks, not signatures or trust anchors.
Callers remain responsible for authenticating producers and enforcing access policy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .serialization import content_digest


ARTIFACT_SCHEMA_VERSION = "agent-physics-artifact/v1"
CLAIM_SCHEMA_VERSION = "agent-physics-claim/v1"


class Sensitivity(str, Enum):
    """A portable data-classification label; enforcement belongs to the runtime."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ClaimStatus(str, Enum):
    """The producer's declared status for a claim."""

    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    RETRACTED = "retracted"


class ClaimAssessmentStatus(str, Enum):
    """A status recalculated from the evidence set at a point in time."""

    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    RETRACTED = "retracted"
    INVALID = "invalid"
    MISSING = "missing"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _address(digest: str) -> str:
    return f"sha256:{digest}"


def _validate_timestamp(created_at_ms: int, fresh_until_ms: int | None) -> None:
    if created_at_ms < 0:
        raise ValueError("created_at_ms cannot be negative")
    if fresh_until_ms is not None and fresh_until_ms < created_at_ms:
        raise ValueError("fresh_until_ms cannot precede created_at_ms")


@dataclass(frozen=True, slots=True)
class Artifact:
    """A byte-exact, typed, content-addressed artifact.

    The address commits to both the payload and its immutable metadata. The payload
    is kept as bytes so re-encoding cannot silently change what was evidenced.
    """

    artifact_id: str
    schema: str
    schema_version: str
    media_type: str
    producer: str
    parents: tuple[str, ...]
    sensitivity: Sensitivity
    created_at_ms: int
    fresh_until_ms: int | None
    payload: bytes
    payload_sha256: str

    @classmethod
    def create(
        cls,
        payload: bytes,
        *,
        schema: str,
        schema_version: str,
        media_type: str,
        producer: str,
        parents: Iterable[str] = (),
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        created_at_ms: int,
        fresh_until_ms: int | None = None,
    ) -> Artifact:
        """Construct a normalized artifact and derive its immutable address."""

        if not isinstance(payload, bytes):
            raise TypeError("artifact payload must be bytes")
        if not all((schema, schema_version, media_type, producer)):
            raise ValueError("schema, schema_version, media_type, and producer are required")
        _validate_timestamp(created_at_ms, fresh_until_ms)
        normalized_parents = tuple(sorted(set(parents)))
        if any(not parent.startswith("sha256:") for parent in normalized_parents):
            raise ValueError("parent references must be sha256 addresses")
        payload_hash = _sha256_bytes(payload)
        material = cls._material(
            schema=schema,
            schema_version=schema_version,
            media_type=media_type,
            producer=producer,
            parents=normalized_parents,
            sensitivity=sensitivity,
            created_at_ms=created_at_ms,
            fresh_until_ms=fresh_until_ms,
            payload_sha256=payload_hash,
            payload_size=len(payload),
        )
        return cls(
            artifact_id=_address(content_digest(material)),
            schema=schema,
            schema_version=schema_version,
            media_type=media_type,
            producer=producer,
            parents=normalized_parents,
            sensitivity=sensitivity,
            created_at_ms=created_at_ms,
            fresh_until_ms=fresh_until_ms,
            payload=bytes(payload),
            payload_sha256=payload_hash,
        )

    @staticmethod
    def _material(
        *,
        schema: str,
        schema_version: str,
        media_type: str,
        producer: str,
        parents: tuple[str, ...],
        sensitivity: Sensitivity,
        created_at_ms: int,
        fresh_until_ms: int | None,
        payload_sha256: str,
        payload_size: int,
    ) -> dict[str, object]:
        return {
            "record_schema_version": ARTIFACT_SCHEMA_VERSION,
            "schema": schema,
            "schema_version": schema_version,
            "media_type": media_type,
            "producer": producer,
            "parents": parents,
            "sensitivity": sensitivity,
            "created_at_ms": created_at_ms,
            "fresh_until_ms": fresh_until_ms,
            "payload_sha256": payload_sha256,
            "payload_size": payload_size,
        }

    def verify(self) -> bool:
        """Recalculate both the byte digest and the metadata-bound address."""

        try:
            _validate_timestamp(self.created_at_ms, self.fresh_until_ms)
        except ValueError:
            return False
        if not all((self.schema, self.schema_version, self.media_type, self.producer)):
            return False
        if tuple(sorted(set(self.parents))) != self.parents:
            return False
        if any(not parent.startswith("sha256:") for parent in self.parents):
            return False
        recalculated_payload = _sha256_bytes(self.payload)
        if recalculated_payload != self.payload_sha256:
            return False
        material = self._material(
            schema=self.schema,
            schema_version=self.schema_version,
            media_type=self.media_type,
            producer=self.producer,
            parents=self.parents,
            sensitivity=self.sensitivity,
            created_at_ms=self.created_at_ms,
            fresh_until_ms=self.fresh_until_ms,
            payload_sha256=recalculated_payload,
            payload_size=len(self.payload),
        )
        return self.artifact_id == _address(content_digest(material))

    def is_fresh(self, as_of_ms: int) -> bool:
        """Return whether this artifact existed and had not expired at ``as_of_ms``."""

        if as_of_ms < self.created_at_ms:
            return False
        return self.fresh_until_ms is None or as_of_ms <= self.fresh_until_ms


@dataclass(frozen=True, slots=True)
class Claim:
    """A digest-protected assertion with explicit evidence and conflicts."""

    claim_id: str
    statement: str
    evidence_refs: tuple[str, ...]
    status: ClaimStatus
    contradicts: tuple[str, ...]
    producer: str
    created_at_ms: int
    claim_digest: str

    @classmethod
    def create(
        cls,
        claim_id: str,
        statement: str,
        *,
        evidence_refs: Iterable[str],
        status: ClaimStatus,
        producer: str,
        created_at_ms: int,
        contradicts: Iterable[str] = (),
    ) -> Claim:
        if not all((claim_id, statement, producer)):
            raise ValueError("claim_id, statement, and producer are required")
        if created_at_ms < 0:
            raise ValueError("created_at_ms cannot be negative")
        normalized_evidence = tuple(sorted(set(evidence_refs)))
        normalized_conflicts = tuple(sorted(set(contradicts)))
        if claim_id in normalized_conflicts:
            raise ValueError("a claim cannot contradict itself")
        if any(not ref.startswith("sha256:") for ref in normalized_evidence):
            raise ValueError("evidence references must be sha256 addresses")
        material = cls._material(
            claim_id=claim_id,
            statement=statement,
            evidence_refs=normalized_evidence,
            status=status,
            contradicts=normalized_conflicts,
            producer=producer,
            created_at_ms=created_at_ms,
        )
        return cls(
            claim_id=claim_id,
            statement=statement,
            evidence_refs=normalized_evidence,
            status=status,
            contradicts=normalized_conflicts,
            producer=producer,
            created_at_ms=created_at_ms,
            claim_digest=content_digest(material),
        )

    @staticmethod
    def _material(
        *,
        claim_id: str,
        statement: str,
        evidence_refs: tuple[str, ...],
        status: ClaimStatus,
        contradicts: tuple[str, ...],
        producer: str,
        created_at_ms: int,
    ) -> dict[str, object]:
        return {
            "record_schema_version": CLAIM_SCHEMA_VERSION,
            "claim_id": claim_id,
            "statement": statement,
            "evidence_refs": evidence_refs,
            "status": status,
            "contradicts": contradicts,
            "producer": producer,
            "created_at_ms": created_at_ms,
        }

    def verify(self) -> bool:
        if not all((self.claim_id, self.statement, self.producer)) or self.created_at_ms < 0:
            return False
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            return False
        if tuple(sorted(set(self.contradicts))) != self.contradicts:
            return False
        if self.claim_id in self.contradicts:
            return False
        if any(not ref.startswith("sha256:") for ref in self.evidence_refs):
            return False
        material = self._material(
            claim_id=self.claim_id,
            statement=self.statement,
            evidence_refs=self.evidence_refs,
            status=self.status,
            contradicts=self.contradicts,
            producer=self.producer,
            created_at_ms=self.created_at_ms,
        )
        return self.claim_digest == content_digest(material)


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    claim_id: str
    status: ClaimAssessmentStatus
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceSet:
    """A deterministic collection that recalculates claim support fail-closed."""

    artifacts: tuple[Artifact, ...]
    claims: tuple[Claim, ...]

    @classmethod
    def from_records(
        cls,
        artifacts: Iterable[Artifact] = (),
        claims: Iterable[Claim] = (),
    ) -> EvidenceSet:
        ordered_artifacts = tuple(sorted(artifacts, key=lambda item: item.artifact_id))
        ordered_claims = tuple(sorted(claims, key=lambda item: item.claim_id))
        artifact_ids = [item.artifact_id for item in ordered_artifacts]
        claim_ids = [item.claim_id for item in ordered_claims]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("evidence set contains duplicate artifact IDs")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("evidence set contains duplicate claim IDs")
        return cls(ordered_artifacts, ordered_claims)

    def artifact(self, artifact_id: str) -> Artifact | None:
        return next((item for item in self.artifacts if item.artifact_id == artifact_id), None)

    def claim(self, claim_id: str) -> Claim | None:
        return next((item for item in self.claims if item.claim_id == claim_id), None)

    def _base_assessment(self, claim: Claim, as_of_ms: int) -> ClaimAssessment:
        if not claim.verify():
            return ClaimAssessment(
                claim.claim_id,
                ClaimAssessmentStatus.INVALID,
                ("claim digest or structure is invalid",),
                claim.evidence_refs,
            )
        if claim.created_at_ms > as_of_ms:
            return ClaimAssessment(
                claim.claim_id,
                ClaimAssessmentStatus.INVALID,
                ("claim did not exist at the requested time",),
                claim.evidence_refs,
            )
        declared = {
            ClaimStatus.UNVERIFIED: ClaimAssessmentStatus.UNVERIFIED,
            ClaimStatus.STALE: ClaimAssessmentStatus.STALE,
            ClaimStatus.CONTRADICTED: ClaimAssessmentStatus.CONTRADICTED,
            ClaimStatus.RETRACTED: ClaimAssessmentStatus.RETRACTED,
        }
        if claim.status in declared:
            return ClaimAssessment(
                claim.claim_id,
                declared[claim.status],
                (f"producer declared claim {claim.status.value}",),
                claim.evidence_refs,
            )
        if not claim.evidence_refs:
            return ClaimAssessment(
                claim.claim_id,
                ClaimAssessmentStatus.UNVERIFIED,
                ("supported claims require at least one evidence reference",),
            )
        missing: list[str] = []
        invalid: list[str] = []
        stale: list[str] = []
        for ref in claim.evidence_refs:
            artifact = self.artifact(ref)
            if artifact is None:
                missing.append(ref)
            elif not artifact.verify():
                invalid.append(ref)
            elif not artifact.is_fresh(as_of_ms):
                stale.append(ref)
        if missing:
            return ClaimAssessment(
                claim.claim_id,
                ClaimAssessmentStatus.UNVERIFIED,
                tuple(f"missing evidence: {ref}" for ref in missing),
                claim.evidence_refs,
            )
        if invalid:
            return ClaimAssessment(
                claim.claim_id,
                ClaimAssessmentStatus.INVALID,
                tuple(f"invalid evidence: {ref}" for ref in invalid),
                claim.evidence_refs,
            )
        if stale:
            return ClaimAssessment(
                claim.claim_id,
                ClaimAssessmentStatus.STALE,
                tuple(f"stale evidence: {ref}" for ref in stale),
                claim.evidence_refs,
            )
        return ClaimAssessment(
            claim.claim_id,
            ClaimAssessmentStatus.SUPPORTED,
            ("all referenced evidence is valid and fresh",),
            claim.evidence_refs,
        )

    def assess_claim(self, claim_id: str, as_of_ms: int) -> ClaimAssessment:
        """Assess integrity, freshness, evidence presence, and explicit conflicts."""

        claim = self.claim(claim_id)
        if claim is None:
            return ClaimAssessment(
                claim_id,
                ClaimAssessmentStatus.MISSING,
                ("claim is absent from the evidence set",),
            )
        base = self._base_assessment(claim, as_of_ms)
        if base.status is not ClaimAssessmentStatus.SUPPORTED:
            return base
        conflicting: list[str] = []
        for other in self.claims:
            if other.claim_id == claim_id:
                continue
            explicitly_conflicts = (
                other.claim_id in claim.contradicts or claim_id in other.contradicts
            )
            if not explicitly_conflicts:
                continue
            other_base = self._base_assessment(other, as_of_ms)
            if other_base.status is ClaimAssessmentStatus.SUPPORTED:
                conflicting.append(other.claim_id)
        if conflicting:
            return ClaimAssessment(
                claim_id,
                ClaimAssessmentStatus.CONTRADICTED,
                tuple(f"conflicts with supported claim: {item}" for item in conflicting),
                claim.evidence_refs,
            )
        return base
