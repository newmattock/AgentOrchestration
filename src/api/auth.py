"""Operator token authorization helpers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Set

from fastapi import Depends, HTTPException, Request


RUN_CANCEL_SCOPE = "runs:cancel"
RUN_CANCEL_ROLES = frozenset({"admin", "operator"})
SESSION_COOKIE = "ao_session"


@dataclass(frozen=True)
class OperatorPrincipal:
    subject: str
    workspace_id: str
    role: str
    scopes: Set[str] = field(default_factory=set)
    expires_at: Optional[datetime] = None
    revoked: bool = False

    def is_fresh(self, now: Optional[datetime] = None) -> bool:
        if self.revoked:
            return False
        if self.expires_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return self.expires_at > now


class OperatorTokenStore:
    def __init__(self):
        self._tokens: Dict[str, OperatorPrincipal] = {}

    def clear(self) -> None:
        self._tokens.clear()

    def add(
        self,
        token: str,
        *,
        subject: str,
        workspace_id: str,
        role: str,
        scopes: Iterable[str],
        expires_at: Optional[datetime] = None,
        revoked: bool = False,
    ) -> None:
        self._tokens[token] = OperatorPrincipal(
            subject=subject,
            workspace_id=workspace_id,
            role=role,
            scopes=set(scopes),
            expires_at=expires_at,
            revoked=revoked,
        )

    def resolve(self, token: str) -> Optional[OperatorPrincipal]:
        return self._tokens.get(token)


class OperatorAuthService:
    def __init__(self, token_store: OperatorTokenStore):
        self._token_store = token_store

    def authenticate(self, request: Request) -> OperatorPrincipal:
        token = _extract_token(request)
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing operator token",
            )

        principal = self._token_store.resolve(token)
        if not principal or not principal.is_fresh():
            raise HTTPException(
                status_code=401,
                detail="Invalid operator token",
            )
        return principal

    def require_run_cancel(
        self,
        request: Request,
        workspace_id: str,
    ) -> OperatorPrincipal:
        principal = self.authenticate(request)
        if principal.workspace_id != workspace_id:
            raise HTTPException(
                status_code=403,
                detail="Workspace scope mismatch",
            )
        if principal.role not in RUN_CANCEL_ROLES:
            raise HTTPException(status_code=403, detail="Insufficient role")
        if RUN_CANCEL_SCOPE not in principal.scopes:
            raise HTTPException(
                status_code=403,
                detail="Missing run cancel scope",
            )
        return principal


operator_tokens = OperatorTokenStore()
operator_auth = OperatorAuthService(operator_tokens)


def _extract_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return request.cookies.get(SESSION_COOKIE)


def get_operator_auth_service() -> OperatorAuthService:
    return operator_auth


def require_run_cancel_principal(
    request: Request,
    workspace_id: str,
    auth_service: OperatorAuthService = Depends(get_operator_auth_service),
) -> OperatorPrincipal:
    return auth_service.require_run_cancel(request, workspace_id)
