import asyncio

from src.agent import AgentStatus
from src.orchestrator.engine import OrchestrationEngine


def test_engine_defers_unhealthy_target_before_execution():
    engine = OrchestrationEngine()
    agent_id = engine.registry.register("rolling-agent", "worker.processor")
    assert engine.registry.update_status(agent_id, AgentStatus.RUNNING)
    assert engine.registry.update_health(
        agent_id,
        "unhealthy",
        reason="rolling_deploy_probe_failed",
    )

    errors = []

    async def capture_error(task, error):
        errors.append((task["id"], str(error)))

    engine.register_hook("on_error", capture_error)

    asyncio.run(
        engine._execute_task(
            {
                "id": "task-1",
                "target_agent": agent_id,
            }
        )
    )

    assert errors == [("task-1", f"Agent {agent_id} is not routable")]
    assert engine.registry.get(agent_id)["status"] == "running"
    assert any(
        record["reason"] == "unhealthy"
        for record in engine.registry.audit_log()
    )
