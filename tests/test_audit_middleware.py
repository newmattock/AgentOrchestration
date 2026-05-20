import logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware import (
    AuditMiddleware,
    AuthMiddleware,
    get_current_audit_actor,
)


SECRET_TOKEN = "super-secret-token"


def build_client(handler, *, raise_server_exceptions=True):
    app = Starlette(
        routes=[Route("/api/v2/workflows", handler)],
        middleware=[
            Middleware(AuthMiddleware),
            Middleware(AuditMiddleware),
        ],
    )
    return TestClient(
        app,
        raise_server_exceptions=raise_server_exceptions,
    )


def test_audit_actor_attaches_only_after_successful_auth():
    seen_actors = []

    async def handler(request):
        seen_actors.append(
            (
                request.state.audit_actor,
                get_current_audit_actor(),
            ),
        )
        return PlainTextResponse("ok")

    response = build_client(handler).get(
        "/api/v2/workflows",
        headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.headers["X-Audit-Status"] == "attached"
    assert response.headers["X-Audit-Actor"].startswith("bearer:")
    assert SECRET_TOKEN not in response.headers["X-Audit-Actor"]
    assert seen_actors == [
        (
            response.headers["X-Audit-Actor"],
            response.headers["X-Audit-Actor"],
        ),
    ]
    assert get_current_audit_actor() is None


def test_rejected_request_does_not_attach_actor_or_call_handler():
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return PlainTextResponse("should not run")

    response = build_client(handler).get(
        "/api/v2/workflows",
        headers={"Authorization": "Basic not-a-bearer-token"},
    )

    assert response.status_code == 401
    assert response.headers["X-Audit-Status"] == "rejected"
    assert "X-Audit-Actor" not in response.headers
    assert not called
    assert SECRET_TOKEN not in response.text
    assert get_current_audit_actor() is None


def test_exception_path_clears_audit_state_and_avoids_request_material(caplog):
    caplog.set_level(logging.ERROR, logger="src.api.middleware")
    seen_actors = []

    async def handler(request):
        seen_actors.append(get_current_audit_actor())
        raise RuntimeError("handler failed")

    response = build_client(
        handler,
        raise_server_exceptions=False,
    ).get(
        "/api/v2/workflows?token=should-not-be-logged",
        headers={"Authorization": f"Bearer {SECRET_TOKEN}"},
    )

    assert response.status_code == 500
    assert seen_actors and seen_actors[0].startswith("bearer:")
    assert get_current_audit_actor() is None
    assert SECRET_TOKEN not in caplog.text
    assert "should-not-be-logged" not in caplog.text


def test_public_auth_token_route_does_not_get_audit_actor():
    seen_actors = []

    async def handler(request):
        seen_actors.append(get_current_audit_actor())
        return PlainTextResponse("token")

    app = Starlette(
        routes=[Route("/api/v2/auth/token", handler)],
        middleware=[
            Middleware(AuthMiddleware),
            Middleware(AuditMiddleware),
        ],
    )

    response = TestClient(app).get("/api/v2/auth/token")

    assert response.status_code == 200
    assert "X-Audit-Actor" not in response.headers
    assert "X-Audit-Status" not in response.headers
    assert seen_actors == [None]
