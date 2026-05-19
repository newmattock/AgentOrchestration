"""Agent Registry — Manages agent lifecycle and metadata."""

import hashlib
import time
import uuid
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class RegistryAuthorizationError(PermissionError):
    """Raised when an agent resolution fails the current permission policy."""


DEFAULT_PRINCIPAL = "__agent__"


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        self._auth_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._audit_log: List[Dict[str, Any]] = []

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
    ) -> str:
        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        agent_config = dict(config or {})
        permissions = self._normalize_permissions(
            agent_config.get("permissions", [])
        )
        agent_config["permissions"] = sorted(permissions)
        authorization_policy = self._normalize_authorization_policy(
            agent_config.get("authorization")
        )
        if DEFAULT_PRINCIPAL not in authorization_policy:
            authorization_policy[DEFAULT_PRINCIPAL] = set(permissions)
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": agent_config,
            "authorization_policy": authorization_policy,
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "auth_version": 1,
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        self._record_auth_audit(
            "registered",
            agent_id,
            decision="allow",
            reason="permissions_validated",
        )
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def list(
        self,
        status: Optional[AgentStatus] = None,
        group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        return list(agents)

    def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        if agent_id not in self._agents:
            return False
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        self.invalidate_authorization_cache(agent_id=agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)

    def update_permissions(
        self,
        agent_id: str,
        permissions: Iterable[str],
    ) -> bool:
        if agent_id not in self._agents:
            return False
        normalized = self._normalize_permissions(permissions)
        agent = self._agents[agent_id]
        agent["config"]["permissions"] = sorted(normalized)
        agent["authorization_policy"][DEFAULT_PRINCIPAL] = set(normalized)
        agent["auth_version"] += 1
        agent["updated_at"] = time.time()
        invalidated = self.invalidate_authorization_cache(agent_id=agent_id)
        self._record_auth_audit(
            "permissions_updated",
            agent_id,
            decision="allow",
            reason="cache_invalidated",
            invalidated=invalidated,
        )
        return True

    def update_authorization_policy(
        self,
        agent_id: str,
        policy: Dict[str, Iterable[str]],
    ) -> bool:
        if agent_id not in self._agents:
            return False
        normalized = self._normalize_authorization_policy(policy)
        agent = self._agents[agent_id]
        agent["authorization_policy"] = normalized
        agent["config"]["authorization"] = {
            principal: sorted(permissions)
            for principal, permissions in normalized.items()
        }
        agent["auth_version"] += 1
        agent["updated_at"] = time.time()
        invalidated = self.invalidate_authorization_cache(agent_id=agent_id)
        self._record_auth_audit(
            "authorization_policy_updated",
            agent_id,
            decision="allow",
            reason="cache_invalidated",
            invalidated=invalidated,
        )
        return True

    def resolve_authorized(
        self,
        agent_id: str,
        permission: str,
        principal_id: str = DEFAULT_PRINCIPAL,
    ) -> Dict[str, Any]:
        permission = self._normalize_permission(permission)
        principal_id = self._normalize_principal(principal_id)
        agent = self._agents.get(agent_id)
        if agent is None:
            self._record_auth_audit(
                "resolve",
                agent_id,
                permission=permission,
                principal_id=principal_id,
                decision="deny",
                reason="agent_missing",
            )
            raise RegistryAuthorizationError("agent is not registered")

        cache_key = (agent_id, principal_id, permission)
        cached = self._auth_cache.get(cache_key)
        if cached and cached["auth_version"] == agent["auth_version"]:
            if self._agent_allows(agent, principal_id, permission):
                self._record_auth_audit(
                    "resolve",
                    agent_id,
                    permission=permission,
                    principal_id=principal_id,
                    decision="allow",
                    reason="cache_hit",
                )
                return agent
            self._auth_cache.pop(cache_key, None)
        elif cached:
            self._auth_cache.pop(cache_key, None)
            self._record_auth_audit(
                "resolve",
                agent_id,
                permission=permission,
                principal_id=principal_id,
                decision="defer",
                reason="permission_changed",
            )

        if not self._agent_allows(agent, principal_id, permission):
            self._record_auth_audit(
                "resolve",
                agent_id,
                permission=permission,
                principal_id=principal_id,
                decision="deny",
                reason="permission_missing",
            )
            raise RegistryAuthorizationError(
                f"agent {agent_id} is not authorized for {permission}"
            )

        self._auth_cache[cache_key] = {
            "agent_id": agent_id,
            "principal_hash": self._principal_hash(principal_id),
            "permission": permission,
            "auth_version": agent["auth_version"],
        }
        self._record_auth_audit(
            "resolve",
            agent_id,
            permission=permission,
            principal_id=principal_id,
            decision="allow",
            reason="policy_check",
        )
        return agent

    def invalidate_authorization_cache(
        self,
        agent_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        permission: Optional[str] = None,
    ) -> int:
        normalized_principal = None
        if principal_id is not None:
            normalized_principal = self._normalize_principal(principal_id)
        normalized_permission = None
        if permission is not None:
            normalized_permission = self._normalize_permission(permission)
        keys = [
            key for key in self._auth_cache
            if (agent_id is None or key[0] == agent_id)
            and (
                normalized_principal is None
                or key[1] == normalized_principal
            )
            and (
                normalized_permission is None
                or key[2] == normalized_permission
            )
        ]
        for key in keys:
            self._auth_cache.pop(key, None)
        return len(keys)

    def authorization_cache_size(self) -> int:
        return len(self._auth_cache)

    def authorization_audit_log(self) -> List[Dict[str, Any]]:
        return [dict(entry) for entry in self._audit_log]

    def _agent_allows(
        self,
        agent: Dict[str, Any],
        principal_id: str,
        permission: str,
    ) -> bool:
        policy = agent.get("authorization_policy", {})
        permissions = set(policy.get(principal_id, set()))
        return "*" in permissions or permission in permissions

    def _normalize_authorization_policy(
        self,
        policy: Optional[Dict[str, Iterable[str]]],
    ) -> Dict[str, Set[str]]:
        if policy is None:
            return {}
        if not isinstance(policy, dict):
            raise ValueError("authorization policy must be a mapping")
        return {
            self._normalize_principal(principal): self._normalize_permissions(
                permissions
            )
            for principal, permissions in policy.items()
        }

    def _normalize_permissions(self, permissions: Iterable[str]) -> Set[str]:
        if isinstance(permissions, str):
            raise ValueError(
                "permissions must be an iterable of permission strings"
            )
        return {
            self._normalize_permission(permission)
            for permission in permissions
        }

    def _normalize_permission(self, permission: str) -> str:
        if not isinstance(permission, str) or not permission.strip():
            raise ValueError("permission must be a non-empty string")
        return permission.strip()

    def _normalize_principal(self, principal_id: str) -> str:
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("principal_id must be a non-empty string")
        return principal_id.strip()

    def _principal_hash(self, principal_id: str) -> str:
        return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:12]

    def _record_auth_audit(
        self,
        event: str,
        agent_id: str,
        *,
        decision: str,
        reason: str,
        permission: Optional[str] = None,
        principal_id: Optional[str] = None,
        invalidated: Optional[int] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "event": event,
            "agent_id": agent_id,
            "decision": decision,
            "reason": reason,
            "timestamp": time.time(),
        }
        if permission is not None:
            entry["permission"] = permission
        if principal_id is not None:
            entry["principal_hash"] = self._principal_hash(principal_id)
        if invalidated is not None:
            entry["invalidated"] = invalidated
        self._audit_log.append(entry)

