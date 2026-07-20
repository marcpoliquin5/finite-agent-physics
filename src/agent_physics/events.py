"""Deterministic events emitted by the scheduler and simulator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EventType(str, Enum):
    RUN_STARTED = "run.started"
    PROFILE_SELECTED = "task.profile_selected"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    TASK_SKIPPED = "task.skipped"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    time_ms: int
    event_type: EventType
    task_id: str | None = None
    details: tuple[tuple[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "time_ms": self.time_ms,
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "details": dict(self.details),
        }
