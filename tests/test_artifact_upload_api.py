import pytest
from fastapi.testclient import TestClient

from src.api.artifacts import ArtifactIngestionService, ArtifactStore
from src.api import routes
from src.api.server import create_app


class RecordingArtifactStore(ArtifactStore):
    def __init__(self):
        super().__init__()
        self.writes = []

    def put(self, workspace_id, artifact_id, content, content_type):
        self.writes.append((workspace_id, artifact_id, content, content_type))
        return super().put(workspace_id, artifact_id, content, content_type)


def _client_with_store(max_body_size=8):
    store = RecordingArtifactStore()
    routes.artifact_service = ArtifactIngestionService(
        store=store,
        max_body_size=max_body_size,
    )
    return TestClient(create_app()), store


def test_authorized_artifact_upload_stores_payload():
    client, store = _client_with_store(max_body_size=8)

    response = client.post(
        "/api/v2/workspaces/ws-1/artifacts/artifact-1/upload",
        content=b"payload",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/octet-stream",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "stored",
        "workspace_id": "ws-1",
        "artifact_id": "artifact-1",
        "size": 7,
        "content_type": "application/octet-stream",
    }
    assert store.writes == [
        ("ws-1", "artifact-1", b"payload", "application/octet-stream")
    ]


def test_authorized_default_workspace_artifact_upload_stores_payload():
    client, store = _client_with_store(max_body_size=8)

    response = client.post(
        "/api/v2/artifacts/artifact-1/upload",
        content=b"payload",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/octet-stream",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "stored",
        "workspace_id": "default",
        "artifact_id": "artifact-1",
        "size": 7,
        "content_type": "application/octet-stream",
    }
    assert store.writes == [
        ("default", "artifact-1", b"payload", "application/octet-stream")
    ]


def test_unauthorized_artifact_upload_does_not_mutate_store():
    client, store = _client_with_store(max_body_size=8)

    response = client.post(
        "/api/v2/workspaces/ws-1/artifacts/artifact-1/upload",
        content=b"payload",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 401
    assert store.writes == []


def test_malformed_artifact_upload_does_not_mutate_store():
    client, store = _client_with_store(max_body_size=8)

    response = client.post(
        "/api/v2/workspaces/ws-1/artifacts/bad..artifact/upload",
        content=b"payload",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/octet-stream",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid artifact_id"}
    assert store.writes == []


def test_oversized_artifact_upload_fails_before_mutation():
    client, store = _client_with_store(max_body_size=4)

    response = client.post(
        "/api/v2/workspaces/ws-1/artifacts/artifact-1/upload",
        content=b"payload",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/octet-stream",
        },
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Artifact upload exceeds 4 bytes"}
    assert store.writes == []


def test_declared_oversized_upload_fails_before_reading_stream():
    store = RecordingArtifactStore()
    service = ArtifactIngestionService(store=store, max_body_size=4)

    async def unread_body_stream():
        raise AssertionError("oversized declared length should fail first")
        yield b"payload"

    with pytest.raises(Exception) as exc_info:
        import asyncio

        asyncio.run(
            service.ingest(
                workspace_id="ws-1",
                artifact_id="artifact-1",
                content_type="application/octet-stream",
                content_length="5",
                body_stream=unread_body_stream(),
            )
        )

    assert exc_info.value.status_code == 413
    assert store.writes == []
