import gzip
import hashlib

from src.storage.artifacts import (
    ArtifactBackupRecord,
    ArtifactBackupValidator,
    ArtifactMetadata,
    CompressedArtifact,
    RestoreCheck,
    validate_compressed_artifact_backup,
    validate_compressed_artifact_backup_records,
)


def compressed_artifact(artifact_id, content, digest=None):
    raw = content.encode("utf-8")
    return CompressedArtifact(
        metadata=ArtifactMetadata(
            artifact_id=artifact_id,
            digest=digest or hashlib.sha256(raw).hexdigest(),
        ),
        payload=gzip.compress(raw),
    )


class RecordingArtifactStore:
    def __init__(self, objects):
        self.objects = objects
        self.downloaded = []

    def download(self, storage_key):
        self.downloaded.append(storage_key)
        return self.objects[storage_key]


class TestArtifactBackupValidator:
    def test_validates_sampled_compressed_artifact_digest(self):
        result = validate_compressed_artifact_backup([
            compressed_artifact("artifact-1", "restored payload"),
        ])

        assert result.ok
        assert result.checked_count == 1
        assert result.failures == []

    def test_decompression_failure_marks_backup_validation_failed(self):
        artifact = CompressedArtifact(
            metadata=ArtifactMetadata(
                artifact_id="artifact-2",
                digest=hashlib.sha256(b"payload").hexdigest(),
            ),
            payload=b"not a gzip stream",
        )

        result = validate_compressed_artifact_backup([artifact])

        assert not result.ok
        assert result.checked_count == 1
        assert result.failures[0].artifact_id == "artifact-2"
        assert result.failures[0].reason.startswith("decompression_failed")

    def test_digest_mismatch_marks_backup_validation_failed(self):
        artifact = compressed_artifact(
            "artifact-3",
            "actual payload",
            digest=hashlib.sha256(b"different payload").hexdigest(),
        )

        result = validate_compressed_artifact_backup([artifact])

        assert not result.ok
        assert result.checked_count == 1
        assert result.failures == [
            RestoreCheck(
                artifact_id="artifact-3",
                ok=False,
                reason="digest_mismatch",
            )
        ]

    def test_samples_configured_number_of_artifacts(self):
        artifacts = [
            compressed_artifact("artifact-1", "one"),
            compressed_artifact("artifact-2", "two"),
        ]

        result = ArtifactBackupValidator(sample_size=1).validate(artifacts)

        assert result.ok
        assert result.checked_count == 1

    def test_downloads_and_restores_sampled_artifact_records(self):
        restored = b"metadata-backed payload"
        skipped = b"not sampled"
        store = RecordingArtifactStore(
            {
                "backups/artifact-4.gz": gzip.compress(restored),
                "backups/artifact-5.gz": gzip.compress(skipped),
            }
        )
        records = [
            ArtifactBackupRecord(
                artifact_id="artifact-4",
                storage_key="backups/artifact-4.gz",
                digest=hashlib.sha256(restored).hexdigest(),
            ),
            ArtifactBackupRecord(
                artifact_id="artifact-5",
                storage_key="backups/artifact-5.gz",
                digest=hashlib.sha256(skipped).hexdigest(),
            ),
        ]

        result = validate_compressed_artifact_backup_records(
            records,
            store,
            sample_size=1,
        )

        assert result.ok
        assert result.checked_count == 1
        assert result.failures == []
        assert store.downloaded == ["backups/artifact-4.gz"]

    def test_download_failure_marks_backup_validation_failed(self):
        store = RecordingArtifactStore({})
        record = ArtifactBackupRecord(
            artifact_id="artifact-6",
            storage_key="missing.gz",
            digest=hashlib.sha256(b"unused").hexdigest(),
        )

        result = validate_compressed_artifact_backup_records([record], store)

        assert not result.ok
        assert result.checked_count == 1
        assert result.failures == [
            RestoreCheck(
                artifact_id="artifact-6",
                ok=False,
                reason="download_failed:KeyError",
            )
        ]

    def test_unsupported_compression_marks_backup_validation_failed(self):
        artifact = CompressedArtifact(
            metadata=ArtifactMetadata(
                artifact_id="artifact-7",
                digest=hashlib.sha256(b"payload").hexdigest(),
                compression="zstd",
            ),
            payload=b"payload",
        )

        result = validate_compressed_artifact_backup([artifact])

        assert not result.ok
        assert result.failures == [
            RestoreCheck(
                artifact_id="artifact-7",
                ok=False,
                reason="unsupported_compression",
            )
        ]
