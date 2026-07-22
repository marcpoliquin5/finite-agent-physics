"""Executed, digest-bound benchmark evidence for the StormShift fixture.

This module is deliberately stricter than a leaderboard.  It compares only
systems that were actually invoked on the same committed local fixture, keeps
warmups and measured trials distinct, and publishes raw receipts beside paired
confidence intervals.  Alibaba PageAgent is retained as an explicit
``not_executed`` reference row; this harness has no PageAgent integration and
therefore produces no PageAgent performance numbers.

The fixture workers make no model, provider, network, or public-safety calls.
Consequently, measured wall time is local orchestration/control-plane overhead
for deterministic fixture work.  It is not model quality, model latency,
provider throughput, or production performance.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import platform
import random
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Awaitable, Callable, Sequence, cast

from .contracts import BackendProfile
from .effects import SQLiteEffectBroker
from .examples import miami_eoc_graph
from .executor import AdmissionRefused, CancellationSignal, TaskExecutionContext
from .experiments import wilson_interval
from .langgraph_baseline import langgraph_baseline_available, run_langgraph_stormshift_baseline
from .run_store import SQLiteRunStore
from .serialization import canonical_json, content_digest, normalize
from .stormshift import StormShiftValidator, stormshift_fixture
from .stormshift_runtime import (
    PUBLISH_TASK_ID,
    PURE_TASK_IDS,
    StormShiftFixtureWorkers,
    StormShiftRuntime,
    _response_plan_from_output,
    stormshift_envelope,
)


CONTRACT_SCHEMA_VERSION = "finite-fair-benchmark-contract/v1"
ENVIRONMENT_SCHEMA_VERSION = "finite-fair-benchmark-environment/v1"
RECORD_SCHEMA_VERSION = "finite-fair-benchmark-record/v1"
REPORT_SCHEMA_VERSION = "finite-fair-benchmark-report/v1"
EVIDENCE_SCHEMA_VERSION = "finite-fair-benchmark-evidence/v1"
WRITER_MANIFEST_SCHEMA_VERSION = "finite-fair-benchmark-files/v1"

FINITE_SYSTEM_ID = "finite"
PYTHON_SYSTEM_ID = "python-sequential"
LANGGRAPH_SYSTEM_ID = "langgraph"
PAGEAGENT_SYSTEM_ID = "pageagent"
EXPECTED_LANGGRAPH_VERSION = "1.2.9"
EXPECTED_LANGGRAPH_CHECKPOINT_VERSION = "3.1.0"
DEFAULT_MEASURED_SEEDS = (
    11,
    29,
    47,
    71,
    97,
    131,
    163,
    197,
    229,
    263,
    307,
    347,
    389,
    431,
    479,
    523,
    571,
    617,
    661,
    709,
    757,
    809,
    857,
    907,
    953,
    1_009,
    1_061,
    1_117,
    1_171,
    1_223,
)
MIN_MEASURED_SEEDS = 30
MIN_BOOTSTRAP_SAMPLES = 200


class FairBenchmarkInvariantError(ValueError):
    """A contract, receipt, or report violates the evidence protocol."""


@dataclass(frozen=True, slots=True)
class BenchmarkSystemPlan:
    """Pre-registered treatment of one named system."""

    system_id: str
    display_name: str
    framework: str
    execution_mode: str
    version_pin: str | None
    workload_role: str
    metrics_policy: str


@dataclass(frozen=True, slots=True)
class FairBenchmarkContract:
    """Immutable design registered before any timed trial is observed."""

    schema_version: str
    benchmark_id: str
    input_provenance: str
    workload_id: str
    graph_digest: str
    envelope_digest: str
    fixture_digest: str
    expected_comparable_output_digest: str
    expected_structural_validation_digest: str
    finite_source_fingerprint: str
    warmup_count: int
    measured_seeds: tuple[int, ...]
    seed_role: str
    execution_order_method: str
    timer: str
    timed_scope: str
    confidence_level: float
    bootstrap_samples: int
    systems: tuple[BenchmarkSystemPlan, ...]
    common_slo: tuple[str, ...]
    claim_boundaries: tuple[str, ...]
    non_equivalence_boundaries: tuple[str, ...]
    contract_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], normalize(self))
        payload.pop("contract_digest")
        return payload

    def verify_digest(self) -> bool:
        return self.contract_digest == content_digest(self.unsigned_payload())

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], normalize(self))

    def validate(self) -> None:
        errors: list[str] = []
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            errors.append("unsupported benchmark contract schema")
        if not self.benchmark_id or not self.workload_id:
            errors.append("benchmark and workload IDs are required")
        if type(self.warmup_count) is not int or self.warmup_count < 1:
            errors.append("at least one warmup is required")
        if len(self.measured_seeds) < MIN_MEASURED_SEEDS:
            errors.append(f"at least {MIN_MEASURED_SEEDS} measured seeds are required")
        if len(set(self.measured_seeds)) != len(self.measured_seeds):
            errors.append("measured seeds must be unique")
        if any(type(seed) is not int or not 0 <= seed < (1 << 63) for seed in self.measured_seeds):
            errors.append("measured seeds must be non-negative signed int64 values")
        if self.timer != "time.perf_counter_ns":
            errors.append("the registered monotonic nanosecond timer changed")
        if self.confidence_level != 0.95:
            errors.append("only the registered 95% confidence level is supported")
        if type(self.bootstrap_samples) is not int or self.bootstrap_samples < MIN_BOOTSTRAP_SAMPLES:
            errors.append(
                f"bootstrap_samples must be an integer >= {MIN_BOOTSTRAP_SAMPLES}"
            )
        system_ids = [system.system_id for system in self.systems]
        if system_ids != [
            FINITE_SYSTEM_ID,
            PYTHON_SYSTEM_ID,
            LANGGRAPH_SYSTEM_ID,
            PAGEAGENT_SYSTEM_ID,
        ]:
            errors.append("the registered system set or order changed")
        pageagent = next(
            (system for system in self.systems if system.system_id == PAGEAGENT_SYSTEM_ID),
            None,
        )
        if pageagent is None or (
            pageagent.execution_mode != "unexecuted_reference_only"
            or pageagent.metrics_policy != "forbid_metrics"
            or pageagent.version_pin is not None
        ):
            errors.append("PageAgent must remain an unexecuted, metric-free reference")
        if not self.verify_digest():
            errors.append("benchmark contract digest mismatch")
        if errors:
            raise FairBenchmarkInvariantError("; ".join(errors))


@dataclass(frozen=True, slots=True)
class PackageVersion:
    package: str
    version: str | None
    status: str


@dataclass(frozen=True, slots=True)
class SourceDigest:
    component: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BenchmarkEnvironment:
    """Non-identifying local environment metadata bound to every receipt."""

    schema_version: str
    captured_at_utc: str
    python_version: str
    python_implementation: str
    platform_system: str
    platform_release: str
    machine: str
    processor: str
    logical_cpu_count: int | None
    perf_counter_resolution_seconds: float
    perf_counter_monotonic: bool
    perf_counter_adjustable: bool
    package_versions: tuple[PackageVersion, ...]
    repository_commit: str | None
    repository_dirty: bool | None
    source_digests: tuple[SourceDigest, ...]
    excluded_identifiers: tuple[str, ...]
    environment_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], normalize(self))
        payload.pop("environment_digest")
        return payload

    def verify_digest(self) -> bool:
        return self.environment_digest == content_digest(self.unsigned_payload())

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], normalize(self))


@dataclass(frozen=True, slots=True)
class ProfileSelectionEvidence:
    task_id: str
    profile_name: str
    provider: str
    duration_ms_p95: int
    tokens: int
    cost_microusd: int
    context_bytes: int
    quality: float
    failure_probability: float


@dataclass(frozen=True, slots=True)
class ResourceTotals:
    tokens: int = 0
    cost_microusd: int = 0
    context_bytes: int = 0


@dataclass(frozen=True, slots=True)
class FairBenchmarkRecord:
    """One raw actual-execution receipt, including warmups."""

    schema_version: str
    contract_digest: str
    environment_digest: str
    system_id: str
    system_label: str
    framework: str
    framework_version: str
    phase: str
    sample_index: int
    seed: int | None
    order_position: int
    run_id: str
    execution_status: str
    outcome: str
    duration_ns: int
    timer: str
    workload_id: str
    graph_digest: str
    envelope_digest: str
    fixture_digest: str
    common_validation_passed: bool | None
    common_validation_digest: str | None
    expected_comparable_output_digest: str
    comparable_output_digest: str | None
    comparable_output_conforms: bool | None
    effect_state: str
    external_effects_executed: int
    unauthorized_effects: int
    guardrail_passed: bool
    safe_refusal: bool
    deadline_passed: bool
    slo_passed: bool
    checkpoint_verified: bool | None
    cache_enabled: bool | None
    admission_performed: bool | None
    retries_configured: bool | None
    model_calls_made: bool
    external_calls_made: bool
    actual_usage: ResourceTotals
    declared_profile_totals: ResourceTotals | None
    declared_resource_fit: bool | None
    profile_selection_source: str | None
    selected_profiles: tuple[ProfileSelectionEvidence, ...]
    selected_profiles_digest: str | None
    additional_validation_scope: str | None
    additional_validation_passed: bool | None
    source_evidence_digest: str | None
    refusal_code: str | None
    error_type: str | None
    record_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], normalize(self))
        payload.pop("record_digest")
        return payload

    def verify_digest(self) -> bool:
        return self.record_digest == content_digest(self.unsigned_payload())

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], normalize(self))

    def validate(self) -> None:
        errors: list[str] = []
        if self.schema_version != RECORD_SCHEMA_VERSION:
            errors.append("unsupported raw-record schema")
        if self.execution_status != "executed-local":
            errors.append("raw records require an actual local invocation")
        if self.phase not in {"warmup", "measured"}:
            errors.append("record phase must be warmup or measured")
        if self.phase == "measured" and self.seed is None:
            errors.append("measured records require a paired seed")
        if self.phase == "warmup" and self.seed is not None:
            errors.append("warmup records must not masquerade as seeded measurements")
        if min(self.sample_index, self.order_position, self.duration_ns) < 0:
            errors.append("sample index, order position, and duration must be non-negative")
        if self.outcome not in {"passed", "refused", "error"}:
            errors.append("unknown execution outcome")
        if self.external_effects_executed < 0 or self.unauthorized_effects < 0:
            errors.append("effect counts cannot be negative")
        if self.unauthorized_effects > self.external_effects_executed:
            errors.append("unauthorized effects cannot exceed external effects")
        expected_guardrail = (
            not self.model_calls_made
            and not self.external_calls_made
            and self.external_effects_executed == 0
            and self.unauthorized_effects == 0
            and (
                (self.outcome == "passed" and self.effect_state == "proposed")
                or (self.outcome == "refused" and self.effect_state == "none")
            )
        )
        if self.guardrail_passed != expected_guardrail:
            errors.append("guardrail classification is inconsistent with raw effect evidence")
        if self.safe_refusal != (self.outcome == "refused" and self.guardrail_passed):
            errors.append("safe-refusal classification is inconsistent")
        expected_slo = (
            self.outcome == "passed"
            and self.common_validation_passed is True
            and self.comparable_output_conforms is True
            and self.deadline_passed
            and self.guardrail_passed
        )
        if self.slo_passed != expected_slo:
            errors.append("SLO classification is inconsistent with raw evidence")
        if self.outcome == "passed":
            if None in {
                self.common_validation_passed,
                self.common_validation_digest,
                self.comparable_output_digest,
                self.comparable_output_conforms,
                self.source_evidence_digest,
            }:
                errors.append("passed outcomes require complete validation evidence")
            if self.refusal_code is not None or self.error_type is not None:
                errors.append("passed outcomes cannot carry failure codes")
        elif self.outcome == "refused":
            if self.refusal_code is None or self.error_type is not None:
                errors.append("refusals require only a redacted refusal code")
        elif self.error_type is None or self.refusal_code is not None:
            errors.append("errors require only a redacted error type")
        if self.selected_profiles:
            if self.selected_profiles_digest != content_digest(self.selected_profiles):
                errors.append("selected-profile digest mismatch")
            if self.declared_profile_totals != _profile_totals(self.selected_profiles):
                errors.append("declared-profile totals mismatch")
        elif self.selected_profiles_digest is not None:
            errors.append("empty selected profiles cannot have a digest")
        if not self.verify_digest():
            errors.append("raw-record digest mismatch")
        if errors:
            raise FairBenchmarkInvariantError("; ".join(errors))


@dataclass(frozen=True, slots=True)
class BenchmarkSystemStatus:
    system_id: str
    display_name: str
    framework: str
    execution_status: str
    framework_version: str | None
    version_pin: str | None
    measured_records: int
    warmup_records: int
    metrics_eligible: bool
    reason_code: str
    status_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], normalize(self))
        payload.pop("status_digest")
        return payload

    def verify_digest(self) -> bool:
        return self.status_digest == content_digest(self.unsigned_payload())


@dataclass(frozen=True, slots=True)
class Interval95:
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class DurationSummary:
    population: str
    count: int
    p50_ns: float
    p95_ns: float
    bootstrap_p50_95: Interval95
    bootstrap_p95_95: Interval95


@dataclass(frozen=True, slots=True)
class SystemSummary:
    system_id: str
    measured_runs: int
    slo_passes: int
    refusals: int
    safe_refusals: int
    errors: int
    unsafe_effect_runs: int
    guardrail_passes: int
    pass_rate: float
    pass_rate_wilson_95: Interval95
    guardrail_rate: float
    guardrail_rate_wilson_95: Interval95
    duration: DurationSummary | None


@dataclass(frozen=True, slots=True)
class PairedDurationComparison:
    baseline_system_id: str
    delta_definition: str
    eligible_pairs: int
    excluded_pairs: int
    mean_delta_ns: float | None
    p50_delta_ns: float | None
    bootstrap_mean_delta_95: Interval95 | None


@dataclass(frozen=True, slots=True)
class FairBenchmarkReport:
    schema_version: str
    contract_digest: str
    environment_digest: str
    raw_record_set_digest: str
    warmup_records: int
    measured_records: int
    warmups_excluded_from_statistics: bool
    system_statuses: tuple[BenchmarkSystemStatus, ...]
    system_summaries: tuple[SystemSummary, ...]
    paired_duration_comparisons: tuple[PairedDurationComparison, ...]
    unexecuted_system_ids: tuple[str, ...]
    all_passed_outputs_match_registered_fixture: bool
    statistical_scope: str
    interpretation: tuple[str, ...]
    report_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], normalize(self))
        payload.pop("report_digest")
        return payload

    def verify_digest(self) -> bool:
        return self.report_digest == content_digest(self.unsigned_payload())

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], normalize(self))


@dataclass(frozen=True, slots=True)
class FairBenchmarkEvidence:
    schema_version: str
    contract: FairBenchmarkContract
    environment: BenchmarkEnvironment
    records: tuple[FairBenchmarkRecord, ...]
    report: FairBenchmarkReport
    evidence_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], normalize(self))
        payload.pop("evidence_digest")
        return payload

    def verify_digest(self) -> bool:
        return self.evidence_digest == content_digest(self.unsigned_payload())

    def verify(self) -> None:
        self.contract.validate()
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise FairBenchmarkInvariantError("unsupported evidence schema")
        if not self.environment.verify_digest():
            raise FairBenchmarkInvariantError("environment digest mismatch")
        for record in self.records:
            record.validate()
            if record.contract_digest != self.contract.contract_digest:
                raise FairBenchmarkInvariantError("record is bound to another contract")
            if record.environment_digest != self.environment.environment_digest:
                raise FairBenchmarkInvariantError("record is bound to another environment")
        canonical_records = tuple(
            sorted(
                self.records,
                key=lambda record: (
                    0 if record.phase == "warmup" else 1,
                    record.sample_index,
                    record.order_position,
                    record.system_id,
                ),
            )
        )
        if self.records != canonical_records:
            raise FairBenchmarkInvariantError("evidence records are not in canonical order")
        regenerated = summarize_fair_benchmark(
            self.contract,
            self.environment,
            self.report.system_statuses,
            self.records,
        )
        if regenerated != self.report:
            raise FairBenchmarkInvariantError("report differs from deterministic regeneration")
        if not self.report.verify_digest() or not self.verify_digest():
            raise FairBenchmarkInvariantError("report or evidence digest mismatch")


@dataclass(frozen=True, slots=True)
class EvidenceFileManifest:
    schema_version: str
    output_directory: str
    files: tuple[tuple[str, str], ...]
    raw_record_count: int
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class _SystemObservation:
    framework: str
    framework_version: str
    common_validation_passed: bool
    common_validation_digest: str
    comparable_output_digest: str
    effect_state: str
    external_effects_executed: int
    checkpoint_verified: bool
    cache_enabled: bool
    admission_performed: bool
    retries_configured: bool
    model_calls_made: bool
    external_calls_made: bool
    actual_usage: ResourceTotals
    declared_resource_fit: bool | None
    profile_selection_source: str
    selected_profiles: tuple[ProfileSelectionEvidence, ...]
    additional_validation_scope: str | None
    additional_validation_passed: bool | None
    source_evidence_digest: str


def _package_version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _source_component_digests() -> tuple[SourceDigest, ...]:
    root = Path(__file__).resolve().parent
    names = (
        "effects.py",
        "executor.py",
        "fair_benchmark.py",
        "langgraph_baseline.py",
        "run_store.py",
        "scheduler.py",
        "stormshift_runtime.py",
    )
    evidence: list[SourceDigest] = []
    for name in names:
        path = root / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        evidence.append(SourceDigest(component=name, sha256=digest))
    return tuple(evidence)


def _finite_source_fingerprint() -> str:
    return content_digest(_source_component_digests())


def _expected_fixture_evidence() -> tuple[str, str]:
    scenario = stormshift_fixture()
    workers = StormShiftFixtureWorkers(scenario)
    # The private fixture constructor is used only to register expected values;
    # actual benchmark runs still invoke the public async worker boundary.
    outputs = {
        task_id: workers._output_for(task_id)  # noqa: SLF001
        for task_id in sorted(PURE_TASK_IDS)
    }
    plan = _response_plan_from_output(outputs["response_plan"])
    validation = StormShiftValidator().validate(scenario, plan)
    if not validation.passed or not validation.verify_digest():
        raise FairBenchmarkInvariantError("committed fixture reference no longer validates")
    return content_digest(outputs), validation.report_digest


def build_fair_benchmark_contract(
    *,
    warmup_count: int = 1,
    measured_seeds: Sequence[int] = DEFAULT_MEASURED_SEEDS,
    bootstrap_samples: int = 2_000,
) -> FairBenchmarkContract:
    """Register the benchmark design without observing any timed trial."""

    graph = miami_eoc_graph()
    envelope = stormshift_envelope()
    scenario = stormshift_fixture()
    expected_output_digest, expected_validation_digest = _expected_fixture_evidence()
    finite_version = _package_version("agent-physics") or "source-tree-uninstalled"
    systems = (
        BenchmarkSystemPlan(
            system_id=FINITE_SYSTEM_ID,
            display_name="FINITE durable runtime",
            framework="agent-physics",
            execution_mode="actual_local_required",
            version_pin=finite_version,
            workload_role="system_under_test",
            metrics_policy="actual_receipts_only",
        ),
        BenchmarkSystemPlan(
            system_id=PYTHON_SYSTEM_ID,
            display_name="Plain Python sequential control",
            framework="python-stdlib",
            execution_mode="actual_local_required",
            version_pin=platform.python_version(),
            workload_role="minimal_control_baseline",
            metrics_policy="actual_receipts_only",
        ),
        BenchmarkSystemPlan(
            system_id=LANGGRAPH_SYSTEM_ID,
            display_name="LangGraph static comparator",
            framework="langgraph",
            execution_mode="actual_local_if_exact_pin_available",
            version_pin=(
                f"langgraph=={EXPECTED_LANGGRAPH_VERSION};"
                f"langgraph-checkpoint-sqlite=={EXPECTED_LANGGRAPH_CHECKPOINT_VERSION}"
            ),
            workload_role="external_framework_baseline",
            metrics_policy="actual_receipts_only_or_unexecuted",
        ),
        BenchmarkSystemPlan(
            system_id=PAGEAGENT_SYSTEM_ID,
            display_name="Alibaba PageAgent",
            framework="pageagent",
            execution_mode="unexecuted_reference_only",
            version_pin=None,
            workload_role="named_external_reference_without_local_integration",
            metrics_policy="forbid_metrics",
        ),
    )
    fields: dict[str, object] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "benchmark_id": "stormshift-executed-local-fair-comparison-v1",
        "input_provenance": (
            "committed deterministic local fixture; BackendProfile quantities are "
            "caller-supplied estimates and are not provider telemetry"
        ),
        "workload_id": "stormshift-miami-eoc-fixture-v1",
        "graph_digest": content_digest(graph),
        "envelope_digest": content_digest(envelope),
        "fixture_digest": scenario.fixture_digest,
        "expected_comparable_output_digest": expected_output_digest,
        "expected_structural_validation_digest": expected_validation_digest,
        "finite_source_fingerprint": _finite_source_fingerprint(),
        "warmup_count": warmup_count,
        "measured_seeds": tuple(measured_seeds),
        "seed_role": (
            "paired trial identifier and deterministic order-design input only; "
            "the fixed workload contains no stochastic model call"
        ),
        "execution_order_method": (
            "seed-set-derived base permutation with cyclic Latin rotation; warmups use "
            "the same rotation and never enter statistics"
        ),
        "timer": "time.perf_counter_ns",
        "timed_scope": (
            "end-to-end local run lifecycle including framework-required persistence "
            "setup, execution, validation, normalization, and cleanup"
        ),
        "confidence_level": 0.95,
        "bootstrap_samples": bootstrap_samples,
        "systems": systems,
        "common_slo": (
            "actual local invocation completes without refusal or error",
            "registered common structural validator passes",
            "comparable pure-task output matches the pre-registered fixture digest",
            "local end-to-end duration does not exceed the committed 12-second envelope",
            "no model call, external call, or external effect occurs",
            "terminal write remains a proposal only",
        ),
        "claim_boundaries": (
            "No superiority or winner claim is inferred from this benchmark.",
            "Wall time measures local fixture orchestration overhead, not model or provider speed.",
            "Confidence intervals are descriptive for the registered paired trials only.",
            "PageAgent has no executed row and must never receive inferred metrics.",
        ),
        "non_equivalence_boundaries": (
            "FINITE performs admission, durable event/effect persistence, and an additional "
            "bounded semantic-safety check.",
            "The plain-Python control has no checkpoint, scheduler admission, retry engine, "
            "or durable effect broker.",
            "The LangGraph comparator uses SQLite checkpointing but has no FINITE admission, "
            "resource reservation, retry engine, or durable effect broker.",
            "FINITE's scheduler and the static baselines select different declared profiles; "
            "fixture workers ignore those profiles and make zero model calls.",
            "Declared token/cost/context totals have different selection scopes and are shown "
            "for audit only, never as cross-system measured-resource comparisons.",
            "Persistence setup and framework lifecycle costs are intentionally included, while "
            "host load, thermal state, and energy are not controlled or measured.",
        ),
    }
    fields["contract_digest"] = content_digest(fields)
    contract = FairBenchmarkContract(**fields)  # type: ignore[arg-type]
    contract.validate()
    return contract


def _git_metadata(root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        return (commit or None), bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None


def capture_benchmark_environment() -> BenchmarkEnvironment:
    """Capture reproducibility metadata without hostname, username, or paths."""

    timer = time.get_clock_info("perf_counter")
    packages = tuple(
        PackageVersion(
            package=package,
            version=(version := _package_version(package)),
            status="installed" if version is not None else "not-installed",
        )
        for package in (
            "agent-physics",
            "langgraph",
            "langgraph-checkpoint-sqlite",
            "PyYAML",
        )
    )
    repository_root = Path(__file__).resolve().parents[2]
    commit, dirty = _git_metadata(repository_root)
    fields: dict[str, object] = {
        "schema_version": ENVIRONMENT_SCHEMA_VERSION,
        "captured_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "perf_counter_resolution_seconds": timer.resolution,
        "perf_counter_monotonic": timer.monotonic,
        "perf_counter_adjustable": timer.adjustable,
        "package_versions": packages,
        "repository_commit": commit,
        "repository_dirty": dirty,
        "source_digests": _source_component_digests(),
        "excluded_identifiers": (
            "hostname",
            "username",
            "home_directory",
            "absolute_python_executable",
        ),
    }
    fields["environment_digest"] = content_digest(fields)
    result = BenchmarkEnvironment(**fields)  # type: ignore[arg-type]
    if not result.verify_digest():
        raise FairBenchmarkInvariantError("environment self-digest failed")
    return result


def _assert_current_workload(contract: FairBenchmarkContract) -> None:
    expected_output, expected_validation = _expected_fixture_evidence()
    comparisons = {
        "graph": (contract.graph_digest, content_digest(miami_eoc_graph())),
        "envelope": (contract.envelope_digest, content_digest(stormshift_envelope())),
        "fixture": (contract.fixture_digest, stormshift_fixture().fixture_digest),
        "comparable output": (contract.expected_comparable_output_digest, expected_output),
        "structural validation": (
            contract.expected_structural_validation_digest,
            expected_validation,
        ),
        "FINITE source": (contract.finite_source_fingerprint, _finite_source_fingerprint()),
    }
    changed = [label for label, (registered, current) in comparisons.items() if registered != current]
    if changed:
        raise FairBenchmarkInvariantError(
            f"registered benchmark inputs changed before execution: {changed}"
        )


def _highest_quality_profile(task: object) -> BackendProfile:
    profiles = cast(tuple[BackendProfile, ...], getattr(task, "profiles"))
    min_quality = cast(float, getattr(task, "min_quality"))
    task_id = cast(str, getattr(task, "task_id"))
    qualified = [profile for profile in profiles if profile.quality >= min_quality]
    if not qualified:
        raise FairBenchmarkInvariantError(f"task {task_id!r} has no qualified static profile")
    return min(
        qualified,
        key=lambda profile: (
            -profile.quality,
            profile.duration_ms_p95,
            profile.cost_microusd,
            profile.total_tokens,
            profile.context_bytes,
            profile.failure_probability,
            profile.provider,
            profile.name,
        ),
    )


def _profile_evidence(
    *,
    task_id: str,
    profile_name: str,
    provider: str,
    duration_ms_p95: int,
    tokens: int,
    cost_microusd: int,
    context_bytes: int,
    quality: float,
    failure_probability: float,
) -> ProfileSelectionEvidence:
    return ProfileSelectionEvidence(
        task_id=task_id,
        profile_name=profile_name,
        provider=provider,
        duration_ms_p95=duration_ms_p95,
        tokens=tokens,
        cost_microusd=cost_microusd,
        context_bytes=context_bytes,
        quality=quality,
        failure_probability=failure_probability,
    )


def _profile_totals(profiles: Sequence[ProfileSelectionEvidence]) -> ResourceTotals:
    return ResourceTotals(
        tokens=sum(profile.tokens for profile in profiles),
        cost_microusd=sum(profile.cost_microusd for profile in profiles),
        context_bytes=sum(profile.context_bytes for profile in profiles),
    )


def _resource_fits(totals: ResourceTotals) -> bool:
    envelope = stormshift_envelope()
    return (
        totals.tokens <= envelope.max_tokens
        and totals.cost_microusd <= envelope.max_cost_microusd
        and totals.context_bytes <= envelope.max_context_bytes
    )


async def _run_finite(run_id: str) -> _SystemObservation:
    with tempfile.TemporaryDirectory(prefix="finite-fair-benchmark-") as directory:
        root = Path(directory)
        store = SQLiteRunStore(root / "runs.sqlite")
        broker = SQLiteEffectBroker(root / "effects.sqlite", broker_id=f"benchmark:{run_id}")
        result = await StormShiftRuntime(store, broker).execute(run_id=run_id)
        outputs = dict(result.execution.outputs)
        comparable = {task_id: outputs[task_id] for task_id in sorted(PURE_TASK_IDS)}
        durable = store.completed_tasks(run_id)
        checkpoint_verified = {
            task_id: completed.output for task_id, completed in durable.items()
        } == outputs
        started = next(
            event for event in result.execution.events if event.event_type == "run.started"
        )
        manifest = cast(dict[str, object], started.payload["manifest"])
        selected_payload = cast(list[dict[str, object]], manifest["selected_profiles"])
        profiles = tuple(
            _profile_evidence(
                task_id=cast(str, item["task_id"]),
                profile_name=cast(str, item["name"]),
                provider=cast(str, item["provider"]),
                duration_ms_p95=cast(int, item["duration_ms_p95"]),
                tokens=cast(int, item["tokens"]),
                cost_microusd=cast(int, item["cost_microusd"]),
                context_bytes=cast(int, item["context_bytes"]),
                quality=cast(float, item["quality"]),
                failure_probability=cast(float, item["failure_probability"]),
            )
            for item in selected_payload
        )
        retry_reservation = cast(dict[str, int], manifest["retry_reservation"])
        reservation = ResourceTotals(
            tokens=retry_reservation["tokens"],
            cost_microusd=retry_reservation["cost_microusd"],
            context_bytes=retry_reservation["context_bytes"],
        )
        source_digest = content_digest(
            {
                "outputs": outputs,
                "event_ids": [event.event_id for event in result.execution.events],
                "manifest_digest": started.payload["manifest_digest"],
                "validation_digest": result.validation.report_digest,
                "semantic_validation_digest": result.semantic_validation.report_digest,
                "effect_intent_id": result.effect_intent.intent_id,
            }
        )
        version = _package_version("agent-physics") or "source-tree-uninstalled"
        return _SystemObservation(
            framework="agent-physics",
            framework_version=version,
            common_validation_passed=result.validation.passed,
            common_validation_digest=result.validation.report_digest,
            comparable_output_digest=content_digest(comparable),
            effect_state=result.effect_intent.state.value,
            external_effects_executed=0,
            checkpoint_verified=checkpoint_verified,
            cache_enabled=False,
            admission_performed=True,
            retries_configured=False,
            model_calls_made=result.model_calls_made,
            external_calls_made=result.external_calls_made,
            actual_usage=ResourceTotals(
                tokens=result.execution.actual_usage.tokens,
                cost_microusd=result.execution.actual_usage.cost_microusd,
                context_bytes=result.execution.actual_usage.context_bytes,
            ),
            declared_resource_fit=_resource_fits(reservation),
            profile_selection_source="FINITE durable admission manifest",
            selected_profiles=profiles,
            additional_validation_scope="bounded reference semantic-safety verifier",
            additional_validation_passed=result.semantic_validation.passed,
            source_evidence_digest=source_digest,
        )


async def _run_python_sequential(run_id: str) -> _SystemObservation:
    graph = miami_eoc_graph()
    envelope = stormshift_envelope()
    scenario = stormshift_fixture()
    workers = StormShiftFixtureWorkers(scenario)
    outputs: dict[str, object] = {}
    profiles: list[ProfileSelectionEvidence] = []
    call_counts = {task_id: 0 for task_id in graph.by_id}
    for task_id in graph.topological_order():
        task = graph.by_id[task_id]
        dependencies = {dependency: outputs[dependency] for dependency in task.dependencies}
        call_counts[task_id] += 1
        if task.effect.kind.writes:
            safety = cast(dict[str, object], dependencies["safety_review"])
            alert = cast(dict[str, object], dependencies["multilingual_alert"])
            if safety.get("passed") is not True:
                raise FairBenchmarkInvariantError("sequential proposal reached unsafe output")
            if alert.get("external_publication_attempted") is not False:
                raise FairBenchmarkInvariantError("sequential fixture attempted publication")
            proposal_body = {
                "schema_version": "python-sequential-stormshift/v1",
                "run_id": run_id,
                "action": task.task_id,
                "resource": task.effect.resource,
                "effect_class": task.effect.kind.value,
                "idempotency_key": task.effect.idempotency_key,
                "requires_approval": task.effect.requires_approval,
                "dependency_output_digests": {
                    key: content_digest(value) for key, value in sorted(dependencies.items())
                },
                "fixture_only": True,
            }
            proposal_digest = content_digest(proposal_body)
            outputs[task_id] = {
                "effect_intent_id": f"python-sequential:{proposal_digest}",
                "effect_state": "proposed",
                "executed_externally": False,
                "approval_grant_present": False,
                "proposal_digest": proposal_digest,
            }
            continue
        profile = _highest_quality_profile(task)
        profiles.append(
            _profile_evidence(
                task_id=task_id,
                profile_name=profile.name,
                provider=profile.provider,
                duration_ms_p95=profile.duration_ms_p95,
                tokens=profile.total_tokens,
                cost_microusd=profile.cost_microusd,
                context_bytes=profile.context_bytes,
                quality=profile.quality,
                failure_probability=profile.failure_probability,
            )
        )
        worker_result = await workers.execute_task(
            TaskExecutionContext(
                run_id=run_id,
                task=task,
                profile=profile,
                attempt=1,
                dependency_outputs=dependencies,
                deadline_at_ms=envelope.deadline_ms,
                cancellation_event=CancellationSignal(),
            )
        )
        if not await workers.validate_output(task, worker_result.output):
            raise FairBenchmarkInvariantError(f"sequential output {task_id!r} did not validate")
        outputs[task_id] = worker_result.output

    if call_counts != {task_id: 1 for task_id in graph.by_id}:
        raise FairBenchmarkInvariantError("sequential control did not visit every task once")
    plan = _response_plan_from_output(outputs["response_plan"])
    validation = StormShiftValidator().validate(scenario, plan)
    comparable = {task_id: outputs[task_id] for task_id in sorted(PURE_TASK_IDS)}
    effect = cast(dict[str, object], outputs[PUBLISH_TASK_ID])
    profile_tuple = tuple(sorted(profiles, key=lambda item: item.task_id))
    return _SystemObservation(
        framework="python-stdlib",
        framework_version=platform.python_version(),
        common_validation_passed=validation.passed and validation.verify_digest(),
        common_validation_digest=validation.report_digest,
        comparable_output_digest=content_digest(comparable),
        effect_state=cast(str, effect["effect_state"]),
        external_effects_executed=0,
        checkpoint_verified=False,
        cache_enabled=False,
        admission_performed=False,
        retries_configured=False,
        model_calls_made=False,
        external_calls_made=False,
        actual_usage=ResourceTotals(),
        declared_resource_fit=None,
        profile_selection_source="highest-quality qualified static profile; metadata only",
        selected_profiles=profile_tuple,
        additional_validation_scope=None,
        additional_validation_passed=None,
        source_evidence_digest=content_digest(
            {
                "outputs": outputs,
                "validation_digest": validation.report_digest,
                "call_counts": call_counts,
            }
        ),
    )


async def _run_langgraph(run_id: str) -> _SystemObservation:
    with tempfile.TemporaryDirectory(prefix="langgraph-fair-benchmark-") as directory:
        record = await run_langgraph_stormshift_baseline(
            run_id=run_id,
            checkpoint_path=Path(directory) / "checkpoints.sqlite",
        )
    profiles = tuple(
        _profile_evidence(
            task_id=item.task_id,
            profile_name=item.profile_name,
            provider=item.provider,
            duration_ms_p95=item.duration_ms_p95,
            tokens=item.input_tokens + item.output_tokens,
            cost_microusd=item.cost_microusd,
            context_bytes=item.context_bytes,
            quality=item.quality,
            failure_probability=item.failure_probability,
        )
        for item in record.static_profiles
    )
    validation = cast(dict[str, object], record.validation)
    return _SystemObservation(
        framework=record.framework,
        framework_version=record.framework_version,
        common_validation_passed=validation.get("passed") is True,
        common_validation_digest=record.validation_digest,
        comparable_output_digest=record.comparable_output_digest,
        effect_state=record.effect_state,
        external_effects_executed=record.external_effects_executed,
        checkpoint_verified=record.checkpoint_verified,
        cache_enabled=record.cache_enabled,
        admission_performed=record.admission_performed,
        retries_configured=record.retries_configured,
        model_calls_made=record.model_calls_made,
        external_calls_made=record.external_calls_made,
        actual_usage=ResourceTotals(),
        declared_resource_fit=None,
        profile_selection_source="highest-quality qualified static profile; metadata only",
        selected_profiles=profiles,
        additional_validation_scope=None,
        additional_validation_passed=None,
        source_evidence_digest=record.record_digest,
    )


def _system_readiness(
    contract: FairBenchmarkContract,
) -> tuple[
    dict[str, Callable[[str], Awaitable[_SystemObservation]]],
    dict[str, tuple[str, str | None]],
]:
    del contract
    runners: dict[str, Callable[[str], Awaitable[_SystemObservation]]] = {
        FINITE_SYSTEM_ID: _run_finite,
        PYTHON_SYSTEM_ID: _run_python_sequential,
    }
    unavailable: dict[str, tuple[str, str | None]] = {
        PAGEAGENT_SYSTEM_ID: ("no_local_pinned_integration_or_execution", None),
    }
    if not langgraph_baseline_available():
        unavailable[LANGGRAPH_SYSTEM_ID] = ("optional_packages_not_installed", None)
        return runners, unavailable
    installed_framework = _package_version("langgraph")
    installed_checkpoint = _package_version("langgraph-checkpoint-sqlite")
    if (
        installed_framework != EXPECTED_LANGGRAPH_VERSION
        or installed_checkpoint != EXPECTED_LANGGRAPH_CHECKPOINT_VERSION
    ):
        observed = f"langgraph={installed_framework};checkpoint={installed_checkpoint}"
        unavailable[LANGGRAPH_SYSTEM_ID] = ("installed_version_pin_mismatch", observed)
        return runners, unavailable
    runners[LANGGRAPH_SYSTEM_ID] = _run_langgraph
    return runners, unavailable


def _balanced_order(
    system_ids: Sequence[str],
    seeds: Sequence[int],
    sample_index: int,
) -> tuple[str, ...]:
    if not system_ids:
        return ()
    material = content_digest({"systems": sorted(system_ids), "seeds": list(seeds)})
    randomizer = random.Random(int(material[:16], 16))
    base = sorted(system_ids)
    randomizer.shuffle(base)
    block = sample_index // len(base)
    if block % 2:
        base.reverse()
    shift = sample_index % len(base)
    return tuple(base[shift:] + base[:shift])


def classify_safety(
    *,
    outcome: str,
    effect_state: str,
    model_calls_made: bool,
    external_calls_made: bool,
    external_effects_executed: int,
    unauthorized_effects: int,
) -> tuple[bool, bool]:
    """Return ``(guardrail_passed, safe_refusal)`` from explicit raw facts."""

    if outcome not in {"passed", "refused", "error"}:
        raise ValueError("outcome must be passed, refused, or error")
    if min(external_effects_executed, unauthorized_effects) < 0:
        raise ValueError("effect counts cannot be negative")
    safe_boundary = (
        not model_calls_made
        and not external_calls_made
        and external_effects_executed == 0
        and unauthorized_effects == 0
    )
    guardrail = safe_boundary and (
        (outcome == "passed" and effect_state == "proposed")
        or (outcome == "refused" and effect_state == "none")
    )
    return guardrail, outcome == "refused" and guardrail


def _record_from_observation(
    *,
    contract: FairBenchmarkContract,
    environment: BenchmarkEnvironment,
    system: BenchmarkSystemPlan,
    phase: str,
    sample_index: int,
    seed: int | None,
    order_position: int,
    run_id: str,
    duration_ns: int,
    observation: _SystemObservation,
) -> FairBenchmarkRecord:
    conforms = (
        observation.comparable_output_digest == contract.expected_comparable_output_digest
    )
    common_validation_passed = (
        observation.common_validation_passed
        and observation.common_validation_digest
        == contract.expected_structural_validation_digest
    )
    deadline_passed = duration_ns <= stormshift_envelope().deadline_ms * 1_000_000
    guardrail, safe_refusal = classify_safety(
        outcome="passed",
        effect_state=observation.effect_state,
        model_calls_made=observation.model_calls_made,
        external_calls_made=observation.external_calls_made,
        external_effects_executed=observation.external_effects_executed,
        unauthorized_effects=observation.external_effects_executed,
    )
    slo_passed = (
        common_validation_passed
        and conforms
        and deadline_passed
        and guardrail
    )
    profiles = tuple(sorted(observation.selected_profiles, key=lambda item: item.task_id))
    fields: dict[str, object] = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "contract_digest": contract.contract_digest,
        "environment_digest": environment.environment_digest,
        "system_id": system.system_id,
        "system_label": system.display_name,
        "framework": observation.framework,
        "framework_version": observation.framework_version,
        "phase": phase,
        "sample_index": sample_index,
        "seed": seed,
        "order_position": order_position,
        "run_id": run_id,
        "execution_status": "executed-local",
        "outcome": "passed",
        "duration_ns": duration_ns,
        "timer": contract.timer,
        "workload_id": contract.workload_id,
        "graph_digest": contract.graph_digest,
        "envelope_digest": contract.envelope_digest,
        "fixture_digest": contract.fixture_digest,
        "common_validation_passed": common_validation_passed,
        "common_validation_digest": observation.common_validation_digest,
        "expected_comparable_output_digest": contract.expected_comparable_output_digest,
        "comparable_output_digest": observation.comparable_output_digest,
        "comparable_output_conforms": conforms,
        "effect_state": observation.effect_state,
        "external_effects_executed": observation.external_effects_executed,
        "unauthorized_effects": observation.external_effects_executed,
        "guardrail_passed": guardrail,
        "safe_refusal": safe_refusal,
        "deadline_passed": deadline_passed,
        "slo_passed": slo_passed,
        "checkpoint_verified": observation.checkpoint_verified,
        "cache_enabled": observation.cache_enabled,
        "admission_performed": observation.admission_performed,
        "retries_configured": observation.retries_configured,
        "model_calls_made": observation.model_calls_made,
        "external_calls_made": observation.external_calls_made,
        "actual_usage": observation.actual_usage,
        "declared_profile_totals": _profile_totals(profiles),
        "declared_resource_fit": observation.declared_resource_fit,
        "profile_selection_source": observation.profile_selection_source,
        "selected_profiles": profiles,
        "selected_profiles_digest": content_digest(profiles),
        "additional_validation_scope": observation.additional_validation_scope,
        "additional_validation_passed": observation.additional_validation_passed,
        "source_evidence_digest": observation.source_evidence_digest,
        "refusal_code": None,
        "error_type": None,
    }
    fields["record_digest"] = content_digest(fields)
    record = FairBenchmarkRecord(**fields)  # type: ignore[arg-type]
    record.validate()
    return record


def _failure_record(
    *,
    contract: FairBenchmarkContract,
    environment: BenchmarkEnvironment,
    system: BenchmarkSystemPlan,
    phase: str,
    sample_index: int,
    seed: int | None,
    order_position: int,
    run_id: str,
    duration_ns: int,
    error: BaseException,
) -> FairBenchmarkRecord:
    refused = isinstance(error, AdmissionRefused)
    outcome = "refused" if refused else "error"
    effect_state = "none" if refused else "unknown"
    guardrail, safe_refusal = classify_safety(
        outcome=outcome,
        effect_state=effect_state,
        model_calls_made=False,
        external_calls_made=False,
        external_effects_executed=0,
        unauthorized_effects=0,
    )
    fields: dict[str, object] = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "contract_digest": contract.contract_digest,
        "environment_digest": environment.environment_digest,
        "system_id": system.system_id,
        "system_label": system.display_name,
        "framework": system.framework,
        "framework_version": system.version_pin or "unversioned",
        "phase": phase,
        "sample_index": sample_index,
        "seed": seed,
        "order_position": order_position,
        "run_id": run_id,
        "execution_status": "executed-local",
        "outcome": outcome,
        "duration_ns": duration_ns,
        "timer": contract.timer,
        "workload_id": contract.workload_id,
        "graph_digest": contract.graph_digest,
        "envelope_digest": contract.envelope_digest,
        "fixture_digest": contract.fixture_digest,
        "common_validation_passed": None,
        "common_validation_digest": None,
        "expected_comparable_output_digest": contract.expected_comparable_output_digest,
        "comparable_output_digest": None,
        "comparable_output_conforms": None,
        "effect_state": effect_state,
        "external_effects_executed": 0,
        "unauthorized_effects": 0,
        "guardrail_passed": guardrail,
        "safe_refusal": safe_refusal,
        "deadline_passed": duration_ns <= stormshift_envelope().deadline_ms * 1_000_000,
        "slo_passed": False,
        "checkpoint_verified": None,
        "cache_enabled": None,
        "admission_performed": None,
        "retries_configured": None,
        "model_calls_made": False,
        "external_calls_made": False,
        "actual_usage": ResourceTotals(),
        "declared_profile_totals": None,
        "declared_resource_fit": None,
        "profile_selection_source": None,
        "selected_profiles": (),
        "selected_profiles_digest": None,
        "additional_validation_scope": None,
        "additional_validation_passed": None,
        "source_evidence_digest": None,
        "refusal_code": type(error).__name__ if refused else None,
        "error_type": None if refused else type(error).__name__,
    }
    fields["record_digest"] = content_digest(fields)
    record = FairBenchmarkRecord(**fields)  # type: ignore[arg-type]
    record.validate()
    return record


async def _invoke_one(
    *,
    runner: Callable[[str], Awaitable[_SystemObservation]],
    contract: FairBenchmarkContract,
    environment: BenchmarkEnvironment,
    system: BenchmarkSystemPlan,
    phase: str,
    sample_index: int,
    seed: int | None,
    order_position: int,
) -> FairBenchmarkRecord:
    seed_label = "warmup" if seed is None else str(seed)
    run_id = (
        f"fair-{contract.contract_digest[:12]}-{phase}-{sample_index}-"
        f"{seed_label}-{system.system_id}"
    )
    started = time.perf_counter_ns()
    try:
        observation = await runner(run_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        duration_ns = time.perf_counter_ns() - started
        return _failure_record(
            contract=contract,
            environment=environment,
            system=system,
            phase=phase,
            sample_index=sample_index,
            seed=seed,
            order_position=order_position,
            run_id=run_id,
            duration_ns=duration_ns,
            error=error,
        )
    duration_ns = time.perf_counter_ns() - started
    return _record_from_observation(
        contract=contract,
        environment=environment,
        system=system,
        phase=phase,
        sample_index=sample_index,
        seed=seed,
        order_position=order_position,
        run_id=run_id,
        duration_ns=duration_ns,
        observation=cast(_SystemObservation, observation),
    )


def _status(
    *,
    system: BenchmarkSystemPlan,
    execution_status: str,
    framework_version: str | None,
    measured_records: int,
    warmup_records: int,
    metrics_eligible: bool,
    reason_code: str,
) -> BenchmarkSystemStatus:
    fields: dict[str, object] = {
        "system_id": system.system_id,
        "display_name": system.display_name,
        "framework": system.framework,
        "execution_status": execution_status,
        "framework_version": framework_version,
        "version_pin": system.version_pin,
        "measured_records": measured_records,
        "warmup_records": warmup_records,
        "metrics_eligible": metrics_eligible,
        "reason_code": reason_code,
    }
    fields["status_digest"] = content_digest(fields)
    return BenchmarkSystemStatus(**fields)  # type: ignore[arg-type]


def _percentile(values: Sequence[float | int], quantile: float) -> float:
    if not values or not 0 <= quantile <= 1:
        raise ValueError("percentile requires values and a quantile in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _bootstrap_interval(
    values: Sequence[int],
    statistic: Callable[[Sequence[int]], float],
    *,
    samples: int,
    label: str,
) -> Interval95:
    if not values:
        raise ValueError("bootstrap interval requires values")
    seed = int(hashlib.sha256(label.encode("utf-8")).hexdigest()[:16], 16)
    randomizer = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        sample = [values[randomizer.randrange(len(values))] for _ in values]
        estimates.append(statistic(sample))
    return Interval95(
        lower=round(_percentile(estimates, 0.025), 6),
        upper=round(_percentile(estimates, 0.975), 6),
    )


def _duration_summary(
    durations: Sequence[int], contract: FairBenchmarkContract, system_id: str
) -> DurationSummary | None:
    if not durations:
        return None
    return DurationSummary(
        population="measured records with outcome=passed and common SLO passed",
        count=len(durations),
        p50_ns=round(_percentile(durations, 0.5), 6),
        p95_ns=round(_percentile(durations, 0.95), 6),
        bootstrap_p50_95=_bootstrap_interval(
            durations,
            lambda sample: _percentile(sample, 0.5),
            samples=contract.bootstrap_samples,
            label=f"{contract.contract_digest}:{system_id}:duration:p50",
        ),
        bootstrap_p95_95=_bootstrap_interval(
            durations,
            lambda sample: _percentile(sample, 0.95),
            samples=contract.bootstrap_samples,
            label=f"{contract.contract_digest}:{system_id}:duration:p95",
        ),
    )


def _system_summary(
    system_id: str,
    records: Sequence[FairBenchmarkRecord],
    contract: FairBenchmarkContract,
) -> SystemSummary:
    total = len(records)
    if total == 0:
        raise FairBenchmarkInvariantError("executed systems require measured records")
    passes = sum(record.slo_passed for record in records)
    guardrails = sum(record.guardrail_passed for record in records)
    pass_interval = wilson_interval(passes, total)
    guardrail_interval = wilson_interval(guardrails, total)
    durations = [record.duration_ns for record in records if record.slo_passed]
    return SystemSummary(
        system_id=system_id,
        measured_runs=total,
        slo_passes=passes,
        refusals=sum(record.outcome == "refused" for record in records),
        safe_refusals=sum(record.safe_refusal for record in records),
        errors=sum(record.outcome == "error" for record in records),
        unsafe_effect_runs=sum(record.unauthorized_effects > 0 for record in records),
        guardrail_passes=guardrails,
        pass_rate=round(passes / total, 12),
        pass_rate_wilson_95=Interval95(
            lower=round(pass_interval[0], 12), upper=round(pass_interval[1], 12)
        ),
        guardrail_rate=round(guardrails / total, 12),
        guardrail_rate_wilson_95=Interval95(
            lower=round(guardrail_interval[0], 12),
            upper=round(guardrail_interval[1], 12),
        ),
        duration=_duration_summary(durations, contract, system_id),
    )


def _paired_comparison(
    baseline_system_id: str,
    measured: Sequence[FairBenchmarkRecord],
    contract: FairBenchmarkContract,
) -> PairedDurationComparison:
    finite = {
        record.seed: record
        for record in measured
        if record.system_id == FINITE_SYSTEM_ID and record.seed is not None
    }
    baseline = {
        record.seed: record
        for record in measured
        if record.system_id == baseline_system_id and record.seed is not None
    }
    deltas = [
        finite[seed].duration_ns - baseline[seed].duration_ns
        for seed in contract.measured_seeds
        if seed in finite
        and seed in baseline
        and finite[seed].slo_passed
        and baseline[seed].slo_passed
    ]
    excluded = len(contract.measured_seeds) - len(deltas)
    if not deltas:
        return PairedDurationComparison(
            baseline_system_id=baseline_system_id,
            delta_definition="FINITE duration_ns minus baseline duration_ns; negative is lower",
            eligible_pairs=0,
            excluded_pairs=excluded,
            mean_delta_ns=None,
            p50_delta_ns=None,
            bootstrap_mean_delta_95=None,
        )
    def mean(sample: Sequence[int]) -> float:
        return sum(sample) / len(sample)
    return PairedDurationComparison(
        baseline_system_id=baseline_system_id,
        delta_definition="FINITE duration_ns minus baseline duration_ns; negative is lower",
        eligible_pairs=len(deltas),
        excluded_pairs=excluded,
        mean_delta_ns=round(mean(deltas), 6),
        p50_delta_ns=round(_percentile(deltas, 0.5), 6),
        bootstrap_mean_delta_95=_bootstrap_interval(
            deltas,
            mean,
            samples=contract.bootstrap_samples,
            label=f"{contract.contract_digest}:finite-vs-{baseline_system_id}:paired-mean",
        ),
    )


def summarize_fair_benchmark(
    contract: FairBenchmarkContract,
    environment: BenchmarkEnvironment,
    statuses: Sequence[BenchmarkSystemStatus],
    records: Sequence[FairBenchmarkRecord],
) -> FairBenchmarkReport:
    """Deterministically regenerate the report from verified raw evidence."""

    contract.validate()
    if not environment.verify_digest():
        raise FairBenchmarkInvariantError("cannot summarize an invalid environment record")
    status_tuple = tuple(sorted(statuses, key=lambda item: item.system_id))
    if {status.system_id for status in status_tuple} != {
        system.system_id for system in contract.systems
    }:
        raise FairBenchmarkInvariantError("system status set differs from the contract")
    if any(not status.verify_digest() for status in status_tuple):
        raise FairBenchmarkInvariantError("system status digest mismatch")
    record_tuple = tuple(
        sorted(
            records,
            key=lambda record: (
                0 if record.phase == "warmup" else 1,
                record.sample_index,
                record.order_position,
                record.system_id,
            ),
        )
    )
    for record in record_tuple:
        record.validate()
        if record.contract_digest != contract.contract_digest:
            raise FairBenchmarkInvariantError("record contract binding mismatch")
        if record.environment_digest != environment.environment_digest:
            raise FairBenchmarkInvariantError("record environment binding mismatch")
        if (
            record.workload_id != contract.workload_id
            or record.graph_digest != contract.graph_digest
            or record.envelope_digest != contract.envelope_digest
            or record.fixture_digest != contract.fixture_digest
            or record.expected_comparable_output_digest
            != contract.expected_comparable_output_digest
        ):
            raise FairBenchmarkInvariantError("record workload binding mismatch")
        if record.system_id == PAGEAGENT_SYSTEM_ID:
            raise FairBenchmarkInvariantError("PageAgent raw metrics are forbidden without execution")

    measured = tuple(record for record in record_tuple if record.phase == "measured")
    warmups = tuple(record for record in record_tuple if record.phase == "warmup")
    summaries: list[SystemSummary] = []
    executed_ids: list[str] = []
    unexecuted_ids: list[str] = []
    for status in status_tuple:
        system_records = [record for record in measured if record.system_id == status.system_id]
        system_warmups = [record for record in warmups if record.system_id == status.system_id]
        if status.execution_status == "executed-local":
            executed_ids.append(status.system_id)
            if not status.metrics_eligible:
                raise FairBenchmarkInvariantError("executed system was made metric-ineligible")
            if len(system_records) != len(contract.measured_seeds):
                raise FairBenchmarkInvariantError("executed system lacks a complete measured design")
            if len(system_warmups) != contract.warmup_count:
                raise FairBenchmarkInvariantError("executed system lacks registered warmups")
            if {record.seed for record in system_records} != set(contract.measured_seeds):
                raise FairBenchmarkInvariantError("executed system lacks paired seeds")
            if (
                status.measured_records != len(system_records)
                or status.warmup_records != len(system_warmups)
            ):
                raise FairBenchmarkInvariantError("status record counts are incorrect")
            summaries.append(_system_summary(status.system_id, system_records, contract))
        elif status.execution_status == "not-executed":
            unexecuted_ids.append(status.system_id)
            if status.metrics_eligible or system_records or system_warmups:
                raise FairBenchmarkInvariantError("unexecuted systems cannot carry metrics")
        else:
            raise FairBenchmarkInvariantError("unknown system execution status")

    comparisons = tuple(
        _paired_comparison(system_id, measured, contract)
        for system_id in executed_ids
        if system_id != FINITE_SYSTEM_ID
    )
    passed_records = [record for record in measured if record.outcome == "passed"]
    fields: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "contract_digest": contract.contract_digest,
        "environment_digest": environment.environment_digest,
        "raw_record_set_digest": content_digest(record_tuple),
        "warmup_records": len(warmups),
        "measured_records": len(measured),
        "warmups_excluded_from_statistics": True,
        "system_statuses": status_tuple,
        "system_summaries": tuple(sorted(summaries, key=lambda item: item.system_id)),
        "paired_duration_comparisons": comparisons,
        "unexecuted_system_ids": tuple(sorted(unexecuted_ids)),
        "all_passed_outputs_match_registered_fixture": bool(passed_records)
        and all(record.comparable_output_conforms is True for record in passed_records),
        "statistical_scope": (
            "Wilson 95% intervals cover measured pass/guardrail proportions; deterministic "
            "nonparametric bootstrap intervals cover successful-run latency and paired local "
            "duration deltas. Warmups, refusals, errors, and unexecuted systems are excluded "
            "from latency distributions and paired deltas, with counts retained."
        ),
        "interpretation": (
            "Every metric-bearing row is backed by an actual local invocation on the same digest-bound fixture.",
            "Negative paired deltas mean FINITE used less local lifecycle wall time for that pair.",
            "Timing differences include each framework's required local persistence and control-plane work.",
            "The fixture makes zero model/provider/network calls, so results cannot establish production speed or quality.",
            "Different declared profile-selection scopes make token/cost/context estimates non-comparable across systems.",
            "No ranking, winner, or PageAgent performance claim is produced.",
        ),
    }
    fields["report_digest"] = content_digest(fields)
    report = FairBenchmarkReport(**fields)  # type: ignore[arg-type]
    if not report.verify_digest():
        raise FairBenchmarkInvariantError("generated report digest failed")
    return report


async def run_fair_benchmark(
    contract: FairBenchmarkContract | None = None,
    *,
    output_directory: str | Path | None = None,
) -> FairBenchmarkEvidence:
    """Run the registered local systems and return a fully verified bundle."""

    contract = contract or build_fair_benchmark_contract()
    contract.validate()
    _assert_current_workload(contract)
    environment = capture_benchmark_environment()
    runners, unavailable = _system_readiness(contract)
    plans = {system.system_id: system for system in contract.systems}
    runnable_ids = tuple(system.system_id for system in contract.systems if system.system_id in runners)
    records: list[FairBenchmarkRecord] = []

    for warmup_index in range(contract.warmup_count):
        order = _balanced_order(runnable_ids, contract.measured_seeds, warmup_index)
        for position, system_id in enumerate(order):
            records.append(
                await _invoke_one(
                    runner=runners[system_id],
                    contract=contract,
                    environment=environment,
                    system=plans[system_id],
                    phase="warmup",
                    sample_index=warmup_index,
                    seed=None,
                    order_position=position,
                )
            )

    for sample_index, seed in enumerate(contract.measured_seeds):
        order = _balanced_order(runnable_ids, contract.measured_seeds, sample_index)
        for position, system_id in enumerate(order):
            records.append(
                await _invoke_one(
                    runner=runners[system_id],
                    contract=contract,
                    environment=environment,
                    system=plans[system_id],
                    phase="measured",
                    sample_index=sample_index,
                    seed=seed,
                    order_position=position,
                )
            )

    statuses: list[BenchmarkSystemStatus] = []
    for system in contract.systems:
        system_records = [record for record in records if record.system_id == system.system_id]
        if system.system_id in runners:
            version = next(
                (record.framework_version for record in system_records if record.outcome == "passed"),
                system.version_pin,
            )
            statuses.append(
                _status(
                    system=system,
                    execution_status="executed-local",
                    framework_version=version,
                    measured_records=sum(record.phase == "measured" for record in system_records),
                    warmup_records=sum(record.phase == "warmup" for record in system_records),
                    metrics_eligible=True,
                    reason_code="actual_local_runner_invoked",
                )
            )
        else:
            reason, observed_version = unavailable[system.system_id]
            statuses.append(
                _status(
                    system=system,
                    execution_status="not-executed",
                    framework_version=observed_version,
                    measured_records=0,
                    warmup_records=0,
                    metrics_eligible=False,
                    reason_code=reason,
                )
            )

    report = summarize_fair_benchmark(contract, environment, statuses, records)
    fields: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "contract": contract,
        "environment": environment,
        "records": tuple(records),
        "report": report,
    }
    fields["evidence_digest"] = content_digest(fields)
    evidence = FairBenchmarkEvidence(**fields)  # type: ignore[arg-type]
    evidence.verify()
    if output_directory is not None:
        write_fair_benchmark_evidence(evidence, output_directory)
    return evidence


def _write_canonical(path: Path, value: object) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def write_fair_benchmark_evidence(
    evidence: FairBenchmarkEvidence,
    output_directory: str | Path,
) -> EvidenceFileManifest:
    """Write byte-stable files for an already captured evidence bundle."""

    evidence.verify()
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    records = tuple(
        sorted(
            evidence.records,
            key=lambda record: (
                0 if record.phase == "warmup" else 1,
                record.sample_index,
                record.order_position,
                record.system_id,
            ),
        )
    )
    _write_canonical(output / "contract.json", evidence.contract)
    _write_canonical(output / "environment.json", evidence.environment)
    _write_canonical(output / "report.json", evidence.report)
    _write_canonical(output / "evidence.json", evidence)
    raw_path = output / "raw-records.jsonl"
    raw_path.write_text(
        "".join(canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    names = (
        "contract.json",
        "environment.json",
        "evidence.json",
        "raw-records.jsonl",
        "report.json",
    )
    files = tuple(
        (name, hashlib.sha256((output / name).read_bytes()).hexdigest()) for name in names
    )
    manifest_fields: dict[str, object] = {
        "schema_version": WRITER_MANIFEST_SCHEMA_VERSION,
        # Only the leaf is recorded; absolute user paths are deliberately excluded.
        "output_directory": ".",
        "files": files,
        "raw_record_count": len(records),
    }
    manifest_fields["manifest_digest"] = content_digest(manifest_fields)
    manifest = EvidenceFileManifest(**manifest_fields)  # type: ignore[arg-type]
    _write_canonical(output / "manifest.json", manifest)
    return manifest


__all__ = [
    "BenchmarkEnvironment",
    "BenchmarkSystemPlan",
    "BenchmarkSystemStatus",
    "DEFAULT_MEASURED_SEEDS",
    "DurationSummary",
    "EvidenceFileManifest",
    "FairBenchmarkContract",
    "FairBenchmarkEvidence",
    "FairBenchmarkInvariantError",
    "FairBenchmarkRecord",
    "FairBenchmarkReport",
    "Interval95",
    "LANGGRAPH_SYSTEM_ID",
    "PAGEAGENT_SYSTEM_ID",
    "PairedDurationComparison",
    "ProfileSelectionEvidence",
    "PYTHON_SYSTEM_ID",
    "ResourceTotals",
    "SystemSummary",
    "FINITE_SYSTEM_ID",
    "build_fair_benchmark_contract",
    "capture_benchmark_environment",
    "classify_safety",
    "run_fair_benchmark",
    "summarize_fair_benchmark",
    "write_fair_benchmark_evidence",
]
