import pytest

from src.common.webhooks import (
    REDACTED,
    WebhookDeliveryLogs,
    contains_private_fields,
    redact_webhook_payload,
)


PRIVATE_VALUES = {
    "Bearer live-secret",
    "cookie-secret",
    "endpoint-secret",
    "internal-trace-1",
    "retry-token-1",
    "signing-secret",
}


def test_valid_delivery_log_redacts_payload_and_callback_fields():
    logs = WebhookDeliveryLogs()
    logs.register_endpoint(
        "endpoint-a",
        "workspace-a",
        "https://example.com/webhook",
        "signing-secret",
    )

    record = logs.record_success(
        "workspace-a",
        "endpoint-a",
        "event-1",
        {
            "event": "agent.failed",
            "authorization": "Bearer live-secret",
            "headers": {
                "cookie": "cookie-secret",
                "x-request-id": "public-id",
            },
            "internal_trace_id": "internal-trace-1",
            "payload": {"status": "failed"},
        },
        callback={
            "status": 200,
            "endpoint_secret": "endpoint-secret",
            "response": {"token": "retry-token-1"},
        },
    )

    assert record.status == "delivered"
    assert record.payload["authorization"] == REDACTED
    assert record.payload["headers"]["cookie"] == REDACTED
    assert record.payload["headers"]["x-request-id"] == "public-id"
    assert "internal_trace_id" not in record.payload
    assert "endpoint_secret" not in record.callback
    assert record.callback["response"]["token"] == REDACTED
    assert not contains_private_fields(record.payload, PRIVATE_VALUES)
    assert not contains_private_fields(record.callback, PRIVATE_VALUES)


def test_rejected_disabled_endpoint_log_does_not_leak_secrets():
    logs = WebhookDeliveryLogs()
    logs.register_endpoint(
        "endpoint-a",
        "workspace-a",
        "https://example.com/webhook",
        "signing-secret",
        enabled=False,
    )

    record = logs.record_failure(
        "workspace-a",
        "endpoint-a",
        "event-1",
        {"event": "agent.failed", "api_key": "Bearer live-secret"},
        {
            "reason": "delivery_failed",
            "raw_headers": {"authorization": "Bearer live-secret"},
            "retry_token": "retry-token-1",
        },
    )

    assert record.status == "rejected"
    assert record.payload["api_key"] == REDACTED
    assert "raw_headers" not in record.failure
    assert record.failure["retry_token"] == REDACTED
    assert record.failure["reason"] == "endpoint_disabled"
    assert not contains_private_fields(record.failure, PRIVATE_VALUES)


def test_retry_records_are_idempotent_and_redacted():
    logs = WebhookDeliveryLogs()
    logs.register_endpoint(
        "endpoint-a",
        "workspace-a",
        "https://example.com/webhook",
        "signing-secret",
    )

    first = logs.record_retry(
        "workspace-a",
        "endpoint-a",
        "event-1",
        {"event": "agent.failed", "private_key": "endpoint-secret"},
        {"reason": "timeout", "authorization": "Bearer live-secret"},
    )
    duplicate = logs.record_retry(
        "workspace-a",
        "endpoint-a",
        "event-1",
        {"event": "agent.failed", "private_key": "changed-secret"},
        {"reason": "timeout", "authorization": "changed-secret"},
    )

    assert duplicate is first
    assert first.status == "retry_scheduled"
    assert first.payload["private_key"] == REDACTED
    assert first.failure["authorization"] == REDACTED
    assert len(logs.workspace_records("workspace-a")) == 1


def test_rotated_endpoint_rejects_stale_version_delivery_logs():
    logs = WebhookDeliveryLogs()
    endpoint = logs.register_endpoint(
        "endpoint-a",
        "workspace-a",
        "https://example.com/webhook",
        "signing-secret",
    )
    assert logs.rotate_endpoint_secret("endpoint-a", "rotated-secret")

    record = logs.record_success(
        "workspace-a",
        "endpoint-a",
        "event-rotated",
        {
            "event": "agent.failed",
            "signature": "Bearer live-secret",
            "private_debug": "internal-trace-1",
        },
        endpoint_version=endpoint.version,
    )

    assert record.status == "rejected"
    assert record.failure["reason"] == "endpoint_rotated"
    assert record.payload["signature"] == REDACTED
    assert "private_debug" not in record.payload
    assert not contains_private_fields(record.payload, PRIVATE_VALUES)


def test_workspace_isolation_blocks_cross_workspace_delivery_logs():
    logs = WebhookDeliveryLogs()
    logs.register_endpoint(
        "endpoint-a",
        "workspace-a",
        "https://example.com/webhook",
        "signing-secret",
    )

    with pytest.raises(ValueError, match="webhook endpoint is not available"):
        logs.record_failure(
            "workspace-b",
            "endpoint-a",
            "event-1",
            {"event": "agent.failed", "token": "Bearer live-secret"},
            {"reason": "timeout", "token": "retry-token-1"},
        )

    assert logs.workspace_records("workspace-a") == []
    assert logs.workspace_records("workspace-b") == []


def test_redaction_recurses_through_lists_and_removes_internal_fields():
    redacted = redact_webhook_payload(
        {
            "events": [
                {"token": "Bearer live-secret"},
                {"nested": {"signing_secret": "signing-secret"}},
            ],
            "internal_metadata": {"debug": "internal-trace-1"},
        },
    )

    assert redacted == {
        "events": [
            {"token": REDACTED},
            {"nested": {}},
        ],
    }
    assert not contains_private_fields(redacted, PRIVATE_VALUES)


def test_api_webhook_module_uses_same_redaction_contract():
    from src.api.webhooks import (
        WebhookDeliveryService,
        sanitize_delivery_fields,
    )

    logs = WebhookDeliveryService()
    logs.register_endpoint(
        "endpoint-a",
        "workspace-a",
        "https://example.com/webhook",
        "signing-secret",
    )

    record = logs.record_failure(
        "workspace-a",
        "endpoint-a",
        "event-1",
        {"private_debug": "internal-trace-1", "token": "retry-token-1"},
        {"reason": "timeout"},
    )

    assert "private_debug" not in record.payload
    assert record.payload["token"] == REDACTED
    assert sanitize_delivery_fields({"internal_headers": {"x": "y"}}) == {}
