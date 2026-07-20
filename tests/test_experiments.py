import json
from collections import defaultdict
from dataclasses import replace

import pytest

from agent_physics.benchmark import benchmark_envelope, generated_scenario
from agent_physics.experiments import (
    BINDING_BUDGET_CONDITION_ID,
    BOOTSTRAP_RESAMPLING_SEED,
    CLAIM_STATUS,
    DEFAULT_PAIRED_SEEDS,
    EXPERIMENT_POLICIES,
    MEASUREMENT_KIND,
    NOMINAL_CONTROL_ID,
    POLICY_ROLES,
    REGISTERED_SIMULATED_FAULTS,
    REVISION_PROVENANCE,
    SimulatedFaultKind,
    apply_simulated_fault,
    experiment_record_id,
    run_registered_experiments,
    summarize_experiments,
    validate_complete_design,
    wilson_interval,
    write_experiment_jsonl,
)
from agent_physics.scheduler import SchedulePolicy


def test_registered_experiment_and_summary_are_repeatable() -> None:
    first = run_registered_experiments(revision="repeatable-test")
    second = run_registered_experiments(revision="repeatable-test")

    assert first == second
    assert summarize_experiments(first) == summarize_experiments(second)
    assert summarize_experiments(first) == summarize_experiments(tuple(reversed(second)))


def test_registered_design_is_complete_paired_and_truthfully_labeled() -> None:
    records = run_registered_experiments(revision="paired-test")
    expected_count = (
        len(DEFAULT_PAIRED_SEEDS)
        * len(REGISTERED_SIMULATED_FAULTS)
        * len(EXPERIMENT_POLICIES)
    )
    assert expected_count == 450
    assert len(records) == expected_count
    assert {record.seed for record in records} == set(DEFAULT_PAIRED_SEEDS)
    assert {record.measurement_kind for record in records} == {MEASUREMENT_KIND}
    assert {record.fault_execution_status for record in records} == {
        "executed-simulated",
        "not-injected-control",
    }
    assert {record.claim_status for record in records} == {CLAIM_STATUS}
    assert {record.revision_provenance for record in records} == {
        REVISION_PROVENANCE
    }
    assert {
        record.policy_role
        for record in records
        if record.policy == SchedulePolicy.STATIC_PARALLEL.value
    } == {POLICY_ROLES[SchedulePolicy.STATIC_PARALLEL]}
    assert all(record.record_id == experiment_record_id(record) for record in records)

    by_seed = defaultdict(list)
    by_pair = defaultdict(list)
    for record in records:
        by_seed[record.seed].append(record)
        by_pair[(record.seed, record.fault_id)].append(record)

    for seed_records in by_seed.values():
        assert len(
            {
                (
                    record.prefault_graph_digest,
                    record.prefault_envelope_digest,
                    record.prefault_config_digest,
                )
                for record in seed_records
            }
        ) == 1

    expected_policies = {policy.value for policy in EXPERIMENT_POLICIES}
    for pair_records in by_pair.values():
        assert {record.policy for record in pair_records} == expected_policies
        assert len({record.pair_id for record in pair_records}) == 1
        assert len({record.faulted_config_digest for record in pair_records}) == 1

    serialized = json.dumps([record.as_dict() for record in records]).lower()
    assert '"measurement_kind": "live"' not in serialized
    assert "tuned langgraph" not in serialized


