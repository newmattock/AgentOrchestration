"""API-facing webhook delivery log redaction helpers."""

from src.common.webhooks import (
    REDACTED,
    DeliveryRecord,
    WebhookDeliveryLogs,
    WebhookEndpoint,
    contains_private_fields,
    redact_webhook_payload,
)


WebhookDeliveryService = WebhookDeliveryLogs
sanitize_delivery_fields = redact_webhook_payload

__all__ = [
    "DeliveryRecord",
    "REDACTED",
    "WebhookDeliveryLogs",
    "WebhookDeliveryService",
    "WebhookEndpoint",
    "contains_private_fields",
    "redact_webhook_payload",
    "sanitize_delivery_fields",
]
