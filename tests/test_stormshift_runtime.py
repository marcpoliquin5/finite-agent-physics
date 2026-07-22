from __future__ import annotations

import asyncio

import pytest

from agent_physics.effects import EffectState, SQLiteEffectBroker
from agent_physics.executor import OutputValidationError, RunState
from agent_physics.run_store import SQLiteRunStore
from agent_physics.stormshift import (
    VALIDATION_LIMITATIONS,
    VALIDATION_SCOPE,
    fault_capacity_loss,
    stormshift_fixture,
)
from agent_physics.stormshift_runtime import (
    PURE_TASK_IDS,
    PUBLISH_TASK_ID,
    StormShiftRuntime,
)


def test_runtime_executes_meaningful_fixture_graph_and_stops_before_write(tmp_path) -> None:
    runtime = StormShiftRuntime(
        SQLiteRunStore(tmp_path / "runs.db"),
        SQLiteEffectBroker(tmp_path / "effects.db", broker_id="stormshift-test"),
    )

    result = asyncio.run(runtime.execute(run_id="stormshift-runtime"))

    assert result.execution.run_state is RunState.AWAITING_EFFECTS
    assert result.execution.skipped_task_ids == ()
    assert set(result.execution.outputs) == {*PURE_TASK_IDS, PUBLISH_TASK_ID}
    assert result.worker_call_counts == {task_id: 1 for task_id in PURE_TASK_IDS}
    assert result.external_calls_made is False
    assert result.model_calls_made is False

    shelter_output = result.execution.outputs["shelter_status"]
    assert isinstance(shelter_output, dict)
    assert shelter_output["total_available"] == 180
    assert shelter_output["total_accessible_available"] == 24
    flood_output = result.execution.outputs["flood_zones"]
    assert isinstance(flood_output, dict)
    assert flood_output["closed_segments"] == ["sim-low-road-x", "sim-low-road-y"]
    social_output = result.execution.outputs["social_signal_scan"]
    assert isinstance(social_output, dict)
    assert social_output["live_scan_performed"] is False
    assert social_output["signals"] == []

    assert sum(item.evacuees for item in result.response_plan.allocations) == 180
    assert result.validation.passed
    assert result.validation.verify_digest()
    assert result.validation.scope == VALIDATION_SCOPE
    assert result.validation.limitations == VALIDATION_LIMITATIONS
    assert result.validator_kind == "deterministic_structural_plus_bounded_semantic"
    assert result.semantic_validation.passed is True
    assert result.semantic_validation.verify_digest() is True
    assert result.alert_preview["english_language_tag"] == "en"
    assert result.alert_preview["spanish_language_tag"] == "es"
    assert result.alert_preview["english"] == result.response_plan.alert.english
    assert result.alert_preview["spanish"] == result.response_plan.alert.spanish

    assert result.effect_intent.state is EffectState.PROPOSED
    assert result.effect_intent.action == PUBLISH_TASK_ID
    assert result.effect_intent.resource == "public-alert-channel"
    effect_output = result.execution.outputs[PUBLISH_TASK_ID]
    assert isinstance(effect_output, dict)
    assert effect_output == {
        "effect_intent_id": result.effect_intent.intent_id,
        "effect_state": "proposed",
        "executed_externally": False,
    }
    assert not any(
        event.task_id == PUBLISH_TASK_ID
        for event in result.execution.events
        if event.event_type == "task.attempt_started"
    )
    assert any(event.event_type == "run.awaiting_effects" for event in result.execution.events)
    assert not any(event.event_type == "run.completed" for event in result.execution.events)


def test_restart_revalidates_every_output_without_worker_reexecution(tmp_path) -> None:
    run_database = tmp_path / "runs.db"
    effect_database = tmp_path / "effects.db"
    first_runtime = StormShiftRuntime(
        SQLiteRunStore(run_database),
        SQLiteEffectBroker(effect_database, broker_id="first-process"),
    )
    first = asyncio.run(first_runtime.execute(run_id="stormshift-restart"))
    first_event_count = len(first.execution.events)

    restarted_runtime = StormShiftRuntime(
        SQLiteRunStore(run_database),
        SQLiteEffectBroker(effect_database, broker_id="restarted-process"),
    )
    restarted = asyncio.run(restarted_runtime.execute(run_id="stormshift-restart"))

    assert first.worker_call_counts == {task_id: 1 for task_id in PURE_TASK_IDS}
    assert restarted.worker_call_counts == {task_id: 0 for task_id in PURE_TASK_IDS}
    assert restarted.execution.resumed_task_ids == tuple(sorted((*PURE_TASK_IDS, PUBLISH_TASK_ID)))
    assert len(restarted.execution.events) == first_event_count
    assert restarted.execution.outputs == first.execution.outputs
    assert restarted.response_plan == first.response_plan
    assert restarted.validation == first.validation
    assert restarted.effect_intent.intent_id == first.effect_intent.intent_id
    assert restarted.effect_intent.state is EffectState.PROPOSED
    assert restarted.execution.run_state is RunState.AWAITING_EFFECTS


def test_structurally_invalid_fixture_fails_before_effect_proposal(tmp_path) -> None:
    scenario = fault_capacity_loss(
        stormshift_fixture(),
        shelter_id="SIM-SHELTER-ALPHA",
        lost_spaces=1,
    )
    store = SQLiteRunStore(tmp_path / "runs.db")
    broker = SQLiteEffectBroker(tmp_path / "effects.db", broker_id="fail-closed")
    runtime = StormShiftRuntime(store, broker, scenario=scenario)

    with pytest.raises(OutputValidationError):
        asyncio.run(runtime.execute(run_id="stormshift-capacity-loss"))

    assert not any(
        event.event_type == "task.effect_intent_created"
        for event in store.events("stormshift-capacity-loss")
    )
    assert broker.pending_outbox() == ()
