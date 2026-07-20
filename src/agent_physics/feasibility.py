"""Preflight analysis and content-addressed admission certificates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contracts import RunEnvelope
from .graph import ExecutionGraph
from .scheduler import SchedulePolicy, ScheduleResult, Scheduler
from .serialization import content_digest, normalize


class FeasibilityStatus(str, Enum):
    FEASIBLE = "feasible"
    DEGRADED = "degraded"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ConstraintCheck:
    name: str
    passed: bool
    observed: int | float | str
    limit: int | float | str
    unit: str


@dataclass(frozen=True, slots=True)
class FeasibilityCertificate:
    schema_version: str
    status: FeasibilityStatus
    graph_digest: str
    envelope_digest: str
    schedule_digest: str
    certificate_digest: str
    model_bound_ms: int
    projected_makespan_ms: int
    selected_backends: tuple[tuple[str, str], ...]
    skipped_optional_tasks: tuple[str, ...]
    checks: tuple[ConstraintCheck, ...]
    assumptions: tuple[str, ...]
    failure_reason: str | None = None

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "graph_digest": self.graph_digest,
            "envelope_digest": self.envelope_digest,
            "schedule_digest": self.schedule_digest,
            "model_bound_ms": self.model_bound_ms,
            "projected_makespan_ms": self.projected_makespan_ms,
            "selected_backends": self.selected_backends,
            "skipped_optional_tasks": self.skipped_optional_tasks,
            "checks": self.checks,
            "assumptions": self.assumptions,
            "failure_reason": self.failure_reason,
        }

    def verify_digest(self) -> bool:
        return self.certificate_digest == content_digest(self.unsigned_payload())

    def as_dict(self) -> dict[str, Any]:
        payload = normalize(self.unsigned_payload())
        payload["certificate_digest"] = self.certificate_digest
        return payload


class FeasibilityAnalyzer:
    """Create deterministic preflight evidence from a pinned graph and envelope."""

    SCHEMA_VERSION = "agent-physics-feasibility/v1"

    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self.scheduler = scheduler or Scheduler()

    def analyze(
        self,
        graph: ExecutionGraph,
        envelope: RunEnvelope,
        policy: SchedulePolicy = SchedulePolicy.ADAPTIVE,
    ) -> tuple[FeasibilityCertificate, ScheduleResult]:
        result = self.scheduler.schedule(graph, envelope, policy)
        checks = (
            ConstraintCheck(
                "deadline",
                result.success and result.makespan_ms <= envelope.deadline_ms,
                result.makespan_ms,
                envelope.deadline_ms,
                "ms",
            ),
            ConstraintCheck(
                "tokens",
                result.total_tokens <= envelope.max_tokens,
                result.total_tokens,
                envelope.max_tokens,
                "tokens",
            ),
            ConstraintCheck(
                "cost",
                result.total_cost_microusd <= envelope.max_cost_microusd,
                result.total_cost_microusd,
                envelope.max_cost_microusd,
                "micro-USD",
            ),
            ConstraintCheck(
                "context_movement",
                result.total_context_bytes <= envelope.max_context_bytes,
                result.total_context_bytes,
                envelope.max_context_bytes,
                "bytes",
            ),
            ConstraintCheck(
                "modeled_success_probability",
                (
                    result.success
                    and result.modeled_success_probability
                    >= envelope.min_modeled_success_probability
                ),
                result.modeled_success_probability,
                envelope.min_modeled_success_probability,
                "independent-profile probability",
            ),
            ConstraintCheck(
                "required_work",
                result.success,
                "complete" if result.success else "incomplete",
                "complete",
                "state",
            ),
        )
        if result.success and result.skipped:
            status = FeasibilityStatus.DEGRADED
        elif result.success:
            status = FeasibilityStatus.FEASIBLE
        else:
            status = FeasibilityStatus.REFUSED

        graph_hash = content_digest(graph)
        envelope_hash = content_digest(envelope)
        schedule_hash = content_digest(result.as_dict())
        unsigned = {
            "schema_version": self.SCHEMA_VERSION,
            "status": status,
            "graph_digest": graph_hash,
            "envelope_digest": envelope_hash,
            "schedule_digest": schedule_hash,
            "model_bound_ms": result.model_bound_ms,
            "projected_makespan_ms": result.makespan_ms,
            "selected_backends": tuple((entry.task_id, entry.backend) for entry in result.entries),
            "skipped_optional_tasks": result.skipped,
            "checks": checks,
            "assumptions": (
                "durations use pinned p95 profile estimates",
                "the model bound applies to selected deterministic p95 estimates, not physical runtime",
                "modeled run success multiplies profile probabilities and assumes independence",
                "the deterministic simulator does not predict correlated provider failures",
                "quality floors are profile assertions until runtime validators settle them",
            ),
            "failure_reason": result.failure_reason,
        }
        certificate = FeasibilityCertificate(
            **unsigned,
            certificate_digest=content_digest(unsigned),
        )
        return certificate, result
