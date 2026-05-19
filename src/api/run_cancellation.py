"""Workspace-scoped run cancellation records."""

from dataclasses import dataclass
from typing import Dict, Optional

from .auth import OperatorPrincipal


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    workspace_id: str
    status: str = "running"


@dataclass(frozen=True)
class RunCancellation:
    run_id: str
    workspace_id: str
    cancelled_by: str
    status: str = "cancelled"


class RunCancellationStore:
    def __init__(self):
        self._runs: Dict[str, RunRecord] = {}
        self._cancellations: Dict[str, RunCancellation] = {}

    def clear(self) -> None:
        self._runs.clear()
        self._cancellations.clear()

    def add_run(self, run_id: str, workspace_id: str) -> None:
        self._runs[run_id] = RunRecord(
            run_id=run_id,
            workspace_id=workspace_id,
        )

    def cancel(
        self,
        run_id: str,
        workspace_id: str,
        principal: OperatorPrincipal,
    ) -> Optional[RunCancellation]:
        record = self._runs.get(run_id)
        if not record or record.workspace_id != workspace_id:
            return None

        cancellation = RunCancellation(
            run_id=run_id,
            workspace_id=workspace_id,
            cancelled_by=principal.subject,
        )
        self._cancellations[run_id] = cancellation
        self._runs[run_id] = RunRecord(
            run_id=run_id,
            workspace_id=workspace_id,
            status="cancelled",
        )
        return cancellation

    def get_cancellation(self, run_id: str) -> Optional[RunCancellation]:
        return self._cancellations.get(run_id)


run_cancellations = RunCancellationStore()
