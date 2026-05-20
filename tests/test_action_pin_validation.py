import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / (
    "validate_github_action_pins.py"
)


def write_workflow(root, content):
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    workflow = workflows / "ci.yml"
    workflow.write_text(content)
    return workflow


def write_dependabot(root, content=None):
    dependabot = root / ".github" / "dependabot.yml"
    dependabot.parent.mkdir(parents=True, exist_ok=True)
    dependabot.write_text(
        content
        or """
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
"""
    )
    return dependabot


def run_validator(root):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_validator_accepts_full_sha_action_refs(tmp_path):
    docker_ref = "docker://python@sha256:" + "a" * 64
    write_workflow(
        tmp_path,
        f"""
name: CI
jobs:
  test:
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
      - uses: ./local-action
      - uses: {docker_ref}
""",
    )
    write_dependabot(tmp_path)

    result = run_validator(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""


def test_validator_rejects_mutable_action_refs(tmp_path):
    workflow = write_workflow(
        tmp_path,
        """
name: CI
jobs:
  test:
    steps:
      - uses: actions/checkout@v4
""",
    )
    write_dependabot(tmp_path)

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert str(workflow) in result.stderr
    assert "actions/checkout@v4" in result.stderr


def test_validator_rejects_external_actions_without_refs(tmp_path):
    write_workflow(
        tmp_path,
        """
name: CI
jobs:
  test:
    steps:
      - uses: actions/checkout
""",
    )
    write_dependabot(tmp_path)

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert "external action has no ref" in result.stderr


def test_validator_rejects_mutable_docker_action_refs(tmp_path):
    write_workflow(
        tmp_path,
        """
name: CI
jobs:
  test:
    steps:
      - uses: docker://python:3.11
""",
    )
    write_dependabot(tmp_path)

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert "docker://python:3.11" in result.stderr


def test_validator_rejects_missing_dependabot_action_updates(tmp_path):
    write_workflow(
        tmp_path,
        """
name: CI
jobs:
  test:
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
""",
    )

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert "missing GitHub Actions update automation" in result.stderr


def test_validator_rejects_dependabot_without_action_updates(tmp_path):
    write_workflow(
        tmp_path,
        """
name: CI
jobs:
  test:
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
""",
    )
    write_dependabot(
        tmp_path,
        """
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
""",
    )

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert "missing github-actions updates" in result.stderr
