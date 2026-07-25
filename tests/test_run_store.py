from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import agent_physics.run_store as run_store_module

from agent_physics.run_store import (
    EventConflict,
    RunDefinitionConflict,
    SCHEMA_VERSION,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLiteRunStore,
    Usage,
    UsageRecord,
)


def _create_run(store: SQLiteRunStore, run_id: str = "run-1"):
    return store.get_or_create_run(
        run_id=run_id,
        graph_digest="graph-v1",
        envelope={"deadline_ms": 5_000, "max_parallelism": 2},
        deadline_at_ms=10_000,
    )


def test_connections_allow_bounded_contention_within_live_request_deadline(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "busy-timeout.db")
    connection = store._connect()  # noqa: SLF001
    try:
        configured = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert SQLITE_BUSY_TIMEOUT_MS == 30_000
    assert configured == SQLITE_BUSY_TIMEOUT_MS


def test_schema_migration_and_database_enforced_append_only_tables(tmp_path: Path) -> None:
    database = tmp_path / "runs.db"
    store = SQLiteRunStore(database, clock_ms=lambda: 100)
    _create_run(store)
    store.append_event(
        run_id="run-1",
        event_id="event-1",
        event_type="run.started",
    )

    assert store.schema_versions() == (1, SCHEMA_VERSION)
    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(run_events)").fetchall()
        }
        assert {
            "estimated_tokens",
            "reserved_tokens",
            "actual_tokens",
            "estimated_cost_microusd",
            "reserved_cost_microusd",
            "actual_cost_microusd",
            "estimated_context_bytes",
            "reserved_context_bytes",
            "actual_context_bytes",
        } <= columns
        run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(run_definitions)").fetchall()
        }
        assert {"manifest_digest", "manifest_revision"} <= run_columns
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE run_events SET event_type = 'forged' WHERE event_id = 'event-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM run_definitions WHERE run_id = 'run-1'")
    finally:
        connection.close()


def test_existing_v1_database_is_migrated_to_manifest_and_completion_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v1-runs.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at_ms INTEGER NOT NULL
            )
            """
        )
        for statement in run_store_module._INITIAL_SCHEMA:
            connection.execute(statement)
        checksum = hashlib.sha256(
            "\n".join(
                statement.strip() for statement in run_store_module._INITIAL_SCHEMA
            ).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at_ms)
            VALUES (1, 'initial_append_only_run_ledger', ?, 100)
            """,
            (checksum,),
        )
        connection.commit()
    finally:
        connection.close()

    store = SQLiteRunStore(database)
    assert store.schema_versions() == (1, 2)
    run = _create_run(store)
    assert run.manifest_digest == "unspecified"
    assert run.manifest_revision == 1


