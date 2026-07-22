"""Strict immutable backend/profile evidence snapshots.

Snapshots distinguish declared estimates from observations and bind one task/profile identity to
versioned provider, model, tool, adapter, pricing, quota, calibration, placement, and timing facts.
The canonical digest is tamper evidence; it is not a provider signature or proof that a named
external source is truthful.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


PROFILE_SNAPSHOT_SCHEMA_VERSION = "finite.profile-snapshot/v1"
MAX_INTEGER = (1 << 63) - 1
MAX_PROBABILITY_PPM = 1_000_000
MAX_FRESHNESS_SECONDS = 31 * 24 * 60 * 60

_ROOT_FIELDS = {
    "schema_version",
    "identity",
    "pricing",
    "quota",
    "metrics",
    "calibration",
    "placement",
    "freshness",
}
_IDENTITY_FIELDS = {"task_id", "profile_id", "provider", "model", "tool", "adapter"}
_COMPONENT_FIELDS = {"name", "version"}
_PRICING_FIELDS = {
    "currency",
    "token_price_unit",
    "request_price_unit",
    "input_microusd_per_million_tokens",
    "output_microusd_per_million_tokens",
    "request_microusd",
    "provenance",
    "source",
    "effective_at",
}
_QUOTA_FIELDS = {
    "rpm",
    "tpm",
    "concurrency",
    "window_ms",
    "provenance",
    "source",
    "sampled_at",
}
_METRICS_FIELDS = {
    "provenance",
    "source",
    "sample_count",
    "sampled_at",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "context_bytes",
    "quality_ppm",
    "failure_rate_ppm",
}
_PERCENTILE_FIELDS = {"p50", "p95"}
_CALIBRATION_FIELDS = {"quality", "failure"}
_QUALITY_CALIBRATION_FIELDS = {
    "provenance",
    "method",
    "source",
    "sample_count",
    "calibrated_at",
    "expected_calibration_error_ppm",
}
_FAILURE_CALIBRATION_FIELDS = {
    "provenance",
    "method",
    "source",
    "sample_count",
    "calibrated_at",
    "observed_failure_rate_ppm",
    "brier_score_ppm",
}
_PLACEMENT_FIELDS = {"region", "failure_domains"}
_FAILURE_DOMAIN_FIELDS = {"provider", "model", "region", "tool", "data"}
_FRESHNESS_FIELDS = {"snapshot_at", "valid_until", "max_source_age_seconds"}

_MEASUREMENT_PROVENANCE = frozenset({"observed", "estimated"})
_PRICING_PROVENANCE = frozenset({"provider_published", "contract", "estimated"})
_QUOTA_PROVENANCE = frozenset({"provider_reported", "account_configured", "estimated"})
_FLOATING_VERSION_NAMES = frozenset({"latest", "current", "default", "*", "unknown", "unversioned"})
_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "client_secret",
        "credential",
        "credentials",
        "authorization",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ProfileSnapshotError(ValueError):
    """A profile snapshot is malformed, stale, ambiguous, or tampered."""


class ProfileIdentityConflict(ProfileSnapshotError):
    """The same task/profile identity was registered with different evidence."""


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """Canonical immutable result of strict snapshot validation."""

    canonical_json: str
    digest: str
    task_id: str
    profile_id: str
    provider_name: str
    provider_version: str
    model_name: str
    model_version: str
    tool_name: str
    tool_version: str
    adapter_name: str
    adapter_version: str
    metrics_provenance: str
    snapshot_at: datetime
    valid_until: datetime

    @property
    def identity_key(self) -> tuple[str, str]:
        """Return the registry identity controlled by this immutable snapshot."""

        return (self.task_id, self.profile_id)

    @property
    def metrics_are_observed(self) -> bool:
        """True only when the manifest explicitly labels metrics as observed."""

        return self.metrics_provenance == "observed"

    def is_fresh(self, at: str | datetime | None = None) -> bool:
        """Return whether the snapshot is active at a trusted caller-supplied time."""

        reference = _reference_time(at)
        return self.snapshot_at <= reference <= self.valid_until

    def to_python(self) -> dict[str, Any]:
        """Return a detached JSON-compatible copy."""

        value = json.loads(self.canonical_json)
        if type(value) is not dict:  # pragma: no cover - construction invariant
            raise RuntimeError("canonical profile snapshot is not an object")
        return value


@dataclass(frozen=True, slots=True)
class _SnapshotParts:
    task_id: str
    profile_id: str
    provider: tuple[str, str]
    model: tuple[str, str]
    tool: tuple[str, str]
    adapter: tuple[str, str]
    metrics_provenance: str
    snapshot_at: datetime
    valid_until: datetime


class ProfileSnapshotRegistry:
    """In-memory immutable-identity registry with idempotent exact re-registration."""

    __slots__ = ("_snapshots",)

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], ProfileSnapshot] = {}

    def register(
        self,
        snapshot: ProfileSnapshot,
        *,
        at: str | datetime | None = None,
        require_fresh: bool = True,
    ) -> bool:
        """Register once; return False for an identical repeat and reject conflicts."""

        if type(snapshot) is not ProfileSnapshot:
            raise ProfileSnapshotError("snapshot must be an exact ProfileSnapshot")
        verified = validate_profile_snapshot(
            snapshot.canonical_json,
            at=at,
            require_fresh=require_fresh,
        )
        if verified != snapshot:
            raise ProfileSnapshotError("snapshot object does not match its canonical evidence")
        existing = self._snapshots.get(snapshot.identity_key)
        if existing is None:
            self._snapshots[snapshot.identity_key] = snapshot
            return True
        if (
            existing.digest == snapshot.digest
            and existing.canonical_json == snapshot.canonical_json
        ):
            return False
        raise ProfileIdentityConflict(
            "conflicting snapshot for task/profile identity "
            f"{snapshot.task_id!r}/{snapshot.profile_id!r}"
        )

    def get(self, task_id: str, profile_id: str) -> ProfileSnapshot | None:
        """Return the registered snapshot, if any, without accepting fuzzy identities."""

        task = _identifier(task_id, "task_id")
        profile = _identifier(profile_id, "profile_id")
        return self._snapshots.get((task, profile))

    def __len__(self) -> int:
        return len(self._snapshots)

    @property
    def digests(self) -> tuple[str, ...]:
        """Return registered digests in deterministic identity order."""

        return tuple(self._snapshots[key].digest for key in sorted(self._snapshots))


def seal_profile_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a detached document with its canonical digest."""

    if type(document) is not dict:
        raise ProfileSnapshotError("$: expected an object")
    if "snapshot_digest" in document:
        raise ProfileSnapshotError("$: seal_profile_snapshot expects an unsealed document")
    _reject_secret_fields(document)
    _validate_document(document, require_digest=False, at=None, require_fresh=False)
    detached = _strict_json_copy(document)
    detached["snapshot_digest"] = _payload_digest(detached)
    return detached


