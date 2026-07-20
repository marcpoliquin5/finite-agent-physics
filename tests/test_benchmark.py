from itertools import product
import json

from agent_physics import BackendProfile, ExecutionGraph, RunEnvelope, Scheduler, TaskContract
from agent_physics.benchmark import (
    exact_additive_admission_oracle,
    generated_scenario,
    run_simulated_benchmark,
    summarize_simulated_records,
    write_jsonl,
)


def test_generated_scenarios_and_records_are_reproducible() -> None:
    assert generated_scenario("mixed", 17) == generated_scenario("mixed", 17)
    first = run_simulated_benchmark(scenario="diamond", seeds=(1, 2), revision="test")
    second = run_simulated_benchmark(scenario="diamond", seeds=(1, 2), revision="test")
    assert first == second
    assert {record.measurement_kind for record in first} == {"deterministic-simulation"}
    summary = summarize_simulated_records(first)
    assert summary["claim_status"] == "descriptive-only"
    selected_by_seed: dict[int, set[tuple[tuple[str, str], ...]]] = {}
    for record in first:
        selected_by_seed.setdefault(record.seed, set()).add(record.selected_backends)
    assert all(len(selections) == 1 for selections in selected_by_seed.values())


def test_every_successful_simulation_respects_its_model_bound() -> None:
    for shape, seed in product(("chain", "fanout", "diamond", "mixed"), range(10)):
        records = run_simulated_benchmark(scenario=shape, seeds=(seed,), revision="test")
        assert all(
            not record.success or record.model_bound_ms <= record.makespan_ms
            for record in records
        )


def test_scheduler_admission_agrees_with_exhaustive_small_oracle() -> None:
    profiles_a = (
        BackendProfile("a-heavy-token", "sim", 1, 1, input_tokens=9, cost_microusd=1),
        BackendProfile("a-heavy-cost", "sim", 1, 1, input_tokens=1, cost_microusd=9),
    )
    profiles_b = (
        BackendProfile("b-heavy-token", "sim", 1, 1, input_tokens=9, cost_microusd=1),
        BackendProfile("b-heavy-cost", "sim", 1, 1, input_tokens=1, cost_microusd=9),
    )
    graph = ExecutionGraph.from_tasks(
        [TaskContract("a", profiles_a), TaskContract("b", profiles_b, ("a",))]
    )
    for token_cap, cost_cap in product(range(2, 21), repeat=2):
        envelope = RunEnvelope(
            deadline_ms=100,
            max_tokens=token_cap,
            max_cost_microusd=cost_cap,
            max_context_bytes=100,
            max_parallelism=1,
        )
        oracle = exact_additive_admission_oracle(graph, envelope)
        result = Scheduler().schedule(graph, envelope)
        assert result.success is oracle.feasible


def test_raw_jsonl_preserves_revision_config_and_simulation_label(tmp_path) -> None:  # type: ignore[no-untyped-def]
    records = run_simulated_benchmark(scenario="chain", seeds=(7,), revision="abc123")
    destination = tmp_path / "raw.jsonl"
    write_jsonl(destination, records)
    payloads = [json.loads(line) for line in destination.read_text().splitlines()]
    assert {payload["revision"] for payload in payloads} == {"abc123"}
    assert {payload["measurement_kind"] for payload in payloads} == {
        "deterministic-simulation"
    }
    assert len({payload["config_digest"] for payload in payloads}) == 1
