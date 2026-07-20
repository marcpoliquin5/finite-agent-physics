from dataclasses import replace

from agent_physics.stormshift import (
    FICTIONAL_DISCLAIMER,
    AccessibilityAttestation,
    BilingualAlert,
    FaultKind,
    FaultSemantics,
    PublicationDisposition,
    RouteAssignment,
    StormShiftValidator,
    UtilityPriority,
    build_reference_plan,
    fault_budget_cut,
    fault_capacity_loss,
    fault_contradiction,
    fault_latency_multiplier,
    fault_provider_429,
    fault_stale_artifact,
    stormshift_fixture,
)


def checks(report):  # type: ignore[no-untyped-def]
    return {check.name: check for check in report.checks}


def test_fixture_is_typed_replay_stable_and_unambiguously_fictional() -> None:
    first = stormshift_fixture()
    replay = stormshift_fixture()

    assert first.fixture_digest == replay.fixture_digest
    assert first.disclaimer == FICTIONAL_DISCLAIMER
    assert "Fictional Simulation" in first.display_name
    assert first.shelters and first.transit and first.hospitals
    assert first.floods and first.utilities and first.evidence
    assert all(item.display_name.startswith("Fictional") for item in first.shelters)
    assert all(item.display_name.startswith("Fictional") for item in first.hospitals)
    assert all(item.assertion.startswith("SIMULATED:") for item in first.evidence)
    assert all(item.is_fresh(first.as_of_ms) for item in first.evidence)


def test_reference_plan_passes_every_validator_without_publication() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    report = StormShiftValidator().validate(scenario, plan)

    assert report.passed
    assert report.verify_digest()
    assert all(check.passed for check in report.checks)
    assert plan.publication_disposition is PublicationDisposition.SIMULATION_DRAFT
    assert not plan.external_publication_attempted
    assert plan.external_targets == ()
    assert "SIMULATION ONLY" in plan.alert.english
    assert "SOLO SIMULACIÓN" in plan.alert.spanish


def test_report_digest_binds_honest_scope_and_limitations() -> None:
    scenario = stormshift_fixture()
    report = StormShiftValidator().validate(scenario, build_reference_plan(scenario))

    assert report.scope
    assert report.limitations
    assert any("structural" in item for item in report.scope)
    assert any("do not assess translation" in item for item in report.limitations)
    assert any("not checked for entailment" in item for item in report.limitations)
    assert any("do not inspect" in item for item in report.limitations)
    forged = replace(report, limitations=report.limitations[:-1])
    assert not forged.verify_digest()


def test_empty_utility_priority_list_is_rejected() -> None:
    scenario = stormshift_fixture()
    plan = replace(build_reference_plan(scenario), utility_priorities=())
    check = checks(StormShiftValidator().validate(scenario, plan))[
        "structural-utility-priority-list-integrity"
    ]

    assert not check.passed
    assert "utility-priority list is empty" in check.details
    assert any("missing utility IDs" in detail for detail in check.details)


def test_unknown_utility_priority_id_is_rejected() -> None:
    scenario = stormshift_fixture()
    priorities = (
        UtilityPriority("SIM-UTILITY-CENTRAL", 1),
        UtilityPriority("SIM-UTILITY-UNKNOWN", 2),
    )
    plan = replace(build_reference_plan(scenario), utility_priorities=priorities)
    check = checks(StormShiftValidator().validate(scenario, plan))[
        "structural-utility-priority-list-integrity"
    ]

    assert not check.passed
    assert any("unknown utility IDs" in detail for detail in check.details)
    assert any("missing utility IDs" in detail for detail in check.details)


def test_duplicate_utility_priority_id_is_rejected() -> None:
    scenario = stormshift_fixture()
    priorities = (
        UtilityPriority("SIM-UTILITY-CENTRAL", 1),
        UtilityPriority("SIM-UTILITY-CENTRAL", 2),
        UtilityPriority("SIM-UTILITY-WEST", 3),
    )
    plan = replace(build_reference_plan(scenario), utility_priorities=priorities)
    check = checks(StormShiftValidator().validate(scenario, plan))[
        "structural-utility-priority-list-integrity"
    ]

    assert not check.passed
    assert any("duplicate utility IDs" in detail for detail in check.details)