def validate_profile_snapshot(
    source: dict[str, Any] | str | bytes,
    *,
    at: str | datetime | None = None,
    require_fresh: bool = True,
) -> ProfileSnapshot:
    """Parse, validate, freshness-check, and verify one sealed snapshot."""

    document = _parse_source(source)
    _reject_secret_fields(document)
    parts = _validate_document(
        document,
        require_digest=True,
        at=at,
        require_fresh=require_fresh,
    )
    declared_digest = _sha256(document["snapshot_digest"], "$.snapshot_digest")
    actual_digest = _payload_digest(document)
    if not hmac.compare_digest(declared_digest, actual_digest):
        raise ProfileSnapshotError("$.snapshot_digest: snapshot digest mismatch")
    canonical = _canonical_json(document)
    return ProfileSnapshot(
        canonical_json=canonical,
        digest=actual_digest,
        task_id=parts.task_id,
        profile_id=parts.profile_id,
        provider_name=parts.provider[0],
        provider_version=parts.provider[1],
        model_name=parts.model[0],
        model_version=parts.model[1],
        tool_name=parts.tool[0],
        tool_version=parts.tool[1],
        adapter_name=parts.adapter[0],
        adapter_version=parts.adapter[1],
        metrics_provenance=parts.metrics_provenance,
        snapshot_at=parts.snapshot_at,
        valid_until=parts.valid_until,
    )


