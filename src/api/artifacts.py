"""Artifact ingestion service helpers."""

import os
import re
from typing import AsyncIterator, Dict, Optional

from fastapi import HTTPException, status


DEFAULT_MAX_ARTIFACT_UPLOAD_BYTES = 10 * 1024 * 1024
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ArtifactStore:
    def __init__(self):
        self._artifacts: Dict[str, Dict[str, object]] = {}

    def put(
        self,
        workspace_id: str,
        artifact_id: str,
        content: bytes,
        content_type: str,
    ) -> Dict[str, object]:
        key = f"{workspace_id}:{artifact_id}"
        record = {
            "workspace_id": workspace_id,
            "artifact_id": artifact_id,
            "size": len(content),
            "content_type": content_type,
        }
        self._artifacts[key] = {**record, "content": content}
        return record

    def get(
        self,
        workspace_id: str,
        artifact_id: str,
    ) -> Optional[Dict[str, object]]:
        return self._artifacts.get(f"{workspace_id}:{artifact_id}")


class ArtifactIngestionService:
    def __init__(
        self,
        store: Optional[ArtifactStore] = None,
        max_body_size: Optional[int] = None,
    ):
        self.store = store or ArtifactStore()
        self.max_body_size = (
            max_body_size
            if max_body_size is not None
            else _configured_max_body_size()
        )

    async def ingest(
        self,
        *,
        workspace_id: str,
        artifact_id: str,
        content_type: str,
        content_length: Optional[str],
        body_stream: AsyncIterator[bytes],
    ) -> Dict[str, object]:
        _validate_identifier("workspace_id", workspace_id)
        _validate_identifier("artifact_id", artifact_id)
        declared_size = _parse_content_length(content_length)
        if declared_size is not None and declared_size > self.max_body_size:
            raise HTTPException(
                status_code=413,
                detail=f"Artifact upload exceeds {self.max_body_size} bytes",
            )

        content = await _read_limited_body(body_stream, self.max_body_size)
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Artifact body is required",
            )

        return self.store.put(
            workspace_id,
            artifact_id,
            bytes(content),
            content_type,
        )


def _configured_max_body_size() -> int:
    raw = os.getenv("AO_ARTIFACT_MAX_UPLOAD_BYTES")
    if raw is None:
        return DEFAULT_MAX_ARTIFACT_UPLOAD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ARTIFACT_UPLOAD_BYTES
    return value if value > 0 else DEFAULT_MAX_ARTIFACT_UPLOAD_BYTES


def _validate_identifier(name: str, value: str) -> None:
    if not _IDENTIFIER_PATTERN.match(value) or ".." in value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {name}",
        )


def _parse_content_length(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Content-Length",
        ) from exc
    if length < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Content-Length",
        )
    return length


async def _read_limited_body(
    body_stream: AsyncIterator[bytes],
    max_body_size: int,
) -> bytes:
    content = bytearray()
    async for chunk in body_stream:
        content.extend(chunk)
        if len(content) > max_body_size:
            raise HTTPException(
                status_code=413,
                detail=f"Artifact upload exceeds {max_body_size} bytes",
            )
    return bytes(content)
