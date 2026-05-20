"""Webhook endpoint registration and fanout dispatch controls."""

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse


class WebhookDispatchError(ValueError):
    """Raised when a webhook endpoint or dispatch request is malformed."""


@dataclass
class WebhookEndpoint:
    endpoint_id: str
    workspace_id: str
    url: str
    limit_per_window: int = 60
    window_seconds: int = 60
    enabled: bool = True
    generation: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    _window_start: float = 0.0
    _window_count: int = 0

    def public_view(self) -> Dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "workspace_id": self.workspace_id,
            "url": self.url,
            "enabled": self.enabled,
            "limit_per_window": self.limit_per_window,
            "window_seconds": self.window_seconds,
        }


@dataclass(frozen=True)
class WebhookDeliveryRecord:
    delivery_id: str
    workspace_id: str
    endpoint_id: str
    event_id: str
    idempotency_key: str
    status: str
    reason: str
    attempt: int
    retry_after: int = 0
    created_at: float = field(default_factory=time.time)

    def public_view(self) -> Dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "workspace_id": self.workspace_id,
            "endpoint_id": self.endpoint_id,
            "event_id": self.event_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "reason": self.reason,
            "attempt": self.attempt,
            "retry_after": self.retry_after,
            "created_at": self.created_at,
        }


class WebhookDispatchController:
    def __init__(
        self,
        delivery_worker: Optional[
            Callable[[WebhookEndpoint, Dict[str, Any]], None]
        ] = None,
    ):
        self._endpoints: Dict[Tuple[str, str], WebhookEndpoint] = {}
        self._deliveries: Dict[
            Tuple[str, str, str, str],
            WebhookDeliveryRecord,
        ] = {}
        self._delivery_worker = delivery_worker or self._noop_delivery

    def register_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
        limit_per_window: int = 60,
        window_seconds: int = 60,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        self._validate_scope(workspace_id, endpoint_id)
        self._validate_url(url)
        if limit_per_window < 1:
            raise WebhookDispatchError("limit_per_window must be positive")
        if window_seconds < 1:
            raise WebhookDispatchError("window_seconds must be positive")

        endpoint_key = self._endpoint_key(workspace_id, endpoint_id)
        existing = self._endpoints.get(endpoint_key)
        generation = existing.generation + 1 if existing else 1
        endpoint = WebhookEndpoint(
            endpoint_id=endpoint_id,
            workspace_id=workspace_id,
            url=url,
            limit_per_window=limit_per_window,
            window_seconds=window_seconds,
            enabled=enabled,
            generation=generation,
        )
        self._endpoints[endpoint_key] = endpoint
        return endpoint.public_view()

    def disable_endpoint(self, workspace_id: str, endpoint_id: str) -> bool:
        endpoint = self._endpoints.get(
            self._endpoint_key(workspace_id, endpoint_id)
        )
        if not endpoint:
            return False
        endpoint.enabled = False
        endpoint.generation += 1
        endpoint.updated_at = time.time()
        return True

    def rotate_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
    ) -> bool:
        endpoint = self._endpoints.get(
            self._endpoint_key(workspace_id, endpoint_id)
        )
        if not endpoint:
            return False
        self._validate_url(url)
        endpoint.url = url
        endpoint.generation += 1
        endpoint.updated_at = time.time()
        return True

    def dispatch(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        attempt: int = 1,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._validate_scope(workspace_id, endpoint_id)
        if not event_id:
            raise WebhookDispatchError("event_id is required")
        if attempt < 1:
            raise WebhookDispatchError("attempt must be positive")
        if not isinstance(payload, dict):
            raise WebhookDispatchError("payload must be an object")

        current_time = time.time() if now is None else now
        key = idempotency_key or event_id
        delivery_key = (workspace_id, endpoint_id, event_id, key)
        existing = self._deliveries.get(delivery_key)
        if existing:
            return existing.public_view()

        endpoint = self._endpoints.get(
            self._endpoint_key(workspace_id, endpoint_id)
        )
        if not endpoint:
            return self._record_delivery(
                delivery_key,
                workspace_id,
                endpoint_id,
                event_id,
                key,
                "rejected",
                "endpoint_not_found",
                attempt,
            ).public_view()

        if not endpoint.enabled:
            return self._record_delivery(
                delivery_key,
                workspace_id,
                endpoint_id,
                event_id,
                key,
                "rejected",
                "endpoint_disabled",
                attempt,
            ).public_view()

        retry_after = self._retry_after(endpoint, current_time)
        if retry_after > 0:
            return self._backpressure_view(
                workspace_id,
                endpoint_id,
                event_id,
                key,
                attempt,
                retry_after,
            )

        self._consume_quota(endpoint, current_time)
        self._delivery_worker(endpoint, dict(payload))
        return self._record_delivery(
            delivery_key,
            workspace_id,
            endpoint_id,
            event_id,
            key,
            "delivered",
            "accepted",
            attempt,
        ).public_view()

    def get_delivery(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        record = self._deliveries.get(
            (workspace_id, endpoint_id, event_id, idempotency_key)
        )
        return record.public_view() if record else None

    def _retry_after(self, endpoint: WebhookEndpoint, now: float) -> int:
        window_age = now - endpoint._window_start
        if (
            endpoint._window_start == 0.0
            or window_age >= endpoint.window_seconds
        ):
            return 0
        if endpoint._window_count < endpoint.limit_per_window:
            return 0
        return max(1, math.ceil(endpoint.window_seconds - window_age))

    def _consume_quota(self, endpoint: WebhookEndpoint, now: float) -> None:
        if endpoint._window_start == 0.0:
            endpoint._window_start = now
            endpoint._window_count = 0
        if now - endpoint._window_start >= endpoint.window_seconds:
            endpoint._window_start = now
            endpoint._window_count = 0
        endpoint._window_count += 1

    def _backpressure_view(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        idempotency_key: str,
        attempt: int,
        retry_after: int,
    ) -> Dict[str, Any]:
        return {
            "delivery_id": None,
            "workspace_id": workspace_id,
            "endpoint_id": endpoint_id,
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "status": "rejected",
            "reason": "endpoint_rate_limited",
            "attempt": attempt,
            "retry_after": retry_after,
        }

    def _record_delivery(
        self,
        delivery_key: Tuple[str, str, str, str],
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        idempotency_key: str,
        status: str,
        reason: str,
        attempt: int,
        retry_after: int = 0,
    ) -> WebhookDeliveryRecord:
        record = WebhookDeliveryRecord(
            delivery_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            idempotency_key=idempotency_key,
            status=status,
            reason=reason,
            attempt=attempt,
            retry_after=retry_after,
        )
        self._deliveries[delivery_key] = record
        return record

    def _validate_scope(self, workspace_id: str, endpoint_id: str) -> None:
        if not workspace_id:
            raise WebhookDispatchError("workspace_id is required")
        if not endpoint_id:
            raise WebhookDispatchError("endpoint_id is required")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WebhookDispatchError("endpoint url must be absolute http(s)")

    def _endpoint_key(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> Tuple[str, str]:
        return (workspace_id, endpoint_id)

    def _noop_delivery(
        self,
        endpoint: WebhookEndpoint,
        payload: Dict[str, Any],
    ) -> None:
        return None