compile_profile_snapshot = validate_profile_snapshot


def _validate_document(
    document: dict[str, Any],
    *,
    require_digest: bool,
    at: str | datetime | None,
    require_fresh: bool,
) -> _SnapshotParts:
    root_fields = _ROOT_FIELDS | ({"snapshot_digest"} if require_digest else set())
    root = _object(document, "$", allowed=root_fields, required=root_fields)
    schema = _string(root["schema_version"], "$.schema_version")
    if schema != PROFILE_SNAPSHOT_SCHEMA_VERSION:
        raise ProfileSnapshotError(
            f"$.schema_version: expected {PROFILE_SNAPSHOT_SCHEMA_VERSION!r}"
        )

    identity = _object(
        root["identity"],
        "$.identity",
        allowed=_IDENTITY_FIELDS,
        required=_IDENTITY_FIELDS,
    )
    task_id = _identifier(identity["task_id"], "$.identity.task_id")
    profile_id = _identifier(identity["profile_id"], "$.identity.profile_id")
    provider = _component(identity["provider"], "$.identity.provider")
    model = _component(identity["model"], "$.identity.model")
    tool = _component(identity["tool"], "$.identity.tool")
    adapter = _component(identity["adapter"], "$.identity.adapter")

    freshness = _object(
        root["freshness"],
        "$.freshness",
        allowed=_FRESHNESS_FIELDS,
        required=_FRESHNESS_FIELDS,
    )
    snapshot_at = _timestamp(freshness["snapshot_at"], "$.freshness.snapshot_at")
    valid_until = _timestamp(freshness["valid_until"], "$.freshness.valid_until")
    max_source_age_seconds = _integer(
        freshness["max_source_age_seconds"],
        "$.freshness.max_source_age_seconds",
        minimum=1,
        maximum=MAX_FRESHNESS_SECONDS,
    )
    validity_seconds = (valid_until - snapshot_at).total_seconds()
    if validity_seconds < 0:
        raise ProfileSnapshotError("$.freshness: valid_until precedes snapshot_at")
    if validity_seconds > MAX_FRESHNESS_SECONDS:
        raise ProfileSnapshotError("$.freshness: validity window exceeds 31 days")

    pricing_effective_at = _validate_pricing(root["pricing"])
    quota_sampled_at = _validate_quota(root["quota"])
    metrics_provenance, metrics_sampled_at = _validate_metrics(root["metrics"])
    calibration_times = _validate_calibration(root["calibration"])
    _validate_placement(root["placement"], provider=provider, model=model, tool=tool)

    if pricing_effective_at > snapshot_at:
        raise ProfileSnapshotError("$.pricing.effective_at: cannot follow snapshot_at")
    _require_source_time(
        quota_sampled_at,
        snapshot_at,
        max_source_age_seconds,
        "$.quota.sampled_at",
    )
    _require_source_time(
        metrics_sampled_at,
        snapshot_at,
        max_source_age_seconds,
        "$.metrics.sampled_at",
    )
    _require_source_time(
        calibration_times[0],
        snapshot_at,
        max_source_age_seconds,
        "$.calibration.quality.calibrated_at",
    )
    _require_source_time(
        calibration_times[1],
        snapshot_at,
        max_source_age_seconds,
        "$.calibration.failure.calibrated_at",
    )

    if require_fresh:
        reference = _reference_time(at)
        if reference < snapshot_at:
            raise ProfileSnapshotError("snapshot is not yet active at the reference time")
        if reference > valid_until:
            raise ProfileSnapshotError("snapshot is stale at the reference time")

    return _SnapshotParts(
        task_id=task_id,
        profile_id=profile_id,
        provider=provider,
        model=model,
        tool=tool,
        adapter=adapter,
        metrics_provenance=metrics_provenance,
        snapshot_at=snapshot_at,
        valid_until=valid_until,
    )


