from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_runner_provenance.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def write_policy(tmp_path: Path) -> Path:
    policy = {
        "approved_runner_labels": ["self-hosted", "linux", "x64", "release"],
        "approved_image_digests": [
            "sha256:" + "a" * 64,
        ],
        "required_runner_labels": ["linux", "x64"],
        "max_image_age_hours": 24,
    }
    path = tmp_path / "runner-policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def run_check(
    tmp_path: Path,
    **env_overrides: str,
) -> subprocess.CompletedProcess[str]:
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "SOURCE_DATE_EPOCH": "1779278400",
        "GITHUB_STEP_SUMMARY": str(summary),
        "RUNNER_ENVIRONMENT": "self-hosted",
        "RUNNER_NAME": "builder-01",
        "RUNNER_PROVENANCE_LABELS": "self-hosted,linux,x64,release",
        "RUNNER_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "RUNNER_IMAGE_BUILT_AT": "2026-05-20T10:00:00Z",
        **env_overrides,
    }
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--policy",
            str(write_policy(tmp_path)),
            "--strict",
        ],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )


def test_valid_runner_provenance_passes_and_writes_summary(tmp_path):
    result = run_check(tmp_path)

    assert result.returncode == 0
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "Runner provenance preflight" in summary
    assert "sha256:aaaaaa" in summary
    assert "builder-01" in summary


def test_unapproved_label_fails_closed(tmp_path):
    result = run_check(
        tmp_path,
        RUNNER_PROVENANCE_LABELS="self-hosted,linux,x64,unknown-gpu",
    )

    assert result.returncode == 1
    assert "unapproved runner labels" in result.stderr


def test_unapproved_image_digest_fails_closed(tmp_path):
    result = run_check(tmp_path, RUNNER_IMAGE_DIGEST="sha256:" + "b" * 64)

    assert result.returncode == 1
    assert "digest is not approved" in result.stderr


def test_stale_image_timestamp_fails_closed(tmp_path):
    result = run_check(tmp_path, RUNNER_IMAGE_BUILT_AT="2026-05-18T10:00:00Z")

    assert result.returncode == 1
    assert "runner image is stale" in result.stderr


def test_ci_workflow_runs_preflight_before_build_steps():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    preflight = workflow.index("Validate runner provenance")
    setup_python = workflow.index("Set up Python")
    release_preflight = workflow.index("Validate release runner provenance")
    release_build = workflow.index("Build release package")
    assert preflight < setup_python
    assert release_preflight < release_build
    assert "--strict" in workflow
    assert "RUNNER_IMAGE_DIGEST" in workflow
