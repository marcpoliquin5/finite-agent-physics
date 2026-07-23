"""Independent, fail-closed verification of one sealed FINITE run.

The verifier is deliberately downstream of execution.  It accepts a canonical
JSON-compatible evidence mapping and does not call, import, or trust the
scheduler, executor, provider adapters, or planner.  It reconstructs the facts
that can be proven from the sealed package: identity, event ordering, resource
conservation, artifact/claim causality, context obligations, approval/effect
uniqueness, and replay binding.

SHA-256 digests provide mutation detection, not producer authentication.  A
deployment that must defend against a malicious evidence producer must sign the
outer ``content_digest`` with a separately trusted key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .artifacts import Artifact, Claim, ClaimStatus, Sensitivity
from .contracts import EffectClass


WHOLE_RUN_SCHEMA_VERSION: Final[str] = "finite-whole-run-evidence/v1"
DIGEST_ALGORITHM: Final[str] = "sha256-canonical-json"
GENESIS_EVENT_DIGEST: Final[str] = "0" * 64

_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_WRITE_EFFECTS: Final[frozenset[str]] = frozenset(
    {
        EffectClass.IDEMPOTENT_WRITE.value,
        EffectClass.REVERSIBLE_WRITE.value,
        EffectClass.IRREVERSIBLE_WRITE.value,
    }
)
_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "run.started",
        "run.completed",
        "run.failed",
        "artifact.ingested",
        "task.attempt_started",
        "task.attempt_succeeded",
        "task.attempt_failed",
        "task.completed",
        "task.failed",
        "task.cancelled",
        "context.validated",
        "approval.granted",
        "effect.proposed",
        "effect.committed",
        "effect.aborted",
        "effect.compensated",
        "replay.recorded",
    }
)
_RUN_EVENTS: Final[frozenset[str]] = frozenset(
    {"run.started", "run.completed", "run.failed", "replay.recorded"}
)
_TASK_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "task.attempt_started",
        "task.attempt_succeeded",
        "task.attempt_failed",
        "task.completed",
        "task.failed",
        "task.cancelled",
        "context.validated",
    }
)
_EFFECT_EVENTS: Final[frozenset[str]] = frozenset(
    {"effect.proposed", "effect.committed", "effect.aborted", "effect.compensated"}
)
_TERMINAL_EFFECT_EVENT: Final[dict[str, str]] = {
    "committed": "effect.committed",
    "aborted": "effect.aborted",
    "compensated": "effect.compensated",
}
_CLAIM_STATUSES: Final[frozenset[str]] = frozenset(item.value for item in ClaimStatus)
_SENSITIVITIES: Final[frozenset[str]] = frozenset(item.value for item in Sensitivity)
_RESOURCE_FIELDS: Final[tuple[str, ...]] = (
    "tokens",
    "cost_microusd",
    "context_bytes",
)

_TOP_FIELDS = frozenset({"schema_version", "digest_algorithm", "content", "content_digest"})
_CONTENT_FIELDS = frozenset(
    {
        "identity",
        "envelope",
        "events",
        "artifacts",
        "claims",
        "context_obligations",
        "approvals",
        "effects",
        "replay_witness",
    }
)
_IDENTITY_FIELDS = frozenset({"run_id", "graph_digest", "manifest_digest", "envelope_digest"})
_ENVELOPE_FIELDS = frozenset(
    {"run_id", "deadline_ms", "resource_caps", "policy_digest", "envelope_digest"}
)
_EVENT_FIELDS = frozenset(
    {
        "run_id",
        "sequence",
        "event_id",
        "event_type",
        "task_id",
        "attempt",
        "occurred_at_ms",
        "previous_event_digest",
        "causes",
        "resources",
        "output_artifact_refs",
        "evidence_refs",
        "approval_id",
        "effect_id",
        "event_digest",
    }
)
_EVENT_RESOURCES_FIELDS = frozenset({"reserved", "actual", "released"})
_VECTOR_FIELDS = frozenset(_RESOURCE_FIELDS)
_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "schema",
        "schema_version",
        "media_type",
        "producer",
        "parents",
        "sensitivity",
        "created_at_ms",
        "fresh_until_ms",
        "payload_base64",
        "payload_sha256",
        "producer_event_digest",
        "record_digest",
    }
)
_CLAIM_FIELDS = frozenset(
    {
        "claim_id",
        "statement",
        "evidence_refs",
        "status",
        "contradicts",
        "producer",
        "created_at_ms",
        "produced_by_event_digest",
        "claim_digest",
        "record_digest",
    }
)
_CONTEXT_FIELDS = frozenset(
    {
        "obligation_id",
        "run_id",
        "task_id",
        "requirement_digest",
        "evidence_refs",
        "validation_event_digest",
        "satisfied",
        "record_digest",
    }
)
_APPROVAL_FIELDS = frozenset(
    {
        "approval_id",
        "run_id",
        "effect_id",
        "principal",
        "scope_digest",
        "grant_event_digest",
        "record_digest",
    }
)
_EFFECT_FIELDS = frozenset(
    {
        "effect_id",
        "run_id",
        "task_id",
        "effect_class",
        "action",
        "resource",
        "idempotency_key",
        "payload_artifact_ref",
        "proposed_event_digest",
        "approval_id",
        "terminal_state",
        "commit_event_digest",
        "terminal_event_digest",
        "record_digest",
    }
)
_REPLAY_FIELDS = frozenset(
    {
        "run_id",
        "graph_digest",
        "envelope_digest",
        "terminal_event_digest",
        "event_count",
        "event_chain_digest",
        "resource_totals",
        "output_set_digest",
        "artifact_set_digest",
        "claim_set_digest",
        "context_set_digest",
        "approval_set_digest",
        "effect_set_digest",
        "witness_digest",
    }
)


@dataclass(frozen=True, slots=True)
class WholeRunViolation:
    """One deterministic reason a package was rejected."""

    code: str
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class WholeRunVerificationReport:
    """Result of a verifier pass; ``passed`` is true only with zero violations."""

    passed: bool
    run_id: str | None
    evidence_digest: str | None
    violations: tuple[WholeRunViolation, ...]

    @property
    def violation_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.violations)


class _SchemaError(ValueError):
    def __init__(self, code: str, path: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.path = path
        self.detail = detail


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _SchemaError(
            "non_canonical_json",
            "$",
            "evidence must contain only finite canonical JSON values",
        ) from exc


def canonical_evidence_digest(value: object) -> str:
    """Return the digest algorithm used by every whole-run evidence seal."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def seal_evidence_record(
    record: Mapping[str, object], *, digest_field: str = "record_digest"
) -> dict[str, object]:
    """Defensively copy a record and append its canonical digest.

    This is a producer convenience, not verification.  The verifier always
    recalculates every seal from the received mapping.
    """

    if digest_field in record:
        raise ValueError(f"record already contains {digest_field!r}")
    copied = json.loads(_canonical_json(dict(record)))
    copied[digest_field] = canonical_evidence_digest(copied)
    return copied


