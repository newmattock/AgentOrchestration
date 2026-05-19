"""Compressed artifact backup restore validation."""

from dataclasses import dataclass
import gzip
import hashlib
import hmac
import zlib
from typing import Callable, List, Protocol, Sequence


class ArtifactPayloadStore(Protocol):
    def download(self, storage_key: str) -> bytes:
        """Return compressed artifact bytes for a backup storage key."""


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str
    digest: str
    compression: str = "gzip"


@dataclass(frozen=True)
class ArtifactBackupRecord:
    artifact_id: str
    storage_key: str
    digest: str
    compression: str = "gzip"


@dataclass(frozen=True)
class CompressedArtifact:
    metadata: ArtifactMetadata
    payload: bytes


@dataclass(frozen=True)
class RestoreCheck:
    artifact_id: str
    ok: bool
    reason: str


@dataclass(frozen=True)
class BackupValidationResult:
    ok: bool
    checked_count: int
    failures: List[RestoreCheck]


class ArtifactBackupValidator:
    def __init__(
        self,
        sample_size: int = 10,
        digest_factory: Callable[[], "hashlib._Hash"] = hashlib.sha256,
    ):
        if sample_size < 1:
            raise ValueError("sample_size must be at least 1")
        self.sample_size = sample_size
        self._digest_factory = digest_factory

    def validate(
        self,
        artifacts: Sequence[CompressedArtifact],
    ) -> BackupValidationResult:
        failures: List[RestoreCheck] = []
        sampled = list(artifacts[:self.sample_size])

        for artifact in sampled:
            check = self._validate_artifact(artifact)
            if not check.ok:
                failures.append(check)

        return BackupValidationResult(
            ok=not failures,
            checked_count=len(sampled),
            failures=failures,
        )

    def validate_records(
        self,
        records: Sequence[ArtifactBackupRecord],
        store: ArtifactPayloadStore,
    ) -> BackupValidationResult:
        failures: List[RestoreCheck] = []
        sampled = list(records[:self.sample_size])

        for record in sampled:
            try:
                payload = store.download(record.storage_key)
            except Exception as exc:
                failures.append(
                    RestoreCheck(
                        artifact_id=record.artifact_id,
                        ok=False,
                        reason=f"download_failed:{exc.__class__.__name__}",
                    )
                )
                continue

            check = self._validate_artifact(
                CompressedArtifact(
                    metadata=ArtifactMetadata(
                        artifact_id=record.artifact_id,
                        digest=record.digest,
                        compression=record.compression,
                    ),
                    payload=payload,
                )
            )
            if not check.ok:
                failures.append(check)

        return BackupValidationResult(
            ok=not failures,
            checked_count=len(sampled),
            failures=failures,
        )

    def _validate_artifact(self, artifact: CompressedArtifact) -> RestoreCheck:
        metadata = artifact.metadata
        if metadata.compression != "gzip":
            return RestoreCheck(
                artifact_id=metadata.artifact_id,
                ok=False,
                reason="unsupported_compression",
            )

        try:
            restored = gzip.decompress(artifact.payload)
        except (EOFError, OSError, gzip.BadGzipFile, zlib.error) as exc:
            return RestoreCheck(
                artifact_id=metadata.artifact_id,
                ok=False,
                reason=f"decompression_failed:{exc.__class__.__name__}",
            )

        digest = self._digest_factory()
        digest.update(restored)
        if not hmac.compare_digest(digest.hexdigest(), metadata.digest):
            return RestoreCheck(
                artifact_id=metadata.artifact_id,
                ok=False,
                reason="digest_mismatch",
            )

        return RestoreCheck(
            artifact_id=metadata.artifact_id,
            ok=True,
            reason="ok",
        )


def validate_compressed_artifact_backup(
    artifacts: Sequence[CompressedArtifact],
    sample_size: int = 10,
) -> BackupValidationResult:
    return ArtifactBackupValidator(sample_size=sample_size).validate(artifacts)


def validate_compressed_artifact_backup_records(
    records: Sequence[ArtifactBackupRecord],
    store: ArtifactPayloadStore,
    sample_size: int = 10,
) -> BackupValidationResult:
    return ArtifactBackupValidator(sample_size=sample_size).validate_records(
        records,
        store,
    )
