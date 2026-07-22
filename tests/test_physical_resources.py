from __future__ import annotations

from dataclasses import replace

import pytest

from agent_physics.contracts import (
    MAX_RESOURCE_UNITS,
    BackendProfile,
    RunEnvelope,
    TaskContract,
)
from agent_physics.graph import ExecutionGraph
from agent_physics.physical_resources import (
    PHYSICAL_ADMISSION_LIMITATIONS,
    CoverageStatus,
    PhysicalAdmissionError,
    PhysicalAdmissionStatus,
    PhysicalResourceAnalyzer,
    analyze_physical_resources,
)


def _profile(name: str = "selected", provider: str = "fixture", **overrides: int):
    values = {
        "cpu_time_ms": 10,
        "peak_memory_bytes": 20,
        "peak_vram_bytes": 30,
        "storage_read_bytes": 40,
        "storage_write_bytes": 50,
        "network_ingress_bytes": 6,
        "network_egress_bytes": 7,
        "min_bandwidth_bps": 100,
        "network_rtt_ms": 9,
        "egress_cost_microusd": 11,
    }
    values.update(overrides)
    return BackendProfile(
        name=name,
        provider=provider,
        duration_ms_p50=1,
        duration_ms_p95=2,
        quality=1.0,
        **values,
    )


def _envelope(**overrides: int) -> RunEnvelope:
    values = {
        "deadline_ms": 2_000,
        "max_tokens": 1_000,
        "max_cost_microusd": 1_000,
        "max_context_bytes": 1_000,
        "max_parallelism": 1,
        "max_cpu_time_ms": 10,
        "max_peak_memory_bytes": 20,
        "max_peak_vram_bytes": 30,
        "max_storage_read_bytes": 40,
        "max_storage_write_bytes": 50,
        "max_network_ingress_bytes": 6,
        "max_network_egress_bytes": 7,
        "available_bandwidth_bps": 100,
        "max_network_rtt_ms": 9,
        "max_egress_cost_microusd": 11,
    }
    values.update(overrides)
    return RunEnvelope(**values)


def _one_task(profile: BackendProfile | None = None):
    selected = profile or _profile()
    graph = ExecutionGraph.from_tasks((TaskContract("task", (selected,)),))
    return graph, {"task": selected}


def _check(report, dimension: str):
    return next(item for item in report.checks if item.dimension == dimension)


def test_all_declared_physical_caps_admit_at_exact_integer_boundaries() -> None:
    graph, selected = _one_task()

    report = analyze_physical_resources(graph, _envelope(), selected)

    assert report.status is PhysicalAdmissionStatus.ADMITTED
    assert report.verify_digest()
    assert not report.violations
    assert report.transport_critical_path_lower_bound_ms == 1_040
    assert report.rtt_critical_path_lower_bound_ms == 9
    assert report.transport_rtt_critical_path_lower_bound_ms == 1_049
    assert report.totals.cpu_time_ms == 10
    assert report.totals.conservative_peak_memory_bytes == 20
    assert report.totals.conservative_peak_vram_bytes == 30


@pytest.mark.parametrize(
    ("envelope_field", "dimension", "failing_limit", "unit"),
    (
        ("max_cpu_time_ms", "cpu_time", 9, "cpu-ms"),
        ("max_peak_memory_bytes", "peak_memory", 19, "bytes"),
        ("max_peak_vram_bytes", "peak_vram", 29, "bytes"),
        ("max_storage_read_bytes", "storage_read", 39, "bytes"),
        ("max_storage_write_bytes", "storage_write", 49, "bytes"),
        ("max_network_ingress_bytes", "network_ingress", 5, "bytes"),
        ("max_network_egress_bytes", "network_egress", 6, "bytes"),
        ("available_bandwidth_bps", "bandwidth", 99, "bits-per-second"),
        ("max_network_rtt_ms", "network_rtt", 8, "milliseconds"),
        ("max_egress_cost_microusd", "egress_cost", 10, "micro-USD"),
    ),
)
def test_each_physical_cap_refuses_independently_with_explicit_unit(
    envelope_field: str,
    dimension: str,
    failing_limit: int,
    unit: str,
) -> None:
    graph, selected = _one_task()

    report = analyze_physical_resources(
        graph,
        _envelope(**{envelope_field: failing_limit}),
        selected,
    )

    check = _check(report, dimension)
    assert report.status is PhysicalAdmissionStatus.REFUSED
    assert not check.passed
    assert check.limit == failing_limit
    assert check.unit == unit


