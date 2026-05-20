"""Authentication and role checks for API routes."""

import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional

from fastapi import Header, HTTPException


ROLE_RANKS = {
    "viewer": 10,
    "editor": 20,
    "operator": 20,
    "admin": 30,
    "owner": 40,
}


@dataclass(frozen=True)
class Principal:
    """Authenticated caller context used by route-level authorization."""

    subject: str
    workspace_id: str
    role: str
    expires_at: Optional[float] = None
    revoked: bool = False
    disabled: bool = False
    client_type: str = "token"
    scopes: FrozenSet[str] = field(
        default_factory=lambda: frozenset({"templates:clone"}),
    )


class TokenAuthority:
    def __init__(self):
        self._principals: Dict[str, Principal] = {}
        self._sessions: Dict[str, Principal] = {}
        self._audit_records: List[Dict[str, Optional[str]]] = []

    def register(self, token: str, principal: Principal) -> None:
        self._principals[token] = principal

    def register_session(self, session_id: str, principal: Principal) -> None:
        self._sessions[session_id] = Principal(
            subject=principal.subject,
            workspace_id=principal.workspace_id,
            role=principal.role,
            expires_at=principal.expires_at,
            revoked=principal.revoked,
            disabled=principal.disabled,
            client_type="browser",
            scopes=principal.scopes,
        )

    def revoke(self, token: str) -> None:
        principal = self._principals.get(token)
        if principal:
            self._principals[token] = Principal(
                subject=principal.subject,
                workspace_id=principal.workspace_id,
                role=principal.role,
                expires_at=principal.expires_at,
                revoked=True,
                disabled=principal.disabled,
                client_type=principal.client_type,
                scopes=principal.scopes,
            )
        session = self._sessions.get(token)
        if session:
            self._sessions[token] = Principal(
                subject=session.subject,
                workspace_id=session.workspace_id,
                role=session.role,
                expires_at=session.expires_at,
                revoked=True,
                disabled=session.disabled,
                client_type=session.client_type,
                scopes=session.scopes,
            )

    def clear(self) -> None:
        self._principals.clear()
        self._sessions.clear()
        self._audit_records.clear()

    def audit_records(self) -> List[Dict[str, Optional[str]]]:
        return [record.copy() for record in self._audit_records]

    def require_role(
        self,
        authorization: Optional[str],
        session_id: Optional[str],
        workspace_id: str,
        required_role: str,
        required_scope: str,
    ) -> Principal:
        principal = self._authenticate(authorization, session_id)
        if required_scope not in principal.scopes:
            self._record_decision(
                "insufficient_scope",
                principal,
                workspace_id,
            )
            raise HTTPException(status_code=403, detail="Insufficient scope")
        if principal.workspace_id != workspace_id:
            self._record_decision("workspace_denied", principal, workspace_id)
            raise HTTPException(
                status_code=403,
                detail="Workspace access denied",
            )
        if ROLE_RANKS.get(principal.role, 0) < ROLE_RANKS[required_role]:
            self._record_decision("insufficient_role", principal, workspace_id)
            raise HTTPException(status_code=403, detail="Insufficient role")
        self._record_decision("authorized", principal, workspace_id)
        return principal

    def _authenticate(
        self,
        authorization: Optional[str],
        session_id: Optional[str],
    ) -> Principal:
        if authorization:
            if not authorization.startswith("Bearer "):
                self._record_decision("malformed_token", None, None)
                raise HTTPException(
                    status_code=401,
                    detail="Malformed bearer token",
                )

            token = authorization.removeprefix("Bearer ").strip()
            principal = self._principals.get(token)
            client_detail = "bearer token"
        elif session_id:
            principal = self._sessions.get(session_id.strip())
            client_detail = "browser session"
        else:
            self._record_decision("missing_credential", None, None)
            raise HTTPException(
                status_code=401,
                detail="Missing bearer token or browser session",
            )

        if not principal:
            self._record_decision("invalid_credential", None, None)
            raise HTTPException(
                status_code=401,
                detail=f"Invalid {client_detail}",
            )
        if principal.revoked:
            self._record_decision(
                "revoked",
                principal,
                principal.workspace_id,
            )
            raise HTTPException(
                status_code=401,
                detail=f"Revoked {client_detail}",
            )
        if principal.disabled:
            self._record_decision(
                "disabled",
                principal,
                principal.workspace_id,
            )
            raise HTTPException(
                status_code=401,
                detail=f"Disabled {client_detail}",
            )
        if (
            principal.expires_at is not None
            and principal.expires_at <= time.time()
        ):
            self._record_decision("stale", principal, principal.workspace_id)
            raise HTTPException(
                status_code=401,
                detail=f"Stale {client_detail}",
            )
        return principal

    def _record_decision(
        self,
        reason: str,
        principal: Optional[Principal],
        requested_workspace_id: Optional[str],
    ) -> None:
        self._audit_records.append({
            "reason": reason,
            "subject": principal.subject if principal else None,
            "workspace_id": (
                principal.workspace_id if principal else requested_workspace_id
            ),
            "requested_workspace_id": requested_workspace_id,
            "role": principal.role if principal else None,
            "client_type": principal.client_type if principal else None,
        })


token_authority = TokenAuthority()


def require_template_clone_principal(
    workspace_id: str,
    authorization: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(
        default=None,
        alias="X-Session-Id",
    ),
    x_integration_session: Optional[str] = Header(
        default=None,
        alias="X-Integration-Session",
    ),
) -> Principal:
    return token_authority.require_role(
        authorization,
        session_id=x_session_id or x_integration_session,
        workspace_id=workspace_id,
        required_role="operator",
        required_scope="templates:clone",
    )
