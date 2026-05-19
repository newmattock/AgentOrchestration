from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.api.auth import RUN_CANCEL_SCOPE, SESSION_COOKIE, operator_tokens
from src.api.run_cancellation import run_cancellations
from src.api.server import create_app


def setup_function():
    operator_tokens.clear()
    run_cancellations.clear()


def client():
    return TestClient(create_app())


def add_operator_token(
    token,
    *,
    workspace_id="workspace-a",
    role="operator",
    scopes=(RUN_CANCEL_SCOPE,),
    expires_at=None,
    revoked=False,
):
    operator_tokens.add(
        token,
        subject=f"user-{token}",
        workspace_id=workspace_id,
        role=role,
        scopes=scopes,
        expires_at=expires_at,
        revoked=revoked,
    )


def cancel_run(api, token="operator-token", workspace_id="workspace-a"):
    return api.post(
        f"/api/v2/workspaces/{workspace_id}/runs/run-1/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )


class TestRunCancellationAuthorization:
    def test_anonymous_principal_is_denied(self):
        run_cancellations.add_run("run-1", "workspace-a")

        response = client().post(
            "/api/v2/workspaces/workspace-a/runs/run-1/cancel",
        )

        assert response.status_code == 401
        assert run_cancellations.get_cancellation("run-1") is None

    def test_stale_operator_token_is_denied(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token(
            "expired-token",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        response = cancel_run(client(), token="expired-token")

        assert response.status_code == 401
        assert run_cancellations.get_cancellation("run-1") is None

    def test_revoked_operator_token_is_denied(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token("revoked-token", revoked=True)

        response = cancel_run(client(), token="revoked-token")

        assert response.status_code == 401
        assert run_cancellations.get_cancellation("run-1") is None

    def test_token_without_cancel_scope_is_denied(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token("read-only-token", scopes=("runs:read",))

        response = cancel_run(client(), token="read-only-token")

        assert response.status_code == 403
        assert response.json()["detail"] == "Missing run cancel scope"
        assert run_cancellations.get_cancellation("run-1") is None

    def test_token_without_operator_role_is_denied(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token("viewer-token", role="viewer")

        response = cancel_run(client(), token="viewer-token")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient role"
        assert run_cancellations.get_cancellation("run-1") is None

    def test_cross_workspace_operator_token_is_denied(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token("workspace-b-token", workspace_id="workspace-b")

        response = cancel_run(client(), token="workspace-b-token")

        assert response.status_code == 403
        assert response.json()["detail"] == "Workspace scope mismatch"
        assert run_cancellations.get_cancellation("run-1") is None

    def test_authorized_operator_token_can_cancel_run(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token("operator-token")

        response = cancel_run(client())

        assert response.status_code == 200
        assert response.json() == {
            "status": "cancelled",
            "run_id": "run-1",
            "workspace_id": "workspace-a",
            "cancelled_by": "user-operator-token",
        }

    def test_browser_session_with_cancel_scope_can_cancel_run(self):
        run_cancellations.add_run("run-1", "workspace-a")
        add_operator_token("browser-session")
        api = client()
        api.cookies.set(SESSION_COOKIE, "browser-session")

        response = api.post("/api/v2/workspaces/workspace-a/runs/run-1/cancel")

        assert response.status_code == 200
        assert response.json()["cancelled_by"] == "user-browser-session"
