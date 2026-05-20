import hashlib
import json

import pytest

from src.common.artifact_cache import ArtifactCacheError, ArtifactDownloadCache


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_cache_hit_verifies_digest_before_reuse(tmp_path):
    cache = ArtifactDownloadCache(tmp_path)
    content = b"approved artifact"
    downloads = 0

    def download(path):
        nonlocal downloads
        downloads += 1
        path.write_bytes(content)

    first_path = cache.get_path("artifact.tar", sha256(content), download)
    second_path = cache.get_path("artifact.tar", sha256(content), download)

    assert first_path == second_path
    assert second_path.read_bytes() == content
    assert downloads == 1


def test_corrupt_cache_entry_is_evicted_and_redownloaded(tmp_path):
    cache = ArtifactDownloadCache(tmp_path)
    content = b"approved artifact"
    downloads = 0

    def download(path):
        nonlocal downloads
        downloads += 1
        path.write_bytes(content)

    artifact_path = cache.get_path("artifact.tar", sha256(content), download)
    artifact_path.write_bytes(b"corrupt artifact")

    repaired_path = cache.get_path("artifact.tar", sha256(content), download)

    assert repaired_path == artifact_path
    assert repaired_path.read_bytes() == content
    assert downloads == 2


def test_partial_file_is_evicted_and_redownloaded(tmp_path):
    cache = ArtifactDownloadCache(tmp_path)
    content = b"complete artifact payload"
    artifact_path = cache._artifact_path("artifact.tar")
    metadata_path = cache._metadata_path("artifact.tar")
    artifact_path.write_bytes(content[:8])
    metadata_path.write_text(
        json.dumps(
            {
                "algorithm": "sha256",
                "digest": sha256(content),
                "size": len(content),
            }
        ),
        encoding="utf-8",
    )
    downloads = 0

    def download(path):
        nonlocal downloads
        downloads += 1
        path.write_bytes(content)

    repaired_path = cache.get_path("artifact.tar", sha256(content), download)

    assert repaired_path.read_bytes() == content
    assert downloads == 1


def test_metadata_digest_mismatch_is_evicted_and_redownloaded(tmp_path):
    cache = ArtifactDownloadCache(tmp_path)
    old_content = b"old artifact"
    new_content = b"new approved artifact"

    old_path = cache.get_path(
        "artifact.tar",
        sha256(old_content),
        lambda path: path.write_bytes(old_content),
    )

    downloads = 0

    def download(path):
        nonlocal downloads
        downloads += 1
        path.write_bytes(new_content)

    new_path = cache.get_path("artifact.tar", sha256(new_content), download)

    assert new_path == old_path
    assert new_path.read_bytes() == new_content
    assert downloads == 1


def test_bad_redownload_digest_is_not_cached(tmp_path):
    cache = ArtifactDownloadCache(tmp_path)

    with pytest.raises(ArtifactCacheError, match="digest mismatch"):
        cache.get_path(
            "artifact.tar",
            sha256(b"expected"),
            lambda path: path.write_bytes(b"wrong"),
        )

    assert not cache._artifact_path("artifact.tar").exists()
    assert not cache._metadata_path("artifact.tar").exists()
