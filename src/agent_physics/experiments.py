"""Paired deterministic control and fault experiments around the scheduling kernel.

The transformations in this module are pre-dispatch simulations over declared task
profiles and run envelopes. They do not inject faults into a provider, model, network,
or worker process. Raw records and summaries retain that scope explicitly.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, fields, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .benchmark import benchmark_envelope, generated_scenario
from .contracts import BackendProfile, RunEnvelope
from .graph import ExecutionGraph
from .scheduler import SchedulePolicy, Scheduler
from .serialization import content_digest

MEASUREMENT_KIND = "deterministic-simulation"
CLAIM_STATUS = "descriptive-only"
REVISION_PROVENANCE = "caller-supplied-unverified"
DEFAULT_SCENARIO = "mixed"
DEFAULT_PAIRED_SEEDS = tuple(range(30))
BOOTSTRAP_RESAMPLING_SEED = 2_026_072_031
BOOTSTRAP_SAMPLES = 1_000
EXPERIMENT_SCHEMA_VERSION = "finite-fault-experiment/v2"
SUMMARY_SCHEMA_VERSION = "finite-fault-summary/v2"
NOMINAL_CONTROL_ID = "nominal-control"
BINDING_BUDGET_CONDITION_ID = "uniform-budget-cut-retain-1-of-75"
EXPERIMENT_POLICIES = (
    SchedulePolicy.ADAPTIVE,
    SchedulePolicy.STATIC_PARALLEL,
    SchedulePolicy.SEQUENTIAL,
)
POLICY_ROLES = {
    SchedulePolicy.ADAPTIVE: "paired-analysis-baseline",
    SchedulePolicy.STATIC_PARALLEL: "development-reference",
    SchedulePolicy.SEQUENTIAL: "development-reference",
}


class SimulatedFaultKind(str, Enum):
    """Control and fault conditions supported by the deterministic model."""

    NOMINAL_CONTROL = "nominal-control"
    DURATION_MULTIPLIER = "duration-multiplier"
    PROFILE_OUTAGE = "profile-outage"
    PROVIDER_CAPACITY_REDUCTION = "provider-capacity-reduction"
    BUDGET_CUT = "budget-cut"


@dataclass(frozen=True, slots=True)
class SimulatedFault:
    """A registered deterministic control or pre-dispatch fault transformation."""

    fault_id: str
    kind: SimulatedFaultKind
    description: str
    provider: str | None = None
    profile_name: str | None = None
    numerator: int = 1
    denominator: int = 1
    capacity: int | None = None

    def validate(self) -> None:
        if not self.fault_id or not self.description:
            raise ValueError("simulated faults require an ID and description")
        if self.denominator <= 0 or self.numerator < 0:
            raise ValueError(f"fault {self.fault_id!r} has an invalid ratio")
        if self.kind is SimulatedFaultKind.NOMINAL_CONTROL:
            if (
                self.provider is not None
                or self.profile_name is not None
                or self.capacity is not None
                or self.numerator != 1
                or self.denominator != 1
            ):
                raise ValueError("nominal controls cannot transform graph or envelope fields")
        elif self.kind is SimulatedFaultKind.DURATION_MULTIPLIER:
            if self.numerator <= self.denominator:
                raise ValueError("duration multipliers must increase modeled duration")
            if not self.provider and not self.profile_name:
                raise ValueError("duration multipliers require a provider or profile selector")
        elif self.kind is SimulatedFaultKind.PROFILE_OUTAGE:
            if not self.provider and not self.profile_name:
                raise ValueError("profile outages require a provider or profile selector")
        elif self.kind is SimulatedFaultKind.PROVIDER_CAPACITY_REDUCTION:
            if not self.provider or self.capacity is None or self.capacity <= 0:
                raise ValueError("provider capacity reductions require a provider and capacity")
        elif self.kind is SimulatedFaultKind.BUDGET_CUT:
            if not 0 <= self.numerator < self.denominator:
                raise ValueError("budget cuts require a ratio in [0, 1)")


REGISTERED_SIMULATED_FAULTS = (
    SimulatedFault(
        fault_id=NOMINAL_CONTROL_ID,
        kind=SimulatedFaultKind.NOMINAL_CONTROL,
        description="Run the declared graph and envelope without a fault transformation.",
    ),
    SimulatedFault(
        fault_id="duration-provider-b-3x",
        kind=SimulatedFaultKind.DURATION_MULTIPLIER,
        description="Multiply declared provider-b p50 and p95 duration by three.",
        provider="sim-provider-b",
        numerator=3,
        denominator=1,
    ),
    SimulatedFault(
        fault_id="profile-economy-outage",
        kind=SimulatedFaultKind.PROFILE_OUTAGE,
        description="Remove the declared economy profile before scheduling.",
        provider="sim-provider-b",
        profile_name="sim-economy",
    ),
    SimulatedFault(
        fault_id="provider-b-capacity-one",
        kind=SimulatedFaultKind.PROVIDER_CAPACITY_REDUCTION,
        description="Reduce declared provider-b concurrency from two to one.",
        provider="sim-provider-b",
        capacity=1,
    ),
    SimulatedFault(
        fault_id=BINDING_BUDGET_CONDITION_ID,
        kind=SimulatedFaultKind.BUDGET_CUT,
        description=(
            "Uniformly retain one seventy-fifth of declared token, cost, and context "
            "budgets for every frozen seed."
        ),
        numerator=1,
        denominator=75,
    ),
)


@dataclass(frozen=True, slots=True)
class AppliedSimulatedFault:
    graph: ExecutionGraph
    envelope: RunEnvelope
    affected_items: tuple[str, ...]
    injection_phase: str = "pre-dispatch"


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """One raw policy result in the complete paired deterministic design."""

    schema_version: str
    measurement_kind: str
    claim_status: str
    revision: str
    revision_provenance: str
    experiment_config_digest: str
    record_id: str
    pair_id: str
    scenario: str
    seed: int
    policy: str
    policy_role: str
    fault_id: str
    fault_kind: str
    condition_role: str
    fault_execution_status: str
    fault_injection_phase: str
    affected_items: tuple[str, ...]
    prefault_graph_digest: str
    prefault_envelope_digest: str
    prefault_config_digest: str
    faulted_graph_digest: str
    faulted_envelope_digest: str
    faulted_config_digest: str
    success: bool
    modeled_schedule_position_ms: int
    modeled_success_makespan_ms: int | None
    modeled_time_to_failure_ms: int | None
    model_bound_ms: int
    model_bound_respected: bool | None
    tokens: int
    cost_microusd: int
    context_bytes: int
    modeled_success_probability: float
    selected_backends: tuple[tuple[str, str, str], ...]
    skipped: tuple[str, ...]
    failure_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _profile_matches(profile: BackendProfile, fault: SimulatedFault) -> bool:
    return (fault.provider is None or profile.provider == fault.provider) and (
        fault.profile_name is None or profile.name == fault.profile_name
    )


def _scaled_duration(value: int, numerator: int, denominator: int) -> int:
    return math.ceil(value * numerator / denominator)


def apply_simulated_fault(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    fault: SimulatedFault,
) -> AppliedSimulatedFault:
    """Apply one pure pre-dispatch transformation without mutating kernel objects."""

    graph.validate()
    fault.validate()

    if fault.kind is SimulatedFaultKind.NOMINAL_CONTROL:
        return AppliedSimulatedFault(
            graph,
            envelope,
            (),
            injection_phase="none-control",
        )

    if fault.kind is SimulatedFaultKind.DURATION_MULTIPLIER:
        affected: list[str] = []
        transformed_tasks = []
        for task in graph.tasks:
            profiles = []
            for profile in task.profiles:
                if _profile_matches(profile, fault):
                    affected.append(f"{task.task_id}:{profile.provider}:{profile.name}")
                    profile = replace(
                        profile,
                        duration_ms_p50=_scaled_duration(
                            profile.duration_ms_p50,
                            fault.numerator,
                            fault.denominator,
                        ),
                        duration_ms_p95=_scaled_duration(
                            profile.duration_ms_p95,
                            fault.numerator,
                            fault.denominator,
                        ),
                    )
                profiles.append(profile)
            transformed_tasks.append(replace(task, profiles=tuple(profiles)))
        if not affected:
            raise ValueError(f"fault {fault.fault_id!r} matched no backend profiles")
        return AppliedSimulatedFault(
            ExecutionGraph.from_tasks(transformed_tasks),
            envelope,
            tuple(sorted(affected)),
        )

    if fault.kind is SimulatedFaultKind.PROFILE_OUTAGE:
        affected = []
        transformed_tasks = []
        for task in graph.tasks:
            retained = []
            for profile in task.profiles:
                if _profile_matches(profile, fault):
                    affected.append(f"{task.task_id}:{profile.provider}:{profile.name}")
                else:
                    retained.append(profile)
            if not retained:
                raise ValueError(
                    f"fault {fault.fault_id!r} removes every profile from task {task.task_id!r}; "
                    "the current graph contract cannot encode an empty candidate set"
                )
            transformed_tasks.append(replace(task, profiles=tuple(retained)))
        if not affected:
            raise ValueError(f"fault {fault.fault_id!r} matched no backend profiles")
        return AppliedSimulatedFault(
            ExecutionGraph.from_tasks(transformed_tasks),
            envelope,
            tuple(sorted(affected)),
        )

    if fault.kind is SimulatedFaultKind.PROVIDER_CAPACITY_REDUCTION:
        assert fault.provider is not None and fault.capacity is not None
        original_capacity = envelope.provider_limit(fault.provider)
        if fault.capacity >= original_capacity:
            raise ValueError(
                f"fault {fault.fault_id!r} must reduce provider capacity below "
                f"{original_capacity}"
            )
        limits = dict(envelope.provider_limits)
        limits[fault.provider] = fault.capacity
        transformed_envelope = replace(
            envelope,
            provider_limits=tuple(sorted(limits.items())),
        )
        return AppliedSimulatedFault(
            graph,
            transformed_envelope,
            (f"{fault.provider}:{original_capacity}->{fault.capacity}",),
        )

    if fault.kind is SimulatedFaultKind.BUDGET_CUT:
        transformed_envelope = replace(
            envelope,
            max_tokens=envelope.max_tokens * fault.numerator // fault.denominator,
            max_cost_microusd=(
                envelope.max_cost_microusd * fault.numerator // fault.denominator
            ),
            max_context_bytes=(
                envelope.max_context_bytes * fault.numerator // fault.denominator
            ),
        )
        return AppliedSimulatedFault(
            graph,
            transformed_envelope,
            (
                f"max_tokens:{envelope.max_tokens}->{transformed_envelope.max_tokens}",
                "max_cost_microusd:"
                f"{envelope.max_cost_microusd}->{transformed_envelope.max_cost_microusd}",
                "max_context_bytes:"
                f"{envelope.max_context_bytes}->{transformed_envelope.max_context_bytes}",
            ),
        )

    raise AssertionError(f"unhandled simulated fault kind {fault.kind!r}")


def _experiment_config_digest(scenario: str) -> str:
    return content_digest(
        {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "scenario": scenario,
            "measurement_kind": MEASUREMENT_KIND,
            "claim_status": CLAIM_STATUS,
            "revision_provenance": REVISION_PROVENANCE,
            "seeds": DEFAULT_PAIRED_SEEDS,
            "policies": tuple(policy.value for policy in EXPERIMENT_POLICIES),
            "policy_roles": tuple(
                (policy.value, POLICY_ROLES[policy]) for policy in EXPERIMENT_POLICIES
            ),
            "faults": REGISTERED_SIMULATED_FAULTS,
            "envelope": benchmark_envelope(),
        }
    )


def experiment_record_id(record: ExperimentRecord) -> str:
    """Return the content address for every field except the address itself."""

    payload = record.as_dict()
    del payload["record_id"]
    return content_digest({"record": payload})


def _condition_role(fault: SimulatedFault) -> str:
    if fault.kind is SimulatedFaultKind.NOMINAL_CONTROL:
        return "nominal-control"
    return "simulated-fault"


def _fault_execution_status(fault: SimulatedFault) -> str:
    if fault.kind is SimulatedFaultKind.NOMINAL_CONTROL:
        return "not-injected-control"
    return "executed-simulated"


def _generate_expected_records(
    *,
    revision: str,
    scenario: str,
) -> tuple[ExperimentRecord, ...]:
    scheduler = Scheduler()
    base_envelope = benchmark_envelope()
    experiment_digest = _experiment_config_digest(scenario)
    records: list[ExperimentRecord] = []

    for seed in DEFAULT_PAIRED_SEEDS:
        base_graph = generated_scenario(scenario, seed)
        prefault_graph_digest = content_digest(base_graph)
        prefault_envelope_digest = content_digest(base_envelope)
        prefault_config_digest = content_digest(
            {
                "graph": prefault_graph_digest,
                "envelope": prefault_envelope_digest,
                "scenario": scenario,
                "seed": seed,
            }
        )

        for fault in REGISTERED_SIMULATED_FAULTS:
            applied = apply_simulated_fault(base_graph, base_envelope, fault)
            faulted_graph_digest = content_digest(applied.graph)
            faulted_envelope_digest = content_digest(applied.envelope)
            faulted_config_digest = content_digest(
                {
                    "fault": fault,
                    "graph": faulted_graph_digest,
                    "envelope": faulted_envelope_digest,
                    "measurement_kind": MEASUREMENT_KIND,
                }
            )
            pair_id = content_digest(
                {
                    "experiment": experiment_digest,
                    "fault": fault.fault_id,
                    "prefault_config": prefault_config_digest,
                    "seed": seed,
                }
            )

            for policy in EXPERIMENT_POLICIES:
                result = scheduler.schedule(applied.graph, applied.envelope, policy)
                model_bound_respected = (
                    result.model_bound_ms <= result.makespan_ms
                    if result.success
                    else None
                )
                record = ExperimentRecord(
                    schema_version=EXPERIMENT_SCHEMA_VERSION,
                    measurement_kind=MEASUREMENT_KIND,
                    claim_status=CLAIM_STATUS,
                    revision=revision,
                    revision_provenance=REVISION_PROVENANCE,
                    experiment_config_digest=experiment_digest,
                    record_id="",
                    pair_id=pair_id,
                    scenario=scenario,
                    seed=seed,
                    policy=policy.value,
                    policy_role=POLICY_ROLES[policy],
                    fault_id=fault.fault_id,
                    fault_kind=fault.kind.value,
                    condition_role=_condition_role(fault),
                    fault_execution_status=_fault_execution_status(fault),
                    fault_injection_phase=applied.injection_phase,
                    affected_items=applied.affected_items,
                    prefault_graph_digest=prefault_graph_digest,
                    prefault_envelope_digest=prefault_envelope_digest,
                    prefault_config_digest=prefault_config_digest,
                    faulted_graph_digest=faulted_graph_digest,
                    faulted_envelope_digest=faulted_envelope_digest,
                    faulted_config_digest=faulted_config_digest,
                    success=result.success,
                    modeled_schedule_position_ms=result.makespan_ms,
                    modeled_success_makespan_ms=(
                        result.makespan_ms if result.success else None
                    ),
                    modeled_time_to_failure_ms=(
                        None if result.success else result.makespan_ms
                    ),
                    model_bound_ms=result.model_bound_ms,
                    model_bound_respected=model_bound_respected,
                    tokens=result.total_tokens,
                    cost_microusd=result.total_cost_microusd,
                    context_bytes=result.total_context_bytes,
                    modeled_success_probability=result.modeled_success_probability,
                    selected_backends=tuple(
                        (entry.task_id, entry.provider, entry.backend)
                        for entry in result.entries
                    ),
                    skipped=result.skipped,
                    failure_reason=result.failure_reason,
                )
                records.append(replace(record, record_id=experiment_record_id(record)))

    return tuple(records)


def run_registered_experiments(
    *,
    revision: str,
    scenario: str = DEFAULT_SCENARIO,
) -> tuple[ExperimentRecord, ...]:
    """Execute one control and four faults over 30 paired seeds and three policies."""

    if not revision.strip():
        raise ValueError("experiment revision cannot be empty")
    if scenario != DEFAULT_SCENARIO:
        raise ValueError(
            f"registered experiment scenario is frozen to {DEFAULT_SCENARIO!r}"
        )

    completed = _generate_expected_records(revision=revision, scenario=scenario)
    validate_complete_design(completed)
    return completed


def validate_complete_design(records: Iterable[ExperimentRecord]) -> None:
    """Reject incomplete, tampered, unpaired, or non-reproducible raw designs."""

    items = tuple(records)
    if not items:
        raise ValueError("experiment design contains no records")

    revisions = {record.revision for record in items}
    scenarios = {record.scenario for record in items}
    config_digests = {record.experiment_config_digest for record in items}
    if len(revisions) != 1 or len(scenarios) != 1 or len(config_digests) != 1:
        raise ValueError("a complete design requires one revision, scenario, and config digest")
    if not items[0].revision.strip():
        raise ValueError("experiment revision cannot be empty")
    if any(record.measurement_kind != MEASUREMENT_KIND for record in items):
        raise ValueError("all records must be labeled deterministic-simulation")
    if any(record.claim_status != CLAIM_STATUS for record in items):
        raise ValueError("all records must retain descriptive-only claim status")
    if any(record.revision_provenance != REVISION_PROVENANCE for record in items):
        raise ValueError("all revisions must remain labeled caller-supplied-unverified")
    if any(record.schema_version != EXPERIMENT_SCHEMA_VERSION for record in items):
        raise ValueError("all records must use the current experiment schema")
    scenario = items[0].scenario
    if scenario != DEFAULT_SCENARIO:
        raise ValueError(
            f"registered experiment scenario is frozen to {DEFAULT_SCENARIO!r}"
        )
    if config_digests != {_experiment_config_digest(scenario)}:
        raise ValueError("experiment config digest does not match the frozen design")
    for record in items:
        if record.record_id != experiment_record_id(record):
            raise ValueError(
                f"record content digest mismatch for seed={record.seed}, "
                f"condition={record.fault_id!r}, policy={record.policy!r}"
            )
    if any(record.model_bound_respected is False for record in items):
        raise ValueError("a successful record reports a model bound above its makespan")

    expected = {
        (seed, fault.fault_id, policy.value)
        for seed in DEFAULT_PAIRED_SEEDS
        for fault in REGISTERED_SIMULATED_FAULTS
        for policy in EXPERIMENT_POLICIES
    }
    observed_keys = [(record.seed, record.fault_id, record.policy) for record in items]
    if len(observed_keys) != len(set(observed_keys)):
        raise ValueError("experiment design contains duplicate seed/fault/policy records")
    observed = set(observed_keys)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(
            f"experiment design is not the frozen Cartesian product; "
            f"missing={missing[:3]}, extra={extra[:3]}"
        )

    registry = {fault.fault_id: fault for fault in REGISTERED_SIMULATED_FAULTS}
    for record in items:
        fault = registry[record.fault_id]
        if record.fault_kind != fault.kind.value:
            raise ValueError(f"fault kind mismatch for {record.fault_id!r}")
        if record.condition_role != _condition_role(fault):
            raise ValueError(f"condition role mismatch for {record.fault_id!r}")
        if record.fault_execution_status != _fault_execution_status(fault):
            raise ValueError(f"execution status mismatch for {record.fault_id!r}")
        policy = SchedulePolicy(record.policy)
        if record.policy_role != POLICY_ROLES[policy]:
            raise ValueError(f"policy role mismatch for {record.policy!r}")
        if record.success:
            if (
                record.modeled_success_makespan_ms
                != record.modeled_schedule_position_ms
                or record.modeled_time_to_failure_ms is not None
                or record.model_bound_respected is not True
            ):
                raise ValueError("successful records must expose success-only makespan")
        elif (
            record.modeled_success_makespan_ms is not None
            or record.modeled_time_to_failure_ms
            != record.modeled_schedule_position_ms
            or record.model_bound_respected is not None
        ):
            raise ValueError("failed records must expose time-to-failure only")

    for seed in DEFAULT_PAIRED_SEEDS:
        seed_items = [record for record in items if record.seed == seed]
        prefault_witnesses = {
            (
                record.prefault_graph_digest,
                record.prefault_envelope_digest,
                record.prefault_config_digest,
            )
            for record in seed_items
        }
        if len(prefault_witnesses) != 1:
            raise ValueError(f"seed {seed} does not preserve one pre-fault graph/config")

        for fault in REGISTERED_SIMULATED_FAULTS:
            pair = [record for record in seed_items if record.fault_id == fault.fault_id]
            policy_set = {record.policy for record in pair}
            if policy_set != {policy.value for policy in EXPERIMENT_POLICIES}:
                raise ValueError(f"seed {seed}, fault {fault.fault_id!r} is not paired")
            postfault_witnesses = {
                (
                    record.pair_id,
                    record.faulted_graph_digest,
                    record.faulted_envelope_digest,
                    record.faulted_config_digest,
                )
                for record in pair
            }
            if len(postfault_witnesses) != 1:
                raise ValueError(
                    f"seed {seed}, fault {fault.fault_id!r} differs across policies"
                )

    expected_records = _generate_expected_records(
        revision=items[0].revision,
        scenario=scenario,
    )
    expected_by_key = {
        (record.seed, record.fault_id, record.policy): record
        for record in expected_records
    }
    for record in items:
        key = (record.seed, record.fault_id, record.policy)
        expected_record = expected_by_key[key]
        if record != expected_record:
            changed_fields = [
                field.name
                for field in fields(ExperimentRecord)
                if getattr(record, field.name) != getattr(expected_record, field.name)
            ]
            raise ValueError(
                f"record {key!r} differs from the registered deterministic execution; "
                f"fields={changed_fields}"
            )

    for policy in EXPERIMENT_POLICIES:
        outcomes = [
            record.success
            for record in items
            if record.fault_id == BINDING_BUDGET_CONDITION_ID
            and record.policy == policy.value
        ]
        if not outcomes or all(outcomes):
            raise ValueError(
                "uniform budget condition is not binding for any frozen seed "
                f"under {policy.value!r}"
            )
        if not any(outcomes):
            raise ValueError(
                "uniform budget condition refuses every frozen seed "
                f"under {policy.value!r}; calibration must retain admitted controls"
            )


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """Return the two-sided Wilson 95% interval for a binomial pass rate."""

    if total <= 0 or not 0 <= successes <= total:
        raise ValueError("Wilson inputs require 0 <= successes <= total and total > 0")
    z = 1.959_963_984_540_054
    proportion = successes / total
    z_squared = z * z
    denominator = 1 + z_squared / total
    center = (proportion + z_squared / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z_squared / (4 * total * total)
        )
        / denominator
    )
    lower = 0.0 if successes == 0 else max(0.0, center - margin)
    upper = 1.0 if successes == total else min(1.0, center + margin)
    return lower, upper


def _percentile(values: Sequence[float | int], quantile: float) -> float:
    if not values or not 0 <= quantile <= 1:
        raise ValueError("percentiles require values and a quantile in [0, 1]")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def _derived_resampling_seed(label: str) -> int:
    material = f"{BOOTSTRAP_RESAMPLING_SEED}:{label}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _bootstrap_interval(
    values: Sequence[int],
    statistic: Callable[[Sequence[int]], float],
    label: str,
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap intervals require at least one value")
    randomizer = random.Random(_derived_resampling_seed(label))
    estimates = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample = [values[randomizer.randrange(len(values))] for _ in values]
        estimates.append(statistic(sample))
    return _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def _metric_summary(values: Sequence[int], label: str) -> dict[str, object]:
    def median_statistic(sample: Sequence[int]) -> float:
        return _percentile(sample, 0.5)

    def p95_statistic(sample: Sequence[int]) -> float:
        return _percentile(sample, 0.95)

    median_interval = _bootstrap_interval(values, median_statistic, f"{label}:p50")
    p95_interval = _bootstrap_interval(values, p95_statistic, f"{label}:p95")
    return {
        "p50": round(_percentile(values, 0.5), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "bootstrap_p50_95": {
            "lower": round(median_interval[0], 6),
            "upper": round(median_interval[1], 6),
        },
        "bootstrap_p95_95": {
            "lower": round(p95_interval[0], 6),
            "upper": round(p95_interval[1], 6),
        },
    }


def _optional_metric_summary(
    values: Sequence[int],
    label: str,
) -> dict[str, object] | None:
    if not values:
        return None
    return _metric_summary(values, label)


def _paired_delta_summary(values: Sequence[int], label: str) -> dict[str, object]:
    if not values:
        return {
            "pairs": 0,
            "mean": None,
            "p50": None,
            "bootstrap_mean_95": None,
        }

    def mean_statistic(sample: Sequence[int]) -> float:
        return sum(sample) / len(sample)

    interval = _bootstrap_interval(values, mean_statistic, f"{label}:paired-mean")
    return {
        "pairs": len(values),
        "mean": round(mean_statistic(values), 6),
        "p50": round(_percentile(values, 0.5), 6),
        "bootstrap_mean_95": {
            "lower": round(interval[0], 6),
            "upper": round(interval[1], 6),
        },
    }


def _failure_reason_counts(records: Sequence[ExperimentRecord]) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for record in records:
        reason = record.failure_reason or "unspecified"
        counts[reason] = counts.get(reason, 0) + 1
    return [
        {"reason": reason, "runs": count}
        for reason, count in sorted(counts.items())
    ]


def _paired_comparisons(
    items: Sequence[ExperimentRecord],
) -> list[dict[str, object]]:
    comparisons: list[dict[str, object]] = []
    for fault in REGISTERED_SIMULATED_FAULTS:
        condition = [record for record in items if record.fault_id == fault.fault_id]
        baseline_by_seed = {
            record.seed: record
            for record in condition
            if record.policy == SchedulePolicy.ADAPTIVE.value
        }
        for comparison_policy in EXPERIMENT_POLICIES:
            if comparison_policy is SchedulePolicy.ADAPTIVE:
                continue
            comparison_by_seed = {
                record.seed: record
                for record in condition
                if record.policy == comparison_policy.value
            }
            per_seed: list[dict[str, object]] = []
            success_deltas: list[int] = []
            success_makespan_deltas: list[int] = []
            success_token_deltas: list[int] = []
            success_cost_deltas: list[int] = []
            success_context_deltas: list[int] = []
            failure_time_deltas: list[int] = []
            for seed in DEFAULT_PAIRED_SEEDS:
                baseline = baseline_by_seed[seed]
                comparison = comparison_by_seed[seed]
                success_delta = int(comparison.success) - int(baseline.success)
                success_deltas.append(success_delta)

                both_succeeded = baseline.success and comparison.success
                both_failed = not baseline.success and not comparison.success
                if both_succeeded:
                    assert baseline.modeled_success_makespan_ms is not None
                    assert comparison.modeled_success_makespan_ms is not None
                    success_makespan_delta = (
                        comparison.modeled_success_makespan_ms
                        - baseline.modeled_success_makespan_ms
                    )
                    success_token_delta = comparison.tokens - baseline.tokens
                    success_cost_delta = (
                        comparison.cost_microusd - baseline.cost_microusd
                    )
                    success_context_delta = (
                        comparison.context_bytes - baseline.context_bytes
                    )
                    success_makespan_deltas.append(success_makespan_delta)
                    success_token_deltas.append(success_token_delta)
                    success_cost_deltas.append(success_cost_delta)
                    success_context_deltas.append(success_context_delta)
                else:
                    success_makespan_delta = None
                    success_token_delta = None
                    success_cost_delta = None
                    success_context_delta = None

                if both_failed:
                    assert baseline.modeled_time_to_failure_ms is not None
                    assert comparison.modeled_time_to_failure_ms is not None
                    failure_time_delta = (
                        comparison.modeled_time_to_failure_ms
                        - baseline.modeled_time_to_failure_ms
                    )
                    failure_time_deltas.append(failure_time_delta)
                else:
                    failure_time_delta = None

                per_seed.append(
                    {
                        "seed": seed,
                        "success_indicator_delta": success_delta,
                        "modeled_success_makespan_ms_delta": success_makespan_delta,
                        "success_tokens_delta": success_token_delta,
                        "success_cost_microusd_delta": success_cost_delta,
                        "success_context_bytes_delta": success_context_delta,
                        "modeled_time_to_failure_ms_delta": failure_time_delta,
                    }
                )

            label = f"{fault.fault_id}:{comparison_policy.value}:vs-adaptive"
            comparisons.append(
                {
                    "fault_id": fault.fault_id,
                    "condition_role": _condition_role(fault),
                    "baseline_policy": SchedulePolicy.ADAPTIVE.value,
                    "comparison_policy": comparison_policy.value,
                    "comparison_policy_role": POLICY_ROLES[comparison_policy],
                    "delta_direction": "comparison-minus-adaptive",
                    "claim_status": CLAIM_STATUS,
                    "per_seed": per_seed,
                    "paired_delta_summary": {
                        "success_indicator": _paired_delta_summary(
                            success_deltas,
                            f"{label}:success-indicator",
                        ),
                        "modeled_success_makespan_ms": _paired_delta_summary(
                            success_makespan_deltas,
                            f"{label}:success-makespan",
                        ),
                        "success_tokens": _paired_delta_summary(
                            success_token_deltas,
                            f"{label}:success-tokens",
                        ),
                        "success_cost_microusd": _paired_delta_summary(
                            success_cost_deltas,
                            f"{label}:success-cost",
                        ),
                        "success_context_bytes": _paired_delta_summary(
                            success_context_deltas,
                            f"{label}:success-context",
                        ),
                        "modeled_time_to_failure_ms": _paired_delta_summary(
                            failure_time_deltas,
                            f"{label}:time-to-failure",
                        ),
                    },
                }
            )
    return comparisons


def summarize_experiments(records: Iterable[ExperimentRecord]) -> dict[str, object]:
    """Produce descriptive-only summaries over a validated paired design."""

    items = tuple(records)
    validate_complete_design(items)
    groups: list[dict[str, object]] = []
    for fault in REGISTERED_SIMULATED_FAULTS:
        for policy in EXPERIMENT_POLICIES:
            group = sorted(
                (
                    record
                    for record in items
                    if record.fault_id == fault.fault_id and record.policy == policy.value
                ),
                key=lambda record: record.seed,
            )
            passes = sum(record.success for record in group)
            wilson = wilson_interval(passes, len(group))
            label = f"{fault.fault_id}:{policy.value}"
            successful = [record for record in group if record.success]
            failed = [record for record in group if not record.success]
            groups.append(
                {
                    "fault_id": fault.fault_id,
                    "fault_kind": fault.kind.value,
                    "condition_role": _condition_role(fault),
                    "policy": policy.value,
                    "policy_role": POLICY_ROLES[policy],
                    "claim_status": CLAIM_STATUS,
                    "runs": len(group),
                    "passes": passes,
                    "pass_rate": round(passes / len(group), 6),
                    "wilson_pass_rate_95": {
                        "lower": round(wilson[0], 6),
                        "upper": round(wilson[1], 6),
                    },
                    "modeled_performance_on_successes": {
                        "runs": len(successful),
                        "modeled_makespan_ms": _optional_metric_summary(
                            [
                                record.modeled_success_makespan_ms
                                for record in successful
                                if record.modeled_success_makespan_ms is not None
                            ],
                            f"{label}:success-makespan",
                        ),
                        "tokens": _optional_metric_summary(
                            [record.tokens for record in successful],
                            f"{label}:success-tokens",
                        ),
                        "cost_microusd": _optional_metric_summary(
                            [record.cost_microusd for record in successful],
                            f"{label}:success-cost",
                        ),
                        "context_bytes": _optional_metric_summary(
                            [record.context_bytes for record in successful],
                            f"{label}:success-context",
                        ),
                    },
                    "modeled_failure_timing": {
                        "runs": len(failed),
                        "modeled_time_to_failure_ms": _optional_metric_summary(
                            [
                                record.modeled_time_to_failure_ms
                                for record in failed
                                if record.modeled_time_to_failure_ms is not None
                            ],
                            f"{label}:time-to-failure",
                        ),
                        "failure_reasons": _failure_reason_counts(failed),
                    },
                }
            )

    first = items[0]
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "measurement_kind": MEASUREMENT_KIND,
        "claim_status": CLAIM_STATUS,
        "revision_provenance": REVISION_PROVENANCE,
        "evidence_scope": (
            "Descriptive deterministic simulation only; no superiority, causal, "
            "live-provider, or external-framework claim. Revision labels are "
            "caller-supplied and unverified."
        ),
        "revision": first.revision,
        "scenario": first.scenario,
        "experiment_config_digest": first.experiment_config_digest,
        "design": {
            "paired": True,
            "paired_seed_count": len(DEFAULT_PAIRED_SEEDS),
            "paired_seeds": list(DEFAULT_PAIRED_SEEDS),
            "policies": [policy.value for policy in EXPERIMENT_POLICIES],
            "policy_roles": {
                policy.value: POLICY_ROLES[policy] for policy in EXPERIMENT_POLICIES
            },
            "paired_comparison_baseline": SchedulePolicy.ADAPTIVE.value,
            "paired_delta_direction": "comparison-minus-adaptive",
            "condition_ids": [
                fault.fault_id for fault in REGISTERED_SIMULATED_FAULTS
            ],
            "nominal_control_id": NOMINAL_CONTROL_ID,
            "binding_budget_condition": {
                "condition_id": BINDING_BUDGET_CONDITION_ID,
                "calibration_scope": "uniform-across-all-frozen-seeds",
                "default_scenario": DEFAULT_SCENARIO,
                "retained_budget_ratio": "1/75",
            },
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_resampling_seed": BOOTSTRAP_RESAMPLING_SEED,
            "bootstrap_unit": "paired-seed",
            "simulated_fault_injection_phase": "pre-dispatch",
            "nominal_control_injection_phase": "none-control",
        },
        "groups": groups,
        "paired_deltas_vs_adaptive": _paired_comparisons(items),
    }


def write_experiment_jsonl(path: Path, records: Iterable[ExperimentRecord]) -> None:
    """Write the validated complete raw design in canonical deterministic order."""

    items = tuple(records)
    validate_complete_design(items)
    ordered = sorted(items, key=lambda item: (item.seed, item.fault_id, item.policy))
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in ordered:
            stream.write(
                json.dumps(
                    record.as_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            )
            stream.write("\n")


__all__ = [
    "AppliedSimulatedFault",
    "BINDING_BUDGET_CONDITION_ID",
    "BOOTSTRAP_RESAMPLING_SEED",
    "BOOTSTRAP_SAMPLES",
    "CLAIM_STATUS",
    "DEFAULT_PAIRED_SEEDS",
    "DEFAULT_SCENARIO",
    "EXPERIMENT_POLICIES",
    "EXPERIMENT_SCHEMA_VERSION",
    "ExperimentRecord",
    "MEASUREMENT_KIND",
    "NOMINAL_CONTROL_ID",
    "POLICY_ROLES",
    "REGISTERED_SIMULATED_FAULTS",
    "REVISION_PROVENANCE",
    "SUMMARY_SCHEMA_VERSION",
    "SimulatedFault",
    "SimulatedFaultKind",
    "apply_simulated_fault",
    "experiment_record_id",
    "run_registered_experiments",
    "summarize_experiments",
    "validate_complete_design",
    "wilson_interval",
    "write_experiment_jsonl",
]
