"""Append-only SQLite persistence for resumable fixture executions.

The store is an event ledger, not a mutable task-state table. Run and attempt
state is reconstructed from immutable events. SQLite serializes sequence
allocation per database; it does not provide a distributed execution lease.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast


SCHEMA_VERSION: Final[int] = 2


class RunStoreError(RuntimeError):
    """Base class for durable run-store failures."""


class RunNotFound(RunStoreError):
    """The requested run is absent."""


class RunDefinitionConflict(RunStoreError):
    """A run ID was reused with a different graph or envelope."""


class EventConflict(RunStoreError):
    """An event ID was replayed with different immutable content."""


@dataclass(frozen=True, slots=True)
class Usage:
    """One explicit token, money, and context accounting vector."""

    tokens: int = 0
    cost_microusd: int = 0
    context_bytes: int = 0

    def __post_init__(self) -> None:
        if min(self.tokens, self.cost_microusd, self.context_bytes) < 0:
            raise ValueError("usage values cannot be negative")

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            tokens=self.tokens + other.tokens,
            cost_microusd=self.cost_microusd + other.cost_microusd,
            context_bytes=self.context_bytes + other.context_bytes,
        )


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Estimated, reserved, and actual usage kept as separate quantities."""

    estimated: Usage = Usage()
    reserved: Usage = Usage()
    actual: Usage = Usage()


@dataclass(frozen=True, slots=True)
class RunDefinition:
    run_id: str
    graph_digest: str
    envelope_json: str
    manifest_digest: str
    manifest_revision: int
    deadline_at_ms: int
    created_at_ms: int

    @property
    def envelope(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.envelope_json))


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One immutable event in a monotonically sequenced run ledger."""

    run_id: str
    sequence: int
    event_id: str
    event_type: str
    task_id: str | None
    attempt: int | None
    occurred_at_ms: int
    payload_json: str
    usage: UsageRecord

    @property
    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.payload_json))


@dataclass(frozen=True, slots=True)
class CompletedTask:
    """Latest durable completion record used to skip work after restart."""

    task_id: str
    output_json: str
    event: RunEvent

    @property
    def output(self) -> object:
        return json.loads(self.output_json)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("run-store values must be canonical JSON data") from exc


_INITIAL_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE run_definitions (
        run_id TEXT PRIMARY KEY,
        graph_digest TEXT NOT NULL,
        envelope_json TEXT NOT NULL,
        deadline_at_ms INTEGER NOT NULL,
        created_at_ms INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE run_events (
        run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        task_id TEXT,
        attempt INTEGER,
        occurred_at_ms INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        estimated_tokens INTEGER NOT NULL,
        estimated_cost_microusd INTEGER NOT NULL,
        estimated_context_bytes INTEGER NOT NULL,
        reserved_tokens INTEGER NOT NULL,
        reserved_cost_microusd INTEGER NOT NULL,
        reserved_context_bytes INTEGER NOT NULL,
        actual_tokens INTEGER NOT NULL,
        actual_cost_microusd INTEGER NOT NULL,
        actual_context_bytes INTEGER NOT NULL,
        PRIMARY KEY (run_id, sequence),
        FOREIGN KEY (run_id) REFERENCES run_definitions(run_id),
        CHECK (sequence > 0),
        CHECK (attempt IS NULL OR attempt > 0),
        CHECK (
            MIN(
                estimated_tokens,
                estimated_cost_microusd,
                estimated_context_bytes,
                reserved_tokens,
                reserved_cost_microusd,
                reserved_context_bytes,
                actual_tokens,
                actual_cost_microusd,
                actual_context_bytes
            ) >= 0
        )
    )
    """,
    "CREATE INDEX idx_run_events_task ON run_events(run_id, task_id, sequence)",
    """
    CREATE TRIGGER schema_migrations_no_update
    BEFORE UPDATE ON schema_migrations
    BEGIN
        SELECT RAISE(ABORT, 'schema migrations are append-only');
    END
    """,
    """
    CREATE TRIGGER schema_migrations_no_delete
    BEFORE DELETE ON schema_migrations
    BEGIN
        SELECT RAISE(ABORT, 'schema migrations are append-only');
    END
    """,
    """
    CREATE TRIGGER run_definitions_no_update
    BEFORE UPDATE ON run_definitions
    BEGIN
        SELECT RAISE(ABORT, 'run definitions are append-only');
    END
    """,
    """
    CREATE TRIGGER run_definitions_no_delete
    BEFORE DELETE ON run_definitions
    BEGIN
        SELECT RAISE(ABORT, 'run definitions are append-only');
    END
    """,
    """
    CREATE TRIGGER run_events_no_update
    BEFORE UPDATE ON run_events
    BEGIN
        SELECT RAISE(ABORT, 'run events are append-only');
    END
    """,
    """
    CREATE TRIGGER run_events_no_delete
    BEFORE DELETE ON run_events
    BEGIN
        SELECT RAISE(ABORT, 'run events are append-only');
    END
    """,
)

