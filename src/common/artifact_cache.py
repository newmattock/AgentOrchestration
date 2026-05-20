"""Local artifact download cache with digest validation."""

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Optional, Union


class ArtifactCacheError(ValueError):
    """Raised when cached or downloaded artifact content is invalid."""


class ArtifactDownloadCache:
    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def get_path(
        self,
        artifact_id: str,
        expected_digest: str,
        download: Callable[[Path], None],
    ) -> Path:
        digest = _normalize_sha256(expected_digest)
        artifact_path = self._artifact_path(artifact_id)
        metadata_path = self._metadata_path(artifact_id)

        if self._is_valid_hit(artifact_path, metadata_path, digest):
            return artifact_path

        self._evict(artifact_path, metadata_path)
        tmp_path = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
        self._evict(tmp_path, None)
        download(tmp_path)

        actual_digest = _sha256_file(tmp_path)
        if actual_digest != digest:
            self._evict(tmp_path, None)
            raise ArtifactCacheError(
                "downloaded artifact digest mismatch: "
                f"expected sha256:{digest}, got sha256:{actual_digest}"
            )

        os.replace(tmp_path, artifact_path)
        metadata_path.write_text(
            json.dumps(
                {
                    "algorithm": "sha256",
                    "digest": digest,
                    "size": artifact_path.stat().st_size,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return artifact_path

    def _is_valid_hit(
        self,
        artifact_path: Path,
        metadata_path: Path,
        expected_digest: str,
    ) -> bool:
        if not artifact_path.exists() or not metadata_path.exists():
            return False

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        if not isinstance(metadata, dict):
            return False
        if metadata.get("algorithm") != "sha256":
            return False
        if metadata.get("digest") != expected_digest:
            return False
        if metadata.get("size") != artifact_path.stat().st_size:
            return False

        return _sha256_file(artifact_path) == expected_digest

    def _artifact_path(self, artifact_id: str) -> Path:
        cache_key = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
        return self.root / f"{cache_key}.artifact"

    def _metadata_path(self, artifact_id: str) -> Path:
        cache_key = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
        return self.root / f"{cache_key}.json"

    @staticmethod
    def _evict(artifact_path: Path, metadata_path: Optional[Path]) -> None:
        for path in (artifact_path, metadata_path):
            if path is not None:
                path.unlink(missing_ok=True)


def _normalize_sha256(digest: str) -> str:
    value = digest.removeprefix("sha256:").lower()
    has_invalid_char = any(char not in "0123456789abcdef" for char in value)
    if len(value) != 64 or has_invalid_char:
        raise ArtifactCacheError("expected a sha256 hex digest")
    return value


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
