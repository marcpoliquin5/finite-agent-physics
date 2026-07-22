"""Execution graph validation and critical-path utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .contracts import BackendProfile, TaskContract


class GraphValidationError(ValueError):
    """Raised when a task graph is unsafe or structurally invalid."""


@dataclass(frozen=True, slots=True)
class ExecutionGraph:
    tasks: tuple[TaskContract, ...]

    @classmethod
    def from_tasks(cls, tasks: Iterable[TaskContract]) -> "ExecutionGraph":
        graph = cls(tuple(tasks))
        graph.validate()
        return graph

    @property
    def by_id(self) -> dict[str, TaskContract]:
        return {task.task_id: task for task in self.tasks}

    @property
    def successors(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, list[str]] = {task.task_id: [] for task in self.tasks}
        for task in self.tasks:
            for dependency in task.dependencies:
                result[dependency].append(task.task_id)
        return {key: tuple(sorted(value)) for key, value in result.items()}

    def validate(self) -> None:
        errors: list[str] = []
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            errors.append("task IDs must be unique")
        known = set(ids)
        for task in self.tasks:
            errors.extend(task.validate())
            for dependency in task.dependencies:
                if dependency not in known:
                    errors.append(f"task {task.task_id!r}: unknown dependency {dependency!r}")
            for input_port in task.input_ports:
                if input_port.source_task_id not in known:
                    errors.append(
                        f"task {task.task_id!r}: input port {input_port.name!r} has missing "
                        f"producer {input_port.source_task_id!r}"
                    )
                    continue
                if input_port.source_task_id not in task.dependencies:
                    errors.append(
                        f"task {task.task_id!r}: input port {input_port.name!r} producer "
                        f"{input_port.source_task_id!r} must be a direct dependency"
                    )
                    continue
                producer = self.by_id[input_port.source_task_id]
                candidates = {output.name: output for output in producer.output_ports}
                output = candidates.get(input_port.source_port)
                if output is None:
                    errors.append(
                        f"task {task.task_id!r}: input port {input_port.name!r} references "
                        f"missing producer port {input_port.source_task_id!r}."
                        f"{input_port.source_port!r}"
                    )
                    continue
                expected = (input_port.schema, input_port.schema_version, input_port.media_type)
                produced = (output.schema, output.schema_version, output.media_type)
                if expected != produced:
                    errors.append(
                        f"task {task.task_id!r}: input port {input_port.name!r} is incompatible "
                        f"with {input_port.source_task_id!r}.{input_port.source_port!r}; "
                        f"expected {expected!r}, produced {produced!r}"
                    )
        if not errors:
            self._validate_acyclic()
        if errors:
            raise GraphValidationError("; ".join(errors))

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = self.by_id

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise GraphValidationError(f"cycle detected at task {task_id!r}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in by_id[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in sorted(by_id):
            visit(task_id)

    def topological_order(self) -> tuple[str, ...]:
        by_id = self.by_id
        indegree = {task_id: len(task.dependencies) for task_id, task in by_id.items()}
        ready = sorted(task_id for task_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        successors = self.successors
        while ready:
            task_id = ready.pop(0)
            order.append(task_id)
            for child in successors[task_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(order) != len(by_id):
            raise GraphValidationError("graph contains a cycle")
        return tuple(order)

    def upward_ranks(self, profiles: Mapping[str, BackendProfile]) -> dict[str, int]:
        """Return HEFT-style p95 rank: own work plus longest downstream path."""

        successors = self.successors
        ranks: dict[str, int] = {}
        for task_id in reversed(self.topological_order()):
            downstream = max((ranks[child] for child in successors[task_id]), default=0)
            ranks[task_id] = profiles[task_id].duration_ms_p95 + downstream
        return ranks
