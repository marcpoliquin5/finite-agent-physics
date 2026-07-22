from __future__ import annotations

import copy
import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_physics.contracts import BackendProfile, RunEnvelope, TaskContract
from agent_physics.executor import (
    AdmissionRefused,
    AsyncGraphExecutor,
    TaskExecutionContext,
    WorkerResult,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.profile_snapshot import (
    MAX_INTEGER,
    PROFILE_SNAPSHOT_SCHEMA_VERSION,
    ProfileIdentityConflict,
    ProfileSnapshotError,
    ProfileSnapshotRegistry,
    seal_profile_snapshot,
    validate_profile_snapshot,
)
from agent_physics.run_store import RunNotFound, SQLiteRunStore


SNAPSHOT_AT = "2026-07-21T12:00:00Z"
VALID_UNTIL = "2026-07-28T12:00:00Z"
REFERENCE_TIME = "2026-07-22T12:00:00Z"


def profile_document() -> dict[str, Any]:
    return {
        "schema_version": PROFILE_SNAPSHOT_SCHEMA_VERSION,
        "identity": {
            "task_id": "stormshift.synthesis",
            "profile_id": "granite.accurate",
            "provider": {"name": "ibm-watsonx", "version": "1.5.3"},
            "model": {"name": "granite-3-8b-instruct", "version": "2026-07-15"},
            "tool": {"name": "finite-worker", "version": "1.0.0"},
            "adapter": {"name": "watsonx-granite", "version": "1.0.0"},
        },
        "pricing": {
            "currency": "USD",
            "token_price_unit": "microusd_per_million_tokens",
            "request_price_unit": "microusd_per_request",
            "input_microusd_per_million_tokens": 250_000,
            "output_microusd_per_million_tokens": 750_000,
            "request_microusd": 0,
            "provenance": "provider_published",
            "source": "watsonx catalog revision 2026-07-01",
            "effective_at": "2026-07-01T00:00:00Z",
        },
        "quota": {
            "rpm": 120,
            "tpm": 240_000,
            "concurrency": 8,
            "window_ms": 60_000,
            "provenance": "provider_reported",
            "source": "redacted account quota receipt",
            "sampled_at": "2026-07-21T11:30:00Z",
        },
        "metrics": {
            "provenance": "observed",
            "source": "paired live receipt corpus",
            "sample_count": 120,
            "sampled_at": "2026-07-21T11:00:00Z",
            "latency_ms": {"p50": 720, "p95": 1_940},
            "input_tokens": {"p50": 1_100, "p95": 1_650},
            "output_tokens": {"p50": 420, "p95": 760},
            "context_bytes": {"p50": 7_200, "p95": 12_800},
            "quality_ppm": {"p50": 910_000, "p95": 970_000},
            "failure_rate_ppm": {"p50": 8_000, "p95": 31_000},
        },
        "calibration": {
            "quality": {
                "provenance": "observed",
                "method": "held-out rubric calibration v1",
                "source": "registered validation split",
                "sample_count": 120,
                "calibrated_at": "2026-07-21T10:30:00Z",
                "expected_calibration_error_ppm": 24_000,
            },
            "failure": {
                "provenance": "observed",
                "method": "receipt outcome calibration v1",
                "source": "registered live outcome corpus",
                "sample_count": 120,
                "calibrated_at": "2026-07-21T10:30:00Z",
                "observed_failure_rate_ppm": 16_000,
                "brier_score_ppm": 11_000,
            },
        },
        "placement": {
            "region": "us-south",
            "failure_domains": {
                "provider": "ibm-watsonx",
                "model": "granite-3-8b-instruct",
                "region": "us-south",
                "tool": "finite-worker",
                "data": "stormshift-fixture-v1",
            },
        },
        "freshness": {
            "snapshot_at": SNAPSHOT_AT,
            "valid_until": VALID_UNTIL,
            "max_source_age_seconds": 24 * 60 * 60,
        },
    }


def sealed_profile() -> dict[str, Any]:
    return seal_profile_snapshot(profile_document())


def estimated_profile_document() -> dict[str, Any]:
    document = profile_document()
    document["pricing"].update(
        {
            "provenance": "estimated",
            "source": "declared planning estimate",
        }
    )
    document["quota"].update(
        {
            "provenance": "estimated",
            "source": "declared local quota estimate",
        }
    )
    document["metrics"].update(
        {
            "provenance": "estimated",
            "source": "declared planning model; not provider observations",
            "sample_count": 0,
        }
    )
    for kind in ("quality", "failure"):
        document["calibration"][kind].update(
            {
                "provenance": "estimated",
                "source": "declared estimate; not observations",
                "sample_count": 0,
            }
        )
    return document


def executor_profile_snapshot() -> Any:
    document = estimated_profile_document()
    document["identity"].update(
        {
            "task_id": "work",
            "profile_id": "fixture",
            "provider": {"name": "local", "version": "1.0.0"},
        }
    )
    document["placement"]["failure_domains"]["provider"] = "local"
    return validate_profile_snapshot(
        seal_profile_snapshot(document),
        at=REFERENCE_TIME,
    )


def test_observed_snapshot_is_canonical_digest_bound_and_fresh() -> None:
    sealed = sealed_profile()
    snapshot = validate_profile_snapshot(sealed, at=REFERENCE_TIME)

    assert snapshot.task_id == "stormshift.synthesis"
    assert snapshot.profile_id == "granite.accurate"
    assert snapshot.identity_key == ("stormshift.synthesis", "granite.accurate")
    assert snapshot.provider_name == "ibm-watsonx"
    assert snapshot.provider_version == "1.5.3"
    assert snapshot.model_name == "granite-3-8b-instruct"
    assert snapshot.tool_name == "finite-worker"
    assert snapshot.adapter_name == "watsonx-granite"
    assert snapshot.metrics_provenance == "observed"
    assert snapshot.metrics_are_observed is True
    assert snapshot.is_fresh(REFERENCE_TIME)
    assert snapshot.digest == sealed["snapshot_digest"]
    assert snapshot.to_python() == sealed
    assert snapshot.canonical_json == json.dumps(
        sealed, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )

    from_text = validate_profile_snapshot(json.dumps(sealed, indent=2), at=REFERENCE_TIME)
    from_bytes = validate_profile_snapshot(snapshot.canonical_json.encode(), at=REFERENCE_TIME)
    assert from_text == from_bytes == snapshot


def test_estimates_remain_explicitly_nonobserved_and_have_zero_samples() -> None:
    snapshot = validate_profile_snapshot(
        seal_profile_snapshot(estimated_profile_document()),
        at=REFERENCE_TIME,
    )
    assert snapshot.metrics_provenance == "estimated"
    assert snapshot.metrics_are_observed is False
    assert snapshot.to_python()["metrics"]["sample_count"] == 0


def test_executor_admission_binds_fresh_profile_snapshot_before_dispatch(tmp_path) -> None:
    snapshot = executor_profile_snapshot()
    profile = BackendProfile(
        "fixture",
        "local",
        duration_ms_p50=1,
        duration_ms_p95=5,
        profile_snapshot_digest=snapshot.digest,
    )
    graph = ExecutionGraph.from_tasks((TaskContract("work", (profile,)),))
    envelope = RunEnvelope(1_000, 100, 100, 1_000, 1)
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"ok": True})

    reference = datetime.strptime(REFERENCE_TIME, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    store = SQLiteRunStore(
        tmp_path / "profiles.db",
        clock_ms=lambda: int(reference.timestamp() * 1_000),
    )
    result = asyncio.run(
        AsyncGraphExecutor(
            store,
            workers={"work": worker},
            profile_snapshots={"work": snapshot},
        ).execute(graph, envelope, run_id="profile-bound")
    )

    assert calls == 1
    started = next(event for event in result.events if event.event_type == "run.started")
    manifest = started.payload["manifest"]
    assert manifest["selected_profiles"][0]["profile_snapshot_digest"] == snapshot.digest
    assert manifest["profile_snapshots"][0]["metrics_provenance"] == "estimated"


def test_missing_profile_snapshot_refuses_before_run_or_worker(tmp_path) -> None:
    snapshot = executor_profile_snapshot()
    profile = BackendProfile(
        "fixture",
        "local",
        duration_ms_p50=1,
        duration_ms_p95=5,
        profile_snapshot_digest=snapshot.digest,
    )
    graph = ExecutionGraph.from_tasks((TaskContract("work", (profile,)),))
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        return WorkerResult({"unexpected": True})

    reference = datetime.strptime(REFERENCE_TIME, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    store = SQLiteRunStore(
        tmp_path / "missing-profile.db",
        clock_ms=lambda: int(reference.timestamp() * 1_000),
    )
    with pytest.raises(AdmissionRefused, match="no immutable profile snapshot"):
        asyncio.run(
            AsyncGraphExecutor(store, workers={"work": worker}).execute(
                graph,
                RunEnvelope(1_000, 100, 100, 1_000, 1),
                run_id="profile-missing",
            )
        )
    assert calls == 0
    with pytest.raises(RunNotFound):
        store.get_run("profile-missing")


@pytest.mark.parametrize(
    ("path", "mutate"),
    [
        ("root", lambda value: value.__setitem__("future", {})),
        ("identity", lambda value: value["identity"].__setitem__("tenant", "x")),
        (
            "component",
            lambda value: value["identity"]["model"].__setitem__("revision", "x"),
        ),
        ("pricing", lambda value: value["pricing"].__setitem__("tax", 0)),
        ("quota", lambda value: value["quota"].__setitem__("burst", 10)),
        ("metrics", lambda value: value["metrics"].__setitem__("mean", 10)),
        (
            "percentile",
            lambda value: value["metrics"]["latency_ms"].__setitem__("p99", 3_000),
        ),
        (
            "calibration",
            lambda value: value["calibration"]["quality"].__setitem__("bins", 10),
        ),
        ("placement", lambda value: value["placement"].__setitem__("zone", "a")),
        (
            "failure domain",
            lambda value: value["placement"]["failure_domains"].__setitem__("account", "x"),
        ),
        ("freshness", lambda value: value["freshness"].__setitem__("grace", 1)),
    ],
)
def test_unknown_fields_fail_closed_at_every_schema_level(
    path: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    document = profile_document()
    mutate(document)
    with pytest.raises(ProfileSnapshotError, match="unknown fields"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("root", "schema_version"),
        ("identity", "profile_id"),
        ("component", "version"),
        ("pricing", "source"),
        ("quota", "sampled_at"),
        ("metrics", "sample_count"),
        ("calibration", "method"),
        ("domain", "data"),
        ("freshness", "valid_until"),
    ],
)
def test_missing_fields_fail_closed(target: str, field: str) -> None:
    document = profile_document()
    if target == "root":
        record = document
    elif target == "identity":
        record = document["identity"]
    elif target == "component":
        record = document["identity"]["provider"]
    elif target == "pricing":
        record = document["pricing"]
    elif target == "quota":
        record = document["quota"]
    elif target == "metrics":
        record = document["metrics"]
    elif target == "calibration":
        record = document["calibration"]["quality"]
    elif target == "domain":
        record = document["placement"]["failure_domains"]
    else:
        record = document["freshness"]
    record.pop(field)
    with pytest.raises(ProfileSnapshotError, match="missing required fields"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["pricing"].__setitem__("input_microusd_per_million_tokens", True),
        lambda value: value["quota"].__setitem__("rpm", True),
        lambda value: value["metrics"].__setitem__("sample_count", False),
        lambda value: value["metrics"]["latency_ms"].__setitem__("p50", True),
        lambda value: value["calibration"]["failure"].__setitem__("sample_count", True),
        lambda value: value["freshness"].__setitem__("max_source_age_seconds", True),
    ],
)
def test_bool_as_integer_is_rejected(mutate: Callable[[dict[str, Any]], None]) -> None:
    document = profile_document()
    mutate(document)
    with pytest.raises(ProfileSnapshotError, match="expected an integer"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["pricing"].__setitem__(
            "input_microusd_per_million_tokens", MAX_INTEGER + 1
        ),
        lambda value: value["quota"].__setitem__("tpm", MAX_INTEGER + 1),
        lambda value: value["metrics"]["input_tokens"].__setitem__("p95", MAX_INTEGER + 1),
        lambda value: value["pricing"].__setitem__("request_microusd", -1),
        lambda value: value["quota"].__setitem__("concurrency", 0),
    ],
)
def test_integer_units_reject_negative_zero_and_overflow(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    document = profile_document()
    mutate(document)
    with pytest.raises(ProfileSnapshotError, match="expected .* through"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    "metric",
    [
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "context_bytes",
        "quality_ppm",
        "failure_rate_ppm",
    ],
)
def test_every_metric_rejects_p95_below_p50(metric: str) -> None:
    document = profile_document()
    document["metrics"][metric] = {"p50": 10, "p95": 9}
    with pytest.raises(ProfileSnapshotError, match="p95 must be greater"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize("metric", ["quality_ppm", "failure_rate_ppm"])
def test_probability_metrics_are_integer_parts_per_million(metric: str) -> None:
    document = profile_document()
    document["metrics"][metric]["p95"] = 1_000_001
    with pytest.raises(ProfileSnapshotError, match="1000000"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    "path",
    ["metrics", "calibration.quality", "calibration.failure"],
)
def test_observed_provenance_requires_positive_sample_count(path: str) -> None:
    document = profile_document()
    target = document
    for segment in path.split("."):
        target = target[segment]
    target["sample_count"] = 0
    with pytest.raises(ProfileSnapshotError, match="observed provenance requires"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    "path",
    ["metrics", "calibration.quality", "calibration.failure"],
)
def test_estimated_provenance_cannot_claim_observation_samples(path: str) -> None:
    document = estimated_profile_document()
    target = document
    for segment in path.split("."):
        target = target[segment]
    target["sample_count"] = 1
    with pytest.raises(ProfileSnapshotError, match="estimated provenance requires"):
        seal_profile_snapshot(document)


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("pricing", "currency", "credits"),
        ("pricing", "token_price_unit", "usd_per_token"),
        ("pricing", "request_price_unit", "microusd"),
        ("pricing", "provenance", "observed"),
        ("quota", "provenance", "measured"),
    ],
)
def test_pricing_and_quota_units_and_provenance_are_closed_enums(
    target: str,
    field: str,
    value: str,
) -> None:
    document = profile_document()
    document[target][field] = value
    with pytest.raises(ProfileSnapshotError):
        seal_profile_snapshot(document)


