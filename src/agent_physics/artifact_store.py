"""Durable, content-addressed artifacts with referentially checked lineage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, cast

from .artifacts import ARTIFACT_SCHEMA_VERSION, Artifact, Sensitivity
from .serialization import canonical_json, content_digest


STORE_SCHEMA_VERSION = 1
PROVENANCE_SCHEMA_VERSION = "finite-artifact-provenance/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ADDRESS = re.compile(r"^sha256:[0-9a-f]{64}$")


class ArtifactStoreError(RuntimeError):
    """Base class for durable artifact-store failures."""


class ArtifactNotFound(ArtifactStoreError):
    """Raised when a requested artifact is absent."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Raised when durable bytes, metadata, or lineage do not verify."""


class ArtifactConflict(ArtifactStoreError):
    """Raised when an immutable identity is reused with different content."""


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    """Attempt-linked transformation record for one durable artifact."""

    artifact_id: str
    run_id: str
    task_id: str
    attempt: int
    producer_event_digest: str
    transformation_digest: str
    input_artifact_ids: tuple[str, ...]
    record_digest: str
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        run_id: str,
        task_id: str,
        attempt: int,
        producer_event_digest: str,
        transformation_digest: str,
        input_artifact_ids: tuple[str, ...],
    ) -> ArtifactProvenance:
        normalized_inputs = tuple(sorted(input_artifact_ids))
        unsigned = {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "run_id": run_id,
            "task_id": task_id,
            "attempt": attempt,
            "producer_event_digest": producer_event_digest,
            "transformation_digest": transformation_digest,
            "input_artifact_ids": normalized_inputs,
        }
        value = cls(**unsigned, record_digest=content_digest(unsigned))
        if not value.verify():
            raise ValueError("artifact provenance is malformed")
        return value

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "producer_event_digest": self.producer_event_digest,
            "transformation_digest": self.transformation_digest,
            "input_artifact_ids": self.input_artifact_ids,
        }

    def verify(self) -> bool:
        return (
            self.schema_version == PROVENANCE_SCHEMA_VERSION
            and _ADDRESS.fullmatch(self.artifact_id) is not None
            and isinstance(self.run_id, str)
            and bool(self.run_id)
            and isinstance(self.task_id, str)
            and bool(self.task_id)
            and type(self.attempt) is int
            and self.attempt > 0
            and _SHA256.fullmatch(self.producer_event_digest) is not None
            and _SHA256.fullmatch(self.transformation_digest) is not None
            and tuple(sorted(set(self.input_artifact_ids))) == self.input_artifact_ids
            and all(_ADDRESS.fullmatch(value) is not None for value in self.input_artifact_ids)
            and self.record_digest == content_digest(self.unsigned_payload())
        )


@dataclass(frozen=True, slots=True)
class ArtifactStoreVerification:
    artifact_count: int
    provenance_count: int
    passed: bool
    failures: tuple[str, ...]
    verification_digest: str

    def verify_digest(self) -> bool:
        return self.verification_digest == content_digest(
            {
                "artifact_count": self.artifact_count,
                "provenance_count": self.provenance_count,
                "passed": self.passed,
                "failures": self.failures,
            }
        )


def _artifact_metadata(artifact: Artifact) -> dict[str, object]:
    return {
        "record_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_id": artifact.artifact_id,
        "schema": artifact.schema,
        "schema_version": artifact.schema_version,
        "media_type": artifact.media_type,
        "producer": artifact.producer,
        "parents": list(artifact.parents),
        "sensitivity": artifact.sensitivity.value,
        "created_at_ms": artifact.created_at_ms,
        "fresh_until_ms": artifact.fresh_until_ms,
        "payload_sha256": artifact.payload_sha256,
        "payload_size": len(artifact.payload),
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate metadata field {key!r}")
        result[key] = value
    return result


def _decode_metadata(value: str) -> dict[str, object]:
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite constant {constant!r}")
            ),
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ArtifactIntegrityError("artifact metadata is not strict JSON") from exc
    if not isinstance(decoded, dict):
        raise ArtifactIntegrityError("artifact metadata must be an object")
    return cast(dict[str, object], decoded)


