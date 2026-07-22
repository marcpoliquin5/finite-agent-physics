from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_physics.contracts import BackendProfile, RunEnvelope, TaskContract
from agent_physics.executor import (
    AsyncGraphExecutor,
    RetryableWorkerError,
    RetryPolicy,
    TaskExecutionContext,
    TaskExecutionFailed,
    WorkerResult,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import SQLiteRunStore, Usage


def _graph() -> ExecutionGraph:
    return ExecutionGraph.from_tasks(
        (
            TaskContract(
                "work",
                (
                    BackendProfile(
                        "fixture",
                        "local",
                        duration_ms_p50=1,
                        duration_ms_p95=10,
                        input_tokens=5,
                        output_tokens=5,
                        cost_microusd=1,
                        context_bytes=100,
                    ),
                ),
            ),
        )
    )


def _envelope() -> RunEnvelope:
    return RunEnvelope(5_000, 100, 100, 1_000, 1)


def test_seeded_jitter_is_bounded_recorded_and_replay_stable(tmp_path: Path) -> None:
    async def run(path: Path, run_id: str) -> tuple[int, int]:
        calls = 0

        async def worker(_context: TaskExecutionContext) -> WorkerResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryableWorkerError("temporary", actual_usage=Usage(tokens=1))
            return WorkerResult({"ok": True}, Usage(tokens=2))

        store = SQLiteRunStore(path)
        result = await AsyncGraphExecutor(
            store,
            workers={"work": worker},
            retry_policy=RetryPolicy(
                max_attempts=2,
                backoff_ms=1,
                jitter_ms=3,
                jitter_seed=99,
            ),
        ).execute(_graph(), _envelope(), run_id=run_id)
        scheduled = next(
            event for event in result.events if event.event_type == "task.retry_scheduled"
        )
        return calls, scheduled.payload["delay_ms"]  # type: ignore[return-value]

    first = asyncio.run(run(tmp_path / "first.db", "jitter-a"))
    second = asyncio.run(run(tmp_path / "second.db", "jitter-b"))

    assert first[0] == second[0] == 2
    assert first[1] == second[1]
    assert 1 <= first[1] <= 4


def test_circuit_opens_before_retry_budget_and_dead_letters_once(tmp_path: Path) -> None:
    calls = 0

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        nonlocal calls
        calls += 1
        raise RetryableWorkerError("provider unavailable", actual_usage=Usage(tokens=1))

    store = SQLiteRunStore(tmp_path / "circuit.db")
    executor = AsyncGraphExecutor(
        store,
        workers={"work": worker},
        retry_policy=RetryPolicy(
            max_attempts=5,
            circuit_failure_threshold=2,
            circuit_cooldown_ms=60_000,
        ),
    )

    with pytest.raises(TaskExecutionFailed, match="circuit opened"):
        asyncio.run(executor.execute(_graph(), _envelope(), run_id="circuit"))

    assert calls == 2
    events = store.events("circuit")
    opened = [event for event in events if event.event_type == "task.circuit_opened"]
    dead = [event for event in events if event.event_type == "task.dead_lettered"]
    assert len(opened) == len(dead) == 1
    assert opened[0].payload["retryable_failure_count"] == 2
    assert dead[0].payload["attempt_count"] == 2
    assert dead[0].payload["payload_redacted"] is True
    assert any(event.event_type == "run.failed" for event in events)


def test_failure_messages_are_digest_only_in_durable_ledger(tmp_path: Path) -> None:
    secret = "provider-secret-should-never-persist"

    async def worker(_context: TaskExecutionContext) -> WorkerResult:
        raise RuntimeError(secret)

    store = SQLiteRunStore(tmp_path / "redaction.db")
    with pytest.raises(TaskExecutionFailed, match=secret):
        asyncio.run(
            AsyncGraphExecutor(store, workers={"work": worker}).execute(
                _graph(), _envelope(), run_id="redacted"
            )
        )

    events = store.events("redacted")
    assert secret not in str(events)
    failed = next(event for event in events if event.event_type == "task.attempt_failed")
    dead = next(event for event in events if event.event_type == "task.dead_lettered")
    terminal = next(event for event in events if event.event_type == "run.failed")
    assert failed.payload["message_redacted"] is True
    assert len(failed.payload["error_message_digest"]) == 64
    assert dead.payload["payload_redacted"] is True
    assert terminal.payload["reason_redacted"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": True},
        {"jitter_ms": True},
        {"jitter_seed": -1},
        {"circuit_failure_threshold": 1},
        {"circuit_cooldown_ms": 1},
    ],
)
def test_retry_resilience_policy_rejects_ambiguous_bounds(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]
