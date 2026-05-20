"""Validate that GitHub Actions use immutable external action refs."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
DOCKER_DIGEST_RE = re.compile(r"^docker://.+@sha256:[0-9a-fA-F]{64}$")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\"'\s#]+)")
GITHUB_ACTIONS_RE = re.compile(
    r"package-ecosystem:\s*[\"']?github-actions[\"']?"
)
ROOT_DIRECTORY_RE = re.compile(r"directory:\s*[\"']?/[\"']?")


def workflow_files(root: Path) -> Iterable[Path]:
    workflows = root / ".github" / "workflows"
    if not workflows.exists():
        return []
    return sorted(
        path
        for suffix in ("*.yml", "*.yaml")
        for path in workflows.glob(suffix)
        if path.is_file()
    )


def is_external_action(reference: str) -> bool:
    return not (reference.startswith("./") or reference.startswith("../"))


def is_pinned_action(reference: str) -> bool:
    if reference.startswith("docker://"):
        return bool(DOCKER_DIGEST_RE.fullmatch(reference))
    if "@" not in reference:
        return False
    ref = reference.rsplit("@", 1)[1]
    return bool(FULL_SHA_RE.fullmatch(ref))


def dependabot_config_violations(root: Path) -> list[str]:
    dependabot = root / ".github" / "dependabot.yml"
    if not dependabot.exists():
        return [f"{dependabot}: missing GitHub Actions update automation"]

    content = dependabot.read_text()
    has_github_actions = GITHUB_ACTIONS_RE.search(content)
    has_root_directory = ROOT_DIRECTORY_RE.search(content)
    if not has_github_actions or not has_root_directory:
        return [
            f"{dependabot}: missing github-actions updates for directory '/'"
        ]
    return []


def mutable_action_refs(root: Path) -> list[str]:
    violations: list[str] = []
    for workflow in workflow_files(root):
        lines = workflow.read_text().splitlines()
        for line_number, line in enumerate(lines, 1):
            match = USES_RE.match(line)
            if not match:
                continue
            reference = match.group(1)
            if not is_external_action(reference):
                continue
            if not is_pinned_action(reference):
                if "@" not in reference:
                    violations.append(
                        f"{workflow}:{line_number}: external action has no "
                        f"ref: {reference}"
                    )
                    continue
                violations.append(
                    f"{workflow}:{line_number}: {reference} is not pinned to "
                    "an immutable commit SHA or Docker digest"
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when workflow actions use mutable refs.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Repository root to validate. Defaults to the current directory.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    violations = mutable_action_refs(root)
    violations.extend(dependabot_config_violations(root))
    if violations:
        print("Mutable GitHub Actions references found:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