def _validate_pricing(value: Any) -> datetime:
    pricing = _object(
        value,
        "$.pricing",
        allowed=_PRICING_FIELDS,
        required=_PRICING_FIELDS,
    )
    if _string(pricing["currency"], "$.pricing.currency") != "USD":
        raise ProfileSnapshotError("$.pricing.currency: expected 'USD'")
    if (
        _string(pricing["token_price_unit"], "$.pricing.token_price_unit")
        != "microusd_per_million_tokens"
    ):
        raise ProfileSnapshotError(
            "$.pricing.token_price_unit: expected 'microusd_per_million_tokens'"
        )
    if (
        _string(pricing["request_price_unit"], "$.pricing.request_price_unit")
        != "microusd_per_request"
    ):
        raise ProfileSnapshotError("$.pricing.request_price_unit: expected 'microusd_per_request'")
    for field in (
        "input_microusd_per_million_tokens",
        "output_microusd_per_million_tokens",
        "request_microusd",
    ):
        _integer(pricing[field], f"$.pricing.{field}", minimum=0)
    _enum_string(
        pricing["provenance"],
        "$.pricing.provenance",
        _PRICING_PROVENANCE,
    )
    _nonempty_string(pricing["source"], "$.pricing.source")
    return _timestamp(pricing["effective_at"], "$.pricing.effective_at")


def _validate_quota(value: Any) -> datetime:
    quota = _object(
        value,
        "$.quota",
        allowed=_QUOTA_FIELDS,
        required=_QUOTA_FIELDS,
    )
    for field in ("rpm", "tpm", "concurrency", "window_ms"):
        _integer(quota[field], f"$.quota.{field}", minimum=1)
    _enum_string(quota["provenance"], "$.quota.provenance", _QUOTA_PROVENANCE)
    _nonempty_string(quota["source"], "$.quota.source")
    return _timestamp(quota["sampled_at"], "$.quota.sampled_at")


def _validate_metrics(value: Any) -> tuple[str, datetime]:
    metrics = _object(
        value,
        "$.metrics",
        allowed=_METRICS_FIELDS,
        required=_METRICS_FIELDS,
    )
    provenance = _enum_string(
        metrics["provenance"],
        "$.metrics.provenance",
        _MEASUREMENT_PROVENANCE,
    )
    sample_count = _integer(metrics["sample_count"], "$.metrics.sample_count", minimum=0)
    _validate_provenance_sample_count(
        provenance,
        sample_count,
        "$.metrics",
    )
    _nonempty_string(metrics["source"], "$.metrics.source")
    sampled_at = _timestamp(metrics["sampled_at"], "$.metrics.sampled_at")
    for field in ("latency_ms", "input_tokens", "output_tokens", "context_bytes"):
        _percentiles(metrics[field], f"$.metrics.{field}", maximum=MAX_INTEGER)
    _percentiles(
        metrics["quality_ppm"],
        "$.metrics.quality_ppm",
        maximum=MAX_PROBABILITY_PPM,
    )
    _percentiles(
        metrics["failure_rate_ppm"],
        "$.metrics.failure_rate_ppm",
        maximum=MAX_PROBABILITY_PPM,
    )
    return provenance, sampled_at


