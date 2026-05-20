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


def run_validator(root):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_validator_accepts_full_sha_action_refs(tmp_path):
    write_workflow(
        tmp_path,
        """
name: CI
jobs:
  test:
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
      - uses: ./local-action
      - uses: docker://python:3.11
""",
    )

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

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert "external action has no ref" in result.stderr
