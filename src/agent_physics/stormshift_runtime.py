"""Deterministic StormShift execution on the durable fixture executor.

This module deliberately has no network, model, shell, or public-safety adapter.
Its workers are trusted in-process fixture functions.  The final declared write is
materialized only as a durable ``PROPOSED`` effect intent; it is never dispatched.
The attached :class:`StormShiftValidator` report is structural only and carries
its own scope and limitations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .contracts import RunEnvelope, TaskContract
from .effects import EffectIntent, EffectState, SQLiteEffectBroker
from .examples import miami_eoc_envelope, miami_eoc_graph
from .executor import (
    AsyncGraphExecutor,
    ExecutionResult,
    FixtureWorker,
    RunState,
    TaskExecutionContext,
    WorkerResult,
)
from .run_store import SQLiteRunStore, Usage
from .serialization import content_digest, normalize
from .stormshift import (
    AccessibilityAttestation,
    BilingualAlert,
    HospitalReservation,
    PlanValidationReport,
    PublicationDisposition,
    ResponsePlan,
    RouteAssignment,
    ShelterAllocation,
    StormShiftScenario,
    StormShiftValidator,
    UtilityPriority,
    build_reference_plan,
    stormshift_fixture,
)


RUNTIME_SCHEMA_VERSION = "stormshift-runtime/v1"
STRUCTURAL_VALIDATOR_REVISION = "stormshift-structural-validator/v1"
PUBLISH_TASK_ID = "publish_simulated_alert"

PURE_TASK_IDS = (
    "incident_intake",
    "shelter_status",
    "transit_status",
    "flood_zones",
    "hospital_capacity",
    "utility_outages",
    "social_signal_scan",
    "response_plan",
    "safety_review",
    "multilingual_alert",
)


class StormShiftRuntimeInvariantError(RuntimeError):
    """A durable output violated the fixture runtime's internal contract."""


@dataclass(frozen=True, slots=True)
class StormShiftRuntimeResult:
    """Typed view over one fixture execution and its proposed terminal effect."""

    execution: ExecutionResult
    response_plan: ResponsePlan
    validation: PlanValidationReport
    alert_preview: dict[str, object]
    effect_intent: EffectIntent
    worker_call_counts: dict[str, int]
    external_calls_made: bool = False
    model_calls_made: bool = False
    validator_kind: str = "deterministic_structural_only"


def stormshift_envelope() -> RunEnvelope:
    """Return the fixed execution envelope used by the demonstration graph.

    StormShift's budget-fault parameter intentionally remains unwired, matching
    the validator's explicit limitations.  The graph still receives FINITE's
    independently declared token, cost, context, deadline, and concurrency caps.
    """

    return miami_eoc_envelope()


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise StormShiftRuntimeInvariantError(f"{label} must be a string-keyed object")
    return cast(dict[str, Any], value)


def _require_sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise StormShiftRuntimeInvariantError(f"{label} must be an array")
    return cast(list[Any], value)


