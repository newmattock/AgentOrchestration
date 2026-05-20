import asyncio

import pytest
from src.orchestrator.scheduler import (
    PriorityQueue,
    QueueCapacityExceeded,
    TaskScheduler,
)


class TestTaskScheduler:
    def setup_method(self):
        self.scheduler = TaskScheduler()

    def test_enqueue_task(self):
        task_id = self.scheduler.enqueue({"type": "test", "payload": {}})
        assert task_id is not None

    def test_dequeue_task(self):
        self.scheduler.enqueue({"type": "test", "payload": {"data": 1}})
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "test"

    def test_enqueue_multiple_priorities(self):
        self.scheduler.enqueue({"type": "low"}, priority=1)
        self.scheduler.enqueue({"type": "high"}, priority=10)
        task = asyncio.run(self.scheduler.dequeue())
        assert task["type"] == "high"

    def test_complete_task(self):
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.complete(task["id"])

    def test_fail_task_with_retry(self):
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.fail(task["id"])

    def test_enqueue_rollback_releases_capacity(self, monkeypatch):
        scheduler = TaskScheduler(queue_capacities={"default": 1})
        queue = PriorityQueue()

        def fail_push(_task, priority=0):
            raise RuntimeError("transaction failed")

        monkeypatch.setattr(queue, "push", fail_push)
        scheduler._queues["default"] = queue
        original_task = {"type": "rollback", "payload": {"private": "secret"}}

        with pytest.raises(RuntimeError):
            scheduler.enqueue(original_task)

        assert original_task == {
            "type": "rollback",
            "payload": {"private": "secret"},
        }
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 0,
            "limit": 1,
        }
        audit = scheduler.audit_log()
        assert audit[-1]["decision"] == "enqueue_rolled_back"
        assert audit[-1]["reason"] == "RuntimeError"
        assert "payload" not in audit[-1]
        assert "secret" not in str(audit[-1])

        scheduler._queues["default"] = PriorityQueue()
        task_id = scheduler.enqueue({"type": "accepted"})
        assert task_id is not None
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 1,
            "limit": 1,
        }

    def test_capacity_rejects_until_complete_releases_claim(self):
        scheduler = TaskScheduler(queue_capacities={"default": 1})
        first_id = scheduler.enqueue({"type": "first"})

        with pytest.raises(QueueCapacityExceeded):
            scheduler.enqueue({"type": "second"})

        first_task = asyncio.run(scheduler.dequeue())
        assert first_task["id"] == first_id
        assert scheduler.complete(first_id)
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 0,
            "limit": 1,
        }

        second_id = scheduler.enqueue({"type": "second"})
        assert second_id is not None

    def test_retry_preserves_capacity_and_task_id(self):
        scheduler = TaskScheduler(queue_capacities={"default": 1})
        task_id = scheduler.enqueue({"type": "retryable"}, priority=4)

        task = asyncio.run(scheduler.dequeue())
        assert scheduler.fail(task["id"])
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 1,
            "limit": 1,
        }

        retried = asyncio.run(scheduler.dequeue())
        assert retried["id"] == task_id
        assert retried["retries"] == 1

    def test_duplicate_retry_ack_does_not_double_book_capacity(self):
        scheduler = TaskScheduler(queue_capacities={"default": 1})
        task_id = scheduler.enqueue({"type": "retryable"}, priority=4)
        task = asyncio.run(scheduler.dequeue())

        assert scheduler.fail(task["id"])
        assert not scheduler.fail(task["id"])
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 1,
            "limit": 1,
        }

        retried = asyncio.run(scheduler.dequeue())
        assert retried["id"] == task_id
        assert scheduler.complete(task_id)
        assert scheduler.capacity_snapshot()["default"]["used"] == 0

    def test_retry_rollback_keeps_in_flight_state(self, monkeypatch):
        scheduler = TaskScheduler(queue_capacities={"default": 1, "dead": 1})
        task_id = scheduler.enqueue({"type": "retryable"}, priority=4)
        task = asyncio.run(scheduler.dequeue())
        dead_queue = PriorityQueue()

        def fail_push(_task, priority=0):
            raise RuntimeError("retry transaction failed")

        monkeypatch.setattr(dead_queue, "push", fail_push)
        scheduler._queues["dead"] = dead_queue

        with pytest.raises(RuntimeError):
            scheduler.fail(task["id"], queue="dead")

        assert task["retries"] == 0
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 1,
            "limit": 1,
        }
        assert scheduler.capacity_snapshot()["dead"] == {
            "used": 0,
            "limit": 1,
        }
        assert scheduler.complete(task_id)
        assert scheduler.capacity_snapshot()["default"]["used"] == 0
        audit = scheduler.audit_log()
        assert audit[-1]["decision"] == "task_completed"
        assert any(
            entry["decision"] == "retry_enqueue_rolled_back"
            and entry["reason"] == "RuntimeError"
            for entry in audit
        )

    def test_retry_to_new_queue_transfers_capacity_claim(self):
        scheduler = TaskScheduler(queue_capacities={"default": 1, "dead": 1})
        task_id = scheduler.enqueue({"type": "retryable"}, priority=4)
        task = asyncio.run(scheduler.dequeue())

        assert scheduler.fail(task["id"], queue="dead")
        assert scheduler.capacity_snapshot()["default"] == {
            "used": 0,
            "limit": 1,
        }
        assert scheduler.capacity_snapshot()["dead"] == {
            "used": 1,
            "limit": 1,
        }

        retried = asyncio.run(scheduler.dequeue("dead"))
        assert retried["id"] == task_id
        assert scheduler.complete(task_id)
        assert scheduler.capacity_snapshot()["dead"]["used"] == 0

