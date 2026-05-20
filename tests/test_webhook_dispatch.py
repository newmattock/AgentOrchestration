from src.api.webhooks import WebhookDispatchController, WebhookDispatchError


def test_webhook_dispatch_delivers_valid_endpoint_once():
    calls = []

    def worker(endpoint, payload):
        calls.append((endpoint.endpoint_id, dict(payload)))

    controller = WebhookDispatchController(delivery_worker=worker)
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
        limit_per_window=2,
        window_seconds=60,
    )

    result = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"safe": True},
        idempotency_key="idem-1",
        now=1000.0,
    )

    assert result["status"] == "delivered"
    assert result["reason"] == "accepted"
    assert calls == [("endpoint-1", {"safe": True})]
    assert controller.get_delivery(
        "workspace-a",
        "endpoint-1",
        "event-1",
        "idem-1",
    ) == result
    assert "generation" not in result
    assert "_window_count" not in str(result)


def test_webhook_dispatch_rejects_rate_limit_before_delivery_work():
    calls = []
    controller = WebhookDispatchController(
        delivery_worker=lambda endpoint, payload: calls.append(payload)
    )
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
        limit_per_window=1,
        window_seconds=30,
    )

    first = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"secret": "not-recorded"},
        idempotency_key="idem-1",
        now=1000.0,
    )
    second = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-2",
        {"secret": "not-delivered"},
        idempotency_key="idem-2",
        now=1005.1,
    )

    assert first["status"] == "delivered"
    assert second["status"] == "rejected"
    assert second["reason"] == "endpoint_rate_limited"
    assert second["retry_after"] == 25
    assert second["delivery_id"] is None
    assert calls == [{"secret": "not-recorded"}]
    assert "not-delivered" not in str(second)


def test_webhook_backpressure_retry_can_deliver_after_window_reset():
    calls = []
    controller = WebhookDispatchController(
        delivery_worker=lambda endpoint, payload: calls.append(payload)
    )
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
        limit_per_window=1,
        window_seconds=30,
    )

    controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"version": 1},
        idempotency_key="idem-1",
        now=1000.0,
    )
    limited = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-2",
        {"version": 2},
        idempotency_key="retry-key",
        now=1001.0,
    )
    retry = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-2",
        {"version": 3},
        idempotency_key="retry-key",
        now=1031.0,
        attempt=2,
    )

    assert limited["status"] == "rejected"
    assert retry["status"] == "delivered"
    assert calls == [{"version": 1}, {"version": 3}]


def test_webhook_delivered_retry_is_idempotent_for_same_key():
    calls = []
    controller = WebhookDispatchController(
        delivery_worker=lambda endpoint, payload: calls.append(payload)
    )
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )

    first = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"version": 1},
        idempotency_key="retry-key",
        attempt=1,
    )
    retry = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"version": 2},
        idempotency_key="retry-key",
        attempt=2,
    )

    assert retry == first
    assert calls == [{"version": 1}]


def test_webhook_workspace_isolation_hides_foreign_endpoint():
    calls = []
    controller = WebhookDispatchController(
        delivery_worker=lambda endpoint, payload: calls.append(payload)
    )
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )

    result = controller.dispatch(
        "workspace-b",
        "endpoint-1",
        "event-1",
        {"work": "foreign"},
        idempotency_key="idem-foreign",
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "endpoint_not_found"
    assert calls == []
    assert "workspace-a" not in str(result)


def test_webhook_disabled_endpoint_rejects_without_delivery():
    calls = []
    controller = WebhookDispatchController(
        delivery_worker=lambda endpoint, payload: calls.append(payload)
    )
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )
    assert controller.disable_endpoint("workspace-a", "endpoint-1")

    first = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"work": "blocked"},
        idempotency_key="idem-disabled",
    )
    retry = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"work": "still-blocked"},
        idempotency_key="idem-disabled",
        attempt=2,
    )

    assert first["status"] == "rejected"
    assert first["reason"] == "endpoint_disabled"
    assert retry == first
    assert calls == []
    assert "blocked" not in str(first)


def test_webhook_rotation_keeps_retry_idempotent_and_new_events_valid():
    calls = []
    controller = WebhookDispatchController(
        delivery_worker=lambda endpoint, payload: calls.append(endpoint.url)
    )
    controller.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://old.example.com/hook",
    )
    first = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"ok": True},
        idempotency_key="idem-1",
    )
    assert controller.rotate_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://new.example.com/hook",
    )
    retry = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"ok": True},
        idempotency_key="idem-1",
    )
    next_event = controller.dispatch(
        "workspace-a",
        "endpoint-1",
        "event-2",
        {"ok": True},
        idempotency_key="idem-2",
    )

    assert retry == first
    assert next_event["status"] == "delivered"
    assert calls == [
        "https://old.example.com/hook",
        "https://new.example.com/hook",
    ]


def test_webhook_rejects_malformed_registration_before_storage():
    controller = WebhookDispatchController()

    try:
        controller.register_endpoint(
            "workspace-a",
            "endpoint-1",
            "file:///tmp/hook",
        )
    except WebhookDispatchError as exc:
        assert "absolute http" in str(exc)
    else:
        raise AssertionError("expected malformed webhook endpoint rejection")
