import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from src.api.middleware import AuthMiddleware
from src.api.server import create_app


def _request(method, path, headers=None):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "scheme": "http",
            "query_string": b"",
            "headers": [
                (name.lower().encode(), value.encode())
                for name, value in (headers or {}).items()
            ],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


def test_cors_preflight_passes_to_cors_middleware_without_auth():
    client = TestClient(create_app())

    response = client.options(
        "/api/v2/agents",
        headers={
            "Origin": "https://console.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://console.example"
    )
    assert "GET" in response.headers["access-control-allow-methods"]
    assert response.text == "OK"


def test_real_options_request_without_preflight_headers_requires_auth():
    client = TestClient(create_app())

    response = client.options(
        "/api/v2/agents",
        headers={"Origin": "https://console.example"},
    )

    assert response.status_code == 401
    assert response.text == "Unauthorized"
    assert (
        response.headers["access-control-allow-origin"]
        == "https://console.example"
    )


def test_real_cors_request_requires_auth_and_keeps_cors_headers():
    client = TestClient(create_app())

    response = client.get(
        "/api/v2/agents",
        headers={"Origin": "https://console.example"},
    )

    assert response.status_code == 401
    assert response.text == "Unauthorized"
    assert (
        response.headers["access-control-allow-origin"]
        == "https://console.example"
    )


def test_authenticated_request_reaches_handler_and_logs_without_token(caplog):
    client = TestClient(create_app())
    secret_token = "Bearer sensitive-token-value"

    with caplog.at_level(logging.INFO, logger="src.api.middleware"):
        response = client.get(
            "/api/v2/agents",
            headers={"Authorization": secret_token},
        )

    assert response.status_code == 200
    assert response.json() == {"agents": []}
    assert "GET /api/v2/agents 200" in caplog.text
    assert secret_token not in caplog.text
    assert "Authorization" not in caplog.text


def test_preflight_and_auth_rejections_do_not_consume_rate_limit():
    client = TestClient(create_app())
    preflight_headers = {
        "Origin": "https://console.example",
        "Access-Control-Request-Method": "GET",
    }
    real_request_headers = {"Origin": "https://console.example"}

    for _ in range(105):
        preflight = client.options(
            "/api/v2/agents",
            headers=preflight_headers,
        )
        rejected = client.get(
            "/api/v2/agents",
            headers=real_request_headers,
        )

        assert preflight.status_code == 200
        assert rejected.status_code == 401

    authenticated = client.get(
        "/api/v2/agents",
        headers={
            "Origin": "https://console.example",
            "Authorization": "Bearer test-token",
        },
    )

    assert authenticated.status_code == 200


def test_auth_request_state_is_cleared_after_success():
    middleware = AuthMiddleware(app=None)
    request = _request(
        "GET",
        "/api/v2/agents",
        headers={"Authorization": "Bearer token"},
    )

    async def call_next(current_request):
        assert current_request.state.auth_checked is True
        assert current_request.state.authenticated is True
        return Response("ok")

    response = asyncio.run(middleware.dispatch(request, call_next))

    assert response.status_code == 200
    assert not hasattr(request.state, "auth_checked")
    assert not hasattr(request.state, "authenticated")


def test_auth_request_state_is_cleared_after_exception():
    middleware = AuthMiddleware(app=None)
    request = _request(
        "GET",
        "/api/v2/agents",
        headers={"Authorization": "Bearer token"},
    )

    async def call_next(current_request):
        assert current_request.state.auth_checked is True
        assert current_request.state.authenticated is True
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        asyncio.run(middleware.dispatch(request, call_next))

    assert not hasattr(request.state, "auth_checked")
    assert not hasattr(request.state, "authenticated")