@pytest.mark.parametrize("component", ["provider", "model", "tool", "adapter"])
@pytest.mark.parametrize("version", ["latest", "CURRENT", "default", "*", "unknown"])
def test_component_versions_cannot_float(component: str, version: str) -> None:
    document = profile_document()
    document["identity"][component]["version"] = version
    with pytest.raises(ProfileSnapshotError, match="floating or unknown"):
        seal_profile_snapshot(document)


def test_failure_domains_bind_provider_model_tool_and_region() -> None:
    for field, value in (
        ("provider", "other-provider"),
        ("model", "other-model"),
        ("tool", "other-tool"),
        ("region", "eu-de"),
    ):
        document = profile_document()
        document["placement"]["failure_domains"][field] = value
        with pytest.raises(ProfileSnapshotError, match=f"failure_domains.{field}"):
            seal_profile_snapshot(document)


def test_pricing_quota_metrics_and_calibration_times_cannot_be_future_or_stale() -> None:
    document = profile_document()
    document["pricing"]["effective_at"] = "2026-07-22T00:00:00Z"
    with pytest.raises(ProfileSnapshotError, match="pricing.effective_at.*follow"):
        seal_profile_snapshot(document)

    for target in (
        ("quota", "sampled_at"),
        ("metrics", "sampled_at"),
        ("quality", "calibrated_at"),
        ("failure", "calibrated_at"),
    ):
        document = profile_document()
        if target[0] in {"quality", "failure"}:
            record = document["calibration"][target[0]]
        else:
            record = document[target[0]]
        record[target[1]] = "2026-07-22T00:00:00Z"
        with pytest.raises(ProfileSnapshotError, match="cannot follow snapshot_at"):
            seal_profile_snapshot(document)

        document = profile_document()
        if target[0] in {"quality", "failure"}:
            record = document["calibration"][target[0]]
        else:
            record = document[target[0]]
        record[target[1]] = "2026-07-19T00:00:00Z"
        with pytest.raises(ProfileSnapshotError, match="source evidence is stale"):
            seal_profile_snapshot(document)


