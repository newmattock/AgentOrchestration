"""Retention workflow helpers for cascading task data deletion."""

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set


PRIMARY_ARTIFACTS = "primary_artifacts"
DERIVED_EMBEDDINGS = "derived_embeddings"
DERIVED_INDEXES = "derived_indexes"


@dataclass(frozen=True)
class RetentionRecord:
    record_id: str
    workspace_id: str
    data_class: str
    source_id: Optional[str] = None


@dataclass(frozen=True)
class DeletionManifestEntry:
    store: str
    data_class: str
    record_ids: List[str]


@dataclass(frozen=True)
class DeletionManifest:
    workspace_id: str
    task_ids: List[str]
    entries: List[DeletionManifestEntry]
    created_at: float = field(default_factory=time.time)

    @property
    def store_names(self) -> List[str]:
        return [entry.store for entry in self.entries]


@dataclass(frozen=True)
class StoreDeletionCompletion:
    store: str
    data_class: str
    deleted_ids: List[str]
    missing_ids: List[str]


@dataclass(frozen=True)
class DeletionCompletion:
    manifest: DeletionManifest
    stores: List[StoreDeletionCompletion]
    completed_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class StoreDeletionVerification:
    store: str
    data_class: str
    remaining_ids: List[str]

    @property
    def complete(self) -> bool:
        return not self.remaining_ids


@dataclass(frozen=True)
class DeletionVerification:
    completion: DeletionCompletion
    stores: List[StoreDeletionVerification]

    @property
    def complete(self) -> bool:
        return all(store.complete for store in self.stores)


class InMemoryRetentionStore:
    """Small store contract used by retention workflow tests and adapters."""

    def __init__(self, name: str, data_class: str):
        self.name = name
        self.data_class = data_class
        self._records: Dict[str, RetentionRecord] = {}

    def add(
        self,
        record_id: str,
        workspace_id: str,
        source_id: Optional[str] = None,
    ) -> None:
        self._records[record_id] = RetentionRecord(
            record_id=record_id,
            workspace_id=workspace_id,
            data_class=self.data_class,
            source_id=source_id,
        )

    def get(self, record_id: str) -> Optional[RetentionRecord]:
        return self._records.get(record_id)

    def has(self, record_id: str) -> bool:
        return record_id in self._records

    def delete(self, record_id: str) -> bool:
        return self._records.pop(record_id, None) is not None

    def list_workspace_records(
        self,
        workspace_id: str,
    ) -> List[RetentionRecord]:
        return [
            record
            for record in self._records.values()
            if record.workspace_id == workspace_id
        ]


class RetentionDeletionWorkflow:
    def __init__(self, stores: Iterable[InMemoryRetentionStore]):
        self._stores = {store.name: store for store in stores}

    def build_manifest(
        self,
        workspace_id: str,
        task_ids: Iterable[str],
    ) -> DeletionManifest:
        task_id_set = set(task_ids)
        primary_ids = self._matching_primary_ids(workspace_id, task_id_set)
        source_ids = task_id_set | primary_ids
        entries = []

        for store in self._stores.values():
            record_ids = sorted(
                record.record_id
                for record in store.list_workspace_records(workspace_id)
                if self._record_matches_manifest(
                    record,
                    task_id_set,
                    source_ids,
                )
            )
            entries.append(
                DeletionManifestEntry(
                    store=store.name,
                    data_class=store.data_class,
                    record_ids=record_ids,
                )
            )

        return DeletionManifest(
            workspace_id=workspace_id,
            task_ids=sorted(task_id_set),
            entries=entries,
        )

    def delete_workspace_task_data(
        self,
        workspace_id: str,
        task_ids: Iterable[str],
    ) -> DeletionCompletion:
        return self.apply_manifest(self.build_manifest(workspace_id, task_ids))

    def apply_manifest(self, manifest: DeletionManifest) -> DeletionCompletion:
        completions = []

        for entry in manifest.entries:
            store = self._stores[entry.store]
            deleted_ids = []
            missing_ids = []
            for record_id in entry.record_ids:
                if store.delete(record_id):
                    deleted_ids.append(record_id)
                else:
                    missing_ids.append(record_id)

            completions.append(
                StoreDeletionCompletion(
                    store=entry.store,
                    data_class=entry.data_class,
                    deleted_ids=deleted_ids,
                    missing_ids=missing_ids,
                )
            )

        return DeletionCompletion(manifest=manifest, stores=completions)

    def verify_completion(
        self,
        completion: DeletionCompletion,
    ) -> DeletionVerification:
        verifications = []

        for entry in completion.manifest.entries:
            store = self._stores[entry.store]
            remaining_ids = sorted(
                record_id
                for record_id in entry.record_ids
                if store.has(record_id)
            )
            verifications.append(
                StoreDeletionVerification(
                    store=entry.store,
                    data_class=entry.data_class,
                    remaining_ids=remaining_ids,
                )
            )

        return DeletionVerification(
            completion=completion,
            stores=verifications,
        )

    def reconcile_stale_derived_records(
        self,
        workspace_id: str,
    ) -> DeletionCompletion:
        active_primary_ids = self._all_primary_ids(workspace_id)
        entries = []

        for store in self._stores.values():
            if store.data_class == PRIMARY_ARTIFACTS:
                continue
            stale_ids = sorted(
                record.record_id
                for record in store.list_workspace_records(workspace_id)
                if (
                    record.source_id
                    and record.source_id not in active_primary_ids
                )
            )
            entries.append(
                DeletionManifestEntry(
                    store=store.name,
                    data_class=store.data_class,
                    record_ids=stale_ids,
                )
            )

        manifest = DeletionManifest(
            workspace_id=workspace_id,
            task_ids=[],
            entries=entries,
        )
        return self.apply_manifest(manifest)

    def _matching_primary_ids(
        self,
        workspace_id: str,
        task_ids: Set[str],
    ) -> Set[str]:
        matching_ids = set()
        for store in self._stores.values():
            if store.data_class != PRIMARY_ARTIFACTS:
                continue
            for record in store.list_workspace_records(workspace_id):
                if (
                    record.record_id in task_ids
                    or record.source_id in task_ids
                ):
                    matching_ids.add(record.record_id)
        return matching_ids

    def _all_primary_ids(self, workspace_id: str) -> Set[str]:
        primary_ids = set()
        for store in self._stores.values():
            if store.data_class != PRIMARY_ARTIFACTS:
                continue
            primary_ids.update(
                record.record_id
                for record in store.list_workspace_records(workspace_id)
            )
        return primary_ids

    def _record_matches_manifest(
        self,
        record: RetentionRecord,
        task_ids: Set[str],
        source_ids: Set[str],
    ) -> bool:
        if record.data_class == PRIMARY_ARTIFACTS:
            return record.record_id in task_ids or record.source_id in task_ids
        return bool(record.source_id and record.source_id in source_ids)
