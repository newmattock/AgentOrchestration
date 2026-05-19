"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
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


@dataclass
class ScheduledTask:
    task: Dict
    due_at: float
    queue: str
    priority: int
    generation: int
    lifecycle: str = "scheduled"


class TaskScheduler:
    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, ScheduledTask] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._audit_records: List[Dict] = []
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["retries"] = 0

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["retries"] = task.get("retries", 0)
        self._scheduled[task_id] = ScheduledTask(
            task=task,
            due_at=time.time() + delay,
            queue=queue,
            priority=priority,
            generation=0,
        )
        return task_id

    def reschedule(
        self,
        task_id: str,
        delay: float,
        queue: Optional[str] = None,
        priority: Optional[int] = None,
        expected_generation: Optional[int] = None,
        expected_revision: Optional[int] = None,
    ) -> bool:
        scheduled = self._scheduled.get(task_id)
        if scheduled is None:
            lifecycle = (
                "in_flight" if task_id in self._in_flight else "missing"
            )
            self._audit(
                "reschedule_rejected",
                task_id,
                "invalid_lifecycle",
                lifecycle=lifecycle,
            )
            return False
        expected = (
            expected_revision
            if expected_revision is not None
            else expected_generation
        )
        if expected is not None and scheduled.generation != expected:
            self._audit(
                "reschedule_rejected",
                task_id,
                "stale_generation",
                generation=scheduled.generation,
                lifecycle=scheduled.lifecycle,
            )
            return False

        self._scheduled[task_id] = ScheduledTask(
            task=scheduled.task,
            due_at=time.time() + delay,
            queue=queue if queue is not None else scheduled.queue,
            priority=priority if priority is not None else scheduled.priority,
            generation=scheduled.generation + 1,
            lifecycle="scheduled",
        )
        self._audit(
            "reschedule_accepted",
            task_id,
            "scheduled",
            generation=scheduled.generation + 1,
            lifecycle="scheduled",
        )
        return True

    @property
    def audit_records(self) -> List[Dict]:
        return list(self._audit_records)

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = time.time()
        expired = [
            (tid, scheduled.generation)
            for tid, scheduled in self._scheduled.items()
            if scheduled.queue == queue and scheduled.due_at <= now
        ]
        for tid, generation in expired:
            scheduled = self._scheduled.get(tid)
            if scheduled is None or scheduled.generation != generation:
                continue
            self._scheduled.pop(tid)
            self._enqueue_existing(
                scheduled.task,
                scheduled.queue,
                scheduled.priority,
            )
            self._audit(
                "scheduled_task_ready",
                tid,
                "delay_elapsed",
                generation=generation,
                lifecycle="ready",
            )

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                task["lifecycle"] = "in_flight"
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False

    def _enqueue_existing(
        self,
        task: Dict,
        queue: str,
        priority: int = 0,
    ) -> None:
        task["enqueued_at"] = time.time()
        task["priority"] = priority
        task["lifecycle"] = "ready"
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

    def _audit(
        self,
        event: str,
        task_id: str,
        reason: str,
        **details: Any,
    ) -> None:
        record = {
            "event": event,
            "task_id": task_id,
            "reason": reason,
        }
        record.update(details)
        self._audit_records.append(record)

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