_MIGRATION_2: Final[tuple[str, ...]] = (
    """
    ALTER TABLE run_definitions
    ADD COLUMN manifest_digest TEXT NOT NULL DEFAULT 'unspecified'
    """,
    """
    ALTER TABLE run_definitions
    ADD COLUMN manifest_revision INTEGER NOT NULL DEFAULT 1
    """,
    """
    CREATE UNIQUE INDEX idx_run_task_single_completion
    ON run_events(run_id, task_id)
    WHERE event_type = 'task.completed'
    """,
    """
    CREATE TRIGGER run_events_completion_requires_source
    BEFORE INSERT ON run_events
    WHEN NEW.event_type = 'task.completed'
      AND (
        NEW.task_id IS NULL
        OR NOT EXISTS (
            SELECT 1
            FROM run_events AS source
            WHERE source.run_id = NEW.run_id
              AND source.task_id = NEW.task_id
              AND source.sequence < NEW.sequence
              AND (
                (
                    source.event_type = 'task.attempt_succeeded'
                    AND source.attempt = NEW.attempt
                )
                OR source.event_type = 'task.effect_intent_created'
              )
        )
      )
    BEGIN
        SELECT RAISE(
            ABORT,
            'task completion requires a succeeded attempt or effect intent'
        );
    END
    """,
)

_MIGRATIONS: Final[tuple[tuple[int, str, tuple[str, ...]], ...]] = (
    (1, "initial_append_only_run_ledger", _INITIAL_SCHEMA),
    (2, "manifest_and_completion_invariants", _MIGRATION_2),
)


