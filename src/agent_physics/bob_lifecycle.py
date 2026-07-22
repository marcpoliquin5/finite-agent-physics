"""Durable IBM Bob-facing FINITE run lifecycle.

The service gives Bob one coherent preflight/run/status/explain/verify surface
instead of a collection of disconnected demonstrations.  Fixture and Granite
probe modes share the append-only run store.  A caller-supplied Bob session
reference is preserved as an explicitly unverified provenance assertion; only
screenshots, Bob logs, and the resulting commit can establish genuine Bob use.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    AdapterRequirements,
    BackendProfile,
    CancellationSemantics,
    CheckpointSemantics,
    RunEnvelope,
    TaskContract,
    UsageSemantics,
)
from .effects import SQLiteEffectBroker
from .executor import AsyncGraphExecutor
from .feasibility import FeasibilityAnalyzer
from .graph import ExecutionGraph
from .run_store import RunEvent, SQLiteRunStore, Usage
from .serialization import content_digest
from .stormshift_runtime import StormShiftRuntime
from .watsonx import WatsonxConfig, WatsonxGraniteAdapter
from .watsonx_worker import (
    WatsonxTaskSpec,
    WatsonxTaskWorker,
    validate_watsonx_task_output,
)


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TERMINAL_EVENTS = frozenset(
    {"run.completed", "run.awaiting_effects", "run.failed", "run.cancelled"}
)


class BobLifecycleError(RuntimeError):
    """Raised when a Bob lifecycle request is malformed or unverifiable."""


@dataclass(frozen=True, slots=True)
class BobRunSummary:
    schema_version: str
    run_id: str
    mode: str
    state: str
    event_count: int
    event_digest: str
    completed_task_ids: tuple[str, ...]
    actual_usage: Usage
    measurement_kind: str
    live_provider_calls: bool
    external_effects_possible: bool

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["actual_usage"] = asdict(self.actual_usage)
        return payload


def _validate_run_id(run_id: str) -> str:
    if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError(
            "run_id must be 1-128 characters using letters, digits, dot, colon, underscore, or dash"
        )
    return run_id


def _normalize_bob_reference(reference: str | None) -> str | None:
    """Validate caller provenance before any model, worker, or effect work starts."""

    if reference is None:
        return None
    if type(reference) is not str or not reference.strip() or len(reference) > 512:
        raise ValueError("bob_session_ref must contain 1-512 characters")
    return reference.strip()


def _nonnegative_int(value: object) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _usage_dict(usage: Usage) -> dict[str, int]:
    return {
        "tokens": usage.tokens,
        "cost_microusd": usage.cost_microusd,
        "context_bytes": usage.context_bytes,
    }


def _event_public_record(event: RunEvent, *, include_payload: bool) -> dict[str, object]:
    record: dict[str, object] = {
        "run_id": event.run_id,
        "sequence": event.sequence,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "task_id": event.task_id,
        "attempt": event.attempt,
        "occurred_at_ms": event.occurred_at_ms,
        "usage": {
            "estimated": _usage_dict(event.usage.estimated),
            "reserved": _usage_dict(event.usage.reserved),
            "actual": _usage_dict(event.usage.actual),
        },
    }
    if include_payload:
        record["payload"] = event.payload
    else:
        record["payload_digest"] = content_digest(event.payload)
    return record


class BobRunService:
    """Persistent lifecycle used by Bob MCP and the HTTP control plane."""

    def __init__(
        self,
        state_directory: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
        inference_factory: Callable[..., Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.state_directory = Path(state_directory)
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteRunStore(
            self.state_directory / "runs.sqlite3",
            clock_ms=clock_ms,
        )
        self.effect_broker = SQLiteEffectBroker(
            self.state_directory / "effects.sqlite3",
            broker_id="finite-bob-lifecycle",
            clock_ms=clock_ms,
        )
        self._environment = environment
        self._inference_factory = inference_factory

    @staticmethod
    def _granite_graph(config: WatsonxConfig, max_new_tokens: int) -> ExecutionGraph:
        profile = BackendProfile(
            name=config.model_id,
            provider="watsonx.ai",
            duration_ms_p50=2_000,
            duration_ms_p95=90_000,
            input_tokens=2_048,
            output_tokens=max_new_tokens,
            cost_microusd=25_000,
            context_bytes=16_384,
            quality=0.80,
            failure_probability=0.05,
        )
        return ExecutionGraph(
            tasks=(
                TaskContract(
                    task_id="granite_probe",
                    profiles=(profile,),
                    min_quality=0.75,
                    deadline_ms=110_000,
                    description="Bounded live Granite synthesis with a redacted receipt",
                    adapter_requirements=AdapterRequirements(
                        cancellation=CancellationSemantics.NONE,
                        checkpoint=CheckpointSemantics.RECEIPT,
                        streaming=False,
                        usage=UsageSemantics.PROVIDER_REPORTED,
                        effect_fencing=False,
                        max_hidden_retries=0,
                    ),
                ),
            )
        )

    @staticmethod
    def _granite_envelope(max_new_tokens: int) -> RunEnvelope:
        return RunEnvelope(
            deadline_ms=120_000,
            max_tokens=2_048 + max_new_tokens,
            max_cost_microusd=25_000,
            max_context_bytes=16_384,
            max_parallelism=1,
            min_modeled_success_probability=0.90,
            provider_limits=(("watsonx.ai", 1),),
        )

    def granite_preflight(self, *, max_new_tokens: int = 256) -> dict[str, object]:
        """Preflight the exact live-probe contract without making a provider call."""

        if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= 1_024:
            raise ValueError("max_new_tokens must be an integer from 1 through 1024")
        config = WatsonxConfig.from_environment(self._environment)
        graph = self._granite_graph(config, max_new_tokens)
        envelope = self._granite_envelope(max_new_tokens)
        certificate, _ = FeasibilityAnalyzer().analyze(graph, envelope)
        payload = certificate.as_dict()
        payload.update(
            {
                "schema_version": "finite-bob-granite-preflight/v1",
                "measurement_kind": "declared-live-profile-preflight",
                "live_provider_calls": False,
                "model_id": config.model_id,
                "configuration": config.public_dict(),
            }
        )
        return payload

    async def run_fixture(
        self,
        *,
        run_id: str,
        bob_session_ref: str | None = None,
    ) -> dict[str, object]:
        """Run or resume the deterministic StormShift fixture lifecycle."""

        run_id = _validate_run_id(run_id)
        bob_session_ref = _normalize_bob_reference(bob_session_ref)
        runtime = StormShiftRuntime(self.store, self.effect_broker)
        result = await runtime.execute(run_id=run_id)
        self._record_caller_provenance(run_id, bob_session_ref)
        summary = self.summary(run_id, mode="fixture")
        payload = summary.as_dict()
        payload.update(
            {
                "response_plan_digest": content_digest(result.response_plan),
                "validation_report_digest": result.validation.report_digest,
                "validation_digest_verified": result.validation.verify_digest(),
                "effect_intent_id": result.effect_intent.intent_id,
                "effect_state": result.effect_intent.state.value,
                "bob_session_ref_status": (
                    "caller-supplied-unverified" if bob_session_ref else "not-supplied"
                ),
            }
        )
        return payload

    async def run_granite_probe(
        self,
        *,
        run_id: str,
        instruction: str,
        max_new_tokens: int = 256,
        bob_session_ref: str | None = None,
    ) -> dict[str, object]:
        """Run or resume one admitted Granite task and persist its redacted receipt."""

        run_id = _validate_run_id(run_id)
        bob_session_ref = _normalize_bob_reference(bob_session_ref)
        if type(instruction) is not str or not instruction.strip():
            raise ValueError("instruction is required")
        if len(instruction) > 4_000:
            raise ValueError("instruction cannot exceed 4000 characters")
        if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= 1_024:
            raise ValueError("max_new_tokens must be an integer from 1 through 1024")
        config = WatsonxConfig.from_environment(self._environment)
        adapter = WatsonxGraniteAdapter(config, self._inference_factory)
        graph = self._granite_graph(config, max_new_tokens)
        envelope = self._granite_envelope(max_new_tokens)
        worker = WatsonxTaskWorker(
            adapter,
            WatsonxTaskSpec(
                task_id="granite_probe",
                instruction=instruction,
                max_new_tokens=max_new_tokens,
                guardrails=True,
            ),
        )
        executor = AsyncGraphExecutor(
            self.store,
            workers={"granite_probe": worker},
            output_validator=validate_watsonx_task_output,
            validator_revision="watsonx-task-output/v1",
        )
        result = await executor.execute(graph, envelope, run_id=run_id)
        self._record_caller_provenance(run_id, bob_session_ref)
        output = result.outputs["granite_probe"]
        if type(output) is not dict:
            raise BobLifecycleError("durable Granite output is malformed")
        summary = self.summary(run_id, mode="granite-probe")
        payload = summary.as_dict()
        payload.update(
            {
                "receipt": output,
                "configuration": config.public_dict(),
                "resumed_task_ids": result.resumed_task_ids,
                "bob_session_ref_status": (
                    "caller-supplied-unverified" if bob_session_ref else "not-supplied"
                ),
            }
        )
        return payload

    def _record_caller_provenance(self, run_id: str, reference: str | None) -> None:
        normalized = _normalize_bob_reference(reference)
        if normalized is None:
            return
        reference_digest = content_digest(normalized)
        self.store.append_event(
            run_id=run_id,
            event_id=f"{run_id}:bob-caller-assertion:{reference_digest[:16]}",
            event_type="provenance.bob_caller_assertion",
            payload={
                "provenance_kind": "caller-supplied-unverified",
                "reference": normalized,
                "reference_digest": reference_digest,
                "warning": "This record alone does not prove a genuine IBM Bob session.",
            },
        )

    def summary(self, run_id: str, *, mode: str = "unknown") -> BobRunSummary:
        run_id = _validate_run_id(run_id)
        events = self.store.events(run_id)
        if not events:
            raise BobLifecycleError("run has no durable events")
        if mode == "unknown":
            mode = (
                "granite-probe"
                if any(event.task_id == "granite_probe" for event in events)
                else "fixture"
            )
        terminals = [event for event in events if event.event_type in _TERMINAL_EVENTS]
        state = terminals[-1].event_type.removeprefix("run.") if terminals else "running"
        actual = Usage()
        for event in events:
            if event.event_type in {
                "task.attempt_failed",
                "task.attempt_succeeded",
                "task.attempt_cancelled",
            }:
                actual = actual + event.usage.actual
        records = tuple(_event_public_record(event, include_payload=False) for event in events)
        measurement_kind = "deterministic-fixture"
        live_provider_calls = False
        if mode == "granite-probe":
            completed = self.store.completed_tasks(run_id).get("granite_probe")
            output = completed.output if completed is not None else None
            if type(output) is dict and type(output.get("measurement_kind")) is str:
                measurement_kind = output["measurement_kind"]
                live_provider_calls = measurement_kind == "live-watsonx"
            else:
                measurement_kind = "granite-probe-without-verified-receipt"
        return BobRunSummary(
            schema_version="finite-bob-run-summary/v1",
            run_id=run_id,
            mode=mode,
            state=state,
            event_count=len(events),
            event_digest=content_digest(records),
            completed_task_ids=tuple(sorted(self.store.completed_tasks(run_id))),
            actual_usage=actual,
            measurement_kind=measurement_kind,
            live_provider_calls=live_provider_calls,
            external_effects_possible=False,
        )

    def explain(
        self,
        run_id: str,
        *,
        include_payloads: bool = False,
    ) -> dict[str, object]:
        """Return recorded public events and numeric facts, never hidden reasoning."""

        run_id = _validate_run_id(run_id)
        events = self.store.events(run_id)
        summary = self.summary(run_id)
        return {
            "schema_version": "finite-bob-run-explanation/v1",
            "run_id": run_id,
            "reasoning_access": False,
            "derivation_scope": "recorded-public-events-and-numeric-usage-only",
            "event_digest": summary.event_digest,
            "event_type_counts": dict(sorted(Counter(e.event_type for e in events).items())),
            "events": [
                _event_public_record(event, include_payload=include_payloads) for event in events
            ],
        }

    def verify(self, run_id: str) -> dict[str, object]:
        """Verify the control ledger without claiming semantic or provider attestation."""

        run_id = _validate_run_id(run_id)
        run = self.store.get_run(run_id)
        events = self.store.events(run_id)
        checks: list[dict[str, object]] = []

        def check(name: str, passed: bool, evidence: object) -> None:
            checks.append({"name": name, "passed": passed, "evidence": evidence})

        sequences = tuple(event.sequence for event in events)
        check("contiguous-sequences", sequences == tuple(range(1, len(events) + 1)), sequences)
        event_ids = tuple(event.event_id for event in events)
        check("unique-event-ids", len(event_ids) == len(set(event_ids)), len(event_ids))
        check("run-identity", all(event.run_id == run_id for event in events), run_id)
        started = [event for event in events if event.event_type == "run.started"]
        check("single-run-start", len(started) == 1, len(started))
        manifest_bound = bool(started) and (
            started[0].payload.get("manifest_digest") == run.manifest_digest
            and started[0].payload.get("manifest_revision") == run.manifest_revision
        )
        check("manifest-binding", manifest_bound, run.manifest_digest)
        terminals = [event for event in events if event.event_type in _TERMINAL_EVENTS]
        check("single-terminal-state", len(terminals) == 1, tuple(e.event_type for e in terminals))

        actual = Usage()
        reservations_valid = True
        for event in events:
            if event.event_type in {
                "task.attempt_failed",
                "task.attempt_succeeded",
                "task.attempt_cancelled",
            }:
                actual = actual + event.usage.actual
                reservations_valid = reservations_valid and (
                    event.usage.actual.tokens <= event.usage.reserved.tokens
                    and event.usage.actual.cost_microusd <= event.usage.reserved.cost_microusd
                    and event.usage.actual.context_bytes <= event.usage.reserved.context_bytes
                )
        check("per-attempt-reservations", reservations_valid, _usage_dict(actual))
        envelope = run.envelope
        max_tokens = _nonnegative_int(envelope.get("max_tokens"))
        max_cost = _nonnegative_int(envelope.get("max_cost_microusd"))
        max_context = _nonnegative_int(envelope.get("max_context_bytes"))
        envelope_valid = (
            max_tokens is not None
            and max_cost is not None
            and max_context is not None
            and actual.tokens <= max_tokens
            and actual.cost_microusd <= max_cost
            and actual.context_bytes <= max_context
        )
        check("run-envelope", envelope_valid, _usage_dict(actual))
        records = tuple(_event_public_record(event, include_payload=True) for event in events)
        return {
            "schema_version": "finite-bob-run-verification/v1",
            "run_id": run_id,
            "passed": all(bool(item["passed"]) for item in checks),
            "measurement_scope": "control-ledger-only-not-semantic-or-provider-attestation",
            "event_digest": content_digest(records),
            "checks": checks,
        }


def default_state_directory(environment: Mapping[str, str] | None = None) -> Path:
    values = environment if environment is not None else os.environ
    configured = values.get("FINITE_STATE_DIR", "").strip()
    return Path(configured) if configured else Path.cwd() / ".finite"


def default_bob_run_service() -> BobRunService:
    return BobRunService(default_state_directory())


__all__ = [
    "BobLifecycleError",
    "BobRunService",
    "BobRunSummary",
    "default_bob_run_service",
    "default_state_directory",
]