def test_peak_ram_vram_and_bandwidth_sum_largest_possible_concurrent_set() -> None:
    profiles = (
        _profile("a", peak_memory_bytes=100, peak_vram_bytes=10, min_bandwidth_bps=5),
        _profile("b", peak_memory_bytes=80, peak_vram_bytes=40, min_bandwidth_bps=20),
        _profile("c", peak_memory_bytes=50, peak_vram_bytes=30, min_bandwidth_bps=10),
    )
    graph = ExecutionGraph.from_tasks(
        tuple(TaskContract(f"task-{index}", (profile,)) for index, profile in enumerate(profiles))
    )
    selected = {f"task-{index}": profile for index, profile in enumerate(profiles)}
    envelope = RunEnvelope(
        deadline_ms=10_000,
        max_tokens=1_000,
        max_cost_microusd=1_000,
        max_context_bytes=1_000,
        max_parallelism=2,
        max_cpu_time_ms=30,
        max_peak_memory_bytes=180,
        max_peak_vram_bytes=70,
        max_storage_read_bytes=120,
        max_storage_write_bytes=150,
        max_network_ingress_bytes=18,
        max_network_egress_bytes=21,
        available_bandwidth_bps=30,
        max_network_rtt_ms=9,
        max_egress_cost_microusd=33,
    )

    report = analyze_physical_resources(graph, envelope, selected)

    assert report.status is PhysicalAdmissionStatus.ADMITTED
    assert report.totals.conservative_peak_memory_bytes == 180
    assert report.totals.conservative_peak_vram_bytes == 70
    assert report.totals.conservative_peak_bandwidth_bps == 30
    assert _check(report, "peak_memory").aggregation == ("conservative-top-max_parallelism-sum")


def test_transport_and_rtt_have_separate_dependency_critical_path_lower_bounds() -> None:
    first = _profile(
        "first",
        cpu_time_ms=0,
        peak_memory_bytes=0,
        peak_vram_bytes=0,
        storage_read_bytes=0,
        storage_write_bytes=0,
        network_ingress_bytes=1_000,
        network_egress_bytes=0,
        min_bandwidth_bps=1,
        network_rtt_ms=2,
        egress_cost_microusd=0,
    )
    second = replace(first, name="second")
    independent = replace(
        first,
        name="independent",
        network_ingress_bytes=0,
        network_rtt_ms=5,
    )
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("first", (first,)),
            TaskContract("second", (second,), ("first",)),
            TaskContract("independent", (independent,)),
        )
    )
    selected = {"first": first, "second": second, "independent": independent}
    envelope = RunEnvelope(
        deadline_ms=5,
        max_tokens=0,
        max_cost_microusd=0,
        max_context_bytes=0,
        max_parallelism=1,
        max_cpu_time_ms=0,
        max_peak_memory_bytes=0,
        max_peak_vram_bytes=0,
        max_storage_read_bytes=0,
        max_storage_write_bytes=0,
        max_network_ingress_bytes=2_000,
        max_network_egress_bytes=0,
        available_bandwidth_bps=8_000_000,
        max_network_rtt_ms=5,
        max_egress_cost_microusd=0,
    )

    report = analyze_physical_resources(graph, envelope, selected)

    assert report.transport_critical_path_lower_bound_ms == 2
    assert report.rtt_critical_path_lower_bound_ms == 5
    assert report.transport_rtt_critical_path_lower_bound_ms == 6
    assert report.status is PhysicalAdmissionStatus.REFUSED
    assert not _check(report, "transport_rtt_deadline_lower_bound").passed


def test_zero_bandwidth_with_nonzero_transport_refuses_even_without_profile_minimum() -> None:
    profile = _profile(min_bandwidth_bps=0)
    graph, selected = _one_task(profile)

    report = analyze_physical_resources(
        graph,
        _envelope(available_bandwidth_bps=0),
        selected,
    )

    assert report.status is PhysicalAdmissionStatus.REFUSED
    assert report.transport_critical_path_lower_bound_ms is None
    assert not _check(report, "transport_bandwidth_defined").passed