def seal_whole_run_evidence(content: Mapping[str, object]) -> dict[str, object]:
    """Defensively copy already record-sealed content into an outer envelope."""

    copied = json.loads(_canonical_json(dict(content)))
    return {
        "schema_version": WHOLE_RUN_SCHEMA_VERSION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "content": copied,
        "content_digest": canonical_evidence_digest(copied),
    }


def _record(value: object, fields: frozenset[str], path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _SchemaError("invalid_type", path, "expected an object")
    keys = set(value)
    if any(type(key) is not str for key in keys):
        raise _SchemaError("invalid_type", path, "object keys must be strings")
    if keys != fields:
        unknown = sorted(keys - fields)
        missing = sorted(fields - keys)
        raise _SchemaError(
            "unknown_fields" if unknown else "missing_fields",
            path,
            f"unknown={unknown}, missing={missing}",
        )
    return value


def _list(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise _SchemaError("invalid_type", path, "expected an array")
    return value


def _string(value: object, path: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise _SchemaError("invalid_type", path, "expected a non-empty string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _SchemaError("invalid_type", path, f"expected an integer >= {minimum}")
    return value


def _optional_integer(value: object, path: str, *, minimum: int = 1) -> int | None:
    if value is None:
        return None
    return _integer(value, path, minimum=minimum)


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise _SchemaError("invalid_type", path, "expected a boolean")
    return value


def _digest(value: object, path: str, *, address: bool = False) -> str:
    text = _string(value, path)
    digest = text.removeprefix("sha256:") if address else text
    if (address and not text.startswith("sha256:")) or _SHA256.fullmatch(digest) is None:
        kind = "sha256:<lowercase-hex> address" if address else "lowercase SHA-256"
        raise _SchemaError("invalid_digest", path, f"expected {kind}")
    return text


def _nullable_digest(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _digest(value, path)


def _string_list(
    value: object,
    path: str,
    *,
    digest: bool = False,
    address: bool = False,
) -> list[str]:
    values = _list(value, path)
    result: list[str] = []
    for index, item in enumerate(values):
        item_path = f"{path}[{index}]"
        result.append(
            _digest(item, item_path, address=address)
            if digest or address
            else _string(item, item_path)
        )
    return result


def _vector(value: object, path: str) -> dict[str, int]:
    record = _record(value, _VECTOR_FIELDS, path)
    return {field: _integer(record[field], f"{path}.{field}") for field in _RESOURCE_FIELDS}


def _event_resources(value: object, path: str) -> dict[str, dict[str, int]]:
    record = _record(value, _EVENT_RESOURCES_FIELDS, path)
    return {
        field: _vector(record[field], f"{path}.{field}")
        for field in ("reserved", "actual", "released")
    }


def _check_record_digest(record: Mapping[str, object], digest_field: str, expected: object) -> bool:
    unsigned = {key: value for key, value in record.items() if key != digest_field}
    return expected == canonical_evidence_digest(unsigned)


def _validate_structure(sealed: Mapping[str, object]) -> Mapping[str, object]:
    top = _record(sealed, _TOP_FIELDS, "$")
    if top["schema_version"] != WHOLE_RUN_SCHEMA_VERSION:
        raise _SchemaError(
            "unsupported_schema",
            "$.schema_version",
            f"expected {WHOLE_RUN_SCHEMA_VERSION!r}",
        )
    if top["digest_algorithm"] != DIGEST_ALGORITHM:
        raise _SchemaError(
            "unsupported_digest_algorithm",
            "$.digest_algorithm",
            f"expected {DIGEST_ALGORITHM!r}",
        )
    content = _record(top["content"], _CONTENT_FIELDS, "$.content")
    _digest(top["content_digest"], "$.content_digest")

    identity = _record(content["identity"], _IDENTITY_FIELDS, "$.content.identity")
    _string(identity["run_id"], "$.content.identity.run_id")
    for field in ("graph_digest", "manifest_digest", "envelope_digest"):
        _digest(identity[field], f"$.content.identity.{field}")

    envelope = _record(content["envelope"], _ENVELOPE_FIELDS, "$.content.envelope")
    _string(envelope["run_id"], "$.content.envelope.run_id")
    _integer(envelope["deadline_ms"], "$.content.envelope.deadline_ms", minimum=1)
    _vector(envelope["resource_caps"], "$.content.envelope.resource_caps")
    _digest(envelope["policy_digest"], "$.content.envelope.policy_digest")
    _digest(envelope["envelope_digest"], "$.content.envelope.envelope_digest")

    events = _list(content["events"], "$.content.events")
    if not events:
        raise _SchemaError("missing_events", "$.content.events", "event chain cannot be empty")
    for index, item in enumerate(events):
        path = f"$.content.events[{index}]"
        event = _record(item, _EVENT_FIELDS, path)
        _string(event["run_id"], f"{path}.run_id")
        _integer(event["sequence"], f"{path}.sequence", minimum=1)
        _string(event["event_id"], f"{path}.event_id")
        event_type = _string(event["event_type"], f"{path}.event_type")
        if event_type not in _EVENT_TYPES:
            raise _SchemaError("unknown_event_type", f"{path}.event_type", event_type)
        _optional_string(event["task_id"], f"{path}.task_id")
        _optional_integer(event["attempt"], f"{path}.attempt")
        _integer(event["occurred_at_ms"], f"{path}.occurred_at_ms")
        _digest(event["previous_event_digest"], f"{path}.previous_event_digest")
        _string_list(event["causes"], f"{path}.causes", digest=True)
        _event_resources(event["resources"], f"{path}.resources")
        _string_list(
            event["output_artifact_refs"],
            f"{path}.output_artifact_refs",
            address=True,
        )
        _string_list(event["evidence_refs"], f"{path}.evidence_refs", address=True)
        _optional_string(event["approval_id"], f"{path}.approval_id")
        _optional_string(event["effect_id"], f"{path}.effect_id")
        _digest(event["event_digest"], f"{path}.event_digest")

    artifacts = _list(content["artifacts"], "$.content.artifacts")
    for index, item in enumerate(artifacts):
        path = f"$.content.artifacts[{index}]"
        artifact = _record(item, _ARTIFACT_FIELDS, path)
        _digest(artifact["artifact_id"], f"{path}.artifact_id", address=True)
        for field in ("schema", "schema_version", "media_type", "producer"):
            _string(artifact[field], f"{path}.{field}")
        _string_list(artifact["parents"], f"{path}.parents", address=True)
        sensitivity = _string(artifact["sensitivity"], f"{path}.sensitivity")
        if sensitivity not in _SENSITIVITIES:
            raise _SchemaError("invalid_sensitivity", f"{path}.sensitivity", sensitivity)
        _integer(artifact["created_at_ms"], f"{path}.created_at_ms")
        if artifact["fresh_until_ms"] is not None:
            _integer(artifact["fresh_until_ms"], f"{path}.fresh_until_ms")
        _string(artifact["payload_base64"], f"{path}.payload_base64", allow_empty=True)
        _digest(artifact["payload_sha256"], f"{path}.payload_sha256")
        _digest(artifact["producer_event_digest"], f"{path}.producer_event_digest")
        _digest(artifact["record_digest"], f"{path}.record_digest")

    claims = _list(content["claims"], "$.content.claims")
    for index, item in enumerate(claims):
        path = f"$.content.claims[{index}]"
        claim = _record(item, _CLAIM_FIELDS, path)
        for field in ("claim_id", "statement", "producer"):
            _string(claim[field], f"{path}.{field}")
        _string_list(claim["evidence_refs"], f"{path}.evidence_refs", address=True)
        status = _string(claim["status"], f"{path}.status")
        if status not in _CLAIM_STATUSES:
            raise _SchemaError("invalid_claim_status", f"{path}.status", status)
        _string_list(claim["contradicts"], f"{path}.contradicts")
        _integer(claim["created_at_ms"], f"{path}.created_at_ms")
        _digest(claim["produced_by_event_digest"], f"{path}.produced_by_event_digest")
        _digest(claim["claim_digest"], f"{path}.claim_digest")
        _digest(claim["record_digest"], f"{path}.record_digest")

    obligations = _list(content["context_obligations"], "$.content.context_obligations")
    for index, item in enumerate(obligations):
        path = f"$.content.context_obligations[{index}]"
        obligation = _record(item, _CONTEXT_FIELDS, path)
        for field in ("obligation_id", "run_id", "task_id"):
            _string(obligation[field], f"{path}.{field}")
        _digest(obligation["requirement_digest"], f"{path}.requirement_digest")
        _string_list(obligation["evidence_refs"], f"{path}.evidence_refs", address=True)
        _digest(obligation["validation_event_digest"], f"{path}.validation_event_digest")
        _boolean(obligation["satisfied"], f"{path}.satisfied")
        _digest(obligation["record_digest"], f"{path}.record_digest")

    approvals = _list(content["approvals"], "$.content.approvals")
    for index, item in enumerate(approvals):
        path = f"$.content.approvals[{index}]"
        approval = _record(item, _APPROVAL_FIELDS, path)
        for field in ("approval_id", "run_id", "effect_id", "principal"):
            _string(approval[field], f"{path}.{field}")
        for field in ("scope_digest", "grant_event_digest", "record_digest"):
            _digest(approval[field], f"{path}.{field}")

    effects = _list(content["effects"], "$.content.effects")
    for index, item in enumerate(effects):
        path = f"$.content.effects[{index}]"
        effect = _record(item, _EFFECT_FIELDS, path)
        for field in (
            "effect_id",
            "run_id",
            "task_id",
            "action",
            "resource",
            "idempotency_key",
        ):
            _string(effect[field], f"{path}.{field}")
        effect_class = _string(effect["effect_class"], f"{path}.effect_class")
        if effect_class not in _WRITE_EFFECTS:
            raise _SchemaError("invalid_effect_class", f"{path}.effect_class", effect_class)
        _digest(effect["payload_artifact_ref"], f"{path}.payload_artifact_ref", address=True)
        _digest(effect["proposed_event_digest"], f"{path}.proposed_event_digest")
        _optional_string(effect["approval_id"], f"{path}.approval_id")
        terminal_state = _string(effect["terminal_state"], f"{path}.terminal_state")
        if terminal_state not in {"proposed", *_TERMINAL_EFFECT_EVENT}:
            raise _SchemaError("invalid_effect_state", f"{path}.terminal_state", terminal_state)
        _nullable_digest(effect["commit_event_digest"], f"{path}.commit_event_digest")
        _nullable_digest(effect["terminal_event_digest"], f"{path}.terminal_event_digest")
        _digest(effect["record_digest"], f"{path}.record_digest")

    replay = _record(content["replay_witness"], _REPLAY_FIELDS, "$.content.replay_witness")
    _string(replay["run_id"], "$.content.replay_witness.run_id")
    for field in (
        "graph_digest",
        "envelope_digest",
        "terminal_event_digest",
        "event_chain_digest",
        "output_set_digest",
        "artifact_set_digest",
        "claim_set_digest",
        "context_set_digest",
        "approval_set_digest",
        "effect_set_digest",
        "witness_digest",
    ):
        _digest(replay[field], f"$.content.replay_witness.{field}")
    _integer(replay["event_count"], "$.content.replay_witness.event_count", minimum=1)
    _vector(replay["resource_totals"], "$.content.replay_witness.resource_totals")
    return content


def _scope_material(effect: Mapping[str, object]) -> dict[str, object]:
    return {
        "run_id": effect["run_id"],
        "effect_id": effect["effect_id"],
        "task_id": effect["task_id"],
        "effect_class": effect["effect_class"],
        "action": effect["action"],
        "resource": effect["resource"],
        "idempotency_key": effect["idempotency_key"],
        "payload_artifact_ref": effect["payload_artifact_ref"],
    }


def _is_sorted_unique(values: Sequence[str]) -> bool:
    return list(values) == sorted(set(values))


def verify_whole_run_evidence(sealed: Mapping[str, object]) -> WholeRunVerificationReport:
    """Verify a sealed run without invoking any execution-side component.

    Untrusted structural input is reported as a rejection rather than raised.
    After canonical structure is established, all independent semantic checks run
    so callers receive a useful set of violations from a single pass.
    """

    try:
        content = _validate_structure(sealed)
    except _SchemaError as exc:
        return WholeRunVerificationReport(
            passed=False,
            run_id=None,
            evidence_digest=None,
            violations=(WholeRunViolation(exc.code, exc.path, exc.detail),),
        )

    identity = content["identity"]
    envelope = content["envelope"]
    events = content["events"]
    artifacts = content["artifacts"]
    claims = content["claims"]
    obligations = content["context_obligations"]
    approvals = content["approvals"]
    effects = content["effects"]
    replay = content["replay_witness"]
    assert isinstance(identity, Mapping)
    assert isinstance(envelope, Mapping)
    assert isinstance(events, list)
    assert isinstance(artifacts, list)
    assert isinstance(claims, list)
    assert isinstance(obligations, list)
    assert isinstance(approvals, list)
    assert isinstance(effects, list)
    assert isinstance(replay, Mapping)

    run_id = str(identity["run_id"])
    supplied_content_digest = str(sealed["content_digest"])
    violations: list[WholeRunViolation] = []

    def fail(code: str, path: str, detail: str) -> None:
        violations.append(WholeRunViolation(code, path, detail))

    observed_content_digest = canonical_evidence_digest(content)
    if supplied_content_digest != observed_content_digest:
        fail(
            "content_digest_mismatch",
            "$.content_digest",
            f"declared={supplied_content_digest}, observed={observed_content_digest}",
        )

    envelope_unsigned = {
        "run_id": envelope["run_id"],
        "deadline_ms": envelope["deadline_ms"],
        "resource_caps": envelope["resource_caps"],
        "policy_digest": envelope["policy_digest"],
    }
    observed_envelope_digest = canonical_evidence_digest(envelope_unsigned)
    if envelope["envelope_digest"] != observed_envelope_digest:
        fail("envelope_digest_mismatch", "$.content.envelope", "envelope seal is invalid")
    if envelope["run_id"] != run_id or identity["envelope_digest"] != envelope["envelope_digest"]:
        fail(
            "run_envelope_identity_mismatch",
            "$.content.identity",
            "run ID or envelope digest disagrees with the sealed envelope",
        )

    typed_events = [item for item in events if isinstance(item, Mapping)]
    event_by_digest = {str(item["event_digest"]): item for item in typed_events}
    event_position = {
        str(item["event_digest"]): index for index, item in enumerate(typed_events, start=1)
    }
    event_ids = [str(item["event_id"]) for item in typed_events]
    event_digests = [str(item["event_digest"]) for item in typed_events]
    if len(event_ids) != len(set(event_ids)):
        fail("duplicate_event_id", "$.content.events", "event IDs must be unique")
    if len(event_digests) != len(set(event_digests)):
        fail("duplicate_event_digest", "$.content.events", "event digests must be unique")

    prior_digest = GENESIS_EVENT_DIGEST
    prior_time = -1
    seen_digests: set[str] = set()
    for index, event in enumerate(typed_events, start=1):
        path = f"$.content.events[{index - 1}]"
        if not _check_record_digest(event, "event_digest", event["event_digest"]):
            fail("event_digest_mismatch", path, "event seal is invalid")
        if event["run_id"] != run_id:
            fail("event_run_identity_mismatch", f"{path}.run_id", str(event["run_id"]))
        if event["sequence"] != index:
            fail("non_monotonic_event_sequence", f"{path}.sequence", f"expected={index}")
        occurred_at = int(event["occurred_at_ms"])
        if occurred_at < prior_time:
            fail("non_monotonic_event_time", f"{path}.occurred_at_ms", f"prior={prior_time}")
        prior_time = occurred_at
        if event["previous_event_digest"] != prior_digest:
            fail(
                "event_chain_break",
                f"{path}.previous_event_digest",
                f"expected={prior_digest}",
            )
        causes = list(event["causes"])
        if not _is_sorted_unique(causes):
            fail("noncanonical_reference_order", f"{path}.causes", "must be sorted and unique")
        if index == 1 and causes:
            fail("invalid_genesis_causes", f"{path}.causes", "first event cannot have causes")
        if index > 1 and not causes:
            fail("missing_causal_predecessor", f"{path}.causes", "non-genesis event needs a cause")
        for cause in causes:
            if cause not in seen_digests:
                fail("invalid_event_cause", f"{path}.causes", f"not a prior event: {cause}")
        for field in ("output_artifact_refs", "evidence_refs"):
            refs = list(event[field])
            if not _is_sorted_unique(refs):
                fail(
                    "noncanonical_reference_order",
                    f"{path}.{field}",
                    "must be sorted and unique",
                )
        prior_digest = str(event["event_digest"])
        seen_digests.add(prior_digest)

    event_types = [str(item["event_type"]) for item in typed_events]
    starts = [index for index, value in enumerate(event_types) if value == "run.started"]
    terminals = [
        index for index, value in enumerate(event_types) if value in {"run.completed", "run.failed"}
    ]
    lifecycle_ok = (
        starts == [0]
        and len(terminals) == 1
        and terminals[0] == len(typed_events) - 1
        and typed_events[0]["occurred_at_ms"] == 0
    )
    if not lifecycle_ok:
        fail(
            "invalid_run_lifecycle",
            "$.content.events",
            f"starts={starts}, terminals={terminals}",
        )
    successful = bool(event_types and event_types[-1] == "run.completed")
    if successful and int(typed_events[-1]["occurred_at_ms"]) > int(envelope["deadline_ms"]):
        fail("run_deadline_exceeded", "$.content.events[-1]", "successful run ended after deadline")

    attempts_started: dict[tuple[str, int], str] = {}
    completed_tasks: set[str] = set()
    task_completion_events: dict[str, Mapping[str, object]] = {}
    output_producers: dict[str, str] = {}
    for index, event in enumerate(typed_events):
        path = f"$.content.events[{index}]"
        event_type = str(event["event_type"])
        task_id = event["task_id"]
        attempt = event["attempt"]
        approval_id = event["approval_id"]
        effect_id = event["effect_id"]
        if event_type in _RUN_EVENTS:
            if any(value is not None for value in (task_id, attempt, approval_id, effect_id)):
                fail(
                    "invalid_event_identity_shape", path, "run event contains task/effect identity"
                )
        elif event_type == "artifact.ingested":
            if any(value is not None for value in (task_id, attempt, approval_id, effect_id)):
                fail(
                    "invalid_event_identity_shape",
                    path,
                    "ingest event contains task/effect identity",
                )
            if not event["output_artifact_refs"]:
                fail("missing_ingested_artifact", path, "ingest event has no artifact")
        elif event_type in _TASK_EVENTS:
            if (
                task_id is None
                or attempt is None
                or approval_id is not None
                or effect_id is not None
            ):
                fail("invalid_event_identity_shape", path, "task event identity is malformed")
        elif event_type == "approval.granted":
            if task_id is None or attempt is not None or approval_id is None or effect_id is None:
                fail("invalid_event_identity_shape", path, "approval event identity is malformed")
        elif event_type in _EFFECT_EVENTS:
            if task_id is None or effect_id is None or attempt is not None:
                fail("invalid_event_identity_shape", path, "effect event identity is malformed")

        outputs = list(event["output_artifact_refs"])
        if outputs and event_type not in {
            "run.started",
            "artifact.ingested",
            "task.attempt_succeeded",
            "task.completed",
        }:
            fail("illegal_output_event", f"{path}.output_artifact_refs", event_type)
        for ref in outputs:
            if ref in output_producers:
                fail("duplicate_artifact_output", f"{path}.output_artifact_refs", ref)
            output_producers[ref] = str(event["event_digest"])

        if event_type == "task.attempt_started" and task_id is not None and attempt is not None:
            key = (str(task_id), int(attempt))
            if key in attempts_started:
                fail("duplicate_attempt_start", path, f"task={task_id}, attempt={attempt}")
            attempts_started[key] = str(event["event_digest"])
        if event_type in {"task.completed", "task.failed", "task.cancelled"}:
            if task_id is None or attempt is None:
                continue
            key = (str(task_id), int(attempt))
            start_digest = attempts_started.get(key)
            if start_digest is None or start_digest not in event["causes"]:
                fail(
                    "invalid_task_causality", path, "terminal task event must cause-link its start"
                )
            if event_type == "task.completed":
                if not outputs:
                    fail(
                        "missing_task_output", path, "completed task has no sealed output artifact"
                    )
                if str(task_id) in completed_tasks:
                    fail("duplicate_task_completion", path, str(task_id))
                completed_tasks.add(str(task_id))
                task_completion_events[str(task_id)] = event

    caps = _vector(envelope["resource_caps"], "$.content.envelope.resource_caps")
    outstanding = {field: 0 for field in _RESOURCE_FIELDS}
    spent = {field: 0 for field in _RESOURCE_FIELDS}
    for index, event in enumerate(typed_events):
        resources = _event_resources(event["resources"], f"$.content.events[{index}].resources")
        for field in _RESOURCE_FIELDS:
            outstanding[field] += resources["reserved"][field]
            consumed = resources["actual"][field] + resources["released"][field]
            if consumed > outstanding[field]:
                fail(
                    "resource_conservation_violation",
                    f"$.content.events[{index}].resources.{field}",
                    f"consumed={consumed}, outstanding={outstanding[field]}",
                )
            else:
                outstanding[field] -= consumed
            spent[field] += resources["actual"][field]
            if spent[field] + outstanding[field] > caps[field]:
                fail(
                    "resource_cap_exceeded",
                    f"$.content.events[{index}].resources.{field}",
                    f"committed={spent[field] + outstanding[field]}, cap={caps[field]}",
                )
    if any(outstanding.values()):
        fail("resource_leak", "$.content.events", f"terminal_outstanding={outstanding}")

    artifact_records = [item for item in artifacts if isinstance(item, Mapping)]
    artifact_ids = [str(item["artifact_id"]) for item in artifact_records]
    artifact_by_id = {str(item["artifact_id"]): item for item in artifact_records}
    if artifact_ids != sorted(artifact_ids) or len(artifact_ids) != len(set(artifact_ids)):
        fail("noncanonical_artifact_set", "$.content.artifacts", "must be sorted and unique")

    for index, record in enumerate(artifact_records):
        path = f"$.content.artifacts[{index}]"
        if not _check_record_digest(record, "record_digest", record["record_digest"]):
            fail("artifact_record_digest_mismatch", path, "artifact record seal is invalid")
        try:
            payload = base64.b64decode(str(record["payload_base64"]), validate=True)
        except (ValueError, binascii.Error):
            payload = b""
            fail("invalid_artifact_payload", f"{path}.payload_base64", "invalid base64")
        if base64.b64encode(payload).decode("ascii") != record["payload_base64"]:
            fail(
                "noncanonical_artifact_payload", f"{path}.payload_base64", "base64 is not canonical"
            )
        try:
            artifact = Artifact(
                artifact_id=str(record["artifact_id"]),
                schema=str(record["schema"]),
                schema_version=str(record["schema_version"]),
                media_type=str(record["media_type"]),
                producer=str(record["producer"]),
                parents=tuple(record["parents"]),
                sensitivity=Sensitivity(str(record["sensitivity"])),
                created_at_ms=int(record["created_at_ms"]),
                fresh_until_ms=(
                    int(record["fresh_until_ms"]) if record["fresh_until_ms"] is not None else None
                ),
                payload=payload,
                payload_sha256=str(record["payload_sha256"]),
            )
        except (TypeError, ValueError):
            artifact = None
        if artifact is None or not Artifact.verify(artifact):
            fail("artifact_integrity_failure", path, "content address or metadata is invalid")
        parents = list(record["parents"])
        if not _is_sorted_unique(parents):
            fail("noncanonical_reference_order", f"{path}.parents", "must be sorted and unique")
        producer_digest = str(record["producer_event_digest"])
        producer_event = event_by_digest.get(producer_digest)
        if (
            producer_event is None
            or record["artifact_id"] not in producer_event["output_artifact_refs"]
        ):
            fail("artifact_producer_mismatch", path, "producer event does not emit this artifact")
        elif int(record["created_at_ms"]) < int(producer_event["occurred_at_ms"]):
            fail("artifact_time_causality", path, "artifact predates its producer event")
        for parent in parents:
            parent_record = artifact_by_id.get(parent)
            if parent_record is None:
                fail("missing_artifact_parent", f"{path}.parents", parent)
                continue
            if event_position.get(
                str(parent_record["producer_event_digest"]), 10**18
            ) >= event_position.get(producer_digest, -1):
                fail("artifact_lineage_causality", f"{path}.parents", parent)

    for ref, producer_digest in output_producers.items():
        record = artifact_by_id.get(ref)
        if record is None:
            fail("missing_output_artifact", "$.content.events", ref)
        elif record["producer_event_digest"] != producer_digest:
            fail("artifact_producer_mismatch", "$.content.artifacts", ref)

    for index, event in enumerate(typed_events):
        for ref in event["evidence_refs"]:
            record = artifact_by_id.get(str(ref))
            if record is None:
                fail(
                    "missing_evidence_artifact",
                    f"$.content.events[{index}].evidence_refs",
                    str(ref),
                )
            elif event_position.get(str(record["producer_event_digest"]), 10**18) >= index + 1:
                fail(
                    "evidence_causality_violation",
                    f"$.content.events[{index}].evidence_refs",
                    str(ref),
                )
            elif int(event["occurred_at_ms"]) < int(record["created_at_ms"]) or (
                record["fresh_until_ms"] is not None
                and int(event["occurred_at_ms"]) > int(record["fresh_until_ms"])
            ):
                fail(
                    "stale_or_future_evidence", f"$.content.events[{index}].evidence_refs", str(ref)
                )

    claim_records = [item for item in claims if isinstance(item, Mapping)]
    claim_ids = [str(item["claim_id"]) for item in claim_records]
    claim_by_id = {str(item["claim_id"]): item for item in claim_records}
    if claim_ids != sorted(claim_ids) or len(claim_ids) != len(set(claim_ids)):
        fail("noncanonical_claim_set", "$.content.claims", "must be sorted and unique")
    for index, record in enumerate(claim_records):
        path = f"$.content.claims[{index}]"
        if not _check_record_digest(record, "record_digest", record["record_digest"]):
            fail("claim_record_digest_mismatch", path, "claim record seal is invalid")
        try:
            claim = Claim(
                claim_id=str(record["claim_id"]),
                statement=str(record["statement"]),
                evidence_refs=tuple(record["evidence_refs"]),
                status=ClaimStatus(str(record["status"])),
                contradicts=tuple(record["contradicts"]),
                producer=str(record["producer"]),
                created_at_ms=int(record["created_at_ms"]),
                claim_digest=str(record["claim_digest"]),
            )
        except (TypeError, ValueError):
            claim = None
        if claim is None or not Claim.verify(claim):
            fail("claim_integrity_failure", path, "claim digest or structure is invalid")
        refs = list(record["evidence_refs"])
        conflicts = list(record["contradicts"])
        if not _is_sorted_unique(refs) or not _is_sorted_unique(conflicts):
            fail("noncanonical_reference_order", path, "claim references must be sorted and unique")
        if record["status"] == ClaimStatus.SUPPORTED.value and not refs:
            fail("unsupported_claim", path, "supported claim has no evidence")
        producer_digest = str(record["produced_by_event_digest"])
        producer_position = event_position.get(producer_digest)
        if producer_position is None:
            fail("missing_claim_producer", path, producer_digest)
        elif int(record["created_at_ms"]) < int(event_by_digest[producer_digest]["occurred_at_ms"]):
            fail("claim_time_causality", path, "claim predates its producer event")
        for ref in refs:
            artifact_record = artifact_by_id.get(ref)
            if artifact_record is None:
                fail("missing_claim_evidence", f"{path}.evidence_refs", ref)
            elif (
                producer_position is not None
                and event_position.get(str(artifact_record["producer_event_digest"]), 10**18)
                > producer_position
            ):
                fail("claim_causality_violation", f"{path}.evidence_refs", ref)
            elif int(record["created_at_ms"]) < int(artifact_record["created_at_ms"]) or (
                artifact_record["fresh_until_ms"] is not None
                and int(record["created_at_ms"]) > int(artifact_record["fresh_until_ms"])
            ):
                fail("stale_or_future_claim_evidence", f"{path}.evidence_refs", ref)
        for conflict in conflicts:
            if conflict not in claim_by_id:
                fail("missing_conflicting_claim", f"{path}.contradicts", conflict)

    obligation_records = [item for item in obligations if isinstance(item, Mapping)]
    obligation_ids = [str(item["obligation_id"]) for item in obligation_records]
    if obligation_ids != sorted(obligation_ids) or len(obligation_ids) != len(set(obligation_ids)):
        fail(
            "noncanonical_context_set",
            "$.content.context_obligations",
            "must be sorted and unique",
        )
    validation_digests: set[str] = set()
    for index, record in enumerate(obligation_records):
        path = f"$.content.context_obligations[{index}]"
        if not _check_record_digest(record, "record_digest", record["record_digest"]):
            fail("context_record_digest_mismatch", path, "context record seal is invalid")
        if record["run_id"] != run_id:
            fail("context_run_identity_mismatch", f"{path}.run_id", str(record["run_id"]))
        refs = list(record["evidence_refs"])
        if not _is_sorted_unique(refs):
            fail(
                "noncanonical_reference_order", f"{path}.evidence_refs", "must be sorted and unique"
            )
        validation_digest = str(record["validation_event_digest"])
        validation_event = event_by_digest.get(validation_digest)
        if validation_digest in validation_digests:
            fail("duplicate_context_validation", path, validation_digest)
        validation_digests.add(validation_digest)
        if (
            validation_event is None
            or validation_event["event_type"] != "context.validated"
            or validation_event["task_id"] != record["task_id"]
            or not set(refs) <= set(validation_event["evidence_refs"])
        ):
            fail("context_validation_mismatch", path, "validation event does not bind obligation")
        if record["satisfied"] and not refs:
            fail("context_evidence_missing", path, "satisfied obligation has no evidence")
        if successful and not record["satisfied"]:
            fail("context_obligation_unsatisfied", path, str(record["obligation_id"]))
        completion = task_completion_events.get(str(record["task_id"]))
        if completion is not None and (
            not record["satisfied"] or validation_digest not in completion["causes"]
        ):
            fail(
                "context_completion_causality", path, "completion lacks satisfied validation cause"
            )
        for ref in refs:
            if ref not in artifact_by_id:
                fail("missing_context_evidence", f"{path}.evidence_refs", ref)

    approval_records = [item for item in approvals if isinstance(item, Mapping)]
    approval_ids = [str(item["approval_id"]) for item in approval_records]
    approval_by_id = {str(item["approval_id"]): item for item in approval_records}
    if approval_ids != sorted(approval_ids) or len(approval_ids) != len(set(approval_ids)):
        fail("noncanonical_approval_set", "$.content.approvals", "must be sorted and unique")
    approval_event_counts: Counter[str] = Counter()
    for event in typed_events:
        if event["event_type"] == "approval.granted" and event["approval_id"] is not None:
            event_approval_id = str(event["approval_id"])
            approval_event_counts[event_approval_id] += 1
            if event_approval_id not in approval_by_id:
                fail("unregistered_approval_event", "$.content.events", event_approval_id)
    for index, record in enumerate(approval_records):
        path = f"$.content.approvals[{index}]"
        if not _check_record_digest(record, "record_digest", record["record_digest"]):
            fail("approval_record_digest_mismatch", path, "approval record seal is invalid")
        if record["run_id"] != run_id:
            fail("approval_run_identity_mismatch", f"{path}.run_id", str(record["run_id"]))
        grant_event = event_by_digest.get(str(record["grant_event_digest"]))
        if (
            grant_event is None
            or grant_event["event_type"] != "approval.granted"
            or grant_event["approval_id"] != record["approval_id"]
            or grant_event["effect_id"] != record["effect_id"]
        ):
            fail("approval_event_mismatch", path, "grant event does not bind approval")
        if approval_event_counts[str(record["approval_id"])] != 1:
            fail("approval_event_uniqueness", path, "approval must have exactly one grant event")

    effect_records = [item for item in effects if isinstance(item, Mapping)]
    effect_ids = [str(item["effect_id"]) for item in effect_records]
    effect_by_id = {str(item["effect_id"]): item for item in effect_records}
    if effect_ids != sorted(effect_ids) or len(effect_ids) != len(set(effect_ids)):
        fail("noncanonical_effect_set", "$.content.effects", "must be sorted and unique")
    idempotency_keys = [str(item["idempotency_key"]) for item in effect_records]
    if len(idempotency_keys) != len(set(idempotency_keys)):
        fail(
            "duplicate_effect_idempotency_key",
            "$.content.effects",
            "effect idempotency keys must be globally unique",
        )
    approval_use = Counter(
        str(item["approval_id"]) for item in effect_records if item["approval_id"] is not None
    )
    effect_event_counts: Counter[tuple[str, str]] = Counter()
    for event in typed_events:
        if event["event_type"] in _EFFECT_EVENTS and event["effect_id"] is not None:
            effect_event_counts[(str(event["effect_id"]), str(event["event_type"]))] += 1
            if str(event["effect_id"]) not in effect_by_id:
                fail("unregistered_effect_event", "$.content.events", str(event["effect_id"]))

    for index, record in enumerate(effect_records):
        path = f"$.content.effects[{index}]"
        effect_id = str(record["effect_id"])
        if not _check_record_digest(record, "record_digest", record["record_digest"]):
            fail("effect_record_digest_mismatch", path, "effect record seal is invalid")
        if record["run_id"] != run_id:
            fail("effect_run_identity_mismatch", f"{path}.run_id", str(record["run_id"]))
        if record["payload_artifact_ref"] not in artifact_by_id:
            fail(
                "missing_effect_payload",
                f"{path}.payload_artifact_ref",
                str(record["payload_artifact_ref"]),
            )
        proposed = event_by_digest.get(str(record["proposed_event_digest"]))
        if (
            proposed is None
            or proposed["event_type"] != "effect.proposed"
            or proposed["effect_id"] != effect_id
            or proposed["task_id"] != record["task_id"]
        ):
            fail("effect_proposal_mismatch", path, "proposal event does not bind effect")
        else:
            payload_record = artifact_by_id.get(str(record["payload_artifact_ref"]))
            if payload_record is not None and event_position.get(
                str(payload_record["producer_event_digest"]), 10**18
            ) >= event_position.get(str(record["proposed_event_digest"]), -1):
                fail(
                    "effect_payload_causality",
                    path,
                    "effect payload was not sealed before proposal",
                )
            if proposed["approval_id"] is not None:
                fail("effect_proposal_mismatch", path, "proposal cannot carry an approval")
        if effect_event_counts[(effect_id, "effect.proposed")] != 1:
            fail("effect_proposal_uniqueness", path, "effect must have exactly one proposal event")
        if effect_event_counts[(effect_id, "effect.committed")] > 1:
            fail("effect_commit_uniqueness", path, "effect has multiple commit events")

        approval_id = record["approval_id"]
        approval = approval_by_id.get(str(approval_id)) if approval_id is not None else None
        if approval_id is not None:
            if approval is None or approval["effect_id"] != effect_id:
                fail("effect_approval_mismatch", path, "approval does not bind effect")
            elif approval["scope_digest"] != canonical_evidence_digest(_scope_material(record)):
                fail(
                    "approval_scope_mismatch", path, "approval scope does not bind immutable effect"
                )
            if approval_use[str(approval_id)] != 1:
                fail("approval_reuse", path, str(approval_id))
            if approval is not None and proposed is not None:
                grant_event = event_by_digest.get(str(approval["grant_event_digest"]))
                if (
                    grant_event is None
                    or str(record["proposed_event_digest"]) not in grant_event["causes"]
                    or event_position[str(approval["grant_event_digest"])]
                    <= event_position[str(record["proposed_event_digest"])]
                ):
                    fail(
                        "approval_causality", path, "grant does not follow and cause-link proposal"
                    )

        terminal_state = str(record["terminal_state"])
        commit_digest = record["commit_event_digest"]
        terminal_digest = record["terminal_event_digest"]
        commit_event = (
            event_by_digest.get(str(commit_digest)) if commit_digest is not None else None
        )
        terminal_event = (
            event_by_digest.get(str(terminal_digest)) if terminal_digest is not None else None
        )
        if terminal_state == "proposed":
            if commit_digest is not None or terminal_digest is not None:
                fail("effect_state_mismatch", path, "proposed effect has terminal evidence")
            if sum(
                effect_event_counts[(effect_id, event_type)]
                for event_type in ("effect.committed", "effect.aborted", "effect.compensated")
            ):
                fail("effect_state_mismatch", path, "proposed effect has terminal events")
        else:
            expected_type = _TERMINAL_EFFECT_EVENT[terminal_state]
            if (
                terminal_event is None
                or terminal_event["event_type"] != expected_type
                or terminal_event["effect_id"] != effect_id
            ):
                fail("effect_terminal_mismatch", path, f"expected {expected_type}")
            if terminal_state == "committed" and commit_digest != terminal_digest:
                fail("effect_commit_binding", path, "committed terminal must be its commit event")
            if terminal_state == "aborted" and commit_digest is not None:
                fail("effect_commit_binding", path, "aborted effect cannot have a commit")
            if terminal_state == "compensated" and (
                commit_event is None or commit_event["event_type"] != "effect.committed"
            ):
                fail("effect_commit_binding", path, "compensation requires one prior commit")
            expected_counts = {
                "effect.committed": 1 if terminal_state in {"committed", "compensated"} else 0,
                "effect.aborted": 1 if terminal_state == "aborted" else 0,
                "effect.compensated": 1 if terminal_state == "compensated" else 0,
            }
            for event_type, expected_count in expected_counts.items():
                if effect_event_counts[(effect_id, event_type)] != expected_count:
                    fail(
                        "effect_terminal_uniqueness",
                        path,
                        f"{event_type}={effect_event_counts[(effect_id, event_type)]}, "
                        f"expected={expected_count}",
                    )

        if commit_event is not None:
            if commit_event["effect_id"] != effect_id:
                fail("effect_commit_binding", path, "commit event belongs to another effect")
            if commit_event["approval_id"] != approval_id:
                fail("effect_commit_binding", path, "commit approval identity disagrees")
            needed_causes = {str(record["proposed_event_digest"])}
            if approval is not None:
                needed_causes.add(str(approval["grant_event_digest"]))
            if not needed_causes <= set(commit_event["causes"]):
                fail("effect_commit_causality", path, "commit lacks proposal or approval cause")
        if terminal_state == "aborted" and terminal_event is not None:
            if str(record["proposed_event_digest"]) not in terminal_event["causes"]:
                fail("effect_terminal_causality", path, "abort lacks proposal cause")
        if terminal_state == "compensated" and terminal_event is not None:
            if commit_digest not in terminal_event["causes"]:
                fail("effect_terminal_causality", path, "compensation lacks commit cause")
        if (
            record["effect_class"] == EffectClass.IRREVERSIBLE_WRITE.value
            and commit_event is not None
            and approval is None
        ):
            fail("irreversible_effect_without_approval", path, effect_id)

    for approval_id, approval in approval_by_id.items():
        if approval_use[approval_id] != 1:
            fail("orphan_or_reused_approval", "$.content.approvals", approval_id)
        if str(approval["effect_id"]) not in effect_by_id:
            fail("orphan_or_reused_approval", "$.content.approvals", approval_id)

    if replay["run_id"] != run_id or replay["graph_digest"] != identity["graph_digest"]:
        fail("replay_identity_mismatch", "$.content.replay_witness", "run or graph differs")
    if replay["envelope_digest"] != identity["envelope_digest"]:
        fail("replay_identity_mismatch", "$.content.replay_witness", "envelope differs")
    expected_replay = {
        "terminal_event_digest": event_digests[-1],
        "event_count": len(event_digests),
        "event_chain_digest": canonical_evidence_digest(event_digests),
        "resource_totals": spent,
        "output_set_digest": canonical_evidence_digest(sorted(output_producers)),
        "artifact_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in artifact_records]
        ),
        "claim_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in claim_records]
        ),
        "context_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in obligation_records]
        ),
        "approval_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in approval_records]
        ),
        "effect_set_digest": canonical_evidence_digest(
            [item["record_digest"] for item in effect_records]
        ),
    }
    for field, expected in expected_replay.items():
        if replay[field] != expected:
            fail(
                "replay_witness_binding_mismatch",
                f"$.content.replay_witness.{field}",
                f"expected={expected}",
            )
    if not _check_record_digest(replay, "witness_digest", replay["witness_digest"]):
        fail(
            "replay_witness_digest_mismatch", "$.content.replay_witness", "witness seal is invalid"
        )

    return WholeRunVerificationReport(
        passed=not violations,
        run_id=run_id,
        evidence_digest=observed_content_digest,
        violations=tuple(violations),
    )


__all__ = [
    "DIGEST_ALGORITHM",
    "GENESIS_EVENT_DIGEST",
    "WHOLE_RUN_SCHEMA_VERSION",
    "WholeRunVerificationReport",
    "WholeRunViolation",
    "canonical_evidence_digest",
    "seal_evidence_record",
    "seal_whole_run_evidence",
    "verify_whole_run_evidence",
]