def _validate_calibration(value: Any) -> tuple[datetime, datetime]:
    calibration = _object(
        value,
        "$.calibration",
        allowed=_CALIBRATION_FIELDS,
        required=_CALIBRATION_FIELDS,
    )
    quality = _object(
        calibration["quality"],
        "$.calibration.quality",
        allowed=_QUALITY_CALIBRATION_FIELDS,
        required=_QUALITY_CALIBRATION_FIELDS,
    )
    quality_provenance = _enum_string(
        quality["provenance"],
        "$.calibration.quality.provenance",
        _MEASUREMENT_PROVENANCE,
    )
    quality_samples = _integer(
        quality["sample_count"],
        "$.calibration.quality.sample_count",
        minimum=0,
    )
    _validate_provenance_sample_count(
        quality_provenance,
        quality_samples,
        "$.calibration.quality",
    )
    _nonempty_string(quality["method"], "$.calibration.quality.method")
    _nonempty_string(quality["source"], "$.calibration.quality.source")
    _integer(
        quality["expected_calibration_error_ppm"],
        "$.calibration.quality.expected_calibration_error_ppm",
        minimum=0,
        maximum=MAX_PROBABILITY_PPM,
    )
    quality_at = _timestamp(
        quality["calibrated_at"],
        "$.calibration.quality.calibrated_at",
    )

    failure = _object(
        calibration["failure"],
        "$.calibration.failure",
        allowed=_FAILURE_CALIBRATION_FIELDS,
        required=_FAILURE_CALIBRATION_FIELDS,
    )
    failure_provenance = _enum_string(
        failure["provenance"],
        "$.calibration.failure.provenance",
        _MEASUREMENT_PROVENANCE,
    )
    failure_samples = _integer(
        failure["sample_count"],
        "$.calibration.failure.sample_count",
        minimum=0,
    )
    _validate_provenance_sample_count(
        failure_provenance,
        failure_samples,
        "$.calibration.failure",
    )
    _nonempty_string(failure["method"], "$.calibration.failure.method")
    _nonempty_string(failure["source"], "$.calibration.failure.source")
    for field in ("observed_failure_rate_ppm", "brier_score_ppm"):
        _integer(
            failure[field],
            f"$.calibration.failure.{field}",
            minimum=0,
            maximum=MAX_PROBABILITY_PPM,
        )
    failure_at = _timestamp(
        failure["calibrated_at"],
        "$.calibration.failure.calibrated_at",
    )
    return quality_at, failure_at


def _validate_placement(
    value: Any,
    *,
    provider: tuple[str, str],
    model: tuple[str, str],
    tool: tuple[str, str],
) -> None:
    placement = _object(
        value,
        "$.placement",
        allowed=_PLACEMENT_FIELDS,
        required=_PLACEMENT_FIELDS,
    )
    region = _nonempty_string(placement["region"], "$.placement.region")
    domains = _object(
        placement["failure_domains"],
        "$.placement.failure_domains",
        allowed=_FAILURE_DOMAIN_FIELDS,
        required=_FAILURE_DOMAIN_FIELDS,
    )
    normalized = {
        field: _nonempty_string(
            domains[field],
            f"$.placement.failure_domains.{field}",
        )
        for field in sorted(_FAILURE_DOMAIN_FIELDS)
    }
    if normalized["region"] != region:
        raise ProfileSnapshotError(
            "$.placement.failure_domains.region: must equal placement.region"
        )
    if normalized["provider"] != provider[0]:
        raise ProfileSnapshotError("$.placement.failure_domains.provider: must equal provider name")
    if normalized["model"] != model[0]:
        raise ProfileSnapshotError("$.placement.failure_domains.model: must equal model name")
    if normalized["tool"] != tool[0]:
        raise ProfileSnapshotError("$.placement.failure_domains.tool: must equal tool name")


def _component(value: Any, path: str) -> tuple[str, str]:
    component = _object(
        value,
        path,
        allowed=_COMPONENT_FIELDS,
        required=_COMPONENT_FIELDS,
    )
    name = _nonempty_string(component["name"], f"{path}.name")
    version = _nonempty_string(component["version"], f"{path}.version")
    if version.casefold() in _FLOATING_VERSION_NAMES:
        raise ProfileSnapshotError(f"{path}.version: floating or unknown versions are forbidden")
    return name, version


def _percentiles(value: Any, path: str, *, maximum: int) -> tuple[int, int]:
    percentile = _object(
        value,
        path,
        allowed=_PERCENTILE_FIELDS,
        required=_PERCENTILE_FIELDS,
    )
    p50 = _integer(percentile["p50"], f"{path}.p50", minimum=0, maximum=maximum)
    p95 = _integer(percentile["p95"], f"{path}.p95", minimum=0, maximum=maximum)
    if p95 < p50:
        raise ProfileSnapshotError(f"{path}: p95 must be greater than or equal to p50")
    return p50, p95


def _validate_provenance_sample_count(provenance: str, sample_count: int, path: str) -> None:
    if provenance == "observed" and sample_count == 0:
        raise ProfileSnapshotError(f"{path}: observed provenance requires a positive sample_count")
    if provenance == "estimated" and sample_count != 0:
        raise ProfileSnapshotError(f"{path}: estimated provenance requires sample_count=0")