def test_additive_signed_int64_overflow_is_a_digest_bound_refusal() -> None:
    first = _profile(
        "first",
        cpu_time_ms=MAX_RESOURCE_UNITS,
        peak_memory_bytes=0,
        peak_vram_bytes=0,
        storage_read_bytes=0,
        storage_write_bytes=0,
        network_ingress_bytes=0,
        network_egress_bytes=0,
        min_bandwidth_bps=0,
        network_rtt_ms=0,
        egress_cost_microusd=0,
    )
    second = replace(first, name="second")
    graph = ExecutionGraph.from_tasks(
        (TaskContract("first", (first,)), TaskContract("second", (second,)))
    )
    envelope = RunEnvelope(
        deadline_ms=100,
        max_tokens=0,
        max_cost_microusd=0,
        max_context_bytes=0,
        max_parallelism=1,
        max_cpu_time_ms=MAX_RESOURCE_UNITS,
    )

    report = analyze_physical_resources(
        graph,
        envelope,
        {"first": first, "second": second},
    )

    assert report.status is PhysicalAdmissionStatus.REFUSED
    assert report.totals.cpu_time_ms is None
    assert "cpu_time" in report.overflow_dimensions
    assert _check(report, "cpu_time").observed == "signed-int64-overflow"
    assert report.verify_digest()


def test_transport_unit_conversion_and_rtt_path_overflow_refuse() -> None:
    huge_transport = _profile(
        network_ingress_bytes=MAX_RESOURCE_UNITS,
        network_egress_bytes=0,
        min_bandwidth_bps=0,
        network_rtt_ms=0,
    )
    graph, selected = _one_task(huge_transport)
    transport = analyze_physical_resources(
        graph,
        _envelope(
            max_network_ingress_bytes=MAX_RESOURCE_UNITS,
            available_bandwidth_bps=MAX_RESOURCE_UNITS,
        ),
        selected,
    )
    assert transport.status is PhysicalAdmissionStatus.REFUSED
    assert "transport_unit_conversion" in transport.overflow_dimensions

    first = _profile(
        "first-rtt",
        network_ingress_bytes=0,
        network_egress_bytes=0,
        min_bandwidth_bps=0,
        network_rtt_ms=MAX_RESOURCE_UNITS,
    )
    second = replace(first, name="second-rtt")
    chain = ExecutionGraph.from_tasks(
        (TaskContract("first", (first,)), TaskContract("second", (second,), ("first",)))
    )
    rtt = analyze_physical_resources(
        chain,
        _envelope(
            deadline_ms=MAX_RESOURCE_UNITS,
            max_cpu_time_ms=20,
            max_peak_memory_bytes=40,
            max_peak_vram_bytes=60,
            max_storage_read_bytes=80,
            max_storage_write_bytes=100,
            max_network_ingress_bytes=12,
            max_network_egress_bytes=14,
            available_bandwidth_bps=200,
            max_network_rtt_ms=MAX_RESOURCE_UNITS,
            max_egress_cost_microusd=22,
        ),
        {"first": first, "second": second},
    )
    assert rtt.status is PhysicalAdmissionStatus.REFUSED
    assert rtt.rtt_critical_path_lower_bound_ms is None
    assert "rtt_critical_path" in rtt.overflow_dimensions


def test_boolean_and_out_of_range_physical_units_fail_before_arithmetic() -> None:
    bool_profile = _profile(cpu_time_ms=True)  # type: ignore[arg-type]
    graph = ExecutionGraph((TaskContract("task", (bool_profile,)),))

    with pytest.raises(PhysicalAdmissionError, match="signed-int64"):
        analyze_physical_resources(graph, _envelope(), {"task": bool_profile})

    valid_graph, selected = _one_task()
    bool_envelope = replace(_envelope(), max_peak_memory_bytes=True)
    with pytest.raises(PhysicalAdmissionError, match="signed-int64"):
        analyze_physical_resources(valid_graph, bool_envelope, selected)

    too_large = _profile(cpu_time_ms=MAX_RESOURCE_UNITS + 1)
    invalid_graph = ExecutionGraph((TaskContract("task", (too_large,)),))
    with pytest.raises(PhysicalAdmissionError, match="signed-int64"):
        analyze_physical_resources(invalid_graph, _envelope(), {"task": too_large})