def test_each_registered_fault_executes_a_supported_transformation() -> None:
    graph = generated_scenario("mixed", 3)
    envelope = benchmark_envelope()
    applications = {
        fault.kind: apply_simulated_fault(graph, envelope, fault)
        for fault in REGISTERED_SIMULATED_FAULTS
    }

    nominal = applications[SimulatedFaultKind.NOMINAL_CONTROL]
    assert nominal.graph == graph
    assert nominal.envelope == envelope
    assert nominal.affected_items == ()
    assert nominal.injection_phase == "none-control"

    duration = applications[SimulatedFaultKind.DURATION_MULTIPLIER]
    original_profile = graph.by_id["root"].profiles[1]
    duration_profile = duration.graph.by_id["root"].profiles[1]
    assert duration_profile.duration_ms_p50 == original_profile.duration_ms_p50 * 3
    assert duration_profile.duration_ms_p95 == original_profile.duration_ms_p95 * 3
    assert duration.envelope == envelope

    outage = applications[SimulatedFaultKind.PROFILE_OUTAGE]
    assert all(
        profile.name != "sim-economy"
        for task in outage.graph.tasks
        for profile in task.profiles
    )

    capacity = applications[SimulatedFaultKind.PROVIDER_CAPACITY_REDUCTION]
    assert capacity.envelope.provider_limit("sim-provider-b") == 1
    assert capacity.graph == graph

    budget = applications[SimulatedFaultKind.BUDGET_CUT]
    assert budget.envelope.max_tokens == envelope.max_tokens // 75
    assert budget.envelope.max_cost_microusd == envelope.max_cost_microusd // 75
    assert budget.envelope.max_context_bytes == envelope.max_context_bytes // 75
    assert budget.graph == graph


def test_nominal_control_is_explicit_and_identity_preserving() -> None:
    records = run_registered_experiments(revision="nominal-control-test")
    nominal = [record for record in records if record.fault_id == NOMINAL_CONTROL_ID]

    assert len(nominal) == len(DEFAULT_PAIRED_SEEDS) * len(EXPERIMENT_POLICIES)
    assert {record.condition_role for record in nominal} == {"nominal-control"}
    assert {record.fault_execution_status for record in nominal} == {
        "not-injected-control"
    }
    assert all(
        record.prefault_graph_digest == record.faulted_graph_digest
        and record.prefault_envelope_digest == record.faulted_envelope_digest
        and not record.affected_items
        for record in nominal
    )


def test_uniform_budget_condition_is_binding_without_seed_selection() -> None:
    records = run_registered_experiments(revision="binding-budget-test")
    budget_records = [
        record
        for record in records
        if record.fault_id == BINDING_BUDGET_CONDITION_ID
    ]

    for policy in EXPERIMENT_POLICIES:
        policy_records = [
            record for record in budget_records if record.policy == policy.value
        ]
        assert {record.seed for record in policy_records} == set(DEFAULT_PAIRED_SEEDS)
        assert any(record.success for record in policy_records)
        assert any(not record.success for record in policy_records)


def test_registered_design_rejects_uncalibrated_scenarios() -> None:
    with pytest.raises(ValueError, match="scenario is frozen to 'mixed'"):
        run_registered_experiments(revision="frozen-scenario-test", scenario="diamond")


def test_validation_rejects_content_tampering_even_with_recomputed_id() -> None:
    records = run_registered_experiments(revision="tamper-test")
    original = records[0]
    changed_metric = replace(original, tokens=original.tokens + 1)

    with pytest.raises(ValueError, match="record content digest mismatch"):
        validate_complete_design((changed_metric,) + records[1:])

    readdressed = replace(
        changed_metric,
        record_id=experiment_record_id(changed_metric),
    )
    with pytest.raises(ValueError, match="registered deterministic execution"):
        validate_complete_design((readdressed,) + records[1:])


