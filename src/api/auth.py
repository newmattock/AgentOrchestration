"""Integration authentication and workspace authorization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping

from starlette.requests import Request


class IntegrationAuthError(Exception):
    """Raised when integration authentication or authorization fails."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class CredentialRecord:
    principal_id: str
    workspace_roles: Mapping[str, str] = field(default_factory=dict)
    scopes: frozenset[str] = field(default_factory=frozenset)
    disabled: bool = False
    revoked: bool = False
    stale: bool = False
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= current


@dataclass(frozen=True)
class AuthPrincipal:
    principal_id: str
    credential_type: str
    workspace_roles: Mapping[str, str]
    scopes: frozenset[str]


class IntegrationAuthService:
    def __init__(self, allow_legacy_bearer: bool = True):
        self.allow_legacy_bearer = allow_legacy_bearer
        self._bearer_tokens: dict[str, CredentialRecord] = {}
        self._session_ids: dict[str, CredentialRecord] = {}

    def register_token(self, token: str, record: CredentialRecord) -> None:
        self._bearer_tokens[token] = record

    def register_session(
        self,
        session_id: str,
        record: CredentialRecord,
    ) -> None:
        self._session_ids[session_id] = record

    def authenticate(self, request: Request) -> AuthPrincipal:
        cached = getattr(request.state, "auth_principal", None)
        if cached is not None:
            return cached

        credential_type, credential = self._extract_credential(request)
        if credential_type == "bearer":
            record = self._bearer_tokens.get(credential)
            if record is None and self.allow_legacy_bearer:
                principal = AuthPrincipal(
                    principal_id="legacy-token",
                    credential_type="bearer",
                    workspace_roles={},
                    scopes=frozenset(),
                )
                request.state.auth_principal = principal
                return principal
        else:
            record = self._session_ids.get(credential)

        if record is None:
            raise IntegrationAuthError(401, "Invalid integration credential")
        self._validate_record(credential_type, credential, record)
        principal = AuthPrincipal(
            principal_id=record.principal_id,
            credential_type=credential_type,
            workspace_roles=dict(record.workspace_roles),
            scopes=frozenset(record.scopes),
        )
        request.state.auth_principal = principal
        return principal

    def require_workspace_permission(
        self,
        request: Request,
        workspace_id: str,
        required_scope: str,
        allowed_roles: Iterable[str],
    ) -> AuthPrincipal:
        principal = self.authenticate(request)
        if required_scope not in principal.scopes:
            raise IntegrationAuthError(
                403,
                "Missing required integration scope",
            )
        role = principal.workspace_roles.get(workspace_id)
        if role not in set(allowed_roles):
            raise IntegrationAuthError(403, "Insufficient workspace role")
        return principal

    def _extract_credential(self, request: Request) -> tuple[str, str]:
        authorization = request.headers.get("Authorization")
        if authorization:
            scheme, separator, token = authorization.partition(" ")
            if (
                scheme.lower() != "bearer"
                or not separator
                or not token.strip()
            ):
                raise IntegrationAuthError(
                    401,
                    "Malformed authorization header",
                )
            return "bearer", token.strip()

        integration_session = request.headers.get("x-integration-session")
        if integration_session is not None:
            if not integration_session.strip():
                raise IntegrationAuthError(
                    401,
                    "Malformed integration session header",
                )
            return "session", integration_session.strip()

        session_id = request.cookies.get("ao_session")
        if session_id:
            return "session", session_id

        raise IntegrationAuthError(401, "Missing integration credential")

    def _validate_record(
        self,
        credential_type: str,
        credential: str,
        record: CredentialRecord,
    ) -> None:
        if record.stale:
            self._invalidate_credential(credential_type, credential)
            raise IntegrationAuthError(401, "Credential is stale")
        if record.disabled:
            raise IntegrationAuthError(401, "Principal is disabled")
        if record.revoked:
            raise IntegrationAuthError(401, "Credential has been revoked")
        if record.is_expired():
            raise IntegrationAuthError(401, "Credential has expired")

    def _invalidate_credential(
        self,
        credential_type: str,
        credential: str,
    ) -> None:
        if credential_type == "bearer":
            self._bearer_tokens.pop(credential, None)
        else:
            self._session_ids.pop(credential, None)


def get_integration_auth_service(request: Request) -> IntegrationAuthService:
    service = getattr(request.app.state, "integration_auth_service", None)
    if service is None:
        service = IntegrationAuthService()
        request.app.state.integration_auth_service = service
    return service