def _response_plan_from_output(output: object) -> ResponsePlan:
    envelope = _require_mapping(output, "response_plan output")
    payload = _require_mapping(envelope.get("plan"), "response_plan.plan")
    try:
        plan = ResponsePlan(
            scenario_id=str(payload["scenario_id"]),
            allocations=tuple(
                ShelterAllocation(
                    shelter_id=str(item["shelter_id"]),
                    evacuees=int(item["evacuees"]),
                    accessible_evacuees=int(item["accessible_evacuees"]),
                )
                for item in (
                    _require_mapping(value, "allocation")
                    for value in _require_sequence(payload["allocations"], "allocations")
                )
            ),
            routes=tuple(
                RouteAssignment(
                    route_id=str(item["route_id"]),
                    shelter_id=str(item["shelter_id"]),
                    passengers=int(item["passengers"]),
                    accessible_passengers=int(item["accessible_passengers"]),
                )
                for item in (
                    _require_mapping(value, "route")
                    for value in _require_sequence(payload["routes"], "routes")
                )
            ),
            hospital_reservations=tuple(
                HospitalReservation(
                    hospital_id=str(item["hospital_id"]),
                    reserved_beds=int(item["reserved_beds"]),
                )
                for item in (
                    _require_mapping(value, "hospital reservation")
                    for value in _require_sequence(
                        payload["hospital_reservations"],
                        "hospital_reservations",
                    )
                )
            ),
            utility_priorities=tuple(
                UtilityPriority(
                    utility_id=str(item["utility_id"]),
                    priority_rank=int(item["priority_rank"]),
                )
                for item in (
                    _require_mapping(value, "utility priority")
                    for value in _require_sequence(
                        payload["utility_priorities"],
                        "utility_priorities",
                    )
                )
            ),
            alert=BilingualAlert(
                english=str(_require_mapping(payload["alert"], "alert")["english"]),
                spanish=str(_require_mapping(payload["alert"], "alert")["spanish"]),
                english_language_tag=str(
                    _require_mapping(payload["alert"], "alert")["english_language_tag"]
                ),
                spanish_language_tag=str(
                    _require_mapping(payload["alert"], "alert")["spanish_language_tag"]
                ),
            ),
            accessibility=AccessibilityAttestation(
                screen_reader_structured=bool(
                    _require_mapping(payload["accessibility"], "accessibility")[
                        "screen_reader_structured"
                    ]
                ),
                plain_language=bool(
                    _require_mapping(payload["accessibility"], "accessibility")[
                        "plain_language"
                    ]
                ),
                nonvisual_route_equivalent=bool(
                    _require_mapping(payload["accessibility"], "accessibility")[
                        "nonvisual_route_equivalent"
                    ]
                ),
                language_tags=tuple(
                    str(value)
                    for value in _require_sequence(
                        _require_mapping(payload["accessibility"], "accessibility")[
                            "language_tags"
                        ],
                        "language_tags",
                    )
                ),
            ),
            citations=tuple(
                str(value)
                for value in _require_sequence(payload["citations"], "citations")
            ),
            publication_disposition=PublicationDisposition(
                str(payload["publication_disposition"])
            ),
            external_publication_attempted=bool(payload["external_publication_attempted"]),
            external_targets=tuple(
                str(value)
                for value in _require_sequence(
                    payload["external_targets"],
                    "external_targets",
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StormShiftRuntimeInvariantError("response_plan output is malformed") from exc
    if envelope.get("plan_digest") != plan.plan_digest:
        raise StormShiftRuntimeInvariantError("response_plan digest does not match its payload")
    return plan


class StormShiftFixtureWorkers:
    """Trusted deterministic workers for the pinned, fictional scenario."""

    def __init__(self, scenario: StormShiftScenario) -> None:
        self.scenario = scenario
        self._reference_plan = build_reference_plan(scenario)
        self._validator = StormShiftValidator()
        self._calls = {task_id: 0 for task_id in PURE_TASK_IDS}

    @property
    def call_counts(self) -> dict[str, int]:
        return dict(self._calls)

    @property
    def workers(self) -> dict[str, FixtureWorker]:
        return {task_id: self.execute_task for task_id in PURE_TASK_IDS}

    def _base(self, kind: str) -> dict[str, object]:
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "kind": kind,
            "scenario_id": self.scenario.scenario_id,
            "scenario_digest": self.scenario.fixture_digest,
            "fixture_only": True,
            "external_calls_made": False,
            "model_calls_made": False,
        }

    def _output_for(self, task_id: str) -> dict[str, object]:
        scenario = self.scenario
        output = self._base(task_id)
        if task_id == "incident_intake":
            output.update(
                {
                    "as_of_ms": scenario.as_of_ms,
                    "evacuee_demand": scenario.evacuee_demand,
                    "disclaimer": scenario.disclaimer,
                    "fixture_schema_version": scenario.schema_version,
                }
            )
        elif task_id == "shelter_status":
            shelters = [
                {
                    "shelter_id": item.shelter_id,
                    "status": item.status.value,
                    "available": item.available,
                    "accessible_available": item.accessible_available,
                    "evidence_id": item.evidence_id,
                }
                for item in sorted(scenario.shelters, key=lambda item: item.shelter_id)
            ]
            output.update(
                {
                    "shelters": shelters,
                    "total_available": sum(item.available for item in scenario.shelters),
                    "total_accessible_available": sum(
                        item.accessible_available for item in scenario.shelters
                    ),
                }
            )
        elif task_id == "transit_status":
            output["routes"] = [
                {
                    "route_id": item.route_id,
                    "destination_shelter_id": item.destination_shelter_id,
                    "status": item.status.value,
                    "capacity": item.capacity,
                    "accessible_capacity": item.accessible_capacity,
                    "segments": list(item.segments),
                    "evidence_id": item.evidence_id,
                }
                for item in sorted(scenario.transit, key=lambda item: item.route_id)
            ]
        elif task_id == "flood_zones":
            output.update(
                {
                    "floods": [
                        {
                            "flood_id": item.flood_id,
                            "zone_id": item.zone_id,
                            "risk": item.risk.value,
                            "simulated_depth_cm": item.simulated_depth_cm,
                            "closed_segments": list(item.closed_segments),
                            "evidence_id": item.evidence_id,
                        }
                        for item in sorted(scenario.floods, key=lambda item: item.flood_id)
                    ],
                    "closed_segments": sorted(
                        {
                            segment
                            for flood in scenario.floods
                            for segment in flood.closed_segments
                        }
                    ),
                }
            )
        elif task_id == "hospital_capacity":
            output["hospitals"] = [
                {
                    "hospital_id": item.hospital_id,
                    "status": item.status.value,
                    "available_beds": item.available_beds,
                    "backup_power_minutes": item.backup_power_minutes,
                    "evidence_id": item.evidence_id,
                }
                for item in sorted(scenario.hospitals, key=lambda item: item.hospital_id)
            ]
        elif task_id == "utility_outages":
            output["utilities"] = [
                {
                    "utility_id": item.utility_id,
                    "status": item.status.value,
                    "available_capacity_percent": item.available_capacity_percent,
                    "simulated_restoration_minutes": item.simulated_restoration_minutes,
                    "evidence_id": item.evidence_id,
                }
                for item in sorted(
                    scenario.utilities,
                    key=lambda item: (item.available_capacity_percent, item.utility_id),
                )
            ]
        elif task_id == "social_signal_scan":
            output.update(
                {
                    "live_scan_performed": False,
                    "signals": [],
                    "reason": "no live or external source is connected to this fixture runtime",
                }
            )
        elif task_id == "response_plan":
            output.update(
                {
                    "plan": normalize(self._reference_plan),
                    "plan_digest": self._reference_plan.plan_digest,
                    "input_task_ids": [
                        "flood_zones",
                        "hospital_capacity",
                        "shelter_status",
                        "transit_status",
                        "utility_outages",
                    ],
                }
            )
        elif task_id == "safety_review":
            report = self._validator.validate(scenario, self._reference_plan)
            output.update(
                {
                    "plan_digest": self._reference_plan.plan_digest,
                    "report": normalize(report),
                    "report_digest": report.report_digest,
                    "passed": report.passed,
                    "structural_only": True,
                    "external_state_checked": False,
                }
            )
        elif task_id == "multilingual_alert":
            output.update(
                {
                    "plan_digest": self._reference_plan.plan_digest,
                    "preview": normalize(self._reference_plan.alert),
                    "publication_disposition": (
                        self._reference_plan.publication_disposition.value
                    ),
                    "simulation_only": True,
                    "external_publication_attempted": False,
                }
            )
        else:
            raise StormShiftRuntimeInvariantError(f"unknown fixture task {task_id!r}")
        return output

    @staticmethod
    def _assert_dependencies(context: TaskExecutionContext) -> None:
        expected = set(context.task.dependencies)
        actual = set(context.dependency_outputs)
        if actual != expected:
            raise StormShiftRuntimeInvariantError(
                f"{context.task.task_id} dependency set differs: {sorted(actual)}"
            )
        for dependency, value in context.dependency_outputs.items():
            output = _require_mapping(value, f"{dependency} output")
            if output.get("scenario_id") is None or output.get("scenario_digest") is None:
                raise StormShiftRuntimeInvariantError(
                    f"{dependency} output is not bound to a scenario"
                )

    async def execute_task(self, context: TaskExecutionContext) -> WorkerResult:
        """Return one replay-stable JSON output without any external call."""

        task_id = context.task.task_id
        if task_id not in self._calls:
            raise StormShiftRuntimeInvariantError(f"worker does not own task {task_id!r}")
        self._assert_dependencies(context)
        for dependency, value in context.dependency_outputs.items():
            output = _require_mapping(value, f"{dependency} output")
            if output["scenario_id"] != self.scenario.scenario_id:
                raise StormShiftRuntimeInvariantError(
                    f"{dependency} output has a different scenario_id"
                )
            if output["scenario_digest"] != self.scenario.fixture_digest:
                raise StormShiftRuntimeInvariantError(
                    f"{dependency} output has a different scenario digest"
                )
            if output != self._output_for(dependency):
                raise StormShiftRuntimeInvariantError(
                    f"{dependency} output differs from its pinned fixture value"
                )
        self._calls[task_id] += 1
        return WorkerResult(output=self._output_for(task_id), actual_usage=Usage())

    async def validate_output(self, task: TaskContract, output: object) -> bool:
        """Recompute the expected fixture output, including during restart."""

        if task.task_id not in self._calls:
            return False
        if output != self._output_for(task.task_id):
            return False
        if task.task_id == "response_plan":
            try:
                plan = _response_plan_from_output(output)
            except StormShiftRuntimeInvariantError:
                return False
            return self._validator.validate(self.scenario, plan).passed
        if task.task_id == "safety_review":
            return bool(cast(dict[str, object], output).get("passed"))
        return True


class StormShiftRuntime:
    """Execute and resume the fictional StormShift graph through FINITE's kernel."""

    def __init__(
        self,
        store: SQLiteRunStore,
        effect_broker: SQLiteEffectBroker,
        *,
        scenario: StormShiftScenario | None = None,
    ) -> None:
        self.scenario = scenario or stormshift_fixture()
        self.store = store
        self.effect_broker = effect_broker
        self.fixture_workers = StormShiftFixtureWorkers(self.scenario)
        validator_revision = (
            f"{STRUCTURAL_VALIDATOR_REVISION}:{self.scenario.fixture_digest}"
        )
        self.executor = AsyncGraphExecutor(
            store,
            workers=self.fixture_workers.workers,
            output_validator=self.fixture_workers.validate_output,
            effect_broker=effect_broker,
            validator_revision=validator_revision,
        )

    async def execute(self, *, run_id: str) -> StormShiftRuntimeResult:
        """Execute or resume, returning typed plan, report, preview, and intent."""

        execution = await self.executor.execute(
            miami_eoc_graph(),
            stormshift_envelope(),
            run_id=run_id,
        )
        if execution.run_state is not RunState.AWAITING_EFFECTS:
            raise StormShiftRuntimeInvariantError(
                "StormShift must stop with its declared write awaiting effects"
            )

        response_plan = _response_plan_from_output(execution.outputs["response_plan"])
        validation = StormShiftValidator().validate(self.scenario, response_plan)
        if not validation.passed or not validation.verify_digest():
            raise StormShiftRuntimeInvariantError(
                "the final response plan failed deterministic structural validation"
            )

        alert_output = _require_mapping(
            execution.outputs["multilingual_alert"],
            "multilingual_alert output",
        )
        alert_preview = _require_mapping(alert_output.get("preview"), "alert preview")
        effect_output = _require_mapping(
            execution.outputs[PUBLISH_TASK_ID],
            "publish effect output",
        )
        intent_id = effect_output.get("effect_intent_id")
        if not isinstance(intent_id, str):
            raise StormShiftRuntimeInvariantError("publish effect output lacks an intent ID")
        effect_intent = self.effect_broker.get(intent_id)
        if (
            effect_intent.state is not EffectState.PROPOSED
            or effect_output.get("effect_state") != EffectState.PROPOSED.value
            or effect_output.get("executed_externally") is not False
        ):
            raise StormShiftRuntimeInvariantError(
                "the declared write crossed the proposed-intent boundary"
            )

        safety_output = _require_mapping(
            execution.outputs["safety_review"],
            "safety_review output",
        )
        if safety_output.get("report_digest") != validation.report_digest:
            raise StormShiftRuntimeInvariantError(
                "durable safety review is not bound to the final validation report"
            )
        if content_digest(alert_preview) != content_digest(normalize(response_plan.alert)):
            raise StormShiftRuntimeInvariantError(
                "alert preview is not bound to the final response plan"
            )

        return StormShiftRuntimeResult(
            execution=execution,
            response_plan=response_plan,
            validation=validation,
            alert_preview=alert_preview,
            effect_intent=effect_intent,
            worker_call_counts=self.fixture_workers.call_counts,
        )
