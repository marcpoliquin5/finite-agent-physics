from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from functools import lru_cache
from types import SimpleNamespace

import pytest

import agent_physics.fair_benchmark as fair_benchmark_module
from agent_physics.cli import main as cli_main
from agent_physics.executor import AdmissionRefused
from agent_physics.fair_benchmark import (
    DEFAULT_MEASURED_SEEDS,
    FINITE_SYSTEM_ID,
    LANGGRAPH_SYSTEM_ID,
    PAGEAGENT_SYSTEM_ID,
    PYTHON_SYSTEM_ID,
    FairBenchmarkInvariantError,
    build_fair_benchmark_contract,
    capture_benchmark_environment,
    classify_safety,
    run_fair_benchmark,
    summarize_fair_benchmark,
    write_fair_benchmark_evidence,
)
from agent_physics.langgraph_baseline import langgraph_baseline_available
from agent_physics.serialization import canonical_json, content_digest, normalize


@lru_cache(maxsize=1)
def _evidence():
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    return asyncio.run(run_fair_benchmark(contract))


def _redigest_record(record, **changes):
    changed = replace(record, **changes)
    payload = normalize(changed)
    payload.pop("record_digest")
    return replace(changed, record_digest=content_digest(payload))


def _redigest_contract(contract, **changes):
    changed = replace(contract, **changes)
    payload = normalize(changed)
    payload.pop("contract_digest")
    return replace(changed, contract_digest=content_digest(payload))


def _redigest_status(status, **changes):
    changed = replace(status, **changes)
    payload = normalize(changed)
    payload.pop("status_digest")
    return replace(changed, status_digest=content_digest(payload))


def _redigest_report(report, **changes):
    changed = replace(report, **changes)
    payload = normalize(changed)
    payload.pop("report_digest")
    return replace(changed, report_digest=content_digest(payload))


def test_cli_writes_verified_fair_benchmark_receipt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _evidence()

    async def fake_run(contract, *, output_directory=None):
        assert contract.bootstrap_samples == 200
        assert output_directory is not None
        write_fair_benchmark_evidence(evidence, output_directory)
        return evidence

    monkeypatch.setattr(fair_benchmark_module, "run_fair_benchmark", fake_run)
    output = tmp_path / "fair"
    assert (
        cli_main(
            [
                "fair-benchmark",
                "--bootstrap-samples",
                "200",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["verified"] is True
    assert receipt["evidence_digest"] == evidence.evidence_digest
    assert PAGEAGENT_SYSTEM_ID in receipt["unexecuted_systems"]
    assert (output / "raw-records.jsonl").is_file()


def test_contract_preregisters_workload_systems_and_claim_boundaries() -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)

    assert contract.verify_digest()
    assert contract.warmup_count == 1
    assert contract.measured_seeds == DEFAULT_MEASURED_SEEDS
    assert contract.timer == "time.perf_counter_ns"
    assert len(contract.graph_digest) == 64
    assert len(contract.expected_comparable_output_digest) == 64
    assert [system.system_id for system in contract.systems] == [
        FINITE_SYSTEM_ID,
        PYTHON_SYSTEM_ID,
        LANGGRAPH_SYSTEM_ID,
        PAGEAGENT_SYSTEM_ID,
    ]
    pageagent = contract.systems[-1]
    assert pageagent.execution_mode == "unexecuted_reference_only"
    assert pageagent.metrics_policy == "forbid_metrics"
    assert pageagent.version_pin is None
    assert any("No superiority" in boundary for boundary in contract.claim_boundaries)
    assert any("different declared profiles" in boundary for boundary in contract.non_equivalence_boundaries)
    assert contract.as_dict()["contract_digest"] == contract.contract_digest


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"warmup_count": 0, "bootstrap_samples": 200}, "warmup"),
        ({"measured_seeds": (1, 2, 3, 4), "bootstrap_samples": 200}, "measured seeds"),
        ({"measured_seeds": (1, 2, 3, 4, 4), "bootstrap_samples": 200}, "unique"),
        ({"measured_seeds": (1, 2, 3, 4, -5), "bootstrap_samples": 200}, "int64"),
        ({"bootstrap_samples": 199}, "bootstrap_samples"),
    ],
)
def test_contract_rejects_underpowered_or_ambiguous_designs(arguments, match) -> None:
    with pytest.raises(FairBenchmarkInvariantError, match=match):
        build_fair_benchmark_contract(**arguments)


