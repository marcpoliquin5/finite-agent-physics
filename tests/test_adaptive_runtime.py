from __future__ import annotations

import copy
from collections import Counter

import pytest

from agent_physics.adaptive_runtime import (
    ADAPTIVE_RUNTIME_LIMITATIONS,
    AdaptiveControlEvent,
    AdaptiveEventKind,
    AdaptiveInvariantError,
    AdaptiveRuntime,
    AdaptiveStatus,
    AdaptiveTaskContext,
    AdaptiveWorkerResult,
    SimulatedAdaptiveCrash,
    adaptive_recovery_drill_envelope,
    adaptive_recovery_drill_graph,
    replay_adaptive_records,
    run_adaptive_recovery_drill,
)
from agent_physics.contracts import BackendProfile, Effect, EffectClass, RunEnvelope, TaskContract
from agent_physics.effects import EffectState, SQLiteEffectBroker
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import SQLiteRunStore, Usage


def _store(tmp_path, name: str = "adaptive.db") -> SQLiteRunStore:
    return SQLiteRunStore(tmp_path / name, clock_ms=lambda: 1_000)


def _worker_for(
    graph: ExecutionGraph,
    calls: list[str],
    *,
    actual_overrides: dict[str, Usage] | None = None,
):
    overrides = actual_overrides or {}

    def make(task_id: str):
        def worker(context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
            calls.append(context.task_id)
            profile = graph.by_id[task_id].profiles[0]
            actual = overrides.get(
                task_id,
                Usage(
                    tokens=profile.total_tokens,
                    cost_microusd=profile.cost_microusd,
                    context_bytes=profile.context_bytes,
                ),
            )
            return AdaptiveWorkerResult(
                {"task_id": task_id, "fixture": True},
                actual,
                duration_ms=1,
            )

        return worker

    return {task.task_id: make(task.task_id) for task in graph.tasks}


def _runtime_after_two_tasks(tmp_path, name: str = "after-two.db"):
    graph = adaptive_recovery_drill_graph()
    calls: list[str] = []
    runtime = AdaptiveRuntime(
        _store(tmp_path, name),
        graph,
        adaptive_recovery_drill_envelope(),
        run_id=f"run-{name}",
        workers=_worker_for(graph, calls),
    )
    assert runtime.dispatch_next(occurred_at_ms=1) == "intake"
    assert runtime.dispatch_next(occurred_at_ms=3) == "assessment"
    assert calls == ["intake", "assessment"]
    return graph, calls, runtime


def _one_profile(
    provider: str = "fixture",
    *,
    tokens: int = 10,
    cost: int = 10,
    context: int = 10,
) -> BackendProfile:
    return BackendProfile(
        name=f"{provider}-profile",
        provider=provider,
        duration_ms_p50=1,
        duration_ms_p95=2,
        input_tokens=tokens,
        output_tokens=0,
        cost_microusd=cost,
        context_bytes=context,
        quality=1.0,
    )


def _small_envelope(cap: int = 100) -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=100,
        max_tokens=cap,
        max_cost_microusd=cap,
        max_context_bytes=cap,
        max_parallelism=1,
        provider_limits=(("fixture", 1),),
    )


def test_integrated_recovery_drill_crashes_restarts_and_replays_call_free(tmp_path) -> None:
    result = run_adaptive_recovery_drill(tmp_path / "drill.db")

    assert result.final_status is AdaptiveStatus.COMPLETED
    assert result.replay_passed
    assert result.control_digest == result.replay_control_digest
    assert result.first_process_worker_calls == ("intake", "assessment")
    assert result.restart_worker_calls == ("mandatory_alert",)
    assert result.resumed_task_ids == ("assessment", "intake")
    assert result.unknown_task_ids == ("optional_enrichment",)
    assert result.shed_task_ids == ("optional_enrichment", "optional_social")
    assert result.completed_task_ids == ("assessment", "intake", "mandatory_alert")
    assert result.provider_reset_honored
    assert result.external_provider_calls == 0
    assert result.controller_record_count == 14


def test_429_reset_and_capacity_change_dispatch_eligibility(tmp_path) -> None:
    _, _, runtime = _runtime_after_two_tasks(tmp_path, "provider-controls.db")

    blocked = runtime.provider_429("burst", occurred_at_ms=5, reset_at_ms=8)
    assert "optional_social" not in blocked.eligible_task_ids
    assert {"mandatory_alert", "optional_enrichment"} <= set(blocked.eligible_task_ids)

    reset = runtime.provider_reset("burst", occurred_at_ms=8)
    assert "optional_social" in reset.eligible_task_ids

    zero = runtime.provider_capacity("burst", 0, occurred_at_ms=9)
    assert "optional_social" not in zero.eligible_task_ids
    restored = runtime.provider_capacity("burst", 1, occurred_at_ms=10)
    assert "optional_social" in restored.eligible_task_ids


