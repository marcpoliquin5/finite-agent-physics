from dataclasses import replace

from agent_physics import (
    BackendProfile,
    ExecutionGraph,
    RunEnvelope,
    Scheduler,
    TaskContract,
    verify_conservation,
)


PROFILE = BackendProfile(
    "reliable",
    "local",
    10,
    10,
    input_tokens=5,
    cost_microusd=7,
    context_bytes=9,
)
GRAPH = ExecutionGraph.from_tasks([TaskContract("required", (PROFILE,))])
ENVELOPE = RunEnvelope(
    deadline_ms=100,
    max_tokens=100,
    max_cost_microusd=100,
    max_context_bytes=100,
    max_parallelism=1,
    min_modeled_success_probability=0.9,
)


def valid_result():  # type: ignore[no-untyped-def]
    return Scheduler().schedule(GRAPH, ENVELOPE)


def test_valid_trace_passes_fail_closed_verifier() -> None:
    report = verify_conservation(GRAPH, ENVELOPE, valid_result())
    assert report.passed


def test_fake_events_cannot_replace_missing_entries() -> None:
    result = valid_result()
    forged = replace(
        result,
        entries=(),
        total_tokens=0,
        total_cost_microusd=0,
        total_context_bytes=0,
        modeled_success_probability=1.0,
        model_bound_ms=0,
    )
    report = verify_conservation(GRAPH, ENVELOPE, forged)
    assert not report.passed
    assert any(check.name == "task-lifecycle" and not check.passed for check in report.checks)


def test_duplicate_entry_is_rejected_even_when_aggregates_are_forged() -> None:
    result = valid_result()
    entry = result.entries[0]
    forged = replace(
        result,
        entries=(entry, entry),
        total_tokens=entry.tokens * 2,
        total_cost_microusd=entry.cost_microusd * 2,
        total_context_bytes=entry.context_bytes * 2,
    )
    report = verify_conservation(GRAPH, ENVELOPE, forged)
    assert not report.passed
    assert any(check.name == "entry-identity" and not check.passed for check in report.checks)


def test_unknown_entry_and_event_ids_fail_without_crashing() -> None:
    result = valid_result()
    unknown_entry = replace(result.entries[0], task_id="unknown")
    entry_report = verify_conservation(
        GRAPH,
        ENVELOPE,
        replace(result, entries=(unknown_entry,)),
    )
    assert not entry_report.passed

    events = list(result.events)
    started_index = next(
        index for index, event in enumerate(events) if event.event_type.value == "task.started"
    )
    events[started_index] = replace(events[started_index], task_id="unknown")
    event_report = verify_conservation(GRAPH, ENVELOPE, replace(result, events=tuple(events)))
    assert not event_report.passed


def test_duplicate_reordered_and_malformed_trace_data_is_rejected() -> None:
    result = valid_result()
    duplicated = replace(result, events=result.events + (result.events[-1],))
    reordered = replace(result, events=tuple(reversed(result.events)))
    malformed_entry = replace(result.entries[0], end_ms=result.entries[0].end_ms + 1)
    malformed = replace(result, entries=(malformed_entry,))

    assert not verify_conservation(GRAPH, ENVELOPE, duplicated).passed
    assert not verify_conservation(GRAPH, ENVELOPE, reordered).passed
    assert not verify_conservation(GRAPH, ENVELOPE, malformed).passed