def test_environment_is_digest_bound_and_excludes_direct_identifiers() -> None:
    environment = capture_benchmark_environment()
    serialized = canonical_json(environment).lower()

    assert environment.verify_digest()
    assert environment.python_version
    assert environment.perf_counter_monotonic is True
    assert "hostname" in environment.excluded_identifiers
    assert "username" in environment.excluded_identifiers
    assert "\\users\\" not in serialized
    assert "/users/" not in serialized
    assert environment.as_dict()["environment_digest"] == environment.environment_digest


def test_actual_local_receipts_share_the_registered_workload() -> None:
    evidence = _evidence()
    evidence.verify()
    statuses = {status.system_id: status for status in evidence.report.system_statuses}
    executed = {
        system_id
        for system_id, status in statuses.items()
        if status.execution_status == "executed-local"
    }

    assert {FINITE_SYSTEM_ID, PYTHON_SYSTEM_ID} <= executed
    assert statuses[PAGEAGENT_SYSTEM_ID].execution_status == "not-executed"
    assert statuses[PAGEAGENT_SYSTEM_ID].metrics_eligible is False
    assert statuses[PAGEAGENT_SYSTEM_ID].reason_code == "no_local_pinned_integration_or_execution"
    assert not any(record.system_id == PAGEAGENT_SYSTEM_ID for record in evidence.records)
    assert len(evidence.records) == len(executed) * (1 + len(DEFAULT_MEASURED_SEEDS))

    for system_id in executed:
        records = [record for record in evidence.records if record.system_id == system_id]
        warmups = [record for record in records if record.phase == "warmup"]
        measured = [record for record in records if record.phase == "measured"]
        assert len(warmups) == 1
        assert warmups[0].seed is None
        assert {record.seed for record in measured} == set(DEFAULT_MEASURED_SEEDS)
        assert all(record.execution_status == "executed-local" for record in records)
        assert all(record.workload_id == evidence.contract.workload_id for record in records)
        assert all(record.graph_digest == evidence.contract.graph_digest for record in records)
        assert all(record.envelope_digest == evidence.contract.envelope_digest for record in records)
        assert all(record.fixture_digest == evidence.contract.fixture_digest for record in records)
        assert all(record.outcome == "passed" for record in records)
        assert all(record.slo_passed for record in records)
        assert all(record.comparable_output_conforms for record in records)
        assert all(record.common_validation_passed for record in records)
        assert all(record.guardrail_passed for record in records)
        assert all(record.external_effects_executed == 0 for record in records)
        assert all(record.model_calls_made is False for record in records)
        assert all(record.external_calls_made is False for record in records)
        assert all(record.effect_state == "proposed" for record in records)
        assert all(record.verify_digest() for record in records)


def test_langgraph_is_executed_only_at_the_exact_local_pin() -> None:
    evidence = _evidence()
    status = next(
        item for item in evidence.report.system_statuses if item.system_id == LANGGRAPH_SYSTEM_ID
    )

    if langgraph_baseline_available():
        # Availability alone is insufficient; the runner also checks both exact versions.
        if status.execution_status == "executed-local":
            assert status.framework_version == "1.2.9"
            records = [
                record for record in evidence.records if record.system_id == LANGGRAPH_SYSTEM_ID
            ]
            assert records
            assert all(record.checkpoint_verified for record in records)
            assert all(record.admission_performed is False for record in records)
            assert all(record.profile_selection_source.startswith("highest-quality") for record in records)
        else:
            assert status.reason_code == "installed_version_pin_mismatch"
    else:
        assert status.execution_status == "not-executed"
        assert status.reason_code == "optional_packages_not_installed"