def test_budget_cut_sheds_only_unaffordable_optional_and_protects_mandatory(tmp_path) -> None:
    _, calls, runtime = _runtime_after_two_tasks(tmp_path, "budget-cut.db")

    decision = runtime.cut_budget(
        Usage(tokens=55, cost_microusd=55, context_bytes=55),
        occurred_at_ms=5,
    )

    assert decision.newly_shed_task_ids == ("optional_social",)
    assert "mandatory_alert" not in runtime.state.shed_task_ids
    assert runtime.dispatch_next(occurred_at_ms=6) == "optional_enrichment"
    assert runtime.state.settled_usage == Usage(tokens=40, cost_microusd=40, context_bytes=40)
    remaining = Usage(
        tokens=runtime.state.caps.tokens - runtime.state.settled_usage.tokens,
        cost_microusd=(
            runtime.state.caps.cost_microusd - runtime.state.settled_usage.cost_microusd
        ),
        context_bytes=(
            runtime.state.caps.context_bytes - runtime.state.settled_usage.context_bytes
        ),
    )
    assert remaining == Usage(tokens=15, cost_microusd=15, context_bytes=15)
    assert runtime.dispatch_next(occurred_at_ms=8) == "mandatory_alert"
    assert runtime.state.status is AdaptiveStatus.COMPLETED
    assert calls == ["intake", "assessment", "optional_enrichment", "mandatory_alert"]


def test_cancellation_stops_all_future_dispatch(tmp_path) -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("mandatory", (_one_profile(),)),))
    calls: list[str] = []
    runtime = AdaptiveRuntime(
        _store(tmp_path, "cancel.db"),
        graph,
        _small_envelope(),
        run_id="cancel-run",
        workers=_worker_for(graph, calls),
    )

    decision = runtime.cancel("operator requested stop", occurred_at_ms=1)

    assert decision.status is AdaptiveStatus.CANCELLED
    assert runtime.dispatch_next(occurred_at_ms=2) is None
    assert calls == []


def test_restart_resumes_completed_task_without_worker_recall(tmp_path) -> None:
    profile = _one_profile()
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("first", (profile,), value=2.0),
            TaskContract("second", (profile,), ("first",), value=1.0),
        )
    )
    store = _store(tmp_path, "resume.db")
    first_calls: list[str] = []
    first = AdaptiveRuntime(
        store,
        graph,
        _small_envelope(),
        run_id="resume-run",
        workers=_worker_for(graph, first_calls),
    )
    assert first.dispatch_next(occurred_at_ms=1) == "first"

    restart_calls: list[str] = []
    restarted = AdaptiveRuntime(
        _store(tmp_path, "resume.db"),
        graph,
        _small_envelope(),
        run_id="resume-run",
        workers=_worker_for(graph, restart_calls),
    )
    result = restarted.run_until_blocked(start_at_ms=3)

    assert result.resumed_task_ids == ("first",)
    assert first_calls == ["first"]
    assert restart_calls == ["second"]
    assert result.state.completed_task_ids == ("first", "second")


def test_unknown_optional_inflight_is_fully_charged_and_never_recalled(tmp_path) -> None:
    profile = _one_profile(tokens=20, cost=20, context=20)
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("mandatory", (_one_profile(),), value=1.0),
            TaskContract("optional", (profile,), optional=True, value=10.0),
        )
    )
    database = "unknown-optional.db"
    first = AdaptiveRuntime(
        _store(tmp_path, database),
        graph,
        _small_envelope(40),
        run_id="unknown-optional-run",
        workers=_worker_for(graph, []),
        crash_after_dispatch_task_ids=("optional",),
    )
    with pytest.raises(SimulatedAdaptiveCrash):
        first.dispatch_next(occurred_at_ms=1)

    restart_calls: list[str] = []
    restarted = AdaptiveRuntime(
        _store(tmp_path, database),
        graph,
        _small_envelope(40),
        run_id="unknown-optional-run",
        workers=_worker_for(graph, restart_calls),
    )
    result = restarted.run_until_blocked(start_at_ms=2)

    assert result.state.unknown_usage == Usage(tokens=20, cost_microusd=20, context_bytes=20)
    assert result.state.unknown_task_ids == ("optional",)
    assert restart_calls == ["mandatory"]
    assert result.state.status is AdaptiveStatus.COMPLETED


