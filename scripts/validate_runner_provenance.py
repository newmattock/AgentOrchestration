#!/usr/bin/env python3
"""Fail closed when CI runner image provenance is missing or unapproved."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class RunnerPolicy:
    approved_runner_labels: frozenset[str]
    approved_image_digests: frozenset[str]
    max_image_age_hours: int
    required_runner_labels: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> "RunnerPolicy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        env_digests = split_values(
            os.environ.get("RUNNER_APPROVED_IMAGE_DIGESTS", "")
        )
        policy_digests = raw.get("approved_image_digests", [])
        return cls(
            approved_runner_labels=frozenset(
                normalize_label(label)
                for label in raw.get("approved_runner_labels", [])
            ),
            approved_image_digests=frozenset(
                digest.lower()
                for digest in [*policy_digests, *env_digests]
                if digest
            ),
            max_image_age_hours=int(raw.get("max_image_age_hours", 168)),
            required_runner_labels=frozenset(
                normalize_label(label)
                for label in raw.get("required_runner_labels", [])
            ),
        )


@dataclass(frozen=True)
class RunnerProvenance:
    labels: frozenset[str]
    image_digest: str
    image_built_at: datetime | None
    runner_name: str
    runner_environment: str

    @classmethod
    def from_env(cls) -> "RunnerProvenance":
        labels = split_values(os.environ.get("RUNNER_PROVENANCE_LABELS", ""))
        fallback_labels = [
            os.environ.get("RUNNER_OS", ""),
            os.environ.get("RUNNER_ARCH", ""),
            os.environ.get("RUNNER_ENVIRONMENT", ""),
        ]
        label_set = frozenset(
            normalize_label(label)
            for label in [*labels, *fallback_labels]
            if label
        )
        return cls(
            labels=label_set,
            image_digest=first_env(
                "RUNNER_IMAGE_DIGEST",
                "ACTIONS_RUNNER_IMAGE_DIGEST",
            ).lower(),
            image_built_at=parse_timestamp(
                first_env(
                    "RUNNER_IMAGE_BUILT_AT",
                    "ACTIONS_RUNNER_IMAGE_BUILT_AT",
                )
            ),
            runner_name=first_env("RUNNER_NAME", default="unknown"),
            runner_environment=normalize_label(
                first_env("RUNNER_ENVIRONMENT", default="unknown")
            ),
        )

    @property
    def needs_strict_image_check(self) -> bool:
        return (
            "self-hosted" in self.labels
            or self.runner_environment == "self-hosted"
        )


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]


def normalize_label(value: str) -> str:
    return value.strip().lower()


def first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return default


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_now() -> datetime:
    fixed_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if fixed_epoch:
        return datetime.fromtimestamp(float(fixed_epoch), tz=timezone.utc)
    return datetime.now(timezone.utc)


def validate(
    policy: RunnerPolicy,
    provenance: RunnerProvenance,
    *,
    strict: bool,
) -> list[str]:
    errors: list[str] = []
    unknown_labels = provenance.labels - policy.approved_runner_labels
    missing_labels = policy.required_runner_labels - provenance.labels
    if not provenance.labels:
        errors.append("missing runner labels")
    if unknown_labels:
        errors.append(
            f"unapproved runner labels: {', '.join(sorted(unknown_labels))}"
        )
    if missing_labels:
        missing = ", ".join(sorted(missing_labels))
        errors.append(
            f"missing required runner labels: {missing}"
        )

    strict_image_check = strict or provenance.needs_strict_image_check
    if not strict_image_check:
        return errors

    if not provenance.image_digest:
        errors.append("missing runner image digest")
    elif not DIGEST_RE.match(provenance.image_digest):
        errors.append("runner image digest is not a sha256 digest")
    elif not policy.approved_image_digests:
        errors.append("no approved image digest allowlist configured")
    elif provenance.image_digest not in policy.approved_image_digests:
        errors.append("runner image digest is not approved")

    if provenance.image_built_at is None:
        errors.append("missing or invalid runner image build timestamp")
    else:
        max_age_seconds = policy.max_image_age_hours * 3600
        age_seconds = (utc_now() - provenance.image_built_at).total_seconds()
        if age_seconds < 0:
            errors.append("runner image build timestamp is in the future")
        elif age_seconds > max_age_seconds:
            errors.append(
                f"runner image is stale: {age_seconds / 3600:.1f}h old "
                f"(max {policy.max_image_age_hours}h)"
            )
    return errors


def masked_digest(digest: str) -> str:
    if not digest:
        return "missing"
    if len(digest) <= 20:
        return digest
    return f"{digest[:14]}...{digest[-8:]}"


def write_summary(
    path: str,
    provenance: RunnerProvenance,
    errors: Iterable[str],
) -> None:
    if not path:
        return
    error_list = list(errors)
    status = "failed" if error_list else "passed"
    built_at = (
        provenance.image_built_at.isoformat()
        if provenance.image_built_at
        else "missing"
    )
    lines = [
        "### Runner provenance preflight",
        "",
        f"- Status: `{status}`",
        f"- Runner: `{provenance.runner_name}`",
        f"- Labels: `{', '.join(sorted(provenance.labels)) or 'missing'}`",
        f"- Image digest: `{masked_digest(provenance.image_digest)}`",
        f"- Image built at: `{built_at}`",
    ]
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy",
        default=".github/runner-provenance-policy.json",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Require image digest and timestamp validation even on "
            "non-self-hosted runners."
        ),
    )
    args = parser.parse_args()

    policy = RunnerPolicy.load(Path(args.policy))
    provenance = RunnerProvenance.from_env()
    errors = validate(policy, provenance, strict=args.strict)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    write_summary(summary_path, provenance, errors)
    if errors:
        for error in errors:
            print(f"runner provenance check failed: {error}", file=sys.stderr)
        return 1
    print("runner provenance check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
