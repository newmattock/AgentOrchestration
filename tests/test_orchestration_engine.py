import asyncio

import pytest

from src.orchestrator.engine import OrchestrationEngine


class TestOrchestrationEngine:
    def setup_method(self):
        self.engine = OrchestrationEngine()

    def test_enqueue_task_pins_worker_capability_version(self):
        agent_id = self.engine.registry.register(
            "hot-reload-worker",
            "worker.processor",
            {"capabilities": ["summarize"]},
        )

        task_id = self.engine.enqueue_task(
            agent_id,
            {"type": "summarize"},
            required_capabilities=["summarize"],
        )
        task = asyncio.run(self.engine.scheduler.dequeue())

        assert task["id"] == task_id
        assert task["target_agent"] == agent_id
        assert task["required_capabilities"] == ["summarize"]
        assert task["agent_capabilities_version"] == 1
        assert task_id in self.engine.scheduler._in_flight

    def test_dequeue_defers_missing_capability_before_in_flight(self):
        agent_id = self.engine.registry.register(
            "hot-reload-worker",
            "worker.processor",
            {"capabilities": ["summarize"]},
        )
        task_id = self.engine.enqueue_task(
            agent_id,
            {"type": "translate", "payload": {"secret": "not-logged"}},
            required_capabilities=["translate"],
        )

        task = asyncio.run(self.engine.scheduler.dequeue())

        assert task is None
        assert task_id not in self.engine.scheduler._in_flight
        assert task_id in self.engine.scheduler._deferred
        assert self.engine.dispatch_decisions[-1] == {
            "task_id": task_id,
            "target_agent": agent_id,
            "allowed": False,
            "reason": "missing_worker_capabilities",
        }
        assert "payload" not in self.engine.dispatch_decisions[-1]

    def test_enqueue_task_rejects_unknown_agent(self):
        with pytest.raises(ValueError, match="Agent missing not found"):
            self.engine.enqueue_task("missing", {"type": "summarize"})