def test_finite_additional_semantic_scope_is_not_attributed_to_baselines() -> None:
    evidence = _evidence()
    finite = [record for record in evidence.records if record.system_id == FINITE_SYSTEM_ID]
    python = [record for record in evidence.records if record.system_id == PYTHON_SYSTEM_ID]

    assert all(record.admission_performed is True for record in finite)
    assert all(record.checkpoint_verified is True for record in finite)
    assert all(record.declared_resource_fit is True for record in finite)
    assert all(record.additional_validation_passed is True for record in finite)
    assert all("semantic-safety" in record.additional_validation_scope for record in finite)
    assert all(record.admission_performed is False for record in python)
    assert all(record.checkpoint_verified is False for record in python)
    assert all(record.declared_resource_fit is None for record in python)
    assert all(record.additional_validation_scope is None for record in python)
    assert all(record.additional_validation_passed is None for record in python)


def test_summary_uses_measured_trials_only_and_pairs_by_seed() -> None:
    evidence = _evidence()
    report = evidence.report
    executed = [
        status
        for status in report.system_statuses
        if status.execution_status == "executed-local"
    ]

    assert report.warmups_excluded_from_statistics is True
    assert report.warmup_records == len(executed)
    assert report.measured_records == len(executed) * len(DEFAULT_MEASURED_SEEDS)
    assert report.all_passed_outputs_match_registered_fixture is True
    assert PAGEAGENT_SYSTEM_ID in report.unexecuted_system_ids
    assert PAGEAGENT_SYSTEM_ID not in {summary.system_id for summary in report.system_summaries}
    for summary in report.system_summaries:
        assert summary.measured_runs == len(DEFAULT_MEASURED_SEEDS)
        assert summary.slo_passes == len(DEFAULT_MEASURED_SEEDS)
        assert summary.pass_rate == 1.0
        assert summary.pass_rate_wilson_95.lower < 1.0
        assert summary.pass_rate_wilson_95.upper == 1.0
        assert summary.guardrail_rate == 1.0
        assert summary.duration is not None
        assert summary.duration.count == len(DEFAULT_MEASURED_SEEDS)
        assert summary.duration.p50_ns > 0
        assert summary.duration.bootstrap_p95_95.upper >= summary.duration.bootstrap_p95_95.lower
    assert {item.baseline_system_id for item in report.paired_duration_comparisons} == {
        status.system_id for status in executed if status.system_id != FINITE_SYSTEM_ID
    }
    assert all(
        comparison.eligible_pairs == len(DEFAULT_MEASURED_SEEDS)
        and comparison.excluded_pairs == 0
        and comparison.bootstrap_mean_delta_95 is not None
        for comparison in report.paired_duration_comparisons
    )
    assert report.as_dict()["report_digest"] == report.report_digest
    assert evidence.records[0].as_dict()["record_digest"] == evidence.records[0].record_digest


def test_report_generation_is_order_independent() -> None:
    evidence = _evidence()
    regenerated = summarize_fair_benchmark(
        evidence.contract,
        evidence.environment,
        tuple(reversed(evidence.report.system_statuses)),
        tuple(reversed(evidence.records)),
    )

    assert regenerated == evidence.report


