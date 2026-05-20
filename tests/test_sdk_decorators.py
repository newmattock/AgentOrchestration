import pytest

from src.sdk.decorators import agent


@pytest.mark.parametrize(
    "version",
    [
        "0.0.0",
        "1.2.3",
        "10.20.30",
        "1.2.3-alpha",
        "1.2.3-alpha.1",
        "1.2.3-rc.1+build.7",
        "1.2.3+build.7",
    ],
)
def test_agent_decorator_accepts_semantic_version_formats(version):
    @agent("worker", version=version)
    class WorkerAgent:
        pass

    assert WorkerAgent.__agent_config__["version"] == version


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


@pytest.mark.parametrize(
    "version",
    [
        "",
        " ",
        "1",
        "1.2",
        "1.2.3.4",
        "v1.2.3",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.x",
        "1.2.3-",
        "1.2.3+",
        "1.2.3-alpha..1",
        1,
        None,
    ],
)
def test_agent_decorator_rejects_malformed_versions(version):
    with pytest.raises(ValueError, match="semantic version"):
        agent("worker", version=version)
