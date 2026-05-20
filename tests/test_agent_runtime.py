import importlib.util
import sys
from pathlib import Path


runtime_path = (
    Path(__file__).resolve().parents[1] / "src" / "agent" / "runtime.py"
)
runtime_spec = importlib.util.spec_from_file_location(
    "agent_runtime", runtime_path
)
agent_runtime = importlib.util.module_from_spec(runtime_spec)
assert runtime_spec and runtime_spec.loader
runtime_spec.loader.exec_module(agent_runtime)

AgentRuntime = agent_runtime.AgentRuntime
RuntimeState = agent_runtime.RuntimeState


def test_get_state_treats_successful_process_exit_as_stopped():
    runtime = AgentRuntime()

    assert runtime.start("agent-1", [sys.executable, "-c", "pass"])
    runtime._processes["agent-1"].wait(timeout=5)

    assert runtime.get_state("agent-1") == RuntimeState.STOPPED


def test_get_state_preserves_nonzero_process_exit_as_crashed():
    runtime = AgentRuntime()

    assert runtime.start(
        "agent-1", [sys.executable, "-c", "import sys; sys.exit(7)"]
    )
    runtime._processes["agent-1"].wait(timeout=5)

    assert runtime.get_state("agent-1") == RuntimeState.CRASHED
