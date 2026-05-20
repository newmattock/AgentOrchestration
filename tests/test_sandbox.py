from src.agent import sandbox as sandbox_module
from src.agent.sandbox import AgentSandbox, ResourceLimits


def test_apply_limits_noops_when_resource_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_module, "resource", None)

    sandbox = AgentSandbox(base_path=str(tmp_path))
    sandbox.apply_limits("agent-1", ResourceLimits())
