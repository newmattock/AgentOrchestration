"""Shared service for optimistic agent config updates."""

from typing import Any, Dict, Optional


class AgentConfigUpdateError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def format_config_etag(version: int) -> str:
    return f'"{version}"'


def parse_config_etag(value: str) -> int:
    etag = value.strip()
    if len(etag) < 3 or not etag.startswith('"') or not etag.endswith('"'):
        raise AgentConfigUpdateError(
            400,
            "If-Match must be a quoted config version",
        )

    version = etag[1:-1]
    if not version.isdigit():
        raise AgentConfigUpdateError(
            400,
            "If-Match must be a quoted config version",
        )
    return int(version)


def update_agent_config(
    registry: Any,
    agent_id: str,
    payload: Dict[str, Any],
    if_match: Optional[str],
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise AgentConfigUpdateError(400, "request body must be an object")
    if "config" not in payload:
        raise AgentConfigUpdateError(400, "config is required")
    if not isinstance(payload["config"], dict):
        raise AgentConfigUpdateError(400, "config must be an object")
    if if_match is None or not if_match.strip():
        raise AgentConfigUpdateError(428, "If-Match header is required")

    expected_version = parse_config_etag(if_match)
    if not _is_valid_agent_id(agent_id):
        raise AgentConfigUpdateError(400, "invalid agent_id")

    result = registry.update_config_if_version(
        agent_id,
        payload["config"],
        expected_version,
    )
    if result is None:
        raise AgentConfigUpdateError(404, "Agent not found")
    if not result["updated"]:
        raise AgentConfigUpdateError(412, "Stale agent config ETag")

    return {
        "agent_id": agent_id,
        "config": dict(result["agent"]["config"]),
        "etag": format_config_etag(result["current_version"]),
    }


def _is_valid_agent_id(agent_id: str) -> bool:
    return bool(
        isinstance(agent_id, str)
        and agent_id
        and ".." not in agent_id
        and "/" not in agent_id
        and "\\" not in agent_id
    )