def test_unknown_mandatory_inflight_fails_closed_instead_of_recalling(tmp_path) -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("mandatory", (_one_profile(),)),))
    database = "unknown-mandatory.db"
    first = AdaptiveRuntime(
        _store(tmp_path, database),
        graph,
        _small_envelope(),
        run_id="unknown-mandatory-run",
        workers=_worker_for(graph, []),
        crash_after_dispatch_task_ids=("mandatory",),
    )
    with pytest.raises(SimulatedAdaptiveCrash):
        first.dispatch_next(occurred_at_ms=1)

    calls: list[str] = []
    restarted = AdaptiveRuntime(
        _store(tmp_path, database),
        graph,
        _small_envelope(),
        run_id="unknown-mandatory-run",
        workers=_worker_for(graph, calls),
    )
    restarted.recover_unknown_inflight(occurred_at_ms=2)

    assert restarted.state.status is AdaptiveStatus.REFUSED
    assert restarted.state.completed_task_ids == ()
    assert restarted.state.unknown_usage == Usage(tokens=10, cost_microusd=10, context_bytes=10)
    assert calls == []
    replay = replay_adaptive_records(
        graph,
        _small_envelope(),
        run_id="unknown-mandatory-run",
        records=restarted.controller_records,
    )
    assert replay.passed


def test_call_free_replay_reconstructs_every_decision_and_state_digest(tmp_path) -> None:
    graph, calls, runtime = _runtime_after_two_tasks(tmp_path, "replay.db")
    runtime.provider_429("burst", occurred_at_ms=5, reset_at_ms=7)
    runtime.provider_reset("burst", occurred_at_ms=7)
    before = tuple(calls)

    first = replay_adaptive_records(
        graph,
        adaptive_recovery_drill_envelope(),
        run_id="run-replay.db",
        records=runtime.controller_records,
    )
    second = replay_adaptive_records(
        graph,
        adaptive_recovery_drill_envelope(),
        run_id="run-replay.db",
        records=runtime.controller_records,
    )

    assert first.passed and second.passed
    assert first.control_digest == second.control_digest == runtime.control_digest
    assert first.final_state == second.final_state == runtime.state
    assert tuple(calls) == before


def test_replay_rejects_unknown_fields_and_mutated_decisions(tmp_path) -> None:
    graph, _, runtime = _runtime_after_two_tasks(tmp_path, "mutation.db")
    records = list(runtime.controller_records)
    unknown = copy.deepcopy(records)
    unknown[0]["event"]["unexpected"] = True

    unknown_report = replay_adaptive_records(
        graph,
        adaptive_recovery_drill_envelope(),
        run_id="run-mutation.db",
        records=unknown,
    )

    assert not unknown_report.passed
    assert "fields differ" in unknown_report.violations[0].detail

    mutated = copy.deepcopy(records)
    mutated[-1]["decision"]["reason_code"] = "trust_the_scheduler"
    mutation_report = replay_adaptive_records(
        graph,
        adaptive_recovery_drill_envelope(),
        run_id="run-mutation.db",
        records=mutated,
    )
    assert not mutation_report.passed
    assert "decision digest" in mutation_report.violations[0].detail


def test_controller_records_bind_monotonic_prior_and_next_state_digests(tmp_path) -> None:
    _, _, runtime = _runtime_after_two_tasks(tmp_path, "chain.db")
    records = runtime.controller_records

    assert [record["revision"] for record in records] == list(range(1, len(records) + 1))
    for prior, current in zip(records, records[1:], strict=False):
        assert current["prior_state_digest"] == prior["next_state"]["state_digest"]
        assert current["decision"]["prior_state_digest"] == current["prior_state_digest"]
        assert current["decision"]["next_state_digest"] == current["next_state"]["state_digest"]


def test_provider_reset_before_declared_time_and_boolean_capacity_fail_closed(tmp_path) -> None:
    _, _, runtime = _runtime_after_two_tasks(tmp_path, "invalid-controls.db")
    runtime.provider_429("burst", occurred_at_ms=5, reset_at_ms=9)

    with pytest.raises(AdaptiveInvariantError, match="before its declared window"):
        runtime.provider_reset("burst", occurred_at_ms=8)
    with pytest.raises(AdaptiveInvariantError, match="integer"):
        runtime.provider_capacity("burst", True, occurred_at_ms=9)  # type: ignore[arg-type]


