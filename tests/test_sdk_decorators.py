import pytest

from src.sdk.decorators import agent


def test_agent_decorator_accepts_semantic_version_metadata():
    @agent(
        "worker",
        version="1.2.3-beta.1+build.7",
        description="handles queued work",
    )
    class WorkerAgent:
        pass

    assert WorkerAgent.__agent_config__ == {
        "name": "worker",
        "version": "1.2.3-beta.1+build.7",
        "description": "handles queued work",
    }


def test_agent_decorator_rejects_missing_patch_version():
    with pytest.raises(ValueError, match="semantic version"):
        agent("worker", version="1.2")


def test_agent_decorator_rejects_leading_zero_version():
    with pytest.raises(ValueError, match="semantic version"):
        agent("worker", version="01.2.3")


def test_agent_decorator_rejects_non_string_version():
    with pytest.raises(ValueError, match="semantic version"):
        agent("worker", version=1)
