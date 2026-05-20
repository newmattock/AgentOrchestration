"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4


class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    def __init__(self, time_fn: Optional[Callable[[], float]] = None):
        self._time_fn = time_fn or time.time
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._audit_records: List[Dict[str, Any]] = []
        self._dispatch_metrics: Dict[str, int] = {
            "blackout_deferrals": 0,
        }
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = self._time_fn()
        task["retries"] = 0
        task["priority"] = priority

        self._queue_task(task, queue, priority)
        return task_id

    def _queue_task(self, task: Dict, queue: str, priority: int) -> None:
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = self._time_fn()
        task["retries"] = 0
        task["priority"] = priority
        self._scheduled[task_id] = {
            "task": task,
            "ready_at": self._time_fn() + delay,
            "queue": queue,
            "priority": priority,
        }
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = self._time_fn()
        self._promote_ready_scheduled(queue, now)

        if queue in self._queues and len(self._queues[queue]) > 0:
            while len(self._queues[queue]) > 0:
                task = self._queues[queue].pop()
                if not task:
                    continue
                blackout = self._active_blackout(task, now)
                if blackout:
                    self._defer_blackout_task(
                        task,
                        queue,
                        task.get("priority", 0),
                        blackout,
                    )
                    continue

                self._in_flight[task["id"]] = task
                return task
        return None

    def _promote_ready_scheduled(self, queue: str, now: float) -> None:
        ready_ids = [
            task_id
            for task_id, entry in self._scheduled.items()
            if entry["queue"] == queue and entry["ready_at"] <= now
        ]
        for task_id in ready_ids:
            entry = self._scheduled[task_id]
            task = entry["task"]
            blackout = self._active_blackout(task, now)
            if blackout:
                self._record_blackout(task, queue, blackout)
                entry["ready_at"] = blackout[1]
                continue
            self._scheduled.pop(task_id, None)
            self._queue_task(task, queue, entry["priority"])

    def _active_blackout(
        self,
        task: Dict,
        now: float,
    ) -> Optional[Tuple[float, float]]:
        for start, end in self._blackout_windows(task):
            if start <= now < end:
                return start, end
        return None

    def _blackout_windows(self, task: Dict) -> List[Tuple[float, float]]:
        policy = task.get("dispatch_policy") or {}
        workflow = task.get("workflow") or {}
        workflow_policy = workflow.get("dispatch_policy") or {}
        candidates = (
            workflow_policy.get("blackout_windows")
            or workflow.get("blackout_windows")
            or policy.get("blackout_windows")
            or task.get("blackout_windows")
            or []
        )
        windows: List[Tuple[float, float]] = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                start = self._float_or_none(candidate.get("start"))
                end = self._float_or_none(candidate.get("end"))
            elif isinstance(candidate, (list, tuple)) and len(candidate) == 2:
                start = self._float_or_none(candidate[0])
                end = self._float_or_none(candidate[1])
            else:
                continue
            if start is not None and end is not None and start < end:
                windows.append((start, end))
        return windows

    def _float_or_none(self, value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _record_blackout(
        self,
        task: Dict,
        queue: str,
        blackout: Tuple[float, float],
    ) -> None:
        self._dispatch_metrics["blackout_deferrals"] += 1
        self._audit_records.append(
            {
                "event": "dispatch_deferred_blackout",
                "task_id": task.get("id"),
                "queue": queue,
                "window_start": blackout[0],
                "window_end": blackout[1],
                "workflow_id": self._workflow_id(task),
                "deferred_until": blackout[1],
                "reason": "workflow_blackout_window",
            }
        )

    def _defer_blackout_task(
        self,
        task: Dict,
        queue: str,
        priority: int,
        blackout: Tuple[float, float],
    ) -> None:
        self._record_blackout(task, queue, blackout)
        self._scheduled[task["id"]] = {
            "task": task,
            "ready_at": blackout[1],
            "queue": queue,
            "priority": priority,
        }

    def _workflow_id(self, task: Dict) -> Optional[str]:
        workflow = task.get("workflow") or {}
        if isinstance(workflow, dict):
            return task.get("workflow_id") or workflow.get("id")
        return task.get("workflow_id")

    @property
    def audit_records(self) -> List[Dict[str, Any]]:
        return list(self._audit_records)

    @property
    def dispatch_metrics(self) -> Dict[str, int]:
        return dict(self._dispatch_metrics)

    def complete(self, task_id: str) -> bool:
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self._queue_task(task, queue, priority=task.get("priority", 0))
                return True
        return False

# 2019-04-25T08:37:12 update

# 2019-06-04T16:40:00 update

# 2019-07-11T12:01:28 update

# 2019-08-02T12:20:21 update

# 2019-08-23T10:38:50 update

# 2019-10-31T13:55:52 update

# 2019-11-04T20:12:32 update

# 2019-12-13T12:22:36 update

# 2020-02-01T10:32:37 update

# 2020-02-26T09:44:38 update

# 2020-03-09T19:00:55 update

# 2020-05-01T18:40:34 update

# 2020-05-12T15:10:31 update

# 2020-06-30T13:24:19 update

# 2020-09-22T16:00:45 update

# 2020-10-20T10:52:48 update

# 2020-10-21T12:18:08 update

# 2020-11-06T12:35:01 update

# 2020-12-09T08:09:33 update

# 2021-01-07T08:20:36 update

# 2021-10-02T15:23:16 update

# 2021-10-06T16:14:57 update

# 2021-10-06T09:27:41 update

# 2021-11-19T08:37:40 update

# 2022-03-01T16:39:54 update

# 2022-05-26T13:43:07 update

# 2022-06-02T10:50:58 update

# 2022-06-14T10:46:48 update

# 2022-07-31T16:44:34 update

# 2022-08-30T18:20:12 update

# 2022-11-04T14:47:03 update

# 2022-12-06T10:36:49 update

# 2022-12-22T13:21:12 update

# 2022-12-26T12:24:50 update

# 2023-03-09T08:09:55 update

# 2023-05-01T10:07:37 update

# 2023-06-08T14:32:15 update

# 2023-07-14T17:24:18 update

# 2023-12-14T08:38:31 update

# 2024-02-20T13:43:58 update

# 2024-03-24T08:52:42 update

# 2024-03-28T15:27:17 update

# 2024-03-29T18:10:33 update

# 2024-04-15T20:18:31 update

# 2024-05-27T13:11:52 update

# 2024-05-27T16:42:56 update

# 2024-06-20T13:03:45 update

# 2024-06-28T12:32:58 update

# 2024-07-10T14:10:16 update

# 2024-07-26T14:18:59 update

# 2024-08-12T08:21:05 update

# 2024-08-21T16:58:40 update

# 2024-09-27T19:54:30 update

# 2024-10-21T13:47:42 update

# 2024-11-11T09:19:27 update

# 2024-12-24T08:23:41 update

# 2025-02-14T10:35:15 update

# 2025-03-31T18:09:40 update

# 2025-06-21T17:32:49 update

# 2025-07-21T16:52:28 update

# 2025-08-20T19:45:16 update

# 2025-11-04T18:54:24 update

# 2025-12-09T20:17:36 update

# 2026-01-12T15:42:32 update

# 2026-01-23T14:41:20 update

# 2026-03-18T14:43:07 update

# 2026-04-13T11:43:19 update
