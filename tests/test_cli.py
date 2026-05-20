from src.cli import main as cli_main


def test_deploy_dry_run_validates_manifest_without_deploying(
    tmp_path,
    monkeypatch,
    capsys,
):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        "name: demo-agent\nruntime: python\n",
        encoding="utf-8",
    )

    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run during dry-run")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", str(manifest), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Dry run passed" in captured.out
    assert "no deploy performed" in captured.out
    assert str(manifest) in captured.out
    assert captured.err == ""


def test_deploy_without_dry_run_validates_and_uses_backend(
    tmp_path,
    monkeypatch,
    capsys,
):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        "name: demo-agent\nruntime: python\n",
        encoding="utf-8",
    )
    deployed = []

    def fake_deploy(manifest_path, loaded_manifest):
        deployed.append((manifest_path, loaded_manifest))
        print(f"deployed {manifest_path}")

    monkeypatch.setattr(cli_main, "deploy_manifest", fake_deploy)

    result = cli_main.cli(["deploy", str(manifest)])

    captured = capsys.readouterr()
    assert result == 0
    assert deployed == [
        (manifest, {"name": "demo-agent", "runtime": "python"}),
    ]
    assert f"deployed {manifest}" in captured.out
    assert captured.err == ""


def test_deploy_dry_run_rejects_missing_manifest(monkeypatch, capsys):
    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run for invalid input")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", "missing.yaml", "--dry-run"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "Deploy validation failed" in captured.err
    assert "manifest does not exist" in captured.err


def test_deploy_rejects_missing_manifest_before_progress(monkeypatch, capsys):
    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run for invalid input")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", "missing.yaml"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "Deploying agent" not in captured.out
    assert "Deploy validation failed" in captured.err
    assert "manifest does not exist" in captured.err


def test_deploy_dry_run_rejects_directory(tmp_path, monkeypatch, capsys):
    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run for invalid input")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", str(tmp_path), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "manifest is not a file" in captured.err


def test_deploy_dry_run_rejects_empty_manifest(
    tmp_path,
    monkeypatch,
    capsys,
):
    manifest = tmp_path / "empty.yaml"
    manifest.write_text("", encoding="utf-8")

    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run for invalid input")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", str(manifest), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "manifest is empty" in captured.err


def test_deploy_dry_run_rejects_non_mapping_manifest(
    tmp_path,
    monkeypatch,
    capsys,
):
    manifest = tmp_path / "list.yaml"
    manifest.write_text("- name\n- runtime\n", encoding="utf-8")

    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run for invalid input")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", str(manifest), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "manifest must be a YAML mapping" in captured.err


def test_deploy_dry_run_rejects_invalid_yaml(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "invalid.yaml"
    manifest.write_text("name: [unterminated\n", encoding="utf-8")

    def fail_deploy(_manifest_path, _manifest):
        raise AssertionError("deploy backend should not run for invalid input")

    monkeypatch.setattr(cli_main, "deploy_manifest", fail_deploy)

    result = cli_main.cli(["deploy", str(manifest), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "manifest is invalid YAML" in captured.err
