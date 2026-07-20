"""StormShift: a fictional Miami-Dade emergency-orchestration workload.

Every facility, route, condition, capacity, and alert in this module is synthetic.
Nothing here is an operational claim, a live public-safety feed, or authorization to
publish an alert. The workload exists only to test deterministic agent orchestration.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from .serialization import content_digest


FICTIONAL_DISCLAIMER = (
    "FICTIONAL SIMULATION — NOT FOR OPERATIONAL USE, DISPATCH, OR PUBLICATION"
)
FIXTURE_AS_OF_MS = 100_000
FIXTURE_FRESH_UNTIL_MS = 130_000
SCENARIO_SCHEMA_VERSION = "stormshift-sim/v1"
VALIDATION_SCHEMA_VERSION = "stormshift-validation/v1"

VALIDATION_SCOPE = (
    "deterministic structural checks over the pinned fictional fixture and plan fields",
    "fixture arithmetic for capacity, demand, route linkage, and modeled closures",
    "utility-priority list integrity and deterministic ordering",
    "caller-declared accessibility fields and allocation counts",
    "English/Spanish numeric-token parity and declared language tags",
    "citation ID presence, fixture freshness windows, and explicit conflict links",
    "declared simulation/publication fields inside the response-plan object",
)

VALIDATION_LIMITATIONS = (
    "passing does not establish real-world safety, feasibility, truth, or operational readiness",
    "accessibility checks do not run assistive technology or constitute a WCAG audit",
    "bilingual checks do not assess translation quality, semantic equivalence, or readability",
    "citations are not checked for entailment, source authenticity, provenance, or factual truth",
    "publication checks do not inspect networks, external systems, queues, or delivery state",
    "429 is marker-only; latency and budget parameters are not wired to a scheduler or executor",
)


class FacilityStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class RouteStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class FloodRisk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class UtilityStatus(str, Enum):
    ENERGIZED = "energized"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class PublicationDisposition(str, Enum):
    SIMULATION_DRAFT = "simulation_draft"
    EXTERNAL_PUBLICATION = "external_publication"


class FaultKind(str, Enum):
    STALE_ARTIFACT = "stale_artifact"
    CONTRADICTION = "contradiction"
    PROVIDER_429 = "provider_429"
    LATENCY_MULTIPLIER = "latency_multiplier"
    CAPACITY_LOSS = "capacity_loss"
    BUDGET_CUT = "budget_cut"


class FaultSemantics(str, Enum):
    """What a fault helper actually changes in this workload implementation."""

    EXECUTED_FIXTURE_TRANSFORMATION = "executed_fixture_transformation"
    MARKER_ONLY = "marker_only"
    PARAMETER_TRANSFORM_NOT_WIRED = "parameter_transform_not_wired"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """A synthetic evidence pointer with explicit temporal validity."""

    evidence_id: str
    subject_id: str
    assertion: str
    source: str
    observed_at_ms: int
    fresh_until_ms: int
    contradicts: tuple[str, ...] = ()

    def is_fresh(self, as_of_ms: int) -> bool:
        return self.observed_at_ms <= as_of_ms <= self.fresh_until_ms


@dataclass(frozen=True, slots=True)
class ShelterFixture:
    shelter_id: str
    display_name: str
    zone_id: str
    status: FacilityStatus
    capacity: int
    occupied: int
    accessible_capacity: int
    accessible_occupied: int
    pet_spaces: int
    evidence_id: str

    @property
    def available(self) -> int:
        return max(0, self.capacity - self.occupied)

    @property
    def accessible_available(self) -> int:
        return max(0, self.accessible_capacity - self.accessible_occupied)


@dataclass(frozen=True, slots=True)
class TransitFixture:
    route_id: str
    origin_zone_id: str
    destination_shelter_id: str
    status: RouteStatus
    capacity: int
    accessible_capacity: int
    segments: tuple[str, ...]
    evidence_id: str


@dataclass(frozen=True, slots=True)
class HospitalFixture:
    hospital_id: str
    display_name: str
    zone_id: str
    status: FacilityStatus
    staffed_beds: int
    occupied_beds: int
    backup_power_minutes: int
    evidence_id: str

    @property
    def available_beds(self) -> int:
        return max(0, self.staffed_beds - self.occupied_beds)


@dataclass(frozen=True, slots=True)
class FloodFixture:
    flood_id: str
    zone_id: str
    risk: FloodRisk
    simulated_depth_cm: int
    closed_segments: tuple[str, ...]
    evidence_id: str


@dataclass(frozen=True, slots=True)
class UtilityFixture:
    utility_id: str
    zone_id: str
    status: UtilityStatus
    available_capacity_percent: int
    simulated_restoration_minutes: int | None
    evidence_id: str


@dataclass(frozen=True, slots=True)
class AccessibilityRequirements:
    evacuees_requiring_accessible_space: int
    require_screen_reader_structure: bool = True
    require_plain_language: bool = True
    require_nonvisual_route_equivalent: bool = True
    required_language_tags: tuple[str, ...] = ("en", "es")


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    max_tokens: int
    max_cost_microusd: int
    max_context_bytes: int


@dataclass(frozen=True, slots=True)
class FaultMarker:
    kind: FaultKind
    target: str
    value: str
    semantics: FaultSemantics


@dataclass(frozen=True, slots=True)
class StormShiftScenario:
    schema_version: str
    scenario_id: str
    display_name: str
    disclaimer: str
    as_of_ms: int
    evacuee_demand: int
    shelters: tuple[ShelterFixture, ...]
    transit: tuple[TransitFixture, ...]
    hospitals: tuple[HospitalFixture, ...]
    floods: tuple[FloodFixture, ...]
    utilities: tuple[UtilityFixture, ...]
    evidence: tuple[EvidenceRecord, ...]
    accessibility: AccessibilityRequirements
    budget: ResourceBudget
    latency_multiplier_permille: int = 1_000
    faults: tuple[FaultMarker, ...] = ()

    @property
    def fixture_digest(self) -> str:
        return content_digest(self)

    def evidence_record(self, evidence_id: str) -> EvidenceRecord | None:
        return next(
            (record for record in self.evidence if record.evidence_id == evidence_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class ShelterAllocation:
    shelter_id: str
    evacuees: int
    accessible_evacuees: int


@dataclass(frozen=True, slots=True)
class RouteAssignment:
    route_id: str
    shelter_id: str
    passengers: int
    accessible_passengers: int


@dataclass(frozen=True, slots=True)
class HospitalReservation:
    hospital_id: str
    reserved_beds: int


@dataclass(frozen=True, slots=True)
class UtilityPriority:
    utility_id: str
    priority_rank: int


@dataclass(frozen=True, slots=True)
class AccessibilityAttestation:
    screen_reader_structured: bool
    plain_language: bool
    nonvisual_route_equivalent: bool
    language_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BilingualAlert:
    english: str
    spanish: str
    english_language_tag: str = "en"
    spanish_language_tag: str = "es"


@dataclass(frozen=True, slots=True)
class ResponsePlan:
    scenario_id: str
    allocations: tuple[ShelterAllocation, ...]
    routes: tuple[RouteAssignment, ...]
    hospital_reservations: tuple[HospitalReservation, ...]
    utility_priorities: tuple[UtilityPriority, ...]
    alert: BilingualAlert
    accessibility: AccessibilityAttestation
    citations: tuple[str, ...]
    publication_disposition: PublicationDisposition
    external_publication_attempted: bool = False
    external_targets: tuple[str, ...] = ()

    @property
    def plan_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    name: str
    passed: bool
    details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanValidationReport:
    """A digest-bound structural report whose scope and limitations travel with it."""

    schema_version: str
    scenario_digest: str
    plan_digest: str
    scope: tuple[str, ...]
    limitations: tuple[str, ...]
    checks: tuple[ValidationCheck, ...]
    passed: bool
    report_digest: str

    def unsigned_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "scenario_digest": self.scenario_digest,
            "plan_digest": self.plan_digest,
            "scope": self.scope,
            "limitations": self.limitations,
            "checks": self.checks,
            "passed": self.passed,
        }

    def verify_digest(self) -> bool:
        return self.report_digest == content_digest(self.unsigned_payload())


def _evidence(
    evidence_id: str,
    subject_id: str,
    assertion: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject_id=subject_id,
        assertion=f"SIMULATED: {assertion}",
        source="StormShift fictional fixture generator",
        observed_at_ms=90_000,
        fresh_until_ms=FIXTURE_FRESH_UNTIL_MS,
    )


def stormshift_fixture() -> StormShiftScenario:
    """Return the replay-stable, wholly fictional Miami-Dade workload."""

    shelters = (
        ShelterFixture(
            "SIM-SHELTER-ALPHA",
            "Fictional Shelter Alpha",
            "SIM-ZONE-NORTH",
            FacilityStatus.OPEN,
            capacity=130,
            occupied=30,
            accessible_capacity=30,
            accessible_occupied=15,
            pet_spaces=20,
            evidence_id="ev:shelter:alpha:v1",
        ),
        ShelterFixture(
            "SIM-SHELTER-BRAVO",
            "Fictional Shelter Bravo",
            "SIM-ZONE-SOUTH",
            FacilityStatus.OPEN,
            capacity=150,
            occupied=70,
            accessible_capacity=25,
            accessible_occupied=16,
            pet_spaces=12,
            evidence_id="ev:shelter:bravo:v1",
        ),
    )
    transit = (
        TransitFixture(
            "SIM-ROUTE-ALPHA",
            "SIM-EVAC-NORTH",
            "SIM-SHELTER-ALPHA",
            RouteStatus.OPEN,
            capacity=100,
            accessible_capacity=15,
            segments=("sim-upland-01", "sim-connector-01"),
            evidence_id="ev:transit:alpha:v1",
        ),
        TransitFixture(
            "SIM-ROUTE-BRAVO",
            "SIM-EVAC-SOUTH",
            "SIM-SHELTER-BRAVO",
            RouteStatus.OPEN,
            capacity=80,
            accessible_capacity=9,
            segments=("sim-upland-02", "sim-connector-02"),
            evidence_id="ev:transit:bravo:v1",
        ),
        TransitFixture(
            "SIM-ROUTE-CLOSED",
            "SIM-EVAC-NORTH",
            "SIM-SHELTER-BRAVO",
            RouteStatus.CLOSED,
            capacity=60,
            accessible_capacity=8,
            segments=("sim-low-road-x",),
            evidence_id="ev:transit:closed:v1",
        ),
    )
    hospitals = (
        HospitalFixture(
            "SIM-HOSPITAL-ONE",
            "Fictional Hospital One",
            "SIM-ZONE-CENTRAL",
            FacilityStatus.OPEN,
            staffed_beds=70,
            occupied_beds=55,
            backup_power_minutes=480,
            evidence_id="ev:hospital:one:v1",
        ),
        HospitalFixture(
            "SIM-HOSPITAL-TWO",
            "Fictional Hospital Two",
            "SIM-ZONE-WEST",
            FacilityStatus.OPEN,
            staffed_beds=50,
            occupied_beds=40,
            backup_power_minutes=360,
            evidence_id="ev:hospital:two:v1",
        ),
    )
    floods = (
        FloodFixture(
            "SIM-FLOOD-LOWLAND",
            "SIM-ZONE-LOWLAND",
            FloodRisk.HIGH,
            simulated_depth_cm=42,
            closed_segments=("sim-low-road-x", "sim-low-road-y"),
            evidence_id="ev:flood:lowland:v1",
        ),
        FloodFixture(
            "SIM-FLOOD-UPLAND",
            "SIM-ZONE-NORTH",
            FloodRisk.LOW,
            simulated_depth_cm=3,
            closed_segments=(),
            evidence_id="ev:flood:upland:v1",
        ),
    )
    utilities = (
        UtilityFixture(
            "SIM-UTILITY-CENTRAL",
            "SIM-ZONE-CENTRAL",
            UtilityStatus.DEGRADED,
            available_capacity_percent=62,
            simulated_restoration_minutes=180,
            evidence_id="ev:utility:central:v1",
        ),
        UtilityFixture(
            "SIM-UTILITY-WEST",
            "SIM-ZONE-WEST",
            UtilityStatus.ENERGIZED,
            available_capacity_percent=91,
            simulated_restoration_minutes=None,
            evidence_id="ev:utility:west:v1",
        ),
    )
    fixture_records: list[EvidenceRecord] = []
    for item in shelters:
        fixture_records.append(
            _evidence(item.evidence_id, item.shelter_id, "shelter capacity snapshot")
        )
    for item in transit:
        fixture_records.append(
            _evidence(item.evidence_id, item.route_id, "transit route availability")
        )
    for item in hospitals:
        fixture_records.append(
            _evidence(item.evidence_id, item.hospital_id, "staffed-bed snapshot")
        )
    for item in floods:
        fixture_records.append(
            _evidence(item.evidence_id, item.flood_id, "modeled flood condition")
        )
    for item in utilities:
        fixture_records.append(
            _evidence(item.evidence_id, item.utility_id, "utility capacity snapshot")
        )
    return StormShiftScenario(
        schema_version=SCENARIO_SCHEMA_VERSION,
        scenario_id="stormshift-miami-dade-fictional-v1",
        display_name="StormShift — Miami-Dade Fictional Simulation",
        disclaimer=FICTIONAL_DISCLAIMER,
        as_of_ms=FIXTURE_AS_OF_MS,
        evacuee_demand=180,
        shelters=shelters,
        transit=transit,
        hospitals=hospitals,
        floods=floods,
        utilities=utilities,
        evidence=tuple(sorted(fixture_records, key=lambda item: item.evidence_id)),
        accessibility=AccessibilityRequirements(24),
        budget=ResourceBudget(
            max_tokens=24_000,
            max_cost_microusd=80_000,
            max_context_bytes=160_000,
        ),
    )


def build_reference_plan(scenario: StormShiftScenario | None = None) -> ResponsePlan:
    """Build the deterministic passing plan for the unmodified fixture."""

    scenario = scenario or stormshift_fixture()
    allocations = (
        ShelterAllocation("SIM-SHELTER-ALPHA", 100, 15),
        ShelterAllocation("SIM-SHELTER-BRAVO", 80, 9),
    )
    routes = (
        RouteAssignment("SIM-ROUTE-ALPHA", "SIM-SHELTER-ALPHA", 100, 15),
        RouteAssignment("SIM-ROUTE-BRAVO", "SIM-SHELTER-BRAVO", 80, 9),
    )
    reservations = (
        HospitalReservation("SIM-HOSPITAL-ONE", 8),
        HospitalReservation("SIM-HOSPITAL-TWO", 4),
    )
    utility_priorities = (
        UtilityPriority("SIM-UTILITY-CENTRAL", 1),
        UtilityPriority("SIM-UTILITY-WEST", 2),
    )
    # Both language variants intentionally contain the same ordered numeric facts.
    english = (
        "SIMULATION ONLY. Move 180 people on 2 routes to 2 shelters. "
        "Reserve 12 hospital beds. Accessible places: 24. DO NOT PUBLISH."
    )
    spanish = (
        "SOLO SIMULACIÓN. Traslade a 180 personas por 2 rutas a 2 refugios. "
        "Reserve 12 camas de hospital. Plazas accesibles: 24. NO PUBLICAR."
    )
    selected_shelters = {item.shelter_id for item in allocations}
    selected_routes = {item.route_id for item in routes}
    selected_hospitals = {item.hospital_id for item in reservations}
    selected_utilities = {item.utility_id for item in utility_priorities}
    citations = {
        item.evidence_id for item in scenario.shelters if item.shelter_id in selected_shelters
    }
    citations.update(
        item.evidence_id for item in scenario.transit if item.route_id in selected_routes
    )
    citations.update(
        item.evidence_id for item in scenario.hospitals if item.hospital_id in selected_hospitals
    )
    citations.update(item.evidence_id for item in scenario.floods)
    citations.update(
        item.evidence_id for item in scenario.utilities if item.utility_id in selected_utilities
    )
    return ResponsePlan(
        scenario_id=scenario.scenario_id,
        allocations=allocations,
        routes=routes,
        hospital_reservations=reservations,
        utility_priorities=utility_priorities,
        alert=BilingualAlert(english, spanish),
        accessibility=AccessibilityAttestation(
            screen_reader_structured=True,
            plain_language=True,
            nonvisual_route_equivalent=True,
            language_tags=("en", "es"),
        ),
        citations=tuple(sorted(citations)),
        publication_disposition=PublicationDisposition.SIMULATION_DRAFT,
    )


def _duplicates(values: Iterable[str]) -> tuple[str, ...]:
    counts = Counter(values)
    return tuple(sorted(value for value, count in counts.items() if count > 1))


def _numbers(text: str) -> Counter[int]:
    return Counter(int(value) for value in re.findall(r"(?<![\w-])\d+(?![\w-])", text))


def _check(name: str, problems: Iterable[str]) -> ValidationCheck:
    details = tuple(sorted(set(problems)))
    return ValidationCheck(name, not details, details or ("passed",))


class StormShiftValidator:
    """Validate a plan without performing dispatch, publication, or other effects."""

    def validate(
        self,
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> PlanValidationReport:
        checks = (
            self._identity(scenario, plan),
            self._capacity(scenario, plan),
            self._routes(scenario, plan),
            self._closures(scenario, plan),
            self._utility_priorities(scenario, plan),
            self._accessibility(scenario, plan),
            self._bilingual_numbers(scenario, plan),
            self._citations(scenario, plan),
            self._publication(scenario, plan),
        )
        passed = all(check.passed for check in checks)
        unsigned = {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "scenario_digest": scenario.fixture_digest,
            "plan_digest": plan.plan_digest,
            "scope": VALIDATION_SCOPE,
            "limitations": VALIDATION_LIMITATIONS,
            "checks": checks,
            "passed": passed,
        }
        return PlanValidationReport(**unsigned, report_digest=content_digest(unsigned))

    @staticmethod
    def _identity(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        if plan.scenario_id != scenario.scenario_id:
            problems.append("plan scenario_id does not match the fixture")
        if "FICTIONAL SIMULATION" not in scenario.disclaimer:
            problems.append("scenario is missing the fictional-simulation disclaimer")
        for label, identifiers in (
            ("shelter", [item.shelter_id for item in scenario.shelters]),
            ("route", [item.route_id for item in scenario.transit]),
            ("hospital", [item.hospital_id for item in scenario.hospitals]),
            ("flood", [item.flood_id for item in scenario.floods]),
            ("utility", [item.utility_id for item in scenario.utilities]),
            ("evidence", [item.evidence_id for item in scenario.evidence]),
        ):
            duplicate = _duplicates(identifiers)
            if duplicate:
                problems.append(f"duplicate {label} IDs: {','.join(duplicate)}")
        return _check("structural-fixture-identity-and-simulation-boundary", problems)

    @staticmethod
    def _capacity(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        shelter_map = {item.shelter_id: item for item in scenario.shelters}
        hospital_map = {item.hospital_id: item for item in scenario.hospitals}
        if _duplicates(item.shelter_id for item in plan.allocations):
            problems.append("plan repeats a shelter allocation")
        if _duplicates(item.hospital_id for item in plan.hospital_reservations):
            problems.append("plan repeats a hospital reservation")
        for allocation in plan.allocations:
            shelter = shelter_map.get(allocation.shelter_id)
            if shelter is None:
                problems.append(f"unknown shelter: {allocation.shelter_id}")
                continue
            if min(allocation.evacuees, allocation.accessible_evacuees) < 0:
                problems.append(f"negative shelter allocation: {allocation.shelter_id}")
            if allocation.accessible_evacuees > allocation.evacuees:
                problems.append(
                    f"accessible allocation exceeds total: {allocation.shelter_id}"
                )
            if shelter.status is not FacilityStatus.OPEN:
                problems.append(f"shelter is closed: {allocation.shelter_id}")
            if allocation.evacuees > shelter.available:
                problems.append(
                    f"shelter capacity exceeded: {allocation.shelter_id} "
                    f"{allocation.evacuees}>{shelter.available}"
                )
            if allocation.accessible_evacuees > shelter.accessible_available:
                problems.append(
                    f"accessible shelter capacity exceeded: {allocation.shelter_id}"
                )
        if sum(item.evacuees for item in plan.allocations) != scenario.evacuee_demand:
            problems.append("shelter allocations do not conserve evacuee demand")
        for reservation in plan.hospital_reservations:
            hospital = hospital_map.get(reservation.hospital_id)
            if hospital is None:
                problems.append(f"unknown hospital: {reservation.hospital_id}")
                continue
            if reservation.reserved_beds < 0:
                problems.append(f"negative bed reservation: {reservation.hospital_id}")
            if hospital.status is not FacilityStatus.OPEN:
                problems.append(f"hospital is closed: {reservation.hospital_id}")
            if reservation.reserved_beds > hospital.available_beds:
                problems.append(f"hospital capacity exceeded: {reservation.hospital_id}")
        return _check("structural-capacity-and-demand-arithmetic", problems)

    @staticmethod
    def _routes(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        route_map = {item.route_id: item for item in scenario.transit}
        allocations = {item.shelter_id: item for item in plan.allocations}
        if _duplicates(item.route_id for item in plan.routes):
            problems.append("plan repeats a route assignment")
        for assignment in plan.routes:
            route = route_map.get(assignment.route_id)
            if route is None:
                problems.append(f"unknown route: {assignment.route_id}")
                continue
            if min(assignment.passengers, assignment.accessible_passengers) < 0:
                problems.append(f"negative route load: {assignment.route_id}")
            if assignment.accessible_passengers > assignment.passengers:
                problems.append(f"accessible route load exceeds total: {assignment.route_id}")
            if route.status is not RouteStatus.OPEN:
                problems.append(f"route is not open: {assignment.route_id}")
            if route.destination_shelter_id != assignment.shelter_id:
                problems.append(f"route destination mismatch: {assignment.route_id}")
            if assignment.passengers > route.capacity:
                problems.append(f"route capacity exceeded: {assignment.route_id}")
            if assignment.accessible_passengers > route.accessible_capacity:
                problems.append(f"accessible route capacity exceeded: {assignment.route_id}")
        if sum(item.passengers for item in plan.routes) != scenario.evacuee_demand:
            problems.append("route assignments do not conserve evacuee demand")
        for shelter_id, allocation in allocations.items():
            routed = sum(
                item.passengers for item in plan.routes if item.shelter_id == shelter_id
            )
            accessible = sum(
                item.accessible_passengers
                for item in plan.routes
                if item.shelter_id == shelter_id
            )
            if routed != allocation.evacuees:
                problems.append(f"route/shelter passenger mismatch: {shelter_id}")
            if accessible != allocation.accessible_evacuees:
                problems.append(f"route/shelter accessible mismatch: {shelter_id}")
        routed_shelters = {item.shelter_id for item in plan.routes}
        if routed_shelters - allocations.keys():
            problems.append("route targets a shelter without an allocation")
        return _check("structural-route-linkage-and-capacity", problems)

    @staticmethod
    def _closures(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        route_map = {item.route_id: item for item in scenario.transit}
        closed_segments = {
            segment for flood in scenario.floods for segment in flood.closed_segments
        }
        for assignment in plan.routes:
            route = route_map.get(assignment.route_id)
            if route is None:
                continue
            conflicts = sorted(set(route.segments) & closed_segments)
            if conflicts:
                problems.append(
                    f"route crosses modeled closure: {route.route_id} ({','.join(conflicts)})"
                )
        return _check("structural-modeled-closure-intersection", problems)

    @staticmethod
    def _utility_priorities(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        """Check only the declared utility-priority list, not grid conditions."""

        problems: list[str] = []
        fixture_ids = {item.utility_id for item in scenario.utilities}
        plan_ids = [item.utility_id for item in plan.utility_priorities]
        if not plan_ids:
            problems.append("utility-priority list is empty")
        duplicates = _duplicates(plan_ids)
        if duplicates:
            problems.append(f"duplicate utility IDs: {','.join(duplicates)}")
        unknown = sorted(set(plan_ids) - fixture_ids)
        missing = sorted(fixture_ids - set(plan_ids))
        if unknown:
            problems.append(f"unknown utility IDs: {','.join(unknown)}")
        if missing:
            problems.append(f"missing utility IDs: {','.join(missing)}")

        ranks = [item.priority_rank for item in plan.utility_priorities]
        if any(rank <= 0 for rank in ranks):
            problems.append("utility priority ranks must be positive")
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            problems.append("utility priority ranks must be unique and consecutive from 1")

        identities_complete = set(plan_ids) == fixture_ids and not duplicates
        ranks_valid = sorted(ranks) == list(range(1, len(ranks) + 1))
        if identities_complete and ranks_valid:
            expected = tuple(
                item.utility_id
                for item in sorted(
                    scenario.utilities,
                    key=lambda item: (
                        item.status is UtilityStatus.ENERGIZED,
                        item.available_capacity_percent,
                        item.status.value,
                        item.utility_id,
                    ),
                )
            )
            actual = tuple(
                item.utility_id
                for item in sorted(
                    plan.utility_priorities,
                    key=lambda item: item.priority_rank,
                )
            )
            if actual != expected:
                problems.append(
                    "utility ordering must put lower-capacity degraded/offline "
                    f"fixtures before energized fixtures; expected {','.join(expected)}"
                )
        return _check("structural-utility-priority-list-integrity", problems)

    @staticmethod
    def _accessibility(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        required = scenario.accessibility
        allocated = sum(item.accessible_evacuees for item in plan.allocations)
        routed = sum(item.accessible_passengers for item in plan.routes)
        if allocated != required.evacuees_requiring_accessible_space:
            problems.append("accessible shelter allocation does not meet the obligation")
        if routed != required.evacuees_requiring_accessible_space:
            problems.append("accessible transit allocation does not meet the obligation")
        if required.require_screen_reader_structure and not plan.accessibility.screen_reader_structured:
            problems.append("screen-reader structure is not attested")
        if required.require_plain_language and not plan.accessibility.plain_language:
            problems.append("plain-language output is not attested")
        if (
            required.require_nonvisual_route_equivalent
            and not plan.accessibility.nonvisual_route_equivalent
        ):
            problems.append("nonvisual route equivalent is not attested")
        missing_tags = set(required.required_language_tags) - set(
            plan.accessibility.language_tags
        )
        if missing_tags:
            problems.append(f"missing accessibility language tags: {','.join(sorted(missing_tags))}")
        return _check("declared-accessibility-fields-structural-only", problems)

    @staticmethod
    def _bilingual_numbers(
        _scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        english_numbers = _numbers(plan.alert.english)
        spanish_numbers = _numbers(plan.alert.spanish)
        if not plan.alert.english.strip() or not plan.alert.spanish.strip():
            problems.append("both English and Spanish alert bodies are required")
        if plan.alert.english_language_tag != "en" or plan.alert.spanish_language_tag != "es":
            problems.append("alert language tags must be en and es")
        if english_numbers != spanish_numbers:
            problems.append("English and Spanish numeric facts differ")
        return _check("bilingual-numeric-parity-structural-only", problems)

    @staticmethod
    def _citations(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        evidence_map = {item.evidence_id: item for item in scenario.evidence}
        shelter_map = {item.shelter_id: item for item in scenario.shelters}
        route_map = {item.route_id: item for item in scenario.transit}
        hospital_map = {item.hospital_id: item for item in scenario.hospitals}
        utility_map = {item.utility_id: item for item in scenario.utilities}
        required: set[str] = {item.evidence_id for item in scenario.floods}
        required.update(
            shelter_map[item.shelter_id].evidence_id
            for item in plan.allocations
            if item.shelter_id in shelter_map
        )
        required.update(
            route_map[item.route_id].evidence_id
            for item in plan.routes
            if item.route_id in route_map
        )
        required.update(
            hospital_map[item.hospital_id].evidence_id
            for item in plan.hospital_reservations
            if item.hospital_id in hospital_map
        )
        required.update(
            utility_map[item.utility_id].evidence_id
            for item in plan.utility_priorities
            if item.utility_id in utility_map
        )
        citation_set = set(plan.citations)
        missing = sorted(required - citation_set)
        unknown = sorted(citation_set - evidence_map.keys())
        if _duplicates(plan.citations):
            problems.append("citation list contains duplicates")
        if missing:
            problems.append(f"missing required citations: {','.join(missing)}")
        if unknown:
            problems.append(f"unknown citations: {','.join(unknown)}")
        for evidence_id in sorted(citation_set & evidence_map.keys()):
            record = evidence_map[evidence_id]
            if not record.is_fresh(scenario.as_of_ms):
                problems.append(f"citation is stale or not yet valid: {evidence_id}")
            conflicts = sorted(
                other.evidence_id
                for other in scenario.evidence
                if (
                    evidence_id in other.contradicts
                    or other.evidence_id in record.contradicts
                )
                and other.is_fresh(scenario.as_of_ms)
            )
            if conflicts:
                problems.append(
                    f"citation has fresh contradictory evidence: {evidence_id} "
                    f"({','.join(conflicts)})"
                )
        return _check("citation-ids-freshness-conflicts-no-entailment", problems)

    @staticmethod
    def _publication(
        scenario: StormShiftScenario,
        plan: ResponsePlan,
    ) -> ValidationCheck:
        problems: list[str] = []
        if plan.publication_disposition is not PublicationDisposition.SIMULATION_DRAFT:
            problems.append("plan disposition is not simulation_draft")
        if plan.external_publication_attempted:
            problems.append("plan records an external publication attempt")
        if plan.external_targets:
            problems.append("simulation plan contains external publication targets")
        if "SIMULATION ONLY" not in plan.alert.english.upper():
            problems.append("English alert lacks an explicit simulation-only boundary")
        if "SOLO SIMULACIÓN" not in plan.alert.spanish.upper():
            problems.append("Spanish alert lacks an explicit simulation-only boundary")
        if scenario.disclaimer != FICTIONAL_DISCLAIMER:
            problems.append("fixture disclaimer changed from the pinned fictional boundary")
        return _check("declared-publication-boundary-not-external-state", problems)


def _faults_with(scenario: StormShiftScenario, marker: FaultMarker) -> tuple[FaultMarker, ...]:
    values = set(scenario.faults)
    values.add(marker)
    return tuple(
        sorted(
            values,
            key=lambda item: (
                item.kind.value,
                item.target,
                item.value,
                item.semantics.value,
            ),
        )
    )


def fault_stale_artifact(
    scenario: StormShiftScenario,
    evidence_id: str,
) -> StormShiftScenario:
    """Execute a fixture transform that expires one synthetic evidence record."""

    if scenario.evidence_record(evidence_id) is None:
        raise KeyError(f"unknown evidence_id: {evidence_id}")
    transformed = tuple(
        replace(record, fresh_until_ms=scenario.as_of_ms - 1)
        if record.evidence_id == evidence_id
        else record
        for record in scenario.evidence
    )
    marker = FaultMarker(
        FaultKind.STALE_ARTIFACT,
        evidence_id,
        "expired",
        FaultSemantics.EXECUTED_FIXTURE_TRANSFORMATION,
    )
    return replace(scenario, evidence=transformed, faults=_faults_with(scenario, marker))


def fault_contradiction(
    scenario: StormShiftScenario,
    evidence_id: str,
) -> StormShiftScenario:
    """Execute a fixture transform that adds explicitly conflicting evidence."""

    original = scenario.evidence_record(evidence_id)
    if original is None:
        raise KeyError(f"unknown evidence_id: {evidence_id}")
    conflict_id = f"fault:contradiction:{evidence_id}"
    conflict = EvidenceRecord(
        evidence_id=conflict_id,
        subject_id=original.subject_id,
        assertion=f"SIMULATED CONFLICT with {evidence_id}",
        source="StormShift deterministic fault injector",
        observed_at_ms=scenario.as_of_ms,
        fresh_until_ms=max(scenario.as_of_ms, original.fresh_until_ms),
        contradicts=(evidence_id,),
    )
    evidence_by_id = {record.evidence_id: record for record in scenario.evidence}
    evidence_by_id[conflict_id] = conflict
    marker = FaultMarker(
        FaultKind.CONTRADICTION,
        evidence_id,
        conflict_id,
        FaultSemantics.EXECUTED_FIXTURE_TRANSFORMATION,
    )
    return replace(
        scenario,
        evidence=tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id)),
        faults=_faults_with(scenario, marker),
    )


def fault_provider_429(
    scenario: StormShiftScenario,
    provider: str,
) -> StormShiftScenario:
    """Attach a marker only; no provider, scheduler, or network call is exercised."""

    if not provider:
        raise ValueError("provider is required")
    marker = FaultMarker(
        FaultKind.PROVIDER_429,
        provider,
        "HTTP 429 (simulated marker only)",
        FaultSemantics.MARKER_ONLY,
    )
    return replace(scenario, faults=_faults_with(scenario, marker))


def fault_latency_multiplier(
    scenario: StormShiftScenario,
    multiplier_permille: int,
) -> StormShiftScenario:
    """Transform a fixture parameter that is not yet wired to runtime scheduling."""

    if multiplier_permille < 1_000:
        raise ValueError("latency fault multiplier must be at least 1000 permille")
    marker = FaultMarker(
        FaultKind.LATENCY_MULTIPLIER,
        "all-backends",
        str(multiplier_permille),
        FaultSemantics.PARAMETER_TRANSFORM_NOT_WIRED,
    )
    return replace(
        scenario,
        latency_multiplier_permille=multiplier_permille,
        faults=_faults_with(scenario, marker),
    )


def fault_capacity_loss(
    scenario: StormShiftScenario,
    shelter_id: str,
    lost_spaces: int,
) -> StormShiftScenario:
    """Execute a fixture transform that reduces one shelter's declared capacity."""

    if lost_spaces <= 0:
        raise ValueError("lost_spaces must be positive")
    if not any(item.shelter_id == shelter_id for item in scenario.shelters):
        raise KeyError(f"unknown shelter_id: {shelter_id}")
    transformed = tuple(
        replace(item, capacity=max(0, item.capacity - lost_spaces))
        if item.shelter_id == shelter_id
        else item
        for item in scenario.shelters
    )
    marker = FaultMarker(
        FaultKind.CAPACITY_LOSS,
        shelter_id,
        str(lost_spaces),
        FaultSemantics.EXECUTED_FIXTURE_TRANSFORMATION,
    )
    return replace(scenario, shelters=transformed, faults=_faults_with(scenario, marker))


def fault_budget_cut(
    scenario: StormShiftScenario,
    remaining_permille: int,
) -> StormShiftScenario:
    """Transform budget parameters that are not yet wired to scheduler admission."""

    if not 0 <= remaining_permille < 1_000:
        raise ValueError("budget-cut remaining_permille must be between 0 and 999")
    budget = ResourceBudget(
        max_tokens=scenario.budget.max_tokens * remaining_permille // 1_000,
        max_cost_microusd=(
            scenario.budget.max_cost_microusd * remaining_permille // 1_000
        ),
        max_context_bytes=(
            scenario.budget.max_context_bytes * remaining_permille // 1_000
        ),
    )
    marker = FaultMarker(
        FaultKind.BUDGET_CUT,
        "run-envelope",
        str(remaining_permille),
        FaultSemantics.PARAMETER_TRANSFORM_NOT_WIRED,
    )
    return replace(scenario, budget=budget, faults=_faults_with(scenario, marker))
