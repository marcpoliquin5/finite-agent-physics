"""Strict int64 physical-resource admission for selected task profiles.

The analyzer is deliberately separate from scheduling.  It accepts an already
selected profile for each admitted task and checks the declared physical fields
without calling a worker or provider.  Every quantity is an integer whose unit
is fixed by the contract field and repeated in the resulting evidence.

All profile values are estimates.  Additive resources are summed, RAM/VRAM and
bandwidth use a conservative top-``max_parallelism`` concurrent bound, and the
transport/RTT critical path is a physical lower bound rather than a predicted
runtime.  No actual physical usage is settled by this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .contracts import MAX_RESOURCE_UNITS, BackendProfile, RunEnvelope, TaskContract
from .graph import ExecutionGraph
from .serialization import content_digest, normalize


PHYSICAL_REPORT_SCHEMA_VERSION: Final[str] = "finite-physical-admission/v1"
INT64_MAX: Final[int] = MAX_RESOURCE_UNITS

PHYSICAL_ADMISSION_LIMITATIONS: Final[tuple[str, ...]] = (
    "all profile quantities are declared estimates, not runtime measurements",
    "RAM, VRAM, and bandwidth use a conservative top-max_parallelism concurrency bound",
    "transport and RTT critical paths are physical lower bounds, not end-to-end predictions",
    "passing a physical lower-bound deadline check is necessary but not sufficient",
    "this analyzer performs no actual-usage settlement or provider telemetry collection",
    "energy is unsupported without measured hardware telemetry",
)


class PhysicalAdmissionError(ValueError):
    """The graph, envelope, or selected-profile contract is malformed."""


class PhysicalAdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    REFUSED = "refused"


class CoverageStatus(str, Enum):
    ESTIMATED = "estimated"
    DERIVED = "derived"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class PhysicalConstraintCheck:
    dimension: str
    passed: bool
    observed: int | str
    limit: int
    unit: str
    aggregation: str


@dataclass(frozen=True, slots=True)
class PhysicalCoverageEntry:
    dimension: str
    unit: str
    status: CoverageStatus
    aggregation: str
    source: str
    limitation: str


@dataclass(frozen=True, slots=True)
class PhysicalTotals:
    cpu_time_ms: int | None
    conservative_peak_memory_bytes: int | None
    conservative_peak_vram_bytes: int | None
    storage_read_bytes: int | None
    storage_write_bytes: int | None
    network_ingress_bytes: int | None
    network_egress_bytes: int | None
    conservative_peak_bandwidth_bps: int | None
    max_network_rtt_ms: int
    egress_cost_microusd: int | None


@dataclass(frozen=True, slots=True)
class PhysicalAdmissionReport:
    schema_version: str
    status: PhysicalAdmissionStatus
    graph_digest: str
    envelope_digest: str
    selection_digest: str
    selected_profiles: tuple[tuple[str, str, str], ...]
    totals: PhysicalTotals
    transport_critical_path_lower_bound_ms: int | None
    rtt_critical_path_lower_bound_ms: int | None
    transport_rtt_critical_path_lower_bound_ms: int | None
    checks: tuple[PhysicalConstraintCheck, ...]
    coverage_matrix: tuple[PhysicalCoverageEntry, ...]
    overflow_dimensions: tuple[str, ...]
    limitations: tuple[str, ...]
    report_digest: str

    @property
    def violations(self) -> tuple[PhysicalConstraintCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "graph_digest": self.graph_digest,
            "envelope_digest": self.envelope_digest,
            "selection_digest": self.selection_digest,
            "selected_profiles": self.selected_profiles,
            "totals": self.totals,
            "transport_critical_path_lower_bound_ms": (self.transport_critical_path_lower_bound_ms),
            "rtt_critical_path_lower_bound_ms": self.rtt_critical_path_lower_bound_ms,
            "transport_rtt_critical_path_lower_bound_ms": (
                self.transport_rtt_critical_path_lower_bound_ms
            ),
            "checks": self.checks,
            "coverage_matrix": self.coverage_matrix,
            "overflow_dimensions": self.overflow_dimensions,
            "limitations": self.limitations,
        }

    def verify_digest(self) -> bool:
        return (
            type(self) is PhysicalAdmissionReport
            and type(self.status) is PhysicalAdmissionStatus
            and type(self.totals) is PhysicalTotals
            and type(self.checks) is tuple
            and all(type(item) is PhysicalConstraintCheck for item in self.checks)
            and type(self.coverage_matrix) is tuple
            and all(type(item) is PhysicalCoverageEntry for item in self.coverage_matrix)
            and _is_digest(self.report_digest)
            and self.report_digest == content_digest(self.unsigned_payload())
        )

    def as_dict(self) -> dict[str, object]:
        result = normalize(self.unsigned_payload())
        result["report_digest"] = self.report_digest
        return result


@dataclass(frozen=True, slots=True)
class _Dimension:
    name: str
    profile_field: str
    envelope_field: str
    unit: str
    aggregation: str


_ADDITIVE_DIMENSIONS: Final[tuple[_Dimension, ...]] = (
    _Dimension(
        "cpu_time",
        "cpu_time_ms",
        "max_cpu_time_ms",
        "cpu-ms",
        "additive-selected-profiles",
    ),
    _Dimension(
        "storage_read",
        "storage_read_bytes",
        "max_storage_read_bytes",
        "bytes",
        "additive-selected-profiles",
    ),
    _Dimension(
        "storage_write",
        "storage_write_bytes",
        "max_storage_write_bytes",
        "bytes",
        "additive-selected-profiles",
    ),
    _Dimension(
        "network_ingress",
        "network_ingress_bytes",
        "max_network_ingress_bytes",
        "bytes",
        "additive-selected-profiles",
    ),
    _Dimension(
        "network_egress",
        "network_egress_bytes",
        "max_network_egress_bytes",
        "bytes",
        "additive-selected-profiles",
    ),
    _Dimension(
        "egress_cost",
        "egress_cost_microusd",
        "max_egress_cost_microusd",
        "micro-USD",
        "additive-selected-profiles",
    ),
)

_PEAK_DIMENSIONS: Final[tuple[_Dimension, ...]] = (
    _Dimension(
        "peak_memory",
        "peak_memory_bytes",
        "max_peak_memory_bytes",
        "bytes",
        "conservative-top-max_parallelism-sum",
    ),
    _Dimension(
        "peak_vram",
        "peak_vram_bytes",
        "max_peak_vram_bytes",
        "bytes",
        "conservative-top-max_parallelism-sum",
    ),
    _Dimension(
        "bandwidth",
        "min_bandwidth_bps",
        "available_bandwidth_bps",
        "bits-per-second",
        "conservative-top-max_parallelism-sum",
    ),
)


def _coverage_matrix() -> tuple[PhysicalCoverageEntry, ...]:
    estimated = "selected BackendProfile integer estimate"
    return (
        PhysicalCoverageEntry(
            "cpu_time",
            "cpu-ms",
            CoverageStatus.ESTIMATED,
            "additive",
            estimated,
            "does not measure scheduler or worker CPU",
        ),
        PhysicalCoverageEntry(
            "peak_memory",
            "bytes",
            CoverageStatus.ESTIMATED,
            "conservative top-max_parallelism sum",
            estimated,
            "may overstate peak when dependency structure prevents overlap",
        ),
        PhysicalCoverageEntry(
            "peak_vram",
            "bytes",
            CoverageStatus.ESTIMATED,
            "conservative top-max_parallelism sum",
            estimated,
            "may overstate peak and does not model allocator fragmentation",
        ),
        PhysicalCoverageEntry(
            "storage_read",
            "bytes",
            CoverageStatus.ESTIMATED,
            "additive",
            estimated,
            "does not model cache hits or IO amplification",
        ),
        PhysicalCoverageEntry(
            "storage_write",
            "bytes",
            CoverageStatus.ESTIMATED,
            "additive",
            estimated,
            "does not model replication or filesystem amplification",
        ),
        PhysicalCoverageEntry(
            "network_ingress",
            "bytes",
            CoverageStatus.ESTIMATED,
            "additive",
            estimated,
            "does not model protocol overhead or retransmission",
        ),
        PhysicalCoverageEntry(
            "network_egress",
            "bytes",
            CoverageStatus.ESTIMATED,
            "additive",
            estimated,
            "does not model protocol overhead or retransmission",
        ),
        PhysicalCoverageEntry(
            "bandwidth",
            "bits-per-second",
            CoverageStatus.ESTIMATED,
            "conservative top-max_parallelism sum",
            estimated,
            "shared-link contention and time-varying throughput are not measured",
        ),
        PhysicalCoverageEntry(
            "network_rtt",
            "milliseconds",
            CoverageStatus.ESTIMATED,
            "maximum and dependency-path additive",
            estimated,
            "jitter, queueing, and retry RTTs are not modeled",
        ),
        PhysicalCoverageEntry(
            "egress_cost",
            "micro-USD",
            CoverageStatus.ESTIMATED,
            "additive",
            estimated,
            "pricing is declared by the profile and not reconciled to an invoice",
        ),
        PhysicalCoverageEntry(
            "transport_rtt_critical_path_lower_bound",
            "milliseconds",
            CoverageStatus.DERIVED,
            "dependency critical-path maximum",
            "network bytes, available bandwidth, and RTT estimates",
            "lower bound excludes compute, queueing, protocol overhead, and retries",
        ),
        PhysicalCoverageEntry(
            "energy",
            "joules",
            CoverageStatus.UNSUPPORTED,
            "unsupported",
            "none",
            "requires measured hardware telemetry; no energy claim is produced",
        ),
    )


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_i64(value: object, label: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= INT64_MAX:
        qualifier = "positive" if positive else "non-negative"
        raise PhysicalAdmissionError(f"{label} must be a {qualifier} signed-int64 integer")
    return value


def _safe_add(left: int, right: int) -> int | None:
    if left > INT64_MAX - right:
        return None
    return left + right


def _safe_sum(values: Iterable[int]) -> int | None:
    total = 0
    for value in values:
        next_total = _safe_add(total, value)
        if next_total is None:
            return None
        total = next_total
    return total


def _safe_multiply(left: int, right: int) -> int | None:
    if left and right > INT64_MAX // left:
        return None
    return left * right


def _check(
    dimension: _Dimension,
    observed: int | None,
    envelope: RunEnvelope,
) -> PhysicalConstraintCheck:
    limit = getattr(envelope, dimension.envelope_field)
    return PhysicalConstraintCheck(
        dimension=dimension.name,
        passed=observed is not None and observed <= limit,
        observed=observed if observed is not None else "signed-int64-overflow",
        limit=limit,
        unit=dimension.unit,
        aggregation=dimension.aggregation,
    )


def _critical_path(
    graph: ExecutionGraph,
    selected_ids: set[str],
    local_values: Mapping[str, int | None],
) -> tuple[int | None, bool]:
    ranks: dict[str, int] = {}
    for task_id in graph.topological_order():
        if task_id not in selected_ids:
            continue
        local = local_values[task_id]
        if local is None:
            return None, True
        predecessor = max(
            (ranks[dependency] for dependency in graph.by_id[task_id].dependencies),
            default=0,
        )
        rank = _safe_add(predecessor, local)
        if rank is None:
            return None, True
        ranks[task_id] = rank
    return max(ranks.values(), default=0), False


class PhysicalResourceAnalyzer:
    """Create digest-bound physical admission evidence from a fixed selection."""

    def analyze(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        selected_profiles: Mapping[str, BackendProfile],
    ) -> PhysicalAdmissionReport:
        selection = self._validate_inputs(graph, envelope, selected_profiles)
        profiles = tuple(profile for _, profile in selection)
        overflow: set[str] = set()
        checks: list[PhysicalConstraintCheck] = []
        additive_totals: dict[str, int | None] = {}

        for dimension in _ADDITIVE_DIMENSIONS:
            total = _safe_sum(getattr(profile, dimension.profile_field) for profile in profiles)
            additive_totals[dimension.name] = total
            if total is None:
                overflow.add(dimension.name)
            checks.append(_check(dimension, total, envelope))

        peak_totals: dict[str, int | None] = {}
        concurrent_count = min(envelope.max_parallelism, len(profiles))
        for dimension in _PEAK_DIMENSIONS:
            values = sorted(
                (getattr(profile, dimension.profile_field) for profile in profiles), reverse=True
            )[:concurrent_count]
            total = _safe_sum(values)
            peak_totals[dimension.name] = total
            if total is None:
                overflow.add(dimension.name)
            checks.append(_check(dimension, total, envelope))

        max_rtt = max((profile.network_rtt_ms for profile in profiles), default=0)
        checks.append(
            PhysicalConstraintCheck(
                dimension="network_rtt",
                passed=max_rtt <= envelope.max_network_rtt_ms,
                observed=max_rtt,
                limit=envelope.max_network_rtt_ms,
                unit="milliseconds",
                aggregation="maximum-selected-profile",
            )
        )

        transport_local: dict[str, int | None] = {}
        rtt_local: dict[str, int | None] = {}
        combined_local: dict[str, int | None] = {}
        bandwidth = envelope.available_bandwidth_bps
        for task_id, profile in selection:
            transfer_bytes = _safe_add(profile.network_ingress_bytes, profile.network_egress_bytes)
            transfer_ms: int | None
            if transfer_bytes is None:
                transfer_ms = None
                overflow.add("transport_byte_sum")
            elif transfer_bytes == 0:
                transfer_ms = 0
            elif bandwidth == 0:
                transfer_ms = None
            else:
                bit_milliseconds = _safe_multiply(transfer_bytes, 8_000)
                if bit_milliseconds is None:
                    transfer_ms = None
                    overflow.add("transport_unit_conversion")
                else:
                    quotient, remainder = divmod(bit_milliseconds, bandwidth)
                    transfer_ms = quotient + (1 if remainder else 0)
            transport_local[task_id] = transfer_ms
            rtt_local[task_id] = profile.network_rtt_ms
            combined_local[task_id] = (
                None if transfer_ms is None else _safe_add(transfer_ms, profile.network_rtt_ms)
            )
            if transfer_ms is not None and combined_local[task_id] is None:
                overflow.add("transport_rtt_local")

        selected_ids = {task_id for task_id, _ in selection}
        transport_bound, transport_overflow = _critical_path(graph, selected_ids, transport_local)
        rtt_bound, rtt_overflow = _critical_path(graph, selected_ids, rtt_local)
        combined_bound, combined_overflow = _critical_path(graph, selected_ids, combined_local)
        if transport_overflow:
            overflow.add("transport_critical_path")
        if rtt_overflow:
            overflow.add("rtt_critical_path")
        if combined_overflow:
            overflow.add("transport_rtt_critical_path")

        checks.append(
            PhysicalConstraintCheck(
                dimension="transport_bandwidth_defined",
                passed=transport_bound is not None,
                observed=(
                    transport_bound
                    if transport_bound is not None
                    else "zero-bandwidth-or-signed-int64-overflow"
                ),
                limit=envelope.deadline_ms,
                unit="milliseconds",
                aggregation="transport-critical-path-lower-bound",
            )
        )
        checks.append(
            PhysicalConstraintCheck(
                dimension="transport_rtt_deadline_lower_bound",
                passed=combined_bound is not None and combined_bound <= envelope.deadline_ms,
                observed=(
                    combined_bound
                    if combined_bound is not None
                    else "undefined-or-signed-int64-overflow"
                ),
                limit=envelope.deadline_ms,
                unit="milliseconds",
                aggregation="dependency-critical-path-lower-bound",
            )
        )

        totals = PhysicalTotals(
            cpu_time_ms=additive_totals["cpu_time"],
            conservative_peak_memory_bytes=peak_totals["peak_memory"],
            conservative_peak_vram_bytes=peak_totals["peak_vram"],
            storage_read_bytes=additive_totals["storage_read"],
            storage_write_bytes=additive_totals["storage_write"],
            network_ingress_bytes=additive_totals["network_ingress"],
            network_egress_bytes=additive_totals["network_egress"],
            conservative_peak_bandwidth_bps=peak_totals["bandwidth"],
            max_network_rtt_ms=max_rtt,
            egress_cost_microusd=additive_totals["egress_cost"],
        )
        status = (
            PhysicalAdmissionStatus.ADMITTED
            if all(check.passed for check in checks)
            else PhysicalAdmissionStatus.REFUSED
        )
        selected_summary = tuple(
            (task_id, profile.provider, profile.name) for task_id, profile in selection
        )
        selection_material = [
            {"task_id": task_id, "profile": normalize(profile)} for task_id, profile in selection
        ]
        unsigned = {
            "schema_version": PHYSICAL_REPORT_SCHEMA_VERSION,
            "status": status,
            "graph_digest": content_digest(graph),
            "envelope_digest": content_digest(envelope),
            "selection_digest": content_digest(selection_material),
            "selected_profiles": selected_summary,
            "totals": totals,
            "transport_critical_path_lower_bound_ms": transport_bound,
            "rtt_critical_path_lower_bound_ms": rtt_bound,
            "transport_rtt_critical_path_lower_bound_ms": combined_bound,
            "checks": tuple(checks),
            "coverage_matrix": _coverage_matrix(),
            "overflow_dimensions": tuple(sorted(overflow)),
            "limitations": PHYSICAL_ADMISSION_LIMITATIONS,
        }
        report = PhysicalAdmissionReport(
            **unsigned,
            report_digest=content_digest(unsigned),
        )
        if not report.verify_digest():
            raise RuntimeError("physical report digest construction disagreed")
        return report

    @staticmethod
    def _validate_inputs(
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        selected_profiles: Mapping[str, BackendProfile],
    ) -> tuple[tuple[str, BackendProfile], ...]:
        if type(graph) is not ExecutionGraph or type(graph.tasks) is not tuple:
            raise PhysicalAdmissionError("graph must use the exact ExecutionGraph contract")
        if type(envelope) is not RunEnvelope:
            raise PhysicalAdmissionError("envelope must use the exact RunEnvelope contract")
        if not isinstance(selected_profiles, Mapping) or any(
            type(task_id) is not str for task_id in selected_profiles
        ):
            raise PhysicalAdmissionError("selected profiles require a string-keyed mapping")

        for field in (
            "deadline_ms",
            "max_parallelism",
            "max_cpu_time_ms",
            "max_peak_memory_bytes",
            "max_peak_vram_bytes",
            "max_storage_read_bytes",
            "max_storage_write_bytes",
            "max_network_ingress_bytes",
            "max_network_egress_bytes",
            "available_bandwidth_bps",
            "max_network_rtt_ms",
            "max_egress_cost_microusd",
        ):
            _strict_i64(
                getattr(envelope, field),
                f"envelope.{field}",
                positive=field in {"deadline_ms", "max_parallelism"},
            )
        envelope_errors = envelope.validate()
        if envelope_errors:
            raise PhysicalAdmissionError("invalid envelope: " + "; ".join(envelope_errors))

        by_id = graph.by_id
        selected_ids = set(selected_profiles)
        unknown = selected_ids - set(by_id)
        if unknown:
            raise PhysicalAdmissionError(f"selection contains unknown tasks: {sorted(unknown)}")
        try:
            protected = _protected_task_ids(graph)
        except KeyError as exc:
            raise PhysicalAdmissionError(
                f"invalid graph dependency referenced unknown task {exc.args[0]!r}"
            ) from exc
        missing_protected = protected - selected_ids
        if missing_protected:
            raise PhysicalAdmissionError(
                f"selection omits protected mandatory work: {sorted(missing_protected)}"
            )
        for task_id in selected_ids:
            missing_dependencies = set(by_id[task_id].dependencies) - selected_ids
            if missing_dependencies:
                raise PhysicalAdmissionError(
                    f"selected task {task_id!r} lacks dependencies {sorted(missing_dependencies)}"
                )

        selection: list[tuple[str, BackendProfile]] = []
        for task_id in sorted(selected_ids):
            task = by_id[task_id]
            if type(task) is not TaskContract or type(task.profiles) is not tuple:
                raise PhysicalAdmissionError(
                    "tasks and profile collections must be exact contracts"
                )
            profile = selected_profiles[task_id]
            if type(profile) is not BackendProfile:
                raise PhysicalAdmissionError(
                    f"selected profile for {task_id!r} must use BackendProfile"
                )
            if profile not in task.profiles:
                raise PhysicalAdmissionError(
                    f"selected profile for {task_id!r} is not declared by the task"
                )
            if profile.quality < task.min_quality:
                raise PhysicalAdmissionError(
                    f"selected profile for {task_id!r} violates its quality floor"
                )
            for field in (
                "cpu_time_ms",
                "peak_memory_bytes",
                "peak_vram_bytes",
                "storage_read_bytes",
                "storage_write_bytes",
                "network_ingress_bytes",
                "network_egress_bytes",
                "min_bandwidth_bps",
                "network_rtt_ms",
                "egress_cost_microusd",
            ):
                _strict_i64(getattr(profile, field), f"{task_id}.{field}")
            selection.append((task_id, profile))
        try:
            graph.validate()
        except ValueError as exc:
            raise PhysicalAdmissionError(f"invalid graph: {exc}") from exc
        return tuple(selection)


def _protected_task_ids(graph: ExecutionGraph) -> set[str]:
    by_id = graph.by_id
    protected = {task.task_id for task in graph.tasks if not task.optional}
    stack = list(protected)
    while stack:
        for dependency in by_id[stack.pop()].dependencies:
            if dependency not in protected:
                protected.add(dependency)
                stack.append(dependency)
    return protected


def analyze_physical_resources(
    graph: ExecutionGraph,
    envelope: RunEnvelope,
    selected_profiles: Mapping[str, BackendProfile],
) -> PhysicalAdmissionReport:
    """Convenience entry point for one deterministic physical admission pass."""

    return PhysicalResourceAnalyzer().analyze(graph, envelope, selected_profiles)


__all__ = [
    "INT64_MAX",
    "PHYSICAL_ADMISSION_LIMITATIONS",
    "PHYSICAL_REPORT_SCHEMA_VERSION",
    "CoverageStatus",
    "PhysicalAdmissionError",
    "PhysicalAdmissionReport",
    "PhysicalAdmissionStatus",
    "PhysicalConstraintCheck",
    "PhysicalCoverageEntry",
    "PhysicalResourceAnalyzer",
    "PhysicalTotals",
    "analyze_physical_resources",
]