def test_worker_usage_overrun_is_never_settled_as_fact(tmp_path) -> None:
    graph = ExecutionGraph.from_tasks((TaskContract("mandatory", (_one_profile(),)),))
    calls: list[str] = []
    runtime = AdaptiveRuntime(
        _store(tmp_path, "overrun.db"),
        graph,
        _small_envelope(),
        run_id="overrun-run",
        workers=_worker_for(
            graph,
            calls,
            actual_overrides={"mandatory": Usage(tokens=11, cost_microusd=10, context_bytes=10)},
        ),
    )

    with pytest.raises(AdaptiveInvariantError, match="exceeds"):
        runtime.dispatch_next(occurred_at_ms=1)

    assert runtime.state.status is AdaptiveStatus.REFUSED
    assert runtime.state.completed_task_ids == ()
    assert runtime.state.unknown_usage == Usage(tokens=10, cost_microusd=10, context_bytes=10)
    assert calls == ["mandatory"]


def test_runtime_claim_boundaries_are_explicit() -> None:
    limitations = " ".join(ADAPTIVE_RUNTIME_LIMITATIONS)
    assert "not authenticated live telemetry" in limitations
    assert "no live-provider claim" in limitations
    assert "single-database" in limitations
    assert "not a producer signature" in limitations


def test_control_event_schema_rejects_unknown_detail_and_bool_time() -> None:
    with pytest.raises(AdaptiveInvariantError, match="details differ"):
        AdaptiveControlEvent.create(
            "event",
            AdaptiveEventKind.PROVIDER_RESET,
            1,
            {"provider": "fixture", "force": True},
        )
    with pytest.raises(AdaptiveInvariantError, match="non-negative integer"):
        AdaptiveControlEvent.create(
            "event",
            AdaptiveEventKind.RUNTIME_STARTED,
            True,  # type: ignore[arg-type]
            {},
        )


def test_worker_calls_are_bounded_to_one_per_completed_task_in_drill(tmp_path) -> None:
    result = run_adaptive_recovery_drill(tmp_path / "bounded-drill.db")
    counts = Counter((*result.first_process_worker_calls, *result.restart_worker_calls))

    assert counts == Counter({"intake": 1, "assessment": 1, "mandatory_alert": 1})
    assert "optional_enrichment" not in counts
    assert "optional_social" not in counts


def test_effect_bearing_adaptive_graph_stops_at_durable_proposal_and_replays(
    tmp_path,
) -> None:
    profile = _one_profile(tokens=1, cost=1, context=1)
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("prepare", (profile,)),
            TaskContract(
                "publish",
                (profile,),
                ("prepare",),
                effect=Effect(
                    kind=EffectClass.IDEMPOTENT_WRITE,
                    resource="fixture://publication",
                    idempotency_key="adaptive-effect-test-v1",
                ),
            ),
        )
    )
    store = _store(tmp_path, "effect-bearing.db")
    broker = SQLiteEffectBroker(
        tmp_path / "effect-bearing-effects.db",
        broker_id="adaptive-effect-test",
        clock_ms=lambda: 1_000,
    )
    calls: list[str] = []
    runtime = AdaptiveRuntime(
        store,
        graph,
        _small_envelope(),
        run_id="effect-bearing",
        workers=_worker_for(graph, calls),
        effect_broker=broker,
    )

    result = runtime.run_until_blocked(start_at_ms=1)
    output = result.outputs["publish"]
    intent = broker.get(output["effect_intent_id"])  # type: ignore[index]

    assert result.state.status is AdaptiveStatus.COMPLETED
    assert calls == ["prepare"]
    assert intent.state is EffectState.PROPOSED
    assert intent.run_id == "effect-bearing"
    assert intent.idempotency_key.startswith("finite-effect/v1:")
    assert intent.idempotency_key != "adaptive-effect-test-v1"
    assert intent.payload["declared_idempotency_key"] == "adaptive-effect-test-v1"
    assert output["declared_idempotency_key"] == "adaptive-effect-test-v1"  # type: ignore[index]
    assert output["executed_externally"] is False  # type: ignore[index]
    assert not any(event.event_type == "effect.committed" for event in broker.pending_outbox())

    replay = replay_adaptive_records(
        graph,
        _small_envelope(),
        run_id="effect-bearing",
        records=result.controller_records,
    )
    assert replay.passed is True
    assert replay.final_state == result.state
