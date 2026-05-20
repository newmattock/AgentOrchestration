from src.common.retention import (
    DERIVED_EMBEDDINGS,
    DERIVED_INDEXES,
    PRIMARY_ARTIFACTS,
    InMemoryRetentionStore,
    RetentionDeletionWorkflow,
)


def make_workflow():
    artifacts = InMemoryRetentionStore("artifact_store", PRIMARY_ARTIFACTS)
    embeddings = InMemoryRetentionStore("embedding_store", DERIVED_EMBEDDINGS)
    indexes = InMemoryRetentionStore("vector_index", DERIVED_INDEXES)
    workflow = RetentionDeletionWorkflow([artifacts, embeddings, indexes])
    return workflow, artifacts, embeddings, indexes


def test_delete_workspace_task_data_cascades_to_derived_stores():
    workflow, artifacts, embeddings, indexes = make_workflow()
    artifacts.add("artifact-1", "workspace-a", source_id="task-1")
    artifacts.add("artifact-2", "workspace-a", source_id="task-2")
    embeddings.add("embedding-1", "workspace-a", source_id="artifact-1")
    indexes.add("index-1", "workspace-a", source_id="artifact-1")
    embeddings.add(
        "embedding-other-task",
        "workspace-a",
        source_id="artifact-2",
    )
    embeddings.add(
        "embedding-other-workspace",
        "workspace-b",
        source_id="artifact-1",
    )

    completion = workflow.delete_workspace_task_data("workspace-a", ["task-1"])

    assert not artifacts.has("artifact-1")
    assert not embeddings.has("embedding-1")
    assert not indexes.has("index-1")
    assert artifacts.has("artifact-2")
    assert embeddings.has("embedding-other-task")
    assert embeddings.has("embedding-other-workspace")
    assert completion.manifest.store_names == [
        "artifact_store",
        "embedding_store",
        "vector_index",
    ]
    assert {
        store.data_class: store.deleted_ids
        for store in completion.stores
    } == {
        PRIMARY_ARTIFACTS: ["artifact-1"],
        DERIVED_EMBEDDINGS: ["embedding-1"],
        DERIVED_INDEXES: ["index-1"],
    }


def test_deletion_completion_records_every_store_even_when_empty():
    workflow, artifacts, embeddings, indexes = make_workflow()
    artifacts.add("artifact-1", "workspace-a", source_id="task-1")
    embeddings.add("embedding-1", "workspace-a", source_id="artifact-1")

    completion = workflow.delete_workspace_task_data("workspace-a", ["task-1"])

    assert [
        (store.store, store.data_class, store.deleted_ids, store.missing_ids)
        for store in completion.stores
    ] == [
        ("artifact_store", PRIMARY_ARTIFACTS, ["artifact-1"], []),
        ("embedding_store", DERIVED_EMBEDDINGS, ["embedding-1"], []),
        ("vector_index", DERIVED_INDEXES, [], []),
    ]
    assert not indexes.list_workspace_records("workspace-a")


def test_apply_manifest_is_idempotent_and_records_missing_ids():
    workflow, artifacts, embeddings, _indexes = make_workflow()
    artifacts.add("artifact-1", "workspace-a", source_id="task-1")
    embeddings.add("embedding-1", "workspace-a", source_id="artifact-1")
    embeddings.add("embedding-live", "workspace-a", source_id="artifact-live")
    manifest = workflow.build_manifest("workspace-a", ["task-1"])

    first_completion = workflow.apply_manifest(manifest)
    second_completion = workflow.apply_manifest(manifest)

    assert [
        store.deleted_ids
        for store in first_completion.stores
    ] == [["artifact-1"], ["embedding-1"], []]
    assert [
        store.missing_ids
        for store in second_completion.stores
    ] == [["artifact-1"], ["embedding-1"], []]
    assert embeddings.has("embedding-live")


def test_verify_completion_reports_no_manifest_records_remaining():
    workflow, artifacts, embeddings, indexes = make_workflow()
    artifacts.add("artifact-1", "workspace-a", source_id="task-1")
    embeddings.add("embedding-1", "workspace-a", source_id="artifact-1")
    indexes.add("index-1", "workspace-a", source_id="artifact-1")

    completion = workflow.delete_workspace_task_data("workspace-a", ["task-1"])
    verification = workflow.verify_completion(completion)

    assert verification.complete
    assert [
        (store.store, store.data_class, store.remaining_ids)
        for store in verification.stores
    ] == [
        ("artifact_store", PRIMARY_ARTIFACTS, []),
        ("embedding_store", DERIVED_EMBEDDINGS, []),
        ("vector_index", DERIVED_INDEXES, []),
    ]


def test_verify_completion_detects_stale_manifest_record_left_behind():
    workflow, artifacts, embeddings, _indexes = make_workflow()
    artifacts.add("artifact-1", "workspace-a", source_id="task-1")
    embeddings.add("embedding-1", "workspace-a", source_id="artifact-1")
    manifest = workflow.build_manifest("workspace-a", ["task-1"])
    completion = workflow.apply_manifest(manifest)
    embeddings.add("embedding-1", "workspace-a", source_id="artifact-1")

    verification = workflow.verify_completion(completion)

    assert not verification.complete
    assert {
        store.data_class: store.remaining_ids
        for store in verification.stores
    } == {
        PRIMARY_ARTIFACTS: [],
        DERIVED_EMBEDDINGS: ["embedding-1"],
        DERIVED_INDEXES: [],
    }


def test_reconcile_stale_derived_records_removes_orphans_only():
    workflow, artifacts, embeddings, indexes = make_workflow()
    artifacts.add("artifact-live", "workspace-a", source_id="task-live")
    embeddings.add("embedding-live", "workspace-a", source_id="artifact-live")
    embeddings.add("embedding-stale", "workspace-a", source_id="artifact-gone")
    indexes.add("index-stale", "workspace-a", source_id="artifact-gone")
    indexes.add(
        "index-other-workspace",
        "workspace-b",
        source_id="artifact-gone",
    )

    completion = workflow.reconcile_stale_derived_records("workspace-a")

    assert embeddings.has("embedding-live")
    assert not embeddings.has("embedding-stale")
    assert not indexes.has("index-stale")
    assert indexes.has("index-other-workspace")
    assert {
        store.data_class: store.deleted_ids
        for store in completion.stores
    } == {
        DERIVED_EMBEDDINGS: ["embedding-stale"],
        DERIVED_INDEXES: ["index-stale"],
    }
