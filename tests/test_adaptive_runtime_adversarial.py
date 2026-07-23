from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics import adaptive_runtime as adaptive
from agent_physics.adaptive_runtime import (
    AdaptiveAction,
    AdaptiveControlEvent,
    AdaptiveControllerRecord,
    AdaptiveDecision,
    AdaptiveEventKind,
    AdaptiveInvariantError,
    AdaptiveReplayError,
    AdaptiveRuntime,
    AdaptiveState,
    AdaptiveStatus,
    AdaptiveTaskContext,
    AdaptiveWorkerResult,
    InflightReservation,
    SimulatedAdaptiveCrash,
    replay_adaptive_records,
)
from agent_physics.contracts import (
    BackendProfile,
    Effect,
    EffectClass,
    RunEnvelope,
    TaskContract,
)
from agent_physics.effects import IdempotencyConflict, SQLiteEffectBroker
from agent_physics.graph import ExecutionGraph
from agent_physics.run_store import SQLiteRunStore, Usage
from agent_physics.serialization import content_digest


def _profile(
    *,
    name: str = "fixture",
    provider: str = "local",
    tokens: int = 10,
) -> BackendProfile:
    return BackendProfile(
        name=name,
        provider=provider,
        duration_ms_p50=1,
        duration_ms_p95=2,
        input_tokens=tokens,
        output_tokens=0,
        cost_microusd=tokens,
        context_bytes=tokens,
    )


def _graph(*tasks: TaskContract) -> ExecutionGraph:
    return ExecutionGraph.from_tasks(tasks or (TaskContract("work", (_profile(),)),))


def _envelope(*, cap: int = 100, deadline_ms: int = 100) -> RunEnvelope:
    return RunEnvelope(
        deadline_ms=deadline_ms,
        max_tokens=cap,
        max_cost_microusd=cap,
        max_context_bytes=cap,
        max_parallelism=1,
        provider_limits=(("local", 1),),
    )


