from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_physics.production_survival import (
    BENCHMARK_ID,
    MIN_TRIALS,
    SCENARIO_IDS,
    SurvivalEvidence,
    SurvivalInvariantError,
    build_survival_contract,
    build_survival_report,
    run_production_survival,
    runtime_identity,
    verify_survival_evidence_directory,
)
from agent_physics.cli import main as cli_main
from agent_physics.serialization import content_digest


def _evidence(tmp_path: Path) -> SurvivalEvidence:
    contract = build_survival_contract(trials_per_scenario=MIN_TRIALS, seed_base=700)
    return run_production_survival(
        contract,
        working_directory=tmp_path / "work",
        source_revision="test-revision",
        source_state="test-dirty",
    )


def test_production_survival_executes_every_preregistered_scenario(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)

    assert evidence.verify()
    assert evidence.contract.benchmark_id == BENCHMARK_ID
    assert evidence.report.total_trials == len(SCENARIO_IDS) * MIN_TRIALS
    assert evidence.report.total_passes == evidence.report.total_trials
    assert evidence.report.all_trials_observed_passed is True
    assert evidence.report.external_provider_calls == 0
    assert evidence.report.duplicate_effect_applications == 0
    assert tuple(summary.scenario_id for summary in evidence.report.scenario_summaries) == (
        SCENARIO_IDS
    )
    for summary in evidence.report.scenario_summaries:
        assert summary.trials == MIN_TRIALS
        assert summary.passes == MIN_TRIALS
        assert summary.per_trial_pass_rate == 1.0
        assert summary.pass_pow_k_estimate == 1.0
        assert summary.all_k_observed is True
        assert summary.p50_duration_ns >= 0
        assert summary.p95_duration_ns >= summary.p50_duration_ns
        assert summary.p99_duration_ns >= summary.p95_duration_ns

    overhead = evidence.report.scenario_summaries[-1]
    assert overhead.p50_direct_duration_ns is not None
    assert overhead.p50_orchestration_overhead_ns is not None
    assert overhead.p50_recovery_duration_ns is None
    effect_summaries = evidence.report.scenario_summaries[1:5]
    assert all(summary.physical_effect_applications == MIN_TRIALS for summary in effect_summaries)