def _require_source_time(
    source_time: datetime,
    snapshot_at: datetime,
    max_age_seconds: int,
    path: str,
) -> None:
    age = (snapshot_at - source_time).total_seconds()
    if age < 0:
        raise ProfileSnapshotError(f"{path}: cannot follow snapshot_at")
    if age > max_age_seconds:
        raise ProfileSnapshotError(f"{path}: source evidence is stale")


def _parse_source(source: dict[str, Any] | str | bytes) -> dict[str, Any]:
    if type(source) is dict:
        return source
    if type(source) is bytes:
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProfileSnapshotError("snapshot bytes must be valid UTF-8") from exc
    elif type(source) is str:
        text = source
    else:
        raise ProfileSnapshotError("snapshot source must be an object, JSON text, or UTF-8 bytes")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ProfileSnapshotError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProfileSnapshotError(f"invalid snapshot JSON: {exc}") from exc
    if type(value) is not dict:
        raise ProfileSnapshotError("$: expected an object")
    return value


def _reject_secret_fields(value: Any, path: str = "$") -> None:
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is str and key.casefold() in _SECRET_FIELD_NAMES:
                raise ProfileSnapshotError(f"{path}.{key}: secret fields are forbidden")
            _reject_secret_fields(item, f"{path}.{key}")
    elif type(value) is list:
        for index, item in enumerate(value):
            _reject_secret_fields(item, f"{path}[{index}]")


def _payload_digest(document: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "snapshot_digest"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _strict_json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value), object_pairs_hook=_unique_json_object)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileSnapshotError(f"snapshot JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ProfileSnapshotError(f"snapshot JSON constant {value!r} is not supported")


def _object(
    value: Any,
    path: str,
    *,
    allowed: set[str],
    required: set[str],
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProfileSnapshotError(f"{path}: expected an object")
    if any(type(key) is not str for key in value):
        raise ProfileSnapshotError(f"{path}: object keys must be strings")
    keys = set(value)
    unknown = sorted(keys - allowed)
    if unknown:
        raise ProfileSnapshotError(f"{path}: unknown fields {unknown}")
    missing = sorted(required - keys)
    if missing:
        raise ProfileSnapshotError(f"{path}: missing required fields {missing}")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str:
        raise ProfileSnapshotError(f"{path}: expected a string")
    return value


def _nonempty_string(value: Any, path: str) -> str:
    result = _string(value, path)
    if not result or result != result.strip() or len(result) > 512:
        raise ProfileSnapshotError(f"{path}: expected a non-empty, trimmed string up to 512 chars")
    if any(ord(character) < 32 for character in result):
        raise ProfileSnapshotError(f"{path}: control characters are forbidden")
    return result


def _identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if _ID_RE.fullmatch(result) is None:
        raise ProfileSnapshotError(f"{path}: malformed identifier")
    return result


def _enum_string(value: Any, path: str, allowed: frozenset[str]) -> str:
    result = _string(value, path)
    if result not in allowed:
        raise ProfileSnapshotError(f"{path}: unsupported value {result!r}")
    return result


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_INTEGER,
) -> int:
    if type(value) is not int:
        raise ProfileSnapshotError(f"{path}: expected an integer")
    if value < minimum or value > maximum:
        raise ProfileSnapshotError(f"{path}: expected {minimum} through {maximum}")
    return value


def _sha256(value: Any, path: str) -> str:
    result = _string(value, path)
    if _SHA256_RE.fullmatch(result) is None:
        raise ProfileSnapshotError(f"{path}: expected 64 lowercase hexadecimal SHA-256")
    return result


def _timestamp(value: Any, path: str) -> datetime:
    text = _string(value, path)
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise ProfileSnapshotError(f"{path}: expected RFC 3339 UTC seconds (YYYY-MM-DDTHH:MM:SSZ)")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ProfileSnapshotError(f"{path}: invalid UTC timestamp") from exc


def _reference_time(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    if type(value) is str:
        return _timestamp(value, "reference_time")
    if type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProfileSnapshotError("reference_time datetime must be timezone-aware")
        return value.astimezone(UTC).replace(microsecond=0)
    raise ProfileSnapshotError("reference_time must be RFC 3339 UTC text or datetime")