def _worker(context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
    return AdaptiveWorkerResult(
        {"task_id": context.task_id},
        Usage(tokens=1, cost_microusd=1, context_bytes=1),
    )


def _runtime(
    tmp_path: Path,
    name: str,
    *,
    graph: ExecutionGraph | None = None,
    envelope: RunEnvelope | None = None,
    workers: dict[str, object] | None = None,
    broker: SQLiteEffectBroker | None = None,
    crash: tuple[str, ...] = (),
) -> AdaptiveRuntime:
    selected_graph = graph or _graph()
    return AdaptiveRuntime(
        SQLiteRunStore(tmp_path / f"{name}.sqlite3", clock_ms=lambda: 1_000),
        selected_graph,
        envelope or _envelope(),
        run_id=name,
        workers=(
            workers  # type: ignore[arg-type]
            if workers is not None
            else {task.task_id: _worker for task in selected_graph.tasks}
        ),
        effect_broker=broker,
        crash_after_dispatch_task_ids=crash,
    )


def _seal_event(value: dict[str, object]) -> None:
    value["event_digest"] = content_digest(
        {key: item for key, item in value.items() if key != "event_digest"}
    )


def _seal_state(value: dict[str, object]) -> None:
    value["state_digest"] = content_digest(
        {key: item for key, item in value.items() if key != "state_digest"}
    )


def _seal_decision(value: dict[str, object]) -> None:
    value["decision_digest"] = content_digest(
        {key: item for key, item in value.items() if key != "decision_digest"}
    )


def _seal_record(value: dict[str, object]) -> None:
    value["record_digest"] = content_digest(
        {key: item for key, item in value.items() if key != "record_digest"}
    )


def _valid_records(tmp_path: Path) -> tuple[AdaptiveRuntime, list[dict[str, object]]]:
    runtime = _runtime(tmp_path, "valid-records")
    assert runtime.dispatch_next(occurred_at_ms=1) == "work"
    return runtime, [copy.deepcopy(item) for item in runtime.controller_records]


def test_strict_replay_scalars_collections_and_reservations_reject_type_confusion(
    tmp_path: Path,
) -> None:
    _, records = _valid_records(tmp_path)
    event = copy.deepcopy(records[0]["event"])
    assert isinstance(event, dict)

    with pytest.raises(AdaptiveReplayError, match="object with string keys"):
        AdaptiveControlEvent.from_dict([])
    with pytest.raises(AdaptiveReplayError, match="object with string keys"):
        AdaptiveControlEvent.from_dict({1: "not-a-string-key"})

    empty_id = copy.deepcopy(event)
    empty_id["event_id"] = ""
    with pytest.raises(AdaptiveReplayError, match="non-empty string"):
        AdaptiveControlEvent.from_dict(empty_id)

    boolean_time = copy.deepcopy(event)
    boolean_time["occurred_at_ms"] = True
    with pytest.raises(AdaptiveReplayError, match="integer"):
        AdaptiveControlEvent.from_dict(boolean_time)

    state = copy.deepcopy(records[0]["next_state"])
    assert isinstance(state, dict)
    wrong_list = copy.deepcopy(state)
    wrong_list["completed_task_ids"] = "work"
    with pytest.raises(AdaptiveReplayError, match="array"):
        AdaptiveState.from_dict(wrong_list)

    duplicate_ids = copy.deepcopy(state)
    duplicate_ids["completed_task_ids"] = ["work", "work"]
    with pytest.raises(AdaptiveReplayError, match="sorted and unique"):
        AdaptiveState.from_dict(duplicate_ids)

    bad_usage = copy.deepcopy(state)
    bad_usage["caps"] = {"tokens": 1, "cost_microusd": 1}
    with pytest.raises(AdaptiveReplayError, match="fields differ"):
        AdaptiveState.from_dict(bad_usage)

    reservation = {
        "task_id": "work",
        "attempt": 1,
        "provider": "local",
        "backend": "fixture",
        "reservation": {"tokens": 1, "cost_microusd": 1, "context_bytes": 1},
        "dispatch_event_digest": "a" * 64,
    }
    assert InflightReservation.from_dict(reservation).task_id == "work"
    reservation["dispatch_event_digest"] = "A" * 64
    with pytest.raises(AdaptiveReplayError, match="dispatch_event_digest"):
        InflightReservation.from_dict(reservation)


def test_control_event_constructor_and_detail_schema_reject_every_ambiguous_shape() -> None:
    with pytest.raises(AdaptiveInvariantError, match="event_id"):
        AdaptiveControlEvent.create("", AdaptiveEventKind.CANCELLATION, 0, {"reason": "x"})
    with pytest.raises(AdaptiveInvariantError, match="exact enum"):
        AdaptiveControlEvent.create(
            "event",
            "cancellation",  # type: ignore[arg-type]
            0,
            {"reason": "x"},
        )
    with pytest.raises(AdaptiveInvariantError, match="non-negative integer"):
        AdaptiveControlEvent.create(
            "event",
            AdaptiveEventKind.CANCELLATION,
            True,
            {"reason": "x"},
        )

    invalid = (
        (AdaptiveEventKind.PROVIDER_429, {"provider": "", "reset_at_ms": 1}, "provider"),
        (AdaptiveEventKind.PROVIDER_429, {"provider": "local", "reset_at_ms": 0}, "integer"),
        (AdaptiveEventKind.PROVIDER_CAPACITY, {"provider": "local", "capacity": True}, "integer"),
        (
            AdaptiveEventKind.BUDGET_CUT,
            {"tokens": 1, "cost_microusd": 1},
            "details differ",
        ),
        (
            AdaptiveEventKind.BUDGET_CUT,
            {"tokens": True, "cost_microusd": 1, "context_bytes": 1},
            "integer",
        ),
        (
            AdaptiveEventKind.TASK_DISPATCHED,
            {
                "task_id": "work",
                "attempt": 1,
                "provider": "local",
                "backend": "fixture",
                "reservation": [],
            },
            "object",
        ),
        (
            AdaptiveEventKind.USAGE_SETTLED,
            {
                "task_id": "work",
                "attempt": 1,
                "actual_usage": {"tokens": 1, "cost_microusd": 1, "context_bytes": 1},
                "output_digest": "invalid",
            },
            "output_digest",
        ),
        (
            AdaptiveEventKind.USAGE_SETTLED,
            {
                "task_id": "work",
                "attempt": 1,
                "actual_usage": [],
                "output_digest": "a" * 64,
            },
            "object",
        ),
        (AdaptiveEventKind.CANCELLATION, {"reason": ""}, "reason"),
        (
            AdaptiveEventKind.UNKNOWN_INFLIGHT,
            {"task_id": "work", "attempt": 1, "reservation": []},
            "object",
        ),
    )
    for index, (kind, details, message) in enumerate(invalid):
        with pytest.raises(AdaptiveInvariantError, match=message):
            AdaptiveControlEvent.create(f"invalid-{index}", kind, 0, details)


def test_event_state_decision_and_record_envelopes_fail_closed_after_resealing(
    tmp_path: Path,
) -> None:
    _, records = _valid_records(tmp_path)
    record = records[0]
    event = copy.deepcopy(record["event"])
    state = copy.deepcopy(record["next_state"])
    decision = copy.deepcopy(record["decision"])
    assert isinstance(event, dict)
    assert isinstance(state, dict)
    assert isinstance(decision, dict)

    for field, value, message in (
        ("schema_version", "unknown", "unsupported"),
        ("kind", "unknown", "unknown adaptive event kind"),
        ("details", [], "must be an object"),
        ("event_digest", "0" * 64, "digest verification"),
    ):
        forged = copy.deepcopy(event)
        forged[field] = value
        with pytest.raises(AdaptiveReplayError, match=message):
            AdaptiveControlEvent.from_dict(forged)

    prior_none = copy.deepcopy(state)
    prior_none["prior_state_digest"] = None
    _seal_state(prior_none)
    assert AdaptiveState.from_dict(prior_none).prior_state_digest is None

    state_failures: tuple[tuple[str, object, str], ...] = (
        ("schema_version", "unknown", "unsupported"),
        ("graph_digest", "0", "graph_digest"),
        ("prior_state_digest", "0", "null or"),
        ("status", "unknown", "unknown adaptive state status"),
        ("state_digest", "0" * 64, "digest verification"),
    )
    for field, value, message in state_failures:
        forged = copy.deepcopy(state)
        forged[field] = value
        with pytest.raises(AdaptiveReplayError, match=message):
            AdaptiveState.from_dict(forged)

    reservation = {
        "task_id": "work",
        "attempt": 1,
        "provider": "local",
        "backend": "fixture",
        "reservation": {"tokens": 1, "cost_microusd": 1, "context_bytes": 1},
        "dispatch_event_digest": "a" * 64,
    }
    too_many = copy.deepcopy(state)
    too_many["inflight"] = [reservation, reservation]
    with pytest.raises(AdaptiveReplayError, match="at most one"):
        AdaptiveState.from_dict(too_many)

    unsorted_providers = copy.deepcopy(state)
    unsorted_providers["provider_resets"] = [
        {"provider": "z", "reset_at_ms": 2},
        {"provider": "a", "reset_at_ms": 1},
    ]
    with pytest.raises(AdaptiveReplayError, match="sorted and unique"):
        AdaptiveState.from_dict(unsorted_providers)

    duplicate_provider = copy.deepcopy(state)
    duplicate_provider["provider_capacities"] = [
        {"provider": "local", "capacity": 0},
        {"provider": "local", "capacity": 1},
    ]
    with pytest.raises(AdaptiveReplayError, match="sorted and unique"):
        AdaptiveState.from_dict(duplicate_provider)

    overlap = copy.deepcopy(state)
    overlap["completed_task_ids"] = ["work"]
    overlap["shed_task_ids"] = ["work"]
    _seal_state(overlap)
    with pytest.raises(AdaptiveReplayError, match="overlap"):
        AdaptiveState.from_dict(overlap)

    unknown_mandatory = copy.deepcopy(state)
    unknown_mandatory["unknown_task_ids"] = ["work"]
    unknown_mandatory["shed_task_ids"] = []
    _seal_state(unknown_mandatory)
    with pytest.raises(AdaptiveReplayError, match="optional"):
        AdaptiveState.from_dict(unknown_mandatory)

    for field, value, message in (
        ("schema_version", "unknown", "unsupported"),
        ("event_digest", "invalid", "invalid digest"),
        ("action", "unknown", "enum value"),
        ("task_id", "", "non-empty string"),
        ("eligible_task_ids", ["z", "a"], "sorted and unique"),
        ("decision_digest", "0" * 64, "digest verification"),
    ):
        forged = copy.deepcopy(decision)
        forged[field] = value
        with pytest.raises(AdaptiveReplayError, match=message):
            AdaptiveDecision.from_dict(forged)

    for field, value, message in (
        ("schema_version", "unknown", "unsupported"),
        ("prior_state_digest", "invalid", "digest field"),
        ("record_digest", "0" * 64, "digest verification"),
    ):
        forged = copy.deepcopy(record)
        forged[field] = value
        with pytest.raises(AdaptiveReplayError, match=message):
            AdaptiveControllerRecord.from_dict(forged)


def test_replay_rejects_resealed_semantic_forgery_at_each_chain_boundary(
    tmp_path: Path,
) -> None:
    runtime, records = _valid_records(tmp_path)

    nonmonotonic = copy.deepcopy(records)
    nonmonotonic[0]["revision"] = 2
    _seal_record(nonmonotonic[0])
    report = replay_adaptive_records(
        runtime.graph,
        runtime.envelope,
        run_id=runtime.run_id,
        records=nonmonotonic,
    )
    assert not report.passed and "non-monotonic" in report.violations[0].detail

    duplicate = [copy.deepcopy(records[0]), copy.deepcopy(records[0])]
    duplicate[1]["revision"] = 2
    _seal_record(duplicate[1])
    report = replay_adaptive_records(
        runtime.graph,
        runtime.envelope,
        run_id=runtime.run_id,
        records=duplicate,
    )
    assert not report.passed and "must be unique" in report.violations[0].detail

    broken_chain = copy.deepcopy(records)
    broken_chain[1]["prior_state_digest"] = "0" * 64
    _seal_record(broken_chain[1])
    report = replay_adaptive_records(
        runtime.graph,
        runtime.envelope,
        run_id=runtime.run_id,
        records=broken_chain,
    )
    assert not report.passed and "chain is broken" in report.violations[0].detail

    forged_state = copy.deepcopy(records)
    next_state = forged_state[1]["next_state"]
    assert isinstance(next_state, dict)
    next_state["now_ms"] = 2
    _seal_state(next_state)
    _seal_record(forged_state[1])
    report = replay_adaptive_records(
        runtime.graph,
        runtime.envelope,
        run_id=runtime.run_id,
        records=forged_state,
    )
    assert not report.passed and "next state differs" in report.violations[0].detail

    forged_decision = copy.deepcopy(records)
    decision = forged_decision[1]["decision"]
    assert isinstance(decision, dict)
    decision["reason_code"] = "attacker-selected-reason"
    _seal_decision(decision)
    _seal_record(forged_decision[1])
    report = replay_adaptive_records(
        runtime.graph,
        runtime.envelope,
        run_id=runtime.run_id,
        records=forged_decision,
    )
    assert not report.passed and "decision differs" in report.violations[0].detail


def test_worker_result_and_runtime_input_contracts_are_exact(tmp_path: Path) -> None:
    with pytest.raises(AdaptiveInvariantError, match="exact Usage"):
        AdaptiveWorkerResult({}, object())  # type: ignore[arg-type]
    with pytest.raises(AdaptiveInvariantError, match="non-negative integer"):
        AdaptiveWorkerResult({}, Usage(), duration_ms=True)
    with pytest.raises(AdaptiveInvariantError, match="canonical JSON"):
        AdaptiveWorkerResult({"not_finite": float("nan")}, Usage())

    store = SQLiteRunStore(tmp_path / "invalid-inputs.sqlite3")
    with pytest.raises(AdaptiveInvariantError, match="ExecutionGraph"):
        AdaptiveRuntime(store, object(), _envelope(), run_id="run", workers={})  # type: ignore[arg-type]
    with pytest.raises(AdaptiveInvariantError, match="RunEnvelope"):
        AdaptiveRuntime(store, _graph(), object(), run_id="run", workers={})  # type: ignore[arg-type]
    with pytest.raises(AdaptiveInvariantError, match="run_id"):
        AdaptiveRuntime(store, _graph(), _envelope(), run_id="", workers={})
    with pytest.raises(AdaptiveInvariantError, match="reject booleans"):
        AdaptiveRuntime(
            store,
            _graph(),
            replace(_envelope(), max_tokens=True),
            run_id="boolean-envelope",
            workers={"work": _worker},
        )

    nonfinite = _graph(TaskContract("work", (_profile(),), value=float("nan")))
    with pytest.raises(AdaptiveInvariantError, match="finite"):
        AdaptiveRuntime(
            store,
            nonfinite,
            _envelope(),
            run_id="nonfinite-task",
            workers={"work": _worker},
        )

    write = _graph(
        TaskContract(
            "work",
            (_profile(),),
            effect=Effect(
                EffectClass.IDEMPOTENT_WRITE,
                resource="finite://work",
                idempotency_key="write-contract",
            ),
        )
    )
    with pytest.raises(AdaptiveInvariantError, match="refuses write effects"):
        AdaptiveRuntime(
            store,
            write,
            _envelope(),
            run_id="write-without-broker",
            workers={},
        )

    unqualified = TaskContract("work", (replace(_profile(), quality=0.1),), min_quality=0.9)
    with pytest.raises(AdaptiveInvariantError, match="no quality-qualified profile"):
        adaptive._canonical_profile(unqualified)
    with pytest.raises(AdaptiveInvariantError, match="subtraction"):
        adaptive._usage_subtract(Usage(tokens=1), Usage(tokens=2))


def test_model_rejects_forged_state_event_and_transition_facts(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "model-facts")
    model = runtime._model
    state = runtime.state
    valid_control = AdaptiveControlEvent.create(
        "capacity",
        AdaptiveEventKind.PROVIDER_CAPACITY,
        0,
        {"provider": "local", "capacity": 1},
    )

    with pytest.raises(AdaptiveInvariantError, match="state digest"):
        model.apply(replace(state, state_digest="0" * 64), valid_control)
    with pytest.raises(AdaptiveInvariantError, match="event digest"):
        model.apply(state, replace(valid_control, event_digest="0" * 64))

    wrong_run = state.as_dict()
    wrong_run["run_id"] = "another-run"
    _seal_state(wrong_run)
    with pytest.raises(AdaptiveInvariantError, match="another run or graph"):
        model.apply(AdaptiveState.from_dict(wrong_run), valid_control)

    started_again = AdaptiveControlEvent.create(
        "started-again",
        AdaptiveEventKind.RUNTIME_STARTED,
        0,
        {},
    )
    with pytest.raises(AdaptiveInvariantError, match="revision zero"):
        model.apply(state, started_again)

    with pytest.raises(AdaptiveInvariantError, match="unknown provider"):
        runtime.provider_capacity("undeclared", 1, occurred_at_ms=0)
    with pytest.raises(AdaptiveInvariantError, match="later than"):
        runtime.provider_429("local", occurred_at_ms=2, reset_at_ms=2)
    with pytest.raises(AdaptiveInvariantError, match="cannot increase"):
        runtime.cut_budget(
            Usage(tokens=101, cost_microusd=101, context_bytes=101),
            occurred_at_ms=0,
        )

    runtime.provider_capacity("local", 1, occurred_at_ms=2)
    with pytest.raises(AdaptiveInvariantError, match="move backwards"):
        runtime.provider_capacity("local", 1, occurred_at_ms=1)

    reservation = {"tokens": 10, "cost_microusd": 10, "context_bytes": 10}
    wrong_dispatch = AdaptiveControlEvent.create(
        "wrong-dispatch",
        AdaptiveEventKind.TASK_DISPATCHED,
        0,
        {
            "task_id": "other",
            "attempt": 1,
            "provider": "local",
            "backend": "fixture",
            "reservation": reservation,
        },
    )
    with pytest.raises(AdaptiveInvariantError, match="deterministic choice"):
        model.apply(state, wrong_dispatch)

    settlement = AdaptiveControlEvent.create(
        "settlement-without-dispatch",
        AdaptiveEventKind.USAGE_SETTLED,
        0,
        {
            "task_id": "work",
            "attempt": 1,
            "actual_usage": {"tokens": 1, "cost_microusd": 1, "context_bytes": 1},
            "output_digest": "a" * 64,
        },
    )
    with pytest.raises(AdaptiveInvariantError, match="exactly one"):
        model.apply(state, settlement)

    recovery = AdaptiveControlEvent.create(
        "recovery-without-dispatch",
        AdaptiveEventKind.UNKNOWN_INFLIGHT,
        0,
        {"task_id": "work", "attempt": 1, "reservation": reservation},
    )
    with pytest.raises(AdaptiveInvariantError, match="one in-flight"):
        model.apply(state, recovery)

    valid_dispatch = AdaptiveControlEvent.create(
        "valid-dispatch",
        AdaptiveEventKind.TASK_DISPATCHED,
        0,
        {
            "task_id": "work",
            "attempt": 1,
            "provider": "local",
            "backend": "fixture",
            "reservation": reservation,
        },
    )
    inflight_state, _ = model.apply(state, valid_dispatch)
    oversized_settlement = AdaptiveControlEvent.create(
        "oversized-settlement",
        AdaptiveEventKind.USAGE_SETTLED,
        0,
        {
            "task_id": "work",
            "attempt": 1,
            "actual_usage": {"tokens": 11, "cost_microusd": 1, "context_bytes": 1},
            "output_digest": "a" * 64,
        },
    )
    with pytest.raises(AdaptiveInvariantError, match="mismatches its reservation"):
        model.apply(inflight_state, oversized_settlement)

    wrong_recovery = AdaptiveControlEvent.create(
        "wrong-recovery",
        AdaptiveEventKind.UNKNOWN_INFLIGHT,
        0,
        {
            "task_id": "other",
            "attempt": 1,
            "reservation": reservation,
        },
    )
    with pytest.raises(AdaptiveInvariantError, match="does not bind"):
        model.apply(inflight_state, wrong_recovery)

    blocked = _runtime(tmp_path, "model-no-eligible")
    blocked.provider_capacity("local", 0, occurred_at_ms=0)
    no_eligible_dispatch = AdaptiveControlEvent.create(
        "no-eligible-dispatch",
        AdaptiveEventKind.TASK_DISPATCHED,
        0,
        {
            "task_id": "work",
            "attempt": 1,
            "provider": "local",
            "backend": "fixture",
            "reservation": reservation,
        },
    )
    with pytest.raises(AdaptiveInvariantError, match="no task is currently eligible"):
        blocked._model.apply(blocked.state, no_eligible_dispatch)

    completed = _runtime(tmp_path, "terminal-control-rejection")
    assert completed.dispatch_next(occurred_at_ms=1) == "work"
    with pytest.raises(AdaptiveInvariantError, match="terminal adaptive state"):
        completed.provider_capacity("local", 1, occurred_at_ms=2)


def test_reconcile_refuses_deadline_and_mandatory_budget_and_propagates_shedding(
    tmp_path: Path,
) -> None:
    deadline = _runtime(tmp_path, "deadline-refusal")
    decision = deadline.provider_capacity("local", 1, occurred_at_ms=101)
    assert decision.status is AdaptiveStatus.REFUSED

    budget = _runtime(tmp_path, "mandatory-budget-refusal")
    decision = budget.cut_budget(Usage(), occurred_at_ms=1)
    assert decision.status is AdaptiveStatus.REFUSED

    graph = _graph(
        TaskContract("mandatory", (_profile(tokens=10),), value=1),
        TaskContract("expensive", (_profile(name="expensive", tokens=30),), optional=True, value=1),
        TaskContract(
            "dependent",
            (_profile(name="dependent", tokens=1),),
            dependencies=("expensive",),
            optional=True,
            value=10,
        ),
    )
    propagated = _runtime(
        tmp_path,
        "dependent-shedding",
        graph=graph,
        envelope=_envelope(cap=30),
    )
    assert propagated.state.shed_task_ids == ("dependent", "expensive")

    dependency_graph = _graph(
        TaskContract("optional-input", (_profile(name="input"),), optional=True),
        TaskContract(
            "mandatory-output",
            (_profile(name="output"),),
            dependencies=("optional-input",),
        ),
    )
    protected_dependency = _runtime(
        tmp_path,
        "protected-dependency",
        graph=dependency_graph,
    )
    assert protected_dependency._model.protected == {"mandatory-output", "optional-input"}

    values = protected_dependency._model._values(protected_dependency.state)
    shed = values["shed"]
    assert isinstance(shed, set)
    shed.add("optional-input")
    protected_dependency._model._reconcile(values)
    assert values["status"] is AdaptiveStatus.REFUSED

    forged_caps = protected_dependency.state.as_dict()
    forged_caps["caps"] = {"tokens": 0, "cost_microusd": 0, "context_bytes": 0}
    _seal_state(forged_caps)
    assert protected_dependency._model._dispatch_choices(AdaptiveState.from_dict(forged_caps)) == []


def test_dispatch_failures_recover_unknown_without_retrying_side_effects(tmp_path: Path) -> None:
    missing = _runtime(tmp_path, "missing-worker", workers={})
    with pytest.raises(AdaptiveInvariantError, match="no local worker"):
        missing.dispatch_next(occurred_at_ms=1)
    assert missing.state.unknown_task_ids == ("work",)

    def wrong_result(_context: AdaptiveTaskContext) -> object:
        return {"not": "AdaptiveWorkerResult"}

    malformed = _runtime(
        tmp_path,
        "wrong-worker-result",
        workers={"work": wrong_result},
    )
    with pytest.raises(AdaptiveInvariantError, match="must return"):
        malformed.dispatch_next(occurred_at_ms=1)
    assert malformed.state.unknown_task_ids == ("work",)

    def exploding(_context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
        raise RuntimeError("deterministic worker crash")

    crashed = _runtime(
        tmp_path,
        "worker-exception",
        workers={"work": exploding},
    )
    with pytest.raises(RuntimeError, match="worker crash"):
        crashed.dispatch_next(occurred_at_ms=1)
    assert crashed.state.unknown_task_ids == ("work",)

    effect = Effect(
        EffectClass.IDEMPOTENT_WRITE,
        resource="finite://work",
        idempotency_key="conflicting-effect-key",
    )
    graph = _graph(TaskContract("work", (_profile(),), effect=effect))
    broker = SQLiteEffectBroker(
        tmp_path / "effect-conflict.sqlite3",
        broker_id="effect-conflict",
    )
    broker.propose(
        run_id="effect-conflict-run",
        action="another-action",
        resource="finite://other",
        effect_class=EffectClass.IDEMPOTENT_WRITE,
        idempotency_key=adaptive.scoped_effect_idempotency_key(
            run_id="effect-conflict-run",
            task_id="work",
            attempt=1,
            declared_key=graph.by_id["work"].effect.idempotency_key,
        ),
        payload={},
    )
    conflicted = _runtime(
        tmp_path,
        "effect-conflict-run",
        graph=graph,
        workers={},
        broker=broker,
    )
    with pytest.raises(IdempotencyConflict):
        conflicted.dispatch_next(occurred_at_ms=1)
    assert conflicted.state.unknown_task_ids == ("work",)


def test_durable_completion_after_dispatch_crash_settles_without_worker_recall(
    tmp_path: Path,
) -> None:
    database = tmp_path / "durable-after-crash.sqlite3"
    store = SQLiteRunStore(database, clock_ms=lambda: 1_000)
    graph = _graph()
    runtime = AdaptiveRuntime(
        store,
        graph,
        _envelope(),
        run_id="durable-after-crash",
        workers={"work": _worker},
        crash_after_dispatch_task_ids=("work",),
    )
    with pytest.raises(SimulatedAdaptiveCrash):
        runtime.dispatch_next(occurred_at_ms=1)

    reserved = Usage(tokens=10, cost_microusd=10, context_bytes=10)
    store.complete_attempt(
        run_id="durable-after-crash",
        task_id="work",
        attempt=1,
        output={"durable": True},
        estimated=reserved,
        reserved=reserved,
        actual=Usage(tokens=1, cost_microusd=1, context_bytes=1),
    )
    calls: list[str] = []

    def forbidden(context: AdaptiveTaskContext) -> AdaptiveWorkerResult:
        calls.append(context.task_id)
        return _worker(context)

    restarted = AdaptiveRuntime(
        SQLiteRunStore(database, clock_ms=lambda: 1_000),
        graph,
        _envelope(),
        run_id="durable-after-crash",
        workers={"work": forbidden},
    )
    decision = restarted.recover_unknown_inflight(occurred_at_ms=2)
    assert decision is not None
    assert restarted.state.status is AdaptiveStatus.COMPLETED
    assert calls == []


def test_stored_history_and_completion_disagreement_refuse_restart(tmp_path: Path) -> None:
    forged = _runtime(tmp_path, "stored-forged-history")
    forged.store.append_event(
        run_id=forged.run_id,
        event_id="stored-forged-history:forged-controller-record",
        event_type="adaptive.controller_transition",
        payload={"forged": True},
    )
    with pytest.raises(AdaptiveReplayError, match="failed replay"):
        _runtime(tmp_path, "stored-forged-history")

    mismatch = _runtime(tmp_path, "stored-completion-mismatch")
    started = mismatch.store.start_attempt(
        run_id=mismatch.run_id,
        task_id="work",
        provider="local",
        backend="fixture",
        estimated=Usage(),
        reserved=Usage(),
    )
    mismatch.store.complete_attempt(
        run_id=mismatch.run_id,
        task_id="work",
        attempt=started.attempt or 1,
        output={"forged": True},
        estimated=Usage(),
        reserved=Usage(),
        actual=Usage(),
    )
    with pytest.raises(AdaptiveReplayError, match="disagree"):
        _runtime(tmp_path, "stored-completion-mismatch")

    duplicate = _runtime(tmp_path, "duplicate-control-event")
    first_event = duplicate.controller_records[0]["event"]
    with pytest.raises(AdaptiveInvariantError, match="already exists"):
        duplicate._append_transition(AdaptiveControlEvent.from_dict(first_event))


def test_cancellation_with_optional_inflight_and_run_loop_bounds(tmp_path: Path) -> None:
    optional_graph = _graph(TaskContract("work", (_profile(),), optional=True))
    inflight = _runtime(
        tmp_path,
        "cancel-optional-inflight",
        graph=optional_graph,
        crash=("work",),
    )
    with pytest.raises(SimulatedAdaptiveCrash):
        inflight.dispatch_next(occurred_at_ms=1)
    cancelled = inflight.cancel("stop", occurred_at_ms=2)
    assert cancelled.status is AdaptiveStatus.CANCELLED
    assert inflight.state.unknown_task_ids == ("work",)
    assert inflight.state.shed_task_ids == ("work",)

    mandatory = _runtime(
        tmp_path,
        "cancel-mandatory-inflight",
        crash=("work",),
    )
    with pytest.raises(SimulatedAdaptiveCrash):
        mandatory.dispatch_next(occurred_at_ms=1)
    mandatory.cancel("stop", occurred_at_ms=2)
    assert mandatory.state.unknown_task_ids == ("work",)
    assert mandatory.state.shed_task_ids == ()

    bounded = _runtime(tmp_path, "run-loop-inputs")
    with pytest.raises(AdaptiveInvariantError, match="positive integer"):
        bounded.run_until_blocked(max_dispatches=True)
    bounded.provider_capacity("local", 1, occurred_at_ms=2)
    with pytest.raises(AdaptiveInvariantError, match="move backwards"):
        bounded.run_until_blocked(start_at_ms=1)

    blocked = _runtime(tmp_path, "run-loop-blocked")
    blocked.provider_capacity("local", 0, occurred_at_ms=0)
    result = blocked.run_until_blocked(start_at_ms=0)
    assert result.state.status is AdaptiveStatus.RUNNING

    two_tasks = _graph(
        TaskContract("a", (_profile(name="a"),)),
        TaskContract("b", (_profile(name="b"),)),
    )
    limited = _runtime(tmp_path, "run-loop-limit", graph=two_tasks)
    with pytest.raises(AdaptiveInvariantError, match="dispatch limit"):
        limited.run_until_blocked(max_dispatches=1)


def test_direct_data_objects_round_trip_valid_values(tmp_path: Path) -> None:
    _, records = _valid_records(tmp_path)
    record = AdaptiveControllerRecord.from_dict(records[0])
    assert record.revision == 1
    assert AdaptiveAction.INITIALIZE.value == "initialize"


def test_runtime_rejects_invalid_envelope_subclass_contracts_and_implicit_retry(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "exact-runtime-contracts.sqlite3")
    with pytest.raises(AdaptiveInvariantError, match="invalid run envelope"):
        AdaptiveRuntime(
            store,
            _graph(),
            replace(_envelope(), max_tokens=-1),
            run_id="invalid-envelope",
            workers={"work": _worker},
        )

    class TaskSubclass(TaskContract):
        pass

    subclass_task = TaskSubclass("work", (_profile(),))
    with pytest.raises(AdaptiveInvariantError, match="exact contracts"):
        AdaptiveRuntime(
            store,
            _graph(subclass_task),
            _envelope(),
            run_id="task-subclass",
            workers={"work": _worker},
        )

    class ProfileSubclass(BackendProfile):
        pass

    subclass_profile = ProfileSubclass(
        name="fixture",
        provider="local",
        duration_ms_p50=1,
        duration_ms_p95=2,
    )
    with pytest.raises(AdaptiveInvariantError, match="profiles must use exact contracts"):
        AdaptiveRuntime(
            store,
            _graph(TaskContract("work", (subclass_profile,))),
            _envelope(),
            run_id="profile-subclass",
            workers={"work": _worker},
        )

    wrong_budget = _runtime(tmp_path, "wrong-budget-contract")
    with pytest.raises(AdaptiveInvariantError, match="exact Usage"):
        wrong_budget.cut_budget(object(), occurred_at_ms=0)  # type: ignore[arg-type]

    retry = _runtime(tmp_path, "implicit-retry")
    retry.store.start_attempt(
        run_id=retry.run_id,
        task_id="work",
        provider="local",
        backend="fixture",
        estimated=Usage(),
        reserved=Usage(),
    )
    with pytest.raises(AdaptiveInvariantError, match="implicit retry"):
        retry.dispatch_next(occurred_at_ms=1)