def test_snapshot_validity_window_and_reference_time_are_enforced() -> None:
    document = profile_document()
    document["freshness"]["valid_until"] = "2026-07-20T12:00:00Z"
    with pytest.raises(ProfileSnapshotError, match="precedes snapshot_at"):
        seal_profile_snapshot(document)

    document = profile_document()
    document["freshness"]["valid_until"] = "2026-09-01T12:00:00Z"
    with pytest.raises(ProfileSnapshotError, match="exceeds 31 days"):
        seal_profile_snapshot(document)

    sealed = sealed_profile()
    with pytest.raises(ProfileSnapshotError, match="not yet active"):
        validate_profile_snapshot(sealed, at="2026-07-20T12:00:00Z")
    with pytest.raises(ProfileSnapshotError, match="snapshot is stale"):
        validate_profile_snapshot(sealed, at="2026-07-29T12:00:00Z")

    historical = validate_profile_snapshot(
        sealed,
        at="2030-01-01T00:00:00Z",
        require_fresh=False,
    )
    assert historical.digest == sealed["snapshot_digest"]


def test_canonical_digest_is_order_independent_and_input_is_not_mutated() -> None:
    document = profile_document()
    original = copy.deepcopy(document)
    reversed_root = dict(reversed(list(document.items())))
    first = seal_profile_snapshot(document)
    second = seal_profile_snapshot(reversed_root)
    assert document == original
    assert first == second
    assert first["snapshot_digest"] == second["snapshot_digest"]


