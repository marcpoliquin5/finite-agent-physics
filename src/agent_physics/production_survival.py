"""Preregistered local production-survival evidence for FINITE.

The benchmark in this module exercises deterministic local fixtures. It does not
call IBM Bob, watsonx, a live model, a remote worker, or an external effect target.
Wall-clock measurements describe this machine and this process only.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Callable, Final, Iterable, TypeAlias, cast

from .adaptive_runtime import (
    AdaptiveRuntime,
    AdaptiveStatus,
    AdaptiveTaskContext,
    AdaptiveWorker,
    AdaptiveWorkerResult,
    adaptive_recovery_drill_envelope,
    adaptive_recovery_drill_graph,
    run_adaptive_recovery_drill,
)
from .contracts import EffectClass
from .effects import (
    AmbiguousCommit,
    ApprovalAuthority,
    ApprovalRequired,
    EffectState,
    SQLiteEffectBroker,
    SimulatedEffectAdapter,
    SimulatedProcessCrash,
    StaleFence,
)
from .run_store import SQLiteRunStore, Usage
from .serialization import canonical_json, content_digest


CONTRACT_SCHEMA_VERSION = "finite-production-survival-contract/v1"
RECORD_SCHEMA_VERSION = "finite-production-survival-record/v1"
REPORT_SCHEMA_VERSION = "finite-production-survival-report/v1"
MANIFEST_SCHEMA_VERSION = "finite-production-survival-files/v1"
BENCHMARK_ID = "finite-production-survival-v1"
MEASUREMENT_KIND = "local-deterministic-fault-injection"
TIMER = "time.perf_counter_ns"
MIN_TRIALS = 3
DEFAULT_TRIALS = 10
APPROVAL_SECRET: Final[bytes] = b"finite-survival-approval-secret-32-bytes"

JsonScalar: TypeAlias = str | int | float | bool | None

SCENARIO_IDS: Final[tuple[str, ...]] = (
    "adaptive-compound-recovery",
    "hard-effect-crash",
    "ambiguous-effect-ack",
    "stale-effect-fence",
    "delayed-human-approval",
    "local-orchestration-overhead",
)

CLAIM_BOUNDARIES: Final[tuple[str, ...]] = (
    "All workers and effect targets are deterministic local fixtures.",
    "No IBM Bob, model, watsonx, network, remote-worker, sandbox, or external-effect call occurs.",
    "Latency and overhead measurements describe only the executing machine and process.",
    "The pass^k value is the descriptive plug-in estimate p_hat^k; independence is not claimed.",
    "SQLite demonstrates single-database durability, not distributed consensus or high availability.",
)


class SurvivalInvariantError(ValueError):
    """A production-survival contract, record, or report is invalid."""


@dataclass(frozen=True, slots=True)
class SurvivalContract:
    schema_version: str
    benchmark_id: str
    measurement_kind: str
    timer: str
    trials_per_scenario: int
    seed_base: int
    scenario_ids: tuple[str, ...]
    seed_derivation: str
    reliability_definition: str
    timed_scope: str
    claim_boundaries: tuple[str, ...]
    contract_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], asdict(self))
        payload.pop("contract_digest")
        return payload

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))

    def verify(self) -> bool:
        return (
            self.schema_version == CONTRACT_SCHEMA_VERSION
            and self.benchmark_id == BENCHMARK_ID
            and self.measurement_kind == MEASUREMENT_KIND
            and self.timer == TIMER
            and type(self.trials_per_scenario) is int
            and self.trials_per_scenario >= MIN_TRIALS
            and type(self.seed_base) is int
            and self.seed_base >= 0
            and self.scenario_ids == SCENARIO_IDS
            and self.claim_boundaries == CLAIM_BOUNDARIES
            and self.contract_digest == content_digest(self.unsigned_payload())
        )


@dataclass(frozen=True, slots=True)
class SurvivalTrialRecord:
    schema_version: str
    benchmark_id: str
    contract_digest: str
    scenario_id: str
    trial_index: int
    seed: int
    measurement_kind: str
    passed: bool
    duration_ns: int
    recovery_duration_ns: int | None
    direct_duration_ns: int | None
    orchestration_overhead_ns: int | None
    external_provider_calls: int
    physical_effect_applications: int
    duplicate_effect_applications: int
    injected_faults: tuple[str, ...]
    assertions: tuple[str, ...]
    observations: tuple[tuple[str, JsonScalar], ...]
    record_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], asdict(self))
        payload.pop("record_digest")
        return payload

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))

    def verify(self, contract: SurvivalContract) -> bool:
        observation_names = tuple(
            item[0]
            for item in self.observations
            if type(item) is tuple and len(item) == 2 and type(item[0]) is str
        )
        return (
            contract.verify()
            and self.schema_version == RECORD_SCHEMA_VERSION
            and self.benchmark_id == contract.benchmark_id
            and self.contract_digest == contract.contract_digest
            and self.scenario_id in contract.scenario_ids
            and 0 <= self.trial_index < contract.trials_per_scenario
            and self.seed == contract.seed_base + self.trial_index
            and self.measurement_kind == contract.measurement_kind
            and type(self.passed) is bool
            and type(self.duration_ns) is int
            and self.duration_ns >= 0
            and _optional_nonnegative_int(self.recovery_duration_ns)
            and _optional_nonnegative_int(self.direct_duration_ns)
            and _optional_nonnegative_int(self.orchestration_overhead_ns)
            and type(self.external_provider_calls) is int
            and self.external_provider_calls >= 0
            and type(self.physical_effect_applications) is int
            and self.physical_effect_applications >= 0
            and type(self.duplicate_effect_applications) is int
            and self.duplicate_effect_applications >= 0
            and type(self.injected_faults) is tuple
            and all(type(item) is str and item for item in self.injected_faults)
            and type(self.assertions) is tuple
            and all(type(item) is str and item for item in self.assertions)
            and type(self.observations) is tuple
            and len(observation_names) == len(self.observations)
            and len(set(observation_names)) == len(observation_names)
            and all(
                _is_json_scalar(item[1])
                for item in self.observations
            )
            and tuple(sorted(self.observations, key=lambda item: item[0])) == self.observations
            and self.record_digest == content_digest(self.unsigned_payload())
        )


@dataclass(frozen=True, slots=True)
class SurvivalScenarioSummary:
    scenario_id: str
    trials: int
    passes: int
    per_trial_pass_rate: float
    pass_pow_k_estimate: float
    all_k_observed: bool
    p50_duration_ns: int
    p95_duration_ns: int
    p99_duration_ns: int
    p50_recovery_duration_ns: int | None
    p95_recovery_duration_ns: int | None
    p99_recovery_duration_ns: int | None
    p50_direct_duration_ns: int | None
    p50_orchestration_overhead_ns: int | None
    external_provider_calls: int
    physical_effect_applications: int
    duplicate_effect_applications: int


@dataclass(frozen=True, slots=True)
class SurvivalReport:
    schema_version: str
    benchmark_id: str
    contract_digest: str
    measurement_kind: str
    source_revision: str
    source_state: str
    environment: tuple[tuple[str, str], ...]
    total_trials: int
    total_passes: int
    all_trials_observed_passed: bool
    scenario_summaries: tuple[SurvivalScenarioSummary, ...]
    external_provider_calls: int
    duplicate_effect_applications: int
    records_digest: str
    claim_boundaries: tuple[str, ...]
    report_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], asdict(self))
        payload.pop("report_digest")
        return payload

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))

    def verify(
        self,
        contract: SurvivalContract,
        records: Iterable[SurvivalTrialRecord],
    ) -> bool:
        materialized = tuple(records)
        expected = build_survival_report(
            contract,
            materialized,
            source_revision=self.source_revision,
            source_state=self.source_state,
            environment=self.environment,
        )
        return self == expected and all(record.verify(contract) for record in materialized)


@dataclass(frozen=True, slots=True)
class SurvivalEvidence:
    contract: SurvivalContract
    records: tuple[SurvivalTrialRecord, ...]
    report: SurvivalReport

    def verify(self) -> bool:
        return self.contract.verify() and self.report.verify(self.contract, self.records)

    def write(self, output_directory: str | Path) -> dict[str, object]:
        if not self.verify():
            raise SurvivalInvariantError("refusing to write invalid survival evidence")
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        contract_path = output / "contract.json"
        records_path = output / "records.jsonl"
        report_path = output / "report.json"
        contract_path.write_text(_pretty_json(self.contract.as_dict()), encoding="utf-8")
        with records_path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in self.records:
                stream.write(canonical_json(record.as_dict()) + "\n")
        report_path.write_text(_pretty_json(self.report.as_dict()), encoding="utf-8")
        files = tuple(
            (path.name, _sha256_file(path))
            for path in (contract_path, records_path, report_path)
        )
        manifest_unsigned = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "benchmark_id": BENCHMARK_ID,
            "contract_digest": self.contract.contract_digest,
            "report_digest": self.report.report_digest,
            "files": files,
        }
        manifest = {
            **manifest_unsigned,
            "manifest_digest": content_digest(manifest_unsigned),
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(_pretty_json(manifest), encoding="utf-8")
        return manifest


def build_survival_contract(
    *,
    trials_per_scenario: int = DEFAULT_TRIALS,
    seed_base: int = 5_000,
) -> SurvivalContract:
    if type(trials_per_scenario) is not int or trials_per_scenario < MIN_TRIALS:
        raise SurvivalInvariantError(
            f"trials_per_scenario must be an integer >= {MIN_TRIALS}"
        )
    if type(seed_base) is not int or seed_base < 0:
        raise SurvivalInvariantError("seed_base must be a non-negative integer")
    unsigned = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "measurement_kind": MEASUREMENT_KIND,
        "timer": TIMER,
        "trials_per_scenario": trials_per_scenario,
        "seed_base": seed_base,
        "scenario_ids": SCENARIO_IDS,
        "seed_derivation": "seed = seed_base + zero_based_trial_index",
        "reliability_definition": (
            "per_trial_pass_rate = passes/k; pass_pow_k_estimate = "
            "per_trial_pass_rate**k; all_k_observed records whether every trial passed"
        ),
        "timed_scope": (
            "Each scenario times its full local operation; recovery duration begins immediately "
            "after the injected failure is observed and ends at durable reconciliation."
        ),
        "claim_boundaries": CLAIM_BOUNDARIES,
    }
    return SurvivalContract(
        **unsigned,
        contract_digest=content_digest(unsigned),
    )


def run_production_survival(
    contract: SurvivalContract,
    *,
    working_directory: str | Path,
    source_revision: str,
    source_state: str,
) -> SurvivalEvidence:
    if not contract.verify():
        raise SurvivalInvariantError("survival contract failed verification")
    if not source_revision.strip() or not source_state.strip():
        raise SurvivalInvariantError("source revision and state are required")
    root = Path(working_directory)
    root.mkdir(parents=True, exist_ok=True)
    scenario_runners: tuple[tuple[str, ScenarioRunner], ...] = (
        ("adaptive-compound-recovery", _adaptive_compound_recovery),
        ("hard-effect-crash", _hard_effect_crash),
        ("ambiguous-effect-ack", _ambiguous_effect_ack),
        ("stale-effect-fence", _stale_effect_fence),
        ("delayed-human-approval", _delayed_human_approval),
        ("local-orchestration-overhead", _local_orchestration_overhead),
    )
    if tuple(name for name, _runner in scenario_runners) != contract.scenario_ids:
        raise SurvivalInvariantError("scenario runner set diverged from the contract")
    records: list[SurvivalTrialRecord] = []
    for scenario_id, runner in scenario_runners:
        scenario_root = root / scenario_id
        scenario_root.mkdir(exist_ok=True)
        for trial_index in range(contract.trials_per_scenario):
            seed = contract.seed_base + trial_index
            record = runner(contract, scenario_root, trial_index, seed)
            if not record.verify(contract):
                raise SurvivalInvariantError(
                    f"scenario {scenario_id!r} emitted an invalid trial record"
                )
            records.append(record)
    materialized = tuple(records)
    report = build_survival_report(
        contract,
        materialized,
        source_revision=source_revision,
        source_state=source_state,
    )
    evidence = SurvivalEvidence(contract, materialized, report)
    if not evidence.verify():
        raise SurvivalInvariantError("generated survival evidence failed verification")
    return evidence


def verify_survival_evidence_directory(
    directory: str | Path,
) -> tuple[SurvivalEvidence, dict[str, object]]:
    """Load and independently verify a previously written evidence directory."""

    root = Path(directory)
    manifest_path = root / "manifest.json"
    try:
        manifest_value = _strict_json_loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, SurvivalInvariantError) as error:
        raise SurvivalInvariantError("survival manifest is missing or invalid JSON") from error
    manifest = _exact_mapping(
        manifest_value,
        {
            "schema_version",
            "benchmark_id",
            "contract_digest",
            "report_digest",
            "files",
            "manifest_digest",
        },
        "survival manifest",
    )
    unsigned_manifest = dict(manifest)
    claimed_manifest_digest = unsigned_manifest.pop("manifest_digest")
    if (
        manifest["schema_version"] != MANIFEST_SCHEMA_VERSION
        or manifest["benchmark_id"] != BENCHMARK_ID
        or claimed_manifest_digest != content_digest(unsigned_manifest)
    ):
        raise SurvivalInvariantError("survival manifest identity verification failed")
    files_value = manifest["files"]
    if type(files_value) is not list:
        raise SurvivalInvariantError("survival manifest files must be a list")
    files: list[tuple[str, str]] = []
    for item in files_value:
        if (
            type(item) is not list
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
        ):
            raise SurvivalInvariantError("survival manifest contains an invalid file identity")
        files.append((item[0], item[1]))
    if tuple(name for name, _digest in files) != (
        "contract.json",
        "records.jsonl",
        "report.json",
    ):
        raise SurvivalInvariantError("survival manifest file set or order changed")
    for name, expected_digest in files:
        path = root / name
        if not path.is_file() or _sha256_file(path) != expected_digest:
            raise SurvivalInvariantError(f"survival evidence file {name!r} failed SHA-256")

    try:
        contract_value = _strict_json_loads(
            (root / "contract.json").read_text(encoding="utf-8")
        )
        report_value = _strict_json_loads(
            (root / "report.json").read_text(encoding="utf-8")
        )
        record_values = tuple(
            _strict_json_loads(line)
            for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        )
    except (OSError, json.JSONDecodeError, SurvivalInvariantError) as error:
        raise SurvivalInvariantError("survival evidence contains invalid JSON") from error
    contract = _contract_from_dict(contract_value)
    records = tuple(_record_from_dict(value) for value in record_values)
    report = _report_from_dict(report_value)
    evidence = SurvivalEvidence(contract, records, report)
    if (
        not evidence.verify()
        or manifest["contract_digest"] != contract.contract_digest
        or manifest["report_digest"] != report.report_digest
    ):
        raise SurvivalInvariantError("survival evidence semantic verification failed")
    return evidence, manifest


def build_survival_report(
    contract: SurvivalContract,
    records: Iterable[SurvivalTrialRecord],
    *,
    source_revision: str,
    source_state: str,
    environment: tuple[tuple[str, str], ...] | None = None,
) -> SurvivalReport:
    materialized = tuple(records)
    if not contract.verify() or not all(record.verify(contract) for record in materialized):
        raise SurvivalInvariantError("cannot summarize invalid survival records")
    expected_count = len(contract.scenario_ids) * contract.trials_per_scenario
    identities = tuple((record.scenario_id, record.trial_index) for record in materialized)
    expected_identities = tuple(
        (scenario_id, trial_index)
        for scenario_id in contract.scenario_ids
        for trial_index in range(contract.trials_per_scenario)
    )
    if len(materialized) != expected_count or identities != expected_identities:
        raise SurvivalInvariantError("records are missing, duplicated, or out of contract order")
    if not source_revision.strip() or not source_state.strip():
        raise SurvivalInvariantError("source revision and state are required")
    summaries = tuple(
        _summarize_scenario(
            scenario_id,
            tuple(record for record in materialized if record.scenario_id == scenario_id),
        )
        for scenario_id in contract.scenario_ids
    )
    records_digest = content_digest(tuple(record.as_dict() for record in materialized))
    runtime_environment = environment or runtime_identity()
    unsigned = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark_id": contract.benchmark_id,
        "contract_digest": contract.contract_digest,
        "measurement_kind": contract.measurement_kind,
        "source_revision": source_revision,
        "source_state": source_state,
        "environment": runtime_environment,
        "total_trials": len(materialized),
        "total_passes": sum(record.passed for record in materialized),
        "all_trials_observed_passed": all(record.passed for record in materialized),
        "scenario_summaries": summaries,
        "external_provider_calls": sum(
            record.external_provider_calls for record in materialized
        ),
        "duplicate_effect_applications": sum(
            record.duplicate_effect_applications for record in materialized
        ),
        "records_digest": records_digest,
        "claim_boundaries": contract.claim_boundaries,
    }
    return SurvivalReport(**unsigned, report_digest=content_digest(unsigned))


ScenarioRunner: TypeAlias = Callable[
    [SurvivalContract, Path, int, int],
    SurvivalTrialRecord,
]


def _adaptive_compound_recovery(
    contract: SurvivalContract,
    root: Path,
    trial_index: int,
    seed: int,
) -> SurvivalTrialRecord:
    started = perf_counter_ns()
    result = run_adaptive_recovery_drill(root / f"trial-{trial_index}.db")
    duration = perf_counter_ns() - started
    passed = (
        result.final_status is AdaptiveStatus.COMPLETED
        and result.replay_passed
        and result.control_digest == result.replay_control_digest
        and result.first_process_worker_calls == ("intake", "assessment")
        and result.restart_worker_calls == ("mandatory_alert",)
        and result.unknown_task_ids == ("optional_enrichment",)
        and result.external_provider_calls == 0
    )
    return _trial_record(
        contract,
        scenario_id="adaptive-compound-recovery",
        trial_index=trial_index,
        seed=seed,
        passed=passed,
        duration_ns=duration,
        recovery_duration_ns=duration,
        injected_faults=(
            "provider-429",
            "provider-capacity-zero",
            "budget-cut",
            "coordinator-crash-after-dispatch",
        ),
        assertions=(
            "mandatory work completes after restart",
            "crash-ambiguous work is not recalled",
            "completed work resumes without recall",
            "control ledger replays call-free to the same digest",
        ),
        observations=(
            ("controller_record_count", result.controller_record_count),
            ("resumed_task_count", len(result.resumed_task_ids)),
            ("unknown_task_count", len(result.unknown_task_ids)),
        ),
    )


def _hard_effect_crash(
    contract: SurvivalContract,
    root: Path,
    trial_index: int,
    seed: int,
) -> SurvivalTrialRecord:
    database = root / f"trial-{trial_index}.db"
    broker_a = SQLiteEffectBroker(database, broker_id="broker-a")
    approved = _approved_effect(broker_a, key=f"hard-{trial_index}")
    adapter = SimulatedEffectAdapter()
    adapter.arm_process_crash_after_apply(approved.idempotency_key)
    started = perf_counter_ns()
    crashed = False
    try:
        broker_a.commit(approved.intent_id, approved.fencing_token, adapter)
    except SimulatedProcessCrash:
        crashed = True
    recovery_started = perf_counter_ns()
    broker_b = SQLiteEffectBroker(database, broker_id="broker-b")
    recovered = broker_b.acquire_fence(approved.intent_id)
    committed = broker_b.commit(recovered.intent_id, recovered.fencing_token, adapter)
    replayed = broker_b.commit(recovered.intent_id, recovered.fencing_token, adapter)
    ended = perf_counter_ns()
    applications = adapter.physical_apply_count(approved.idempotency_key)
    committed_events = tuple(
        event for event in broker_b.pending_outbox() if event.event_type == "effect.committed"
    )
    passed = (
        crashed
        and committed.state is EffectState.COMMITTED
        and replayed == committed
        and applications == 1
        and len(committed_events) == 1
    )
    return _trial_record(
        contract,
        scenario_id="hard-effect-crash",
        trial_index=trial_index,
        seed=seed,
        passed=passed,
        duration_ns=ended - started,
        recovery_duration_ns=ended - recovery_started,
        physical_effect_applications=applications,
        duplicate_effect_applications=max(0, applications - 1),
        injected_faults=("process-crash-after-target-apply-before-checkpoint",),
        assertions=(
            "restart acquires a newer fence",
            "target-side idempotency prevents duplicate physical application",
            "exactly one committed outbox event is durable",
        ),
        observations=(("committed_event_count", len(committed_events)),),
    )


def _ambiguous_effect_ack(
    contract: SurvivalContract,
    root: Path,
    trial_index: int,
    seed: int,
) -> SurvivalTrialRecord:
    broker = SQLiteEffectBroker(root / f"trial-{trial_index}.db", broker_id="broker-a")
    approved = _approved_effect(broker, key=f"ambiguous-{trial_index}")
    adapter = SimulatedEffectAdapter()
    adapter.arm_ambiguous_after_apply(approved.idempotency_key)
    started = perf_counter_ns()
    ambiguous = False
    try:
        broker.commit(approved.intent_id, approved.fencing_token, adapter)
    except AmbiguousCommit:
        ambiguous = True
    state_after_fault = broker.get(approved.intent_id).state
    recovery_started = perf_counter_ns()
    committed = broker.commit(approved.intent_id, approved.fencing_token, adapter)
    ended = perf_counter_ns()
    applications = adapter.physical_apply_count(approved.idempotency_key)
    passed = (
        ambiguous
        and state_after_fault is EffectState.AMBIGUOUS
        and committed.state is EffectState.COMMITTED
        and applications == 1
    )
    return _trial_record(
        contract,
        scenario_id="ambiguous-effect-ack",
        trial_index=trial_index,
        seed=seed,
        passed=passed,
        duration_ns=ended - started,
        recovery_duration_ns=ended - recovery_started,
        physical_effect_applications=applications,
        duplicate_effect_applications=max(0, applications - 1),
        injected_faults=("target-applied-without-acknowledgement",),
        assertions=(
            "ambiguous state is durable",
            "status reconciliation commits without a second physical application",
        ),
        observations=(("ambiguous_state_recorded", state_after_fault is EffectState.AMBIGUOUS),),
    )


def _stale_effect_fence(
    contract: SurvivalContract,
    root: Path,
    trial_index: int,
    seed: int,
) -> SurvivalTrialRecord:
    database = root / f"trial-{trial_index}.db"
    broker_a = SQLiteEffectBroker(database, broker_id="broker-a")
    approved = _approved_effect(broker_a, key=f"stale-{trial_index}")
    stale_token = approved.fencing_token
    broker_b = SQLiteEffectBroker(database, broker_id="broker-b")
    claimed = broker_b.acquire_fence(approved.intent_id)
    adapter = SimulatedEffectAdapter()
    started = perf_counter_ns()
    stale_rejected = False
    try:
        broker_a.commit(approved.intent_id, stale_token, adapter)
    except StaleFence:
        stale_rejected = True
    recovery_started = perf_counter_ns()
    committed = broker_b.commit(claimed.intent_id, claimed.fencing_token, adapter)
    ended = perf_counter_ns()
    applications = adapter.physical_apply_count(approved.idempotency_key)
    passed = (
        stale_rejected
        and claimed.fence_version > stale_token.version
        and committed.state is EffectState.COMMITTED
        and applications == 1
    )
    return _trial_record(
        contract,
        scenario_id="stale-effect-fence",
        trial_index=trial_index,
        seed=seed,
        passed=passed,
        duration_ns=ended - started,
        recovery_duration_ns=ended - recovery_started,
        physical_effect_applications=applications,
        duplicate_effect_applications=max(0, applications - 1),
        injected_faults=("stale-coordinator-fence",),
        assertions=(
            "stale owner is rejected before target application",
            "new owner commits once with a monotonic fence",
        ),
        observations=(("fence_version_delta", claimed.fence_version - stale_token.version),),
    )


def _delayed_human_approval(
    contract: SurvivalContract,
    root: Path,
    trial_index: int,
    seed: int,
) -> SurvivalTrialRecord:
    database = root / f"trial-{trial_index}.db"
    clock = [1_000]
    trusted = {"survival-approver": APPROVAL_SECRET}
    broker_a = SQLiteEffectBroker(
        database,
        broker_id="broker-a",
        trusted_approval_keys=trusted,
        clock_ms=lambda: clock[0],
    )
    proposed = broker_a.propose(
        run_id=f"approval-run-{trial_index}",
        action="publish_notice",
        resource="fixture/notices",
        effect_class=EffectClass.IRREVERSIBLE_WRITE,
        idempotency_key=f"approval-{trial_index}",
        payload={"message": "fixture only"},
    )
    prepared = broker_a.prepare(proposed.intent_id)
    started = perf_counter_ns()
    approval_blocked = False
    try:
        broker_a.approve(prepared.intent_id, prepared.fencing_token)
    except ApprovalRequired:
        approval_blocked = True
    adapter = SimulatedEffectAdapter()
    applications_before_approval = adapter.physical_apply_count(prepared.idempotency_key)
    clock[0] += 86_400_000 * 365
    broker_b = SQLiteEffectBroker(
        database,
        broker_id="broker-b",
        trusted_approval_keys=trusted,
        clock_ms=lambda: clock[0],
    )
    resumed = broker_b.acquire_fence(prepared.intent_id)
    authority = ApprovalAuthority("survival-approver", APPROVAL_SECRET)
    grant = authority.issue(
        resumed,
        principal="fixture-human@example.invalid",
        now_ms=clock[0],
        ttl_ms=60_000,
    )
    recovery_started = perf_counter_ns()
    approved = broker_b.approve(resumed.intent_id, resumed.fencing_token, grant)
    committed = broker_b.commit(approved.intent_id, approved.fencing_token, adapter)
    ended = perf_counter_ns()
    applications = adapter.physical_apply_count(prepared.idempotency_key)
    passed = (
        approval_blocked
        and applications_before_approval == 0
        and committed.state is EffectState.COMMITTED
        and applications == 1
    )
    return _trial_record(
        contract,
        scenario_id="delayed-human-approval",
        trial_index=trial_index,
        seed=seed,
        passed=passed,
        duration_ns=ended - started,
        recovery_duration_ns=ended - recovery_started,
        physical_effect_applications=applications,
        duplicate_effect_applications=max(0, applications - 1),
        injected_faults=("approval-absent-across-one-year-logical-delay",),
        assertions=(
            "irreversible effect remains blocked without an exact signed grant",
            "restart and arbitrary logical delay preserve the prepared intent",
            "approved effect commits once",
        ),
        observations=(
            ("applications_before_approval", applications_before_approval),
            ("logical_pause_ms", 86_400_000 * 365),
        ),
    )


def _local_orchestration_overhead(
    contract: SurvivalContract,
    root: Path,
    trial_index: int,
    seed: int,
) -> SurvivalTrialRecord:
    graph = adaptive_recovery_drill_graph()
    envelope = adaptive_recovery_drill_envelope()
    workers = _fixture_workers()
    direct_started = perf_counter_ns()
    direct_outputs: dict[str, object] = {}
    for task_id in graph.topological_order():
        task = graph.by_id[task_id]
        profile = task.profiles[0]
        result = workers[task.task_id](
            AdaptiveTaskContext(
                run_id=f"direct-{trial_index}",
                task_id=task.task_id,
                attempt=1,
                provider=profile.provider,
                backend=profile.name,
                dependency_outputs={
                    dependency: direct_outputs[dependency]
                    for dependency in task.dependencies
                },
            )
        )
        direct_outputs[task.task_id] = result.output
    direct_duration = perf_counter_ns() - direct_started
    finite_started = perf_counter_ns()
    runtime = AdaptiveRuntime(
        SQLiteRunStore(root / f"trial-{trial_index}.db"),
        graph,
        envelope,
        run_id=f"finite-overhead-{trial_index}",
        workers=workers,
    )
    result = runtime.run_until_blocked(start_at_ms=1, max_dispatches=len(graph.tasks))
    finite_duration = perf_counter_ns() - finite_started
    passed = (
        result.state.status is AdaptiveStatus.COMPLETED
        and content_digest(direct_outputs) == content_digest(result.outputs)
    )
    return _trial_record(
        contract,
        scenario_id="local-orchestration-overhead",
        trial_index=trial_index,
        seed=seed,
        passed=passed,
        duration_ns=finite_duration,
        recovery_duration_ns=None,
        direct_duration_ns=direct_duration,
        orchestration_overhead_ns=max(0, finite_duration - direct_duration),
        injected_faults=(),
        assertions=(
            "direct fixture and FINITE produce identical output digests",
            "FINITE reaches a durable terminal state",
        ),
        observations=(
            ("completed_task_count", len(result.state.completed_task_ids)),
            ("timed_task_count", len(graph.tasks)),
        ),
    )


def _fixture_workers() -> dict[str, AdaptiveWorker]:
    graph = adaptive_recovery_drill_graph()
    workers: dict[str, AdaptiveWorker] = {}
    for task in graph.tasks:
        profile = task.profiles[0]

        def worker(
            context: AdaptiveTaskContext,
            *,
            task_id: str = task.task_id,
            usage: Usage = Usage(
                tokens=profile.total_tokens,
                cost_microusd=profile.cost_microusd,
                context_bytes=profile.context_bytes,
            ),
        ) -> AdaptiveWorkerResult:
            return AdaptiveWorkerResult(
                output={"task_id": task_id, "source": "deterministic-local-fixture"},
                actual_usage=usage,
                duration_ms=1,
            )

        workers[task.task_id] = worker
    return workers


def _approved_effect(broker: SQLiteEffectBroker, *, key: str):
    proposed = broker.propose(
        run_id=f"survival-{key}",
        action="publish_notice",
        resource="fixture/notices",
        effect_class=EffectClass.IDEMPOTENT_WRITE,
        idempotency_key=key,
        payload={"message": "fixture only"},
    )
    prepared = broker.prepare(proposed.intent_id)
    return broker.approve(prepared.intent_id, prepared.fencing_token)


def _trial_record(
    contract: SurvivalContract,
    *,
    scenario_id: str,
    trial_index: int,
    seed: int,
    passed: bool,
    duration_ns: int,
    recovery_duration_ns: int | None,
    direct_duration_ns: int | None = None,
    orchestration_overhead_ns: int | None = None,
    external_provider_calls: int = 0,
    physical_effect_applications: int = 0,
    duplicate_effect_applications: int = 0,
    injected_faults: tuple[str, ...],
    assertions: tuple[str, ...],
    observations: tuple[tuple[str, JsonScalar], ...],
) -> SurvivalTrialRecord:
    unsigned = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "benchmark_id": contract.benchmark_id,
        "contract_digest": contract.contract_digest,
        "scenario_id": scenario_id,
        "trial_index": trial_index,
        "seed": seed,
        "measurement_kind": contract.measurement_kind,
        "passed": passed,
        "duration_ns": duration_ns,
        "recovery_duration_ns": recovery_duration_ns,
        "direct_duration_ns": direct_duration_ns,
        "orchestration_overhead_ns": orchestration_overhead_ns,
        "external_provider_calls": external_provider_calls,
        "physical_effect_applications": physical_effect_applications,
        "duplicate_effect_applications": duplicate_effect_applications,
        "injected_faults": injected_faults,
        "assertions": assertions,
        "observations": tuple(sorted(observations)),
    }
    return SurvivalTrialRecord(**unsigned, record_digest=content_digest(unsigned))


def _summarize_scenario(
    scenario_id: str,
    records: tuple[SurvivalTrialRecord, ...],
) -> SurvivalScenarioSummary:
    trials = len(records)
    passes = sum(record.passed for record in records)
    pass_rate = passes / trials
    recoveries = tuple(
        record.recovery_duration_ns
        for record in records
        if record.recovery_duration_ns is not None
    )
    directs = tuple(
        record.direct_duration_ns
        for record in records
        if record.direct_duration_ns is not None
    )
    overheads = tuple(
        record.orchestration_overhead_ns
        for record in records
        if record.orchestration_overhead_ns is not None
    )
    return SurvivalScenarioSummary(
        scenario_id=scenario_id,
        trials=trials,
        passes=passes,
        per_trial_pass_rate=pass_rate,
        pass_pow_k_estimate=pass_rate**trials,
        all_k_observed=passes == trials,
        p50_duration_ns=_percentile(tuple(record.duration_ns for record in records), 0.50),
        p95_duration_ns=_percentile(tuple(record.duration_ns for record in records), 0.95),
        p99_duration_ns=_percentile(tuple(record.duration_ns for record in records), 0.99),
        p50_recovery_duration_ns=_optional_percentile(recoveries, 0.50),
        p95_recovery_duration_ns=_optional_percentile(recoveries, 0.95),
        p99_recovery_duration_ns=_optional_percentile(recoveries, 0.99),
        p50_direct_duration_ns=_optional_percentile(directs, 0.50),
        p50_orchestration_overhead_ns=_optional_percentile(overheads, 0.50),
        external_provider_calls=sum(record.external_provider_calls for record in records),
        physical_effect_applications=sum(
            record.physical_effect_applications for record in records
        ),
        duplicate_effect_applications=sum(
            record.duplicate_effect_applications for record in records
        ),
    )


def _percentile(values: tuple[int, ...], quantile: float) -> int:
    if not values:
        raise SurvivalInvariantError("percentile requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _optional_percentile(values: tuple[int, ...], quantile: float) -> int | None:
    return _percentile(values, quantile) if values else None


def _optional_nonnegative_int(value: int | None) -> bool:
    return value is None or (type(value) is int and value >= 0)


def _is_json_scalar(value: object) -> bool:
    return (
        value is None
        or type(value) in {str, int, bool}
        or (type(value) is float and math.isfinite(value))
    )


def _strict_json_loads(value: str) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise SurvivalInvariantError(f"duplicate JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(value: str) -> object:
        raise SurvivalInvariantError(f"non-finite JSON constant {value!r}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise SurvivalInvariantError(f"non-finite JSON number {value!r}")
        return parsed

    return json.loads(
        value,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
        parse_float=parse_finite_float,
    )


def _pretty_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_mapping(value: object, fields: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise SurvivalInvariantError(f"{label} fields changed")
    return cast(dict[str, object], value)


def _contract_from_dict(value: object) -> SurvivalContract:
    fields = set(SurvivalContract.__dataclass_fields__)
    mapping = _exact_mapping(value, fields, "survival contract")
    try:
        contract = SurvivalContract(
            schema_version=cast(str, mapping["schema_version"]),
            benchmark_id=cast(str, mapping["benchmark_id"]),
            measurement_kind=cast(str, mapping["measurement_kind"]),
            timer=cast(str, mapping["timer"]),
            trials_per_scenario=cast(int, mapping["trials_per_scenario"]),
            seed_base=cast(int, mapping["seed_base"]),
            scenario_ids=tuple(cast(list[str], mapping["scenario_ids"])),
            seed_derivation=cast(str, mapping["seed_derivation"]),
            reliability_definition=cast(str, mapping["reliability_definition"]),
            timed_scope=cast(str, mapping["timed_scope"]),
            claim_boundaries=tuple(cast(list[str], mapping["claim_boundaries"])),
            contract_digest=cast(str, mapping["contract_digest"]),
        )
    except (TypeError, ValueError) as error:
        raise SurvivalInvariantError("survival contract shape is invalid") from error
    if not contract.verify():
        raise SurvivalInvariantError("survival contract verification failed")
    return contract


def _record_from_dict(value: object) -> SurvivalTrialRecord:
    fields = set(SurvivalTrialRecord.__dataclass_fields__)
    mapping = _exact_mapping(value, fields, "survival trial record")
    observations = mapping["observations"]
    if type(observations) is not list or any(
        type(item) is not list or len(item) != 2 for item in observations
    ):
        raise SurvivalInvariantError("survival trial observations are invalid")
    try:
        return SurvivalTrialRecord(
            schema_version=cast(str, mapping["schema_version"]),
            benchmark_id=cast(str, mapping["benchmark_id"]),
            contract_digest=cast(str, mapping["contract_digest"]),
            scenario_id=cast(str, mapping["scenario_id"]),
            trial_index=cast(int, mapping["trial_index"]),
            seed=cast(int, mapping["seed"]),
            measurement_kind=cast(str, mapping["measurement_kind"]),
            passed=cast(bool, mapping["passed"]),
            duration_ns=cast(int, mapping["duration_ns"]),
            recovery_duration_ns=cast(int | None, mapping["recovery_duration_ns"]),
            direct_duration_ns=cast(int | None, mapping["direct_duration_ns"]),
            orchestration_overhead_ns=cast(
                int | None, mapping["orchestration_overhead_ns"]
            ),
            external_provider_calls=cast(int, mapping["external_provider_calls"]),
            physical_effect_applications=cast(
                int, mapping["physical_effect_applications"]
            ),
            duplicate_effect_applications=cast(
                int, mapping["duplicate_effect_applications"]
            ),
            injected_faults=tuple(cast(list[str], mapping["injected_faults"])),
            assertions=tuple(cast(list[str], mapping["assertions"])),
            observations=tuple(
                (cast(str, item[0]), cast(JsonScalar, item[1]))
                for item in cast(list[list[object]], observations)
            ),
            record_digest=cast(str, mapping["record_digest"]),
        )
    except (TypeError, ValueError) as error:
        raise SurvivalInvariantError("survival trial record shape is invalid") from error


def _report_from_dict(value: object) -> SurvivalReport:
    fields = set(SurvivalReport.__dataclass_fields__)
    mapping = _exact_mapping(value, fields, "survival report")
    environment = mapping["environment"]
    summaries = mapping["scenario_summaries"]
    if type(environment) is not list or any(
        type(item) is not list or len(item) != 2 for item in environment
    ):
        raise SurvivalInvariantError("survival report environment is invalid")
    if type(summaries) is not list:
        raise SurvivalInvariantError("survival report summaries are invalid")
    try:
        parsed_summaries = tuple(
            SurvivalScenarioSummary(
                **_exact_mapping(
                    item,
                    set(SurvivalScenarioSummary.__dataclass_fields__),
                    "survival scenario summary",
                )
            )
            for item in summaries
        )
        return SurvivalReport(
            schema_version=cast(str, mapping["schema_version"]),
            benchmark_id=cast(str, mapping["benchmark_id"]),
            contract_digest=cast(str, mapping["contract_digest"]),
            measurement_kind=cast(str, mapping["measurement_kind"]),
            source_revision=cast(str, mapping["source_revision"]),
            source_state=cast(str, mapping["source_state"]),
            environment=tuple(
                (cast(str, item[0]), cast(str, item[1]))
                for item in cast(list[list[object]], environment)
            ),
            total_trials=cast(int, mapping["total_trials"]),
            total_passes=cast(int, mapping["total_passes"]),
            all_trials_observed_passed=cast(
                bool, mapping["all_trials_observed_passed"]
            ),
            scenario_summaries=parsed_summaries,
            external_provider_calls=cast(int, mapping["external_provider_calls"]),
            duplicate_effect_applications=cast(
                int, mapping["duplicate_effect_applications"]
            ),
            records_digest=cast(str, mapping["records_digest"]),
            claim_boundaries=tuple(cast(list[str], mapping["claim_boundaries"])),
            report_digest=cast(str, mapping["report_digest"]),
        )
    except (TypeError, ValueError) as error:
        raise SurvivalInvariantError("survival report shape is invalid") from error


def runtime_identity() -> tuple[tuple[str, str], ...]:
    """Return the non-secret runtime identity used in public benchmark metadata."""

    return tuple(
        sorted(
            (
                ("executable", Path(sys.executable).name),
                ("machine", platform.machine() or "unknown"),
                ("platform", platform.platform()),
                ("python", platform.python_version()),
                ("python_implementation", platform.python_implementation()),
            )
        )
    )


__all__ = [
    "BENCHMARK_ID",
    "CLAIM_BOUNDARIES",
    "DEFAULT_TRIALS",
    "MIN_TRIALS",
    "SCENARIO_IDS",
    "SurvivalContract",
    "SurvivalEvidence",
    "SurvivalInvariantError",
    "SurvivalReport",
    "SurvivalScenarioSummary",
    "SurvivalTrialRecord",
    "build_survival_contract",
    "build_survival_report",
    "run_production_survival",
    "runtime_identity",
    "verify_survival_evidence_directory",
]
