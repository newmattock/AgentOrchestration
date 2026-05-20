import asyncio

from src.agent import AgentStatus
from src.orchestrator.engine import OrchestrationEngine


def test_engine_rechecks_permission_before_lifecycle_transition():
    engine = OrchestrationEngine()
    agent_id = engine.registry.register(
        "test-agent",
        "worker.processor",
        {"permissions": ["tasks.run"]},
    )
    assert engine.registry.resolve_authorized(agent_id, "tasks.run")
    assert engine.registry.update_permissions(agent_id, [])
    errors = []

    async def capture_error(task, error):
        errors.append((task["id"], str(error)))

    engine.register_hook("on_error", capture_error)
    task = {
        "id": "task-1",
        "target_agent": agent_id,
        "required_permission": "tasks.run",
    }

    asyncio.run(engine._execute_task(task))

    agent = engine.registry.get(agent_id)
    assert agent["status"] == AgentStatus.PENDING.value
    assert agent["metrics"]["authorization_denials"] == 1
    assert errors == [
        (
            "task-1",
            f"Agent {agent_id} not found or not authorized",
        )
    ]
