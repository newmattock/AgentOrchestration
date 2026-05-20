import pytest
from fastapi.testclient import TestClient

from src.agent import AgentRegistry
from src.api import routes
from src.api.agent_config import AgentConfigUpdateError, update_agent_config
from src.api.server import create_app


AUTH = {"Authorization": "Bearer test-token"}


class GuardedRegistry:
    def update_config_if_version(self, *_args, **_kwargs):
        raise AssertionError("config update path should not run")


def make_client():
    routes.registry = AgentRegistry()
    return TestClient(create_app())


def register_agent(config=None):
    return routes.registry.register(
        "test-agent",
        "worker.processor",
        config or {"retries": 1, "mode": "safe"},
    )


def test_authorized_config_update_returns_next_etag():
    client = make_client()
    agent_id = register_agent()

    response = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={**AUTH, "If-Match": '"1"'},
        json={"config": {"retries": 2}},
    )

    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    assert response.json() == {
        "agent_id": agent_id,
        "config": {"retries": 2},
    }
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 2}
    assert agent["config_version"] == 2


def test_stale_etag_cannot_overwrite_newer_agent_config():
    client = make_client()
    agent_id = register_agent()

    first = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={**AUTH, "If-Match": '"1"'},
        json={"config": {"retries": 2}},
    )
    assert first.status_code == 200

    stale = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={**AUTH, "If-Match": '"1"'},
        json={"config": {"retries": 99}},
    )

    assert stale.status_code == 412
    assert stale.json()["detail"] == "Stale agent config ETag"
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 2}
    assert agent["config_version"] == 2


def test_config_update_without_auth_is_rejected_before_mutation():
    client = make_client()
    agent_id = register_agent()

    response = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={"If-Match": '"1"'},
        json={"config": {"retries": 99}},
    )

    assert response.status_code == 401
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 1, "mode": "safe"}
    assert agent["config_version"] == 1


def test_missing_if_match_is_rejected_before_mutation():
    client = make_client()
    agent_id = register_agent()

    response = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers=AUTH,
        json={"config": {"retries": 99}},
    )

    assert response.status_code == 428
    assert response.json()["detail"] == "If-Match header is required"
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 1, "mode": "safe"}
    assert agent["config_version"] == 1


def test_malformed_if_match_is_rejected_before_mutation():
    client = make_client()
    agent_id = register_agent()

    response = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={**AUTH, "If-Match": "1"},
        json={"config": {"retries": 99}},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "If-Match must be a quoted config version"
    )
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 1, "mode": "safe"}
    assert agent["config_version"] == 1


def test_malformed_config_payload_is_rejected_before_mutation():
    client = make_client()
    agent_id = register_agent()

    response = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={**AUTH, "If-Match": '"1"'},
        json={"config": ["not", "an", "object"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "config must be an object"
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 1, "mode": "safe"}
    assert agent["config_version"] == 1


def test_malformed_top_level_body_uses_service_guard_before_mutation():
    client = make_client()
    agent_id = register_agent()

    response = client.patch(
        f"/api/v2/agents/{agent_id}/config",
        headers={**AUTH, "If-Match": '"1"'},
        json=["not", "an", "object"],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "request body must be an object"
    agent = routes.registry.get(agent_id)
    assert agent["config"] == {"retries": 1, "mode": "safe"}
    assert agent["config_version"] == 1


def test_invalid_agent_id_is_rejected_before_route_update_path():
    client = make_client()
    routes.registry = GuardedRegistry()

    response = client.patch(
        "/api/v2/agents/bad..id/config",
        headers={**AUTH, "If-Match": '"1"'},
        json={"config": {"retries": 2}},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid agent_id"


def test_service_rejects_malformed_requests_before_update_path():
    cases = [
        (
            "agent-1",
            ["not-object"],
            '"1"',
            "request body must be an object",
            400,
        ),
        ("agent-1", {}, '"1"', "config is required", 400),
        (
            "agent-1",
            {"config": ["bad"]},
            '"1"',
            "config must be an object",
            400,
        ),
        (
            "agent-1",
            {"config": {"retries": 2}},
            None,
            "If-Match header is required",
            428,
        ),
        (
            "agent-1",
            {"config": {"retries": 2}},
            "1",
            "If-Match must be a quoted config version",
            400,
        ),
        (
            "../agent",
            {"config": {"retries": 2}},
            '"1"',
            "invalid agent_id",
            400,
        ),
    ]

    for agent_id, payload, if_match, detail, status_code in cases:
        with pytest.raises(AgentConfigUpdateError) as exc:
            update_agent_config(
                GuardedRegistry(),
                agent_id,
                payload,
                if_match,
            )

        assert exc.value.status_code == status_code
        assert exc.value.detail == detail
