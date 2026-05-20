import asyncio

from src.orchestrator.engine import OrchestrationEngine


def test_engine_persists_completion_before_post_execute_hooks():
    engine = OrchestrationEngine()
    agent_id = engine.registry.register("worker", "runtime.worker")
    task_id = engine.scheduler.enqueue({
        "type": "run",
        "target_agent": agent_id,
    })
    task = asyncio.run(engine.scheduler.dequeue())
    observed = {}

    async def post_execute(task, result):
        observed["outcome"] = engine.scheduler.terminal_outcome(task["id"])
        observed["in_flight"] = engine.scheduler.is_in_flight(task["id"])
        observed["result"] = result

    engine.register_hook("post_execute", post_execute)

    asyncio.run(engine._execute_task(task))

    outcome = engine.scheduler.terminal_outcome(task_id)
    assert outcome["status"] == "completed"
    assert outcome["result"]["status"] == "completed"
    assert observed["outcome"]["task_id"] == task_id
    assert observed["outcome"]["status"] == "completed"
    assert observed["in_flight"] is False
    assert observed["result"] == outcome["result"]