def test_nonpositive_or_nonconsecutive_utility_ranks_are_rejected() -> None:
    scenario = stormshift_fixture()
    priorities = (
        UtilityPriority("SIM-UTILITY-CENTRAL", 0),
        UtilityPriority("SIM-UTILITY-WEST", 3),
    )
    plan = replace(build_reference_plan(scenario), utility_priorities=priorities)
    check = checks(StormShiftValidator().validate(scenario, plan))[
        "structural-utility-priority-list-integrity"
    ]

    assert not check.passed
    assert "utility priority ranks must be positive" in check.details
    assert "utility priority ranks must be unique and consecutive from 1" in check.details


def test_energized_utility_cannot_outrank_degraded_lower_capacity_utility() -> None:
    scenario = stormshift_fixture()
    priorities = (
        UtilityPriority("SIM-UTILITY-WEST", 1),
        UtilityPriority("SIM-UTILITY-CENTRAL", 2),
    )
    plan = replace(build_reference_plan(scenario), utility_priorities=priorities)
    check = checks(StormShiftValidator().validate(scenario, plan))[
        "structural-utility-priority-list-integrity"
    ]

    assert not check.passed
    assert any("degraded/offline" in detail for detail in check.details)


def test_capacity_conservation_and_accessible_capacity_fail_closed() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    allocations = list(plan.allocations)
    allocations[0] = replace(
        allocations[0],
        evacuees=101,
        accessible_evacuees=16,
    )
    report = StormShiftValidator().validate(
        scenario,
        replace(plan, allocations=tuple(allocations)),
    )

    result = checks(report)
    assert not result["structural-capacity-and-demand-arithmetic"].passed
    assert not result["declared-accessibility-fields-structural-only"].passed
    assert any(
        "shelter capacity exceeded" in detail
        for detail in result["structural-capacity-and-demand-arithmetic"].details
    )


def test_closed_flooded_route_is_rejected_by_route_and_closure_checks() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    routed_over_closure = RouteAssignment(
        "SIM-ROUTE-CLOSED",
        "SIM-SHELTER-BRAVO",
        passengers=80,
        accessible_passengers=9,
    )
    report = StormShiftValidator().validate(
        scenario,
        replace(plan, routes=(plan.routes[0], routed_over_closure)),
    )

    result = checks(report)
    assert not result["structural-route-linkage-and-capacity"].passed
    assert not result["structural-modeled-closure-intersection"].passed
    assert any(
        "sim-low-road-x" in detail
        for detail in result["structural-modeled-closure-intersection"].details
    )


def test_bilingual_numerical_drift_is_detected() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    broken_alert = BilingualAlert(
        plan.alert.english,
        plan.alert.spanish.replace("180", "181"),
    )
    report = StormShiftValidator().validate(
        scenario,
        replace(plan, alert=broken_alert),
    )

    bilingual = checks(report)["bilingual-numeric-parity-structural-only"]
    assert not bilingual.passed
    assert "English and Spanish numeric facts differ" in bilingual.details


def test_missing_stale_and_contradictory_citations_are_explicit() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    cited = plan.citations[0]

    missing_report = StormShiftValidator().validate(
        scenario,
        replace(plan, citations=tuple(item for item in plan.citations if item != cited)),
    )
    stale_report = StormShiftValidator().validate(
        fault_stale_artifact(scenario, cited),
        plan,
    )
    contradiction_report = StormShiftValidator().validate(
        fault_contradiction(scenario, cited),
        plan,
    )

    citation_check = "citation-ids-freshness-conflicts-no-entailment"
    assert not checks(missing_report)[citation_check].passed
    assert any(
        "stale" in detail
        for detail in checks(stale_report)[citation_check].details
    )
    assert any(
        "contradictory" in detail
        for detail in checks(contradiction_report)[citation_check].details
    )