def test_writer_is_byte_stable_and_keeps_raw_warmups(tmp_path) -> None:
    evidence = _evidence()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = write_fair_benchmark_evidence(evidence, first)
    second_manifest = write_fair_benchmark_evidence(evidence, second)

    assert first_manifest == second_manifest
    for name in (
        "contract.json",
        "environment.json",
        "evidence.json",
        "raw-records.jsonl",
        "report.json",
        "manifest.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    lines = (first / "raw-records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(evidence.records)
    assert any('"phase":"warmup"' in line for line in lines)
    assert first_manifest.raw_record_count == len(evidence.records)


def test_record_tampering_and_pageagent_metric_fabrication_are_rejected() -> None:
    evidence = _evidence()
    record = evidence.records[0]
    tampered = replace(record, duration_ns=record.duration_ns + 1)
    with pytest.raises(FairBenchmarkInvariantError, match="digest"):
        tampered.validate()

    fabricated_pageagent = _redigest_record(
        record,
        system_id=PAGEAGENT_SYSTEM_ID,
        system_label="Alibaba PageAgent",
        framework="pageagent",
        framework_version="invented",
    )
    fabricated_pageagent.validate()
    with pytest.raises(FairBenchmarkInvariantError, match="PageAgent raw metrics"):
        summarize_fair_benchmark(
            evidence.contract,
            evidence.environment,
            evidence.report.system_statuses,
            (*evidence.records, fabricated_pageagent),
        )


def test_refusal_and_unsafe_effect_semantics_are_distinct() -> None:
    assert classify_safety(
        outcome="refused",
        effect_state="none",
        model_calls_made=False,
        external_calls_made=False,
        external_effects_executed=0,
        unauthorized_effects=0,
    ) == (True, True)
    assert classify_safety(
        outcome="error",
        effect_state="unknown",
        model_calls_made=False,
        external_calls_made=False,
        external_effects_executed=0,
        unauthorized_effects=0,
    ) == (False, False)
    assert classify_safety(
        outcome="passed",
        effect_state="executed",
        model_calls_made=False,
        external_calls_made=True,
        external_effects_executed=1,
        unauthorized_effects=1,
    ) == (False, False)
    with pytest.raises(ValueError, match="outcome"):
        classify_safety(
            outcome="unknown",
            effect_state="none",
            model_calls_made=False,
            external_calls_made=False,
            external_effects_executed=0,
            unauthorized_effects=0,
        )


def test_actual_runner_failures_become_redacted_refusal_or_error_receipts() -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    environment = capture_benchmark_environment()
    system = contract.systems[0]

    async def refuse(_run_id):
        raise AdmissionRefused("sensitive refusal details must not be retained")

    async def fail(_run_id):
        raise RuntimeError("sensitive exception details must not be retained")

    refusal = asyncio.run(
        fair_benchmark_module._invoke_one(
            runner=refuse,
            contract=contract,
            environment=environment,
            system=system,
            phase="warmup",
            sample_index=0,
            seed=None,
            order_position=0,
        )
    )
    error = asyncio.run(
        fair_benchmark_module._invoke_one(
            runner=fail,
            contract=contract,
            environment=environment,
            system=system,
            phase="measured",
            sample_index=0,
            seed=contract.measured_seeds[0],
            order_position=0,
        )
    )

    assert refusal.outcome == "refused"
    assert refusal.refusal_code == "AdmissionRefused"
    assert refusal.safe_refusal is True
    assert refusal.guardrail_passed is True
    assert refusal.slo_passed is False
    assert refusal.error_type is None
    assert "sensitive" not in canonical_json(refusal)
    refusal.validate()
    assert error.outcome == "error"
    assert error.error_type == "RuntimeError"
    assert error.guardrail_passed is False
    assert error.safe_refusal is False
    assert error.refusal_code is None
    assert "sensitive" not in canonical_json(error)
    error.validate()


def test_caller_cancellation_is_not_converted_into_a_benchmark_error() -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    environment = capture_benchmark_environment()

    async def cancel(_run_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            fair_benchmark_module._invoke_one(
                runner=cancel,
                contract=contract,
                environment=environment,
                system=contract.systems[0],
                phase="warmup",
                sample_index=0,
                seed=None,
                order_position=0,
            )
        )
    with pytest.raises(ValueError, match="effect counts"):
        classify_safety(
            outcome="passed",
            effect_state="proposed",
            model_calls_made=False,
            external_calls_made=False,
            external_effects_executed=-1,
            unauthorized_effects=0,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda contract: replace(contract, schema_version="unsupported"),
        lambda contract: replace(contract, benchmark_id=""),
        lambda contract: replace(contract, timer="time.time_ns"),
        lambda contract: replace(contract, confidence_level=0.90),
        lambda contract: replace(contract, systems=contract.systems[:-1]),
        lambda contract: replace(
            contract,
            systems=(
                *contract.systems[:-1],
                replace(contract.systems[-1], execution_mode="actual_local_required"),
            ),
        ),
        lambda contract: replace(contract, contract_digest="0" * 64),
    ],
)
def test_contract_mutation_is_rejected(mutation) -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    with pytest.raises(FairBenchmarkInvariantError):
        mutation(contract).validate()


