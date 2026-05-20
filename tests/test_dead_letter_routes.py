import asyncio

import pytest
from fastapi import HTTPException

from src.api import routes
from src.orchestrator.scheduler import TaskScheduler


def build_dead_letter_scheduler():
    scheduler = TaskScheduler()
    scheduler._max_retries = 1
    task_id = scheduler.enqueue({
        "type": "email",
        "payload": {
            "recipient": "customer@example.com",
            "token": "secret-token",
        },
    })
    task = asyncio.run(scheduler.dequeue())
    assert not scheduler.fail(task["id"])
    return scheduler, task_id


def test_dead_letter_route_defaults_to_redacted_summary(monkeypatch):
    scheduler, task_id = build_dead_letter_scheduler()
    monkeypatch.setattr(routes, "scheduler", scheduler)

    response = asyncio.run(routes.list_dead_letters())

    [summary] = response["dead_letters"]
    assert summary["id"] == task_id
    assert "payload" not in summary
    assert summary["payload_summary"]["recipient"] == {"type": "str"}
    assert summary["payload_summary"]["token"] == "[redacted]"
    assert "secret-token" not in repr(response)
    assert "customer@example.com" not in repr(response)


def test_raw_dead_letter_route_audits_actor_and_reason(monkeypatch):
    scheduler, task_id = build_dead_letter_scheduler()
    monkeypatch.setattr(routes, "scheduler", scheduler)

    raw = asyncio.run(routes.get_raw_dead_letter(
        task_id,
        actor="operator-1",
        reason="debug incident",
    ))

    assert raw["payload"]["token"] == "secret-token"
    [audit_record] = scheduler.raw_access_audit()
    assert audit_record["actor"] == "operator-1"
    assert audit_record["reason"] == "debug incident"
    assert "payload" not in audit_record


def test_raw_dead_letter_route_rejects_missing_reason(monkeypatch):
    scheduler, task_id = build_dead_letter_scheduler()
    monkeypatch.setattr(routes, "scheduler", scheduler)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(routes.get_raw_dead_letter(
            task_id,
            actor="operator-1",
            reason="",
        ))

    assert excinfo.value.status_code == 400
