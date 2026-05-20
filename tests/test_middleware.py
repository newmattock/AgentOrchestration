import asyncio

import pytest
from starlette.responses import Response
from starlette.testclient import TestClient

from src.api.middleware import RateLimitMiddleware
from src.api.server import create_app


class BodyReadingApp:
    def __init__(self):
        self.body_reads = 0
        self.body_bytes = 0
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        assert scope["state"]["rate_limit"]["allowed"] is True
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break
        self.body_reads += 1
        self.body_bytes += len(body)
        response = Response("ok")
        await response(scope, receive, send)


class FailingApp:
    def __init__(self):
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.scope = scope
        assert scope["state"]["rate_limit"]["allowed"] is True
        raise RuntimeError("handler exploded")


async def noop_app(scope, receive, send):
    response = Response("ok")
    await response(scope, receive, send)


def test_create_app_places_rate_limit_first_in_ingress_stack():
    app = create_app()
    assert app.user_middleware[0].cls is RateLimitMiddleware


def test_rate_limit_rejects_before_downstream_body_read():
    current_time = [100.0]
    app = BodyReadingApp()
    client = TestClient(
        RateLimitMiddleware(
            app,
            max_requests=1,
            window=60,
            time_provider=lambda: current_time[0],
        )
    )

    accepted = client.post("/api/v2/agents", content=b"first")
    rejected = client.post("/api/v2/agents", content=b"second-secret")

    assert accepted.status_code == 200
    assert accepted.headers["x-ratelimit-decision"] == "allowed"
    assert accepted.headers["x-ratelimit-limit"] == "1"
    assert accepted.headers["x-ratelimit-remaining"] == "0"
    assert accepted.headers["x-ratelimit-window"] == "60"
    assert rejected.status_code == 429
    assert rejected.headers["x-ratelimit-decision"] == "limited"
    assert rejected.headers["x-ratelimit-remaining"] == "0"
    assert rejected.headers["retry-after"] == "60"
    assert app.body_reads == 1
    assert app.body_bytes == len(b"first")
    assert "rate_limit" not in app.scopes[0]["state"]


def test_rate_limit_rejection_does_not_call_receive():
    asyncio.run(_assert_rejection_does_not_call_receive())


async def _assert_rejection_does_not_call_receive():
    receive_calls = 0

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        return {
            "type": "http.request",
            "body": b"do-not-read",
            "more_body": False,
        }

    sent = []

    async def send(message):
        sent.append(message)

    middleware = RateLimitMiddleware(noop_app, max_requests=0, window=60)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v2/agents",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "state": {},
        },
        receive,
        send,
    )

    assert receive_calls == 0
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 429


def test_rate_limit_state_is_cleared_when_downstream_raises():
    app = FailingApp()
    client = TestClient(RateLimitMiddleware(app, max_requests=5, window=60))

    with pytest.raises(RuntimeError, match="handler exploded"):
        client.get("/api/v2/fail")

    assert app.scope is not None
    assert "rate_limit" not in app.scope["state"]
