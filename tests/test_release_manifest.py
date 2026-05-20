import pytest

from src.common.release import (
    ArchitectureImageValidation,
    ManifestValidationError,
    create_multi_arch_release_manifest,
)


REQUIRED_ARCHITECTURES = ("linux/amd64", "linux/arm64")


def test_multi_arch_manifest_includes_only_validated_digests():
    manifest = create_multi_arch_release_manifest(
        [
            ArchitectureImageValidation(
                architecture="linux/amd64",
                digest="sha256:" + "a" * 64,
                tests_passed=True,
                scan_passed=True,
            ),
            ArchitectureImageValidation(
                architecture="linux/arm64",
                digest="sha256:" + "b" * 64,
                tests_passed=True,
                scan_passed=True,
            ),
        ],
        REQUIRED_ARCHITECTURES,
    )

    assert manifest.as_manifest() == {
        "manifests": [
            {
                "architecture": "linux/amd64",
                "digest": "sha256:" + "a" * 64,
                "tests": "passed",
                "scan": "passed",
            },
            {
                "architecture": "linux/arm64",
                "digest": "sha256:" + "b" * 64,
                "tests": "passed",
                "scan": "passed",
            },
        ],
    }


def test_missing_architecture_result_fails_manifest_publication():
    with pytest.raises(
        ManifestValidationError,
        match="missing validation result.*linux/arm64",
    ):
        create_multi_arch_release_manifest(
            [
                ArchitectureImageValidation(
                    architecture="linux/amd64",
                    digest="sha256:" + "a" * 64,
                    tests_passed=True,
                    scan_passed=True,
                ),
            ],
            REQUIRED_ARCHITECTURES,
        )


@pytest.mark.parametrize(
    ("tests_passed", "scan_passed", "message"),
    [
        (None, True, "missing a passing test result"),
        (False, True, "missing a passing test result"),
        (True, None, "missing a passing scan result"),
        (True, False, "missing a passing scan result"),
    ],
)
def test_missing_test_or_scan_result_fails_release(
    tests_passed,
    scan_passed,
    message,
):
    with pytest.raises(ManifestValidationError, match=message):
        create_multi_arch_release_manifest(
            [
                ArchitectureImageValidation(
                    architecture="linux/amd64",
                    digest="sha256:" + "a" * 64,
                    tests_passed=tests_passed,
                    scan_passed=scan_passed,
                ),
                ArchitectureImageValidation(
                    architecture="linux/arm64",
                    digest="sha256:" + "b" * 64,
                    tests_passed=True,
                    scan_passed=True,
                ),
            ],
            REQUIRED_ARCHITECTURES,
        )


def test_release_summary_lists_each_architecture_digest_and_status():
    manifest = create_multi_arch_release_manifest(
        [
            ArchitectureImageValidation(
                architecture="linux/amd64",
                digest="sha256:" + "a" * 64,
                tests_passed=True,
                scan_passed=True,
            ),
            ArchitectureImageValidation(
                architecture="linux/arm64",
                digest="sha256:" + "b" * 64,
                tests_passed=True,
                scan_passed=True,
            ),
        ],
        REQUIRED_ARCHITECTURES,
    )

    assert manifest.release_summary().splitlines() == [
        f"linux/amd64: sha256:{'a' * 64} tests=passed scan=passed",
        f"linux/arm64: sha256:{'b' * 64} tests=passed scan=passed",
    ]