def test_survival_evidence_writes_digest_bound_raw_files(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    output = tmp_path / "evidence"

    manifest = evidence.write(output)

    assert manifest["schema_version"] == "finite-production-survival-files/v1"
    assert manifest["contract_digest"] == evidence.contract.contract_digest
    assert manifest["report_digest"] == evidence.report.report_digest
    files = dict(manifest["files"])
    assert set(files) == {"contract.json", "records.jsonl", "report.json"}
    for name, expected_digest in files.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected_digest
    records = (output / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == evidence.report.total_trials
    assert all(json.loads(line)["record_digest"] for line in records)
    loaded, loaded_manifest = verify_survival_evidence_directory(output)
    assert loaded == evidence
    assert loaded_manifest["manifest_digest"] == manifest["manifest_digest"]
    assert loaded_manifest["files"] == [list(item) for item in manifest["files"]]


def test_survival_contract_and_report_fail_closed_on_tampering(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    invalid_contract = replace(evidence.contract, trials_per_scenario=MIN_TRIALS + 1)
    invalid_record = replace(evidence.records[0], passed=False)
    invalid_report = replace(evidence.report, total_passes=0)

    assert invalid_contract.verify() is False
    assert invalid_record.verify(evidence.contract) is False
    assert invalid_report.verify(evidence.contract, evidence.records) is False
    with pytest.raises(SurvivalInvariantError, match="invalid survival records"):
        build_survival_report(
            evidence.contract,
            (invalid_record, *evidence.records[1:]),
            source_revision="revision",
            source_state="state",
        )
    with pytest.raises(SurvivalInvariantError, match="missing, duplicated, or out"):
        build_survival_report(
            evidence.contract,
            tuple(reversed(evidence.records)),
            source_revision="revision",
            source_state="state",
        )
    with pytest.raises(SurvivalInvariantError, match="invalid survival evidence"):
        SurvivalEvidence(invalid_contract, evidence.records, evidence.report).write(
            tmp_path / "invalid"
        )


def test_survival_offline_verifier_rejects_a_modified_file(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    output = tmp_path / "tampered"
    evidence.write(output)
    report_path = output / "report.json"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace(
            '"total_passes": 18',
            '"total_passes": 0',
        ),
        encoding="utf-8",
    )

    with pytest.raises(SurvivalInvariantError, match="failed SHA-256"):
        verify_survival_evidence_directory(output)


def test_survival_offline_verifier_rejects_overflow_json_number(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    output = tmp_path / "nonfinite"
    evidence.write(output)
    report_path = output / "report.json"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace(
            '"per_trial_pass_rate": 1.0',
            '"per_trial_pass_rate": 1e400',
            1,
        ),
        encoding="utf-8",
    )
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        [
            name,
            hashlib.sha256((output / name).read_bytes()).hexdigest()
            if name == "report.json"
            else digest,
        ]
        for name, digest in manifest["files"]
    ]
    unsigned_manifest = dict(manifest)
    unsigned_manifest.pop("manifest_digest")
    manifest["manifest_digest"] = content_digest(unsigned_manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SurvivalInvariantError, match="invalid JSON"):
        verify_survival_evidence_directory(output)

    nonfinite_record = replace(
        evidence.records[0],
        observations=(("nonfinite", float("nan")),),
    )
    assert nonfinite_record.verify(evidence.contract) is False


@pytest.mark.parametrize(
    ("trials", "seed", "message"),
    (
        (MIN_TRIALS - 1, 0, "trials_per_scenario"),
        (MIN_TRIALS, -1, "seed_base"),
        (True, 0, "trials_per_scenario"),
    ),
)
def test_survival_contract_rejects_invalid_bounds(
    trials: int,
    seed: int,
    message: str,
) -> None:
    with pytest.raises(SurvivalInvariantError, match=message):
        build_survival_contract(trials_per_scenario=trials, seed_base=seed)


def test_survival_runner_rejects_unbound_source_or_contract(tmp_path: Path) -> None:
    contract = build_survival_contract(trials_per_scenario=MIN_TRIALS)
    with pytest.raises(SurvivalInvariantError, match="source revision"):
        run_production_survival(
            contract,
            working_directory=tmp_path / "empty-source",
            source_revision=" ",
            source_state="clean",
        )
    with pytest.raises(SurvivalInvariantError, match="contract failed"):
        run_production_survival(
            replace(contract, timer="not-the-registered-timer"),
            working_directory=tmp_path / "invalid-contract",
            source_revision="revision",
            source_state="clean",
        )


def test_runtime_identity_is_non_secret_canonical_and_child_process_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called() -> str:
        raise AssertionError("runtime identity must not invoke platform.platform()")

    monkeypatch.setattr(
        "agent_physics.production_survival.platform.platform",
        fail_if_called,
    )
    identity = runtime_identity()
    assert identity == tuple(sorted(identity))
    assert {"executable", "machine", "platform", "python", "python_implementation"} == {
        name for name, _value in identity
    }
    assert all(value for _name, value in identity)


def test_production_survival_cli_preserves_verified_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "cli-evidence"

    exit_code = cli_main(
        [
            "production-survival",
            "--output",
            str(output),
            "--trials",
            str(MIN_TRIALS),
            "--seed-base",
            "900",
            "--revision",
            "cli-test-unverified",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["verified"] is True
    assert payload["all_trials_observed_passed"] is True
    assert payload["total_trials"] == len(SCENARIO_IDS) * MIN_TRIALS
    assert payload["external_provider_calls"] == 0
    assert payload["duplicate_effect_applications"] == 0
    assert payload["source_revision"] == "cli-test-unverified"
    assert payload["source_state"] == "caller-supplied-unverified"
    assert (output / "manifest.json").is_file()

    verify_exit = cli_main(
        [
            "production-survival",
            "--verify-only",
            str(output),
        ]
    )
    verified = json.loads(capsys.readouterr().out)
    assert verify_exit == 0
    assert verified["verified"] is True
    assert verified["report_digest"] == payload["report_digest"]