def test_record_invariant_matrix_rejects_internally_consistent_digest_forgery() -> None:
    evidence = _evidence()
    warmup = evidence.records[0]
    measured = next(record for record in evidence.records if record.phase == "measured")
    invalid_records = (
        _redigest_record(warmup, schema_version="unsupported"),
        _redigest_record(warmup, execution_status="not-executed"),
        _redigest_record(warmup, phase="unknown"),
        _redigest_record(measured, seed=None),
        _redigest_record(warmup, seed=1),
        _redigest_record(warmup, duration_ns=-1),
        _redigest_record(warmup, outcome="unknown"),
        _redigest_record(warmup, external_effects_executed=-1),
        _redigest_record(warmup, external_effects_executed=0, unauthorized_effects=1),
        _redigest_record(warmup, guardrail_passed=False),
        _redigest_record(warmup, safe_refusal=True),
        _redigest_record(warmup, slo_passed=False),
        _redigest_record(warmup, common_validation_digest=None),
        _redigest_record(warmup, refusal_code="NotARefusal"),
        _redigest_record(
            warmup,
            outcome="refused",
            effect_state="none",
            guardrail_passed=True,
            safe_refusal=True,
            slo_passed=False,
        ),
        _redigest_record(
            warmup,
            outcome="error",
            effect_state="unknown",
            guardrail_passed=False,
            safe_refusal=False,
            slo_passed=False,
        ),
        _redigest_record(warmup, selected_profiles_digest="0" * 64),
        _redigest_record(warmup, declared_profile_totals=None),
        _redigest_record(warmup, selected_profiles=()),
    )

    for record in invalid_records:
        assert record.verify_digest()
        with pytest.raises(FairBenchmarkInvariantError):
            record.validate()


def test_evidence_bundle_rejects_rebinding_reordering_and_report_substitution() -> None:
    evidence = _evidence()
    first = evidence.records[0]

    with pytest.raises(FairBenchmarkInvariantError, match="schema"):
        replace(evidence, schema_version="unsupported").verify()
    bad_environment = replace(evidence.environment, python_version="0.0")
    with pytest.raises(FairBenchmarkInvariantError, match="environment digest"):
        replace(evidence, environment=bad_environment).verify()
    wrong_contract_record = _redigest_record(first, contract_digest="0" * 64)
    with pytest.raises(FairBenchmarkInvariantError, match="another contract"):
        replace(evidence, records=(wrong_contract_record, *evidence.records[1:])).verify()
    wrong_environment_record = _redigest_record(first, environment_digest="0" * 64)
    with pytest.raises(FairBenchmarkInvariantError, match="another environment"):
        replace(evidence, records=(wrong_environment_record, *evidence.records[1:])).verify()
    with pytest.raises(FairBenchmarkInvariantError, match="canonical order"):
        replace(evidence, records=tuple(reversed(evidence.records))).verify()

    substituted_report = _redigest_report(
        evidence.report,
        statistical_scope="substituted after execution",
    )
    with pytest.raises(FairBenchmarkInvariantError, match="deterministic regeneration"):
        replace(evidence, report=substituted_report).verify()
    with pytest.raises(FairBenchmarkInvariantError, match="evidence digest"):
        replace(evidence, evidence_digest="0" * 64).verify()


def test_local_readiness_never_substitutes_an_unpinned_langgraph(monkeypatch) -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    monkeypatch.setattr(fair_benchmark_module, "langgraph_baseline_available", lambda: False)
    runners, unavailable = fair_benchmark_module._system_readiness(contract)
    assert LANGGRAPH_SYSTEM_ID not in runners
    assert unavailable[LANGGRAPH_SYSTEM_ID][0] == "optional_packages_not_installed"

    monkeypatch.setattr(fair_benchmark_module, "langgraph_baseline_available", lambda: True)
    monkeypatch.setattr(fair_benchmark_module, "_package_version", lambda _package: "0.0")
    runners, unavailable = fair_benchmark_module._system_readiness(contract)
    assert LANGGRAPH_SYSTEM_ID not in runners
    assert unavailable[LANGGRAPH_SYSTEM_ID][0] == "installed_version_pin_mismatch"
    assert "langgraph=0.0" in unavailable[LANGGRAPH_SYSTEM_ID][1]

    versions = {
        "langgraph": fair_benchmark_module.EXPECTED_LANGGRAPH_VERSION,
        "langgraph-checkpoint-sqlite": (
            fair_benchmark_module.EXPECTED_LANGGRAPH_CHECKPOINT_VERSION
        ),
    }
    monkeypatch.setattr(
        fair_benchmark_module, "_package_version", lambda package: versions[package]
    )
    runners, unavailable = fair_benchmark_module._system_readiness(contract)
    assert LANGGRAPH_SYSTEM_ID in runners
    assert LANGGRAPH_SYSTEM_ID not in unavailable


