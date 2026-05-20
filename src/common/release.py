"""Release manifest validation helpers."""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple


SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PASSING_STATUSES = {"ok", "pass", "passed", "success", "successful", "true"}


class ManifestValidationError(ValueError):
    """Raised when a release manifest would include unvalidated images."""


@dataclass(frozen=True)
class ArchitectureImageValidation:
    architecture: str
    digest: str
    tests_passed: Any
    scan_passed: Any
    source_sha: str = ""


@dataclass(frozen=True)
class ValidatedArchitectureDigest:
    architecture: str
    digest: str
    test_status: str = "passed"
    scan_status: str = "passed"
    source_sha: str = ""
    image_ref: str = ""

    def summary_line(self) -> str:
        summary = (
            f"{self.architecture}: {self.digest} "
            f"tests={self.test_status} scan={self.scan_status}"
        )
        if self.source_sha:
            summary += f" source={self.source_sha}"
        return summary

    def as_manifest_entry(self) -> Dict[str, str]:
        entry = {
            "architecture": self.architecture,
            "digest": self.digest,
            "tests": self.test_status,
            "scan": self.scan_status,
        }
        if self.source_sha:
            entry["source_sha"] = self.source_sha
        if self.image_ref:
            entry["image_ref"] = self.image_ref
        return entry


@dataclass(frozen=True)
class MultiArchReleaseManifest:
    digests: Tuple[ValidatedArchitectureDigest, ...]

    def as_manifest(self) -> Dict[str, List[Dict[str, str]]]:
        return {
            "manifests": [
                digest.as_manifest_entry()
                for digest in self.digests
            ],
        }

    def release_summary(self) -> str:
        return "\n".join(digest.summary_line() for digest in self.digests)

    def image_refs(self) -> Tuple[str, ...]:
        return tuple(
            digest.image_ref or digest.digest
            for digest in self.digests
        )


def create_multi_arch_release_manifest(
    validations: Iterable[ArchitectureImageValidation],
    required_architectures: Iterable[str],
    expected_source_sha: str = "",
    image_repository: str = "",
) -> MultiArchReleaseManifest:
    required_order = tuple(required_architectures)
    if not required_order:
        raise ManifestValidationError(
            "at least one architecture is required for manifest publication"
        )

    required_architecture_set = set(required_order)
    validations_by_architecture: Dict[str, ArchitectureImageValidation] = {}
    for validation in validations:
        architecture = validation.architecture.strip()
        if not architecture:
            raise ManifestValidationError(
                "architecture-specific validation is missing an architecture"
            )
        if architecture not in required_architecture_set:
            raise ManifestValidationError(
                f"unexpected architecture in release validation: "
                f"{architecture}"
            )
        if architecture in validations_by_architecture:
            raise ManifestValidationError(
                f"duplicate validation result for architecture {architecture}"
            )
        validations_by_architecture[architecture] = validation

    missing_architectures = [
        architecture
        for architecture in required_order
        if architecture not in validations_by_architecture
    ]
    if missing_architectures:
        missing = ", ".join(missing_architectures)
        raise ManifestValidationError(
            f"missing validation result for architecture(s): {missing}"
        )

    validated_digests = []
    for architecture in required_order:
        validation = validations_by_architecture[architecture]
        _validate_architecture_result(validation, expected_source_sha)
        validated_digests.append(
            ValidatedArchitectureDigest(
                architecture=architecture,
                digest=validation.digest,
                source_sha=validation.source_sha.strip(),
                image_ref=_image_ref(
                    image_repository,
                    validation.digest,
                ),
            )
        )

    return MultiArchReleaseManifest(digests=tuple(validated_digests))


def _validate_architecture_result(
    validation: ArchitectureImageValidation,
    expected_source_sha: str = "",
) -> None:
    architecture = validation.architecture.strip()
    if not SHA256_DIGEST_RE.fullmatch(validation.digest):
        raise ManifestValidationError(
            f"{architecture} is missing a validated sha256 digest"
        )
    if not _is_passing_validation(validation.tests_passed):
        raise ManifestValidationError(
            f"{architecture} is missing a passing test result"
        )
    if not _is_passing_validation(validation.scan_passed):
        raise ManifestValidationError(
            f"{architecture} is missing a passing scan result"
        )
    source_sha = validation.source_sha.strip()
    expected_sha = expected_source_sha.strip()
    if expected_sha and source_sha != expected_sha:
        built_from = source_sha or "unknown source sha"
        raise ManifestValidationError(
            f"{architecture} digest was built from {built_from} "
            f"instead of release source {expected_sha}"
        )


def _is_passing_validation(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in PASSING_STATUSES


def _image_ref(image_repository: str, digest: str) -> str:
    image = image_repository.strip()
    if not image:
        return ""
    return f"{image}@{digest}"
