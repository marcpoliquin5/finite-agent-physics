from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.artifact_store import (
    ArtifactConflict,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactProvenance,
    SQLiteArtifactStore,
    transformation_digest,
)
from agent_physics.artifacts import Artifact, Sensitivity


def _artifact(payload: bytes, *, parents: tuple[str, ...] = ()) -> Artifact:
    return Artifact.create(
        payload,
        schema="stormshift.test",
        schema_version="1.0.0",
        media_type="application/json",
        producer="test-worker",
        parents=parents,
        sensitivity=Sensitivity.INTERNAL,
        created_at_ms=1_000,
        fresh_until_ms=10_000,
    )


def _provenance(artifact: Artifact) -> ArtifactProvenance:
    return ArtifactProvenance.create(
        artifact_id=artifact.artifact_id,
        run_id="run-artifacts-1",
        task_id="derive",
        attempt=1,
        producer_event_digest="a" * 64,
        transformation_digest=transformation_digest(
            revision="derive/v1", parameters={"safe": True}
        ),
        input_artifact_ids=artifact.parents,
    )


def test_put_get_deduplicate_restart_and_attempt_lineage(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    first = SQLiteArtifactStore(path)
    parent = _artifact(b'{"source":"fixture"}')
    child = _artifact(b'{"derived":true}', parents=(parent.artifact_id,))
    provenance = _provenance(child)

    assert first.put(parent) is True
    assert first.put(child, provenance=provenance) is True
    assert first.put(child, provenance=provenance) is False

    restarted = SQLiteArtifactStore(path)
    assert restarted.get(parent.artifact_id) == parent
    assert restarted.get(child.artifact_id) == child
    assert restarted.provenance(child.artifact_id) == provenance
    assert restarted.artifact_ids() == tuple(sorted((parent.artifact_id, child.artifact_id)))
    verification = restarted.verify_all()
    assert verification.passed is True
    assert verification.artifact_count == 2
    assert verification.provenance_count == 1
    assert verification.verify_digest() is True


def test_missing_parent_and_mismatched_provenance_fail_atomically(tmp_path: Path) -> None:
    store = SQLiteArtifactStore(tmp_path / "missing.sqlite3")
    parent = _artifact(b"parent")
    child = _artifact(b"child", parents=(parent.artifact_id,))

    with pytest.raises(ArtifactIntegrityError, match="parents are missing"):
        store.put(child, provenance=_provenance(child))
    assert store.artifact_ids() == ()

    store.put(parent)
    unrelated = _artifact(b"unrelated")
    bad = replace(_provenance(child), artifact_id=unrelated.artifact_id)
    with pytest.raises(ArtifactIntegrityError, match="failed verification|does not match"):
        store.put(child, provenance=bad)
    with pytest.raises(ArtifactNotFound):
        store.get(child.artifact_id)


def test_durable_identity_cannot_lose_or_change_provenance(tmp_path: Path) -> None:
    store = SQLiteArtifactStore(tmp_path / "conflict.sqlite3")
    artifact = _artifact(b"value")
    provenance = _provenance(artifact)
    store.put(artifact, provenance=provenance)

    with pytest.raises(ArtifactConflict, match="provenance differs"):
        store.put(artifact)
    conflicting = ArtifactProvenance.create(
        artifact_id=artifact.artifact_id,
        run_id="run-artifacts-1",
        task_id="different-task",
        attempt=1,
        producer_event_digest="a" * 64,
        transformation_digest="b" * 64,
        input_artifact_ids=(),
    )
    with pytest.raises(ArtifactConflict, match="provenance differs"):
        store.put(artifact, provenance=conflicting)


def test_payload_and_metadata_tampering_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "tamper.sqlite3"
    store = SQLiteArtifactStore(path)
    artifact = _artifact(b"original")
    store.put(artifact)

    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE artifacts SET payload = ? WHERE artifact_id = ?",
        (b"tampered", artifact.artifact_id),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ArtifactIntegrityError):
        store.get(artifact.artifact_id)
    report = store.verify_all()
    assert report.passed is False
    assert report.failures
    assert report.verify_digest()

    path2 = tmp_path / "metadata.sqlite3"
    other_store = SQLiteArtifactStore(path2)
    other_store.put(artifact)
    connection = sqlite3.connect(path2)
    metadata = json.loads(
        connection.execute(
            "SELECT metadata_json FROM artifacts WHERE artifact_id = ?",
            (artifact.artifact_id,),
        ).fetchone()[0]
    )
    metadata["undeclared"] = True
    connection.execute(
        "UPDATE artifacts SET metadata_json = ? WHERE artifact_id = ?",
        (json.dumps(metadata), artifact.artifact_id),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ArtifactIntegrityError, match="fields are not exact"):
        other_store.get(artifact.artifact_id)


def test_provenance_rejects_boolean_attempt_and_noncanonical_inputs() -> None:
    artifact = _artifact(b"value")
    valid = _provenance(artifact)

    assert valid.verify()
    assert not replace(valid, attempt=True).verify()
    assert not replace(
        valid,
        input_artifact_ids=("sha256:" + "f" * 64, "sha256:" + "f" * 64),
    ).verify()
