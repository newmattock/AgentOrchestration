"""Release manifest validation helpers."""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


class ManifestValidationError(ValueError):
    """Raised when a release manifest would include unvalidated images."""


@dataclass(frozen=True)
class ArchitectureImageValidation:
    architecture: str
    digest: str
    tests_passed: Optional[bool]
    scan_passed: Optional[bool]


@dataclass(frozen=True)
class ValidatedArchitectureDigest:
    architecture: str
    digest: str
    test_status: str = "passed"
    scan_status: str = "passed"

    def summary_line(self) -> str:
        return (
            f"{self.architecture}: {self.digest} "
            f"tests={self.test_status} scan={self.scan_status}"
        )


@dataclass(frozen=True)
class MultiArchReleaseManifest:
    digests: Tuple[ValidatedArchitectureDigest, ...]

    def as_manifest(self) -> Dict[str, List[Dict[str, str]]]:
        return {
            "manifests": [
                {
                    "architecture": digest.architecture,
                    "digest": digest.digest,
                    "tests": digest.test_status,
                    "scan": digest.scan_status,
                }
                for digest in self.digests
            ],
        }

    def release_summary(self) -> str:
        return "\n".join(digest.summary_line() for digest in self.digests)


def create_multi_arch_release_manifest(
    validations: Iterable[ArchitectureImageValidation],
    required_architectures: Iterable[str],
) -> MultiArchReleaseManifest:
    required_order = tuple(required_architectures)
    if not required_order:
        raise ManifestValidationError(
            "at least one architecture is required for manifest publication"
        )

    validations_by_architecture: Dict[str, ArchitectureImageValidation] = {}
    for validation in validations:
        architecture = validation.architecture.strip()
        if not architecture:
            raise ManifestValidationError(
                "architecture-specific validation is missing an architecture"
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
        _validate_architecture_result(validation)
        validated_digests.append(
            ValidatedArchitectureDigest(
                architecture=architecture,
                digest=validation.digest,
            )
        )

    return MultiArchReleaseManifest(digests=tuple(validated_digests))


def _validate_architecture_result(
    validation: ArchitectureImageValidation,
) -> None:
    architecture = validation.architecture.strip()
    if not validation.digest.startswith("sha256:"):
        raise ManifestValidationError(
            f"{architecture} is missing a validated sha256 digest"
        )
    if validation.tests_passed is not True:
        raise ManifestValidationError(
            f"{architecture} is missing a passing test result"
        )
    if validation.scan_passed is not True:
        raise ManifestValidationError(
            f"{architecture} is missing a passing scan result"
        )