# 2019-01-09T19:07:03 update

# 2019-02-18T12:30:02 update

# 2019-04-11T16:04:51 update

# 2019-04-17T16:25:46 update

# 2019-05-24T19:32:13 update

# 2019-07-02T12:54:25 update

# 2019-07-03T20:37:00 update

# 2019-08-21T19:37:17 update

# 2019-10-18T10:30:31 update

# 2019-10-25T09:01:38 update

# 2019-10-29T12:59:34 update

# 2019-11-05T10:07:06 update

# 2019-11-11T10:43:52 update

# 2020-01-17T13:40:02 update

# 2020-02-07T14:06:34 update

# 2020-04-03T08:53:40 update

# 2020-04-06T19:36:29 update

# 2020-05-12T11:51:05 update

# 2020-08-17T08:37:15 update

# 2020-09-15T10:39:38 update

# 2020-10-06T11:26:19 update

# 2020-10-21T13:32:43 update

# 2020-12-14T18:18:36 update

# 2020-12-23T17:15:03 update

# 2021-01-25T16:29:00 update

# 2021-02-23T11:23:50 update

# 2021-03-19T12:21:19 update

# 2021-07-29T18:48:25 update

# 2021-08-25T12:46:58 update

# 2021-09-09T16:27:13 update

# 2021-12-16T12:05:30 update

# 2022-05-07T14:05:12 update

# 2022-07-18T20:52:29 update

# 2022-07-31T18:42:26 update

# 2022-09-09T13:10:08 update

# 2023-01-04T15:16:57 update

# 2023-01-17T14:49:04 update

# 2023-02-15T13:51:30 update

# 2023-03-08T09:15:53 update

# 2023-03-23T16:32:20 update

# 2023-03-28T09:32:01 update

# 2023-05-05T17:28:22 update

# 2023-06-01T08:13:52 update

# 2023-06-20T09:58:10 update

# 2023-07-04T16:14:34 update

# 2023-07-17T20:49:40 update

# 2023-12-26T11:49:18 update

# 2024-05-27T11:00:06 update

# 2024-07-04T08:53:03 update

# 2024-07-18T16:19:02 update

# 2024-08-07T09:35:35 update

# 2024-08-22T14:32:14 update

# 2025-05-20T14:19:23 update

# 2025-07-17T17:54:48 update

# 2025-07-28T13:06:30 update

# 2025-12-22T19:05:25 update

# 2026-01-08T18:43:02 update

# 2026-01-12T16:53:28 update

# 2026-04-16T16:58:23 update