def test_langgraph_receipt_normalization_does_not_require_importing_framework(monkeypatch) -> None:
    static_profile = SimpleNamespace(
        task_id="fixture-task",
        profile_name="declared-profile",
        provider="declared-provider",
        duration_ms_p95=5,
        input_tokens=2,
        output_tokens=3,
        cost_microusd=7,
        context_bytes=11,
        quality=0.9,
        failure_probability=0.01,
    )
    baseline_record = SimpleNamespace(
        static_profiles=(static_profile,),
        validation={"passed": True},
        framework="langgraph",
        framework_version="1.2.9",
        validation_digest="a" * 64,
        comparable_output_digest="b" * 64,
        effect_state="proposed",
        external_effects_executed=0,
        checkpoint_verified=True,
        cache_enabled=False,
        admission_performed=False,
        retries_configured=False,
        model_calls_made=False,
        external_calls_made=False,
        record_digest="c" * 64,
    )

    async def fake_baseline(**_kwargs):
        return baseline_record

    monkeypatch.setattr(
        fair_benchmark_module,
        "run_langgraph_stormshift_baseline",
        fake_baseline,
    )
    observation = asyncio.run(fair_benchmark_module._run_langgraph("normalized"))

    assert observation.framework == "langgraph"
    assert observation.checkpoint_verified is True
    assert observation.selected_profiles[0].tokens == 5
    assert observation.declared_resource_fit is None
    assert observation.source_evidence_digest == "c" * 64


def test_low_level_protocol_helpers_fail_closed(monkeypatch, tmp_path) -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    changed = _redigest_contract(contract, graph_digest="0" * 64)
    with pytest.raises(FairBenchmarkInvariantError, match="inputs changed"):
        fair_benchmark_module._assert_current_workload(changed)

    with pytest.raises(FairBenchmarkInvariantError, match="no qualified"):
        fair_benchmark_module._highest_quality_profile(
            SimpleNamespace(task_id="empty", profiles=(), min_quality=1.0)
        )
    assert fair_benchmark_module._balanced_order((), contract.measured_seeds, 0) == ()
    with pytest.raises(ValueError, match="percentile"):
        fair_benchmark_module._percentile((), 0.5)
    with pytest.raises(ValueError, match="bootstrap"):
        fair_benchmark_module._bootstrap_interval(
            (), lambda values: float(len(values)), samples=200, label="empty"
        )
    assert fair_benchmark_module._duration_summary((), contract, "empty") is None
    with pytest.raises(FairBenchmarkInvariantError, match="measured records"):
        fair_benchmark_module._system_summary("empty", (), contract)
    comparison = fair_benchmark_module._paired_comparison("missing", (), contract)
    assert comparison.eligible_pairs == 0
    assert comparison.excluded_pairs == len(contract.measured_seeds)

    monkeypatch.setattr(
        fair_benchmark_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git unavailable")),
    )
    assert fair_benchmark_module._git_metadata(tmp_path) == (None, None)


