"""Workflow Manager — Defines and executes multi-step agent workflows."""

from collections.abc import Mapping, Sequence
from enum import Enum
from itertools import product
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4


DEFAULT_MAX_MATRIX_EXPANSION = 256


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowLifecycleError(ValueError):
    """Raised when a workflow graph changes after execution has started."""


class WorkflowMatrixError(ValueError):
    """Raised when a workflow step matrix violates fan-out policy."""


class WorkflowStep:
    def __init__(
        self,
        name: str,
        handler: Callable,
        retries: int = 0,
        timeout: int = 300,
        matrix: Optional[Mapping[str, Sequence[Any]]] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.retries = retries
        self.timeout = timeout
        self.matrix = matrix or {}
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None


class Workflow:
    def __init__(
        self,
        name: str,
        description: str = "",
        max_matrix_expansion: int = DEFAULT_MAX_MATRIX_EXPANSION,
    ):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self.max_matrix_expansion = max_matrix_expansion
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self.audit_log: List[Dict[str, Any]] = []
        self.status = StepStatus.PENDING

    def add_step(self, step: WorkflowStep) -> "Workflow":
        if self.status is not StepStatus.PENDING:
            self._record_rejection(
                step,
                "workflow_not_pending",
                {"status": self.status.value},
            )
            raise WorkflowLifecycleError(
                "workflow steps cannot be added after execution starts"
            )

        step.matrix = self._validate_matrix(step)
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)

    def expand_matrix(self, step: WorkflowStep) -> List[Dict[str, Any]]:
        matrix = self._validate_matrix(step)
        if not matrix:
            return [{}]

        keys = list(matrix)
        return [
            dict(zip(keys, values))
            for values in product(*(matrix[key] for key in keys))
        ]

    def _validate_matrix(
        self,
        step: WorkflowStep,
    ) -> Mapping[str, tuple[Any, ...]]:
        matrix = step.matrix or {}
        if not isinstance(matrix, Mapping):
            self._record_rejection(
                step,
                "invalid_matrix_definition",
                {"matrix_type": type(matrix).__name__},
            )
            raise WorkflowMatrixError("step matrix must be a mapping")

        snapshot: Dict[str, tuple[Any, ...]] = {}
        expansion_count = 1
        for dimension, raw_values in matrix.items():
            values = self._matrix_values(step, dimension, raw_values)
            snapshot[str(dimension)] = values
            expansion_count *= len(values)
            if expansion_count > self.max_matrix_expansion:
                self._record_rejection(
                    step,
                    "matrix_expansion_limit_exceeded",
                    {
                        "dimension_count": len(snapshot),
                        "expansion_count": expansion_count,
                        "limit": self.max_matrix_expansion,
                    },
                )
                raise WorkflowMatrixError(
                    "matrix expansion exceeds workflow fan-out limit"
                )

        return MappingProxyType(snapshot)

    def _matrix_values(
        self,
        step: WorkflowStep,
        dimension: Any,
        raw_values: Any,
    ) -> tuple[Any, ...]:
        if (
            isinstance(raw_values, (str, bytes))
            or not isinstance(raw_values, Sequence)
        ):
            self._record_rejection(
                step,
                "invalid_matrix_dimension",
                {"dimension": str(dimension)},
            )
            raise WorkflowMatrixError(
                f"matrix dimension {dimension!r} must be a non-empty sequence"
            )

        values = tuple(raw_values)
        if not values:
            self._record_rejection(
                step,
                "empty_matrix_dimension",
                {"dimension": str(dimension)},
            )
            raise WorkflowMatrixError(
                f"matrix dimension {dimension!r} must not be empty"
            )
        if len({repr(value) for value in values}) != len(values):
            self._record_rejection(
                step,
                "duplicate_matrix_dimension_values",
                {"dimension": str(dimension)},
            )
            raise WorkflowMatrixError(
                f"matrix dimension {dimension!r} must not contain duplicates"
            )
        return values

    def _record_rejection(
        self,
        step: WorkflowStep,
        reason: str,
        details: Dict[str, Any],
    ) -> None:
        self.audit_log.append(
            {
                "event": "workflow_step_rejected",
                "workflow_id": self.id,
                "step_id": step.id,
                "step_name": step.name,
                "reason": reason,
                "details": details,
            }
        )


class WorkflowManager:
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}

    def create_workflow(
        self,
        name: str,
        description: str = "",
        max_matrix_expansion: int = DEFAULT_MAX_MATRIX_EXPANSION,
    ) -> Workflow:
        workflow = Workflow(name, description, max_matrix_expansion)
        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def execute_workflow(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        try:
            for step in workflow.steps:
                step.matrix = workflow._validate_matrix(step)
        except WorkflowMatrixError:
            return False

        workflow.status = StepStatus.RUNNING
        for step in workflow.steps:
            step.status = StepStatus.RUNNING
            try:
                result = step.handler()
                step.result = result
                step.status = StepStatus.COMPLETED
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                workflow.status = StepStatus.FAILED
                return False

        workflow.status = StepStatus.COMPLETED
        return True

# 2019-03-27T19:58:07 update

# 2019-05-09T09:42:56 update

# 2019-12-03T10:07:42 update

# 2020-01-16T18:43:28 update

# 2020-03-20T10:40:15 update

# 2020-04-17T15:36:50 update

# 2020-05-04T14:44:01 update

# 2020-06-16T13:17:31 update

# 2020-08-05T17:00:24 update

# 2020-09-04T08:29:23 update

# 2020-09-09T17:52:02 update

# 2020-10-23T10:57:44 update

# 2020-12-05T20:55:47 update

# 2021-01-15T19:23:40 update

# 2021-02-03T20:43:12 update

# 2021-03-16T12:26:47 update

# 2021-04-20T14:33:28 update

# 2021-10-14T15:03:32 update

# 2021-10-21T17:24:55 update

# 2021-11-16T17:01:08 update

# 2021-11-22T09:51:21 update

# 2021-12-21T16:15:47 update

# 2022-03-23T16:52:27 update

# 2022-12-21T09:25:50 update

# 2023-01-09T09:55:25 update

# 2023-01-13T11:06:15 update

# 2023-01-26T11:00:59 update

# 2023-02-23T08:56:54 update

# 2023-05-17T08:07:16 update

# 2023-06-06T17:09:34 update

# 2023-06-13T10:35:28 update

# 2023-08-24T20:36:06 update

# 2023-10-30T19:10:13 update

# 2024-01-02T08:27:25 update

# 2024-01-24T12:13:15 update

# 2024-02-08T13:35:49 update

# 2024-05-07T16:09:24 update

# 2024-05-11T09:48:46 update

# 2024-05-21T19:25:41 update

# 2024-06-05T12:00:30 update

# 2024-06-25T09:40:26 update

# 2024-09-17T13:49:39 update

# 2024-10-14T17:39:35 update

# 2024-11-27T20:14:35 update

# 2024-12-25T19:31:41 update

# 2025-01-16T13:15:09 update

# 2025-02-05T14:06:59 update

# 2025-02-17T20:55:11 update

# 2025-04-30T19:36:53 update

# 2025-07-17T10:14:40 update

# 2025-08-29T12:13:15 update

# 2025-09-03T13:51:11 update

# 2025-09-19T16:08:24 update

# 2025-11-27T08:38:12 update

# 2026-01-27T13:23:38 update

# 2026-01-28T11:22:50 update