class SQLiteArtifactStore:
    """Atomic SQLite blob store with restart-safe deduplication and parent FKs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifact_store_schema (
                    version INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_parents (
                    child_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                    PRIMARY KEY (child_id, parent_id),
                    UNIQUE (child_id, ordinal),
                    FOREIGN KEY (child_id) REFERENCES artifacts(artifact_id),
                    FOREIGN KEY (parent_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS artifact_provenance (
                    artifact_id TEXT PRIMARY KEY,
                    provenance_json TEXT NOT NULL,
                    record_digest TEXT NOT NULL,
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                );
                """
            )
            versions = tuple(
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM artifact_store_schema ORDER BY version"
                )
            )
            if not versions:
                connection.execute(
                    "INSERT INTO artifact_store_schema(version) VALUES (?)",
                    (STORE_SCHEMA_VERSION,),
                )
            elif versions != (STORE_SCHEMA_VERSION,):
                raise ArtifactStoreError(f"unsupported artifact store schema: {versions!r}")

    @staticmethod
    def _provenance_json(provenance: ArtifactProvenance) -> str:
        payload = provenance.unsigned_payload()
        payload["record_digest"] = provenance.record_digest
        return canonical_json(payload)

    def put(
        self,
        artifact: Artifact,
        *,
        provenance: ArtifactProvenance | None = None,
    ) -> bool:
        """Persist once; return ``False`` for an exact durable duplicate."""

        if type(artifact) is not Artifact or not artifact.verify():
            raise ArtifactIntegrityError("artifact failed content-address verification")
        if provenance is not None:
            if type(provenance) is not ArtifactProvenance or not provenance.verify():
                raise ArtifactIntegrityError("artifact provenance failed verification")
            if provenance.artifact_id != artifact.artifact_id:
                raise ArtifactIntegrityError("provenance output does not match artifact")
            if provenance.input_artifact_ids != artifact.parents:
                raise ArtifactIntegrityError("provenance inputs do not match artifact parents")

        metadata_json = canonical_json(_artifact_metadata(artifact))
        with self._transaction() as connection:
            missing = [
                parent
                for parent in artifact.parents
                if connection.execute(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ?", (parent,)
                ).fetchone()
                is None
            ]
            if missing:
                raise ArtifactIntegrityError(f"artifact parents are missing: {missing!r}")
            existing = connection.execute(
                "SELECT metadata_json, payload, payload_sha256 FROM artifacts "
                "WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            inserted = existing is None
            if existing is None:
                connection.execute(
                    "INSERT INTO artifacts(artifact_id, metadata_json, payload, payload_sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        artifact.artifact_id,
                        metadata_json,
                        artifact.payload,
                        artifact.payload_sha256,
                    ),
                )
                connection.executemany(
                    "INSERT INTO artifact_parents(child_id, parent_id, ordinal) VALUES (?, ?, ?)",
                    (
                        (artifact.artifact_id, parent, index)
                        for index, parent in enumerate(artifact.parents)
                    ),
                )
            elif (
                existing["metadata_json"] != metadata_json
                or bytes(existing["payload"]) != artifact.payload
                or existing["payload_sha256"] != artifact.payload_sha256
            ):
                raise ArtifactConflict("artifact ID already exists with different content")

            existing_provenance = connection.execute(
                "SELECT provenance_json FROM artifact_provenance WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            supplied_json = self._provenance_json(provenance) if provenance is not None else None
            if existing_provenance is None and supplied_json is not None:
                connection.execute(
                    "INSERT INTO artifact_provenance(artifact_id, provenance_json, record_digest) "
                    "VALUES (?, ?, ?)",
                    (artifact.artifact_id, supplied_json, provenance.record_digest),
                )
            elif existing_provenance is not None and (
                supplied_json is None or existing_provenance["provenance_json"] != supplied_json
            ):
                raise ArtifactConflict("artifact provenance differs from the durable record")
            return inserted

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row, parents: tuple[str, ...]) -> Artifact:
        metadata = _decode_metadata(row["metadata_json"])
        expected_fields = {
            "record_schema_version",
            "artifact_id",
            "schema",
            "schema_version",
            "media_type",
            "producer",
            "parents",
            "sensitivity",
            "created_at_ms",
            "fresh_until_ms",
            "payload_sha256",
            "payload_size",
        }
        if set(metadata) != expected_fields:
            raise ArtifactIntegrityError("artifact metadata fields are not exact")
        if metadata["record_schema_version"] != ARTIFACT_SCHEMA_VERSION:
            raise ArtifactIntegrityError("artifact metadata schema is unsupported")
        if metadata["parents"] != list(parents):
            raise ArtifactIntegrityError("artifact parent rows differ from metadata")
        payload = bytes(row["payload"])
        if type(metadata["payload_size"]) is not int or metadata["payload_size"] != len(payload):
            raise ArtifactIntegrityError("artifact payload size differs from metadata")
        try:
            artifact = Artifact(
                artifact_id=cast(str, metadata["artifact_id"]),
                schema=cast(str, metadata["schema"]),
                schema_version=cast(str, metadata["schema_version"]),
                media_type=cast(str, metadata["media_type"]),
                producer=cast(str, metadata["producer"]),
                parents=parents,
                sensitivity=Sensitivity(cast(str, metadata["sensitivity"])),
                created_at_ms=cast(int, metadata["created_at_ms"]),
                fresh_until_ms=cast(int | None, metadata["fresh_until_ms"]),
                payload=payload,
                payload_sha256=cast(str, metadata["payload_sha256"]),
            )
        except (TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("artifact metadata values are malformed") from exc
        if artifact.artifact_id != row["artifact_id"] or not artifact.verify():
            raise ArtifactIntegrityError("artifact bytes or metadata failed verification")
        if row["payload_sha256"] != artifact.payload_sha256:
            raise ArtifactIntegrityError("artifact payload digest column differs")
        return artifact

    def get(self, artifact_id: str) -> Artifact:
        if type(artifact_id) is not str or _ADDRESS.fullmatch(artifact_id) is None:
            raise ValueError("artifact_id must be a sha256 address")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ArtifactNotFound(artifact_id)
            parents = tuple(
                parent["parent_id"]
                for parent in connection.execute(
                    "SELECT parent_id FROM artifact_parents WHERE child_id = ? ORDER BY ordinal",
                    (artifact_id,),
                )
            )
            return self._artifact_from_row(row, parents)
        finally:
            connection.close()

    def provenance(self, artifact_id: str) -> ArtifactProvenance | None:
        self.get(artifact_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT provenance_json, record_digest FROM artifact_provenance "
                "WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                return None
            value = _decode_metadata(row["provenance_json"])
            expected = {
                "schema_version",
                "artifact_id",
                "run_id",
                "task_id",
                "attempt",
                "producer_event_digest",
                "transformation_digest",
                "input_artifact_ids",
                "record_digest",
            }
            if set(value) != expected or not isinstance(value["input_artifact_ids"], list):
                raise ArtifactIntegrityError("artifact provenance fields are not exact")
            provenance = ArtifactProvenance(
                schema_version=cast(str, value["schema_version"]),
                artifact_id=cast(str, value["artifact_id"]),
                run_id=cast(str, value["run_id"]),
                task_id=cast(str, value["task_id"]),
                attempt=cast(int, value["attempt"]),
                producer_event_digest=cast(str, value["producer_event_digest"]),
                transformation_digest=cast(str, value["transformation_digest"]),
                input_artifact_ids=tuple(cast(list[str], value["input_artifact_ids"])),
                record_digest=cast(str, value["record_digest"]),
            )
            if row["record_digest"] != provenance.record_digest or not provenance.verify():
                raise ArtifactIntegrityError("artifact provenance failed verification")
            return provenance
        finally:
            connection.close()

    def artifact_ids(self) -> tuple[str, ...]:
        connection = self._connect()
        try:
            return tuple(
                row["artifact_id"]
                for row in connection.execute(
                    "SELECT artifact_id FROM artifacts ORDER BY artifact_id"
                )
            )
        finally:
            connection.close()

    def verify_all(self) -> ArtifactStoreVerification:
        failures: list[str] = []
        artifact_ids = self.artifact_ids()
        provenance_count = 0
        connection = self._connect()
        try:
            foreign_key_failures = tuple(connection.execute("PRAGMA foreign_key_check"))
            if foreign_key_failures:
                failures.append("SQLite foreign-key integrity failed")
            provenance_count = cast(
                int,
                connection.execute("SELECT COUNT(*) FROM artifact_provenance").fetchone()[0],
            )
        finally:
            connection.close()
        for artifact_id in artifact_ids:
            try:
                self.get(artifact_id)
                self.provenance(artifact_id)
            except (ArtifactStoreError, ValueError) as exc:
                failures.append(f"{artifact_id}: {exc}")
        normalized = tuple(sorted(failures))
        unsigned = {
            "artifact_count": len(artifact_ids),
            "provenance_count": provenance_count,
            "passed": not normalized,
            "failures": normalized,
        }
        return ArtifactStoreVerification(
            **unsigned,
            verification_digest=content_digest(unsigned),
        )


def transformation_digest(*, revision: str, parameters: object) -> str:
    """Bind a transformation implementation revision and canonical parameters."""

    if not revision:
        raise ValueError("revision is required")
    return hashlib.sha256(
        canonical_json({"revision": revision, "parameters": parameters}).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ArtifactConflict",
    "ArtifactIntegrityError",
    "ArtifactNotFound",
    "ArtifactProvenance",
    "ArtifactStoreError",
    "ArtifactStoreVerification",
    "SQLiteArtifactStore",
    "transformation_digest",
]