class SQLiteRunStore:
    """SQLite append-only run ledger with idempotent event insertion."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("a resumable run store requires a filesystem database")
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def now_ms(self) -> int:
        return self._clock_ms()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at_ms INTEGER NOT NULL
                )
                """
            )
            try:
                connection.execute("BEGIN EXCLUSIVE")
                current_row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
                ).fetchone()
                current = int(current_row["version"])
                if current > SCHEMA_VERSION:
                    raise RunStoreError(
                        f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                    )
                for version, name, statements in _MIGRATIONS:
                    checksum = hashlib.sha256(
                        "\n".join(statement.strip() for statement in statements).encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    if version <= current:
                        recorded = connection.execute(
                            "SELECT checksum FROM schema_migrations WHERE version = ?",
                            (version,),
                        ).fetchone()
                        if recorded is None or recorded["checksum"] != checksum:
                            raise RunStoreError(
                                f"schema migration {version} checksum mismatch"
                            )
                        continue
                    if version == 2:
                        duplicate = connection.execute(
                            """
                            SELECT 1
                            FROM run_events
                            WHERE event_type = 'task.completed'
                            GROUP BY run_id, task_id
                            HAVING COUNT(*) > 1
                            LIMIT 1
                            """
                        ).fetchone()
                        unsupported = connection.execute(
                            """
                            SELECT 1
                            FROM run_events AS completion
                            WHERE completion.event_type = 'task.completed'
                              AND NOT EXISTS (
                                SELECT 1
                                FROM run_events AS source
                                WHERE source.run_id = completion.run_id
                                  AND source.task_id = completion.task_id
                                  AND source.sequence < completion.sequence
                                  AND (
                                    (
                                      source.event_type = 'task.attempt_succeeded'
                                      AND source.attempt = completion.attempt
                                    )
                                    OR source.event_type = 'task.effect_intent_created'
                                  )
                              )
                            LIMIT 1
                            """
                        ).fetchone()
                        if duplicate is not None or unsupported is not None:
                            raise RunStoreError(
                                "schema migration 2 refuses invalid historical completions"
                            )
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum, applied_at_ms)
                        VALUES (?, ?, ?, ?)
                        """,
                        (version, name, checksum, self.now_ms),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunDefinition:
        return RunDefinition(
            run_id=row["run_id"],
            graph_digest=row["graph_digest"],
            envelope_json=row["envelope_json"],
            manifest_digest=row["manifest_digest"],
            manifest_revision=row["manifest_revision"],
            deadline_at_ms=row["deadline_at_ms"],
            created_at_ms=row["created_at_ms"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> RunEvent:
        return RunEvent(
            run_id=row["run_id"],
            sequence=row["sequence"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            task_id=row["task_id"],
            attempt=row["attempt"],
            occurred_at_ms=row["occurred_at_ms"],
            payload_json=row["payload_json"],
            usage=UsageRecord(
                estimated=Usage(
                    tokens=row["estimated_tokens"],
                    cost_microusd=row["estimated_cost_microusd"],
                    context_bytes=row["estimated_context_bytes"],
                ),
                reserved=Usage(
                    tokens=row["reserved_tokens"],
                    cost_microusd=row["reserved_cost_microusd"],
                    context_bytes=row["reserved_context_bytes"],
                ),
                actual=Usage(
                    tokens=row["actual_tokens"],
                    cost_microusd=row["actual_cost_microusd"],
                    context_bytes=row["actual_context_bytes"],
                ),
            ),
        )

    def schema_versions(self) -> tuple[int, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            return tuple(row["version"] for row in rows)
        finally:
            connection.close()

    def get_or_create_run(
        self,
        *,
        run_id: str,
        graph_digest: str,
        envelope: Mapping[str, object],
        deadline_at_ms: int,
        manifest_digest: str = "unspecified",
        manifest_revision: int = 1,
    ) -> RunDefinition:
        """Insert an immutable run definition or validate an exact replay."""

        if not run_id or not graph_digest or not manifest_digest:
            raise ValueError("run_id, graph_digest, and manifest_digest are required")
        if manifest_revision <= 0:
            raise ValueError("manifest_revision must be positive")
        envelope_json = _canonical_json(dict(envelope))
        if deadline_at_ms <= 0:
            raise ValueError("deadline_at_ms must be positive")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM run_definitions WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is not None:
                existing = self._run_from_row(row)
                if (
                    existing.graph_digest != graph_digest
                    or existing.envelope_json != envelope_json
                    or existing.manifest_digest != manifest_digest
                    or existing.manifest_revision != manifest_revision
                ):
                    raise RunDefinitionConflict(
                        f"run {run_id!r} was defined with different immutable inputs"
                    )
                return existing
            now_ms = self.now_ms
            connection.execute(
                """
                INSERT INTO run_definitions (
                    run_id, graph_digest, envelope_json, manifest_digest,
                    manifest_revision, deadline_at_ms, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    graph_digest,
                    envelope_json,
                    manifest_digest,
                    manifest_revision,
                    deadline_at_ms,
                    now_ms,
                ),
            )
            row = connection.execute(
                "SELECT * FROM run_definitions WHERE run_id = ?", (run_id,)
            ).fetchone()
            return self._run_from_row(row)

    def get_run(self, run_id: str) -> RunDefinition:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM run_definitions WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RunNotFound(run_id)
            return self._run_from_row(row)
        finally:
            connection.close()

    @staticmethod
    def _immutable_event_values(
        *,
        run_id: str,
        event_type: str,
        task_id: str | None,
        attempt: int | None,
        payload_json: str,
        usage: UsageRecord,
    ) -> tuple[object, ...]:
        return (
            run_id,
            event_type,
            task_id,
            attempt,
            payload_json,
            usage.estimated.tokens,
            usage.estimated.cost_microusd,
            usage.estimated.context_bytes,
            usage.reserved.tokens,
            usage.reserved.cost_microusd,
            usage.reserved.context_bytes,
            usage.actual.tokens,
            usage.actual.cost_microusd,
            usage.actual.context_bytes,
        )

    @staticmethod
    def _row_immutable_event_values(row: sqlite3.Row) -> tuple[object, ...]:
        return (
            row["run_id"],
            row["event_type"],
            row["task_id"],
            row["attempt"],
            row["payload_json"],
            row["estimated_tokens"],
            row["estimated_cost_microusd"],
            row["estimated_context_bytes"],
            row["reserved_tokens"],
            row["reserved_cost_microusd"],
            row["reserved_context_bytes"],
            row["actual_tokens"],
            row["actual_cost_microusd"],
            row["actual_context_bytes"],
        )

    def _append_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_id: str,
        event_type: str,
        task_id: str | None,
        attempt: int | None,
        payload_json: str,
        usage: UsageRecord,
    ) -> RunEvent:
        existing = connection.execute(
            "SELECT * FROM run_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        supplied = self._immutable_event_values(
            run_id=run_id,
            event_type=event_type,
            task_id=task_id,
            attempt=attempt,
            payload_json=payload_json,
            usage=usage,
        )
        if existing is not None:
            if self._row_immutable_event_values(existing) != supplied:
                raise EventConflict(f"event ID {event_id!r} has different content")
            return self._event_from_row(existing)
        run_exists = connection.execute(
            "SELECT 1 FROM run_definitions WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run_exists is None:
            raise RunNotFound(run_id)
        next_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM run_events WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        sequence = int(next_row["next_sequence"])
        connection.execute(
            """
            INSERT INTO run_events (
                run_id, sequence, event_id, event_type, task_id, attempt,
                occurred_at_ms, payload_json,
                estimated_tokens, estimated_cost_microusd, estimated_context_bytes,
                reserved_tokens, reserved_cost_microusd, reserved_context_bytes,
                actual_tokens, actual_cost_microusd, actual_context_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                event_id,
                event_type,
                task_id,
                attempt,
                self.now_ms,
                payload_json,
                usage.estimated.tokens,
                usage.estimated.cost_microusd,
                usage.estimated.context_bytes,
                usage.reserved.tokens,
                usage.reserved.cost_microusd,
                usage.reserved.context_bytes,
                usage.actual.tokens,
                usage.actual.cost_microusd,
                usage.actual.context_bytes,
            ),
        )
        inserted = connection.execute(
            "SELECT * FROM run_events WHERE run_id = ? AND sequence = ?",
            (run_id, sequence),
        ).fetchone()
        return self._event_from_row(inserted)

    def append_event(
        self,
        *,
        run_id: str,
        event_id: str,
        event_type: str,
        task_id: str | None = None,
        attempt: int | None = None,
        payload: Mapping[str, object] | None = None,
        usage: UsageRecord = UsageRecord(),
    ) -> RunEvent:
        """Append once by event ID, allocating the next per-run sequence."""

        if not event_id or not event_type:
            raise ValueError("event_id and event_type are required")
        if attempt is not None and attempt <= 0:
            raise ValueError("attempt must be positive")
        payload_json = _canonical_json(dict(payload or {}))
        with self._transaction() as connection:
            return self._append_in_transaction(
                connection,
                run_id=run_id,
                event_id=event_id,
                event_type=event_type,
                task_id=task_id,
                attempt=attempt,
                payload_json=payload_json,
                usage=usage,
            )

    def start_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
        provider: str,
        backend: str,
        estimated: Usage,
        reserved: Usage,
    ) -> RunEvent:
        """Atomically allocate an attempt number and append its start record."""

        if not task_id or not provider or not backend:
            raise ValueError("task_id, provider, and backend are required")
        with self._transaction() as connection:
            attempt_row = connection.execute(
                """
                SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
                FROM run_events
                WHERE run_id = ? AND task_id = ? AND event_type = 'task.attempt_started'
                """,
                (run_id, task_id),
            ).fetchone()
            attempt = int(attempt_row["next_attempt"])
            return self._append_in_transaction(
                connection,
                run_id=run_id,
                event_id=f"{run_id}:{task_id}:attempt:{attempt}:started",
                event_type="task.attempt_started",
                task_id=task_id,
                attempt=attempt,
                payload_json=_canonical_json({"provider": provider, "backend": backend}),
                usage=UsageRecord(estimated=estimated, reserved=reserved),
            )

    def complete_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt: int,
        output: object,
        estimated: Usage,
        reserved: Usage,
        actual: Usage,
        output_kind: str = "fixture_output",
    ) -> tuple[RunEvent, RunEvent]:
        """Atomically append successful-attempt and resumable-completion records."""

        if not task_id or attempt <= 0 or not output_kind:
            raise ValueError("task_id, positive attempt, and output_kind are required")
        output_json = _canonical_json(output)
        output_value = json.loads(output_json)
        usage = UsageRecord(estimated=estimated, reserved=reserved, actual=actual)
        with self._transaction() as connection:
            succeeded = self._append_in_transaction(
                connection,
                run_id=run_id,
                event_id=f"{run_id}:{task_id}:attempt:{attempt}:succeeded",
                event_type="task.attempt_succeeded",
                task_id=task_id,
                attempt=attempt,
                payload_json=_canonical_json(
                    {"output": output_value, "output_validated": True}
                ),
                usage=usage,
            )
            completed = self._append_in_transaction(
                connection,
                run_id=run_id,
                event_id=f"{run_id}:{task_id}:completed",
                event_type="task.completed",
                task_id=task_id,
                attempt=attempt,
                payload_json=_canonical_json(
                    {"output": output_value, "kind": output_kind}
                ),
                usage=usage,
            )
            return succeeded, completed

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        connection = self._connect()
        try:
            if connection.execute(
                "SELECT 1 FROM run_definitions WHERE run_id = ?", (run_id,)
            ).fetchone() is None:
                raise RunNotFound(run_id)
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            return tuple(self._event_from_row(row) for row in rows)
        finally:
            connection.close()

    def completed_tasks(self, run_id: str) -> dict[str, CompletedTask]:
        """Return durable outputs that an executor must not recompute on restart."""

        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT event.*
                FROM run_events AS event
                JOIN (
                    SELECT task_id, MAX(sequence) AS sequence
                    FROM run_events
                    WHERE run_id = ? AND event_type = 'task.completed'
                    GROUP BY task_id
                ) AS latest
                  ON event.run_id = ?
                 AND event.task_id = latest.task_id
                 AND event.sequence = latest.sequence
                ORDER BY event.task_id
                """,
                (run_id, run_id),
            ).fetchall()
            completed: dict[str, CompletedTask] = {}
            for row in rows:
                event = self._event_from_row(row)
                payload = event.payload
                if "output" not in payload:
                    raise RunStoreError(
                        f"task completion {event.event_id!r} has no output payload"
                    )
                completed[cast(str, event.task_id)] = CompletedTask(
                    task_id=cast(str, event.task_id),
                    output_json=_canonical_json(payload["output"]),
                    event=event,
                )
            return completed
        finally:
            connection.close()
