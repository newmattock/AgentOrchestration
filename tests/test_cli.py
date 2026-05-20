import argparse

from src.cli.main import cli, handle_deploy


def test_deploy_handler_returns_nonzero_when_backend_fails(capsys):
    args = argparse.Namespace(manifest="agent.yaml")

    def failing_backend(manifest):
        raise ConnectionError(f"orchestrator unavailable for {manifest}")

    exit_code = handle_deploy(args, deploy_backend=failing_backend)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        "Deploy failed: orchestrator unavailable for agent.yaml"
        in captured.err
    )


def test_deploy_cli_success_returns_zero(capsys):
    exit_code = cli(["deploy", "agent.yaml"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Deploying agent from manifest: agent.yaml" in captured.out
