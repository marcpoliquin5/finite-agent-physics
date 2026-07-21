from __future__ import annotations

import asyncio
import json

import pytest


pytest.importorskip(
    "langgraph.graph",
    reason="install the pinned comparator with `pip install -e .[langgraph]`",
)
pytest.importorskip(
    "langgraph.checkpoint.sqlite.aio",
    reason="install the pinned comparator with `pip install -e .[langgraph]`",
)

from agent_physics.effects import EffectState, SQLiteEffectBroker
from agent_physics.examples import miami_eoc_graph
from agent_physics.langgraph_baseline import (
    BASELINE_SCHEMA_VERSION,
    COMPARATOR_KIND,
    run_langgraph_stormshift_baseline,
)
from agent_physics.cli import main as cli_main
from agent_physics.run_store import SQLiteRunStore
from agent_physics.serialization import content_digest, normalize
from agent_physics.stormshift_runtime import (
    PUBLISH_TASK_ID,
    PURE_TASK_IDS,
    StormShiftRuntime,
)


def _run_baseline(tmp_path, run_id: str = "langgraph-baseline-test"):
    return asyncio.run(
        run_langgraph_stormshift_baseline(
            run_id=run_id,
            checkpoint_path=tmp_path / f"{run_id}.checkpoints.db",
        )
    )


def test_actual_langgraph_executes_every_committed_task_once(tmp_path) -> None:
    record = _run_baseline(tmp_path)
    graph = miami_eoc_graph()

    assert record.schema_version == BASELINE_SCHEMA_VERSION
    assert record.comparator_kind == COMPARATOR_KIND
    assert record.framework == "langgraph"
    assert record.framework_version == "1.2.9"
    assert record.checkpoint_package_version == "3.1.0"
    assert record.checkpoint_verified is True
    assert record.graph_digest == content_digest(graph)
    assert dict(record.task_call_counts) == {task_id: 1 for task_id in graph.by_id}
    assert set(record.outputs) == set(graph.by_id)
    assert record.verify_digest()


def test_all_predecessor_joins_present_exact_dependency_outputs(tmp_path) -> None:
    record = _run_baseline(tmp_path, "langgraph-dependencies")
    graph = miami_eoc_graph()
    observations = {item.task_id: item for item in record.dependency_observations}

    assert set(observations) == set(graph.by_id)
    for task in graph.tasks:
        observation = observations[task.task_id]
        assert observation.dependency_ids == tuple(sorted(task.dependencies))
        assert dict(observation.dependency_output_digests) == {
            dependency: content_digest(record.outputs[dependency])
            for dependency in sorted(task.dependencies)
        }

    assert observations["response_plan"].dependency_ids == tuple(
        sorted(graph.by_id["response_plan"].dependencies)
    )
    assert observations[PUBLISH_TASK_ID].dependency_ids == (
        "multilingual_alert",
        "safety_review",
    )


def test_comparable_outputs_and_validation_match_finite_fixture_runtime(tmp_path) -> None:
    baseline = _run_baseline(tmp_path, "langgraph-semantic-equivalence")
    finite = asyncio.run(
        StormShiftRuntime(
            SQLiteRunStore(tmp_path / "finite-runs.db"),
            SQLiteEffectBroker(tmp_path / "finite-effects.db", broker_id="comparison"),
        ).execute(run_id="finite-semantic-equivalence")
    )
    finite_comparable_outputs = {
        task_id: finite.execution.outputs[task_id]
        for task_id in sorted(PURE_TASK_IDS)
    }

    assert {
        task_id: baseline.outputs[task_id]
        for task_id in sorted(PURE_TASK_IDS)
    } == finite_comparable_outputs
    assert baseline.comparable_output_digest == content_digest(finite_comparable_outputs)
    assert baseline.validation_digest == finite.validation.report_digest
    assert baseline.validation == normalize(finite.validation)
    assert baseline.validation["report_digest"] == finite.validation.report_digest
    assert finite.effect_intent.state is EffectState.PROPOSED


def test_static_profile_manifest_and_real_concurrency_stay_within_caps(tmp_path) -> None:
    record = _run_baseline(tmp_path, "langgraph-cap-conformance")
    envelope_limits = dict(record.configured_provider_limits)

    assert record.configured_max_concurrency == 4
    assert 1 < record.observed_max_worker_concurrency <= 4
    assert dict(record.observed_provider_maxima)
    assert all(
        maximum <= envelope_limits[provider]
        for provider, maximum in record.observed_provider_maxima
    )
    assert dict(record.observed_provider_maxima)["simulated-watsonx"] == 2

    # The frozen static comparator deliberately chooses the highest-quality
    # eligible profile; the deterministic workers do not make those model calls.
    assert {item.task_id for item in record.static_profiles} == set(PURE_TASK_IDS)
    assert {item.profile_name for item in record.static_profiles} == {
        "simulated-granite-accurate"
    }
    assert all(item.quality >= 0.95 for item in record.static_profiles)
    assert record.profile_snapshot_digest == content_digest(record.static_profiles)
    assert record.admission_performed is False
    assert record.retries_configured is False


def test_effect_is_only_a_deterministic_proposal_and_record_replays(tmp_path) -> None:
    first = _run_baseline(tmp_path, "langgraph-deterministic")
    second = asyncio.run(
        run_langgraph_stormshift_baseline(
            run_id="langgraph-deterministic",
            checkpoint_path=tmp_path / "independent-checkpoint.db",
        )
    )
    proposal = first.outputs[PUBLISH_TASK_ID]

    assert isinstance(proposal, dict)
    assert proposal["effect_state"] == "proposed"
    assert proposal["executed_externally"] is False
    assert proposal["approval_grant_present"] is False
    assert str(proposal["effect_intent_id"]).startswith("langgraph-static:")
    assert first.effect_proposal_digest == proposal["proposal_digest"]
    assert first.effect_state == "proposed"
    assert first.cache_enabled is False
    assert first.model_calls_made is False
    assert first.external_calls_made is False
    assert first.external_effects_executed == 0
    assert first.as_dict() == second.as_dict()


def test_cli_emits_a_verified_portable_record(tmp_path, capsys) -> None:
    output = tmp_path / "langgraph-record.json"
    exit_code = cli_main(
        [
            "langgraph-baseline",
            "--run-id",
            "langgraph-cli-portable-record",
            "--checkpoint",
            str(tmp_path / "cli-checkpoint.sqlite"),
            "--output",
            str(output),
        ]
    )

    receipt = json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert receipt["verified"] is True
    assert receipt["framework_version"] == "1.2.9"
    assert receipt["record_digest"] == record["record_digest"]
    assert record["checkpoint_verified"] is True
    assert record["model_calls_made"] is False
    assert record["external_effects_executed"] == 0