def test_post_seal_mutation_and_malformed_digest_fail_closed() -> None:
    sealed = sealed_profile()
    sealed["identity"]["model"]["version"] = "tampered"
    with pytest.raises(ProfileSnapshotError, match="snapshot digest mismatch"):
        validate_profile_snapshot(sealed, at=REFERENCE_TIME)

    sealed = sealed_profile()
    sealed["snapshot_digest"] = "A" * 64
    with pytest.raises(ProfileSnapshotError, match="lowercase hexadecimal SHA-256"):
        validate_profile_snapshot(sealed, at=REFERENCE_TIME)


def test_registry_is_idempotent_for_exact_snapshot_and_rejects_conflicts() -> None:
    first = validate_profile_snapshot(sealed_profile(), at=REFERENCE_TIME)
    registry = ProfileSnapshotRegistry()
    assert registry.register(first, at=REFERENCE_TIME) is True
    assert registry.register(first, at=REFERENCE_TIME) is False
    assert len(registry) == 1
    assert registry.get(first.task_id, first.profile_id) == first
    assert registry.digests == (first.digest,)

    changed = profile_document()
    changed["identity"]["model"]["version"] = "2026-07-20"
    conflicting = validate_profile_snapshot(
        seal_profile_snapshot(changed),
        at=REFERENCE_TIME,
    )
    with pytest.raises(ProfileIdentityConflict, match="conflicting snapshot"):
        registry.register(conflicting, at=REFERENCE_TIME)
    assert len(registry) == 1


