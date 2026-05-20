import time

from fastapi.testclient import TestClient

from src.api.auth import Principal, token_authority
from src.api.routes import template_store
from src.api.server import create_app


class TestTemplateApi:
    def setup_method(self):
        token_authority.clear()
        template_store.reset()
        template_store.add_template(
            "template-1",
            workspace_id="workspace-a",
            name="Build agent",
            config={"model": "safe-default"},
        )
        self.client = TestClient(create_app())

    def test_clone_template_rejects_anonymous_before_store_mutation(self):
        response = self.client.post(
            "/api/v2/workspaces/workspace-a/agent-templates/template-1/clone",
            json={"name": "clone"},
        )

        assert response.status_code == 401
        assert template_store.clone_attempts == 0

    def test_clone_template_rejects_stale_token_before_store_mutation(self):
        token_authority.register(
            "stale-token",
            Principal(
                subject="user-1",
                workspace_id="workspace-a",
                role="operator",
                expires_at=time.time() - 1,
            ),
        )

        response = self.client.post(
            "/api/v2/workspaces/workspace-a/agent-templates/template-1/clone",
            headers={"Authorization": "Bearer stale-token"},
            json={"name": "clone"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Stale bearer token"
        assert template_store.clone_attempts == 0

    def test_clone_template_rejects_revoked_token_before_store_mutation(self):
        token_authority.register(
            "revoked-token",
            Principal(
                subject="user-1",
                workspace_id="workspace-a",
                role="operator",
            ),
        )
        token_authority.revoke("revoked-token")

        response = self.client.post(
            "/api/v2/workspaces/workspace-a/agent-templates/template-1/clone",
            headers={"Authorization": "Bearer revoked-token"},
            json={"name": "clone"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Revoked bearer token"
        assert template_store.clone_attempts == 0

    def test_clone_template_rejects_insufficient_role_before_store_mutation(
        self,
    ):
        token_authority.register(
            "viewer-token",
            Principal(
                subject="user-1",
                workspace_id="workspace-a",
                role="viewer",
            ),
        )

        response = self.client.post(
            "/api/v2/workspaces/workspace-a/agent-templates/template-1/clone",
            headers={"Authorization": "Bearer viewer-token"},
            json={"name": "clone"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient role"
        assert template_store.clone_attempts == 0

    def test_clone_template_rejects_wrong_workspace_before_store_mutation(
        self,
    ):
        token_authority.register(
            "operator-token",
            Principal(
                subject="user-1",
                workspace_id="workspace-b",
                role="operator",
            ),
        )

        response = self.client.post(
            "/api/v2/workspaces/workspace-a/agent-templates/template-1/clone",
            headers={"Authorization": "Bearer operator-token"},
            json={"name": "clone"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Workspace access denied"
        assert template_store.clone_attempts == 0

    def test_clone_template_allows_operator_for_same_workspace(self):
        token_authority.register(
            "operator-token",
            Principal(
                subject="user-1",
                workspace_id="workspace-a",
                role="operator",
            ),
        )

        response = self.client.post(
            "/api/v2/workspaces/workspace-a/agent-templates/template-1/clone",
            headers={"Authorization": "Bearer operator-token"},
            json={"name": "clone"},
        )

        assert response.status_code == 201
        assert response.json()["template"] == {
            "id": response.json()["template"]["id"],
            "source_template_id": "template-1",
            "workspace_id": "workspace-a",
            "name": "clone",
            "created_by": "user-1",
            "config": {"model": "safe-default"},
        }
        assert template_store.clone_attempts == 1