def test_selection_requires_mandatory_dependency_closure_but_may_omit_optional() -> None:
    dependency = _profile("dependency")
    mandatory = _profile("mandatory")
    extra = _profile("optional")
    graph = ExecutionGraph.from_tasks(
        (
            TaskContract("dependency", (dependency,), optional=True),
            TaskContract("mandatory", (mandatory,), ("dependency",)),
            TaskContract("extra", (extra,), optional=True),
        )
    )
    envelope = replace(
        _envelope(),
        deadline_ms=3_000,
        max_cpu_time_ms=20,
        max_peak_memory_bytes=20,
        max_peak_vram_bytes=30,
        max_storage_read_bytes=80,
        max_storage_write_bytes=100,
        max_network_ingress_bytes=12,
        max_network_egress_bytes=14,
        max_egress_cost_microusd=22,
    )

    with pytest.raises(PhysicalAdmissionError, match="protected mandatory"):
        analyze_physical_resources(graph, envelope, {"mandatory": mandatory})

    report = analyze_physical_resources(
        graph,
        envelope,
        {"dependency": dependency, "mandatory": mandatory},
    )
    assert report.status is PhysicalAdmissionStatus.ADMITTED
    assert {task_id for task_id, _, _ in report.selected_profiles} == {
        "dependency",
        "mandatory",
    }


def test_mapping_order_does_not_change_report_or_digest() -> None:
    first = _profile("first")
    second = _profile("second")
    graph = ExecutionGraph.from_tasks((TaskContract("a", (first,)), TaskContract("b", (second,))))
    envelope = replace(
        _envelope(),
        max_parallelism=2,
        max_cpu_time_ms=20,
        max_peak_memory_bytes=40,
        max_peak_vram_bytes=60,
        max_storage_read_bytes=80,
        max_storage_write_bytes=100,
        max_network_ingress_bytes=12,
        max_network_egress_bytes=14,
        available_bandwidth_bps=200,
        max_egress_cost_microusd=22,
    )
    analyzer = PhysicalResourceAnalyzer()

    first_report = analyzer.analyze(graph, envelope, {"a": first, "b": second})
    second_report = analyzer.analyze(graph, envelope, {"b": second, "a": first})

    assert first_report == second_report
    assert first_report.report_digest == second_report.report_digest
    assert first_report.as_dict()["report_digest"] == first_report.report_digest


def test_report_digest_detects_post_construction_mutation() -> None:
    graph, selected = _one_task()
    report = analyze_physical_resources(graph, _envelope(), selected)
    assert report.verify_digest()

    mutated_totals = replace(report.totals, cpu_time_ms=9)
    mutated = replace(report, totals=mutated_totals)

    assert not mutated.verify_digest()


def test_coverage_matrix_is_complete_and_energy_is_explicitly_unsupported() -> None:
    graph, selected = _one_task()
    report = analyze_physical_resources(graph, _envelope(), selected)
    by_dimension = {entry.dimension: entry for entry in report.coverage_matrix}

    assert set(by_dimension) == {
        "cpu_time",
        "peak_memory",
        "peak_vram",
        "storage_read",
        "storage_write",
        "network_ingress",
        "network_egress",
        "bandwidth",
        "network_rtt",
        "egress_cost",
        "transport_rtt_critical_path_lower_bound",
        "energy",
    }
    assert by_dimension["energy"].status is CoverageStatus.UNSUPPORTED
    assert by_dimension["energy"].unit == "joules"
    assert "measured hardware telemetry" in by_dimension["energy"].limitation
    assert all(
        entry.status in {CoverageStatus.ESTIMATED, CoverageStatus.DERIVED}
        for name, entry in by_dimension.items()
        if name != "energy"
    )
    assert any("no actual-usage settlement" in item for item in PHYSICAL_ADMISSION_LIMITATIONS)