def test_summary_rejects_incomplete_or_rebound_designs() -> None:
    evidence = _evidence()
    statuses = list(evidence.report.system_statuses)
    records = list(evidence.records)
    finite_status_index = next(
        index for index, status in enumerate(statuses) if status.system_id == FINITE_SYSTEM_ID
    )
    pageagent_status_index = next(
        index for index, status in enumerate(statuses) if status.system_id == PAGEAGENT_SYSTEM_ID
    )

    bad_environment = replace(evidence.environment, environment_digest="0" * 64)
    with pytest.raises(FairBenchmarkInvariantError, match="invalid environment"):
        summarize_fair_benchmark(
            evidence.contract, bad_environment, statuses, records
        )
    with pytest.raises(FairBenchmarkInvariantError, match="status set"):
        summarize_fair_benchmark(
            evidence.contract, evidence.environment, statuses[:-1], records
        )
    bad_statuses = statuses.copy()
    bad_statuses[0] = replace(bad_statuses[0], status_digest="0" * 64)
    with pytest.raises(FairBenchmarkInvariantError, match="status digest"):
        summarize_fair_benchmark(
            evidence.contract, evidence.environment, bad_statuses, records
        )

    for field, message in (
        ("contract_digest", "contract binding"),
        ("environment_digest", "environment binding"),
        ("workload_id", "workload binding"),
    ):
        rebound = _redigest_record(records[0], **{field: "0" * 64})
        with pytest.raises(FairBenchmarkInvariantError, match=message):
            summarize_fair_benchmark(
                evidence.contract,
                evidence.environment,
                statuses,
                (rebound, *records[1:]),
            )

    metric_ineligible = statuses.copy()
    metric_ineligible[finite_status_index] = _redigest_status(
        statuses[finite_status_index], metrics_eligible=False
    )
    with pytest.raises(FairBenchmarkInvariantError, match="metric-ineligible"):
        summarize_fair_benchmark(
            evidence.contract, evidence.environment, metric_ineligible, records
        )

    finite_measured = next(
        record
        for record in records
        if record.system_id == FINITE_SYSTEM_ID and record.phase == "measured"
    )
    with pytest.raises(FairBenchmarkInvariantError, match="complete measured"):
        summarize_fair_benchmark(
            evidence.contract,
            evidence.environment,
            statuses,
            [record for record in records if record is not finite_measured],
        )
    finite_warmup = next(
        record
        for record in records
        if record.system_id == FINITE_SYSTEM_ID and record.phase == "warmup"
    )
    with pytest.raises(FairBenchmarkInvariantError, match="registered warmups"):
        summarize_fair_benchmark(
            evidence.contract,
            evidence.environment,
            statuses,
            [record for record in records if record is not finite_warmup],
        )

    duplicate_seed_records = records.copy()
    finite_measured_records = [
        (index, record)
        for index, record in enumerate(duplicate_seed_records)
        if record.system_id == FINITE_SYSTEM_ID and record.phase == "measured"
    ]
    first_index, first_record = finite_measured_records[0]
    duplicate_seed_records[first_index] = _redigest_record(
        first_record, seed=finite_measured_records[1][1].seed
    )
    with pytest.raises(FairBenchmarkInvariantError, match="paired seeds"):
        summarize_fair_benchmark(
            evidence.contract,
            evidence.environment,
            statuses,
            duplicate_seed_records,
        )

    wrong_counts = statuses.copy()
    wrong_counts[finite_status_index] = _redigest_status(
        statuses[finite_status_index], measured_records=99
    )
    with pytest.raises(FairBenchmarkInvariantError, match="record counts"):
        summarize_fair_benchmark(
            evidence.contract, evidence.environment, wrong_counts, records
        )

    fake_pageagent = statuses.copy()
    fake_pageagent[pageagent_status_index] = _redigest_status(
        statuses[pageagent_status_index], metrics_eligible=True
    )
    with pytest.raises(FairBenchmarkInvariantError, match="unexecuted systems"):
        summarize_fair_benchmark(
            evidence.contract, evidence.environment, fake_pageagent, records
        )
    unknown_status = statuses.copy()
    unknown_status[pageagent_status_index] = _redigest_status(
        statuses[pageagent_status_index], execution_status="unknown"
    )
    with pytest.raises(FairBenchmarkInvariantError, match="unknown system"):
        summarize_fair_benchmark(
            evidence.contract, evidence.environment, unknown_status, records
        )


def test_runner_can_write_verified_evidence_directly(tmp_path) -> None:
    contract = build_fair_benchmark_contract(bootstrap_samples=200)
    evidence = asyncio.run(run_fair_benchmark(contract, output_directory=tmp_path))
    assert evidence.verify_digest()
    assert (tmp_path / "manifest.json").is_file()
