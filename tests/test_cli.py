import argparse

from src.cli.main import ERROR, SUCCESS, USAGE_ERROR, cli, handle_deploy


class FailingDeployClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def deploy_agent(self, manifest):
        name = manifest["name"]
        raise ConnectionError(f"orchestrator unavailable for {name}")


class RejectedDeployClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def deploy_agent(self, manifest):
        return {"error": 422, "message": f"invalid agent {manifest['name']}"}


class SuccessfulDeployClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def deploy_agent(self, manifest):
        return {"id": f"deploy-{manifest['name']}"}


def test_deploy_handler_returns_nonzero_when_backend_fails(capsys):
    args = argparse.Namespace(manifest="agent.yaml")

    def failing_backend(manifest):
        raise ConnectionError(f"orchestrator unavailable for {manifest}")

    exit_code = handle_deploy(args, deploy_backend=failing_backend)

    captured = capsys.readouterr()
    assert exit_code == ERROR
    assert (
        "Deploy failed: orchestrator unavailable for agent.yaml"
        in captured.err
    )


def test_deploy_cli_returns_nonzero_when_backend_is_unreachable(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "agent.json"
    manifest.write_text('{"name": "worker"}')

    exit_code = cli(
        ["deploy", str(manifest)],
        client_factory=FailingDeployClient,
    )

    captured = capsys.readouterr()
    assert exit_code == ERROR
    assert (
        "Deploy failed: orchestrator unavailable for worker"
        in captured.err
    )


def test_deploy_cli_returns_nonzero_when_backend_rejects_manifest(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "agent.json"
    manifest.write_text('{"name": "worker"}')

    exit_code = cli(
        ["deploy", str(manifest)],
        client_factory=RejectedDeployClient,
    )

    captured = capsys.readouterr()
    assert exit_code == ERROR
    assert (
        "Deploy failed: orchestrator rejected deployment: invalid agent worker"
        in captured.err
    )


def test_deploy_cli_success_returns_zero_for_yaml_manifest(tmp_path, capsys):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text("name: worker\n")

    exit_code = cli(
        ["deploy", str(manifest)],
        client_factory=SuccessfulDeployClient,
    )

    captured = capsys.readouterr()
    assert exit_code == SUCCESS
    assert "Deploy succeeded: deploy-worker" in captured.out


def test_deploy_cli_returns_usage_error_for_missing_manifest(capsys):
    exit_code = cli(
        ["deploy", "missing.json"],
        client_factory=SuccessfulDeployClient,
    )

    captured = capsys.readouterr()
    assert exit_code == USAGE_ERROR
    assert "Deploy failed: could not read manifest" in captured.err
