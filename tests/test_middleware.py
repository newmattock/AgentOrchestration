from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.api.middleware import AUTH_DECISION_HEADER, AuthMiddleware
from src.api.server import create_app


def build_app(seen):
    app = FastAPI()

    @app.get("/api/v2/secure")
    async def secure(request: Request):
        seen["secure_state"] = request.state
        seen["secure_context"] = dict(request.state.auth_context)
        return {"ok": True}

    @app.options("/api/v2/non_preflight")
    async def non_preflight(request: Request):
        seen["non_preflight_state"] = request.state
        return {"ok": True}

    @app.post("/api/v2/auth/token")
    async def token(request: Request):
        seen["token_state"] = request.state
        return {"token": "issued"}

    @app.get("/api/v2/error")
    async def error(request: Request):
        seen["error_state"] = request.state
        seen["error_context"] = dict(request.state.auth_context)
        raise RuntimeError("handler exploded")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://client.example"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthMiddleware)
    return app


def test_cors_preflight_bypasses_auth_without_handler_state():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.options(
        "/api/v2/secure",
        headers={
            "Origin": "https://client.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers[AUTH_DECISION_HEADER] == "cors-preflight"
    assert response.headers["access-control-allow-origin"] == (
        "https://client.example"
    )
    assert "secure_state" not in seen


def test_real_options_request_without_preflight_headers_requires_auth():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.options("/api/v2/non_preflight")

    assert response.status_code == 401
    assert response.headers[AUTH_DECISION_HEADER] == "rejected"
    assert response.headers["www-authenticate"] == "Bearer"
    assert "non_preflight_state" not in seen


def test_real_api_request_requires_bearer_before_handler():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.get("/api/v2/secure")

    assert response.status_code == 401
    assert response.headers[AUTH_DECISION_HEADER] == "rejected"
    assert "secure_state" not in seen


def test_real_cors_api_request_requires_bearer_before_handler():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.get(
        "/api/v2/secure",
        headers={"Origin": "https://client.example"},
    )

    assert response.status_code == 401
    assert response.headers[AUTH_DECISION_HEADER] == "rejected"
    assert "secure_state" not in seen


def test_authenticated_request_sets_and_clears_request_state():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.get(
        "/api/v2/secure",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.headers[AUTH_DECISION_HEADER] == "accepted"
    assert seen["secure_context"] == {
        "authenticated": True,
        "cors_preflight": False,
        "scheme": "bearer",
    }
    assert not hasattr(seen["secure_state"], "auth_context")
    assert "test-token" not in repr(response.headers)


def test_auth_state_is_cleared_when_handler_raises():
    seen = {}
    client = TestClient(build_app(seen), raise_server_exceptions=False)

    response = client.get(
        "/api/v2/error",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 500
    assert seen["error_context"]["authenticated"] is True
    assert not hasattr(seen["error_state"], "auth_context")


def test_auth_logs_do_not_include_authorization_header(caplog):
    seen = {}
    client = TestClient(build_app(seen))

    with caplog.at_level("WARNING", logger="src.api.middleware"):
        response = client.get(
            "/api/v2/secure",
            headers={"Authorization": "Basic very-secret"},
        )

    assert response.status_code == 401
    assert "very-secret" not in caplog.text
    assert "Authorization" not in caplog.text


def test_unprotected_routes_skip_auth():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.get("/health")

    assert response.status_code == 200
    assert AUTH_DECISION_HEADER not in response.headers


def test_token_route_skips_auth():
    seen = {}
    client = TestClient(build_app(seen))

    response = client.post("/api/v2/auth/token")

    assert response.status_code == 200
    assert response.json() == {"token": "issued"}
    assert AUTH_DECISION_HEADER not in response.headers
    assert not hasattr(seen["token_state"], "auth_context")


def test_create_app_preflight_stays_outside_auth():
    client = TestClient(create_app())

    response = client.options(
        "/api/v2/agents",
        headers={
            "Origin": "https://client.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://client.example"
    )
    assert AUTH_DECISION_HEADER not in response.headers


def test_create_app_rejected_cors_request_keeps_cors_headers():
    client = TestClient(create_app())

    response = client.get(
        "/api/v2/agents",
        headers={"Origin": "https://client.example"},
    )

    assert response.status_code == 401
    assert response.headers[AUTH_DECISION_HEADER] == "rejected"
    assert response.headers["access-control-allow-origin"] == (
        "https://client.example"
    )


def test_create_app_authenticated_cors_request_reaches_handler():
    client = TestClient(create_app())

    response = client.get(
        "/api/v2/agents",
        headers={
            "Origin": "https://client.example",
            "Authorization": "Bearer test-token",
        },
    )

    assert response.status_code == 200
    assert response.headers[AUTH_DECISION_HEADER] == "accepted"
    assert response.headers["access-control-allow-origin"] == (
        "https://client.example"
    )