def test_event_append_is_idempotent_and_conflicts_are_rejected(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db", clock_ms=lambda: 100)
    original_run = _create_run(store)
    replayed_run = store.get_or_create_run(
        run_id="run-1",
        graph_digest="graph-v1",
        envelope={"max_parallelism": 2, "deadline_ms": 5_000},
        deadline_at_ms=99_999,
    )
    assert replayed_run == original_run
    with pytest.raises(RunDefinitionConflict):
        store.get_or_create_run(
            run_id="run-1",
            graph_digest="different-graph",
            envelope={"deadline_ms": 5_000, "max_parallelism": 2},
            deadline_at_ms=10_000,
        )

    usage = UsageRecord(
        estimated=Usage(tokens=10, cost_microusd=20, context_bytes=30),
        reserved=Usage(tokens=11, cost_microusd=21, context_bytes=31),
        actual=Usage(tokens=8, cost_microusd=18, context_bytes=28),
    )
    first = store.append_event(
        run_id="run-1",
        event_id="stable-id",
        event_type="fixture.observed",
        task_id="task-a",
        attempt=1,
        payload={"output": {"answer": 42}},
        usage=usage,
    )
    replay = store.append_event(
        run_id="run-1",
        event_id="stable-id",
        event_type="fixture.observed",
        task_id="task-a",
        attempt=1,
        payload={"output": {"answer": 42}},
        usage=usage,
    )
    assert replay == first
    assert first.sequence == 1
    assert first.usage == usage
    with pytest.raises(EventConflict):
        store.append_event(
            run_id="run-1",
            event_id="stable-id",
            event_type="fixture.observed",
            task_id="task-a",
            attempt=1,
            payload={"output": {"answer": 43}},
            usage=usage,
        )


def test_sequence_allocation_is_monotonic_under_concurrent_appends(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db")
    _create_run(store)

    def append(index: int) -> None:
        store.append_event(
            run_id="run-1",
            event_id=f"event-{index}",
            event_type="fixture.event",
            payload={"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))

    events = store.events("run-1")
    assert tuple(event.sequence for event in events) == tuple(range(1, 25))
    assert {event.event_id for event in events} == {f"event-{index}" for index in range(24)}


def test_completed_outputs_are_reconstructed_with_actual_usage(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db", clock_ms=lambda: 100)
    _create_run(store)
    started = store.start_attempt(
        run_id="run-1",
        task_id="task-a",
        provider="fixture-provider",
        backend="fixture-fast",
        estimated=Usage(tokens=100, cost_microusd=50, context_bytes=1_000),
        reserved=Usage(tokens=100, cost_microusd=50, context_bytes=1_000),
    )
    assert started.attempt == 1
    actual = Usage(tokens=81, cost_microusd=40, context_bytes=700)
    store.complete_attempt(
        run_id="run-1",
        task_id="task-a",
        attempt=1,
        output={"status": "safe"},
        estimated=Usage(tokens=100, cost_microusd=50, context_bytes=1_000),
        reserved=Usage(tokens=100, cost_microusd=50, context_bytes=1_000),
        actual=actual,
    )

    completion = store.completed_tasks("run-1")["task-a"]
    assert completion.output == {"status": "safe"}
    assert completion.event.usage.actual == actual


def test_success_and_resumable_completion_append_atomically(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db", clock_ms=lambda: 100)
    _create_run(store)
    store.start_attempt(
        run_id="run-1",
        task_id="task-a",
        provider="fixture-provider",
        backend="fixture-fast",
        estimated=Usage(tokens=10),
        reserved=Usage(tokens=10),
    )
    store.complete_attempt(
        run_id="run-1",
        task_id="task-a",
        attempt=1,
        output={"first": True},
        estimated=Usage(tokens=10),
        reserved=Usage(tokens=10),
        actual=Usage(tokens=8),
    )
    store.start_attempt(
        run_id="run-1",
        task_id="task-a",
        provider="fixture-provider",
        backend="fixture-fast",
        estimated=Usage(tokens=10),
        reserved=Usage(tokens=10),
    )

    with pytest.raises(EventConflict):
        store.complete_attempt(
            run_id="run-1",
            task_id="task-a",
            attempt=2,
            output={"safe": True},
            estimated=Usage(tokens=10),
            reserved=Usage(tokens=10),
            actual=Usage(tokens=8),
        )

    succeeded = [
        event for event in store.events("run-1") if event.event_type == "task.attempt_succeeded"
    ]
    assert [event.attempt for event in succeeded] == [1]

    store.append_event(
        run_id="run-1",
        event_id="run-1:task-a:attempt:2:succeeded",
        event_type="task.attempt_succeeded",
        task_id="task-a",
        attempt=2,
        payload={"output": {"second": True}, "output_validated": True},
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.append_event(
            run_id="run-1",
            event_id="different-completion-id",
            event_type="task.completed",
            task_id="task-a",
            attempt=2,
            payload={"output": {"second": True}, "kind": "fixture_output"},
        )


def test_completion_requires_a_success_or_effect_intent_source(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db")
    _create_run(store)
    store.start_attempt(
        run_id="run-1",
        task_id="unfinished",
        provider="fixture-provider",
        backend="fixture-fast",
        estimated=Usage(tokens=10),
        reserved=Usage(tokens=10),
    )
    with pytest.raises(sqlite3.IntegrityError, match="requires a succeeded attempt"):
        store.append_event(
            run_id="run-1",
            event_id="forged-completion",
            event_type="task.completed",
            task_id="unfinished",
            attempt=1,
            payload={"output": {"forged": True}, "kind": "fixture_output"},
        )