def test_accessibility_attestations_are_mandatory() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    inaccessible = AccessibilityAttestation(
        screen_reader_structured=False,
        plain_language=False,
        nonvisual_route_equivalent=False,
        language_tags=("en",),
    )
    report = StormShiftValidator().validate(
        scenario,
        replace(plan, accessibility=inaccessible),
    )

    accessibility = checks(report)["declared-accessibility-fields-structural-only"]
    assert not accessibility.passed
    assert len(accessibility.details) == 4


def test_any_external_publication_state_is_rejected() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    unsafe = replace(
        plan,
        publication_disposition=PublicationDisposition.EXTERNAL_PUBLICATION,
        external_publication_attempted=True,
        external_targets=("public-alert-endpoint",),
    )
    report = StormShiftValidator().validate(scenario, unsafe)

    boundary = checks(report)["declared-publication-boundary-not-external-state"]
    assert not boundary.passed
    assert len(boundary.details) == 3


def test_capacity_loss_invalidates_the_previous_plan() -> None:
    scenario = stormshift_fixture()
    plan = build_reference_plan(scenario)
    degraded = fault_capacity_loss(scenario, "SIM-SHELTER-ALPHA", 1)
    report = StormShiftValidator().validate(degraded, plan)

    assert not checks(report)["structural-capacity-and-demand-arithmetic"].passed
    assert any(marker.kind is FaultKind.CAPACITY_LOSS for marker in degraded.faults)


def test_all_fault_transformations_are_pure_deterministic_and_typed() -> None:
    base = stormshift_fixture()
    cited = build_reference_plan(base).citations[0]

    def transform():  # type: ignore[no-untyped-def]
        scenario = fault_stale_artifact(base, cited)
        scenario = fault_contradiction(scenario, cited)
        scenario = fault_provider_429(scenario, "simulated-watsonx")
        scenario = fault_latency_multiplier(scenario, 2_500)
        scenario = fault_capacity_loss(scenario, "SIM-SHELTER-BRAVO", 10)
        return fault_budget_cut(scenario, 400)

    first = transform()
    replay = transform()

    assert base.faults == ()
    assert first.fixture_digest == replay.fixture_digest
    assert {marker.kind for marker in first.faults} == set(FaultKind)
    semantics = {marker.kind: marker.semantics for marker in first.faults}
    assert semantics[FaultKind.STALE_ARTIFACT] is (
        FaultSemantics.EXECUTED_FIXTURE_TRANSFORMATION
    )
    assert semantics[FaultKind.CONTRADICTION] is (
        FaultSemantics.EXECUTED_FIXTURE_TRANSFORMATION
    )
    assert semantics[FaultKind.CAPACITY_LOSS] is (
        FaultSemantics.EXECUTED_FIXTURE_TRANSFORMATION
    )
    assert semantics[FaultKind.PROVIDER_429] is FaultSemantics.MARKER_ONLY
    assert semantics[FaultKind.LATENCY_MULTIPLIER] is (
        FaultSemantics.PARAMETER_TRANSFORM_NOT_WIRED
    )
    assert semantics[FaultKind.BUDGET_CUT] is (
        FaultSemantics.PARAMETER_TRANSFORM_NOT_WIRED
    )
    assert first.latency_multiplier_permille == 2_500
    assert first.budget.max_tokens == base.budget.max_tokens * 400 // 1_000
    assert first.budget.max_cost_microusd == (
        base.budget.max_cost_microusd * 400 // 1_000
    )
    assert first.budget.max_context_bytes == (
        base.budget.max_context_bytes * 400 // 1_000
    )
    assert first.evidence_record(cited) is not None
    assert not first.evidence_record(cited).is_fresh(first.as_of_ms)  # type: ignore[union-attr]
