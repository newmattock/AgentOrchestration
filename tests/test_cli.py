import sys

import pytest

from src.cli.main import cli


def test_deploy_fails_when_manifest_is_missing(monkeypatch, capsys):
    missing_manifest = "missing-agent-manifest.yaml"
    monkeypatch.setattr(sys, "argv", ["ao", "deploy", missing_manifest])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert f"Manifest file not found: {missing_manifest}" in captured.err
    assert "Deploying agent from manifest" not in captured.out


def test_deploy_fails_when_manifest_is_directory(
    monkeypatch,
    capsys,
    tmp_path,
):
    monkeypatch.setattr(sys, "argv", ["ao", "deploy", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert f"Manifest file not found: {tmp_path}" in captured.err
    assert "Deploying agent from manifest" not in captured.out


def test_deploy_accepts_existing_manifest(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text("name: test-agent\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["ao", "deploy", str(manifest)])

    cli()

    captured = capsys.readouterr()
    assert f"Deploying agent from manifest: {manifest}" in captured.out


def test_deploy_expands_home_manifest_path(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text("name: home-agent\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ao", "deploy", "~/agent.yaml"])

    cli()

    captured = capsys.readouterr()
    assert f"Deploying agent from manifest: {manifest}" in captured.out