def test_registry_revalidates_snapshot_objects_and_freshness() -> None:
    snapshot = validate_profile_snapshot(sealed_profile(), at=REFERENCE_TIME)
    registry = ProfileSnapshotRegistry()
    forged = replace(snapshot, digest="0" * 64)
    with pytest.raises(ProfileSnapshotError, match="does not match its canonical evidence"):
        registry.register(forged, at=REFERENCE_TIME)
    with pytest.raises(ProfileSnapshotError, match="snapshot is stale"):
        registry.register(snapshot, at="2026-07-29T12:00:00Z")
    with pytest.raises(ProfileSnapshotError, match="exact ProfileSnapshot"):
        registry.register(object(), at=REFERENCE_TIME)  # type: ignore[arg-type]


@pytest.mark.parametrize("secret_field", ["api_key", "access_token", "password", "secret"])
def test_secret_fields_are_rejected_before_generic_unknown_field_handling(
    secret_field: str,
) -> None:
    document = profile_document()
    document["identity"]["provider"][secret_field] = "must-not-enter-a-snapshot"
    with pytest.raises(ProfileSnapshotError, match="secret fields are forbidden"):
        seal_profile_snapshot(document)


def test_duplicate_json_keys_nonfinite_values_and_unsafe_source_types_are_rejected() -> None:
    with pytest.raises(ProfileSnapshotError, match="duplicate key"):
        validate_profile_snapshot('{"schema_version":"a","schema_version":"b"}')
    with pytest.raises(ProfileSnapshotError, match="not supported"):
        validate_profile_snapshot('{"schema_version":NaN}')
    with pytest.raises(ProfileSnapshotError, match="expected an object"):
        validate_profile_snapshot("[]")
    with pytest.raises(ProfileSnapshotError, match="valid UTF-8"):
        validate_profile_snapshot(b"\xff")

    class DictionarySubclass(dict[str, Any]):
        pass

    with pytest.raises(ProfileSnapshotError, match="source must be"):
        validate_profile_snapshot(DictionarySubclass(profile_document()))  # type: ignore[arg-type]


