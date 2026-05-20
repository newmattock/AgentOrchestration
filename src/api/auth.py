"""Authentication and role checks for API routes."""

import time
from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import Header, HTTPException


ROLE_RANKS = {
    "viewer": 10,
    "operator": 20,
    "admin": 30,
}


@dataclass(frozen=True)
class Principal:
    """Authenticated caller context used by route-level authorization."""

    subject: str
    workspace_id: str
    role: str
    expires_at: Optional[float] = None
    revoked: bool = False


class TokenAuthority:
    def __init__(self):
        self._principals: Dict[str, Principal] = {}

    def register(self, token: str, principal: Principal) -> None:
        self._principals[token] = principal

    def revoke(self, token: str) -> None:
        principal = self._principals.get(token)
        if principal:
            self._principals[token] = Principal(
                subject=principal.subject,
                workspace_id=principal.workspace_id,
                role=principal.role,
                expires_at=principal.expires_at,
                revoked=True,
            )

    def clear(self) -> None:
        self._principals.clear()

    def require_role(
        self,
        authorization: Optional[str],
        workspace_id: str,
        required_role: str,
    ) -> Principal:
        principal = self._authenticate(authorization)
        if principal.workspace_id != workspace_id:
            raise HTTPException(
                status_code=403,
                detail="Workspace access denied",
            )
        if ROLE_RANKS.get(principal.role, 0) < ROLE_RANKS[required_role]:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return principal

    def _authenticate(self, authorization: Optional[str]) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")

        token = authorization.removeprefix("Bearer ").strip()
        principal = self._principals.get(token)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        if principal.revoked:
            raise HTTPException(status_code=401, detail="Revoked bearer token")
        if (
            principal.expires_at is not None
            and principal.expires_at <= time.time()
        ):
            raise HTTPException(status_code=401, detail="Stale bearer token")
        return principal


token_authority = TokenAuthority()


def require_template_clone_principal(
    workspace_id: str,
    authorization: Optional[str] = Header(default=None),
) -> Principal:
    return token_authority.require_role(
        authorization,
        workspace_id=workspace_id,
        required_role="operator",
    )
