from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.api.auth import CredentialRecord, IntegrationAuthService
from src.api.routes import webhook_registry
from src.api.server import create_app


WORKSPACE_ID = "workspace-a"


def build_client(service: IntegrationAuthService) -> TestClient:
    webhook_registry.clear()
    app = create_app()
    app.state.integration_auth_service = service
    return TestClient(app)


def credential(**overrides) -> CredentialRecord:
    values = {
        "principal_id": "user-1",
        "workspace_roles": {WORKSPACE_ID: "admin"},
        "scopes": frozenset({"webhooks:manage"}),
    }
    values.update(overrides)
    return CredentialRecord(**values)


def post_webhook(client: TestClient, **kwargs):
    return client.post(
        f"/api/v2/workspaces/{WORKSPACE_ID}/webhooks",
        json={"url": "https://example.test/hook", "events": ["agent.created"]},
        **kwargs,
    )


@pytest.mark.parametrize(
    ("record", "expected_detail"),
    [
        (credential(disabled=True), "Principal is disabled"),
        (credential(revoked=True), "Credential has been revoked"),
        (
            credential(
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
            "Credential has expired",
        ),
    ],
)
def test_rejects_stale_token_principals(record, expected_detail):
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_token("token-a", record)
    client = build_client(service)

    response = post_webhook(
        client,
        headers={"Authorization": "Bearer token-a"},
    )

    assert response.status_code == 401
    assert expected_detail in response.text
    assert webhook_registry == {}


def test_webhook_management_rejects_anonymous_principal():
    client = build_client(IntegrationAuthService(allow_legacy_bearer=False))

    response = post_webhook(client)

    assert response.status_code == 401
    assert "Missing integration credential" in response.text
    assert webhook_registry == {}


@pytest.mark.parametrize(
    ("headers", "expected_detail"),
    [
        ({"Authorization": "Basic token-a"}, "Malformed authorization header"),
        ({"Authorization": "Bearer "}, "Malformed authorization header"),
        (
            {"Authorization": "Bearer unknown"},
            "Invalid integration credential",
        ),
    ],
)
def test_webhook_management_rejects_malformed_token_principals(
    headers,
    expected_detail,
):
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_token("token-a", credential())
    client = build_client(service)

    response = post_webhook(client, headers=headers)

    assert response.status_code == 401
    assert expected_detail in response.text
    assert webhook_registry == {}


@pytest.mark.parametrize(
    "record",
    [
        credential(scopes=frozenset({"agents:read"})),
        credential(workspace_roles={WORKSPACE_ID: "viewer"}),
        credential(workspace_roles={"other-workspace": "admin"}),
    ],
)
def test_webhook_management_rejects_insufficient_principals(record):
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_token("token-a", record)
    client = build_client(service)

    response = post_webhook(
        client,
        headers={"Authorization": "Bearer token-a"},
    )

    assert response.status_code == 403
    assert webhook_registry == {}


def test_webhook_management_rejects_disabled_browser_session():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_session("session-a", credential(disabled=True))
    client = build_client(service)
    client.cookies.set("ao_session", "session-a")

    response = post_webhook(client)

    assert response.status_code == 401
    assert "Principal is disabled" in response.text
    assert webhook_registry == {}


def test_webhook_management_rejects_disabled_integration_session_header():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_session("session-a", credential(disabled=True))
    client = build_client(service)

    response = post_webhook(
        client,
        headers={"x-integration-session": "session-a"},
    )

    assert response.status_code == 401
    assert "Principal is disabled" in response.text
    assert webhook_registry == {}


def test_webhook_management_rejects_malformed_integration_session_header():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_session("session-a", credential())
    client = build_client(service)

    response = post_webhook(
        client,
        headers={"x-integration-session": " "},
    )

    assert response.status_code == 401
    assert "Malformed integration session header" in response.text
    assert webhook_registry == {}


def test_authorized_token_principal_can_manage_webhooks():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_token("token-a", credential())
    client = build_client(service)

    response = post_webhook(
        client,
        headers={"Authorization": "Bearer token-a"},
    )
    listing = client.get(
        f"/api/v2/workspaces/{WORKSPACE_ID}/webhooks",
        headers={"Authorization": "Bearer token-a"},
    )

    assert response.status_code == 200
    assert response.json()["webhook"]["created_by"] == "user-1"
    assert listing.status_code == 200
    assert (
        listing.json()["webhooks"][0]["url"]
        == "https://example.test/hook"
    )


def test_authorized_browser_session_can_manage_webhooks():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_session(
        "session-a",
        credential(principal_id="browser-user"),
    )
    client = build_client(service)
    client.cookies.set("ao_session", "session-a")

    response = post_webhook(client)

    assert response.status_code == 200
    assert response.json()["webhook"]["created_by"] == "browser-user"


def test_authorized_integration_session_header_can_manage_webhooks():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_session(
        "session-a",
        credential(principal_id="header-user"),
    )
    client = build_client(service)

    response = post_webhook(
        client,
        headers={"x-integration-session": " session-a "},
    )

    assert response.status_code == 200
    assert response.json()["webhook"]["created_by"] == "header-user"


def test_integration_session_header_takes_precedence_over_cookie():
    service = IntegrationAuthService(allow_legacy_bearer=False)
    service.register_session(
        "cookie-session",
        credential(principal_id="cookie-user", disabled=True),
    )
    service.register_session(
        "header-session",
        credential(principal_id="header-user"),
    )
    client = build_client(service)
    client.cookies.set("ao_session", "cookie-session")

    response = post_webhook(
        client,
        headers={"x-integration-session": "header-session"},
    )

    assert response.status_code == 200
    assert response.json()["webhook"]["created_by"] == "header-user"