def test_schema_identifiers_strings_timestamps_and_reference_clock_are_strict() -> None:
    document = profile_document()
    document["schema_version"] = "finite.profile-snapshot/v2"
    with pytest.raises(ProfileSnapshotError, match="schema_version"):
        seal_profile_snapshot(document)

    document = profile_document()
    document["identity"]["task_id"] = "bad task id"
    with pytest.raises(ProfileSnapshotError, match="malformed identifier"):
        seal_profile_snapshot(document)

    document = profile_document()
    document["identity"]["adapter"]["name"] = "line\nbreak"
    with pytest.raises(ProfileSnapshotError, match="control characters"):
        seal_profile_snapshot(document)

    document = profile_document()
    document["freshness"]["snapshot_at"] = "2026-07-21T12:00:00-04:00"
    with pytest.raises(ProfileSnapshotError, match="RFC 3339 UTC seconds"):
        seal_profile_snapshot(document)

    sealed = sealed_profile()
    with pytest.raises(ProfileSnapshotError, match="timezone-aware"):
        validate_profile_snapshot(sealed, at=datetime(2026, 7, 22))
    assert validate_profile_snapshot(
        sealed,
        at=datetime(2026, 7, 22, 12, tzinfo=UTC),
    ).is_fresh(REFERENCE_TIME)


def test_sealing_requires_unsealed_input_and_validation_requires_digest() -> None:
    sealed = sealed_profile()
    with pytest.raises(ProfileSnapshotError, match="expects an unsealed"):
        seal_profile_snapshot(sealed)

    with pytest.raises(ProfileSnapshotError, match="missing required fields.*snapshot_digest"):
        validate_profile_snapshot(profile_document(), at=REFERENCE_TIME)


def test_failure_and_quality_calibration_integer_bounds_are_enforced() -> None:
    for section, field in (
        ("quality", "expected_calibration_error_ppm"),
        ("failure", "observed_failure_rate_ppm"),
        ("failure", "brier_score_ppm"),
    ):
        document = profile_document()
        document["calibration"][section][field] = 1_000_001
        with pytest.raises(ProfileSnapshotError, match="1000000"):
            seal_profile_snapshot(document)
