"""Deterministic, fail-closed evidence context packing."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Iterable

from .artifacts import Artifact, Claim, ClaimAssessmentStatus, EvidenceSet
from .serialization import canonical_json, content_digest


CONTEXT_SCHEMA_VERSION = "agent-physics-context/v1"
MANIFEST_SCHEMA_VERSION = "agent-physics-context-manifest/v1"
TOKEN_ESTIMATOR_VERSION = "utf8-byte-upper-bound/v1"


class ContextSourceKind(str, Enum):
    ARTIFACT = "artifact"
    CLAIM = "claim"


class PackingStatus(str, Enum):
    PACKED = "packed"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_bytes: int
    max_tokens: int

    def validate(self) -> None:
        if self.max_bytes < 0 or self.max_tokens < 0:
            raise ValueError("context byte and token caps cannot be negative")


@dataclass(frozen=True, slots=True)
class ContextObligations:
    """Artifact and claim references that may never be silently dropped."""

    required_artifacts: tuple[str, ...] = ()
    required_claims: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        required_artifacts: Iterable[str] = (),
        required_claims: Iterable[str] = (),
    ) -> ContextObligations:
        return cls(
            tuple(sorted(set(required_artifacts))),
            tuple(sorted(set(required_claims))),
        )

    def normalized(self) -> ContextObligations:
        return self.create(
            required_artifacts=self.required_artifacts,
            required_claims=self.required_claims,
        )


@dataclass(frozen=True, slots=True)
class OptionalArtifact:
    """An artifact eligible for deterministic value-density selection."""

    artifact_id: str
    value_units: int

    def validate(self) -> None:
        if not self.artifact_id:
            raise ValueError("optional artifact_id is required")
        if self.value_units < 0:
            raise ValueError("optional artifact value_units cannot be negative")


@dataclass(frozen=True, slots=True)
class ContextBlock:
    """A framed block whose source bytes have data semantics only.

    The entire source document is base64 inside a runtime-controlled envelope. It
    can contain text such as "ignore previous instructions", but cannot inject an
    authority role or alter the envelope fields.
    """

    source_kind: ContextSourceKind
    source_ref: str
    data: bytes

    def wire_bytes(self) -> bytes:
        envelope = {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "authority": "none",
            "instruction_semantics": False,
            "content_semantics": "data-only",
            "data_encoding": "base64",
            "data": base64.b64encode(self.data).decode("ascii"),
        }
        return canonical_json(envelope).encode("utf-8")

    @property
    def block_digest(self) -> str:
        return hashlib.sha256(self.wire_bytes()).hexdigest()

    @property
    def block_id(self) -> str:
        return f"sha256:{self.block_digest}"

    @property
    def byte_size(self) -> int:
        return len(self.wire_bytes())

    @property
    def token_size(self) -> int:
        # The framed wire format is ASCII. One token per byte is a deterministic
        # upper bound for byte-level subword tokenizers, not a provider estimate.
        return max(1, self.byte_size)


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    source_kind: ContextSourceKind
    source_ref: str
    mandatory: bool
    included: bool
    reason: str
    block_id: str | None
    block_digest: str | None
    byte_size: int
    token_size: int
    value_units: int = 0


@dataclass(frozen=True, slots=True)
class LossReport:
    total_optional_value_units: int
    included_optional_value_units: int
    lost_optional_value_units: int
    excluded_optional_count: int
    unsatisfied_mandatory: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextManifest:
    schema_version: str
    status: PackingStatus
    as_of_ms: int
    byte_cap: int
    token_cap: int
    used_bytes: int
    used_tokens: int
    token_estimator: str
    included: tuple[ManifestEntry, ...]
    excluded: tuple[ManifestEntry, ...]
    loss_report: LossReport
    refusal_reasons: tuple[str, ...]
    manifest_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "as_of_ms": self.as_of_ms,
            "byte_cap": self.byte_cap,
            "token_cap": self.token_cap,
            "used_bytes": self.used_bytes,
            "used_tokens": self.used_tokens,
            "token_estimator": self.token_estimator,
            "included": self.included,
            "excluded": self.excluded,
            "loss_report": self.loss_report,
            "refusal_reasons": self.refusal_reasons,
        }

    def verify_digest(self) -> bool:
        return self.manifest_digest == content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class PackedContext:
    blocks: tuple[ContextBlock, ...]
    manifest: ContextManifest

    @property
    def wire_bytes(self) -> bytes:
        """Return replay-stable context bytes with an unambiguous separator."""

        return b"\n".join(block.wire_bytes() for block in self.blocks)

    def verify(self) -> bool:
        """Verify manifest integrity, block digests, accounting, and caps."""

        if not self.manifest.verify_digest():
            return False
        if self.manifest.status is PackingStatus.REFUSED:
            return not self.blocks and not self.manifest.included
        if self.manifest.refusal_reasons:
            return False
        entries = self.manifest.included
        if len(entries) != len(self.blocks):
            return False
        for entry, block in zip(entries, self.blocks, strict=True):
            if (
                not entry.included
                or entry.source_kind is not block.source_kind
                or entry.source_ref != block.source_ref
                or entry.block_id != block.block_id
                or entry.block_digest != block.block_digest
                or entry.byte_size != block.byte_size
                or entry.token_size != block.token_size
            ):
                return False
        used_bytes = sum(block.byte_size for block in self.blocks)
        used_tokens = sum(block.token_size for block in self.blocks)
        return (
            used_bytes == self.manifest.used_bytes
            and used_tokens == self.manifest.used_tokens
            and used_bytes <= self.manifest.byte_cap
            and used_tokens <= self.manifest.token_cap
        )


def _artifact_data(artifact: Artifact) -> bytes:
    """Serialize metadata and payload inside the data-only block boundary."""

    material = {
        "artifact_id": artifact.artifact_id,
        "schema": artifact.schema,
        "schema_version": artifact.schema_version,
        "media_type": artifact.media_type,
        "producer": artifact.producer,
        "parents": artifact.parents,
        "sensitivity": artifact.sensitivity,
        "created_at_ms": artifact.created_at_ms,
        "fresh_until_ms": artifact.fresh_until_ms,
        "payload_sha256": artifact.payload_sha256,
        "payload_encoding": "base64",
        "payload": base64.b64encode(artifact.payload).decode("ascii"),
    }
    return canonical_json(material).encode("utf-8")


def _claim_data(claim: Claim) -> bytes:
    material = {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "status": claim.status,
        "evidence_refs": claim.evidence_refs,
        "contradicts": claim.contradicts,
        "producer": claim.producer,
        "created_at_ms": claim.created_at_ms,
        "claim_digest": claim.claim_digest,
    }
    return canonical_json(material).encode("utf-8")


def _artifact_block(artifact: Artifact) -> ContextBlock:
    return ContextBlock(
        ContextSourceKind.ARTIFACT,
        artifact.artifact_id,
        _artifact_data(artifact),
    )


def _claim_block(claim: Claim) -> ContextBlock:
    return ContextBlock(ContextSourceKind.CLAIM, claim.claim_id, _claim_data(claim))


def _manifest_entry(
    block: ContextBlock,
    *,
    mandatory: bool,
    included: bool,
    reason: str,
    value_units: int = 0,
) -> ManifestEntry:
    return ManifestEntry(
        source_kind=block.source_kind,
        source_ref=block.source_ref,
        mandatory=mandatory,
        included=included,
        reason=reason,
        block_id=block.block_id,
        block_digest=block.block_digest,
        byte_size=block.byte_size,
        token_size=block.token_size,
        value_units=value_units,
    )


def _missing_entry(
    kind: ContextSourceKind,
    ref: str,
    *,
    mandatory: bool,
    reason: str,
    value_units: int = 0,
) -> ManifestEntry:
    return ManifestEntry(
        source_kind=kind,
        source_ref=ref,
        mandatory=mandatory,
        included=False,
        reason=reason,
        block_id=None,
        block_digest=None,
        byte_size=0,
        token_size=0,
        value_units=value_units,
    )


class ContextPacker:
    """Pack all required evidence or refuse; then greedily add optional value."""

    def pack(
        self,
        evidence: EvidenceSet,
        obligations: ContextObligations,
        budget: ContextBudget,
        *,
        as_of_ms: int,
        optional_artifacts: Iterable[OptionalArtifact] = (),
    ) -> PackedContext:
        budget.validate()
        if as_of_ms < 0:
            raise ValueError("as_of_ms cannot be negative")
        obligations = obligations.normalized()
        option_values: dict[str, int] = {}
        for option in optional_artifacts:
            option.validate()
            option_values[option.artifact_id] = max(
                option.value_units,
                option_values.get(option.artifact_id, 0),
            )

        mandatory_blocks: dict[tuple[ContextSourceKind, str], ContextBlock] = {}
        origins: dict[tuple[ContextSourceKind, str], set[str]] = {}
        invalid_required: list[ManifestEntry] = []
        refusal_reasons: list[str] = []

        def require_block(block: ContextBlock, origin: str) -> None:
            key = (block.source_kind, block.source_ref)
            mandatory_blocks[key] = block
            origins.setdefault(key, set()).add(origin)

        for artifact_id in obligations.required_artifacts:
            artifact = evidence.artifact(artifact_id)
            reason: str | None = None
            if artifact is None:
                reason = "required artifact is missing"
            elif not artifact.verify():
                reason = "required artifact failed integrity verification"
            elif not artifact.is_fresh(as_of_ms):
                reason = "required artifact is stale or not yet valid"
            if reason is not None:
                invalid_required.append(
                    _missing_entry(
                        ContextSourceKind.ARTIFACT,
                        artifact_id,
                        mandatory=True,
                        reason=reason,
                    )
                )
                refusal_reasons.append(f"artifact {artifact_id}: {reason}")
            else:
                assert artifact is not None
                require_block(_artifact_block(artifact), "required-artifact")

        for claim_id in obligations.required_claims:
            assessment = evidence.assess_claim(claim_id, as_of_ms)
            if assessment.status is not ClaimAssessmentStatus.SUPPORTED:
                detail = "; ".join(assessment.reasons)
                reason = f"required claim is {assessment.status.value}: {detail}"
                invalid_required.append(
                    _missing_entry(
                        ContextSourceKind.CLAIM,
                        claim_id,
                        mandatory=True,
                        reason=reason,
                    )
                )
                refusal_reasons.append(f"claim {claim_id}: {reason}")
                continue
            claim = evidence.claim(claim_id)
            assert claim is not None
            require_block(_claim_block(claim), "required-claim")
            for artifact_id in claim.evidence_refs:
                artifact = evidence.artifact(artifact_id)
                # A supported assessment guarantees these exact records are valid and fresh.
                assert artifact is not None
                require_block(_artifact_block(artifact), f"evidence-for:{claim_id}")

        ordered_mandatory = tuple(
            mandatory_blocks[key]
            for key in sorted(mandatory_blocks, key=lambda item: (item[0].value, item[1]))
        )
        total_optional_value = sum(option_values.values())
        if refusal_reasons:
            excluded = list(invalid_required)
            excluded.extend(
                _manifest_entry(
                    block,
                    mandatory=True,
                    included=False,
                    reason="context refused because another mandatory obligation failed",
                )
                for block in ordered_mandatory
            )
            excluded.extend(
                _missing_entry(
                    ContextSourceKind.ARTIFACT,
                    ref,
                    mandatory=False,
                    reason="optional selection not evaluated after refusal",
                    value_units=value,
                )
                for ref, value in sorted(option_values.items())
            )
            return self._refused(
                budget,
                as_of_ms,
                excluded,
                refusal_reasons,
                total_optional_value,
                obligations,
            )

        mandatory_bytes = sum(block.byte_size for block in ordered_mandatory)
        mandatory_tokens = sum(block.token_size for block in ordered_mandatory)
        if mandatory_bytes > budget.max_bytes or mandatory_tokens > budget.max_tokens:
            reason = (
                "mandatory context exceeds cap: "
                f"requires {mandatory_bytes} bytes/{mandatory_tokens} tokens, "
                f"cap is {budget.max_bytes} bytes/{budget.max_tokens} tokens"
            )
            excluded = [
                _manifest_entry(
                    block,
                    mandatory=True,
                    included=False,
                    reason="required context exceeds byte or token cap",
                )
                for block in ordered_mandatory
            ]
            excluded.extend(
                _missing_entry(
                    ContextSourceKind.ARTIFACT,
                    ref,
                    mandatory=False,
                    reason="optional selection not evaluated after refusal",
                    value_units=value,
                )
                for ref, value in sorted(option_values.items())
            )
            return self._refused(
                budget,
                as_of_ms,
                excluded,
                (reason,),
                total_optional_value,
                obligations,
            )

        blocks: list[ContextBlock] = list(ordered_mandatory)
        included: list[ManifestEntry] = []
        for block in ordered_mandatory:
            origin = ",".join(
                sorted(origins[(block.source_kind, block.source_ref)])
            )
            included.append(
                _manifest_entry(
                    block,
                    mandatory=True,
                    included=True,
                    reason=f"mandatory obligation: {origin}",
                )
            )
        excluded: list[ManifestEntry] = []
        optional_candidates: list[tuple[Fraction, int, ContextBlock]] = []
        mandatory_artifact_refs = {
            block.source_ref
            for block in ordered_mandatory
            if block.source_kind is ContextSourceKind.ARTIFACT
        }
        for ref, value in sorted(option_values.items()):
            if ref in mandatory_artifact_refs:
                excluded.append(
                    _missing_entry(
                        ContextSourceKind.ARTIFACT,
                        ref,
                        mandatory=False,
                        reason="optional artifact already included as mandatory",
                        value_units=value,
                    )
                )
                continue
            artifact = evidence.artifact(ref)
            reason = None
            if artifact is None:
                reason = "optional artifact is missing"
            elif not artifact.verify():
                reason = "optional artifact failed integrity verification"
            elif not artifact.is_fresh(as_of_ms):
                reason = "optional artifact is stale or not yet valid"
            if reason is not None:
                excluded.append(
                    _missing_entry(
                        ContextSourceKind.ARTIFACT,
                        ref,
                        mandatory=False,
                        reason=reason,
                        value_units=value,
                    )
                )
                continue
            assert artifact is not None
            block = _artifact_block(artifact)
            footprint = max(1, block.byte_size + 4 * block.token_size)
            optional_candidates.append((Fraction(value, footprint), value, block))

        optional_candidates.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                item[2].byte_size,
                item[2].token_size,
                item[2].source_ref,
            )
        )
        used_bytes = mandatory_bytes
        used_tokens = mandatory_tokens
        included_optional_value = 0
        for _density, value, block in optional_candidates:
            fits = (
                used_bytes + block.byte_size <= budget.max_bytes
                and used_tokens + block.token_size <= budget.max_tokens
            )
            if fits:
                blocks.append(block)
                included.append(
                    _manifest_entry(
                        block,
                        mandatory=False,
                        included=True,
                        reason="selected by deterministic optional value density",
                        value_units=value,
                    )
                )
                used_bytes += block.byte_size
                used_tokens += block.token_size
                included_optional_value += value
            else:
                excluded.append(
                    _manifest_entry(
                        block,
                        mandatory=False,
                        included=False,
                        reason="optional artifact does not fit remaining byte or token cap",
                        value_units=value,
                    )
                )

        excluded.sort(key=lambda item: (item.source_kind.value, item.source_ref, item.reason))
        loss = LossReport(
            total_optional_value_units=total_optional_value,
            included_optional_value_units=included_optional_value,
            lost_optional_value_units=total_optional_value - included_optional_value,
            excluded_optional_count=sum(not item.mandatory for item in excluded),
            unsatisfied_mandatory=(),
        )
        manifest = self._manifest(
            status=PackingStatus.PACKED,
            budget=budget,
            as_of_ms=as_of_ms,
            used_bytes=used_bytes,
            used_tokens=used_tokens,
            included=tuple(included),
            excluded=tuple(excluded),
            loss=loss,
            refusal_reasons=(),
        )
        result = PackedContext(tuple(blocks), manifest)
        if not result.verify():
            raise AssertionError("internal context accounting invariant failed")
        return result

    def _refused(
        self,
        budget: ContextBudget,
        as_of_ms: int,
        excluded: Iterable[ManifestEntry],
        reasons: Iterable[str],
        total_optional_value: int,
        obligations: ContextObligations,
    ) -> PackedContext:
        ordered_excluded = tuple(
            sorted(
                excluded,
                key=lambda item: (item.source_kind.value, item.source_ref, item.reason),
            )
        )
        unsatisfied = tuple(
            sorted(
                {f"artifact:{ref}" for ref in obligations.required_artifacts}
                | {f"claim:{ref}" for ref in obligations.required_claims}
            )
        )
        loss = LossReport(
            total_optional_value_units=total_optional_value,
            included_optional_value_units=0,
            lost_optional_value_units=total_optional_value,
            excluded_optional_count=sum(not item.mandatory for item in ordered_excluded),
            unsatisfied_mandatory=unsatisfied,
        )
        manifest = self._manifest(
            status=PackingStatus.REFUSED,
            budget=budget,
            as_of_ms=as_of_ms,
            used_bytes=0,
            used_tokens=0,
            included=(),
            excluded=ordered_excluded,
            loss=loss,
            refusal_reasons=tuple(sorted(set(reasons))),
        )
        return PackedContext((), manifest)

    @staticmethod
    def _manifest(
        *,
        status: PackingStatus,
        budget: ContextBudget,
        as_of_ms: int,
        used_bytes: int,
        used_tokens: int,
        included: tuple[ManifestEntry, ...],
        excluded: tuple[ManifestEntry, ...],
        loss: LossReport,
        refusal_reasons: tuple[str, ...],
    ) -> ContextManifest:
        unsigned = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "status": status,
            "as_of_ms": as_of_ms,
            "byte_cap": budget.max_bytes,
            "token_cap": budget.max_tokens,
            "used_bytes": used_bytes,
            "used_tokens": used_tokens,
            "token_estimator": TOKEN_ESTIMATOR_VERSION,
            "included": included,
            "excluded": excluded,
            "loss_report": loss,
            "refusal_reasons": refusal_reasons,
        }
        return ContextManifest(**unsigned, manifest_digest=content_digest(unsigned))