def test_summary_and_writer_reject_cherry_picked_or_duplicate_records(tmp_path) -> None:  # type: ignore[no-untyped-def]
    records = run_registered_experiments(revision="no-cherry-pick-test")
    validate_complete_design(records)

    with pytest.raises(ValueError, match="frozen Cartesian product"):
        summarize_experiments(records[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        validate_complete_design(records + (records[0],))
    with pytest.raises(ValueError, match="frozen Cartesian product"):
        write_experiment_jsonl(tmp_path / "partial.jsonl", records[1:])


def test_successful_records_respect_their_planning_model_bound() -> None:
    records = run_registered_experiments(revision="bound-test")
    successful = [record for record in records if record.success]
    assert successful
    assert all(
        record.modeled_success_makespan_ms is not None
        and record.model_bound_ms <= record.modeled_success_makespan_ms
        and record.modeled_time_to_failure_ms is None
        for record in successful
    )
    assert all(
        record.modeled_success_makespan_ms is None
        and record.modeled_time_to_failure_ms
        == record.modeled_schedule_position_ms
        and record.model_bound_respected is None
        for record in records
        if not record.success
    )
    assert all(record.model_bound_respected is True for record in successful)


def test_jsonl_is_raw_complete_labeled_and_byte_repeatable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    records = run_registered_experiments(revision="jsonl-test")
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    write_experiment_jsonl(first_path, records)
    write_experiment_jsonl(second_path, reversed(records))

    assert first_path.read_bytes() == second_path.read_bytes()
    payloads = [json.loads(line) for line in first_path.read_text().splitlines()]
    assert len(payloads) == len(records)
    assert {payload["measurement_kind"] for payload in payloads} == {
        "deterministic-simulation"
    }
    assert {payload["seed"] for payload in payloads} == set(DEFAULT_PAIRED_SEEDS)


def test_summary_preserves_pairs_and_separates_success_from_failure_timing() -> None:
    records = run_registered_experiments(revision="interval-test")
    summary = summarize_experiments(records)
    design = summary["design"]
    assert design["paired_seed_count"] == 30
    assert design["bootstrap_resampling_seed"] == BOOTSTRAP_RESAMPLING_SEED
    assert design["bootstrap_unit"] == "paired-seed"
    assert design["nominal_control_id"] == NOMINAL_CONTROL_ID
    assert design["policy_roles"][SchedulePolicy.STATIC_PARALLEL.value] == (
        POLICY_ROLES[SchedulePolicy.STATIC_PARALLEL]
    )
    assert summary["claim_status"] == CLAIM_STATUS
    assert summary["revision_provenance"] == REVISION_PROVENANCE
    assert "no superiority" in summary["evidence_scope"].lower()

    groups = summary["groups"]
    assert len(groups) == len(REGISTERED_SIMULATED_FAULTS) * len(EXPERIMENT_POLICIES)
    assert all(group["runs"] == 30 for group in groups)
    assert all("wilson_pass_rate_95" in group for group in groups)
    assert all("modeled_performance_on_successes" in group for group in groups)
    assert all("modeled_failure_timing" in group for group in groups)
    assert "makespan_ms_all_runs" not in json.dumps(summary)

    comparisons = summary["paired_deltas_vs_adaptive"]
    assert len(comparisons) == len(REGISTERED_SIMULATED_FAULTS) * 2
    assert all(comparison["baseline_policy"] == "adaptive" for comparison in comparisons)
    assert all(len(comparison["per_seed"]) == 30 for comparison in comparisons)
    assert all(
        comparison["paired_delta_summary"]["success_indicator"][
            "bootstrap_mean_95"
        ]
        is not None
        for comparison in comparisons
    )
    assert {
        row["seed"] for comparison in comparisons for row in comparison["per_seed"]
    } == set(DEFAULT_PAIRED_SEEDS)

    nominal_static = next(
        comparison
        for comparison in comparisons
        if comparison["fault_id"] == NOMINAL_CONTROL_ID
        and comparison["comparison_policy"] == SchedulePolicy.STATIC_PARALLEL.value
    )
    seed_zero = nominal_static["per_seed"][0]
    adaptive_record = next(
        record
        for record in records
        if record.fault_id == NOMINAL_CONTROL_ID
        and record.policy == SchedulePolicy.ADAPTIVE.value
        and record.seed == 0
    )
    static_record = next(
        record
        for record in records
        if record.fault_id == NOMINAL_CONTROL_ID
        and record.policy == SchedulePolicy.STATIC_PARALLEL.value
        and record.seed == 0
    )
    assert seed_zero["success_indicator_delta"] == (
        int(static_record.success) - int(adaptive_record.success)
    )
    assert seed_zero["modeled_success_makespan_ms_delta"] == (
        static_record.modeled_success_makespan_ms
        - adaptive_record.modeled_success_makespan_ms
    )

    no_pass_lower, no_pass_upper = wilson_interval(0, 30)
    all_pass_lower, all_pass_upper = wilson_interval(30, 30)
    assert no_pass_lower == 0.0
    assert 0.11 < no_pass_upper < 0.12
    assert 0.88 < all_pass_lower < 0.89
    assert all_pass_upper == 1.0
