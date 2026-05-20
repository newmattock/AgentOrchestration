"""Webhook delivery logging helpers with safe redaction."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


REDACTED = "[REDACTED]"

SENSITIVE_FIELD_TOKENS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "signature",
)

INTERNAL_ONLY_FIELDS = {
    "endpoint_secret",
    "internal_headers",
    "internal_metadata",
    "internal_retry_state",
    "internal_trace_id",
    "raw_headers",
    "signing_secret",
}


@dataclass(frozen=True)
class WebhookEndpoint:
    endpoint_id: str
    workspace_id: str
    url: str
    signing_secret: str
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    workspace_id: str
    endpoint_id: str
    event_id: str
    status: str
    attempt: int
    payload: Dict[str, Any]
    failure: Dict[str, Any] = field(default_factory=dict)
    callback: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class WebhookDeliveryLogs:
    def __init__(self):
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._records_by_key: Dict[tuple[str, str, str], DeliveryRecord] = {}
        self._records: List[DeliveryRecord] = []

    def register_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
        url: str,
        signing_secret: str,
        enabled: bool = True,
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            endpoint_id=endpoint_id,
            workspace_id=workspace_id,
            url=url,
            signing_secret=signing_secret,
            enabled=enabled,
        )
        self._endpoints[endpoint_id] = endpoint
        return endpoint

    def disable_endpoint(self, endpoint_id: str) -> bool:
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return False
        self._endpoints[endpoint_id] = WebhookEndpoint(
            endpoint_id=endpoint.endpoint_id,
            workspace_id=endpoint.workspace_id,
            url=endpoint.url,
            signing_secret=endpoint.signing_secret,
            enabled=False,
            version=endpoint.version,
        )
        return True

    def rotate_endpoint_secret(
        self,
        endpoint_id: str,
        signing_secret: str,
    ) -> bool:
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return False
        self._endpoints[endpoint_id] = WebhookEndpoint(
            endpoint_id=endpoint.endpoint_id,
            workspace_id=endpoint.workspace_id,
            url=endpoint.url,
            signing_secret=signing_secret,
            enabled=endpoint.enabled,
            version=endpoint.version + 1,
        )
        return True

    def record_success(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Dict[str, Any],
        callback: Optional[Dict[str, Any]] = None,
    ) -> DeliveryRecord:
        endpoint = self._scoped_endpoint(workspace_id, endpoint_id)
        if not endpoint.enabled:
            return self.record_failure(
                workspace_id,
                endpoint_id,
                event_id,
                payload,
                {"reason": "endpoint_disabled"},
                callback=callback,
                status="rejected",
            )
        return self._persist_once(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            attempt=1,
            status="delivered",
            payload=payload,
            failure={},
            callback=callback or {},
        )

    def record_failure(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Dict[str, Any],
        failure: Dict[str, Any],
        callback: Optional[Dict[str, Any]] = None,
        status: str = "failed",
    ) -> DeliveryRecord:
        endpoint = self._scoped_endpoint(workspace_id, endpoint_id)
        if status != "rejected" and not endpoint.enabled:
            status = "rejected"
            failure = {**failure, "reason": "endpoint_disabled"}
        return self._persist_once(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            attempt=1,
            status=status,
            payload=payload,
            failure=failure,
            callback=callback or {},
        )

    def record_retry(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Dict[str, Any],
        failure: Dict[str, Any],
    ) -> DeliveryRecord:
        endpoint = self._scoped_endpoint(workspace_id, endpoint_id)
        existing_retry = self._matching_records(
            workspace_id,
            endpoint_id,
            event_id,
            status="retry_scheduled",
        )
        if existing_retry:
            return existing_retry[-1]
        if not endpoint.enabled:
            return self.record_failure(
                workspace_id,
                endpoint_id,
                event_id,
                payload,
                {**failure, "reason": "endpoint_disabled"},
                status="rejected",
            )
        attempt = (
            len(self._matching_records(workspace_id, endpoint_id, event_id))
            + 1
        )
        return self._persist_once(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            attempt=attempt,
            status="retry_scheduled",
            payload=payload,
            failure=failure,
            callback={},
        )

    def workspace_records(self, workspace_id: str) -> List[DeliveryRecord]:
        return [
            record
            for record in self._records
            if record.workspace_id == workspace_id
        ]

    def _matching_records(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        status: Optional[str] = None,
    ) -> List[DeliveryRecord]:
        return [
            record
            for record in self._records
            if (
                record.workspace_id == workspace_id
                and record.endpoint_id == endpoint_id
                and record.event_id == event_id
                and (status is None or record.status == status)
            )
        ]

    def _persist_once(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        attempt: int,
        status: str,
        payload: Dict[str, Any],
        failure: Dict[str, Any],
        callback: Dict[str, Any],
    ) -> DeliveryRecord:
        key = (workspace_id, endpoint_id, event_id, str(attempt))
        if key in self._records_by_key:
            return self._records_by_key[key]

        record = DeliveryRecord(
            delivery_id=":".join(key),
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            attempt=attempt,
            status=status,
            payload=redact_webhook_payload(payload),
            failure=redact_webhook_payload(failure),
            callback=redact_webhook_payload(callback),
        )
        self._records_by_key[key] = record
        self._records.append(record)
        return record

    def _scoped_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> WebhookEndpoint:
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint or endpoint.workspace_id != workspace_id:
            raise ValueError("webhook endpoint is not available")
        return endpoint


def redact_webhook_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, nested in value.items():
            if _is_internal_only(key):
                continue
            if _is_sensitive(key):
                redacted[key] = REDACTED
                continue
            redacted[key] = redact_webhook_payload(nested)
        return redacted
    if isinstance(value, list):
        return [redact_webhook_payload(item) for item in value]
    return value


def contains_private_fields(value: Any, private_values: Iterable[str]) -> bool:
    private_strings = {str(private) for private in private_values}
    if isinstance(value, dict):
        return any(
            str(key) in private_strings
            or contains_private_fields(nested, private_strings)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(
            contains_private_fields(item, private_strings)
            for item in value
        )
    return str(value) in private_strings


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(token in normalized for token in SENSITIVE_FIELD_TOKENS)


def _is_internal_only(key: str) -> bool:
    return key.lower().replace("-", "_") in INTERNAL_ONLY_FIELDS
