"""Validate that GitHub Actions use immutable external action refs."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\"'\s#]+)")


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
    return not (
        reference.startswith("./")
        or reference.startswith("../")
        or reference.startswith("docker://")
    )


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
            if "@" not in reference:
                violations.append(
                    f"{workflow}:{line_number}: external action has no ref: "
                    f"{reference}"
                )
                continue
            action, ref = reference.rsplit("@", 1)
            if not FULL_SHA_RE.fullmatch(ref):
                violations.append(
                    f"{workflow}:{line_number}: {action}@{ref} is not pinned "
                    "to a full commit SHA"
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

    violations = mutable_action_refs(Path(args.root))
    if violations:
        print("Mutable GitHub Actions references found:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