# 2019-01-29T11:24:49 update

# 2019-04-09T13:38:38 update

# 2019-04-11T11:24:12 update

# 2019-06-26T17:03:48 update

# 2019-07-03T14:55:48 update

# 2019-07-18T18:18:47 update

# 2019-11-05T11:27:19 update

# 2019-11-20T11:35:05 update

# 2019-11-23T15:28:54 update

# 2020-03-13T09:23:07 update

# 2020-03-30T19:31:18 update

# 2020-04-22T15:03:30 update

# 2020-07-21T10:00:48 update

# 2020-09-10T09:02:08 update

# 2020-09-10T13:39:12 update

# 2020-09-22T16:27:52 update

# 2020-10-15T10:33:14 update

# 2021-05-13T11:15:56 update

# 2021-07-07T14:57:13 update

# 2021-07-13T15:15:19 update

# 2021-07-27T10:18:16 update

# 2022-03-11T15:24:11 update

# 2022-09-22T13:24:20 update

# 2022-11-01T12:20:40 update

# 2023-01-30T12:32:27 update

# 2023-03-10T09:43:50 update

# 2023-05-10T14:28:01 update

# 2023-05-11T20:04:46 update

# 2023-05-30T17:00:59 update

# 2023-07-13T17:54:32 update

# 2023-07-20T19:04:20 update

# 2023-07-31T17:00:02 update

# 2023-09-05T19:42:07 update

# 2024-01-02T10:29:47 update

# 2024-09-17T12:45:29 update

# 2024-09-17T11:51:01 update

# 2024-11-06T18:20:15 update

# 2025-01-12T15:13:14 update

# 2025-01-14T20:24:39 update

# 2025-03-26T20:21:27 update

# 2025-04-10T18:27:06 update

# 2025-06-19T20:34:58 update

# 2025-06-21T20:23:53 update

# 2025-06-24T20:30:30 update

# 2025-07-03T13:28:03 update

# 2025-07-24T17:42:21 update

# 2025-08-19T17:42:23 update

# 2025-08-21T11:06:52 update

# 2025-10-24T09:10:08 update

# 2025-12-18T19:34:38 update

# 2026-02-06T11:22:22 update

# 2026-02-13T15:42:04 update

# 2026-04-10T08:16:30 update

# 2026-04-29T18:16:11 update
