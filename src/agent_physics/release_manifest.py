"""Fail-closed FINITE release-evidence manifest validation.

The manifest is deliberately smaller than a general provenance format.  It is a strict
release boundary for the exact capability, integrated-proof, and release-gate sets in the
v5 contract.  Unknown fields and ambiguous Python/JSON values are rejected rather than
preserved or coerced.

This module validates metadata and, for a release-ready manifest, requires the caller to
provide every referenced artifact's bytes so the declared length and SHA-256 can be checked.
It never fetches a URI, verifies a third-party signature, or decides whether the human content
of a Bob/provider attestation is truthful; those are separate trust and review operations.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


SCHEMA_VERSION = "finite.evidence-manifest/v1"
CAPABILITY_IDS = tuple(
    [f"M{index:02d}" for index in range(1, 38)] + [f"S{index:02d}" for index in range(1, 26)]
)
INTEGRATED_PROOF_IDS = tuple(f"R{index:02d}" for index in range(1, 9))
RELEASE_GATE_IDS = tuple(f"V5-{index:02d}" for index in range(1, 10))
REQUIRED_EXTERNAL_KINDS = frozenset(
    {
        "github",
        "bob",
        "watsonx",
        "eligibility",
        "skillsbuild",
        "deployment",
        "video",
        "submission",
    }
)

_CAPABILITY_STATUSES = frozenset({"pass", "partial", "absent", "blocked"})
_PROOF_STATUSES = _CAPABILITY_STATUSES
_GATE_STATUSES = frozenset({"pass", "fail", "blocked"})
_SCOPES = frozenset({"local", "simulation", "live", "distributed"})
_DECISIONS = frozenset({"pass", "fail", "blocked"})
_CLASSIFICATIONS = frozenset(
    {
        "local",
        "fixture",
        "simulated",
        "live",
        "genuine_external",
        "human_attested_private",
    }
)
_EXTERNAL_CLASSIFICATIONS = frozenset(
    {"fixture", "simulated", "genuine_external", "human_attested_private"}
)
_GENUINE_EXTERNAL_CLASSIFICATIONS = frozenset({"genuine_external", "human_attested_private"})
_PUBLIC_EXTERNAL_KINDS = frozenset(
    {"github", "bob", "watsonx", "deployment", "video", "submission"}
)
_PUBLIC_AVAILABILITY_KINDS = frozenset({"github", "deployment", "video", "submission"})
_JUDGE_AVAILABILITY_KINDS = frozenset({"bob", "watsonx"})
_AVAILABILITY = frozenset({"public", "available-to-judges", "private-attested"})
_MAX_FRESHNESS_SECONDS = 31 * 24 * 60 * 60
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ARTIFACT_ID_RE = re.compile(r"^artifact:[a-z0-9][a-z0-9._-]*$")
_ATTESTATION_ID_RE = re.compile(r"^attestation:[a-z0-9][a-z0-9._-]*$")
_RUN_ID_RE = re.compile(r"^run:[A-Za-z0-9][A-Za-z0-9._:-]*$")

_ROOT_FIELDS = {
    "schema_version",
    "release",
    "source",
    "capabilities",
    "integrated_proofs",
    "release_gates",
    "artifacts",
    "external_attestations",
}
_RELEASE_FIELDS = {
    "name",
    "version",
    "decision",
    "ready",
    "generated_at",
    "evidence_max_age_seconds",
}
_SOURCE_FIELDS = {
    "repository",
    "commit",
    "tree_sha256",
    "tag",
    "dirty",
    "program_sha256",
    "release_contract_sha256",
}
_CAPABILITY_FIELDS = {
    "id",
    "gate_text_sha256",
    "status",
    "scope",
    "source_commit",
    "evidence_refs",
    "evidence_digest",
    "test_refs",
    "claim_ids",
    "limitations",
}
_PROOF_FIELDS = {
    "id",
    "gate_text_sha256",
    "status",
    "scope",
    "source_commit",
    "evidence_refs",
    "evidence_digest",
    "run_ids",
    "limitations",
}
_GATE_FIELDS = {
    "id",
    "status",
    "scope",
    "source_commit",
    "evidence_refs",
    "evidence_digest",
    "validator",
    "limitations",
}
_ARTIFACT_FIELDS = {
    "id",
    "path_or_uri",
    "media_type",
    "sha256",
    "bytes",
    "produced_by",
    "exit_code",
    "observed_at",
    "source_commit",
    "classification",
    "contains_secrets",
}
_ATTESTATION_FIELDS = {
    "id",
    "kind",
    "classification",
    "owner",
    "observed_at",
    "redacted_artifact_ref",
    "original_sha256",
    "reviewed_by",
    "availability",
    "source_commit",
}
_SEAL_FIELDS = {"algorithm", "manifest_sha256"}

_EXTERNAL_BINDINGS: Mapping[str, tuple[str, str]] = {
    "github": ("release_gate", "V5-02"),
    "bob": ("proof", "R01"),
    "watsonx": ("proof", "R01"),
    "eligibility": ("release_gate", "V5-09"),
    "skillsbuild": ("release_gate", "V5-09"),
    "deployment": ("proof", "R08"),
    "video": ("release_gate", "V5-09"),
    "submission": ("release_gate", "V5-09"),
}


class ReleaseManifestError(ValueError):
    """Raised when release evidence is ambiguous, incomplete, stale, or tampered."""


@dataclass(frozen=True, slots=True)
class ValidatedReleaseManifest:
    """Immutable canonical result returned by :func:`validate_release_manifest`."""

    canonical_json: str
    manifest_sha256: str
    source_commit: str
    decision: str
    release_ready: bool
    artifact_payloads_verified: bool

    def to_python(self) -> dict[str, Any]:
        """Return a detached JSON-compatible copy of the sealed document."""

        value = json.loads(self.canonical_json)
        if type(value) is not dict:  # pragma: no cover - construction invariant
            raise RuntimeError("canonical release manifest is not an object")
        return value


@dataclass(frozen=True, slots=True)
class _ValidatedParts:
    source_commit: str
    decision: str
    release_ready: bool
    artifacts: Mapping[str, Mapping[str, Any]]
    payloads_verified: bool


def compute_evidence_digest(
    *,
    record_id: str,
    status: str,
    scope: str,
    source_commit: str,
    evidence_refs: Sequence[str],
    artifact_digests: Mapping[str, str],
    artifact_classifications: Mapping[str, str],
) -> str:
    """Bind one gate record to its exact commit and declared evidence artifacts."""

    if type(record_id) is not str or not record_id:
        raise ReleaseManifestError("record_id must be a non-empty string")
    if type(status) is not str or not status:
        raise ReleaseManifestError("status must be a non-empty string")
    if type(scope) is not str or scope not in _SCOPES:
        raise ReleaseManifestError("scope is unsupported")
    _require_commit(source_commit, "source_commit")
    if type(evidence_refs) not in (list, tuple):
        raise ReleaseManifestError("evidence_refs must be an array")

    refs = tuple(evidence_refs)
    _require_sorted_unique_strings(refs, "evidence_refs", allow_empty=True)
    evidence: list[dict[str, str]] = []
    for reference in refs:
        try:
            digest = artifact_digests[reference]
            classification = artifact_classifications[reference]
        except KeyError as exc:
            raise ReleaseManifestError(
                f"evidence reference {reference!r} has no declared artifact"
            ) from exc
        _require_sha256(digest, f"artifact digest for {reference}")
        if classification not in _CLASSIFICATIONS:
            raise ReleaseManifestError(f"artifact classification for {reference!r} is unsupported")
        evidence.append(
            {
                "artifact_id": reference,
                "classification": classification,
                "sha256": digest,
            }
        )

    material = {
        "schema_version": "finite.evidence-record/v1",
        "record_id": record_id,
        "status": status,
        "scope": scope,
        "source_commit": source_commit,
        "evidence": evidence,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def seal_release_manifest(document: dict[str, Any]) -> dict[str, Any]:
    """Validate and seal an unsealed manifest without claiming artifact-byte verification.

    The input must omit ``seal``.  A detached canonical copy is returned.  A subsequent
    :func:`validate_release_manifest` call requires artifact payloads when ``release.ready``
    is true.
    """

    if type(document) is not dict:
        raise ReleaseManifestError("$: expected an object")
    if "seal" in document:
        raise ReleaseManifestError("$: seal_release_manifest expects an unsealed document")
    _validate_document(document, require_seal=False, artifact_payloads=None)
    detached = _strict_json_copy(document)
    digest = _payload_digest(detached)
    detached["seal"] = {"algorithm": "sha256", "manifest_sha256": digest}
    return detached


def validate_release_manifest(
    source: dict[str, Any] | str | bytes,
    *,
    artifact_payloads: Mapping[str, bytes] | None = None,
) -> ValidatedReleaseManifest:
    """Parse, validate, and verify a sealed release manifest.

    JSON text uses duplicate-key rejection.  Python input must be an exact ``dict`` rather
    than a mapping proxy or subclass.  When ``release.ready`` is true, ``artifact_payloads``
    must contain exactly one byte payload for every declared artifact.
    """

    document = _parse_source(source)
    parts = _validate_document(
        document,
        require_seal=True,
        artifact_payloads=artifact_payloads,
    )
    seal = _object(
        document["seal"],
        "$.seal",
        allowed=_SEAL_FIELDS,
        required=_SEAL_FIELDS,
    )
    algorithm = _string(seal["algorithm"], "$.seal.algorithm")
    if algorithm != "sha256":
        raise ReleaseManifestError("$.seal.algorithm: expected 'sha256'")
    declared_digest = _sha256(seal["manifest_sha256"], "$.seal.manifest_sha256")
    actual_digest = _payload_digest(document)
    if not hmac.compare_digest(declared_digest, actual_digest):
        raise ReleaseManifestError("$.seal.manifest_sha256: manifest seal mismatch")

    canonical = _canonical_json(document)
    return ValidatedReleaseManifest(
        canonical_json=canonical,
        manifest_sha256=actual_digest,
        source_commit=parts.source_commit,
        decision=parts.decision,
        release_ready=parts.release_ready,
        artifact_payloads_verified=parts.payloads_verified,
    )


compile_release_manifest = validate_release_manifest


def _parse_source(source: dict[str, Any] | str | bytes) -> dict[str, Any]:
    if type(source) is dict:
        return source
    if type(source) is bytes:
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseManifestError("manifest bytes must be valid UTF-8") from exc
    elif type(source) is str:
        text = source
    else:
        raise ReleaseManifestError("manifest source must be an object, JSON text, or UTF-8 bytes")

    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ReleaseManifestError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ReleaseManifestError(f"invalid manifest JSON: {exc}") from exc
    if type(value) is not dict:
        raise ReleaseManifestError("$: expected an object")
    return value


def _validate_document(
    document: dict[str, Any],
    *,
    require_seal: bool,
    artifact_payloads: Mapping[str, bytes] | None,
) -> _ValidatedParts:
    root_fields = _ROOT_FIELDS | ({"seal"} if require_seal else set())
    root = _object(document, "$", allowed=root_fields, required=root_fields)
    schema_version = _string(root["schema_version"], "$.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ReleaseManifestError(
            f"$.schema_version: unsupported version {schema_version!r}; expected {SCHEMA_VERSION!r}"
        )

    release = _object(
        root["release"],
        "$.release",
        allowed=_RELEASE_FIELDS,
        required=_RELEASE_FIELDS,
    )
    if _string(release["name"], "$.release.name") != "FINITE":
        raise ReleaseManifestError("$.release.name: expected 'FINITE'")
    version = _nonempty_string(release["version"], "$.release.version")
    decision = _enum_string(release["decision"], "$.release.decision", _DECISIONS)
    ready = _boolean(release["ready"], "$.release.ready")
    generated_at = _timestamp(release["generated_at"], "$.release.generated_at")
    freshness_seconds = _integer(
        release["evidence_max_age_seconds"],
        "$.release.evidence_max_age_seconds",
    )
    if freshness_seconds <= 0 or freshness_seconds > _MAX_FRESHNESS_SECONDS:
        raise ReleaseManifestError(
            f"$.release.evidence_max_age_seconds: expected 1 through {_MAX_FRESHNESS_SECONDS}"
        )

    source = _object(
        root["source"],
        "$.source",
        allowed=_SOURCE_FIELDS,
        required=_SOURCE_FIELDS,
    )
    repository = _nonempty_string(source["repository"], "$.source.repository")
    _validate_repository(repository)
    source_commit = _commit(source["commit"], "$.source.commit")
    _sha256(source["tree_sha256"], "$.source.tree_sha256")
    tag = _nonempty_string(source["tag"], "$.source.tag")
    dirty = _boolean(source["dirty"], "$.source.dirty")
    _sha256(source["program_sha256"], "$.source.program_sha256")
    _sha256(source["release_contract_sha256"], "$.source.release_contract_sha256")

    artifacts = _validate_artifacts(
        root["artifacts"],
        source_commit=source_commit,
        generated_at=generated_at,
        freshness_seconds=freshness_seconds,
    )
    artifact_digests = {artifact_id: value["sha256"] for artifact_id, value in artifacts.items()}
    artifact_classifications = {
        artifact_id: value["classification"] for artifact_id, value in artifacts.items()
    }

    capabilities = _validate_gate_records(
        root["capabilities"],
        path="$.capabilities",
        expected_ids=CAPABILITY_IDS,
        allowed_fields=_CAPABILITY_FIELDS,
        allowed_statuses=_CAPABILITY_STATUSES,
        source_commit=source_commit,
        artifact_digests=artifact_digests,
        artifact_classifications=artifact_classifications,
        record_kind="capability",
    )
    proofs = _validate_gate_records(
        root["integrated_proofs"],
        path="$.integrated_proofs",
        expected_ids=INTEGRATED_PROOF_IDS,
        allowed_fields=_PROOF_FIELDS,
        allowed_statuses=_PROOF_STATUSES,
        source_commit=source_commit,
        artifact_digests=artifact_digests,
        artifact_classifications=artifact_classifications,
        record_kind="proof",
    )
    release_gates = _validate_gate_records(
        root["release_gates"],
        path="$.release_gates",
        expected_ids=RELEASE_GATE_IDS,
        allowed_fields=_GATE_FIELDS,
        allowed_statuses=_GATE_STATUSES,
        source_commit=source_commit,
        artifact_digests=artifact_digests,
        artifact_classifications=artifact_classifications,
        record_kind="release_gate",
    )
    attestations = _validate_attestations(
        root["external_attestations"],
        artifacts=artifacts,
        source_commit=source_commit,
        generated_at=generated_at,
        freshness_seconds=freshness_seconds,
    )

    all_program_pass = all(record["status"] == "pass" for record in capabilities.values())
    all_proofs_pass = all(record["status"] == "pass" for record in proofs.values())
    all_release_gates_pass = all(record["status"] == "pass" for record in release_gates.values())
    external_ready = _external_evidence_is_release_ready(
        attestations=attestations,
        proofs=proofs,
        release_gates=release_gates,
    )
    status_ready = all_program_pass and all_proofs_pass and all_release_gates_pass

    if decision == "pass" and not ready:
        raise ReleaseManifestError("$.release: decision 'pass' requires ready=true")
    if ready and decision != "pass":
        raise ReleaseManifestError("$.release: ready=true requires decision 'pass'")
    if ready and not status_ready:
        raise ReleaseManifestError(
            "$.release: ready=true requires every capability, proof, and release gate to pass"
        )
    if ready and not external_ready:
        raise ReleaseManifestError(
            "$.release: ready=true requires genuine evidence for every external gate"
        )
    if ready and (version != "5.0.0" or tag != "v5.0.0"):
        raise ReleaseManifestError(
            "$.release: ready=true requires version '5.0.0' and source tag 'v5.0.0'"
        )
    if ready and dirty:
        raise ReleaseManifestError("$.source.dirty: release-ready source must be clean")

    payloads_verified = _validate_artifact_payloads(
        artifacts,
        artifact_payloads,
        required=ready and require_seal,
    )
    return _ValidatedParts(
        source_commit=source_commit,
        decision=decision,
        release_ready=ready,
        artifacts=artifacts,
        payloads_verified=payloads_verified,
    )


def _validate_artifacts(
    value: Any,
    *,
    source_commit: str,
    generated_at: datetime,
    freshness_seconds: int,
) -> dict[str, Mapping[str, Any]]:
    items = _array(value, "$.artifacts")
    artifacts: dict[str, Mapping[str, Any]] = {}
    ordered_ids: list[str] = []
    for index, raw in enumerate(items):
        path = f"$.artifacts[{index}]"
        artifact = _object(
            raw,
            path,
            allowed=_ARTIFACT_FIELDS,
            required=_ARTIFACT_FIELDS,
        )
        artifact_id = _matching_string(artifact["id"], f"{path}.id", _ARTIFACT_ID_RE)
        if artifact_id in artifacts:
            raise ReleaseManifestError(f"{path}.id: duplicate artifact ID {artifact_id!r}")
        ordered_ids.append(artifact_id)
        _path_or_uri(artifact["path_or_uri"], f"{path}.path_or_uri")
        _media_type(artifact["media_type"], f"{path}.media_type")
        digest = _sha256(artifact["sha256"], f"{path}.sha256")
        byte_count = _integer(artifact["bytes"], f"{path}.bytes")
        if byte_count < 0:
            raise ReleaseManifestError(f"{path}.bytes: must be non-negative")
        _nonempty_string(artifact["produced_by"], f"{path}.produced_by")
        _integer(artifact["exit_code"], f"{path}.exit_code")
        observed_at = _timestamp(artifact["observed_at"], f"{path}.observed_at")
        _require_fresh(observed_at, generated_at, freshness_seconds, f"{path}.observed_at")
        record_commit = _commit(artifact["source_commit"], f"{path}.source_commit")
        if record_commit != source_commit:
            raise ReleaseManifestError(f"{path}.source_commit: does not match source commit")
        classification = _enum_string(
            artifact["classification"],
            f"{path}.classification",
            _CLASSIFICATIONS,
        )
        contains_secrets = _boolean(artifact["contains_secrets"], f"{path}.contains_secrets")
        if contains_secrets:
            raise ReleaseManifestError(f"{path}.contains_secrets: secrets are forbidden")
        if _integer(artifact["exit_code"], f"{path}.exit_code") != 0:
            raise ReleaseManifestError(f"{path}.exit_code: evidence producer did not pass")
        artifacts[artifact_id] = {
            "sha256": digest,
            "bytes": byte_count,
            "classification": classification,
        }
    if ordered_ids != sorted(ordered_ids):
        raise ReleaseManifestError("$.artifacts: records must be sorted by id")
    if not artifacts:
        raise ReleaseManifestError("$.artifacts: at least one evidence artifact is required")
    return artifacts


def _validate_gate_records(
    value: Any,
    *,
    path: str,
    expected_ids: tuple[str, ...],
    allowed_fields: set[str],
    allowed_statuses: frozenset[str],
    source_commit: str,
    artifact_digests: Mapping[str, str],
    artifact_classifications: Mapping[str, str],
    record_kind: str,
) -> dict[str, Mapping[str, Any]]:
    items = _array(value, path)
    records: dict[str, Mapping[str, Any]] = {}
    ordered_ids: list[str] = []
    for index, raw in enumerate(items):
        record_path = f"{path}[{index}]"
        record = _object(
            raw,
            record_path,
            allowed=allowed_fields,
            required=allowed_fields,
        )
        record_id = _nonempty_string(record["id"], f"{record_path}.id")
        if record_id in records:
            raise ReleaseManifestError(f"{record_path}.id: duplicate ID {record_id!r}")
        ordered_ids.append(record_id)
        status = _enum_string(record["status"], f"{record_path}.status", allowed_statuses)
        scope = _enum_string(record["scope"], f"{record_path}.scope", _SCOPES)
        record_commit = _commit(record["source_commit"], f"{record_path}.source_commit")
        if record_commit != source_commit:
            raise ReleaseManifestError(f"{record_path}.source_commit: does not match source commit")
        evidence_refs = _string_array(
            record["evidence_refs"],
            f"{record_path}.evidence_refs",
            sorted_unique=True,
            allow_empty=status != "pass",
        )
        for reference in evidence_refs:
            if reference not in artifact_digests:
                raise ReleaseManifestError(
                    f"{record_path}.evidence_refs: missing artifact {reference!r}"
                )
        if status == "pass" and not evidence_refs:  # defensive; allow_empty already rejects
            raise ReleaseManifestError(
                f"{record_path}.evidence_refs: pass records require evidence"
            )
        declared_evidence_digest = _sha256(
            record["evidence_digest"], f"{record_path}.evidence_digest"
        )
        calculated_evidence_digest = compute_evidence_digest(
            record_id=record_id,
            status=status,
            scope=scope,
            source_commit=source_commit,
            evidence_refs=evidence_refs,
            artifact_digests=artifact_digests,
            artifact_classifications=artifact_classifications,
        )
        if not hmac.compare_digest(declared_evidence_digest, calculated_evidence_digest):
            raise ReleaseManifestError(f"{record_path}.evidence_digest: evidence digest mismatch")
        _string_array(
            record["limitations"],
            f"{record_path}.limitations",
            sorted_unique=False,
            allow_empty=True,
        )

        if record_kind == "capability":
            _sha256(record["gate_text_sha256"], f"{record_path}.gate_text_sha256")
            test_refs = _string_array(
                record["test_refs"],
                f"{record_path}.test_refs",
                sorted_unique=True,
                allow_empty=status != "pass",
            )
            for test_ref in test_refs:
                _relative_path(test_ref, f"{record_path}.test_refs")
            _string_array(
                record["claim_ids"],
                f"{record_path}.claim_ids",
                sorted_unique=True,
                allow_empty=True,
            )
        elif record_kind == "proof":
            _sha256(record["gate_text_sha256"], f"{record_path}.gate_text_sha256")
            run_ids = _string_array(
                record["run_ids"],
                f"{record_path}.run_ids",
                sorted_unique=True,
                allow_empty=status != "pass",
            )
            for run_id in run_ids:
                if _RUN_ID_RE.fullmatch(run_id) is None:
                    raise ReleaseManifestError(
                        f"{record_path}.run_ids: malformed run ID {run_id!r}"
                    )
        elif record_kind == "release_gate":
            _nonempty_string(record["validator"], f"{record_path}.validator")
        else:  # pragma: no cover - internal call invariant
            raise RuntimeError(f"unknown record kind {record_kind!r}")

        records[record_id] = {
            "status": status,
            "scope": scope,
            "evidence_refs": tuple(evidence_refs),
        }

    if ordered_ids != list(expected_ids):
        duplicates = sorted(
            {record_id for record_id in ordered_ids if ordered_ids.count(record_id) > 1}
        )
        missing = sorted(set(expected_ids) - set(ordered_ids))
        unknown = sorted(set(ordered_ids) - set(expected_ids))
        details: list[str] = []
        if duplicates:
            details.append(f"duplicates={duplicates}")
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        if not details:
            details.append("records are not in canonical ID order")
        raise ReleaseManifestError(f"{path}: exact ID set/order required; " + "; ".join(details))
    return records


def _validate_attestations(
    value: Any,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    source_commit: str,
    generated_at: datetime,
    freshness_seconds: int,
) -> dict[str, Mapping[str, Any]]:
    items = _array(value, "$.external_attestations")
    attestations: dict[str, Mapping[str, Any]] = {}
    ordered_ids: list[str] = []
    for index, raw in enumerate(items):
        path = f"$.external_attestations[{index}]"
        attestation = _object(
            raw,
            path,
            allowed=_ATTESTATION_FIELDS,
            required=_ATTESTATION_FIELDS,
        )
        attestation_id = _matching_string(attestation["id"], f"{path}.id", _ATTESTATION_ID_RE)
        if attestation_id in attestations:
            raise ReleaseManifestError(f"{path}.id: duplicate attestation ID {attestation_id!r}")
        ordered_ids.append(attestation_id)
        kind = _enum_string(
            attestation["kind"],
            f"{path}.kind",
            REQUIRED_EXTERNAL_KINDS,
        )
        classification = _enum_string(
            attestation["classification"],
            f"{path}.classification",
            _EXTERNAL_CLASSIFICATIONS,
        )
        _nonempty_string(attestation["owner"], f"{path}.owner")
        observed_at = _timestamp(attestation["observed_at"], f"{path}.observed_at")
        _require_fresh(observed_at, generated_at, freshness_seconds, f"{path}.observed_at")
        artifact_ref = _matching_string(
            attestation["redacted_artifact_ref"],
            f"{path}.redacted_artifact_ref",
            _ARTIFACT_ID_RE,
        )
        if artifact_ref not in artifacts:
            raise ReleaseManifestError(
                f"{path}.redacted_artifact_ref: missing artifact {artifact_ref!r}"
            )
        if artifacts[artifact_ref]["classification"] != classification:
            raise ReleaseManifestError(
                f"{path}.classification: does not match redacted artifact classification"
            )
        _sha256(attestation["original_sha256"], f"{path}.original_sha256")
        _nonempty_string(attestation["reviewed_by"], f"{path}.reviewed_by")
        availability = _enum_string(
            attestation["availability"],
            f"{path}.availability",
            _AVAILABILITY,
        )
        record_commit = _commit(attestation["source_commit"], f"{path}.source_commit")
        if record_commit != source_commit:
            raise ReleaseManifestError(f"{path}.source_commit: does not match source commit")
        attestations[attestation_id] = {
            "kind": kind,
            "classification": classification,
            "artifact_ref": artifact_ref,
            "availability": availability,
        }
    if ordered_ids != sorted(ordered_ids):
        raise ReleaseManifestError("$.external_attestations: records must be sorted by id")
    return attestations


def _external_evidence_is_release_ready(
    *,
    attestations: Mapping[str, Mapping[str, Any]],
    proofs: Mapping[str, Mapping[str, Any]],
    release_gates: Mapping[str, Mapping[str, Any]],
) -> bool:
    by_kind: dict[str, list[Mapping[str, Any]]] = {kind: [] for kind in REQUIRED_EXTERNAL_KINDS}
    for attestation in attestations.values():
        by_kind[str(attestation["kind"])].append(attestation)

    for kind in REQUIRED_EXTERNAL_KINDS:
        genuine = [
            item
            for item in by_kind[kind]
            if item["classification"] in _GENUINE_EXTERNAL_CLASSIFICATIONS
        ]
        if not genuine:
            return False
        if kind in _PUBLIC_EXTERNAL_KINDS and any(
            item["classification"] != "genuine_external" for item in genuine
        ):
            genuine = [item for item in genuine if item["classification"] == "genuine_external"]
            if not genuine:
                return False
        if kind in _PUBLIC_AVAILABILITY_KINDS:
            genuine = [item for item in genuine if item["availability"] == "public"]
            if not genuine:
                return False
        elif kind in _JUDGE_AVAILABILITY_KINDS:
            genuine = [
                item
                for item in genuine
                if item["availability"] in {"public", "available-to-judges"}
            ]
            if not genuine:
                return False
        record_kind, record_id = _EXTERNAL_BINDINGS[kind]
        record = proofs[record_id] if record_kind == "proof" else release_gates[record_id]
        if not any(item["artifact_ref"] in record["evidence_refs"] for item in genuine):
            return False
    return True


def _validate_artifact_payloads(
    artifacts: Mapping[str, Mapping[str, Any]],
    payloads: Mapping[str, bytes] | None,
    *,
    required: bool,
) -> bool:
    if payloads is None:
        if required:
            raise ReleaseManifestError(
                "artifact_payloads: release-ready validation requires every artifact payload"
            )
        return False
    if not isinstance(payloads, Mapping):
        raise ReleaseManifestError("artifact_payloads: expected a mapping")
    non_string = [key for key in payloads if type(key) is not str]
    if non_string:
        raise ReleaseManifestError("artifact_payloads: keys must be strings")
    missing = sorted(set(artifacts) - set(payloads))
    unknown = sorted(set(payloads) - set(artifacts))
    if missing or unknown:
        raise ReleaseManifestError(
            f"artifact_payloads: exact artifact set required; missing={missing}; unknown={unknown}"
        )
    for artifact_id, artifact in artifacts.items():
        payload = payloads[artifact_id]
        if type(payload) is not bytes:
            raise ReleaseManifestError(f"artifact_payloads[{artifact_id!r}]: expected bytes")
        if len(payload) != artifact["bytes"]:
            raise ReleaseManifestError(
                f"artifact_payloads[{artifact_id!r}]: declared byte length mismatch"
            )
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, str(artifact["sha256"])):
            raise ReleaseManifestError(f"artifact_payloads[{artifact_id!r}]: SHA-256 mismatch")
    return True


def _payload_digest(document: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "seal"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _strict_json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value), object_pairs_hook=_unique_json_object)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseManifestError(f"manifest JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ReleaseManifestError(f"manifest JSON constant {value!r} is not supported")


def _object(
    value: Any,
    path: str,
    *,
    allowed: set[str],
    required: set[str],
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ReleaseManifestError(f"{path}: expected an object")
    non_string = [key for key in value if type(key) is not str]
    if non_string:
        raise ReleaseManifestError(f"{path}: object keys must be strings")
    keys = set(value)
    unknown = sorted(keys - allowed)
    if unknown:
        raise ReleaseManifestError(f"{path}: unknown fields {unknown}")
    missing = sorted(required - keys)
    if missing:
        raise ReleaseManifestError(f"{path}: missing required fields {missing}")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if type(value) is not list:
        raise ReleaseManifestError(f"{path}: expected an array")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str:
        raise ReleaseManifestError(f"{path}: expected a string")
    return value


def _nonempty_string(value: Any, path: str) -> str:
    result = _string(value, path)
    if not result or result != result.strip():
        raise ReleaseManifestError(f"{path}: expected a non-empty, trimmed string")
    return result


def _enum_string(value: Any, path: str, allowed: frozenset[str]) -> str:
    result = _string(value, path)
    if result not in allowed:
        raise ReleaseManifestError(f"{path}: unsupported value {result!r}")
    return result


def _matching_string(value: Any, path: str, pattern: re.Pattern[str]) -> str:
    result = _string(value, path)
    if pattern.fullmatch(result) is None:
        raise ReleaseManifestError(f"{path}: malformed value {result!r}")
    return result


def _integer(value: Any, path: str) -> int:
    if type(value) is not int:
        raise ReleaseManifestError(f"{path}: expected an integer")
    return value


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ReleaseManifestError(f"{path}: expected a boolean")
    return value


def _sha256(value: Any, path: str) -> str:
    result = _string(value, path)
    _require_sha256(result, path)
    return result


def _require_sha256(value: str, path: str) -> None:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ReleaseManifestError(f"{path}: expected 64 lowercase hexadecimal SHA-256")


def _commit(value: Any, path: str) -> str:
    result = _string(value, path)
    _require_commit(result, path)
    return result


def _require_commit(value: str, path: str) -> None:
    if type(value) is not str or _COMMIT_RE.fullmatch(value) is None:
        raise ReleaseManifestError(f"{path}: expected a 40-character lowercase commit ID")


def _timestamp(value: Any, path: str) -> datetime:
    text = _string(value, path)
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise ReleaseManifestError(f"{path}: expected RFC 3339 UTC seconds (YYYY-MM-DDTHH:MM:SSZ)")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ReleaseManifestError(f"{path}: invalid UTC timestamp") from exc


def _require_fresh(
    observed_at: datetime,
    generated_at: datetime,
    freshness_seconds: int,
    path: str,
) -> None:
    age = (generated_at - observed_at).total_seconds()
    if age < 0:
        raise ReleaseManifestError(f"{path}: evidence is dated after manifest generation")
    if age > freshness_seconds:
        raise ReleaseManifestError(f"{path}: stale evidence exceeds the declared freshness window")


def _string_array(
    value: Any,
    path: str,
    *,
    sorted_unique: bool,
    allow_empty: bool,
) -> list[str]:
    values = _array(value, path)
    result = [_nonempty_string(item, f"{path}[{index}]") for index, item in enumerate(values)]
    if not allow_empty and not result:
        raise ReleaseManifestError(f"{path}: at least one entry is required")
    if len(result) != len(set(result)):
        raise ReleaseManifestError(f"{path}: entries must be unique")
    if sorted_unique and result != sorted(result):
        raise ReleaseManifestError(f"{path}: entries must be sorted")
    return result


def _require_sorted_unique_strings(values: Sequence[Any], path: str, *, allow_empty: bool) -> None:
    if not allow_empty and not values:
        raise ReleaseManifestError(f"{path}: at least one entry is required")
    if any(type(value) is not str or not value for value in values):
        raise ReleaseManifestError(f"{path}: entries must be non-empty strings")
    if len(values) != len(set(values)) or list(values) != sorted(values):
        raise ReleaseManifestError(f"{path}: entries must be sorted and unique")


def _relative_path(value: str, path: str) -> None:
    if "\\" in value:
        raise ReleaseManifestError(f"{path}: paths must use forward slashes")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or value in {"", "."}:
        raise ReleaseManifestError(f"{path}: expected a normalized relative path")


def _path_or_uri(value: Any, path: str) -> str:
    result = _nonempty_string(value, path)
    if result.startswith("https://"):
        parsed = urlsplit(result)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ReleaseManifestError(f"{path}: malformed or credential-bearing HTTPS URI")
        return result
    if "://" in result:
        raise ReleaseManifestError(f"{path}: only HTTPS URIs are supported")
    _relative_path(result, path)
    return result


def _media_type(value: Any, path: str) -> str:
    result = _nonempty_string(value, path)
    if "/" not in result or any(character.isspace() for character in result):
        raise ReleaseManifestError(f"{path}: malformed media type")
    return result


def _validate_repository(repository: str) -> None:
    parsed = urlsplit(repository)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
    ):
        raise ReleaseManifestError(
            "$.source.repository: expected an HTTPS GitHub owner/repository URL"
        )
